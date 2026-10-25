"""첫 관리자 계정 만들기.

계정이 하나도 없을 때만, 환경변수 ADMIN_EMAIL · ADMIN_PASSWORD(· ADMIN_NAME)로 관리자를 만듭니다.
이후에 환경변수를 바꿔도 비밀번호는 바뀌지 않습니다 → 설정 › 계정에서 바꾸거나
`flask --app server set-password <이메일>`을 씁니다.
"""
import os

from ..extensions import db
from ..models import User


def ensure_admin(app):
    if db.session.query(User.id).first() is not None:
        return None
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        app.logger.warning("로그인 계정이 없습니다. ADMIN_EMAIL·ADMIN_PASSWORD를 넣고 다시 실행하거나 "
                           "`flask --app server create-user <이메일> --admin`으로 만드세요.")
        return None
    if len(password) < 8:
        app.logger.error("ADMIN_PASSWORD는 8자 이상이어야 합니다. 관리자 계정을 만들지 않았습니다.")
        return None
    from .emails import normalize_email
    try:
        email = normalize_email(email)
    except ValueError:
        app.logger.error("ADMIN_EMAIL 형식이 올바르지 않습니다. 관리자 계정을 만들지 않았습니다.")
        return None
    user = User(email=email, name=os.environ.get("ADMIN_NAME", "").strip() or email.split("@")[0],
                title=os.environ.get("ADMIN_TITLE", "").strip(), is_admin=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    app.logger.info("관리자 계정을 만들었습니다: %s", email)
    return user
