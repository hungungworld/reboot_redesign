"""인사이트 AI (OpenAI · server.core.ai.openai_json) — 판단은 규칙, AI는 문장 · 분류 · 요약만.

- 같은 입력이면 저장된 결과를 씀 (insights_ai_cache) → 비용 없음
- 호출은 openai_json이 작업 로그(AI 프롬프트)에 자동으로 남김
- 키가 없으면 부르지 않음 (화면은 규칙 결과 그대로)
"""
import hashlib
import json
from pathlib import Path

from server.core.ai import AIError, openai_json, openai_model, openai_ready
from server.extensions import db
from .classify import QUESTION_TOPICS, TOPICS
from .models import AICache, load_json

PART = "insights"
SAFE = ("숙취 해소 · 치료 · 예방 같은 효능 표현(cure, prevent, treat, detox 등)을 새로 만들지 마세요. "
        "주어진 자료에 없는 숫자 · 사실 · 브랜드를 지어내지 마세요. 한국어 존댓말(-요체)로 짧게 쓰세요.")


def _key(kind, payload):
    raw = kind + "\u0001" + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_get(kind, payload):
    row = db.session.get(AICache, _key(kind, payload))
    return (load_json(row.data, {}), row) if row else (None, None)


def cache_put(kind, payload, data):
    k = _key(kind, payload)
    row = db.session.get(AICache, k) or AICache(key=k, kind=kind)
    row.data, row.model = json.dumps(data, ensure_ascii=False), openai_model()
    db.session.add(row)
    db.session.commit()


def ready():
    return openai_ready()


# ───────── 이번 주 액션 문장 ─────────

ACTIONS_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "fact", "why", "todo"],
        "properties": {"id": {"type": "string"}, "fact": {"type": "string"}, "why": {"type": "string"}, "todo": {"type": "string"}}}}},
}


def _action_payload(actions):
    return [{"id": a["id"], "grade": a["grade"], "rule": a["ruleLabel"], "fact": a["fact"], "why": a["why"], "todo": a["todo"],
             "facts": a["facts"]} for a in actions]


def cached_rewrite(actions):
    """저장된 AI 문장이 있으면 {id: {fact, why, todo}}, 없으면 {}."""
    if not actions:
        return {}, None
    data, row = cache_get("weekly_actions", _action_payload(actions))
    return ((data or {}).get("items") or {}), row


def rewrite_actions(actions):
    """규칙이 고른 신호의 문장만 다듬음 (등급 · 판단은 그대로)."""
    payload = _action_payload(actions)
    data, _ = cache_get("weekly_actions", payload)
    if data:
        return data.get("items") or {}, True
    system = ("너는 미국에 진출한 한국 숙취해소 젤리 · 음료 브랜드(REBOOT, 실제 데이터는 알디콤)의 마케팅 보조야. "
              "규칙이 고른 '이번 주 액션'의 문장을 마케터가 바로 이해하게 다듬어. 등급과 판단은 바꾸지 말고, "
              "각 항목마다 fact(사실 1문장) · why(왜 중요한가 1문장) · todo(할 일 1문장)를 써. id는 그대로 돌려줘. " + SAFE)
    out = openai_json(system, {"actions": payload}, schema=ACTIONS_SCHEMA, schema_name="weekly_actions", part=PART,
                      action="이번 주 액션 문장 작성", max_tokens=2500)
    ids = {a["id"] for a in actions}
    items = {i["id"]: {"fact": i["fact"].strip(), "why": i["why"].strip(), "todo": i["todo"].strip()}
             for i in out.get("items") or [] if i.get("id") in ids and i.get("fact")}
    cache_put("weekly_actions", payload, {"items": items})
    return items, False


# ───────── 리뷰 · 댓글 분류 ─────────

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "topics", "sentiment", "summary_ko"],
        "properties": {"id": {"type": "string"},
                       "topics": {"type": "array", "items": {"type": "string", "enum": list(TOPICS)}},
                       "sentiment": {"type": "string", "enum": ["+", "-", "0"]},
                       "summary_ko": {"type": "string"}}}}},
}


