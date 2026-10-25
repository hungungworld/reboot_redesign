"""인플루언서 · 파트너 API (담당: 혁주). 모든 주소는 로그인 필요 (server/security.py).

상태·설정
  GET    /api/partners/status                 규제 DB · OpenAI · Gmail 발송 연결 상태
  GET    /api/partners/config                 상태 + 세부 카테고리 · 기본 프롬프트 · 가이드 목적 · 첨부 규칙
업체 (3-1)
  GET    /api/partners  · /contacts           업체 목록 (?type=케이터링,인플루언서 &tags=festival,vip &q=검색어)
  POST   /api/partners/import                 업체 파일 가져오기 (multipart: catering=CSV, influencer=XLSX, file=자동 판단)
  POST   /api/partners/example                예시 데이터(가상 업체 · example.com) 불러오기 — 비어 있는 구분만
  GET    /api/partners/template/<catering.csv|influencer.xlsx>   가져오기 양식
  GET    /api/partners/overview               컨택 업체 수 · 진행 중 협업 · 회신 받음 · 회신 대기 · 업체별 최신 진행 상태
협업 메일 (3-2)
  POST   /api/partners/email/generate         영문 제목·본문 생성 (OpenAI JSON)
  POST   /api/partners/translate              {text, to:'ko'|'en'} 번역 (Markdown·REBOOT·해시태그·URL·코드 유지)
  POST   /api/partners/email/send             Gmail 발송 (확인 체크 필수) + 이력 자동 저장 (성공·실패 모두)
  GET    /api/partners/attachments            첨부파일 보관함
  POST   /api/partners/attachments            올리기 (multipart file — PDF·CSV·Word·Excel·PowerPoint·PNG·JPG·ZIP·TXT, 25MB)
  GET    /api/partners/attachments/<id>       내려받기
  DELETE /api/partners/attachments/<id>       보관함에서 삭제
협업 가이드 (3-3)
  POST   /api/partners/guide/generate         가이드 초안 (OpenAI · 고정 구성 Markdown)
  POST   /api/partners/guide/docx             Word 변환 {markdown, type, purpose, attach} → attach면 보관함 저장, 아니면 내려받기
협업 이력 (3-4)
  GET    /api/partners/history                (?partner_id= &kind= &status=)
  POST   /api/partners/history                수기 기록 추가 (또는 {restore} — 발송 후 저장 실패한 기록 다시 저장)
  PATCH  /api/partners/history/<id>           편집 (활동·상태·진행 상태·담당자·일시·메모·제목·본문)
  POST   /api/partners/history/delete         {ids:[…]} 선택 삭제
  POST   /api/partners/replies/check          Gmail 답장·반송 확인 (IMAP) → 상태·메모 갱신
  POST   /api/partners/history/<id>/reply-summary   회신 AI 요약 (한국어: 핵심·요청사항·분위기·다음 액션)
  POST   /api/partners/history/<id>/reply-draft     AI 영문 답장 초안 {points}
판매 실적 (3-5)
  GET    /api/partners/sales                  ?start=YYYY-MM-DD&end=YYYY-MM-DD — 🧪 더미 데이터
유튜브 크리에이터 추천 (youtube.py · 소개란 공개 이메일만 · 30일 안에 갱신, 못 하면 삭제)
  GET    /api/partners/youtube                목록 (?refresh=0이면 오래된 값 갱신 안 함)
  POST   /api/partners/youtube/search         {keywords:[…]} 추천 받기 (YouTube Data API)
  POST   /api/partners/youtube/delete         {ids:[…]} 삭제 (크리에이터 삭제 요청 시)
  GET    /api/partners/youtube/export.csv     CSV 내보내기 (이 표만)
  POST   /api/partners/youtube/import         CSV 가져오기 (multipart file — 내보내기 형식, 기준일 30일 이내만)
"""
import csv
import io
import json
import mimetypes
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Response, current_app, jsonify, request, send_file
from flask_login import current_user
from werkzeug.utils import secure_filename

from server import envkeys, worklog
from server.catalog import service as catalog
from server.core import ai as core_ai
from server.extensions import db
from server.models import iso, utcnow
from server.security import ApiError, clip, json_body
from . import guide_docx, importer, mailer, prompts, sales, youtube
from .importer import CATERING, INFLUENCER, TYPES
from .models import PartnerAttachment, PartnerContact, PartnerHistory, PartnerYoutubeCreator

bp = Blueprint("partners", __name__, url_prefix="/api/partners")

PROGRESS = ("컨택 중", "협의 중", "협업 중", "마케팅 진행 중", "협업 완료", "보류", "협업 중단")
ACTIVE = ("협의 중", "협업 중", "마케팅 진행 중")
MAIL_STATUSES = ("발송 성공", "발송 실패", "반송", "회신 받음", "회신 대기")
SENT_OK = ("발송 성공", "회신 받음", "회신 대기", "반송")
AWAITING = ("발송 성공", "회신 대기")
KINDS = ("자동", "수기", "샘플")
ALLOWED_EXT = (".pdf", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".zip", ".txt")
MB = 1024 * 1024
MAX_ATTACH_FILE = 25 * MB
MAX_ATTACH_TOTAL = 25 * MB      # Gmail 첨부 한도 (README) — 인코딩 후 실제 메일은 약 1.37배
ATT_ID = re.compile(r"^[0-9a-f]{32}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ───────── 도우미 ─────────

def _who():
    return current_user.name or current_user.email


def _tz():
    try:
        return ZoneInfo(current_app.config.get("TIMEZONE", "Asia/Seoul"))
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9))


def _local(dt):
    return dt.replace(tzinfo=timezone.utc).astimezone(_tz()) if dt else None


