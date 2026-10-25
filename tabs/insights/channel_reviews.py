"""채널 리뷰 — 알디콤이 입점한 채널(Olive Young Global · Amazon · Yami)의 구매자 리뷰.

- 상품 주소: data/review_sources.json. 포장 · 개수가 달라도 제품 라인(알디콤 A · V · Plus · 부스트) 태그로 묶어 봄
- Olive Young: 영어 리뷰(Global Reviews 탭)만. 한국 리뷰 탭은 누르지 않음
  rawHtml에서 별점(채운 반쪽 별 수 ÷ 2) · 본문 · 날짜를 읽고, 리뷰가 10개를 넘으면 'More' 버튼을 (리뷰 수 ÷ 10 − 1)번 눌러 끝까지 받음
  (Firecrawl 'click' 동작은 이 버튼에 먹지 않아 executeJavascript로 누름 — 2026-10 실측)
  영어 리뷰가 0개인 상품은 사이트가 한국 리뷰를 대신 보여 주므로 아무것도 읽지 않음. 읽은 수도 영어 리뷰 수를 넘지 않게 자름
- Amazon: SerpApi 상품 조회의 대표 리뷰(최대 8개) + 별점 · 평가 수. 아마존 전체 리뷰 목록은 로그인해야 보여서 대표 리뷰만
  (매주 대표 리뷰가 바뀌면 새 것만 더해짐). 진열대 수집이 같은 리뷰를 경쟁사로 먼저 넣었어도 자사로 바꿔 둠
- Yami: 리뷰가 없으면 'containerNoComment' 표시만 확인 (1크레딧). 있으면 Firecrawl JSON 추출로 읽음
- 작성자 이름은 저장하지 않음. 전체 리뷰 수 · 평균 별점은 kv 'channel_review_stats'
"""
import html
import json
import math
import re

from server.config import ROOT
from server.extensions import db
from server.models import iso, utcnow
from . import amazon, store
from .http import CollectError
from .models import Review, kv_get, kv_set
from .util import clean, to_float, to_int
from .weee import firecrawl

SOURCES_FILE = ROOT / "tabs" / "insights" / "data" / "review_sources.json"
CHANNEL_LABEL = {"oliveyoung": "Olive Young", "amazon": "Amazon", "yami": "Yami"}
LINES = ("알디콤 A", "알디콤 V", "알디콤 Plus", "부스트 그린", "부스트 핑크", "부스트")
PAGE_SIZE, MAX_CLICKS = 10, 20
OY_MORE_JS = "var b=document.querySelector('.review-list-more-btn'); if(b){b.scrollIntoView(); b.click();}"
JSON_CREDITS = 5  # Firecrawl JSON 추출은 일반 수집(1)보다 비쌈 (대략)

OY_COUNT_RE = re.compile(r"Global Reviews\s*\(\s*(\d[\d,]*)\s*\)", re.I)
OY_AVG_RE = re.compile(r"(\d(?:\.\d)?)\s+(\d[\d,]*)\s+reviews\b", re.I)
OY_UNIT = 'class="product-review-unit-main'
OY_STAR_RE = re.compile(r'class="icon-star[^"]*\bfilled\b')
OY_TEXT_RE = re.compile(r'class="review-unit-cont-comment[^"]*"[^>]*>(.*?)</div>', re.S)
OY_DATE_RE = re.compile(r'class="review-write-info-date[^"]*"[^>]*>\s*(\d{4}/\d{2}/\d{2})')
OY_OPTION_RE = re.compile(r'class="review-unit-option[^"]*"[^>]*>\s*<span>(.*?)</span>', re.S)
TAG_RE = re.compile(r"<[^>]+>")

YAMI_SCHEMA = {
    "type": "object",
    "properties": {
        "total_reviews": {"type": ["integer", "null"]},
        "average_rating": {"type": ["number", "null"]},
        "reviews": {"type": "array", "items": {"type": "object", "properties": {
            "rating": {"type": ["number", "null"]}, "date": {"type": "string"}, "text": {"type": "string"}},
            "required": ["text"]}},
    },
    "required": ["reviews"],
}
YAMI_PROMPT = ("From the product page's customer reviews section only, list every customer review: star rating (1-5), "
               "date as shown, and review text exactly as written. Do not include reviewer names. "
               "If there are no reviews, return an empty list.")


