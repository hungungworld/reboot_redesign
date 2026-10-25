"""이미지 생성·편집 작업 (README 7.1·8장 · 설명서 9장).

- 명확한 요청이 있을 때만 대화 턴이 작업을 등록 → 대화 HTTP 요청을 붙잡지 않고 뒤에서 실행 (jobs.run_background)
- 상태: queued → running → saving → succeeded / failed / submission_unknown (응답 시간 초과 · 자동 재제출 없음)
- 편집: 기준 이미지 파일을 실제로 넣어 고침 (GPT 이미지 계열). 지원하지 않으면 '새 시안'으로 표시하고 원본 수정이라 하지 않음
- 글자(헤드라인·슬로건·CTA): 기본은 이미지 모델이 그림과 어울리게 직접 그림 (text_mode "bake" · README 7.2)
  "글자 없이"를 고르면 글자 없는 이미지 ("none"). 그린 문구는 image_text로 따로 저장해 키워드·컴플라이언스 검사에 씀
  예전 버전의 텍스트 레이어(text_layers)는 보여 주기만 함
- 제품·캐릭터 참고 사진: 선택 제품 · 맛 · 캐릭터 Rebu 사용 여부에 맞는 최종 자료 사진(assets.py)을 함께 넣어 생성
  → 병·봉지·젤리 모양·라벨을 사진과 같게 (참고 사진을 받지 않는 모델이면 브랜드 키트의 외형 설명으로만 생성)
- 파일: DATA_DIR/uploads/content/<이름>.png → GET /api/content/files/<이름>.png (로그인 필요)
- 브라우저 편집기(자르기·밝기·텍스트 레이어)에서 만든 PNG도 같은 곳에 새 버전으로 보관 (AI 호출 없음)
- 제공사: 이미지는 AI 설정의 image_provider (OpenAI · Higgsfield Marketing Studio), 영상은 Higgsfield Seedance 2.0 720p
  Higgsfield는 제출 → 상태 확인 → 결과 받기 (higgsfield.py). 요청 ID는 작업 snapshot에 기록 (취소·대조용)
  키가 없거나 실패해도 다른 제공사로 조용히 바꾸지 않음 (README 8장)
"""
import re
import secrets
import time
from pathlib import Path

import requests
from flask import current_app

from server import jobs, worklog
from server.core.ai import AIError
from server.extensions import db
from server.models import utcnow
from . import assets, brandkit, higgsfield, prompts, providers, repository, review, schemas
from .models import ContentCampaign, ContentJob, ContentVersion

FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{5,80}\.(png|jpg|webp|mp4)$")
MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".mp4": "video/mp4"}
POLL_EVERY = 5  # Higgsfield 상태 확인 간격(초) — 테스트는 0으로
WAIT = {"image": 5 * 60, "video": 15 * 60}  # 이 시간 안에 안 끝나면 '결과 확인 불가' (요청 ID로 Higgsfield에서 확인)
NO_TEXT = ("Do not render any text, letters, numbers, logos, watermarks or labels anywhere in the image; "
           "leave clean negative space for a separate text overlay. Generic unbranded packaging only.")
REF_TEXT = ("Reproduce the products and the mascot exactly as they appear in the reference images: same shape, colors, "
            "proportions and printed labels/logo; do not redesign them. Ignore measurement lines, dimension numbers and captions "
            "in the reference images.")
REF_NO_TEXT = (" Do not add any other text, captions, slogans, numbers or watermarks; leave clean negative space "
               "for a separate text overlay.")
TEXT_MODES = ("bake", "none")
IMAGE_SCOPE = "이미지에 그리라고 넘긴 문구만 검사 · 실제 그려진 글자(철자)는 검사하지 않음"
NO_TEXT_RE = re.compile(r"(글자|문구|텍스트|카피)\s*(없이|빼고|빼줘|없는|넣지\s*마)|no\s+text|without\s+text", re.I)
VIEW_EN = {"trio": "three bottles, front", "front": "front view", "angle": "lying at an angle", "spec": "product views",
           "pouch": "pouch, front", "scene": "pouch with wrapped jellies", "wrapped": "individually wrapped jelly"}
VIDEO_REF_TEXT = ("Keep the products and the mascot exactly as they appear in the reference images: same shape, colors, "
                  "proportions and printed labels/logo. Ignore measurement lines and captions in the reference images. "
                  "No on-screen text, subtitles, captions, logos or watermarks. No dialogue or voice-over.")
VIDEO_NO_TEXT = "No on-screen text, subtitles, captions, logos or watermarks. Generic unbranded packaging only. No dialogue."
SLOGANS = brandkit.slogans()  # Restart Your Day · Buy Your Tomorrow (원문 표기 그대로)
SLOGAN = SLOGANS[0]           # 고른 슬로건이 없을 때 기본


def official_slogan(text):
    """'RESTART YOUR DAY!' · 'buy your tomorrow' → 원문 표기 슬로건 (공식 슬로건이 아니면 None)."""
    key = re.sub(r"[^a-z]", "", str(text or "").lower())
    return next((s for s in SLOGANS if re.sub(r"[^a-z]", "", s.lower()) == key), None) if key else None


def chosen_slogans(decisions):
    """decisions.slogan_choice → 그릴 슬로건 목록 ('둘 다'면 두 개, 없거나 모르면 기본 하나)."""
    v = re.sub(r"[^a-z가-힣]", "", str(((decisions or {}).get("slogan_choice") or {}).get("value") or "").lower())
    if "둘" in v or "both" in v or all(re.sub(r"[^a-z]", "", s.lower()) in v for s in SLOGANS):
        return list(SLOGANS)
    return [s for s in SLOGANS if re.sub(r"[^a-z]", "", s.lower()) in v][:1] or [SLOGAN]


class Canceled(Exception):
    """제공사에서 요청이 취소됨."""


