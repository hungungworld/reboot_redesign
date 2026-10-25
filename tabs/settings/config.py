"""수집 설정 (설정 README 1장) — 인사이트가 무엇을, 어떤 기준으로 모을지.

    from tabs.settings.config import get_collect_config
    cfg = get_collect_config()          # 기본값 + 화면에서 고친 값 (항상 모든 항목이 있음)
    cfg["amazon_queries"]               # ['hangover', 'korean hangover']

- 바꾼 값은 다음 데이터 업데이트부터 반영 (이미 모은 데이터는 그대로)
- 시즌은 여기가 아니라 공용 시즌 API(/api/seasons)에 있음 (monitor 표시)
"""
import copy
import json
import re
from urllib.parse import urlsplit

from server.extensions import db
from server.models import iso, utcnow
from .models import CollectConfig

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
SET_IDS = ("A", "B", "K")
SET_LABELS = {"A": "제품 리뷰", "B": "숙취 일반", "K": "한국 숙취해소제"}
LANGS = ("en", "ko_en")

# 기본값 (코드에 둠). 화면에서 고친 항목만 DB에 저장되고 나머지는 이 값을 씀
DEFAULTS = {
    "search_sets": {
        "A": ["hangover jelly review", "hangover drink review", "korean hangover cure review"],
        "B": ["hangover remedy", "how to get rid of a hangover", "best hangover cure"],
        "K": ["korean hangover drink", "korean hangover jelly", "korean convenience store hangover"],
    },
    "min_views": 10000,
    "video_language": "en",
    "videos_per_query": 10,
    "amazon_queries": ["hangover", "korean hangover"],
    "amazon_product_lookups": 6,
    "tracked_asins": [],
    "trend_keywords": ["hangover", "hangover cure", "hangover drink", "korean hangover"],
    "own_terms": ["aldicom", "알디콤"],
    "weee_urls": ["https://www.sayweee.com/en/search?keyword=hangover"],
    "exclude_terms": ["soup", "해장국", "haejang"],
}

LIMITS = {"search_set": 10, "amazon_queries": 15, "trend_keywords": 10, "own_terms": 10, "weee_urls": 15,
          "exclude_terms": 20, "tracked_asins": 30}


def defaults():
    return copy.deepcopy(DEFAULTS)


