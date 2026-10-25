"""화면용 데이터 묶기 — 주간 리뷰 · 월간 리뷰 · 데이터 탭 · 다른 파트 공개(searchTrend · personas).

모두 표에 쌓인 실제 수집 결과로만 계산합니다. 없으면 빈 값과 안내 문장(데이터 업데이트 · 키 필요)을 돌려줍니다.
"""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from server import envkeys
from server.catalog import service as catalog
from server.extensions import db
from server.models import iso, utcnow
from tabs.settings.config import get_collect_config
from . import ai as insights_ai
from . import channel_reviews, competitors, lines, rules, slack
from .classify import QUESTION_LABEL, TOPICS, expressions
from .models import (Comment, Competitor, Monthly, Review, ReviewCS, Run, ServingOverride, ShelfItem, TrendSeries, Video, kv_get,
                     load_json)
from .util import (BUCKET_LABEL, bucket, month_key, month_range, per_serving, prev_month, review_month, week_key, week_label,
                   week_range)

SOURCES = ("amazon", "weee", "yami")
SOURCE_LABEL = {"amazon": "Amazon", "weee": "Weee!", "yami": "Yami"}
REVIEW_SOURCE_LABEL = {"amazon": "Amazon", "oliveyoung": "Olive Young", "yami": "Yami", "weee": "Weee!"}
CS_STATUS = {"new": "미확인", "todo": "대응 필요", "done": "대응 완료", "skip": "대응 안 함"}
# 불만이 아니어도 마케터가 봐야 하는 말 (배송 · 파손 · 환불 등)
CS_WORDS = re.compile(r"leak|broken|damag|crush|missing|refund|expired|never arrived|arrived late|late delivery|배송|파손|누수|환불|유통기한",
                      re.I)
GENERIC = {"korean", "hangover", "premium", "organic", "natural", "the", "new", "best", "original", "extra", "advanced",
           "ultimate", "super", "pure", "daily", "fast", "hangover-relief", "anti-hangover", "1", "2"}
TIER_WORDS = {"direct": ("jelly", "stick", "korean", "korea", "젤리", "스틱"),
              "category": ("hangover", "숙취", "morning after", "after party", "recovery", "liver")}
TIER_LABEL = {"direct": "직접 경쟁", "category": "카테고리 경쟁", "substitute": "대체재"}


def today():
    return catalog._today()


# ───────── 진열대 ─────────

def guess_brand(title):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&.+-]*", title or "")
    if not words:
        parts = str(title or "").split()
        return parts[0][:40] if parts else ""
    if words[0].lower() in GENERIC and len(words) > 1:
        return " ".join(words[:2])[:60]
    return words[0][:60]


def _watch_set():
    return {str(b).lower() for b in kv_get("watch", [])}


def shelf_dict(r, ovr=None, own_ovr=None, watch=None):
    ovr = ovr if ovr is not None else {o.key: o.servings for o in ServingOverride.query.all()}
    own_ovr = own_ovr if own_ovr is not None else kv_get("own_overrides", {})
    watch = watch if watch is not None else _watch_set()
    if r.key in ovr:
        servings, src = ovr[r.key], "manual"
    elif r.servings:
        servings, src = r.servings, "parsed"
    else:
        servings, src = 1, "default"
    ps = per_serving(r.price, servings)
    known = lines.known_brand(r.brand, r.title)  # 별칭 사전 (Q.one · Easy Tomorrow → 상쾌환)
    brand = known["name"] if known else (r.brand or guess_brand(r.title))
    return {
        "id": r.id, "week": r.week, "source": r.source, "sourceLabel": SOURCE_LABEL.get(r.source, r.source), "key": r.key,
        "position": r.position, "queries": r.queries, "title": r.title, "brand": brand, "brandGuessed": not (known or r.brand),
        "price": r.price, "priceText": r.price_text, "servings": servings, "servingsSource": src, "servingsRule": r.servings_rule,
        "perServing": ps, "bucket": bucket(ps), "rating": r.rating, "reviews": r.reviews, "availability": r.availability,
        "in_stock": r.in_stock, "link": r.link, "message": r.message, "own": bool(own_ovr.get(r.key, r.own)),
        "tracked": bool(r.tracked), "detailed": bool(r.detailed), "watched": brand.lower() in watch,
        "editedBy": r.edited_by, "collectedAt": iso(r.collected_at),
    }


def _weeks(source, upto=None):
    q = db.session.query(ShelfItem.week).filter(ShelfItem.source == source)
    if upto:
        q = q.filter(ShelfItem.week <= upto)
    return [w for (w,) in q.distinct().order_by(ShelfItem.week.desc()).all()]


def shelf_maps():
    """shelf_dict에 넘길 값 (회분 수정 · 자사 표시 · 주시 브랜드) — 여러 줄을 바꿀 때 한 번만 읽기."""
    return {o.key: o.servings for o in ServingOverride.query.all()}, kv_get("own_overrides", {}), _watch_set()


def shelf_rows(week, source):
    ovr, own_ovr, watch = shelf_maps()
    rows = ShelfItem.query.filter_by(week=week, source=source).order_by(ShelfItem.position.is_(None), ShelfItem.position,
                                                                        ShelfItem.id).all()
    return lines.annotate([shelf_dict(r, ovr, own_ovr, watch) for r in rows])


def snapshots(upto=None):
    """판매처마다 가장 최근 묶음(이번)과 그 전 묶음(지난) → (이번 rows, 지난 rows 또는 None, 정보)."""
    now, prev, info = [], [], {}
    for src in SOURCES:
        weeks = _weeks(src, upto)
        if not weeks:
            continue
        now += shelf_rows(weeks[0], src)
        info[src] = {"week": weeks[0], "prev": weeks[1] if len(weeks) > 1 else None}
        if len(weeks) > 1:
            prev += shelf_rows(weeks[1], src)
    has_prev = any(i["prev"] for i in info.values())
    return now, (prev if has_prev else None), info


