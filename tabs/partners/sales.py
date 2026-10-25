"""크리에이터별 판매 실적 — 🧪 더미 데이터 (README 3-5). Shopify 연동 전 화면·성과분석 연결 확인용.

- 인플루언서 상위 12곳(가져온 순서, 번호가 숫자가 아닌 IX 예시 행 제외) × 최근 60일
  가져온 인플루언서가 없으면 'Creator A' … 'Creator L' (실존 인물 아님)
- 이름·날짜마다 고정 시드 → 같은 이름·날짜는 언제 불러도 같은 값 (결정적)
- 크리에이터별 할인코드 주문 (환불 약 5% → 합계에서 제외) · 방문(접속량) · 가상 단가 UNIT_PRICE 달러
Shopify를 연동하면 _day()를 Orders API(discount_codes) 집계로, 접속량은 UTM·추적 링크 집계로 바꿉니다.
"""
import hashlib
import random
import re
from datetime import date, timedelta
from functools import lru_cache

DAYS = 60
TOP = 12
UNIT_PRICE = 24.0          # 가상 단가 (실제 가격 미정 · 팩트시트 price가 null)
REFUND_RATE = 0.05
PLACEHOLDERS = tuple(f"Creator {c}" for c in "ABCDEFGHIJKL")
WEEKDAY = (0.85, 0.85, 0.9, 1.0, 1.25, 1.35, 1.1)   # 월~일 (주말 접속 많음)


def creator_list(names):
    names = [str(n).strip() for n in (names or []) if str(n or "").strip()][:TOP]
    return (names, "imported") if names else (list(PLACEHOLDERS), "placeholder")


def _code(name, i, used):
    base = re.sub(r"[^A-Z0-9]", "", name.upper())[:10] or f"RB{i + 1:02d}"
    code, k = base + "10", 2
    while code in used:
        code, k = f"{base}{k}10", k + 1
    used.add(code)
    return code


def _profile(name):
    h = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], 16)
    popularity = 40 + h % 260                  # 하루 평균 접속 40~300
    conv = 0.012 + (h // 260) % 30 / 1000      # 전환율 1.2%~4.1%
    return popularity, conv


@lru_cache(maxsize=8192)
def _day(name, day_iso):
    popularity, conv = _profile(name)
    d = date.fromisoformat(day_iso)
    rng = random.Random(f"{name}|{day_iso}")   # 문자열 시드는 sha512 기반 → 실행마다 같음
    visits = max(0, int(round(popularity * WEEKDAY[d.weekday()] * rng.uniform(0.6, 1.4))))
    orders = units = refunds = 0
    for _ in range(visits):
        if rng.random() < conv:
            u = rng.choice((1, 1, 1, 2, 2, 3))
            if rng.random() < REFUND_RATE:
                refunds += 1
                continue
            orders += 1
            units += u
    return visits, orders, units, round(units * UNIT_PRICE, 2), refunds


def window(today):
    return today - timedelta(days=DAYS - 1), today


def _pct(a, b):
    return round(a / b * 100, 2) if b else 0.0


def _change(cur, prev):
    """이전 같은 길이 기간 대비 증감률(%) — 이전 값이 0이면 None"""
    return round((cur - prev) / prev * 100, 1) if prev else None


def report(names, today, start=None, end=None):
    lo, hi = window(today)
    end = min(max(end or hi, lo), hi)
    start = min(max(start or max(lo, end - timedelta(days=29)), lo), hi)
    if start > end:
        start, end = end, start
    creators, source = creator_list(names)
    used, rows = set(), []
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    daily = {d: {"date": d.isoformat(), "visits": 0, "orders": 0, "units": 0, "revenue": 0.0} for d in days}
    prev_days = [start - timedelta(days=i) for i in range(len(days), 0, -1)]   # 바로 앞 같은 길이 기간 (상승률용)
    prev_total = 0.0
    for i, name in enumerate(creators):
        t = {"visits": 0, "orders": 0, "units": 0, "revenue": 0.0, "refunds": 0}
        cdaily = []
        prev = sum(_day(name, d.isoformat())[3] for d in prev_days)
        prev_total += prev
        for d in days:
            v, o, u, r, f = _day(name, d.isoformat())
            cdaily.append({"date": d.isoformat(), "visits": v, "orders": o, "revenue": r})
            t["visits"] += v
            t["orders"] += o
            t["units"] += u
            t["revenue"] += r
            t["refunds"] += f
            dd = daily[d]
            dd["visits"] += v
            dd["orders"] += o
            dd["units"] += u
            dd["revenue"] += r
        rows.append({"name": name, "code": _code(name, i, used), "visits": t["visits"], "orders": t["orders"],
                     "units": t["units"], "revenue": round(t["revenue"], 2), "refunds": t["refunds"],
                     "conversion": _pct(t["orders"], t["visits"]), "daily": cdaily,
                     "prevRevenue": round(prev, 2), "change": _change(t["revenue"], prev)})
    rows.sort(key=lambda r: (-r["revenue"], r["name"]))
    totals = {k: sum(r[k] for r in rows) for k in ("visits", "orders", "units", "refunds")}
    totals["revenue"] = round(sum(r["revenue"] for r in rows), 2)
    totals["conversion"] = _pct(totals["orders"], totals["visits"])
    totals["prevRevenue"] = round(prev_total, 2)
    totals["change"] = _change(totals["revenue"], prev_total)
    for dd in daily.values():
        dd["revenue"] = round(dd["revenue"], 2)
    return {"dummy": True, "source": source, "start": start.isoformat(), "end": end.isoformat(),
            "range": {"min": lo.isoformat(), "max": hi.isoformat()}, "unitPrice": UNIT_PRICE, "currency": "USD",
            "creators": rows, "daily": [daily[d] for d in days], "totals": totals,
            "note": "더미 데이터 — Shopify 할인코드 주문·UTM 방문 연동 전 가상값입니다 (환불 주문 제외)."}
