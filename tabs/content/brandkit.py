"""콘텐츠 제작용 브랜드 키트 — tabs/content/data/brand_kit.json (사용자 제공 · 2026-10-24 · 슬로건 2개 2026-10-25).

공용 팩트시트(server/catalog)는 다른 탭도 함께 쓰므로 고치지 않고, 콘텐츠 제작 때만 이 파일의 값을 위에 덮어씁니다.
제품마다 따로 덮어쓰며 한 제품의 값(가격 · ZERO SUGAR 등)을 다른 제품이나 브랜드 전체로 옮기지 않습니다.
"""
import copy
import json
from functools import lru_cache
from pathlib import Path

from server.catalog import service as catalog

KIT_FILE = Path(__file__).resolve().parent / "data" / "brand_kit.json"
BRAND_KEYS = ("name", "slogan", "slogans", "colors", "character", "cm_song", "category", "intro", "market", "company",
              "key_ingredient")


@lru_cache(maxsize=1)
def load():
    return json.loads(KIT_FILE.read_text(encoding="utf-8"))


def slogans():
    """공식 슬로건 (원문 표기 그대로) — 첫 번째가 기본."""
    return tuple(load()["brand"]["slogans"])


def product_facts(product_ids=None):
    """catalog.product_facts와 같은 모양 + 브랜드 키트 값 (제품별). facts_version 뒤에 키트 버전을 붙임."""
    out = catalog.product_facts(product_ids)
    kit = load()
    out["brand"] = dict(out.get("brand") or {}, **kit["brand"])
    out["products"] = [dict(p, **copy.deepcopy(kit["products"].get(p.get("entity_id"), {}))) for p in out.get("products") or []]
    out["facts_version"] = f"{out.get('facts_version') or ''}+kit-{kit['version']}"
    return out


def brand_view(facts):
    """AI에 넘길 브랜드 사실 (키트 항목 포함)."""
    b = facts.get("brand") or {}
    return {k: b.get(k) for k in BRAND_KEYS}


def zero_sugar_ids(facts):
    return [p["entity_id"] for p in facts.get("products") or [] if p.get("zero_sugar")]
