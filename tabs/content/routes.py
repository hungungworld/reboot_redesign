"""콘텐츠 제작 API (담당: 나원). 모두 로그인 필요 · 쓰기는 CSRF (core.js가 자동).

  GET    /api/content/meta                          모델·연결 상태 · 선택지
  GET    /api/content/meta/check                    연결 테스트 (OpenAI 모델 조회 · Higgsfield 없는 요청 조회 — 둘 다 비용 없음)
  GET    /api/content/campaigns?q=&status=          내 캠페인 목록 (+ 상태별 개수)
  POST   /api/content/campaigns                     시트로 캠페인 시작 {content_type, channels, brand_focus, product_ids, goal, extra_request}
  GET    /api/content/campaigns/<id>                복원: 시트 · 결정 · 대화 · 버전 · 작업 · 제품 근거
  PATCH  /api/content/campaigns/<id>                {expected_revision, sheet?, title?, status?, settings?, reference_text?} (다르면 409)
  DELETE /api/content/campaigns/<id>                만든 사람·관리자만
  POST   /api/content/campaigns/<id>/copy           {channel} 다른 채널로 만들기 — 시트·정한 내용 복사, 대화·버전·파일은 복사 안 함
  POST   /api/content/campaigns/<id>/decisions      {expected_revision, changes:[{field, value, status}], source} 후보 선택·직접 입력·되돌리기
  POST   /api/content/campaigns/<id>/chat           {message, expected_revision, base_version_id?, client_request_id?, force_action?, kickoff?}
  POST   /api/content/campaigns/<id>/interview      {action: start|answer|skip|finish, expected_revision, option?|value?, client_request_id?}
  POST   /api/content/campaigns/<id>/versions       직접 작성 {kind, body, label?, title?, parent_id?}
  POST   /api/content/campaigns/<id>/images         브라우저 편집기 PNG (multipart: file, parent_id, text_layers, edits, baked)
  POST   /api/content/campaigns/<id>/references     레퍼런스 이미지 (multipart: file, note?) — 최대 3장 · AI 요약 · 대화에서만 씀
  DELETE /api/content/campaigns/<id>/references/<rid>
  POST   /api/content/campaigns/<id>/select         {version_id|null} 기준 결과물
  GET    /api/content/campaigns/<id>/export         ZIP (?version=<id> 한 버전만)
  POST   /api/content/suggest                       {field: goal|extra, sheet} → 후보 3개 (키 없으면 예시 추천)
  POST   /api/content/versions/<id>/final           {final: true|false}
  POST   /api/content/versions/<id>/review          컴플라이언스(화면에서 판정한 결과) 기록
  POST   /api/content/versions/<id>/ai-review       AI 보조 검토 (review_v2)
  GET    /api/content/jobs/<id>                     이미지·영상 작업 상태
  POST   /api/content/jobs/<id>/cancel              Higgsfield 요청 취소 (시작 전만 가능)
  GET    /api/content/files/<이름>.png|jpg|webp|mp4 생성 이미지·영상 (로그인 필요)
  GET    /api/content/assets                        브랜드 자산 목록 (로고 · 제품 · 캐릭터 Rebu 사진)
  GET    /api/content/assets/<ID>.jpg               자산 사진 (로그인 필요)
"""
import json

from flask import Blueprint, Response, jsonify, request, send_from_directory
from flask_login import current_user

from server import worklog
from server.core.ai import AIError
from server.extensions import db
from server.models import iso, utcnow
from server.security import ApiError, clip, json_body
from . import assets, brandkit, conversation, higgsfield, media, prompts, providers, repository, review, schemas
from . import options as O
from .export import build_zip
from .models import ContentCampaign, ContentDecisionEvent, ContentJob, ContentMessage, ContentReference, ContentVersion

bp = Blueprint("content", __name__, url_prefix="/api/content")


def _who():
    return current_user.name or current_user.email.split("@")[0]


def _campaign(cid):
    c = db.session.get(ContentCampaign, cid)
    if c is None or c.content_type in O.RETIRED_TYPES:  # 없앤 콘텐츠 종류(상품 설명·기획서)는 숨김 — DB에는 남음
        raise ApiError("없는 캠페인입니다.", 404)
    return c


def _version(vid):
    v = db.session.get(ContentVersion, vid)
    if v is None:
        raise ApiError("없는 버전입니다.", 404)
    return v


def _state(cid, status=200, **extra):
    c = db.session.get(ContentCampaign, cid)
    return jsonify(campaign=repository.campaign_state(c), **extra), status


@bp.errorhandler(repository.Conflict)
def _conflict(e):
    db.session.rollback()
    c = db.session.get(ContentCampaign, e.campaign_id)
    if c is None:
        return jsonify(error="캠페인이 삭제되었습니다."), 404
    return jsonify(error="다른 곳에서 먼저 바뀌었어요. 최신 상태를 불러왔으니 확인 후 다시 시도하세요.",
                   conflict=True, revision=c.revision, campaign=repository.campaign_state(c)), 409


@bp.errorhandler(repository.Duplicate)
def _duplicate(e):
    db.session.rollback()
    c = db.session.get(ContentCampaign, e.campaign_id)
    body = {"error": "같은 요청을 이미 처리했어요. 최신 대화를 불러왔어요.", "duplicate": True}
    if c is not None:
        body["campaign"] = repository.campaign_state(c)
    return jsonify(body), 409


