"""한→영 번역 (Claude): 마케터가 한국어로 쓴 광고 문구를 미국용 영어 문구로 옮깁니다.

뜻을 그대로 옮기는 것이 목적입니다. 효능 표현을 빼거나 더하지 않아야 번역한 뒤의 규칙 점검이 의미가 있습니다.
판정은 화면의 규칙 점검이 하고, 번역 결과는 참고용입니다.
Flask 통합: 키는 server.envkeys(설정 › 연결 화면 → .env)에서 읽습니다.
"""
import hashlib
import json
import threading
from pathlib import Path

import anthropic
from flask import current_app

from server.envkeys import get as env

from . import ai_usage

MODEL = "claude-opus-5-5"
MAX_TEXT = 2000

SYSTEM = """You translate Korean marketing copy into natural US English for REBOOT ("Restart Your Day"), a Korean after-party brand of stick jelly and non-carbonated drinks sold in the US as conventional foods.

Translate faithfully:
- Keep the meaning, tone and every claim exactly as written. Do not soften, remove, add or "fix" any claim, including health, hangover, safety, sugar or effectiveness claims. A separate compliance check runs on your translation, so it must show what the Korean actually says.
- Write it the way a US marketer would phrase the same message (natural, concise, not word-for-word), keeping line breaks.
- Keep brand and product names, numbers, hashtags, @handles and URLs as written. Keep "REBOOT" in capitals.
- Do not invent facts that are not in the Korean text.

Return `text` (the English copy) and `note_ko`: one short Korean sentence only if something could not be translated directly (a pun, a Korea-only reference, an ambiguous phrase); otherwise an empty string."""

SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "note_ko": {"type": "string"},
    },
    "required": ["text", "note_ko"],
    "additionalProperties": False,
}