def _changes(now, prev):
    before = {(x["source"], x["key"]): x for x in prev or []}
    compare = {x["source"] for x in prev or []}
    out = {}
    for x in now:
        if x["source"] not in compare:
            continue
        p = before.get((x["source"], x["key"]))
        if p is None:
            out[x["id"]] = {"new": True}
        elif p.get("price") and x.get("price"):
            pct = round((x["price"] - p["price"]) / p["price"] * 100, 1)
            out[x["id"]] = {"new": False, "pricePct": pct, "before": p["price"], "wasInStock": p.get("in_stock")}
    return out


def shelf_view():
    now, prev, info = snapshots()
    changes = _changes(now, prev)
    for x in now:
        x["change"] = changes.get(x["id"])
    groups = {k: [] for k, _ in (("low", 0), ("mid", 0), ("high", 0))}
    unknown = []
    for x in sorted(now, key=lambda r: (r["perServing"] is None, r["perServing"] or 0)):
        (groups[x["bucket"]] if x["bucket"] else unknown).append(x)
    own = [x for x in now if x["own"]]
    oos = [x for x in now if x["in_stock"] is False]
    if now:
        parts = [f"{BUCKET_LABEL[k]} {len(v)}개" for k, v in groups.items()]
        one = f"진열대 상품 {len(now)}개 · 1회분 단가 " + " / ".join(parts)
        if own:
            ps = [x["perServing"] for x in own if x["perServing"] is not None]
            one += f" · 자사 {len(own)}개" + (f" (1회분 {'$%.2f' % min(ps)}~)" if ps else "")
        else:
            one += " · 자사 상품은 아직 진열대에서 찾지 못했어요"
        if oos:
            one += f" · 품절 {len(oos)}개"
    else:
        one = ""
    raw = kv_get("weee_raw", {})
    return {
        "oneLine": one, "items": now, "groups": groups, "unknown": unknown, "info": info, "lines": lines.build(now),
        "lowStock": lines.LOW_STOCK, "demo": demo_info(), "forms": competitors.FORMS,
        "weekLabels": {src: week_label(i["week"]) for src, i in info.items()},
        "compare": bool(prev), "weeeRaw": [{"url": u, **v} for u, v in raw.items()],
        "bucketLabels": BUCKET_LABEL,
    }


# ───────── 리뷰 · 댓글 묶음 ─────────

def own_complaints(limit=5):
    rows = Review.query.filter_by(own=True, sentiment="-").order_by(Review.collected_at.desc()).limit(500).all()
    counts, examples = Counter(), defaultdict(list)
    for r in rows:
        for t in r.topic_list() or ["기타"]:
            counts[t] += 1
            if len(examples[t]) < 3:
                examples[t].append(r.summary_ko or r.text[:200])
    return [{"topic": t, "count": c, "examples": examples[t]} for t, c in counts.most_common(limit)]


def latest_comment_month():
    row = db.session.query(Comment.month).order_by(Comment.month.desc()).first()
    return row[0] if row else None


def top_questions(month=None, limit=5):
    month = month or latest_comment_month()
    if not month:
        return []
    rows = Comment.query.filter_by(month=month, is_question=True, relevant=True).order_by(Comment.likes.desc()).all()
    counts, examples = Counter(), defaultdict(list)
    for c in rows:
        k = c.topic
        if k not in QUESTION_LABEL:  # 주제 없음 · 예전 '기타'는 집계하지 않음
            continue
        counts[k] += 1
        if len(examples[k]) < 3:
            examples[k].append(c.text[:300])
    return [{"key": k, "label": QUESTION_LABEL.get(k, k), "count": n, "examples": examples[k]}
            for k, n in counts.most_common(limit)]


# ───────── 검색 트렌드 ─────────

def latest_trends():
    """키워드마다 가장 최근 묶음 → ({키워드: 점}, {키워드: 정보}). 설정의 키워드 순서 먼저."""
    rows = TrendSeries.query.order_by(TrendSeries.collected_at.desc()).all()
    series, meta = {}, {}
    for r in rows:
        if r.keyword in series:
            continue
        pts = r.point_list()
        if pts:
            series[r.keyword] = pts
            meta[r.keyword] = {"source": r.source, "week": r.week, "collectedAt": iso(r.collected_at)}
    order = [k for k in get_collect_config().get("trend_keywords", []) if k in series] + [k for k in series]
    seen = []
    for k in order:
        if k not in seen:
            seen.append(k)
    return {k: series[k] for k in seen}, meta


def _season_match(d, seasons):
    """급등한 날 d 근처(±7일)에 해마다 오는 시즌이 있으면 이름 (작년 날짜로 옮겨 비교 · 추정)."""
    for s in seasons:
        start = date.fromisoformat(s["start"])
        for y in (d.year - 1, d.year, d.year + 1):
            try:
                cand = start.replace(year=y)
            except ValueError:
                continue
            if abs((cand - d).days) <= 7:
                return s["name"]
    return None


