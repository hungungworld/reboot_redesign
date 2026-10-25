"""캠페인 상태: 시트·결정 검증과 반영, 리비전 확인, 화면용 직렬화.

시트 수정 · 채팅 패치 · 후보 선택 · 직접 입력 · 되돌리기는 모두 plan_changes() → commit_changes() 한 경로를 씁니다
(설명서 4.2 "시트 수정과 자연어 수정은 하나의 상태 변경 경로"). plan_changes는 DB를 건드리지 않는 순수 함수라
AI 호출 전후에 미리 계산해 두고, 저장은 마지막 짧은 트랜잭션에서만 합니다.
"""
import copy
import hashlib
import json
import re
from datetime import timedelta

from sqlalchemy import func, update

from server.catalog import service as catalog
from server.extensions import db
from server.models import iso, utcnow
from . import brandkit
from . import options as O
from .models import ContentCampaign, ContentDecisionEvent, ContentJob, ContentMessage, ContentReference, ContentVersion

EMPTY_SHEET = {"content_type": "", "channels": [], "brand_focus": False, "product_ids": [], "goal": "", "extra_request": ""}
SHEET_FIELDS = tuple(EMPTY_SHEET)
DECISION_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
DECISION_STATUSES = ("confirmed", "proposed", "undecided")
MAX_DECISIONS = 30
ACTIVE_JOB = ("queued", "running", "saving")
JOB_STALE = timedelta(minutes=12)
GOAL_MAX, EXTRA_MAX, REFERENCE_MAX = 200, 1000, 4000


class Conflict(Exception):
    """expected_revision이 현재와 다름 — 다른 곳에서 먼저 저장함."""

    def __init__(self, campaign_id):
        super().__init__("conflict")
        self.campaign_id = campaign_id


class Duplicate(Exception):
    """같은 client_request_id로 이미 처리한 요청."""

    def __init__(self, campaign_id):
        super().__init__("duplicate")
        self.campaign_id = campaign_id


class FactChange(Exception):
    """제품·브랜드 사실 변경 요청 — 대화로 바꾸지 않고 확인 대기로 기록."""


# ───────── 기준 데이터 ─────────

def products_index():
    """{제품 ID: 팩트시트 dict} — 표준 순서."""
    return {p["id"]: p for p in catalog.get_products() if p.get("id")}


def clip_text(value, n):
    return re.sub(r"[ \t]+\n", "\n", str(value if value is not None else "")).strip()[:n]


# ───────── 시트 ─────────

def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [x for x in re.split(r"[,\s]+", value.strip()) if x]
    return []


def norm_sheet_value(field, value, products):
    if field == "content_type":
        v = str(value or "").strip()
        if not v:
            raise ValueError("만들 콘텐츠를 골라 주세요.")
        if v not in O.CONTENT_TYPES:
            raise ValueError(f"알 수 없는 콘텐츠 종류입니다: {v[:30]}")
        return v
    if field == "channels":
        out = []
        for x in _as_list(value):
            x = str(x).strip().lower()
            if x not in O.CHANNELS:
                raise ValueError(f"알 수 없는 채널입니다: {x[:30]}")
            if x not in out:
                out.append(x)
        return out
    if field == "brand_focus":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError("'Reboot 브랜드 자체' 선택은 예/아니요 값이어야 합니다.")
    if field == "product_ids":
        wanted = []
        for x in _as_list(value):
            x = str(x).strip()
            if x not in products:
                raise ValueError(f"알 수 없는 제품입니다: {x[:40]}")
            if x not in wanted:
                wanted.append(x)
        return [pid for pid in products if pid in wanted]
    if field == "goal":
        return clip_text(value, GOAL_MAX)
    if field == "extra_request":
        return clip_text(value, EXTRA_MAX)
    raise ValueError("허용되지 않은 시트 항목입니다.")


def check_sheet(s):
    """시작 조건 (README 2.2·3.3): 콘텐츠 · 채널 · 대상(브랜드 자체 또는 제품 1개 이상)."""
    if s.get("content_type") not in O.CONTENT_TYPES:
        raise ValueError("만들 콘텐츠를 골라 주세요.")
    if not s.get("channels"):
        raise ValueError("게시 채널을 골라 주세요.")
    if len(s["channels"]) > 1:
        raise ValueError("게시 채널은 하나만 고를 수 있어요.")
    if (s["content_type"] == O.CRM_TYPE) != (s["channels"][0] == O.CRM_CHANNEL):
        raise ValueError("CRM 메시지는 CRM 채널하고만, CRM 채널은 CRM 메시지하고만 만들 수 있어요.")
    if not s.get("brand_focus") and not s.get("product_ids"):
        raise ValueError("제작 대상을 하나 이상 골라 주세요 (Reboot 브랜드 자체 또는 제품).")


