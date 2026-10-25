"""컴플라이언스 API (담당: 소준희). 예전 server/app.py와 같은 주소·입력·출력입니다.

  POST /api/compliance/ai-review        {text, channel, category, rule, rules, product?} → AI 2차 판정 (참고용)
  POST /api/compliance/ko               {items:[{id, title, summary, source}]} → 한글 제목·한 줄 요약 (최대 30건)
  POST /api/compliance/translate        {text} → 한국어 광고 문구를 영어로 (뜻 그대로 · 참고용) {text, note_ko, model}
  POST /api/compliance/translate-ko     {text} → 영어를 한국어로 (뜻 확인용 · 크롬 내장 번역을 못 쓸 때) {text, note_ko, model}
  POST /api/compliance/translate-batch  {texts:[...]} → 일괄 심의 한국어 줄들을 한 번에 영어로 {items:[{text, note_ko}], model} (200줄까지)
  POST /api/compliance/monitor/refresh  규제 모니터링 수집 → monitor-data.js 저장 + 결과 반환 (경고장은 목록만)
  POST /api/compliance/letters/xlsx     multipart file=경고장 목록 XLSX (브라우저에서 받은 것) → 식품 경고장 추가 + 수집 결과 반환
  POST /api/compliance/letters/text     {text, url?, firm?, issued?, subject?} → 경고장 본문의 인용 문구 → 사례집 후보 + 수집 결과 반환
  GET  /api/compliance/ai-usage         이번 달 AI 사용량·예상 비용 (컴플라이언스·CS · 기능별) + 예산 한도
하루 1회 작업: 규제 모니터링 수집 (POST /api/jobs/daily) · 경고장 본문도 30초 간격으로 읽음
"""
from flask import Blueprint, current_app, jsonify, request

from server import jobs, worklog
from server.security import ApiError, clip, json_body

bp = Blueprint("compliance", __name__, url_prefix="/api/compliance")


@bp.post("/ai-review")
def ai_review():
    from . import ai_review as ai
    data = json_body(max_bytes=64 * 1024)
    text = data.get("text", "")
    try:
        out = ai.review(text, data.get("channel", ""), data.get("category", "FOOD"),
                        data.get("rule"), data.get("rules"), clip(data.get("product"), 300),
                        persona=clip(data.get("persona"), ai.MAX_PERSONA))
    except ai.ReviewError as e:
        raise ApiError(str(e), e.status)
    worklog.add("compliance", "AI 프롬프트", "AI 2차 판정", detail={
        "model": out.get("model"), "verdict": out.get("verdict"), "channel": clip(data.get("channel"), 60),
        "copy": clip(text, 400), "summary": clip(out.get("summary"), 300)})
    return jsonify(out)


@bp.get("/ai-usage")
def ai_usage_summary():
    from . import ai_usage
    return jsonify(ai_usage.summary())


@bp.post("/ko")
def ko_items():
    from . import ko_summary
    data = json_body(max_bytes=64 * 1024)
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ApiError("요약할 항목이 없습니다.")
    items = [i for i in items[:30] if isinstance(i, dict)]
    try:
        ko = ko_summary.summarize([{"source": clip(i.get("source"), 80), "id": clip(i.get("id"), 80),
                                    "title": clip(i.get("title"), 300), "summary": clip(i.get("summary"), 500)} for i in items])
    except ko_summary.KoError as e:
        raise ApiError(str(e), 502)
    return jsonify(items=[dict(id=clip(i.get("id"), 80), **(k or {})) for i, k in zip(items, ko)])


@bp.post("/translate-ko")
def translate_to_korean():
    """'한글로 번역'(뜻 확인용)이 크롬 내장 번역을 못 쓸 때 서버 AI로 영→한."""
    from . import translate
    data = json_body(max_bytes=64 * 1024)
    try:
        out = translate.to_korean(data.get("text", ""))
    except translate.TranslateError as e:
        raise ApiError(str(e), e.status)
    worklog.add("compliance", "AI 프롬프트", "영→한 번역 (뜻 확인)", detail={"model": out.get("model"), "chars": len(str(data.get("text", "")))})
    return jsonify(out)


