"""홈 · 데일리 브리핑 API — 뉴스 수집(구글 뉴스 RSS는 가짜 응답) · AI 요약 · 이번 주 To-Do (인터넷 · 키 없이)."""
import json
import threading
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

import pytest

from server.extensions import db
from tabs.home import news, routes, rss, todos
from tabs.home.models import HomeNewsArticle, HomeNewsRun

TODAY = date(2026, 10, 23)  # 금요일 → 이번 주 월요일 10-19


@pytest.fixture()
def today(monkeypatch):
    monkeypatch.setattr("server.catalog.service._today", lambda: TODAY)
    return TODAY


@pytest.fixture()
def fast(app):
    app.config["HOME_RSS_INTERVAL"] = 0  # 테스트에서는 요청 사이를 기다리지 않음
    return app


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeRes:
    def __init__(self, status=200, body=b""):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")


def rss_xml(items):
    def one(i):
        pub = format_datetime(i["date"].replace(tzinfo=timezone.utc), usegmt=True)
        return (f"<item><title>{escape(i['title'])}</title><link>{escape(i['url'])}</link><pubDate>{pub}</pubDate>"
                f"<source url=\"https://src.example.com\">{escape(i['source'])}</source></item>")
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><title>Google News</title>"
            + "".join(one(i) for i in items) + "</channel></rss>").encode("utf-8")


def items_for(group):
    """검색어별 가짜 기사: 공통 기사 1 + 자기 기사 2 + 노이즈 1 + 잘못된 주소 1 (+ hangover는 급증용 기사)."""
    now = _now()
    items = [{"url": "https://www.example-news.com/shared-story", "title": "Non-alcoholic drinks surge before Halloween - Example News",
              "source": "Example News", "date": now - timedelta(hours=3)}]
    items += [{"url": f"https://{group}.example.com/story-{i}", "title": f"{group} story {i} &amp; more - {group.title()} Daily",
               "source": f"{group.title()} Daily", "date": now - timedelta(hours=10 + i)} for i in range(2)]
    items.append({"url": f"https://{group}.example.com/crash", "title": "Two arrested after DUI crash", "source": "Local", "date": now})
    items.append({"url": "javascript:alert(1)", "title": "bad url", "source": "x", "date": now})
    if group == "category":  # 최근 7일 6건 · 그 전 7일 1건 → 보도 급증
        items += [{"url": f"https://hangover.example.com/new-{i}", "title": f"Hangover trend {i}", "source": "Trend",
                   "date": now - timedelta(days=1, hours=i)} for i in range(3)]
        items.append({"url": "https://hangover.example.com/old", "title": "Old hangover story", "source": "Trend",
                      "date": now - timedelta(days=10)})
    return items


def valid_new(group):
    return [i for i in items_for(group) if i["url"].startswith("https://") and "DUI" not in i["title"]]


def make_session(monkeypatch, responder):
    calls = []

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            assert timeout == 30 and (url == rss.BASE) == (params is not None)
            params = dict(params) if params else {"feed": url}   # 업계지 피드는 주소를 그대로 부름
            calls.append(params)
            return responder(params)

    monkeypatch.setattr("tabs.home.rss.requests.Session", FakeSession)
    return calls


def group_of(params):
    q = params.get("q")
    for g in news.load_queries()["groups"]:
        if params.get("feed") and params["feed"] == g.get("feed"):
            return g["id"]
        if q and q == f"{g.get('query')} when:14d":
            return g["id"]
    raise AssertionError(q)


def good_responder(params):
    return FakeRes(body=rss_xml(items_for(group_of(params))))


# ───────── 검색어 · RSS 클라이언트 ─────────

def test_queries_are_plain_search_terms():
    cfg = news.load_queries()
    queries = [g for g in cfg["groups"] if not g.get("feed")]
    feeds = [g for g in cfg["groups"] if g.get("feed")]
    assert len(queries) == 8  # 2026-10-24 팀 결정 (9개 → 무알코올 · 기능성 합쳐 8개)
    assert len(feeds) == 6 and "trend" in {g["area"] for g in feeds}   # 업계지 · 트렌드 피드 (같은 날 추가)
    for g in queries:
        assert g.get("label") and news.build_query(cfg, g) == g["query"]
    for g in cfg["groups"]:
        assert g["kind"] in ("signal", "flow", "trend") and g["areaLabel"]
    for g in feeds:
        assert g["feed"].startswith("https://") and g.get("source") and not g.get("query")
    assert {a for a, _ in news.AREAS} == {g["area"] for g in cfg["groups"]}
    assert {g["area"] for g in cfg["groups"] if g["kind"] == "signal"} == {"category", "competitor", "regulation"}
    assert {"DUI", "drunk driving", "crash"} <= set(cfg["exclude"])


