"""내보내기 ZIP (README 9장 · A21).

선택 버전(또는 전체)의 결과 · 생성 시점 설정 · 제품 근거 · 검토 요약을 담고, 미검토·임시·시안 상태를 파일에 남깁니다.
최종본 지정은 법적 승인이 아니므로 어떤 파일에도 '승인본'이라고 쓰지 않습니다. 키·비밀값은 넣지 않습니다.
"""
import io
import json
import zipfile

from server.core.templating import local_now
from server.models import iso
from . import options as O
from . import brandkit, repository
from .media import upload_dir

NOTICE = """REBOOT 콘텐츠 제작 — 내보내기 묶음

이 파일들은 작업 중인 초안입니다. 승인본이 아닙니다.
- 각 버전 파일 머리말에 상태(최종본 지정 여부 · 검토 상태 · 변경 사항 미반영 여부)를 적었습니다.
- '최종본'은 팀이 고른 버전이라는 뜻일 뿐 법적 승인이나 사실 확인 완료를 뜻하지 않습니다.
- 검토 표시: 키워드 확인(서버 규칙) · 컴플라이언스(1차 규칙 판정, 외부 검토)는 서로 다릅니다.
  기록이 없으면 '미검토'입니다. 어떤 결과도 법적 승인이 아닙니다.
- 제품 정보는 브랜드 키트(2026-10-24) 기준입니다. Balance의 Zero Sugar는 표기 조건 충족 가정, 성분 함량·젤리 봉지 안 개수는 미제공입니다 (facts.json 참고).
- 이미지는 AI 시안입니다. 최종 패키지·캐릭터 사진을 참고해 만들지만 라벨 글자·모양은 게시 전 확인이 필요하며, 헤드라인·슬로건 문구는 이미지에 그리지 않고 텍스트 레이어로 따로 둡니다.
- 숏폼 구성안은 실제 영상이 아닙니다.
"""


def _review_line(review_):
    kw = (review_ or {}).get("keyword")
    cmp_ = (review_ or {}).get("compliance")
    kw_s = {"error": "수정 필요", "warn": "확인 필요", "clear": "키워드 미탐지"}.get((kw or {}).get("status"), "미검토")
    if not cmp_:
        cmp_s = "미검토"
    elif cmp_.get("verdict") in ("UNAVAILABLE", "ERROR"):
        cmp_s = "미연동" if cmp_["verdict"] == "UNAVAILABLE" else "미검토 (검사 오류)"
    else:
        cmp_s = f"{cmp_.get('label') or cmp_.get('verdict')} (규칙 판정 · 승인 아님)"
    return kw_s, cmp_s


def _version_md(v, vd, campaign, seq_by_id):
    kw_s, cmp_s = _review_line(v.review)
    head = [
        f"# {v.label} · v{v.seq} — {O.KIND_LABELS.get(v.kind, v.kind)}",
        "",
        f"- 캠페인: {campaign.title} (#{campaign.id})",
        f"- 상태: {'최종본으로 지정됨 (승인 아님)' if vd['isFinal'] else '초안 (미승인)'}",
        f"- 만든 방식: {'AI 생성' if v.source == 'ai' else '직접 작성'} · {v.created_by} · {iso(v.created_at)}",
        f"- 부모 버전: {('v' + str(seq_by_id[v.parent_id])) if v.parent_id in seq_by_id else '없음'}",
        f"- 제품 기준 버전(facts_version): {v.facts_version or '기록 없음'}",
        f"- 변경 사항 미반영: {'예 — ' + ', '.join(vd['staleFields']) if vd['stale'] else '아니요'}",
        f"- 키워드 확인: {kw_s} · 컴플라이언스: {cmp_s}",
    ]
    if v.kind == "script":
        head.append("- 숏폼 구성안 · 실제 영상 아님")
    if v.note:
        head.append(f"- 메모: {v.note}")
    if v.approach:
        head.append(f"- 접근: {v.approach}")
    if v.needs_check:
        head.append("- 확인 필요: " + " / ".join(v.needs_check))
    kw = (v.review or {}).get("keyword") or {}
    if kw.get("items"):
        head.append("- 키워드 확인 항목: " + " / ".join(f"{i['label']} ({', '.join(i['terms'])})" for i in kw["items"]))
    img = (v.snapshot or {}).get("image")
    if img:
        head.append(f"- 이미지 모델: {img.get('model')} · 크기 {img.get('size')} · {'편집' if img.get('mode') == 'edit' else '생성'}")
        if img.get("image_text"):
            head.append("- 이미지에 그린 문구 (철자는 게시 전 확인): " + " / ".join(t["text"] for t in img["image_text"]))
        elif img.get("text_layers"):
            head.append("- 텍스트 레이어(이미지 위에 따로 얹을 문구): " + " / ".join(t["text"] for t in img["text_layers"]))
    body = v.body or ""
    return "\n".join(head) + "\n\n---\n\n" + body + "\n"