@bp.post("/translate-batch")
def translate_batch():
    from . import translate
    data = json_body(max_bytes=600 * 1024)
    try:
        out = translate.to_english_batch(data.get("texts"))
    except translate.TranslateError as e:
        raise ApiError(str(e), e.status)
    worklog.add("compliance", "AI 프롬프트", "한→영 번역 (일괄 심의)", detail={"model": out.get("model"), "rows": len(out["items"])})
    return jsonify(out)


@bp.post("/translate")
def translate_to_english():
    from . import translate
    data = json_body(max_bytes=64 * 1024)
    text = data.get("text", "")
    try:
        out = translate.to_english(text)
    except translate.TranslateError as e:
        raise ApiError(str(e), e.status)
    worklog.add("compliance", "AI 프롬프트", "한→영 번역", detail={
        "model": out.get("model"), "ko": clip(text, 400), "en": clip(out.get("text"), 400)})
    return jsonify(out)


def _collect_and_save(bodies=False):
    from . import refresh
    data = refresh.collect(bodies=bodies)
    refresh.write(data)
    worklog.add("compliance", "수집", "규제 모니터링 수집", status="실패" if not any(s["items"] for s in data["sources"]) and data["errors"] else "완료",
                detail={"counts": {s["id"]: len(s["items"]) for s in data["sources"]},
                        "errors": [f'{e["label"]}: {e["error"]}' for e in data["errors"]]})
    return data


@bp.post("/monitor/refresh")
def monitor_refresh():
    try:
        with jobs.file_lock("compliance-monitor", stale_seconds=1800):
            return jsonify(_collect_and_save())
    except jobs.Busy:
        raise ApiError("이미 수집 중입니다. 잠시 뒤 다시 확인하세요.", 409)


def _letters_saved(kind, detail):
    from . import refresh, warning_letters
    with jobs.file_lock("compliance-monitor", stale_seconds=1800):
        data = refresh.update_source(warning_letters.as_source())
    worklog.add("compliance", "수집", kind, detail=detail)
    return data


@bp.post("/letters/xlsx")
def letters_xlsx():
    from . import warning_letters
    f = request.files.get("file")
    if f is None or not f.filename:
        raise ApiError("경고장 목록 XLSX 파일을 골라 주세요.")
    try:
        out = warning_letters.import_xlsx(f.read(warning_letters.MAX_XLSX + 1))
        data = _letters_saved("FDA 경고장 목록 올리기", out)
    except warning_letters.LetterError as e:
        raise ApiError(str(e))
    except jobs.Busy:
        raise ApiError("지금 수집 중입니다. 잠시 뒤 다시 올려 주세요.", 409)
    return jsonify(dict(data, letters=out))


@bp.post("/letters/text")
def letters_text():
    from . import warning_letters
    data = json_body(max_bytes=warning_letters.MAX_TEXT * 4 + 4096)
    try:
        out = warning_letters.add_text(data.get("text", ""), clip(data.get("url"), 400), clip(data.get("firm"), 160),
                                       clip(data.get("issued"), 20), clip(data.get("subject"), 200))
        saved = _letters_saved("FDA 경고장 본문 올리기", out)
    except warning_letters.LetterError as e:
        raise ApiError(str(e))
    except jobs.Busy:
        raise ApiError("지금 수집 중입니다. 잠시 뒤 다시 올려 주세요.", 409)
    return jsonify(dict(saved, letters=out))


@jobs.daily("compliance-monitor", "규제 모니터링 수집 (FDA 경고장·수입 거부 · openFDA · Regulations.gov · CourtListener · CBP CROSS · Federal Register)")
def daily_monitor():
    current_app.logger.info("규제 모니터링 수집 시작")
    _collect_and_save(bodies=True)