def build_sheet(data, products):
    s = copy.deepcopy(EMPTY_SHEET)
    if "channel" in data and "channels" not in data:
        data = dict(data, channels=[data.get("channel")] if data.get("channel") else [])
    for k in SHEET_FIELDS:
        if k in data and data[k] is not None:
            s[k] = norm_sheet_value(k, data[k], products)
    check_sheet(s)
    return s


def sheet_of(c):
    return {"content_type": c.content_type, "channels": c.channels, "brand_focus": bool(c.brand_focus),
            "product_ids": c.product_ids, "goal": c.goal or "", "extra_request": c.extra_request or ""}


def settings_of(c):
    s = dict(O.DEFAULT_SETTINGS)
    s.update({k: v for k, v in (c.settings or {}).items() if k in O.DEFAULT_SETTINGS})
    return s


def norm_settings(data, base=None):
    s = dict(base or O.DEFAULT_SETTINGS)
    if "answer_length" in data:
        if data["answer_length"] not in O.ANSWER_LENGTHS:
            raise ValueError("답변 길이는 짧게 · 보통 · 자세히 중 하나입니다.")
        s["answer_length"] = data["answer_length"]
    for key, lo, hi, label in (("candidates", 1, 3, "제안 수"), ("rewrite_max", 0, 2, "자동 재작성 횟수")):
        if key in data:
            try:
                v = int(data[key])
            except (TypeError, ValueError):
                raise ValueError(f"{label}는 숫자입니다.")
            if not lo <= v <= hi:
                raise ValueError(f"{label}는 {lo}~{hi} 사이입니다.")
            s[key] = v
    if "video_mode" in data:
        if data["video_mode"] not in O.VIDEO_MODES:
            raise ValueError("영상 모드는 구성안만 · 실제 영상 중 하나입니다.")
        s["video_mode"] = data["video_mode"]
    if "image_provider" in data:
        if data["image_provider"] not in O.IMAGE_PROVIDERS:
            raise ValueError("이미지 AI는 OpenAI · Higgsfield 중 하나입니다.")
        s["image_provider"] = data["image_provider"]
    return s


def auto_title(sheet):
    today = catalog._today()
    return f"{O.CHANNELS[sheet['channels'][0]]['short']} {O.CONTENT_TYPES[sheet['content_type']]['label']} · {today:%m.%d}"


# ───────── 결정 ─────────

def decision_key(raw):
    k = str(raw or "").strip()
    k = O.DECISION_ALIASES.get(k, O.DECISION_ALIASES.get(k.lower(), k))
    k = k.lower().replace("-", "_").replace(" ", "_")
    if k in O.FACT_KEYS:
        raise FactChange()
    if not DECISION_KEY_RE.match(k):
        raise ValueError("허용되지 않은 결정 항목 이름입니다.")
    if k in SHEET_FIELDS or k in ("title", "status", "owner", "revision", "created_by", "id"):
        raise ValueError("시트·캠페인 정보는 결정 항목으로 바꿀 수 없어요.")
    return k


def decision_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "예" if value else "아니요"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        value = ", ".join(str(x) for x in value if x is not None)
    v = clip_text(value, 300)
    return v or None


def field_label(field):
    if field.startswith("sheet."):
        return O.SHEET_LABELS.get(field[6:], field[6:])
    if field.startswith("decisions."):
        k = field[10:]
        return O.DECISION_LABELS.get(k, k)
    return field


