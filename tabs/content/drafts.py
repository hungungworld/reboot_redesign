"""텍스트 초안 · 부분 수정 (content_draft_v2 + 작업별 템플릿 + 근거 리마인더).

- 새 초안: 제안 수(기본 3)만큼 서로 다른 안 (기획서는 1개)
- 수정: 기준 버전 하나만 새 버전으로. 카드뉴스 '2장만' · 숏폼 '장면 3만'은 서버가 다른 장·장면을 원문 그대로 보존
- 키워드 확인에서 '수정 필요'가 나오면 설정의 자동 재작성 횟수(기본 1)만큼 다시 쓰게 함 (텍스트만 · 유료 미디어 아님)
"""
import re
import time

from server.core.ai import AIError
from . import options as O
from . import assets, brandkit, prompts, providers, review, schemas

ASSET_KINDS = ("copy", "cardnews", "script")  # 장·장면·이미지에 쓸 자산 ID를 적는 결과물
SLIDE_HEAD_RE = re.compile(r"^#{2,3}\s*S(\d{1,2})\b.*$", re.M)
SCENE_ROW_RE = re.compile(r"^\|\s*SC\s*(\d{1,2})\s*\|.*$", re.M | re.I)


def _clip(v, n):
    return str(v if v is not None else "").strip()[:n]


def sanitize(out, n):
    items = []
    for d in (out or {}).get("drafts") or []:
        if not isinstance(d, dict):
            continue
        body = _clip(d.get("body"), 20000)
        if not body:
            continue
        claims = []
        for c in d.get("claims") or []:
            if isinstance(c, dict):
                claims.append({"text": _clip(c.get("text"), 300), "entity_id": _clip(c.get("entity_id"), 40),
                               "fact_id": _clip(c.get("fact_id"), 40)})
        items.append({"approach": _clip(d.get("approach"), 300), "title": _clip(d.get("title"), 200), "body": body,
                      "claims": claims[:20],
                      "needs_check": [_clip(x, 200) for x in d.get("needs_check") or [] if isinstance(x, str) and x.strip()][:12]})
        if len(items) >= n:
            break
    return items


# ───────── 부분 수정 보존 ─────────

def split_slides(body):
    """'## S1 · …' 제목 기준으로 [(번호, 블록)] (+ 앞부분)."""
    heads = list(SLIDE_HEAD_RE.finditer(body or ""))
    if not heads:
        return None, []
    pre = body[: heads[0].start()]
    blocks = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        blocks.append((int(m.group(1)), body[m.start():end].rstrip() + "\n"))
    return pre, blocks


def merge_slides(base, new, targets):
    pre, old_blocks = split_slides(base)
    _, new_blocks = split_slides(new)
    if not old_blocks or not new_blocks:
        return None
    new_map = {n: b for n, b in new_blocks}
    changed, missing, out = [], [], []
    for n, b in old_blocks:
        if n in targets and n in new_map:
            out.append(new_map[n])
            changed.append(n)
        else:
            if n in targets:
                missing.append(n)
            out.append(b)
    return {"body": (pre or "") + "\n".join(x.rstrip("\n") + "\n" for x in out).rstrip() + "\n",
            "changed": [f"S{n}" for n in changed], "missing": [f"S{n}" for n in missing]}


def merge_scenes(base, new, targets):
    new_rows = {int(m.group(1)): m.group(0) for m in SCENE_ROW_RE.finditer(new or "")}
    if not SCENE_ROW_RE.search(base or "") or not new_rows:
        return None
    changed, missing = [], []

    def repl(m):
        n = int(m.group(1))
        if n in targets and n in new_rows:
            changed.append(n)
            return new_rows[n]
        return m.group(0)
    body = SCENE_ROW_RE.sub(repl, base)
    missing = [n for n in targets if n not in changed]
    return {"body": body, "changed": [f"SC{n}" for n in changed], "missing": [f"SC{n}" for n in missing]}


def apply_unit_merge(kind, base_body, draft, targets):
    """지정한 장·장면만 바꾸고 나머지는 기준 버전 원문 그대로 (README A12)."""
    if kind == "cardnews" and targets.get("slides"):
        merged = merge_slides(base_body, draft["body"], set(targets["slides"]))
    elif kind == "script" and targets.get("scenes"):
        merged = merge_scenes(base_body, draft["body"], set(targets["scenes"]))
    else:
        return draft, None
    if merged is None:
        return draft, {"preserved": False, "note": "장·장면 구조를 찾지 못해 AI가 쓴 전체 본문을 그대로 저장했어요."}
    d = dict(draft, body=merged["body"])
    note = f"{', '.join(merged['changed']) or '없음'}만 바꾸고 나머지는 기준 버전 그대로 유지"
    if merged["missing"]:
        note += f" · 찾지 못한 대상: {', '.join(merged['missing'])}"
    return d, {"preserved": True, "changed": merged["changed"], "missing": merged["missing"], "note": note}


# ───────── 생성 ─────────

def _base_dict(base):
    if base is None:
        return None
    return {"id": base.id, "label": base.label, "kind": base.kind, "title": base.title, "body": base.body[:12000]}


