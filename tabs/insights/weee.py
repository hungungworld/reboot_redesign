"""Weee! 진열대 (Firecrawl → 마크다운에서 상품 · 가격 · 품절 읽기, 최선 노력).

- 목록 페이지: 상품 주소(/product/<이름>/<번호>)마다 바로 뒤의 첫 $가격 · 'Sold out' 여부
  지금 Weee!는 상품 카드 전체가 링크라서 [이미지 · 가격 · 상품명 · 배송](주소) 모양 → 링크 글자 안에서 읽음
- 상품 페이지(주소에 /product/): 제목(# …) · 가격 · 품절 · 설명 앞부분(핵심 메시지)
- 읽기가 불안정할 수 있어 원문 앞부분을 저장해 두고, 진열대 › 표로 보기에서 가격 · 재고를 고칠 수 있게 함
- 사이트 주소가 sayweee.com → weee.com으로 바뀌어 둘 다 읽음
- 구매자 리뷰: 상품마다 공개 리뷰 페이지(/en/social/review/more/<번호>, robots.txt 허용)에서 'Reviews (N)'을 보고,
  N이 있으면 Firecrawl JSON 추출로 별점 · 날짜 · 본문(영어 번역)을 읽음. 작성자 이름은 저장하지 않음
  (api.weee.com 리뷰 API는 robots.txt의 */api/ 금지라 쓰지 않음. 2026-10-25 숙취 상품 22개 중 리뷰 있는 상품 3개)
"""
import re

from server.models import iso, utcnow
from . import http, store
from .http import CollectError
from .models import kv_get, kv_set
from .util import clean, has_any, to_float

FIRECRAWL = "https://api.firecrawl.dev/v1/scrape"
URL_RE = re.compile(r"https?://(?:www\.)?(?:say)?weee\.com/[^\s)\]\"'<>]*?/product/([^/\s)\]\"'<>]+)/(\d+)[^\s)\]\"'<>]*", re.I)
PRICE_RE = re.compile(r"\$\s?(\d{1,4}(?:\.\d{1,2})?)")
UNIT_PRICE_RE = re.compile(r"\$\s?\d{1,4}(?:\.\d{1,2})?\s*/")             # $1.26/fl.oz (단위 가격)
SOLD_OUT_RE = re.compile(r"sold\s*out|out of stock|품절|currently unavailable", re.I)
LINK_TEXT_RE = re.compile(r"\[([^\[\]]{2,300})\]\($")
IMG_ALT_RE = re.compile(r"!\[([^\[\]]{2,300})\]\([^()]*\)\]\($")
IMG_RE = re.compile(r"!\[([^\[\]]*)\]\([^()]*\)")
SPLIT_PRICE_RE = re.compile(r"^\$\s*\n+\s*(\d{1,4})\s*\n+\s*(\d{2})\s*$", re.M)
PACK_RE = re.compile(r"^\s*\d{1,3}\s*(?:sticks?|packs?|pouch(?:es)?|bags?|pcs|sachets?|bottles?|count)\b[^\n]{0,40}$", re.I | re.M)
RELEVANT_RE = re.compile(r"hangover|숙취|hovenia|헛개|liver|after[\s-]*(?:drink|party)|before[\s/-]*after|\bdhm\b|recovery", re.I)
REVIEW_PAGE = "https://www.weee.com/en/social/review/more/{}"
REVIEW_COUNT_RE = re.compile(r"Reviews\s*\(\s*(\d[\d,]*)\s*\)", re.I)
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"reviews": {"type": "array", "items": {"type": "object", "properties": {
        "rating": {"type": ["number", "null"]}, "date": {"type": "string"}, "text": {"type": "string"}},
        "required": ["text"]}}},
    "required": ["reviews"],
}
REVIEW_PROMPT = ("List every customer review on this page: star rating (1-5) if shown, date as shown, and the review text "
                 "in English (use the translated text when both original and translation are shown). Do not include reviewer names.")
