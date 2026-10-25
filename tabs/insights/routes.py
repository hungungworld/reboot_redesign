"""시장 · 고객 인사이트 API (담당: 이현경) — 3번째 대시보드.

화면
  GET   /api/insights/weekly                   주간 리뷰 (이번 주 액션 · 진열대 변화 · 새 리뷰 · 다가오는 시즌)
  GET   /api/insights/monthly?month=YYYY-MM    월간 리뷰 (1~7일에는 지난달)
  GET   /api/insights/data/<탭>                 shelf · competitors · reviews · audience · trends
수정 · 올리기
  PATCH /api/insights/shelf/<id>               진열대 한 줄 고치기 {price, in_stock, own}
  PUT   /api/insights/servings                 회분 수 고치기 {key, servings|null} (모든 주차에 적용)
  PUT   /api/insights/lines                    제품 라인 묶음 고치기 {key, brand, form} (둘 다 null이면 자동으로)
  POST  /api/insights/lines/confirm            지금 묶음을 모두 '확인함'으로 기억
  PUT   /api/insights/watch                    주시 브랜드 {brand, watch}
경쟁사 분석
  POST  /api/insights/competitors/discover     새 경쟁사 찾기 (지금 진열대 기준 · 무료)
  POST  /api/insights/competitors/<id>/research   한 곳 다시 조사 (뒤에서 실행)
  POST  /api/insights/competitors/research-all    상위 15개 모두 다시 조사 (뒤에서 실행)
  PATCH /api/insights/competitors/<id>         {status: active|archived, website}
  GET   /api/insights/competitors.csv · /competitors.json
  POST  /api/insights/reviews/<id>/translate   리뷰 한국어 번역 (OpenAI · 같은 원문은 저장된 번역)
  POST  /api/insights/reviews/<id>/cs          자사 리뷰 CS 대응 {status: new · todo · done · skip, memo}
  POST  /api/insights/reviews/<id>/slack       슬랙 채널로 보내기 {note, preview} (preview면 글만 돌려줌 · 웹훅 SLACK_WEBHOOK_URL)
  POST  /api/insights/trends/upload            Google Trends CSV {csv} (SerpApi 키가 없을 때)
데이터 업데이트
  GET   /api/insights/update/plan              항목 · 필요한 키 · 예상 비용 · 이번 달 쓴 양
  POST  /api/insights/update                   {items:[…]} → 뒤에서 실행
  GET   /api/insights/update/status            진행 상황
내려받기
  GET   /api/insights/report/weekly.docx · /report/monthly.docx · /export.xlsx
다른 파트 공개 (REBOOT.use('insights'))
  GET   /api/insights/trends                   {keywords:[{keyword, points:[{date, value}]}], updatedAt}
  GET   /api/insights/personas                 [{name, oneLine, needs, message}]
"""
import csv
import io
import json
import re

from flask import Blueprint, Response, jsonify, request, send_file
from flask_login import current_user

from server import worklog
from server.extensions import db
from server.models import utcnow
from server.security import ApiError, clip, json_body
from . import ai as insights_ai
from . import accuracy, competitors, lines, reports, service, slack, store, trends, update
from .http import CollectError
from .models import Competitor, Review, ReviewCS, ServingOverride, ShelfItem, kv_get, kv_set
from .util import clean, review_month, week_key

bp = Blueprint("insights", __name__, url_prefix="/api/insights")

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _who():
    return current_user.name or current_user.email.split("@")[0]


def _month_arg():
    m = request.args.get("month", "").strip()
    if m and not MONTH_RE.match(m):
        raise ApiError("월은 YYYY-MM 형식으로 주세요.")
    return m or None


# ───────── 화면 ─────────

@bp.get("/weekly")
def weekly():
    return jsonify(service.weekly_view())


@bp.get("/monthly")
def monthly():
    return jsonify(service.monthly_view(_month_arg()))


