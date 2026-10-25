"""Amazon 진열대 · 대표 리뷰 (SerpApi).

- 검색: engine=amazon · 검색어마다 광고(sponsored)를 뺀 상위 8개
- 상세: engine=amazon_product · 리뷰 많은 상품 N개 + 추적 ASIN → 재고 · 브랜드 · 판매 문구 · 대표 리뷰
  (제품 선택 · 상품 정보의 Item Form이 있으면 판매 문구 맨 앞에 'Item Form: …'으로 붙임 → 제형 판단용)
- 응답 모양이 조금씩 달라서 여러 이름을 차례로 확인합니다 (못 찾으면 빈 값)
"""
import re

from . import http, store
from .http import CollectError
from .models import kv_get, kv_set
from .util import clean, has_any, parse_price, to_float, to_int

SERP = "https://serpapi.com/search.json"
TOP = 8
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
OUT_WORDS = ("out of stock", "currently unavailable", "unavailable", "sold out", "품절")
IN_WORDS = ("in stock", "left in stock", "available to ship")


def _ok(status, data):
    """SerpApi 응답 확인 → 결과가 있으면 True, '결과 없음'이면 False, 키 · 한도 문제는 CollectError."""
    err = str((data or {}).get("error") or "") if isinstance(data, dict) else ""
    low = err.lower()
    if err:
        if "hasn't returned any results" in low or "no results" in low:
            return False
        if "run out of searches" in low or status == 429:
            raise CollectError("SerpApi 이번 달 검색을 모두 썼어요 (무료 월 250회).")
        if status in (401, 403) or "api key" in low or "invalid" in low:
            raise CollectError("SerpApi가 키를 거절했어요. 설정 › 연결에서 키를 확인하세요.")
        raise CollectError(f"SerpApi가 요청을 처리하지 못했어요 ({status}).")
    if status != 200 or not isinstance(data, dict):
        raise CollectError(f"SerpApi 응답 오류 ({status}).")
    return True


def stock_state(text):
    t = str(text or "").lower()
    if not t:
        return None
    if any(w in t for w in OUT_WORDS):
        return False
    if any(w in t for w in IN_WORDS) or re.search(r"only \d+ left", t):
        return True
    return None


def _brand(v):
    s = clean(v, 120)
    s = re.sub(r"^(visit the|brand:)\s+", "", s, flags=re.I)
    s = re.sub(r"\s+store$", "", s, flags=re.I)
    return s


def parse_search_item(r):
    asin = str(r.get("asin") or "").strip().upper()
    price = parse_price(r.get("extracted_price"))
    if price is None:
        price = parse_price(r.get("price"))
    if price is None and isinstance(r.get("prices"), list) and r["prices"]:
        price = parse_price(r["prices"][0])
    price_text = r.get("price") if isinstance(r.get("price"), str) else (f"${price:.2f}" if price is not None else "")
    return {
        "asin": asin, "key": f"amazon:{asin}", "title": clean(r.get("title"), 500), "brand": _brand(r.get("brand")),
        "price": price, "price_text": clean(price_text, 60), "rating": to_float(r.get("rating")),
        "reviews": to_int(r.get("reviews") if r.get("reviews") is not None else r.get("ratings_total")),
        "availability": clean(r.get("availability") or r.get("stock") or "", 200),
        "in_stock": stock_state(r.get("availability") or r.get("stock")),
        "link": r.get("link_clean") or r.get("link") or f"https://www.amazon.com/dp/{asin}",
    }


def search(key, query):
    status, data = http.get_json("SerpApi", SERP, params={
        "engine": "amazon", "k": query, "amazon_domain": "amazon.com", "api_key": key})
    if not _ok(status, data):
        return []
    out = []
    for r in data.get("organic_results") or []:
        if not isinstance(r, dict) or r.get("sponsored") or r.get("is_sponsored"):
            continue
        item = parse_search_item(r)
        if not ASIN_RE.match(item["asin"]):
            continue
        out.append(item)
        if len(out) >= TOP:
            break
    return out


def _first_list(*vals):
    for v in vals:
        if isinstance(v, list) and v:
            return v
    return []


FORM_KEY_RE = re.compile(r"^(item[\s_]*form|product[\s_]*form|form)$", re.I)


def _item_form(data, pr):
    """'제품 선택' · 상품 정보 표의 Item Form (예: Tablet). 응답 모양이 달라 여러 곳을 봄, 못 찾으면 ''."""
    for v in (pr.get("variants"), data.get("variants")):   # [{"title": "Item Form", "items": [{"name", "selected"}]}]
        for g in v if isinstance(v, list) else []:
            if isinstance(g, dict) and FORM_KEY_RE.match(str(g.get("title") or "").strip().rstrip(":")):
                items = [i for i in g.get("items") or [] if isinstance(i, dict)]
                sel = next((i for i in items if i.get("selected")), items[0] if len(items) == 1 else None)
                if sel and sel.get("name"):
                    return clean(sel["name"], 60)
    for v in (data.get("product_details"), pr.get("product_details"), data.get("item_specifications"),
              data.get("specifications"), data.get("product_information"), pr.get("item_specifications")):
        if isinstance(v, dict):
            pairs = v.items()
        elif isinstance(v, list):   # [{"name": "Item Form", "value": "Tablet"}]
            pairs = [(r.get("name") or r.get("title"), r.get("value")) for r in v if isinstance(r, dict)]
        else:
            continue
        for k, val in pairs:
            if FORM_KEY_RE.match(str(k or "").strip().rstrip(":")) and isinstance(val, str) and val.strip():
                return clean(val, 60)
    return ""


