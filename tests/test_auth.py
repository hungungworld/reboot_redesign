"""로그인 README 11장 테스트 체크리스트."""
from datetime import timedelta

from server.extensions import db
from server.models import User, utcnow

from conftest import PASSWORD, make_app

FAIL = "이메일 또는 비밀번호가 올바르지 않습니다."


def body(res):
    return res.get_data(as_text=True)


def test_login_success_goes_to_dashboard(login):
    res = login()
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/dashboard")


def test_wrong_password_shows_common_message(login):
    res = login(password="wrong-password")
    assert res.status_code == 200
    assert FAIL in body(res)


def test_unknown_email_shows_same_message(login):
    res = login(email="nobody@reboot.test")
    assert FAIL in body(res)


def test_invalid_email_format_message(login):
    res = login(email="not-an-email")
    assert "올바른 이메일을 입력해 주세요." in body(res)


def test_five_failures_lock_account(app, login):
    for _ in range(5):
        login(password="wrong-password")
    res = login()  # 맞는 비밀번호여도 잠김
    assert "로그인 시도가 너무 많습니다" in body(res)
    with app.app_context():
        u = User.query.filter_by(email="tester@reboot.test").first()
        assert u.locked_until is not None and u.locked_until > utcnow()
        u.locked_until = utcnow() - timedelta(seconds=1)  # 잠금 시간이 지나면 다시 로그인
        db.session.commit()
    assert login().status_code == 302


def test_inactive_account(app, login):
    with app.app_context():
        u = User.query.filter_by(email="member@reboot.test").first()
        u.is_active = False
        db.session.commit()
    res = login(email="member@reboot.test")
    assert "비활성화된 계정입니다" in body(res)


def test_csrf_missing_returns_400(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, WTF_CSRF_ENABLED=True)
    res = app.test_client().post("/login", data={"email": "tester@reboot.test", "password": PASSWORD})
    assert res.status_code == 400


def test_next_parameter_same_site(login):
    res = login(next_url="/dashboard/reports")
    assert res.headers["Location"].endswith("/dashboard/reports")


def test_next_parameter_external_is_ignored(login):
    for bad in ("https://evil.com", "//evil.com", "/\\evil.com"):
        res = login(next_url=bad)
        assert res.headers["Location"].endswith("/dashboard"), bad


def test_logged_in_user_redirected_from_login(auth_client):
    res = auth_client.get("/login")
    assert res.status_code == 302 and res.headers["Location"].endswith("/dashboard")


def test_remember_me_sets_cookie(client, login):
    res = login(remember=True)
    cookies = res.headers.getlist("Set-Cookie")
    assert any(c.startswith("reboot_remember=") for c in cookies)


def test_logout_then_dashboard_redirects_to_login(auth_client):
    res = auth_client.post("/logout")
    assert res.status_code == 302 and "/login" in res.headers["Location"]
    res = auth_client.get("/dashboard")
    assert res.status_code == 302
    assert "/login?next=" in res.headers["Location"]


def test_password_change_invalidates_old_sessions(app, auth_client):
    assert auth_client.get("/dashboard").status_code == 200
    with app.app_context():
        u = User.query.filter_by(email="tester@reboot.test").first()
        u.set_password("new-password-1")
        db.session.commit()
    assert auth_client.get("/dashboard").status_code == 302


def test_forgot_password_page(client):
    assert "비밀번호 찾기" in body(client.get("/forgot-password"))


def test_admin_bootstrap_from_env(tmp_path, monkeypatch):
    from server import create_app
    monkeypatch.setenv("ADMIN_EMAIL", "Boss@Reboot.Test")
    monkeypatch.setenv("ADMIN_PASSWORD", "very-secret-1")
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'boot.db').as_posix()}"})
    with app.app_context():
        u = User.query.filter_by(email="boss@reboot.test").first()
        assert u is not None and u.is_admin and u.check_password("very-secret-1")
