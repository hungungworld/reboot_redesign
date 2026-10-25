"""대화 한 턴 (LangGraph 없이 고정 단계 — 설명서 5.1의 흐름을 서버 함수로).

  1 읽기   캠페인 최신 리비전 · 시트 · 결정 · 버전 (업무 DB가 원본, 매 턴 다시 읽음)
  2 맥락   브랜드(항상) + 선택 제품 사실(상태 포함) + 최근 대화 + 서버 신호 + 작업 지침
  3 판단   대화·제작 AI → intent · assistant_text · decision_patch · candidates · questions · action_request
  4 검증   허용 필드 · 제품 ID · 대상 조건 · 확정 조건("좋네"는 확정 아님) · 실행 의도(사용자가 명확히 요청했을 때만)
  5 실행   텍스트 초안·수정(같은 요청 안) · 이미지·영상(작업 등록 → 뒤에서) · 버전 선택
  6 저장   마지막에 짧은 트랜잭션 한 번 — AI를 부르는 동안에는 DB에 쓰지 않음 (SQLite 잠금 · 실패 시 상태 유지)

재개 지점은 DB 자체입니다. 실패(AI 오류·형식 오류·거절)면 아무것도 저장하지 않고 결정도 바꾸지 않습니다.
"""
import re
import time

from flask import current_app
from sqlalchemy.exc import IntegrityError

from server.catalog import service as catalog
from server.core.ai import AIError
from server.extensions import db
from server.models import utcnow
from server.security import ApiError, clip
from . import assets, brandkit, drafts, heuristics, higgsfield, media, prompts, providers, repository, schemas
from . import options as O
from .models import ContentCampaign, ContentMessage, ContentVersion

NO_KEY = ("OpenAI 키가 없어 AI 대화를 쓸 수 없습니다. 설정 › 연결에서 OPENAI_API_KEY를 넣으면 사용 가능합니다. "
          "지금은 시트·결정 사항 정리와 초안 직접 작성으로 진행할 수 있어요.")
TURN_BUDGET = 150  # 초 — 이 시간이 지나면 선택 단계(자동 재작성)는 건너뜀 (gunicorn timeout 300초 안에서)
REQ_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
CHIPS_MAX = 3  # AI가 질문하면 추천 답 ①②③
OFFER_TYPES = tuple(t for t in O.ACTION_TYPES if t not in ("none", "select_version"))
DONE_PATCH_RE = re.compile(r"(반영했|반영해\s*뒀|반영되었|반영됐|적용했|적용됐|저장했|저장됐|변경했|변경됐|바꿨|바꿔\s*뒀|업데이트했|확정했)")
DONE_MAKE_RE = re.compile(r"(만들었|생성했|생성됐|생성되었|작성했|완성했|그렸|완료했|완료됐)")
FILLED_Q = {
    "goal": re.compile(r"목적(은|이|을)\s*(무엇|뭐|어떻게|정해)|어떤\s*목적|목적을\s*(알려|정해)"),
    "channels": re.compile(r"(어느|어떤)\s*채널|채널(은|을|이)\s*(무엇|뭐|어디)"),
    "content_type": re.compile(r"(어떤|무슨)\s*(콘텐츠|형식)(을|를)?\s*(만들|원하)"),
    "target": re.compile(r"(어떤|어느)\s*제품(을|으로|을\s*대상)?\s*(선택|고르|만들|원하)|제작\s*대상(은|을|이)\s*(무엇|뭐)"),
}


# ───────── 2 맥락 ─────────

def _msg_line(m):
    role = {"user": "사용자", "assistant": "AI", "event": "기록"}.get(m.role, m.role)
    line = f"[{role}] {m.text[:600]}"
    p = m.payload or {}
    extra = []
    if p.get("applied"):
        extra.append("서버 반영: " + repository.describe_applied(p["applied"], 6))
    act = p.get("action") or {}
    if act.get("versionLabels"):
        extra.append("생성 버전: " + ", ".join(act["versionLabels"]))
    if act.get("status") in ("skipped", "failed") and act.get("note"):
        extra.append("실행 안 함: " + act["note"][:120])
    if p.get("candidates"):
        extra.append("후보: " + " / ".join(c.get("label", "") for c in p["candidates"]))
    return line + (" (" + " · ".join(extra) + ")" if extra else "")


def flow_context(sheet, decisions):
    """흐름 가이드 — 항목 key · 이름 · 현재 값 · 안내 (순서는 권장 · 서버는 강제하지 않음)."""
    out = []
    for key in O.flow_of(sheet):
        d = decisions.get(key) or {}
        item = {"key": key, "label": O.DECISION_LABELS.get(key, key), "hint": prompts.FLOW_HINTS.get(key, ""),
                "value": d.get("value"), "status": d.get("status") or "undecided"}
        if key == "ratio":  # 채널에 맞는 보기 (첫 번째가 추천) — 챗봇이 chips로 씀
            item["recommended_options"] = [{"label": a, "value": b, "reason": c} for a, b, c in O.ratio_options(sheet)]
        out.append(item)
    return out


def references_context(refs):
    return [{"id": r.id, "summary": r.summary or "(요약 없음 — AI가 아직 보지 못함)", "note": r.note or None} for r in refs] or None


