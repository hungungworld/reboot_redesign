"""규제 모니터링 항목의 한글 제목·한 줄 요약 (Claude).

수집한 영어 제목·요약을 사람이 빨리 훑을 수 있게 한글로 붙입니다. 원문을 대신하지 않는 참고용이며,
원문에 없는 사실은 만들지 않습니다. 같은 항목은 DATA_DIR/cache/compliance-ko.json에 저장해 다시 부르지 않습니다.
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
BATCH = 40
_lock = threading.Lock()

SYSTEM = """You help a Korean marketing team at REBOOT (Korean after-party stick jelly and non-carbonated drinks, incl. sugar-reduced versions, sold in the US as conventional foods) skim US regulatory monitoring results.
For each item you get its source, English title and English summary. Return for every item:
- title_ko: a natural Korean translation of the title (keep product names, company names, case names and document numbers as written).
- summary_ko: one short Korean sentence (under 90 characters) saying what the item is, based only on the given text. If the item clearly matters for a jelly snack, a bottled drink, a hangover product, sugar or nutrition labeling, food labeling, or food/supplement advertising, add why briefly; if it does not, end with "(REBOOT 직접 관련 낮음)".
Never add facts that are not in the given text. Keep the same id."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title_ko": {"type": "string"},
                    "summary_ko": {"type": "string"},
                },
                "required": ["id", "title_ko", "summary_ko"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class KoError(Exception):
    pass


def available():
    return env("ANTHROPIC_API_KEY") != ""


def _key(item):
    raw = json.dumps([item.get("source", ""), item.get("id", ""), item.get("title", ""), item.get("summary", "")], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_path():
    return Path(current_app.config["DATA_DIR"]) / "cache" / "compliance-ko.json"


def _load_cache():
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _call(client, batch):
    payload = [{"id": str(i), "source": b.get("source", "")[:80], "title": b.get("title", "")[:300], "summary": b.get("summary", "")[:500]}
               for i, b in enumerate(batch)]
    budget = ai_usage.over_budget()
    if budget:
        raise KoError(budget)
    try:
        res = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.AuthenticationError:
        raise KoError("ANTHROPIC_API_KEY가 올바르지 않습니다")
    except anthropic.RateLimitError:
        raise KoError("요청이 많아 잠시 막혔습니다")
    except anthropic.APIStatusError as e:
        raise KoError(f"AI 서버 오류 ({e.status_code})")
    except anthropic.APIConnectionError:
        raise KoError("AI 서버에 연결하지 못했습니다")
    ai_usage.record("compliance", "한글 요약 (모니터링)", res)
    if res.stop_reason in ("refusal", "max_tokens"):
        raise KoError(f"AI 응답 중단 ({res.stop_reason})")
    text = next((b.text for b in res.content if b.type == "text"), "")
    try:
        rows = json.loads(text).get("items", [])
    except ValueError:
        raise KoError("AI 응답을 읽지 못했습니다")
    out = {}
    for r in rows:
        try:
            idx = int(r.get("id", ""))
        except ValueError:
            continue
        if 0 <= idx < len(batch):
            out[idx] = {"title_ko": str(r.get("title_ko", ""))[:300], "summary_ko": str(r.get("summary_ko", ""))[:200]}
    return out


def summarize(items):
    """items: [{source, id, title, summary}] → 같은 순서의 [{title_ko, summary_ko} | None]."""
    if not available():
        raise KoError("ANTHROPIC_API_KEY가 없습니다 (설정 › 연결 또는 .env)")
    with _lock:
        cache = _load_cache()
    keys = [_key(it) for it in items]
    todo = [i for i, k in enumerate(keys) if k not in cache]
    if todo:
        client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), timeout=180.0)
        for start in range(0, len(todo), BATCH):
            idxs = todo[start:start + BATCH]
            got = _call(client, [items[i] for i in idxs])
            for pos, i in enumerate(idxs):
                if pos in got:
                    cache[keys[i]] = got[pos]
        with _lock:
            merged = _load_cache()
            merged.update(cache)
            _save_cache(merged)
    return [cache.get(k) for k in keys]


def enrich_sources(sources):
    """refresh.py 결과(sources)에 title_ko·summary_ko를 붙임 (제자리 수정)."""
    flat = [(src, it) for src in sources for it in src.get("items", [])]
    if not flat:
        return
    ko = summarize([{"source": src.get("label", ""), "id": it.get("id", ""), "title": it.get("title", ""),
                     "summary": f'{it.get("firm", "")} · {it.get("summary", "")}'} for src, it in flat])
    for (src, it), k in zip(flat, ko):
        if k:
            it["title_ko"] = k["title_ko"]
            it["summary_ko"] = k["summary_ko"]
