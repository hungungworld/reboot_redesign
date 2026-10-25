"""공용 기준 데이터(시즌 · 팩트시트) · 계정 관리 API."""
from conftest import PASSWORD


def test_seasons_seeded_with_dday(auth_client):
    data = auth_client.get("/api/seasons?all=1").get_json()
    names = [s["name"] for s in data["items"]]
    assert "Halloween" in names and "Thanksgiving Eve" in names and "Super Bowl LXI" in names
    s = data["items"][0]
    for k in ("dday", "inLead", "ongoing", "leadDays", "priority"):
        assert k in s


def test_season_crud_and_validation(auth_client):
    res = auth_client.post("/api/seasons", json={"name": "Test Day", "start": "2027-01-10", "category": "occasion",
                                                 "priority": 3, "leadDays": 10})
    assert res.status_code == 201
    sid = res.get_json()["id"]
    assert res.get_json()["end"] == "2027-01-10"
    assert auth_client.put(f"/api/seasons/{sid}", json={"end": "2027-01-01"}).status_code == 400  # 종료일 < 시작일
    assert auth_client.put(f"/api/seasons/{sid}", json={"category": "party"}).status_code == 400
    assert auth_client.put(f"/api/seasons/{sid}", json={"notes": "메모"}).get_json()["notes"] == "메모"
    assert auth_client.delete(f"/api/seasons/{sid}").status_code == 200
    assert auth_client.delete(f"/api/seasons/{sid}").status_code == 404


def test_seasons_csv_roundtrip(auth_client):
    csv_text = auth_client.get("/api/seasons/export.csv").get_data(as_text=True)
    assert csv_text.lstrip("﻿").startswith("name,start_date")
    res = auth_client.post("/api/seasons/import", json={"csv": "name,start_date\nOnly One,2027-06-01\n", "mode": "replace"})
    assert res.status_code == 200 and res.get_json()["count"] == 1
    bad = auth_client.post("/api/seasons/import", json={"csv": "title,date\nx,y\n"})
    assert bad.status_code == 400


def test_facts_seeded_and_update_rules(auth_client):
    data = auth_client.get("/api/facts").get_json()
    ids = [p["id"] for p in data["products"]]
    assert ids == ["jelly_original", "jelly_balance", "drink_original", "drink_balance"]
    assert data["brand"]["slogan"] == "Restart Your Day"
    assert data["flat"]["brand"] == "REBOOT" and data["flat"]["flavor"] is None
    # 출처 없이 '확인 완료' 불가
    assert auth_client.put("/api/facts/jelly_original", json={"evidence": "확인 완료"}).status_code == 400
    ok = auth_client.put("/api/facts/jelly_original", json={
        "evidence": "확인 완료", "sources": [{"field": "flavors", "url": "https://example.com/a"}], "price": "3.5"})
    assert ok.status_code == 200 and ok.get_json()["price"] == 3.5
    assert auth_client.put("/api/facts/unknown", json={"name": "x"}).status_code == 404


def test_users_admin_only(client, login):
    login(email="member@reboot.test")
    assert client.get("/api/users").status_code == 403


def test_users_create_update_and_last_admin(auth_client, client, login):
    res = auth_client.post("/api/users", json={"email": "new@reboot.local", "name": "새 팀원", "password": "short"})
    assert res.status_code == 400
    res = auth_client.post("/api/users", json={"email": "new@reboot.local", "name": "새 팀원", "password": "longenough1"})
    assert res.status_code == 201
    uid = res.get_json()["id"]
    assert auth_client.post("/api/users", json={"email": "NEW@reboot.local", "password": "longenough1"}).status_code == 409
    me = [u for u in auth_client.get("/api/users").get_json()["items"] if u["email"] == "tester@reboot.test"][0]
    assert auth_client.patch(f"/api/users/{me['id']}", json={"isAdmin": False}).status_code == 400  # 마지막 관리자
    assert auth_client.patch(f"/api/users/{uid}", json={"isActive": False}).get_json()["isActive"] is False


def test_me_password_change(auth_client):
    assert auth_client.post("/api/me/password", json={"current": "wrong", "new": "another-pass-1"}).status_code == 400
    res = auth_client.post("/api/me/password", json={"current": PASSWORD, "new": "another-pass-1"})
    assert res.status_code == 200
    assert auth_client.get("/dashboard").status_code == 200  # 바꾼 브라우저는 로그인 유지
