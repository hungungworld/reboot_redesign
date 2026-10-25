"""외부 호출 (requests + timeout). 테스트는 get · post를 가짜로 바꿔 끼웁니다 (인터넷 없이).

- 실패는 CollectError(화면에 보여 줄 한국어 문장). 키가 주소에 섞일 수 있어 예외 원문은 넣지 않고 이름만 씀
"""
import requests


class CollectError(Exception):
    """수집 실패 — 화면 · 작업 로그에 그대로 보여 줄 문장 (키 · 주소 넣지 않기)."""


def get(url, params=None, headers=None, timeout=25):
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def post(url, json=None, headers=None, timeout=90):
    return requests.post(url, json=json, headers=headers, timeout=timeout)


def _send(service, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except requests.Timeout:
        raise CollectError(f"{service} 응답이 늦어 중단했어요. 잠시 뒤 다시 시도하세요.")
    except requests.RequestException as e:
        raise CollectError(f"{service}에 연결하지 못했어요 ({type(e).__name__}). 인터넷 연결을 확인하세요.")


def _json(res):
    try:
        return res.json()
    except ValueError:
        return None


def get_json(service, url, params=None, headers=None, timeout=25):
    """→ (상태 코드, JSON 또는 None)."""
    res = _send(service, get, url, params=params, headers=headers, timeout=timeout)
    return res.status_code, _json(res)


def post_json(service, url, payload, headers=None, timeout=90):
    res = _send(service, post, url, json=payload, headers=headers, timeout=timeout)
    return res.status_code, _json(res)
