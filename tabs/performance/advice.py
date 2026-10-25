"""AI 해석 · 다음 주 예산 제안 (README 5장) — 한 번 호출로 해석 + 예산을 JSON으로 받음.

- 페르소나: 10년 경력 해외 마케팅 담당자 · 성과분석 전문 컨설턴트
- 퍼널(노출 → 클릭 → 전환) 단계별 원인, 주어진 숫자에만 근거
- 예산 총액은 기준 광고비(이번 기간 광고비, 7일이 아니면 주 단위로 환산)의 ±20% 안 — 벗어나면 서버가 비율을 유지한 채 맞춤
"""
from server.catalog import service as catalog
from server.core.ai import AIError, openai_json, openai_model
from . import metrics

BAND = 0.20
ROUND_TO = 1000
SEASON_WINDOW_DAYS = 75

SYSTEM = """당신은 10년 경력의 해외 마케팅 담당자이자 성과분석 전문 컨설턴트입니다.
한국 숙취해소 젤리·음료 브랜드 REBOOT(일반 식품)의 미국 시장 광고 성과를 분석해 한국인 마케터에게 조언합니다.

규칙
- 사용자 메시지(JSON)에 있는 숫자만 근거로 씁니다. 업계 평균·시장 규모·경쟁사 수치처럼 주어지지 않은 숫자나 사실을 만들지 않습니다.
- 퍼널(노출 → 클릭 → 전환) 단계별로 원인을 찾습니다: CTR(클릭÷노출), CVR(전환÷클릭), CPA(광고비÷전환), ROAS(매출÷광고비).
- compare가 null이면 증감·추세를 말하지 않습니다.
- data_source가 "sample"이면 샘플(가상) 데이터라는 점을 overview 첫 문장에 밝힙니다.
- 제품 효능(숙취 해소·예방 등)을 단정하는 문구를 제안하지 않습니다 (미국 식품 광고 규제).
- 모든 문장은 한국어로, 짧고 구체적으로 씁니다. 금액은 원 단위.

출력
- overview: 이번 기간 성과 총평 2~3문장
- analysis.good: ROAS가 가장 높은 채널의 해석과 강점을 키우는 방법 (2~4문장)
- analysis.improve: 개선이 가장 시급한 채널, 퍼널에서 문제가 되는 단계, 개선 방향 (2~4문장)
- budget: 다음 주 채널별 예산(원, 정수). channels에 있는 채널 이름을 그대로, 모든 채널을 한 번씩. 총액은 budget_base.min ~ budget_base.max. reason은 한 문장
- actions: upcoming_seasons(다가오는 미국 음주 시즌)에 대비한 실행 과제 2~3개. title(짧게), detail(1~2문장), season(관련 시즌 이름 그대로, 없으면 빈 문자열)"""

SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "analysis": {
            "type": "object",
            "properties": {"good": {"type": "string"}, "improve": {"type": "string"}},
            "required": ["good", "improve"],
            "additionalProperties": False,
        },
        "budget": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"channel": {"type": "string"}, "amount": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["channel", "amount", "reason"],
                "additionalProperties": False,
            },
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "detail": {"type": "string"}, "season": {"type": "string"}},
                "required": ["title", "detail", "season"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "analysis", "budget", "actions"],
    "additionalProperties": False,
}

_CH_KEYS = ("spend", "impressions", "clicks", "conversions", "revenue", "roas", "ctr", "cvr", "cpa", "profit")


def _txt(v, n):
    return str(v if v is not None else "").strip()[:n]


def upcoming_seasons(today=None):
    out = []
    for s in catalog.list_seasons(today=today):
        if s["dday"] <= SEASON_WINDOW_DAYS:
            out.append({"name": s["name"], "start": s["start"], "end": s["end"], "dday": s["dday"],
                        "inLead": s["inLead"], "ongoing": s["ongoing"], "leadDays": s["leadDays"],
                        "priority": s["priority"], "notes": s["notes"]})
    return out[:6]


def budget_base(summary):
    """다음 주 예산의 기준: 이번 기간 광고비 (7일이 아니면 7일 기준으로 환산)."""
    n = summary["days"]
    spend = summary["totals"]["spend"]
    weekly = round(spend * 7 / n) if n else 0
    chans = {c["channel"]: round(c["spend"] * 7 / n) if n else 0 for c in summary["channels"]}
    return {"weekly_spend": weekly, "min": round(weekly * (1 - BAND)), "max": round(weekly * (1 + BAND)),
            "scaled": n != 7, "days": n, "channels": chans}


