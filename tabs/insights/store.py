"""수집 결과를 표에 넣기 (진열대 주차 묶음 · 리뷰 중복 제거)."""
import json

from server.extensions import db
from server.models import utcnow
from . import classify
from .models import Review, ShelfItem
from .util import clean, parse_servings, short_hash

SHELF_FIELDS = ("position", "queries", "title", "brand", "price", "price_text", "rating", "reviews", "availability",
                "in_stock", "link", "message", "own", "tracked", "detailed")


def save_shelf(week, source, items):
    """같은 주차 · 판매처 묶음을 새로 바꿈 (같은 주에 다시 모으면 마지막 결과). 회분 수는 상품명 · 포장 문구에서 읽음."""
    ShelfItem.query.filter_by(week=week, source=source).delete(synchronize_session=False)
    now = utcnow()
    for it in items:
        row = ShelfItem(week=week, source=source, key=it["key"][:200], collected_at=now)
        for k in SHELF_FIELDS:
            if k in it and it[k] is not None:
                v = it[k]
                if k == "queries" and isinstance(v, (list, tuple)):
                    v = ", ".join(v)
                setattr(row, k, v)
        n, rule = parse_servings(" ".join(str(it.get(k) or "") for k in ("title", "pack_text")))
        row.servings, row.servings_rule = n, rule
        row.title = clean(row.title, 500)
        row.brand = clean(row.brand, 120)
        row.price_text = clean(row.price_text, 60)
        row.availability = clean(row.availability, 200)
        row.link = str(row.link or "")[:800]
        db.session.add(row)
    db.session.commit()
    return len(items)


def save_reviews(rows, week):
    """리뷰 넣기 (같은 리뷰는 한 번만) → 새로 넣은 개수. 규칙 분류를 바로 붙임 (AI 분류는 '리뷰 분류' 항목에서)."""
    new = 0
    seen = set()
    for r in rows:
        text = clean(r.get("text"), 5000)
        if not text:
            continue
        uid = short_hash(r.get("source"), r.get("product_key"), clean(r.get("title"), 300), text[:400], r.get("date_text", ""))
        if uid in seen or db.session.query(Review.id).filter_by(uid=uid).first() is not None:
            continue
        seen.add(uid)
        rating = r.get("rating")
        topics = classify.review_topics(f"{r.get('title', '')} {text}")
        db.session.add(Review(
            uid=uid, source=r.get("source", ""), product_key=str(r.get("product_key") or "")[:200],
            product_title=clean(r.get("product_title"), 500), brand=clean(r.get("brand"), 120), own=bool(r.get("own")),
            rating=rating, title=clean(r.get("title"), 300), text=text, date_text=clean(r.get("date_text"), 80),
            topics=json.dumps(topics, ensure_ascii=False), sentiment=classify.review_sentiment(text, rating),
            classified_by="rule", week=week))
        new += 1
    db.session.commit()
    return new
