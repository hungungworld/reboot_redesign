"""경쟁사 분석 (README '경쟁사 분석') — 마케터용: 무슨 말로 · 얼마에 · 어디서 팔고, 고객은 어떻게 보나.

- 등록: Amazon · Yami · Weee! 진열대의 브랜드를 insights_competitors에 넣음 (무료). 대체재 검색어로만 잡힌 브랜드는 '대체재'
- 사실(규칙 · 무료): 판매 채널별 최저가 · 1회분 단가 · 제형(상품명 규칙 → 못 정하면 AI) · 리뷰 칭찬/불만 · 유튜브 언급 · 이번 주 변화
- 분석(AI · 리뷰 상위 15개 브랜드): 핵심 메시지 · 주력 성분 · 기타 판매처 · 최근 소식 · 알디콤이 쓸 말 / 피할 말
  ① 근거 = 진열대(P) · 리뷰 요약(S) · 구매자 리뷰(R) · 브랜드 홈페이지(W) · 뉴스 검색(N)
  ② 근거로 못 채운 사실 칸만 OpenAI 웹 검색(G, 출처 주소 필수)
  ③ 그래도 없으면 '공개 자료에서 찾지 못함' (사실로 적음 · 지어내지 않음)
- 다시 조사하면 이전 분석과 바뀐 항목을 insights_competitor_history에 기록
"""
import json
import re
from collections import Counter
from datetime import timedelta
from urllib.parse import urlparse

from server import envkeys
from server.config import ROOT
from server.extensions import db
from server.models import iso, utcnow
from . import ai as insights_ai
from . import http
from .classify import TOPICS
from .http import CollectError
from .models import Comment, Competitor, CompetitorHistory, ProductPosition, Review, Video, kv_get, kv_set, load_json
from .util import clean, month_key, short_hash
from .weee import firecrawl

KEYWORDS_FILE = ROOT / "tabs" / "insights" / "data" / "competitor_keywords.json"
FIRECRAWL_SEARCH = "https://api.firecrawl.dev/v1/search"
DEEP_TOP = 15                      # 깊은 분석은 리뷰 상위 15개 브랜드
STALE_DAYS = 25                    # 데이터 업데이트 때 다시 조사하는 간격 (월 1회 안팎)
CLASSIFY_BATCH = 60
NOT_FOUND = "공개 자료에서 찾지 못함"
CHANNEL_LABEL = {"amazon": "Amazon", "yami": "Yami", "weee": "Weee!"}

FIELDS = [("message", "핵심 메시지"), ("ingredients", "주력 성분"), ("retail", "기타 판매처 (오프라인 · 자사몰)"),
          ("news", "최근 소식"), ("use", "알디콤이 쓸 말"), ("avoid", "피할 말 · 주의 표현")]
FIELD_LABEL = dict(FIELDS)
FACT_FIELDS = ("message", "ingredients", "retail", "news")   # 출처가 있어야 채움
LIST_FIELDS = ("ingredients",)

FORMS = [("liquid", "Liquid (샷 · 음료)"), ("powder", "Powder (분말 · 과립)"), ("stick", "Stick (스틱 포장)"), ("gummy", "Gummy/Jelly (구미 · 젤리)"),
         ("pellet", "Pellet (환)"), ("tablet", "Tablet/Capsule (정제 · 캡슐)"), ("other", "기타")]