# ───────── 기준 정보 ─────────

@bp.get("/meta")
def meta():
    return jsonify(
        textModel=providers.text_model(), textReady=providers.ready(),
        imageModel=providers.image_model(), imageReady=providers.ready(), imageEdit=providers.image_supports_edit(),
        video={"modes": O.VIDEO_MODES, "model": "Seedance 2.0", "resolution": higgsfield.VIDEO_RESOLUTION,
               "higgsfieldKey": higgsfield.ready()},
        imageProviders=O.IMAGE_PROVIDERS, higgsfieldImageModel=higgsfield.IMAGE_MODEL,
        connections={"openai": {"key": providers.ready(), "textModel": providers.text_model(),
                                "imageModel": providers.image_model(), "imageEdit": providers.image_supports_edit()},
                     "higgsfield": {"key": higgsfield.ready(), "wired": True, "imageModel": higgsfield.IMAGE_MODEL,
                                    "videoModel": f"Seedance 2.0 · {higgsfield.VIDEO_RESOLUTION}"}},
        defaults=O.DEFAULT_SETTINGS, answerLengths=O.ANSWER_LENGTHS, promptVersion=prompts.PROMPT_BUNDLE_VERSION,
        contentTypes=[{"id": k, "label": v["label"], "kind": v["kind"]} for k, v in O.CONTENT_TYPES.items()],
        channels=[{"id": k, "label": v["label"]} for k, v in O.CHANNELS.items()], statuses=list(O.STATUSES),
        kinds=O.KIND_LABELS, decisionLabels=O.DECISION_LABELS, flows=O.CONTENT_FLOWS,
        references={"max": media.REF_PER_CAMPAIGN, "maxBytes": media.REF_MAX_BYTES},
        character={"choices": list(assets.CHARACTER_CHOICES), "contentTypes": list(assets.CHARACTER_TYPES), "name": "Rebu"},
        productKit={pid: {"form": p.get("form"), "flavors": (p.get("flavors") or {}).get("value")}
                    for pid, p in brandkit.load()["products"].items()})


@bp.get("/meta/check")
def meta_check():
    """연결 테스트 — OpenAI는 모델 조회만, Higgsfield는 없는 요청 상태 조회만 (둘 다 생성 비용 없음)."""
    hf = higgsfield.check_connection()
    hf["key"] = higgsfield.ready()
    return jsonify(openai=providers.check_connection(), higgsfield=hf)


# ───────── 캠페인 ─────────

@bp.get("/campaigns")
def campaigns_list():
    q = (request.args.get("q") or "").strip().lower()[:100]
    status = (request.args.get("status") or "").strip()
    rows = (ContentCampaign.query.filter(ContentCampaign.content_type.notin_(O.RETIRED_TYPES))
            .order_by(ContentCampaign.updated_at.desc()).limit(500).all())
    products = repository.products_index()
    counts = {s: 0 for s in O.STATUSES}
    for c in rows:
        counts[c.status] = counts.get(c.status, 0) + 1
    if q:
        terms = q.split()
        rows = [c for c in rows if all(t in repository.search_text(c, products) for t in terms)]
    if status and status != "전체":
        rows = [c for c in rows if c.status == status]
    ids = [c.id for c in rows]
    vcount, covers = {}, {}
    if ids:
        for cid, n in (db.session.query(ContentVersion.campaign_id, db.func.count(ContentVersion.id))
                       .filter(ContentVersion.campaign_id.in_(ids)).group_by(ContentVersion.campaign_id)):
            vcount[cid] = n
        finals = {c.id: c.final_version_id for c in rows}
        for vid, cid, name in (db.session.query(ContentVersion.id, ContentVersion.campaign_id, ContentVersion.image_file)
                               .filter(ContentVersion.campaign_id.in_(ids), ContentVersion.image_file.isnot(None))
                               .order_by(ContentVersion.id.desc())):
            if finals.get(cid) == vid or cid not in covers:  # 최종본 이미지 우선, 없으면 가장 최근 이미지
                covers[cid] = name
    items = [repository.campaign_summary(c, vcount.get(c.id, 0), covers.get(c.id), products) for c in rows]
    total_all = sum(counts.values())
    return jsonify(items=items, total=total_all, counts=counts, active=counts.get("제작 중", 0) + counts.get("검토 중", 0))


SHEET_LANGUAGES = {"한국어": "한국어", "영어": "미국 영어 (US English)"}  # 시트 칩 → 결정 값 (대화 질문 보기와 같은 값)