def build_input(summary, source, today=None):
    base = budget_base(summary)
    return {
        "brand": "REBOOT (한국 숙취해소 젤리·음료, 미국 진출 초기)",
        "currency": "KRW",
        "data_source": source,
        "period": {"start": summary["start"], "end": summary["end"], "days": summary["days"]},
        "compare": summary["compare"],
        "totals": {k: summary["totals"][k] for k in _CH_KEYS},
        "compare_totals": summary["compareTotals"] and {k: summary["compareTotals"][k] for k in _CH_KEYS},
        "change_pct": summary["change"],
        "channels": [dict({k: c[k] for k in _CH_KEYS}, channel=c["channel"], compare=c.get("prev"))
                     for c in summary["channels"]],
        "budget_base": {"weekly_spend": base["weekly_spend"], "min": base["min"], "max": base["max"],
                        "note": "이번 기간이 7일이 아니어서 7일 기준으로 환산" if base["scaled"] else "이번 기간(7일) 광고비"},
        "upcoming_seasons": upcoming_seasons(today),
    }


def _round(n):
    return int(round(n / ROUND_TO)) * ROUND_TO


def normalize_budget(items, summary):
    """AI 예산 → 채널 이름 맞추기 · 빠진 채널 채우기 · 총액 ±20% 맞추기."""
    base = budget_base(summary)
    current = base["channels"]
    names = list(current)
    lookup = {n.lower(): n for n in names}
    got = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = lookup.get(_txt(it.get("channel"), 60).lower())
        if not name or name in got:
            continue  # 없는 채널 이름은 버림
        try:
            amount = max(0, int(it.get("amount") or 0))
        except (TypeError, ValueError):
            amount = 0
        got[name] = {"amount": amount, "reason": _txt(it.get("reason"), 300)}
    notes = []
    for n in names:
        if n not in got:
            got[n] = {"amount": current[n], "reason": "AI 제안에 빠져 이번 기간 광고비를 그대로 두었습니다."}
            notes.append(f"{n}: AI 제안 없음 → 유지")
    total = sum(v["amount"] for v in got.values())
    lo, hi = base["min"], base["max"]
    adjusted = False
    if base["weekly_spend"] > 0 and total <= 0:
        for n in names:
            got[n]["amount"] = current[n]
        notes.insert(0, "AI 제안 금액이 0원이라 이번 기간 광고비를 그대로 두었습니다.")
        total = sum(v["amount"] for v in got.values())
    if base["weekly_spend"] > 0 and total > 0 and not lo <= total <= hi:
        target = hi if total > hi else lo
        factor = target / total
        for v in got.values():
            v["amount"] = v["amount"] * factor
        adjusted = True
    for v in got.values():
        v["amount"] = _round(v["amount"])
    total = sum(v["amount"] for v in got.values())
    if base["weekly_spend"] > 0 and got:
        biggest = max(got.values(), key=lambda v: v["amount"])
        if total > hi:
            biggest["amount"] = max(0, biggest["amount"] - (total - hi))
        elif total < lo:
            biggest["amount"] += lo - total
        total = sum(v["amount"] for v in got.values())
    if adjusted:
        notes.insert(0, "AI 제안 총액이 기준의 ±20%를 벗어나 채널 비율을 유지한 채 맞췄습니다.")
    out_items = []
    for n in names:
        v = got[n]
        cur = current[n]
        out_items.append({"channel": n, "amount": v["amount"], "current": cur, "delta": v["amount"] - cur,
                          "deltaPct": metrics.pct_change(v["amount"], cur),
                          "share": round(v["amount"] / total * 100, 1) if total else 0, "reason": v["reason"]})
    weekly = base["weekly_spend"]
    return {"total": total, "current": weekly, "delta": total - weekly, "deltaPct": metrics.pct_change(total, weekly),
            "min": lo, "max": hi, "scaled": base["scaled"], "days": base["days"], "adjusted": adjusted,
            "notes": notes, "items": out_items}


