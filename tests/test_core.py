"""화면 셸 · 파트 파일 · 공용 저장소 · 작업 로그 · 상태 · 크론."""
import json
from pathlib import Path


def test_root_redirects(client):
    res = client.get("/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/dashboard")


def test_pages_require_login(client):
    for url in ("/dashboard", "/dashboard/compliance", "/parts/compliance/compliance.js"):
        res = client.get(url)
        assert res.status_code == 302 and "/login" in res.headers["Location"], url


def test_api_requires_login(client):
    res = client.get("/api/health")
    assert res.status_code == 401 and res.is_json and "로그인" in res.get_json()["error"]


def test_healthz_is_public(client):
    assert client.get("/healthz").get_data(as_text=True) == "ok"


def test_dashboard_renders_shell_and_boot(auth_client):
    res = auth_client.get("/dashboard")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert 'id="part-root"' in html and 'id="top-nav"' in html and 'id="subnav"' in html  # 시안 v3: 상단 알약 메뉴 + 왼쪽 하위 메뉴
    assert "테스터님" in html  # 홈 인사말
    boot = json.loads(html.split('<script id="reboot-boot" type="application/json">')[1].split("</script>")[0])
    assert boot["part"] == "home" and boot["user"]["email"] == "tester@reboot.test"
    assert "csrf" in boot and isinstance(boot["store"], dict)


def test_each_part_page(auth_client):
    for pid in ("insights", "content", "compliance", "cs", "partners", "performance", "settings"):
        res = auth_client.get(f"/dashboard/{pid}")
        assert res.status_code == 200, pid
    assert auth_client.get("/dashboard/nope").status_code == 404


def test_compliance_scripts_loaded_in_order(auth_client):
    html = auth_client.get("/dashboard/compliance").get_data(as_text=True)
    i_core = html.index("/core/core.js")
    i_data = html.index("/parts/compliance/data.js")
    i_main = html.index("/parts/compliance/compliance.js")
    assert i_core < i_data < i_main


def test_part_file_serving_and_override(app, auth_client):
    res = auth_client.get("/parts/compliance/data.js")
    assert res.status_code == 200 and b"REBOOT_DATA" in res.data
    assert auth_client.get("/parts/../server/config.py").status_code == 404
    assert auth_client.get("/parts/compliance/nothing.js").status_code == 404
    override = Path(app.config["DATA_DIR"]) / "parts_override" / "compliance" / "monitor-data.js"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("/* override */ window.REBOOT_MONITOR = {sources: []};", encoding="utf-8")
    assert b"override" in auth_client.get("/parts/compliance/monitor-data.js").data


def test_store_roundtrip(auth_client):
    value = {"items": [{"id": 1, "text": "Prevents hangovers.", "status": "검토 요청"}]}
    res = auth_client.put("/api/store/compliance/history", json=value)
    assert res.status_code == 200 and res.get_json()["ok"]
    assert auth_client.get("/api/store").get_json()["compliance"]["history"] == value
    html = auth_client.get("/dashboard/compliance").get_data(as_text=True)
    assert "Prevents hangovers." in html  # 페이지에 미리 실려 옴 (동기 읽기)
    assert auth_client.delete("/api/store/compliance/history").status_code == 200
    assert "history" not in auth_client.get("/api/store").get_json().get("compliance", {})


def test_store_rejects_bad_names_and_types(auth_client):
    assert auth_client.put("/api/store/BAD/x", json=1).status_code == 400
    assert auth_client.put("/api/store/cs/bad key!", json=1).status_code in (400, 404)
    res = auth_client.put("/api/store/cs/inquiries", data="{}", content_type="text/plain")
    assert res.status_code == 415


def test_store_scalar_values(auth_client):
    # core.js는 JSON.stringify(value)를 application/json으로 보냄 (null → "null")
    for v in (0, "text", None, [1, 2], True):
        res = auth_client.put("/api/store/cs/value", data=json.dumps(v), content_type="application/json")
        assert res.status_code == 200
        assert auth_client.get("/api/store").get_json()["cs"]["value"] == v


def test_worklog_add_and_redact(auth_client):
    res = auth_client.post("/api/worklog", json={"part": "insights", "kind": "수집", "action": "주간 데이터 수집",
                                                "detail": {"to": "someone@example.com", "key": "sk-abcdefghijklmnop"}})
    assert res.status_code == 200
    items = auth_client.get("/api/worklog?part=insights").get_json()["items"]
    assert items[0]["action"] == "주간 데이터 수집" and items[0]["user"] == "테스터"
    dumped = json.dumps(items[0]["detail"], ensure_ascii=False)
    assert "someone@example.com" not in dumped and "sk-abcdefghijklmnop" not in dumped
    assert auth_client.post("/api/worklog", json={"action": ""}).status_code == 400


def test_health_shape(auth_client):
    h = auth_client.get("/api/health").get_json()
    for k in ("ok", "ai", "gmail", "gmailQuery", "monitor", "openai", "keys"):
        assert k in h
    assert h["ai"] is False and h["openai"] is False


def test_jobs_daily_token(client):
    assert client.post("/api/jobs/daily").status_code == 403
    assert client.post("/api/jobs/daily", headers={"X-Cron-Token": "wrong"}).status_code == 403


def test_run_background_is_exclusive(app):
    import threading

    from server import jobs
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)

    with app.app_context():
        t = jobs.run_background(app, "t-job", slow)
        assert t is not None and started.wait(5)
        assert jobs.run_background(app, "t-job", slow) is None  # 실행 중이면 시작하지 않음
        assert jobs.is_running("t-job")
        release.set()
        t.join(5)
        assert not jobs.is_running("t-job")  # 끝나면 잠금 해제


def test_api_404_is_json(auth_client):
    res = auth_client.get("/api/does-not-exist")
    assert res.status_code == 404 and res.is_json