@bp.post("/campaigns")
def campaigns_create():
    data = json_body()
    products = repository.products_index()
    try:
        sheet = repository.build_sheet(data, products)
        character = (assets.norm_character(data["use_character"])
                     if data.get("use_character") and sheet["content_type"] in assets.CHARACTER_TYPES else None)
    except ValueError as e:
        raise ApiError(str(e))
    try:  # 브라우저에 저장된 새 캠페인 기본값 — 이상하면 기본값으로
        settings = repository.norm_settings(data["settings"]) if isinstance(data.get("settings"), dict) else dict(O.DEFAULT_SETTINGS)
    except ValueError:
        settings = dict(O.DEFAULT_SETTINGS)
    user = _who()
    title = clip(data.get("title"), 80).strip() or repository.auto_title(sheet)
    c = ContentCampaign(title=title, content_type=sheet["content_type"], brand_focus=sheet["brand_focus"],
                        goal=sheet["goal"], extra_request=sheet["extra_request"],
                        reference_text=repository.clip_text(data.get("reference_text"), repository.REFERENCE_MAX),
                        status="제작 중", revision=1, created_by=user, created_by_id=current_user.id, updated_by=user)
    c.channels, c.product_ids = sheet["channels"], sheet["product_ids"]
    c.settings = settings
    # 시트에서 고른 콘텐츠 언어(한국어 · 영어) → 확정. 안 보냈으면 영어를 '초기 제안'으로 (README 5.4)
    lang = SHEET_LANGUAGES.get(data.get("language"))
    if lang:
        c.decisions = {"language": {"value": lang, "status": "confirmed", "source": "sheet", "by": user,
                                    "at": iso(utcnow()), "revision": 1, "messageId": None}}
    else:
        c.decisions = {"language": {"value": "미국 영어 (US English)", "status": "proposed", "source": "default", "by": "기본값",
                                    "at": iso(utcnow()), "revision": 1, "messageId": None}}
    ratio = O.default_ratio(sheet)
    if ratio:  # 채널 기본 비율도 '초기 제안' — 대화에서 바꿀 수 있음
        c.decisions = dict(c.decisions, ratio={"value": ratio, "status": "proposed", "source": "default", "by": "채널 기본값",
                                               "at": iso(utcnow()), "revision": 1, "messageId": None})
    if character is not None:  # 시트에서 고른 캐릭터 Rebu 사용 여부 → 확정 결정
        c.decisions = dict(c.decisions, use_character={"value": character, "status": "confirmed", "source": "sheet",
                                                       "by": user, "at": iso(utcnow()), "revision": 1, "messageId": None})
    db.session.add(c)
    db.session.flush()
    lab = repository.labels_of(sheet, products)
    summary = f"{lab['contentType']} · {', '.join(lab['channels'])} · {lab['target']}"
    if sheet["goal"]:
        summary += f" · 목적: {sheet['goal']}"
    repository.add_event(c, f"기본 정보 시트로 시작했어요 — {summary}", {"kind": "created"}, user=user)
    db.session.commit()
    worklog.add("content", "기타", f"캠페인 생성: {c.title}", detail={
        "campaign": c.id, "content_type": sheet["content_type"], "channels": sheet["channels"],
        "brand_focus": sheet["brand_focus"], "product_ids": sheet["product_ids"]})
    return _state(c.id, 201)


@bp.get("/campaigns/<int:cid>")
def campaigns_get(cid):
    c = _campaign(cid)
    return jsonify(campaign=repository.campaign_state(c))


@bp.patch("/campaigns/<int:cid>")
def campaigns_patch(cid):
    c = _campaign(cid)
    data = json_body()
    expected = conversation.parse_revision(data)
    if expected != c.revision:
        raise repository.Conflict(cid)
    user = _who()
    changed = False
    plan = None
    sheet_in = data.get("sheet") if isinstance(data.get("sheet"), dict) else {}
    if sheet_in:
        changes = [{"field": f"sheet.{k}", "value": v, "status": "confirmed"} for k, v in sheet_in.items()
                   if k in repository.SHEET_FIELDS]
        if "channel" in sheet_in and "channels" not in sheet_in:
            changes.append({"field": "sheet.channels", "value": [sheet_in["channel"]], "status": "confirmed"})
        if sheet_in.get("use_character"):  # 캐릭터 Rebu 사용 여부는 결정 사항으로 저장
            try:
                changes.append({"field": "decisions.use_character", "value": assets.norm_character(sheet_in["use_character"]),
                                "status": "confirmed"})
            except ValueError as e:
                raise ApiError(str(e))
        plan = repository.plan_changes(repository.sheet_of(c), c.decisions, changes, user=user, source="sheet",
                                       revision=c.revision)
        if plan["rejected"]:
            raise ApiError(plan["rejected"][0]["reason"])
        if plan["applied"]:
            repository.commit_changes(c, plan, source="sheet", user=user)
            changed = True
    if "title" in data:
        title = clip(data.get("title"), 80).strip()
        if not title:
            raise ApiError("캠페인명을 입력하세요.")
        if title != c.title:
            c.title, changed = title, True
    if "status" in data:
        if data["status"] not in O.STATUSES:
            raise ApiError("상태는 제작 중 · 검토 중 · 완료 중 하나입니다.")
        if data["status"] != c.status:
            c.status, changed = data["status"], True
    if isinstance(data.get("settings"), dict):
        try:
            s = repository.norm_settings(data["settings"], repository.settings_of(c))
        except ValueError as e:
            raise ApiError(str(e))
        if s != repository.settings_of(c):
            c.settings, changed = s, True
    if "reference_text" in data:
        ref = repository.clip_text(data.get("reference_text"), repository.REFERENCE_MAX)
        if ref != (c.reference_text or ""):
            c.reference_text, changed = ref, True
    if not changed:
        return jsonify(campaign=repository.campaign_state(c), unchanged=True)
    if plan and plan["applied"]:
        repository.add_event(c, "기본 정보 시트를 바꿨어요 — " + repository.describe_applied(plan["applied"]),
                             {"kind": "sheet_changed", "applied": plan["applied"]}, user=user)
    c.updated_by, c.updated_at = user, utcnow()
    repository.guarded_bump(cid, expected)
    db.session.commit()
    return _state(cid)


