"""홈 표: 뉴스 기사 · 수집 기록 · 이번 주 To-Do (표 이름은 home_ 으로 시작)."""
import json

from server.extensions import db
from server.models import iso, utcnow


def _loads(text, default):
    try:
        v = json.loads(text or "")
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


class HomeNewsArticle(db.Model):
    """구글 뉴스 기사 (URL·제목 기준 중복 제거). 본문은 저장하지 않음 (제목·매체·날짜만). domain 칸에 매체명 저장."""
    __tablename__ = "home_news_articles"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(1000), nullable=False, unique=True, index=True)
    title = db.Column(db.String(500), nullable=False, default="")
    domain = db.Column(db.String(200), nullable=False, default="")
    seen_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)  # 기사 날짜 pubDate (UTC)
    group_id = db.Column(db.String(40), nullable=False, default="")             # 처음 찾은 키워드 묶음
    groups = db.Column(db.String(200), nullable=False, default="")              # 찾은 키워드 묶음들 (쉼표)
    language = db.Column(db.String(40), nullable=False, default="")
    source_country = db.Column(db.String(60), nullable=False, default="")
    run_id = db.Column(db.Integer, nullable=True)
    relevant = db.Column(db.Boolean, nullable=True)                             # AI 관련성 (None: 판단 전)
    collected_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self):
        return {"id": self.id, "title": self.title, "url": self.url, "domain": self.domain, "date": iso(self.seen_at),
                "group": self.group_id, "groups": [g for g in self.groups.split(",") if g], "relevant": self.relevant}


class HomeNewsRun(db.Model):
    """수집 1회 기록: 상태 · 묶음별 개수 · 오류 · 급증 시그널 · AI 요약."""
    __tablename__ = "home_news_runs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(12), nullable=False, default="running")  # running · ok · partial · failed
    trigger = db.Column(db.String(12), nullable=False, default="manual")  # manual · daily
    user_name = db.Column(db.String(100), nullable=False, default="")
    counts = db.Column(db.Text, nullable=False, default="{}")
    errors = db.Column(db.Text, nullable=False, default="[]")
    signals = db.Column(db.Text, nullable=False, default="[]")
    ai_status = db.Column(db.String(12), nullable=False, default="")     # ok · no_key · error · skipped
    ai_error = db.Column(db.String(300), nullable=False, default="")
    summary = db.Column(db.Text, nullable=False, default="{}")            # AI: {headline, trends, relevant_ids}
    model = db.Column(db.String(80), nullable=False, default="")

    def to_dict(self):
        return {"id": self.id, "startedAt": iso(self.started_at), "finishedAt": iso(self.finished_at), "status": self.status,
                "trigger": self.trigger, "user": self.user_name, "counts": _loads(self.counts, {}),
                "errors": _loads(self.errors, []), "aiStatus": self.ai_status, "aiError": self.ai_error}


class HomeNewsSaved(db.Model):
    """보관한 기사 (책갈피). 원래 기사는 90일 뒤 지워지므로 화면에 보인 내용을 그대로 복사해 둠. 시장·고객 인사이트 › 데이터 › 뉴스 수집에서 봄."""
    __tablename__ = "home_news_saved"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(1000), nullable=False, unique=True, index=True)
    title = db.Column(db.String(500), nullable=False, default="")       # 영어 원제목
    title_ko = db.Column(db.String(300), nullable=False, default="")
    summary_ko = db.Column(db.String(600), nullable=False, default="")
    why_ko = db.Column(db.String(600), nullable=False, default="")      # 마케터의 시선 (트렌드는 적용점)
    kind = db.Column(db.String(10), nullable=False, default="flow")     # flow 업계 뉴스 · trend 트렌드
    area_label = db.Column(db.String(60), nullable=False, default="")
    domain = db.Column(db.String(200), nullable=False, default="")
    published_at = db.Column(db.DateTime, nullable=True)
    saved_by = db.Column(db.String(100), nullable=False, default="")
    saved_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    def to_dict(self):
        return {"id": self.id, "url": self.url, "title": self.title, "titleKo": self.title_ko, "summaryKo": self.summary_ko,
                "whyKo": self.why_ko, "kind": self.kind, "areaLabel": self.area_label, "domain": self.domain,
                "date": iso(self.published_at), "savedBy": self.saved_by, "savedAt": iso(self.saved_at)}


class HomeTodo(db.Model):
    """이번 주 To-Do 한 줄. origin: ai · rule(키 없을 때 규칙) · manual(직접 추가)."""
    __tablename__ = "home_todos"

    id = db.Column(db.Integer, primary_key=True)
    week_start = db.Column(db.Date, nullable=False, index=True)          # 그 주 월요일
    title = db.Column(db.String(200), nullable=False)
    reason = db.Column(db.String(500), nullable=False, default="")       # 근거
    source = db.Column(db.String(40), nullable=False, default="home")    # 관련 파트 ID
    origin = db.Column(db.String(10), nullable=False, default="manual")
    done = db.Column(db.Boolean, nullable=False, default=False)
    done_at = db.Column(db.DateTime, nullable=True)
    done_by = db.Column(db.String(100), nullable=False, default="")
    sort = db.Column(db.Integer, nullable=False, default=0)
    created_by = db.Column(db.String(100), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def to_dict(self):
        return {"id": self.id, "weekStart": self.week_start.isoformat(), "title": self.title, "reason": self.reason,
                "source": self.source, "origin": self.origin, "ai": self.origin == "ai", "done": bool(self.done),
                "doneAt": iso(self.done_at), "doneBy": self.done_by, "createdBy": self.created_by,
                "createdAt": iso(self.created_at)}


class HomeTodoWeek(db.Model):
    """주마다 자동 생성을 한 번만 하도록 남기는 표시."""
    __tablename__ = "home_todo_weeks"

    week_start = db.Column(db.Date, primary_key=True)
    origin = db.Column(db.String(10), nullable=False, default="rule")
    generated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    generated_by = db.Column(db.String(100), nullable=False, default="")
    note = db.Column(db.String(300), nullable=False, default="")
