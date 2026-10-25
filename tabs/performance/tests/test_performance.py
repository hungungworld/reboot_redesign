"""성과분석 API — CSV 검사·저장 · 기간 비교 · 환율 · AI 제안 (인터넷 · 키 없이, 외부 호출은 가짜로)."""
import csv
import io
import json
from datetime import date
from pathlib import Path

import pytest

from tabs.performance import csvdata

HEADER = "날짜,채널,광고비,노출수,클릭수,전환수,매출"
SAMPLE = Path(__file__).resolve().parent.parent / "data" / "performance_sample.csv"


def _sample_rows():
    with SAMPLE.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _sum(rows, start, end, col):
    return sum(int(r[col]) for r in rows if start <= r["날짜"] <= end)


@pytest.fixture()
def today(monkeypatch):
    monkeypatch.setattr("server.catalog.service._today", lambda: date(2026, 10, 23))
    return date(2026, 10, 23)


# ───────── 로그인 · 샘플 ─────────

def test_requires_login(client):
    for url in ("/api/performance/data", "/api/performance/summary", "/api/performance/fx"):
        assert client.get(url).status_code == 401, url
    assert client.put("/api/performance", json={"csv": HEADER}).status_code == 401


def test_sample_data_is_labeled(auth_client):
    j = auth_client.get("/api/performance/data").get_json()
    assert j["isSample"] is True and j["source"] == "sample"
    assert j["range"] == {"start": "2026-09-01", "end": "2026-10-21"}
    assert sorted(j["channels"]) == ["Amazon", "Google", "Instagram", "TikTok"]
    assert len(j["rows"]) == 51 * 4
    ex = next(r for r in j["rows"] if r["date"] == "2026-10-19" and r["channel"] == "Instagram")
    assert ex == {"date": "2026-10-19", "channel": "Instagram", "spend": 300000, "impressions": 52000,
                  "clicks": 1300, "conversions": 42, "revenue": 1260000}  # README 예시 행


def test_sample_file_header_and_values():
    text = SAMPLE.read_text(encoding="utf-8")
    assert text.splitlines()[0] == HEADER
    rows, info = csvdata.parse_csv(text)
    assert info["duplicates"] == 0 and all(r["spend"] > 0 and r["revenue"] > 0 for r in rows)


# ───────── 기간 요약 (홈 KPI · weekSummary) ─────────

def test_summary_latest_week_matches_csv(auth_client):
    rows = _sample_rows()
    j = auth_client.get("/api/performance/summary").get_json()
    assert (j["start"], j["end"], j["days"]) == ("2026-10-15", "2026-10-21", 7)
    assert j["sample"] is True
    rev = _sum(rows, "2026-10-15", "2026-10-21", "매출")
    spend = _sum(rows, "2026-10-15", "2026-10-21", "광고비")
    assert j["revenue"] == rev and j["spend"] == spend
    assert j["roas"] == round(rev / spend * 100, 2)  # 합계로 다시 계산
    assert j["compare"] == {"start": "2026-10-08", "end": "2026-10-14", "days": 7, "label": "전주 대비"}
    prev = _sum(rows, "2026-10-08", "2026-10-14", "매출")
    assert j["change"]["revenue"] == round((rev - prev) / prev * 100, 2)
    assert len(j["daily"]) == 7 and sum(d["revenue"] for d in j["daily"]) == rev


def test_summary_custom_period_and_no_compare(auth_client):
    j = auth_client.get("/api/performance/summary?start=2026-10-10&end=2026-10-12").get_json()
    assert j["compare"] == {"start": "2026-10-07", "end": "2026-10-09", "days": 3, "label": "직전 기간 대비"}
    # 비교 기간이 CSV 시작일(09-01)보다 앞 → 비교하지 않음
    j = auth_client.get("/api/performance/summary?start=2026-09-01&end=2026-09-03").get_json()
    assert j["compare"] is None and j["change"] is None
    # 범위 밖은 CSV 범위 안으로
    j = auth_client.get("/api/performance/summary?start=2026-10-20&end=2026-12-31").get_json()
    assert (j["start"], j["end"]) == ("2026-10-20", "2026-10-21")
    assert auth_client.get("/api/performance/summary?start=2026-13-01").status_code == 400
    assert auth_client.get("/api/performance/summary?start=2027-01-01&end=2027-01-05").status_code == 400


# ───────── 업로드 ─────────

