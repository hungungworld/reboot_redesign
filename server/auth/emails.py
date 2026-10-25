"""계정 이메일 형식 확인 (로그인 · 계정 만들기 공통).

email-validator로 형식을 확인합니다. 다만 사내 시연용 도메인(.local · .test 등 예약 도메인)은
email-validator가 거부하므로, 형식(이름@도메인.끝)만 맞으면 허용합니다.
"""
import re

from email_validator import EmailNotValidError, validate_email
from wtforms.validators import ValidationError

MESSAGE = "올바른 이메일을 입력해 주세요."
_BASIC = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value):
    """형식이 맞으면 소문자 이메일을, 아니면 ValueError(MESSAGE)."""
    value = (value or "").strip()
    if not value or len(value) > 255:
        raise ValueError(MESSAGE)
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError as e:
        if "special-use" in str(e) and _BASIC.match(value):
            return value.lower()
        raise ValueError(MESSAGE) from None


class AccountEmail:
    """WTForms 검증기: normalize_email과 같은 기준."""

    def __init__(self, message=MESSAGE):
        self.message = message

    def __call__(self, form, field):
        try:
            normalize_email(field.data)
        except ValueError:
            raise ValidationError(self.message)