FORM_LABEL = dict(FORMS)
FORM_RULES = [  # 앞에서부터 먼저 맞는 것 (예: 'Jelly … Powder'는 젤리, 'Drink Mix Powder'는 분말)
    ("pellet", re.compile(r"\bpellets?\b|(?<![가-힣])환(?![가-힣])|상쾌환|컨디션\s?환|여명환", re.I)),   # 한국식 환 (알갱이를 삼킴)
    ("tablet", re.compile(r"\b(tablets?|capsules?|caps|pills?|softgels?|veggie caps|effervescent|fizz(?:y|ies)?)\b|발포", re.I)),   # 발포정도 정제
    ("stick", re.compile(r"\bsticks?\b|스틱", re.I)),   # 스틱 포장이면 내용물(젤리 · 분말 · 액상)과 상관없이 스틱
    ("gummy", re.compile(r"\b(gumm(?:y|ies)|jell(?:y|ies)|chews?|chewables?)\b|젤리|구미", re.I)),
    ("powder", re.compile(r"\b(powders?|drink mix|granules?|packets?|sachets?)\b|분말|가루", re.I)),
    ("liquid", re.compile(r"\b(shots?|drinks?|liquid|beverages?|tonic|elixir|bottles?|ready to drink|rtd|"
                          r"\d+(?:\.\d+)?\s*(?:ml|fl\.?\s*oz))\b|음료|드링크", re.I)),
    ("liquid", re.compile(r"\bginseng\b|인삼|홍삼", re.I)),   # 인삼은 보통 액상 — 다른 제형 단어가 있으면 그쪽이 먼저
]
ITEM_FORM_RE = re.compile(r"^Item Form:\s*([^/]+?)\s*(?:/|$)", re.I)   # amazon.py가 판매 문구 앞에 붙인 '제품 선택' 값
SKIP_HOSTS = ("amazon.", "walmart.", "target.", "ebay.", "reddit.", "youtube.", "facebook.", "instagram.", "tiktok.",
              "cvs.", "walgreens.", "iherb.", "wikipedia.", "google.", "pinterest.", "x.com", "twitter.", "linkedin.",
              "costco.", "gnc.", "vitaminshoppe.", "sayweee.", "yami.", "trustpilot.", "yelp.", "oliveyoung.")
LISTICLE_RE = re.compile(r"\b(best|top \d+|\d+ best|review(?:s|ed)?|vs\.?|ranked|tested)\b", re.I)


# ───────── 검색어 역할 ─────────

def keyword_roles():
    """검색어 → 역할 (대표 · 제형 · 타이밍 · 대체재). 파일이 없거나 깨지면 빈 값."""
    try:
        data = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k.get("keyword", "")).strip().lower(): str(k.get("role", "")) for k in data.get("keywords") or []
            if isinstance(k, dict) and k.get("keyword")}


def _queries(text):
    return [q.strip() for q in str(text or "").split(",") if q.strip()]


def substitute_only(items, roles=None):
    """브랜드 상품이 모두 대체재 검색어로만 잡혔으면 True (검색어 기록이 없으면 False)."""
    roles = keyword_roles() if roles is None else roles
    qs = {q.lower() for x in items for q in _queries(x.get("queries"))}
    return bool(qs) and all(roles.get(q) == "대체재" for q in qs)


# ───────── 제형 (규칙 → AI) ─────────

def rule_form(text):
    t = str(text or "")
    for form, rx in FORM_RULES:
        if rx.search(t):
            return form
    return ""


def _sig(x):
    return short_hash(x.get("title", ""), str(x.get("message", ""))[:800])


def stored_positions():
    return {p.key: p for p in ProductPosition.query.all()}


def rule_form_of(x):
    """상품명 → 제품 선택(Amazon Item Form) → 판매 문구 ('Do not chew' 같은 문구가 상품명보다 앞서지 않게)."""
    msg = str(x.get("message", "") or "")
    m = ITEM_FORM_RE.match(msg)
    return rule_form(x.get("title", "")) or (rule_form(m.group(1)) if m else "") or rule_form(msg)


def form_of(x, stored=None):
    """진열대 한 줄 → 제형. 규칙이 정하면 규칙 → 저장된 AI 분류(문구가 같을 때) → 기타.
    규칙 결과는 저장값보다 앞섬 (규칙을 고치면 예전 상품도 바로 바뀜)."""
    form = rule_form_of(x)
    if form:
        return form
    row = (stored or {}).get(x["key"])
    if row is not None and row.sig == _sig(x) and row.form in FORM_LABEL:  # 예전 값(solid 등)은 기타
        return row.form
    return "other"


def classify_products(rows):
    """규칙으로 제형을 정하고, 못 정한 상품만 AI 묶음 호출 → {"rule": n, "ai": n, "calls": n}."""
    stored = stored_positions()
    todo, rule_n = [], 0
    for x in rows:
        sig, row = _sig(x), stored.get(x["key"])
        if row is not None and row.sig == sig and row.form in FORM_LABEL and (row.form != "other" or not insights_ai.ready()
                                                                or row.classified_by == "ai"):
            continue
        form = rule_form_of(x)
        row = row or ProductPosition(key=x["key"][:200])
        row.brand, row.sig, row.updated_at = (x["brand"] or "")[:120], sig, utcnow()
        row.form, row.timing, row.wellness, row.classified_by = form or "other", "", False, "rule"
        db.session.add(row)
        stored[x["key"]] = row
        rule_n += 1
        if not form:
            todo.append(x)
    db.session.commit()
    calls, ai_n = 0, 0
    if insights_ai.ready():
        for i in range(0, len(todo), CLASSIFY_BATCH):
            batch = todo[i:i + CLASSIFY_BATCH]
            out, cached = insights_ai.classify_forms(
                [{"id": x["key"], "title": x["title"][:300], "text": str(x.get("message") or "")[:600]} for x in batch])
            calls += 0 if cached else 1
            for x in batch:
                o = out.get(x["key"])
                if o:
                    stored[x["key"]].form, stored[x["key"]].classified_by = o, "ai"
                    ai_n += 1
            db.session.commit()
    return {"rule": rule_n, "ai": ai_n, "calls": calls}