def trends_view(t=None):
    t = t or today()
    series, meta = latest_trends()
    all_seasons = catalog.list_seasons(include_past=True, today=t)
    upcoming = [s for s in all_seasons if s.get("monitor") and s["dday"] >= 0]
    nxt = []
    for s in upcoming[:8]:
        best = None
        for kw, pts in series.items():
            r = rules.season_lift(pts, s["start"])
            if r and (best is None or r["pct"] > best["pct"]):
                best = dict(r, keyword=kw)
        nxt.append({"name": s["name"], "start": s["start"], "dday": s["dday"], "lift": best,
                    "opportunity": bool(best and best["pct"] >= rules.SEASON_LIFT and s["dday"] <= rules.SEASON_DAYS)})
    spikes = []
    for kw, pts in series.items():
        vals = [p["value"] for p in pts]
        if len(vals) < 5:
            continue
        avg = sum(vals) / len(vals)
        if avg <= 0:
            continue
        peaks = []
        for i, p in enumerate(pts):
            left = pts[i - 1]["value"] if i else -1
            right = pts[i + 1]["value"] if i + 1 < len(pts) else -1
            if p["value"] >= avg * 1.3 and p["value"] >= left and p["value"] >= right:
                peaks.append(p)
        for p in sorted(peaks, key=lambda x: -x["value"])[:3]:
            d = date.fromisoformat(p["date"])
            season = _season_match(d, all_seasons)
            spikes.append({"keyword": kw, "date": p["date"], "value": p["value"], "pct": round((p["value"] - avg) / avg * 100, 1),
                           "reason": f"{season} 무렵 (추정)" if season else "이유 미확인"})
    spikes.sort(key=lambda x: -x["pct"])
    updated = max((m["collectedAt"] for m in meta.values()), default=None)
    one = ""
    if series:
        top = next((n for n in nxt if n["lift"]), None)
        one = f"키워드 {len(series)}개의 지난 12개월 검색 추이"
        if top:
            one += f" · 다음 시즌 {top['name']}(D-{top['dday']}) — 작년 같은 시기 '{top['lift']['keyword']}' 평균 대비 {top['lift']['pct']:+.0f}%"
    return {"oneLine": one, "series": [{"keyword": k, "points": v, **meta[k]} for k, v in series.items()], "next": nxt,
            "spikes": spikes[:8], "updatedAt": updated}


def trend_expose():
    series, meta = latest_trends()
    return {"keywords": [{"keyword": k, "points": v} for k, v in series.items()],
            "updatedAt": max((m["collectedAt"] for m in meta.values()), default=None)}


# ───────── 주간 리뷰 ─────────

def weekly_context(t=None):
    t = t or today()
    now, prev, info = snapshots(week_key(t))
    series, _ = latest_trends()
    ctx = {"today": t, "shelf_now": now, "shelf_prev": prev, "seasons": catalog.list_seasons(today=t), "trends": series,
           "questions": top_questions(limit=None), "own_complaints": own_complaints()}
    return ctx, info


def _new_reviews(days=7):
    since = utcnow() - timedelta(days=days)
    rows = Review.query.filter(Review.collected_at >= since).all()
    praise, complaint = Counter(), Counter()
    for r in rows:
        for tp in r.topic_list():
            if r.sentiment == "+":
                praise[tp] += 1
            elif r.sentiment == "-":
                complaint[tp] += 1
    return {"count": len(rows), "own": sum(1 for r in rows if r.own), "other": sum(1 for r in rows if not r.own),
            "praise": [{"topic": k, "count": v} for k, v in praise.most_common(3)],
            "complaint": [{"topic": k, "count": v} for k, v in complaint.most_common(3)]}


def last_run():
    r = Run.query.order_by(Run.id.desc()).first()
    return run_dict(r) if r else None


def last_collect(items=SOURCES, scan=30):
    """판매처마다 가장 최근 실행의 결과 {ok, count, message, at} (마지막 실행에 없던 항목도 찾음)."""
    out = {}
    for r in Run.query.order_by(Run.id.desc()).limit(scan).all():
        res = load_json(r.results, {})
        for k in items:
            if k not in out and isinstance(res.get(k), dict):
                out[k] = {key: res[k].get(key) for key in ("ok", "count", "message", "at")}
        if len(out) == len(items):
            break
    return out


def demo_info():
    """시연 전용 DB 표시 (kv 'demo' = {weeks: [...], note}) — 실제 DB에는 없음."""
    d = kv_get("demo", {})
    return d if d.get("weeks") else None


def run_dict(r):
    return {"id": r.id, "status": r.status, "items": load_json(r.items, []), "results": load_json(r.results, {}),
            "current": r.current, "usage": load_json(r.usage, {}), "startedAt": iso(r.started_at),
            "finishedAt": iso(r.finished_at), "user": r.user_name}


def comp_changes(now, prev):
    """이번 주 경쟁사 변화 — 경쟁사 탭 · 주간 리뷰 요약이 같은 숫자를 쓰도록 여기서만 계산 → (변화 목록 또는 None, 예시 여부)."""
    changes = competitors.weekly_changes(now, prev)
    if changes is None and any(not x["own"] for x in now):   # 지난주 수집이 없으면 시연용 예시 지난주와 비교 (화면에 '예시' 배지)
        return competitors.weekly_changes(now, competitors.demo_prev(now)), True
    return changes, False


