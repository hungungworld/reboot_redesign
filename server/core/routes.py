"""화면 셸 · 파트 파일 · 공용 저장소 · 작업 로그 · 상태 API (공통 담당).

화면
  GET  /                         → /dashboard
  GET  /dashboard                홈
  GET  /dashboard/<파트ID>        각 메뉴 (셸 + 모든 파트 파일 + 현재 파트 render)
  GET  /parts/<파트ID>/<파일>      파트 JS·CSS = tabs/<파트ID>/static/<파일> (DATA_DIR/parts_override에 같은 이름이 있으면 그 파일)
  GET  /healthz                  Render 상태 확인 (로그인 없이)

API (로그인 필요)
  GET    /api/health             연결 상태 (키 값은 내보내지 않음)
  GET    /api/me                 로그인한 사용자
  GET    /api/store              저장소 전체 (파트별)
  PUT    /api/store/<파트>/<키>   값 저장 (JSON)
  DELETE /api/store/<파트>/<키>
  GET    /api/worklog            작업 로그 (?kind=&part=&limit=&before=)
  POST   /api/worklog            작업 로그 추가 {part, kind, action, status, approval, detail}
  POST   /api/jobs/daily         하루 1회 작업 실행 (X-Cron-Token, 로그인 없이)
"""
import hmac
import json
import re
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import generate_csrf
from sqlalchemy import text
from werkzeug.security import safe_join

from .. import envkeys, jobs, worklog
from ..extensions import csrf, db
from ..models import StoreItem, WorkLog, iso, utcnow
from ..registry import PARTS, assets, get_part, static_dir
from ..security import ApiError, json_body
from .templating import greeting, local_now

bp = Blueprint("core", __name__)

PART_RE = re.compile(r"^[a-z0-9-]{1,40}$")
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MAX_STORE_VALUE = 3 * 1024 * 1024
PART_FILE_TYPES = {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".json", ".csv", ".woff2"}


# ───────── 화면 ─────────

@bp.get("/")
def index():
    return redirect(url_for("core.dashboard"))


@bp.get("/dashboard")
@login_required
def dashboard():
    return _render_part("home")


@bp.get("/dashboard/<part_id>")
@login_required
def part_page(part_id):
    if part_id == "home":
        return redirect(url_for("core.dashboard"))
    return _render_part(part_id)


def _store_snapshot():
    out = {}
    for row in StoreItem.query.all():
        out.setdefault(row.part, {})[row.key] = row.parsed()
    return out


def _render_part(part_id, extra=None):
    part = get_part(part_id)
    if part is None:
        abort(404)
    css, js = assets()
    user = current_user.to_public()
    title = greeting(user["name"]) if part.id == "home" else part.title
    from ..catalog import service as catalog
    boot = {
        "part": part.id,
        "user": user,
        "csrf": generate_csrf(),
        "store": _store_snapshot(),
        "today": local_now().strftime("%Y-%m-%d"),
        # 공용 기준 데이터 (동기로 바로 쓰도록 페이지에 실음) — 원본은 /api/seasons · /api/facts
        "seasons": catalog.list_seasons(),
        "facts": {"brand": catalog.get_brand(), "products": catalog.get_products(),
                  "flat": catalog.flat_facts(), "version": catalog.facts_version()},
        "env": {"label": current_app.config["ENV_LABEL"], "render": bool(current_app.config["IS_RENDER"])},
        "parts": [{"id": p.id, "label": p.label, "icon": p.icon, "owner": p.owner, "priority": p.priority,
                   "title": p.title, "subtitle": p.subtitle, "nav": p.nav,
                   "short": p.short or p.label, "kicker": p.kicker} for p in PARTS],
        "page": extra or {},
    }
    return render_template("shell.html", part=part, title=title, css=css, js=js, boot=boot)


@bp.get("/parts/<path:filename>")
@login_required
def part_file(filename):
    if Path(filename).suffix.lower() not in PART_FILE_TYPES:
        abort(404)
    pid, _, name = filename.partition("/")
    if get_part(pid) is None or not name:
        abort(404)
    # 덮어쓴 파일(DATA_DIR/parts_override/<파트ID>/<파일>) → 저장소 기본 파일(tabs/<파트ID>/static/<파일>)
    for base, rel in ((Path(current_app.config["DATA_DIR"]) / "parts_override", filename), (static_dir(pid), name)):
        full = safe_join(str(base), rel)
        if full and Path(full).is_file():
            resp = send_from_directory(str(base), rel, max_age=0 if request.args.get("v") is None else 86400)
            return resp
    abort(404)