def build_zip(c, only_version_id=None):
    sheet = repository.sheet_of(c)
    cur = repository.snapshot_core(sheet, c.decisions)
    from .models import ContentVersion
    all_versions = ContentVersion.query.filter_by(campaign_id=c.id).order_by(ContentVersion.id).all()
    seq_by_id = {v.id: v.seq for v in all_versions}
    versions = [v for v in all_versions if only_version_id is None or v.id == only_version_id]
    facts = brandkit.product_facts(sheet["product_ids"])
    labels = repository.labels_of(sheet)
    now = local_now()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_README.txt", NOTICE)
        lines = [
            f"# {c.title}", "",
            f"- 캠페인 상태: {c.status} · 리비전 {c.revision}",
            f"- 내보낸 시각: {now:%Y-%m-%d %H:%M} (Asia/Seoul)",
            f"- 제품 기준 버전(facts_version): {facts.get('facts_version')}",
            f"- 묶음 상태: 초안 · 미승인 (최종본 지정 ≠ 승인)", "",
            "## 기본 정보 시트", "",
            f"- 콘텐츠: {labels['contentType']}", f"- 게시 채널: {', '.join(labels['channels'])}",
            f"- 제작 대상: {labels['target']}", f"- 목적: {sheet['goal'] or '미정'}",
            f"- 추가 요청: {sheet['extra_request'] or '없음'}", "",
            "## 현재 결정 사항", "", "| 항목 | 값 | 상태 |", "| --- | --- | --- |",
        ]
        for k, d in c.decisions.items():
            st = {"confirmed": "확정", "proposed": "제안"}.get(d.get("status"), "미정")
            lines.append(f"| {O.DECISION_LABELS.get(k, k)} | {str(d.get('value') or '').replace('|', '/')} | {st} |")
        if not c.decisions:
            lines.append("| (없음) | | |")
        lines += ["", "## 버전", "", "| 버전 | 종류 | 상태 | 키워드 확인 | 컴플라이언스 | 미반영 |", "| --- | --- | --- | --- | --- | --- |"]
        index = []
        for v in versions:
            vd = repository.version_dict(v, cur, c.final_version_id, c.selected_version_id)
            kw_s, cmp_s = _review_line(v.review)
            lines.append(f"| {v.label} · v{v.seq} | {O.KIND_LABELS.get(v.kind, v.kind)} | {'최종본(승인 아님)' if vd['isFinal'] else '초안'} "
                         f"| {kw_s} | {cmp_s} | {'예' if vd['stale'] else '아니요'} |")
            stem = f"versions/v{v.seq:02d}-{_safe(v.label)}-{v.kind}"
            z.writestr(stem + ".md", _version_md(v, vd, c, seq_by_id))
            if v.image_file:
                path = upload_dir() / v.image_file
                if path.is_file():
                    z.write(path, stem + path.suffix)
                else:
                    z.writestr(stem + "-파일없음.txt", "결과 파일을 찾지 못했습니다 (서버 저장소 확인).")
            index.append({"id": v.id, "seq": v.seq, "label": v.label, "kind": v.kind, "final": vd["isFinal"],
                          "stale": vd["stale"], "source": v.source, "facts_version": v.facts_version,
                          "settings": (v.snapshot or {}).get("settings"), "model": (v.snapshot or {}).get("model"),
                          "review": v.review})
        if not versions:
            lines.append("| (버전 없음) | | | | | |")
        z.writestr("campaign.md", "\n".join(lines) + "\n")
        z.writestr("facts.json", json.dumps(facts, ensure_ascii=False, indent=2, default=str))
        z.writestr("versions.json", json.dumps(index, ensure_ascii=False, indent=2, default=str))
        z.writestr("settings.json", json.dumps({"settings": repository.settings_of(c), "sheet": sheet,
                                                "decisions": c.decisions}, ensure_ascii=False, indent=2, default=str))
    return buf.getvalue(), f"reboot-content-{c.id}-{now:%Y%m%d}.zip"


def _safe(label):
    out = "".join(ch for ch in str(label) if ch.isalnum() or ch in "-_")
    return out or "v"
