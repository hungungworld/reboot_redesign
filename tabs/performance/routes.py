"""성과분석 API (담당: 혁주) — README 7장의 주소를 /api/performance 아래로 옮김.

  GET    /api/performance/data           성과 데이터 {rows, isSample, range, channels, meta}
  PUT    /api/performance                CSV 저장 — JSON {csv, mode: merge|replace, filename} 또는 multipart(file, mode)
  POST   /api/performance/upload         multipart 업로드 (REBOOT.upload용, 엑셀 EUC-KR 파일도 그대로)
  DELETE /api/performance                업로드한 데이터 지우고 샘플로 (백업 후)
  GET    /api/performance/template.csv   빈 양식
  GET    /api/performance/export.csv     지금 데이터 내려받기
  GET    /api/performance/summary        기간 요약 (?start&end, 없으면 CSV 마지막 날짜 기준 최근 7일) — 홈 KPI
  GET    /api/performance/fx             USD 환율 (?refresh=1) {status: ok|no_key|error, rate, date, reason}
  POST   /api/performance/advice         {start, end} → AI 해석 · 다음 주 예산 · 실행 과제 (키 없으면 503)
  POST   /api/performance/insight        {start, end} → 채널별 성과 표 위 AI 인사이트 {lines: 최대 3줄} (키 없으면 503)
"""
from datetime import date

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user

from server import jobs, worklog
from server.core.ai import openai_ready
from server.security import ApiError, clip, json_body
from . import csvdata, metrics

bp = Blueprint("performance", __name__, url_prefix="/api/performance")

NO_AI = "OpenAI가 연결되어 있지 않습니다. 설정 › 연결에서 OPENAI_API_KEY를 넣으면 AI 해석·예산 제안을 쓸 수 있습니다."


def _who():
    return current_user.name or current_user.email


def _current():
    data = csvdata.load()
    if data.get("error"):
        raise ApiError(data["error"], 500)
    return data


def _period(rng, start="", end=""):
    """기간 확인 → (start, end). 없으면 최근 7일. CSV 범위 밖은 안쪽으로 맞춤."""
    start, end = str(start or "").strip()[:10], str(end or "").strip()[:10]
    if not start and not end:
        return metrics.latest_week(rng)
    try:
        s = date.fromisoformat(start or end)
        e = date.fromisoformat(end or start)
    except ValueError:
        raise ApiError("기간은 YYYY-MM-DD 형식이어야 합니다.")
    if s > e:
        s, e = e, s
    lo, hi = date.fromisoformat(rng["start"]), date.fromisoformat(rng["end"])
    s, e = max(s, lo), min(e, hi)
    if s > e:
        raise ApiError("고른 기간에는 CSV 데이터가 없습니다. 기간을 바꿔 주세요.")
    return s.isoformat(), e.isoformat()


@bp.get("/data")
def data():
    d = _current()
    rows = d["rows"]
    return jsonify(rows=rows, isSample=d["source"] == "sample", source=d["source"], range=csvdata.date_range(rows),
                   channels=csvdata.channels(rows), meta=d["meta"], header=list(csvdata.HEADER))


def _save(text, mode, filename):
    if mode not in ("merge", "replace"):
        raise ApiError("방식은 merge(합치기) 또는 replace(전체 바꾸기)입니다.")
    try:
        rows, info = csvdata.parse_csv(text)
    except csvdata.CsvError as e:
        raise ApiError(str(e))
    try:
        with jobs.file_lock("performance-csv", stale_seconds=120):
            meta = csvdata.save(rows, mode, _who(), filename)
    except jobs.Busy:
        raise ApiError("다른 사람이 지금 CSV를 저장하고 있습니다. 잠시 뒤 다시 시도하세요.", 409)
    meta["duplicates"] = info["duplicates"]
    worklog.add("performance", "기타", f"성과 CSV 업로드 ({'합치기' if mode == 'merge' else '전체 바꾸기'} · {meta['rows']}행)",
                detail={"file": meta["filename"], "added": meta["added"], "updated": meta["updated"],
                        "backup": meta["backup"], "fromSample": meta["fromSample"]})
    d = csvdata.load()
    return jsonify(ok=True, result=meta, rows=d["rows"], isSample=False, source=d["source"],
                   range=csvdata.date_range(d["rows"]), channels=csvdata.channels(d["rows"]), meta=d["meta"])


