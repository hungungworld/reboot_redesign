"""Higgsfield 어댑터 (이미지 · 영상) — 공식 Quick start의 제출 → 상태 조회 → 결과 수신 패턴 (README 8장).

- 인증: Authorization: Key <KEY_ID:KEY_SECRET> (HIGGSFIELD_API_KEY · 서버에서만 읽음)
- 영상: Seedance 2.0 · 720p 고정 (참고 사진이 있으면 reference-to-video, 없으면 text-to-video)
- 이미지: Marketing Studio Image (참고 사진 image_urls)
- 참고 사진: /files/generate-upload-url → 받은 주소에 PUT → public_url
- 유료 호출이라 제출은 자동 재시도하지 않음. 응답 시간 초과·연결 끊김은 '제출 여부 불명'(SubmissionUnknown)
- 취소는 아직 시작 전인 요청만 가능 (시작 후에는 제공사가 400을 줌). 확인을 멈추는 것만으로는 취소되지 않음
- 연결 확인: 없는 요청 ID의 상태 조회 — 키가 맞으면 404, 틀리면 401 (생성 비용 없음)
테스트는 이 모듈의 ready · submit · status · cancel · upload_ref · download를 바꿔 끼웁니다 (인터넷·키 없이).
"""
import re
import uuid

import requests

from server import envkeys
from .providers import ProviderError, SubmissionUnknown

BASE = "https://api.higgsfield.ai"
VIDEO_TEXT = "bytedance/seedance-2.0/text-to-video"
VIDEO_REF = "bytedance/seedance-2.0/reference-to-video"
VIDEO_RESOLUTION = "720p"
VIDEO_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9")
VIDEO_SECONDS = (4, 15)
IMAGE_MODEL = "marketing-studio/image"
IMAGE_RESOLUTION = "1k"
IMAGE_RATIOS = ("1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9")
DONE = ("completed", "failed", "nsfw", "canceled")
TIMEOUT = 30
DOWNLOAD_MAX = 200 * 1024 * 1024


def ready():
    return envkeys.has("HIGGSFIELD_API_KEY")


def _headers():
    key = (envkeys.get("HIGGSFIELD_API_KEY") or "").strip()
    if not key:
        raise ProviderError("Higgsfield 키가 없습니다. 설정 › 연결에서 HIGGSFIELD_API_KEY를 넣어 주세요.", 503)
    if key.lower().startswith("key "):
        key = key[4:].strip()
    return {"Authorization": f"Key {key}", "Content-Type": "application/json", "Accept": "application/json"}


def _detail(res):
    try:
        d = res.json()
    except ValueError:
        return ""
    d = d.get("detail") if isinstance(d, dict) else d
    return str(d)[:200] if d else ""


def _check(res, what):
    if res.status_code < 400:
        return
    detail = _detail(res)
    if res.status_code in (401, 403):
        raise ProviderError(f"Higgsfield 키가 올바르지 않거나 권한이 없습니다 ({res.status_code}"
                            + (f" · {detail}" if detail else "") + " · 설정 › 연결 확인).", 503)
    if res.status_code == 402:
        raise ProviderError("Higgsfield 크레딧이 부족합니다. 충전 후 다시 요청하세요.", 402)
    if res.status_code == 404:
        raise ProviderError(f"Higgsfield에서 {what}을 찾지 못했습니다.", 404)
    if res.status_code == 429:
        raise ProviderError("Higgsfield 요청이 많거나 사용 한도를 넘었습니다. 잠시 뒤 다시 요청하세요.", 429)
    if res.status_code < 500:
        raise ProviderError(f"Higgsfield가 {what} 요청을 받지 않았습니다" + (f" ({detail})." if detail else "."), 400)
    raise ProviderError(f"Higgsfield 서버 오류 ({res.status_code}). 잠시 뒤 다시 요청하세요.", 502)


def submit(model_path, body):
    """생성 요청 → request_id. 응답을 못 받으면 SubmissionUnknown (다시 제출하지 않음)."""
    try:
        res = requests.post(f"{BASE}/{model_path}", json=body, headers=_headers(), timeout=TIMEOUT)
    except (requests.Timeout, requests.ConnectionError):
        raise SubmissionUnknown("Higgsfield 응답을 받지 못했습니다. 생성 여부를 확인할 수 없어 자동으로 다시 제출하지 않습니다.", 504)
    _check(res, "생성")
    try:
        rid = res.json().get("request_id")
    except ValueError:
        rid = None
    if not rid:
        raise SubmissionUnknown("Higgsfield가 요청 ID를 주지 않았습니다. 생성 여부를 확인할 수 없어 자동으로 다시 제출하지 않습니다.", 502)
    return str(rid)