def weekly_view(t=None):
    t = t or today()
    ctx, info = weekly_context(t)
    actions = rules.weekly_actions(ctx)
    ai_items, ai_row = insights_ai.cached_rewrite(actions)
    for a in actions:
        a["base"] = {"fact": a["fact"], "why": a["why"], "todo": a["todo"]}
        w = ai_items.get(a["id"])
        if w:
            a.update(fact=w["fact"], why=w["why"], todo=w["todo"], ai=True)
        a.pop("weight", None)
    now, prev = ctx["shelf_now"], ctx["shelf_prev"]
    chg, chg_demo = comp_changes(now, prev)
    weee = sorted([x for x in now if x["source"] == "weee" and x["perServing"] is not None], key=lambda x: x["perServing"])
    own_pos = None
    for i, x in enumerate(weee, start=1):
        if x["own"]:
            own_pos = {"title": x["title"], "perServing": x["perServing"], "bucket": x["bucket"], "rank": i, "total": len(weee),
                       "bucketLabel": BUCKET_LABEL.get(x["bucket"], "")}
            break
    seasons = []
    series = ctx["trends"]
    for s in [s for s in ctx["seasons"] if s.get("monitor") and s["dday"] >= 0][:4]:
        best = None
        for kw, pts in series.items():
            r = rules.season_lift(pts, s["start"])
            if r and (best is None or r["pct"] > best["pct"]):
                best = dict(r, keyword=kw)
        seasons.append({"name": s["name"], "start": s["start"], "end": s["end"], "dday": s["dday"], "inLead": s["inLead"],
                        "ongoing": s["ongoing"], "lastYear": best})
    has_data = bool(now or series or db.session.query(Review.id).first() or db.session.query(Comment.id).first())
    stale = None
    if now and all(i["week"] != week_key(t) for i in info.values()):
        latest = max(i["week"] for i in info.values())
        stale = {"week": latest, "label": week_label(latest)}
    comp = [x for x in now if not x["own"]]
    line_list = lines.build(comp)
    before = {(x["source"], x["key"]): x for x in prev or []}
    restocks = [x for x in comp if before.get((x["source"], x["key"]), {}).get("in_stock") is False and x["in_stock"] is True]
    watch_lines = [{"id": g["id"], "label": g["label"], "oosChannels": [SOURCE_LABEL[s] for s in g["oosChannels"]], "partialOos": g["partialOos"],
                    "low": [{"source": SOURCE_LABEL[v["source"]], "left": v["left"], "servings": v["servings"]} for v in g["low"][:2]],
                    "channels": [SOURCE_LABEL[s] for s in g["channels"]], "perServing": g["perServing"], "confirmed": g["confirmed"]}
                   for g in line_list if g["oosChannels"] or g["low"] or g["partialOos"]][:4]
    return {
        "week": week_key(t), "weekLabel": week_label(week_key(t)), "today": t.isoformat(), "hasData": has_data, "stale": stale,
        "actions": actions, "aiUsed": any(a["ai"] for a in actions), "aiReady": insights_ai.ready(),
        "aiAt": iso(ai_row.created_at) if ai_row else None,
        "shelf": {"total": len(now), "oos": sum(1 for x in now if x["in_stock"] is False and not x["own"]),
                  "ownOos": sum(1 for x in now if x["in_stock"] is False and x["own"]), "compare": prev is not None,
                  "compChanges": None if chg is None else {
                      "total": len(chg), "demo": chg_demo, "price": sum(1 for c in chg if c["kind"] == "price"),
                      "new": sum(1 for c in chg if c["kind"] == "new"),
                      **{g: sum(1 for c in chg if c["group"] == g) for g in ("threat", "chance", "watch")}},
                  "weeks": {src: {"now": week_label(i["week"]), "prev": week_label(i["prev"]) if i["prev"] else None}
                            for src, i in info.items()},
                  "ownWeee": own_pos, "ownCount": sum(1 for x in now if x["own"]),
                  "lines": len(line_list), "oosLines": sum(1 for g in line_list if g["oosChannels"]),
                  "lowStock": sum(len(g["low"]) for g in line_list), "lowLimit": lines.LOW_STOCK, "restocks": len(restocks),
                  "unknownStock": sum(1 for x in comp if x["in_stock"] is None), "watchLines": watch_lines,
                  "unconfirmed": sum(1 for g in line_list if not g["confirmed"]),
                  "lastCollect": last_collect()},
        "reviews": _new_reviews(), "seasons": seasons, "lastRun": last_run(), "demo": demo_info(),
    }


# ───────── 월간 리뷰 ─────────

def _weeks_in_month(source, month):
    first, last = month_range(month)
    return sorted(w for w in _weeks(source) if first <= week_range(w)[0] <= last)


def monthly_metrics(month):
    first, last = month_range(month)
    own_oos_weeks, price_changes, compared = set(), 0, False
    for src in SOURCES:
        weeks_all = sorted(_weeks(src))
        weeks = _weeks_in_month(src, month)
        for w in weeks:
            rows = shelf_rows(w, src)
            if any(x["own"] and x["in_stock"] is False for x in rows):
                own_oos_weeks.add(w)
            i = weeks_all.index(w)
            if i > 0:
                compared = True
                prev = shelf_rows(weeks_all[i - 1], src)
                ch = _changes(rows, prev)
                price_changes += sum(1 for x in rows if not x["own"] and (ch.get(x["id"]) or {}).get("pricePct") is not None
                                     and abs(ch[x["id"]]["pricePct"]) >= 10)
    start = datetime(first.year, first.month, first.day)
    end = datetime(last.year, last.month, last.day) + timedelta(days=1)
    reviews = Review.query.filter(Review.collected_at >= start, Review.collected_at < end).all()
    series, _ = latest_trends()
    pm = prev_month(month)
    this_vals, prev_vals = [], []
    for pts in series.values():
        a = [p["value"] for p in pts if p["date"][:7] == month]
        b = [p["value"] for p in pts if p["date"][:7] == pm]
        if a and b:
            this_vals.append(sum(a) / len(a))
            prev_vals.append(sum(b) / len(b))
    search = None
    if this_vals:
        cur, before = sum(this_vals) / len(this_vals), sum(prev_vals) / len(prev_vals)
        search = {"value": round(cur, 1), "prev": round(before, 1),
                  "changePct": round((cur - before) / before * 100, 1) if before else None, "keywords": len(this_vals)}
    has_shelf = any(_weeks_in_month(s, month) for s in SOURCES)
    return {
        "ownOosWeeks": len(own_oos_weeks) if has_shelf else None,
        "priceChanges": price_changes if compared else None,
        "newReviews": {"own": sum(1 for r in reviews if r.own), "other": sum(1 for r in reviews if not r.own),
                       "total": len(reviews)},
        "search": search, "hasShelf": has_shelf,
    }