def upload_dir():
    d = Path(current_app.config["DATA_DIR"]) / "uploads" / "content"
    d.mkdir(parents=True, exist_ok=True)
    return d


def image_size(ratio_text):
    """비율 결정값 → 모델이 지원하는 크기 (정확히 같지 않으면 근사값을 기록)."""
    r = str(ratio_text or "").replace(" ", "").lower()
    if any(x in r for x in ("9:16", "4:5", "2:3", "3:4", "세로", "portrait", "스토리", "릴스", "reels", "story")):
        return "1024x1536"
    if any(x in r for x in ("16:9", "3:2", "4:3", "가로", "landscape", "와이드", "wide")):
        return "1536x1024"
    return "1024x1024"


def has_active_job(campaign_id):
    repository.settle_stale_jobs(campaign_id)
    return ContentJob.query.filter(ContentJob.campaign_id == campaign_id,
                                   ContentJob.status.in_(repository.ACTIVE_JOB)).first() is not None


def new_job(campaign, *, kind, base, instructions, snapshot, request_key, user, message_id=None, provider="openai"):
    """kind: image · image_edit · video. provider: openai · higgsfield (영상은 항상 higgsfield)."""
    ratio = (snapshot.get("decisions", {}).get("ratio") or {}).get("value")
    if kind == "video":
        provider, model = "higgsfield", higgsfield.VIDEO_TEXT
        size = f"{higgsfield.nearest_ratio(ratio, higgsfield.VIDEO_RATIOS, '9:16')} · {higgsfield.VIDEO_RESOLUTION}"
    elif provider == "higgsfield":
        model, size = higgsfield.IMAGE_MODEL, higgsfield.nearest_ratio(ratio, higgsfield.IMAGE_RATIOS, "1:1")
    else:
        provider, model, size = "openai", providers.image_model(), image_size(ratio)
    job = ContentJob(id=secrets.token_hex(16), campaign_id=campaign.id, kind=kind, status="queued",
                     request_key=request_key, base_version_id=base.id if base is not None else None,
                     model=model, size=size, instructions=(instructions or "")[:2000],
                     created_by=user, message_id=message_id)
    job.snapshot = dict(snapshot, provider=provider)
    return job


def launch(app, job_id):
    jobs.run_background(app, f"content-image-{job_id}", run_job, job_id)


def run_job(job_id):
    job = db.session.get(ContentJob, job_id)
    if job is not None and job.kind == "video":
        return run_video_job(job_id)
    return run_image_job(job_id)


def _provider(job):
    return (job.snapshot or {}).get("provider") or "openai"


def _what(job):
    return "영상" if job.kind == "video" else "이미지"


def text_mode_of(requested, message):
    """이미지 글자 방식 — 메시지에 '글자 없이'가 있으면 none, 아니면 화면 선택값 (기본 bake)."""
    if NO_TEXT_RE.search(str(message or "")):
        return "none"
    return requested if requested in TEXT_MODES else "bake"


def _text_mode(snap):
    return snap.get("image_text") if snap.get("image_text") in TEXT_MODES else "bake"


def image_text_of(img):
    """버전의 이미지 메타 → 소비자에게 보일 문구 (새 방식은 image_text, 예전 버전은 text_layers)."""
    img = img or {}
    return img.get("image_text") or img.get("text_layers") or []


def _bake_text(lines):
    words = "; ".join(f'{t["role"]}: "{t["text"]}"' for t in lines)
    return ("Render this text inside the image as part of the design, spelled exactly as written, and nothing else: "
            f"{words}. Use typography (typeface, weight, size, color, placement) that fits the image style and stays legible. "
            "Do not add any other words, numbers or watermarks.")


# ───────── 캡션 · 해시태그 추천 (이미지·영상 생성 직후 자동 · 2026-10-25) ─────────
# 이미지는 완성된 이미지를 대화 AI가 직접 보고, 영상은 파일을 볼 수 없어 영상 장면 설명(프롬프트)으로 추천.
# 실패해도 결과물은 그대로 저장하고 '추천 못 함'만 표시 (텍스트 AI 호출 1번 · 이미지·영상 재생성 없음).

CAPTION_COUNT = 3
HASHTAG_MAX = 10


def _social_input(snap, basis, texts, video_plan):
    dec = snap.get("decisions") or {}
    ch = next(iter((snap.get("sheet") or {}).get("channels") or []), "")
    return {"basis": basis, "sheet": snap.get("labels"),
            "decisions": {k: (v or {}).get("value") for k, v in dec.items()},
            "brand_facts": (snap.get("facts") or {}).get("brand"), "product_facts": (snap.get("facts") or {}).get("products"),
            "channel": ch, "channel_profile": prompts.CHANNEL_PROFILES.get(ch), "language": prompts.language_of(dec),
            "image_text": [t["text"] for t in texts or []], "copy_draft": snap.get("copy_draft"),
            "video_plan": video_plan or None}


def clean_social(raw, snap, basis):
    raw = raw if isinstance(raw, dict) else {}
    caps = []
    for c in raw.get("captions") or []:
        text = str((c or {}).get("text") or "").strip()[:1500] if isinstance(c, dict) else ""
        if text:
            caps.append({"angle": str(c.get("angle") or "").strip()[:40], "text": text})
        if len(caps) >= CAPTION_COUNT:
            break
    tags = []
    for t in raw.get("hashtags") or []:
        word = re.sub(r"[^\w]", "", str(t or ""))[:40]  # '#'·공백·기호 제거 (한글·영문·숫자·_만)
        if word and word.lower() not in [x[1:].lower() for x in tags]:
            tags.append("#" + word)
        if len(tags) >= HASHTAG_MAX:
            break
    text = "\n\n".join(c["text"] for c in caps) + ("\n" + " ".join(tags) if tags else "")
    return {"status": "done" if caps else "failed", "basis": basis, "captions": caps, "hashtags": tags,
            "note": str(raw.get("note_ko") or "").strip()[:300] if caps else "AI가 캡션을 주지 않았어요.",
            "model": providers.text_model(),
            "review": review.keyword_check(text, "copy", snap.get("sheet") or {}, snap.get("facts") or {}) if caps else None}