def status(request_id):
    """상태 조회 → {status, url, error}. 네트워크 오류는 requests 예외 그대로 (부른 쪽이 몇 번 더 확인)."""
    res = requests.get(f"{BASE}/requests/{request_id}/status", headers=_headers(), timeout=TIMEOUT)
    _check(res, "요청")
    d = res.json()
    url = ((d.get("video") or {}).get("url")
           or next((i.get("url") for i in d.get("images") or [] if isinstance(i, dict) and i.get("url")), None))
    return {"status": d.get("status") or "", "url": url, "error": str(d.get("error") or "")[:300]}


def cancel(request_id):
    """시작 전 요청만 취소됨. 반환: True(취소됨) · False(이미 시작돼 취소 불가)."""
    try:
        res = requests.post(f"{BASE}/requests/{request_id}/cancel", headers=_headers(), timeout=TIMEOUT)
    except (requests.Timeout, requests.ConnectionError):
        raise ProviderError("Higgsfield에 연결하지 못해 취소 요청을 보내지 못했습니다.", 502)
    if res.status_code == 400:
        return False
    _check(res, "취소")
    return True


def upload_ref(path, content_type):
    """참고 사진 올리기 → public_url (PUT이 성공한 뒤에만 씀)."""
    try:
        res = requests.post(f"{BASE}/files/generate-upload-url", json={"content_type": content_type},
                            headers=_headers(), timeout=TIMEOUT)
        _check(res, "참고 사진 업로드")
        d = res.json()
        with open(path, "rb") as f:
            put = requests.put(d["upload_url"], data=f.read(), headers=d.get("upload_headers") or {}, timeout=60)
    except (requests.Timeout, requests.ConnectionError):
        raise ProviderError("참고 사진을 Higgsfield에 올리지 못했습니다 (연결 확인). 생성 요청은 보내지 않았습니다.", 502)
    except (KeyError, ValueError, TypeError):
        raise ProviderError("Higgsfield 업로드 주소를 읽지 못했습니다. 생성 요청은 보내지 않았습니다.", 502)
    if put.status_code >= 300:
        raise ProviderError(f"참고 사진 업로드 실패 ({put.status_code}). 생성 요청은 보내지 않았습니다.", 502)
    return d["public_url"]


def download(url):
    """결과 파일 받기 (결과 주소는 일정 시간 뒤 만료됨)."""
    try:
        with requests.get(url, timeout=120, stream=True) as res:
            if res.status_code >= 400:
                raise ProviderError(f"결과 파일을 받지 못했습니다 ({res.status_code}).", 502)
            data = bytearray()
            for chunk in res.iter_content(1024 * 256):
                data.extend(chunk)
                if len(data) > DOWNLOAD_MAX:
                    raise ProviderError("결과 파일이 너무 큽니다.", 502)
            return bytes(data)
    except (requests.Timeout, requests.ConnectionError):
        raise ProviderError("결과 파일을 받는 중 연결이 끊겼습니다.", 502)


def nearest_ratio(ratio_text, allowed, default):
    """'4:5 세로' 같은 결정값 → 모델이 받는 비율 (정확히 같지 않으면 가장 가까운 값)."""
    r =str(ratio_text or "").replace(" ", "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)[:x×/](\d+(?:\.\d+)?)", r)
    if not m:
        if any(x in r for x in ("세로", "portrait", "스토리", "릴스", "reels", "story", "숏폼")):
            return "9:16" if "9:16" in allowed else default
        if any(x in r for x in ("가로", "landscape", "와이드", "wide")):
            return "16:9" if "16:9" in allowed else default
        return default
    if not float(m.group(1)) or not float(m.group(2)):
        return default
    want = float(m.group(1)) / float(m.group(2))
    return min(allowed, key=lambda a: abs(float(a.split(":")[0]) / float(a.split(":")[1]) - want))


def check_connection():
    """키가 실제로 동작하는지 — 없는 요청 ID 상태 조회 (생성 비용 없음). 반환: {ok, checked, message}"""
    if not ready():
        return {"ok": False, "checked": False, "message": "HIGGSFIELD_API_KEY가 없어요 (설정 › 연결)."}
    try:
        res = requests.get(f"{BASE}/requests/{uuid.uuid4()}/status", headers=_headers(), timeout=15)
    except (requests.Timeout, requests.ConnectionError):
        return {"ok": False, "checked": False, "message": "Higgsfield 서버에 연결하지 못했습니다 (인터넷 확인)."}
    if res.status_code in (401, 403):
        return {"ok": False, "checked": True, "message": "Higgsfield 키가 올바르지 않습니다 (KEY_ID:KEY_SECRET 형식 확인)."}
    if res.status_code in (200, 404):
        return {"ok": True, "checked": True, "message": "Higgsfield 키를 확인했어요."}
    return {"ok": False, "checked": True, "message": f"Higgsfield 서버 오류 ({res.status_code})."}


__all__ = ["ready", "submit", "status", "cancel", "upload_ref", "download", "check_connection", "nearest_ratio",
           "VIDEO_TEXT", "VIDEO_REF", "VIDEO_RESOLUTION", "IMAGE_MODEL", "DONE"]
