"""데이터 업데이트 — 항목을 골라 뒤에서 실행 (README '데이터 업데이트' · '월간 분석 실행').

- plan(): 항목 · 필요한 키 · 예상 비용 · 이번 달 쓴 양 (화면 확인 창)
- start(): 실행 기록(Run)을 만들고 jobs.run_background로 뒤에서 run() 실행
- 항목마다 결과(성공 · 실패 문장)를 Run에 바로 써서 화면이 진행 상황을 봄
- 수집은 작업 로그 '수집', AI 호출은 openai_json이 'AI 프롬프트'로 자동 기록, 액션 선정은 '승인 · 선정'(자동)
"""
import json
import math
from datetime import datetime, timedelta

from flask import current_app

from server import envkeys, jobs, worklog
from server.core.ai import AIError
from server.extensions import db
from server.models import iso, utcnow
from tabs.settings.config import get_collect_config
from . import ai as insights_ai
from . import accuracy, amazon, channel_reviews, competitors, rules, service, trends, weee, yami, youtube
from .http import CollectError
from .models import Comment, Monthly, Review, Run, ShelfItem, load_json
from .util import review_month, week_key

JOB = "insights-update"
ITEMS = [
    {"id": "weee", "label": "Weee! 진열대 · 리뷰", "group": "weekly", "needs": ["FIRECRAWL_API_KEY"],
     "desc": "목록 · 상품 페이지에서 가격 · 품절 · 핵심 메시지 · 상품별 구매자 리뷰"},
    {"id": "amazon", "label": "Amazon 진열대 · 리뷰", "group": "weekly", "needs": ["SERPAPI_KEY"],
     "desc": "검색 상위(광고 제외) · 리뷰 많은 상품 상세 · 대표 리뷰"},
    {"id": "yami", "label": "Yami 진열대", "group": "weekly", "needs": ["FIRECRAWL_API_KEY"],
     "desc": "숙취 검색 결과 첫 페이지: 브랜드 · 가격 · 정가 · 판매량"},
    {"id": "channel_reviews", "label": "채널 리뷰 (Olive Young · Amazon · Yami)", "group": "weekly", "needs": ["FIRECRAWL_API_KEY"],
     "desc": "알디콤 상품의 구매자 리뷰 · 별점 (Olive Young 영어 리뷰 전부 · Amazon 대표 리뷰는 SerpApi)"},
    {"id": "trends", "label": "검색 트렌드", "group": "weekly", "needs": ["SERPAPI_KEY"],
     "desc": "트렌드 키워드마다 미국 12개월 (키가 없으면 CSV 올리기)"},
    {"id": "reviews", "label": "리뷰 분류", "group": "weekly", "needs": [], "ai": True,
     "desc": "주제(효과 · 맛·향 · 복용 편의 · 부작용 · 가격) · 칭찬/불만"},
    {"id": "competitors", "label": "경쟁사 분석", "group": "weekly", "needs": [], "ai": True,
     "desc": "새 경쟁사 등록 · 제형 분류 · 리뷰 상위 15개 브랜드 분석(진열대 · 홈페이지 · 뉴스 → AI 웹 검색, 25일 지난 것만)"},
    {"id": "actions", "label": "이번 주 액션", "group": "weekly", "needs": [], "ai": True,
     "desc": "규칙으로 신호를 고르고, AI 키가 있으면 문장을 다듬음"},
    {"id": "youtube", "label": "유튜브 수집", "group": "monthly", "needs": ["YOUTUBE_API_KEY"],
     "desc": "검색어 세트 A · B · K → 영상 · 댓글"},
    {"id": "voc", "label": "VOC 분류", "group": "monthly", "needs": [], "ai": True,
     "desc": "댓글의 관련 여부 · 질문 주제 (사람 정답 예시 24건을 함께 줌)"},
    {"id": "accuracy", "label": "정확도 검증", "group": "monthly", "needs": [], "ai": True, "manual": True,
     "desc": "정답 76건으로 규칙 / AI 예시 없이 / AI 예시 포함 채점 (월간 묶음에 자동으로 들어가지 않음)"},
    {"id": "monthly", "label": "요약 · 전략", "group": "monthly", "needs": ["OPENAI_API_KEY"],
     "desc": "이달의 요약 3줄 · 고객 인사이트 · 페르소나 · SWOT"},
]
BY_ID = {i["id"]: i for i in ITEMS}
ORDER = ["weee", "amazon", "yami", "channel_reviews", "trends", "youtube", "reviews", "voc", "competitors", "actions", "monthly",
         "accuracy"]