def suggest_social(snap, *, basis, image=None, texts=(), video_plan=""):
    """완성된 이미지(image=(bytes, mime) · 직접 봄) 또는 영상(video_plan 글) → 캡션 3안 + 해시태그. 예외 없이 상태를 돌려줌."""
    empty = {"basis": basis, "captions": [], "hashtags": [], "review": None}
    if not providers.ready():
        return dict(empty, status="skipped", note="OpenAI 키가 없어 캡션·해시태그를 추천하지 못했어요.")
    user = prompts.dumps(_social_input(snap, basis, texts, video_plan))
    action = ("이미지" if basis == "image" else "영상") + " 캡션·해시태그 추천"
    try:
        if image is not None:
            raw = providers.call_json_images(prompts.SOCIAL_V1, user, [image], schema=schemas.SOCIAL, name="content_caption",
                                             action=action, max_tokens=1200, timeout=90)
        else:
            raw = providers.call_json(prompts.SOCIAL_V1, user, schema=schemas.SOCIAL, name="content_caption",
                                      action=action, max_tokens=1200, timeout=90)
    except AIError as e:
        return dict(empty, status="failed", note=f"캡션·해시태그를 추천하지 못했어요: {e}")
    except Exception:  # 결과물 저장을 막지 않음 — 원문은 서버 로그에만
        current_app.logger.exception("콘텐츠 캡션 추천 실패")
        return dict(empty, status="failed", note="캡션·해시태그 추천 중 알 수 없는 오류가 났어요.")
    return clean_social(raw, snap, basis)


# ───────── 실행 (뒤에서) ─────────

def _fallback_plan(snap, job):
    dec = {k: (v or {}).get("value") for k, v in (snap.get("decisions") or {}).items()}
    forms = []
    for p in (snap.get("facts") or {}).get("products") or []:
        pid = p.get("entity_id") or ""
        forms.append(p.get("look") or ("a pouch of individually wrapped shaped jellies" if pid.startswith("jelly")
                                       else "small flask-shaped glass bottles of a non-carbonated drink"))
    bits = ["Marketing key visual for REBOOT, a US food & beverage brand.",
            "Brand palette: deep black with neon lime accents."]
    if dec.get("background"):
        bits.append(f"Background direction (Korean note): {dec['background']}.")
    if forms:
        bits.append("Show " + " and ".join(forms) + ".")
    else:
        bits.append("Brand mood image without products.")
    for k in ("concept", "style", "mood", "layout", "composition", "lighting"):
        if dec.get(k):
            bits.append(f"{k.capitalize()} (Korean note): {dec[k]}.")
    if job.instructions:
        bits.append(f"Art direction (Korean): {job.instructions[:400]}")
    copy = snap.get("copy_draft")
    if copy:
        bits.append(f"Leave clear space for the copy of this draft (Korean): {copy['body'][:300]}")
    layers = [{"role": "slogan", "text": s} for s in chosen_slogans(snap.get("decisions"))]
    if dec.get("headline"):
        layers.insert(0, {"role": "headline", "text": dec["headline"][:120]})
    include = assets.character_choice(snap.get("decisions")) == "사용"
    if include:
        bits.append("Include Rebu, the Reboot mascot shown in the reference images.")
    return {"prompt_en": " ".join(bits), "keep": [], "change": [], "text_layers": layers,
            "notes_ko": "AI 프롬프트 작성에 실패해 결정 사항으로 만든 기본 프롬프트를 썼어요.", "flavors": [],
            "include_character": include, "fallback": True}


def _plan(snap, job, base):
    user = {
        "mode": "edit" if job.kind == "image_edit" else "generate",
        "sheet": snap.get("labels"), "decisions": snap.get("decisions"),
        "brand_facts": (snap.get("facts") or {}).get("brand"), "product_facts": (snap.get("facts") or {}).get("products"),
        "base_version": ({"label": base.label, "notes": base.body[:1500], "image": snap.get("base_image"),
                          "text": image_text_of((base.snapshot or {}).get("image"))} if base else None),
        "copy_draft": snap.get("copy_draft"),
        "request": snap.get("request"), "instructions": job.instructions,
        "size": job.size, "character_use": assets.character_choice(snap.get("decisions")), "text_mode": _text_mode(snap),
        "reference_photos": _provider(job) == "higgsfield" or providers.image_supports_edit(job.model),
    }
    try:
        out = providers.call_json(prompts.CREATIVE_EDIT_V2, prompts.dumps(user), schema=schemas.IMAGE_PLAN,
                                  name="content_image_plan", action="이미지 프롬프트 작성", max_tokens=1500, timeout=90)
    except AIError:
        return _fallback_plan(snap, job)
    layers = []
    for t in out.get("text_layers") or []:
        if not isinstance(t, dict):
            continue
        role = str(t.get("role") or "text")[:20]
        text = str(t.get("text") or "").strip()[:160]
        if role == "slogan" or official_slogan(text):  # 슬로건은 원문 그대로 (모르는 문구면 고른 슬로건)
            role, text = "slogan", official_slogan(text) or chosen_slogans(snap.get("decisions"))[0]
        if text:
            layers.append({"role": role, "text": text})
    return {"prompt_en": str(out.get("prompt_en") or "").strip()[:2500] or _fallback_plan(snap, job)["prompt_en"],
            "keep": [str(x)[:120] for x in out.get("keep") or []][:8],
            "change": [str(x)[:120] for x in out.get("change") or []][:8],
            "text_layers": layers[:4], "notes_ko": str(out.get("notes_ko") or "")[:400],
            "flavors": [f for f in out.get("flavors") or [] if f in assets.FLAVORS][:3],
            "include_character": bool(out.get("include_character")), "fallback": False}


