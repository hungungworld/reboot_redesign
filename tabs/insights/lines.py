"""제품 라인 묶기 — 채널 · 용량이 달라도 같은 제품이면 한 줄로 (진열대 · 이번 주 액션 규칙 7~9).

- 제품 라인 = 브랜드(별칭 사전 data/product_lines.json) + 제형. 예: '상쾌환 · 환'
- 옵션 = 회분 수 (10포 · 20포). 맛 · 버전은 나누지 않음 (같은 옵션 안에서 '2개 중 1개 품절'로 요약)
- 자동으로 묶고, 사람이 확인한 묶음(line_checks)과 고친 묶음(line_overrides)은 기억 → 확인 안 된 것만 '확인 필요'
- 재고 임박 = Amazon 'Only N left' 문구의 N이 LOW_STOCK 이하
"""
import json
import re

from server.config import ROOT
from server.models import iso, utcnow
from .competitors import FORM_LABEL, form_of, stored_positions
from .models import kv_get, kv_set
from .rules import LOW_STOCK

LINES_FILE = ROOT / "tabs" / "insights" / "data" / "product_lines.json"
LEFT_RE = re.compile(r"only\s+(\d{1,4})\s+left", re.I)
SOURCES = ("amazon", "weee", "yami")


def _load_brands():
    try:
        data = json.loads(LINES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for b in data.get("brands") or []:
        try:
            rx = [re.compile(a, re.I) for a in b.get("aliases") or []]
        except re.error:
            continue
        if b.get("name") and rx:
            out.append({"name": b["name"], "rx": rx, "forms": b.get("forms") or [], "default": b.get("default") or ""})
    return out


BRANDS = _load_brands()


def stock_left(availability):
    m = LEFT_RE.search(availability or "")
    return int(m.group(1)) if m else None


def known_brand(brand, title):
    """브랜드 칸 → 상품명 순서로 별칭 사전에서 찾기 → 사전 항목 또는 None."""
    for text in (brand or "", title or ""):
        for b in BRANDS:
            if any(r.search(text) for r in b["rx"]):
                return b
    return None


def line_id(brand, form):
    return f"{brand}|{form}"


FORM_SHORT = {"liquid": "음료", "powder": "분말", "stick": "스틱", "gummy": "젤리", "pellet": "환", "tablet": "캡슐 · 정제", "other": "기타"}


def line_label(brand, form):
    return f"{brand} · {FORM_SHORT.get(form, form)}"


def auto_line(x, stored=None):
    """진열대 한 줄 → (브랜드, 제형) 자동 판정."""
    b = known_brand(x.get("brand"), x.get("title"))
    form = form_of(x, stored)
    if b:
        if b["forms"] and form not in b["forms"]:
            form = b["default"] or form
        return b["name"], form
    return x.get("brand") or "브랜드 모름", form


def annotate(rows):
    """shelf_dict rows에 line · stockLeft를 붙임 (DB 읽기는 한 번)."""
    stored = stored_positions()
    overrides, checks = kv_get("line_overrides", {}), kv_get("line_checks", {})
    for x in rows:
        x["stockLeft"] = stock_left(x.get("availability"))
        brand, form = auto_line(x, stored)
        o = overrides.get(x["key"])
        if o:
            brand, form = o.get("brand") or brand, o.get("form") or form
            if o.get("brand"):  # 사람이 고친 브랜드는 경쟁사 등록 · 카드에도 씀
                x["brand"], x["brandGuessed"] = brand, False
        lid = line_id(brand, form)
        c = checks.get(x["key"])
        x["line"] = {"id": lid, "brand": brand, "form": form, "label": line_label(brand, form),
                     "edited": bool(o), "confirmed": bool(o) or bool(c and c.get("line") == lid),
                     "checkedBy": (o or c or {}).get("by", "")}
    return rows


def confirm(rows, user):
    """지금 묶음을 '확인함'으로 기억 (rows는 annotate된 것) → 확인한 수."""
    checks = kv_get("line_checks", {})
    at = iso(utcnow())
    for x in rows:
        checks[x["key"]] = {"line": x["line"]["id"], "by": user, "at": at}
    kv_set("line_checks", checks, user)
    return len(rows)


def set_override(key, brand, form, user):
    overrides = kv_get("line_overrides", {})
    if brand is None and form is None:
        overrides.pop(key, None)
    else:
        overrides[key] = {"brand": (brand or "").strip()[:60], "form": form if form in FORM_LABEL else "",
                          "by": user, "at": iso(utcnow())}
    kv_set("line_overrides", overrides, user)


def _cell(items):
    known = [x for x in items if x.get("in_stock") is not None]
    oos = [x for x in items if x.get("in_stock") is False]
    lefts = [x["stockLeft"] for x in items if x.get("stockLeft") is not None and x.get("in_stock") is not False]
    ps = [x["perServing"] for x in items if x.get("perServing") is not None]
    if items and len(oos) == len(items):
        state = "oos"
    elif oos:
        state = "partial"
    elif lefts and min(lefts) <= LOW_STOCK:
        state = "low"
    elif not known:
        state = "unknown"
    else:
        state = "in"
    return {"state": state, "count": len(items), "oos": len(oos), "left": min(lefts) if lefts else None,
            "perServing": min(ps) if ps else None, "price": min((x["price"] for x in items if x.get("price") is not None), default=None),
            "ids": [x["id"] for x in items], "titles": [x["title"] for x in items]}


def build(rows):
    """annotate된 rows → 제품 라인 목록 (채널 × 옵션 표 · 품절 채널 · 1회분 범위 · 묶음 할인)."""
    groups = {}
    for x in rows:
        groups.setdefault(x["line"]["id"], []).append(x)
    out = []
    for lid, items in groups.items():
        first = items[0]["line"]
        chans = [s for s in SOURCES if any(x["source"] == s for x in items)]
        opts = {}
        for x in items:
            k = x["servings"] if x.get("servingsSource") != "default" else 0
            opts.setdefault(k, []).append(x)
        options = []
        for k in sorted(opts, key=lambda v: (v == 0, v)):
            its = opts[k]
            ps = [x["perServing"] for x in its if x.get("perServing") is not None]
            options.append({"servings": k or None, "label": f"{k}회분" if k else "회분 확인 필요", "perServing": min(ps) if ps else None,
                            "cells": {s: _cell([x for x in its if x["source"] == s]) for s in chans if any(x["source"] == s for x in its)}})
        by_chan = {s: [x for x in items if x["source"] == s] for s in chans}
        oos_chans = [s for s in chans if by_chan[s] and all(x.get("in_stock") is False for x in by_chan[s])]
        low = sorted(({"source": x["source"], "left": x["stockLeft"], "title": x["title"], "id": x["id"],
                       "servings": x["servings"]} for x in items
                      if x.get("stockLeft") is not None and x["stockLeft"] <= LOW_STOCK and x.get("in_stock") is not False),
                     key=lambda v: v["left"])
        ps = [x["perServing"] for x in items if x.get("perServing") is not None]
        sized = [o for o in options if o["servings"] and o["perServing"] is not None]
        bulk = None
        if len(sized) > 1 and sized[0]["perServing"]:
            small, large = sized[0], sized[-1]
            pct = round((large["perServing"] - small["perServing"]) / small["perServing"] * 100, 1)
            # ±60%를 넘는 차이는 묶음 할인보다 회분 수를 잘못 읽은 경우가 많음 (예: '1 count' = 1상자)
            bulk = {"small": small["label"], "large": large["label"], "pct": pct, "suspect": abs(pct) > 60}
        out.append({
            "id": lid, "brand": first["brand"], "form": first["form"], "formLabel": FORM_LABEL.get(first["form"], first["form"]),
            "label": first["label"], "own": any(x.get("own") for x in items), "watched": any(x.get("watched") for x in items),
            "channels": chans, "options": options, "oosChannels": oos_chans,
            "partialOos": sum(1 for x in items if x.get("in_stock") is False) if not oos_chans else 0,
            "unknownStock": sum(1 for x in items if x.get("in_stock") is None),
            "low": low, "perServing": {"min": min(ps), "max": max(ps)} if ps else None, "bulk": bulk,
            "products": len(items), "reviews": sum(x.get("reviews") or 0 for x in items),
            "confirmed": all(x["line"]["confirmed"] for x in items),
        })
    out.sort(key=lambda g: (g["own"], not g["oosChannels"], not g["low"], -g["reviews"]))
    return out
