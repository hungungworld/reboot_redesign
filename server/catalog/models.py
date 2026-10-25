"""시즌 · 팩트시트 표."""
import json

from ..extensions import db
from ..models import utcnow


class Season(db.Model):
    __tablename__ = "seasons"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=False)
    category = db.Column(db.String(20), nullable=False, default="holiday")  # holiday · sports · occasion
    priority = db.Column(db.Integer, nullable=False, default=2)              # 1(최우선) ~ 3
    lead_days = db.Column(db.Integer, nullable=False, default=30)            # 캠페인 준비 시작 (N일 전)
    notes = db.Column(db.Text, nullable=False, default="")
    monitor = db.Column(db.Boolean, nullable=False, default=True)            # 인사이트 주간 리뷰·검색 수요에서 볼 시즌
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(100), nullable=False, default="")


class FactRecord(db.Model):
    """브랜드(entity='brand') 또는 제품(entity=제품 ID) 한 건의 팩트를 JSON으로."""
    __tablename__ = "facts"

    entity = db.Column(db.String(40), primary_key=True)
    kind = db.Column(db.String(10), nullable=False)  # brand · product
    sort = db.Column(db.Integer, nullable=False, default=0)
    data = db.Column(db.Text, nullable=False, default="{}")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    updated_by = db.Column(db.String(100), nullable=False, default="")

    def parsed(self):
        try:
            return json.loads(self.data or "{}")
        except ValueError:
            return {}