def classify_reviews(rows):
    """rows: [{id, rating, title, text}] → {id: {topics, sentiment, summary_ko}}."""
    system = ("구매자 리뷰를 주제와 감정으로 분류해. 주제는 효과 · 맛·향 · 복용 편의 · 부작용 · 가격 중 해당하는 것 모두(없으면 빈 목록), "
              "감정은 + (칭찬) · - (불만) · 0 (중립), summary_ko는 한국어 한 문장 요약. " + SAFE)
    out = openai_json(system, {"reviews": rows}, schema=REVIEW_SCHEMA, schema_name="review_topics", part=PART,
                      action="리뷰 주제 분류", max_tokens=4000)
    return {str(i["id"]): i for i in out.get("items") or []}


TRANSLATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["text_ko"],
    "properties": {"text_ko": {"type": "string"}},
}


def is_korean(text):
    """한글이 글자의 30% 이상이면 한국어 (번역하지 않음)."""
    letters = [c for c in str(text or "") if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if "가" <= c <= "힣") / len(letters) >= 0.3


def translate_review(text):
    """리뷰 원문 → 한국어 번역. 같은 원문은 저장된 번역을 씀 (비용 없음). 한국어면 그대로."""
    text = str(text or "")[:4000]
    if is_korean(text):
        return text
    payload = {"text": text}
    hit, _ = cache_get("review_ko", payload)
    if hit and hit.get("text_ko"):
        return hit["text_ko"]
    system = ("구매자 리뷰를 자연스러운 한국어로 번역해. 뜻을 더하거나 빼지 말고 말투(구어 · 이모지)는 살려. 번역문만 text_ko에 넣어. " + SAFE)
    out = openai_json(system, payload, schema=TRANSLATE_SCHEMA, schema_name="review_ko", part=PART, action="리뷰 번역",
                      max_tokens=2000)
    text_ko = str(out.get("text_ko") or "")[:6000]
    if text_ko:
        cache_put("review_ko", payload, {"text_ko": text_ko})
    return text_ko


COMMENT_TOPICS = [k for k, _l, _w in QUESTION_TOPICS] + ["none"]
# reason을 먼저 쓰게 함 (판단 근거 → 답 순서, CoT)
COMMENT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "reason", "relevant", "question", "topic"],
        "properties": {"id": {"type": "string"}, "reason": {"type": "string"}, "relevant": {"type": "boolean"},
                       "question": {"type": "boolean"}, "topic": {"type": "string", "enum": COMMENT_TOPICS}}}}},
}
COMMENT_EXAMPLES = Path(__file__).parent / "data" / "comment_examples.json"


def comment_examples():
    """사람이 정답을 붙인 댓글 100건 (use=example 24건은 퓨샷 예시, use=test는 정확도 검증용)."""
    return json.loads(COMMENT_EXAMPLES.read_text(encoding="utf-8"))["items"]


def _few_shot_text():
    lines = []
    for x in comment_examples():
        if x["use"] != "example":
            continue
        ans = {"reason": x["reason"], "relevant": x["relevant"], "question": x["question"], "topic": x["topic"]}
        lines.append(f"- {json.dumps(x['text'], ensure_ascii=False)} → {json.dumps(ans, ensure_ascii=False)}")
    return "\n".join(lines)


def classify_comments(rows, few_shot=True):
    """rows: [{id, text}] → {id: {reason, relevant, question, topic}}. few_shot=False는 정확도 검증의 비교용."""
    topics = ", ".join(f"{k}={label}" for k, label, _w in QUESTION_TOPICS)
    system = ("유튜브 댓글이 숙취해소 제품 · 해결책과 관련 있는지(relevant), 제품을 사기 전 궁금증을 묻는 질문인지(question) 판단하고 "
              f"질문이면 주제(topic)를 골라: {topics}. 어느 주제에도 맞지 않으면 question=false, topic=none. "
              "숙취 공감 · 한탄 · 농담('anyone else hungover?' 등), 유튜버 칭찬 · 잡담 · 광고는 relevant=false. "
              "relevant=false면 question=false, topic=none. "
              "각 댓글마다 먼저 reason에 판단 근거를 한국어 한 문장(20자 안팎)으로 쓰고, 그 근거에 맞게 나머지 칸을 채워.")
    if few_shot:
        system += "\n\n사람이 정답을 붙인 예시 (댓글 → 답):\n" + _few_shot_text()
    out = openai_json(system, {"comments": rows}, schema=COMMENT_SCHEMA, schema_name="voc", part=PART,
                      action="VOC(유튜브 댓글) 분류" + ("" if few_shot else " · 예시 없이 (정확도 검증)"), max_tokens=8000)
    return {str(i["id"]): i for i in out.get("items") or []}