@pytest.mark.parametrize("title, group, why", [
    ("The AI hangover is coming for tech", None, "metaphor"),
    ("Tariff hangovers hit retailers", None, "metaphor"),
    ("Holiday hangovers: what actually helps", None, None),        # 진짜 숙취 기사는 남김
    ("Can DHM help with a hangover?", None, None),
    ("Provi Names Matthew Tucker as Chief Executive Officer", None, "personnel"),
    ("Liquid I.V. hires agency for new campaign", None, None),      # 직책이 없으면 인사 소식 아님
    ("Low-and-no consumption rises to 53% in UK", None, "foreign"),
    ("US vs UK: non-alcoholic beer sales compared", None, None),     # 미국이 함께 나오면 남김
    ("Two arrested after DUI crash", None, "incident"),
    ("Police target alcohol sales to minors", None, "incident"),
    ("숙취해소제 미국 진출", "kfood", None),                           # K-푸드만 한국어 허용
    ("숙취해소제 미국 진출", "sober", "korean"),
])
def test_rule_filter(title, group, why):
    cfg = news.load_queries()
    groups = {g["id"]: g for g in cfg["groups"]}
    assert news.make_filter(cfg)(title, groups.get(group)) == why


def test_same_title_catches_mixed_up_ids():
    assert news.same_title("FDA Finalizes NDI Guidance on Identity", "FDA Finalizes NDI Guidance on Identity and Safety Information")
    assert not news.same_title("FDA Finalizes NDI Guidance on Identity", "Ads of the Week: 10 Campaigns That Caught Our Eye")


def test_pick_guarantees_signal_slots_and_area_cap():
    kinds = {"category": "signal", "regulation": "signal", "behavior": "flow", "sales": "flow"}.get
    cands = ([{"id": i, "area": "behavior", "score": 7} for i in range(6)]
             + [{"id": 10 + i, "area": "sales", "score": 6} for i in range(4)]
             + [{"id": 20, "area": "category", "score": 2}, {"id": 21, "area": "category", "score": 1},
                {"id": 22, "area": "category", "score": 1}, {"id": 30, "area": "regulation", "score": 0}])
    out = news.pick(cands, kinds)
    by = {}
    for c in out:
        by[c["area"]] = by.get(c["area"], 0) + 1
    assert len(out) == 8
    assert by == {"category": 2, "regulation": 1, "behavior": 3, "sales": 2}   # signal은 점수가 낮아도 자리 보장 · 분야는 3개까지
    assert [c["score"] for c in out] == sorted((c["score"] for c in out), reverse=True)


def test_pick_keeps_trends_in_their_own_slots():
    kinds = {"behavior": "flow", "trend": "trend"}.get
    cands = ([{"id": i, "area": "behavior", "score": 5} for i in range(3)]
             + [{"id": 10 + i, "area": "trend", "score": 9 - i} for i in range(6)])
    out = news.pick(cands, kinds)
    assert [c["area"] for c in out] == ["behavior"] * 3 + ["trend"] * news.TREND_ITEMS   # 트렌드는 뒤에 따로 · 점수순 4개
    assert [c["id"] for c in out[3:]] == [10, 11, 12, 13]


def test_pick_spreads_trends_across_sources():
    kinds = {"trend": "trend"}.get
    mr = [{"id": i, "area": "trend", "domain": "Modern Retail", "score": 9} for i in range(4)]
    others = [{"id": 10, "area": "trend", "domain": "Mintel", "score": 3}, {"id": 11, "area": "trend", "domain": "NOSH", "score": 2}]
    out = news.pick(mr + others, kinds)
    assert sorted(c["id"] for c in out) == [0, 1, 10, 11]          # 한 매체 2개까지 · 다른 매체로 채움
    only = news.pick(mr + others[:1], kinds)
    assert sorted(c["id"] for c in only) == [0, 1, 2, 10]          # 다른 매체가 모자라면 점수순으로 채움