def _reviews(data):
    info = data.get("reviews_information") if isinstance(data.get("reviews_information"), dict) else {}
    rows = _first_list(info.get("authors_reviews"), info.get("reviews"), data.get("authors_reviews"), data.get("top_reviews"),
                       data.get("reviews") if isinstance(data.get("reviews"), list) else None)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        text = r.get("text") or r.get("body") or r.get("content") or r.get("review") or ""
        if not str(text).strip():
            continue
        out.append({"title": clean(r.get("title"), 300), "text": clean(text, 5000),
                    "rating": to_float(r.get("rating") if r.get("rating") is not None else r.get("stars")),
                    "date_text": clean(r.get("date") or r.get("review_date") or "", 80)})
        if len(out) >= 8:
            break
    summary = info.get("summary")
    if isinstance(summary, dict):
        summary = summary.get("text") or summary.get("snippet") or ""
    if not summary:
        summary = (data.get("ai_summary") or {}).get("text") if isinstance(data.get("ai_summary"), dict) else ""
    return out, clean(summary, 1500)


def product(key, asin):
    status, data = http.get_json("SerpApi", SERP, params={
        "engine": "amazon_product", "asin": asin, "amazon_domain": "amazon.com", "api_key": key})
    if not _ok(status, data):
        return None
    pr = data.get("product_results") if isinstance(data.get("product_results"), dict) else {}
    price = parse_price(pr.get("extracted_price"))
    if price is None:
        price = parse_price(pr.get("price"))
    stock_text = pr.get("stock") or pr.get("availability") or data.get("stock") or ""
    if isinstance(stock_text, dict):
        stock_text = stock_text.get("text") or ""
    bullets = _first_list(data.get("about_item"), pr.get("about_item"), pr.get("feature_bullets"), data.get("feature_bullets"))
    reviews, summary = _reviews(data)
    form = _item_form(data, pr)   # 판매 문구 앞에 붙여 저장 → competitors.rule_form_of가 상품명 다음으로 봄
    bullets = [f"Item Form: {form}"] + bullets[:3] if form else bullets
    return {
        "asin": asin, "title": clean(pr.get("title"), 500), "brand": _brand(pr.get("brand")), "price": price,
        "price_text": clean(pr.get("price") if isinstance(pr.get("price"), str) else "", 60),
        "rating": to_float(pr.get("rating")), "reviews": to_int(pr.get("reviews") if pr.get("reviews") is not None else pr.get("ratings_total")),
        "availability": clean(stock_text, 200), "in_stock": stock_state(stock_text),
        "message": clean(" / ".join(str(b) for b in bullets[:4 if form else 3] if b), 1200),
        "review_rows": reviews, "summary": summary,
    }


def collect(cfg, key, week):
    """→ {count, calls, reviews, warnings}. 검색이 실패하면 CollectError (화면에 그대로)."""
    own_terms, exclude = cfg.get("own_terms") or [], cfg.get("exclude_terms") or []
    tracked = {a["asin"]: a for a in cfg.get("tracked_asins") or [] if a.get("asin")}
    items, calls, warnings = {}, 0, []
    for q in cfg.get("amazon_queries") or []:
        found = search(key, q)
        calls += 1
        for pos, it in enumerate(found, start=1):
            if has_any(it["title"], exclude):
                continue
            cur = items.get(it["asin"])
            if cur:
                cur["queries"].append(q)
                continue
            it.update(position=pos, queries=[q])
            items[it["asin"]] = it
    ranked = sorted(items.values(), key=lambda x: -(x.get("reviews") or 0))[: int(cfg.get("amazon_product_lookups") or 0)]
    lookups = [x["asin"] for x in ranked] + [a for a in tracked if a not in {x["asin"] for x in ranked}]
    review_rows, summaries = [], kv_get("amazon_summaries", {})
    for asin in lookups:
        try:
            det = product(key, asin)
        except CollectError as e:
            if "키" in str(e) or "모두 썼" in str(e):
                warnings.append(str(e))
                break
            warnings.append(f"{asin}: {e}")
            continue
        finally:
            calls += 1
        if det is None:
            continue
        it = items.get(asin)
        if it is None:  # 검색에 안 나온 추적 ASIN
            it = {"asin": asin, "key": f"amazon:{asin}", "position": None, "queries": ["추적 ASIN"],
                  "link": f"https://www.amazon.com/dp/{asin}"}
            items[asin] = it
        for k in ("title", "brand", "price", "price_text", "rating", "reviews", "availability", "in_stock", "message"):
            if det.get(k) not in (None, ""):
                it[k] = det[k]
        it["detailed"] = True
        if det["summary"]:
            summaries[asin] = {"title": it.get("title", ""), "text": det["summary"]}
        for r in det["review_rows"]:
            review_rows.append(dict(r, source="amazon", product_key=it["key"], product_title=it.get("title", ""),
                                    brand=it.get("brand", ""), own=False))
    for asin, it in items.items():
        t = tracked.get(asin)
        it["tracked"] = t is not None
        it["own"] = bool(t and t.get("own")) or has_any(f"{it.get('title', '')} {it.get('brand', '')}", own_terms)
    own_keys = {it["key"] for it in items.values() if it["own"]}
    for r in review_rows:
        r["own"] = r["product_key"] in own_keys
    store.save_shelf(week, "amazon", list(items.values()))
    new_reviews = store.save_reviews(review_rows, week)
    kv_set("amazon_summaries", summaries)
    return {"count": len(items), "calls": calls, "reviews": new_reviews, "warnings": warnings}
