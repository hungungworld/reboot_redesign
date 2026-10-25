"""이번 주 To-Do (README 3장) — 시즌 캘린더 + 뉴스 동향·급증 시그널 + 파트별 현황을 모아 이번 주 할 일 생성.

- OpenAI 키가 있으면 AI가 종합 (origin=ai), 없거나 실패하면 규칙으로 (origin=rule) — 각 항목에 근거(reason)
- 주(월~일)마다 처음 열 때 한 번 자동 생성 (표시: home_todo_weeks). '다시 만들기'는 완료 안 한 자동 항목만 바꿈
- 직접 추가한 항목(origin=manual)과 완료한 항목은 다시 만들어도 그대로
"""
from datetime import timedelta

from server import jobs, worklog
from server.catalog import service as catalog
from server.core.ai import AIError, openai_json, openai_ready
from server.extensions import db
from server.models import iso, utcnow
from . import news
from .models import HomeTodo, HomeTodoWeek

SOURCES = ("home", "insights", "content", "compliance", "cs", "partners", "performance", "settings")
MAX_ITEMS = 8

AI_SYSTEM = """당신은 미국에 진출한 한국 숙취해소 젤리·음료 브랜드 REBOOT(일반 식품) 마케팅 팀의 리더입니다.
주어진 시즌 캘린더 · 뉴스 동향 · 파트별 현황만 근거로 이번 주(월~일)에 실행할 To-Do를 3~7개 만듭니다.

규칙
- title: 바로 실행할 수 있는 구체적인 행동 (40자 이내)
- reason: 근거 한 문장 — 어떤 시즌(D-day)·뉴스·현황 숫자 때문인지 적습니다
- source: 담당 메뉴 id — home(데일리 브리핑) · insights(시장·고객 인사이트) · content(콘텐츠 제작) · compliance(광고 문구 점검) · cs(고객 응대) · partners(인플루언서·파트너) · performance(성과분석) · settings(설정)
- 준비 기간(inLead)에 들어왔거나 진행 중(ongoing)인 시즌을 먼저 다룹니다.
- 주어지지 않은 숫자·사실을 만들지 않고, 근거가 없는 항목은 만들지 않습니다. performance.sample이 true면 성과 숫자는 샘플이라 근거로 쓰지 않습니다.
- 숙취 해소 효능을 단정하는 문구를 쓰지 않습니다.
- 한국어로 씁니다."""

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                    "source": {"type": "string", "enum": list(SOURCES)},
                },
                "required": ["title", "reason", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def week_start(d=None):
    d = d or catalog._today()
    return d - timedelta(days=d.weekday())


def _int(v, hi=1_000_000):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(0, min(hi, n))


def _num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) < 1e13 else None


def clean_context(raw):
    """화면이 보낸 파트별 현황 (숫자만 받음)."""
    raw = raw if isinstance(raw, dict) else {}
    ctx = {}
    cs = raw.get("cs") if isinstance(raw.get("cs"), dict) else None
    if cs and _int(cs.get("open")) is not None:
        ctx["cs"] = {k: _int(cs.get(k)) or 0 for k in ("open", "urgent", "overdue", "owner_required")}
    comp = raw.get("compliance") if isinstance(raw.get("compliance"), dict) else None
    if comp and _int(comp.get("pending")) is not None:
        ctx["compliance"] = {"pending": _int(comp.get("pending"))}
    perf = raw.get("performance") if isinstance(raw.get("performance"), dict) else None
    if perf and _num(perf.get("revenue")) is not None:
        ch = perf.get("change") if isinstance(perf.get("change"), dict) else {}
        ctx["performance"] = {"start": str(perf.get("start") or "")[:10], "end": str(perf.get("end") or "")[:10],
                              "revenue": _num(perf.get("revenue")), "spend": _num(perf.get("spend")),
                              "roas": _num(perf.get("roas")), "conversions": _num(perf.get("conversions")),
                              "change": {k: _num(ch.get(k)) for k in ("revenue", "roas")}, "sample": bool(perf.get("sample"))}
    content = raw.get("content") if isinstance(raw.get("content"), dict) else None
    if content and _int(content.get("active")) is not None:
        ctx["content"] = {"active": _int(content.get("active")), "total": _int(content.get("total")) or 0}
    return ctx