def test_client_parses_rss_and_retries(monkeypatch):
    slept = []
    now = _now().replace(microsecond=0)
    seq = [FakeRes(429), FakeRes(body=rss_xml([{"url": "https://a.example.com/x", "title": "Beer sales fall - Reuters",
                                                "source": "Reuters", "date": now}]))]

    class S:
        def get(self, url, params=None, timeout=None, headers=None):
            assert params["hl"] == "en-US" and params["gl"] == "US" and params["ceid"] == "US:en"
            return seq.pop(0)

    c = rss.RssClient(interval=1, sleep=slept.append, session=S())
    arts = c.articles("beer sales")                  # 429 → 쉬고 한 번 더
    assert c.calls == 2 and c.rate_limited == 1 and slept
    assert arts == [{"url": "https://a.example.com/x", "title": "Beer sales fall", "domain": "Reuters", "date": now}]


def test_client_rejects_non_rss_without_retry():
    class S:
        n = 0

        def get(self, url, params=None, timeout=None, headers=None):
            S.n += 1
            return FakeRes(200, "<html>not rss")

    c = rss.RssClient(interval=0, sleep=lambda s: None, session=S())
    with pytest.raises(rss.RssError) as e:
        c.articles("x")
    assert "RSS" in str(e.value) and S.n == 1


def test_feed_reads_site_rss():
    now = _now().replace(microsecond=0)
    body = ('<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>BevNET</title>'
            f"<item><title>Brand launches &amp; more</title><link>https://bev.example.com/a</link>"
            f"<pubDate>{format_datetime(now.replace(tzinfo=timezone.utc), usegmt=True)}</pubDate></item>"
            f"<item><title>Dated by dc</title><link>https://bev.example.com/b</link><dc:date>{now.isoformat()}Z</dc:date></item>"
            "</channel></rss>")
    seen = []

    class S:
        def get(self, url, params=None, timeout=None, headers=None):
            seen.append((url, params))
            return FakeRes(body=body) if url.endswith("/feed") else FakeRes(403, "<html>Just a moment")

    c = rss.RssClient(interval=0, sleep=lambda s: None, session=S())
    arts = c.feed("https://bev.example.com/feed", "BevNET")
    assert seen == [("https://bev.example.com/feed", None)]
    assert arts == [{"url": "https://bev.example.com/a", "title": "Brand launches & more", "domain": "BevNET", "date": now},
                    {"url": "https://bev.example.com/b", "title": "Dated by dc", "domain": "BevNET", "date": now}]
    with pytest.raises(rss.RssError) as e:      # 봇 차단(403)은 다시 시도하지 않음
        c.feed("https://blocked.example.com/rss")
    assert "403" in str(e.value) and len(seen) == 2


def test_noise_and_title_cleanup():
    assert rss.is_noise("Driver arrested after crash", ["arrest"]) and rss.is_noise("DUI checkpoint", ["DUI"])
    assert not rss.is_noise("Non-alcoholic beer sales rise", ["DUI", "crash"])
    assert rss.clean_title("Heineken warns on GLP-1 - Reuters", "Reuters") == "Heineken warns on GLP-1"


def test_surge_signal():
    now = datetime(2026, 10, 22, 12)
    dates = [now - timedelta(days=1)] * 6 + [now - timedelta(days=10)] * 2
    s = rss.surge(dates, now)
    assert s["surge"] is True and s["recent"] == 6 and s["previous"] == 2 and s["changePct"] == 200.0
    few = rss.surge([now - timedelta(days=1)] * 3, now)            # 5건 미만이면 급증 아님
    assert few["surge"] is False and few["changePct"] is None
    flat = rss.surge([now - timedelta(days=1)] * 5 + [now - timedelta(days=9)] * 5, now)
    assert flat["surge"] is False and flat["changePct"] == 0.0
    assert rss.surge([], now) is None


# ───────── 수집 ─────────

def test_requires_login(client):
    assert client.get("/api/home/news").status_code == 401
    assert client.get("/api/home/todos").status_code == 401
    assert client.post("/api/home/todos", json={"title": "x"}).status_code == 401
    assert client.post("/api/home/news/collect").status_code == 401
    assert client.get("/api/home/news/saved").status_code == 401