# ───────── 경쟁사: 제형 분류 · 브랜드 분석 · 웹 검색 보충 ─────────

FORM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "form"],
        "properties": {"id": {"type": "string"},
                       "form": {"type": "string", "enum": ["liquid", "powder", "stick", "gummy", "pellet", "tablet", "other"]}}}}},
}


def classify_forms(rows):
    """rows: [{id, title, text}] → ({id: form}, 저장된 결과였는지). 상품명 규칙으로 못 정한 상품만."""
    data, _ = cache_get("product_forms", rows)
    if data:
        return data.get("items") or {}, True
    system = ("숙취 관련 상품의 상품명 · 판매 문구를 보고 제형을 하나 골라. liquid = 마시는 액상(샷 · 음료 · 병), "
              "powder = 분말 · 드링크 믹스 · 과립, stick = 스틱 포장(내용물이 젤리 · 분말 · 액상이어도 스틱), "
              "gummy = 구미 · 젤리 · 츄어블, pellet = 한국식 환(작은 알갱이를 물과 삼킴), "
              "tablet = 정제 · 캡슐 · 알약, other = 패치처럼 위 여섯이 아닌 것. 상품명 · 문구에 나온 말로 판단해.")
    out = openai_json(system, {"products": rows}, schema=FORM_SCHEMA, schema_name="product_forms", part=PART,
                      action="경쟁 상품 제형 분류", max_tokens=3000)
    ids = {r["id"] for r in rows}
    items = {i["id"]: i["form"] for i in out.get("items") or [] if i.get("id") in ids}
    cache_put("product_forms", rows, {"items": items})
    return items, False


_SRC = {"type": "array", "items": {"type": "string"}}
_TEXT = {"type": "object", "additionalProperties": False, "required": ["text", "sources"],
         "properties": {"text": {"type": "string"}, "sources": _SRC}}
_LIST = {"type": "object", "additionalProperties": False, "required": ["values", "sources"],
         "properties": {"values": _SRC, "sources": _SRC}}
COMPETITOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["message", "ingredients", "retail", "news", "use", "avoid"],
    "properties": {"message": _TEXT, "ingredients": _LIST, "retail": _TEXT, "news": _TEXT, "use": _TEXT, "avoid": _TEXT},
}


def analyze_competitor(payload):
    """payload: {brand, evidence:[{id, type, title, text}]} → (6개 항목, 저장된 결과였는지)."""
    data, _ = cache_get("competitor_analysis_v2", payload)
    if data:
        return data, True
    system = ("너는 미국 숙취해소제 시장을 보는 브랜드 마케터야. 당사는 미국에 진출한 한국 숙취해소 젤리 · 음료 브랜드"
              "(REBOOT, 실제 데이터는 알디콤 · Amazon · Weee! · Yami에서 판매)야. 주어진 경쟁사의 evidence(근거 자료)만 보고 채워. "
              "message = 이 브랜드가 소비자에게 내세우는 핵심 메시지 한 줄(한국어, 판매 문구 · 홈페이지 표현 기준), "
              "ingredients = 주력 성분 이름(영문 그대로, 최대 5개), retail = Amazon · Weee! · Yami 말고 근거에 나온 판매처"
              "(자사몰 · 오프라인 매장 · 다른 온라인몰), news = 근거의 뉴스 · 기사에 나온 최근 소식 1문장. "
              "이 네 항목은 sources에 근거 id(P1 · S1 · R1 · W1 · N1 …)를 꼭 넣고, 근거에 없으면 text는 빈 문자열, values는 빈 목록으로 둬. "
              "use = 알디콤이 이 경쟁사와 비교해 마케팅 문구에 쓸 만한 말 1문장, avoid = 이 경쟁사와 겹치거나 "
              "효능 단정(cure · prevent · treat)이라 피할 말 1문장 — 이 둘은 너의 판단이고 sources는 참고한 근거 id. " + SAFE)
    out = openai_json(system, payload, schema=COMPETITOR_SCHEMA, schema_name="competitor_analysis", part=PART,
                      action=f"경쟁사 분석: {payload.get('brand', '')}", max_tokens=2000, timeout=90)
    cache_put("competitor_analysis_v2", payload, out)
    return out, False