@bp.delete("/campaigns/<int:cid>")
def campaigns_delete(cid):
    c = _campaign(cid)
    if not (current_user.is_admin or (c.created_by_id and c.created_by_id == current_user.id)):
        raise ApiError("캠페인을 만든 사람이나 관리자만 삭제할 수 있어요.", 403)
    files = [n for (n,) in db.session.query(ContentVersion.image_file).filter(
        ContentVersion.campaign_id == cid, ContentVersion.image_file.isnot(None))]
    files += [r.file for r in repository.references_of(cid)]
    title = c.title
    for model in (ContentVersion, ContentMessage, ContentDecisionEvent, ContentJob, ContentReference):
        model.query.filter_by(campaign_id=cid).delete()
    db.session.delete(c)
    db.session.commit()
    media.delete_files(files)
    worklog.add("content", "기타", f"캠페인 삭제: {title}", detail={"campaign": cid, "files": len(files)})
    return jsonify(ok=True)


# 다른 채널로 만들기 — 채널마다 달라지는 값(비율 · 캡션 · 멘트)은 복사하지 않음 (비율은 새 채널 기본값 제안)
COPY_SKIP_DECISIONS = ("ratio", "caption", "hook")


@bp.post("/campaigns/<int:cid>/copy")
def campaigns_copy(cid):
    src = _campaign(cid)
    data = json_body()
    channel = str(data.get("channel") or "").strip()
    if channel not in O.CHANNELS:
        raise ApiError("게시 채널을 골라 주세요.")
    if src.content_type == O.CRM_TYPE or channel == O.CRM_CHANNEL:
        raise ApiError("CRM 메시지는 CRM 채널하고만 만들 수 있어 다른 채널로 바꿀 수 없어요.")
    if channel in src.channels:
        raise ApiError("지금과 같은 채널이에요. 다른 채널을 골라 주세요.")
    products = repository.products_index()
    try:
        sheet = repository.build_sheet(dict(repository.sheet_of(src), channels=[channel]), products)
    except ValueError as e:
        raise ApiError(str(e))
    user, now = _who(), iso(utcnow())
    # 원래 캠페인의 최종본(텍스트가 아니면 가장 최근 텍스트 버전) 문구를 참고 문구로 넘겨 새 채널에 맞게 고쳐 쓰게 함
    texts = ContentVersion.query.filter(ContentVersion.campaign_id == cid, ContentVersion.kind.in_(O.TEXT_KINDS))
    base = (texts.filter(ContentVersion.id == src.final_version_id).first() if src.final_version_id else None) \
        or texts.order_by(ContentVersion.id.desc()).first()
    ref = (f"[원래 캠페인 '{src.title}'의 {base.label} v{base.seq} 문구 — {O.CHANNELS[channel]['label']}에 맞게 고쳐 쓰기]\n{base.body}"
           if base else src.reference_text or "")
    c = ContentCampaign(title=repository.auto_title(sheet), content_type=sheet["content_type"], brand_focus=sheet["brand_focus"],
                        goal=sheet["goal"], extra_request=sheet["extra_request"],
                        reference_text=repository.clip_text(ref, repository.REFERENCE_MAX),
                        status="제작 중", revision=1, created_by=user, created_by_id=current_user.id, updated_by=user)
    c.channels, c.product_ids = sheet["channels"], sheet["product_ids"]
    c.settings = repository.settings_of(src)
    decisions = {k: dict(d, revision=1, messageId=None) for k, d in (src.decisions or {}).items()
                 if k not in COPY_SKIP_DECISIONS and (d or {}).get("value")}
    ratio = O.default_ratio(sheet)
    if ratio:
        decisions["ratio"] = {"value": ratio, "status": "proposed", "source": "default", "by": "채널 기본값",
                              "at": now, "revision": 1, "messageId": None}
    c.decisions = decisions
    db.session.add(c)
    db.session.flush()
    old = ", ".join(O.CHANNELS[ch]["label"] for ch in src.channels if ch in O.CHANNELS)
    repository.add_event(c, f"'{src.title}'에서 채널만 바꿔 복사했어요 — {old} → {O.CHANNELS[channel]['label']} "
                            f"(정한 내용 {len(decisions)}개 · 대화와 버전은 복사하지 않음)",
                         {"kind": "created", "copiedFrom": src.id}, user=user)
    db.session.commit()
    worklog.add("content", "기타", f"캠페인 복사: {src.title} → {c.title}", detail={
        "campaign": c.id, "from": src.id, "channel": channel})
    return _state(c.id, 201)