# ───────── 등록 ─────────

def brand_groups(rows):
    groups = {}
    for x in rows:
        if not x["own"] and x["brand"]:
            groups.setdefault(x["brand"].lower(), []).append(x)
    return groups


def sync(rows, tier_of):
    """최신 진열대 rows(service.shelf_dict)의 브랜드를 등록 → 새로 넣은 브랜드 이름 목록.

    tier_of(상품명 목록) → direct · category · substitute (service._tier). 대체재 검색어로만 잡히면 substitute,
    숙취 검색어(대표 · 제형 · 타이밍)로 잡혔으면 적어도 category."""
    roles = keyword_roles()
    have = {c.brand.lower(): c for c in Competitor.query.all()}
    added, now = [], utcnow()
    for b, items in brand_groups(rows).items():
        brand = items[0]["brand"]
        qs = sorted({q for x in items for q in _queries(x.get("queries"))})
        tier = "substitute" if substitute_only(items, roles) else tier_of([x["title"] for x in items])
        if tier == "substitute" and any(roles.get(q.lower()) not in (None, "대체재") for q in qs):
            tier = "category"
        c = have.get(b)
        if c is None:
            c = Competitor(brand=brand[:120], tier=tier, keywords=json.dumps(qs, ensure_ascii=False), first_seen=now)
            db.session.add(c)
            have[b] = c
            added.append(brand)
        else:
            c.keywords = json.dumps(sorted(set(load_json(c.keywords, [])) | set(qs)), ensure_ascii=False)
            c.tier = tier
        c.last_seen = now
    db.session.commit()
    return added


# ───────── 사실 (규칙 · 무료) ─────────

def _mentions(name, texts):
    b = name.lower()
    if len(b) < 3:
        return 0
    rx = re.compile(r"(?<![a-z0-9])" + re.escape(b) + r"(?![a-z0-9])")
    return sum(1 for t in texts if rx.search(t))


def youtube_texts():
    """유튜브 영상 제목 + 관련 댓글 (소문자) — 브랜드 언급 세기용."""
    texts = [v.title.lower() for v in Video.query.all() if v.title]
    texts += [c.text.lower() for c in Comment.query.filter_by(relevant=True).limit(5000).all()]
    return texts


def feedback(items, brand):
    """구매자 리뷰 → 주제별 칭찬 · 불만 수, 가장 많은 칭찬 · 불만 주제와 예시 한 줄."""
    keys = [x["key"] for x in items]
    q = Review.query.filter(Review.product_key.in_(keys)) if keys else None
    rows = q.all() if q is not None else []
    if not rows:
        rows = Review.query.filter(db.func.lower(Review.brand) == brand.lower()).all()
    pos, neg, ex = Counter(), Counter(), {}
    for r in rows:
        for t in r.topic_list():
            if r.sentiment == "+":
                pos[t] += 1
                ex.setdefault(("+", t), r.summary_ko or clean(r.text, 140))
            elif r.sentiment == "-":
                neg[t] += 1
                ex.setdefault(("-", t), r.summary_ko or clean(r.text, 140))
    top = lambda c, s: ({"topic": c.most_common(1)[0][0], "count": c.most_common(1)[0][1],  # noqa: E731
                         "example": clean(ex.get((s, c.most_common(1)[0][0])), 160)} if c else None)
    return {"reviews": len(rows), "praise": top(pos, "+"), "complaint": top(neg, "-"),
            "topics": {t: {"pos": pos[t], "neg": neg[t]} for t in TOPICS if pos[t] or neg[t]}}