def _monthly_row(month):
    return db.session.get(Monthly, month)


def monthly_view(month=None, t=None):
    t = t or today()
    month = month or review_month(t)
    row = _monthly_row(month)
    data = load_json(row.data, {}) if row else {}
    metrics = monthly_metrics(month)
    q = top_questions(month) if Comment.query.filter_by(month=month).first() else []  # 그 달 댓글만 (다른 달로 채우지 않음)
    rev = Review.query.order_by(Review.collected_at.desc()).limit(1000).all()
    praise, complaint = Counter(), Counter()
    for r in rev:
        for tp in r.topic_list():
            (praise if r.sentiment == "+" else complaint if r.sentiment == "-" else Counter())[tp] += 1
    comments = [c.text for c in Comment.query.filter_by(month=month, relevant=True).all()]
    rule_insights = []
    if q:
        rule_insights.append({"title": f"가장 많은 질문: {q[0]['label']}", "detail": f"유튜브 댓글 질문 {sum(x['count'] for x in q)}건 중 "
                              f"{q[0]['count']}건", "faq": [x["label"] for x in q[:3]], "expressions": []})
    ex = expressions(comments, 3)
    if ex:
        rule_insights.append({"title": f"자주 쓰는 표현: '{ex[0]['phrase']}'", "detail": f"관련 댓글 {len(comments)}개에서 모은 표현",
                              "faq": [], "expressions": [e["phrase"] for e in ex]})
    if praise or complaint:
        p = praise.most_common(1)
        c = complaint.most_common(1)
        rule_insights.append({"title": "리뷰 칭찬 · 불만 1위",
                              "detail": f"칭찬 1위 {p[0][0]} ({p[0][1]}건)" if p else "칭찬 리뷰 없음",
                              "faq": [], "expressions": [f"불만 1위 {c[0][0]} ({c[0][1]}건)"] if c else []})
    comp = competitors_view(limit_text=True)
    watch_first = sorted([x for col in comp["columns"].values() for x in col], key=lambda x: (not x["watched"], -x["score"]))
    rule_comp = [f"{x['brand']}: {x['reason']}" for x in watch_first[:3]]
    prev_row = _monthly_row(prev_month(month))
    return {
        "month": month, "label": f"{int(month[:4])}년 {int(month[5:])}월", "isPrevMonth": month != month_key(t),
        "metrics": metrics, "ai": bool(row), "aiReady": insights_ai.ready(),
        "createdAt": iso(row.created_at) if row else None, "model": row.model if row else "",
        "summary": data.get("summary") or [],
        "insights": data.get("insights") or rule_insights, "insightsAi": bool(data.get("insights")),
        "competitorTrends": data.get("competitor_trends") or rule_comp, "competitorAi": bool(data.get("competitor_trends")),
        "strategy": {k: data.get(k) for k in ("changes", "personas", "swot", "target", "positioning")} if row else None,
        "prevStrategy": bool(prev_row),
        "sources": data.get("sources"),
        "questions": q, "expressions": ex,
    }


def monthly_payload(month, t=None):
    """월간 AI에 넘길 자료 (실제 수집 값만 · 개인 정보 없음)."""
    t = t or today()
    view = monthly_view(month, t)
    shelf = shelf_view()
    rev = Review.query.order_by(Review.collected_at.desc()).limit(60).all()
    prev_row = _monthly_row(prev_month(month))
    prev = load_json(prev_row.data, {}) if prev_row else {}
    return {
        "month": month, "brand": "REBOOT (실제 데이터 기준 기업: 알디콤)", "metrics": view["metrics"],
        "shelf": [{"source": x["sourceLabel"], "title": x["title"][:120], "brand": x["brand"], "price": x["price"],
                   "perServing": x["perServing"], "inStock": x["in_stock"], "own": x["own"], "watched": x["watched"]}
                  for x in shelf["items"][:40]],
        "reviews": [{"own": r.own, "brand": r.brand, "rating": r.rating, "topics": r.topic_list(), "sentiment": r.sentiment,
                     "text": (r.summary_ko or r.text)[:300]} for r in rev],
        "questions": view["questions"], "expressions": view["expressions"],
        "competitors": view["competitorTrends"],
        "trends": trends_view(t)["next"][:5],
        "lastMonthStrategy": {k: prev.get(k) for k in ("personas", "swot", "target", "positioning")} if prev else None,
    }


def monthly_sources(payload):
    """월간 AI에 넘긴 자료 개수 (화면 'AI가 읽은 자료')."""
    return {"shelf": len(payload["shelf"]), "reviews": len(payload["reviews"]),
            "questions": sum(q["count"] for q in payload["questions"]), "expressions": len(payload["expressions"]),
            "competitors": len(payload["competitors"]), "trends": len(payload["trends"]),
            "lastMonth": bool(payload["lastMonthStrategy"])}


# ───────── 경쟁사 ─────────

def _tier(titles):
    t = " ".join(titles).lower()
    if any(w in t for w in TIER_WORDS["direct"]):
        return "direct"
    if any(w in t for w in TIER_WORDS["category"]):
        return "category"
    return "substitute"


