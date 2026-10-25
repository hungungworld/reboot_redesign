"""/api 공통 규칙 (공통 담당).

- /api/* 는 로그인한 사용자만 (아니면 401 JSON). 예외는 PUBLIC_API에 등록
- 쓰기 요청(POST·PUT·PATCH·DELETE)은 CSRF 토큰 필요 — core/core.js가 같은 주소 요청에 자동으로 붙임
- 다른 사이트에서 온 쓰기 요청은 거절 (Sec-Fetch-Site)
- 오류는 화면에 그대로 보여 줄 수 있는 한국어 문장으로 {"error": "..."}

파트 API에서 쓰는 법
    from server.security import ApiError, json_body
    data = json_body()                      # JSON 객체가 아니면 400
    if not data.get("text"): raise ApiError("문구가 없습니다.")
"""
from flask import jsonify, request
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

# 로그인 없이 부를 수 있는 API (엔드포인트 이름)
PUBLIC_API = {"core.jobs_daily"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class ApiError(Exception):
    """화면에 그대로 보여 줄 수 있는 실패 (키·내부 정보 넣지 않기)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def json_body(max_bytes=256 * 1024):
    if not request.is_json:
        raise ApiError("JSON만 받습니다.", 415)
    if request.content_length is not None and request.content_length > max_bytes:
        raise ApiError("요청이 너무 큽니다.", 413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("JSON 객체만 받습니다.")
    return data


def clip(value, n):
    """문자열로 바꾸고 길이를 자름 (None → '')."""
    return str(value if value is not None else "")[:n]


def is_api():
    return request.path.startswith("/api/")


def register_api_guards(app):
    @app.before_request
    def _api_guard():
        if not is_api():
            return None
        if request.method in UNSAFE and request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="다른 사이트에서 온 요청은 받지 않습니다."), 403
        if request.endpoint in PUBLIC_API:
            return None
        if not current_user.is_authenticated:
            return jsonify(error="로그인이 필요합니다. 새로고침 후 다시 로그인해 주세요.", login=True), 401
        return None

    @app.after_request
    def _no_store(resp):
        if is_api():
            resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    @app.errorhandler(ApiError)
    def _api_error(e):
        return jsonify(error=str(e)), e.status

    from .core.ai import AIError

    @app.errorhandler(AIError)
    def _ai_error(e):
        return jsonify(error=str(e)), e.status

    @app.errorhandler(CSRFError)
    def _csrf_error(e):
        if is_api():
            return jsonify(error="보안 토큰이 맞지 않습니다. 새로고침 후 다시 시도하세요."), 400
        return e.description, 400

    @app.errorhandler(HTTPException)
    def _http_error(e):
        if is_api():
            msg = {404: "없는 API입니다.", 405: "허용되지 않은 방식입니다.", 413: "요청(파일)이 너무 큽니다.",
                   429: "요청이 많습니다. 잠시 뒤 다시 시도하세요."}.get(e.code, e.description or "요청을 처리하지 못했습니다.")
            return jsonify(error=msg), e.code
        page_msg = {403: "이 화면을 볼 권한이 없습니다.", 404: "찾는 화면이 없습니다. 주소를 확인해 주세요.",
                    429: "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요."}.get(e.code)
        if page_msg:
            from flask import render_template
            return render_template("errors/error.html", code=e.code, message=page_msg), e.code
        return e

    @app.errorhandler(Exception)
    def _unexpected(e):
        if isinstance(e, HTTPException):
            return _http_error(e)
        app.logger.exception("처리하지 못한 오류: %s", type(e).__name__)
        if is_api():
            return jsonify(error="서버 내부 오류가 났습니다. 잠시 뒤 다시 시도하세요."), 500
        from flask import render_template
        return render_template("errors/error.html", code=500, message="일시적인 문제가 생겼습니다. 잠시 뒤 다시 시도해 주세요."), 500
