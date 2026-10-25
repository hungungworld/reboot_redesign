"""작은 도우미: 주차 · 월 · 1회분 단가 · 글자 정리."""
import hashlib
import re
from datetime import date, timedelta

# ───────── 주차 · 월 ─────────


def week_key(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(key):
    """'2026-W43' → (월요일, 일요일)."""
    y, w = key.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    return mon, mon + timedelta(days=6)


def week_label(key):
    try:
        a, b = week_range(key)
    except (ValueError, AttributeError):
        return key
    return f"{a.month}.{a.day} – {b.month}.{b.day}"


def month_key(d):
    return d.strftime("%Y-%m")


def month_range(key):
    y, m = (int(x) for x in key.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt - timedelta(days=1)


def prev_month(key):
    first, _ = month_range(key)
    return month_key(first - timedelta(days=1))


def review_month(today):
    """월간 리뷰가 볼 달: 1~7일에는 지난달 (README 월간 리뷰)."""
    return prev_month(month_key(today)) if today.day <= 7 else month_key(today)


# ───────── 글자 ─────────

def clean(value, n=300):
    s = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return s[:n]


def has_any(text, terms):
    t = str(text or "").lower()
    return any(str(x).lower() in t for x in (terms or []) if str(x).strip())


def short_hash(*parts):
    return hashlib.sha256("\u0001".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:40]


def to_float(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def to_int(v):
    f = to_float(v)
    return int(f) if f is not None else None


def parse_price(v):
    """$12.99 · '12.99' · {'value': '$12.99'} · 12.99 → 12.99 (못 읽으면 None)."""
    if isinstance(v, dict):
        for k in ("extracted_value", "extracted_price", "value", "raw", "price"):
            if v.get(k) is not None:
                return parse_price(v[k])
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 2) if v >= 0 else None
    m = re.search(r"\$?\s*(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)", str(v or ""))
    if not m:
        return None
    return round(float(m.group(1).replace(",", "")), 2)


# ───────── 1회분 단가 (README '1회분 단가 계산') ─────────

_MIN = re.compile(r"\(\s*min(?:imum)?\.?\s*(?:order\s*(?:of\s*)?)?:?\s*\d+\s*\w*\s*\)|\bmin(?:imum)?\.?\s*(?:order\s*(?:of\s*)?)?:?\s*\d+\b", re.I)
_UNIT = r"(?:packs?|pk|pcs?|pieces?|sticks?|pouch(?:es)?|sachets?|packets?|bottles?|shots?|vials?|bags?|count|ct|servings?|ea)"
_BOX = r"(?:boxes?|packs?|pk|cases?|bags?|cartons?|sets?)"
_VOL = r"(?:ml|g|oz|fl\.?\s*oz|l)"
_TIMES = [  # 곱하기 표기가 있으면 먼저
    ("부피 × 개수", re.compile(rf"\b\d+(?:\.\d+)?\s*{_VOL}\s*[*x×]\s*(\d+)(?!\d)", re.I), lambda m: int(m.group(1))),
    ("개수 × 부피", re.compile(rf"\b(\d+)\s*[*x×]\s*\d+(?:\.\d+)?\s*{_VOL}\b", re.I), lambda m: int(m.group(1))),
    ("개수 × 묶음", re.compile(rf"\b(\d+)\s*{_UNIT}?\s*[*x×]\s*(\d+)\s*{_BOX}?\b", re.I), lambda m: int(m.group(1)) * int(m.group(2))),
]
_PACK_OF = re.compile(r"\b(?:pack|set|box|case)\s+of\s+(\d+)\b", re.I)
_COUNT = re.compile(r"\b(\d+)\s*-?\s*(?:count|ct|servings?|sticks?|pouch(?:es)?|sachets?|packets?|bottles?|shots?|vials?|"
                    r"tablets?|capsules?|caps|gummies|chews|pcs|pieces)\b", re.I)
_N_PACK = re.compile(r"\b(\d+)\s*-?\s*(?:packs?|pk)\b", re.I)
_KO = re.compile(r"(\d+)\s*(?:팩|포|개입|개|병|스틱|봉|입)")


def parse_servings(text):
    """포장 문구 → (회분 수, 읽은 규칙). 못 읽으면 (None, '').

    예) '5 pack*4 boxes' = 20 · 'Pack of 6' = 6 · '12 Count (Pack of 2)' = 24 · '(min 2)'는 최소 주문 수량이라 무시."""
    s = _MIN.sub(" ", str(text or ""))
    ok = lambda n: n if n and 1 <= n <= 500 else None  # noqa: E731
    for name, rx, fn in _TIMES:
        m = rx.search(s)
        if m and ok(fn(m)):
            return fn(m), name
    pack_of = _PACK_OF.search(s)
    count = _COUNT.search(s)
    if pack_of and count and ok(int(pack_of.group(1)) * int(count.group(1))):
        return int(pack_of.group(1)) * int(count.group(1)), "N개 × Pack of"
    for name, m in (("Pack of N", pack_of), ("N개 단위", count), ("N pack", _N_PACK.search(s)), ("한국어 개수", _KO.search(s))):
        if m and ok(int(m.group(1))):
            return int(m.group(1)), name
    return None, ""


BUCKETS = (("low", "$3 미만"), ("mid", "$3~6"), ("high", "$6 이상"))
BUCKET_LABEL = dict(BUCKETS)


def bucket(per_serving):
    if per_serving is None:
        return None
    return "low" if per_serving < 3 else "mid" if per_serving < 6 else "high"


def per_serving(price, servings):
    if price is None or not servings:
        return None
    return round(price / servings, 2)