WEB_MODEL = "gpt-4o-mini-search-preview"  # OpenAI 검색 모델 (웹을 찾아보고 답함)
WEB_FIELD_HINT = {"message": "the brand's main marketing message or slogan to consumers",
                  "ingredients": "key active ingredients (up to 5)",
                  "retail": "where it is sold besides Amazon, Weee! and Yami (own online store, US retail chains, other e-commerce)",
                  "news": "one recent news item about the brand (launch, campaign, funding, retail expansion)"}
_WEB = {"type": "object", "additionalProperties": False, "required": ["text", "values", "urls"],
        "properties": {"text": {"type": "string"}, "values": _SRC, "urls": _SRC}}


def web_fill(brand, fields, month):
    """근거로 못 채운 사실 칸만 OpenAI 웹 검색으로 → ({field: {text, values, urls}}, 저장된 결과였는지). 같은 달 · 같은 칸이면 다시 안 부름."""
    payload = {"brand": brand, "fields": sorted(fields), "month": month}
    data, _ = cache_get("competitor_web", payload)
    if data:
        return data, True
    schema = {"type": "object", "additionalProperties": False, "required": sorted(fields),
              "properties": {f: _WEB for f in sorted(fields)}}
    system = ("Search the web about the hangover-relief brand given by the user (US market). For each requested field, "
              "answer only from pages you actually found and put those page URLs in urls. Write text in Korean "
              "(ingredients go in values as English names). If you cannot find a reliable source, leave text empty, "
              "values empty and urls empty. Do not guess. Do not use listicle or affiliate 'best hangover cures' pages.")
    user = {"brand": brand, "fields": {f: WEB_FIELD_HINT[f] for f in sorted(fields)}}
    out = openai_json(system, user, schema=schema, schema_name="competitor_web", model=WEB_MODEL, part=PART,
                      action=f"경쟁사 웹 검색: {brand}", max_tokens=1500, timeout=120)
    cache_put("competitor_web", payload, out)
    return out, False


# ───────── 월간 요약 · 전략 ─────────

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
MONTHLY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "insights", "competitor_trends", "changes", "personas", "swot", "target", "positioning"],
    "properties": {
        "summary": _STRS,
        "insights": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "detail", "faq", "expressions"],
            "properties": {"title": _STR, "detail": _STR, "faq": _STRS, "expressions": _STRS}}},
        "competitor_trends": _STRS,
        "changes": _STRS,
        "personas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "oneLine", "needs", "message"],
            "properties": {"name": _STR, "oneLine": _STR, "needs": _STR, "message": _STR}}},
        "swot": {"type": "object", "additionalProperties": False, "required": ["S", "W", "O", "T"],
                 "properties": {"S": _STRS, "W": _STRS, "O": _STRS, "T": _STRS}},
        "target": _STR,
        "positioning": _STR,
    },
}


def monthly_analysis(payload):
    system = ("너는 미국 시장에 진출한 한국 숙취해소 젤리 · 음료 브랜드(발표용 이름 REBOOT, 실제 데이터는 알디콤)의 시장 분석가야. "
              "주어진 한 달치 자료(진열대 · 리뷰 · 유튜브 댓글 · 검색 추이 · 지난달 전략)만 근거로 써. "
              "summary는 정확히 3줄, insights는 3개(각각 faq · expressions는 자료에 있는 고객 질문 · 표현 그대로 최대 3개), "
              "competitor_trends는 3줄(주시 중인 경쟁사 먼저), changes는 지난달 전략 대비 바뀐 점(없으면 빈 목록), "
              "personas는 최대 3개(name · oneLine · needs · message), SWOT는 칸마다 최대 3개, target과 positioning은 1문장. "
              "자료가 부족한 항목은 '자료 부족'이라고 써. " + SAFE)
    out = openai_json(system, payload, schema=MONTHLY_SCHEMA, schema_name="monthly_review", part=PART,
                      action="월간 요약 · 전략", max_tokens=6000, timeout=120)
    out["summary"] = [s for s in out.get("summary") or [] if s][:3]
    out["insights"] = (out.get("insights") or [])[:3]
    out["competitor_trends"] = (out.get("competitor_trends") or [])[:3]
    out["personas"] = (out.get("personas") or [])[:3]
    out["swot"] = {k: ((out.get("swot") or {}).get(k) or [])[:3] for k in ("S", "W", "O", "T")}
    return out
