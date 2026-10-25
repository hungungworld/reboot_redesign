"""API 키 · 설정값 읽기 (공통 담당).

우선순위 (설정 README 3장)
  1) 설정 › 연결 화면에서 입력한 값 — DB에 암호화해서 저장, 화면에는 끝 4자리만
  2) 환경변수 (.env · Render Environment). 예전 이름도 함께 읽음 (open_api, gmail_api …)
  3) 기본값

파트 코드에서는 이렇게 씁니다.
    from server import envkeys
    key = envkeys.get("OPENAI_API_KEY")      # 없으면 ""
    if envkeys.has("SERPAPI_KEY"): ...
"""
import base64
import hashlib
import os
from dataclasses import dataclass, field

from flask import current_app, has_app_context

from .extensions import db
from .models import AppSetting, utcnow


@dataclass(frozen=True)
class Service:
    name: str
    label: str
    used_by: str
    limit: str = ""
    secret: bool = True
    aliases: tuple = field(default_factory=tuple)
    group: str = "key"  # key · setting


SERVICES = [
    Service("OPENAI_API_KEY", "OpenAI", "홈 브리핑 · 인사이트 · 콘텐츠 · 파트너 메일 · 성과분석 AI", "유료 (사용량 과금)",
            aliases=("OPENAI_API_KEY", "open_api", "AI_API_KEY")),
    Service("ANTHROPIC_API_KEY", "Claude (Anthropic)", "컴플라이언스 AI 2차 판정 · CS 답장 초안 · 모니터링 한글 요약", "유료 (사용량 과금)"),
    Service("SERPAPI_KEY", "SerpApi", "Amazon 진열대·리뷰 · Google 검색 트렌드", "월 250회"),
    Service("YOUTUBE_API_KEY", "YouTube Data API", "잠재 고객 (영상 검색·댓글)", "하루 10,000 units"),
    Service("FIRECRAWL_API_KEY", "Firecrawl", "Weee! 진열대", "월 1,000 크레딧"),
    Service("KEXIM_API_KEY", "한국수출입은행 환율", "성과분석 달러 환산", "하루 1,000회"),
    Service("OPENFDA_API_KEY", "openFDA", "규제 모니터링 (키 없이 먼저 호출)", "키 없이도 동작"),
    Service("REGULATIONS_GOV_API_KEY", "Regulations.gov", "규제 모니터링 (규칙·규칙안)", "무료 키"),
    Service("COURTLISTENER_TOKEN", "CourtListener", "규제 모니터링 (허위광고 판결)", "무료 토큰"),
    Service("FDA_DASHBOARD_USER", "FDA Data Dashboard 계정 (이메일)", "규제 모니터링 (수입 거부 · 경고장)", secret=False),
    Service("FDA_DASHBOARD_KEY", "FDA Data Dashboard 키", "규제 모니터링 (수입 거부 · 경고장)", "무료 키"),
    Service("SHOPIFY_TOKEN", "Shopify", "할인코드별 주문·매출 (연동 예정)"),
    Service("HIGGSFIELD_API_KEY", "Higgsfield", "콘텐츠 이미지·영상 (연동 예정)"),
    Service("GMAIL_ADDRESS", "Gmail 주소 (SMTP)", "협업 메일 발송", secret=False,
            aliases=("GMAIL_ADDRESS", "gmail_address")),
    Service("GMAIL_APP_PASSWORD", "Gmail 앱 비밀번호", "협업 메일 발송 · 답장 확인 (IMAP)", "16자리 앱 비밀번호",
            aliases=("GMAIL_APP_PASSWORD", "gmail_api")),
    Service("GOOGLE_CLIENT_ID", "Google OAuth 클라이언트 ID", "Google로 로그인 (CS Gmail 문의함·답장 초안)", secret=False),
    Service("GOOGLE_CLIENT_SECRET", "Google OAuth 클라이언트 보안 비밀", "Google로 로그인 (CS Gmail 문의함·답장 초안)"),
    Service("SLACK_WEBHOOK_URL", "Slack 웹훅 주소", "인사이트 리뷰 카드 '슬랙으로 보내기' (보내기 전 확인)", "무료"),
    # 키가 아닌 설정값
    Service("OPENAI_MODEL", "OpenAI 모델", "OpenAI를 쓰는 모든 기능", secret=False, group="setting"),
    Service("GMAIL_QUERY", "CS 문의 메일 조건", "CS 문의함 (Gmail 검색어)", secret=False, group="setting"),
    Service("PRODUCT_DESC", "제품 설명 (AI 프롬프트)", "파트너 메일·가이드", secret=False, group="setting"),
    Service("REGULATION_DB_URL", "규제(검열) DB 주소", "파트너 협업 메일 '규제 DB 연결' 표시 (통합 DB 미정)", group="setting"),
    Service("CONTENT_TEXT_MODEL", "콘텐츠 대화·제작 모델", "콘텐츠 제작 (비우면 OPENAI_MODEL)", secret=False, group="setting"),
    Service("CONTENT_IMAGE_MODEL", "콘텐츠 이미지 모델", "콘텐츠 이미지 생성 (비우면 gpt-image-1)", secret=False, group="setting"),
]
_BY_NAME = {s.name: s for s in SERVICES}
DEFAULTS = {"OPENAI_MODEL": "gpt-4o-mini"}