def _include_character(snap, plan):
    """시트·결정의 선택이 우선 (사용 · 사용 안 함). AI 판단일 때만 프롬프트 작성 AI의 판단을 따름."""
    choice = assets.character_choice(snap.get("decisions"))
    return choice == "사용" or (choice == "AI 판단" and bool(plan.get("include_character")))


def _references(snap, job, plan, include_char, has_base):
    """참고 사진 고르기 — 사용자가 말한 맛이 있으면 그 맛, 없으면 AI가 고른 맛."""
    if _provider(job) == "openai" and not providers.image_supports_edit(job.model):
        return []
    dec = snap.get("decisions") or {}
    said = assets.flavors_in(snap.get("request"), job.instructions,
                             *((dec.get(k) or {}).get("value") for k in ("flavor", "featured_product", "concept")))
    pids = (snap.get("sheet") or {}).get("product_ids") or []
    cap = assets.HIGGSFIELD_MAX_REFS if _provider(job) == "higgsfield" else assets.MAX_REFS
    return assets.pick_refs(pids, flavors=said or plan.get("flavors") or [], character=include_char,
                            limit=cap - (1 if has_base else 0))


def _ref_lines(refs, facts, first_index):
    names = {p.get("entity_id"): p.get("consumer_name") or p.get("internal_name") for p in facts.get("products") or []}
    lines = []
    for i, a in enumerate(refs, start=first_index):
        if a["type"] == "character":
            what = "Rebu, the Reboot mascot (character sheet; use the look only, not the layout)"
        else:
            what = " · ".join(x for x in (names.get(a.get("product_id")) or "Reboot product", a.get("flavor"),
                                          VIEW_EN.get(a.get("view"), "")) if x)
        lines.append(f"Reference image {i}: {what}.")
    return " ".join(lines)


def file_ext(data):
    """결과 파일 형식 (Higgsfield 이미지는 PNG가 아닐 수 있음)."""
    if data[:8] == PNG_MAGIC:
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp":
        return ".mp4"
    return None


def _save_png(data, campaign_id, job_id, ext=".png"):
    name = f"c{campaign_id}-{job_id[:12]}-{secrets.token_hex(4)}{ext}"
    path = upload_dir() / name
    last = None
    for wait in (0, 0.5, 1.5):  # 파일 보관만 다시 시도 (새 생성 비용 없음)
        time.sleep(wait)
        try:
            path.write_bytes(data)
            return name
        except OSError as e:
            last = e
    current_app.logger.error("콘텐츠 결과 파일 저장 실패: %s", type(last).__name__)
    return None


def _event(job, text, payload):
    c = db.session.get(ContentCampaign, job.campaign_id)
    if c is not None:
        repository.add_event(c, text, payload, user=job.created_by)
    return c


def _fail(job, message, status="failed"):
    job.status = status
    job.error = message[:400]
    job.finished_at = utcnow()
    what = _what(job)
    head = {"submission_unknown": f"{what} 생성 결과를 확인하지 못했어요: ", "canceled": f"{what} 생성 취소: "}
    _event(job, head.get(status, f"{what} 생성 실패: ") + message, {"kind": "job_failed", "jobId": job.id, "status": status})
    db.session.commit()
    worklog.add("content", "AI 프롬프트", f"{what} 생성", status="실패",
                detail={"model": job.model, "size": job.size, "error": message, "campaign": job.campaign_id},
                user_name=job.created_by)


