"""설정 (공통 담당).

값은 환경변수에서 읽습니다. 로컬은 .env, Render는 서비스의 Environment에 넣습니다.
키·비밀번호는 코드에 넣지 않습니다.

.env는 저장소 맨 위에 하나만 둡니다 (이미 있는 환경변수는 덮어쓰지 않음).
"""
import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent  # 저장소 맨 위
load_dotenv(ROOT / ".env", override=False)

log = logging.getLogger(__name__)


def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


IS_RENDER = env_bool("RENDER")
IS_PRODUCTION = IS_RENDER or os.environ.get("APP_ENV", "").strip().lower() == "production"


def default_data_dir():
    """업로드·DB·캐시를 두는 곳. Render는 영구 디스크(/var/data)를 DATA_DIR로 연결합니다."""
    return Path(os.environ.get("DATA_DIR") or (ROOT / "instance")).resolve()


def _secret_key(ddir):
    if IS_PRODUCTION:
        raise RuntimeError("SECRET_KEY 환경변수가 없습니다. Render → Environment에 SECRET_KEY를 넣으세요.")
    # 로컬 개발: 재시작해도 로그인이 풀리지 않게 DATA_DIR에 개발용 키를 만들어 둠 (깃에 올라가지 않는 폴더)
    f = ddir / ".dev_secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32), encoding="utf-8")
        log.warning("SECRET_KEY가 없어 개발용 키를 만들었습니다: %s", f)
    return f.read_text(encoding="utf-8").strip()


def _database_url(ddir):
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):  # Render·Heroku 형식 → SQLAlchemy 형식
        url = "postgresql://" + url[len("postgres://"):]
    return url or f"sqlite:///{(ddir / 'reboot.db').as_posix()}"


def finalize(config):
    """create_app에서 덮어쓴 값(DATA_DIR 등)을 반영해 나머지 값을 정함. 파일·폴더는 여기서만 만듦."""
    ddir = Path(config["DATA_DIR"]).resolve()
    ddir.mkdir(parents=True, exist_ok=True)
    config["DATA_DIR"] = ddir
    if not config.get("SECRET_KEY"):
        config["SECRET_KEY"] = _secret_key(ddir)
    if not config.get("APP_SECRET_KEY"):
        config["APP_SECRET_KEY"] = config["SECRET_KEY"]
    if not config.get("SQLALCHEMY_DATABASE_URI"):
        config["SQLALCHEMY_DATABASE_URI"] = _database_url(ddir)
    if "SQLALCHEMY_ENGINE_OPTIONS" not in config or config["SQLALCHEMY_ENGINE_OPTIONS"] is None:
        config["SQLALCHEMY_ENGINE_OPTIONS"] = (
            {"connect_args": {"timeout": 20, "check_same_thread": False}}
            if config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite") else {"pool_pre_ping": True})


class Config:
    # 값이 비어 있으면 create_app → finalize()에서 DATA_DIR 기준으로 정함
    DATA_DIR = default_data_dir()
    SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or None
    # 화면에서 입력한 API 키를 암호화하는 키. 바꾸면 저장된 키를 다시 입력해야 합니다 (설정 README 3장)
    APP_SECRET_KEY = os.environ.get("APP_SECRET_KEY", "").strip() or None

    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_ENGINE_OPTIONS = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 세션 쿠키 (로그인 README 7장)
    SESSION_COOKIE_NAME = "reboot_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_NAME = "reboot_remember"
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # CSRF: 화면을 오래 열어 둬도 토큰이 만료되지 않게 (세션이 끝나면 함께 만료)
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT = IS_PRODUCTION

    # 업로드 한도: 메일 첨부 25MB + 여유
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024

    # 로그인 요청 제한 (프로세스 메모리). gunicorn 워커 1개 기준
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_HEADERS_ENABLED = False

    TIMEZONE = os.environ.get("TIMEZONE", "Asia/Seoul")
    IS_PRODUCTION = IS_PRODUCTION
    IS_RENDER = IS_RENDER
    # 상단 바의 환경 표시 (예: "DEMO · 예시 데이터")
    ENV_LABEL = os.environ.get("ENV_LABEL", "").strip() or ("RENDER" if IS_RENDER else "LOCAL")
    # 외부 크론(GitHub Actions · Render Cron · cron-job.org)이 하루 1회 작업을 부를 때 쓰는 토큰
    CRON_TOKEN = os.environ.get("CRON_TOKEN", "").strip()
    # 1이면 로그인 화면에 관리자 계정을 미리 채움 (시연용)
    DEMO_LOGIN = env_bool("DEMO_LOGIN")