@bp.get("/data/<tab>")
def data_tab(tab):
    if tab == "shelf":
        return jsonify(service.shelf_view())
    if tab == "competitors":
        return jsonify(service.competitors_view())
    if tab == "reviews":
        return jsonify(service.reviews_view())
    if tab == "audience":
        return jsonify(service.audience_view(_month_arg()))
    if tab == "trends":
        return jsonify(service.trends_view())
    if tab == "accuracy":
        return jsonify(accuracy.view())
    raise ApiError("없는 데이터 탭입니다.", 404)


# ───────── 수정 · 올리기 ─────────

@bp.patch("/shelf/<int:row_id>")
def shelf_edit(row_id):
    row = db.session.get(ShelfItem, row_id)
    if row is None:
        raise ApiError("없는 진열대 항목입니다.", 404)
    data = json_body()
    changed = []
    if "price" in data:
        v = data["price"]
        if v in (None, ""):
            row.price = None
        else:
            try:
                v = round(float(v), 2)
            except (TypeError, ValueError):
                raise ApiError("가격은 숫자(달러)로 입력하세요.")
            if not 0 <= v <= 10000:
                raise ApiError("가격은 0~10,000달러 사이로 입력하세요.")
            row.price = v
        row.price_text = f"${row.price:.2f}" if row.price is not None else ""
        changed.append("price")
    if "in_stock" in data:
        v = data["in_stock"]
        if v not in (True, False, None):
            raise ApiError("재고는 있음 · 품절 · 모름 중 하나입니다.")
        row.in_stock = v
        changed.append("in_stock")
    if "own" in data:
        ovr = kv_get("own_overrides", {})
        ovr[row.key] = bool(data["own"])
        kv_set("own_overrides", ovr, _who())
        changed.append("own")
    if not changed:
        raise ApiError("고칠 값이 없습니다.")
    row.edited_by = _who()
    db.session.commit()
    worklog.add("insights", "기타", f"진열대 값 수정: {row.title[:60]}", detail={"fields": changed, "week": row.week})
    return jsonify(service.shelf_dict(row))


@bp.put("/servings")
def servings_edit():
    data = json_body()
    key = clip(data.get("key"), 200)
    if not key or db.session.query(ShelfItem.id).filter_by(key=key).first() is None:
        raise ApiError("없는 상품입니다.", 404)
    v = data.get("servings")
    row = db.session.get(ServingOverride, key)
    if v in (None, ""):
        if row:
            db.session.delete(row)
        db.session.commit()
        return jsonify(key=key, servings=None)
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ApiError("회분 수는 숫자로 입력하세요.")
    if not 1 <= n <= 500:
        raise ApiError("회분 수는 1~500 사이로 입력하세요.")
    row = row or ServingOverride(key=key)
    row.servings, row.updated_by, row.updated_at = n, _who(), utcnow()
    db.session.add(row)
    db.session.commit()
    title = (ShelfItem.query.filter_by(key=key).order_by(ShelfItem.id.desc()).first() or ShelfItem(title=key)).title
    worklog.add("insights", "기타", f"회분 수 수정: {title[:60]} → {n}회분")
    return jsonify(key=key, servings=n)


@bp.put("/lines")
def line_edit():
    data = json_body()
    key = clip(data.get("key"), 200)
    if not key or db.session.query(ShelfItem.id).filter_by(key=key).first() is None:
        raise ApiError("없는 상품입니다.", 404)
    brand, form = clean(data.get("brand"), 60) or None, data.get("form") or None
    if form is not None and form not in competitors.FORM_LABEL:
        raise ApiError("제형은 목록에서 고르세요.")
    lines.set_override(key, brand, form, _who())
    worklog.add("insights", "기타", f"제품 묶음 수정: {key} → {brand or '자동'} · {form or '자동'}")
    return jsonify(ok=True)


@bp.post("/lines/confirm")
def line_confirm():
    now, _, _ = service.snapshots()
    n = lines.confirm(now, _who())
    worklog.add("insights", "승인 · 선정", f"제품 묶음 확인 {n}개", approval=_who())
    return jsonify(ok=True, confirmed=n)