def brand_facts(items, stored, yt_texts=None, name=None):
    """한 브랜드(또는 알디콤)의 진열대 상품들 → 판매 채널 · 1회분 단가 · 제형 · 리뷰 · 칭찬/불만 · 유튜브 언급."""
    name = name or (items[0]["brand"] if items else "")
    chans = {}
    for x in items:
        c = chans.setdefault(x["source"], {"source": x["source"], "label": CHANNEL_LABEL.get(x["source"], x["source"]),
                                           "products": 0, "minPrice": None, "minPerServing": None, "inStock": False,
                                           "link": "", "sold": ""})
        c["products"] += 1
        if x["price"] is not None and (c["minPrice"] is None or x["price"] < c["minPrice"]):
            c["minPrice"], c["link"] = x["price"], x.get("link") or ""
        if x["perServing"] is not None and (c["minPerServing"] is None or x["perServing"] < c["minPerServing"]):
            c["minPerServing"] = x["perServing"]
        c["inStock"] = c["inStock"] or x["in_stock"] is not False
        sold = re.search(r"\d[\d,]*\+? Sold", x.get("availability") or "")
        if sold and not c["sold"]:
            c["sold"] = sold.group(0)
    ps = [x["perServing"] for x in items if x["perServing"] is not None]
    forms = Counter(form_of(x, stored) for x in items)
    rated = [(x["rating"], x["reviews"] or 0) for x in items if x.get("rating")]
    weight = sum(n for _, n in rated)
    return {
        "channels": [chans[s] for s in CHANNEL_LABEL if s in chans],
        "perServing": min(ps) if ps else None,
        "forms": [{"key": f, "label": FORM_LABEL[f], "products": n} for f, _ in FORMS for n in [forms[f]] if n],
        "reviews": sum(x["reviews"] or 0 for x in items),
        "rating": round(sum(r * n for r, n in rated) / weight, 1) if weight else None,
        "feedback": feedback(items, name),
        "youtube": _mentions(name, yt_texts) if yt_texts is not None else None,
        "products": [{"title": x["title"], "source": x["sourceLabel"], "price": x["price"], "perServing": x["perServing"],
                      "form": FORM_LABEL[form_of(x, stored)], "link": x["link"], "in_stock": x["in_stock"],
                      "reviews": x["reviews"], "availability": x.get("availability") or ""}
                     for x in sorted(items, key=lambda x: -(x["reviews"] or 0))[:6]],
    }


# 변화 → 알디콤 입장: 위협(고객을 뺏길 수 있음) · 기회(빈자리) · 관찰(말하는 방식)
CHANGE_GROUP = {"down": "threat", "new": "threat", "back": "threat", "oos": "chance", "up": "chance", "message": "watch"}


def _phrases(text):
    parts = re.split(r"(?<=[.!?])\s+|\s*/\s+", clean(text, 3000))
    return [x.strip(" ./") for x in parts if len(x.strip(" ./")) > 3]


def demo_prev(now):
    """시연용 '지난주' 진열대 — 지난주 수집이 없을 때만 씀 (화면에 '예시' 배지 · DB에 저장 안 함 · 보고서 · 숫자 요약에는 안 씀).
    이번 주 실제 진열대를 복사한 뒤, 상품 key로 순서를 고정해 몇 개만 바꿈 (새로고침해도 같은 결과):
    새 상품 2 · 가격 인하 2 · 가격 인상 2 · 재입고 1 · 품절 1 · 판매 문구 변경 1."""
    import hashlib
    comp = sorted((x for x in now if not x["own"]), key=lambda x: hashlib.md5(f"{x['source']}:{x['key']}".encode()).hexdigest())
    priced = [x for x in comp if x.get("price")]
    oos_now = [x for x in comp if x.get("in_stock") is False]
    in_now = [x for x in comp if x.get("in_stock") is True]
    msg_now = [x for x in comp if len(_phrases(x.get("message") or "")) >= 2]
    used, plan = set(), {}

    def pick(pool, n, kind):
        for x in pool:
            k = (x["source"], x["key"])
            if n and k not in used:
                used.add(k)
                plan[k] = kind
                n -= 1

    pick(priced, 2, "new")
    pick(priced, 2, "down")    # 지난주가 더 비쌌음 → 이번 주 가격 인하
    pick(priced, 2, "up")      # 지난주가 더 쌌음 → 이번 주 가격 인상
    pick(in_now, 1, "back")    # 지난주 품절 → 재입고
    pick(oos_now, 1, "oos")    # 지난주 재고 있음 → 품절
    pick(msg_now, 1, "message")
    out = []
    for x in now:
        kind = plan.get((x["source"], x["key"]))
        if kind == "new":
            continue
        p = dict(x)
        h = int(hashlib.md5(x["key"].encode()).hexdigest()[:4], 16) / 0xFFFF   # 상품마다 고정된 0~1
        if kind in ("down", "up"):
            pct = 0.08 + 0.12 * h   # 8~20%
            p["price"] = round(x["price"] * (1 + pct if kind == "down" else 1 - pct), 2)
        elif kind == "back":
            p["in_stock"] = False
        elif kind == "oos":
            p["in_stock"] = True
        elif kind == "message":
            p["message"] = ". ".join(_phrases(x["message"])[1:])   # 첫 문장(광고 문구)이 없던 예전 문구
        out.append(p)
    return out