JSON_CREDITS = 5  # Firecrawl JSON 추출은 일반 수집(1)보다 비쌈 (대략)
CARD_SKIP_RE = re.compile(r"^(\d+% off|ships from|get it |buy \$|free shipping|snap|ebt|add to cart|sold\s*out|\$)", re.I)


def scrape(key, url):
    return str(firecrawl(key, {"url": url, "formats": ["markdown"], "onlyMainContent": True}).get("markdown") or "")


def firecrawl(key, payload):
    """Firecrawl v1/scrape 한 번 → data(dict: markdown · rawHtml · json · metadata). 채널 리뷰 수집도 같이 씀."""
    status, data = http.post_json("Firecrawl", FIRECRAWL, payload, headers={"Authorization": f"Bearer {key}"})
    if status in (401, 403):
        raise CollectError("Firecrawl이 키를 거절했어요. 설정 › 연결에서 키를 확인하세요.")
    if status == 402:
        raise CollectError("Firecrawl 크레딧을 모두 썼어요.")
    if status == 429:
        raise CollectError("Firecrawl 요청이 많아요. 잠시 뒤 다시 시도하세요.")
    if status != 200 or not isinstance(data, dict) or data.get("success") is False:
        raise CollectError(f"Firecrawl이 페이지를 읽지 못했어요 ({status}).")
    return data.get("data") if isinstance(data.get("data"), dict) else {}


def _slug_title(slug):
    return clean(re.sub(r"[-_]+", " ", slug), 300)


def _title_before(md, start):
    before = md[max(0, start - 400):start]
    m = IMG_ALT_RE.search(before)
    alt = m.group(1) if m else ""
    m = LINK_TEXT_RE.search(before)
    text = m.group(1) if m and not m.group(1).startswith("!") else ""
    return clean(text or alt, 300)


def _link_text(md, start):
    """주소 바로 앞 '[ … ](' 의 글자 (안쪽 이미지 [](…) 괄호는 건너뜀). 링크가 아니면 None."""
    if md[max(0, start - 2):start] != "](":
        return None
    depth, i = 0, start - 3
    while i >= 0 and start - i < 3000:
        ch = md[i]
        if ch == "]":
            depth += 1
        elif ch == "[":
            if depth == 0:
                return md[i + 1:start - 2]
            depth -= 1
        i -= 1
    return None


def _card(text):
    """카드 링크 글자 → {title, price, sold_out} (가격이 없으면 카드가 아님 → None)."""
    alt = next((a for a in IMG_RE.findall(text) if a.strip()), "")
    body = IMG_RE.sub("", text).replace("\\*", "*").replace("\\_", "_")
    lines = [ln.strip(" \\\t") for ln in re.split(r"\\\\\s*\n|\n", body)]
    lines = [ln for ln in lines if ln]
    prices = [ln for ln in lines if PRICE_RE.match(ln) and not UNIT_PRICE_RE.match(ln)]
    if not prices:
        return None
    title = next((ln for ln in lines if not CARD_SKIP_RE.match(ln) and "$" not in ln and len(ln) > 3), "")
    if not title:
        title = re.sub(r"^weee_[a-z]+_", "", alt)  # 이미지 설명 'weee_<분류>_상품명'
    return {"title": clean(title, 300), "price": round(float(PRICE_RE.match(prices[0]).group(1)), 2),
            "sold_out": bool(SOLD_OUT_RE.search(body))}


