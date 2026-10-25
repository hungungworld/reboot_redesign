"""USD 환율 — 한국수출입은행 현재환율 API의 매매기준율 (README 6장).

- 영업일 오전 11시쯤 하루 한 번 고시 → 주말·공휴일·고시 전에는 빈 목록 → 최대 7일 전까지 거슬러 찾음
- 키가 없거나 실패하면 1,400원 + status(no_key·error) + reason
- 성공한 값은 DATA_DIR/cache/performance-fx.json에 보관 (같은 날 고시값이면 다시 부르지 않음)
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import current_app

from server import envkeys
from server.catalog import service as catalog
from server.models import iso, utcnow

URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"
DEFAULT_RATE = 1400.0
LOOKBACK_DAYS = 7
TIMEOUT = 10
REFETCH_AFTER = timedelta(hours=3)  # 오늘 고시 전 값을 가진 캐시는 3시간 뒤 다시 확인
RESULT_REASON = {2: "요청 형식 오류", 3: "인증키 오류", 4: "한도 초과"}


def cache_path():
    return Path(current_app.config["DATA_DIR"]) / "cache" / "performance-fx.json"


def _fallback(status, reason):
    return {"status": status, "rate": DEFAULT_RATE, "date": None, "reason": reason, "source": "default"}


def _read_cache():
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        return data if data.get("status") == "ok" and data.get("rate") else None
    except (OSError, ValueError):
        return None


def _fresh(cached, today):
    if cached.get("date") == today.isoformat():
        return True
    try:
        fetched = datetime.fromisoformat(cached.get("fetchedAt", "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - fetched < REFETCH_AFTER and cached.get("checkedFor") == today.isoformat()


def _rate(text):
    return float(str(text).replace(",", "").strip())


def fetch(key, today):
    """수출입은행 API 호출 → 결과 dict (실패도 dict)."""
    session = requests.Session()  # 수출입은행은 쿠키를 붙인 리디렉션을 쓰기도 함
    for back in range(LOOKBACK_DAYS + 1):
        day = today - timedelta(days=back)
        try:
            res = session.get(URL, params={"authkey": key, "searchdate": day.strftime("%Y%m%d"), "data": "AP01"},
                              timeout=TIMEOUT, headers={"Accept": "application/json"})
        except requests.RequestException as e:
            current_app.logger.warning("환율 조회 실패: %s", type(e).__name__)
            return _fallback("error", "연결 실패")
        if res.status_code != 200:
            return _fallback("error", f"응답 오류 {res.status_code}")
        try:
            data = res.json()
        except ValueError:
            return _fallback("error", "응답 형식 오류")
        if not isinstance(data, list):
            return _fallback("error", "응답 형식 오류")
        if not data:
            continue  # 주말 · 공휴일 · 고시 전
        result = data[0].get("result") if isinstance(data[0], dict) else None
        if result != 1:
            return _fallback("error", RESULT_REASON.get(result, "조회 실패"))
        usd = next((x for x in data if isinstance(x, dict) and str(x.get("cur_unit", "")).upper().startswith("USD")), None)
        if not usd:
            return _fallback("error", "USD 환율 없음")
        try:
            rate = _rate(usd.get("deal_bas_r"))
        except (TypeError, ValueError):
            return _fallback("error", "응답 형식 오류")
        if not 100 < rate < 10000:
            return _fallback("error", "환율 값 이상")
        return {"status": "ok", "rate": rate, "date": day.isoformat(), "source": "kexim",
                "fetchedAt": iso(utcnow()), "checkedFor": today.isoformat()}
    return _fallback("error", f"최근 {LOOKBACK_DAYS}일 고시 환율 없음")


def get_rate(refresh=False):
    key = envkeys.get("KEXIM_API_KEY")
    if not key:
        return _fallback("no_key", "키 없음")
    today = catalog._today()
    cached = _read_cache()
    if cached and not refresh and _fresh(cached, today):
        return dict(cached, cached=True)
    out = fetch(key, today)
    if out["status"] == "ok":
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    elif cached:
        # 새로 받지 못하면 마지막 고시값을 쓰고 실패 이유를 함께 알림
        return dict(cached, cached=True, warning=out["reason"])
    return out
