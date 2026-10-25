"""유튜브 댓글 (YouTube Data API v3) — 월 1회.

검색어 세트 A · B · K → 영상 검색(100 units/회) → 조회수 확인(1 unit/50개)
→ 최소 조회수 + 제목이 해결책 · 제품 · 리뷰 영상인 것만 댓글(1 unit/영상)
댓글은 작성자 이름 없이 본문만 저장하고, 규칙으로 관련 여부 · 질문 주제를 바로 붙입니다.
"""
from server.extensions import db
from server.models import utcnow
from . import classify, http
from .http import CollectError
from .models import Comment, Video
from .util import clean, to_int

API = "https://www.googleapis.com/youtube/v3"


def _reasons(data):
    err = (data or {}).get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return set()
    return {e.get("reason") for e in (err.get("errors") or []) if isinstance(e, dict)}


def _fail(status, data):
    reasons = _reasons(data)
    if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
        raise CollectError("YouTube 오늘 사용량(하루 10,000 units)을 모두 썼어요. 내일 다시 시도하세요.")
    if "accessNotConfigured" in reasons:
        raise CollectError("Google Cloud 콘솔에서 YouTube Data API v3를 '사용'으로 켜 주세요.")
    if status in (400, 401, 403) and (reasons & {"keyInvalid", "badRequest"} or status != 403):
        raise CollectError("YouTube가 키를 거절했어요. 설정 › 연결에서 키를 확인하세요.")
    raise CollectError(f"YouTube 응답 오류 ({status}).")


def collect(cfg, key, month):
    units, found = 0, {}
    lang = cfg.get("video_language", "en")
    per_query = int(cfg.get("videos_per_query") or 10)
    for sid in ("A", "B", "K"):
        for q in (cfg.get("search_sets") or {}).get(sid, []):
            params = {"part": "snippet", "type": "video", "q": q, "maxResults": per_query, "key": key}
            if lang == "en":
                params.update(relevanceLanguage="en", regionCode="US")
            status, data = http.get_json("YouTube", f"{API}/search", params=params)
            units += 100
            if status != 200:
                _fail(status, data)
            for it in (data or {}).get("items") or []:
                vid = (it.get("id") or {}).get("videoId") if isinstance(it.get("id"), dict) else None
                if not vid or vid in found:
                    continue
                sn = it.get("snippet") or {}
                found[vid] = {"id": vid, "title": clean(sn.get("title"), 300), "channel": clean(sn.get("channelTitle"), 200),
                              "channel_id": clean(sn.get("channelId"), 40), "published_at": clean(sn.get("publishedAt"), 30),
                              "query_set": sid, "search_query": clean(q, 200), "views": None, "likes": None, "comment_count": None}
    ids = list(found)
    for i in range(0, len(ids), 50):
        status, data = http.get_json("YouTube", f"{API}/videos", params={
            "part": "statistics", "id": ",".join(ids[i:i + 50]), "key": key})
        units += 1
        if status != 200:
            _fail(status, data)
        for it in (data or {}).get("items") or []:
            st = it.get("statistics") or {}
            v = found.get(it.get("id"))
            if v:
                v.update(views=to_int(st.get("viewCount")), likes=to_int(st.get("likeCount")),
                         comment_count=to_int(st.get("commentCount")))
    min_views = int(cfg.get("min_views") or 0)
    own = cfg.get("own_terms") or []
    for v in found.values():
        v["kept"] = (v["views"] or 0) >= min_views and classify.video_on_topic(v["title"], own)
    off_topic = sum(1 for v in found.values() if (v["views"] or 0) >= min_views and not v["kept"])
    kept = [v for v in found.values() if v["kept"]]
    comments, disabled = [], 0
    for v in kept:
        if v.get("comment_count") == 0:
            continue
        status, data = http.get_json("YouTube", f"{API}/commentThreads", params={
            "part": "snippet", "videoId": v["id"], "maxResults": 100, "order": "relevance", "textFormat": "plainText", "key": key})
        units += 1
        if status != 200:
            reasons = _reasons(data)
            if "commentsDisabled" in reasons or status == 404:
                disabled += 1
                continue
            _fail(status, data)
        for th in (data or {}).get("items") or []:
            top = ((th.get("snippet") or {}).get("topLevelComment") or {})
            sn = top.get("snippet") or {}
            text = clean(sn.get("textOriginal") or sn.get("textDisplay"), 3000)
            if top.get("id") and text:
                comments.append({"id": top["id"][:64], "video_id": v["id"], "text": text, "likes": to_int(sn.get("likeCount")),
                                 "published_at": clean(sn.get("publishedAt"), 30)})
    now = utcnow()
    for v in found.values():
        row = db.session.get(Video, v["id"]) or Video(id=v["id"])
        for k in ("title", "channel", "channel_id", "published_at", "views", "likes", "comment_count", "query_set", "search_query"):
            setattr(row, k, v[k])
        row.month, row.kept, row.collected_at = month, v["kept"], now
        db.session.add(row)
    for c in comments:
        row = db.session.get(Comment, c["id"]) or Comment(id=c["id"])
        cls = classify.classify_comment(c["text"])
        row.video_id, row.text, row.likes, row.published_at = c["video_id"], c["text"], c["likes"], c["published_at"]
        row.month, row.collected_at = month, now
        if row.classified_by != "ai":
            row.relevant, row.is_question, row.topic, row.classified_by = cls["relevant"], cls["is_question"], cls["topic"], "rule"
        db.session.add(row)
    db.session.commit()
    warnings = [f"댓글이 꺼진 영상 {disabled}개는 건너뛰었어요."] if disabled else []
    if off_topic:
        warnings.append(f"제목이 숙취해소 제품 · 리뷰와 맞지 않는 영상 {off_topic}개는 댓글을 모으지 않았어요.")
    return {"count": len(kept), "found": len(found), "comments": len(comments), "units": units, "warnings": warnings}
