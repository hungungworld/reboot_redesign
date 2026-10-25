"""설정 및 브랜드 관리 API (담당: 이현경).

  GET  /api/settings/collect                      수집 설정 {config, defaults, updatedAt, updatedBy}
  PUT  /api/settings/collect                      수집 설정 저장 (다음 데이터 업데이트부터 반영)
  GET  /api/settings/connections                  연결 목록 (값은 끝 4자리만 · 마지막 테스트 결과)
  PUT  /api/settings/connections/<NAME>           키 입력 {value} — 관리자만 · 빈 값이면 화면 입력을 지워 .env로
  POST /api/settings/connections/<NAME>/test      연결 테스트 → {status, message, detail}
  GET  /api/settings/worklog.xlsx                 작업 로그 Excel (구분별 시트, ?kind=&part=)

브랜드·제품(팩트시트)과 시즌은 공용 API(/api/facts · /api/seasons), 계정은 /api/users · /api/me를 그대로 씁니다.
"""
import hashlib
import json
import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request, send_file, url_for
from flask_login import current_user
from werkzeug.routing import BuildError

from server import envkeys, worklog
from server.extensions import db
from server.models import WorkLog, iso, utcnow
from server.security import ApiError, json_body
from . import config, connections, xlsx
from .models import ConnectionCheck

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

STATUS_OK = ("정상", "오류", "미연결")


def _who():
    return current_user.name or current_user.email.split("@")[0]


def _require_admin():
    if not current_user.is_admin:
        raise ApiError("키 입력은 관리자만 할 수 있습니다. 관리자에게 요청하세요.", 403)


# ───────── 수집 설정 ─────────

@bp.get("/collect")
def collect_get():
    return jsonify(config=config.get_collect_config(), defaults=config.defaults(), **config.collect_meta())


@bp.put("/collect")
def collect_put():
    data = json_body()
    try:
        new, changed = config.save_collect_config(data.get("config", data), _who())
    except ValueError as e:
        raise ApiError(str(e))
    if changed:
        worklog.add("settings", "기타", "수집 설정 변경", detail={"fields": changed})
    return jsonify(config=new, changed=changed, **config.collect_meta())


# ───────── 연결 ─────────

def _tail(name):
    """테스트 결과가 지금 값의 것인지 확인용: 비밀값은 끝 4자리(화면에도 보이는 값), 일반 설정값은 짧은 해시."""
    shown = envkeys.masked(name) or ""
    svc = envkeys.service(name)
    if svc is not None and not svc.secret:
        return hashlib.sha256(shown.encode("utf-8")).hexdigest()[:8] if shown else ""
    return shown[-4:]


def _item(svc_status, checks):
    name = svc_status["name"]
    chk = checks.get(name)
    item = dict(svc_status)
    item["testable"] = connections.testable(name)
    last = None
    if chk is not None:
        last = {"status": chk.status, "message": chk.message, "detail": chk.parsed_detail(), "at": iso(chk.checked_at),
                "by": chk.checked_by}
    fresh = bool(chk and chk.source == item["source"] and chk.tail == _tail(name))
    if not item["connected"]:
        item["status"] = "미연결"
    elif fresh and chk.status in STATUS_OK:
        item["status"] = chk.status
    else:
        item["status"] = "확인 전"   # 키는 있지만 아직 테스트하지 않았거나 키가 바뀜
    item["lastCheck"] = last if fresh else None
    return item


def _list():
    checks = {c.name: c for c in ConnectionCheck.query.all()}
    return [_item(s, checks) for s in envkeys.status_list()]


@bp.get("/connections")
def connections_list():
    try:
        redirect_uri = url_for("cs.gmail_callback", _external=True)
    except BuildError:
        redirect_uri = request.host_url.rstrip("/") + "/api/cs/gmail/callback"
    return jsonify(items=_list(), isAdmin=bool(current_user.is_admin),
                   setup={"appSecretKey": bool(os.environ.get("APP_SECRET_KEY", "").strip()),
                          "googleClient": envkeys.has("GOOGLE_CLIENT_ID") and envkeys.has("GOOGLE_CLIENT_SECRET"),
                          "redirectUri": redirect_uri})


def _service_or_404(name):
    svc = envkeys.service(name)
    if svc is None:
        raise ApiError("없는 연결 항목입니다.", 404)
    return svc


def _forget_checks(name):
    names = connections.GMAIL_PAIR if name in connections.GMAIL_PAIR else (name,)
    ConnectionCheck.query.filter(ConnectionCheck.name.in_(names)).delete(synchronize_session=False)


