"""키워드 확인 — 텍스트 결과물의 브랜드 표기 · 제품별 근거 · 금지된 내부 표현 (README 9장).

AI 보조 검토(review_v2) · 외부 검토(컴플라이언스 파트)와 따로 표시합니다. 찾은 것이 없어도 '통과'가 아니라 '키워드 미탐지'입니다.
"""
import re

from server.models import iso, utcnow
from . import brandkit

SUGAR_RE = re.compile(r"\b(low|reduced|less|lower|zero|no|minimal|light)[\s-]*sugars?\b|\bsugar[\s-]*(free|less)\b|"
                      r"\bno\s+added\s+sugars?\b|\bunsweetened\b", re.I)
ZERO_SUGAR_RE = re.compile(r"zero[\s-]*sugars?", re.I)  # 'Zero Sugar'는 이 탭에서 경고하지 않음 (사용자 결정 2026-10-24)
HANGOVER_RE = re.compile(r"\bhang[\s-]?overs?\b|숙취")
SLOGANS = brandkit.slogans()  # Restart Your Day · Buy Your Tomorrow — 둘 다 공식 (2026-10-25)
SLOGAN_RES = [(s, re.compile(r"\s*".join(s.split()), re.I)) for s in SLOGANS]
BRAND_WORD_RE = re.compile(r"\bre[\s-]?boot\b", re.I)
BRAND_OK = ("Reboot", "REBOOT", "reboot")  # 소문자 reboot는 동사일 수 있어 그대로 둠
NUMBER_RE = re.compile(r"(\$\s?\d[\d,.]*|\b\d+(?:\.\d+)?\s?(?:g|mg|mcg|ml|mL|oz|fl\.?\s?oz|kcal|cal|calories|grams?|%)(?![A-Za-z]))", re.I)
INTERNAL_KO_RE = re.compile(r"저당")
OLD_CHARACTER_RE = re.compile(r"리부부|rebubu", re.I)
CM_SONG_RE = re.compile(r"니온\s*앤\s*리부|neon\s*(and|&|n)\s*reboo", re.I)
TODO_RE = re.compile(r"\[확인 필요[^\]]*\]")
JELLY_RE = re.compile(r"\bjell(y|ies)\b|젤리", re.I)
DRINK_RE = re.compile(r"\bdrinks?\b|\bbeverages?\b|음료", re.I)
BALANCE_RE = re.compile(r"\bbalance\b", re.I)

BRAND_FACTS = {"name", "slogan", "slogans", "colors", "character", "cm_song", "category", "intro", "market", "company", "key_ingredient"}
PRODUCT_FACTS = {"internal_name": "internal_name", "name": "internal_name", "consumer_name": "consumer_name", "form": "form",
                 "flavors": "flavors", "flavor_shapes": "flavor_shapes", "pack": "pack", "price": "price", "volume": "volume",
                 "bottle": "bottle", "ingredients": "ingredients", "zero_sugar": "zero_sugar",
                 "development_direction": "development_direction", "direction": "development_direction"}
CONSUMER_KINDS = ("copy", "cardnews", "script", "desc", "crm")
SCOPE = "텍스트만 확인 (이미지·영상 속 글자는 검사하지 않음)"


def _terms(regex, text, limit=5, exact=False):
    seen = []
    for m in regex.finditer(text):
        t = m.group(0).strip()
        key = (lambda x: x) if exact else str.lower
        if t and key(t) not in [key(s) for s in seen]:
            seen.append(t)
        if len(seen) >= limit:
            break
    return seen


def fact_value(facts, entity_id, fact_id):
    if entity_id == "brand":
        if fact_id not in BRAND_FACTS:
            return None
        v = (facts.get("brand") or {}).get(fact_id)
        return v if v not in ("", None) else None
    p = next((x for x in facts.get("products") or [] if x.get("entity_id") == entity_id), None)
    key = PRODUCT_FACTS.get(fact_id)
    if p is None or key is None:
        return None
    v = p.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    if key == "zero_sugar":
        return True if v is True else None  # Original(false)에 Zero Sugar 근거를 달면 맞지 않음
    return v if v not in ("", None, []) else None


def _num_key(term):
    return re.sub(r"\s+", "", term.lower()).replace(".00", "")


def known_numbers(facts):
    """브랜드 키트에 있는 가격·용량 — 카피에 써도 '팩트시트에 없는 값' 경고를 띄우지 않음."""
    out = set()
    for p in facts.get("products") or []:
        for pr in p.get("price") or []:
            if isinstance(pr, dict) and pr.get("usd") is not None:
                out.add(_num_key(f"${pr['usd']}"))
        if p.get("volume"):
            out.add(_num_key(str(p["volume"])))
    return out


