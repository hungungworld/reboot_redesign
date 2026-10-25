"""홈 · 데일리 브리핑 API (담당: 이현경).

  GET    /api/home/news              저장된 뉴스 (구글 뉴스를 부르지 않음) — 기사 · AI 한국어 요약 · 급증 시그널 · 수집 상태
  POST   /api/home/news/collect      지금 수집 (뒤에서 실행, 바로 응답) → 화면이 /news/status로 끝났는지 확인
  GET    /api/home/news/status       수집 중인지 · 마지막 수집 결과
  GET    /api/home/news/saved        보관한 기사 (최신 보관 순) — 시장·고객 인사이트 › 데이터 › 뉴스 수집도 이 주소를 씀
  POST   /api/home/news/saved        {url, title, titleKo, summaryKo, whyKo, kind, areaLabel, domain, date} 보관 (같은 URL이면 덮어씀)
  DELETE /api/home/news/saved/<id>   보관 해제
  GET    /api/home/todos             이번 주 To-Do (?week=YYYY-MM-DD 그 주 월요일)
  POST   /api/home/todos/generate    {auto, context} → AI(키 있을 때) 또는 규칙으로 생성
  POST   /api/home/todos             {title, reason} 직접 추가
  PATCH  /api/home/todos/<id>        {done, title, reason}
  DELETE /api/home/todos/<id>
하루 1회 작업: 뉴스 수집 → 이번 주 To-Do가 없으면 자동 생성 (POST /api/jobs/daily)
"""
import re
from datetime import date, datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from server import jobs
from server.extensions import db
from server.models import utcnow
from server.security import ApiError, clip, json_body
from . import news, todos
from .models import HomeNewsSaved, HomeTodo

bp = Blueprint("home", __name__, url_prefix="/api/home")


def _who():
    return current_user.name or current_user.email.split("@")[0]


# ───────── 뉴스 ─────────

@bp.get("/news")
def news_get():
    return jsonify(news.payload())


@bp.get("/news/status")
def news_status():
    run = news.running_run()
    latest = run or news.HomeNewsRun.query.order_by(news.HomeNewsRun.id.desc()).first()
    return jsonify(running=run is not None, lastRun=latest.to_dict() if latest else None)


@bp.post("/news/collect")
def news_collect():
    if news.running_run() is not None or jobs.is_running("home-news-collect"):
        raise ApiError("이미 수집 중입니다. 끝나면 화면이 바뀝니다 (보통 2~5분).", 409)
    run = news.start_run("manual", _who())
    if jobs.run_background(current_app._get_current_object(), "home-news", news.collect,
                           run_id=run.id, user_name=_who()) is None:
        # 확인과 시작 사이에 다른 요청이 먼저 시작한 경우 → 이 실행 기록은 닫음
        news._finish_failed(run, "이미 수집 중이라 건너뜀")
        raise ApiError("이미 수집 중입니다. 끝나면 화면이 바뀝니다 (보통 2~5분).", 409)
    return jsonify(started=True, runId=run.id), 202


def _date(text):
    try:
        d = datetime.fromisoformat(str(text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


@bp.get("/news/saved")
def saved_get():
    rows = HomeNewsSaved.query.order_by(HomeNewsSaved.saved_at.desc(), HomeNewsSaved.id.desc()).all()
    return jsonify(items=[r.to_dict() for r in rows])


@bp.post("/news/saved")
def saved_add():
    data = json_body()
    url = clip(data.get("url"), 1000).strip()
    if not re.match(r"^https?://", url, re.I):
        raise ApiError("기사 주소(http/https)가 필요합니다.")
    title = clip(data.get("title"), 500).strip()
    title_ko = clip(data.get("titleKo"), 300).strip()
    if not (title or title_ko):
        raise ApiError("기사 제목이 필요합니다.")
    s = HomeNewsSaved.query.filter_by(url=url).first()
    created = s is None
    if created:
        s = HomeNewsSaved(url=url)
        db.session.add(s)
    s.title, s.title_ko = title, title_ko
    s.summary_ko, s.why_ko = clip(data.get("summaryKo"), 600).strip(), clip(data.get("whyKo"), 600).strip()
    s.kind = "trend" if data.get("kind") == "trend" else "flow"
    s.area_label, s.domain = clip(data.get("areaLabel"), 60).strip(), clip(data.get("domain"), 200).strip()
    s.published_at = _date(data.get("date"))
    s.saved_by, s.saved_at = _who(), utcnow()
    db.session.commit()
    return jsonify(s.to_dict()), 201 if created else 200


@bp.delete("/news/saved/<int:sid>")
def saved_delete(sid):
    s = db.session.get(HomeNewsSaved, sid)
    if s is None:
        raise ApiError("없는 보관 기사입니다.", 404)
    db.session.delete(s)
    db.session.commit()
    return jsonify(ok=True)


@jobs.daily("home-news", "홈 데일리 브리핑: 미국 주류 업계 뉴스 수집(구글 뉴스) · AI 요약 → 이번 주 To-Do 자동 생성")
def daily_briefing():
    news.collect(trigger="daily", user_name="시스템")
    todos.generate({}, auto=True, user="시스템")


# ───────── To-Do ─────────

def _todo(tid):
    t = db.session.get(HomeTodo, tid)
    if t is None:
        raise ApiError("없는 할 일입니다.", 404)
    return t


@bp.get("/todos")
def todos_get():
    ws = None
    if request.args.get("week"):
        try:
            ws = todos.week_start(date.fromisoformat(request.args["week"][:10]))
        except ValueError:
            raise ApiError("week는 YYYY-MM-DD 형식이어야 합니다.")
    return jsonify(todos.week_payload(ws))


@bp.post("/todos/generate")
def todos_generate():
    data = json_body(max_bytes=64 * 1024)
    return jsonify(todos.generate(data.get("context"), auto=bool(data.get("auto")), user=_who()))


@bp.post("/todos")
def todos_add():
    data = json_body()
    title = clip(data.get("title"), 200).strip()
    if not title:
        raise ApiError("할 일을 입력하세요.")
    ws = todos.week_start()
    top = db.session.query(db.func.max(HomeTodo.sort)).filter(HomeTodo.week_start == ws).scalar() or 0
    src = str(data.get("source") or "home")
    t = HomeTodo(week_start=ws, title=title, reason=clip(data.get("reason"), 500).strip() or "직접 추가",
                 source=src if src in todos.SOURCES else "home", origin="manual", sort=top + 1, created_by=_who())
    db.session.add(t)
    db.session.commit()
    return jsonify(t.to_dict()), 201


@bp.patch("/todos/<int:tid>")
def todos_update(tid):
    t = _todo(tid)
    data = json_body()
    if "title" in data:
        title = clip(data.get("title"), 200).strip()
        if not title:
            raise ApiError("할 일을 입력하세요.")
        t.title = title
    if "reason" in data:
        t.reason = clip(data.get("reason"), 500).strip()
    if "done" in data:
        done = bool(data.get("done"))
        if done != t.done:
            t.done = done
            t.done_at = utcnow() if done else None
            t.done_by = _who() if done else ""
    db.session.commit()
    return jsonify(t.to_dict())


@bp.delete("/todos/<int:tid>")
def todos_delete(tid):
    t = _todo(tid)
    db.session.delete(t)
    db.session.commit()
    return jsonify(ok=True)
