"""인사이트 파트 표 (표 이름은 insights_로 시작).

수집한 원본은 주차(YYYY-Www) · 월(YYYY-MM)로 나눠 쌓고, 화면은 이 표를 읽어 계산합니다.
실제로 모은 값만 넣습니다 (예시 · 가짜 데이터 없음).
"""
import json

from server.extensions import db
from server.models import utcnow


def load_json(text, default):
    try:
        v = json.loads(text) if text else default
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


class ShelfItem(db.Model):
    """진열대 한 상품의 주차별 모습 (Amazon · Weee!)."""
    __tablename__ = "insights_shelf_items"

    id = db.Column(db.Integer, primary_key=True)
    week = db.Column(db.String(10), nullable=False, index=True)
    source = db.Column(db.String(10), nullable=False)               # amazon · weee
    key = db.Column(db.String(200), nullable=False, index=True)     # amazon:<ASIN> · weee:<상품번호>
    position = db.Column(db.Integer)
    queries = db.Column(db.String(300), nullable=False, default="")
    title = db.Column(db.String(500), nullable=False, default="")
    brand = db.Column(db.String(120), nullable=False, default="")
    price = db.Column(db.Float)
    price_text = db.Column(db.String(60), nullable=False, default="")
    servings = db.Column(db.Integer)                                 # 문구에서 읽은 회분 수 (못 읽으면 null)
    servings_rule = db.Column(db.String(60), nullable=False, default="")
    rating = db.Column(db.Float)
    reviews = db.Column(db.Integer)
    availability = db.Column(db.String(200), nullable=False, default="")
    in_stock = db.Column(db.Boolean)                                 # null = 모름
    link = db.Column(db.String(800), nullable=False, default="")
    message = db.Column(db.Text, nullable=False, default="")         # 판매 문구 (불릿 · 설명 앞부분)
    own = db.Column(db.Boolean, nullable=False, default=False)       # 자사 (알디콤)
    tracked = db.Column(db.Boolean, nullable=False, default=False)   # 추적 ASIN
    detailed = db.Column(db.Boolean, nullable=False, default=False)  # 상품 상세 조회까지 함
    edited_by = db.Column(db.String(100), nullable=False, default="")
    collected_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class ServingOverride(db.Model):
    """사람이 고친 회분 수 (한 번 확인한 개수는 기억 · 모든 주차에 적용)."""
    __tablename__ = "insights_serving_overrides"

    key = db.Column(db.String(200), primary_key=True)
    servings = db.Column(db.Integer, nullable=False)
    updated_by = db.Column(db.String(100), nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Review(db.Model):
    """구매자 리뷰 (Amazon 대표 리뷰 · Olive Young · Yami · Weee!). 작성자 이름은 저장하지 않음."""
    __tablename__ = "insights_reviews"

    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(64), nullable=False, unique=True)
    source = db.Column(db.String(10), nullable=False)                # amazon · oliveyoung · yami · weee
    product_key = db.Column(db.String(200), nullable=False, default="", index=True)
    product_title = db.Column(db.String(500), nullable=False, default="")
    brand = db.Column(db.String(120), nullable=False, default="")
    own = db.Column(db.Boolean, nullable=False, default=False)
    rating = db.Column(db.Float)
    title = db.Column(db.String(300), nullable=False, default="")
    text = db.Column(db.Text, nullable=False, default="")
    date_text = db.Column(db.String(80), nullable=False, default="")
    topics = db.Column(db.String(200), nullable=False, default="[]")  # ["효과", "맛·향" …]
    sentiment = db.Column(db.String(2), nullable=False, default="0")  # + · - · 0
    summary_ko = db.Column(db.Text, nullable=False, default="")
    classified_by = db.Column(db.String(10), nullable=False, default="")  # rule · ai
    week = db.Column(db.String(10), nullable=False, default="")
    collected_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    def topic_list(self):
        return load_json(self.topics, [])


class ReviewCS(db.Model):
    """자사 리뷰 CS 대응 상태 — 마케터가 바꿀 때만 저장 (없으면 화면이 규칙으로 '대응 필요' · '미확인'을 붙임)."""
    __tablename__ = "insights_review_cs"

    review_id = db.Column(db.Integer, primary_key=True)                # insights_reviews.id
    status = db.Column(db.String(10), nullable=False, default="new")   # new · todo · done · skip
    memo = db.Column(db.String(1000), nullable=False, default="")
    updated_by = db.Column(db.String(100), nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class TrendSeries(db.Model):
    """검색 트렌드 12개월 (키워드마다 따로 조회한 결과 한 묶음)."""
    __tablename__ = "insights_trend_series"

    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(120), nullable=False, index=True)
    week = db.Column(db.String(10), nullable=False, default="")
    source = db.Column(db.String(10), nullable=False, default="serpapi")  # serpapi · csv
    points = db.Column(db.Text, nullable=False, default="[]")             # [{date, value}]
    collected_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def point_list(self):
        return load_json(self.points, [])