@bp.post("/campaigns/<int:cid>/decisions")
def campaigns_decisions(cid):
    c = _campaign(cid)
    data = json_body()
    expected = conversation.parse_revision(data)
    if expected != c.revision:
        raise repository.Conflict(cid)
    changes = data.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ApiError("바꿀 항목이 없습니다.")
    source = data.get("source") if data.get("source") in ("candidate", "manual", "undo") else "manual"
    user = _who()
    try:
        message_id = int(data["message_id"]) if data.get("message_id") is not None else None
    except (TypeError, ValueError):
        message_id = None
    plan = repository.plan_changes(repository.sheet_of(c), c.decisions, changes[:10], user=user, source=source,
                                   revision=c.revision)
    if plan["rejected"]:
        raise ApiError(plan["rejected"][0]["reason"])
    if not plan["applied"]:
        return jsonify(campaign=repository.campaign_state(c), unchanged=True)
    repository.commit_changes(c, plan, source=source, user=user, message_id=message_id)
    verb = {"candidate": "후보를 골랐어요", "undo": "되돌렸어요", "manual": "결정 사항을 바꿨어요"}[source]
    repository.add_event(c, f"{verb} — {repository.describe_applied(plan['applied'])}",
                         {"kind": "decision_changed", "source": source, "applied": plan["applied"]}, user=user)
    c.updated_by, c.updated_at = user, utcnow()
    repository.guarded_bump(cid, expected)
    db.session.commit()
    return _state(cid, applied=plan["applied"])


@bp.post("/campaigns/<int:cid>/chat")
def campaigns_chat(cid):
    c = _campaign(cid)
    data = json_body(max_bytes=64 * 1024)
    out = conversation.run_turn(c, data, _who())
    return jsonify(out)


@bp.post("/campaigns/<int:cid>/interview")
def campaigns_interview(cid):
    c = _campaign(cid)
    data = json_body()
    return jsonify(conversation.run_interview(c, data, _who()))


@bp.post("/campaigns/<int:cid>/images")
def images_upload(cid):
    """브라우저 편집기 결과 PNG → 새 이미지 버전 (부모 연결 · 원본 보존 · AI 아님)."""
    c = _campaign(cid)
    f = request.files.get("file")
    if f is None:
        raise ApiError("이미지 파일이 없습니다.")
    data = f.read(media.UPLOAD_MAX + 1)
    if len(data) > media.UPLOAD_MAX:
        raise ApiError("이미지가 너무 큽니다 (12MB 이하).", 413)
    size = media.png_size(data)
    if size is None or not (16 <= size[0] <= 4096 and 16 <= size[1] <= 4096):
        raise ApiError("PNG 이미지만 저장할 수 있어요 (가로·세로 16~4096px).")
    try:
        parent = db.session.get(ContentVersion, int(request.form.get("parent_id") or 0))
    except (TypeError, ValueError):
        parent = None
    if parent is None or parent.campaign_id != cid or parent.kind != "image":
        raise ApiError("편집한 원본 이미지 버전을 찾지 못했습니다.")
    try:
        layers = media.clean_layers(json.loads(request.form.get("text_layers") or "[]"))
        edits = json.loads(request.form.get("edits") or "{}")
    except ValueError:
        raise ApiError("편집 정보를 읽지 못했습니다.")
    edits = {k: clip(v, 40) for k, v in (edits.items() if isinstance(edits, dict) else []) if k in media.EDIT_KEYS}
    baked = request.form.get("baked") == "1"
    name = media.save_upload(data, cid)
    if not name:
        raise ApiError("이미지를 저장하지 못했습니다 (서버 저장 공간 확인).", 500)
    user = _who()
    sheet = repository.sheet_of(c)
    facts = brandkit.product_facts(sheet["product_ids"])
    snap, shash = repository.make_snapshot(sheet, c.decisions, repository.settings_of(c), facts.get("facts_version"),
                                           model=None, mode="manual")
    drawn = ((parent.snapshot or {}).get("image") or {}).get("image_text") or []  # AI가 이미지에 그린 문구는 그대로 이어받음
    snap["image"] = {"model": None, "size": f"{size[0]}x{size[1]}", "mode": "manual_edit", "text_layers": layers,
                     "baked": baked or bool(drawn), "image_text": drawn, "edits": edits,
                     "base": {"label": parent.label, "seq": parent.seq}}
    note = [f"{parent.label} · v{parent.seq}을 브라우저 편집기로 수정 (AI 아님)"]
    if baked:
        note.append("글자를 이미지에 합성")
    note.append("제품 형태는 시안 (실제 패키지 아님)")
    v = ContentVersion(campaign_id=cid, seq=repository.next_seq(cid), kind="image", label=parent.label,
                       title=parent.title, body=parent.body, image_file=name, parent_id=parent.id, source="manual",
                       approach=", ".join(f"{k} {val}" for k, val in edits.items())[:300], note=" · ".join(note)[:500],
                       facts_version=facts.get("facts_version") or "", snapshot_hash=shash, created_by=user)
    v.snapshot = snap
    v.review = {"keyword": review.keyword_check("\n".join(t["text"] for t in layers + drawn), "copy", sheet, facts),
                "imageScope": media.IMAGE_SCOPE if drawn else "이미지 속 픽셀·글자는 검사하지 않음 (텍스트 레이어만 확인)"}
    db.session.add(v)
    db.session.flush()
    c.selected_version_id = v.id
    c.updated_by, c.updated_at = user, utcnow()
    repository.add_event(c, f"이미지를 직접 편집했어요 · {v.label} (v{v.seq})", {"kind": "manual_version", "versionId": v.id},
                         user=user)
    db.session.commit()
    worklog.add("content", "기타", f"이미지 직접 편집: {c.title} · {v.label} v{v.seq}",
                detail={"campaign": cid, "version": v.id, "parent": parent.id, "edits": edits})
    return _state(cid, 201, versionId=v.id)


