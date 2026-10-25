"""콘텐츠 제작 API — 인터넷·키 없이 (AI·이미지 호출은 tabs.content.providers를 바꿔 끼움)."""
import base64
import json
import io
import re
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from tabs.content import assets, brandkit, conversation, drafts, heuristics, higgsfield, media, providers, repository, review
from tabs.content import options as O

from conftest import PASSWORD

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 16
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
ROOT = Path(__file__).resolve().parents[3]  # 저장소 맨 위


# ───────── 도우미 ─────────

def sheet(**kw):
    s = {"content_type": "image", "channels": ["instagram"], "brand_focus": True, "product_ids": []}
    s.update(kw)
    return s


def create(client, **kw):
    res = client.post("/api/content/campaigns", json=sheet(**kw))
    assert res.status_code == 201, res.get_json()
    return res.get_json()["campaign"]


def get(client, cid):
    return client.get(f"/api/content/campaigns/{cid}").get_json()["campaign"]


def turn(text="네, 진행할게요.", intent="other", patch=None, cands=None, qs=None, action="none", target=None, instr=""):
    return {"intent": intent, "assistant_text": text, "decision_patch": patch or [], "candidates": cands or [],
            "clarification_questions": qs or [], "action_request": {"type": action, "target_version_id": target,
                                                                    "instructions": instr}}


def draft(body, approach="접근", claims=None):
    return {"approach": approach, "title": "제목", "body": body, "claims": claims or [], "needs_check": []}


def chat(client, camp, message, **kw):
    body = {"message": message, "expected_revision": camp["revision"]}
    body.update(kw)
    return client.post(f"/api/content/campaigns/{camp['id']}/chat", json=body)


class FakeAI:
    """providers.call_json 대역: 작업 이름별로 미리 넣어 둔 응답을 차례로 돌려줌."""

    def __init__(self):
        self.queue = {"content_turn": [], "content_draft": [], "content_image_plan": [], "content_suggest": [],
                      "content_review": [], "content_interview": [], "content_video_plan": [], "content_reference": [],
                      "content_caption": []}
        self.calls = []

    def add(self, name, *items):
        self.queue[name].extend(items)

    def __call__(self, system, user, *, schema, name, action, max_tokens=3000, timeout=120, images=0):
        if name == "content_caption" and not self.queue[name]:
            # 생성 직후 자동 캡션 추천 — 응답을 넣지 않은 테스트는 'AI 실패'로 (기록하지 않아 calls[-1]이 그대로)
            raise providers.AIError("테스트: 캡션 응답 없음")
        self.calls.append({"name": name, "system": system, "user": user, "action": action, "images": images})
        if not self.queue.get(name):
            raise AssertionError(f"예상하지 못한 AI 호출: {name}")
        return self.queue[name].pop(0)

    def with_images(self, system, user, images, *, schema, name, action, max_tokens=3000, timeout=90):
        """providers.call_json_images 대역 — 받은 이미지 수를 기록."""
        assert all(isinstance(b, bytes) and mime.startswith("image/") for b, mime in images)
        return self(system, user, schema=schema, name=name, action=action, images=len(images))

    def ctx(self, i=-1):
        u = self.calls[i]["user"]
        return u if isinstance(u, dict) else json.loads(u)

    def names(self):
        return [c["name"] for c in self.calls]


@pytest.fixture()
def ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(providers, "ready", lambda: True)
    monkeypatch.setattr(providers, "call_json", fake)
    monkeypatch.setattr(providers, "call_json_images", fake.with_images)
    return fake


@pytest.fixture()
def sync_jobs(monkeypatch):
    """이미지·영상 작업을 요청 안에서 바로 실행 (스레드 없이 · 상태 확인 대기 없음)."""
    monkeypatch.setattr(media, "launch", lambda app, job_id: media.run_job(job_id))
    monkeypatch.setattr(media, "POLL_EVERY", 0)


class FakeHiggsfield:
    """higgsfield 모듈 대역: 올린 사진 · 제출 본문 · 상태 순서를 기록하고 결과 파일을 돌려줌 (인터넷·키·비용 없음)."""

    def __init__(self, monkeypatch, result=MP4, statuses=("in_progress", "completed"), error=""):
        self.uploads, self.submits, self.result, self.error = [], [], result, error
        self.statuses = list(statuses)
        monkeypatch.setattr(higgsfield, "ready", lambda: True)
        monkeypatch.setattr(higgsfield, "upload_ref", self.upload_ref)
        monkeypatch.setattr(higgsfield, "submit", self.submit)
        monkeypatch.setattr(higgsfield, "status", self.status)
        monkeypatch.setattr(higgsfield, "download", lambda url: self.result)

    def upload_ref(self, path, content_type):
        self.uploads.append((Path(path).name, content_type))
        return f"https://files.example/{Path(path).name}"

    def submit(self, model_path, body):
        self.submits.append((model_path, body))
        return "11111111-2222-3333-4444-555555555555"

    def status(self, request_id):
        s = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"status": s, "url": "https://cdn.example/out" if s == "completed" else None, "error": self.error}


def set_settings(client, camp, **settings):
    res = client.patch(f"/api/content/campaigns/{camp['id']}", json={"expected_revision": camp["revision"], "settings": settings})
    assert res.status_code == 200, res.get_json()
    return res.get_json()["campaign"]


# ───────── 권한 · 화면 파일 ─────────

def test_api_requires_login(client):
    assert client.get("/api/content/campaigns").status_code == 401
    assert client.post("/api/content/campaigns", json=sheet()).status_code == 401
    assert client.get("/api/content/files/c1-abcdef123456-0000aaaa.png").status_code == 401


def test_content_page_loads_data_then_main(auth_client):
    html = auth_client.get("/dashboard/content").get_data(as_text=True)
    assert html.index("/parts/content/data.js") < html.index("/parts/content/content.js")
    assert "/parts/content/content.css" in html


def test_data_js_ids_match_server_options():
    text = (ROOT / "tabs" / "content" / "static" / "data.js").read_text(encoding="utf-8")
    for key in list(O.CONTENT_TYPES) + list(O.CHANNELS) + list(O.STATUSES):
        assert f"'{key}'" in text, key
    cmp_map = re.search(r"complianceChannel: \{([^}]*)\}", text).group(1)
    for ch in O.CHANNELS:  # 채널마다 컴플라이언스 규칙 묶음 연결이 있어야 함 (없으면 b2c로 조용히 검사)
        assert re.search(rf"\b{ch}: '(b2c|amazon)'", cmp_map), ch
    for retired in O.RETIRED_TYPES:
        assert f"'{retired}'" not in text, retired


# ───────── 캠페인 생성 · 목록 ─────────

def test_create_brand_only_and_defaults(auth_client):
    camp = create(auth_client)
    assert re.fullmatch(r"인스타 이미지 · \d\d\.\d\d", camp["title"])
    assert camp["revision"] == 1 and camp["status"] == "제작 중"
    assert camp["sheet"]["brand_focus"] is True and camp["sheet"]["product_ids"] == []
    assert camp["decisions"]["language"]["status"] == "proposed"  # 영어는 초기 제안 (확정 아님)
    assert camp["messages"][0]["role"] == "event" and "Reboot 브랜드 자체" in camp["messages"][0]["text"]
    assert camp["facts"]["products"] == [] and camp["facts"]["brand"]["slogan"] == "Restart Your Day"


def test_create_sheet_language_confirmed(auth_client):
    ko = create(auth_client, language="한국어")["decisions"]["language"]
    assert ko["value"] == "한국어" and ko["status"] == "confirmed"  # 시트에서 고르면 확정 → 대화에서 다시 안 물음
    en = create(auth_client, language="영어")["decisions"]["language"]
    assert en["value"] == "미국 영어 (US English)" and en["status"] == "confirmed"
    assert create(auth_client, language="일본어")["decisions"]["language"]["status"] == "proposed"  # 모르는 값 → 기본 제안


def test_create_validation(auth_client):
    def err(**kw):
        res = auth_client.post("/api/content/campaigns", json=kw)
        assert res.status_code == 400, res.get_json()
        return res.get_json()["error"]
    assert "콘텐츠" in err(channels=["instagram"], brand_focus=True)
    assert "채널" in err(content_type="image", brand_focus=True)
    assert "대상" in err(content_type="image", channels=["instagram"])
    assert "대상" in err(content_type="image", channels=["instagram"], brand_focus=False, product_ids=[])
    assert "알 수 없는 제품" in err(content_type="image", channels=["instagram"], product_ids=["jelly_original", "fake"])
    assert "하나만" in err(content_type="image", channels=["instagram", "tiktok"], brand_focus=True)
    assert "채널" in err(content_type="image", channels=["myspace"], brand_focus=True)
    # 상품 설명 · 기획서는 없앤 종류 (기획서도 채널 하나)
    assert "알 수 없는 콘텐츠" in err(content_type="product_desc", channels=["amazon"], brand_focus=True)
    assert "알 수 없는 콘텐츠" in err(content_type="plan", channels=["instagram"], brand_focus=True)
    # CRM 메시지 ↔ CRM 채널끼리만
    assert "CRM" in err(content_type="crm", channels=["instagram"], brand_focus=True)
    assert "CRM" in err(content_type="image", channels=["crm"], brand_focus=True)
    oy = create(auth_client, content_type="cardnews", channels=["oliveyoung"])
    assert oy["sheet"]["channels"] == ["oliveyoung"] and oy["labels"]["channels"] == ["Global Olive Young"]
    assert oy["title"].startswith("올리브영 카드뉴스")
    assert create(auth_client, channels=["yamibuy"])["labels"]["channels"] == ["Yami"]  # ID는 그대로 · 표시만 Yami
    multi = create(auth_client, brand_focus=False, product_ids=["drink_balance", "jelly_original"])
    assert multi["sheet"]["product_ids"] == ["jelly_original", "drink_balance"]  # 표준 순서 · 브랜드와 분리
    assert [p["entity_id"] for p in multi["facts"]["products"]] == ["jelly_original", "drink_balance"]
    assert multi["labels"]["target"] == "Reboot Jelly Original + Reboot Drink Balance"


def test_list_search_status_and_counts(auth_client):
    a = create(auth_client)
    b = create(auth_client, channels=["amazon"], content_type="image", brand_focus=False, product_ids=["jelly_balance"])
    create(auth_client, content_type="crm", channels=["crm"])
    res = auth_client.patch(f"/api/content/campaigns/{b['id']}", json={"expected_revision": 1, "status": "완료"})
    assert res.status_code == 200
    data = auth_client.get("/api/content/campaigns").get_json()
    assert data["total"] == 3 and data["counts"] == {"제작 중": 2, "검토 중": 0, "완료": 1} and data["active"] == 2
    assert {"id", "title", "status", "updatedAt", "contentTypeLabel", "channelLabels", "targetLabel"} <= set(data["items"][0])
    assert [i["id"] for i in auth_client.get("/api/content/campaigns?status=완료").get_json()["items"]] == [b["id"]]
    assert [i["id"] for i in auth_client.get("/api/content/campaigns?q=아마존").get_json()["items"]] == [b["id"]]
    assert [i["id"] for i in auth_client.get("/api/content/campaigns?q=저당 젤리").get_json()["items"]] == [b["id"]]
    assert a["id"] in [i["id"] for i in auth_client.get("/api/content/campaigns?q=브랜드").get_json()["items"]]


def test_retired_types_hidden_not_deleted(app, auth_client):
    """예전에 만든 상품 설명 · 기획서 캠페인은 DB에 남기고 목록 · 열기에서만 숨김."""
    from server.extensions import db
    from tabs.content.models import ContentCampaign
    keep = create(auth_client)
    with app.app_context():
        old = []
        for ct, chs in (("product_desc", ["amazon"]), ("plan", ["instagram", "tiktok"])):
            c = ContentCampaign(title=f"옛 {ct}", content_type=ct, brand_focus=True, status="제작 중", revision=1,
                                created_by="t", updated_by="t")
            c.channels, c.product_ids, c.settings, c.decisions = chs, [], {}, {}
            db.session.add(c)
            db.session.flush()
            old.append(c.id)
        db.session.commit()
    data = auth_client.get("/api/content/campaigns").get_json()
    assert [i["id"] for i in data["items"]] == [keep["id"]] and data["total"] == 1
    for cid in old:
        assert auth_client.get(f"/api/content/campaigns/{cid}").status_code == 404
    with app.app_context():
        assert ContentCampaign.query.filter(ContentCampaign.id.in_(old)).count() == 2  # 지우지 않음


# ───────── 시트 수정 · 리비전 ─────────