def parse_listing(md):
    """목록 마크다운 → [{id, title, price, in_stock, link}] (주소가 나온 순서)."""
    marks = [(m.group(2), m.group(1), m.start(), m.end(), m.group(0)) for m in URL_RE.finditer(md)]
    out, order = {}, []
    for i, (pid, slug, start, end, link) in enumerate(marks):
        text = _link_text(md, start)
        card = _card(text) if text else None
        title = card["title"] if card else _title_before(md, start)
        it = out.get(pid)
        if it is None:
            it = {"id": pid, "slug": slug, "title": "", "price": None, "in_stock": None, "link": link.split("?")[0]}
            out[pid] = it
            order.append(pid)
        if title and (not it["title"] or len(title) > len(it["title"]) or card):
            it["title"] = title
        if card:  # 가격 · 품절이 카드 안에 있음 (뒤쪽은 다음 상품 카드)
            it["price"] = card["price"]
            tail = md[end:end + 200].split("\n[", 1)[0]
            if card["sold_out"] or SOLD_OUT_RE.search(tail):
                it["in_stock"] = False
            continue
        nxt = next((m[2] for m in marks[i + 1:] if m[0] != pid), len(md))
        seg = md[end:nxt]
        if it["price"] is None:
            pm = PRICE_RE.search(seg)
            if pm:
                it["price"] = round(float(pm.group(1)), 2)
        if SOLD_OUT_RE.search(seg):
            it["in_stock"] = False
    items = []
    for pid in order:
        it = out[pid]
        it["title"] = it["title"] or _slug_title(it["slug"])
        if it["in_stock"] is None and it["price"] is not None:
            it["in_stock"] = True
        items.append(it)
    return items


def parse_product_page(md, url):
    m = URL_RE.search(url)
    pid = m.group(2) if m else re.sub(r"\W+", "", url)[-20:]
    title = ""
    hm = re.search(r"^#\s+(.+)$", md, re.M)
    if hm:
        title = clean(hm.group(1), 300)
    after = md[hm.end():] if hm else md
    top = after.split("## Details", 1)[0][:4000]  # 아래쪽 '함께 본 상품'의 가격 · 품절은 빼고
    sm = SPLIT_PRICE_RE.search(top)               # 지금 Weee!: '$' · '35' · '30' 줄 나눔 = $35.30
    pm = None if sm else PRICE_RE.search(after)
    price = round(int(sm.group(1)) + int(sm.group(2)) / 100, 2) if sm else (round(float(pm.group(1)), 2) if pm else None)
    if not title:
        im = IMG_RE.search(top)
        title = clean(im.group(1), 300) if im and "zoom" not in im.group(1) else ""
    pack = PACK_RE.search(top)
    paras = [clean(p, 400) for p in re.split(r"\n\s*\n", after)
             if p.strip() and not p.strip().startswith(("!", "[", "#", "|", "$")) and len(p.strip()) > 30]
    return {"id": pid, "slug": m.group(1) if m else "", "title": title or (_slug_title(m.group(1)) if m else ""),
            "price": price, "pack_text": clean(pack.group(0), 80) if pack else "",
            "in_stock": False if SOLD_OUT_RE.search(top) else (True if price is not None else None),
            "link": url.split("?")[0], "message": paras[0] if paras else ""}


def relevant(title, own_terms=()):
    """검색 목록 상품이 숙취 관련인지 (검색어와 상관없는 젤리 · 잼 등이 섞여 나옴). 상품 페이지는 사람이 고른 것이라 검사 안 함."""
    from .lines import known_brand  # lines → competitors → weee 순서로 불러서 여기서
    return bool(RELEVANT_RE.search(title or "") or known_brand("", title) or has_any(title, own_terms))


def review_count(md):
    """리뷰 페이지 마크다운 → 'Reviews (N)'의 N (못 찾으면 None)."""
    m = REVIEW_COUNT_RE.search(md or "")
    return int(m.group(1).replace(",", "")) if m else None