def plan_changes(sheet, decisions, changes, *, user, source, revision, vague=False, allow_sheet=True,
                 protect_confirmed=False, products=None):
    """변경안 검증 → 새 시트·결정 (DB에 쓰지 않음).

    changes: [{field, value, status}] — field는 sheet.<항목> 또는 decisions.<키>
    vague: 사용자가 "좋네"처럼 감상만 말함 → 확정을 제안으로 낮추고 시트는 바꾸지 않음
    protect_confirmed: AI 제안이 이미 확정된 값을 덮지 않게 (채팅 경로)
    반환: {sheet, decisions, applied, rejected, fact_requests, demoted}
    """
    products = products if products is not None else products_index()
    sheet = copy.deepcopy(sheet)
    decisions = copy.deepcopy(decisions)
    applied, rejected, fact_requests, demoted = [], [], [], []
    pending_sheet = {}  # 시트 변경은 모아서 한 번에 검사 (예: CRM → 이미지 + 채널 Instagram)
    now = iso(utcnow())
    for ch in list(changes or [])[:20]:
        if not isinstance(ch, dict):
            continue
        field = str(ch.get("field") or "").strip()[:80]
        status = ch.get("status") if ch.get("status") in DECISION_STATUSES else "confirmed"
        value = ch.get("value")
        try:
            if field.startswith("sheet."):
                key = field[6:]
                if key not in SHEET_FIELDS:
                    raise ValueError("허용되지 않은 시트 항목입니다.")
                if not allow_sheet or status != "confirmed" or vague:
                    if key in ("goal", "extra_request") and value:
                        demoted.append({"field": field, "value": decision_value(value)})
                    raise ValueError("시트는 사용자가 분명히 바꾸자고 할 때만 바꿔요 (제안은 후보로 표시)." if allow_sheet
                                     else "첫 답변에서는 시트를 바꾸지 않아요 (제안은 후보로 표시).")
                pending_sheet[key] = norm_sheet_value(key, value, products)
            elif field.startswith("decisions."):
                key = decision_key(field[10:])
                field = "decisions." + key
                old = decisions.get(key)
                if status == "undecided":
                    if old is None:
                        continue
                    if protect_confirmed and old.get("status") == "confirmed":
                        raise ValueError("확정된 값은 AI가 미정으로 되돌리지 않아요.")
                    decisions.pop(key)
                    applied.append({"field": field, "label": field_label(field), "old": _pub(old), "new": None})
                    continue
                v = decision_value(value)
                if v is None:
                    raise ValueError("값이 비어 있어요.")
                if vague and status == "confirmed":
                    status = "proposed"
                if old and old.get("value") == v and old.get("status") == status:
                    continue
                if protect_confirmed and old and old.get("status") == "confirmed" and status == "proposed":
                    raise ValueError("확정된 값은 AI 제안으로 덮어쓰지 않아요 (후보로 표시).")
                if old is None and len(decisions) >= MAX_DECISIONS:
                    raise ValueError("결정 항목이 너무 많아요.")
                decisions[key] = {"value": v, "status": status, "source": source, "by": user, "at": now,
                                  "revision": revision + 1, "messageId": None}
                applied.append({"field": field, "label": field_label(field), "old": _pub(old),
                                "new": {"value": v, "status": status}})
            elif field.split(".")[0] in ("facts", "fact", "product", "products", "brand"):
                raise FactChange()
            else:
                raise ValueError("허용되지 않은 항목입니다.")
        except FactChange:
            fact_requests.append({"field": field, "value": decision_value(value)})
            rejected.append({"field": field, "label": field_label(field),
                             "reason": "제품·브랜드 사실은 대화로 바꾸지 않아요 (확인 대기로 기록 · 설정 › 제품 정보에서 확인)."})
        except ValueError as e:
            rejected.append({"field": field, "label": field_label(field), "reason": str(e)})
    changed = {k: v for k, v in pending_sheet.items() if v != sheet[k]}
    if changed:
        trial = dict(sheet, **changed)
        try:
            check_sheet(trial)
        except ValueError as e:
            for k in changed:
                rejected.append({"field": "sheet." + k, "label": O.SHEET_LABELS[k], "reason": str(e)})
        else:
            sheet_applied = [{"field": "sheet." + k, "label": O.SHEET_LABELS[k],
                              "old": {"value": sheet[k], "status": "confirmed"}, "new": {"value": v, "status": "confirmed"}}
                             for k, v in changed.items()]
            applied = sheet_applied + applied
            sheet = trial
    return {"sheet": sheet, "decisions": decisions, "applied": applied, "rejected": rejected,
            "fact_requests": fact_requests, "demoted": demoted}


def _pub(d):
    return None if d is None else {"value": d.get("value"), "status": d.get("status")}