def test_saved_news(auth_client):
    assert auth_client.get("/api/home/news/saved").get_json()["items"] == []
    assert auth_client.post("/api/home/news/saved", json={"url": "javascript:alert(1)", "title": "x"}).status_code == 400
    assert auth_client.post("/api/home/news/saved", json={"url": "https://example.com/a"}).status_code == 400
    body = {"url": "https://example.com/a", "title": "FDA Finalizes NDI Guidance", "titleKo": "FDA NDI 지침 확정",
            "summaryKo": "요약", "whyKo": "성분 표기 다시 점검", "kind": "nope", "areaLabel": "규제", "domain": "BevNET",
            "date": "2026-10-20T13:00:00Z"}
    r = auth_client.post("/api/home/news/saved", json=body)
    s = r.get_json()
    assert r.status_code == 201 and s["kind"] == "flow" and s["date"] == "2026-10-20T13:00:00Z" and s["savedBy"]
    r = auth_client.post("/api/home/news/saved", json=dict(body, kind="trend", whyKo="고친 시선", date="bad"))
    assert r.status_code == 200 and r.get_json()["id"] == s["id"] and r.get_json()["date"] is None   # 같은 URL은 덮어씀
    items = auth_client.get("/api/home/news/saved").get_json()["items"]
    assert len(items) == 1 and items[0]["kind"] == "trend" and items[0]["whyKo"] == "고친 시선"
    assert auth_client.delete(f"/api/home/news/saved/{s['id']}").get_json()["ok"] is True
    assert auth_client.delete(f"/api/home/news/saved/{s['id']}").status_code == 404
    assert auth_client.get("/api/home/news/saved").get_json()["items"] == []


def test_news_before_first_collection(auth_client):
    j = auth_client.get("/api/home/news").get_json()
    assert j["status"] == "never" and j["articles"] == [] and j["ai"] is None and j["aiReady"] is False and j["issueNo"] == 0
    assert len(j["groups"]) >= 3 and "판매량" in j["limitNote"]
    assert len(j["queryStats"]) == 14 and not any(q["replace"] for q in j["queryStats"])   # 돌린 적 없으면 교체 후보 없음


def test_collect_stores_dedupes_and_signals(fast, auth_client, monkeypatch):
    calls = make_session(monkeypatch, good_responder)
    groups = [g["id"] for g in news.load_queries()["groups"]]
    expected = len({i["url"] for g in groups for i in valid_new(g)})
    with fast.app_context():
        run = news.collect(trigger="manual", user_name="테스터")
        assert run.status == "ok" and run.ai_status == "no_key"
        assert len(calls) == len(groups)                       # 검색어 · 피드마다 RSS 1번
        assert len([c for c in calls if "feed" in c]) == 6
        assert HomeNewsArticle.query.count() == expected        # 같은 기사 한 번만, 노이즈·javascript: 주소는 버림
        shared = HomeNewsArticle.query.filter_by(url="https://www.example-news.com/shared-story").one()
        assert len(shared.groups.split(",")) == len(groups) and shared.domain == "Example News"
        assert shared.title == "Non-alcoholic drinks surge before Halloween"   # " - 매체명" 뗌
        assert "&amp;" not in HomeNewsArticle.query.filter(HomeNewsArticle.url.like("%story-0")).first().title
        assert HomeNewsArticle.query.filter(HomeNewsArticle.title.like("%DUI%")).count() == 0
        counts = json.loads(run.counts)["category"]
        assert counts["dropped"] == {"incident": 1} and counts["kept"] == counts["found"] - 1   # DUI 기사는 규칙으로 버림
        again = news.collect(trigger="manual", user_name="테스터")
        assert sum(c["new"] for c in json.loads(again.counts).values()) == 0
    j = auth_client.get("/api/home/news").get_json()
    assert j["status"] == "ok" and j["running"] is False and j["ai"] is None and j["issueNo"] == 2   # 마켓 레터 호수 = 성공한 수집 횟수
    assert 0 < len(j["articles"]) <= 8 and all(a["url"].startswith("https://") for a in j["articles"])
    assert j["signals"][0]["group"] == "category" and j["signals"][0]["surge"] is True
    hang = next(q for q in j["queryStats"] if q["id"] == "category")
    assert hang["runs"] == 2 and hang["found"] > hang["kept"] > 0 and hang["picked"] == 0 and hang["replace"] is False
    assert not any(s["surge"] for s in j["signals"][1:])
    feed_ids = {g["id"] for g in news.load_queries()["groups"] if g.get("feed")}
    assert not feed_ids & {s["group"] for s in j["signals"]}             # 피드는 보도 급증 계산에서 뺌
    assert {q["id"]: q["feed"] for q in j["queryStats"]}["bevnet"].startswith("https://")
    logs = auth_client.get("/api/worklog?part=home&kind=수집").get_json()["items"]
    assert logs and logs[0]["status"] == "완료" and logs[0]["user"] == "테스터"