def _parse_at(value):
    """ISO 일시(Z·오프셋 포함) → UTC. 시간대가 없으면 서버 시간대(Asia/Seoul)로 봄."""
    s = str(value or "").strip()
    if not s:
        return utcnow()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("일시 형식이 올바르지 않습니다.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _ptype(value, default=None):
    v = str(value or "").strip().lower()
    if v in ("케이터링", "catering"):
        return CATERING
    if v in ("인플루언서", "influencer"):
        return INFLUENCER
    if default is not None:
        return default
    raise ApiError("구분은 케이터링 또는 인플루언서입니다.")


def _one_line(s):
    return re.sub(r"[\r\n\t]+", " ", str(s or "")).strip()


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError("번호가 올바르지 않습니다.")


def _display_name(filename):
    name = Path(str(filename or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f\"<>:|?*]", "", name).strip().strip(".") or "file"
    if len(name) > 150:
        stem, ext = Path(name).stem, Path(name).suffix[:10]
        name = stem[:150 - len(ext)] + ext
    return name


def _history_or_404(hid):
    rec = db.session.get(PartnerHistory, hid)
    if rec is None:
        raise ApiError("없는 이력입니다.", 404)
    return rec


def _regulation_db():
    # 통합 규제 DB가 정해지면 여기서 실제 접속 확인 (README 3-2). 지금은 주소가 있는지만 봄
    return bool((envkeys.get("REGULATION_DB_URL") or "").strip())


def _status():
    sender, _ = mailer.credentials()
    return {"regulationDb": _regulation_db(), "openai": core_ai.openai_ready(), "model": core_ai.openai_model(),
            "smtp": mailer.ready(), "sender": sender if mailer.ready() else ""}


# ───────── 상태 · 설정 ─────────

@bp.get("/status")
def status():
    return jsonify(_status())


@bp.get("/config")
def config():
    products = catalog.get_products()
    return jsonify(status=_status(), types=list(TYPES), subcategories=importer.subcategory_meta(),
                   prompts=prompts.DEFAULT_PROMPTS, mailSteps=prompts.MAIL_STEPS,
                   guidePurposes=prompts.GUIDE_PURPOSES, guideSections=prompts.GUIDE_SECTIONS,
                   productDesc=prompts.product_desc(catalog.get_brand(), products),
                   products=[{"id": p.get("id"), "name": p.get("name") or p.get("id"), "form": p.get("form") or ""}
                             for p in products],
                   attachments={"extensions": list(ALLOWED_EXT), "maxFileMB": MAX_ATTACH_FILE // MB,
                                "maxTotalMB": MAX_ATTACH_TOTAL // MB})


# ───────── 업체 ─────────

def _imports_meta():
    out = {}
    for t in TYPES:
        row = PartnerContact.query.filter_by(ptype=t).order_by(PartnerContact.imported_at.desc()).first()
        out[t] = {"count": PartnerContact.query.filter_by(ptype=t).count(), "file": row.source_file if row else "",
                  "at": iso(row.imported_at) if row else None, "by": row.imported_by if row else ""}
    return out


def _sync_bundled():
    """tabs/partners/data/contacts/의 기본 업체 파일을 필요할 때만 DB에 반영 (importer.sync_bundled)."""
    loaded = importer.sync_bundled()
    for t in loaded:
        worklog.add("partners", "수집", f"기본 업체 파일 자동 불러오기 ({t})", detail={"type": t})
    return loaded


def _haystack(c):
    return " ".join((c["name"], c["location"], c["category"], c["email"])).lower()


@bp.get("")
@bp.get("/contacts")
def contacts():
    _sync_bundled()
    rows = PartnerContact.query.all()
    rows.sort(key=lambda c: (c.ptype != CATERING, c.sort))
    items = [c.to_dict() for c in rows]
    types = [_ptype(t) for t in request.args.get("type", "").replace(";", ",").split(",") if t.strip()]
    tags = {t.strip() for t in request.args.get("tags", "").split(",") if t.strip()}
    q = request.args.get("q", "").strip().lower()
    shown = [c for c in items if (not types or c["type"] in types) and (not tags or tags & set(c["tags"]))
             and (not q or q in _haystack(c))]
    return jsonify(items=shown, total=len(items), counts={"total": len(shown), "withEmail": sum(1 for c in shown if c["email"])},
                   imports=_imports_meta(), subcategories=importer.subcategory_meta())


@bp.post("/import")
def import_contacts():
    uploads = []
    for field, ptype in (("catering", CATERING), ("influencer", INFLUENCER), ("file", None)):
        for f in request.files.getlist(field):
            if f and f.filename:
                uploads.append((ptype, f.filename, f.read(importer.MAX_FILE_BYTES + 1)))
    if not uploads:
        raise ApiError("가져올 파일을 고르세요 (케이터링 CSV · 인플루언서 XLSX).")
    if len(uploads) > 4:
        raise ApiError("한 번에 4개 파일까지 가져올 수 있습니다.")
    parsed = []
    for ptype, name, raw in uploads:
        shown = _display_name(name)
        try:
            t = ptype or importer.detect_type(name, raw)
            recs, warnings = importer.parse_file(t, name, raw)
        except importer.ImportFailed as e:
            raise ApiError(f"{shown}: {e}")
        parsed.append((t, shown, recs, warnings))
    if len({p[0] for p in parsed}) != len(parsed):
        raise ApiError("같은 구분의 파일을 두 개 올렸습니다. 구분별로 하나씩 올려 주세요.")
    results = []
    for t, shown, recs, warnings in parsed:   # 모두 읽은 뒤에 바꿈 (하나라도 실패하면 아무것도 안 바뀜)
        n = importer.replace_contacts(t, recs, shown, _who())
        with_email = sum(1 for r in recs if r["email"])
        results.append({"type": t, "file": shown, "count": n, "withEmail": with_email, "warnings": warnings})
        worklog.add("partners", "수집", f"업체 파일 가져오기 ({t} {n}곳)",
                    detail={"file": shown, "type": t, "count": n, "withEmail": with_email, "warnings": warnings})
    return jsonify(ok=True, results=results, imports=_imports_meta())


@bp.post("/example")
def load_example():
    loaded = []
    try:
        for t in TYPES:
            if PartnerContact.query.filter_by(ptype=t).count():
                continue   # 실제로 가져온 업체가 있으면 건드리지 않음
            importer.replace_contacts(t, importer.example_records(t), "예시 데이터 (가상 업체)", _who())
            loaded.append(t)
    except importer.ImportFailed as e:
        raise ApiError(str(e), 500)
    samples = _add_samples()
    if loaded or samples:
        worklog.add("partners", "기타", "예시 데이터 불러오기", detail={"types": loaded, "samples": samples})
    return jsonify(ok=True, loaded=loaded, samples=samples, imports=_imports_meta())


def _add_samples():
    """협업 이력 '샘플' 2건 (예시 업체용 · 실제 발송 아님). 이미 샘플이 있으면 추가하지 않음."""
    if PartnerHistory.query.filter_by(kind="샘플").count():
        return 0
    now = utcnow()
    made = 0
    a = db.session.get(PartnerContact, "CX01")
    if a is not None:
        db.session.add(PartnerHistory(
            partner_id=a.id, partner_name=a.name, partner_type=a.ptype, kind="샘플", activity="메일 발송",
            mail_status="회신 받음", progress="협의 중", owner="예시", at=now - timedelta(days=3), to_addr=a.email,
            subject="REBOOT x Sample Catering A — event sampling (예시)",
            body="Hi Sample Catering A team,\n\n(예시 기록 — 실제로 보낸 메일이 아닙니다.)\n\nREBOOT Partnerships Team",
            memo="[예시] 화면 확인용 샘플 기록입니다.", reply_from=f"Sample Catering A <{a.email}>",
            reply_subject="Re: REBOOT x Sample Catering A — event sampling (예시)",
            reply_body=("Hi REBOOT team,\n\nThanks for reaching out — this is an example reply for the dashboard demo. "
                        "We would be glad to talk about sampling at our November events. Could you share the product "
                        "details and your preferred dates?\n\nBest,\nSample Catering A"),
            reply_at=now - timedelta(days=2), created_by="예시"))
        made += 1
    b = db.session.get(PartnerContact, "IX01")
    if b is not None:
        db.session.add(PartnerHistory(
            partner_id=b.id, partner_name=b.name, partner_type=b.ptype, kind="샘플", activity="메일 발송",
            mail_status="발송 성공", progress="컨택 중", owner="예시", at=now - timedelta(days=1), to_addr=b.email,
            subject="REBOOT x Sample Creator Agency — creator seeding (예시)",
            body="Hi Sample Creator Agency team,\n\n(예시 기록 — 실제로 보낸 메일이 아닙니다.)\n\nREBOOT Partnerships Team",
            memo="[예시] 화면 확인용 샘플 기록입니다.", created_by="예시"))
        made += 1
    db.session.commit()
    return made


@bp.get("/template/<name>")
def template(name):
    if name == "catering.csv":
        return Response(importer.template_csv(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=reboot-partners-catering-template.csv"})
    if name == "influencer.xlsx":
        return send_file(io.BytesIO(importer.template_xlsx()), as_attachment=True,
                         download_name="reboot-partners-influencer-template.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    raise ApiError("없는 양식입니다.", 404)


def _partner_key(h):
    return h.partner_id or ("name:" + (h.partner_name or "").strip().lower())


@bp.get("/overview")
def overview():
    _sync_bundled()
    contacts_all = PartnerContact.query.all()
    hist = PartnerHistory.query.order_by(PartnerHistory.at.asc(), PartnerHistory.id.asc()).all()
    latest, counts, mail_latest = {}, {}, {}
    for h in hist:
        k = _partner_key(h)
        latest[k] = h
        counts[k] = counts.get(k, 0) + 1
        if h.mail_status:
            mail_latest[k] = h.mail_status
    real = [h for h in hist if h.kind != "샘플"]   # 상단 숫자는 샘플(예시) 기록을 빼고 셈
    real_keys = {_partner_key(h) for h in real}
    real_latest = {}
    for h in real:
        real_latest[_partner_key(h)] = h
    breakdown = {p: 0 for p in ACTIVE}
    for h in real_latest.values():
        if h.progress in breakdown:
            breakdown[h.progress] += 1
    replied = {_partner_key(h) for h in real if h.mail_status == "회신 받음"}
    sent = {_partner_key(h) for h in real if h.mail_status in SENT_OK}
    partners = []
    for k, h in latest.items():
        partners.append({"key": k, "id": h.partner_id, "name": h.partner_name, "type": h.partner_type,
                         "progress": h.progress, "mailStatus": mail_latest.get(k, ""), "lastAt": iso(h.at),
                         "lastActivity": h.activity, "count": counts[k], "sample": k not in real_keys})
    partners.sort(key=lambda p: p["lastAt"] or "", reverse=True)
    return jsonify(
        contacts={"total": len(contacts_all), "withEmail": sum(1 for c in contacts_all if c.email),
                  "byType": {t: sum(1 for c in contacts_all if c.ptype == t) for t in TYPES}},
        contacted=len(real_keys), active=sum(breakdown.values()), activeBreakdown=breakdown,
        replied=len(replied), sentPartners=len(sent),
        replyRate=round(len(replied) / len(sent) * 100, 1) if sent else None,
        awaiting=sum(1 for h in real if h.mail_status in AWAITING),
        bounced=sum(1 for h in real if h.mail_status == "반송"),
        failed=sum(1 for h in real if h.mail_status == "발송 실패"),
        partners=partners)


# ───────── 협업 메일 · 번역 ─────────

def _contact_facts(contact):
    if contact is None:
        return None
    return {"name": contact.name, "type": contact.ptype, "location": contact.location, "category": contact.category,
            "note": contact.note[:600], "website": contact.url}


@bp.post("/email/generate")
def email_generate():
    data = json_body()
    ptype = _ptype(data.get("type"), INFLUENCER)
    partner = data.get("partner") if isinstance(data.get("partner"), dict) else {}
    pid = clip(partner.get("id"), 20).strip()
    contact = db.session.get(PartnerContact, pid) if pid else None
    name = clip(partner.get("name"), 200).strip() or (contact.name if contact else "") or "your team"
    reply = None
    if data.get("reply_to") not in (None, "", 0):
        rec = _history_or_404(_int(data.get("reply_to")))
        reply = {"subject": rec.subject, "body": rec.body, "from": rec.reply_from, "reply": rec.reply_body}
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    system, user = prompts.mail_messages(
        ptype, clip(data.get("prompt"), 6000), prompts.product_desc(catalog.get_brand(), catalog.get_products()),
        clip(data.get("ideal"), 1500).strip(), name, _contact_facts(contact) or {"name": name, "type": ptype},
        clip(data.get("purpose"), 2000), clip(period.get("start"), 10), clip(period.get("end"), 10),
        clip(data.get("proposal"), 3000), clip(data.get("requests"), 3000), reply=reply)
    out = core_ai.openai_json(system, user, schema=prompts.MAIL_SCHEMA, schema_name="partner_email", max_tokens=1500,
                              part="partners", action="협업 답장 초안 (메일 화면)" if reply else "협업 제안 메일 생성")
    subject = _one_line(out.get("subject"))[:300]
    body = str(out.get("body") or "").strip()
    if not subject or not body:
        raise ApiError("AI가 제목·본문을 만들지 못했습니다. 다시 시도하세요.", 502)
    return jsonify(subject=subject, body=body, words=len(body.split()), model=core_ai.openai_model(), ai=True)


@bp.post("/translate")
def translate():
    data = json_body(max_bytes=400 * 1024)
    text = str(data.get("text") or "")
    to = data.get("to")
    if to not in ("ko", "en"):
        raise ApiError("번역 방향(to)은 ko 또는 en입니다.")
    if not text.strip():
        raise ApiError("번역할 내용이 없습니다.")
    if len(text) > 30000:
        raise ApiError("번역할 내용이 너무 깁니다 (30,000자까지).")
    out = core_ai.openai_json(prompts.translate_system(to), text, schema=prompts.TEXT_SCHEMA, schema_name="translation",
                              max_tokens=8000, timeout=120, part="partners",
                              action="번역 (영→한)" if to == "ko" else "번역 (한→영)")
    result = str(out.get("text") or "")
    if not result.strip():
        raise ApiError("번역 결과가 비어 있습니다. 다시 시도하세요.", 502)
    return jsonify(text=result, to=to, ai=True)


def _recipients(value):
    items = value if isinstance(value, list) else re.split(r"[,;\n]+", str(value or ""))
    out = []
    for it in items[:20]:
        s = str(it or "").strip()
        if not s:
            continue
        m = re.match(r"^(.*)<([^<>]+)>\s*$", s)
        addr = (m.group(2) if m else s).strip()
        if not EMAIL_RE.match(addr):
            raise ApiError(f"수신자 이메일 형식이 올바르지 않습니다: {s[:80]}")
        if addr.lower() not in (o.lower() for o in out):
            out.append(addr)
    if not out:
        raise ApiError("수신자 이메일을 입력하세요.")
    if len(out) > 5:
        raise ApiError("수신자는 5명까지 넣을 수 있습니다.")
    return out


def _attachments_for(ids):
    if not ids:
        return []
    if not isinstance(ids, list) or len(ids) > 20:
        raise ApiError("첨부파일 목록이 올바르지 않습니다.")
    rows = []
    for aid in ids:
        aid = str(aid)
        if not ATT_ID.match(aid):
            raise ApiError("첨부파일 목록이 올바르지 않습니다.")
        a = db.session.get(PartnerAttachment, aid)
        if a is None:
            raise ApiError("보관함에 없는 첨부파일이 있습니다. 목록을 새로고침하세요.", 404)
        if a not in rows:
            rows.append(a)
    total = sum(a.size for a in rows)
    if total > MAX_ATTACH_TOTAL:
        raise ApiError(f"첨부파일 합계가 {total / MB:.1f}MB입니다. Gmail 한도 25MB를 넘어 보낼 수 없습니다.")
    return rows


def _current_progress(partner_id):
    if not partner_id:
        return None
    row = (PartnerHistory.query.filter(PartnerHistory.partner_id == partner_id, PartnerHistory.kind != "샘플")
           .order_by(PartnerHistory.at.desc(), PartnerHistory.id.desc()).first())
    return row.progress if row else None


def _auto_progress(partner_id, reply):
    """메일 발송 → '컨택 중', 회신에 답장 → '협의 중' (README 3-4). 이미 더 나아간 단계면 그대로 둠."""
    cur = _current_progress(partner_id)
    if reply:
        return cur if cur in ("협업 중", "마케팅 진행 중", "협업 완료") else "협의 중"
    return cur if cur in ("협의 중", "협업 중", "마케팅 진행 중", "협업 완료") else "컨택 중"


@bp.post("/email/send")
def email_send():
    data = json_body()
    if data.get("confirm") is not True:
        raise ApiError("발송 전 '확인' 체크가 필요합니다.")
    owner = clip(data.get("owner"), 60).strip()
    if not owner:
        raise ApiError("담당자를 입력하세요.")
    recipients = _recipients(data.get("to"))
    subject = _one_line(clip(data.get("subject"), 300))
    body = clip(data.get("body"), 20000).strip()
    if not subject or not body:
        raise ApiError("제목과 본문을 입력하세요.")
    att_rows = _attachments_for(data.get("attachment_ids"))
    reply_of = None
    if data.get("reply_to") not in (None, "", 0):
        reply_of = _history_or_404(_int(data.get("reply_to")))
    if not mailer.ready():
        raise ApiError("Gmail이 연결되지 않았습니다. 설정 › 연결에서 GMAIL_ADDRESS와 GMAIL_APP_PASSWORD를 넣으면 발송할 수 있습니다.", 503)

    partner_id = clip(data.get("partner_id"), 20).strip() or (reply_of.partner_id if reply_of else "")
    contact = db.session.get(PartnerContact, partner_id) if partner_id else None
    partner_name = (clip(data.get("partner_name"), 200).strip() or (contact.name if contact else "")
                    or (reply_of.partner_name if reply_of else "") or recipients[0])
    partner_type = (contact.ptype if contact else "") or (reply_of.partner_type if reply_of else "") or \
        (_ptype(data.get("partner_type"), "") if data.get("partner_type") else "")
    in_reply_to, refs = "", ""
    if reply_of is not None:
        in_reply_to = reply_of.reply_message_id or reply_of.message_id
        chain = (reply_of.refs.split() if reply_of.refs else []) + [reply_of.message_id, reply_of.reply_message_id]
        refs = " ".join(dict.fromkeys(x for x in chain if x))

    paths = []
    for a in att_rows:
        p = _att_path(a)
        if not p.is_file():
            raise ApiError(f"첨부파일을 찾지 못했습니다: {a.filename}. 보관함에서 지우고 다시 올려 주세요.", 404)
        paths.append((a.filename, p))
    sender, password = mailer.credentials()
    msg, message_id = mailer.build_message(sender, recipients, subject, body, paths, in_reply_to, refs)
    status_, error = "발송 성공", ""
    try:
        mailer.send(msg, sender, password)
    except mailer.MailError as e:
        status_, error = "발송 실패", str(e)
    rec = PartnerHistory(
        partner_id=partner_id, partner_name=partner_name, partner_type=partner_type, kind="자동",
        activity="답장 발송" if reply_of else "메일 발송", mail_status=status_,
        progress=_auto_progress(partner_id, bool(reply_of)), owner=owner, at=utcnow(),
        memo=f"발송 실패: {error}" if error else "", to_addr=", ".join(recipients), sender=sender, subject=subject,
        body=body, message_id=message_id if status_ == "발송 성공" else "", in_reply_to=in_reply_to, refs=refs,
        attachments=json.dumps([a.filename for a in att_rows], ensure_ascii=False), error=error[:500],
        reply_of=reply_of.id if reply_of else None, created_by=_who())
    try:
        db.session.add(rec)
        if reply_of is not None and status_ == "발송 성공" and reply_of.progress not in ("협업 중", "마케팅 진행 중", "협업 완료"):
            reply_of.progress = "협의 중"
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("파트너 메일 이력 저장 실패")
        payload = {k: v for k, v in rec.to_dict().items() if k not in ("id", "createdAt", "updatedAt")}
        payload["at"] = iso(rec.at)
        return jsonify(error=("발송은 끝났지만 협업 이력 저장에 실패했습니다. '기록만 다시 저장'을 누르세요 (다시 발송하지 않음)."
                              if status_ == "발송 성공" else "발송에 실패했고 이력 저장도 실패했습니다."),
                       sent=status_ == "발송 성공", record=payload), 500
    worklog.add("partners", "승인 · 선정", f"협업 메일 {'답장 ' if reply_of else ''}발송 — {partner_name}",
                status="완료" if status_ == "발송 성공" else "실패", approval=owner,
                detail={"partner": partner_name, "subject": subject, "attachments": [a.filename for a in att_rows],
                        "error": error, "historyId": rec.id})
    return jsonify(ok=status_ == "발송 성공", error=error, history=rec.to_dict())


# ───────── 첨부파일 보관함 ─────────

def _att_dir():
    d = Path(current_app.config["DATA_DIR"]) / "uploads" / "partners" / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _att_path(a):
    base = _att_dir().resolve()
    p = (base / a.stored_name).resolve()
    if p.parent != base:   # 경로 벗어남 방지 (stored_name은 서버가 만든 이름이지만 한 번 더 확인)
        raise ApiError("없는 첨부파일입니다.", 404)
    return p


def _save_attachment(name, data, source):
    aid = uuid.uuid4().hex
    ext = Path(name).suffix.lower()
    stem = secure_filename(Path(name).stem)[:60] or "file"
    stored = f"{aid}_{stem}{ext}"
    (_att_dir() / stored).write_bytes(data)
    a = PartnerAttachment(id=aid, filename=name, stored_name=stored, size=len(data), source=source, created_by=_who())
    db.session.add(a)
    return a


def _attachment_or_404(aid):
    if not ATT_ID.match(str(aid)):
        raise ApiError("없는 첨부파일입니다.", 404)
    a = db.session.get(PartnerAttachment, aid)
    if a is None:
        raise ApiError("없는 첨부파일입니다.", 404)
    return a


@bp.get("/attachments")
def attachments_list():
    rows = PartnerAttachment.query.order_by(PartnerAttachment.created_at.desc()).all()
    return jsonify(items=[a.to_dict() for a in rows], totalBytes=sum(a.size for a in rows),
                   maxTotalMB=MAX_ATTACH_TOTAL // MB, extensions=list(ALLOWED_EXT))


@bp.post("/attachments")
def attachments_upload():
    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        raise ApiError("올릴 파일을 고르세요.")
    if len(files) > 10:
        raise ApiError("한 번에 10개까지 올릴 수 있습니다.")
    ready = []
    for f in files:   # 모두 확인한 뒤 저장
        name = _display_name(f.filename)
        if Path(name).suffix.lower() not in ALLOWED_EXT:
            raise ApiError(f"올릴 수 없는 형식입니다: {name} (PDF · CSV · Word · Excel · PowerPoint · PNG · JPG · ZIP · TXT)")
        data = f.read(MAX_ATTACH_FILE + 1)
        if len(data) > MAX_ATTACH_FILE:
            raise ApiError(f"파일이 너무 큽니다: {name} (25MB까지).")
        if not data:
            raise ApiError(f"빈 파일입니다: {name}")
        ready.append((name, data))
    saved = [_save_attachment(name, data, "upload") for name, data in ready]
    db.session.commit()
    return jsonify(items=[a.to_dict() for a in saved]), 201


@bp.get("/attachments/<aid>")
def attachments_download(aid):
    a = _attachment_or_404(aid)
    p = _att_path(a)
    if not p.is_file():
        raise ApiError("파일이 보관함에 없습니다.", 404)
    # 메모리로 읽어 보냄 (25MB 이하) — 내려받는 중에도 Windows에서 파일이 잠기지 않게
    return send_file(io.BytesIO(p.read_bytes()), as_attachment=True, download_name=a.filename,
                     mimetype=mimetypes.guess_type(a.filename)[0] or "application/octet-stream")


@bp.delete("/attachments/<aid>")
def attachments_delete(aid):
    a = _attachment_or_404(aid)
    try:
        _att_path(a).unlink(missing_ok=True)
    except OSError:
        raise ApiError("파일을 사용 중이라 지우지 못했습니다. 잠시 뒤 다시 시도하세요.", 409)
    db.session.delete(a)
    db.session.commit()
    return jsonify(ok=True)


# ───────── 협업 가이드 ─────────

@bp.post("/guide/generate")
def guide_generate():
    data = json_body()
    ptype = _ptype(data.get("type"), INFLUENCER)
    purpose = clip(data.get("purpose"), 200).strip() or prompts.GUIDE_PURPOSES[ptype][0]
    banned = []
    for b in (data.get("banned") if isinstance(data.get("banned"), list) else [])[:60]:
        if isinstance(b, dict) and str(b.get("term") or "").strip():
            banned.append({"dont": clip(b.get("term"), 80), "do_instead": clip(b.get("alt"), 200)})
    sender, _ = mailer.credentials()
    ids = [str(x) for x in (data.get("product_ids") if isinstance(data.get("product_ids"), list) else [])[:20]]
    facts = catalog.product_facts(ids or None)
    if not facts["products"]:   # 고른 제품이 목록에 없으면 전체
        facts = catalog.product_facts()
    system, user = prompts.guide_messages(
        ptype, purpose, clip(data.get("schedule"), 2000), clip(data.get("must_include"), 3000),
        clip(data.get("deliverables"), 3000), banned,
        {"brand": {k: facts["brand"].get(k) for k in ("name", "slogan", "category", "market")},
         "products": [{k: p.get(k) for k in ("internal_name", "form", "flavors", "pack")} for p in facts["products"]]},
        sender, catalog._today().isoformat())
    out = core_ai.openai_json(system, user, schema=prompts.GUIDE_SCHEMA, schema_name="partner_guide", max_tokens=6000,
                              timeout=150, part="partners", action=f"협업 가이드 생성 ({ptype})")
    md = str(out.get("markdown") or "").strip()
    title = _one_line(out.get("title"))[:120] or f"REBOOT {ptype} 협업 가이드"
    if not md:
        raise ApiError("AI가 가이드를 만들지 못했습니다. 다시 시도하세요.", 502)
    if not md.lstrip().startswith("# "):
        md = f"# {title}\n\n{md}"
    return jsonify(title=title, markdown=md, ai=True, model=core_ai.openai_model())


@bp.post("/guide/docx")
def guide_docx_route():
    data = json_body(max_bytes=512 * 1024)
    md = clip(data.get("markdown"), 60000).strip()
    if not md:
        raise ApiError("가이드 내용이 없습니다. 먼저 초안을 만들거나 원문을 입력하세요.")
    ptype = _ptype(data.get("type"), INFLUENCER)
    purpose = _one_line(clip(data.get("purpose"), 120))
    today = catalog._today()
    blob = guide_docx.build(md, title="", subtitle=f"{ptype} 협업 · {purpose}" if purpose else f"{ptype} 협업",
                            date_text=today.isoformat())
    if data.get("attach"):
        name = f"REBOOT_{ptype}_협업가이드_{today:%Y%m%d}.docx"
        # 같은 날 같은 유형의 가이드를 다시 첨부하면 예전 파일을 바꿈 (보관함에 같은 이름이 두 번 체크되지 않게)
        replaced = []
        for old in PartnerAttachment.query.filter_by(source="guide", filename=name).all():
            try:
                _att_path(old).unlink(missing_ok=True)
            except OSError:
                continue   # 사용 중이면 남겨 둠
            replaced.append(old.id)
            db.session.delete(old)
        a = _save_attachment(name, blob, "guide")
        db.session.commit()
        worklog.add("partners", "기타", f"협업 가이드 Word 첨부 ({ptype})", detail={"file": a.filename, "size": a.size, "replaced": len(replaced)})
        return jsonify(attachment=a.to_dict(), replaced=replaced), 201
    return send_file(io.BytesIO(blob), as_attachment=True, download_name=f"reboot-partners-guide-{today:%Y%m%d}.docx",
                     mimetype=DOCX_MIME)


# ───────── 협업 이력 ─────────

@bp.get("/history")
def history_list():
    q = PartnerHistory.query
    if request.args.get("partner_id"):
        q = q.filter(PartnerHistory.partner_id == request.args["partner_id"][:20])
    if request.args.get("kind") in KINDS:
        q = q.filter(PartnerHistory.kind == request.args["kind"])
    if request.args.get("status") in MAIL_STATUSES:
        q = q.filter(PartnerHistory.mail_status == request.args["status"])
    rows = q.order_by(PartnerHistory.at.desc(), PartnerHistory.id.desc()).limit(2000).all()
    awaiting = PartnerHistory.query.filter(PartnerHistory.kind != "샘플", PartnerHistory.mail_status.in_(AWAITING)).count()
    return jsonify(items=[r.to_dict() for r in rows], awaiting=awaiting)


def _check_choice(value, choices, label, allow_empty=False):
    v = str(value or "").strip()
    if allow_empty and v == "":
        return ""
    if v not in choices:
        raise ApiError(f"{label} 값이 올바르지 않습니다: {v[:20]}")
    return v


@bp.post("/history")
def history_add():
    data = json_body()
    if isinstance(data.get("restore"), dict):
        return _history_restore(data["restore"])
    pid = clip(data.get("partner_id"), 20).strip()
    contact = db.session.get(PartnerContact, pid) if pid else None
    name = clip(data.get("partner_name"), 200).strip() or (contact.name if contact else "")
    if not name:
        raise ApiError("업체를 고르거나 업체 이름을 입력하세요.")
    rec = PartnerHistory(
        partner_id=pid, partner_name=name,
        partner_type=contact.ptype if contact else (_ptype(data.get("partner_type"), "") if data.get("partner_type") else ""),
        kind="수기", activity=clip(data.get("activity"), 40).strip() or "메모",
        mail_status=_check_choice(data.get("mail_status"), MAIL_STATUSES, "메일 상태", allow_empty=True),
        progress=_check_choice(data.get("progress") or "컨택 중", PROGRESS, "진행 상태"),
        owner=clip(data.get("owner"), 60).strip() or _who(), at=_parse_at(data.get("at")),
        memo=clip(data.get("memo"), 4000).strip(), created_by=_who())
    db.session.add(rec)
    db.session.commit()
    worklog.add("partners", "기타", f"협업 이력 수기 추가 — {name}", detail={"activity": rec.activity, "progress": rec.progress})
    return jsonify(rec.to_dict()), 201


def _history_restore(r):
    """발송은 됐는데 이력 저장이 실패한 기록을 다시 저장 (메일은 다시 보내지 않음)."""
    rec = PartnerHistory(
        partner_id=clip(r.get("partnerId"), 20), partner_name=clip(r.get("partnerName"), 200) or "(이름 없음)",
        partner_type=clip(r.get("partnerType"), 10), kind="자동", activity=clip(r.get("activity"), 40) or "메일 발송",
        mail_status=_check_choice(r.get("mailStatus"), MAIL_STATUSES, "메일 상태", allow_empty=True),
        progress=_check_choice(r.get("progress") or "컨택 중", PROGRESS, "진행 상태"), owner=clip(r.get("owner"), 60),
        at=_parse_at(r.get("at")), memo=clip(r.get("memo"), 4000), to_addr=clip(r.get("to"), 600),
        sender=clip(r.get("sender"), 255), subject=clip(r.get("subject"), 300), body=clip(r.get("body"), 20000),
        message_id=clip(r.get("messageId"), 300),
        attachments=json.dumps([clip(x, 200) for x in (r.get("attachments") or [])][:20], ensure_ascii=False),
        error=clip(r.get("error"), 500), created_by=_who())
    db.session.add(rec)
    db.session.commit()
    worklog.add("partners", "기타", f"발송 기록 다시 저장 — {rec.partner_name}")
    return jsonify(rec.to_dict()), 201


@bp.patch("/history/<int:hid>")
def history_edit(hid):
    rec = _history_or_404(hid)
    data = json_body()
    if "activity" in data:
        rec.activity = clip(data["activity"], 40).strip() or rec.activity
    if "mail_status" in data:
        rec.mail_status = _check_choice(data["mail_status"], MAIL_STATUSES, "메일 상태", allow_empty=True)
    if "progress" in data:
        rec.progress = _check_choice(data["progress"], PROGRESS, "진행 상태")
    if "owner" in data:
        rec.owner = clip(data["owner"], 60).strip()
    if "at" in data:
        rec.at = _parse_at(data["at"])
    if "memo" in data:
        rec.memo = clip(data["memo"], 4000)
    if "subject" in data:
        rec.subject = _one_line(clip(data["subject"], 300))
    if "body" in data:
        rec.body = clip(data["body"], 20000)
    db.session.commit()
    worklog.add("partners", "기타", f"협업 이력 편집 — {rec.partner_name}", detail={"id": rec.id, "fields": sorted(data)[:12]})
    return jsonify(rec.to_dict())


@bp.post("/history/delete")
def history_delete():
    data = json_body()
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise ApiError("삭제할 기록을 고르세요.")
    ids = sorted({_int(i) for i in ids})
    n = PartnerHistory.query.filter(PartnerHistory.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    worklog.add("partners", "기타", f"협업 이력 삭제 {n}건", detail={"ids": ids[:50]})
    return jsonify(ok=True, deleted=n)


@bp.post("/replies/check")
def replies_check():
    if not mailer.ready():
        raise ApiError("Gmail이 연결되지 않았습니다. 설정 › 연결에서 GMAIL_ADDRESS와 GMAIL_APP_PASSWORD를 넣으면 답장을 확인할 수 있습니다.", 503)
    pending = PartnerHistory.query.filter(PartnerHistory.kind != "샘플", PartnerHistory.mail_status.in_(AWAITING)).all()
    records = [{"id": r.id, "message_id": r.message_id, "to": r.to_addr, "subject": r.subject, "at": r.at}
               for r in pending if r.message_id or (r.to_addr and r.subject)]
    if not records:
        return jsonify(checked=0, replies=0, bounces=0, items=[])
    sender, password = mailer.credentials()
    try:
        own_ids = {m for (m,) in db.session.query(PartnerHistory.message_id).filter(PartnerHistory.message_id != "")}
        found = mailer.check(records, sender, password, own_ids)
    except mailer.MailError as e:
        worklog.add("partners", "수집", "Gmail 답장 확인", status="실패", detail={"error": str(e)})
        raise ApiError(str(e), 502)
    by_id = {r.id: r for r in pending}
    replies = bounces = 0
    updated = []
    for hit in found:
        rec = by_id.get(hit["id"])
        if rec is None:
            continue
        when = _local(hit["at"]).strftime("%m-%d %H:%M") if hit.get("at") else ""
        if hit["status"] == "회신 받음":
            replies += 1
            rec.mail_status = "회신 받음"
            rec.reply_from, rec.reply_subject = hit["from"], hit["subject"]
            rec.reply_body, rec.reply_at = hit["body"], hit["at"]
            rec.reply_message_id, rec.reply_estimated = hit["message_id"], bool(hit["estimated"])
            rec.reply_summary = ""
            line = f"[회신 {when}{' · 추정' if hit['estimated'] else ''}] {hit['first']}".strip()
        else:
            bounces += 1
            rec.mail_status = "반송"
            line = f"[반송 {when}] 실패 수신자: {', '.join(hit.get('failed') or []) or '확인 필요'}"
        rec.memo = (rec.memo + "\n" if rec.memo else "") + line
        updated.append(rec)
    db.session.commit()
    worklog.add("partners", "수집", "Gmail 답장 확인", detail={"checked": len(records), "replies": replies, "bounces": bounces})
    return jsonify(checked=len(records), replies=replies, bounces=bounces, items=[r.to_dict() for r in updated])


@bp.post("/history/<int:hid>/reply-summary")
def reply_summary(hid):
    rec = _history_or_404(hid)
    if not rec.reply_body.strip():
        raise ApiError("요약할 회신 내용이 없습니다.")
    data = request.get_json(silent=True) or {}
    if rec.reply_summary and not data.get("force"):
        cached = rec.to_dict()["reply"]["summary"]
        if cached:
            return jsonify(summary=cached, cached=True, ai=True)
    out = core_ai.openai_json(prompts.SUMMARY_SYSTEM,
                              prompts.summary_user(rec.subject, rec.body, rec.reply_from, rec.reply_subject, rec.reply_body),
                              schema=prompts.SUMMARY_SCHEMA, schema_name="reply_summary", max_tokens=800,
                              part="partners", action="회신 요약")
    summary = {k: clip(out.get(k), 600).strip() for k in ("core", "requests", "mood", "next_action")}
    rec.reply_summary = json.dumps(summary, ensure_ascii=False)
    db.session.commit()
    return jsonify(summary=summary, cached=False, ai=True)


def _re_subject(rec):
    base = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", rec.reply_subject or rec.subject or "", flags=re.I).strip()
    return f"Re: {base}" if base else "Re:"


@bp.post("/history/<int:hid>/reply-draft")
def reply_draft(hid):
    rec = _history_or_404(hid)
    data = json_body()
    if not rec.reply_body.strip():
        raise ApiError("답장할 회신 내용이 없습니다.")
    system, user = prompts.reply_draft_messages(
        rec.subject, rec.body, rec.partner_name, rec.reply_from, rec.reply_body, clip(data.get("points"), 4000),
        prompts.product_desc(catalog.get_brand(), catalog.get_products()))
    out = core_ai.openai_json(system, user, schema=prompts.MAIL_SCHEMA, schema_name="partner_reply", max_tokens=1200,
                              part="partners", action="AI 답장 초안")
    body = str(out.get("body") or "").strip()
    if not body:
        raise ApiError("AI가 답장을 만들지 못했습니다. 다시 시도하세요.", 502)
    return jsonify(subject=_re_subject(rec), body=body, ai=True)


# ───────── 판매 실적 (더미) ─────────

def _date_arg(name):
    v = request.args.get(name, "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ApiError("날짜 형식이 올바르지 않습니다 (YYYY-MM-DD).")


@bp.get("/sales")
def sales_report():
    _sync_bundled()
    rows = PartnerContact.query.filter_by(ptype=INFLUENCER).order_by(PartnerContact.sort).all()
    names = [c.name for c in rows if not c.is_example]
    return jsonify(sales.report(names, catalog._today(), _date_arg("start"), _date_arg("end")))


# ───────── 유튜브 크리에이터 추천 (youtube.py) ─────────

YT_CSV_HEADER = ["channel_id", "크리에이터", "핸들", "국가", "구독자", "평균 조회수(최근 10개)", "영상 수", "총 조회수",
                 "이메일(소개란 공개)", "이메일 가져온 날짜", "매칭 키워드", "기준일", "채널 URL", "프로필 사진"]
YT_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
YT_CSV_MAX = 2 * MB


def _yt_error(e):
    return ApiError(str(e), e.status if e.status in (400, 409, 429, 503) else 502)


@bp.before_request
def _yt_schema():
    if request.path.startswith("/api/partners/youtube"):
        youtube.ensure_schema()


@bp.get("/youtube")
def youtube_list():
    info = {"refreshed": 0, "removed": 0, "warning": ""}
    if request.args.get("refresh", "1") != "0":
        info = youtube.refresh()
    rows = PartnerYoutubeCreator.query.all()
    rows.sort(key=lambda r: -(r.subscribers or 0))
    return jsonify(items=[r.to_dict() for r in rows], ready=youtube.ready(), keywords=youtube.KEYWORDS,
                   staleDays=youtube.STALE_DAYS, maxDays=youtube.MAX_DAYS, **info)


@bp.post("/youtube/search")
def youtube_search():
    kws = json_body().get("keywords") or []
    if not isinstance(kws, list):
        raise ApiError("키워드 목록이 올바르지 않습니다.")
    if not youtube.ready():
        raise ApiError("YouTube 키가 없습니다. 설정 › 연결에서 YOUTUBE_API_KEY를 넣어 주세요.", 503)
    try:
        out = youtube.search([clip(k, 60) for k in kws], _who())
    except youtube.YoutubeError as e:
        raise _yt_error(e)
    worklog.add("partners", "수집", f"유튜브 크리에이터 추천 ({out['found']}개 채널, 새로 {out['added']}개)",
                detail={"keywords": out["keywords"]})
    return jsonify(out)


@bp.post("/youtube/delete")
def youtube_delete():
    ids = json_body().get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise ApiError("지울 채널을 고르세요.")
    n = 0
    for cid in ids[:500]:
        row = db.session.get(PartnerYoutubeCreator, str(cid))
        if row is not None:
            db.session.delete(row)
            n += 1
    db.session.commit()
    worklog.add("partners", "기타", f"유튜브 크리에이터 {n}개 삭제", detail={"count": n})
    return jsonify(deleted=n)


def _num_cell(v):
    return "" if v is None else v


@bp.get("/youtube/export.csv")
def youtube_export():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(YT_CSV_HEADER)
    rows = PartnerYoutubeCreator.query.all()
    rows.sort(key=lambda r: -(r.subscribers or 0))
    for r in rows:
        w.writerow([r.id, r.title, r.handle, r.country, _num_cell(r.subscribers), _num_cell(r.avg_views),
                    _num_cell(r.video_count), _num_cell(r.total_views), r.email,
                    _local(r.email_at).date().isoformat() if r.email_at else "", " | ".join(r.keyword_list()),
                    _local(r.stats_at).date().isoformat(), r.url, r.thumb])
    today = _local(utcnow()).date().isoformat()
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=reboot-youtube-creators-{today}.csv"})


def _csv_int(v):
    s = re.sub(r"[,\s]", "", str(v or ""))
    return int(s) if s.isdigit() else None


def _csv_date(v):
    try:
        d = date.fromisoformat(str(v or "").strip()[:10])
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=_tz()).astimezone(timezone.utc).replace(tzinfo=None)


@bp.post("/youtube/import")
def youtube_import():
    f = request.files.get("file")
    if not f or not f.filename:
        raise ApiError("가져올 CSV 파일을 고르세요.")
    raw = f.read(YT_CSV_MAX + 1)
    if len(raw) > YT_CSV_MAX:
        raise ApiError("CSV는 2MB까지 가져올 수 있습니다.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if "channel_id" not in (reader.fieldnames or []):
        raise ApiError("유튜브 크리에이터 CSV 내보내기 형식이 아닙니다 (channel_id 열 없음).")
    now = utcnow()
    oldest = now - timedelta(days=youtube.MAX_DAYS)
    added = updated = skipped = 0
    for rec in reader:
        cid = str(rec.get("channel_id") or "").strip()
        stats_at = _csv_date(rec.get("기준일"))
        # 형식이 틀렸거나 기준일이 보관 기간(30일)보다 오래된 값, 미국 채널이 아닌 줄은 건너뜀
        if (not YT_ID.match(cid) or stats_at is None or stats_at < oldest or stats_at > now + timedelta(days=1)
                or str(rec.get("국가") or "").strip().upper() != youtube.COUNTRY
                or (_csv_int(rec.get("구독자")) or 0) <= youtube.MIN_SUBSCRIBERS):
            skipped += 1
            continue
        row = db.session.get(PartnerYoutubeCreator, cid)
        if row is not None and row.stats_at >= stats_at:
            skipped += 1        # 이미 더 최근 값이 있음
            continue
        if row is None:
            row = PartnerYoutubeCreator(id=cid, created_by=_who())
            db.session.add(row)
            added += 1
        else:
            updated += 1
        email = str(rec.get("이메일(소개란 공개)") or "").strip().lower()
        row.title = clip(rec.get("크리에이터"), 200)
        row.handle = clip(rec.get("핸들"), 100)
        row.country = clip(rec.get("국가"), 10)
        thumb = str(rec.get("프로필 사진") or "").strip()
        row.thumb = thumb[:500] if thumb.startswith("https://") else ""
        row.subscribers = _csv_int(rec.get("구독자"))
        row.avg_views = _csv_int(rec.get("평균 조회수(최근 10개)"))
        row.video_count = _csv_int(rec.get("영상 수"))
        row.total_views = _csv_int(rec.get("총 조회수"))
        row.email = email if EMAIL_RE.match(email) else ""
        row.email_at = (_csv_date(rec.get("이메일 가져온 날짜")) or stats_at) if row.email else None
        row.set_keywords([k.strip() for k in str(rec.get("매칭 키워드") or "").split("|") if k.strip()])
        row.stats_at = stats_at
    db.session.commit()
    worklog.add("partners", "수집", f"유튜브 크리에이터 CSV 가져오기 (새로 {added} · 갱신 {updated} · 건너뜀 {skipped})",
                detail={"file": _display_name(f.filename)})
    return jsonify(added=added, updated=updated, skipped=skipped)