LIMITS = {"serpapi": 250, "firecrawl": 1000, "youtube": 10000}
REVIEW_BATCH, REVIEW_MAX = 40, 200
COMMENT_BATCH, COMMENT_MAX = 80, 400


def _pending_reviews():
    return Review.query.filter(Review.classified_by != "ai").count()


def _pending_comments(month):
    return Comment.query.filter(Comment.month == month, Comment.classified_by != "ai").count() if month else 0


def estimate(item_id, cfg):
    """한 번 돌릴 때 대략 (README 비용 표 기준)."""
    ai = envkeys.has("OPENAI_API_KEY")
    sets = sum(len(v) for v in (cfg.get("search_sets") or {}).values())
    per = int(cfg.get("videos_per_query") or 0)
    if item_id == "weee":  # 진열대 페이지 + 지난번 상품 수만큼 리뷰 페이지 (리뷰가 있으면 JSON 추출 더)
        last = db.session.query(db.func.max(ShelfItem.week)).filter(ShelfItem.source == "weee").scalar()
        n = ShelfItem.query.filter_by(source="weee", week=last).count() if last else 0
        return {"firecrawl": len(cfg.get("weee_urls") or []) + n}
    if item_id == "yami":
        return {"firecrawl": len(yami.queries())}
    if item_id == "amazon":
        return {"serpapi": len(cfg.get("amazon_queries") or []) + int(cfg.get("amazon_product_lookups") or 0)
                + len(cfg.get("tracked_asins") or [])}
    if item_id == "channel_reviews":  # Olive Young은 첫 페이지 + 더 보기 = 상품마다 약 2
        src = channel_reviews.load_sources()
        cost = {"firecrawl": sum(2 if s["channel"] == "oliveyoung" else 1 for s in src if s["channel"] != "amazon")}
        if envkeys.has("SERPAPI_KEY"):
            cost["serpapi"] = sum(1 for s in src if s["channel"] == "amazon")
        return {k: v for k, v in cost.items() if v}
    if item_id == "trends":
        return {"serpapi": len(cfg.get("trend_keywords") or [])}
    if item_id == "youtube":
        return {"youtube": sets * 100 + math.ceil(sets * per / 50) + sets * per}
    if item_id == "reviews":
        return {"openai": math.ceil(min(_pending_reviews(), REVIEW_MAX) / REVIEW_BATCH) if ai else 0}
    if item_id == "voc":
        return {"openai": math.ceil(min(_pending_comments(service.latest_comment_month()), COMMENT_MAX) / COMMENT_BATCH) if ai else 0}
    if item_id == "competitors":
        return _competitor_cost(ai)
    if item_id in ("actions", "monthly"):
        return {"openai": 1 if ai else 0}
    if item_id == "accuracy":
        return {"openai": accuracy.calls_needed(ai)}
    return {}


def _competitor_cost(ai, force=False, only=None):
    if not ai:
        return {}
    now, _, _ = service.snapshots()
    if only is not None:
        n, site = 1, 1
    else:
        targets = [c for c in competitors.deep_targets(now) if competitors.stale(c, force)]
        n, site = len(targets), sum(1 for c in targets if not c.website)
    fc = n * 2 + site if envkeys.has("FIRECRAWL_API_KEY") else 0  # 홈페이지 1장 + 뉴스 검색 1회 (+ 처음이면 홈페이지 찾기)
    return {k: v for k, v in {"openai": n * 2 + 1, "firecrawl": fc}.items() if v}  # 분석 + 못 채운 칸 웹 검색


