"""작업 로그 남기기 (공통 담당) — 설정 README 4장.

    from server import worklog
    worklog.add("insights", "수집", "주간 데이터 수집", detail={"count": 12})
    worklog.add("content", "AI 프롬프트", "카피 초안 작성", detail={"model": "...", "tokens": 812})

- 구분: 수집 · AI 프롬프트 · 승인 · 선정 · 기타
- 상태: 완료 · 검토 대기 · 실패      승인: 자동 · 승인한 사람 이름 · –
- 이메일 주소·키처럼 보이는 값은 가려서 저장합니다. 로그는 수정·삭제하지 않습니다.
"""
import json
import re

from flask import has_request_context
from flask_login import current_user

from .extensions import db
from .models import WorkLog

KINDS = ("수집", "AI 프롬프트", "승인 · 선정", "기타")
STATUSES = ("완료", "검토 대기", "실패")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_KEYLIKE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|[A-Za-z0-9_-]{36,})\b")


def redact(value):
    if isinstance(value, str):
        return _KEYLIKE.sub("[가림]", _EMAIL.sub("[이메일]", value))
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _who():
    if has_request_context() and current_user and current_user.is_authenticated:
        return current_user.name or current_user.email.split("@")[0]
    return "시스템"


def add(part, kind, action, status="완료", approval=None, detail=None, user_name=None):
    kind = kind if kind in KINDS else "기타"
    status = status if status in STATUSES else "완료"
    if approval is None:
        approval = "–" if status == "검토 대기" else "자동"
    body = json.dumps(redact(detail or {}), ensure_ascii=False, default=str)
    if len(body) > 60_000:  # 프롬프트 전문이 너무 길면 앞부분만
        body = json.dumps({"truncated": True, "head": body[:20_000]}, ensure_ascii=False)
    entry = WorkLog(part=str(part or "")[:40], kind=kind, action=redact(str(action or ""))[:200], status=status,
                    approval=str(approval)[:100], detail=body, user_name=(user_name or _who())[:100])
    try:
        db.session.add(entry)
        db.session.commit()
    except Exception:  # 로그 실패가 본 작업을 막지 않게
        db.session.rollback()
        return None
    return entry