def _words(value, label, limit, max_len=100):
    """문자열 목록 정리: 앞뒤 공백 제거 · 빈 값 제거 · 대소문자 무시 중복 제거."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[\n,]", value)]
    if not isinstance(value, list):
        raise ValueError(f"{label}은(는) 목록이어야 합니다.")
    out, seen = [], set()
    for v in value:
        s = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
        if not s:
            continue
        if len(s) > max_len:
            raise ValueError(f"{label}: '{s[:20]}…'이(가) 너무 깁니다 ({max_len}자 이하).")
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    if len(out) > limit:
        raise ValueError(f"{label}은(는) {limit}개까지 넣을 수 있습니다.")
    return out


def _int(value, label, lo, hi):
    try:
        n = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label}은(는) 숫자로 입력하세요.")
    if not lo <= n <= hi:
        raise ValueError(f"{label}은(는) {lo:,}~{hi:,} 사이로 입력하세요.")
    return n


def _asins(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("추적 ASIN은 목록이어야 합니다.")
    out, seen = [], set()
    for v in value:
        if isinstance(v, str):
            v = {"asin": v}
        if not isinstance(v, dict):
            continue
        asin = str(v.get("asin", "")).strip().upper()
        if not asin:
            continue
        if not ASIN_RE.match(asin):
            raise ValueError(f"ASIN '{asin[:20]}'이(가) 올바르지 않습니다 (영문 대문자·숫자 10자리, 예: B0ABCDEFGH).")
        if asin in seen:
            continue
        seen.add(asin)
        out.append({"asin": asin, "label": re.sub(r"\s+", " ", str(v.get("label") or "")).strip()[:60],
                    "own": bool(v.get("own"))})
    if len(out) > LIMITS["tracked_asins"]:
        raise ValueError(f"추적 ASIN은 {LIMITS['tracked_asins']}개까지 넣을 수 있습니다.")
    return out


def _urls(value):
    urls = _words(value, "Weee! 주소", LIMITS["weee_urls"], max_len=500)
    for u in urls:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not (host == "sayweee.com" or host.endswith(".sayweee.com")):
            raise ValueError(f"Weee! 주소는 https://www.sayweee.com/… 형식만 넣을 수 있습니다 ({u[:40]}).")
    return urls


def normalize(data):
    """화면에서 받은 값 검사 → 저장할 dict. 없는 항목은 기본값. 잘못된 값은 ValueError(화면에 보여 줄 문장)."""
    if not isinstance(data, dict):
        raise ValueError("수집 설정 형식이 올바르지 않습니다.")
    base = defaults()
    out = {}
    sets = data.get("search_sets", base["search_sets"])
    if not isinstance(sets, dict):
        raise ValueError("검색어 세트 형식이 올바르지 않습니다.")
    out["search_sets"] = {sid: _words(sets.get(sid, []), f"검색어 세트 {sid} · {SET_LABELS[sid]}", LIMITS["search_set"])
                          for sid in SET_IDS}
    out["min_views"] = _int(data.get("min_views", base["min_views"]), "영상 최소 조회수", 0, 1_000_000_000)
    lang = str(data.get("video_language", base["video_language"]))
    if lang not in LANGS:
        raise ValueError("영상 언어는 '영어' 또는 '한국어 · 영어'입니다.")
    out["video_language"] = lang
    out["videos_per_query"] = _int(data.get("videos_per_query", base["videos_per_query"]), "검색어당 영상 수", 1, 50)
    out["amazon_queries"] = _words(data.get("amazon_queries", base["amazon_queries"]), "Amazon 검색어", LIMITS["amazon_queries"])
    out["amazon_product_lookups"] = _int(data.get("amazon_product_lookups", base["amazon_product_lookups"]),
                                         "상세 조회 상품 수", 0, 10)
    out["tracked_asins"] = _asins(data.get("tracked_asins", base["tracked_asins"]))
    out["trend_keywords"] = _words(data.get("trend_keywords", base["trend_keywords"]), "트렌드 키워드", LIMITS["trend_keywords"])
    out["own_terms"] = _words(data.get("own_terms", base["own_terms"]), "자사 상품 표시 단어", LIMITS["own_terms"], max_len=60)
    out["weee_urls"] = _urls(data.get("weee_urls", base["weee_urls"]))
    out["exclude_terms"] = _words(data.get("exclude_terms", base["exclude_terms"]), "제외 단어", LIMITS["exclude_terms"], max_len=60)
    return out


def _row():
    try:
        return db.session.get(CollectConfig, 1)
    except Exception:  # 표가 아직 없을 때 등
        db.session.rollback()
        return None


def get_collect_config():
    """기본값 + 저장된 값. 저장된 값이 깨져 있으면 기본값으로 (수집이 멈추지 않게)."""
    row = _row()
    stored = row.parsed() if row else {}
    merged = defaults()
    merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
    try:
        return normalize(merged)
    except ValueError:
        return defaults()


def collect_meta():
    row = _row()
    return {"updatedAt": iso(row.updated_at) if row else None, "updatedBy": row.updated_by if row else ""}


def save_collect_config(data, user=""):
    """검사 후 저장 → (새 설정, 바뀐 항목 이름 목록)."""
    new = normalize(data)
    old = get_collect_config()
    changed = sorted(k for k in DEFAULTS if old.get(k) != new.get(k))
    row = _row()
    if row is None:
        row = CollectConfig(id=1)
        db.session.add(row)
    row.data = json.dumps(new, ensure_ascii=False)
    row.updated_at, row.updated_by = utcnow(), user[:100]
    db.session.commit()
    return new, changed
