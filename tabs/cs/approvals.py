"""CS 답장 승인 기록 (서버 보관) — Gmail로 바로 보내기 전에 대조합니다.

화면에서 [최종 승인]을 누르면 Gmail 문의마다 '누가 · 어떤 본문을(본문 지문 SHA-256) · 어떤 재검사 판정으로' 승인했는지
서버에 남깁니다. 보내기(/api/cs/gmail/send)는 이 기록이 있고 지금 보내려는 본문이 승인한 본문과 글자 하나까지 같을 때만
보내며, 한 번 보낸 승인은 다시 보낼 수 없습니다. 판정 엔진은 브라우저에만 있으므로 판정 값은 화면이 보낸 것을 쓰지만,
승인한 계정은 로그인 정보로 서버가 기록합니다.

한 번만 보내기 (2026-10-24): 보내기 직전에 '보내는 중' 표시 파일(DATA_DIR/cs/sending/<지문>.lock)을
O_EXCL로 만듭니다. 서버 프로세스가 여러 개여도 같은 답장은 먼저 만든 요청 하나만 보냅니다.
보냈는지 확실하지 않은 실패(Gmail 응답 끊김 등)는 표시를 남겨 자동으로 다시 보내지 않고, 사람이 Gmail 보낸편지함을
확인한 뒤 다시 최종 승인하면 풀립니다.

저장: DATA_DIR/cs/approvals.json (깃에 올리지 않음 · 고객 메일 본문은 저장하지 않고 지문만)
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

_lock = threading.Lock()
VERDICTS = ("PASS", "REVISE", "REVIEW", "COND")   # BLOCK은 승인 불가


class ApprovalError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _path():
    return Path(current_app.config["DATA_DIR"]) / "cs" / "approvals.json"


def _sending_path(message_id):
    return Path(current_app.config["DATA_DIR"]) / "cs" / "sending" / (hashlib.sha256(str(message_id).encode()).hexdigest()[:32] + ".lock")


def _load():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def fingerprint(body):
    return hashlib.sha256(str(body).replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(message_id, body, approver, account, verdict):
    if verdict not in VERDICTS:
        raise ApprovalError("BLOCK이거나 재검사 판정이 없는 답장은 승인할 수 없습니다.")
    with _lock:
        data = _load()
        cur = data.get(message_id) or {}
        if cur.get("sent_at"):
            raise ApprovalError("이미 보낸 문의입니다. 새 답장은 Gmail에서 직접 이어서 보내세요.", 409)
        data[message_id] = {"hash": fingerprint(body), "approver": approver, "account": account,
                            "verdict": verdict, "approved_at": _now()}
        _save(data)
        _sending_path(message_id).unlink(missing_ok=True)   # 다시 승인하면 '결과 불확실' 표시도 풀림
        return data[message_id]


def revoke(message_id):
    with _lock:
        data = _load()
        cur = data.get(message_id)
        if cur and not cur.get("sent_at"):
            del data[message_id]
            _save(data)
            return True
        return False


def check(message_id, body):
    """보내기 직전 대조. 통과하면 승인 기록을 돌려줌."""
    cur = _load().get(message_id)
    if not cur:
        raise ApprovalError("서버에 승인 기록이 없습니다. 문의함에서 다시 최종 승인하세요.")
    if cur.get("sent_at"):
        raise ApprovalError("이미 보낸 답장입니다 (같은 답장을 두 번 보내지 않음).", 409)
    if cur.get("hash") != fingerprint(body):
        raise ApprovalError("승인한 뒤 답장 내용이 바뀌었습니다. 문의함에서 다시 최종 승인하세요.")
    return cur


def begin_send(message_id, body):
    """보내기 직전 대조 + '보내는 중' 표시를 한 번에. 이미 표시가 있으면(동시 요청 · 결과 불확실) 거절."""
    cur = check(message_id, body)
    lock = _sending_path(message_id)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ApprovalError("이 답장은 보내는 중이거나 지난번 보내기 결과가 확실하지 않습니다. "
                            "Gmail 보낸편지함을 확인하고, 안 갔으면 문의함에서 다시 최종 승인하세요.", 409)
    with os.fdopen(fd, "w") as f:
        f.write(_now())
    return cur


def cancel_send(message_id):
    """확실히 안 보내진 실패(로그인 필요 등)만 '보내는 중' 표시를 풀어 바로 다시 시도할 수 있게."""
    _sending_path(message_id).unlink(missing_ok=True)


def mark_sent(message_id, sent_id, to):
    with _lock:
        data = _load()
        cur = data.get(message_id) or {}
        cur.update({"sent_at": _now(), "sent_id": sent_id, "to": to})
        data[message_id] = cur
        _save(data)
