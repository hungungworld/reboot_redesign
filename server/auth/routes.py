"""로그인 · 로그아웃 (로그인 README 4·5·7장).

  GET  /login             로그인 화면 (이미 로그인 → /dashboard)
  POST /login             로그인 처리 (IP당 분당 10회)
  POST /logout            로그아웃 → /login
  GET  /forgot-password   비밀번호 찾기 안내 (추후 구현)
"""
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, limiter
from ..models import User, utcnow
from .forms import LoginForm

bp = Blueprint("auth", __name__)

MAX_FAILS = 5
LOCK_MINUTES = 10
MSG_FAIL = "이메일 또는 비밀번호가 올바르지 않습니다."
MSG_LOCKED = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."
MSG_INACTIVE = "비활성화된 계정입니다. 관리자에게 문의하세요."
# 없는 이메일이어도 비밀번호 확인과 비슷한 시간이 걸리게 (가입 여부를 응답 시간으로 알 수 없게)
_DUMMY_HASH = generate_password_hash("reboot-dummy-password")


def is_safe_next(target):
    """같은 사이트의 상대 경로만 허용 (오픈 리다이렉트 방지)."""
    if not target:
        return False
    target = target.replace("\\", "/")
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//")


def _render(form):
    no_users = db.session.query(User.id).first() is None
    return render_template("auth/login.html", form=form, no_users=no_users)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], error_message="로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = LoginForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            for errors in form.errors.values():
                if errors:
                    flash(errors[0], "error")
                    break
            return _render(form)

        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()
        now = utcnow()

        if user and user.locked_until and user.locked_until > now:
            flash(MSG_LOCKED, "error")
            return _render(form)

        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)
            flash(MSG_FAIL, "error")
            return _render(form)

        if not user.check_password(form.password.data):
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= MAX_FAILS:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_login_count = 0
            db.session.commit()
            flash(MSG_FAIL, "error")
            return _render(form)

        if not user.is_active:
            flash(MSG_INACTIVE, "error")
            return _render(form)

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        db.session.commit()

        next_page = request.args.get("next")
        session.clear()  # 세션 고정 방지: 로그인 전 세션 값을 버리고 새로 시작
        login_user(user, remember=bool(form.remember.data))
        return redirect(next_page if is_safe_next(next_page) else url_for("core.dashboard"))

    return _render(form)


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("auth.login"))


@bp.get("/forgot-password")
def forgot_password():
    return render_template("auth/forgot.html")


# ───────── 계정 관리 API (설정 › 계정 화면에서 사용) ─────────
#   GET   /api/users              계정 목록 (관리자)
#   POST  /api/users              계정 만들기 {email, name, title, password, isAdmin} (관리자)
#   PATCH /api/users/<id>         {name, title, isAdmin, isActive, password, unlock} (관리자)
#   PATCH /api/me                 내 이름·소속 {name, title}
#   POST  /api/me/password        내 비밀번호 {current, new}

def _user_json(u):
    from ..models import iso
    return {"id": u.id, "email": u.email, "name": u.name, "title": u.title, "isAdmin": bool(u.is_admin),
            "isActive": bool(u.is_active), "locked": bool(u.locked_until and u.locked_until > utcnow()),
            "lastLoginAt": iso(u.last_login_at), "createdAt": iso(u.created_at)}


def _require_admin():
    from ..security import ApiError
    if not current_user.is_authenticated or not current_user.is_admin:
        raise ApiError("관리자만 할 수 있습니다.", 403)


def _check_password(pw):
    from ..security import ApiError
    if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 128:
        raise ApiError("비밀번호는 8자 이상 128자 이하로 입력하세요.")


def _active_admins():
    return User.query.filter_by(is_admin=True, is_active=True).count()


@bp.get("/api/users")
def users_list():
    from flask import jsonify
    _require_admin()
    return jsonify(items=[_user_json(u) for u in User.query.order_by(User.id).all()])


@bp.post("/api/users")
def users_create():
    from flask import jsonify

    from .. import worklog
    from ..security import ApiError, json_body
    from .emails import normalize_email
    _require_admin()
    data = json_body()
    try:
        email = normalize_email(data.get("email"))
    except ValueError as e:
        raise ApiError(str(e))
    if User.query.filter_by(email=email).first():
        raise ApiError("이미 있는 이메일입니다.", 409)
    _check_password(data.get("password"))
    u = User(email=email, name=str(data.get("name") or email.split("@")[0]).strip()[:100],
             title=str(data.get("title") or "").strip()[:100], is_admin=bool(data.get("isAdmin")))
    u.set_password(data["password"])
    db.session.add(u)
    db.session.commit()
    worklog.add("settings", "승인 · 선정", f"계정 만들기: {u.name}", approval=current_user.name or current_user.email)
    return jsonify(_user_json(u)), 201


@bp.patch("/api/users/<int:uid>")
def users_update(uid):
    from flask import jsonify

    from .. import worklog
    from ..security import ApiError, json_body
    _require_admin()
    u = db.session.get(User, uid)
    if u is None:
        raise ApiError("없는 계정입니다.", 404)
    data = json_body()
    removing_admin = ("isAdmin" in data and not data["isAdmin"] and u.is_admin) or \
                     ("isActive" in data and not data["isActive"] and u.is_admin)
    if removing_admin and _active_admins() <= 1:
        raise ApiError("마지막 관리자는 끄거나 일반 계정으로 바꿀 수 없습니다.")
    if "name" in data:
        u.name = str(data["name"] or "").strip()[:100]
    if "title" in data:
        u.title = str(data["title"] or "").strip()[:100]
    if "isAdmin" in data:
        u.is_admin = bool(data["isAdmin"])
    if "isActive" in data:
        u.is_active = bool(data["isActive"])
        if not u.is_active:
            import secrets
            u.session_token = secrets.token_hex(8)  # 다른 기기 로그인도 바로 풀림
    if data.get("password"):
        _check_password(data["password"])
        u.set_password(data["password"])
    if data.get("unlock") or data.get("password"):
        u.failed_login_count, u.locked_until = 0, None
    db.session.commit()
    worklog.add("settings", "승인 · 선정", f"계정 수정: {u.name}", approval=current_user.name or current_user.email,
                detail={"fields": sorted(k for k in data if k != "password") + (["password"] if data.get("password") else [])})
    return jsonify(_user_json(u))


@bp.patch("/api/me")
def me_update():
    from flask import jsonify

    from ..security import json_body
    data = json_body()
    if "name" in data:
        current_user.name = str(data["name"] or "").strip()[:100] or current_user.email.split("@")[0]
    if "title" in data:
        current_user.title = str(data["title"] or "").strip()[:100]
    db.session.commit()
    return jsonify(current_user.to_public())


@bp.post("/api/me/password")
def me_password():
    from flask import jsonify

    from ..security import ApiError, json_body
    data = json_body()
    if not current_user.check_password(str(data.get("current", ""))):
        raise ApiError("현재 비밀번호가 맞지 않습니다.")
    _check_password(data.get("new"))
    current_user.set_password(data["new"])
    db.session.commit()
    # 비밀번호를 바꾸면 세션 토큰이 바뀌므로 이 브라우저는 다시 로그인 상태로 만들어 둠
    login_user(current_user._get_current_object(), remember=False)
    return jsonify(ok=True)
