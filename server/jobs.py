"""오래 걸리는 작업 · 하루 1회 작업 (공통 담당).

- run_background(app, "이름", 함수)   요청을 붙잡지 않고 뒤에서 실행 (같은 이름은 동시에 하나만)
- @daily("이름", "설명")              하루 1회 작업 등록 → POST /api/jobs/daily (X-Cron-Token) 또는 `flask --app server run-daily`

Render 무료 크론이 없을 때: GitHub Actions schedule이나 cron-job.org에서
  curl -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<서비스주소>/api/jobs/daily
"""
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from flask import current_app

_DAILY = {}


class Busy(Exception):
    """같은 작업이 이미 실행 중."""


def daily(name, label):
    def deco(fn):
        _DAILY[name] = (label, fn)
        return fn
    return deco


def daily_jobs():
    return {k: v[0] for k, v in _DAILY.items()}


def _lock_path(name, data_dir=None):
    base = Path(data_dir if data_dir is not None else current_app.config["DATA_DIR"])
    return base / "locks" / f"{name}.lock"


def _acquire(path, name, stale_seconds):
    """잠금 파일을 만들고 파일 번호를 돌려줌. 이미 있으면 Busy (오래된 잠금은 지우고 다시 시도)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale_seconds:
                    path.unlink(missing_ok=True)  # 서버가 중간에 꺼져 남은 잠금
                    continue
            except FileNotFoundError:
                continue
            raise Busy(name)
    raise Busy(name)


def _release(fd, path):
    try:
        os.close(fd)
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def file_lock(name, stale_seconds=3600):
    """프로세스가 여러 개여도 같은 작업이 겹치지 않게 DATA_DIR/locks/<name>.lock 파일로 잠금."""
    path = _lock_path(name)
    fd = _acquire(path, name, stale_seconds)
    try:
        yield
    finally:
        _release(fd, path)


def is_running(name, stale_seconds=3600):
    """잠금 파일이 있으면 실행 중. 오래된 잠금(서버가 중간에 꺼져 남은 것)은 실행 중으로 보지 않음."""
    try:
        return time.time() - _lock_path(name).stat().st_mtime <= stale_seconds
    except FileNotFoundError:
        return False


def run_background(app, name, fn, *args, stale_seconds=3600, **kwargs):
    """뒤에서 실행. 잠금은 부른 쪽에서 바로 잡으므로 겹치지 않음.
    반환: 시작했으면 Thread, 같은 이름이 이미 실행 중이면 None."""
    path = _lock_path(name, app.config["DATA_DIR"])
    try:
        fd = _acquire(path, name, stale_seconds)
    except Busy:
        app.logger.info("작업 %s는 이미 실행 중이라 건너뜀", name)
        return None

    def target():
        with app.app_context():
            try:
                fn(*args, **kwargs)
            except Exception:
                app.logger.exception("작업 %s 실패", name)
            finally:
                _release(fd, path)

    t = threading.Thread(target=target, name=f"job-{name}", daemon=True)
    try:
        t.start()
    except Exception:
        _release(fd, path)
        raise
    return t


def run_daily(app, wait=False):
    threads, started = [], []
    for name, (_label, fn) in _DAILY.items():
        t = run_background(app, f"daily-{name}", fn)
        if t is not None:
            threads.append(t)
            started.append(name)
    if wait:
        for t in threads:
            t.join()
    return started