def _from_request():
    """JSON {csv, mode, filename} 또는 multipart(file, mode)."""
    if request.files.get("file"):
        f = request.files["file"]
        raw = f.read(csvdata.MAX_BYTES + 1)
        if len(raw) > csvdata.MAX_BYTES:
            raise ApiError("파일이 너무 큽니다 (5MB 이하).", 413)
        try:
            text = csvdata.decode(raw)
        except csvdata.CsvError as e:
            raise ApiError(str(e))
        return text, request.form.get("mode", "merge"), clip(f.filename, 120)
    if request.is_json:
        body = json_body(max_bytes=csvdata.MAX_BYTES * 3)
        return str(body.get("csv") or ""), str(body.get("mode") or "merge"), clip(body.get("filename"), 120)
    raise ApiError("CSV 파일(file) 또는 JSON {csv, mode}를 보내 주세요.", 415)


@bp.put("")
def put_csv():
    return _save(*_from_request())


@bp.post("/upload")
def upload_csv():
    return _save(*_from_request())


@bp.delete("")
def reset():
    backup = csvdata.reset_to_sample()
    worklog.add("performance", "기타", "성과 데이터 지우기 (샘플로 되돌림)", detail={"backup": backup})
    return jsonify(ok=True, backup=backup)


def _csv_response(text, name):
    return Response("\ufeff" + text, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@bp.get("/template.csv")
def template():
    return _csv_response(csvdata.HEADER_TEXT + "\n", "reboot-performance-template.csv")


@bp.get("/export.csv")
def export():
    d = _current()
    name = "reboot-performance-sample.csv" if d["source"] == "sample" else "reboot-performance-data.csv"
    return _csv_response(csvdata.to_csv(d["rows"]), name)


@bp.get("/summary")
def summary():
    d = _current()
    rows = d["rows"]
    rng = csvdata.date_range(rows)
    if not rng:
        return jsonify(empty=True, sample=False, start=None, end=None, revenue=None, spend=None, roas=None,
                       conversions=None, cpa=None, profit=None, change=None, compare=None, daily=[])
    start, end = _period(rng, request.args.get("start"), request.args.get("end"))
    s = metrics.summarize(rows, start, end, rng, csvdata.channels(rows))
    t, ch = s["totals"], s["change"]
    return jsonify(
        start=start, end=end, days=s["days"], sample=d["source"] == "sample", empty=False, range=rng,
        revenue=t["revenue"], spend=t["spend"], roas=t["roas"], conversions=t["conversions"], cpa=t["cpa"],
        profit=t["profit"], impressions=t["impressions"], clicks=t["clicks"], ctr=t["ctr"], cvr=t["cvr"],
        change=ch and {k: ch[k] for k in ("revenue", "spend", "roas", "conversions", "cpa", "profit")},
        compare=s["compare"], compareTotals=s["compareTotals"], daily=s["daily"],
        channels=[{k: c[k] for k in ("channel", "spend", "revenue", "roas", "conversions", "cpa")} for c in s["channels"]],
    )


@bp.get("/fx")
def fx():
    from . import fx as fxmod
    return jsonify(fxmod.get_rate(refresh=request.args.get("refresh") == "1"))


@bp.post("/advice")
def advice():
    from . import advice as adv
    body = json_body()
    if not openai_ready():
        raise ApiError(NO_AI, 503)
    d = _current()
    rows = d["rows"]
    rng = csvdata.date_range(rows)
    if not rng:
        raise ApiError("성과 데이터가 아직 없어요. 매출 CSV를 먼저 올려 주세요.")
    start, end = _period(rng, body.get("start"), body.get("end"))
    s = metrics.summarize(rows, start, end, rng, csvdata.channels(rows))
    out = adv.advise(s, d["source"])
    out.update(start=start, end=end, sample=d["source"] == "sample", compare=s["compare"])
    return jsonify(out)


@bp.post("/insight")
def insight():
    from . import advice as adv
    body = json_body()
    if not openai_ready():
        raise ApiError(NO_AI, 503)
    d = _current()
    rows = d["rows"]
    rng = csvdata.date_range(rows)
    if not rng:
        raise ApiError("성과 데이터가 아직 없어요. 매출 CSV를 먼저 올려 주세요.")
    start, end = _period(rng, body.get("start"), body.get("end"))
    s = metrics.summarize(rows, start, end, rng, csvdata.channels(rows))
    out = adv.insight(s, d["source"])
    out.update(start=start, end=end, sample=d["source"] == "sample")
    return jsonify(out)