def competitors_view(limit_text=False):
    now, prev, _ = snapshots()
    watch = _watch_set()
    own = [x for x in now if x["own"]]
    own_sources = {x["source"] for x in own}
    own_buckets = {x["bucket"] for x in own if x["bucket"]}
    own_ps = [x["perServing"] for x in own if x["perServing"] is not None]
    texts = [c.text.lower() for c in Comment.query.filter_by(relevant=True).limit(3000).all()]
    texts += [r.text.lower() for r in Review.query.limit(3000).all()]
    groups = defaultdict(list)
    for x in now:
        if not x["own"] and x["brand"]:
            groups[x["brand"]].append(x)
    cols = {"direct": [], "category": [], "substitute": []}
    for brand, items in groups.items():
        b = brand.lower()
        sources = {x["source"] for x in items}
        ps = [x["perServing"] for x in items if x["perServing"] is not None]
        mentions = [t for t in texts if len(b) >= 3 and b not in GENERIC and re.search(r"(?<![a-z0-9])" + re.escape(b) + r"(?![a-z0-9])", t)]
        same_seller = bool(own_sources & sources)
        similar = bool(ps and own_ps and (any(bucket(p) in own_buckets for p in ps) or
                                          any(abs(p - o) / o <= 0.3 for p in ps for o in own_ps if o)))
        signals = {"sameSeller": same_seller, "mention": len(mentions), "similarPrice": similar}
        score = int(same_seller) + int(bool(mentions)) + int(similar)
        bits = [f"신호 {score}/3"]
        if same_seller:
            bits.append("같은 판매처 (" + ", ".join(SOURCE_LABEL[s] for s in sorted(sources & own_sources)) + ")")
        if mentions:
            bits.append(f"고객 언급 {len(mentions)}건")
        if similar:
            bits.append("가격대 비슷 (1회분 " + ", ".join(sorted({BUCKET_LABEL[bucket(p)] for p in ps})) + ")")
        if score == 0 and ps:
            bits.append("1회분 $" + f"{min(ps):.2f}" + "~")
        tier = _tier([x["title"] for x in items])
        cols[tier].append({
            "brand": brand, "brandGuessed": all(x["brandGuessed"] for x in items), "tier": tier, "tierLabel": TIER_LABEL[tier],
            "score": score, "signals": signals, "reason": " · ".join(bits), "watched": b in watch,
            "products": [{"title": x["title"], "source": x["sourceLabel"], "price": x["price"], "perServing": x["perServing"],
                          "link": x["link"], "in_stock": x["in_stock"], "rating": x["rating"], "reviews": x["reviews"]}
                         for x in items[:5]],
            "mentions": [] if limit_text else [m[:200] for m in mentions[:3]],
            "reviews": sum(x["reviews"] or 0 for x in items),
        })
    for k in cols:
        cols[k].sort(key=lambda x: (not x["watched"], -x["score"], -x["reviews"]))
    ranked = sorted([x for col in cols.values() for x in col], key=lambda x: (-x["score"], -x["reviews"]))
    recommended = [x["brand"] for x in ranked[:6] if x["score"] > 0]
    for col in cols.values():
        for x in col:
            x["recommended"] = x["brand"] in recommended
    total = sum(len(v) for v in cols.values())
    out = {"columns": cols, "tierLabels": TIER_LABEL, "watch": kv_get("watch", []), "ownFound": bool(own), "recommended": recommended}
    if limit_text:  # 월간 리뷰 · Excel은 열 묶음만 씀
        out["oneLine"] = ""
        return out
    return dict(out, **_analysis_view(now, prev, cols, watch, total))


def _analysis_view(now, prev, cols, watch, total):
    """경쟁사 분석: 이번 주 변화 · 비교 표(알디콤 맨 위) · 대체재 · 보관함 (competitors.py)."""
    shelf = {x["brand"].lower(): x for col in cols.values() for x in col}
    regs = {c.brand.lower(): c for c in Competitor.query.all()}
    rule_sub = {b for b, x in shelf.items() if x["tier"] == "substitute"}
    sub_set = {b for b, c in regs.items() if c.tier == "substitute"} | (rule_sub - set(regs))
    targets = {c.id for c in competitors.deep_targets(now)}
    groups = competitors.brand_groups(now)
    stored = competitors.stored_positions()
    yt = competitors.youtube_texts()
    rows, subs, archived = [], [], []
    for b in set(shelf) | set(regs):
        c, s = regs.get(b), shelf.get(b)
        d = competitors.comp_dict(c) if c else {"id": None, "brand": s["brand"], "tier": s["tier"], "status": "active",
                                                  "analysis": {}, "analyzed": False, "sources": [], "keywords": [],
                                                  "website": "", "isNew": True, "history": [], "researchedAt": None}
        d.update(competitors.brand_facts(groups.get(b, []), stored, yt, d["brand"]))
        d.update(tierLabel=TIER_LABEL.get(d["tier"], d["tier"]), deep=bool(c and c.id in targets), onShelf=s is not None,
                 watched=b in watch, own=False, signals=s["signals"] if s else None,
                 reason=s["reason"] if s else "이번 진열대에는 없어요.", brandGuessed=bool(s and s["brandGuessed"]))
        (archived if d["status"] == "archived" else subs if b in sub_set else rows).append(d)
    order = lambda d: (not d["watched"], not d["deep"], not d["analyzed"], -d["reviews"], d["brand"].lower())  # noqa: E731
    rows.sort(key=order)
    subs.sort(key=lambda d: -d["reviews"])
    own_items = [x for x in now if x["own"]]
    own = None
    if own_items:
        own = dict(competitors.brand_facts(own_items, stored, yt, "aldicom"), id=None, brand="알디콤 (자사)", own=True,
                   tierLabel="자사", analysis={}, analyzed=False, status="active", watched=False, deep=False, onShelf=True)
    changes, changes_demo = comp_changes(now, prev)
    analyzed = sum(1 for d in rows if d["analyzed"])
    chans = sorted({ch["label"] for d in rows for ch in d["channels"]})
    one = (f"경쟁사 {len(rows)}개 (분석 {analyzed}개) · 판매 채널 {' · '.join(chans) or '없음'}"
           + (f" · 이번 주 변화 {len(changes)}건" + (" (예시)" if changes_demo else "") if changes else "")
           + (f" · 주시 중 {len(watch)}개" if watch else "")) if total or rows else ""
    return {"oneLine": one, "own": own, "table": rows, "substitutes": subs, "archived": archived, "changes": changes, "changesDemo": changes_demo,
            "fields": [{"key": k, "label": label} for k, label in competitors.FIELDS],
            "channelLabels": competitors.CHANNEL_LABEL, "formLabels": competitors.FORM_LABEL,
            "aiReady": insights_ai.ready(), "firecrawlReady": envkeys.has("FIRECRAWL_API_KEY"), "deepTop": competitors.DEEP_TOP}