@bp.get("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        return "db error", 503
    return "ok"


# ───────── 상태 ─────────

def _gmail_ready():
    try:
        from tabs.cs import gmail
        return gmail.ready()
    except Exception:
        return False


@bp.get("/api/health")
def health():
    keys = {s.name: envkeys.has(s.name) for s in envkeys.SERVICES if s.group == "key"}
    return jsonify({
        "ok": True,
        # 컴플라이언스·CS 파트가 보는 값 (예전 server/app.py와 같은 이름)
        "ai": keys.get("ANTHROPIC_API_KEY", False),
        "gmail": _gmail_ready(),
        "gmailQuery": envkeys.has("GMAIL_QUERY"),
        "monitor": {"openfda": True, "regulations": keys.get("REGULATIONS_GOV_API_KEY", False),
                    "courtlistener": keys.get("COURTLISTENER_TOKEN", False)},
        # 다른 파트용
        "openai": keys.get("OPENAI_API_KEY", False),
        "openaiModel": envkeys.get("OPENAI_MODEL"),
        "smtp": keys.get("GMAIL_APP_PASSWORD", False) and envkeys.has("GMAIL_ADDRESS"),
        "keys": keys,
        "env": {"label": current_app.config["ENV_LABEL"], "render": bool(current_app.config["IS_RENDER"])},
    })


@bp.get("/api/me")
def me():
    return jsonify(current_user.to_public())


# ───────── 공용 저장소 (REBOOT.store) ─────────

def _check_store_path(part, key):
    if not PART_RE.match(part) or not KEY_RE.match(key):
        raise ApiError("저장소 이름이 올바르지 않습니다.")


@bp.get("/api/store")
def store_all():
    return jsonify(_store_snapshot())


@bp.put("/api/store/<part>/<key>")
def store_put(part, key):
    _check_store_path(part, key)
    if not request.is_json:
        raise ApiError("JSON만 받습니다.", 415)
    raw = request.get_data(cache=False, as_text=True)
    if len(raw.encode("utf-8")) > MAX_STORE_VALUE:
        raise ApiError("저장할 값이 너무 큽니다 (3MB 이하).", 413)
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        raise ApiError("JSON을 읽지 못했습니다.")
    row = StoreItem.query.filter_by(part=part, key=key).first()
    if row is None:
        row = StoreItem(part=part, key=key)
        db.session.add(row)
    row.value = json.dumps(value, ensure_ascii=False)
    row.updated_at = utcnow()
    row.updated_by = current_user.name or current_user.email
    db.session.commit()
    return jsonify(ok=True, updatedAt=iso(row.updated_at))


@bp.delete("/api/store/<part>/<key>")
def store_delete(part, key):
    _check_store_path(part, key)
    StoreItem.query.filter_by(part=part, key=key).delete()
    db.session.commit()
    return jsonify(ok=True)


# ───────── 작업 로그 ─────────

@bp.get("/api/worklog")
def worklog_list():
    q = WorkLog.query
    if request.args.get("kind"):
        q = q.filter(WorkLog.kind == request.args["kind"])
    if request.args.get("part"):
        q = q.filter(WorkLog.part == request.args["part"])
    if request.args.get("before", "").isdigit():
        q = q.filter(WorkLog.id < int(request.args["before"]))
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
    except ValueError:
        limit = 100
    rows = q.order_by(WorkLog.id.desc()).limit(limit).all()
    return jsonify(items=[r.to_dict() for r in rows])


@bp.post("/api/worklog")
def worklog_add():
    data = json_body(max_bytes=128 * 1024)
    action = str(data.get("action", "")).strip()
    if not action:
        raise ApiError("작업 내용이 없습니다.")
    part = str(data.get("part", "") or "")
    if part and not PART_RE.match(part):
        raise ApiError("파트 ID가 올바르지 않습니다.")
    entry = worklog.add(part, str(data.get("kind", "기타")), action, status=str(data.get("status", "완료")),
                        approval=data.get("approval"), detail=data.get("detail") if isinstance(data.get("detail"), dict) else {})
    if entry is None:
        raise ApiError("작업 로그를 저장하지 못했습니다.", 500)
    return jsonify(entry.to_dict())


# ───────── 하루 1회 작업 (외부 크론) ─────────

@bp.post("/api/jobs/daily")
@csrf.exempt
def jobs_daily():
    expected = current_app.config.get("CRON_TOKEN") or ""
    given = request.headers.get("X-Cron-Token", "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        return jsonify(error="토큰이 맞지 않습니다."), 403
    started = jobs.run_daily(current_app._get_current_object())
    return jsonify(ok=True, started=started, at=iso(utcnow()))