def build_context(c, sheet, decisions, settings, facts, labels, versions, base, message, sig, kickoff, refs=()):
    recent = (ContentMessage.query.filter_by(campaign_id=c.id).order_by(ContentMessage.id.desc()).limit(12).all())
    total = ContentMessage.query.filter_by(campaign_id=c.id).count()
    vlist = sorted(versions, key=lambda v: v.id, reverse=True)[:12]
    upcoming = [{"name": s["name"], "start": s["start"], "dday": s["dday"]} for s in catalog.list_seasons()[:3]]
    return {
        "today": catalog._today().isoformat(),
        "campaign": {"id": c.id, "title": c.title, "revision": c.revision, "status": c.status},
        "sheet": {"content_type": sheet["content_type"], "content_type_label": labels["contentType"],
                  "channels": sheet["channels"], "channel_labels": labels["channels"],
                  "brand_focus": sheet["brand_focus"], "product_ids": sheet["product_ids"], "target_label": labels["target"],
                  "goal": sheet["goal"] or None, "extra_request": sheet["extra_request"] or None},
        "decisions": {k: {"value": v.get("value"), "status": v.get("status"), "label": O.DECISION_LABELS.get(k, k)}
                      for k, v in decisions.items()},
        "brand_facts": brandkit.brand_view(facts),
        "product_facts": facts.get("products") or [],
        "facts_version": facts.get("facts_version"),
        "character_use": assets.character_choice(decisions),
        "versions": [{"id": v.id, "label": v.label, "seq": v.seq, "kind": v.kind, "parent_id": v.parent_id, "source": v.source,
                      "excerpt": (v.body or "")[:240], "is_final": v.id == c.final_version_id,
                      "is_selected": v.id == (base.id if base else None)} for v in vlist],
        "selected_version": ({"id": base.id, "label": base.label, "kind": base.kind, "body": base.body[:5000],
                              "image": bool(base.image_file)} if base else None),
        "user_materials": {"reference_text": c.reference_text[:4000]} if c.reference_text else None,
        "user_references": references_context(refs),
        "flow": flow_context(sheet, decisions),
        "channel_profile": prompts.CHANNEL_PROFILES.get((sheet["channels"] or [""])[0]),
        "recent_messages": [_msg_line(m) for m in reversed(recent)],
        "older_messages_omitted": max(0, total - len(recent)),
        "upcoming_seasons": upcoming,
        "settings": {"answer_length": settings["answer_length"], "candidates": settings["candidates"]},
        "server_signals": {
            "explicit_request": sig["explicit_request"], "vague_ack": sig["vague_ack"], "question": sig["question"],
            "excluded_media": sig["excluded_media"], "unit_targets": sig["unit_targets"],
            "image_generation": "가능 (명확한 요청 시)", "image_edit_supported": image_edit_supported(settings),
            "video_generation": ("가능 (명확한 요청 시 · Higgsfield Seedance 2.0 720p · 유료)" if video_ready(settings)
                                 else "꺼짐 — 숏폼은 구성안(실제 영상 아님)만"),
            "active_image_job": media.has_active_job(c.id),
            "pending_offer": sig.get("pending_offer"),
            "agreed_to_offer": bool(sig.get("pending_offer")) and sig.get("agreement", False),
            "reference_images_attached": sig.get("reference_images", 0),
        },
        "user_message": None if kickoff else message,
        "task": prompts.KICKOFF_TASK if kickoff else prompts.TURN_TASK,
    }


# ───────── 4 검증 ─────────

def _candidate_field(field):
    field = str(field or "").strip()
    if field in ("sheet.goal", "sheet.extra_request"):
        return field
    if field.startswith("decisions."):
        try:
            return "decisions." + repository.decision_key(field[10:])
        except (ValueError, repository.FactChange):
            return None
    return None


def sanitize_turn(raw):
    raw = raw if isinstance(raw, dict) else {}
    intent = raw.get("intent") if raw.get("intent") in O.INTENTS else "other"
    text = clip(raw.get("assistant_text"), 2000).strip()
    patches = [p for p in raw.get("decision_patch") or [] if isinstance(p, dict)][:12]
    cands = []
    for cd in raw.get("candidates") or []:
        if not isinstance(cd, dict):
            continue
        field = _candidate_field(cd.get("field"))
        value = clip(cd.get("value"), 300).strip()
        if not field or not value:
            continue
        cands.append({"field": field, "label": clip(cd.get("label"), 80).strip() or value[:80], "value": value,
                      "reason": clip(cd.get("reason"), 200).strip()})
        if len(cands) >= 3:
            break
    questions = [clip(q, 200).strip() for q in raw.get("clarification_questions") or [] if isinstance(q, str) and q.strip()]
    chips = []
    for ch in raw.get("chips") or []:
        if isinstance(ch, dict) and clip(ch.get("value"), 200).strip():
            value = clip(ch.get("value"), 200).strip()
            label = clip(ch.get("label"), 30).strip() or value[:30]
            if all(x["value"] != value for x in chips):
                chips.append({"label": label, "value": value, "reason": clip(ch.get("reason"), 160).strip()})
        if len(chips) >= CHIPS_MAX:
            break
    of = raw.get("offer") if isinstance(raw.get("offer"), dict) else None
    offer = ({"type": of["type"], "summary": clip(of.get("summary"), 300).strip()}
             if of and of.get("type") in OFFER_TYPES else None)
    ar = raw.get("action_request") if isinstance(raw.get("action_request"), dict) else {}
    t = ar.get("type") if ar.get("type") in O.ACTION_TYPES else "none"
    tv = ar.get("target_version_id")
    tv = tv if isinstance(tv, int) and not isinstance(tv, bool) else None
    return {"intent": intent, "assistant_text": text, "decision_patch": patches, "candidates": cands,
            "clarification_questions": questions, "chips": chips, "offer": offer,
            "action_request": {"type": t, "target_version_id": tv, "instructions": clip(ar.get("instructions"), 1000).strip()}}


def last_offer(cid):
    """직전 AI 답의 만들기 제안 (그 뒤 사용자 말이 이번 메시지뿐일 때만 유효)."""
    m = (ContentMessage.query.filter(ContentMessage.campaign_id == cid, ContentMessage.role != "event")
         .order_by(ContentMessage.id.desc()).first())
    return (m.payload or {}).get("offer") if m is not None and m.role == "assistant" else None


def references_for_turn(refs, message, kickoff):
    """이번 턴에 이미지를 직접 보여 줄 레퍼런스: 마지막 AI 답 뒤에 새로 올린 것 · 사용자가 레퍼런스를 말하면 전부."""
    if kickoff or not refs:
        return []
    if heuristics.mentions_reference(message):
        return list(refs)
    last = (ContentMessage.query.filter_by(campaign_id=refs[0].campaign_id, role="assistant")
            .order_by(ContentMessage.id.desc()).first())
    return [r for r in refs if last is None or r.created_at > last.created_at]


