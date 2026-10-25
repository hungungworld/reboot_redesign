"""REBOOT US 마케팅 대시보드 — Flask 앱 (공통 담당).

    로컬 실행:   python -m server            → http://127.0.0.1:5000
    Render:      gunicorn -c gunicorn.conf.py wsgi:app

구성
  core/                화면 공통 (base.css · core.js) — /core/… 로 제공
  server/              공통 서버 (설정 · 로그인 · 공용 기준 데이터 · 화면 셸)
  tabs/<파트ID>/       탭 하나의 모든 것
    *.py               파트 서버 기능 (routes.py의 bp, models.py) — /api/<파트ID>/…
    static/            파트 화면 (JS·CSS·data.js) — /parts/<파트ID>/… 로 제공 (로그인 필요)
    docs/ · data/ · tests/
"""
import importlib
import logging
import os

from flask import Flask
from sqlalchemy import event
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import ROOT, Config, finalize
from .extensions import csrf, db, limiter, login_manager
from .registry import PARTS
from .security import register_api_guards

# 서버 모듈: <패키지>/routes.py 에 bp(Blueprint)가 있으면 등록, models.py가 있으면 표를 만듦
# 공통은 server.<이름>, 탭(파트)은 tabs.<파트ID>
MODULES = tuple(f"server.{n}" for n in ("core", "auth", "catalog")) + tuple(f"tabs.{p.id}" for p in PARTS)


def create_app(test_config=None):
    app = Flask(
        __name__,
        static_folder=str(ROOT / "core"),
        static_url_path="/core",
        template_folder="templates",
    )
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    finalize(app.config)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Render는 앞단 프록시를 거쳐 들어오므로 실제 접속 IP·https 여부를 헤더에서 읽음
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "로그인이 필요합니다."
    login_manager.login_message_category = "info"
    login_manager.session_protection = "basic"

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        uid, _, token = str(user_id).partition(":")
        if not uid.isdigit():
            return None
        user = db.session.get(User, int(uid))
        if user is None or user.session_token != token or not user.is_active:
            return None
        return user

    register_api_guards(app)

    from .core.templating import register_template_helpers
    register_template_helpers(app)

    for name in MODULES:
        _try_import(app, f"{name}.models")
        mod = _try_import(app, f"{name}.routes")
        bp = getattr(mod, "bp", None) if mod else None
        if bp is not None:
            app.register_blueprint(bp)

    with app.app_context():
        _prepare_database(app)
        from .auth.bootstrap import ensure_admin
        ensure_admin(app)

    from .auth.cli import register_cli
    register_cli(app)
    return app


def _try_import(app, dotted):
    try:
        return importlib.import_module(dotted)
    except ModuleNotFoundError as e:
        if e.name == dotted or (e.name and dotted.startswith(e.name + ".")):
            return None  # 아직 없는 파트 모듈
        raise


def _prepare_database(app):
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        @event.listens_for(db.engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=20000")
            cur.close()
    db.create_all()
    for sub in ("uploads", "cache", "parts_override", "backups", "locks"):
        os.makedirs(os.path.join(app.config["DATA_DIR"], sub), exist_ok=True)
    # 공용 기준 데이터 초기값 (표가 비어 있을 때만): data/seasons.csv · data/products.json
    from .catalog import service as catalog
    try:
        catalog.seed_seasons()
        catalog.seed_facts()
    except Exception:
        db.session.rollback()
        app.logger.exception("시즌·팩트시트 초기값을 넣지 못했습니다")