def run_image_job(job_id):
    job = db.session.get(ContentJob, job_id)
    if job is None or job.status != "queued":
        return  # 이미 실행했거나 취소 — 다시 제출하지 않음
    job.status, job.started_at = "running", utcnow()
    db.session.commit()
    snap = job.snapshot
    base = db.session.get(ContentVersion, job.base_version_id) if job.base_version_id else None
    started = time.time()
    try:
        plan = _plan(snap, job, base)
        has_base = bool(job.kind == "image_edit" and base is not None and base.image_file)
        include_char = _include_character(snap, plan)
        refs = _references(snap, job, plan, include_char, has_base)
        mode = _text_mode(snap)
        texts = (plan["text_layers"] or [{"role": "slogan", "text": s} for s in chosen_slogans(snap.get("decisions"))]
                 if mode == "bake" else [])
        if refs:
            prompt = (plan["prompt_en"] + ("\n\nImage 1 is the base image to edit. " if has_base else "\n\n")
                      + _ref_lines(refs, snap.get("facts") or {}, 2 if has_base else 1) + "\n" + REF_TEXT
                      + ("\n" + _bake_text(texts) if texts else REF_NO_TEXT))
        elif texts:
            prompt = plan["prompt_en"] + "\n\n" + _bake_text(texts) + " Generic unbranded packaging only."
        else:
            prompt = plan["prompt_en"] + "\n\n" + NO_TEXT
        job.prompt = prompt
        db.session.commit()
        ref_paths = [str(assets.path_of(a)) for a in refs]
        if _provider(job) == "higgsfield":
            paths = ([str(upload_dir() / base.image_file)] if has_base else []) + ref_paths
            body = {"prompt": prompt, "aspect_ratio": job.size, "resolution": higgsfield.IMAGE_RESOLUTION}
            png, usage = _hf_run(job, higgsfield.IMAGE_MODEL, body, paths)
        elif has_base:
            base_path = str(upload_dir() / base.image_file)
            png, usage = providers.edit_image([base_path] + ref_paths if ref_paths else base_path, prompt, job.size, job.model)
        elif ref_paths:
            png, usage = providers.edit_image(ref_paths, prompt, job.size, job.model)
        else:
            png, usage = providers.generate_image(prompt, job.size, job.model)
    except Canceled as e:
        return _fail(job, str(e), status="canceled")
    except providers.SubmissionUnknown as e:
        return _fail(job, str(e), status="submission_unknown")
    except providers.ProviderError as e:
        return _fail(job, str(e))
    except Exception:  # 예상 못 한 오류 — 원문은 서버 로그에만
        current_app.logger.exception("콘텐츠 이미지 작업 실패")
        return _fail(job, "이미지 생성 중 알 수 없는 오류가 났습니다.")

    job.status = "saving"
    db.session.commit()
    ext = file_ext(png)
    if ext is None or ext == ".mp4":
        return _fail(job, "이미지 결과 파일 형식을 알 수 없어 저장하지 않았습니다.")
    name = _save_png(png, job.campaign_id, job.id, ext)
    if not name:
        return _fail(job, "이미지는 생성됐지만 파일을 저장하지 못했습니다 (서버 저장 공간 확인).")
    social = suggest_social(snap, basis="image", image=(png, MIMES[ext]), texts=texts)  # AI 호출 중 DB에 쓰지 않음

    c = db.session.get(ContentCampaign, job.campaign_id)
    if c is None:  # 작업 중 캠페인이 지워짐
        (upload_dir() / name).unlink(missing_ok=True)
        job.status, job.finished_at, job.error = "failed", utcnow(), "캠페인이 삭제되어 결과를 버렸습니다."
        db.session.commit()
        return
    edit = job.kind == "image_edit" and base is not None
    hf = _provider(job) == "higgsfield"
    requested_ratio = (snap.get("decisions", {}).get("ratio") or {}).get("value")
    note_bits = ["Higgsfield Marketing Studio로 생성"] if hf else []
    if edit and hf:
        note_bits.append(f"{base.label} · v{base.seq}을 참고 이미지 1번으로 넣어 다시 생성 (원본 픽셀 보존 아님)")
    elif edit:
        note_bits.append(f"{base.label} · v{base.seq}을 기준 이미지로 넣어 편집 (모델 편집 — 영역 밖 픽셀 완전 보존은 보장하지 않음)")
    elif job.base_version_id:
        note_bits.append("새 시안 (원본 편집 아님)")
    copy = snap.get("copy_draft")
    if copy and not edit:
        note_bits.append(f"카피 초안 {copy['label']} · v{copy['seq']} 문구 기준")
    if requested_ratio and (not requested_ratio.replace(" ", "").startswith(job.size) if hf
                            else image_size(requested_ratio) != "1024x1024"):
        note_bits.append(f"요청 비율 {requested_ratio} → {job.size}로 생성 · 게시 전 크롭 필요할 수 있음")
    if refs:
        note_bits.append(f"최종 패키지·캐릭터 사진 {len(refs)}장을 참고해 생성 · 라벨 글자·모양은 게시 전 확인")
    elif (snap.get("sheet") or {}).get("product_ids"):
        note_bits.append("참고 사진 없이 생성 (이 이미지 모델은 참고 사진을 받지 않음) · 제품 모양 확인 필요")
    if include_char:
        note_bits.append("캐릭터 Rebu 포함")
    note_bits.append("문구를 이미지에 직접 그림 · 철자는 게시 전 확인" if texts else "글자 없이 생성")
    layers_text = "\n".join(t["text"] for t in texts)
    image_meta = {"model": job.model, "provider": _provider(job), "size": job.size, "mode": "edit" if edit else "generate",
                  "prompt": job.prompt[:3000], "text_mode": mode, "image_text": texts, "baked": bool(texts),
                  "requested_ratio": requested_ratio, "text_layers": [], "keep": plan["keep"],
                  "change": plan["change"], "prompt_fallback": plan.get("fallback", False), "usage": usage,
                  "references": [a["id"] for a in refs], "include_character": bool(include_char),
                  "seconds": round(time.time() - started, 1), "job_id": job.id}
    v = ContentVersion(
        campaign_id=c.id, seq=repository.next_seq(c.id), kind="image",
        label=base.label if edit else repository.next_image_label(c.id), title=(plan["notes_ko"] or "이미지 시안")[:200],
        body=plan["notes_ko"] or "", image_file=name, parent_id=base.id if edit else None, source="ai",
        approach="; ".join(plan["change"])[:300], note=" · ".join(note_bits)[:500],
        facts_version=(snap.get("facts") or {}).get("facts_version") or "", created_by=job.created_by, message_id=job.message_id)
    v.snapshot = dict(snap.get("snapshot") or {}, image=image_meta, social=social, model=job.model)
    v.snapshot_hash = snap.get("snapshot_hash") or ""
    v.review = {"keyword": review.keyword_check(layers_text, "copy", snap.get("sheet") or {}, snap.get("facts") or {}),
                "imageScope": IMAGE_SCOPE}
    db.session.add(v)
    db.session.flush()
    job.status, job.result_version_id, job.finished_at = "succeeded", v.id, utcnow()
    # 요청한 뒤 사용자가 다른 버전으로 옮겨 가지 않았으면 새 결과를 보여 줌 (최종본은 바꾸지 않음 · A19)
    if c.selected_version_id in (None, job.base_version_id, snap.get("selected_at_request")):
        c.selected_version_id = v.id
    repository.add_event(c, f"이미지 {'편집' if edit else '시안'}이 준비됐어요 · {v.label} (v{v.seq})",
                         {"kind": "job_done", "jobId": job.id, "versionId": v.id}, user=job.created_by)
    c.updated_at = utcnow()
    db.session.commit()
    worklog.add("content", "AI 프롬프트", "이미지 편집" if edit else "이미지 생성",
                detail={"model": job.model, "size": job.size, "prompt": job.prompt[:1500], "campaign": c.id,
                        "version": v.id, "usage": usage, "seconds": image_meta["seconds"]},
                user_name=job.created_by)


