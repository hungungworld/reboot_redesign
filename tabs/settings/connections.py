"""설정 › 연결: 연결 테스트 (설정 README 3장).

- 결과: {"status": "정상" | "오류" | "미연결", "message": 화면 문장, "detail": {…}}
- 키 값은 결과·로그·오류 문장 어디에도 넣지 않음 (requests 예외 문장에는 주소=키가 섞일 수 있어 예외 이름만 씀)
- 외부 호출은 _get · _smtp_login 두 함수로만 → 테스트에서 바꿔 끼움 (인터넷 없이)
"""
import smtplib
from datetime import timedelta

import requests

from server import envkeys

TIMEOUT = 15
GMAIL_PAIR = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")


class _Fail(Exception):
    """테스트 실패 → 상태 '오류' + 문장."""


def _get(url, params=None, headers=None):
    return requests.get(url, params=params, headers=headers, timeout=TIMEOUT)


def _smtp_login(address, password):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=TIMEOUT) as s:
        s.login(address, password)


def _call(url, service, params=None, headers=None):
    try:
        return _get(url, params=params, headers=headers)
    except requests.Timeout:
        raise _Fail(f"{service} 응답이 늦어 중단했어요 ({TIMEOUT}초). 잠시 뒤 다시 시도하세요.")
    except requests.RequestException as e:
        raise _Fail(f"{service}에 연결하지 못했어요 ({type(e).__name__}). 인터넷 연결을 확인하세요.")


def _json(res):
    try:
        return res.json()
    except ValueError:
        return None


def _http_fail(service, res):
    code = res.status_code
    if code in (401, 403):
        return _Fail(f"{service}가 키를 거절했어요 ({code}). 키를 다시 확인하세요.")
    if code == 429:
        return _Fail(f"{service} 사용 한도를 넘었거나 요청이 많아요 (429).")
    return _Fail(f"{service} 응답 오류 ({code}).")


# ───────── 서비스별 테스트 ─────────

def _openai(key):
    res = _call("https://api.openai.com/v1/models", "OpenAI", headers={"Authorization": f"Bearer {key}"})
    if res.status_code != 200:
        raise _http_fail("OpenAI", res)
    ids = [m.get("id") for m in ((_json(res) or {}).get("data") or []) if isinstance(m, dict)]
    model = envkeys.get("OPENAI_MODEL") or "gpt-4o-mini"
    detail = {"models": len(ids), "model": model, "modelAvailable": model in ids}
    if ids and model not in ids:
        return "정상", f"키는 정상이에요. 다만 설정한 모델 '{model}'이(가) 목록에 없어요 (OpenAI 모델 설정 확인).", detail
    return "정상", f"키 정상 · 모델 '{model}' 사용 가능", detail


