"""이번 주 액션 — 판단은 규칙이 하고, AI는 문장만 씁니다 (README 주간 리뷰).

규칙 (README 그대로)
  1. 자사(알디콤) 품절 + 45일 안에 시즌                 → 긴급
  2. 경쟁사 가격 ±10% 이상 변동 (지난 수집 대비)          → 인하 긴급 · 인상 참고
  3. 신규 제품 진입                                     → 참고
  4. 작년에 검색이 평균보다 20% 이상 늘었던 시즌이 30일 안 → 기회
  5. 유튜브 댓글 최다 질문                             → 기회
  6. 자사 리뷰 불만 1위                                 → 긴급
  7. 경쟁 제품 라인 품절 (한 채널)                       → 기회 · 같은 라인이 2채널 이상 품절 → 긴급
  8. 경쟁 상품 재고 임박 (Amazon 'Only N left', N ≤ 20)  → 참고
  9. 경쟁 상품 재입고 (지난 수집 품절 → 이번 재고 있음)   → 참고

입력(ctx)은 화면 · 테스트가 같은 모양으로 넘기는 dict입니다 (DB를 직접 읽지 않음):
  today(date) · shelf_now · shelf_prev(None이면 첫 주 = 비교 불가) · seasons · trends{키워드: 점} · questions · own_complaints
  진열대 한 줄의 line({id, label}) · stockLeft는 있으면 씀 (없으면 상품 하나 = 라인 하나)
"""
from datetime import date, timedelta

GRADE_ORDER = {"긴급": 0, "기회": 1, "참고": 2}
SOURCE_LABEL = {"amazon": "Amazon", "weee": "Weee!", "yami": "Yami"}
RULE_LABEL = {
    "own_oos_season": "자사 품절 + 45일 안에 시즌 → 긴급",
    "price_cut": "경쟁사 가격 10% 이상 인하 → 긴급",
    "price_up": "경쟁사 가격 10% 이상 인상 → 참고",
    "new_entry": "신규 제품 진입 → 참고",
    "season_search": "작년 검색이 평균보다 20% 이상 늘었던 시즌이 30일 안 → 기회",
    "top_question": "유튜브 댓글 최다 질문 → 기회",
    "own_complaint": "자사 리뷰 불만 1위 → 긴급",
    "comp_oos": "경쟁 제품 한 채널 품절 → 기회",
    "comp_oos_multi": "경쟁 제품 2채널 이상 동시 품절 → 긴급",
    "low_stock": "경쟁 상품 재고 20개 이하 → 참고",
    "restock": "경쟁 상품 재입고 → 참고",
}
COMPLIANCE = "광고 문구에 효능 표현이나 경쟁 브랜드 이름을 넣을 때는 컴플라이언스 탭 검토를 거쳐요."
LOW_STOCK = 20
PRICE_PCT = 10
OOS_DAYS = 45
SEASON_DAYS = 30
SEASON_LIFT = 20
QUESTION_MIN = 5       # 최다 질문 액션: 최소 건수
QUESTION_SHARE = 15    # 최다 질문 액션: 분류된 질문 중 최소 비중(%)