def test_patch_revision_conflict_and_sheet_rules(auth_client):
    camp = create(auth_client)
    cid = camp["id"]
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={"expected_revision": 7, "title": "새 이름"})
    assert res.status_code == 409 and res.get_json()["conflict"] and res.get_json()["revision"] == 1
    assert res.get_json()["campaign"]["title"] == camp["title"]  # 조용히 덮어쓰지 않음
    assert auth_client.patch(f"/api/content/campaigns/{cid}", json={"title": "x"}).status_code == 400  # 리비전 없음
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={
        "expected_revision": 1, "sheet": {"product_ids": ["jelly_original", "drink_original"], "goal": "제품 소개"}})
    assert res.status_code == 200
    c = res.get_json()["campaign"]
    assert c["revision"] == 2 and c["sheet"]["goal"] == "제품 소개"
    assert c["messages"][-1]["role"] == "event" and "시트" in c["messages"][-1]["text"]
    assert {e["field"] for e in c["recentChanges"]} >= {"sheet.product_ids", "sheet.goal"}
    # 대상이 모두 빠지면 거절 (리비전 그대로)
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={"expected_revision": 2, "sheet": {"brand_focus": False, "product_ids": []}})
    assert res.status_code == 400 and "대상" in res.get_json()["error"]
    assert get(auth_client, cid)["revision"] == 2
    # 콘텐츠만 CRM으로 바꾸면 거절 · CRM ↔ 이미지(채널 함께)는 한 번에 바꿔도 순서와 상관없이 검사
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={"expected_revision": 2, "sheet": {"content_type": "crm"}})
    assert res.status_code == 400 and "CRM" in res.get_json()["error"]
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={"expected_revision": 2, "sheet": {"content_type": "crm", "channels": ["crm"]}})
    assert res.status_code == 200
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={"expected_revision": 3, "sheet": {"content_type": "image", "channels": ["tiktok"]}})
    assert res.status_code == 200 and res.get_json()["campaign"]["sheet"]["channels"] == ["tiktok"]
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={"expected_revision": 4, "settings": {"candidates": 9}})
    assert res.status_code == 400
    res = auth_client.patch(f"/api/content/campaigns/{cid}", json={"expected_revision": 4, "settings": {"candidates": 2}, "status": "검토 중"})
    assert res.status_code == 200 and res.get_json()["campaign"]["settings"]["candidates"] == 2


def test_guarded_bump_detects_concurrent_write(app, auth_client):
    camp = create(auth_client)
    with app.app_context():
        with pytest.raises(repository.Conflict):
            repository.guarded_bump(camp["id"], 0)


def test_decisions_candidate_manual_undo(auth_client):
    camp = create(auth_client)
    cid = camp["id"]
    url = f"/api/content/campaigns/{cid}/decisions"
    res = auth_client.post(url, json={"expected_revision": 1, "source": "candidate",
                                      "changes": [{"field": "decisions.concept", "value": "After-party ritual", "status": "confirmed"}]})
    assert res.status_code == 200
    c = res.get_json()["campaign"]
    assert c["decisions"]["concept"]["status"] == "confirmed" and c["decisions"]["concept"]["source"] == "candidate"
    assert c["revision"] == 2 and "후보를 골랐어요" in c["messages"][-1]["text"]
    # 제품 사실은 결정 항목으로 바꿀 수 없음
    res = auth_client.post(url, json={"expected_revision": 2, "changes": [{"field": "decisions.price", "value": "$3"}]})
    assert res.status_code == 400 and "확인 대기" in res.get_json()["error"]
    res = auth_client.post(url, json={"expected_revision": 2, "changes": [{"field": "owner", "value": "x"}]})
    assert res.status_code == 400
    # 되돌리기 = 미정으로
    res = auth_client.post(url, json={"expected_revision": 2, "source": "undo",
                                      "changes": [{"field": "decisions.concept", "value": None, "status": "undecided"}]})
    assert res.status_code == 200 and "concept" not in res.get_json()["campaign"]["decisions"]
    assert auth_client.post(url, json={"expected_revision": 1, "changes": [{"field": "decisions.tone", "value": "밝게"}]}).status_code == 409


# ───────── 대화 ─────────

def test_chat_without_key_is_503_and_keeps_state(auth_client):
    camp = create(auth_client)
    res = chat(auth_client, camp, "검정 배경으로 해줘")
    assert res.status_code == 503 and "설정 › 연결" in res.get_json()["error"]
    after = get(auth_client, camp["id"])
    assert after["revision"] == 1 and len(after["messages"]) == 1


def test_chat_applies_valid_patches_and_filters_invalid(auth_client, ai):
    camp = create(auth_client)
    ai.add("content_turn", turn(
        text="배경은 검정으로 정리할게요.", intent="update_decision",
        patch=[{"field": "decisions.background", "value": "검정", "status": "confirmed"},
               {"field": "decisions.concept", "value": "Neon morning", "status": "proposed"},
               {"field": "sheet.product_ids", "value": ["jelly_original", "fake_product"], "status": "confirmed"},
               {"field": "facts.price", "value": "$3.99", "status": "confirmed"},
               {"field": "decisions.ingredients", "value": "vitamin C", "status": "confirmed"},
               {"field": "owner", "value": "someone", "status": "confirmed"}],
        qs=["목적은 무엇인가요?", "어떤 채널에 올릴까요?", "두 제품 중 무엇을 크게 보여 줄까요?"]))
    res = chat(auth_client, camp, "배경은 검정으로 해줘")
    assert res.status_code == 200, res.get_json()
    out = res.get_json()
    pay = out["reply"]["payload"]
    assert [a["field"] for a in pay["applied"]] == ["decisions.background", "decisions.concept"]
    rejected = {r["field"] for r in pay["rejected"]}
    assert {"sheet.product_ids", "facts.price", "decisions.ingredients", "owner"} <= rejected
    assert {f["field"] for f in pay["factRequests"]} == {"facts.price", "decisions.ingredients"}
    c = out["campaign"]
    assert c["revision"] == 2 and c["sheet"]["product_ids"] == []
    bg = c["decisions"]["background"]
    assert bg["status"] == "confirmed" and bg["source"] == "chat" and bg["messageId"] == out["userMessage"]["id"]
    assert c["decisions"]["concept"]["status"] == "proposed"
    # 시트에 이미 있는 채널을 다시 묻지 않음 (목적은 비어 있어 질문 허용) · 최대 2개
    assert "어떤 채널에 올릴까요?" not in pay["questions"] and len(pay["questions"]) <= 2
    assert [m["role"] for m in c["messages"]][-2:] == ["user", "assistant"]
    assert any(e["field"] == "decisions.background" and e["source"] == "chat" for e in c["recentChanges"])
    # 시트에 목적이 있으면 목적 질문은 버림
    assert conversation.filter_questions(["목적은 무엇인가요?"], dict(c["sheet"], goal="구매 유도")) == []


def test_chat_sheet_change_from_chat_updates_sheet(auth_client, ai):
    camp = create(auth_client, brand_focus=False, product_ids=["jelly_original", "drink_original"])
    ai.add("content_turn", turn(intent="update_decision", text="젤리만 남길게요.",
                                patch=[{"field": "sheet.product_ids", "value": ["jelly_original"], "status": "confirmed"}]))
    out = chat(auth_client, camp, "음료는 빼고 젤리만").get_json()
    c = out["campaign"]
    assert c["sheet"]["product_ids"] == ["jelly_original"]
    assert [p["entity_id"] for p in c["facts"]["products"]] == ["jelly_original"]  # 다음 호출엔 최신 대상만
    assert out["reply"]["payload"]["action"]["status"] == "none" and c["versions"] == []


def test_chat_vague_ack_does_not_confirm_or_generate(auth_client, ai):
    camp = create(auth_client)
    ai.add("content_turn", turn(
        text="좋아요! 시안을 만들었어요. 다음엔 톤을 정해요.", intent="draft", action="draft_copy",
        patch=[{"field": "decisions.concept", "value": "Neon morning", "status": "confirmed"},
               {"field": "sheet.goal", "value": "구매 유도", "status": "confirmed"}]))
    out = chat(auth_client, camp, "좋네").get_json()
    assert "content_draft" not in ai.names()
    c = out["campaign"]
    assert c["versions"] == [] and c["decisions"]["concept"]["status"] == "proposed" and c["sheet"]["goal"] == ""
    pay = out["reply"]["payload"]
    assert pay["action"]["status"] == "skipped"
    assert any(cd["field"] == "sheet.goal" for cd in pay["candidates"])  # 시트 변경은 후보로만
    assert "만들었어요" not in out["reply"]["text"] and "톤을 정해요" in out["reply"]["text"]


def test_chat_question_and_excluded_media_do_not_run(auth_client, ai, sync_jobs):
    camp = create(auth_client)
    ai.add("content_turn", turn(intent="question", action="draft_copy", text="검정 배경이 네온과 대비가 커서 그래요."))
    out = chat(auth_client, camp, "왜 이 구도야?").get_json()
    assert out["reply"]["payload"]["action"]["status"] == "skipped" and out["campaign"]["versions"] == []
    camp = out["campaign"]
    ai.add("content_turn", turn(intent="generate_media", action="generate_image", text="이미지도 만들어 볼게요."))
    out = chat(auth_client, camp, "일단 이미지는 말고 카피만 만들어줘").get_json()
    assert out["job"] is None and out["campaign"]["jobs"] == []
    assert "이미지는 만들지 않았어요" in out["reply"]["payload"]["action"]["note"]
    assert "content_draft" not in ai.names()


def test_video_request_becomes_plan_only_script(auth_client, ai):
    camp = create(auth_client, content_type="shortform", channels=["tiktok"])
    ai.add("content_turn", turn(intent="generate_media", action="generate_video", text="15초 영상으로 가 볼게요."))
    ai.add("content_draft", {"note": "", "drafts": [draft("| 장면 | 시간 |\n| --- | --- |\n| SC1 | 0-3s |\n")]})
    out = chat(auth_client, camp, "숏폼 영상 만들어줘").get_json()
    act = out["reply"]["payload"]["action"]
    assert act["type"] == "draft_script" and "AI 설정에서 '실제 영상'" in act["note"] and out["job"] is None
    v = out["campaign"]["versions"][0]
    assert v["kind"] == "script" and v["kindLabel"] == "숏폼 구성안" and "실제 영상 아님" in v["note"]


def test_chat_explicit_creation_makes_three_versions_with_review(auth_client, ai):
    camp = create(auth_client, brand_focus=False, product_ids=["jelly_original"])
    ai.add("content_turn", turn(intent="draft", action="draft_copy", text="카피 세 가지 접근으로 쓸게요."))
    ai.add("content_draft", {"note": "", "drafts": [
        draft("**Headline** Restart Your Day.\n**Caption** Stick jelly for your morning.", "아침 루틴",
              [{"text": "stick jelly", "entity_id": "jelly_original", "fact_id": "form"}]),
        draft("**Headline** Low sugar, big energy.", "당 강조"),
        draft("**Headline** Six bottles of fresh start.", "구성",
              [{"text": "6 bottles", "entity_id": "drink_original", "fact_id": "pack"}]),
    ]})
    ai.add("content_draft", {"note": "", "drafts": [draft("**Headline** A neon start for your morning.", "당 강조 → 분위기")]})
    out = chat(auth_client, camp, "카피 바로 만들어줘").get_json()
    c = out["campaign"]
    assert ai.names() == ["content_turn", "content_draft", "content_draft"]  # 초안 + 자동 재작성 1회
    vs = sorted(c["versions"], key=lambda v: v["id"])
    assert [v["label"] for v in vs] == ["A안", "B안", "C안"] and {v["kind"] for v in vs} == {"copy"}
    assert c["selectedVersionId"] == vs[0]["id"] and out["newVersionIds"] == [v["id"] for v in vs]
    assert vs[0]["review"]["keyword"]["status"] == "clear"
    assert vs[1]["review"]["keyword"]["status"] != "error" and "재작성" in vs[1]["note"]
    assert any(i["code"] == "claims" for i in vs[2]["review"]["keyword"]["items"])  # 고르지 않은 제품 근거
    assert all(v["factsVersion"].startswith("user-2026-10-23") and not v["stale"] for v in vs)
    reminder = ai.calls[1]["user"]
    assert "근거 리마인더" in reminder and "Restart Your Day" in ai.calls[1]["system"]
    assert out["reply"]["payload"]["action"]["versionLabels"] == ["A안 (v1)", "B안 (v2)", "C안 (v3)"]


def _manual(client, cid, kind, body, label, parent_id=None):
    res = client.post(f"/api/content/campaigns/{cid}/versions",
                      json={"kind": kind, "body": body, "label": label, "parent_id": parent_id})
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def test_chat_edit_only_the_named_version(auth_client, ai):
    camp = create(auth_client)
    for lab in ("A안", "B안", "C안"):
        _manual(auth_client, camp["id"], "copy", f"{lab} copy body that is long", lab)
    camp = get(auth_client, camp["id"])
    before = {v["label"]: v for v in camp["versions"]}
    ai.add("content_turn", turn(intent="edit", action="draft_copy", text="B안만 줄일게요."))
    ai.add("content_draft", {"note": "", "drafts": [draft("B short")]})
    out = chat(auth_client, camp, "B안 더 짧게").get_json()
    c = out["campaign"]
    assert len(c["versions"]) == 4
    new = next(v for v in c["versions"] if v["id"] == out["newVersionIds"][0])
    assert new["label"] == "B안" and new["parentId"] == before["B안"]["id"] and new["body"] == "B short"
    still = {v["id"]: v["body"] for v in c["versions"]}
    assert still[before["A안"]["id"]] == before["A안"]["body"] and still[before["C안"]["id"]] == before["C안"]["body"]
    assert "base_version" in ai.calls[1]["user"] and "B안 copy body" in ai.calls[1]["user"]
    # 모델이 엉뚱한 버전(A안)을 가리켜도 사용자가 말한 C안을 고침
    c = out["campaign"]
    ai.add("content_turn", turn(intent="edit", action="draft_copy", target=before["A안"]["id"]))
    ai.add("content_draft", {"note": "", "drafts": [draft("C short")]})
    out = chat(auth_client, c, "C안 더 짧게").get_json()
    new = next(v for v in out["campaign"]["versions"] if v["id"] == out["newVersionIds"][0])
    assert new["label"] == "C안" and new["parentId"] == before["C안"]["id"]