class TranslateError(Exception):
    """화면에 그대로 보여 줄 수 있는 실패 (키·내부 정보 없음)."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def available():
    return env("ANTHROPIC_API_KEY") != ""


BATCH_MAX = 200          # 한 번에 받는 줄 수 (화면 일괄 심의는 500행까지 → 화면이 나눠 보냄)
BATCH_CHUNK = 40         # Claude 한 번 호출에 넣는 줄 수
BATCH_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "text": {"type": "string"}, "note_ko": {"type": "string"}},
        "required": ["i", "text", "note_ko"], "additionalProperties": False}}},
    "required": ["items"],
    "additionalProperties": False,
}


def _call(system, content, schema, what):
    """Claude 호출 1회 → JSON. 실패는 화면에 보여 줄 수 있는 TranslateError로."""
    budget = ai_usage.over_budget()
    if budget:
        raise TranslateError(budget, 429)
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), timeout=180.0)
    try:
        res = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError:
        raise TranslateError("ANTHROPIC_API_KEY가 올바르지 않습니다 (설정 › 연결 확인).", 503)
    except anthropic.PermissionDeniedError:
        raise TranslateError("이 API 키로는 해당 모델을 쓸 수 없습니다.", 503)
    except anthropic.RateLimitError:
        raise TranslateError("요청이 많아 잠시 막혔습니다. 1분 뒤 다시 시도하세요.", 429)
    except anthropic.APIStatusError as e:
        raise TranslateError(f"AI 서버 오류 ({e.status_code}). 잠시 뒤 다시 시도하세요.")
    except anthropic.APIConnectionError:
        raise TranslateError("AI 서버에 연결하지 못했습니다 (인터넷 확인).")
    ai_usage.record("compliance", f"번역 ({what})", res)
    if res.stop_reason == "refusal":
        raise TranslateError(f"AI가 이 {what} 번역을 거절했습니다. 직접 영어로 써서 점검하세요.", 422)
    if res.stop_reason == "max_tokens":
        raise TranslateError("AI 응답이 길어 잘렸습니다. 문구를 줄여 다시 시도하세요.")
    body = next((b.text for b in res.content if b.type == "text"), "")
    try:
        return json.loads(body), res.model
    except ValueError:
        raise TranslateError("AI 응답을 읽지 못했습니다. 다시 시도하세요.")


# ───────── 번역 재사용 (같은 문장은 다시 번역하지 않음 · 비용 0 · 즉시) ─────────
# DATA_DIR/compliance/translate-cache.json · 방향(ko-en / en-ko)과 모델별로 따로 · 최근 CACHE_MAX개만
CACHE_MAX = 3000
_cache_lock = threading.Lock()
_cache = {}


def _cache_path():
    return Path(current_app.config["DATA_DIR"]) / "compliance" / "translate-cache.json"


def _cache_key(kind, text):
    return hashlib.sha256(f"{kind}|{MODEL}|{text}".encode("utf-8")).hexdigest()[:32]


def _cache_load():
    path = str(_cache_path())   # 테스트마다 DATA_DIR이 달라서 경로별로 따로 기억
    if path not in _cache:
        try:
            _cache[path] = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache[path] = {}
    return _cache[path]


def cache_get(kind, text):
    with _cache_lock:
        return _cache_load().get(_cache_key(kind, text))


def cache_put(kind, text, value):
    with _cache_lock:
        c = _cache_load()
        k = _cache_key(kind, text)
        c.pop(k, None)
        c[k] = {"text": value["text"], "note_ko": value.get("note_ko", "")}
        while len(c) > CACHE_MAX:
            c.pop(next(iter(c)))
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)


def to_english(text):
    text = str(text or "").strip()
    if not text:
        raise TranslateError("번역할 한국어 문구가 없습니다.", 400)
    if len(text) > MAX_TEXT:
        raise TranslateError(f"문구가 너무 깁니다 ({MAX_TEXT}자 이하).", 400)
    hit = cache_get("ko-en", text)
    if hit:
        return dict(hit, model=MODEL, cached=True)
    if not available():
        raise TranslateError("ANTHROPIC_API_KEY가 없습니다 (설정 › 연결 또는 .env).", 503)
    out, model = _call(SYSTEM, "Translate this Korean copy into English:\n" + text, SCHEMA, "문구")
    english = str(out.get("text", "")).strip()
    if not english:
        raise TranslateError("AI가 빈 번역을 돌려줬습니다. 다시 시도하세요.")
    res = {"text": english[:4000], "note_ko": str(out.get("note_ko", ""))[:300], "model": model}
    cache_put("ko-en", text, res)
    return res


def to_english_batch(texts):
    """일괄 심의용: 한국어 문구 여러 줄을 한 번에 → [{text, note_ko}] (입력과 같은 순서 · 같은 번역 규칙)."""
    if not isinstance(texts, list) or not texts:
        raise TranslateError("번역할 문구가 없습니다.", 400)
    if len(texts) > BATCH_MAX:
        raise TranslateError(f"한 번에 {BATCH_MAX}줄까지 번역할 수 있습니다.", 400)
    texts = [str(t or "").strip() for t in texts]
    if any(len(t) > MAX_TEXT for t in texts):
        raise TranslateError(f"{MAX_TEXT}자가 넘는 줄이 있습니다.", 400)
    result, model = [None] * len(texts), MODEL
    for i, t in enumerate(texts):   # 전에 번역한 줄은 다시 보내지 않음
        if t:
            result[i] = cache_get("ko-en", t)
    todo = [(i, t) for i, t in enumerate(texts) if t and not result[i]]
    if todo and not available():
        raise TranslateError("ANTHROPIC_API_KEY가 없습니다 (설정 › 연결 또는 .env).", 503)
    system = SYSTEM.replace("Return `text`", "Each input line is a separate piece of copy; translate each on its own and return one item per line with its index `i`.\nFor each item return `text`")
    for start in range(0, len(todo), BATCH_CHUNK):
        chunk = todo[start:start + BATCH_CHUNK]
        lines = "\n".join(json.dumps({"i": i, "ko": t}, ensure_ascii=False) for i, t in chunk)
        out, model = _call(system, "Translate each Korean line (JSON per line) into English:\n" + lines, BATCH_SCHEMA, "목록")
        for item in out.get("items", []):
            i = item.get("i")
            if isinstance(i, int) and 0 <= i < len(texts) and texts[i]:
                result[i] = {"text": str(item.get("text", "")).strip()[:4000], "note_ko": str(item.get("note_ko", ""))[:300]}
                if result[i]["text"]:
                    cache_put("ko-en", texts[i], result[i])
    missing = [i for i, t in enumerate(texts) if t and not (result[i] and result[i]["text"])]
    if missing:
        raise TranslateError(f"{len(missing)}줄을 번역하지 못했습니다. 다시 시도하세요.")
    return {"items": [r or {"text": "", "note_ko": ""} for r in result], "model": model,
            "cached": sum(1 for t in texts if t) - len(todo)}


KO_SYSTEM = """You translate English text (US ad copy, customer e-mails, CS replies, FDA/FTC notices) into natural Korean so that a Korean marketer can understand exactly what it says.
- Keep the meaning, tone and every claim exactly; do not soften, add or explain anything.
- Keep line breaks. Keep brand and product names, numbers, URLs, e-mail addresses and codes such as R2 or 21 CFR 101.60 as written.
Return `text` (the Korean translation) and `note_ko`: one short Korean note only if a phrase has a US-specific meaning worth knowing (for example "the morning after" usually means a hangover); otherwise an empty string."""


def to_korean(text):
    """영→한 (뜻 확인용 · 화면의 '한글로 번역'이 크롬 내장 번역을 못 쓸 때)."""
    text = str(text or "").strip()
    if not text:
        raise TranslateError("번역할 문구가 없습니다.", 400)
    if len(text) > 8000:
        raise TranslateError("문구가 너무 깁니다 (8000자 이하).", 400)
    hit = cache_get("en-ko", text)
    if hit:
        return dict(hit, model=MODEL, cached=True)
    if not available():
        raise TranslateError("ANTHROPIC_API_KEY가 없습니다 (설정 › 연결 또는 .env).", 503)
    out, model = _call(KO_SYSTEM, "Translate into Korean:\n" + text, SCHEMA, "문구")
    korean = str(out.get("text", "")).strip()
    if not korean:
        raise TranslateError("AI가 빈 번역을 돌려줬습니다. 다시 시도하세요.")
    res = {"text": korean[:12000], "note_ko": str(out.get("note_ko", ""))[:300], "model": model}
    cache_put("en-ko", text, res)
    return res