def check_claims(claims, facts):
    """모델이 단 근거(entity_id·fact_id)가 실제 선택 제품·브랜드의 값과 대응하는지."""
    bad = []
    for c in claims or []:
        e, f = str(c.get("entity_id") or ""), str(c.get("fact_id") or "")
        if fact_value(facts, e, f) is None:
            bad.append(f"{e or '?'}/{f or '?'}")
    return bad


def keyword_check(text, kind, sheet, facts, claims=None):
    text = text or ""
    items = []

    def add(code, label, level, terms=None):
        items.append({"code": code, "label": label, "level": level, "terms": terms or []})

    sugar = [t for t in _terms(SUGAR_RE, text, 8) if not ZERO_SUGAR_RE.fullmatch(t)]
    if sugar:
        add("sugar", "당 표현 — 'Zero Sugar'(Balance) 외의 당 표현은 쓰지 않아요", "error", sugar[:5])
    if HANGOVER_RE.search(text):
        add("hangover", "숙취(hangover) 언급 — 효능처럼 읽혀 쓰지 않아요", "error", _terms(HANGOVER_RE, text))
    for slogan, slogan_re in SLOGAN_RES:
        for m in slogan_re.finditer(text):
            found = m.group(0)
            if found == slogan:
                continue
            if found == slogan.upper():
                add("slogan", f"슬로건이 대문자로만 쓰였어요 — 원문 표기는 '{slogan}'", "warn", [found])
            else:
                add("slogan", f"슬로건 표기 — 정확히 '{slogan}'", "error", [found])
            break
    odd = [w for w in _terms(BRAND_WORD_RE, text, 12, exact=True) if w not in BRAND_OK]
    if odd:
        add("brand", "브랜드명 표기 — REBOOT 또는 Reboot", "warn", odd)
    if kind in CONSUMER_KINDS and INTERNAL_KO_RE.search(text):
        add("internal", "내부 표시 '저당'은 소비자용 문구에 쓰지 않아요", "error", ["저당"])
    known = known_numbers(facts)
    nums = [t for t in _terms(NUMBER_RE, text, 8) if "{{" not in t and _num_key(t) not in known]
    if nums:
        add("number", "수치·가격 — 팩트시트에 없는 값이면 지우거나 [확인 필요]로", "warn", nums)

    pids = sheet.get("product_ids") or []
    has_jelly = any(p.startswith("jelly") for p in pids)
    has_drink = any(p.startswith("drink") for p in pids)
    has_balance = any(p.endswith("balance") for p in pids)
    if not pids and (JELLY_RE.search(text) or DRINK_RE.search(text)):
        add("product", "브랜드 자체 선택인데 제품을 언급했어요 — 의도한 것인지 확인", "warn",
            _terms(JELLY_RE, text, 2) + _terms(DRINK_RE, text, 2))
    else:
        if pids and not has_jelly and JELLY_RE.search(text):
            add("product", "선택하지 않은 제품(젤리) 언급", "warn", _terms(JELLY_RE, text, 3))
        if pids and not has_drink and DRINK_RE.search(text):
            add("product", "선택하지 않은 제품(음료) 언급", "warn", _terms(DRINK_RE, text, 3))
        if pids and not has_balance and BALANCE_RE.search(text):
            add("product", "Balance 제품을 고르지 않았는데 'Balance'가 나와요", "warn", _terms(BALANCE_RE, text, 2))
    if OLD_CHARACTER_RE.search(text):
        add("character", "캐릭터 이름은 'Rebu'로 통일했어요", "warn", _terms(OLD_CHARACTER_RE, text, 3))
    if CM_SONG_RE.search(text):
        add("cm_song", "CM송은 제작 중이에요 — 가사·음원 없이 '음원 연결 필요'로 표시", "warn", _terms(CM_SONG_RE, text, 3))
    bad = check_claims(claims, facts) if claims is not None else []
    if bad:
        add("claims", "근거 연결이 선택 제품·브랜드의 실제 값과 맞지 않아요", "warn", bad[:6])
    todos = TODO_RE.findall(text)
    if todos:
        add("todo", f"[확인 필요] {len(todos)}곳 — 채우기 전에는 게시하지 않아요", "info", todos[:4])

    status = "error" if any(i["level"] == "error" for i in items) else "warn" if any(i["level"] == "warn" for i in items) else "clear"
    return {"method": "키워드 확인", "status": status, "items": items, "scope": SCOPE, "checkedAt": iso(utcnow())}


def error_count(result):
    return sum(1 for i in (result or {}).get("items", []) if i.get("level") == "error")