def _seasons(today):
    return [s for s in catalog.list_seasons(today=today) if s["dday"] <= 120]


def rule_items(today, ctx, brief):
    """키가 없을 때: 시즌 준비 기간 · 파트 현황 · 급증 시그널로 만드는 규칙 기반 할 일."""
    items = []
    seasons = _seasons(today)
    hot = sorted([s for s in seasons if s["ongoing"] or s["inLead"]], key=lambda s: s["dday"])
    for s in hot[:3]:
        if s["ongoing"]:
            items.append({"title": f"{s['name']} 진행 중 — 게시 일정·재고 확인",
                          "reason": f"{s['name']} 진행 중 ({s['start']} ~ {s['end']})", "source": "content"})
            continue
        items.append({"title": f"{s['name']} 캠페인 소재·카피 준비",
                      "reason": f"{s['name']} D-{s['dday']} · 준비 기간({s['leadDays']}일)에 들어옴", "source": "content"})
        if s["priority"] == 1:
            items.append({"title": f"{s['name']} 광고 문구 컴플라이언스 사전 점검",
                          "reason": f"우선순위 1 시즌 · D-{s['dday']}", "source": "compliance"})
    for s in seasons:
        if s["ongoing"] or s["inLead"]:
            continue
        to_lead = s["dday"] - s["leadDays"]
        if 0 < to_lead <= 14:
            lead_day = today + timedelta(days=to_lead)
            items.append({"title": f"{s['name']} 준비 일정 잡기",
                          "reason": f"준비 시작 {lead_day:%m.%d} (D-{to_lead}) · 시즌 {s['start']}", "source": "content"})
            break
    cs = ctx.get("cs")
    if cs and cs["open"] > 0:
        items.append({"title": f"미처리 CS 문의 {cs['open']}건 답변",
                      "reason": f"CS 미처리 {cs['open']}건 (긴급 {cs['urgent']} · 기한 초과 {cs['overdue']})", "source": "cs"})
    comp = ctx.get("compliance")
    if comp and comp["pending"] > 0:
        items.append({"title": f"검토 대기 카피 {comp['pending']}건 확인",
                      "reason": f"컴플라이언스 검토 요청 {comp['pending']}건", "source": "compliance"})
    perf = ctx.get("performance")
    if perf:
        if perf["sample"]:
            items.append({"title": "실제 성과 CSV 올리기", "reason": "성과분석이 아직 샘플 데이터로 표시됨", "source": "performance"})
        else:
            chg = perf["change"].get("roas")
            change = f" · 전주 대비 {chg:+.1f}%" if chg is not None else ""
            roas = f"ROAS {perf['roas']:.0f}%" if perf["roas"] is not None else "성과"
            items.append({"title": "지난주 채널별 성과 확인 · 다음 주 예산 점검",
                          "reason": f"최근 7일({perf['start']} ~ {perf['end']}) {roas}{change}", "source": "performance"})
    surges = [s for s in (brief or {}).get("signals", []) if s.get("surge")][:2]
    for s in surges:
        pct = f"+{s['changePct']:.0f}%" if s.get("changePct") is not None else "새로 등장"
        items.append({"title": f"'{s['label']}' 보도량 급증 기사 확인",
                      "reason": f"최근 7일 보도량 전주 대비 {pct} (구글 뉴스)", "source": "home"})
    if not items:
        items.append({"title": "이번 주 시즌·뉴스 확인", "reason": "준비 기간에 들어온 시즌과 급증 시그널이 없음", "source": "home"})
    return items[:MAX_ITEMS]