def test_cardnews_partial_edit_keeps_other_slides(auth_client, ai):
    camp = create(auth_client, content_type="cardnews")
    base_body = "## S1 · 훅\nHeadline: One\n\n## S2 · 소개\nHeadline: Two\n\n## S3 · 마무리\nHeadline: Three\n"
    made = _manual(auth_client, camp["id"], "cardnews", base_body, "A안")
    camp = made["campaign"]
    ai.add("content_turn", turn(intent="edit", action="draft_cardnews", target=made["versionId"]))
    ai.add("content_draft", {"note": "", "drafts": [draft(
        "## S1 · 훅\nHeadline: CHANGED ONE\n\n## S2 · 소개\nHeadline: New two\n\n## S3 · 마무리\nHeadline: CHANGED THREE\n")]})
    out = chat(auth_client, camp, "2장만 문구 바꿔줘", base_version_id=made["versionId"]).get_json()
    new = next(v for v in out["campaign"]["versions"] if v["id"] == out["newVersionIds"][0])
    assert "Headline: New two" in new["body"]
    assert "Headline: One" in new["body"] and "Headline: Three" in new["body"] and "CHANGED" not in new["body"]
    assert new["parentId"] == made["versionId"] and "S2만" in new["note"]


def test_merge_scenes_unit():
    base = "| 장면 | 시간 |\n| --- | --- |\n| SC1 | 0-3s A |\n| SC2 | 3-8s B |\n| SC3 | 8-15s C |\n"
    new = "| 장면 | 시간 |\n| --- | --- |\n| SC1 | X |\n| SC2 | 3-8s NEW |\n| SC3 | Y |\n"
    out = drafts.merge_scenes(base, new, {2})
    assert "| SC2 | 3-8s NEW |" in out["body"] and "| SC1 | 0-3s A |" in out["body"] and "| SC3 | 8-15s C |" in out["body"]


def test_select_version_and_duplicate_request(auth_client, ai):
    camp = create(auth_client)
    a = _manual(auth_client, camp["id"], "copy", "A body", "A안")
    _manual(auth_client, camp["id"], "copy", "B body", "B안")
    camp = get(auth_client, camp["id"])
    ai.add("content_turn", turn(intent="select_version", action="select_version", text="A안으로 돌아갈게요."))
    out = chat(auth_client, camp, "아까 A안으로 돌아가자", client_request_id="req-000001").get_json()
    assert out["campaign"]["selectedVersionId"] == a["versionId"] and len(out["campaign"]["versions"]) == 2
    res = chat(auth_client, out["campaign"], "아까 A안으로 돌아가자", client_request_id="req-000001")
    assert res.status_code == 409 and res.get_json()["duplicate"]


def test_kickoff_turn_no_user_message_no_action(auth_client, ai):
    camp = create(auth_client, extra_request="검정 배경, 영어로")
    ai.add("content_turn", turn(
        text="브랜드 자체 인스타 이미지로 시작할게요. 검정 배경에 네온 포인트를 제안해요.", action="draft_copy",
        patch=[{"field": "decisions.background", "value": "검정", "status": "confirmed"},
               {"field": "sheet.goal", "value": "브랜드 알리기", "status": "confirmed"}],
        cands=[{"field": "decisions.concept", "label": "Neon morning", "value": "Neon morning", "reason": "브랜드 색"},
               {"field": "decisions.price", "label": "x", "value": "$1", "reason": "사실"}]))
    out = auth_client.post(f"/api/content/campaigns/{camp['id']}/chat",
                           json={"kickoff": True, "expected_revision": 1}).get_json()
    c = out["campaign"]
    assert out["userMessage"] is None and [m["role"] for m in c["messages"]] == ["event", "assistant"]
    assert c["decisions"]["background"]["status"] == "confirmed" and c["sheet"]["goal"] == ""
    pay = out["reply"]["payload"]
    assert pay["action"]["type"] == "none"
    # 시트 제안은 거절 줄 대신 후보 카드로 · 사실 키 후보는 버림
    assert [cd["field"] for cd in pay["candidates"]] == ["decisions.concept", "sheet.goal"]
    assert not any(r["field"].startswith("sheet.") for r in pay["rejected"])


def test_strip_unsupported_claims():
    text = "배경을 검정으로 반영했어요. 카피 3안을 만들었어요! 다음엔 톤을 정해 볼까요?"
    assert conversation.strip_unsupported_claims(text, patched=False, made=False) == "다음엔 톤을 정해 볼까요?"
    assert "반영했어요" in conversation.strip_unsupported_claims(text, patched=True, made=False)


@pytest.mark.parametrize("msg,base,expected", [
    ("좋네", False, False), ("좋아요!", False, False), ("바로 만들어줘", False, True), ("왜 이 구도야?", False, False),
    ("카피만 먼저", False, True), ("일단 이미지는 말고 카피만", False, True), ("B안 더 짧게", False, True),
    ("배경만 검정으로", True, True), ("배경만 검정으로", False, False), ("음료는 빼고 젤리만", False, False),
    ("이미지 생성해줘", False, True), ("생성된 이유가 뭐야?", False, False), ("검정도 괜찮겠다", False, False),
])
def test_explicit_request_heuristic(msg, base, expected):
    assert heuristics.is_explicit_request(msg, base) is expected


def test_unit_targets_heuristic():
    assert heuristics.unit_targets("2장만 수정해줘")["slides"] == [2]
    assert heuristics.unit_targets("1, 3장만 바꿔")["slides"] == [1, 3]
    assert heuristics.unit_targets("5장 구성으로 만들어줘")["slides"] == []
    assert heuristics.unit_targets("장면 2랑 SC4 수정")["scenes"] == [2, 4]
    assert heuristics.excluded_media("영상 없이 대본만") == ["video"]


# ───────── 이미지 작업 · 파일 ─────────

def test_image_generate_then_edit_with_base_file(auth_client, ai, sync_jobs, monkeypatch, app):
    calls = {}

    def fake_generate(prompt, size, model):
        calls["generate"] = (prompt, size, model)
        return PNG, {"total": 10}

    def fake_edit(paths, prompt, size, model):
        paths = paths if isinstance(paths, list) else [paths]
        calls.setdefault("edit", []).append((paths, prompt, size, model))
        assert all(Path(x).is_file() for x in paths)
        return PNG, {}
    monkeypatch.setattr(providers, "generate_image", fake_generate)
    monkeypatch.setattr(providers, "edit_image", fake_edit)
    camp = create(auth_client, brand_focus=False, product_ids=["jelly_original"])
    camp = auth_client.post(f"/api/content/campaigns/{camp['id']}/decisions", json={
        "expected_revision": 1, "changes": [{"field": "decisions.ratio", "value": "4:5 세로"}]}).get_json()["campaign"]
    ai.add("content_turn", turn(intent="generate_media", action="generate_image", text="이미지 시안을 준비할게요."))
    ai.add("content_image_plan", {"prompt_en": "Neon lime jelly pouch on black.", "keep": [], "change": ["background"],
                                  "text_layers": [{"role": "slogan", "text": "RESTART YOUR DAY!"}, {"role": "headline", "text": "Hello"}],
                                  "notes_ko": "검정 배경 시안", "flavors": [], "include_character": False})
    out = chat(auth_client, camp, "이미지 만들어줘").get_json()
    assert out["job"]["status"] == "queued"
    c = get(auth_client, camp["id"])
    img = next(v for v in c["versions"] if v["kind"] == "image")
    assert img["label"] == "시안 1" and img["imageUrl"].startswith("/api/content/files/")
    # 제품을 골랐으니 최종 패키지 사진을 참고 이미지로 넣어 생성 (generate 아님)
    assert "generate" not in calls and calls["edit"][0][2] == "1024x1536" and img["image"]["size"] == "1024x1536"
    sent, prompt = calls["edit"][0][0], calls["edit"][0][1]
    # 그 제품의 참고용 사진 전부 (상한 16장 · 2026-10-25)
    assert img["image"]["references"] == [a["id"] for a in assets.pick_refs(["jelly_original"])]
    assert len(img["image"]["references"]) > 2 and img["image"]["references"][:2] == ["jelly-original-pouch-front",
                                                                                      "jelly-original-cherry-hat-wrapped"]
    assert [Path(x).name for x in sent] == [r + ".jpg" for r in img["image"]["references"]]
    assert "Reference image 1: Reboot Jelly Original" in prompt and "exactly as they appear in the reference images" in prompt
    # 새 방식: 문구는 이미지 AI가 직접 그림 (슬로건은 원문 그대로 · 텍스트 레이어 없음)
    assert {"role": "slogan", "text": "Restart Your Day"} in img["image"]["image_text"]
    assert img["image"]["text_layers"] == [] and img["image"]["text_mode"] == "bake" and img["image"]["baked"] is True
    assert 'spelled exactly as written' in prompt and 'slogan: "Restart Your Day"' in prompt and 'headline: "Hello"' in prompt
    assert "separate text overlay" not in prompt and "철자는 게시 전 확인" in img["note"]
    assert "최종 패키지" in img["note"] and "Rebu" not in img["note"] and c["jobs"][0]["status"] == "succeeded"
    assert any("이미지 시안이 준비됐어요" in m["text"] for m in c["messages"])
    res = auth_client.get(img["imageUrl"])
    assert res.status_code == 200 and res.mimetype == "image/png" and res.data == PNG
    job = auth_client.get(f"/api/content/jobs/{c['jobs'][0]['id']}").get_json()["job"]
    assert job["versionId"] == img["id"]
    # 기준 이미지를 넣어 편집 → 새 버전 · 부모 연결 · 원본 보존
    ai.add("content_turn", turn(intent="edit", action="edit_image", text="배경만 바꿔 볼게요."))
    ai.add("content_image_plan", {"prompt_en": "Same jelly pouch, white background.", "keep": ["products"],
                                  "change": ["background"], "text_layers": [], "notes_ko": "흰 배경", "flavors": ["lime"],
                                  "include_character": False})
    out = chat(auth_client, c, "배경만 흰색으로", base_version_id=img["id"]).get_json()
    c = get(auth_client, camp["id"])
    edited = next(v for v in c["versions"] if v["kind"] == "image" and v["id"] != img["id"])
    assert edited["parentId"] == img["id"] and edited["label"] == "시안 1" and edited["image"]["mode"] == "edit"
    base_sent = calls["edit"][1][0]
    assert base_sent[0].endswith(img["imageUrl"].rsplit("/", 1)[1])  # 1번 = 편집할 기준 이미지
    # 고른 맛(라임) 사진 전부 + 맛 구분 없는 봉지 사진
    assert [Path(x).name for x in base_sent[1:]] == ["jelly-original-lime-boot-wrapped.jpg", "jelly-original-lime-boot-spec.jpg",
                                                     "jelly-original-pouch-front.jpg", "jelly-original-pouch-scene-2.jpg"]
    assert "Image 1 is the base image to edit" in calls["edit"][1][1]
    assert auth_client.get(img["imageUrl"]).status_code == 200  # 원본 파일 유지
    # 수정할 때 프롬프트 작성 AI에게 기준 이미지에 들어간 문구를 넘김 (문구 수정 · 유지 판단용)
    plan_in = next(ai.ctx(i) for i in range(len(ai.calls) - 1, -1, -1) if ai.calls[i]["name"] == "content_image_plan")
    assert {"role": "headline", "text": "Hello"} in plan_in["base_version"]["text"] and plan_in["text_mode"] == "bake"


def test_image_without_text_mode(auth_client, ai, sync_jobs, monkeypatch):
    calls = {}

    def fake_generate(prompt, size, model):
        calls["prompt"] = prompt
        return PNG, {}
    monkeypatch.setattr(providers, "generate_image", fake_generate)
    camp = create(auth_client)
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", {"prompt_en": "Neon on black.", "keep": [], "change": [],
                                  "text_layers": [{"role": "headline", "text": "Hello"}], "notes_ko": "시안",
                                  "flavors": [], "include_character": False})
    chat(auth_client, camp, "이미지 만들어줘", image_text="none")
    img = next(v for v in get(auth_client, camp["id"])["versions"] if v["kind"] == "image")
    assert img["image"]["text_mode"] == "none" and img["image"]["image_text"] == [] and not img["image"]["baked"]
    assert "Do not render any text" in calls["prompt"] and "Hello" not in calls["prompt"] and "글자 없이" in img["note"]


def test_text_mode_from_message():
    from tabs.content import media
    assert media.text_mode_of(None, "이미지 만들어줘") == "bake"
    assert media.text_mode_of("bake", "글자 없이 이미지만 만들어줘") == "none"
    assert media.text_mode_of("none", "이미지 만들어줘") == "none"
    assert media.text_mode_of("evil", "make it, no text please") == "none"


def test_image_timeout_is_submission_unknown_not_retried(auth_client, ai, sync_jobs, monkeypatch):
    n = {"calls": 0}

    def boom(prompt, size, model):
        n["calls"] += 1
        raise providers.SubmissionUnknown("이미지 응답이 늦어 연결을 끊었습니다.")
    monkeypatch.setattr(providers, "generate_image", boom)
    camp = create(auth_client)
    ai.add("content_turn", turn(action="generate_image"))
    ai.add("content_image_plan", {"prompt_en": "x", "keep": [], "change": [], "text_layers": [], "notes_ko": "",
                                  "flavors": [], "include_character": False})
    chat(auth_client, camp, "이미지 만들어줘")
    c = get(auth_client, camp["id"])
    assert n["calls"] == 1 and c["jobs"][0]["status"] == "submission_unknown"
    assert not any(v["kind"] == "image" for v in c["versions"])
    assert "확인하지 못했어요" in c["messages"][-1]["text"]