def month_usage():
    """이 대시보드가 이번 달(YouTube는 오늘) 쓴 양 — 실행 기록 기준 (대략)."""
    now = utcnow()
    first = datetime(now.year, now.month, 1)
    day = now - timedelta(hours=24)
    out = {"serpapi": 0, "firecrawl": 0, "youtube": 0, "openai": 0}
    for r in Run.query.filter(Run.started_at >= min(first, day)).all():
        u = load_json(r.usage, {})
        for k in ("serpapi", "firecrawl", "openai"):
            if r.started_at >= first:
                out[k] += int(u.get(k) or 0)
        if r.started_at >= day:
            out["youtube"] += int(u.get("youtube") or 0)
    return out


def plan():
    cfg = get_collect_config()
    keys = {k: envkeys.has(k) for k in ("FIRECRAWL_API_KEY", "SERPAPI_KEY", "YOUTUBE_API_KEY", "OPENAI_API_KEY")}
    items = []
    for it in ITEMS:
        missing = [k for k in it["needs"] if not keys.get(k)]
        note, ready = "", not missing
        if it.get("ai") and not keys["OPENAI_API_KEY"]:
            note = "AI 키가 없어 규칙으로만 처리해요 (무료)."
        if it["id"] == "weee" and not cfg.get("weee_urls"):
            ready, note = False, "설정 › 수집 설정에 Weee! 주소가 없어요."
        if it["id"] == "channel_reviews" and not channel_reviews.load_sources():
            ready, note = False, "리뷰 상품 주소가 없어요 (data/review_sources.json)."
        items.append(dict(it, ready=ready, missing=missing, cost=estimate(it["id"], cfg), note=note))
    return {"items": items, "keys": keys, "usage": month_usage(), "limits": LIMITS, "running": jobs.is_running(JOB),
            "lastRun": service.last_run(), "reviewMonth": review_month(service.today()), "week": week_key(service.today())}


def start(items, user, options=None):
    """options: 경쟁사 분석용 {force: 상위 15개 모두 다시 조사, only: 경쟁사 id 한 곳}."""
    items = [i for i in ORDER if i in set(items)]
    if not items:
        raise ValueError("업데이트할 항목을 하나 이상 고르세요.")
    # 잠금 파일은 뒤 작업이 시작돼야 생겨서, 막 시작한 실행 기록(2분 안)도 함께 확인 (두 번 누름 방지)
    fresh = Run.query.filter(Run.status == "running", Run.started_at >= utcnow() - timedelta(minutes=2)).first()
    if jobs.is_running(JOB) or fresh is not None:
        raise RuntimeError("이미 데이터 업데이트가 진행 중이에요. 끝난 뒤 다시 시도하세요.")
    p = {x["id"]: x for x in plan()["items"]}
    blocked = [p[i]["label"] for i in items if not p[i]["ready"]]
    if blocked:
        raise ValueError(f"키 · 설정이 없어 실행할 수 없는 항목이 있어요: {', '.join(blocked)} (설정 › 연결 · 수집 설정).")
    run = Run(items=json.dumps(items, ensure_ascii=False), user_name=(user or "")[:100], status="running")
    db.session.add(run)
    db.session.commit()
    if jobs.run_background(current_app._get_current_object(), JOB, run_update, run.id, items, user, options or {}) is None:
        # 확인과 시작 사이에 다른 요청이 먼저 잠금을 잡은 경우 → 이 실행 기록은 닫음
        run.status, run.finished_at, run.current = "failed", utcnow(), ""
        db.session.commit()
        raise RuntimeError("이미 데이터 업데이트가 진행 중이에요. 끝난 뒤 다시 시도하세요.")
    return run


# ───────── 항목별 실행 ─────────

def _collect_log(label, res, user, failed=False, collector=True):
    # README 작업 로그: '수집' = 데이터 업데이트 · 월간 분석 실행 (분류 · 요약 단계는 '실행'으로 적음)
    worklog.add("insights", "수집", f"{label} {'수집' if collector else '실행'}", status="실패" if failed else "완료",
                user_name=user or "시스템",
                detail={k: v for k, v in res.items() if k in ("count", "calls", "reviews", "comments", "found", "units",
                                                               "warnings", "message")})