def filter_questions(questions, sheet):
    """이미 시트에 있는 값을 다시 묻는 질문은 버림 (A06) · 최대 2개."""
    out = []
    for q in questions:
        if sheet["goal"] and FILLED_Q["goal"].search(q):
            continue
        if sheet["channels"] and FILLED_Q["channels"].search(q):
            continue
        if sheet["content_type"] and FILLED_Q["content_type"].search(q):
            continue
        if (sheet["brand_focus"] or sheet["product_ids"]) and FILLED_Q["target"].search(q):
            continue
        out.append(q)
    return out[:2]


def strip_unsupported_claims(text, *, patched, made):
    """서버가 실제로 반영·생성하지 않았는데 '반영했어요/만들었어요'라고 쓴 문장은 뺌 (README 6장)."""
    lines = []
    for line in (text or "").split("\n"):
        parts = re.split(r"(?<=[.!?。])\s+", line)
        kept = [p for p in parts if not ((not patched and DONE_PATCH_RE.search(p)) or (not made and DONE_MAKE_RE.search(p)))]
        if kept or not line.strip():
            lines.append(" ".join(kept))
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out


def video_ready(settings):
    return (settings or {}).get("video_mode") == "higgsfield" and higgsfield.ready()


def image_edit_supported(settings):
    """Higgsfield는 기준 이미지를 참고 이미지로 넣어 다시 생성 (픽셀 편집은 아님)."""
    if (settings or {}).get("image_provider") == "higgsfield":
        return True
    return providers.image_supports_edit()


def decide_action(turn, sig, sheet, base, versions, force, settings=None):
    """모델의 실행 요청안 + 사용자 발화 신호 → 실제로 할 일. 모델 출력만으로는 실행하지 않음 (설명서 5.3)."""
    ar = turn["action_request"]
    t = ar["type"]
    vmap = {v.id: v for v in versions}
    model_target = vmap.get(ar["target_version_id"]) if ar["target_version_id"] else None
    # 사용자가 'B안'·'v5'처럼 직접 가리킨 버전이 모델의 추측보다 우선 (모델 대상이 그중 하나면 그대로)
    referred = [v for v in versions if any((r.get("label") and v.label == r["label"]) or (r.get("seq") and v.seq == r["seq"])
                                           for r in sig["version_refs"])]
    if model_target is not None and (not referred or model_target in referred):
        target = model_target
    else:
        target = repository.resolve_version_ref(sig["version_refs"], versions) or model_target
    default_kind = O.CONTENT_TYPES[sheet["content_type"]]["kind"]
    if force == "draft" and not t.startswith("draft_"):
        t = "draft_" + default_kind
    if force == "image" and t not in ("generate_image", "edit_image"):
        t = "edit_image" if (base is not None and base.kind == "image") else "generate_image"
    if force == "video":  # 영상 만들기 버튼 — 대화 AI 판단과 상관없이 실제 영상
        t = "generate_video"
    out = {"type": t, "run": None, "status": "none", "note": "", "target": None, "kind": None, "mode": None}
    if t == "none":
        return out
    if t == "select_version":
        if target is not None:
            out.update(run="select", target=target, status="done")
        else:
            out.update(status="skipped", note="어느 버전인지 찾지 못했어요. 오른쪽 결과 목록에서 골라 주세요.")
        return out
    if not (sig["explicit_request"] or force):
        out.update(status="skipped", reason="not_explicit",
                   note="생성·수정은 '만들어줘', 'B안 더 짧게'처럼 요청하시면 바로 시작할게요.")
        return out
    if t == "generate_video":
        if "video" in sig["excluded_media"]:
            out.update(status="skipped", note="요청대로 영상 작업은 하지 않았어요.")
            return out
        if force == "video" and not higgsfield.ready():
            out.update(status="skipped", note="Higgsfield 키가 없거나 확인되지 않아 영상을 만들지 않았어요. "
                                              "설정 › 연결에서 HIGGSFIELD_API_KEY(KEY_ID:KEY_SECRET)를 확인해 주세요.")
            return out
        if force == "video" or video_ready(settings):
            # 기준: 가리킨·기준 영상(수정 = 새로 생성) → 구성안 → 텍스트 초안 → 가장 최근 구성안 (없으면 시트·결정만으로)
            picked = [v for v in (target, base) if v is not None]
            script = next((v for v in picked if v.kind == "video"), None)
            script = script or next((v for v in picked if v.kind == "script"), None)
            script = script or next((v for v in picked if v.kind not in ("image", "video")), None)
            script = script or next((v for v in reversed(versions) if v.kind == "script"), None)
            out.update(run="video", type=t, target=script, status="queued")
            return out
        t = "draft_script"
        out.update(type=t, note=("Higgsfield 키가 없어 실제 영상 대신 숏폼 구성안(실제 영상 아님)으로 만들어요."
                                 if (settings or {}).get("video_mode") == "higgsfield"
                                 else "실제 영상은 AI 설정에서 '실제 영상'을 켜야 만들어요. 지금은 숏폼 구성안(실제 영상 아님)으로 만들어요."))
    if t in ("generate_image", "edit_image"):
        if "image" in sig["excluded_media"]:
            out.update(status="skipped", note="요청대로 이미지는 만들지 않았어요.")
            return out
        if (settings or {}).get("image_provider") == "higgsfield" and not higgsfield.ready():
            out.update(status="skipped", note="이미지 AI가 Higgsfield인데 키가 없어 만들지 않았어요. "
                                              "AI 설정에서 OpenAI로 바꾸거나 HIGGSFIELD_API_KEY를 넣어 주세요.")
            return out
        img = target if (target is not None and target.kind == "image") else (base if base is not None and base.kind == "image" else None)
        if t == "edit_image" and img is None:
            t, out["note"] = "generate_image", "기준 이미지가 없어 새 시안으로 만들어요."
        elif t == "edit_image" and not image_edit_supported(settings):
            t, out["note"] = "generate_image", "선택한 이미지 모델은 기준 이미지 편집을 지원하지 않아 새 시안으로 만들어요 (원본 수정 아님)."
        # 새 시안은 카피 초안을 먼저 만들었으면 그 문구로: 가리킨·기준 텍스트 버전 → 가장 최근 텍스트 버전
        copy = None
        if t == "generate_image":
            texts = [v for v in (target, base) if v is not None and v.kind not in ("image", "video")]
            copy = texts[0] if texts else next((v for v in reversed(versions) if v.kind not in ("image", "video")), None)
        out.update(run="image", type=t, target=img if t == "edit_image" else None, copy=copy, status="queued")
        return out
    if t in O.ACTION_KINDS:
        kind = O.ACTION_KINDS[t]
        edit_target = None
        if target is not None and target.kind == kind:
            edit_target = target
        elif turn["intent"] == "edit" and base is not None and base.kind == kind:
            edit_target = base
        out.update(run="draft", type=t, kind=kind, mode="edit" if edit_target is not None else "new",
                   target=edit_target, status="pending")
    return out