def _d(v):
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _short(title, n=48):
    t = str(title or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _usd(v):
    return f"${v:,.2f}" if v is not None else "—"


def season_lift(points, season_start, window=7):
    """작년 같은 시기(시즌 날짜 - 1년 ± window일) 최고값이 12개월 평균보다 몇 % 높았나 → {pct, peak, avg, date} 또는 None."""
    pts = [p for p in points or [] if p.get("value") is not None]
    if len(pts) < 8:
        return None
    start = _d(season_start)
    try:
        last = start.replace(year=start.year - 1)
    except ValueError:  # 2월 29일
        last = start - timedelta(days=365)
    lo, hi = last - timedelta(days=window), last + timedelta(days=window)
    first, final = _d(pts[0]["date"]), _d(pts[-1]["date"])
    if hi < first or lo > final + timedelta(days=6):
        return None
    near = [p for p in pts if lo <= _d(p["date"]) <= hi]
    if not near:
        return None
    avg = sum(p["value"] for p in pts) / len(pts)
    if avg <= 0:
        return None
    peak = max(near, key=lambda p: p["value"])
    return {"pct": round((peak["value"] - avg) / avg * 100, 1), "peak": peak["value"], "avg": round(avg, 1), "date": peak["date"]}


def _src(x):
    return SOURCE_LABEL.get(x.get("source"), x.get("source"))


def _line(x):
    ln = x.get("line") or {}
    return ln.get("id") or x.get("key"), ln.get("label") or _short(x.get("title"))


def line_oos(rows):
    """경쟁 상품 rows → {라인 id: {label, channels(판매처 전체), oos(그 판매처 상품이 모두 품절인 판매처)}}."""
    by = {}
    for x in rows or []:
        if x.get("own"):
            continue
        lid, label = _line(x)
        g = by.setdefault(lid, {"label": label, "items": {}})
        g["items"].setdefault(x.get("source"), []).append(x)
    out = {}
    for lid, g in by.items():
        oos = [s for s, its in g["items"].items() if all(i.get("in_stock") is False for i in its)]
        out[lid] = {"label": g["label"], "channels": list(g["items"]), "oos": oos}
    return out


def _action(rule, grade, fact, why, todo, facts, weight=0):
    return {"id": rule, "rule": rule, "grade": grade, "ruleLabel": RULE_LABEL[rule], "fact": fact, "why": why, "todo": todo,
            "facts": facts, "weight": weight, "ai": False}


def weekly_actions(ctx):
    today = _d(ctx["today"])
    acts = []
    seasons = []
    for s in ctx.get("seasons") or []:
        if s.get("monitor") is False:
            continue
        days = (_d(s["start"]) - today).days
        if days >= 0:
            seasons.append((s, days))
    seasons.sort(key=lambda x: x[1])
    now = ctx.get("shelf_now") or []
    prev = ctx.get("shelf_prev")

    # 1) 자사 품절 + 45일 안 시즌
    oos = [x for x in now if x.get("own") and x.get("in_stock") is False]
    soon = [(s, d) for s, d in seasons if d <= OOS_DAYS]
    if oos and soon:
        s, d = soon[0]
        names = ", ".join(f"{_short(x['title'])} ({SOURCE_LABEL.get(x.get('source'), x.get('source'))})" for x in oos[:3])
        acts.append(_action(
            "own_oos_season", "긴급",
            f"자사(알디콤) 상품 {len(oos)}개가 품절이에요: {names}.",
            f"{s['name']}까지 {d}일 남았어요 (D-{d}). 수요가 몰리는 시기에 살 수 없으면 고객이 경쟁 제품으로 넘어가요.",
            "판매처에 재입고 일정을 확인하고, 시즌 전 재입고가 어렵다면 캠페인 링크를 재고가 있는 판매처로 바꾸세요.",
            {"품절 상품": [{"title": x["title"], "source": x.get("source"), "link": x.get("link")} for x in oos], "시즌": s["name"], "시즌 시작": s["start"], "남은 날": d}, weight=100 - d))

    # 2) 경쟁사 가격 ±10% · 3) 신규 진입 — 지난 수집이 있어야 비교 가능
    if prev is not None:
        before = {(x.get("source"), x.get("key")): x for x in prev}
        compare = {x.get("source") for x in prev}  # 지난 묶음이 있는 판매처끼리만 비교
        cuts, ups = [], []
        for x in now:
            if x.get("own") or x.get("source") not in compare:
                continue
            p = before.get((x.get("source"), x.get("key")))
            if not p or not p.get("price") or not x.get("price"):
                continue
            pct = (x["price"] - p["price"]) / p["price"] * 100
            row = {"title": x["title"], "source": x.get("source"), "before": p["price"], "after": x["price"], "pct": round(pct, 1),
                   "link": x.get("link")}
            if pct <= -PRICE_PCT:
                cuts.append(row)
            elif pct >= PRICE_PCT:
                ups.append(row)
        for rows, rule, grade in ((cuts, "price_cut", "긴급"), (ups, "price_up", "참고")):
            if not rows:
                continue
            rows.sort(key=lambda r: -abs(r["pct"]))
            top = rows[0]
            word = "내렸어요" if rule == "price_cut" else "올렸어요"
            more = f" 외 {len(rows) - 1}개" if len(rows) > 1 else ""
            fact = (f"경쟁 상품 {len(rows)}개가 가격을 10% 넘게 {word}. 가장 큰 변동: {_short(top['title'])} "
                    f"({SOURCE_LABEL.get(top['source'], top['source'])}) {_usd(top['before'])} → {_usd(top['after'])} ({top['pct']:+.0f}%){more}.")
            if rule == "price_cut":
                why = "같은 진열대에서 가격 차이가 벌어지면 자사 상품이 상대적으로 비싸 보여 구매가 줄 수 있어요."
                todo = "데이터 › 진열대에서 1회분 단가 구간을 비교하고, 할인 · 묶음 구성으로 대응할지 정하세요."
            else:
                why = "경쟁사 가격이 오르면 자사 가격 경쟁력이 상대적으로 좋아져요."
                todo = "1회분 단가 비교를 상세페이지 · 콘텐츠 메시지에 쓸 수 있는지 검토하세요."
            acts.append(_action(rule, grade, fact, why, todo, {"변동 상품": rows[:10]}, weight=abs(top["pct"])))
        new = [x for x in now if x.get("source") in compare and (x.get("source"), x.get("key")) not in before
               and not x.get("own")]
        if new:
            names = ", ".join(_short(x["title"], 36) for x in new[:3])
            more = f" 외 {len(new) - 3}개" if len(new) > 3 else ""
            acts.append(_action(
                "new_entry", "참고",
                f"진열대에 새 상품 {len(new)}개가 들어왔어요: {names}{more}.",
                "같은 검색어 · 카테고리에서 고객의 선택지가 늘어요.",
                "데이터 › 경쟁사에서 새 상품의 가격 · 메시지를 확인하고 주시할지 정하세요.",
                {"새 상품": [{"title": x["title"], "source": x.get("source"), "price": x.get("price"), "link": x.get("link")} for x in new[:10]]},
                weight=len(new)))

    # 7) 경쟁 제품 라인 품절 — 한 채널 = 기회 · 2채널 이상 = 긴급 (지난 수집에도 품절이면 'N주째')
    was = line_oos(prev) if prev is not None else {}
    single, multi = [], []
    for lid, g in line_oos(now).items():
        if not g["oos"]:
            continue
        row = {"line": g["label"], "oos": [SOURCE_LABEL.get(s, s) for s in g["oos"]],
               "channels": [SOURCE_LABEL.get(s, s) for s in g["channels"]],
               "again": bool(set(g["oos"]) & set((was.get(lid) or {}).get("oos") or []))}
        (multi if len(g["oos"]) >= 2 else single).append(row)
    if multi:
        top = multi[0]
        more = f" 외 {len(multi) - 1}개 제품" if len(multi) > 1 else ""
        again = " 지난 수집부터 계속 품절이에요." if top["again"] else ""
        acts.append(_action(
            "comp_oos_multi", "긴급",
            f"{top['line']} — {' · '.join(top['oos'])}에서 모두 품절이에요{more}.{again}",
            "여러 채널에서 한꺼번에 비면 공백이 길어질 수 있어요. 이 제품을 찾던 고객이 다른 제품을 처음 사 보는 시기예요.",
            "외부 광고(IG · TikTok) 랜딩을 재고가 넉넉한 자사 채널로 모으고, 처음 산 고객이 계속 사도록 묶음 구성이나 공구 일정 앞당기기를 검토하세요. "
            "품절 원인은 판매처 공지로 확인하세요."
            + (" 늘린 광고가 실제로 팔리고 있는지 성과분석에서 확인하세요." if top["again"] else "") + " " + COMPLIANCE,
            {"품절 제품": multi[:10]}, weight=200 + len(multi)))
    if single:
        top = single[0]
        ch = top["oos"][0]
        more = f" 외 {len(single) - 1}개 제품" if len(single) > 1 else ""
        acts.append(_action(
            "comp_oos", "기회",
            f"{top['line']} — {ch}에서 품절이에요{more}." + (" 지난 수집부터 계속 품절이에요." if top["again"] else ""),
            f"{ch}에서 이 제품을 찾던 고객이 바로 살 수 있는 다른 제품을 찾아요. 이 채널에서 고객을 데려올 기회예요.",
            f"{ch}에서 경쟁 브랜드 키워드 입찰 상향을 검토하고, 소재에 '재고 있음 · 즉시 출고'를 넣으세요. "
            "이 기간에는 할인 쿠폰 없이도 팔릴 수 있으니 쿠폰 보류도 검토하세요. " + COMPLIANCE,
            {"품절 제품": single[:10]}, weight=len(single)))

    # 8) 경쟁 상품 재고 임박 (Only N left)
    low = sorted((x for x in now if not x.get("own") and x.get("in_stock") is not False
                  and x.get("stockLeft") is not None and x["stockLeft"] <= LOW_STOCK), key=lambda x: x["stockLeft"])
    if low:
        x = low[0]
        more = f" 외 {len(low) - 1}개" if len(low) > 1 else ""
        acts.append(_action(
            "low_stock", "참고",
            f"{_line(x)[1]} ({_src(x)}) — {x['stockLeft']}개 남았어요{more}.",
            "곧 품절될 수 있어요. 품절이 뜬 뒤에 소재를 만들면 늦어요.",
            "경쟁 제품 품절에 대비하세요. 경쟁 키워드 광고 세트와 '즉시 출고' 소재를 미리 만들어 두고, "
            "다음 수집에서 품절이 확인되면 바로 켜세요. " + COMPLIANCE,
            {"재고 임박": [{"title": i["title"], "source": i.get("source"), "price": i.get("price"), "link": i.get("link")} for i in low[:10]],
             "남은 개수": [i["stockLeft"] for i in low[:10]], "기준": f"{LOW_STOCK}개 이하"}, weight=LOW_STOCK - x["stockLeft"]))

    # 9) 경쟁 상품 재입고
    if prev is not None:
        before = {(x.get("source"), x.get("key")): x for x in prev}
        back = [x for x in now if not x.get("own") and x.get("in_stock") is True
                and (before.get((x.get("source"), x.get("key"))) or {}).get("in_stock") is False]
        if back:
            x = back[0]
            more = f" 외 {len(back) - 1}개" if len(back) > 1 else ""
            acts.append(_action(
                "restock", "참고",
                f"{_line(x)[1]} ({_src(x)}) — 다시 들어왔어요{more}.",
                "경쟁 제품이 돌아오면 올려 둔 입찰가는 클릭 값만 비싸지고 전환은 줄어요.",
                "경쟁 제품이 돌아왔어요. 품절 때 올렸던 입찰가를 평소대로 낮추고, 보류했던 쿠폰을 다시 켤지 정하세요.",
                {"재입고": [{"title": i["title"], "source": i.get("source"), "price": i.get("price"), "link": i.get("link")}
                         for i in back[:10]]},
                weight=len(back)))

    # 4) 작년 검색 +20% 시즌이 30일 안
    trends = ctx.get("trends") or {}
    for s, d in seasons:
        if d > SEASON_DAYS:
            break
        best = None
        for kw, pts in trends.items():
            r = season_lift(pts, s["start"])
            if r and r["pct"] >= SEASON_LIFT and (best is None or r["pct"] > best["pct"]):
                best = dict(r, keyword=kw)
        if best:
            acts.append(_action(
                "season_search", "기회",
                f"작년 {s['name']} 무렵 '{best['keyword']}' 검색이 12개월 평균보다 {best['pct']:+.0f}% 많았어요 ({best['date']} 주).",
                f"{s['name']}까지 {d}일 남았어요. 올해도 비슷하게 늘면 그 전에 노출을 시작해야 해요.",
                "시즌 캠페인 소재와 광고 일정을 이번 주에 확정하세요 (콘텐츠 제작).",
                {"시즌": s["name"], "시즌 시작": s["start"], "남은 날": d, "키워드": best["keyword"], "작년 최고": best["peak"],
                 "12개월 평균": best["avg"], "최고 주": best["date"]}, weight=best["pct"]))

    # 5) 유튜브 댓글 최다 질문
    qs = [q for q in ctx.get("questions") or [] if q.get("count") and q.get("key") not in ("", "other")]
    total = sum(q["count"] for q in qs)
    if qs and qs[0]["count"] >= QUESTION_MIN and qs[0]["count"] * 100 >= total * QUESTION_SHARE:
        q = qs[0]
        ex = f' 예: "{_short(q["examples"][0], 80)}"' if q.get("examples") else ""
        acts.append(_action(
            "top_question", "기회",
            f"유튜브 댓글에서 가장 많은 질문은 '{q['label']}'이에요 ({q['count']}건).{ex}",
            "사기 전에 고객이 궁금해하는 점이라, 먼저 답해 주면 망설임을 줄일 수 있어요.",
            "상세페이지 FAQ · 짧은 영상으로 답하세요. 효능 표현은 컴플라이언스 검토를 거쳐요.",
            {"질문 주제": q["label"], "건수": q["count"], "예시": (q.get("examples") or [])[:3]}, weight=q["count"]))

    # 6) 자사 리뷰 불만 1위
    cs = [c for c in ctx.get("own_complaints") or [] if c.get("count")]
    if cs:
        c = cs[0]
        ex = f' 예: "{_short(c["examples"][0], 80)}"' if c.get("examples") else ""
        acts.append(_action(
            "own_complaint", "긴급",
            f"자사 리뷰 불만 1위는 '{c['topic']}'이에요 ({c['count']}건).{ex}",
            "같은 불만이 쌓이면 별점과 구매 전환이 함께 떨어져요.",
            "CS · 제품 담당과 원인을 확인하고, 상세페이지 안내나 CS 답변 템플릿을 고치세요.",
            {"불만 주제": c["topic"], "건수": c["count"], "예시": (c.get("examples") or [])[:3]}, weight=c["count"]))

    acts.sort(key=lambda a: (GRADE_ORDER[a["grade"]], -a["weight"]))
    seen = {}
    for a in acts:  # 같은 규칙이 여러 번 걸리면 id 뒤에 번호 (AI 문장을 id로 붙이기 때문)
        n = seen.get(a["rule"], 0)
        seen[a["rule"]] = n + 1
        if n:
            a["id"] = f"{a['rule']}-{n + 1}"
    return acts