def test_failure_keeps_last_results(fast, auth_client, monkeypatch):
    make_session(monkeypatch, good_responder)
    with fast.app_context():
        news.collect(trigger="manual")
    calls = make_session(monkeypatch, lambda p: FakeRes(200, "<html>blocked"))
    with fast.app_context():
        run = news.collect(trigger="daily")
        assert run.status == "failed" and run.ai_status == "skipped"
        errs = json.loads(run.errors)
        assert any("RSS" in e for e in errs)
        assert len([c for c in calls if "q" in c]) == news.MAX_FAILS_IN_ROW and any("건너뜀" in e for e in errs)  # 연달아 실패하면 더 부르지 않음
        assert len([c for c in calls if "feed" in c]) == 6     # 업계지 피드는 사이트가 달라 따로 시도
    j = auth_client.get("/api/home/news").get_json()
    assert j["status"] == "failed" and j["lastSuccessAt"] and j["articles"]  # 마지막 결과 유지
    assert j["signals"]
    logs = auth_client.get("/api/worklog?part=home").get_json()["items"]
    assert logs[0]["status"] == "실패"


def test_partial_failure(fast, auth_client, monkeypatch):
    def responder(p):
        if group_of(p) == "nonalc":
            return FakeRes(503)
        return good_responder(p)

    make_session(monkeypatch, responder)
    with fast.app_context():
        run = news.collect()
        assert run.status == "partial"
    assert auth_client.get("/api/home/news").get_json()["status"] == "partial"


TREND_IDS = {"marketingdive", "modernretail", "mintel", "nosh"}


def test_vague_copy_filter(monkeypatch):
    assert news.vague("새로운 기회가 될 수 있다") and news.vague("전략을 세워야 한다")
    assert not news.vague("Liquid I.V.가 F1 협찬 — 스포츠 관람 시즌 채널 검토")
    monkeypatch.setattr(news, "STRICT_COPY", False)
    assert not news.vague("새로운 기회")