EXPORT_HEAD = [("brand", "브랜드"), ("tier", "구분"), ("status", "상태"), ("channels", "판매 채널 (최저가)"),
               ("perServing", "1회분 최저 ($)"), ("forms", "제형"), ("reviews", "진열대 리뷰 수"), ("praise", "고객 칭찬"),
               ("complaint", "고객 불만"), ("youtube", "유튜브 언급")] + list(competitors.FIELDS) + [
               ("website", "홈페이지"), ("sources", "출처"), ("researched_at", "조사일(UTC)")]


def competitor_export():
    """CSV · JSON · Excel용 한 줄씩 (보관 포함, 알디콤 제외)."""
    v = competitors_view()
    out = []
    for d in v["table"] + v["substitutes"] + v["archived"]:
        fb = d["feedback"]
        out.append({
            "brand": d["brand"], "tier": d["tierLabel"], "status": "보관" if d["status"] == "archived" else "관리 중",
            "channels": " · ".join(f"{ch['label']} ${ch['minPrice']:.2f}" if ch["minPrice"] is not None else ch["label"]
                                   for ch in d["channels"]),
            "perServing": d["perServing"], "forms": ", ".join(f["label"] for f in d["forms"]), "reviews": d["reviews"],
            "praise": f"{fb['praise']['topic']} {fb['praise']['count']}건" if fb["praise"] else "",
            "complaint": f"{fb['complaint']['topic']} {fb['complaint']['count']}건" if fb["complaint"] else "",
            "youtube": d["youtube"],
            **{f: competitors.field_text(d["analysis"], f) for f, _ in competitors.FIELDS},
            "website": d["website"], "sources": " ".join(s["url"] for s in d["sources"] if s.get("url")),
            "researched_at": d["researchedAt"] or "",
        })
    return out


# ───────── 구매자 리뷰 ─────────

def cs_dict(r, cs=None):
    """자사 리뷰의 CS 상태 — 저장된 것이 없으면 규칙(불만 · 부작용 · 배송/파손 단어)으로 '대응 필요', 아니면 '미확인'."""
    if cs is not None:
        return {"cs": cs.status, "csAuto": False, "memo": cs.memo, "csBy": cs.updated_by, "csAt": iso(cs.updated_at)}
    todo = r.sentiment == "-" or "부작용" in r.topic_list() or bool(CS_WORDS.search(f"{r.title} {r.text}"))
    return {"cs": "todo" if todo else "new", "csAuto": True, "memo": "", "csBy": "", "csAt": None}


def _cs_items(rows):
    """자사 리뷰 전부 (채널 · 제품 라인 · 주제 · 감정 · CS 상태로 거르는 화면용)."""
    own = [r for r in rows if r.own]
    saved = {c.review_id: c for c in ReviewCS.query.filter(ReviewCS.review_id.in_([r.id for r in own])).all()} if own else {}
    out = []
    for r in own:
        out.append({"id": r.id, "source": r.source, "sourceLabel": REVIEW_SOURCE_LABEL.get(r.source, r.source),
                    "line": channel_reviews.product_line(r.product_title), "product": r.product_title[:200], "rating": r.rating,
                    "title": r.title, "text": r.text[:3000], "summaryKo": r.summary_ko, "topics": r.topic_list(),
                    "sentiment": r.sentiment, "date": r.date_text, "by": r.classified_by, **cs_dict(r, saved.get(r.id))})
    return out


def _channel_stats():
    """채널 · 상품별 전체 리뷰 수 · 평균 별점 (사이트에 보이는 숫자) + 모은 수."""
    out = []
    for k, v in kv_get("channel_review_stats", {}).items():
        out.append(dict(v, key=k, channelLabel=REVIEW_SOURCE_LABEL.get(v.get("channel"), v.get("channel"))))
    return sorted(out, key=lambda x: (x.get("channel", ""), x.get("line", "")))