def product_line(title):
    """상품 이름 → 제품 라인 태그 (포장 · 개수 무시). 이름에 V · Plus · Boost가 없으면 알디콤 A."""
    t = str(title or "").lower()
    if "boost" in t or "부스트" in t:
        if "green" in t or "그린" in t:
            return "부스트 그린"
        if "pink" in t or "핑크" in t:
            return "부스트 핑크"
        return "부스트"
    if re.search(r"\bplus\b|플러스", t):
        return "알디콤 Plus"
    if re.search(r"\bv\b", t):
        return "알디콤 V"
    return "알디콤 A"


def load_sources():
    try:
        data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data.get("products") or [] if p.get("channel") in CHANNEL_LABEL and (
        amazon.ASIN_RE.match(str(p.get("asin", ""))) if p.get("channel") == "amazon" else str(p.get("url", "")).startswith("https://"))]


def _text(fragment, n=5000):
    s = re.sub(r"<br\s*/?>", "\n", fragment or "", flags=re.I)
    return clean(html.unescape(TAG_RE.sub(" ", s)), n)


def _page_title(data):
    t = str((data.get("metadata") or {}).get("title") or "")
    return clean(t.split(" | ")[0], 500)


def parse_oliveyoung(raw):
    """Olive Young 상품 페이지 rawHtml → {total, avg, reviews:[{rating, text, date, option}]} (영어 리뷰 탭만 보이는 상태)."""
    plain = html.unescape(TAG_RE.sub(" ", raw or ""))
    m = OY_COUNT_RE.search(plain)
    total = to_int(m.group(1).replace(",", "")) if m else None
    m = OY_AVG_RE.search(plain)
    avg = to_float(m.group(1)) if m else None
    reviews = []
    for part in (raw or "").split(OY_UNIT)[1:]:
        tm = OY_TEXT_RE.search(part)
        text = _text(tm.group(1)) if tm else ""
        if not text:
            continue
        head = part.split("review-unit-option")[0]  # 첫 별점 줄 = 전체 별점 (옵션 아래 별은 항목별 별점)
        filled = len(OY_STAR_RE.findall(head))
        dm = OY_DATE_RE.search(part)
        om = OY_OPTION_RE.search(part)
        reviews.append({"rating": filled / 2 if filled else None, "text": text, "date": dm.group(1) if dm else "",
                        "option": _text(om.group(1), 300) if om else ""})
    return {"total": total, "avg": avg, "reviews": reviews}


def _oliveyoung(key, url):
    data = firecrawl(key, {"url": url, "formats": ["rawHtml"], "waitFor": 3000})
    calls, warnings = 1, []
    p = parse_oliveyoung(str(data.get("rawHtml") or ""))
    title = _page_title(data)
    total = p["total"] or 0
    if not total:  # 영어 리뷰 없음 → 화면에 보이는 건 한국 리뷰
        p["reviews"] = []
    if total > len(p["reviews"]) and p["reviews"]:
        clicks = min(math.ceil(total / PAGE_SIZE) - 1, MAX_CLICKS)
        actions = [{"type": "wait", "milliseconds": 5000}]
        for _ in range(clicks):
            actions += [{"type": "executeJavascript", "script": OY_MORE_JS}, {"type": "wait", "milliseconds": 3000}]
        try:
            more = firecrawl(key, {"url": url, "formats": ["rawHtml"], "actions": actions})
            p2 = parse_oliveyoung(str(more.get("rawHtml") or ""))
            if len(p2["reviews"]) > len(p["reviews"]):
                p["reviews"] = p2["reviews"]
        except CollectError as e:
            if "키" in str(e) or "크레딧" in str(e):
                raise
            warnings.append(f"Olive Young '{title[:40]}' 더 보기 실패 — 첫 페이지 {len(p['reviews'])}개만 ({e})")
        finally:
            calls += 1
        if len(p["reviews"]) < total and not warnings:
            warnings.append(f"Olive Young '{title[:40]}' 영어 리뷰 {total}개 중 {len(p['reviews'])}개만 읽었어요 (다음 업데이트 때 나머지를 다시 시도해요).")
    m = re.search(r"prdtNo=([A-Za-z0-9]+)", url)
    return {"id": m.group(1) if m else url[-40:], "title": title, "total": p["total"], "avg": p["avg"] if total else None,
            "reviews": p["reviews"][:total], "calls": calls, "credits": calls, "warnings": warnings}


