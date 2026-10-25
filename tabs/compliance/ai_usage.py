"""AI 사용량 · 비용 기록 (2026-10-24 · GPT 검토 ⑭) — 컴플라이언스·CS의 Claude 호출을 한 줄씩 남깁니다.

팀 API 예산(10만 원)을 지키려고:
- 호출마다 모델 · 기능 · 토큰(입력·출력·캐시)과 예상 비용(USD)을 DATA_DIR/ai-usage/YYYY-MM.jsonl에 기록
- 이번 달 합계가 AI_MONTHLY_BUDGET_USD(기본 30달러 ≈ 4만 원)를 넘으면 새 AI 호출을 막음 (0이면 막지 않음)
- 80%를 넘으면 화면에 경고

단가 (100만 토큰당 USD · Claude API 공식 · 2026-10 확인): 캐시 쓰기는 입력 단가의 1.25배(5분 캐시).
비용은 예상치입니다. 실제 청구는 Claude Console의 Usage에서 확인합니다.
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from server.envkeys import get as env

PRICES = {   # model: (input, output, cache_read) · USD / 1M tokens
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_BUDGET_USD = 30.0
KRW_PER_USD = 1400   # 화면 표시용 어림 환율
_lock = threading.Lock()


def _dir():
    return Path(current_app.config["DATA_DIR"]) / "ai-usage"


def _month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def cost_usd(model, usage):
    """usage(dict) → 예상 비용. 모르는 모델(대체 모델 등)은 Opus 단가로 넉넉히 계산."""
    price_in, price_out, price_read = PRICES.get(model, PRICES[DEFAULT_MODEL])
    write = usage.get("cache_creation_input_tokens", 0) or 0
    return (usage.get("input_tokens", 0) * price_in + write * price_in * 1.25
            + (usage.get("cache_read_input_tokens", 0) or 0) * price_read
            + usage.get("output_tokens", 0) * price_out) / 1_000_000


def record(part, feature, res):
    """Claude 응답 1건의 사용량을 기록. 기록 실패는 기능을 막지 않음."""
    try:
        u = getattr(res, "usage", None)
        usage = {k: int(getattr(u, k, 0) or 0) for k in
                 ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
        model = str(getattr(res, "model", "") or DEFAULT_MODEL)
        row = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "part": part, "feature": feature,
               "model": model, **usage, "usd": round(cost_usd(model, usage), 6)}
        with _lock:
            d = _dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{_month()}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:   # noqa: BLE001 — 사용량 기록 때문에 판정·번역이 실패하면 안 됨
        pass


def _rows(month=None):
    try:
        lines = (_dir() / f"{month or _month()}.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def budget_usd():
    try:
        return max(0.0, float(env("AI_MONTHLY_BUDGET_USD", str(DEFAULT_BUDGET_USD)) or 0))
    except ValueError:
        return DEFAULT_BUDGET_USD


def summary(month=None):
    rows = _rows(month)
    by = {}
    for r in rows:
        k = f'{r.get("part")}:{r.get("feature")}'
        b = by.setdefault(k, {"part": r.get("part"), "feature": r.get("feature"), "calls": 0,
                              "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "usd": 0.0})
        b["calls"] += 1
        for f in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
            b[f] += int(r.get(f, 0) or 0)
        b["usd"] += float(r.get("usd", 0) or 0)
    total = sum(b["usd"] for b in by.values())
    limit = budget_usd()
    return {"month": month or _month(), "calls": len(rows), "usd": round(total, 4), "krw": round(total * KRW_PER_USD),
            "budget_usd": limit, "used_ratio": round(total / limit, 4) if limit else 0,
            "krw_per_usd": KRW_PER_USD, "features": sorted(by.values(), key=lambda b: -b["usd"])}


def over_budget():
    """이번 달 예상 비용이 예산을 넘었으면 화면에 보여 줄 안내 문장, 아니면 ''."""
    try:
        limit = budget_usd()
        used = sum(float(r.get("usd", 0) or 0) for r in _rows()) if limit else 0.0
    except RuntimeError:   # 앱 밖(명령줄 시험)에서는 확인하지 않음
        return ""
    if not limit:
        return ""
    if used < limit:
        return ""
    return (f"이번 달 AI 예상 비용이 한도(${limit:.0f} ≈ {limit * KRW_PER_USD:,.0f}원)를 넘어 AI 기능을 멈췄습니다 "
            f"(사용 ${used:.2f}). 규칙 판정은 그대로 쓸 수 있습니다. 한도는 AI_MONTHLY_BUDGET_USD로 바꿉니다.")
