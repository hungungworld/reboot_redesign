"""시즌 캘린더 · 팩트시트 API (공통 담당). 화면 편집은 설정 파트, 표시는 홈·인사이트·콘텐츠가 씁니다.

  GET    /api/seasons                 다가오는 시즌 (?all=1 지난 시즌 포함) — D-day · 준비 기간(inLead) 포함
  POST   /api/seasons                 추가 {name, start, end, category, priority, leadDays, notes, monitor}
  PUT    /api/seasons/<id>            수정
  DELETE /api/seasons/<id>            삭제
  GET    /api/seasons/export.csv      CSV 내려받기
  POST   /api/seasons/import          {csv, mode: merge|replace}
  GET    /api/facts                   {brand, products, version}
  PUT    /api/facts/<entity>          브랜드('brand') · 제품(ID) 수정 — 허용 항목만
"""
from flask import Blueprint, Response, jsonify, request
from flask_login import current_user

from .. import worklog
from ..extensions import db
from ..security import ApiError, json_body
from . import service
from .models import Season

bp = Blueprint("catalog", __name__, url_prefix="/api")


def _who():
    return current_user.name or current_user.email


@bp.get("/seasons")
def seasons_list():
    return jsonify(items=service.list_seasons(include_past=request.args.get("all") == "1"),
                   today=service._today().isoformat())


@bp.post("/seasons")
def seasons_add():
    data = json_body()
    try:
        s = service.apply_season(Season(), data, _who())
    except ValueError as e:
        raise ApiError(str(e))
    db.session.add(s)
    db.session.commit()
    worklog.add("settings", "기타", f"시즌 추가: {s.name}")
    return jsonify(service.season_dict(s)), 201


@bp.put("/seasons/<int:sid>")
def seasons_update(sid):
    s = db.session.get(Season, sid)
    if s is None:
        raise ApiError("없는 시즌입니다.", 404)
    try:
        service.apply_season(s, json_body(), _who())
    except ValueError as e:
        db.session.rollback()
        raise ApiError(str(e))
    db.session.commit()
    worklog.add("settings", "기타", f"시즌 수정: {s.name}")
    return jsonify(service.season_dict(s))


@bp.delete("/seasons/<int:sid>")
def seasons_delete(sid):
    s = db.session.get(Season, sid)
    if s is None:
        raise ApiError("없는 시즌입니다.", 404)
    name = s.name
    db.session.delete(s)
    db.session.commit()
    worklog.add("settings", "기타", f"시즌 삭제: {name}")
    return jsonify(ok=True)


@bp.get("/seasons/export.csv")
def seasons_export():
    return Response("﻿" + service.export_seasons_csv(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=reboot-settings-seasons.csv"})


@bp.post("/seasons/import")
def seasons_import():
    data = json_body(max_bytes=512 * 1024)
    try:
        n = service.import_seasons_csv(str(data.get("csv", "")), replace=data.get("mode") == "replace", user=_who())
    except ValueError as e:
        db.session.rollback()
        raise ApiError(str(e))
    worklog.add("settings", "기타", f"시즌 CSV 가져오기 ({n}개 · {'전체 바꾸기' if data.get('mode') == 'replace' else '합치기'})")
    return jsonify(ok=True, count=n, items=service.list_seasons(include_past=True))


@bp.get("/facts")
def facts_all():
    return jsonify(brand=service.get_brand(), products=service.get_products(), version=service.facts_version(),
                   flat=service.flat_facts())


@bp.put("/facts/<entity>")
def facts_update(entity):
    data = json_body()
    try:
        out = service.update_facts(entity, data, _who())
    except KeyError:
        raise ApiError("없는 항목입니다.", 404)
    except ValueError as e:
        db.session.rollback()
        raise ApiError(str(e))
    worklog.add("settings", "기타", f"제품 정보 수정: {entity}", detail={"fields": sorted(k for k in data if k in service.PRODUCT_FIELDS + service.BRAND_FIELDS)})
    return jsonify(out)
