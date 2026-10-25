"""설정 파트 표 (표 이름은 settings_로 시작)."""
import json

from server.extensions import db
from server.models import utcnow


class CollectConfig(db.Model):
    """수집 설정 한 줄 (id=1). 값은 JSON — 기본값과 합쳐서 씀 (config.get_collect_config)."""
    __tablename__ = "settings_collect_config"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Text, nullable=False, default="{}")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_by = db.Column(db.String(100), nullable=False, default="")

    def parsed(self):
        try:
            v = json.loads(self.data or "{}")
        except ValueError:
            return {}
        return v if isinstance(v, dict) else {}


class ConnectionCheck(db.Model):
    """마지막 연결 테스트 결과. 키 값은 저장하지 않고, 테스트할 때의 출처·끝 4자리만 (키가 바뀌면 결과를 다시 '확인 전'으로)."""
    __tablename__ = "settings_connection_checks"

    name = db.Column(db.String(80), primary_key=True)
    status = db.Column(db.String(10), nullable=False, default="")    # 정상 · 오류 · 미연결
    message = db.Column(db.String(300), nullable=False, default="")
    detail = db.Column(db.Text, nullable=False, default="{}")
    source = db.Column(db.String(10), nullable=False, default="")    # screen · env
    tail = db.Column(db.String(8), nullable=False, default="")       # 테스트한 값의 끝 4자리
    checked_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    checked_by = db.Column(db.String(100), nullable=False, default="")

    def parsed_detail(self):
        try:
            v = json.loads(self.detail or "{}")
        except ValueError:
            return {}
        return v if isinstance(v, dict) else {}