def commit_changes(c, plan, *, source, user, message_id=None):
    """plan_changes 결과를 캠페인에 씀 (커밋은 호출한 쪽). 리비전은 guarded_bump로 올림."""
    s = plan["sheet"]
    c.content_type, c.channels, c.brand_focus = s["content_type"], s["channels"], bool(s["brand_focus"])
    c.product_ids, c.goal, c.extra_request = s["product_ids"], s["goal"], s["extra_request"]
    decisions = plan["decisions"]
    for a in plan["applied"]:
        if a["field"].startswith("decisions.") and a["new"] is not None:
            d = decisions.get(a["field"][10:])
            if d is not None and message_id:
                d["messageId"] = message_id
        db.session.add(ContentDecisionEvent(
            campaign_id=c.id, field=a["field"], old_json=json.dumps(a["old"], ensure_ascii=False),
            new_json=json.dumps(a["new"], ensure_ascii=False), source=source, message_id=message_id,
            user_name=user, revision=c.revision + 1))
    c.decisions = decisions
    c.updated_by = user


def guarded_bump(campaign_id, expected):
    """리비전을 expected → expected+1로 (그 사이 다른 저장이 있었으면 Conflict). 같은 트랜잭션 안에서 부름."""
    db.session.flush()
    res = db.session.execute(
        update(ContentCampaign).where(ContentCampaign.id == campaign_id, ContentCampaign.revision == expected)
        .values(revision=expected + 1).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.session.rollback()
        raise Conflict(campaign_id)


def add_event(c, text, payload=None, user=""):
    m = ContentMessage(campaign_id=c.id, role="event", text=text[:2000], revision=c.revision, user_name=user)
    m.payload = payload or {}
    db.session.add(m)
    return m


def describe_applied(applied, limit=4):
    parts = []
    for a in applied[:limit]:
        new = a.get("new")
        parts.append(f"{a['label']} → {short_value(new['value']) if new else '미정'}")
    more = len(applied) - limit
    return " · ".join(parts) + (f" 외 {more}건" if more > 0 else "")


def short_value(v, n=40):
    if isinstance(v, bool):
        return "예" if v else "아니요"
    if isinstance(v, list):
        names = []
        for x in v:
            names.append(O.CHANNELS.get(x, {}).get("label") or O.CONTENT_TYPES.get(x, {}).get("label") or str(x))
        v = ", ".join(names) if names else "없음"
    elif isinstance(v, str) and v in O.CONTENT_TYPES:
        v = O.CONTENT_TYPES[v]["label"]
    s = str(v if v not in (None, "") else "비움")
    return s if len(s) <= n else s[: n - 1] + "…"


# ───────── 생성 시점 설정 (변경 사항 미반영 판단) ─────────

def snapshot_core(sheet, decisions):
    """생성에 영향을 주는 값만 (캠페인명·상태 제외 — README 6장)."""
    return {"sheet": {k: sheet.get(k) for k in SHEET_FIELDS},
            "decisions": {k: (v or {}).get("value") for k, v in sorted((decisions or {}).items())}}


def snapshot_hash(core):
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def stale_fields(snap_core, cur_core):
    if not snap_core:
        return []
    out = []
    a, b = snap_core.get("sheet") or {}, cur_core.get("sheet") or {}
    for k in SHEET_FIELDS:
        if a.get(k) != b.get(k):
            out.append(O.SHEET_LABELS[k])
    da, db_ = snap_core.get("decisions") or {}, cur_core.get("decisions") or {}
    for k in sorted(set(da) | set(db_)):
        if da.get(k) != db_.get(k):
            out.append(O.DECISION_LABELS.get(k, k))
    return out


def make_snapshot(sheet, decisions, settings, facts_version, **extra):
    core = snapshot_core(sheet, decisions)
    snap = {"core": core, "settings": dict(settings), "facts_version": facts_version,
            "decision_status": {k: (v or {}).get("status") for k, v in (decisions or {}).items()}}
    snap.update(extra)
    return snap, snapshot_hash(core)


# ───────── 버전 번호 · 라벨 ─────────

def next_seq(campaign_id):
    n = db.session.query(func.max(ContentVersion.seq)).filter(ContentVersion.campaign_id == campaign_id).scalar()
    return (n or 0) + 1


def next_letter_labels(campaign_id, n):
    used = {lab for (lab,) in db.session.query(ContentVersion.label).filter(ContentVersion.campaign_id == campaign_id)}
    out, i = [], 0
    while len(out) < n and i < 26 * 3:
        letter = chr(ord("A") + i % 26) + ("" if i < 26 else str(i // 26 + 1))
        lab = f"{letter}안"
        if lab not in used:
            out.append(lab)
        i += 1
    return out


def next_image_label(campaign_id):
    n = ContentVersion.query.filter_by(campaign_id=campaign_id, kind="image", parent_id=None).count()
    return f"시안 {n + 1}"


def resolve_version_ref(refs, versions):
    """'B안' · '시안 2' · 'v5' → 가장 최근 버전."""
    for r in refs or []:
        cands = [v for v in versions if (r.get("label") and v.label == r["label"]) or (r.get("seq") and v.seq == r["seq"])]
        if cands:
            return max(cands, key=lambda v: v.id)
    return None


# ───────── 작업 상태 정리 ─────────

def settle_stale_jobs(campaign_id=None):
    """서버 재시작 등으로 멈춘 작업 → 제출 여부 불명 (자동 재제출 없음 · README 8장 A17)."""
    q = ContentJob.query.filter(ContentJob.status.in_(ACTIVE_JOB), ContentJob.updated_at < utcnow() - JOB_STALE)
    if campaign_id is not None:
        q = q.filter(ContentJob.campaign_id == campaign_id)
    changed = False
    for j in q.all():
        if j.status == "queued" or (j.status == "running" and not j.prompt):  # 제공사에 보내기 전에 멈춤 → 비용 없음
            j.status, j.error = "failed", "이미지 요청을 보내기 전에 멈췄습니다 (서버 재시작 등). 필요하면 다시 요청하세요."
        elif j.status == "saving":  # 결과는 받았지만 파일 보관 전에 멈춤 (결과 데이터는 메모리에만 있었음)
            j.status, j.error = "failed", "이미지는 생성됐지만 파일을 보관하기 전에 멈췄습니다. 자동으로 다시 생성하지 않으니 필요하면 다시 요청하세요."
        else:  # 제공사에 보낸 뒤 응답을 못 받음 → 생성 여부 불명
            j.status = "submission_unknown"
            j.error = "결과를 확인하지 못한 채 오래 멈춰 있습니다 (서버 재시작 등). 자동으로 다시 제출하지 않으니 필요하면 다시 요청하세요."
        j.finished_at = utcnow()
        changed = True
    if changed:
        db.session.commit()


# ───────── 직렬화 ─────────

def message_dict(m):
    if m is None:
        return None
    return {"id": m.id, "role": m.role, "text": m.text, "payload": m.payload, "revision": m.revision,
            "user": m.user_name, "createdAt": iso(m.created_at)}


def event_dict(e):
    return {"id": e.id, "field": e.field, "label": field_label(e.field), "old": e.old, "new": e.new, "source": e.source,
            "messageId": e.message_id, "user": e.user_name, "revision": e.revision, "at": iso(e.created_at)}


def job_dict(j):
    if j is None:
        return None
    return {"id": j.id, "campaignId": j.campaign_id, "kind": j.kind, "status": j.status, "model": j.model, "size": j.size,
            "baseVersionId": j.base_version_id, "versionId": j.result_version_id, "error": j.error,
            "instructions": j.instructions[:300], "createdAt": iso(j.created_at), "updatedAt": iso(j.updated_at),
            "finishedAt": iso(j.finished_at), "createdBy": j.created_by,
            "provider": (j.snapshot or {}).get("provider") or "openai",
            "cancelable": bool((j.snapshot or {}).get("request_id")) and j.status in ("queued", "running")}


def file_url(name):
    return f"/api/content/files/{name}" if name else None


def reference_dict(r):
    return {"id": r.id, "url": file_url(r.file), "width": r.width, "height": r.height, "note": r.note, "summary": r.summary,
            "createdBy": r.created_by, "createdAt": iso(r.created_at)}


def references_of(campaign_id):
    return ContentReference.query.filter_by(campaign_id=campaign_id).order_by(ContentReference.id).all()


def version_dict(v, cur_core=None, final_id=None, selected_id=None):
    snap = v.snapshot
    stale = stale_fields(snap.get("core"), cur_core) if cur_core is not None else []
    return {
        "id": v.id, "seq": v.seq, "kind": v.kind, "kindLabel": O.KIND_LABELS.get(v.kind, v.kind), "label": v.label,
        "title": v.title, "body": v.body, "imageUrl": file_url(v.image_file) if v.kind != "video" else None,
        "videoUrl": file_url(v.image_file) if v.kind == "video" else None, "video": snap.get("video"), "parentId": v.parent_id, "source": v.source,
        "approach": v.approach, "note": v.note, "factsVersion": v.facts_version, "review": v.review, "claims": v.claims,
        "needsCheck": v.needs_check, "image": snap.get("image"), "social": snap.get("social"),
        "settings": snap.get("settings"), "model": snap.get("model"),
        "createdAt": iso(v.created_at), "createdBy": v.created_by, "isFinal": v.id == final_id,
        "isSelected": v.id == selected_id, "stale": bool(stale), "staleFields": stale,
    }


def labels_of(sheet, products=None):
    products = products if products is not None else products_index()
    names = [{"id": pid, "name": (products.get(pid) or {}).get("name") or pid, "ko": (products.get(pid) or {}).get("ko") or ""}
             for pid in sheet["product_ids"]]
    target = (["Reboot 브랜드 자체"] if sheet["brand_focus"] else []) + [n["name"] for n in names]
    return {"contentType": O.CONTENT_TYPES.get(sheet["content_type"], {}).get("label", sheet["content_type"]),
            "channels": [O.CHANNELS.get(c, {}).get("label", c) for c in sheet["channels"]],
            "products": names, "target": " + ".join(target)}


def campaign_state(c, *, message_limit=200):
    settle_stale_jobs(c.id)
    products = products_index()
    sheet = sheet_of(c)
    decisions = c.decisions
    cur = snapshot_core(sheet, decisions)
    versions = ContentVersion.query.filter_by(campaign_id=c.id).order_by(ContentVersion.id.desc()).limit(300).all()
    msgs = (ContentMessage.query.filter_by(campaign_id=c.id).order_by(ContentMessage.id.desc()).limit(message_limit).all())
    jobs_ = ContentJob.query.filter_by(campaign_id=c.id).order_by(ContentJob.created_at.desc()).limit(8).all()
    events = (ContentDecisionEvent.query.filter_by(campaign_id=c.id).order_by(ContentDecisionEvent.id.desc()).limit(15).all())
    total_msgs = ContentMessage.query.filter_by(campaign_id=c.id).count()
    return {
        "id": c.id, "title": c.title, "status": c.status, "revision": c.revision,
        "sheet": sheet, "referenceText": c.reference_text or "", "decisions": decisions, "settings": settings_of(c),
        "selectedVersionId": c.selected_version_id, "finalVersionId": c.final_version_id,
        "createdBy": c.created_by, "createdById": c.created_by_id, "createdAt": iso(c.created_at),
        "updatedAt": iso(c.updated_at), "updatedBy": c.updated_by,
        "labels": labels_of(sheet, products),
        "messages": [message_dict(m) for m in reversed(msgs)], "messagesOmitted": max(0, total_msgs - len(msgs)),
        "versions": [version_dict(v, cur, c.final_version_id, c.selected_version_id) for v in versions],
        "jobs": [job_dict(j) for j in jobs_],
        "recentChanges": [event_dict(e) for e in events],
        "facts": brandkit.product_facts(sheet["product_ids"]),
        "references": [reference_dict(r) for r in references_of(c.id)],
        "flow": O.flow_of(sheet),
        "currentHash": snapshot_hash(cur),
    }


def campaign_summary(c, version_count=0, cover=None, products=None):
    sheet = sheet_of(c)
    lab = labels_of(sheet, products)
    return {"id": c.id, "title": c.title, "status": c.status, "revision": c.revision,
            "contentType": c.content_type, "contentTypeLabel": lab["contentType"], "channels": sheet["channels"],
            "channelLabels": lab["channels"], "brandFocus": sheet["brand_focus"], "productIds": sheet["product_ids"],
            "products": lab["products"], "targetLabel": lab["target"], "goal": sheet["goal"],
            "versionCount": version_count, "finalVersionId": c.final_version_id, "cover": file_url(cover),
            "createdBy": c.created_by, "createdAt": iso(c.created_at), "updatedAt": iso(c.updated_at)}


def search_text(c, products):
    sheet = sheet_of(c)
    lab = labels_of(sheet, products)
    bits = [c.title, lab["contentType"], sheet["content_type"], c.status, sheet["goal"], lab["target"],
            " ".join(lab["channels"]), " ".join(sheet["channels"]), " ".join(p["ko"] for p in lab["products"]),
            " ".join(O.CHANNELS.get(x, {}).get("short", "") for x in sheet["channels"])]
    if sheet["brand_focus"]:
        bits.append("브랜드 brand reboot")
    return " ".join(b for b in bits if b).lower()