def _vs_own(ps, own_ps):
    """1회분 단가를 알디콤 최저 1회분과 비교한 한 줄 (둘 중 하나라도 없으면 빈 문자열)."""
    if ps is None or not own_ps:
        return ""
    gap = ps - min(own_ps)
    if abs(gap) < 0.005:
        return f"1회분 ${ps:.2f} · 알디콤과 같아요"
    return f"1회분 ${ps:.2f} · 알디콤보다 ${abs(gap):.2f} {'싸요' if gap < 0 else '비싸요'}"


def weekly_changes(now, prev, limit=30):
    """지난 묶음 대비 경쟁 상품 변화: 가격(±5% 이상) · 새 상품 · 품절 · 재입고 · 판매 문구 변경.
    항목마다 group(threat · chance · watch)과 알디콤에게 뜻하는 한 줄(impact)을 붙임 — 수집한 숫자로만 계산."""
    if not prev:
        return None
    before = {(x["source"], x["key"]): x for x in prev}
    compared = {x["source"] for x in prev}
    known = lambda x: x.get("perServing") if x.get("servingsSource") != "default" else None  # noqa: E731 — 회분을 모르면 1회분 비교 안 함
    own_ps = [known(x) for x in now if x["own"] and known(x) is not None]
    own_src = {x["source"] for x in now if x["own"]}
    out = []
    for x in now:
        if x["own"] or x["source"] not in compared:
            continue
        p = before.get((x["source"], x["key"]))
        ps = known(x)
        base = {"brand": x["brand"], "title": x["title"][:120], "source": x["sourceLabel"], "link": x.get("link") or "",
                "price": x.get("price"), "perServing": ps}

        def add(kind, group, label, text, impact, weight, **extra):
            out.append(dict(base, kind=kind, group=CHANGE_GROUP[group], label=label, text=text, impact=impact, weight=weight, **extra))

        if p is None:
            add("new", "new", "새 상품", f"{x['title'][:70]} ({x['sourceLabel']}" + (f" · ${x['price']:.2f})" if x["price"] is not None else ")"),
                _vs_own(ps, own_ps) or "이번 주 진열대에 처음 보여요.", 50)
            continue
        if p.get("price") and x.get("price") and abs(x["price"] - p["price"]) / p["price"] >= 0.05:
            pct = (x["price"] - p["price"]) / p["price"] * 100
            vs = _vs_own(ps, own_ps)
            add("price", "down" if pct < 0 else "up", "가격 인하" if pct < 0 else "가격 인상",
                f"${p['price']:.2f} → ${x['price']:.2f} ({pct:+.0f}%) · {x['sourceLabel']}",
                vs or ("가격 비교에서 불리해질 수 있어요." if pct < 0 else "가격 비교 메시지에 쓸 수 있어요."),
                100 + abs(pct), prevPrice=p["price"], pct=round(pct, 1))
        if p.get("in_stock") is not False and x["in_stock"] is False:
            add("oos", "oos", "품절", f"{x['title'][:60]} · {x['sourceLabel']}",
                f"{x['sourceLabel']}에서 이 상품을 찾던 고객을 데려올 기회예요" + (" (알디콤도 여기서 팔아요)." if x["source"] in own_src else "."), 60)
        elif p.get("in_stock") is False and x["in_stock"] is True:
            add("back", "back", "재입고", f"{x['title'][:60]} · {x['sourceLabel']}", "다시 팔기 시작했어요. 품절 동안 넘어온 고객을 지켜야 해요.", 40)
        if p.get("message") and x.get("message") and clean(p["message"], 400) != clean(x["message"], 400):
            old, new = _phrases(p["message"]), _phrases(x["message"])
            add("message", "message", "판매 문구 변경", clean(x["message"], 140), "강조하는 말이 바뀌었어요. 브랜드 카드에서 핵심 메시지를 확인하세요.", 70,
                added=[s[:120] for s in new if s not in old][:2], removed=[s[:120] for s in old if s not in new][:2])
    out.sort(key=lambda c: -c.pop("weight"))
    return out[:limit]