def test_upload_replace_accepts_korean_formats(app, auth_client):
    text = (HEADER + "\n"
            '2026/10/19,Instagram,"300,000",52000,1300,42,1260000원\n'
            "2026.10.20,TikTok,₩200000,90000,1500,20,540000\n"
            "2026. 10. 21.,Amazon,250000,27000,300,30,990000\n")
    res = auth_client.put("/api/performance", json={"csv": text, "mode": "replace", "filename": "w42.csv"})
    assert res.status_code == 200, res.get_json()
    j = res.get_json()
    assert j["isSample"] is False and j["range"] == {"start": "2026-10-19", "end": "2026-10-21"}
    assert j["rows"][0] == {"date": "2026-10-19", "channel": "Instagram", "spend": 300000, "impressions": 52000,
                           "clicks": 1300, "conversions": 42, "revenue": 1260000}
    assert j["rows"][1]["spend"] == 200000
    data = auth_client.get("/api/performance/data").get_json()
    assert data["isSample"] is False and data["meta"]["filename"] == "w42.csv" and data["meta"]["fromSample"] is True
    saved = Path(app.config["DATA_DIR"]) / "uploads" / "performance" / "performance.csv"
    assert saved.read_text(encoding="utf-8-sig").splitlines()[0] == HEADER
    logs = auth_client.get("/api/worklog?part=performance").get_json()["items"]
    assert logs and "업로드" in logs[0]["action"]


def test_header_mismatch_message(auth_client):
    res = auth_client.put("/api/performance", json={"csv": "date,channel,spend\n2026-10-19,IG,1", "mode": "replace"})
    assert res.status_code == 400
    assert "첫 줄은" in res.get_json()["error"] and HEADER in res.get_json()["error"]
    # 순서가 다른 헤더도 거절
    swapped = "채널,날짜,광고비,노출수,클릭수,전환수,매출\nIG,2026-10-19,1,1,1,1,1"
    assert auth_client.put("/api/performance", json={"csv": swapped}).status_code == 400


@pytest.mark.parametrize("line,word", [
    ("2026-13-40,IG,1,1,1,1,1", "날짜"),
    ("2026-10-19,IG,-5,1,1,1,1", "0 이상"),
    ("2026-10-19,IG,abc,1,1,1,1", "숫자가 아닙니다"),
    ("2026-10-19,,1,1,1,1,1", "채널"),
    ("2026-10-19,IG,1,1", "칸이"),
    ("2026-10-19,IG,300,000,1,1,1,1", "7개보다"),
])
def test_bad_rows_report_line(auth_client, line, word):
    res = auth_client.put("/api/performance", json={"csv": f"{HEADER}\n{line}\n", "mode": "replace"})
    assert res.status_code == 400
    err = res.get_json()["error"]
    assert "2번째 줄" in err and word in err


def test_empty_and_wrong_mode(auth_client):
    assert auth_client.put("/api/performance", json={"csv": HEADER + "\n\n"}).status_code == 400
    assert auth_client.put("/api/performance", json={"csv": HEADER + "\n2026-10-19,IG,1,1,1,1,1", "mode": "x"}).status_code == 400
    assert auth_client.put("/api/performance", data="x", content_type="text/plain").status_code == 415


def test_merge_overwrites_same_date_channel_and_backs_up(app, auth_client):
    first = HEADER + "\n2026-10-19,Instagram,100,1000,10,1,3000\n2026-10-19,TikTok,200,2000,20,2,4000\n"
    assert auth_client.put("/api/performance", json={"csv": first, "mode": "merge"}).status_code == 200
    # 샘플과 섞이지 않음: 처음 업로드는 합치기여도 새 파일
    rows = auth_client.get("/api/performance/data").get_json()["rows"]
    assert len(rows) == 2
    second = HEADER + "\n2026-10-19,Instagram,999,9990,99,9,29970\n2026-10-20,Instagram,100,1000,10,1,3000\n"
    j = auth_client.put("/api/performance", json={"csv": second, "mode": "merge"}).get_json()
    assert j["result"]["added"] == 1 and j["result"]["updated"] == 1
    rows = {(r["date"], r["channel"]): r for r in j["rows"]}
    assert len(rows) == 3
    assert rows[("2026-10-19", "Instagram")]["spend"] == 999
    assert rows[("2026-10-19", "TikTok")]["spend"] == 200
    backups = list((Path(app.config["DATA_DIR"]) / "backups").glob("performance-*.csv"))
    assert len(backups) == 1 and "2026-10-19,Instagram,100" in backups[0].read_text(encoding="utf-8-sig")
    # 전체 바꾸기
    third = HEADER + "\n2026-10-01,Google,5,5,5,5,5\n"
    j = auth_client.put("/api/performance", json={"csv": third, "mode": "replace"}).get_json()
    assert len(j["rows"]) == 1 and j["rows"][0]["channel"] == "Google"
    assert len(list((Path(app.config["DATA_DIR"]) / "backups").glob("performance-*.csv"))) == 2