# ───────── 1~6 한 턴 ─────────

def _request_id(raw):
    raw = str(raw or "").strip()
    return raw if REQ_ID_RE.match(raw) else None


def parse_revision(data):
    raw = data.get("expected_revision", data.get("expectedRevision"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ApiError("expected_revision(현재 리비전)이 필요합니다. 새로고침 후 다시 시도하세요.")


def run_turn(c, data, user):
    if not providers.ready():
        raise ApiError(NO_KEY, 503)
    kickoff = bool(data.get("kickoff"))
    message = clip(data.get("message"), 4000).strip()
    if not kickoff and not message:
        raise ApiError("메시지를 입력하세요.")
    expected = parse_revision(data)
    if expected != c.revision:
        raise repository.Conflict(c.id)
    req_id = _request_id(data.get("client_request_id") or data.get("clientRequestId"))
    if req_id and ContentMessage.query.filter_by(campaign_id=c.id, client_request_id=req_id).first():
        raise repository.Duplicate(c.id)
    force = data.get("force_action") if data.get("force_action") in ("draft", "image", "video") else None
    try:
        base_id = int(data.get("base_version_id")) if data.get("base_version_id") not in (None, "") else None
    except (TypeError, ValueError):
        base_id = None

    # 1 읽기
    started = time.monotonic()
    cid, rev0 = c.id, c.revision
    products = repository.products_index()
    sheet, decisions, settings = repository.sheet_of(c), c.decisions, repository.settings_of(c)
    versions = ContentVersion.query.filter_by(campaign_id=cid).order_by(ContentVersion.id).all()
    base = next((v for v in versions if v.id == base_id), None)
    labels = repository.labels_of(sheet, products)
    facts = brandkit.product_facts(sheet["product_ids"])
    sig = heuristics.signals(message, has_base=base is not None, force=force, kickoff=kickoff)
    # 직전 AI가 "이대로 만들까요?"(offer)라고 했고 사용자가 '좋아요'처럼 동의함 → 그 제안대로 실행
    offer = None if kickoff else last_offer(cid)
    agreed = bool(offer) and not force and heuristics.is_agreement(message)
    if agreed:
        sig.update(explicit_request=True, vague_ack=False)
    sig.update(pending_offer=offer, agreement=agreed)
    refs = repository.references_of(cid)
    shown = references_for_turn(refs, message, kickoff)
    images = [x for x in (media.read_reference(r.file) for r in shown) if x]
    sig["reference_images"] = len(images)

    # 2·3 맥락 → 판단
    ctx = build_context(c, sheet, decisions, settings, facts, labels, versions, base, message, sig, kickoff, refs)
    action_name = "콘텐츠 대화 (첫 답변)" if kickoff else "콘텐츠 대화"
    if images:
        raw = providers.call_json_images(prompts.turn_system(settings), prompts.dumps(ctx), images, schema=schemas.TURN,
                                         name="content_turn", action=action_name + " · 레퍼런스 이미지", max_tokens=2500,
                                         timeout=60)
    else:
        raw = providers.call_json(prompts.turn_system(settings), ctx, schema=schemas.TURN, name="content_turn",
                                  action=action_name, max_tokens=2500, timeout=60)
    turn = sanitize_turn(raw)
    if agreed and turn["action_request"]["type"] == "none":  # 모델이 실행안을 빠뜨려도 동의한 제안은 실행
        turn["action_request"].update(type=offer["type"], instructions=turn["action_request"]["instructions"] or offer["summary"])

    # 4 검증 — 결정 변경안
    plan = repository.plan_changes(sheet, decisions, turn["decision_patch"], user=user, source="chat", revision=rev0,
                                   vague=sig["vague_ack"], allow_sheet=not kickoff, protect_confirmed=True,
                                   products=products)
    new_sheet, new_dec = plan["sheet"], plan["decisions"]
    candidates = list(turn["candidates"])
    for d in plan["demoted"]:
        if len(candidates) < 3 and d.get("value"):
            candidates.append({"field": d["field"], "label": d["value"][:80], "value": d["value"],
                               "reason": "AI 제안 — 고르면 시트에 반영돼요"})
    questions = filter_questions(turn["clarification_questions"], new_sheet)
    if kickoff:  # 첫 답변: 실행 없음 · 제안 없음 · 시트 제안은 후보 카드로만 (거절 줄은 숨김)
        turn["action_request"]["type"] = "none"
        turn["offer"] = None
        plan["rejected"] = [r for r in plan["rejected"] if not r["field"].startswith("sheet.")]

    # 5 실행
    act = decide_action(turn, sig, new_sheet, base, versions, force, settings)
    new_labels = repository.labels_of(new_sheet, products)
    new_facts = facts if new_sheet["product_ids"] == sheet["product_ids"] else brandkit.product_facts(new_sheet["product_ids"])
    drafts_out, draft_meta, job_spec = [], None, None
    if act["run"] == "draft":
        try:
            drafts_out, draft_meta = drafts.generate(
                act["kind"], mode=act["mode"], sheet=new_sheet, decisions=new_dec, settings=settings, facts=new_facts,
                labels=new_labels, base=act["target"], request=message, instructions=turn["action_request"]["instructions"],
                targets=sig["unit_targets"], reference=c.reference_text or "", deadline=started + TURN_BUDGET)
            act["status"] = "done"
        except AIError as e:
            act.update(status="failed", note=f"초안을 만들지 못했어요: {e}")
    elif act["run"] in ("image", "video"):
        if media.has_active_job(cid):
            act.update(run=None, status="skipped", note="이미지·영상 작업이 이미 진행 중이에요. 끝난 뒤 다시 요청해 주세요.")
        else:
            job_spec = dict(act, image_text=media.text_mode_of(data.get("image_text"), message))

    made = bool(drafts_out) or job_spec is not None
    if act["status"] not in ("none", "skipped"):  # 이번 턴에 실행했으면 새 제안은 띄우지 않음
        turn["offer"] = None
    text = strip_unsupported_claims(turn["assistant_text"], patched=bool(plan["applied"]), made=made)
    if not text:
        text = "요청을 확인했어요." if not kickoff else "기본 정보 시트를 확인했어요."

    # 6 저장 (짧은 트랜잭션) — 같은 요청 ID가 동시에 두 번 오면 UNIQUE 제약으로 하나만 저장
    try:
        bot, user_msg, new_versions, job = _persist(
            cid, rev0, user=user, message=message, kickoff=kickoff, req_id=req_id, base=base, force=force, plan=plan,
            settings=settings, facts=new_facts, labels=new_labels, act=act, drafts_out=drafts_out, draft_meta=draft_meta,
            job_spec=job_spec, turn=turn, text=text, candidates=candidates, questions=questions, sig=sig)
    except IntegrityError:
        db.session.rollback()
        raise repository.Duplicate(cid)
    job_out = repository.job_dict(job)  # 등록 시점 상태 (작업은 이제부터 뒤에서)
    if job is not None:
        media.launch(current_app._get_current_object(), job_out["id"])
    c = db.session.get(ContentCampaign, cid)
    return {"campaign": repository.campaign_state(c), "reply": repository.message_dict(bot),
            "userMessage": repository.message_dict(user_msg), "newVersionIds": [v.id for v in new_versions],
            "job": job_out}


def _persist(cid, rev0, *, user, message, kickoff, req_id, base, force, plan, settings, facts, labels, act, drafts_out,
             draft_meta, job_spec, turn, text, candidates, questions, sig):
    c = db.session.get(ContentCampaign, cid)
    if c is None:
        raise ApiError("캠페인이 삭제되었습니다.", 404)
    if plan["applied"] and c.revision != rev0:
        raise repository.Conflict(cid)
    new_sheet, new_dec = plan["sheet"], plan["decisions"]
    user_msg = None
    if not kickoff:
        user_msg = ContentMessage(campaign_id=cid, role="user", text=message, revision=rev0, client_request_id=req_id,
                                  user_name=user)
        user_msg.payload = {"baseVersionId": base.id if base else None, "forceAction": force}
        db.session.add(user_msg)
        db.session.flush()
    msg_id = user_msg.id if user_msg else None
    if plan["applied"]:
        repository.commit_changes(c, plan, source="chat", user=user, message_id=msg_id)

    new_versions = []
    if drafts_out:
        snap, shash = repository.make_snapshot(new_sheet, new_dec, settings, facts.get("facts_version"),
                                               model=draft_meta["model"], mode=act["mode"], request=message[:500],
                                               language=draft_meta["language"])
        labels_for = ([act["target"].label] if act["mode"] == "edit"
                      else repository.next_letter_labels(cid, len(drafts_out)))
        seq = repository.next_seq(cid)
        for i, d in enumerate(drafts_out):
            note_bits = []
            if act["kind"] == "script":
                note_bits.append("구성안 · 실제 영상 아님")
            if draft_meta.get("merge") and draft_meta["merge"].get("note"):
                note_bits.append(draft_meta["merge"]["note"])
            if d.get("rewritten"):
                note_bits.append("키워드 확인 후 자동 재작성")
            v = ContentVersion(campaign_id=cid, seq=seq + i, kind=act["kind"], label=labels_for[min(i, len(labels_for) - 1)],
                               title=d["title"], body=d["body"], parent_id=act["target"].id if act["mode"] == "edit" else None,
                               source="ai", approach=d["approach"], note=" · ".join(note_bits)[:500],
                               facts_version=facts.get("facts_version") or "", snapshot_hash=shash, created_by=user,
                               message_id=msg_id)
            v.snapshot = snap
            v.claims, v.needs_check = d["claims"], d["needs_check"]
            v.review = {"keyword": d["review"], "rewrites": 1 if d.get("rewritten") else 0}
            db.session.add(v)
            new_versions.append(v)
        db.session.flush()
        c.selected_version_id = new_versions[0].id
    if act["run"] == "select" and act["target"] is not None:
        c.selected_version_id = act["target"].id

    job = None
    if job_spec is not None:
        jsnap, jhash = repository.make_snapshot(new_sheet, new_dec, settings, facts.get("facts_version"))
        tgt, cp = job_spec["target"], job_spec.get("copy")
        kind = "video" if job_spec["run"] == "video" else "image_edit" if job_spec["type"] == "edit_image" else "image"
        job = media.new_job(
            c, kind=kind, base=tgt, provider=settings.get("image_provider") or "openai",
            instructions=turn["action_request"]["instructions"] or message,
            snapshot={"sheet": new_sheet, "labels": labels, "decisions": new_dec, "facts": facts,
                      "request": message[:1000], "snapshot": jsnap, "snapshot_hash": jhash,
                      "base_image": {"label": tgt.label, "seq": tgt.seq} if tgt is not None else None,
                      "copy_draft": ({"label": cp.label, "seq": cp.seq, "title": cp.title, "body": cp.body[:1500]}
                                     if cp is not None else None),
                      "image_text": job_spec["image_text"], "selected_at_request": c.selected_version_id},
            request_key=f"{cid}:{req_id}" if req_id else None, user=user, message_id=msg_id)
        db.session.add(job)

    action_payload = {"type": act["type"], "status": act["status"], "note": act["note"],
                      "versionIds": [v.id for v in new_versions], "versionLabels": [f"{v.label} (v{v.seq})" for v in new_versions],
                      "jobId": job.id if job is not None else None, "mode": act.get("mode"), "kind": act.get("kind")}
    if act["run"] == "select" and act["target"] is not None:
        action_payload["versionIds"] = [act["target"].id]
        action_payload["versionLabels"] = [f"{act['target'].label} (v{act['target'].seq})"]
    bot = ContentMessage(campaign_id=cid, role="assistant", text=text, revision=rev0 + (1 if plan["applied"] else 0),
                         user_name="REBOOT AI")
    bot.payload = {"intent": turn["intent"], "candidates": candidates, "questions": questions,
                   "chips": turn["chips"], "offer": turn["offer"], "referenceImages": sig.get("reference_images", 0),
                   "agreedOffer": sig.get("pending_offer") if sig.get("agreement") else None,
                   "applied": plan["applied"], "rejected": plan["rejected"], "factRequests": plan["fact_requests"],
                   "action": action_payload, "model": providers.text_model(), "kickoff": kickoff,
                   "signals": {"explicit": sig["explicit_request"], "vagueAck": sig["vague_ack"],
                               "agreement": sig.get("agreement", False)},
                   "promptVersion": prompts.PROMPT_BUNDLE_VERSION}
    db.session.add(bot)
    c.updated_at, c.updated_by = utcnow(), user
    if plan["applied"]:
        repository.guarded_bump(cid, rev0)
    db.session.commit()
    return bot, user_msg, new_versions, job


# ───────── 인터뷰 (한 번에 질문 하나 · 보기 ①②③ + 직접 입력 · 개수는 AI가 판단) ─────────
#
# 상태는 따로 표를 두지 않고 대화 메시지에 둡니다 — 질문 메시지 payload.interview, 끝 메시지 payload.interviewDone.
# 답은 기존 결정 사항(확정)으로 저장하므로 오른쪽 표·시트·다음 대화가 같은 값을 봅니다.
# 질문 수는 AI가 정하고(윤곽이 잡히면 ready → 화면이 만들기를 제안), 끝없이 이어지지 않게 안전 상한만 둡니다.
# AI가 없거나 실패하면 아래 예시 질문으로 이어 갑니다 (화면에 '예시 질문' 표시 · 답 READY_AFTER개부터 ready).

INTERVIEW_MAX = 10   # 안전 상한 (화면에는 표시하지 않음)
READY_AFTER = 3
CIRCLED = "①②③"


def _q(field, label, question, *options):
    return {"field": field, "label": label, "question": question,
            "options": [{"label": a, "value": b, "reason": c} for a, b, c in options]}


EXAMPLE_QUESTIONS = {
    "goal": _q("sheet.goal", "목적", "이번 콘텐츠로 가장 얻고 싶은 건 무엇인가요?",
               ("브랜드 알리기", "브랜드 알리기", "Reboot를 처음 보는 사람에게 첫인상을 남겨요"),
               ("제품 소개", "제품 소개", "어떤 제품인지 한눈에 알게 해요"),
               ("구매 유도", "구매 유도", "바로 사러 가게 만드는 데 집중해요")),
    "target": _q("decisions.target", "타깃", "누구에게 가장 보여주고 싶나요? (가설)",
                 ("바쁜 아침 직장인", "아침을 바쁘게 시작하는 20~30대 직장인", "슬로건 Restart Your Day와 장면이 잘 맞아요"),
                 ("간식 찾는 대학생", "새로운 간식을 찾는 대학생", "가볍고 재미있는 톤이 잘 통해요"),
                 ("K-푸드 관심층", "K-푸드에 관심 있는 미국 소비자", "브랜드 첫인상을 넓게 알리기 좋아요")),
    "concept": _q("decisions.concept", "콘셉트", "어떤 콘셉트로 갈까요?",
                  ("네온 모닝", "검정 배경에 네온 라임으로 하루를 켜는 느낌", "브랜드 색(검정 & 네온)을 가장 잘 보여줘요"),
                  ("다시 시작 의식", "하루를 다시 시작하는 작은 의식", "슬로건 Restart Your Day를 이야기로 풀어요"),
                  ("팝 & 플레이", "밝고 경쾌한 팝 그래픽", "SNS 피드에서 눈에 잘 띄어요")),
    "tone": _q("decisions.tone", "말투·톤", "말투와 톤은 어떻게 할까요?",
               ("경쾌하고 위트 있게", "경쾌하고 위트 있게", "짧은 SNS 콘텐츠에 잘 맞아요"),
               ("깔끔하고 미니멀하게", "깔끔하고 미니멀하게", "브랜드 색이 돋보여요"),
               ("친근하고 따뜻하게", "친근하고 따뜻하게", "처음 보는 사람도 편하게 받아들여요")),
    "background": _q("decisions.background", "배경", "배경은 어떤 느낌이 좋을까요?",
                     ("검정 + 네온", "검정 배경에 네온 라임 포인트", "브랜드 색 그대로예요"),
                     ("아침 햇살", "밝은 아침 햇살이 드는 공간", "하루의 시작 느낌을 줘요"),
                     ("단색 스튜디오", "단색 컬러 스튜디오 배경", "제품과 문구가 또렷하게 보여요")),
    # 보기는 채널에 맞게 바꿔 씀 (_example_question → options.ratio_options · 첫 번째가 추천)
    "ratio": _q("decisions.ratio", "비율·크기", "비율과 크기는 어떻게 할까요? 채널에 맞는 추천을 첫 번째에 뒀어요.",
                ("1:1 정사각", "1:1 정사각", "피드 기본 비율이에요"),
                ("4:5 세로", "4:5 세로", "피드에서 화면을 더 많이 차지해요"),
                ("9:16 세로", "9:16 세로 (스토리·릴스)", "스토리·릴스에 맞아요")),
    "slide_count": _q("decisions.slide_count", "장 수", "카드뉴스는 몇 장으로 할까요?",
                      ("3장", "3장", "짧고 빠르게 읽혀요"),
                      ("5장", "5장", "훅 → 내용 → CTA 흐름이 자연스러워요"),
                      ("7장", "7장", "이야기를 자세히 풀 수 있어요")),
    "duration": _q("decisions.duration", "영상 길이", "숏폼 길이는 어느 정도가 좋을까요?",
                   ("15초", "15초", "끝까지 보기 쉬워요"),
                   ("30초", "30초", "장면을 조금 더 보여줄 수 있어요"),
                   ("60초", "60초", "이야기를 담을 수 있어요")),
    "message": _q("decisions.message", "핵심 메시지", "가장 먼저 전할 메시지는 무엇인가요?",
                  ("슬로건 중심", "슬로건(Restart Your Day 또는 Buy Your Tomorrow) 중심", "확인된 브랜드 문구라 안전해요"),
                  ("브랜드 무드 중심", "검정 & 네온 브랜드 무드 중심", "제품 사실 없이도 쓸 수 있어요"),
                  ("제품 형태 중심", "제품 형태(모양 젤리·소형 병 음료) 중심", "제공된 제품 정보만 써요")),
    "cta": _q("decisions.cta", "CTA", "마지막 행동 유도 문구는 어떻게 할까요?",
              ("Shop now", "Shop now", "바로 구매로 이어져요"),
              ("Learn more", "Learn more", "부담 없이 더 알아보게 해요"),
              ("Try it today", "Try it today", "처음 써 보는 사람에게 맞아요")),
    "format": _q("decisions.format", "형식", "CRM은 어떤 형식으로 쓸까요? (발송은 하지 않아요)",
                 ("이메일 1통", "이메일 1통", "가장 간단해요"),
                 ("SMS 1통", "SMS 1통", "짧고 빠르게 전달해요"),
                 ("이메일 3통 시퀀스", "이메일 3통 시퀀스", "단계적으로 관심을 이어 가요")),
    "language": _q("decisions.language", "결과물 언어", "소비자에게 보일 문구는 어떤 언어로 쓸까요?",
                   ("미국 영어", "미국 영어 (US English)", "미국 소비자 대상이라 기본으로 제안해요"),
                   ("영어 + 한국어", "영어 + 한국어 병기", "한인 고객도 함께 겨냥해요"),
                   ("한국어", "한국어", "한국어 채널용이에요")),
}
EXAMPLE_ORDER = {
    "image": ["goal", "concept", "background", "ratio", "tone", "language"],
    "cardnews": ["goal", "concept", "tone", "slide_count", "ratio", "target", "language"],
    "shortform": ["goal", "concept", "duration", "ratio", "tone", "target"],
    "crm": ["goal", "format", "tone", "cta", "target"],
}
FIELD_RE = re.compile(r"^decisions\.[a-z][a-z0-9_]{1,39}$")


def interview_messages(cid):
    """이 캠페인의 인터뷰 질문 메시지들과 끝났는지."""
    msgs = ContentMessage.query.filter_by(campaign_id=cid, role="assistant").order_by(ContentMessage.id).all()
    qs = [m for m in msgs if (m.payload or {}).get("interview")]
    done = any((m.payload or {}).get("interviewDone") for m in msgs)
    return qs, done


def _taken(sheet, decisions, asked_fields):
    """다시 묻지 않을 항목: 이번 인터뷰에서 물은 것 · 시트 목적이 이미 있음 · 이미 확정된 결정.
    채널 기본 비율(proposed)은 확정이 아니라서 묻는다 — 보기 첫 번째가 채널 추천 (2026-10-25)."""
    out = set(asked_fields)
    if sheet["goal"]:
        out.add("sheet.goal")
    out.update("decisions." + k for k, d in decisions.items() if (d or {}).get("status") == "confirmed")
    if sheet["content_type"] not in ("image", "cardnews", "shortform"):
        out.add("decisions.ratio")
    return out


def _clean_question(raw, taken):
    """AI 질문 검증 → dict · 'done' · None(못 씀 → 예시 질문으로)."""
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("done") is True:
        return "done"
    field = str(raw.get("field") or "").strip()
    if field.startswith("decisions."):
        try:
            field = "decisions." + repository.decision_key(field[10:])
        except (ValueError, repository.FactChange):
            return None
    if not (field == "sheet.goal" or FIELD_RE.match(field)) or field in taken:
        return None
    question = clip(raw.get("question"), 200).strip()
    opts = []
    for o in raw.get("options") or []:
        if isinstance(o, dict) and clip(o.get("value"), 300).strip():
            value = clip(o.get("value"), 300).strip()
            opts.append({"label": clip(o.get("label"), 40).strip() or value[:40], "value": value,
                         "reason": clip(o.get("reason"), 160).strip()})
    if not question or len(opts) < 3:
        return None
    return {"field": field, "label": clip(raw.get("label"), 30).strip() or repository.field_label(field),
            "question": question, "options": opts[:3], "ready": raw.get("ready") is True}


def _example_question(sheet, taken):
    for key in EXAMPLE_ORDER.get(sheet["content_type"], EXAMPLE_ORDER["image"]):
        q = EXAMPLE_QUESTIONS[key]
        if q["field"] not in taken:
            if key == "ratio":
                return dict(q, options=[{"label": a, "value": b, "reason": c} for a, b, c in O.ratio_options(sheet)])
            return dict(q, options=[dict(o) for o in q["options"]])
    return None


def next_question(sheet, decisions, facts, labels, asked):
    """다음 질문 하나와 짧은 인사말. 질문이 None이면 끝. asked: [{field, question, status, answer}]"""
    taken = _taken(sheet, decisions, [a["field"] for a in asked])
    intro = ""
    if providers.ready():
        ctx = {"first": not asked, "step": len(asked) + 1, "max": INTERVIEW_MAX,
               "sheet": {"content_type": labels["contentType"], "channels": labels["channels"], "target": labels["target"],
                         "brand_focus": sheet["brand_focus"], "goal": sheet["goal"] or None,
                         "extra_request": sheet["extra_request"] or None},
               "decisions": {k: {"value": v.get("value"), "status": v.get("status")} for k, v in decisions.items()},
               "brand_facts": brandkit.brand_view(facts), "character_use": assets.character_choice(decisions),
               "product_facts": facts.get("products") or [],
               "asked": [{"field": a["field"], "question": a["question"],
                          "answer": a["answer"] if a["status"] == "answered" else "(건너뜀)"} for a in asked],
               "avoid_fields": sorted(taken),
               "flow": flow_context(sheet, decisions),
               "channel_profile": prompts.CHANNEL_PROFILES.get((sheet["channels"] or [""])[0]),
               "language": prompts.language_of(decisions)}
        try:
            raw = providers.call_json(prompts.interview_system(), prompts.dumps(ctx), schema=schemas.INTERVIEW,
                                      name="content_interview", action="콘텐츠 인터뷰 질문", max_tokens=900, timeout=60)
        except AIError:
            raw = None
        if isinstance(raw, dict):
            intro = strip_unsupported_claims(clip(raw.get("intro"), 400).strip(), patched=False, made=False)
            q = _clean_question(raw, taken)
            if q == "done" and asked:
                return None, intro
            if isinstance(q, dict):
                return dict(q, source="ai"), intro
    q = _example_question(sheet, taken)
    ready = sum(1 for a in asked if a["status"] == "answered") >= READY_AFTER
    return (dict(q, source="example", ready=ready) if q else None), intro


def _asked_list(qs):
    return [{"field": m.payload["interview"]["field"], "question": m.payload["interview"]["question"],
             "status": m.payload["interview"]["status"],
             "answer": (m.payload["interview"].get("answer") or {}).get("value")} for m in qs]


def run_interview(c, data, user):
    """action: start(첫 질문) · answer(답 → 결정 확정 → 다음 질문) · skip(건너뛰기) · finish(바로 끝내기)."""
    action = data.get("action")
    if action not in ("start", "answer", "skip", "finish"):
        raise ApiError("인터뷰 동작은 start · answer · skip · finish 중 하나입니다.")
    expected = parse_revision(data)
    if expected != c.revision:
        raise repository.Conflict(c.id)
    req_id = _request_id(data.get("client_request_id") or data.get("clientRequestId"))
    if req_id and ContentMessage.query.filter_by(campaign_id=c.id, client_request_id=req_id).first():
        raise repository.Duplicate(c.id)
    cid, rev0 = c.id, c.revision
    qs, done = interview_messages(cid)
    open_q = qs[-1] if qs and qs[-1].payload["interview"]["status"] == "open" else None  # 열린 질문은 늘 마지막 하나
    if action == "start":
        if qs or done:  # 이미 시작함 — 같은 질문을 또 만들지 않음
            return {"campaign": repository.campaign_state(c), "applied": [], "started": False}
    elif done:
        raise ApiError("인터뷰가 이미 끝났어요. 오른쪽 표에서 답을 고칠 수 있어요.")
    elif open_q is None and action != "finish":
        raise ApiError("지금 답할 질문이 없어요.")

    products = repository.products_index()
    sheet, decisions = repository.sheet_of(c), c.decisions
    plan, answer, user_text = None, None, None
    source = "manual"
    if action == "answer":
        iv = open_q.payload["interview"]
        opt = data.get("option")
        if isinstance(opt, int) and not isinstance(opt, bool) and 0 <= opt < len(iv["options"]):
            o = iv["options"][opt]  # 보기 값은 서버가 저장해 둔 것을 씀 (화면이 보낸 글자 대신)
            answer = {"value": o["value"], "label": o["label"], "option": opt}
            user_text, source = f"{CIRCLED[opt]} {o['label']}", "candidate"
        else:
            value = clip(data.get("value"), 300).strip()
            if not value:
                raise ApiError("답을 고르거나 직접 입력하세요.")
            answer = {"value": value, "label": value[:40], "option": None}
            user_text = value
        plan = repository.plan_changes(sheet, decisions, [{"field": iv["field"], "value": value_of(answer), "status": "confirmed"}],
                                       user=user, source=source, revision=rev0, products=products)
        if plan["rejected"]:
            raise ApiError(plan["rejected"][0]["reason"])
        sheet, decisions = plan["sheet"], plan["decisions"]
    elif action == "skip":
        user_text = "이 질문은 건너뛸게요"

    # 다음 질문 (AI를 부르는 동안 DB에 쓰지 않음)
    asked = _asked_list(qs)
    if open_q is not None:
        asked[-1].update(status="answered" if action == "answer" else "skipped", answer=value_of(answer))
    nxt, intro = None, ""
    if action != "finish" and len(asked) < INTERVIEW_MAX:
        labels = repository.labels_of(sheet, products)
        nxt, intro = next_question(sheet, decisions, brandkit.product_facts(sheet["product_ids"]), labels, asked)

    # 저장 (짧은 트랜잭션)
    c = db.session.get(ContentCampaign, cid)
    if c is None:
        raise ApiError("캠페인이 삭제되었습니다.", 404)
    if c.revision != rev0:
        raise repository.Conflict(cid)
    applied = bool(plan and plan["applied"])
    try:
        msg_id = None
        if user_text:
            um = ContentMessage(campaign_id=cid, role="user", text=user_text, revision=rev0, client_request_id=req_id,
                                user_name=user)
            um.payload = {"interviewAnswer": action}
            db.session.add(um)
            db.session.flush()
            msg_id = um.id
        if applied:
            repository.commit_changes(c, plan, source=source, user=user, message_id=msg_id)
        if open_q is not None:
            p = dict(open_q.payload)
            p["interview"] = dict(p["interview"], status="answered" if action == "answer" else "skipped", answer=answer)
            open_q.payload = p
        bot = ContentMessage(campaign_id=cid, role="assistant", text="", revision=rev0 + (1 if applied else 0),
                             user_name="REBOOT AI")
        if nxt is not None:
            bot.text = nxt["question"]
            bot.payload = {"interview": dict(nxt, step=len(qs) + 1, status="open", answer=None, intro=intro),
                           "model": providers.text_model() if nxt["source"] == "ai" else None,
                           "promptVersion": prompts.PROMPT_BUNDLE_VERSION}
        else:
            n_ans = sum(1 for a in asked if a["status"] == "answered")
            bot.text = ((intro + "\n\n") if intro else "") + (
                f"질문을 마쳤어요 (답 {n_ans}개). 오른쪽 '브리프' 표를 확인하고, 괜찮으면 [이대로 만들기]를 눌러 주세요. "
                "표의 답은 언제든 고칠 수 있고, 아래 입력창으로 자유롭게 더 이야기해도 돼요.")
            bot.payload = {"interviewDone": {"answered": n_ans, "asked": len(asked)}}
        db.session.add(bot)
        c.updated_at, c.updated_by = utcnow(), user
        if applied:
            repository.guarded_bump(cid, rev0)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise repository.Duplicate(cid)
    c = db.session.get(ContentCampaign, cid)
    return {"campaign": repository.campaign_state(c), "applied": plan["applied"] if plan else []}


def value_of(answer):
    return answer["value"] if answer else None