def service(name):
    return _BY_NAME.get(name)


def _aliases(name):
    s = _BY_NAME.get(name)
    return s.aliases if s and s.aliases else (name,)


def _fernet():
    from cryptography.fernet import Fernet
    secret = current_app.config["APP_SECRET_KEY"].encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def _db_value(name):
    if not has_app_context():
        return ""
    try:
        row = db.session.get(AppSetting, name)
    except Exception:  # 표가 아직 없을 때 (첫 실행) 등
        db.session.rollback()
        return ""
    if not row or not row.value:
        return ""
    if not row.secret:
        return row.value
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(row.value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # APP_SECRET_KEY가 바뀌면 예전 키를 풀 수 없음 → 화면에서 다시 입력
        current_app.logger.warning("저장된 %s를 풀지 못했습니다 (APP_SECRET_KEY 변경?)", name)
        return ""


def _env_value(name):
    for alias in _aliases(name):
        v = os.environ.get(alias, "").strip()
        if v:
            return v
    return ""


def get(name, default=""):
    return _db_value(name) or _env_value(name) or DEFAULTS.get(name, default)


def has(name):
    return bool(_db_value(name) or _env_value(name))


def source(name):
    """'screen' (화면 입력) · 'env' (.env/환경변수) · '' (없음)"""
    if _db_value(name):
        return "screen"
    if _env_value(name):
        return "env"
    return ""


def masked(name):
    v = _db_value(name) or _env_value(name)
    if not v:
        return ""
    s = _BY_NAME.get(name)
    if s and not s.secret:
        return v
    return "••••" + v[-4:] if len(v) > 4 else "••••"


def set_value(name, value, user_name=""):
    """화면에서 입력한 값 저장. 빈 값이면 지워서 .env 값으로 돌아감."""
    s = _BY_NAME.get(name)
    if not s:
        raise KeyError(name)
    value = (value or "").strip()
    row = db.session.get(AppSetting, name)
    if not value:
        if row:
            db.session.delete(row)
        db.session.commit()
        return
    stored = _fernet().encrypt(value.encode("utf-8")).decode("utf-8") if s.secret else value
    if row is None:
        row = AppSetting(key=name)
        db.session.add(row)
    row.value, row.secret, row.updated_by, row.updated_at = stored, s.secret, user_name, utcnow()
    db.session.commit()


def status_list():
    """설정 › 연결 화면용: 값은 내보내지 않고 상태·끝 4자리만."""
    out = []
    for s in SERVICES:
        src = source(s.name)
        out.append({"name": s.name, "label": s.label, "usedBy": s.used_by, "limit": s.limit, "group": s.group,
                    "secret": s.secret, "source": src, "connected": bool(src), "masked": masked(s.name),
                    "default": DEFAULTS.get(s.name, "")})
    return out