def reviews_view():
    rows = Review.query.order_by(Review.collected_at.desc()).limit(2000).all()
    watch = _watch_set()
    brands = {}
    items = []
    for r in rows:
        key = "자사 (알디콤)" if r.own else (r.brand or guess_brand(r.product_title) or "브랜드 미상")
        b = brands.setdefault(key, {"brand": key, "own": r.own, "watched": key.lower() in watch, "total": 0,
                                    "topics": {t: {"pos": 0, "neg": 0, "neu": 0} for t in TOPICS}, "ratings": []})
        b["total"] += 1
        if r.rating is not None:
            b["ratings"].append(r.rating)
        for t in r.topic_list():
            if t in b["topics"]:
                b["topics"][t]["pos" if r.sentiment == "+" else "neg" if r.sentiment == "-" else "neu"] += 1
        if len(items) < 400:
            items.append({"id": r.id, "brand": key, "own": r.own, "source": r.source,
                          "sourceLabel": REVIEW_SOURCE_LABEL.get(r.source, r.source), "product": r.product_title[:200],
                          "rating": r.rating, "title": r.title, "text": r.text[:1500], "summaryKo": r.summary_ko,
                          "topics": r.topic_list(), "sentiment": r.sentiment, "date": r.date_text, "by": r.classified_by})
    table = sorted(brands.values(), key=lambda b: (not b["own"], not b["watched"], -b["total"]))[:12]
    for b in table:
        b["avgRating"] = round(sum(b["ratings"]) / len(b["ratings"]), 2) if b["ratings"] else None
        b.pop("ratings")
    summaries = [{"asin": k, **v} for k, v in kv_get("amazon_summaries", {}).items()]
    one = ""
    if rows:
        neg = Counter(t for r in rows if r.sentiment == "-" for t in r.topic_list())
        pos = Counter(t for r in rows if r.sentiment == "+" for t in r.topic_list())
        one = f"리뷰 {len(rows)}개 (Amazon 대표 리뷰 · Olive Young · Yami · Weee!)"
        if pos:
            one += f" · 칭찬 1위 {pos.most_common(1)[0][0]}"
        if neg:
            one += f" · 불만 1위 {neg.most_common(1)[0][0]}"
    cs = _cs_items(rows)
    shared = slack.shared_map()
    for x in items + cs:  # 슬랙으로 보낸 리뷰 (누가 · 언제)
        s = shared.get(str(x["id"])) or {}
        x["slackBy"], x["slackAt"] = s.get("by", ""), s.get("at")
    return {"oneLine": one, "table": table, "topics": list(TOPICS), "items": items, "summaries": summaries,
            "cs": cs, "csStatus": list(CS_STATUS.items()), "lines": list(channel_reviews.LINES), "channels": _channel_stats(),
            "stats": {"total": len(rows), "amazon": sum(1 for r in rows if r.source == "amazon"),
                      "oliveyoung": sum(1 for r in rows if r.source == "oliveyoung"),
                      "yami": sum(1 for r in rows if r.source == "yami"),
                      "weee": sum(1 for r in rows if r.source == "weee"), "own": sum(1 for r in rows if r.own),
                      "csTodo": sum(1 for x in cs if x["cs"] == "todo"),
                      "ai": sum(1 for r in rows if r.classified_by == "ai")},
            "new7d": _new_reviews()["count"], "slackReady": slack.ready()}


# ───────── 유튜브 댓글 ─────────

def audience_view(month=None):
    months = [m for (m,) in db.session.query(Video.month).distinct().order_by(Video.month.desc()).all()]
    month = month if month in months else (months[0] if months else None)
    if not month:
        return {"month": None, "months": [], "oneLine": "", "questions": [], "expressions": [], "creators": [], "comments": [],
                "process": None}
    videos = Video.query.filter_by(month=month).all()
    kept = [v for v in videos if v.kept]
    vids = {v.id: v for v in videos}
    comments = Comment.query.filter_by(month=month).order_by(Comment.likes.desc()).all()
    relevant = [c for c in comments if c.relevant]
    questions = top_questions(month, limit=None)  # 주제 전체 (비율이 전체 질문 기준이 되도록)
    ex = expressions([c.text for c in relevant], 5)
    ch = defaultdict(lambda: {"views": 0, "videos": 0, "relevant": 0, "titles": []})
    for v in kept:
        c = ch[(v.channel_id, v.channel)]
        c["views"] += v.views or 0
        c["videos"] += 1
        if len(c["titles"]) < 3:
            c["titles"].append(v.title)
    for cm in relevant:
        v = vids.get(cm.video_id)
        if v and v.kept:
            ch[(v.channel_id, v.channel)]["relevant"] += 1
    creators = sorted(({"channel": name, "channelId": cid, "url": f"https://www.youtube.com/channel/{cid}" if cid else "", **c}
                       for (cid, name), c in ch.items()), key=lambda x: (-x["relevant"], -x["views"]))[:5]
    cfg = get_collect_config()
    by_set = Counter(v.query_set for v in videos)
    one = f"{month} · 영상 {len(kept)}개 · 관련 댓글 {len(relevant)}개"
    if questions:
        one += f" · 최다 질문 '{questions[0]['label']}' {questions[0]['count']}건"
    return {
        "month": month, "months": months, "oneLine": one, "questions": questions, "expressions": ex, "creators": creators,
        "comments": [{"text": c.text[:600], "likes": c.likes, "question": c.is_question, "topic": QUESTION_LABEL.get(c.topic, ""),
                      "video": vids[c.video_id].title if c.video_id in vids else "", "videoId": c.video_id, "id": c.id, "by": c.classified_by}
                     for i, c in enumerate(relevant) if i < 150 or c.is_question],  # 질문 댓글은 모두 (질문 주제를 누르면 보여 줌)
        "process": {"found": len(videos), "kept": len(kept), "minViews": cfg.get("min_views"), "comments": len(comments),
                    "relevant": len(relevant), "noisePct": round((len(comments) - len(relevant)) * 100 / len(comments))
                    if comments else None, "bySet": dict(by_set),
                    "queries": cfg.get("search_sets"), "ai": sum(1 for c in comments if c.classified_by == "ai")},
    }


def personas_expose():
    row = Monthly.query.order_by(Monthly.month.desc()).first()
    while row is not None:
        data = load_json(row.data, {})
        ps = [p for p in data.get("personas") or [] if isinstance(p, dict) and p.get("name")]
        if ps:
            return [{"name": p.get("name", ""), "oneLine": p.get("oneLine", ""), "needs": p.get("needs", ""),
                     "message": p.get("message", "")} for p in ps[:3]]
        row = Monthly.query.filter(Monthly.month < row.month).order_by(Monthly.month.desc()).first()
    return []
