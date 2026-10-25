"""콘텐츠 제작 표 (표 이름은 content_로 시작 — DEV_GUIDE 4장).

- ContentCampaign       캠페인: 시트(5항목) · 결정 사항(JSON) · 설정 · 상태 · 리비전
- ContentMessage        대화 (user · assistant · event) — payload에 후보·질문·반영 결과
- ContentDecisionEvent  시트·결정 변경 이력 (이전 값 · 새 값 · 출처 메시지 · 사용자 · 리비전) → 되돌리기
- ContentVersion        결과물 버전 (텍스트 · 이미지 파일) — 부모 버전 · 생성 시점 설정 · 검토 결과
- ContentJob            이미지 생성·편집 작업 (뒤에서 실행) — 요청 중복 방지 키 · 상태
- ContentReference      사용자가 올린 레퍼런스 이미지 — 대화 AI가 보고 쓴 요약 (이미지 생성 참고 사진으로는 쓰지 않음)
"""
import json

from server.extensions import db
from server.models import utcnow


def _load(text, default):
    try:
        v = json.loads(text) if text else default
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def _dump(v):
    return json.dumps(v, ensure_ascii=False)


def _json_prop(column, default):
    def getter(self):
        return _load(getattr(self, column), default() if callable(default) else default)

    def setter(self, value):
        setattr(self, column, _dump(value))
    return property(getter, setter)


class ContentCampaign(db.Model):
    __tablename__ = "content_campaigns"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False, default="")
    content_type = db.Column(db.String(20), nullable=False)
    channels_json = db.Column("channels", db.Text, nullable=False, default="[]")
    brand_focus = db.Column(db.Boolean, nullable=False, default=False)
    product_ids_json = db.Column("product_ids", db.Text, nullable=False, default="[]")
    goal = db.Column(db.String(300), nullable=False, default="")
    extra_request = db.Column(db.Text, nullable=False, default="")
    reference_text = db.Column(db.Text, nullable=False, default="")       # 기존 카피·참고 메모 (선택)
    decisions_json = db.Column("decisions", db.Text, nullable=False, default="{}")
    settings_json = db.Column("settings", db.Text, nullable=False, default="{}")
    status = db.Column(db.String(10), nullable=False, default="제작 중", index=True)
    revision = db.Column(db.Integer, nullable=False, default=1)
    selected_version_id = db.Column(db.Integer, nullable=True)            # 기준 결과물
    final_version_id = db.Column(db.Integer, nullable=True)               # 최종본 (최신과 별개)
    created_by = db.Column(db.String(100), nullable=False, default="")
    created_by_id = db.Column(db.Integer, nullable=True)
    updated_by = db.Column(db.String(100), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow, index=True)

    channels = _json_prop("channels_json", list)
    product_ids = _json_prop("product_ids_json", list)
    decisions = _json_prop("decisions_json", dict)
    settings = _json_prop("settings_json", dict)


class ContentMessage(db.Model):
    __tablename__ = "content_messages"
    __table_args__ = (db.UniqueConstraint("campaign_id", "client_request_id", name="uq_content_message_request"),)

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, nullable=False, index=True)
    role = db.Column(db.String(12), nullable=False)                        # user · assistant · event
    text = db.Column(db.Text, nullable=False, default="")
    payload_json = db.Column("payload", db.Text, nullable=False, default="{}")
    revision = db.Column(db.Integer, nullable=False, default=0)
    client_request_id = db.Column(db.String(64), nullable=True)            # 재전송·중복 클릭 방지
    user_name = db.Column(db.String(100), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    payload = _json_prop("payload_json", dict)


class ContentDecisionEvent(db.Model):
    __tablename__ = "content_decision_events"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, nullable=False, index=True)
    field = db.Column(db.String(80), nullable=False)                       # sheet.goal · decisions.background …
    old_json = db.Column(db.Text, nullable=False, default="null")          # {value, status} 또는 null
    new_json = db.Column(db.Text, nullable=False, default="null")
    source = db.Column(db.String(20), nullable=False, default="")          # sheet · chat · candidate · manual · undo · default
    message_id = db.Column(db.Integer, nullable=True)
    user_name = db.Column(db.String(100), nullable=False, default="")
    revision = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    @property
    def old(self):
        try:
            return json.loads(self.old_json or "null")
        except ValueError:
            return None

    @property
    def new(self):
        try:
            return json.loads(self.new_json or "null")
        except ValueError:
            return None