def ai_items(today, ctx, brief):
    ws = week_start(today)
    data = {
        "today": today.isoformat(), "week": {"start": ws.isoformat(), "end": (ws + timedelta(days=6)).isoformat()},
        "seasons": [{k: s[k] for k in ("name", "start", "end", "dday", "inLead", "ongoing", "leadDays", "priority", "notes")}
                    for s in _seasons(today)],
        "news": brief, "parts": ctx,
    }
    raw = openai_json(AI_SYSTEM, data, schema=AI_SCHEMA, schema_name="weekly_todos", max_tokens=1500,
                      part="home", action="이번 주 To-Do 생성")
    out = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()[:200]
        if not title:
            continue
        src = it.get("source") if it.get("source") in SOURCES else "home"
        out.append({"title": title, "reason": str(it.get("reason") or "").strip()[:500], "source": src})
    if not out:
        raise AIError("AI가 할 일을 만들지 못했습니다.")
    return out[:MAX_ITEMS]


def week_payload(ws=None):
    ws = ws or week_start()
    rows = HomeTodo.query.filter_by(week_start=ws).order_by(HomeTodo.done, HomeTodo.sort, HomeTodo.id).all()
    marker = db.session.get(HomeTodoWeek, ws)
    return {"week": {"start": ws.isoformat(), "end": (ws + timedelta(days=6)).isoformat()},
            "items": [r.to_dict() for r in rows], "open": sum(1 for r in rows if not r.done),
            "generated": marker is not None, "generatedAt": iso(marker.generated_at) if marker else None,
            "origin": marker.origin if marker else None, "aiReady": openai_ready(),
            "today": catalog._today().isoformat()}


def generate(context=None, auto=False, user="시스템"):
    """이번 주 할 일 만들기. auto면 이미 만든 주는 건너뜀. → week_payload + {skipped, warning}"""
    today = catalog._today()
    ws = week_start(today)
    try:
        with jobs.file_lock("home-todos", stale_seconds=300):
            marker = db.session.get(HomeTodoWeek, ws)
            if auto and marker is not None:
                return dict(week_payload(ws), skipped=True, warning="")
            ctx = clean_context(context)
            brief = news.brief()
            origin, warning = "rule", ""
            if openai_ready():
                try:
                    items, origin = ai_items(today, ctx, brief), "ai"
                except AIError as e:
                    items, warning = rule_items(today, ctx, brief), f"AI로 만들지 못해 규칙으로 만들었습니다 ({e})"
            else:
                items = rule_items(today, ctx, brief)
            # 완료 안 한 자동 항목만 바꿈 (직접 추가 · 완료 항목은 그대로)
            HomeTodo.query.filter(HomeTodo.week_start == ws, HomeTodo.origin.in_(("ai", "rule")),
                                  HomeTodo.done.is_(False)).delete(synchronize_session=False)
            keep = {t.title for t in HomeTodo.query.filter_by(week_start=ws).all()}
            base = (db.session.query(db.func.max(HomeTodo.sort)).filter(HomeTodo.week_start == ws).scalar() or 0) + 1
            added = 0
            for i, it in enumerate(items):
                if it["title"] in keep:
                    continue
                keep.add(it["title"])
                db.session.add(HomeTodo(week_start=ws, title=it["title"], reason=it["reason"], source=it["source"],
                                        origin=origin, sort=base + i, created_by=user))
                added += 1
            if marker is None:
                marker = HomeTodoWeek(week_start=ws)
                db.session.add(marker)
            marker.origin, marker.generated_at, marker.generated_by = origin, utcnow(), str(user or "")[:100]
            marker.note = warning[:300]
            db.session.commit()
            worklog.add("home", "기타", f"이번 주 To-Do {'AI' if origin == 'ai' else '규칙'} 생성 ({added}개)",
                        detail={"week": ws.isoformat(), "auto": bool(auto), "warning": warning}, user_name=user)
            return dict(week_payload(ws), skipped=False, warning=warning)
    except jobs.Busy:
        return dict(week_payload(ws), skipped=True, warning="다른 사람이 지금 만들고 있습니다. 잠시 뒤 새로고침하세요.")
