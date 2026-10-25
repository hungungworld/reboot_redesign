"""AI 구조화 출력 (OpenAI Structured Outputs · strict).

strict 규칙: 모든 속성을 required에 넣고 additionalProperties: false. 형식만 맞출 뿐 내용의 사실성은 서버가 따로 검사합니다.
"""
from .options import ACTION_TYPES, INTENTS

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}


def _obj(props):
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


# 대화 한 턴 (설명서 6.1)
TURN = _obj({
    "intent": {"type": "string", "enum": list(INTENTS)},
    "assistant_text": _STR,
    "decision_patch": {"type": "array", "items": _obj({
        "field": _STR,
        "value": {"anyOf": [_STR, {"type": "boolean"}, _STR_LIST, {"type": "null"}]},
        "status": {"type": "string", "enum": ["confirmed", "proposed", "undecided"]},
    })},
    "candidates": {"type": "array", "items": _obj({"field": _STR, "label": _STR, "value": _STR, "reason": _STR})},
    "clarification_questions": _STR_LIST,
    # 질문에 붙는 보기 칩 (고르면 그 글이 사용자 메시지로 감) — 고르기 쉬운 질문에만
    "chips": {"type": "array", "items": _obj({"label": _STR, "value": _STR, "reason": _STR})},
    # 만들기 제안 — 브리프가 찼을 때 "이대로 만들까요?" (사용자가 '좋아요'로 답하면 서버가 이 type으로 실행)
    "offer": {"anyOf": [{"type": "null"}, _obj({
        "type": {"type": "string", "enum": [t for t in ACTION_TYPES if t not in ("none", "select_version")]},
        "summary": _STR,
    })]},
    "action_request": _obj({
        "type": {"type": "string", "enum": list(ACTION_TYPES)},
        "target_version_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "instructions": _STR,
    }),
})

# 텍스트 초안 (content_draft_v2)
DRAFT = _obj({
    "note": _STR,
    "drafts": {"type": "array", "items": _obj({
        "approach": _STR,
        "title": _STR,
        "body": _STR,
        "claims": {"type": "array", "items": _obj({"text": _STR, "entity_id": _STR, "fact_id": _STR})},
        "needs_check": _STR_LIST,
    })},
})

# 시트 추천 (sheet_suggestion_v2)
SUGGEST = _obj({"items": {"type": "array", "items": _obj({"value": _STR, "reason": _STR})}})

# 이미지 프롬프트 (creative_edit_v2)
IMAGE_PLAN = _obj({
    "prompt_en": _STR,
    "keep": _STR_LIST,
    "change": _STR_LIST,
    "text_layers": {"type": "array", "items": _obj({"role": _STR, "text": _STR})},
    "notes_ko": _STR,
    "flavors": {"type": "array", "items": {"type": "string", "enum": ["cherry", "lemon", "lime"]}},
    "include_character": {"type": "boolean"},
})

# 영상 프롬프트 (video_v1) — Higgsfield Seedance 2.0
VIDEO_PLAN = _obj({
    "prompt_en": _STR,
    "notes_ko": _STR,
    "flavors": {"type": "array", "items": {"type": "string", "enum": ["cherry", "lemon", "lime"]}},
    "include_character": {"type": "boolean"},
})

# 완성된 이미지·영상 → 캡션 3안 · 해시태그 (social_v1)
SOCIAL = _obj({
    "captions": {"type": "array", "items": _obj({"angle": _STR, "text": _STR})},
    "hashtags": _STR_LIST,
    "note_ko": _STR,
})

# 인터뷰 질문 하나 (interview_v1) — 보기는 서버가 정확히 3개만 씀
INTERVIEW = _obj({
    "done": {"type": "boolean"},
    "ready": {"type": "boolean"},
    "intro": _STR,
    "field": _STR,
    "label": _STR,
    "question": _STR,
    "options": {"type": "array", "items": _obj({"label": _STR, "value": _STR, "reason": _STR})},
})

# 레퍼런스 이미지 요약 (reference_v1)
REFERENCE = _obj({"summary": _STR})

# 보조 검토 (review_v2)
REVIEW = _obj({
    "summary": _STR,
    "issues": {"type": "array", "items": _obj({"location": _STR, "problem": _STR, "suggestion": _STR, "basis": _STR})},
    "checked_scope": _STR,
})