class ContentVersion(db.Model):
    __tablename__ = "content_versions"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, nullable=False, index=True)
    seq = db.Column(db.Integer, nullable=False, default=1)                 # 캠페인 안 번호 (v1, v2 …)
    kind = db.Column(db.String(20), nullable=False)                        # copy · cardnews · script · desc · crm · plan · image
    label = db.Column(db.String(40), nullable=False, default="")           # A안 · B안 · 시안 1 …
    title = db.Column(db.String(200), nullable=False, default="")
    body = db.Column(db.Text, nullable=False, default="")                  # 마크다운 (이미지는 설명)
    image_file = db.Column(db.String(120), nullable=True, unique=True)
    parent_id = db.Column(db.Integer, nullable=True)
    source = db.Column(db.String(10), nullable=False, default="ai")        # ai · manual
    approach = db.Column(db.String(300), nullable=False, default="")
    note = db.Column(db.String(500), nullable=False, default="")
    snapshot_json = db.Column("snapshot", db.Text, nullable=False, default="{}")   # 생성 시점 시트·결정·설정
    snapshot_hash = db.Column(db.String(64), nullable=False, default="")
    facts_version = db.Column(db.String(80), nullable=False, default="")
    review_json = db.Column("review", db.Text, nullable=False, default="{}")
    claims_json = db.Column("claims", db.Text, nullable=False, default="[]")
    needs_check_json = db.Column("needs_check", db.Text, nullable=False, default="[]")
    message_id = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.String(100), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    snapshot = _json_prop("snapshot_json", dict)
    review = _json_prop("review_json", dict)
    claims = _json_prop("claims_json", list)
    needs_check = _json_prop("needs_check_json", list)


class ContentJob(db.Model):
    __tablename__ = "content_jobs"

    id = db.Column(db.String(32), primary_key=True)
    campaign_id = db.Column(db.Integer, nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="image")       # image · image_edit
    # queued · running · saving · succeeded · failed · submission_unknown
    status = db.Column(db.String(24), nullable=False, default="queued", index=True)
    request_key = db.Column(db.String(120), nullable=True, unique=True)
    base_version_id = db.Column(db.Integer, nullable=True)
    result_version_id = db.Column(db.Integer, nullable=True)
    model = db.Column(db.String(80), nullable=False, default="")
    size = db.Column(db.String(20), nullable=False, default="")
    instructions = db.Column(db.Text, nullable=False, default="")
    snapshot_json = db.Column("snapshot", db.Text, nullable=False, default="{}")
    prompt = db.Column(db.Text, nullable=False, default="")
    error = db.Column(db.String(400), nullable=False, default="")
    message_id = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.String(100), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    snapshot = _json_prop("snapshot_json", dict)


class ContentReference(db.Model):
    __tablename__ = "content_references"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, nullable=False, index=True)
    file = db.Column(db.String(120), nullable=False, unique=True)         # 업로드 폴더 파일 이름 (jpg · png · webp)
    width = db.Column(db.Integer, nullable=False, default=0)
    height = db.Column(db.Integer, nullable=False, default=0)
    note = db.Column(db.String(300), nullable=False, default="")          # 올린 사람이 붙인 한 줄 메모 (선택)
    summary = db.Column(db.Text, nullable=False, default="")              # AI가 본 요약 (키 없거나 실패하면 빈 값)
    summary_model = db.Column(db.String(80), nullable=False, default="")
    created_by = db.Column(db.String(100), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
