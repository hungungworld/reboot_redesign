"""구글 뉴스 RSS · 업계지 RSS 읽기 (키 없음 · 팀 결정: GDELT 대신 구글 뉴스, 2026-10-24 업계지 피드 추가).

- 주소: https://news.google.com/rss/search?q={검색어} when:{기간}&hl=en-US&gl=US&ceid=US:en
- 항목마다 제목 · 매체(source) · 링크 · 날짜(pubDate)만 읽음 (본문 없음). 제목 끝의 " - 매체명"은 뗌
- 업계지 피드(feed): 사이트 RSS 주소를 그대로 읽음. <source>가 없으니 매체 이름은 설정의 이름을 씀
- 요청 사이 잠깐 쉬고, 요청 제한·서버 오류는 한 번만 다시 시도
- 보도 급증: 받아 온 기사 날짜로 최근 7일 vs 그 전 7일 기사 수를 비교
"""
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

BASE = "https://news.google.com/rss/search"
UA = "REBOOT-dashboard/1.0 (home daily briefing)"
MIN_INTERVAL = 1.0
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"


class RssError(Exception):
    """화면에 보여 줄 수 있는 짧은 실패 이유."""


class RssClient:
    def __init__(self, interval=MIN_INTERVAL, timeout=30, retries=1, sleep=time.sleep, session=None):
        self.interval = max(0.0, float(interval))
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.session = session or requests.Session()
        self.calls = 0
        self.rate_limited = 0
        self._last = None

    def _wait(self):
        if self._last is None or self.interval <= 0:
            return
        gap = time.monotonic() - self._last
        if gap < self.interval:
            self.sleep(self.interval - gap)

    def _once(self, params, url=BASE):
        """요청 1번 → (XML 루트, None) 또는 (None, (오류, 다시 시도할지))."""
        self._wait()
        try:
            res = self.session.get(url, params=params, timeout=self.timeout, headers={"User-Agent": UA})
        except requests.RequestException as e:
            return None, (RssError(f"연결 실패 ({type(e).__name__})"), True)
        finally:
            self._last = time.monotonic()
            self.calls += 1
        if res.status_code == 429:
            self.rate_limited += 1
            return None, (RssError("요청 제한 (잠시 뒤 다시)"), True)
        if res.status_code >= 500:
            return None, (RssError(f"서버 오류 ({res.status_code})"), True)
        if res.status_code != 200:
            return None, (RssError(f"응답 오류 ({res.status_code})"), False)
        body = res.content or b""
        if not body.strip():
            return None, (RssError("빈 응답"), False)
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return None, (RssError("RSS 형식이 아닌 응답"), False)
        if root.find("channel") is None:
            return None, (RssError("RSS 형식 오류 (channel 없음)"), False)
        return root, None

    def get(self, params, url=BASE):
        err = None
        for attempt in range(self.retries + 1):
            root, fail = self._once(params, url)
            if fail is None:
                return root
            err, retry = fail
            if not retry:
                break
            if attempt < self.retries:
                self.sleep(max(self.interval, 1.0) * 2)
        raise err

    def articles(self, query, when="14d"):
        root = self.get({"q": f"{query} when:{when}", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        out = []
        for item in root.iter("item"):
            source_el = item.find("source")
            source = (source_el.text or "").strip() if source_el is not None else ""
            out.append({
                "url": (item.findtext("link") or "").strip(),
                "title": clean_title(item.findtext("title"), source),
                "domain": source,
                "date": parse_date(item.findtext("pubDate")),
            })
        return out

    def feed(self, url, source=""):
        """업계지 RSS 주소 → 기사 목록 (기간 조건이 없으니 날짜는 부르는 쪽에서 거름)."""
        root = self.get(None, url)
        out = []
        for item in root.iter("item"):
            out.append({
                "url": (item.findtext("link") or "").strip(),
                "title": clean_title(item.findtext("title")),
                "domain": source,
                "date": parse_date(item.findtext("pubDate") or item.findtext(DC_DATE)),
            })
        return out


def parse_date(v):
    """RSS 날짜 'Fri, 23 Oct 2026 08:00:00 GMT'(또는 dc:date '2026-10-23T08:00:00Z') → UTC datetime (시간대 정보 없음)."""
    v = str(v or "").strip()
    try:
        d = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d is None:
        return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def clean_title(v, source=""):
    t = re.sub(r"\s+", " ", html.unescape(str(v or ""))).strip()
    if source and t.endswith(f" - {source}"):
        t = t[: -len(source) - 3].rstrip()
    return t


def is_noise(title, words):
    """노이즈 단어(음주 사고 등)가 제목에 있으면 True. 단어 앞쪽 경계만 봐서 arrest → arrested도 걸러짐."""
    low = str(title or "").lower()
    return any(re.search(r"\b" + re.escape(str(w).lower()), low) for w in words or [] if str(w).strip())


def surge(dates, now, ratio=1.5, min_recent=5):
    """최근 7일 vs 그 전 7일 기사 수 → 급증 여부 (최근 7일이 min_recent건 이상이고 ratio배 이상). 날짜별 개수도 함께."""
    dates = [d for d in dates if d is not None]
    if not dates:
        return None
    cut1, cut2 = now - timedelta(days=7), now - timedelta(days=14)
    recent = sum(1 for d in dates if d > cut1)
    prev = sum(1 for d in dates if cut2 < d <= cut1)
    change = round((recent - prev) / prev * 100, 1) if prev > 0 else None
    is_surge = recent >= min_recent and (prev == 0 or recent / prev >= ratio)
    days = {}
    for d in dates:
        if d > cut2:
            k = d.date().isoformat()
            days[k] = days.get(k, 0) + 1
    daily = [{"date": k, "value": v} for k, v in sorted(days.items())][-14:]
    return {"recent": recent, "previous": prev, "changePct": change, "surge": bool(is_surge), "daily": daily}