# ───────── Higgsfield (제출 → 상태 확인 → 결과 받기) ─────────

def _ctype(path):
    return MIMES.get(Path(path).suffix.lower(), "image/jpeg")


def _hf_run(job, model_path, body, ref_paths):
    """참고 사진 올리기 → 제출 → 끝날 때까지 상태 확인 → 결과 파일. 반환: (bytes, usage)"""
    urls = [higgsfield.upload_ref(p, _ctype(p)) for p in ref_paths]
    if urls:
        body = dict(body, image_urls=urls)
    rid = higgsfield.submit(model_path, body)
    job.snapshot = dict(job.snapshot, request_id=rid, request={k: v for k, v in body.items() if k != "image_urls"})
    db.session.commit()
    deadline, misses = time.time() + WAIT["video" if job.kind == "video" else "image"], 0
    while True:
        time.sleep(POLL_EVERY)
        try:
            st = higgsfield.status(rid)
            misses = 0
        except (requests.RequestException, ValueError):
            misses += 1
            if misses >= 5:
                raise providers.SubmissionUnknown(f"Higgsfield 상태를 확인하지 못했습니다. Higgsfield에서 요청 {rid}를 확인하세요 "
                                                  "(자동으로 다시 제출하지 않음).", 502)
            continue
        job.updated_at = utcnow()  # 오래 걸리는 영상이 '멈춘 작업'으로 처리되지 않게
        db.session.commit()
        s = st["status"]
        if s == "completed":
            if not st["url"]:
                raise providers.ProviderError("Higgsfield가 완료라고 했지만 결과 주소가 없습니다.")
            return higgsfield.download(st["url"]), {"request_id": rid, "references": len(urls)}
        if s == "nsfw":
            raise providers.ProviderError("Higgsfield 안전 정책에 걸려 만들지 못했습니다. 요청 표현을 바꿔 보세요.", 400)
        if s == "failed":
            raise providers.ProviderError("Higgsfield 생성 실패" + (f" ({st['error']})" if st["error"] else "") + ".")
        if s == "canceled":
            raise Canceled("Higgsfield에서 요청이 취소됐어요.")
        if time.time() > deadline:
            raise providers.SubmissionUnknown(f"정해진 시간 안에 끝나지 않았어요. Higgsfield에서 요청 {rid}를 확인하세요 "
                                              "(자동으로 다시 제출하지 않음).", 504)


def cancel_job(job):
    """Higgsfield 요청 취소 (시작 전만 가능). 반환: (ok, 메시지)"""
    rid = (job.snapshot or {}).get("request_id")
    if job.status not in ("queued", "running") or not rid:
        return False, "취소할 수 있는 Higgsfield 요청이 없어요 (아직 제출 전이거나 이미 끝났어요)."
    if not higgsfield.cancel(rid):
        return False, "이미 생성이 시작돼 취소할 수 없어요. 끝나면 결과가 저장돼요."
    return True, "취소 요청을 보냈어요. Higgsfield가 취소를 확인하면 작업이 '취소됨'으로 바뀌어요."


# ───────── 영상 (Higgsfield Seedance 2.0 · 720p) ─────────

def video_seconds(text):
    """'15초 세로형' → 15 (4~15초, 없으면 5초)."""
    m = re.search(r"(\d+)\s*(초|s\b|sec)", str(text or ""), re.I)
    lo, hi = higgsfield.VIDEO_SECONDS
    return max(lo, min(hi, int(m.group(1)))) if m else 5


def _video_plan(snap, job, base, seconds, ratio):
    dec = {k: (v or {}).get("value") for k, v in (snap.get("decisions") or {}).items()}
    prev = _prev_video(base)
    user = {"sheet": snap.get("labels"), "decisions": snap.get("decisions"),
            "brand_facts": (snap.get("facts") or {}).get("brand"), "product_facts": (snap.get("facts") or {}).get("products"),
            "script": ({"label": base.label, "body": base.body[:4000]} if base is not None and not prev else None),
            "previous_video": ({"label": base.label, "prompt": prev.get("prompt", "")[:2500], "notes": base.body[:400]}
                               if prev else None),
            "request": snap.get("request"), "instructions": job.instructions, "duration_seconds": seconds,
            "aspect_ratio": ratio, "character_use": assets.character_choice(snap.get("decisions")), "reference_photos": True}
    try:
        out = providers.call_json(prompts.VIDEO_V1, prompts.dumps(user), schema=schemas.VIDEO_PLAN,
                                  name="content_video_plan", action="영상 프롬프트 작성", max_tokens=1200, timeout=90)
    except AIError:
        if prev.get("prompt"):  # 수정: 이전 프롬프트 + 수정 요청
            first = prev["prompt"].split("\n\n")[0][:1500]
            return {"prompt_en": first + (f" Revision (Korean): {job.instructions[:300]}" if job.instructions else ""),
                    "notes_ko": "AI 프롬프트 작성에 실패해 이전 영상 프롬프트에 수정 요청을 붙여 썼어요.",
                    "flavors": [], "include_character": bool(prev.get("include_character")), "fallback": True}
        bits = [f"{seconds}-second short-form video for REBOOT, a US food & beverage brand.",
                "Deep black background with neon lime accents, smooth camera motion."]
        if dec.get("concept"):
            bits.append(f"Concept (Korean note): {dec['concept']}.")
        if job.instructions:
            bits.append(f"Direction (Korean): {job.instructions[:300]}")
        return {"prompt_en": " ".join(bits), "notes_ko": "AI 프롬프트 작성에 실패해 결정 사항으로 만든 기본 프롬프트를 썼어요.",
                "flavors": [], "include_character": assets.character_choice(snap.get("decisions")) == "사용",
                "fallback": True}
    return {"prompt_en": str(out.get("prompt_en") or "").strip()[:2000] or "Short product video for REBOOT on black.",
            "notes_ko": str(out.get("notes_ko") or "")[:400],
            "flavors": [f for f in out.get("flavors") or [] if f in assets.FLAVORS][:3],
            "include_character": bool(out.get("include_character")), "fallback": False}


