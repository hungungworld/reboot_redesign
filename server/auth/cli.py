"""터미널 명령 (flask --app server <명령>).

  create-user <이메일> [--name 이름] [--title 소속] [--admin]   계정 만들기 (비밀번호는 입력 창에서)
  set-password <이메일>                                        비밀번호 바꾸기 (잠금도 풀림)
  list-users                                                   계정 목록
  run-daily                                                    하루 1회 작업을 지금 실행 (끝날 때까지 기다림)
"""
import click
from flask import current_app

from ..extensions import db
from ..models import User


def _password_prompt():
    pw = click.prompt("비밀번호 (8자 이상)", hide_input=True, confirmation_prompt="한 번 더")
    if len(pw) < 8:
        raise click.ClickException("비밀번호는 8자 이상이어야 합니다.")
    return pw


def register_cli(app):
    @app.cli.command("create-user")
    @click.argument("email")
    @click.option("--name", default="", help="표시 이름")
    @click.option("--title", default="", help="소속·역할 (예: 글로벌사업팀 · 미국 채널 담당)")
    @click.option("--admin", is_flag=True, help="관리자 (계정 관리·키 입력 가능)")
    @click.option("--password", default=None, help="비밀번호 (생략하면 입력 창)")
    def create_user(email, name, title, admin, password):
        from .emails import normalize_email
        try:
            email = normalize_email(email)
        except ValueError as e:
            raise click.ClickException(str(e))
        if User.query.filter_by(email=email).first():
            raise click.ClickException(f"이미 있는 이메일입니다: {email}")
        pw = password or _password_prompt()
        if len(pw) < 8:
            raise click.ClickException("비밀번호는 8자 이상이어야 합니다.")
        user = User(email=email, name=name or email.split("@")[0], title=title, is_admin=admin)
        user.set_password(pw)
        db.session.add(user)
        db.session.commit()
        click.echo(f"계정을 만들었습니다: {email}{' (관리자)' if admin else ''}")

    @app.cli.command("set-password")
    @click.argument("email")
    @click.option("--password", default=None, help="새 비밀번호 (생략하면 입력 창)")
    def set_password(email, password):
        user = User.query.filter_by(email=email.strip().lower()).first()
        if not user:
            raise click.ClickException("없는 이메일입니다.")
        pw = password or _password_prompt()
        if len(pw) < 8:
            raise click.ClickException("비밀번호는 8자 이상이어야 합니다.")
        user.set_password(pw)
        user.failed_login_count, user.locked_until = 0, None
        db.session.commit()
        click.echo("비밀번호를 바꿨습니다. 다른 기기의 로그인 유지도 함께 풀립니다.")

    @app.cli.command("list-users")
    def list_users():
        for u in User.query.order_by(User.id).all():
            click.echo(f"{u.id:>3}  {u.email:<32} {u.name:<10} {'관리자' if u.is_admin else '일반':<4} "
                       f"{'활성' if u.is_active else '비활성'}")

    @app.cli.command("run-daily")
    def run_daily():
        from .. import jobs
        names = jobs.run_daily(current_app._get_current_object(), wait=True)
        click.echo("실행한 작업: " + (", ".join(names) or "없음"))

    @app.cli.command("gmail-login")
    def gmail_login():
        """로컬 PC에서 Gmail(CS) 로그인 — 브라우저가 열림. DATA_DIR/google/credentials.json 필요."""
        from tabs.cs import gmail
        try:
            svc = gmail.service(interactive=True)
        except gmail.LoginRequired as e:
            raise click.ClickException(str(e))
        click.echo(f"연결됨: {gmail.profile_email(svc)}  (로그인 정보: {gmail.token_path()})")