def _run_weee(cfg, ctx):
    r = weee.collect(cfg, envkeys.get("FIRECRAWL_API_KEY"), ctx["week"])
    r["usage"] = {"firecrawl": r["credits"]}
    r["message"] = f"Weee! 상품 {r['count']}개 · 리뷰 {r['reviews']}개(새 리뷰 {r['newReviews']}개)를 모았어요 (페이지 {r['calls']}개)."
    return r


def _run_yami(cfg, ctx):
    r = yami.collect(cfg, envkeys.get("FIRECRAWL_API_KEY"), ctx["week"])
    r["usage"] = {"firecrawl": r["calls"]}
    r["message"] = f"Yami 상품 {r['count']}개를 모았어요 (검색 {r['calls']}회)."
    return r


def _run_amazon(cfg, ctx):
    r = amazon.collect(cfg, envkeys.get("SERPAPI_KEY"), ctx["week"])
    r["usage"] = {"serpapi": r["calls"]}
    r["message"] = f"Amazon 상품 {r['count']}개 · 새 리뷰 {r['reviews']}개를 모았어요 (SerpApi {r['calls']}회)."
    return r


def _run_channel_reviews(cfg, ctx):
    serp = envkeys.get("SERPAPI_KEY") if envkeys.has("SERPAPI_KEY") else None
    r = channel_reviews.collect(envkeys.get("FIRECRAWL_API_KEY"), ctx["week"], serp)
    r["usage"] = {"firecrawl": r["credits"], "serpapi": r["serpapi"]}
    r["message"] = (f"채널 리뷰 {r['count']}개를 읽고 새 리뷰 {r['reviews']}개를 넣었어요 "
                    f"(Firecrawl 약 {r['credits']} 크레딧 · SerpApi {r['serpapi']}회).")
    return r


def _run_trends(cfg, ctx):
    r = trends.collect(cfg, envkeys.get("SERPAPI_KEY"), ctx["week"])
    r["usage"] = {"serpapi": r["calls"]}
    r["message"] = f"키워드 {r['count']}개의 12개월 검색 추이를 모았어요 (SerpApi {r['calls']}회)."
    return r


def _run_youtube(cfg, ctx):
    r = youtube.collect(cfg, envkeys.get("YOUTUBE_API_KEY"), ctx["month"])
    r["usage"] = {"youtube": r["units"]}
    r["message"] = (f"영상 {r['count']}개 (검색 {r['found']}개 중 조회수 {int(cfg.get('min_views') or 0):,} 이상) · "
                    f"댓글 {r['comments']}개를 모았어요 ({r['units']:,} units).")
    return r


def _run_reviews(cfg, ctx):
    from . import classify
    rows = Review.query.filter(Review.classified_by == "").all()
    for r in rows:  # 규칙 분류가 빠진 리뷰 (예전 데이터 등)
        r.topics = json.dumps(classify.review_topics(f"{r.title} {r.text}"), ensure_ascii=False)
        r.sentiment, r.classified_by = classify.review_sentiment(r.text, r.rating), "rule"
    db.session.commit()
    if not insights_ai.ready():
        total = Review.query.count()
        return {"ok": True, "count": total, "message": f"리뷰 {total}개를 규칙으로 분류했어요 (AI 키 없음).", "usage": {}}
    todo = Review.query.filter(Review.classified_by != "ai").order_by(Review.collected_at.desc()).limit(REVIEW_MAX).all()
    calls, done = 0, 0
    for i in range(0, len(todo), REVIEW_BATCH):
        batch = todo[i:i + REVIEW_BATCH]
        out = insights_ai.classify_reviews([{"id": str(r.id), "rating": r.rating, "title": r.title, "text": r.text[:800]}
                                            for r in batch])
        calls += 1
        for r in batch:
            o = out.get(str(r.id))
            if not o:
                continue
            r.topics = json.dumps([t for t in o["topics"] if t in classify.TOPICS], ensure_ascii=False)
            r.sentiment, r.summary_ko, r.classified_by = o["sentiment"], o["summary_ko"][:500], "ai"
            done += 1
        db.session.commit()
    return {"ok": True, "count": done, "message": f"리뷰 {done}개를 AI로 분류했어요 (OpenAI {calls}회).", "usage": {"openai": calls}}