def test_stale_running_job_becomes_unknown(app, auth_client):
    camp = create(auth_client)
    from tabs.content.models import ContentJob
    from server.extensions import db
    from server.models import utcnow
    old = utcnow() - timedelta(hours=1)
    with app.app_context():
        db.session.add(ContentJob(id="a" * 32, campaign_id=camp["id"], status="running", prompt="sent", updated_at=old,
                                  created_at=old))
        db.session.add(ContentJob(id="b" * 32, campaign_id=camp["id"], status="queued", updated_at=old,
                                  created_at=old - timedelta(minutes=1)))
        db.session.commit()
    jobs = {j["id"]: j for j in get(auth_client, camp["id"])["jobs"]}
    assert jobs["a" * 32]["status"] == "submission_unknown" and "자동으로 다시 제출하지 않" in jobs["a" * 32]["error"]
    assert jobs["b" * 32]["status"] == "failed" and "보내기 전" in jobs["b" * 32]["error"]  # 보내지 않은 작업은 비용 없음


def test_file_serving_path_safety(app, auth_client):
    folder = Path(app.config["DATA_DIR"]) / "uploads" / "content"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "c1-orphan000000-aaaa0000.png").write_bytes(PNG)  # DB에 없는 파일
    for url in ("/api/content/files/..%2F..%2Ftest.db", "/api/content/files/test.db", "/api/content/files/c1-orphan000000-aaaa0000.png",
                "/api/content/files/%2e%2e%2freboot.db", "/api/content/files/x.png"):
        res = auth_client.get(url)
        assert res.status_code == 404, url
    assert auth_client.get("/api/content/files/../../test.db").status_code == 404


# ───────── 직접 작성 · 최종본 · 내보내기 · 검토 기록 ─────────

def test_manual_version_final_and_export(auth_client):
    camp = create(auth_client, brand_focus=True, product_ids=["jelly_balance"])
    cid = camp["id"]
    url = f"/api/content/campaigns/{cid}/versions"
    assert auth_client.post(url, json={"kind": "poem", "body": "x"}).status_code == 400
    assert auth_client.post(url, json={"kind": "copy", "body": "  "}).status_code == 400
    other = create(auth_client)
    ov = _manual(auth_client, other["id"], "copy", "other", "A안")
    assert auth_client.post(url, json={"kind": "copy", "body": "x", "parent_id": ov["versionId"]}).status_code == 400
    made = _manual(auth_client, cid, "copy", "**Headline** Restart Your Day with Reboot Jelly Balance. Less sugar!", "A안")
    v = made["campaign"]["versions"][0]
    assert v["source"] == "manual" and v["review"]["keyword"]["status"] == "error"
    res = auth_client.post(f"/api/content/versions/{v['id']}/final", json={})
    assert res.status_code == 200 and res.get_json()["campaign"]["finalVersionId"] == v["id"]
    assert "승인" in res.get_json()["campaign"]["messages"][-1]["text"]
    res = auth_client.post(f"/api/content/versions/{v['id']}/review", json={
        "verdict": "BLOCK", "label": "사용 불가", "channel": "b2c", "products": ["jelly-ls"],
        "findings": [{"code": "R11", "name": "당 표시", "verdict": "BLOCK", "terms": ["Less sugar"]}]})
    assert res.status_code == 200
    assert res.get_json()["campaign"]["versions"][0]["review"]["compliance"]["verdict"] == "BLOCK"
    assert auth_client.post(f"/api/content/versions/{v['id']}/review", json={"verdict": "OK"}).status_code == 400
    res = auth_client.get(f"/api/content/campaigns/{cid}/export")
    assert res.status_code == 200 and res.mimetype == "application/zip"
    assert re.search(r'filename="reboot-content-\d+-\d{8}\.zip"', res.headers["Content-Disposition"])
    z = zipfile.ZipFile(io.BytesIO(res.data))
    names = z.namelist()
    assert {"00_README.txt", "campaign.md", "facts.json", "versions.json", "settings.json"} <= set(names)
    every = "\n".join(z.read(n).decode("utf-8") for n in names)
    assert "승인본이 아닙니다" in every and "user-2026-10-23" in every and "최종본으로 지정됨 (승인 아님)" in every
    assert '"zero_sugar": true' in every and "+kit-2026-10-25" in every  # 브랜드 키트 값 (Balance = Zero Sugar)
    assert "sk-" not in every and "OPENAI_API_KEY" not in every and "password" not in every.lower()
    one = auth_client.get(f"/api/content/campaigns/{cid}/export?version={v['id']}")
    assert one.status_code == 200
    assert auth_client.get(f"/api/content/campaigns/{cid}/export?version={ov['versionId']}").status_code == 404
    res = auth_client.post(f"/api/content/versions/{v['id']}/final", json={"final": False})
    assert res.get_json()["campaign"]["finalVersionId"] is None


def test_ai_review_needs_key_then_records(auth_client, ai, monkeypatch):
    camp = create(auth_client)
    made = _manual(auth_client, camp["id"], "copy", "Restart Your Day.", "A안")
    ai.add("content_review", {"summary": "슬로건 표기는 원문과 같아요.", "issues": [
        {"location": "1행", "problem": "CTA 없음", "suggestion": "Shop now 추가", "basis": "brand"}], "checked_scope": "텍스트"})
    res = auth_client.post(f"/api/content/versions/{made['versionId']}/ai-review", json={})
    assert res.status_code == 200
    rv = res.get_json()["campaign"]["versions"][0]["review"]["ai"]
    assert rv["issues"][0]["problem"] == "CTA 없음" and "독립" in rv["note"]
    monkeypatch.setattr(providers, "ready", lambda: False)
    assert auth_client.post(f"/api/content/versions/{made['versionId']}/ai-review", json={}).status_code == 503


# ───────── 시트 추천 ─────────

def test_suggest_examples_without_key_and_ai_with_key(auth_client, monkeypatch):
    res = auth_client.post("/api/content/suggest", json={"field": "goal", "sheet": sheet()})
    data = res.get_json()
    assert res.status_code == 200 and data["source"] == "example" and len(data["items"]) == 3
    assert data["basis"]["brand_focus"] is True and "키" in data["note"]
    assert auth_client.post("/api/content/suggest", json={"field": "title", "sheet": sheet()}).status_code == 400
    assert auth_client.post("/api/content/suggest", json={"field": "goal", "sheet": {}}).status_code == 400
    assert auth_client.post("/api/content/suggest", json={"field": "extra", "sheet": sheet(product_ids=["nope"])}).status_code == 400
    fake = FakeAI()
    fake.add("content_suggest", {"items": [{"value": "브랜드 첫인상", "reason": "브랜드 자체 선택"},
                                           {"value": "슬로건 소개", "reason": "확인된 슬로건"},
                                           {"value": "색 소개", "reason": "검정 & 네온"}, {"value": "4번째", "reason": "버림"}]})
    monkeypatch.setattr(providers, "ready", lambda: True)
    monkeypatch.setattr(providers, "call_json", fake)
    data = auth_client.post("/api/content/suggest", json={"field": "goal", "sheet": sheet()}).get_json()
    assert data["source"] == "ai" and [i["value"] for i in data["items"]] == ["브랜드 첫인상", "슬로건 소개", "색 소개"]


def test_example_suggestions_never_invent_facts():
    for ct in O.CONTENT_TYPES:
        for s in (sheet(content_type=ct), sheet(content_type=ct, brand_focus=False, product_ids=["jelly_original", "drink_balance"])):
            for field in ("goal", "extra"):
                items = O.example_suggestions(field, s)
                assert len(items) == 3
                text = " ".join(i["value"] + i["reason"] for i in items).lower()
                assert not re.search(r"low sugar|sugar free|hangover|숙취|\d+\s?(g|ml|kcal)|\$", text)


# ───────── 삭제 권한 ─────────

def test_delete_only_creator_or_admin(app, auth_client):
    camp = create(auth_client)
    member = app.test_client()
    assert member.post("/login", data={"email": "member@reboot.test", "password": PASSWORD}).status_code == 302
    res = member.delete(f"/api/content/campaigns/{camp['id']}")
    assert res.status_code == 403
    mine = member.post("/api/content/campaigns", json=sheet()).get_json()["campaign"]
    assert member.delete(f"/api/content/campaigns/{mine['id']}").status_code == 200
    assert auth_client.delete(f"/api/content/campaigns/{camp['id']}").status_code == 200
    assert auth_client.get(f"/api/content/campaigns/{camp['id']}").status_code == 404


# ───────── API 연결 상태 ─────────

def test_meta_connections_and_check(auth_client, monkeypatch):
    meta = auth_client.get("/api/content/meta").get_json()
    assert meta["connections"]["openai"]["key"] is False and meta["connections"]["higgsfield"]["wired"] is True
    assert meta["connections"]["higgsfield"]["key"] is False and meta["video"]["resolution"] == "720p"
    assert set(meta["imageProviders"]) == {"openai", "higgsfield"} and set(meta["video"]["modes"]) == {"plan_only", "higgsfield"}
    out = auth_client.get("/api/content/meta/check").get_json()
    assert out["openai"]["ok"] is False and "키가 없습니다" in out["openai"]["message"]  # 키 없으면 네트워크 호출 없음
    assert out["higgsfield"]["checked"] is False and out["higgsfield"]["ok"] is False
    monkeypatch.setattr(providers, "check_connection", lambda: {"ok": True, "message": "확인", "models": {"m": True}})
    assert auth_client.get("/api/content/meta/check").get_json()["openai"]["ok"] is True


# ───────── 후보 선택 취소 (고르기 전 값으로) ─────────

def test_candidate_cancel_restores_previous_values(auth_client):
    camp = create(auth_client, goal="제품 소개")
    url = f"/api/content/campaigns/{camp['id']}/decisions"
    c = auth_client.post(url, json={"expected_revision": 1, "source": "candidate",
                                    "changes": [{"field": "sheet.goal", "value": "구매 유도", "status": "confirmed"}]}).get_json()["campaign"]
    assert c["sheet"]["goal"] == "구매 유도"
    c = auth_client.post(url, json={"expected_revision": c["revision"], "source": "undo",
                                    "changes": [{"field": "sheet.goal", "value": "제품 소개", "status": "confirmed"}]}).get_json()["campaign"]
    assert c["sheet"]["goal"] == "제품 소개"
    c = auth_client.post(url, json={"expected_revision": c["revision"], "source": "undo",
                                    "changes": [{"field": "sheet.goal", "value": "", "status": "confirmed"}]}).get_json()["campaign"]
    assert c["sheet"]["goal"] == ""  # 목적은 비워도 시작 조건(콘텐츠·채널·대상)은 그대로


# ───────── 인터뷰 (질문 하나씩 · 보기 3개 · 최대 5개) ─────────

def iv(client, camp, action, **kw):
    body = {"action": action, "expected_revision": camp["revision"]}
    body.update(kw)
    return client.post(f"/api/content/campaigns/{camp['id']}/interview", json=body)


def open_question(camp):
    qs = [m for m in camp["messages"] if (m["payload"] or {}).get("interview")]
    return qs[-1]["payload"]["interview"] if qs and qs[-1]["payload"]["interview"]["status"] == "open" else None


def test_interview_example_flow_without_key(auth_client):
    camp = create(auth_client)
    c = iv(auth_client, camp, "start").get_json()["campaign"]
    q = open_question(c)
    assert q["step"] == 1 and q["field"] == "sheet.goal" and q["source"] == "example" and len(q["options"]) == 3
    assert iv(auth_client, c, "start").get_json()["started"] is False  # 두 번 눌러도 같은 질문을 또 만들지 않음
    c = iv(auth_client, c, "answer", option=0, value="무시됨").get_json()["campaign"]
    assert c["sheet"]["goal"] == "브랜드 알리기"  # 보기 값은 서버가 저장해 둔 것
    assert [m["text"] for m in c["messages"] if m["role"] == "user"] == ["① 브랜드 알리기"]
    q = open_question(c)
    assert q["step"] == 2 and q["field"] == "decisions.concept"
    c = iv(auth_client, c, "answer", value="검정 배경의 미니멀 무드").get_json()["campaign"]  # 직접 입력
    assert c["decisions"]["concept"] == dict(c["decisions"]["concept"], value="검정 배경의 미니멀 무드", status="confirmed",
                                             source="manual")
    assert open_question(c)["ready"] is False  # 답 2개 — 아직 윤곽 전
    c = iv(auth_client, c, "skip").get_json()["campaign"]
    # 비율·크기는 채널 기본값(제안)이 있어도 물음 — 보기 첫 번째가 채널(Instagram) 추천
    q = open_question(c)
    assert "background" not in c["decisions"] and q["field"] == "decisions.ratio"
    assert q["options"][0]["label"].startswith("4:5") and "추천" in q["options"][0]["label"]
    c = iv(auth_client, c, "answer", option=0).get_json()["campaign"]
    assert open_question(c)["field"] == "decisions.tone" and open_question(c)["ready"] is True  # 답 3개 → 만들기 제안
    c = iv(auth_client, c, "answer", option=2).get_json()["campaign"]
    assert open_question(c)["field"] == "decisions.language"
    c = iv(auth_client, c, "answer", option=0).get_json()["campaign"]
    assert open_question(c) is None  # 예시 질문을 다 쓰면 끝
    done = [m for m in c["messages"] if (m["payload"] or {}).get("interviewDone")]
    assert len(done) == 1 and done[0]["payload"]["interviewDone"] == {"answered": 5, "asked": 6}
    assert c["decisions"]["ratio"] == dict(c["decisions"]["ratio"], value="4:5 세로 · 1080×1350", status="confirmed")
    assert c["decisions"]["tone"]["value"] == "친근하고 따뜻하게"
    assert c["decisions"]["language"]["status"] == "confirmed"
    assert iv(auth_client, c, "answer", option=0).status_code == 400  # 끝난 뒤에는 답하지 않음