def build_user(*, kind, mode, n, sheet, decisions, facts, labels, base, request, instructions, targets, reference):
    payload = {
        "task": ("기준 버전(base_version)을 요청대로 고친 새 버전 1개를 쓴다. 요청하지 않은 부분은 원문을 최대한 그대로 둔다."
                 if mode == "edit" else f"새 초안 {n}개를 쓴다. 안마다 접근을 다르게 한다."),
        "kind": kind, "kind_label": O.KIND_LABELS[kind], "count": n,
        "sheet": {"content_type": labels["contentType"], "channels": labels["channels"], "target": labels["target"],
                  "brand_focus": sheet["brand_focus"], "product_ids": sheet["product_ids"], "goal": sheet["goal"] or "미정",
                  "extra_request": sheet["extra_request"] or ""},
        "decisions": {k: {"value": v.get("value"), "status": v.get("status")} for k, v in decisions.items()},
        "brand_facts": _brand(facts), "product_facts": facts.get("products") or [], "facts_version": facts.get("facts_version"),
        "character_use": assets.character_choice(decisions),
        "brand_assets": assets.catalog_for(sheet["product_ids"], assets.character_choice(decisions)) if kind in ASSET_KINDS else None,
        "base_version": base, "unit_targets": targets if mode == "edit" else None,
        "user_request": request, "instructions": instructions,
        "user_materials": {"reference_text": reference} if reference else None,
    }
    return prompts.dumps(payload) + "\n\n" + prompts.EVIDENCE_REMINDER


def _brand(facts):
    return brandkit.brand_view(facts)


def generate(kind, *, mode, sheet, decisions, settings, facts, labels, base=None, request="", instructions="",
             targets=None, reference="", deadline=None):
    """→ (drafts, meta). AI 실패는 AIError. deadline(time.monotonic 기준)이 지나면 자동 재작성은 건너뜀."""
    if mode == "edit":
        n = 1
    else:
        n = max(1, min(3, int(settings.get("candidates") or 3)))
    language = prompts.language_of(decisions)
    system = prompts.draft_system(kind, language)
    user = build_user(kind=kind, mode=mode, n=n, sheet=sheet, decisions=decisions, facts=facts, labels=labels,
                      base=_base_dict(base), request=request, instructions=instructions, targets=targets,
                      reference=reference)
    action = f"{O.KIND_LABELS[kind]} {'수정' if mode == 'edit' else '초안'}"
    out = providers.call_json(system, user, schema=schemas.DRAFT, name="content_draft", action=action,
                              max_tokens=7000, timeout=85)
    items = sanitize(out, n)
    if not items:
        raise AIError("AI가 초안을 돌려주지 않았습니다. 다시 요청하세요.")
    merge_info = None
    if mode == "edit" and base is not None and targets:
        items[0], merge_info = apply_unit_merge(kind, base.body, items[0], targets)
    for d in items:
        d["review"] = review.keyword_check(d["body"], kind, sheet, facts, d["claims"])

    rewrites = 0
    max_rw = max(0, min(2, int(settings.get("rewrite_max") or 0)))
    while rewrites < max_rw:
        bad = [i for i, d in enumerate(items) if d["review"]["status"] == "error"]
        if not bad or (deadline is not None and time.monotonic() > deadline):
            break  # 고칠 것이 없거나 요청 시간이 길어져 재작성 생략 (검토 결과는 그대로 표시)
        rewrites += 1
        try:
            fixed = _rewrite(kind, system, [items[i] for i in bad], sheet, facts, language)
        except AIError:
            break  # 재작성 실패 → 원래 초안 유지 (검토 결과는 그대로 표시)
        for i, nd in zip(bad, fixed):
            if mode == "edit" and base is not None and targets:
                nd, merge_info = apply_unit_merge(kind, base.body, nd, targets)
            nd["review"] = review.keyword_check(nd["body"], kind, sheet, facts, nd["claims"])
            if review.error_count(nd["review"]) < review.error_count(items[i]["review"]):
                nd["rewritten"] = True
                items[i] = nd
    return items, {"count": n, "rewrites": rewrites, "merge": merge_info, "note": _clip((out or {}).get("note"), 500),
                   "model": providers.text_model(), "language": language}


def _rewrite(kind, system, drafts, sheet, facts, language):
    payload = {
        "task": "아래 초안들을 '문제' 목록만 고쳐 같은 순서·같은 개수로 다시 쓴다. 문제 없는 부분과 접근은 유지한다.",
        "drafts": [{"approach": d["approach"], "title": d["title"], "body": d["body"],
                    "problems": [f"{i['label']}: {', '.join(i['terms'])}" for i in d["review"]["items"] if i["level"] == "error"]}
                   for d in drafts],
        "brand_facts": _brand(facts), "product_facts": facts.get("products") or [],
    }
    user = prompts.dumps(payload) + "\n\n" + prompts.EVIDENCE_REMINDER
    out = providers.call_json(system, user, schema=schemas.DRAFT, name="content_draft", action=f"{O.KIND_LABELS[kind]} 자동 재작성",
                              max_tokens=7000, timeout=80)
    fixed = sanitize(out, len(drafts))
    if len(fixed) != len(drafts):
        raise AIError("재작성 결과 개수가 맞지 않습니다.")
    return fixed