def test_ai_filter_and_korean_items(fast, auth_client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    make_session(monkeypatch, good_responder)
    seen = {}

    def fake_openai_json(system, user, **kw):
        if user["section"] == "trend":   # 트렌드는 따로 부름 (여기선 고를 것 없음)
            assert all(a["group"] in TREND_IDS for a in user["articles"])
            return {"relevant_ids": [], "headline": "", "articles": []}
        ids = [a["id"] for a in user["articles"]]
        hang = next(a["id"] for a in user["articles"] if a["title"].startswith("Hangover trend"))
        seen.update(system=system, user=user, kw=kw)
        return {"relevant_ids": ids[:3] + [99999], "headline": "핼러윈 앞두고 무알코올 보도가 늘었습니다.",
                "articles": [{"id": hang, "title_ko": "숙취 관련 보도 증가", "summary_ko": "숙취 관련 기사가 늘었습니다.", "topic": "소비 트렌드",
                              "why_ko": "숙취 시장에 큰 기회가 될 수 있다"},
                             {"id": hang, "title_ko": "중복", "summary_ko": "x", "topic": "업계 일반"},
                             {"id": 12345, "title_ko": "없는 기사", "summary_ko": "x", "topic": "판매 동향"}]}

    monkeypatch.setattr("tabs.home.news.openai_json", fake_openai_json)
    with fast.app_context():
        run = news.collect()
        assert run.ai_status == "ok"
        assert HomeNewsArticle.query.filter(HomeNewsArticle.relevant.is_(True)).count() == 4
    assert "본문" in seen["system"] and seen["kw"]["schema"]["additionalProperties"] is False
    assert seen["kw"]["part"] == "home" and "signals" not in seen["user"] and seen["user"]["articles"][0]["source"]   # 급증 수치는 AI에 안 줌
    j = auth_client.get("/api/home/news").get_json()
    assert j["aiReady"] is True and j["ai"]["headline"] == "업계: 핼러윈 앞두고 무알코올 보도가 늘었습니다."
    assert seen["user"]["articles"][0]["area"] and "why_ko" in seen["kw"]["schema"]["properties"]["articles"]["items"]["required"]
    items = j["ai"]["items"]
    assert len(items) == 4 and [i["fallback"] for i in items] == [False, True, True, True]  # 겹친 · 없는 기사는 버리고, 관련 기사로 채움
    assert all(i["titleKo"] == "" and i["kind"] != "trend" for i in items[1:])
    it = items[0]
    assert it["titleKo"] == "숙취 관련 보도 증가" and it["topic"] == "소비 트렌드" and it["surge"] is True
    assert it["whyKo"] == ""   # 막연한 문구('기회')는 버림
    assert it["url"].startswith("https://") and it["title"].startswith("Hangover trend") and it["domain"] == "Trend"
    assert all(a["relevant"] for a in j["articles"])
    with fast.app_context():
        b = news.brief()
    assert b["trends"][0]["title"] == "숙취 관련 보도 증가" and any(s["surge"] for s in b["signals"])


def test_feed_drops_old_articles_and_ai_trend_slot(fast, auth_client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    def responder(p):
        if p.get("feed", "").startswith("https://www.marketingdive.com"):
            now = _now()
            return FakeRes(body=rss_xml([{"url": "https://md.example.com/tiktok-shop", "title": "Brands bet on TikTok Shop live sales",
                                          "source": "x", "date": now - timedelta(days=1)},
                                         {"url": "https://md.example.com/old", "title": "Old campaign recap", "source": "x",
                                          "date": now - timedelta(days=40)}]))
        return good_responder(p)

    make_session(monkeypatch, responder)

    def fake_openai_json(system, user, **kw):
        if user["section"] == "news":
            return {"relevant_ids": [], "headline": "", "articles": []}
        tid = next(a["id"] for a in user["articles"] if a["title"].startswith("Brands bet"))
        return {"relevant_ids": [tid], "headline": "h",
                "articles": [{"id": tid, "title_en": "Brands bet on TikTok Shop live sales", "title_ko": "틱톡샵 라이브 판매 확대",
                              "summary_ko": "s", "why_ko": "런칭 채널에 라이브 판매 검토", "area": "trend", "topic": "소비 트렌드",
                              "distance": 1, "action": 2, "source": 1, "novelty": 1}]}

    monkeypatch.setattr("tabs.home.news.openai_json", fake_openai_json)
    with fast.app_context():
        run = news.collect()
        assert json.loads(run.counts)["marketingdive"]["found"] == 1        # 14일 넘은 피드 기사는 버림
        md = HomeNewsArticle.query.filter_by(url="https://md.example.com/tiktok-shop").one()
        assert md.domain == "Marketing Dive" and md.group_id == "marketingdive"
        assert HomeNewsArticle.query.filter_by(url="https://md.example.com/old").count() == 0
    it = auth_client.get("/api/home/news").get_json()["ai"]["items"][0]
    assert it["kind"] == "trend" and it["areaLabel"] == "트렌드" and it["whyKo"].startswith("런칭")


def test_ai_error_keeps_articles(fast, auth_client, monkeypatch):
    from server.core.ai import AIError
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    make_session(monkeypatch, good_responder)

    def boom(*a, **k):
        raise AIError("AI 서버 오류 (500). 잠시 뒤 다시 시도하세요.")

    monkeypatch.setattr("tabs.home.news.openai_json", boom)
    with fast.app_context():
        run = news.collect()
        assert run.status == "ok" and run.ai_status == "error"
    j = auth_client.get("/api/home/news").get_json()
    assert j["aiStatus"] == "error" and "AI 서버 오류" in j["aiError"] and j["articles"]


def test_collect_endpoint_runs_in_background(fast, auth_client, monkeypatch):
    make_session(monkeypatch, good_responder)
    res = auth_client.post("/api/home/news/collect")
    assert res.status_code == 202 and res.get_json()["started"] is True
    for t in threading.enumerate():
        if t.name == "job-home-news":
            t.join(20)
    s = auth_client.get("/api/home/news/status").get_json()
    assert s["running"] is False and s["lastRun"]["status"] == "ok" and s["lastRun"]["trigger"] == "manual"
    # 수집 중이면 409
    with fast.app_context():
        news.start_run("manual", "다른 사람")
    assert auth_client.post("/api/home/news/collect").status_code == 409
    assert auth_client.get("/api/home/news").get_json()["status"] == "running"


def test_busy_collection_is_skipped_without_record(fast, auth_client, monkeypatch):
    from server import jobs
    make_session(monkeypatch, good_responder)
    with fast.app_context():
        pre = news.start_run("manual", "테스터")
        with jobs.file_lock("home-news-collect"):
            assert news.collect(trigger="daily") is None          # 다른 수집 중 → 건너뜀
            assert news.collect(run_id=pre.id) is None
        assert HomeNewsRun.query.count() == 0                       # 실패 기록을 남기지 않음


def test_server_error_marks_run_failed(fast, auth_client, monkeypatch):
    make_session(monkeypatch, good_responder)
    monkeypatch.setattr("tabs.home.news.store_articles", lambda *a: 1 / 0)
    with fast.app_context():
        run = news.collect(trigger="manual", user_name="테스터")
        assert run.status == "failed" and "서버 오류" in run.errors
    assert auth_client.get("/api/home/news").get_json()["status"] == "failed"


def test_stale_running_run_counts_as_failed(app, auth_client):
    with app.app_context():
        run = news.start_run("manual", "x")
        run.started_at = run.started_at - timedelta(hours=2)
        db.session.commit()
    j = auth_client.get("/api/home/news").get_json()
    assert j["status"] == "failed" and j["running"] is False


# ───────── To-Do ─────────

CTX = {"cs": {"open": 5, "urgent": 1, "overdue": 2, "owner_required": 0}, "compliance": {"pending": 2},
       "performance": {"start": "2026-10-15", "end": "2026-10-21", "revenue": 26632200, "spend": 7295000,
                       "roas": 365.07, "conversions": 875, "change": {"revenue": 2.6, "roas": 3.18}, "sample": True},
       "evil": {"x": "<script>"}}


def test_rule_todos_without_key(auth_client, today):
    j = auth_client.get("/api/home/todos").get_json()
    assert j["week"] == {"start": "2026-10-19", "end": "2026-10-25"} and j["items"] == [] and j["generated"] is False
    j = auth_client.post("/api/home/todos/generate", json={"auto": True, "context": CTX}).get_json()
    assert j["skipped"] is False and j["origin"] == "rule" and j["generated"] is True
    titles = [i["title"] for i in j["items"]]
    by = {i["title"]: i for i in j["items"]}
    assert "Halloween 캠페인 소재·카피 준비" in titles
    assert "D-8" in by["Halloween 캠페인 소재·카피 준비"]["reason"]          # 근거
    assert any("Thanksgiving Eve 준비 일정 잡기" == t for t in titles)       # 준비 시작이 2주 안
    assert by["미처리 CS 문의 5건 답변"]["source"] == "cs" and "긴급 1" in by["미처리 CS 문의 5건 답변"]["reason"]
    assert "검토 대기 카피 2건 확인" in titles and "실제 성과 CSV 올리기" in titles
    assert all(i["origin"] == "rule" and i["ai"] is False and i["reason"] for i in j["items"])
    assert j["open"] == len(j["items"])
    # 같은 주 자동 생성은 한 번만
    again = auth_client.post("/api/home/todos/generate", json={"auto": True, "context": {}}).get_json()
    assert again["skipped"] is True and [i["id"] for i in again["items"]] == [i["id"] for i in j["items"]]


def test_regenerate_keeps_manual_and_done(auth_client, today):
    j = auth_client.post("/api/home/todos/generate", json={"auto": True, "context": CTX}).get_json()
    first = j["items"][0]
    assert auth_client.patch(f"/api/home/todos/{first['id']}", json={"done": True}).get_json()["done"] is True
    mine = auth_client.post("/api/home/todos", json={"title": "파트너 미팅 자료 준비", "reason": "목요일 미팅"})
    assert mine.status_code == 201 and mine.get_json()["origin"] == "manual"
    j2 = auth_client.post("/api/home/todos/generate", json={"auto": False, "context": {}}).get_json()
    titles = [i["title"] for i in j2["items"]]
    assert "파트너 미팅 자료 준비" in titles
    assert titles.count(first["title"]) == 1                     # 완료 항목은 남고 중복으로 다시 생기지 않음
    assert "미처리 CS 문의 5건 답변" not in titles                # 현황이 없으니 빠짐
    done = [i for i in j2["items"] if i["done"]]
    assert done and done[0]["id"] == first["id"] and done[0]["doneBy"] == "테스터"
    assert j2["items"][-1]["done"] is True                       # 완료 항목은 아래로


def test_todo_crud_and_validation(auth_client, today):
    assert auth_client.post("/api/home/todos", json={"title": "  "}).status_code == 400
    assert auth_client.post("/api/home/todos", data="x", content_type="text/plain").status_code == 415
    t = auth_client.post("/api/home/todos", json={"title": "<b>굵게</b> 할 일", "source": "nope"}).get_json()
    assert t["source"] == "home" and t["reason"] == "직접 추가" and t["weekStart"] == "2026-10-19"
    assert auth_client.patch(f"/api/home/todos/{t['id']}", json={"title": ""}).status_code == 400
    u = auth_client.patch(f"/api/home/todos/{t['id']}", json={"title": "고친 할 일", "reason": "근거", "done": True}).get_json()
    assert u["title"] == "고친 할 일" and u["done"] is True and u["doneAt"]
    u = auth_client.patch(f"/api/home/todos/{t['id']}", json={"done": False}).get_json()
    assert u["done"] is False and u["doneAt"] is None and u["doneBy"] == ""
    assert auth_client.delete(f"/api/home/todos/{t['id']}").get_json()["ok"] is True
    assert auth_client.delete(f"/api/home/todos/{t['id']}").status_code == 404
    assert auth_client.patch("/api/home/todos/999", json={"done": True}).status_code == 404
    assert auth_client.get("/api/home/todos?week=bad").status_code == 400
    assert auth_client.get("/api/home/todos?week=2026-10-21").get_json()["week"]["start"] == "2026-10-19"


def test_ai_todos(auth_client, today, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    seen = {}

    def fake(system, user, **kw):
        seen.update(user=user, kw=kw)
        return {"items": [{"title": "핼러윈 릴스 2편 게시", "reason": "Halloween D-8", "source": "content"},
                          {"title": "이상한 출처", "reason": "x", "source": "hacker"},
                          {"title": "", "reason": "빈 제목", "source": "home"}]}

    monkeypatch.setattr("tabs.home.todos.openai_json", fake)
    j = auth_client.post("/api/home/todos/generate", json={"auto": True, "context": CTX}).get_json()
    assert j["origin"] == "ai" and [i["title"] for i in j["items"]] == ["핼러윈 릴스 2편 게시", "이상한 출처"]
    assert j["items"][1]["source"] == "home" and all(i["ai"] for i in j["items"])
    u = seen["user"]
    assert u["week"] == {"start": "2026-10-19", "end": "2026-10-25"}
    assert any(s["name"] == "Halloween" and s["inLead"] for s in u["seasons"])
    assert u["parts"]["cs"]["open"] == 5 and "evil" not in u["parts"]
    assert seen["kw"]["schema"]["properties"]["items"]["items"]["properties"]["source"]["enum"]


def test_ai_todo_failure_falls_back_to_rules(auth_client, today, monkeypatch):
    from server.core.ai import AIError
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    def boom(*a, **k):
        raise AIError("요청이 많거나 사용 한도를 넘었습니다.", 429)

    monkeypatch.setattr("tabs.home.todos.openai_json", boom)
    j = auth_client.post("/api/home/todos/generate", json={"auto": False, "context": CTX}).get_json()
    assert j["origin"] == "rule" and "규칙으로" in j["warning"] and j["items"]


def test_daily_job_collects_then_generates(fast, monkeypatch, today):
    make_session(monkeypatch, good_responder)
    with fast.app_context():
        routes.daily_briefing()
        assert HomeNewsRun.query.one().trigger == "daily"
        payload = todos.week_payload()
        assert payload["generated"] is True and payload["items"]
        # 급증 시그널이 규칙 To-Do 근거로 들어감
        assert any(i["source"] == "home" and "보도량" in i["reason"] for i in payload["items"])


def test_home_page_loads_part_files(auth_client):
    html = auth_client.get("/dashboard").get_data(as_text=True)
    assert "/parts/home/home.js" in html and "/parts/home/home.css" in html
    assert html.index("/parts/performance/metrics.js") < html.index("/parts/performance/performance.js")
    for f in ("home/home.js", "home/home.css", "performance/metrics.js", "performance/performance.js", "performance/performance.css"):
        assert auth_client.get(f"/parts/{f}").status_code == 200, f
