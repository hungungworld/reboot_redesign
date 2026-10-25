"""테스트 공통 준비: 임시 DATA_DIR · 임시 DB · 테스트 계정."""
import pytest

from server import create_app
from server.extensions import db
from server.models import User

PASSWORD = "password123"


def make_app(tmp_path, monkeypatch, **overrides):
    for name in ("ADMIN_EMAIL", "ADMIN_PASSWORD", "OPENAI_API_KEY", "open_api", "AI_API_KEY", "ANTHROPIC_API_KEY",
                 "GMAIL_ADDRESS", "gmail_address", "GMAIL_APP_PASSWORD", "gmail_api", "SERPAPI_KEY", "YOUTUBE_API_KEY",
                 "FIRECRAWL_API_KEY", "KEXIM_API_KEY", "GMAIL_TOKEN_JSON", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                 "SLACK_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)
    cfg = {
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "DATA_DIR": tmp_path,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "CRON_TOKEN": "test-cron-token",
    }
    cfg.update(overrides)
    app = create_app(cfg)
    with app.app_context():
        admin = User(email="tester@reboot.test", name="테스터", title="QA", is_admin=True)
        admin.set_password(PASSWORD)
        member = User(email="member@reboot.test", name="팀원")
        member.set_password(PASSWORD)
        db.session.add_all([admin, member])
        db.session.commit()
    return app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return make_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def login(client):
    def _login(email="tester@reboot.test", password=PASSWORD, remember=False, next_url=None):
        url = "/login" + (f"?next={next_url}" if next_url else "")
        data = {"email": email, "password": password}
        if remember:
            data["remember"] = "y"
        return client.post(url, data=data, follow_redirects=False)
    return _login


@pytest.fixture()
def auth_client(client, login):
    res = login()
    assert res.status_code == 302, res.data[:500]
    return client
