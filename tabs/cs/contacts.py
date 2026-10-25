"""CS 담당자 연락처 (서버 보관) — 문의 전달 · 승인 요청 · 부작용 접수 공유를 받을 팀 사람들.

Gmail로 담당자에게 보내기(/api/cs/notify)는 여기 등록된 주소로만 보냅니다 (아무 주소로나 보내지 못하게).
저장: DATA_DIR/cs/contacts.json · 역할(role)은 답변 템플릿의 담당 이름과 같게 쓰면 문의를 열 때 자동으로 골라짐.
"""
import json
import re
import threading
from pathlib import Path

from flask import current_app

EMAIL = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}$")
MAX = 30
_lock = threading.Lock()


class ContactError(Exception):
    pass


def _path():
    return Path(current_app.config["DATA_DIR"]) / "cs" / "contacts.json"


def load():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(items, by=""):
    if not isinstance(items, list) or len(items) > MAX:
        raise ContactError(f"담당자는 {MAX}명까지 등록할 수 있습니다.")
    out, seen = [], set()
    for c in items:
        if not isinstance(c, dict):
            continue
        email = str(c.get("email", "")).strip()
        role = str(c.get("role", "")).strip()[:60]
        name = str(c.get("name", "")).strip()[:60]
        if not email and not role and not name:
            continue
        if not EMAIL.match(email):
            raise ContactError(f"이메일 주소가 올바르지 않습니다: {email or '(빈칸)'}")
        if email.lower() in seen:
            continue
        seen.add(email.lower())
        out.append({"role": role, "name": name, "email": email})
    with _lock:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    return out


def allowed(email):
    e = str(email or "").strip().lower()
    return any(c["email"].lower() == e for c in load())
