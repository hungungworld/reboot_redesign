"""공통 데이터 모델 (공통 담당).

- User       로그인 계정 (로그인 README 6장)
- StoreItem  파트 저장소 REBOOT.store('<파트ID>') — 예전에는 브라우저(localStorage)에만 남던 값을 서버에 모아 팀원과 공유
- WorkLog    작업 로그 (설정 README 4장) — 수정·삭제 기능 없음
- AppSetting 설정 › 연결에서 입력한 키·설정값 (비밀값은 암호화)

파트 전용 표는 tabs/<파트ID>/models.py에 두고, 그 파일을 server/__init__.py가 불러옵니다.
"""
import json
import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    """DB에는 UTC(시간대 정보 없음)로 저장합니다. 화면 표시는 브라우저가 현지 시각으로 바꿉니다."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False, default="")
    title = db.Column(db.String(100), nullable=False, default="")  # 소속·역할 (상단 프로필에 표시)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    # 비밀번호를 바꾸거나 계정을 끄면 바뀌어서, 다른 기기의 로그인 유지 쿠키도 함께 무효가 됨
    session_token = db.Column(db.String(32), nullable=False, default=lambda: secrets.token_hex(8))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.session_token = secrets.token_hex(8)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return f"{self.id}:{self.session_token}"

    @property
    def initials(self):
        src = (self.name or self.email or "?").strip()
        parts = [p for p in src.replace("@", " ").split() if p]
        if len(parts) >= 2 and parts[0][0].isascii():
            return (parts[0][0] + parts[1][0]).upper()
        return src[:2].upper() if src[:1].isascii() else src[-2:]

    def to_public(self):
        return {"id": self.id, "email": self.email, "name": self.name or self.email.split("@")[0],
                "title": self.title, "initials": self.initials, "isAdmin": bool(self.is_admin)}


class StoreItem(db.Model):
    __tablename__ = "store_items"
    __table_args__ = (db.UniqueConstraint("part", "key", name="uq_store_part_key"),)

    id = db.Column(db.Integer, primary_key=True)
    part = db.Column(db.String(40), nullable=False, index=True)
    key = db.Column(db.String(80), nullable=False)
    value = db.Column(db.Text, nullable=False)  # JSON 문자열
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(100), nullable=False, default="")

    def parsed(self):
        try:
            return json.loads(self.value)
        except ValueError:
            return None


class WorkLog(db.Model):
    __tablename__ = "work_logs"

    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_name = db.Column(db.String(100), nullable=False, default="")
    part = db.Column(db.String(40), nullable=False, default="", index=True)
    kind = db.Column(db.String(20), nullable=False, default="기타")      # 수집 / AI 프롬프트 / 승인 · 선정 / 기타
    action = db.Column(db.String(200), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="완료")     # 완료 / 검토 대기 / 실패
    approval = db.Column(db.String(100), nullable=False, default="–")    # 자동 · 승인한 사람 이름 · –
    detail = db.Column(db.Text, nullable=False, default="{}")           # JSON (모델·프롬프트 요약·토큰 등)

    def to_dict(self):
        try:
            detail = json.loads(self.detail or "{}")
        except ValueError:
            detail = {}
        return {"id": self.id, "at": iso(self.at), "user": self.user_name, "part": self.part, "kind": self.kind,
                "action": self.action, "status": self.status, "approval": self.approval, "detail": detail}


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")
    secret = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(100), nullable=False, default="")