@bp.post("/campaigns/<int:cid>/versions")
def versions_create(cid):
    """직접 작성 (키 없이도). 결과 수정은 항상 새 버전 (README 9장)."""
    c = _campaign(cid)
    data = json_body(max_bytes=256 * 1024)
    kind = data.get("kind")
    if kind not in O.TEXT_KINDS:
        raise ApiError("결과물 종류를 고르세요 (카피 · 카드뉴스 · 숏폼 구성안 · 상품 설명 · CRM 메시지 · 기획서).")
    body = repository.clip_text(data.get("body"), 20000)
    if not body:
        raise ApiError("본문을 입력하세요.")
    parent = None
    if data.get("parent_id") not in (None, ""):
        try:
            parent = db.session.get(ContentVersion, int(data["parent_id"]))
        except (TypeError, ValueError):
            parent = None
        if parent is None or parent.campaign_id != cid:
            raise ApiError("기준 버전을 찾지 못했습니다.")
    user = _who()
    label = clip(data.get("label"), 30).strip() or (parent.label if parent else "직접 작성")
    sheet = repository.sheet_of(c)
    facts = brandkit.product_facts(sheet["product_ids"])
    snap, shash = repository.make_snapshot(sheet, c.decisions, repository.settings_of(c), facts.get("facts_version"),
                                           model=None, mode="manual")
    v = ContentVersion(campaign_id=cid, seq=repository.next_seq(cid), kind=kind, label=label,
                       title=clip(data.get("title"), 200).strip(), body=body, parent_id=parent.id if parent else None,
                       source="manual", note="직접 작성" + (f" · {parent.label} v{parent.seq}에서 수정" if parent else ""),
                       facts_version=facts.get("facts_version") or "", snapshot_hash=shash, created_by=user)
    v.snapshot = snap
    v.review = {"keyword": review.keyword_check(body, kind, sheet, facts)}
    db.session.add(v)
    db.session.flush()
    c.selected_version_id = v.id
    c.updated_by, c.updated_at = user, utcnow()
    repository.add_event(c, f"초안을 직접 작성했어요 — {v.label} (v{v.seq})", {"kind": "manual_version", "versionId": v.id}, user=user)
    db.session.commit()
    return _state(cid, 201, versionId=v.id)


@bp.post("/campaigns/<int:cid>/references")
def references_upload(cid):
    """레퍼런스 이미지 (multipart: file, note?) → 저장 → 대화 AI가 한 번 보고 요약. 이미지 생성 참고 사진으로는 쓰지 않음."""
    c = _campaign(cid)
    if len(repository.references_of(cid)) >= media.REF_PER_CAMPAIGN:
        raise ApiError(f"레퍼런스는 캠페인마다 {media.REF_PER_CAMPAIGN}장까지예요. 하나를 지우고 올려 주세요.")
    f = request.files.get("file")
    if f is None:
        raise ApiError("이미지 파일이 없습니다.")
    data = f.read(media.REF_MAX_BYTES + 1)
    if len(data) > media.REF_MAX_BYTES:
        raise ApiError("이미지가 너무 큽니다 (5MB 이하).", 413)
    try:
        name, dims = media.save_reference(data, cid)
    except ValueError as e:
        raise ApiError(str(e))
    if not name:
        raise ApiError("이미지를 저장하지 못했습니다 (서버 저장 공간 확인).", 500)
    user = _who()
    note = clip(request.form.get("note"), 300).strip()
    ref = ContentReference(campaign_id=cid, file=name, width=dims[0], height=dims[1], note=note, created_by=user)
    db.session.add(ref)
    repository.add_event(c, "레퍼런스 이미지를 올렸어요" + (f" — {note}" if note else ""), {"kind": "reference"}, user=user)
    db.session.commit()
    rid = ref.id
    # AI 요약 (DB에 쓰지 않는 동안 호출 · 실패해도 이미지는 남고 다음 대화에서 직접 봄)
    summary, warn = "", None
    if providers.ready():
        try:
            out = providers.call_json_images(prompts.REFERENCE_V1, prompts.dumps({"note": note or None}),
                                             [(data, media.REF_MIMES[media.file_ext(data)])], schema=schemas.REFERENCE,
                                             name="content_reference", action="레퍼런스 이미지 요약", max_tokens=700,
                                             timeout=60)
            summary = clip(out.get("summary"), 1500).strip()
        except AIError as e:
            warn = f"이미지는 올렸지만 AI가 보지 못했어요: {e}"
    else:
        warn = "OpenAI 키가 없어 AI 요약 없이 저장했어요."
    if summary:
        ref = db.session.get(ContentReference, rid)
        if ref is not None:
            ref.summary, ref.summary_model = summary, providers.text_model()
            db.session.commit()
    worklog.add("content", "기타", f"레퍼런스 이미지 업로드: {c.title}", detail={"campaign": cid, "summary": bool(summary)})
    return _state(cid, 201, warning=warn)


@bp.delete("/campaigns/<int:cid>/references/<int:rid>")
def references_delete(cid, rid):
    _campaign(cid)
    ref = db.session.get(ContentReference, rid)
    if ref is None or ref.campaign_id != cid:
        raise ApiError("없는 레퍼런스입니다.", 404)
    name = ref.file
    db.session.delete(ref)
    db.session.commit()
    media.delete_files([name])
    return _state(cid)