def test_interview_skips_filled_and_validates_ai(auth_client, ai):
    camp = create(auth_client, goal="구매 유도")
    camp = auth_client.post(f"/api/content/campaigns/{camp['id']}/decisions", json={
        "expected_revision": 1, "changes": [{"field": "decisions.concept", "value": "네온", "status": "confirmed"}]}).get_json()["campaign"]
    opts = [{"label": f"보기{i}", "value": f"값{i}", "reason": "이유"} for i in range(3)]
    ai.add("content_interview",
           {"done": False, "intro": "반영했어요. 인스타 이미지로 시작할게요.", "field": "decisions.price", "label": "가격",
            "question": "가격은?", "options": opts})  # 사실 항목 → 예시 질문으로
    c = iv(auth_client, camp, "start").get_json()["campaign"]
    q = open_question(c)
    assert q["field"] == "decisions.background" and q["source"] == "example"  # 목적·콘셉트는 이미 있어 건너뜀
    assert "반영했어요" not in q["intro"] and "시작할게요" in q["intro"]
    ai.add("content_interview", {"done": False, "intro": "", "field": "decisions.mood", "label": "분위기",
                                 "question": "분위기는?", "options": opts})
    c = iv(auth_client, c, "answer", option=0).get_json()["campaign"]
    q = open_question(c)
    assert q["field"] == "decisions.mood" and q["source"] == "ai" and q["options"][2]["value"] == "값2"
    ai.add("content_interview", {"done": True, "intro": "충분해요.", "field": "", "label": "", "question": "", "options": []})
    c = iv(auth_client, c, "answer", option=1).get_json()["campaign"]
    assert open_question(c) is None and c["decisions"]["mood"]["value"] == "값1"
    assert "충분해요." in c["messages"][-1]["text"] and ai.names() == ["content_interview"] * 3


def test_interview_ai_ready_and_safety_cap(auth_client, ai):
    camp = create(auth_client)
    opts = [{"label": f"보기{i}", "value": f"값{i}", "reason": "이유"} for i in range(3)]
    for n in range(conversation.INTERVIEW_MAX):  # AI가 계속 새 질문을 주더라도
        ai.add("content_interview", {"done": False, "ready": n >= 2, "intro": "", "field": f"decisions.item_{n}",
                                     "label": f"항목{n}", "question": f"질문{n}?", "options": opts})
    c = iv(auth_client, camp, "start").get_json()["campaign"]
    assert open_question(c)["ready"] is False
    for n in range(1, conversation.INTERVIEW_MAX):
        c = iv(auth_client, c, "answer", option=0).get_json()["campaign"]
        assert open_question(c)["step"] == n + 1 and open_question(c)["ready"] is (n >= 2)
    c = iv(auth_client, c, "answer", option=0).get_json()["campaign"]
    assert open_question(c) is None  # 안전 상한 10개 — 11번째 질문은 AI에 묻지도 않음
    assert len(ai.names()) == conversation.INTERVIEW_MAX == 10
    assert c["messages"][-1]["payload"]["interviewDone"] == {"answered": 10, "asked": 10}


def test_interview_finish_and_conflict(auth_client):
    camp = create(auth_client, content_type="crm", channels=["crm"])
    c = iv(auth_client, camp, "start").get_json()["campaign"]
    assert iv(auth_client, dict(c, revision=c["revision"] + 5), "skip").status_code == 409
    c = iv(auth_client, c, "finish").get_json()["campaign"]
    assert open_question(c) is None
    qs = [m for m in c["messages"] if (m["payload"] or {}).get("interview")]
    assert qs[0]["payload"]["interview"]["status"] == "skipped"
    assert c["messages"][-1]["payload"]["interviewDone"]["answered"] == 0


# ───────── 이미지 직접 편집 (AI 없음 · 새 버전) ─────────

def _png(w, h):
    head = PNG[:16] + w.to_bytes(4, "big") + h.to_bytes(4, "big")
    return head + PNG[24:]


def test_image_editor_upload_makes_child_version(auth_client, ai, sync_jobs, monkeypatch):
    monkeypatch.setattr(providers, "generate_image", lambda prompt, size, model: (PNG, {}))
    camp = create(auth_client)
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", {"prompt_en": "Neon on black.", "keep": [], "change": [], "text_layers": [],
                                  "notes_ko": "시안", "character_draft": False})
    chat(auth_client, camp, "이미지 만들어줘")
    c = get(auth_client, camp["id"])
    img = next(v for v in c["versions"] if v["kind"] == "image")
    url = f"/api/content/campaigns/{camp['id']}/images"

    def post(data, parent=img["id"], **form):
        fields = {"file": (io.BytesIO(data), "edit.png"), "parent_id": str(parent)}
        fields.update(form)
        return auth_client.post(url, data=fields, content_type="multipart/form-data")

    assert post(b"not a png").status_code == 400
    assert post(_png(8000, 10)).status_code == 400
    text_v = _manual(auth_client, camp["id"], "copy", "본문", "A안")["versionId"]
    res = post(_png(20, 20), parent=text_v)
    assert res.status_code == 400 and "원본 이미지" in res.get_json()["error"]  # 이미지 버전만 편집
    layers = [{"role": "slogan", "text": "Restart!!", "x": 0.9, "y": 0.9, "size": 0.05, "color": "#d7ff3f", "align": "right"},
              {"role": "headline", "text": "Hello", "x": 5, "y": -1, "size": 1, "color": "red", "align": "?"}]
    res = post(_png(20, 25), text_layers=json.dumps(layers), edits=json.dumps({"crop": "4:5", "evil": "x"}), baked="0")
    assert res.status_code == 201
    c = res.get_json()["campaign"]
    v = next(x for x in c["versions"] if x["id"] == res.get_json()["versionId"])
    assert v["parentId"] == img["id"] and v["label"] == img["label"] and v["source"] == "manual"
    assert v["image"]["mode"] == "manual_edit" and v["image"]["size"] == "20x25" and v["image"]["edits"] == {"crop": "4:5"}
    assert v["image"]["text_layers"][0]["text"] == "Restart Your Day"  # 슬로건은 원문 고정
    h = v["image"]["text_layers"][1]
    assert (h["x"], h["y"], h["size"], h["color"], h["align"]) == (1, 0, 0.2, "#ffffff", "left")  # 범위 밖 값은 잘라냄
    assert "AI 아님" in v["note"] and c["selectedVersionId"] == v["id"]
    assert auth_client.get(v["imageUrl"]).status_code == 200 and auth_client.get(img["imageUrl"]).status_code == 200


# ───────── 브랜드 키트 · 자산 · 캐릭터 Rebu (2026-10-24) ─────────

def test_brand_kit_overlays_facts_per_product(auth_client):
    camp = create(auth_client, brand_focus=False, product_ids=["jelly_balance", "drink_original"])
    f = camp["facts"]
    assert f["facts_version"].startswith("user-2026-10-23") and f["facts_version"].endswith("+kit-2026-10-25")
    assert f["brand"]["slogans"] == ["Restart Your Day", "Buy Your Tomorrow"] and f["brand"]["slogan"] == "Restart Your Day"
    assert f["brand"]["character"].startswith("Rebu") and "A-HAT" in f["brand"]["key_ingredient"]
    jb, do = (next(p for p in f["products"] if p["entity_id"] == pid) for pid in ("jelly_balance", "drink_original"))
    assert jb["zero_sugar"] is True and do["zero_sugar"] is False  # 제품마다 따로 — Original로 옮기지 않음
    assert do["volume"] == "100ml" and [x["usd"] for x in do["price"]] == [7, 19] and jb["price"][0]["usd"] == 18
    assert "올가미" in jb["form"] and "리볼버" not in json.dumps(f, ensure_ascii=False)
    meta = auth_client.get("/api/content/meta").get_json()
    assert meta["character"]["choices"] == list(assets.CHARACTER_CHOICES) and "모양 젤리" in meta["productKit"]["jelly_original"]["form"]


def test_manifest_files_exist_and_ids_are_valid():
    kit = brandkit.load()
    seen = set()
    for a in assets.all_assets():
        assert assets.ID_RE.match(a["id"]) and a["id"] not in seen, a["id"]
        seen.add(a["id"])
        assert assets.path_of(a).is_file() and assets.path_of(a).suffix == ".jpg", a["file"]
        if a["type"] == "product":
            assert a["product_id"] in kit["products"] and a.get("flavor") in (None, *assets.FLAVORS)
    # 리볼버가 보이는 사진 · 글자 많은 캐릭터 시트는 참고 사진으로 고르지 않음
    assert not assets.get("jelly-original-pouch-scene")["ref"] and not assets.get("rebu-profile-sheet")["ref"]


def test_pick_refs_by_flavor_and_character():
    ids = lambda refs: [a["id"] for a in refs]
    # 제품(·맛)에 맞는 참고용 사진 전부 — 우선순위 순, 상한 16장 (2026-10-25)
    one = ids(assets.pick_refs(["drink_original"]))
    assert one[:2] == ["drink-original-trio", "drink-original-cherry-front"] and len(one) == 10
    assert ids(assets.pick_refs(["drink_original"], flavors=["lime"])) == [
        "drink-original-lime-front", "drink-original-lime-angle", "drink-original-lime-spec", "drink-original-trio"]
    two = ids(assets.pick_refs(["jelly_balance", "drink_balance"], flavors=["lemon"], character=True))
    assert two[:2] == ["jelly-balance-lemon-lasso-wrapped", "drink-balance-lemon-front"] and two[-1] == "rebu-poses-fullbody"
    assert not any(x for x in two if "cherry" in x or "lime" in x)  # 고른 맛 · 맛 구분 없는 사진만
    many = assets.pick_refs(["jelly_original", "jelly_balance", "drink_original", "drink_balance"], character=True)
    assert len(many) == assets.MAX_REFS == 16 and many[-1]["type"] == "character"
    assert {a["product_id"] for a in many[:-1]} == {"jelly_original", "jelly_balance", "drink_original", "drink_balance"}
    assert len(assets.pick_refs(["jelly_original"], limit=assets.HIGGSFIELD_MAX_REFS)) == 5
    assert all(a["ref"] for a in many) and assets.flavors_in("체리 맛으로", "Lime!") == ["cherry", "lime"]


def test_assets_require_login(client):
    assert client.get("/api/content/assets").status_code == 401
    assert client.get("/api/content/assets/rebu-faces.jpg").status_code == 401


def test_assets_endpoints(auth_client):
    items = auth_client.get("/api/content/assets").get_json()["items"]
    assert len(items) == len(assets.all_assets()) and all(i["url"].startswith("/api/content/assets/") for i in items)
    res = auth_client.get("/api/content/assets/rebu-faces.jpg")
    assert res.status_code == 200 and res.mimetype == "image/jpeg" and res.data[:2] == b"\xff\xd8"
    for url in ("/api/content/assets/nope.jpg", "/api/content/assets/rebu-faces.png", "/api/content/assets/manifest.json",
                "/api/content/assets/..%2Fbrand_kit.json", "/api/content/assets/%2e%2e%2f%2e%2e%2froutes.py"):
        assert auth_client.get(url).status_code == 404, url


def test_character_choice_from_sheet_and_patch(auth_client):
    camp = create(auth_client, use_character="사용")
    d = camp["decisions"]["use_character"]
    assert d["value"] == "사용" and d["status"] == "confirmed" and d["source"] == "sheet"
    assert assets.character_choice(camp["decisions"]) == "사용"
    res = auth_client.post("/api/content/campaigns", json=sheet(use_character="가끔"))
    assert res.status_code == 400 and "Rebu" in res.get_json()["error"]
    crm = create(auth_client, content_type="crm", channels=["crm"], use_character="사용")
    assert "use_character" not in crm["decisions"]  # 시각 콘텐츠가 아니면 무시
    res = auth_client.patch(f"/api/content/campaigns/{camp['id']}",
                            json={"expected_revision": camp["revision"], "sheet": {"use_character": "사용 안 함"}})
    assert res.status_code == 200
    c = res.get_json()["campaign"]
    assert c["decisions"]["use_character"]["value"] == "사용 안 함" and assets.character_choice(c["decisions"]) == "사용 안 함"
    assert "캐릭터 Rebu" in c["messages"][-1]["text"]
    assert assets.character_choice({}) == "AI 판단" and assets.character_choice({"use_character": {"value": "아니요"}}) == "사용 안 함"


def test_image_character_choice_controls_rebu(auth_client, ai, sync_jobs, monkeypatch):
    sent = []
    monkeypatch.setattr(providers, "edit_image", lambda paths, prompt, size, model: (sent.append((paths, prompt)), (PNG, {}))[1])
    monkeypatch.setattr(providers, "generate_image", lambda prompt, size, model: (sent.append((None, prompt)), (PNG, {}))[1])
    plan = {"prompt_en": "Mascot on black.", "keep": [], "change": [], "text_layers": [], "notes_ko": "시안", "flavors": [],
            "include_character": True}
    # 사용 안 함 → AI가 넣자고 해도 Rebu 없음 · 브랜드만 골랐으니 참고 사진 없이 생성 (문구가 없으면 슬로건만 그림)
    camp = create(auth_client, use_character="사용 안 함")
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", dict(plan))
    chat(auth_client, camp, "이미지 만들어줘")
    img = next(v for v in get(auth_client, camp["id"])["versions"] if v["kind"] == "image")
    assert sent[-1][0] is None and 'slogan: "Restart Your Day"' in sent[-1][1] and img["image"]["references"] == []
    assert img["image"]["include_character"] is False
    # 사용 → 캐릭터 시트를 참고 이미지로
    camp = create(auth_client, use_character="사용")
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", dict(plan, include_character=False))
    chat(auth_client, camp, "이미지 만들어줘")
    img = next(v for v in get(auth_client, camp["id"])["versions"] if v["kind"] == "image")
    assert [Path(x).name for x in sent[-1][0]] == ["rebu-poses-fullbody.jpg"] and "Rebu, the Reboot mascot" in sent[-1][1]
    assert img["image"]["references"] == ["rebu-poses-fullbody"] and "캐릭터 Rebu 포함" in img["note"]