def advise(summary, source, today=None):
    if not summary["channels"] or summary["totals"]["spend"] <= 0:
        raise AIError("이 기간에는 광고비 데이터가 없어 AI 제안을 만들 수 없습니다.", 400)
    data = build_input(summary, source, today)
    raw = openai_json(SYSTEM, data, schema=SCHEMA, schema_name="performance_advice", max_tokens=3000,
                      part="performance", action=f"성과 AI 해석·예산 제안 ({summary['start']} ~ {summary['end']})")
    analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
    actions = []
    for a in (raw.get("actions") or [])[:3]:
        if isinstance(a, dict) and _txt(a.get("title"), 120):
            actions.append({"title": _txt(a.get("title"), 120), "detail": _txt(a.get("detail"), 400),
                            "season": _txt(a.get("season"), 100)})
    return {
        "overview": _txt(raw.get("overview"), 800),
        "analysis": {"good": _txt(analysis.get("good"), 1000), "improve": _txt(analysis.get("improve"), 1000)},
        "budget": normalize_budget(raw.get("budget"), summary),
        "actions": actions,
        "model": openai_model(),
        "seasons": [s["name"] for s in data["upcoming_seasons"]],
    }


# ───────── 채널별 성과 표 위 AI 인사이트 (두괄식) — 위 예산 제안과 따로 부름 ─────────
INSIGHT_SYSTEM = """당신은 10년 경력의 전략 마케팅 전문가입니다.
한국 숙취해소 젤리·음료 브랜드 REBOOT(일반 식품)의 미국 시장 광고 성과를 보고, 바쁜 한국인 마케터가 표를 보기 전에 읽을 핵심을 두괄식으로 짚어 줍니다.

규칙
- 사용자 메시지(JSON)에 있는 숫자만 근거로 씁니다. 주어지지 않은 숫자나 사실을 만들지 않습니다. 비중(%)처럼 계산한 값은 주어진 숫자로 직접 계산합니다.
- compare가 null이면 증감·추세를 말하지 않습니다.
- data_source가 "sample"이면 headline을 "샘플 데이터 기준,"으로 시작합니다.
- 제품 효능(숙취 해소·예방 등)을 단정하는 문구를 쓰지 않습니다.
- 숫자 형식: ROAS·CTR·CVR은 %를 붙이고 ROAS는 정수(예: 365%), CTR·CVR은 소수 둘째 자리까지(예: 0.99%). 금액은 ₩와 천 단위 쉼표(예: ₩7,295,000). 개수는 천 단위 쉼표.
- 숫자와 핵심 어휘(채널 이름, 지표 이름, 핵심 판단)는 **이렇게** 굵게 감쌉니다. 한 문장에 굵게는 3곳 이하.
- 한국어, 문장은 짧게(80자 안팎).

출력
- headline: 결론 한 문장. 이번 기간 성과에서 가장 중요한 판단
- points: 2~4개. label은 근거·원인·판단 같은 2~4글자 이름표, text는 한 문장
  (근거: 결론을 받치는 숫자 / 원인: 퍼널 CTR·CVR·CPA에서 찾은 이유 / 판단: 지금 가장 먼저 할 전략적 행동)"""

INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "text": {"type": "string"}},
                "required": ["label", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "points"],
    "additionalProperties": False,
}


def insight(summary, source, today=None):
    if not summary["channels"] or summary["totals"]["spend"] <= 0:
        raise AIError("이 기간에는 광고비 데이터가 없어 AI 인사이트를 만들 수 없습니다.", 400)
    data = build_input(summary, source, today)
    data.pop("budget_base", None)
    data.pop("upcoming_seasons", None)
    raw = openai_json(INSIGHT_SYSTEM, data, schema=INSIGHT_SCHEMA, schema_name="performance_insight", max_tokens=800,
                      part="performance", action=f"성과 AI 인사이트 ({summary['start']} ~ {summary['end']})")
    headline = _txt(raw.get("headline"), 300)
    points = []
    for p in (raw.get("points") or [])[:4]:
        if isinstance(p, dict) and _txt(p.get("text"), 300):
            points.append({"label": _txt(p.get("label"), 10), "text": _txt(p.get("text"), 300)})
    if not headline:
        raise AIError("AI가 인사이트를 돌려주지 않았습니다. 다시 만들기를 눌러 주세요.")
    return {"headline": headline, "points": points, "model": openai_model()}
