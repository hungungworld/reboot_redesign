"""브랜드 자산 (로고 · 제품 · 캐릭터 Rebu 사진) — tabs/content/data/assets/manifest.json.

- 이미지 생성: 선택 제품 · 맛 · 캐릭터 사용 여부에 맞는 사진을 골라 참고 이미지로 함께 보냄 (media.py)
- 텍스트 초안: 카드뉴스 · 숏폼 구성안에 자산 ID 목록을 줘서 장·장면마다 어떤 사진을 쓸지 적게 함 (drafts.py)
- 화면: GET /api/content/assets (목록) · /api/content/assets/<ID>.jpg (파일, 로그인 필요)
"""
import itertools
import json
import re
from functools import lru_cache
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "data" / "assets"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
MAX_REFS = 16           # 한 번에 보내는 참고 사진 수 (편집 기준 이미지 포함) — OpenAI 이미지 편집이 받는 최대 장수 (2026-10-25)
HIGGSFIELD_MAX_REFS = 5  # Higgsfield는 참고 사진 상한을 확인하지 못해 예전 값 유지
FLAVORS = {"cherry": ("cherry", "체리"), "lemon": ("lemon", "레몬"), "lime": ("lime", "라임")}
CHARACTER_CHOICES = ("사용", "사용 안 함", "AI 판단")
CHARACTER_TYPES = ("image", "cardnews", "shortform")   # 시트에 캐릭터 칸을 보여 주는 콘텐츠


@lru_cache(maxsize=1)
def manifest():
    return json.loads((ASSET_DIR / "manifest.json").read_text(encoding="utf-8"))


def all_assets():
    return manifest()["assets"]


def get(asset_id):
    if not ID_RE.match(asset_id or ""):
        return None
    return next((a for a in all_assets() if a["id"] == asset_id), None)


def path_of(a):
    return ASSET_DIR / a["file"]


def url_of(a):
    return f"/api/content/assets/{a['id']}.jpg"


def public(a):
    return {"id": a["id"], "type": a["type"], "productId": a.get("product_id"), "flavor": a.get("flavor"),
            "view": a.get("view"), "ref": a["ref"], "desc": a["desc"], "url": url_of(a)}


# ───────── 캐릭터 사용 여부 (decisions.use_character) ─────────

def character_choice(decisions):
    """결정 사항의 값 → 사용 · 사용 안 함 · AI 판단 (없으면 AI 판단)."""
    v = str(((decisions or {}).get("use_character") or {}).get("value") or "").replace(" ", "").lower()
    if not v:
        return "AI 판단"
    if any(w in v for w in ("안함", "안해", "빼", "없이", "제외", "아니요", "false")):
        return "사용 안 함"
    if any(w in v for w in ("판단", "ai", "알아서", "자동")):
        return "AI 판단"
    return "사용"


def norm_character(value):
    """시트에서 온 값 검증 → 저장할 값."""
    v = str(value or "").strip()
    if v not in CHARACTER_CHOICES:
        raise ValueError("캐릭터 Rebu는 사용 · 사용 안 함 · AI 판단 중 하나입니다.")
    return v


# ───────── 참고 사진 고르기 ─────────

def flavors_in(*texts):
    """글에서 언급한 맛 (영어·한국어)."""
    t = " ".join(str(x or "") for x in texts).lower()
    return [f for f, words in FLAVORS.items() if any(w in t for w in words)]


def _product_refs(pid, flavors):
    """그 제품의 참고용 사진 전부 (우선순위 순). 맛을 골랐으면 그 맛 사진 먼저 + 맛 구분 없는 전체 사진(3병·봉지)만."""
    cands = sorted((a for a in all_assets() if a.get("product_id") == pid and a["ref"]),
                   key=lambda a: (a["priority"], a.get("flavor") is not None))
    if not flavors:
        return cands
    return ([a for a in cands if a.get("flavor") in flavors and a.get("flavor") is not None]
            + [a for a in cands if a.get("flavor") is None])


def pick_refs(product_ids, *, flavors=(), character=False, limit=MAX_REFS):
    """선택 제품(·맛)에 맞는 참고용 사진 전부 + 캐릭터 1장, 최대 limit장. 리볼버·글자 많은 사진(ref=false)은 고르지 않음.
    제품을 번갈아 넣어 상한에서 잘려도 제품마다 고르게 들어감."""
    char_slot = 1 if character else 0
    lists = [_product_refs(pid, flavors) for pid in product_ids]
    refs = [a for row in itertools.zip_longest(*lists) for a in row if a is not None]
    refs = refs[: max(0, limit - char_slot)]
    if character:
        refs.extend(sorted((a for a in all_assets() if a["type"] == "character" and a["ref"]), key=lambda a: a["priority"])[:1])
    return refs


def catalog_for(product_ids, character_choice_value):
    """텍스트 초안용 자산 목록 (선택 제품 + 캐릭터를 쓸 수 있으면 캐릭터 + 로고)."""
    wanted = set(product_ids or [])
    out = []
    for a in all_assets():
        if a["type"] == "product" and a.get("product_id") not in wanted:
            continue
        if a["type"] == "character" and character_choice_value == "사용 안 함":
            continue
        if not a["ref"] and a["type"] != "logo":
            continue
        out.append({"id": a["id"], "desc": a["desc"]})
    return out