@bp.put("/watch")
def watch_edit():
    data = json_body()
    brand = clean(data.get("brand"), 60)
    if not brand:
        raise ApiError("브랜드 이름이 없습니다.")
    watch = [b for b in kv_get("watch", []) if b.lower() != brand.lower()]
    on = bool(data.get("watch", True))
    if on:
        if len(watch) >= 12:
            raise ApiError("주시 브랜드는 12개까지 고를 수 있어요 (5~6개 추천).")
        watch.append(brand)
    kv_set("watch", watch, _who())
    worklog.add("insights", "승인 · 선정", f"주시 브랜드 {'선정' if on else '해제'}: {brand}", approval=_who())
    return jsonify(watch=watch)


# ───────── 경쟁사 분석 ─────────

@bp.post("/competitors/discover")
def competitors_discover():
    now, _, _ = service.snapshots()
    if not now:
        raise ApiError("진열대 데이터가 없어요. 먼저 데이터 업데이트에서 Amazon · Weee! 진열대를 모으세요.")
    added = competitors.sync(now, service._tier)
    worklog.add("insights", "수집", "새 경쟁사 찾기 (진열대 기준)", detail={"added": added[:30]})
    return jsonify(ok=True, added=added)


def _start_competitors(options, label):
    try:
        run = update.start(["competitors"], _who(), options)
    except ValueError as e:
        raise ApiError(str(e))
    except RuntimeError as e:
        raise ApiError(str(e), 409)
    worklog.add("insights", "기타", label)
    return jsonify(ok=True, runId=run.id), 202


@bp.post("/competitors/<int:cid>/research")
def competitor_research(cid):
    c = db.session.get(Competitor, cid)
    if c is None:
        raise ApiError("없는 경쟁사입니다.", 404)
    return _start_competitors({"only": c.id}, f"경쟁사 다시 조사: {c.brand}")


@bp.post("/competitors/research-all")
def competitors_research_all():
    return _start_competitors({"force": True}, "경쟁사 전체 다시 조사")


@bp.patch("/competitors/<int:cid>")
def competitor_edit(cid):
    c = db.session.get(Competitor, cid)
    if c is None:
        raise ApiError("없는 경쟁사입니다.", 404)
    data = json_body()
    changed = []
    if "status" in data:
        if data["status"] not in ("active", "archived"):
            raise ApiError("상태는 active(관리 중) · archived(보관) 중 하나입니다.")
        c.status = data["status"]
        changed.append("status")
    if "website" in data:
        url = clip(data.get("website"), 500).strip()
        if url and not re.match(r"^https?://[^\s/]+\.[^\s]+$", url):
            raise ApiError("홈페이지 주소는 https://로 시작하게 입력하세요.")
        c.website = url
        changed.append("website")
    if not changed:
        raise ApiError("고칠 값이 없습니다.")
    db.session.commit()
    worklog.add("insights", "기타", f"경쟁사 수정: {c.brand}", detail={"fields": changed, "status": c.status})
    return jsonify(competitors.comp_dict(c))


@bp.get("/competitors.json")
def competitors_json():
    body = json.dumps(service.competitor_export(), ensure_ascii=False, indent=2)
    return Response(body, content_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=reboot-insights-competitors.json"})


@bp.get("/competitors.csv")
def competitors_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in service.EXPORT_HEAD])
    for r in service.competitor_export():
        w.writerow(["" if r[k] is None else r[k] for k, _ in service.EXPORT_HEAD])
    return Response("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=reboot-insights-competitors.csv"})


def _review(rid):
    r = db.session.get(Review, rid)
    if r is None:
        raise ApiError("리뷰를 찾지 못했어요.", 404)
    return r


@bp.post("/reviews/<int:rid>/translate")
def review_translate(rid):
    r = _review(rid)
    if insights_ai.is_korean(r.text):
        return jsonify(textKo=r.text)
    if not insights_ai.ready():
        raise ApiError("OpenAI 키가 없어 번역할 수 없어요. 설정 › 연결에서 키를 넣으세요.")
    return jsonify(textKo=insights_ai.translate_review(r.text))


