"""로그인 폼 (로그인 README 10장 forms.py)."""
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length

from .emails import AccountEmail

EMAIL_MSG = "올바른 이메일을 입력해 주세요."
PASSWORD_MSG = "비밀번호를 입력해 주세요."


class LoginForm(FlaskForm):
    email = StringField("이메일", validators=[DataRequired(EMAIL_MSG), Length(max=255, message=EMAIL_MSG), AccountEmail(EMAIL_MSG)])
    password = PasswordField("비밀번호", validators=[DataRequired(PASSWORD_MSG), Length(max=128, message=PASSWORD_MSG)])
    remember = BooleanField("로그인 상태 유지")
    submit = SubmitField("로그인")