# ───────── 브랜드 분석 (근거 → AI → 웹 검색) ─────────

def deep_targets(rows, limit=DEEP_TOP):
    """분석 대상: 대체재 · 보관을 뺀 브랜드 중 진열대 리뷰 합계(같으면 판매 채널 수) 상위 limit개."""
    comps = {c.brand.lower(): c for c in Competitor.query.filter_by(status="active").all() if c.tier != "substitute"}
    score = {b: (sum(x["reviews"] or 0 for x in items), len({x["source"] for x in items}))
             for b, items in brand_groups(rows).items() if b in comps}
    return [comps[b] for b in sorted(score, key=lambda b: (-score[b][0], -score[b][1], b))[:limit]]


def stale(c, force=False):
    old_schema = "message" not in load_json(c.analysis, {})
    return force or old_schema or not c.researched_at or c.researched_at < utcnow() - timedelta(days=STALE_DAYS)


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _token(brand):
    return re.sub(r"[^a-z0-9]", "", brand.lower())[:8]


def web_search(key, query, limit=5):
    """Firecrawl 검색 → [{url, title, description}] (1크레딧 안팎)."""
    status, data = http.post_json("Firecrawl", FIRECRAWL_SEARCH, {"query": query, "limit": limit},
                                  headers={"Authorization": f"Bearer {key}"})
    if status in (401, 403):
        raise CollectError("Firecrawl이 키를 거절했어요. 설정 › 연결에서 키를 확인하세요.")
    if status == 402:
        raise CollectError("Firecrawl 크레딧을 모두 썼어요.")
    if status != 200 or not isinstance(data, dict):
        return []
    res = data.get("data")
    return [r for r in (res if isinstance(res, list) else []) if isinstance(r, dict) and str(r.get("url", "")).startswith("http")]


def find_website(results, brand):
    """검색 결과에서 공식 홈페이지 (주소에 브랜드 이름이 들어간 첫 결과, 쇼핑몰 · SNS 제외)."""
    token = _token(brand)
    for r in results:
        host = _host(r["url"])
        if host and not any(s in host for s in SKIP_HOSTS) and len(token) >= 3 and token in re.sub(r"[^a-z0-9]", "", host):
            return f"{urlparse(r['url']).scheme}://{host}/"
    return ""


def news_items(results, brand, site=""):
    """검색 결과 중 뉴스 · 보도자료만 (홈페이지 · 쇼핑몰 · 'Best 10' 같은 비교 글 제외, 브랜드 이름이 들어간 것)."""
    token, out = brand.lower(), []
    for r in results:
        host, text = _host(r["url"]), f"{r.get('title', '')} {r.get('description', '')}"
        if not host or any(s in host for s in SKIP_HOSTS) or (site and host == _host(site)):
            continue
        if token not in text.lower() or LISTICLE_RE.search(r.get("title", "")):
            continue
        out.append(r)
    return out[:3]


def evidence(c, items):
    """근거 목록 [{id, type, title, url, text}] — 진열대 상품, 아마존 리뷰 요약, 구매자 리뷰."""
    items = sorted(items, key=lambda x: -(x["reviews"] or 0))
    out = []
    for i, x in enumerate(items[:6], start=1):
        price = f" · ${x['price']:.2f}" if x.get("price") is not None else ""
        out.append({"id": f"P{i}", "type": f"{x['sourceLabel']} 진열대", "title": x["title"][:200], "url": x.get("link") or "",
                    "text": clean(f"{x['title']}{price} · {x.get('availability') or ''} · {x.get('message') or ''}", 900)})
    summaries = kv_get("amazon_summaries", {})
    keys = [x["key"] for x in items]
    n = 0
    for k in keys:
        s = summaries.get(k.split(":", 1)[-1])
        if s and s.get("text"):
            n += 1
            out.append({"id": f"S{n}", "type": "Amazon 리뷰 요약", "title": clean(s.get("title"), 200), "url": "",
                        "text": clean(s["text"], 600)})
    revs = (Review.query.filter(Review.product_key.in_(keys)).order_by(Review.collected_at.desc()).limit(6).all()
            if keys else [])
    for i, r in enumerate(revs, start=1):
        out.append({"id": f"R{i}", "type": "구매자 리뷰", "title": clean(r.product_title, 200), "url": "",
                    "text": clean(f"별점 {r.rating or '?'} · {r.title} · {r.text}", 400)})
    return out