def _prev_video(base):
    """기준 버전이 영상이면 그 영상의 생성 기록 (수정 = 이 기록 + 수정 요청으로 새로 생성)."""
    if base is None or base.kind != "video":
        return {}
    return (base.snapshot or {}).get("video") or {}


def _ratio_in(text):
    """수정 요청에 비율이 직접 있으면 그 비율 ('4:5 세로' · '가로로')."""
    t = str(text or "")
    if re.search(r"\d+\s*[:x×/]\s*\d+", t) or re.search(r"세로|가로|portrait|landscape", t, re.I):
        return higgsfield.nearest_ratio(t, higgsfield.VIDEO_RATIOS, "9:16")
    return None


def _next_video_label(campaign_id):
    return f"영상 {ContentVersion.query.filter_by(campaign_id=campaign_id, kind='video').count() + 1}"


def run_video_job(job_id):
    job = db.session.get(ContentJob, job_id)
    if job is None or job.status != "queued":
        return  # 이미 실행했거나 취소 — 다시 제출하지 않음
    job.status, job.started_at = "running", utcnow()
    db.session.commit()
    snap = job.snapshot
    dec = snap.get("decisions") or {}
    base = db.session.get(ContentVersion, job.base_version_id) if job.base_version_id else None  # 구성안·초안 또는 수정할 영상
    prev = _prev_video(base)
    started = time.time()
    seconds = video_seconds((dec.get("duration") or {}).get("value") or (dec.get("length") or {}).get("value"))
    ratio = higgsfield.nearest_ratio((dec.get("ratio") or {}).get("value"), higgsfield.VIDEO_RATIOS, "9:16")
    if prev:  # 수정: 요청에 길이·비율이 없으면 이전 영상 그대로
        said = re.search(r"\d+\s*(초|s\b|sec)", job.instructions or "", re.I)
        seconds = video_seconds(job.instructions) if said else (prev.get("duration") or seconds)
        ratio = _ratio_in(job.instructions) or prev.get("aspect_ratio") or ratio
    try:
        plan = _video_plan(snap, job, base, seconds, ratio)
        include_char = _include_character(snap, plan)
        refs = _references(snap, job, plan, include_char, False)
        prompt = plan["prompt_en"] + "\n\n" + ((_ref_lines(refs, snap.get("facts") or {}, 1) + "\n" + VIDEO_REF_TEXT)
                                                if refs else VIDEO_NO_TEXT)
        model_path = higgsfield.VIDEO_REF if refs else higgsfield.VIDEO_TEXT
        job.prompt, job.model = prompt, model_path
        job.size = f"{ratio} · {higgsfield.VIDEO_RESOLUTION} · {seconds}초"
        db.session.commit()
        body = {"prompt": prompt, "duration": seconds, "resolution": higgsfield.VIDEO_RESOLUTION, "aspect_ratio": ratio,
                "generate_audio": False}
        data, usage = _hf_run(job, model_path, body, [str(assets.path_of(a)) for a in refs])
    except Canceled as e:
        return _fail(job, str(e), status="canceled")
    except providers.SubmissionUnknown as e:
        return _fail(job, str(e), status="submission_unknown")
    except providers.ProviderError as e:
        return _fail(job, str(e))
    except Exception:  # 예상 못 한 오류 — 원문은 서버 로그에만
        current_app.logger.exception("콘텐츠 영상 작업 실패")
        return _fail(job, "영상 생성 중 알 수 없는 오류가 났습니다.")

    job.status = "saving"
    db.session.commit()
    if file_ext(data) != ".mp4":
        return _fail(job, "영상 결과 파일 형식(mp4)을 확인하지 못해 저장하지 않았습니다.")
    name = _save_png(data, job.campaign_id, job.id, ".mp4")
    if not name:
        return _fail(job, "영상은 생성됐지만 파일을 저장하지 못했습니다 (서버 저장 공간 확인).")
    social = suggest_social(snap, basis="video_plan",
                            video_plan="\n".join(x for x in (plan["notes_ko"], plan["prompt_en"]) if x))
    c = db.session.get(ContentCampaign, job.campaign_id)
    if c is None:  # 작업 중 캠페인이 지워짐
        (upload_dir() / name).unlink(missing_ok=True)
        job.status, job.finished_at, job.error = "failed", utcnow(), "캠페인이 삭제되어 결과를 버렸습니다."
        db.session.commit()
        return
    note_bits = [f"Higgsfield Seedance 2.0 · {higgsfield.VIDEO_RESOLUTION} · {seconds}초 · {ratio} · 소리 없음"]
    if prev:
        note_bits.append(f"{base.label} · v{base.seq}을 바탕으로 수정 요청을 반영해 새로 생성 (원본 영상 유지)")
    elif base is not None:
        note_bits.append(f"{base.label} · v{base.seq} {'구성안' if base.kind == 'script' else '초안'} 기준")
    if refs:
        note_bits.append(f"최종 패키지·캐릭터 사진 {len(refs)}장을 참고해 생성 · 라벨·모양은 게시 전 확인")
    if include_char:
        note_bits.append("캐릭터 Rebu 포함")
    video_meta = {"model": model_path, "provider": "higgsfield", "resolution": higgsfield.VIDEO_RESOLUTION,
                  "duration": seconds, "aspect_ratio": ratio, "prompt": job.prompt[:3000], "usage": usage,
                  "references": [a["id"] for a in refs], "include_character": bool(include_char),
                  "prompt_fallback": plan.get("fallback", False), "seconds": round(time.time() - started, 1),
                  "job_id": job.id, "script_version_id": prev.get("script_version_id") if prev else (base.id if base is not None else None),
                  "revised_from": base.id if prev else None}
    v = ContentVersion(
        campaign_id=c.id, seq=repository.next_seq(c.id), kind="video",
        label=base.label if prev else _next_video_label(c.id),
        title=(plan["notes_ko"] or "영상 시안")[:200], body=plan["notes_ko"] or "", image_file=name,
        parent_id=base.id if prev else None,
        source="ai", approach="", note=" · ".join(note_bits)[:500],
        facts_version=(snap.get("facts") or {}).get("facts_version") or "", created_by=job.created_by, message_id=job.message_id)
    v.snapshot = dict(snap.get("snapshot") or {}, video=video_meta, social=social, model=model_path)
    v.snapshot_hash = snap.get("snapshot_hash") or ""
    v.review = {"videoScope": "영상 속 장면·제품 모양은 자동 검사하지 않음 — 게시 전 사람이 확인"}
    db.session.add(v)
    db.session.flush()
    job.status, job.result_version_id, job.finished_at = "succeeded", v.id, utcnow()
    if c.selected_version_id in (None, job.base_version_id, snap.get("selected_at_request")):
        c.selected_version_id = v.id
    repository.add_event(c, f"{'영상 수정본' if prev else '영상'}이 준비됐어요 · {v.label} (v{v.seq})",
                         {"kind": "job_done", "jobId": job.id, "versionId": v.id}, user=job.created_by)
    c.updated_at = utcnow()
    db.session.commit()
    worklog.add("content", "AI 프롬프트", "영상 생성",
                detail={"model": model_path, "size": job.size, "prompt": job.prompt[:1500], "campaign": c.id,
                        "version": v.id, "usage": usage, "seconds": video_meta["seconds"]},
                user_name=job.created_by)


