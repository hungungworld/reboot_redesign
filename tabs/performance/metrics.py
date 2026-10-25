"""기간 지표 계산 (README 2·4장) — 화면 tabs/performance/static/metrics.js와 같은 규칙.

- 비교 기간 = 고른 기간 바로 앞의 같은 길이. CSV 시작일보다 앞으로 나가면 비교하지 않음(None)
- 합계의 ROAS · CTR · CVR · CPA는 채널 평균이 아니라 합계 값으로 다시 계산
- 증감률 = (이번 − 비교) ÷ 비교 × 100
"""
from datetime import date, timedelta

SUM_KEYS = ("spend", "impressions", "clicks", "conversions", "revenue")


def to_date(v):
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def days(start, end):
    return (to_date(end) - to_date(start)).days + 1


def derive(t):
    s, i, c, cv, r = (t.get(k, 0) or 0 for k in SUM_KEYS)
    out = dict(t)
    out["roas"] = round(r / s * 100, 2) if s else None
    out["ctr"] = round(c / i * 100, 2) if i else None
    out["cvr"] = round(cv / c * 100, 2) if c else None
    out["cpa"] = round(s / cv) if cv else None
    out["profit"] = r - s
    return out


def totals(rows):
    t = {k: 0 for k in SUM_KEYS}
    for r in rows:
        for k in SUM_KEYS:
            t[k] += r[k]
    return derive(t)


def in_period(rows, start, end):
    a, b = str(start), str(end)
    return [r for r in rows if a <= r["date"] <= b]


def pct_change(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / abs(prev) * 100, 2)


def compare_period(start, end, data_start):
    """같은 길이의 바로 앞 기간. CSV 시작일보다 앞이면 None (일부만 비교되는 왜곡 방지)."""
    start, end = to_date(start), to_date(end)
    n = days(start, end)
    pend = start - timedelta(days=1)
    pstart = start - timedelta(days=n)
    if data_start is None or pstart < to_date(data_start):
        return None
    return {"start": pstart.isoformat(), "end": pend.isoformat(), "days": n,
            "label": "전주 대비" if n == 7 else "직전 기간 대비"}


def latest_week(rng):
    """CSV 마지막 날짜 기준 최근 7일 (CSV가 7일보다 짧으면 있는 만큼)."""
    end = to_date(rng["end"])
    start = max(to_date(rng["start"]), end - timedelta(days=6))
    return start.isoformat(), end.isoformat()


def by_channel(rows, order=None):
    groups = {}
    for r in rows:
        groups.setdefault(r["channel"], []).append(r)
    names = [c for c in (order or []) if c in groups] + [c for c in groups if c not in (order or [])]
    return [dict(totals(groups[c]), channel=c) for c in names]


def daily(rows, start, end):
    """기간의 날짜마다 합계 (데이터 없는 날은 0)."""
    acc = {}
    for r in in_period(rows, start, end):
        d = acc.setdefault(r["date"], {k: 0 for k in SUM_KEYS})
        for k in SUM_KEYS:
            d[k] += r[k]
    out, d, last = [], to_date(start), to_date(end)
    while d <= last:
        key = d.isoformat()
        day = acc.get(key, {k: 0 for k in SUM_KEYS})
        out.append({"date": key, "revenue": day["revenue"], "spend": day["spend"], "conversions": day["conversions"]})
        d += timedelta(days=1)
    return out


def summarize(rows, start, end, rng, order=None):
    """기간 요약: 합계 · 비교 기간 합계 · 증감 · 채널별 · 일별."""
    cur_rows = in_period(rows, start, end)
    cur = totals(cur_rows)
    cmp = compare_period(start, end, rng["start"] if rng else None)
    prev = totals(in_period(rows, cmp["start"], cmp["end"])) if cmp else None
    change = {k: pct_change(cur[k], prev[k]) for k in ("spend", "revenue", "roas", "conversions", "cpa", "profit", "ctr", "cvr")} if prev else None
    prev_ch = {c["channel"]: c for c in by_channel(in_period(rows, cmp["start"], cmp["end"]))} if cmp else {}
    chans = []
    for c in by_channel(cur_rows, order):
        p = prev_ch.get(c["channel"])
        c["prev"] = p and {k: p[k] for k in ("spend", "revenue", "roas", "conversions", "cpa", "ctr", "cvr")}
        chans.append(c)
    return {
        "start": str(start), "end": str(end), "days": days(start, end), "rows": len(cur_rows),
        "totals": cur, "compare": cmp, "compareTotals": prev, "change": change,
        "channels": chans, "daily": daily(rows, start, end),
    }