@bp.put("/connections/<name>")
def connections_put(name):
    _require_admin()
    svc = _service_or_404(name)
    data = json_body(max_bytes=16 * 1024)
    value = data.get("value", "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError("값은 글자로 입력하세요.")
    value = value.strip()
    if name == "GMAIL_APP_PASSWORD":
        value = "".join(value.split())  # 앱 비밀번호는 'abcd efgh …'처럼 띄어 써도 됨
    if len(value) > 4000:
        raise ApiError("값이 너무 깁니다 (4,000자 이하).")
    if svc.secret and value and any(c.isspace() for c in value):
        raise ApiError("키에 공백이나 줄바꿈이 들어 있어요. 다시 복사해 붙여 넣으세요.")
    if svc.group == "key" and value and len(value) < 8:
        raise ApiError("키가 너무 짧아요. 전체를 복사했는지 확인하세요.")
    envkeys.set_value(name, value, _who())
    _forget_checks(name)
    db.session.commit()
    after = envkeys.source(name)
    worklog.add("settings", "기타", f"연결 {'값 저장' if value else '화면 입력 삭제'}: {svc.label}",
                detail={"name": name, "source": {"screen": "화면 입력", "env": ".env"}.get(after, "없음")})
    item = next(i for i in _list() if i["name"] == name)
    return jsonify(item)


@bp.post("/connections/<name>/test")
def connections_test(name):
    svc = _service_or_404(name)
    if not connections.testable(name):
        raise ApiError(f"{svc.label}은(는) 연결 테스트를 지원하지 않아요. 키 저장 여부만 확인할 수 있어요.")
    result = connections.run_test(name)
    names = connections.GMAIL_PAIR if name in connections.GMAIL_PAIR else (name,)
    now = utcnow()
    for n in names:
        row = db.session.get(ConnectionCheck, n) or ConnectionCheck(name=n)
        row.status, row.message = result["status"], result["message"]
        row.detail = json.dumps(result["detail"], ensure_ascii=False, default=str)
        row.source, row.tail = envkeys.source(n), _tail(n)
        row.checked_at, row.checked_by = now, _who()
        db.session.add(row)
    db.session.commit()
    worklog.add("settings", "기타", f"연결 테스트: {svc.label} → {result['status']}",
                status="실패" if result["status"] == "오류" else "완료", detail={"message": result["message"]})
    return jsonify(dict(result, at=iso(now), name=name))


# ───────── 작업 로그 Excel ─────────

def _tz():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Seoul"))
    except Exception:
        from datetime import timedelta, timezone
        return timezone(timedelta(hours=9))


def _detail_text(d):
    if not d:
        return ""
    return json.dumps(d, ensure_ascii=False)[:2000]


@bp.get("/worklog.xlsx")
def worklog_xlsx():
    q = WorkLog.query
    if request.args.get("kind"):
        q = q.filter(WorkLog.kind == request.args["kind"])
    if request.args.get("part"):
        q = q.filter(WorkLog.part == request.args["part"])
    rows = [r.to_dict() | {"_at": r.at} for r in q.order_by(WorkLog.id.desc()).limit(5000).all()]
    tz = _tz()
    wb = xlsx.new_workbook()
    head = ["일시", "구분", "파트", "작업", "상태", "승인", "작성자", "상세"]
    xlsx.add_sheet(wb, "전체", head, [[xlsx.local_text(r["_at"], tz), r["kind"], r["part"], r["action"], r["status"],
                                       r["approval"], r["user"], _detail_text(r["detail"])] for r in rows],
                   widths={8: 70})
    for kind in worklog.KINDS:
        sub = [r for r in rows if r["kind"] == kind]
        if kind == "AI 프롬프트":
            def tokens(d):
                t = d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
                return t.get("total")
            xlsx.add_sheet(wb, kind, ["일시", "파트", "작업", "상태", "모델", "토큰(합계)", "프롬프트", "응답 요약", "작성자"],
                           [[xlsx.local_text(r["_at"], tz), r["part"], r["action"], r["status"], r["detail"].get("model", ""),
                             tokens(r["detail"]), str(r["detail"].get("prompt", ""))[:4000],
                             str(r["detail"].get("response", ""))[:2000], r["user"]] for r in sub],
                           widths={7: 70, 8: 50})
        else:
            xlsx.add_sheet(wb, kind, head, [[xlsx.local_text(r["_at"], tz), r["kind"], r["part"], r["action"], r["status"],
                                             r["approval"], r["user"], _detail_text(r["detail"])] for r in sub],
                           widths={8: 70})
    stamp = datetime.now(tz).strftime("%Y%m%d")
    return send_file(xlsx.to_bytes(wb), as_attachment=True, download_name=f"reboot-settings-worklog-{stamp}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