# ───────── 브라우저 편집기에서 저장 (AI 아님 · 비용 없음) ─────────

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
UPLOAD_MAX = 12 * 1024 * 1024
LAYER_ROLES = ("headline", "slogan", "cta", "text")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
EDIT_KEYS = ("crop", "offset", "brightness", "contrast", "saturation")  # 기록용 편집 값 (화면에서 보낸 것 중 이것만)


def png_size(data):
    """PNG 머리(IHDR)에서 가로·세로 — PNG가 아니면 None."""
    if len(data) < 24 or not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _num(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, f)) if f == f else default


def clean_layers(raw):
    """편집기 텍스트 레이어 → 허용 값만. 슬로건은 원문 그대로 (README 7.2)."""
    out = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        role = t.get("role") if t.get("role") in LAYER_ROLES else "text"
        text = (official_slogan(t.get("text")) or SLOGAN) if role == "slogan" else str(t.get("text") or "").strip()[:160]
        if not text:
            continue
        out.append({"role": role, "text": text, "x": _num(t.get("x"), 0, 1, .06), "y": _num(t.get("y"), 0, 1, .06),
                    "size": _num(t.get("size"), .02, .2, .06),
                    "color": t.get("color") if COLOR_RE.match(str(t.get("color") or "")) else "#ffffff",
                    "align": t.get("align") if t.get("align") in ("left", "center", "right") else "left"})
        if len(out) >= 6:
            break
    return out


# ───────── 레퍼런스 이미지 (사용자가 올림 · 대화 AI가 봄 · 생성 참고 사진으로는 안 씀) ─────────

REF_MAX_BYTES = 5 * 1024 * 1024   # 화면이 긴 쪽 1024px JPEG로 줄여 보내므로 넉넉한 상한
REF_PER_CAMPAIGN = 3
REF_MIMES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def image_dims(data):
    """JPEG · PNG · WebP 머리에서 가로·세로 (못 읽으면 None)."""
    size = png_size(data)
    if size:
        return size
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            seg = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + seg
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
        if chunk == b"VP8 ":
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        if chunk == b"VP8L":
            b = int.from_bytes(data[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    return None


def save_reference(data, campaign_id):
    """검사 통과한 레퍼런스 바이트 → (파일 이름, (가로, 세로)). 형식이 아니면 ValueError."""
    ext = file_ext(data)
    if ext not in REF_MIMES:
        raise ValueError("레퍼런스는 JPG · PNG · WebP 이미지만 올릴 수 있어요.")
    dims = image_dims(data)
    if dims is None or not (16 <= dims[0] <= 4096 and 16 <= dims[1] <= 4096):
        raise ValueError("이미지 크기를 읽지 못했거나 너무 커요 (가로·세로 16~4096px).")
    name = f"c{campaign_id}-ref-{secrets.token_hex(8)}{ext}"
    try:
        (upload_dir() / name).write_bytes(data)
    except OSError:
        current_app.logger.exception("콘텐츠 레퍼런스 저장 실패")
        return None, dims
    return name, dims


def read_reference(name):
    """대화 AI에 보낼 (바이트, MIME) — 파일이 없으면 None."""
    if not FILE_RE.match(name or ""):
        return None
    path = upload_dir() / name
    if not path.is_file():
        return None
    return path.read_bytes(), REF_MIMES.get("." + name.rsplit(".", 1)[1], "image/jpeg")


def save_upload(data, campaign_id):
    name = f"c{campaign_id}-edit-{secrets.token_hex(8)}.png"
    try:
        (upload_dir() / name).write_bytes(data)
    except OSError:
        current_app.logger.exception("콘텐츠 편집 이미지 저장 실패")
        return None
    return name


def delete_files(names):
    d = upload_dir()
    for n in names:
        if n and FILE_RE.match(n):
            (d / n).unlink(missing_ok=True)