class Video(db.Model):
    __tablename__ = "insights_videos"

    id = db.Column(db.String(20), primary_key=True)                  # YouTube videoId
    month = db.Column(db.String(7), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False, default="")
    channel = db.Column(db.String(200), nullable=False, default="")
    channel_id = db.Column(db.String(40), nullable=False, default="")
    published_at = db.Column(db.String(30), nullable=False, default="")
    views = db.Column(db.Integer)
    likes = db.Column(db.Integer)
    comment_count = db.Column(db.Integer)
    query_set = db.Column(db.String(2), nullable=False, default="")
    search_query = db.Column(db.String(200), nullable=False, default="")  # ('query'는 Model.query와 겹쳐서 다른 이름)
    kept = db.Column(db.Boolean, nullable=False, default=False)      # 최소 조회수 통과
    collected_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Comment(db.Model):
    """유튜브 댓글 (작성자 이름 없이 본문만)."""
    __tablename__ = "insights_comments"

    id = db.Column(db.String(64), primary_key=True)                  # commentId
    video_id = db.Column(db.String(20), nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False, default="")
    likes = db.Column(db.Integer)
    published_at = db.Column(db.String(30), nullable=False, default="")
    relevant = db.Column(db.Boolean, nullable=False, default=True)
    is_question = db.Column(db.Boolean, nullable=False, default=False)
    topic = db.Column(db.String(40), nullable=False, default="")
    classified_by = db.Column(db.String(10), nullable=False, default="rule")
    collected_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Run(db.Model):
    """데이터 업데이트 한 번 (항목 · 결과 · 쓴 양)."""
    __tablename__ = "insights_runs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(10), nullable=False, default="running")  # running · done · partial · failed
    items = db.Column(db.Text, nullable=False, default="[]")
    results = db.Column(db.Text, nullable=False, default="{}")
    current = db.Column(db.String(20), nullable=False, default="")
    usage = db.Column(db.Text, nullable=False, default="{}")             # {serpapi, firecrawl, youtube, openai}
    user_name = db.Column(db.String(100), nullable=False, default="")


class AICache(db.Model):
    """AI 결과 저장 — 같은 입력이면 다시 부르지 않음 (비용 없음)."""
    __tablename__ = "insights_ai_cache"

    key = db.Column(db.String(64), primary_key=True)
    kind = db.Column(db.String(40), nullable=False)
    data = db.Column(db.Text, nullable=False, default="{}")
    model = db.Column(db.String(80), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Monthly(db.Model):
    """월간 분석 (AI 요약 · 전략). 월마다 한 줄, 다시 돌리면 바뀜."""
    __tablename__ = "insights_monthly"

    month = db.Column(db.String(7), primary_key=True)
    data = db.Column(db.Text, nullable=False, default="{}")
    model = db.Column(db.String(80), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_by = db.Column(db.String(100), nullable=False, default="")


class Competitor(db.Model):
    """경쟁사 한 곳 — 진열대에서 새 브랜드가 보이면 등록, 분석은 리뷰 상위 15개 브랜드만 (README '경쟁사 분석')."""
    __tablename__ = "insights_competitors"

    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(120), nullable=False, unique=True)
    tier = db.Column(db.String(12), nullable=False, default="category")   # direct · category · substitute
    status = db.Column(db.String(10), nullable=False, default="active")   # active · archived
    keywords = db.Column(db.Text, nullable=False, default="[]")           # 처음 · 다시 발견된 검색어
    website = db.Column(db.String(500), nullable=False, default="")
    analysis = db.Column(db.Text, nullable=False, default="{}")           # 7개 항목 (AI) · 비어 있으면 아직 분석 전
    sources = db.Column(db.Text, nullable=False, default="[]")            # [{id, type, title, url}]
    model = db.Column(db.String(80), nullable=False, default="")
    first_seen = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen = db.Column(db.DateTime, nullable=False, default=utcnow)
    researched_at = db.Column(db.DateTime)


class CompetitorHistory(db.Model):
    """다시 조사했을 때 바뀌기 전 분석 + 바뀐 항목."""
    __tablename__ = "insights_competitor_history"

    id = db.Column(db.Integer, primary_key=True)
    competitor_id = db.Column(db.Integer, nullable=False, index=True)
    analysis = db.Column(db.Text, nullable=False, default="{}")
    changed = db.Column(db.Text, nullable=False, default="[]")
    researched_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class ProductPosition(db.Model):
    """매트릭스 점 하나 = 상품 하나 — 제형(규칙) · 섭취 타이밍(AI 또는 규칙) · 웰니스."""
    __tablename__ = "insights_product_positions"

    key = db.Column(db.String(200), primary_key=True)                     # 진열대 key (amazon:<ASIN> …)
    brand = db.Column(db.String(120), nullable=False, default="")
    form = db.Column(db.String(10), nullable=False, default="")           # liquid · solid · "" (모름)
    timing = db.Column(db.String(10), nullable=False, default="")         # pre · post · both · "" (모름)
    wellness = db.Column(db.Boolean, nullable=False, default=False)
    classified_by = db.Column(db.String(10), nullable=False, default="rule")
    sig = db.Column(db.String(40), nullable=False, default="")            # 상품명 · 문구가 바뀌면 다시 분류
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class KV(db.Model):
    """작은 상태값: 주시 브랜드 · Weee! 원문 일부 · 아마존 리뷰 요약."""
    __tablename__ = "insights_kv"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="null")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_by = db.Column(db.String(100), nullable=False, default="")


def kv_get(key, default):
    row = db.session.get(KV, key)
    return load_json(row.value, default) if row else default


def kv_set(key, value, user=""):
    row = db.session.get(KV, key) or KV(key=key)
    row.value = json.dumps(value, ensure_ascii=False, default=str)
    row.updated_at, row.updated_by = utcnow(), (user or "")[:100]
    db.session.add(row)
    db.session.commit()
