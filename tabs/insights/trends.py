"""Google 검색 트렌드 (SerpApi · 미국 · 지난 12개월).

- 키워드마다 따로 조회 (같이 조회하면 큰 키워드가 작은 키워드를 납작하게 만듦 — README)
- 키가 없으면 Google Trends에서 받은 CSV(multiTimeline.csv)를 올려 대신 씀
"""
import csv
import io
import json
import re
from datetime import date, datetime, timezone

from server.extensions import db
from server.models import utcnow
from . import http
from .amazon import SERP, _ok
from .models import TrendSeries
from .util import clean

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    if s.startswith("<"):
        return 0.0
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _date_from_text(s):
    """'Oct 26 – Nov 1, 2025' · 'Oct 26, 2025' → 2025-10-26 (첫 날짜)."""
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\s+(\d{1,2})(?:,\s*(\d{4}))?", str(s or ""))
    year = re.search(r"(\d{4})", str(s or ""))
    if not m or not year:
        return None
    y = int(m.group(3) or year.group(1))
    try:
        return date(y, MONTHS[m.group(1).lower()], int(m.group(2)))
    except (KeyError, ValueError):
        return None


def parse_timeseries(data):
    rows = ((data or {}).get("interest_over_time") or {}).get("timeline_data") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = None
        ts = row.get("timestamp")
        if ts is not None and str(ts).isdigit():
            d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        if d is None:
            d = _date_from_text(row.get("date"))
        vals = row.get("values") or []
        v = vals[0] if vals and isinstance(vals[0], dict) else {}
        value = _num(v.get("extracted_value", v.get("value")))
        if d is None or value is None:
            continue
        out.append({"date": d.isoformat(), "value": value})
    out.sort(key=lambda p: p["date"])
    return out


def fetch(key, keyword):
    status, data = http.get_json("SerpApi", SERP, params={
        "engine": "google_trends", "q": keyword, "geo": "US", "date": "today 12-m", "data_type": "TIMESERIES", "api_key": key})
    if not _ok(status, data):
        return []
    return parse_timeseries(data)


def save(keyword, week, source, points):
    TrendSeries.query.filter_by(keyword=keyword, week=week).delete(synchronize_session=False)
    db.session.add(TrendSeries(keyword=clean(keyword, 120), week=week, source=source,
                               points=json.dumps(points, ensure_ascii=False), collected_at=utcnow()))
    db.session.commit()


def collect(cfg, key, week):
    count, calls, warnings = 0, 0, []
    for kw in cfg.get("trend_keywords") or []:
        points = fetch(key, kw)
        calls += 1
        if not points:
            warnings.append(f"'{kw}': 데이터가 없어요 (검색량이 너무 적을 수 있어요).")
            continue
        save(kw, week, "serpapi", points)
        count += 1
    return {"count": count, "calls": calls, "warnings": warnings}


def parse_csv(text):
    """Google Trends CSV → {키워드: [{date, value}]}.

    첫 줄 'Category: …', 빈 줄, 머리글 'Week,hangover: (United States)' 다음에 날짜별 값. '<1'은 0."""
    lines = [ln for ln in str(text or "").replace("﻿", "").splitlines()]
    start = None
    for i, ln in enumerate(lines):
        first = ln.split(",")[0].strip().strip('"').lower()
        if first in ("week", "day", "month", "time", "date", "주", "일", "월", "날짜", "시간"):
            start = i
            break
    if start is None:
        raise ValueError("Google Trends CSV 형식이 아니에요. 첫 열이 Week(주) · Day(일) · Month(월)인 머리글이 필요해요.")
    reader = csv.reader(io.StringIO("\n".join(lines[start:])))
    header = next(reader)
    names = [re.sub(r":\s*\(.*\)\s*$", "", h).strip() for h in header[1:]]
    if not names:
        raise ValueError("키워드 열이 없어요.")
    out = {n: [] for n in names if n}
    for row in reader:
        if not row or not row[0].strip():
            continue
        raw = row[0].strip()
        try:
            d = date.fromisoformat(raw[:10]) if re.match(r"\d{4}-\d{2}(-\d{2})?", raw) and len(raw) >= 10 else \
                date.fromisoformat(raw[:7] + "-01")
        except ValueError:
            continue
        for name, cell in zip(names, row[1:]):
            v = _num(cell)
            if name and v is not None:
                out[name].append({"date": d.isoformat(), "value": v})
    out = {k: v for k, v in out.items() if v}
    if not out:
        raise ValueError("CSV에서 숫자를 찾지 못했어요.")
    return out