def test_duplicate_rows_in_file_keep_last(auth_client):
    text = HEADER + "\n2026-10-19,IG,1,1,1,1,1\n2026-10-19,IG,2,2,2,2,2\n"
    j = auth_client.put("/api/performance", json={"csv": text, "mode": "replace"}).get_json()
    assert len(j["rows"]) == 1 and j["rows"][0]["spend"] == 2 and j["result"]["duplicates"] == 1


def test_multipart_upload_euc_kr(auth_client):
    text = HEADER + "\r\n2026-10-19,인스타그램,300000,52000,1300,42,1260000\r\n"
    data = {"file": (io.BytesIO(text.encode("cp949")), "엑셀.csv"), "mode": "replace"}
    res = auth_client.post("/api/performance/upload", data=data, content_type="multipart/form-data")
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["rows"][0]["channel"] == "인스타그램"
    # PUT도 multipart를 받음 (UTF-8 BOM)
    data = {"file": (io.BytesIO(("\ufeff" + text).encode("utf-8")), "utf8.csv"), "mode": "merge"}
    assert auth_client.put("/api/performance", data=data, content_type="multipart/form-data").status_code == 200


def test_reset_to_sample(app, auth_client):
    auth_client.put("/api/performance", json={"csv": HEADER + "\n2026-10-19,IG,1,1,1,1,1", "mode": "replace"})
    assert auth_client.get("/api/performance/data").get_json()["isSample"] is False
    j = auth_client.delete("/api/performance").get_json()
    assert j["ok"] and j["backup"]
    assert auth_client.get("/api/performance/data").get_json()["isSample"] is True


def test_template_and_export(auth_client):
    res = auth_client.get("/api/performance/template.csv")
    assert res.status_code == 200 and "attachment" in res.headers["Content-Disposition"]
    assert res.data.decode("utf-8-sig").strip() == HEADER
    res = auth_client.get("/api/performance/export.csv")
    lines = res.data.decode("utf-8-sig").splitlines()
    assert lines[0] == HEADER and len(lines) == 1 + 204 and "sample" in res.headers["Content-Disposition"]


# ───────── 환율 ─────────

class _Res:
    def __init__(self, payload, status=200, text=None):
        self.payload, self.status_code, self.text = payload, status, text

    def json(self):
        if self.text is not None:
            raise ValueError("not json")
        return self.payload


def _fake_session(monkeypatch, responder):
    calls = []

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            calls.append(params["searchdate"])
            return responder(params)

    monkeypatch.setattr("tabs.performance.fx.requests.Session", FakeSession)
    return calls


def test_fx_without_key(auth_client):
    j = auth_client.get("/api/performance/fx").get_json()
    assert j["status"] == "no_key" and j["rate"] == 1400 and j["reason"]


def test_fx_walks_back_and_caches(app, auth_client, monkeypatch, today):
    monkeypatch.setenv("KEXIM_API_KEY", "test-kexim-key")

    def responder(params):
        if params["searchdate"] in ("20261023", "20261022"):
            return _Res([])  # 고시 전 · 공휴일
        return _Res([{"result": 1, "cur_unit": "JPY(100)", "deal_bas_r": "905.1"},
                     {"result": 1, "cur_unit": "USD", "deal_bas_r": "1,324.6"}])

    calls = _fake_session(monkeypatch, responder)
    j = auth_client.get("/api/performance/fx").get_json()
    assert j["status"] == "ok" and j["rate"] == 1324.6 and j["date"] == "2026-10-21"
    assert calls == ["20261023", "20261022", "20261021"]
    assert (Path(app.config["DATA_DIR"]) / "cache" / "performance-fx.json").exists()
    # 다시 부르면 캐시 (3시간 안)
    j = auth_client.get("/api/performance/fx").get_json()
    assert j["cached"] is True and len(calls) == 3
    # 환율 확인(refresh=1)은 다시 부름
    auth_client.get("/api/performance/fx?refresh=1")
    assert len(calls) == 6