@bp.post("/reviews/<int:rid>/cs")
def review_cs(rid):
    r = _review(rid)
    if not r.own:
        raise ApiError("CS 대응은 자사 리뷰에만 표시할 수 있어요.")
    data = json_body()
    status = str(data.get("status", "")).strip()
    if status not in service.CS_STATUS:
        raise ApiError("대응 상태가 올바르지 않아요.")
    row = db.session.get(ReviewCS, rid) or ReviewCS(review_id=rid)
    row.status = status
    if "memo" in data:
        row.memo = clip(str(data.get("memo") or ""), 1000)
    row.updated_by, row.updated_at = _who()[:100], utcnow()
    db.session.add(row)
    db.session.commit()
    return jsonify(ok=True, **service.cs_dict(r, row))


@bp.post("/reviews/<int:rid>/slack")
def review_slack(rid):
    r = _review(rid)
    data = json_body()
    note = clip(str(data.get("note") or "").strip(), 500)
    cs = db.session.get(ReviewCS, rid) if r.own else None
    if data.get("preview"):
        return jsonify(ok=True, ready=slack.ready(), text=slack.message(r, cs, note, _who()))
    try:
        shared = slack.send(r, cs, note, _who()[:100])
    except CollectError as e:
        raise ApiError(str(e))
    worklog.add("insights", "기타", "리뷰 슬랙 보내기", detail={"review": rid, "own": r.own, "note": bool(note)})
    return jsonify(ok=True, **shared)


@bp.post("/trends/upload")
def trends_upload():
    data = json_body(max_bytes=2 * 1024 * 1024)
    try:
        parsed = trends.parse_csv(str(data.get("csv", "")))
    except ValueError as e:
        raise ApiError(str(e))
    week = week_key(service.today())
    for kw, pts in parsed.items():
        trends.save(kw, week, "csv", pts)
    worklog.add("insights", "수집", "검색 트렌드 CSV 올리기", detail={"keywords": list(parsed), "points": sum(map(len, parsed.values()))})
    return jsonify(ok=True, keywords=list(parsed))


# ───────── 데이터 업데이트 ─────────

@bp.get("/update/plan")
def update_plan():
    return jsonify(update.plan())


@bp.post("/update")
def update_start():
    data = json_body()
    items = data.get("items")
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise ApiError("항목 목록이 올바르지 않습니다.")
    unknown = [i for i in items if i not in update.BY_ID]
    if unknown:
        raise ApiError("없는 항목이 있습니다: " + ", ".join(clip(u, 20) for u in unknown[:3]))
    try:
        run = update.start(items, _who())
    except ValueError as e:
        raise ApiError(str(e))
    except RuntimeError as e:
        raise ApiError(str(e), 409)
    return jsonify(ok=True, runId=run.id), 202


@bp.get("/update/status")
def update_status():
    return jsonify(update.status())


# ───────── 내려받기 ─────────

@bp.get("/report/weekly.docx")
def report_weekly():
    buf, name = reports.weekly_docx()
    return send_file(buf, as_attachment=True, download_name=name, mimetype=DOCX)


@bp.get("/report/monthly.docx")
def report_monthly():
    buf, name = reports.monthly_docx(_month_arg())
    return send_file(buf, as_attachment=True, download_name=name, mimetype=DOCX)


@bp.get("/export.xlsx")
def export_xlsx():
    buf, name = reports.data_xlsx()
    return send_file(buf, as_attachment=True, download_name=name, mimetype=XLSX)


# ───────── 다른 파트 공개 ─────────

@bp.get("/trends")
def trends_expose():
    return jsonify(service.trend_expose())


@bp.get("/personas")
def personas_expose():
    return jsonify(service.personas_expose())


@bp.get("/meta")
def meta():
    """화면 머리글용 작은 요약: 오늘 · 볼 주차 · 볼 달."""
    t = service.today()
    return jsonify(today=t.isoformat(), week=week_key(t), month=review_month(t))