def collect_reviews(key, week, items):
    """이번 주 진열대 상품의 Weee! 리뷰 → store.save_reviews (source='weee'). 리뷰가 없는 상품은 1크레딧만."""
    from .lines import known_brand
    rows, calls, credits, warnings, found = [], 0, 0, [], 0
    for it in items:
        pid = it["key"].split(":", 1)[1]
        url = REVIEW_PAGE.format(pid)
        try:
            md = str(firecrawl(key, {"url": url, "formats": ["markdown"], "waitFor": 3000}).get("markdown") or "")
            calls, credits = calls + 1, credits + 1
            n = review_count(md)
            if n is None:
                warnings.append(f"Weee! 리뷰 수를 찾지 못했어요: {it['title'][:40]}")
                continue
            if not n:
                continue
            j = firecrawl(key, {"url": url, "formats": ["json"], "waitFor": 3000,
                                "jsonOptions": {"schema": REVIEW_SCHEMA, "prompt": REVIEW_PROMPT}}).get("json") or {}
            calls, credits = calls + 1, credits + JSON_CREDITS
        except CollectError as e:
            if "키" in str(e) or "크레딧" in str(e):
                raise
            warnings.append(f"Weee! 리뷰를 읽지 못했어요: {it['title'][:40]} ({e})")
            continue
        known = known_brand("", it["title"])
        for r in j.get("reviews") or []:
            text = clean(r.get("text"), 5000) if isinstance(r, dict) else ""
            if not text:
                continue
            rating = to_float(r.get("rating"))
            found += 1
            rows.append({"source": "weee", "product_key": it["key"], "product_title": it["title"],
                         "brand": known["name"] if known else "", "own": it.get("own", False),
                         "rating": rating if rating is not None and 1 <= rating <= 5 else None,
                         "title": "", "text": text, "date_text": clean(r.get("date"), 80)})
    added = store.save_reviews(rows, week) if rows else 0
    return {"reviews": found, "added": added, "calls": calls, "credits": credits, "warnings": warnings}


def collect(cfg, key, week):
    own_terms, exclude = cfg.get("own_terms") or [], cfg.get("exclude_terms") or []
    raw = kv_get("weee_raw", {})
    items, calls, warnings = {}, 0, []
    for url in cfg.get("weee_urls") or []:
        try:
            md = scrape(key, url)
        except CollectError as e:
            calls += 1
            if "키" in str(e) or "크레딧" in str(e) or not items:
                raise
            warnings.append(str(e))
            continue
        calls += 1
        raw[url] = {"at": iso(utcnow()), "chars": len(md), "snippet": md[:3000]}
        is_product = "/product/" in url
        found = [parse_product_page(md, url)] if is_product else parse_listing(md)
        if not found or not found[0].get("title"):
            warnings.append(f"상품을 찾지 못했어요: {url[:80]} (원문 앞부분을 저장했어요)")
            continue
        for pos, it in enumerate(found, start=1):
            if has_any(it["title"], exclude) or not (is_product or relevant(it["title"], own_terms)):
                continue
            k = f"weee:{it['id']}"
            cur = items.get(k)
            row = {"key": k, "position": None if is_product else pos, "queries": [url], "title": it["title"],
                   "price": it["price"], "price_text": f"${it['price']:.2f}" if it["price"] is not None else "",
                   "in_stock": it["in_stock"], "availability": "Sold out" if it["in_stock"] is False else "",
                   "link": it["link"], "message": it.get("message", ""), "detailed": is_product, "pack_text": it.get("pack_text", ""),
                   "own": has_any(it["title"], own_terms)}
            if cur is None:
                items[k] = row
            elif is_product:  # 상품 페이지 값이 목록보다 정확
                cur.update({kk: v for kk, v in row.items() if v not in (None, "", []) and kk != "queries"})
                cur["queries"].append(url)
    kv_set("weee_raw", raw)
    store.save_shelf(week, "weee", list(items.values()))
    rv = collect_reviews(key, week, list(items.values()))
    return {"count": len(items), "calls": calls + rv["calls"], "credits": calls + rv["credits"],
            "reviews": rv["reviews"], "newReviews": rv["added"], "warnings": warnings + rv["warnings"]}