def _anthropic(key):
    res = _call("https://api.anthropic.com/v1/models", "Anthropic",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    if res.status_code != 200:
        raise _http_fail("Anthropic", res)
    models = [m.get("id") for m in ((_json(res) or {}).get("data") or []) if isinstance(m, dict)]
    return "정상", f"키 정상 · 쓸 수 있는 모델 {len(models)}개", {"models": len(models)}


def _serpapi(key):
    res = _call("https://serpapi.com/account.json", "SerpApi", params={"api_key": key})
    data = _json(res) or {}
    if res.status_code != 200 or data.get("error"):
        if res.status_code in (200, 401, 403) and data.get("error"):
            raise _Fail("SerpApi가 키를 거절했어요. 키를 다시 확인하세요.")
        raise _http_fail("SerpApi", res)
    left = data.get("total_searches_left", data.get("plan_searches_left"))
    detail = {"plan": data.get("plan_name"), "searchesPerMonth": data.get("searches_per_month"),
              "searchesLeft": left, "thisMonthUsage": data.get("this_month_usage")}
    plan = data.get("plan_name") or "플랜 정보 없음"
    msg = f"키 정상 · {plan} · 이번 달 남은 검색 {left:,}회" if isinstance(left, int) else f"키 정상 · {plan}"
    if isinstance(left, int) and left <= 0:
        return "오류", f"키는 맞지만 이번 달 검색을 모두 썼어요 ({plan}).", detail
    return "정상", msg, detail


def _youtube(key):
    res = _call("https://www.googleapis.com/youtube/v3/videos", "YouTube",
                params={"part": "id", "chart": "mostPopular", "maxResults": 1, "key": key})
    if res.status_code == 200:
        return "정상", "키 정상 (테스트에 1 unit 사용 · 하루 10,000 units)", {"unitsUsed": 1}
    data = _json(res) or {}
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    reasons = {e.get("reason") for e in (err.get("errors") or []) if isinstance(e, dict)}
    if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
        raise _Fail("키는 맞지만 오늘 사용량(10,000 units)을 모두 썼어요. 내일 다시 시도하세요.")
    if "accessNotConfigured" in reasons or "SERVICE_DISABLED" in str(err.get("status", "")):
        raise _Fail("Google Cloud 콘솔에서 YouTube Data API v3를 '사용'으로 켜 주세요.")
    if res.status_code == 400 or "keyInvalid" in reasons:
        raise _Fail("YouTube가 키를 거절했어요 (API key not valid). 키를 다시 확인하세요.")
    raise _http_fail("YouTube", res)


def _firecrawl(key):
    # 남은 크레딧 조회 (크레딧을 쓰지 않음). 주소가 없으면(404) 키 형식만 확인
    res = _call("https://api.firecrawl.dev/v1/team/credit-usage", "Firecrawl", headers={"Authorization": f"Bearer {key}"})
    if res.status_code == 200:
        data = (_json(res) or {}).get("data") or {}
        left = data.get("remaining_credits", data.get("remainingCredits"))
        if isinstance(left, (int, float)):
            return "정상", f"키 정상 · 남은 크레딧 {int(left):,}", {"remainingCredits": int(left)}
        return "정상", "키 정상", {}
    if res.status_code == 404:
        if key.startswith("fc-") and len(key) >= 20:
            return "정상", "키 형식만 확인했어요 (fc-로 시작). 실제 수집 때 다시 확인돼요.", {"formatOnly": True}
        raise _Fail("Firecrawl 키 형식이 아니에요 (fc-로 시작해야 해요).")
    raise _http_fail("Firecrawl", res)


def _kexim(key):
    from server.catalog.service import _today
    url = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"
    day = _today()
    for _ in range(5):  # 주말·휴일·오전 11시 이전에는 빈 목록 → 며칠 전까지
        res = _call(url, "한국수출입은행", params={"authkey": key, "searchdate": day.strftime("%Y%m%d"), "data": "AP01"})
        if res.status_code != 200:
            raise _http_fail("한국수출입은행", res)
        rows = _json(res)
        if isinstance(rows, list) and rows:
            first = rows[0] if isinstance(rows[0], dict) else {}
            code = first.get("result")
            if code == 3:
                raise _Fail("한국수출입은행이 인증키를 거절했어요 (인증코드 오류).")
            if code == 4:
                raise _Fail("오늘 호출 한도(하루 1,000회)를 모두 썼어요.")
            usd = next((r for r in rows if isinstance(r, dict) and str(r.get("cur_unit", "")).upper() == "USD"), None)
            rate = (usd or {}).get("deal_bas_r")
            return "정상", f"키 정상 · {day.isoformat()} 기준 USD {rate}원" if rate else "키 정상", \
                {"date": day.isoformat(), "usd": rate}
        day -= timedelta(days=1)
    raise _Fail("최근 5일 환율 응답이 비어 있어요. 휴일이거나 키가 아직 승인되지 않았을 수 있어요.")


def _gmail(_key):
    address, password = envkeys.get("GMAIL_ADDRESS"), envkeys.get("GMAIL_APP_PASSWORD")
    if not address or not password:
        return "미연결", "GMAIL_ADDRESS와 GMAIL_APP_PASSWORD가 모두 있어야 해요.", {}
    try:
        _smtp_login(address, password.replace(" ", ""))
    except smtplib.SMTPAuthenticationError:
        raise _Fail("Gmail이 로그인을 거절했어요. 주소와 앱 비밀번호(16자리)를 확인하세요.")
    except (smtplib.SMTPException, OSError) as e:
        raise _Fail(f"Gmail SMTP에 연결하지 못했어요 ({type(e).__name__}).")
    return "정상", "SMTP 로그인 성공 (메일은 보내지 않았어요)", {}


TESTERS = {
    "OPENAI_API_KEY": _openai,
    "ANTHROPIC_API_KEY": _anthropic,
    "SERPAPI_KEY": _serpapi,
    "YOUTUBE_API_KEY": _youtube,
    "FIRECRAWL_API_KEY": _firecrawl,
    "KEXIM_API_KEY": _kexim,
    "GMAIL_ADDRESS": _gmail,
    "GMAIL_APP_PASSWORD": _gmail,
}


def testable(name):
    return name in TESTERS


def run_test(name):
    """연결 테스트 → {status, message, detail}. 지원하지 않는 서비스는 KeyError."""
    fn = TESTERS[name]
    if name in GMAIL_PAIR:
        if not all(envkeys.has(n) for n in GMAIL_PAIR):
            return {"status": "미연결", "message": "GMAIL_ADDRESS와 GMAIL_APP_PASSWORD가 모두 있어야 해요.", "detail": {}}
    elif not envkeys.has(name):
        return {"status": "미연결", "message": "키가 없어요. 관리자가 '키 입력'으로 넣거나 .env에 넣어 주세요.", "detail": {}}
    try:
        status, message, detail = fn(envkeys.get(name))
    except _Fail as e:
        status, message, detail = "오류", str(e), {}
    except Exception as e:  # 예상 못 한 응답 형식 등 — 예외 원문은 내보내지 않음
        status, message, detail = "오류", f"테스트 중 문제가 생겼어요 ({type(e).__name__}).", {}
    return {"status": status, "message": message[:300], "detail": detail}
