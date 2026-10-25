"""리뷰 카드 → 슬랙 채널로 보내기 (Incoming Webhook · 키 SLACK_WEBHOOK_URL: 설정 › 연결 또는 .env).

- 사람이 리뷰 카드의 확인 창에서 '보내기'를 눌렀을 때만 보냄 (자동 발송 없음). 같은 함수로 미리 보기 글도 만듦
- 리뷰 작성자 이름은 원래 저장하지 않음. 웹훅 주소는 응답 · 작업 로그 · 오류 문장에 넣지 않음
- 보낸 기록: kv 'slack_shared' {리뷰 id: {by, at}} → 리뷰 카드에 '슬랙 공유 · 이름 · 시간'
"""
import re

from server import envkeys
from server.models import iso, utcnow
from . import http
from .http import CollectError
from .models import kv_get, kv_set

KEY = "SLACK_WEBHOOK_URL"
URL_RE = re.compile(r"^https://hooks\.slack\.com/(services|workflows|triggers)/[\w/-]+$")
TEXT_MAX = 600   # 원문은 앞부분만 (긴 리뷰가 채널을 덮지 않게)


def ready():
    return envkeys.has(KEY)


def _esc(t):
    """슬랙 mrkdwn에서 특수 의미가 있는 &, <, > 만 바꿈."""
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cut(t, n):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def message(r, cs, note, who):
    """리뷰 한 개 → 슬랙에 올릴 글 (mrkdwn)."""
    from . import channel_reviews, service  # service가 이 파일을 부르기 전에 불러지지 않게 여기서
    src = service.REVIEW_SOURCE_LABEL.get(r.source, r.source)
    name = channel_reviews.product_line(r.product_title) if r.own else (r.brand or service.guess_brand(r.product_title) or "브랜드 미상")
    stars = "★" * round(r.rating) + "☆" * (5 - round(r.rating)) if r.rating is not None else "별점 없음"
    sent = {"+": "칭찬", "-": "불만"}.get(r.sentiment, "중립")
    tags = " ".join("#" + t.replace(" ", "") for t in r.topic_list())
    lines = [f"*[{_esc(name)} · {_esc(src)}]* {stars} {sent}" + (f" · {_esc(tags)}" if tags else "") +
             (f" · {_esc(r.date_text)}" if r.date_text else "")]
    if r.summary_ko:
        lines.append(f"*{_esc(r.summary_ko)}*")
    lines.append("> " + _esc(_cut((r.title + " — " if r.title else "") + r.text, TEXT_MAX)))
    if r.product_title:
        lines.append(f"상품: {_esc(_cut(r.product_title, 120))}")
    if r.own:
        d = service.cs_dict(r, cs)
        lines.append(f"대응: {service.CS_STATUS.get(d['cs'], d['cs'])}" + (f" · 메모: {_esc(_cut(d['memo'], 200))}" if d["memo"] else ""))
    if note:
        lines.append(f"💬 {_esc(who)}: {_esc(note)}")
    lines.append(f"_REBOOT 대시보드 › 인사이트 › 구매자 리뷰 · {_esc(who)} 공유_")
    return "\n".join(lines)


def send(r, cs, note, who):
    """보내기 → {slackBy, slackAt}. 실패는 CollectError (화면에 보여 줄 문장)."""
    url = envkeys.get(KEY).strip()
    if not url:
        raise CollectError("Slack 웹훅 주소가 없어요. 설정 › 연결이나 .env에 SLACK_WEBHOOK_URL을 넣고 서버를 다시 켜세요.")
    if not URL_RE.match(url):
        raise CollectError("Slack 웹훅 주소 모양이 달라요 (https://hooks.slack.com/services/… 로 시작해야 해요).")
    res = http._send("Slack", http.post, url, json={"text": message(r, cs, note, who)}, timeout=15)
    if res.status_code in (403, 404, 410):
        raise CollectError("Slack이 웹훅 주소를 거절했어요. 웹훅이 지워졌거나 채널이 없어졌을 수 있어요 (api.slack.com/apps에서 확인).")
    if res.status_code == 429:
        raise CollectError("Slack에 짧은 시간에 너무 많이 보냈어요. 잠시 뒤 다시 보내세요.")
    if res.status_code != 200:
        raise CollectError(f"Slack이 메시지를 받지 않았어요 ({res.status_code}).")
    shared = kv_get("slack_shared", {})
    shared[str(r.id)] = {"by": who, "at": iso(utcnow())}
    kv_set("slack_shared", shared)
    return {"slackBy": who, "slackAt": shared[str(r.id)]["at"]}


def shared_map():
    return kv_get("slack_shared", {})