def _run_voc(cfg, ctx):
    month = service.latest_comment_month()
    if not month:
        return {"ok": True, "count": 0, "message": "분류할 유튜브 댓글이 없어요 (먼저 유튜브 수집).", "usage": {}}
    if not insights_ai.ready():
        n = Comment.query.filter_by(month=month).count()
        return {"ok": True, "count": n, "message": f"댓글 {n}개는 수집 때 규칙으로 분류했어요 (AI 키 없음).", "usage": {}}
    todo = (Comment.query.filter(Comment.month == month, Comment.classified_by != "ai")
            .order_by(Comment.likes.desc()).limit(COMMENT_MAX).all())
    calls, done = 0, 0
    for i in range(0, len(todo), COMMENT_BATCH):
        batch = todo[i:i + COMMENT_BATCH]
        out = insights_ai.classify_comments([{"id": c.id, "text": c.text[:500]} for c in batch])
        calls += 1
        for c in batch:
            o = out.get(c.id)
            if not o:
                continue
            c.relevant = bool(o["relevant"])
            c.is_question = bool(o["question"]) and c.relevant and o["topic"] != "none"
            c.topic = o["topic"] if c.is_question else ""
            c.classified_by = "ai"
            done += 1
        db.session.commit()
    return {"ok": True, "count": done, "message": f"댓글 {done}개를 AI로 분류했어요 (OpenAI {calls}회).", "usage": {"openai": calls}}


def _run_competitors(cfg, ctx):
    now, _, _ = service.snapshots()
    if not now:
        return {"ok": True, "count": 0, "message": "진열대 데이터가 없어 경쟁사를 찾지 못했어요 (먼저 Amazon · Weee! 수집).", "usage": {}}
    opt = ctx.get("options") or {}
    return competitors.run(now, service._tier, force=bool(opt.get("force")), only=opt.get("only"))


def _run_actions(cfg, ctx):
    context, _ = service.weekly_context(ctx["today"])
    acts = rules.weekly_actions(context)
    worklog.add("insights", "승인 · 선정", "이번 주 액션 선정 (규칙)", approval="자동", user_name=ctx["user"] or "시스템",
                detail={"actions": [{"grade": a["grade"], "rule": a["ruleLabel"]} for a in acts]})
    if not acts:
        return {"ok": True, "count": 0, "message": "규칙에 걸린 신호가 없어요 (특이사항 없음).", "usage": {}}
    if not insights_ai.ready():
        return {"ok": True, "count": len(acts), "message": f"이번 주 액션 {len(acts)}개 (AI 키가 없어 규칙 문장 그대로예요).",
                "usage": {}}
    _, cached = insights_ai.rewrite_actions(acts)
    return {"ok": True, "count": len(acts), "usage": {} if cached else {"openai": 1},
            "message": f"이번 주 액션 {len(acts)}개 · AI가 문장을 다듬었어요{' (저장된 문장 사용)' if cached else ''}."}


def _run_monthly(cfg, ctx):
    month = ctx["month"]
    payload = service.monthly_payload(month, ctx["today"])
    out = insights_ai.monthly_analysis(payload)
    out["sources"] = service.monthly_sources(payload)   # 화면 'AI가 읽은 자료' (실행 때 실제로 넘긴 개수)
    row = db.session.get(Monthly, month) or Monthly(month=month)
    row.data = json.dumps(out, ensure_ascii=False)
    row.model, row.created_at, row.created_by = insights_ai.openai_model(), utcnow(), (ctx["user"] or "")[:100]
    db.session.add(row)
    db.session.commit()
    return {"ok": True, "count": 1, "message": f"{month} 월간 요약 · 전략을 만들었어요.", "usage": {"openai": 1}}


