"""Yami 진열대 (Firecrawl → 검색 결과 마크다운에서 브랜드 · 상품 · 가격 · 판매량 읽기).

- 검색어: data/competitor_keywords.json의 yami_queries (첫 페이지 약 60개)
- 한 상품 = "[브랜드](…/b/…) [상품명](…/p/<이름>/<번호>)" 다음 줄들의 "N+ Sold" · "$판매가$정가" · "Sold Out"
- robots.txt: 검색 · 상품 페이지 허용, 리뷰(/comment/) · API는 막혀 있어 읽지 않음 (2026-10-24 확인)
- 숙취와 관계없는 상품(제목에 숙취 · 음주 · 간 관련 단어가 없음)은 넣지 않음
"""
import json
import re
from urllib.parse import quote

from server.config import ROOT
from . import store
from .util import clean, has_any
from .weee import firecrawl

SEARCH = "https://www.yami.com/us/en/search?q={q}"
KEYWORDS_FILE = ROOT / "tabs" / "insights" / "data" / "competitor_keywords.json"
ITEM_RE = re.compile(r"\[([^\[\]]{1,80})\]\(https://www\.yami\.com/us/en/b/[^)\s]+\)\s*"
                     r"\[((?:[^\[\]]|\\\[|\\\])+?)\]\((https://www\.yami\.com/us/en/p/[^/\s)]+/(\d+))[^)]*\)")
PRICE_RE = re.compile(r"\$(\d{1,4}(?:\.\d{1,2})?)(?:\$(\d{1,4}(?:\.\d{1,2})?))?")
SOLD_RE = re.compile(r"(\d[\d,]*\+?) +Sold\b(?! by)")  # "Options: 4\n\nSold by …"(판매자)와 구분
OOS_RE = re.compile(r"sold\s*out|out of stock", re.I)
RELEVANT_RE = re.compile(r"hangover|alcohol|drinking|liver|sober|after party|숙취|해장", re.I)  # 'drink'만으로는 음료 전체가 걸림


def queries():
    try:
        data = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(q).strip() for q in data.get("yami_queries") or [] if str(q).strip()][:5]


def _unescape(s):
    return re.sub(r"\\([\\*\[\]_])", r"\1", s)


def parse_search(md):
    """검색 결과 마크다운 → [{id, brand, title, link, price, list_price, sold, in_stock}] (나온 순서)."""
    found = list(ITEM_RE.finditer(md or ""))
    out, seen = [], set()
    for i, m in enumerate(found):
        pid = m.group(4)
        if pid in seen:
            continue
        seen.add(pid)
        tail = md[m.end(): found[i + 1].start() if i + 1 < len(found) else m.end() + 800]
        pm = PRICE_RE.search(tail)
        price = float(pm.group(1)) if pm else None
        list_price = float(pm.group(2)) if pm and pm.group(2) else None
        sold = SOLD_RE.search(tail)
        out.append({"id": pid, "brand": clean(_unescape(m.group(1)), 120), "title": clean(_unescape(m.group(2)), 300),
                    "link": m.group(3), "price": price, "list_price": list_price,
                    "sold": sold.group(1) if sold else "", "in_stock": False if OOS_RE.search(tail[:400]) else (True if pm else None)})
    return out


def collect(cfg, key, week):
    """→ {count, calls, warnings}. 키 · 크레딧 문제는 CollectError 그대로."""
    own_terms, exclude = cfg.get("own_terms") or [], cfg.get("exclude_terms") or []
    items, calls, warnings = {}, 0, []
    for q in queries():
        data = firecrawl(key, {"url": SEARCH.format(q=quote(q)), "formats": ["markdown"], "onlyMainContent": True,
                               "waitFor": 3000})
        calls += 1
        found = parse_search(str(data.get("markdown") or ""))
        if not found:
            warnings.append(f"Yami '{q}' 검색에서 상품을 찾지 못했어요.")
            continue
        for pos, it in enumerate(found, start=1):
            if not RELEVANT_RE.search(it["title"]) or has_any(it["title"], exclude):
                continue
            k = f"yami:{it['id']}"
            if k in items:
                items[k]["queries"].append(q)
                continue
            sale = (f"정가 ${it['list_price']:.2f}" if it["list_price"] and it["price"] and it["list_price"] > it["price"] else "")
            items[k] = {"key": k, "position": pos, "queries": [q], "title": it["title"], "brand": it["brand"],
                        "price": it["price"], "price_text": f"${it['price']:.2f}" if it["price"] is not None else "",
                        "in_stock": it["in_stock"], "availability": " · ".join(x for x in (f"{it['sold']} Sold" if it["sold"] else "", sale) if x),
                        "link": it["link"], "message": "", "detailed": False,
                        "own": has_any(f"{it['title']} {it['brand']}", own_terms)}
    store.save_shelf(week, "yami", list(items.values()))
    return {"count": len(items), "calls": calls, "warnings": warnings}