def _norm(f, v):
    if f in LIST_FIELDS:
        return sorted(v.get("values") or [])
    return clean(v.get("text"), 2000)


def changed_fields(old, new):
    if not old:
        return []
    return [FIELD_LABEL[f] for f, _ in FIELDS if _norm(f, old.get(f) or {}) != _norm(f, new.get(f) or {})]


def clean_analysis(out, ids):
    """AI 결과 정리: 없는 출처 번호는 지우고, 사실 칸인데 출처가 없으면 비워 둠(→ 웹 검색 대상)."""
    res = {}
    for f, _ in FIELDS:
        v = out.get(f) or {}
        src = [s for s in v.get("sources") or [] if s in ids]
        if f in LIST_FIELDS:
            vals = [clean(x, 60) for x in v.get("values") or [] if clean(x, 60)][:5]
            res[f] = {"values": vals if src else [], "sources": src if vals else []}
        else:
            text = clean(v.get("text"), 600)
            if f in FACT_FIELDS and not src:
                text = ""
            res[f] = {"text": text, "sources": src if text else []}
    return res


def _empty(f, v):
    return not (v.get("values") if f in LIST_FIELDS else v.get("text"))


def research(c, items):
    """경쟁사 한 곳 분석 → {usage, cached, changed, warnings}. OpenAI 키가 없으면 CollectError."""
    if not insights_ai.ready():
        raise CollectError("OpenAI 키가 없어 경쟁사 분석을 할 수 없어요 (설정 › 연결).")
    usage, warnings = {"firecrawl": 0, "openai": 0}, []
    ev = evidence(c, items)
    fc = envkeys.get("FIRECRAWL_API_KEY")
    if fc:
        try:
            if not c.website and c.brand.lower() not in kv_get("comp_site_searched", {}):
                found = web_search(fc, f"{c.brand} hangover official site")
                usage["firecrawl"] += 1
                c.website = find_website(found, c.brand)[:500]
                searched = kv_get("comp_site_searched", {})
                searched[c.brand.lower()] = iso(utcnow())
                kv_set("comp_site_searched", searched)
            if c.website:
                md = str(firecrawl(fc, {"url": c.website, "formats": ["markdown"], "onlyMainContent": True}).get("markdown") or "")
                usage["firecrawl"] += 1
                if md:
                    ev.append({"id": "W1", "type": "브랜드 홈페이지", "title": c.website, "url": c.website, "text": clean(md, 6000)})
            news = news_items(web_search(fc, f'"{c.brand}" hangover news'), c.brand, c.website)
            usage["firecrawl"] += 1
            for i, r in enumerate(news, start=1):
                ev.append({"id": f"N{i}", "type": "뉴스 · 기사", "title": clean(r.get("title"), 200), "url": r["url"],
                           "text": clean(f"{r.get('title', '')} — {r.get('description', '')}", 500)})
        except CollectError as e:
            warnings.append(f"{c.brand}: {e}")
            if "키를 거절" in str(e) or "크레딧" in str(e):
                fc = ""
    if not ev:
        raise CollectError(f"{c.brand}: 근거로 쓸 진열대 · 홈페이지 자료가 없어요.")
    payload = {"brand": c.brand, "evidence": [{k: e[k] for k in ("id", "type", "title", "text")} for e in ev]}
    out, cached = insights_ai.analyze_competitor(payload)
    usage["openai"] += 0 if cached else 1
    new = clean_analysis(out, {e["id"] for e in ev})
    missing = [f for f in FACT_FIELDS if _empty(f, new[f])]
    if missing:
        try:
            web, wcached = insights_ai.web_fill(c.brand, missing, month_key(utcnow().date()))
            usage["openai"] += 0 if wcached else 1
        except insights_ai.AIError as e:
            web = {}
            warnings.append(f"{c.brand} AI 웹 검색: {e}")
        g = 0
        for f in missing:
            v = web.get(f) or {}
            urls = [u for u in v.get("urls") or [] if str(u).startswith(("http://", "https://"))][:3]
            val = [clean(x, 60) for x in v.get("values") or [] if clean(x, 60)][:5] if f in LIST_FIELDS else clean(v.get("text"), 600)
            if not urls or not val:
                continue
            ids = []
            for u in urls:
                g += 1
                ev.append({"id": f"G{g}", "type": "AI 웹 검색", "title": _host(u) or u, "url": u, "text": ""})
                ids.append(f"G{g}")
            new[f] = {"values": val, "sources": ids} if f in LIST_FIELDS else {"text": val, "sources": ids}
    for f in FACT_FIELDS:
        if _empty(f, new[f]):
            new[f] = {"values": [], "text": NOT_FOUND, "sources": []} if f in LIST_FIELDS else {"text": NOT_FOUND, "sources": []}
    old = load_json(c.analysis, {})
    changed = changed_fields(old, new)
    if old:
        db.session.add(CompetitorHistory(competitor_id=c.id, analysis=c.analysis, changed=json.dumps(changed, ensure_ascii=False),
                                         researched_at=c.researched_at))
    c.analysis = json.dumps(new, ensure_ascii=False)
    c.sources = json.dumps([{k: e[k] for k in ("id", "type", "title", "url")} for e in ev], ensure_ascii=False)
    c.model, c.researched_at = insights_ai.openai_model(), utcnow()
    db.session.commit()
    return {"usage": usage, "cached": cached, "changed": changed, "warnings": warnings}