def test_new_image_uses_copy_draft_made_first(auth_client, ai, sync_jobs, monkeypatch):
    monkeypatch.setattr(providers, "generate_image", lambda prompt, size, model: (PNG, {}))
    plan = {"prompt_en": "Neon lime on black.", "keep": [], "change": [], "text_layers": [], "notes_ko": "시안", "flavors": [],
            "include_character": False}
    # 초안 없이 바로 이미지 → 카피 초안 정보 없음
    camp = create(auth_client)
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", dict(plan))
    camp = chat(auth_client, camp, "이미지 만들어줘").get_json()["campaign"]
    assert ai.ctx()["copy_draft"] is None
    # 카피 초안 → 이미지: 가장 최근 초안 문구를 이미지 기획에 넘김 (기준 이미지 편집은 아님)
    ai.add("content_turn", turn(intent="generate", action="draft_copy"))
    ai.add("content_draft", {"note": "", "drafts": [draft("**Headline** A neon start.\n**CTA** Try it today")]})
    camp = chat(auth_client, camp, "카피 초안 만들어줘").get_json()["campaign"]
    copy_v = next(v for v in camp["versions"] if v["kind"] not in ("image", "video"))
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", dict(plan))
    chat(auth_client, camp, "이 초안으로 이미지 만들어줘", force="image")
    sent = ai.ctx()["copy_draft"]
    assert sent["label"] == copy_v["label"] and "A neon start" in sent["body"] and ai.ctx()["mode"] == "generate"
    imgs = [v for v in get(auth_client, camp["id"])["versions"] if v["kind"] == "image"]
    assert f"카피 초안 {copy_v['label']} · v{copy_v['seq']} 문구 기준" in imgs[0]["note"]


def test_keyword_check_zero_sugar_rebu_and_known_prices():
    facts = {"brand": {}, "products": [  # 앱 없이 쓸 수 있게 브랜드 키트 모양만 직접 만듦
        {"entity_id": "drink_original", "price": [{"usd": 7}, {"usd": 19}], "volume": "100ml", "zero_sugar": False},
        {"entity_id": "jelly_balance", "price": [{"usd": 18}], "zero_sugar": True}]}
    s = {"product_ids": ["drink_original", "jelly_balance"]}
    codes = lambda text: {i["code"]: i for i in review.keyword_check(text, "copy", s, facts)["items"]}
    assert "sugar" not in codes("Reboot Jelly Balance — Zero Sugar. ZERO-SUGAR fun.")  # 이 탭에서는 경고하지 않음
    assert codes("Low sugar and sugar free!")["sugar"]["terms"] == ["Low sugar", "sugar free"]
    assert "number" not in codes("Drink Original $7 · 3-pack $19 · 100 ml bottle")
    assert codes("Only $5 today")["number"]["terms"] == ["$5"]
    assert "character" in codes("리부부와 함께") and "cm_song" in codes("니온 앤 리부 노래") and not codes("Meet Rebu!")
    assert review.fact_value(facts, "jelly_balance", "zero_sugar") is True
    assert review.fact_value(facts, "drink_original", "zero_sugar") is None  # Original에 Zero Sugar 근거를 달면 맞지 않음


# ───────── Higgsfield (영상 · 이미지) ─────────

def test_settings_accept_video_mode_and_image_provider(auth_client):
    camp = create(auth_client)
    for bad in ({"video_mode": "sora"}, {"image_provider": "midjourney"}):
        res = auth_client.patch(f"/api/content/campaigns/{camp['id']}", json={"expected_revision": 1, "settings": bad})
        assert res.status_code == 400
    c = set_settings(auth_client, camp, video_mode="higgsfield", image_provider="higgsfield")
    assert c["settings"]["video_mode"] == "higgsfield" and c["settings"]["image_provider"] == "higgsfield"


def test_higgsfield_video_job_uses_script_refs_and_saves_mp4(auth_client, ai, sync_jobs, monkeypatch):
    hf = FakeHiggsfield(monkeypatch)
    camp = create(auth_client, content_type="shortform", channels=["tiktok"], brand_focus=False, product_ids=["jelly_original"])
    camp = auth_client.post(f"/api/content/campaigns/{camp['id']}/decisions", json={
        "expected_revision": camp["revision"], "changes": [{"field": "decisions.duration", "value": "20초 세로형"}]}).get_json()["campaign"]
    camp = set_settings(auth_client, camp, video_mode="higgsfield")
    # 먼저 구성안 → 그 구성안 기준으로 실제 영상
    ai.add("content_turn", turn(intent="draft", action="draft_script"))
    ai.add("content_draft", {"note": "", "drafts": [draft("| 장면 | 시간 |\n| --- | --- |\n| SC1 | 0-3s |\n")]})
    camp = chat(auth_client, camp, "숏폼 구성안 만들어줘").get_json()["campaign"]
    script = camp["versions"][0]
    ai.add("content_turn", turn(intent="generate_media", action="generate_video", text="영상으로 만들어 볼게요."))
    ai.add("content_video_plan", {"prompt_en": "Pouch spins on black, neon lime light.", "notes_ko": "검정 배경 회전 컷",
                                  "flavors": [], "include_character": False})
    out = chat(auth_client, camp, "이 구성안으로 실제 영상 만들어줘").get_json()
    assert out["job"]["kind"] == "video" and out["job"]["provider"] == "higgsfield"
    c = get(auth_client, camp["id"])
    video = next(v for v in c["versions"] if v["kind"] == "video")
    model_path, body = hf.submits[0]
    assert model_path == higgsfield.VIDEO_REF and body["resolution"] == "720p" and body["aspect_ratio"] == "9:16"
    assert body["duration"] == 15 and body["generate_audio"] is False  # 20초 → 최대 15초
    assert body["image_urls"] == [f"https://files.example/{n}" for n, _ in hf.uploads] and hf.uploads[0][1] == "image/jpeg"
    assert "Reference image 1: Reboot Jelly Original" in body["prompt"] and "No on-screen text" in body["prompt"]
    user = json.loads(ai.calls[-1]["user"])
    assert ai.calls[-1]["name"] == "content_video_plan" and user["script"]["label"] == script["label"]
    assert video["label"] == "영상 1" and video["videoUrl"].endswith(".mp4") and video["imageUrl"] is None
    assert video["video"]["duration"] == 15 and "Seedance 2.0" in video["note"] and "구성안 기준" in video["note"]
    assert c["jobs"][0]["status"] == "succeeded" and any("영상이 준비됐어요" in m["text"] for m in c["messages"])
    res = auth_client.get(video["videoUrl"])
    assert res.status_code == 200 and res.mimetype == "video/mp4" and res.data == MP4
    z = zipfile.ZipFile(io.BytesIO(auth_client.get(f"/api/content/campaigns/{camp['id']}/export").data))
    assert any(n.endswith("-video.mp4") for n in z.namelist())


def test_higgsfield_video_needs_key_and_setting(auth_client, ai, monkeypatch):
    camp = create(auth_client, content_type="shortform", channels=["tiktok"])
    camp = set_settings(auth_client, camp, video_mode="higgsfield")  # 키 없음 → 구성안
    ai.add("content_turn", turn(intent="generate_media", action="generate_video"))
    ai.add("content_draft", {"note": "", "drafts": [draft("| 장면 | 시간 |\n| --- | --- |\n| SC1 | 0-3s |\n")]})
    out = chat(auth_client, camp, "숏폼 영상 만들어줘").get_json()
    assert out["job"] is None and "Higgsfield 키가 없어" in out["reply"]["payload"]["action"]["note"]


def test_video_button_makes_video_from_draft_and_revise_keeps_original(auth_client, ai, sync_jobs, monkeypatch):
    hf = FakeHiggsfield(monkeypatch)
    camp = create(auth_client, content_type="shortform", channels=["tiktok"], brand_focus=False, product_ids=["jelly_original"])
    camp = auth_client.post(f"/api/content/campaigns/{camp['id']}/decisions", json={
        "expected_revision": camp["revision"], "changes": [{"field": "decisions.duration", "value": "8초 가로형 16:9"}]}).get_json()["campaign"]
    ai.add("content_turn", turn(intent="draft", action="draft_script"))
    ai.add("content_draft", {"note": "", "drafts": [draft("| 장면 | 시간 |\n| --- | --- |\n| SC1 | 0-3s |\n")]})
    camp = chat(auth_client, camp, "숏폼 구성안 만들어줘").get_json()["campaign"]
    script = camp["versions"][0]
    # 영상 설정이 '구성안만'이어도 영상 만들기 버튼(force_action=video)은 실제 영상
    ai.add("content_turn", turn(text="영상으로 만들어 볼게요."))
    ai.add("content_video_plan", {"prompt_en": "Pouch spins on black.", "notes_ko": "회전 컷", "flavors": [], "include_character": False})
    out = chat(auth_client, camp, "이 초안으로 영상 만들어줘", force_action="video", base_version_id=script["id"]).get_json()
    assert out["job"]["kind"] == "video" and out["reply"]["payload"]["action"]["type"] == "generate_video"
    c = get(auth_client, camp["id"])
    first = next(v for v in c["versions"] if v["kind"] == "video")
    assert first["label"] == "영상 1" and hf.submits[0][1]["duration"] == 8 and "구성안 기준" in first["note"]
    assert json.loads(ai.calls[-1]["user"])["script"]["label"] == script["label"]
    # 수정: 이전 프롬프트 + 요청 → 새 버전 (원본 유지 · 길이는 요청대로, 비율은 이전 그대로)
    ai.add("content_turn", turn(text="수정해 볼게요."))
    ai.add("content_video_plan", {"prompt_en": "Pouch spins slowly on black.", "notes_ko": "느린 회전", "flavors": [], "include_character": False})
    out = chat(auth_client, c, "카메라를 더 천천히, 길이는 5초로", force_action="video", base_version_id=first["id"]).get_json()
    user = json.loads(ai.calls[-1]["user"])
    assert user["previous_video"]["prompt"].startswith("Pouch spins on black.") and user["script"] is None
    _, body = hf.submits[1]
    assert body["duration"] == 5 and body["aspect_ratio"] == hf.submits[0][1]["aspect_ratio"]
    c = get(auth_client, camp["id"])
    videos = sorted((v for v in c["versions"] if v["kind"] == "video"), key=lambda v: v["seq"])
    assert len(videos) == 2 and videos[1]["label"] == "영상 1" and "원본 영상 유지" in videos[1]["note"]
    assert "원본 영상 유지" not in videos[0]["note"] and videos[0]["videoUrl"] != videos[1]["videoUrl"]
    assert any("영상 수정본이 준비됐어요" in m["text"] for m in c["messages"])


def test_video_button_without_higgsfield_key_does_not_fall_back(auth_client, ai, monkeypatch):
    monkeypatch.setattr(higgsfield, "ready", lambda: False)
    camp = create(auth_client)
    ai.add("content_turn", turn())
    out = chat(auth_client, camp, "영상 만들어줘", force_action="video").get_json()
    assert out["job"] is None and "Higgsfield 키" in out["reply"]["payload"]["action"]["note"]
    assert not get(auth_client, camp["id"])["versions"]


def test_higgsfield_image_provider_saves_jpg_and_keeps_provider(auth_client, ai, sync_jobs, monkeypatch):
    hf = FakeHiggsfield(monkeypatch, result=JPG, statuses=("queued", "in_progress", "completed"))
    called = []
    monkeypatch.setattr(providers, "edit_image", lambda *a, **k: called.append(a))
    monkeypatch.setattr(providers, "generate_image", lambda *a, **k: called.append(a))
    camp = create(auth_client, brand_focus=False, product_ids=["jelly_original"])
    camp = auth_client.post(f"/api/content/campaigns/{camp['id']}/decisions", json={
        "expected_revision": camp["revision"], "changes": [{"field": "decisions.ratio", "value": "4:5 세로"}]}).get_json()["campaign"]
    camp = set_settings(auth_client, camp, image_provider="higgsfield")
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", {"prompt_en": "Pouch on black.", "keep": [], "change": [], "text_layers": [],
                                  "notes_ko": "시안", "flavors": [], "include_character": False})
    chat(auth_client, camp, "이미지 만들어줘")
    c = get(auth_client, camp["id"])
    img = next(v for v in c["versions"] if v["kind"] == "image")
    model_path, body = hf.submits[0]
    assert called == [] and model_path == higgsfield.IMAGE_MODEL and body["aspect_ratio"] == "3:4"  # 4:5에 가장 가까운 비율
    assert len(body["image_urls"]) == assets.HIGGSFIELD_MAX_REFS and img["image"]["provider"] == "higgsfield"
    assert img["imageUrl"].endswith(".jpg") and "Marketing Studio" in img["note"] and "4:5 세로 → 3:4" in img["note"]
    res = auth_client.get(img["imageUrl"])
    assert res.status_code == 200 and res.mimetype == "image/jpeg"


def test_higgsfield_image_without_key_is_not_switched_to_openai(auth_client, ai, monkeypatch):
    monkeypatch.setattr(higgsfield, "ready", lambda: False)
    camp = set_settings(auth_client, create(auth_client), image_provider="higgsfield")
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    out = chat(auth_client, camp, "이미지 만들어줘").get_json()
    act = out["reply"]["payload"]["action"]
    assert out["job"] is None and act["status"] == "skipped" and "OpenAI로 바꾸거나" in act["note"]