def _run_accuracy(cfg, ctx):
    r, calls = accuracy.run(ctx["user"])
    m = r["methods"]
    name = dict(accuracy.METHODS)
    text = " · ".join(f"{name[k]} {v['exact']}%" for k, v in m.items())
    note = "" if "few" in m else " AI 키가 없어 규칙만 채점했어요."
    return {"ok": True, "count": r["tests"], "message": f"정답 {r['tests']}건 채점: {text}.{note}", "usage": {"openai": calls}}


RUNNERS = {"weee": _run_weee, "amazon": _run_amazon, "yami": _run_yami, "channel_reviews": _run_channel_reviews, "trends": _run_trends, "youtube": _run_youtube, "reviews": _run_reviews,
           "voc": _run_voc, "competitors": _run_competitors, "actions": _run_actions, "monthly": _run_monthly,
           "accuracy": _run_accuracy}
COLLECTORS = {"weee", "amazon", "yami", "channel_reviews", "trends", "youtube"}


def run_update(run_id, items, user, options=None):
    run = db.session.get(Run, run_id)
    if run is None:
        return
    t = service.today()
    cfg = get_collect_config()
    # 월간 묶음: 1~7일에는 지난달 (월간 리뷰가 보는 달과 같게 — 유튜브 수집 · 요약이 같은 달을 봄)
    ctx = {"today": t, "week": week_key(t), "month": review_month(t), "user": user, "options": options or {}}
    results, usage = {}, {"serpapi": 0, "firecrawl": 0, "youtube": 0, "openai": 0}
    for item in items:
        label = BY_ID[item]["label"]
        run.current = item
        db.session.commit()
        try:
            res = RUNNERS[item](cfg, ctx)
            res["ok"] = True
        except (CollectError, AIError) as e:
            db.session.rollback()
            res = {"ok": False, "message": str(e)}
        except Exception as e:  # 예상 못 한 오류 — 원문 대신 이름만
            db.session.rollback()
            current_app.logger.exception("인사이트 업데이트 %s 실패", item)
            res = {"ok": False, "message": f"처리 중 문제가 생겼어요 ({type(e).__name__})."}
        for k, v in (res.get("usage") or {}).items():
            usage[k] = usage.get(k, 0) + int(v or 0)
        if res.get("warnings"):
            res["message"] = res.get("message", "") + " 참고: " + " / ".join(res["warnings"][:3])
        if item in COLLECTORS or item == "competitors" or not res["ok"]:
            _collect_log(label, res, user, failed=not res["ok"], collector=item in COLLECTORS)
        run = db.session.get(Run, run_id)
        results[item] = {"ok": res["ok"], "message": res.get("message", ""), "count": res.get("count"),
                         "at": iso(utcnow())}
        run.results = json.dumps(results, ensure_ascii=False)
        run.usage = json.dumps(usage)
        db.session.commit()
    ok = [r["ok"] for r in results.values()]
    run.status = "done" if all(ok) else "partial" if any(ok) else "failed"
    run.current, run.finished_at = "", utcnow()
    db.session.commit()


def status():
    r = Run.query.order_by(Run.id.desc()).first()
    if r is None:
        return {"run": None, "running": False}
    running = jobs.is_running(JOB)
    age = utcnow() - r.started_at
    # 서버가 중간에 꺼진 경우: 잠금이 없거나, 잠금이 남아 있어도 30분 넘게 '진행 중'이면 멈춘 것으로 봄
    if r.status == "running" and ((not running and age > timedelta(minutes=2)) or age > timedelta(minutes=30)):
        running = False
        r.status, r.finished_at, r.current = "failed", utcnow(), ""
        results = load_json(r.results, {})
        results.setdefault("_", {"ok": False, "message": "업데이트가 중간에 멈췄어요. 다시 실행하세요."})
        r.results = json.dumps(results, ensure_ascii=False)
        db.session.commit()
    return {"run": service.run_dict(r), "running": running or r.status == "running"}
