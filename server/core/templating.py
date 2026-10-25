"""화면 템플릿 도우미 (공통 담당): 아이콘 · 캐시 버전이 붙은 파일 주소 · 인사말."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, url_for
from markupsafe import Markup, escape

from ..config import ROOT
from ..registry import PARTS, group_of, nav_groups, nav_parts, static_dir

# 디자인 시안(REBOOT Marketing Workspace)의 선 아이콘. core/core.js의 ICONS와 같은 목록
ICONS = {
    "grid": "M3 3h6v6H3z M15 3h6v6h-6z M3 15h6v6H3z M15 15h6v6h-6z",
    "home": "M3 10l9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z",
    "spark": "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
    "shield": "M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z m-4 9 3 3 5-6",
    "chat": "M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3H3V6a2 2 0 0 1 2-2 M7 9h10 M7 13h6",
    "users": "M16 21v-3a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v3 M9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M17 3a4 4 0 0 1 0 8 M22 21v-3a4 4 0 0 0-3-4",
    "chart": "M3 3v18h18 M7 16v-5 M12 16V6 M17 16V9",
    "calendar": "M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2 M7 3v4 M17 3v4 M3 11h18",
    "settings": "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M10 2h4l1 3 3 1 3 3-1 3 1 3-3 3-3 1-1 3h-4l-1-3-3-1-3-3 1-3-1-3 3-3 3-1z",
    "bell": "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M9 21h6",
    "arrow": "M5 12h14 M13 6l6 6-6 6",
    "up": "M6 17 17 6 M6 6h11v11",
    "plus": "M12 5v14 M5 12h14",
    "image": "M3 3h18v18H3z M3 16l5-5 5 5 3-3 5 5 M16 7h.01",
    "video": "M3 4h13v16H3z M16 9l5-3v12l-5-3",
    "doc": "M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h5",
    "mail": "M3 5h18v14H3z M3 5l9 8 9-8",
    "search": "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6",
    "globe": "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M2 12h20 M12 2c-6 5-6 15 0 20 M12 2c6 5 6 15 0 20",
    "check": "m5 12 4 4L19 6",
    "download": "M12 3v12 m-5-5 5 5 5-5 M4 16v5h16v-5",
    "clock": "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M12 6v6l4 2",
    "filter": "M4 6h16 M7 12h10 M10 18h4",
    "edit": "m15 3 6 6-12 12H3v-6z M12 6l6 6",
    "dollar": "M12 2v20 M18 6c-6-6-16 2-6 6s0 12-6 6",
    "logout": "M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4 M10 17l5-5-5-5 M15 12H3",
    "menu": "M4 6h16 M4 12h16 M4 18h16",
    "moon": "M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z",
    "user": "M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10 M3 22a9 9 0 0 1 18 0",
}


def icon(name, cls="icon"):
    d = ICONS.get(name, ICONS["grid"])
    return Markup(f'<svg class="{escape(cls)}" viewBox="0 0 24 24" aria-hidden="true"><path d="{d}"/></svg>')


def _mtime(path):
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def asset(kind, path):
    """kind='core' → /core/<path>?v=… · kind='parts' → /parts/<path>?v=… (DATA_DIR에 덮어쓴 파일이 있으면 그 시각)."""
    if kind == "core":
        return url_for("static", filename=path, v=_mtime(ROOT / "core" / path))
    override = Path(current_app.config["DATA_DIR"]) / "parts_override" / path
    pid, _, name = path.partition("/")
    v = _mtime(override) if override.is_file() else _mtime(static_dir(pid) / name)
    return url_for("core.part_file", filename=path, v=v)


def local_now():
    try:
        tz = ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Seoul"))
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=9))
    return datetime.now(tz)


def greeting(name):
    h = local_now().hour
    if 5 <= h < 11:
        msg = "좋은 아침이에요"
    elif 11 <= h < 17:
        msg = "좋은 오후예요"
    elif 17 <= h < 22:
        msg = "오늘도 수고 많았어요"
    else:
        msg = "늦은 시간까지 수고 많아요"
    return f"{msg}, {name}님"


def register_template_helpers(app):
    app.jinja_env.globals.update(icon=icon, asset=asset, all_parts=PARTS, nav_parts=nav_parts,
                               nav_groups=nav_groups, group_of=group_of)