@pytest.mark.parametrize("status,expect,text", [("nsfw", "failed", "안전 정책"), ("failed", "failed", "boom"),
                                                ("canceled", "canceled", "취소")])
def test_higgsfield_terminal_failures(auth_client, ai, sync_jobs, monkeypatch, status, expect, text):
    FakeHiggsfield(monkeypatch, statuses=(status,), error="boom")
    camp = set_settings(auth_client, create(auth_client, content_type="shortform", channels=["tiktok"]), video_mode="higgsfield")
    ai.add("content_turn", turn(intent="generate_media", action="generate_video"))
    ai.add("content_video_plan", {"prompt_en": "x", "notes_ko": "", "flavors": [], "include_character": False})
    chat(auth_client, camp, "영상 만들어줘")
    c = get(auth_client, camp["id"])
    assert c["jobs"][0]["status"] == expect and text in c["jobs"][0]["error"]
    assert not any(v["kind"] == "video" for v in c["versions"])


def test_higgsfield_submit_unknown_is_not_retried(auth_client, ai, sync_jobs, monkeypatch):
    hf = FakeHiggsfield(monkeypatch)

    def boom(model_path, body):
        hf.submits.append(body)
        raise providers.SubmissionUnknown("Higgsfield 응답을 받지 못했습니다.")
    monkeypatch.setattr(higgsfield, "submit", boom)
    camp = set_settings(auth_client, create(auth_client, content_type="shortform", channels=["tiktok"]), video_mode="higgsfield")
    ai.add("content_turn", turn(intent="generate_media", action="generate_video"))
    ai.add("content_video_plan", {"prompt_en": "x", "notes_ko": "", "flavors": [], "include_character": False})
    chat(auth_client, camp, "영상 만들어줘")
    c = get(auth_client, camp["id"])
    assert len(hf.submits) == 1 and c["jobs"][0]["status"] == "submission_unknown"
    assert "확인하지 못했어요" in c["messages"][-1]["text"]


def test_higgsfield_cancel_route(app, auth_client, monkeypatch):
    from tabs.content.models import ContentJob
    from server.extensions import db
    camp = create(auth_client)
    with app.app_context():
        j = ContentJob(id="c" * 32, campaign_id=camp["id"], kind="video", status="running")
        j.snapshot = {"provider": "higgsfield", "request_id": "11111111-2222-3333-4444-555555555555"}
        db.session.add(j)
        db.session.add(ContentJob(id="d" * 32, campaign_id=camp["id"], kind="video", status="queued"))
        db.session.commit()
    sent = []
    monkeypatch.setattr(higgsfield, "cancel", lambda rid: (sent.append(rid), False)[1])
    out = auth_client.post(f"/api/content/jobs/{'c' * 32}/cancel", json={}).get_json()
    assert out["ok"] is False and "이미 생성이 시작돼" in out["message"] and sent == ["11111111-2222-3333-4444-555555555555"]
    monkeypatch.setattr(higgsfield, "cancel", lambda rid: True)
    out = auth_client.post(f"/api/content/jobs/{'c' * 32}/cancel", json={}).get_json()
    assert out["ok"] is True and out["job"]["cancelable"] is True
    out = auth_client.post(f"/api/content/jobs/{'d' * 32}/cancel", json={}).get_json()  # 아직 제출 전
    assert out["ok"] is False and "제출 전" in out["message"]
    assert auth_client.post("/api/content/jobs/nope!/cancel", json={}).status_code == 404


def test_higgsfield_helpers():
    assert higgsfield.nearest_ratio("4:5 세로", higgsfield.IMAGE_RATIOS, "1:1") == "3:4"
    assert higgsfield.nearest_ratio("16:9", higgsfield.VIDEO_RATIOS, "9:16") == "16:9"
    assert higgsfield.nearest_ratio("릴스", higgsfield.VIDEO_RATIOS, "1:1") == "9:16"
    assert higgsfield.nearest_ratio("0:0", higgsfield.VIDEO_RATIOS, "9:16") == "9:16"
    assert higgsfield.nearest_ratio(None, higgsfield.VIDEO_RATIOS, "9:16") == "9:16"
    assert media.video_seconds("15초 세로형") == 15 and media.video_seconds("2초") == 4 and media.video_seconds("") == 5
    assert media.file_ext(MP4) == ".mp4" and media.file_ext(JPG) == ".jpg" and media.file_ext(PNG) == ".png"
    assert media.file_ext(b"nope") is None
    assert higgsfield.check_connection()["checked"] is False  # 키 없으면 네트워크 호출 없음


# ───────── 합쳐진 대화: 흐름 · 채널 · 보기 칩 · 만들기 제안 (2026-10-24) ─────────

def kickoff(client, camp):
    res = client.post(f"/api/content/campaigns/{camp['id']}/chat", json={"kickoff": True, "expected_revision": camp["revision"]})
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def test_flow_and_channel_profile_in_context(auth_client, ai):
    camp = create(auth_client, content_type="image", channels=["instagram"])
    assert camp["flow"] == ["concept", "story", "design_note", "emphasis", "ratio"]
    assert camp["decisions"]["ratio"]["value"] == "4:5 세로" and camp["decisions"]["ratio"]["status"] == "proposed"
    ai.add("content_turn", dict(turn("인스타 이미지로 시작할게요. 원하는 컨셉이나 레퍼런스가 있나요?"),
                                offer={"type": "generate_image", "summary": "x"}))
    out = kickoff(auth_client, camp)
    ctx = ai.ctx()
    assert [f["key"] for f in ctx["flow"]] == camp["flow"] and ctx["flow"][0]["status"] == "undecided"
    assert ctx["channel_profile"].startswith("Instagram") and ctx["user_references"] is None
    assert out["reply"]["payload"]["offer"] is None  # 첫 답변에는 만들기 제안 없음
    assert "흐름" in ai.calls[-1]["system"] and "offer" in ai.calls[-1]["system"]
    # 비율·크기는 흐름에서 물음 — 채널 추천 보기가 첫 번째 (2026-10-25) · 캡션 · 멘트는 흐름 질문에 없음
    ratio_item = next(f for f in ctx["flow"] if f["key"] == "ratio")
    assert ratio_item["recommended_options"][0]["value"].startswith("4:5")
    amazon = create(auth_client, content_type="image", channels=["amazon"])
    assert amazon["flow"] == ["concept", "story", "design_note", "emphasis", "ratio"]
    assert amazon["decisions"]["ratio"]["value"] == "1:1 정사각"
    assert O.ratio_options({"content_type": "image", "channels": ["amazon"]})[0][0].startswith("1:1")
    assert O.ratio_options({"content_type": "image", "channels": ["tiktok"]})[0][0].startswith("9:16")
    assert "ratio" not in create(auth_client, content_type="shortform", channels=["instagram"])["decisions"]
    assert create(auth_client, content_type="cardnews", channels=["oliveyoung"])["flow"] == [
        "content_points", "concept", "emphasis", "slide_count", "ratio"]
    assert create(auth_client, content_type="shortform", channels=["tiktok"])["flow"][:3] == ["concept", "duration", "ratio"]
    assert O.ratio_options({"content_type": "shortform", "channels": ["instagram"]})[0][1] == "9:16 세로"
    assert "caption" not in create(auth_client, content_type="crm", channels=["crm"])["flow"]
    from tabs.content import prompts
    assert set(prompts.CHANNEL_PROFILES) == set(O.CHANNELS)
    assert "확인 필요" not in prompts.CHANNEL_PROFILES["oliveyoung"] and "입점" in prompts.CHANNEL_PROFILES["oliveyoung"]
    assert all(k in prompts.FLOW_HINTS for steps in O.CONTENT_FLOWS.values() for k in steps)


def test_chips_and_offer_are_sanitized(auth_client, ai):
    camp = create(auth_client)
    ai.add("content_turn", dict(turn("비율은 어떻게 할까요?"),
                                chips=[{"label": "4:5", "value": "4:5 세로로", "reason": "피드에서 크게 보여요"},
                                       {"label": "중복", "value": "4:5 세로로", "reason": ""},
                                       {"label": "", "value": "1:1", "reason": ""}, {"label": "x", "value": " ", "reason": ""},
                                       {"label": "9:16", "value": "9:16", "reason": ""}, {"label": "넘침", "value": "16:9", "reason": ""}],
                                offer={"type": "select_version", "summary": "안 됨"}))
    pay = chat(auth_client, camp, "비율 추천해줘").get_json()["reply"]["payload"]
    # 추천 답은 ①②③ 세 개까지 · 이유 한 줄 함께
    assert [c["value"] for c in pay["chips"]] == ["4:5 세로로", "1:1", "9:16"] and pay["chips"][1]["label"] == "1:1"
    assert pay["chips"][0]["reason"] == "피드에서 크게 보여요"
    assert pay["offer"] is None  # 허용하지 않은 제안 종류는 버림


def test_offer_then_agreement_runs_offer(auth_client, ai):
    camp = create(auth_client)
    summary = "검정 배경 · 네온 · 아침 루틴 장면 · 4:5"
    ai.add("content_turn", dict(turn("정리하면 이래요. 이대로 만들까요?"), offer={"type": "draft_copy", "summary": summary}))
    out = chat(auth_client, camp, "배경은 검정으로 하고 아침 루틴 느낌이면 좋겠어").get_json()
    assert out["reply"]["payload"]["offer"]["type"] == "draft_copy" and out["reply"]["payload"]["action"]["status"] == "none"
    # '좋아요'만 해도 실행 (모델이 실행안을 빠뜨려도 서버가 제안대로)
    ai.add("content_turn", turn("만들어 볼게요."))
    ai.add("content_draft", {"note": "", "drafts": [draft("**Headline** Restart Your Day")]})
    out = chat(auth_client, out["campaign"], "좋아요").get_json()
    pay = out["reply"]["payload"]
    assert pay["action"]["type"] == "draft_copy" and pay["action"]["status"] == "done" and len(out["newVersionIds"]) == 1
    assert pay["agreedOffer"]["summary"] == summary and pay["offer"] is None and pay["signals"]["agreement"] is True
    assert ai.ctx(-2)["server_signals"]["agreed_to_offer"] is True
    # 제안이 이미 쓰였으니 다음 '좋아요'는 실행 아님
    ai.add("content_turn", turn("고마워요!", action="draft_copy"))
    pay = chat(auth_client, out["campaign"], "좋아요").get_json()["reply"]["payload"]
    assert pay["action"]["status"] == "skipped" and "not_explicit" not in json.dumps(pay)  # 이유 코드는 화면에 안 나감


def test_agreement_needs_offer_and_no_changes(auth_client, ai):
    camp = create(auth_client)
    ai.add("content_turn", turn("좋아요!", action="draft_copy"))
    out = chat(auth_client, camp, "좋아요").get_json()
    assert out["reply"]["payload"]["action"]["status"] == "skipped" and out["newVersionIds"] == []
    ai.add("content_turn", dict(turn("이대로 만들까요?"), offer={"type": "draft_copy", "summary": "s"}))
    out = chat(auth_client, out["campaign"], "컨셉은 네온으로").get_json()
    ai.add("content_turn", turn("흰색으로 바꿀게요."))
    out = chat(auth_client, out["campaign"], "좋아요 근데 배경은 흰색으로").get_json()  # 수정이 섞이면 동의가 아님
    assert out["reply"]["payload"]["action"]["status"] == "none" and out["newVersionIds"] == []


def test_agreement_heuristics():
    for t in ("좋아요", "네", "응 만들어줘", "ㄱㄱ", "좋아요, 이대로 만들어줘", "ok", "진행해 주세요"):
        assert heuristics.is_agreement(t), t
    for t in ("", "아니요", "좋아요 근데 배경은 흰색으로", "잠깐만", "좋아요?", "이거 말고 다른 거", "네" * 41):
        assert not heuristics.is_agreement(t), t
    assert heuristics.mentions_reference("아까 올린 레퍼런스처럼") and heuristics.mentions_reference("참고 이미지 느낌으로")
    assert not heuristics.mentions_reference("검정 배경으로 해줘")


# ───────── 레퍼런스 이미지 ─────────

def jpeg(w=60, h=40):
    """SOF0 머리만 있는 작은 JPEG (가로·세로를 읽을 수 있음)."""
    return (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 9 + b"\xff\xd9")


def upload_ref(client, camp, data=None, name="ref.jpg", note=""):
    return client.post(f"/api/content/campaigns/{camp['id']}/references",
                       data={"file": (io.BytesIO(jpeg() if data is None else data), name), "note": note},
                       content_type="multipart/form-data")


def test_image_dims():
    assert media.image_dims(jpeg(60, 40)) == (60, 40)
    assert media.image_dims(PNG) == (1, 1)
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 8 + (99).to_bytes(3, "little") + (49).to_bytes(3, "little")
    assert media.image_dims(webp) == (100, 50)
    assert media.image_dims(b"\xff\xd8\xff\x00junk") is None