@pytest.mark.parametrize("payload,reason", [
    ([{"result": 3}], "인증키 오류"),
    ([{"result": 4}], "한도 초과"),
    ({"oops": 1}, "응답 형식 오류"),
])
def test_fx_errors_fall_back(auth_client, monkeypatch, today, payload, reason):
    monkeypatch.setenv("KEXIM_API_KEY", "test-kexim-key")
    _fake_session(monkeypatch, lambda p: _Res(payload))
    j = auth_client.get("/api/performance/fx").get_json()
    assert j["status"] == "error" and j["rate"] == 1400 and j["reason"] == reason


def test_fx_network_error_and_non_json(auth_client, monkeypatch, today):
    import requests
    monkeypatch.setenv("KEXIM_API_KEY", "test-kexim-key")

    def boom(params):
        raise requests.ConnectionError("down")

    _fake_session(monkeypatch, boom)
    j = auth_client.get("/api/performance/fx").get_json()
    assert j["status"] == "error" and j["reason"] == "연결 실패" and j["rate"] == 1400
    _fake_session(monkeypatch, lambda p: _Res(None, text="<html>"))
    assert auth_client.get("/api/performance/fx").get_json()["reason"] == "응답 형식 오류"


# ───────── AI 제안 ─────────

def test_advice_without_key_is_503(auth_client):
    res = auth_client.post("/api/performance/advice", json={"start": "2026-10-15", "end": "2026-10-21"})
    assert res.status_code == 503 and "OPENAI_API_KEY" in res.get_json()["error"]


def test_advice_clamps_budget_and_fills_channels(auth_client, monkeypatch, today):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    seen = {}

    def fake_openai_json(system, user, **kw):
        seen["system"], seen["user"], seen["kw"] = system, user, kw
        return {
            "overview": "샘플 데이터 기준 총평입니다.",
            "analysis": {"good": "Instagram이 가장 좋습니다.", "improve": "TikTok의 전환율이 낮습니다."},
            "budget": [
                {"channel": "instagram", "amount": 9_000_000, "reason": "ROAS 최고"},
                {"channel": "TikTok", "amount": 3_000_000, "reason": "테스트"},
                {"channel": "Facebook", "amount": 5_000_000, "reason": "없는 채널"},
            ],
            "actions": [{"title": "핼러윈 소재 준비", "detail": "D-8", "season": "Halloween"}] * 5,
        }

    monkeypatch.setattr("tabs.performance.advice.openai_json", fake_openai_json)
    res = auth_client.post("/api/performance/advice", json={"start": "2026-10-15", "end": "2026-10-21"})
    assert res.status_code == 200, res.get_json()
    j = res.get_json()
    b = j["budget"]
    spend = _sum(_sample_rows(), "2026-10-15", "2026-10-21", "광고비")
    assert b["current"] == spend
    assert spend * 0.8 <= b["total"] <= spend * 1.2 and b["adjusted"] is True
    assert sorted(i["channel"] for i in b["items"]) == ["Amazon", "Google", "Instagram", "TikTok"]
    assert sum(i["amount"] for i in b["items"]) == b["total"]
    assert all(i["amount"] % 1000 == 0 and i["amount"] >= 0 for i in b["items"])
    ig = next(i for i in b["items"] if i["channel"] == "Instagram")
    tt = next(i for i in b["items"] if i["channel"] == "TikTok")
    assert ig["amount"] > tt["amount"]  # 비율 유지
    assert len(j["actions"]) == 3 and j["sample"] is True
    # 프롬프트: 페르소나 · 퍼널 · 주어진 숫자만 · 다가오는 시즌
    assert "10년 경력" in seen["system"] and "퍼널" in seen["system"] and "숫자만" in seen["system"]
    payload = seen["user"]
    assert payload["data_source"] == "sample" and payload["period"]["days"] == 7
    assert any(s["name"] == "Halloween" for s in payload["upcoming_seasons"])
    assert seen["kw"]["schema"]["additionalProperties"] is False and seen["kw"]["part"] == "performance"


def test_advice_scales_budget_for_short_period(auth_client, monkeypatch, today):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("tabs.performance.advice.openai_json", lambda *a, **k: {
        "overview": "", "analysis": {"good": "", "improve": ""}, "budget": [], "actions": []})
    j = auth_client.post("/api/performance/advice", json={"start": "2026-10-19", "end": "2026-10-21"}).get_json()
    spend3 = _sum(_sample_rows(), "2026-10-19", "2026-10-21", "광고비")
    assert j["budget"]["scaled"] is True and j["budget"]["current"] == round(spend3 * 7 / 3)
    assert j["budget"]["adjusted"] is False  # 빠진 채널은 지금 광고비 유지 → 총액이 기준과 같음
    assert len(j["budget"]["notes"]) == 4