def _yami(key, url):
    data = firecrawl(key, {"url": url, "formats": ["rawHtml"], "waitFor": 3000})
    raw = str(data.get("rawHtml") or "")
    m = re.search(r"/(\d{6,})(?:[/?#]|$)", url)
    out = {"id": m.group(1) if m else url[-40:], "title": _page_title(data), "total": 0, "avg": None, "reviews": [],
           "calls": 1, "credits": 1, "warnings": []}
    if "containerNoComment" in raw:
        return out
    if "pdp-comment-section" not in raw:
        out["total"] = None
        out["warnings"].append("Yami 리뷰 영역을 찾지 못했어요 (페이지 모양이 바뀌었을 수 있어요).")
        return out
    j = firecrawl(key, {"url": url, "formats": ["json"], "waitFor": 3000,
                        "jsonOptions": {"schema": YAMI_SCHEMA, "prompt": YAMI_PROMPT}}).get("json") or {}
    out["calls"] += 1
    out["credits"] += JSON_CREDITS
    for r in j.get("reviews") or []:
        text = clean(r.get("text"), 5000) if isinstance(r, dict) else ""
        if text:
            out["reviews"].append({"rating": to_float(r.get("rating")), "text": text, "date": clean(r.get("date"), 80)})
    out["total"] = to_int(j.get("total_reviews")) if j.get("total_reviews") is not None else len(out["reviews"])
    out["avg"] = to_float(j.get("average_rating"))
    return out


def _amazon(serp_key, asin):
    det = amazon.product(serp_key, asin)
    if det is None:
        raise CollectError("SerpApi가 상품을 찾지 못했어요.")
    return {"id": asin, "title": det["title"], "total": det["reviews"], "avg": det["rating"], "calls": 1, "credits": 0,
            "warnings": [], "reviews": [{"rating": r["rating"], "title": r["title"], "text": r["text"], "date": r["date_text"]}
                                        for r in det["review_rows"]]}


def collect(key, week, serp_key=None):
    """→ {count, reviews(새로 넣은 수), calls, credits(Firecrawl), serpapi, warnings}. 키 · 크레딧 문제나 전부 실패면 CollectError.
    serp_key가 없으면 Amazon은 건너뜀 (경고)."""
    sources = load_sources()
    if not sources:
        raise CollectError("리뷰를 모을 상품 주소가 없어요 (tabs/insights/data/review_sources.json).")
    stats = kv_get("channel_review_stats", {})
    rows, calls, credits, serp, warnings, ok = [], 0, 0, 0, [], 0
    for src in sources:
        ch = src["channel"]
        url = f"https://www.amazon.com/dp/{src['asin']}" if ch == "amazon" else src["url"]
        if ch == "amazon" and not serp_key:
            warnings.append("SerpApi 키가 없어 Amazon 리뷰는 건너뛰었어요.")
            continue
        try:
            if ch == "amazon":
                serp += 1
                res = _amazon(serp_key, src["asin"])
            else:
                res = (_oliveyoung if ch == "oliveyoung" else _yami)(key, url)
        except CollectError as e:
            if ch != "amazon":
                calls, credits = calls + 1, credits + 1
            if "키" in str(e) or "크레딧" in str(e) or "모두 썼" in str(e):
                raise
            warnings.append(f"{CHANNEL_LABEL[ch]} {url[-24:]}: {e}")
            continue
        ok += 1
        calls, credits = calls + res["calls"], credits + res["credits"]
        warnings += res["warnings"]
        pkey = f"{ch}:{res['id']}"
        stats[pkey] = {"channel": ch, "title": res["title"], "line": product_line(res["title"]), "url": url,
                       "total": res["total"], "avg": res["avg"], "got": len(res["reviews"]), "at": iso(utcnow())}
        for r in res["reviews"]:
            rows.append({"source": ch, "product_key": pkey, "product_title": res["title"], "brand": "Aldicom", "own": True,
                         "rating": r["rating"], "title": r.get("title", ""), "text": r["text"], "date_text": r["date"]})
    if not ok:
        raise CollectError(warnings[0] if warnings else "채널 리뷰를 하나도 읽지 못했어요.")
    kv_set("channel_review_stats", stats)
    added = store.save_reviews(rows, week)
    keys = {r["product_key"] for r in rows}
    if keys:  # 진열대 수집이 경쟁사(own=False)로 먼저 넣은 같은 리뷰도 자사로
        Review.query.filter(Review.product_key.in_(keys), Review.own.is_(False)).update({"own": True}, synchronize_session=False)
        db.session.commit()
    return {"count": len(rows), "reviews": added, "calls": calls, "credits": credits, "serpapi": serp, "warnings": warnings}