@bp.post("/campaigns/<int:cid>/select")
def campaigns_select(cid):
    c = _campaign(cid)
    data = json_body()
    vid = data.get("version_id", data.get("versionId"))
    if vid is not None:
        v = db.session.get(ContentVersion, int(vid)) if str(vid).isdigit() else None
        if v is None or v.campaign_id != cid:
            raise ApiError("이 캠페인의 버전이 아닙니다.")
        vid = v.id
    c.selected_version_id = vid
    db.session.commit()
    return _state(cid)


@bp.get("/campaigns/<int:cid>/export")
def campaigns_export(cid):
    c = _campaign(cid)
    only = request.args.get("version")
    only_id = None
    if only:
        if not only.isdigit():
            raise ApiError("버전 번호가 올바르지 않습니다.")
        v = db.session.get(ContentVersion, int(only))
        if v is None or v.campaign_id != cid:
            raise ApiError("이 캠페인의 버전이 아닙니다.", 404)
        only_id = v.id
    data, filename = build_zip(c, only_id)
    worklog.add("content", "기타", f"내보내기: {c.title}", detail={"campaign": cid, "version": only_id, "bytes": len(data)})
    return Response(data, mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ───────── 시트 추천 ─────────

@bp.post("/suggest")
def suggest():
    data = json_body()
    field = data.get("field")
    if field not in ("goal", "extra"):
        raise ApiError("추천할 항목은 목적(goal) 또는 추가 요청(extra)입니다.")
    raw = data.get("sheet") if isinstance(data.get("sheet"), dict) else {}
    products = repository.products_index()
    sheet = dict(repository.EMPTY_SHEET)
    try:
        for k in repository.SHEET_FIELDS:
            if raw.get(k) not in (None, "", []):
                sheet[k] = repository.norm_sheet_value(k, raw[k], products)
    except ValueError as e:
        raise ApiError(str(e))
    if not sheet["content_type"]:
        raise ApiError("만들 콘텐츠를 먼저 골라 주세요.")
    basis = {k: sheet[k] for k in ("content_type", "channels", "brand_focus", "product_ids")}
    if not providers.ready():
        return jsonify(field=field, items=O.example_suggestions(field, sheet), source="example", basis=basis,
                       note="OpenAI 키가 없어 예시 추천을 보여 드려요. 설정 › 연결에서 키를 넣으면 AI 추천을 받을 수 있어요.")
    from server.catalog import service as catalog
    facts = brandkit.product_facts(sheet["product_ids"])
    lab = repository.labels_of(sheet, products)
    user = {"field": field, "sheet": {"content_type": lab["contentType"], "channels": lab["channels"], "target": lab["target"],
                                      "brand_focus": sheet["brand_focus"], "goal": sheet["goal"] or None,
                                      "extra_request": sheet["extra_request"] or None},
            "brand_facts": brandkit.brand_view(facts),
            "product_facts": facts.get("products"),
            "upcoming_seasons": [{"name": s["name"], "start": s["start"], "dday": s["dday"]} for s in catalog.list_seasons()[:3]]}
    try:
        out = providers.call_json(prompts.suggest_system(field), prompts.dumps(user), schema=schemas.SUGGEST,
                                  name="content_suggest", action=f"시트 추천 ({'목적' if field == 'goal' else '추가 요청'})",
                                  max_tokens=800, timeout=60)
    except AIError as e:
        return jsonify(field=field, items=O.example_suggestions(field, sheet), source="example", basis=basis,
                       note=f"AI 추천을 받지 못해 예시를 보여 드려요 ({e})")
    items = []
    for it in out.get("items") or []:
        if isinstance(it, dict) and str(it.get("value") or "").strip():
            items.append({"value": clip(it["value"], 120).strip(), "reason": clip(it.get("reason"), 160).strip()})
        if len(items) >= 3:
            break
    if not items:
        return jsonify(field=field, items=O.example_suggestions(field, sheet), source="example", basis=basis,
                       note="AI가 후보를 돌려주지 않아 예시를 보여 드려요.")
    return jsonify(field=field, items=items, source="ai", basis=basis, model=providers.text_model())


# ───────── 버전 ─────────

@bp.post("/versions/<int:vid>/final")
def versions_final(vid):
    v = _version(vid)
    c = _campaign(v.campaign_id)
    data = json_body()
    final = data.get("final", True) is not False
    user = _who()
    if final:
        c.final_version_id = v.id
        text = f"최종본으로 지정했어요 — {v.label} (v{v.seq}) · 승인·사실 확인 완료를 뜻하지 않아요"
    else:
        if c.final_version_id != v.id:
            return _state(c.id)
        c.final_version_id = None
        text = f"최종본 지정을 해제했어요 — {v.label} (v{v.seq})"
    repository.add_event(c, text, {"kind": "final", "versionId": v.id, "final": final}, user=user)
    c.updated_by, c.updated_at = user, utcnow()
    db.session.commit()
    worklog.add("content", "승인 · 선정", f"최종본 {'지정' if final else '해제'}: {c.title} · {v.label} v{v.seq}",
                detail={"campaign": c.id, "version": v.id, "kind": v.kind, "note": "최종본 지정은 법적 승인이 아님"})
    return _state(c.id)


@bp.post("/versions/<int:vid>/review")
def versions_review(vid):
    """화면(컴플라이언스 파트 규칙 판정) 결과를 버전에 기록 — 판정은 1차 참고용."""
    v = _version(vid)
    data = json_body()
    verdict = str(data.get("verdict") or "")
    if verdict not in ("BLOCK", "REVISE", "COND", "REVIEW", "PASS", "UNAVAILABLE", "ERROR"):
        raise ApiError("판정 값이 올바르지 않습니다.")
    findings = []
    for f in (data.get("findings") or [])[:30]:
        if isinstance(f, dict):
            findings.append({"code": clip(f.get("code"), 10), "name": clip(f.get("name"), 80), "verdict": clip(f.get("verdict"), 10),
                             "terms": [clip(t, 60) for t in (f.get("terms") or [])[:8]]})
    r = v.review
    r["compliance"] = {"verdict": verdict, "label": clip(data.get("label"), 30), "findings": findings,
                       "channel": clip(data.get("channel"), 20), "products": [clip(p, 20) for p in (data.get("products") or [])[:4]],
                       "by": _who(), "at": iso(utcnow()), "note": "컴플라이언스 규칙 1차 판정 · 법적 승인 아님"}
    v.review = r
    db.session.commit()
    return _state(v.campaign_id)


@bp.post("/versions/<int:vid>/ai-review")
def versions_ai_review(vid):
    v = _version(vid)
    c = _campaign(v.campaign_id)
    if v.kind == "image":
        raise ApiError("이미지는 AI 보조 검토 대상이 아닙니다 (텍스트 레이어만 키워드 확인).")
    if not providers.ready():
        raise ApiError(conversation.NO_KEY, 503)
    sheet = repository.sheet_of(c)
    facts = brandkit.product_facts(sheet["product_ids"])
    user = {"version": {"label": v.label, "kind": O.KIND_LABELS.get(v.kind), "body": v.body[:12000]},
            "sheet": repository.labels_of(sheet), "brand_facts": facts.get("brand"), "product_facts": facts.get("products"),
            "keyword_check": (v.review or {}).get("keyword")}
    out = providers.call_json(prompts.REVIEW_V2, prompts.dumps(user), schema=schemas.REVIEW, name="content_review",
                              action="AI 보조 검토", max_tokens=1800, timeout=90)
    issues = [{k: clip(i.get(k), 400) for k in ("location", "problem", "suggestion", "basis")}
              for i in (out.get("issues") or [])[:12] if isinstance(i, dict)]
    v = db.session.get(ContentVersion, vid)
    r = v.review
    r["ai"] = {"summary": clip(out.get("summary"), 600), "issues": issues, "scope": clip(out.get("checked_scope"), 200),
               "model": providers.text_model(), "at": iso(utcnow()), "by": _who(),
               "note": "같은 모델의 보조 검토 · 독립적인 사실 검증이나 법적 승인이 아님"}
    v.review = r
    db.session.commit()
    return _state(v.campaign_id)


# ───────── 작업 · 파일 ─────────

@bp.get("/jobs/<job_id>")
def jobs_get(job_id):
    if not job_id.isalnum() or len(job_id) > 32:
        raise ApiError("없는 작업입니다.", 404)
    repository.settle_stale_jobs()
    j = db.session.get(ContentJob, job_id)
    if j is None:
        raise ApiError("없는 작업입니다.", 404)
    return jsonify(job=repository.job_dict(j))


@bp.post("/jobs/<job_id>/cancel")
def jobs_cancel(job_id):
    if not job_id.isalnum() or len(job_id) > 32:
        raise ApiError("없는 작업입니다.", 404)
    j = db.session.get(ContentJob, job_id)
    if j is None:
        raise ApiError("없는 작업입니다.", 404)
    try:
        ok, message = media.cancel_job(j)
    except providers.ProviderError as e:
        raise ApiError(str(e), e.status)
    if ok:
        worklog.add("content", "AI 프롬프트", "Higgsfield 취소 요청", detail={"job": j.id, "campaign": j.campaign_id},
                    user_name=_who())
    return jsonify(ok=ok, message=message, job=repository.job_dict(j))


@bp.get("/assets")
def assets_list():
    return jsonify(items=[assets.public(a) for a in assets.all_assets()], version=assets.manifest()["version"])


@bp.get("/assets/<name>")
def assets_file(name):
    a = assets.get(name[:-4]) if (name or "").endswith(".jpg") else None
    if a is None or not assets.path_of(a).is_file():
        raise ApiError("없는 자산입니다.", 404)
    resp = send_from_directory(str(assets.path_of(a).parent), assets.path_of(a).name, mimetype="image/jpeg", max_age=0)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.get("/files/<name>")
def files(name):
    if not media.FILE_RE.match(name or ""):
        raise ApiError("없는 파일입니다.", 404)
    if (ContentVersion.query.filter_by(image_file=name).first() is None
            and ContentReference.query.filter_by(file=name).first() is None):
        raise ApiError("없는 파일입니다.", 404)
    folder = media.upload_dir()
    if not (folder / name).is_file():
        raise ApiError("파일을 찾지 못했습니다 (서버 저장소 확인).", 404)
    resp = send_from_directory(str(folder), name, mimetype=media.MIMES["." + name.rsplit(".", 1)[1]], max_age=0)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["Content-Disposition"] = f'inline; filename="reboot-content-{name}"'
    return resp