def test_reference_upload_summary_and_chat_images(app, auth_client, ai):
    camp = create(auth_client)
    ai.add("content_reference", {"summary": "어두운 배경 · 제품 클로즈업 · 네온 조명"})
    res = upload_ref(auth_client, camp, note="이 느낌")
    assert res.status_code == 201, res.get_json()
    c = res.get_json()["campaign"]
    assert res.get_json()["warning"] is None
    ref = c["references"][0]
    assert ref["summary"].startswith("어두운") and ref["note"] == "이 느낌" and (ref["width"], ref["height"]) == (60, 40)
    assert ai.calls[-1]["images"] == 1 and ai.calls[-1]["name"] == "content_reference"
    assert c["messages"][-1]["role"] == "event" and "레퍼런스" in c["messages"][-1]["text"]
    assert auth_client.get(ref["url"]).status_code == 200  # 업로드 폴더 파일 제공
    # 올린 뒤 첫 대화: 이미지를 직접 보여 줌 · 요약도 맥락에
    ai.add("content_turn", turn("레퍼런스처럼 어두운 배경에 네온으로 가 볼까요?"))
    out = chat(auth_client, c, "이 느낌 어때?").get_json()
    assert ai.calls[-1]["images"] == 1 and out["reply"]["payload"]["referenceImages"] == 1
    assert ai.ctx()["user_references"][0]["summary"].startswith("어두운")
    assert ai.ctx()["server_signals"]["reference_images_attached"] == 1
    # 그다음엔 요약만 (비용 절약) · 레퍼런스를 말하면 다시 이미지
    ai.add("content_turn", turn("네."), turn("다시 볼게요."))
    out = chat(auth_client, out["campaign"], "배경은 검정").get_json()
    assert ai.calls[-1]["images"] == 0 and out["reply"]["payload"]["referenceImages"] == 0
    chat(auth_client, out["campaign"], "레퍼런스 구도를 더 따라가 줘")
    assert ai.calls[-1]["images"] == 1
    # 지우면 파일도 지움
    folder = Path(app.config["DATA_DIR"]) / "uploads" / "content"
    name = ref["url"].rsplit("/", 1)[1]
    assert (folder / name).is_file()
    res = auth_client.delete(f"/api/content/campaigns/{camp['id']}/references/{ref['id']}")
    assert res.status_code == 200 and res.get_json()["campaign"]["references"] == []
    assert not (folder / name).exists() and auth_client.get(ref["url"]).status_code == 404


def test_reference_limits_and_types(auth_client, ai, monkeypatch):
    camp = create(auth_client)
    assert upload_ref(auth_client, camp, data=b"GIF89a" + b"\x00" * 30, name="a.gif").status_code == 400
    assert upload_ref(auth_client, camp, data=jpeg(8000, 10)).status_code == 400  # 너무 큼
    assert upload_ref(auth_client, camp, data=b"\xff\xd8\xff" + b"\x00" * (media.REF_MAX_BYTES)).status_code == 413
    other = create(auth_client)
    assert auth_client.delete(f"/api/content/campaigns/{other['id']}/references/999").status_code == 404
    ai.add("content_reference", {"summary": "a"}, {"summary": "b"}, {"summary": "c"})
    for _ in range(3):
        assert upload_ref(auth_client, camp).status_code == 201
    res = upload_ref(auth_client, camp)
    assert res.status_code == 400 and "3장" in res.get_json()["error"]
    # 다른 캠페인의 레퍼런스는 지울 수 없음
    rid = get(auth_client, camp["id"])["references"][0]["id"]
    assert auth_client.delete(f"/api/content/campaigns/{other['id']}/references/{rid}").status_code == 404


def test_reference_without_key_or_ai_failure_keeps_image(auth_client, monkeypatch):
    camp = create(auth_client)
    monkeypatch.setattr(providers, "ready", lambda: False)
    res = upload_ref(auth_client, camp)
    assert res.status_code == 201 and "키가 없어" in res.get_json()["warning"]
    assert res.get_json()["campaign"]["references"][0]["summary"] == ""
    monkeypatch.setattr(providers, "ready", lambda: True)

    def boom(*a, **k):
        raise providers.AIError("모델이 이미지를 못 읽음")
    monkeypatch.setattr(providers, "call_json_images", boom)
    res = upload_ref(auth_client, camp)
    assert res.status_code == 201 and "AI가 보지 못했어요" in res.get_json()["warning"]
    assert len(res.get_json()["campaign"]["references"]) == 2


def test_delete_campaign_removes_reference_files(app, auth_client, ai):
    camp = create(auth_client)
    ai.add("content_reference", {"summary": "s"})
    name = upload_ref(auth_client, camp).get_json()["campaign"]["references"][0]["url"].rsplit("/", 1)[1]
    folder = Path(app.config["DATA_DIR"]) / "uploads" / "content"
    assert (folder / name).is_file()
    assert auth_client.delete(f"/api/content/campaigns/{camp['id']}").status_code == 200
    assert not (folder / name).exists()


def test_copy_campaign_to_other_channel(auth_client, app):
    camp = create(auth_client, goal="구매 유도")
    camp = auth_client.post(f"/api/content/campaigns/{camp['id']}/decisions", json={
        "expected_revision": camp["revision"], "changes": [
            {"field": "decisions.concept", "value": "검정 배경 네온", "status": "confirmed"},
            {"field": "decisions.caption", "value": "인스타 캡션", "status": "confirmed"}]}).get_json()["campaign"]
    out = _manual(auth_client, camp["id"], "copy", "**Headline** Restart Your Day", "A안")
    v = next(x for x in out["campaign"]["versions"] if x["id"] == out["versionId"])
    url = f"/api/content/campaigns/{camp['id']}/copy"
    res = auth_client.post(url, json={"channel": "tiktok"})
    assert res.status_code == 201, res.get_json()
    new = res.get_json()["campaign"]
    assert new["id"] != camp["id"] and new["sheet"]["channels"] == ["tiktok"] and new["sheet"]["goal"] == "구매 유도"
    assert new["decisions"]["concept"]["value"] == "검정 배경 네온" and "caption" not in new["decisions"]
    assert new["decisions"]["ratio"] == dict(new["decisions"]["ratio"], value="9:16 세로", status="proposed")  # 새 채널 기본값
    assert "Restart Your Day" in new["referenceText"] and f"{v['label']} v{v['seq']}" in new["referenceText"]
    assert new["versions"] == [] and [m["role"] for m in new["messages"]] == ["event"]
    # 같은 채널 · CRM · 없는 채널은 거절, 로그인 없이는 안 됨
    assert auth_client.post(url, json={"channel": "instagram"}).status_code == 400
    assert auth_client.post(url, json={"channel": "crm"}).status_code == 400
    assert auth_client.post(url, json={"channel": "nope"}).status_code == 400
    crm = create(auth_client, content_type="crm", channels=["crm"])
    assert auth_client.post(f"/api/content/campaigns/{crm['id']}/copy", json={"channel": "instagram"}).status_code == 400
    assert app.test_client().post(url, json={"channel": "tiktok"}).status_code in (302, 401, 403)


# ───────── 슬로건 2개 · 캡션·해시태그 자동 추천 · 영상 썸네일 (2026-10-25) ─────────

def test_two_official_slogans():
    assert brandkit.slogans() == ("Restart Your Day", "Buy Your Tomorrow")
    assert media.official_slogan("BUY YOUR TOMORROW!") == "Buy Your Tomorrow"
    assert media.official_slogan("restart your day.") == "Restart Your Day" and media.official_slogan("Hello") is None
    pick = lambda v: media.chosen_slogans({"slogan_choice": {"value": v}} if v else {})
    assert pick(None) == ["Restart Your Day"] and pick("Buy Your Tomorrow") == ["Buy Your Tomorrow"]
    assert pick("둘 다") == ["Restart Your Day", "Buy Your Tomorrow"]
    s = sheet()
    assert review.keyword_check("Buy Your Tomorrow. Restart Your Day.", "copy", s, {})["status"] == "clear"
    kw = review.keyword_check("buy your tomorrow", "copy", s, {})
    assert kw["items"][0]["code"] == "slogan" and kw["items"][0]["level"] == "error"
    assert review.keyword_check("BUY YOUR TOMORROW", "copy", s, {})["items"][0]["level"] == "warn"
    assert media.clean_layers([{"role": "slogan", "text": "buy your tomorrow"}])[0]["text"] == "Buy Your Tomorrow"
    assert "Buy Your Tomorrow" in conversation.prompts.CONVERSATION_CORE_V3 and "Buy Your Tomorrow" in O.example_suggestions(
        "goal", sheet(brand_focus=False, product_ids=["jelly_original"]))[2]["reason"]


def test_image_slogan_choice_is_drawn(auth_client, ai, sync_jobs, monkeypatch):
    sent = []
    monkeypatch.setattr(providers, "generate_image", lambda prompt, size, model: (sent.append(prompt), (PNG, {}))[1])
    camp = create(auth_client)
    camp = auth_client.post(f"/api/content/campaigns/{camp['id']}/decisions", json={
        "expected_revision": camp["revision"], "changes": [{"field": "decisions.slogan_choice", "value": "Buy Your Tomorrow",
                                                            "status": "confirmed"}]}).get_json()["campaign"]
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", {"prompt_en": "Neon on black.", "keep": [], "change": [], "text_layers": [],
                                  "notes_ko": "시안", "flavors": [], "include_character": False})
    chat(auth_client, camp, "이미지 만들어줘")
    img = next(v for v in get(auth_client, camp["id"])["versions"] if v["kind"] == "image")
    assert img["image"]["image_text"] == [{"role": "slogan", "text": "Buy Your Tomorrow"}]
    assert 'slogan: "Buy Your Tomorrow"' in sent[0]


def test_image_gets_three_captions_and_hashtags(auth_client, ai, sync_jobs, monkeypatch):
    monkeypatch.setattr(providers, "edit_image", lambda paths, prompt, size, model: (PNG, {}))  # 제품 사진을 참고로 넣어 생성
    camp = create(auth_client, brand_focus=False, product_ids=["drink_original"])
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", {"prompt_en": "Bottles on black.", "keep": [], "change": [], "text_layers": [],
                                  "notes_ko": "시안", "flavors": [], "include_character": False})
    ai.add("content_caption", {"captions": [{"angle": "감성형", "text": "Your morning, rebooted. Restart Your Day."},
                                            {"angle": "정보형", "text": "Reboot Drink Original in a 100ml glass bottle."},
                                            {"angle": "행동 유도형", "text": "Buy Your Tomorrow — shop now."},
                                            {"angle": "남는 안", "text": "버림"}],
                               "hashtags": ["#Reboot", "neon vibes", "#reboot", "#Morning!"], "note_ko": "검정 배경 병 사진을 보고 썼어요."})
    chat(auth_client, camp, "이미지 만들어줘")
    img = next(v for v in get(auth_client, camp["id"])["versions"] if v["kind"] == "image")
    s = img["social"]
    assert s["status"] == "done" and s["basis"] == "image" and len(s["captions"]) == 3
    assert s["captions"][2] == {"angle": "행동 유도형", "text": "Buy Your Tomorrow — shop now."}
    assert s["hashtags"] == ["#Reboot", "#neonvibes", "#Morning"]  # 기호 제거 · 대소문자 중복 제거
    assert s["review"]["method"] == "키워드 확인"
    call = next(c for c in ai.calls if c["name"] == "content_caption")
    user = json.loads(call["user"])
    assert call["images"] == 1 and user["basis"] == "image" and user["channel"] == "instagram"
    assert user["channel_profile"].startswith("Instagram") and "Buy Your Tomorrow" in call["system"]


def test_caption_failure_keeps_image(auth_client, ai, sync_jobs, monkeypatch):
    monkeypatch.setattr(providers, "generate_image", lambda prompt, size, model: (PNG, {}))
    camp = create(auth_client)
    ai.add("content_turn", turn(intent="generate_media", action="generate_image"))
    ai.add("content_image_plan", {"prompt_en": "Neon.", "keep": [], "change": [], "text_layers": [], "notes_ko": "시안",
                                  "flavors": [], "include_character": False})
    chat(auth_client, camp, "이미지 만들어줘")  # 캡션 응답 없음 → AI 실패
    c = get(auth_client, camp["id"])
    img = next(v for v in c["versions"] if v["kind"] == "image")
    assert c["jobs"][0]["status"] == "succeeded" and img["social"]["status"] == "failed" and img["social"]["captions"] == []
    assert "추천하지 못했어요" in img["social"]["note"]


def test_video_captions_use_scene_plan(auth_client, ai, sync_jobs, monkeypatch):
    FakeHiggsfield(monkeypatch)
    camp = create(auth_client, content_type="shortform", channels=["tiktok"], brand_focus=False, product_ids=["jelly_original"])
    camp = set_settings(auth_client, camp, video_mode="higgsfield")
    ai.add("content_turn", turn(intent="generate_media", action="generate_video"))
    ai.add("content_video_plan", {"prompt_en": "Pouch spins on black.", "notes_ko": "회전 컷", "flavors": [],
                                  "include_character": False})
    ai.add("content_caption", {"captions": [{"angle": a, "text": f"Caption {a}"} for a in ("A", "B", "C")],
                               "hashtags": ["#Reboot", "#JellyTime", "#NeonNight"], "note_ko": "장면 설명으로 썼어요."})
    chat(auth_client, camp, "실제 영상 만들어줘")
    c = get(auth_client, camp["id"])
    video = next(v for v in c["versions"] if v["kind"] == "video")
    assert video["social"]["basis"] == "video_plan" and len(video["social"]["captions"]) == 3
    call = next(x for x in ai.calls if x["name"] == "content_caption")
    user = json.loads(call["user"])
    assert call["images"] == 0 and "Pouch spins on black." in user["video_plan"] and user["channel"] == "tiktok"
    # 내 캠페인 목록: 영상만 있는 캠페인도 대표 파일(mp4)이 썸네일로 나감
    items = auth_client.get("/api/content/campaigns").get_json()["items"]
    assert next(i for i in items if i["id"] == camp["id"])["cover"].endswith(".mp4")