def run(rows, tier_of, force=False, only=None):
    """데이터 업데이트 '경쟁사 분석' — 등록 → 제형 분류 → 분석(오래된 것 · force면 상위 15개 모두 · only면 한 곳)."""
    added = sync(rows, tier_of)
    cls = classify_products(rows)
    usage = {"firecrawl": 0, "openai": cls["calls"]}
    warnings, done, changed = [], 0, 0
    if only is not None:
        c = db.session.get(Competitor, only)
        targets = [c] if c is not None else []
    else:
        targets = [c for c in deep_targets(rows) if stale(c, force)]
    if targets and not insights_ai.ready():
        warnings.append("OpenAI 키가 없어 브랜드 분석은 건너뛰었어요.")
        targets = []
    groups = brand_groups(rows)
    for c in targets:
        try:
            r = research(c, groups.get(c.brand.lower(), []))
        except CollectError as e:
            warnings.append(str(e))
            continue
        for k, v in r["usage"].items():
            usage[k] += v
        warnings += r["warnings"]
        done += 1
        changed += bool(r["changed"])
    msg = (f"새 경쟁사 {len(added)}개 · 제형 분류 {cls['rule']}개(AI {cls['ai']}개) · 브랜드 분석 {done}개"
           + (f" (바뀐 곳 {changed}개)" if changed else "") + ".")
    return {"count": done, "added": added, "usage": {k: v for k, v in usage.items() if v}, "warnings": warnings, "message": msg}


# ───────── 화면 · 내려받기 ─────────

def comp_dict(c):
    a = load_json(c.analysis, {})
    if a and "message" not in a:  # 예전 7개 항목 분석(포지셔닝 축 등)은 분석 전으로 보고 다음 조사 때 새로 채움
        a = {}
    return {"id": c.id, "brand": c.brand, "tier": c.tier, "status": c.status, "website": c.website,
            "keywords": load_json(c.keywords, []), "analysis": a, "analyzed": bool(a), "sources": load_json(c.sources, []),
            "model": c.model, "firstSeen": iso(c.first_seen), "lastSeen": iso(c.last_seen), "researchedAt": iso(c.researched_at),
            "isNew": bool(c.first_seen and c.first_seen >= utcnow() - timedelta(days=7)),
            "history": [{"changed": load_json(h.changed, []), "researchedAt": iso(h.researched_at), "savedAt": iso(h.created_at)}
                        for h in CompetitorHistory.query.filter_by(competitor_id=c.id).order_by(CompetitorHistory.id.desc()).limit(3)]}


def field_text(a, f):
    v = (a or {}).get(f) or {}
    if f in LIST_FIELDS:
        return ", ".join(v.get("values") or []) or v.get("text") or ""
    return v.get("text") or ""
