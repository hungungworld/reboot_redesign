"""시즌 · 팩트시트를 읽고 쓰는 함수 (다른 파트의 서버 코드는 이 함수를 씁니다).

    from server.catalog import service as catalog
    catalog.list_seasons()            # D-day·준비 기간 포함 목록
    catalog.get_brand()               # 브랜드 기본 정보 dict
    catalog.get_products()            # 제품 4종 [dict]
    catalog.product_facts(["jelly_original"])   # AI 프롬프트에 넣을 제품별 사실 (값 상태 포함)
"""
import csv
import io
import json
from datetime import date

from ..config import ROOT
from ..extensions import db
from ..models import iso, utcnow
from .models import FactRecord, Season

SEASON_CATEGORIES = ("holiday", "sports", "occasion")
BRAND_FIELDS = ("name", "base_company", "market", "founded", "intro", "slogan", "colors", "character", "cm_song", "category")
PRODUCT_FIELDS = ("name", "ko", "category", "form", "flavors", "flavor_status", "pack", "direction", "ingredients", "volume",
                  "usage", "consumer_name", "price", "cost", "evidence", "sources", "allergens", "notes")
EVIDENCE = ("확인 필요", "확인 완료")
FLAVOR_STATUS = ("provisional", "confirmed")


# ───────── 시즌 ─────────

def _today():
    from ..core.templating import local_now
    return local_now().date()


def seed_seasons():
    if db.session.query(Season.id).first() is not None:
        return 0
    path = ROOT / "data" / "seasons.csv"
    if not path.exists():
        return 0
    n = import_seasons_csv(path.read_text(encoding="utf-8-sig"), replace=False, user="초기값")
    return n


def season_dict(s, today=None):
    today = today or _today()
    dday = (s.start_date - today).days
    return {
        "id": s.id, "name": s.name, "start": s.start_date.isoformat(), "end": s.end_date.isoformat(),
        "category": s.category, "priority": s.priority, "leadDays": s.lead_days, "notes": s.notes,
        "monitor": bool(s.monitor), "dday": dday,
        "ongoing": s.start_date <= today <= s.end_date,
        "inLead": 0 < dday <= s.lead_days,
        "past": s.end_date < today,
        "updatedAt": iso(s.updated_at), "updatedBy": s.updated_by,
    }


def list_seasons(include_past=False, today=None):
    today = today or _today()
    rows = Season.query.order_by(Season.start_date, Season.priority).all()
    out = [season_dict(s, today) for s in rows]
    return out if include_past else [x for x in out if not x["past"]]


def _parse_date(v, label):
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        raise ValueError(f"{label} 날짜 형식이 올바르지 않습니다 (YYYY-MM-DD).")


def apply_season(s, data, user=""):
    name = str(data.get("name", s.name or "")).strip()
    if not name:
        raise ValueError("시즌 이름이 없습니다.")
    start = _parse_date(data.get("start", data.get("start_date", s.start_date)), "시작일")
    end_raw = data.get("end", data.get("end_date", s.end_date)) or start
    end = _parse_date(end_raw, "종료일")
    if end < start:
        raise ValueError("종료일이 시작일보다 빠릅니다.")
    category = str(data.get("category", s.category or "holiday")).strip() or "holiday"
    if category not in SEASON_CATEGORIES:
        raise ValueError("구분은 holiday · sports · occasion 중 하나입니다.")
    try:
        priority = int(data.get("priority", s.priority or 2))
        lead = int(data.get("leadDays", data.get("lead_days", s.lead_days if s.lead_days is not None else 30)))
    except (TypeError, ValueError):
        raise ValueError("우선순위·준비 기간은 숫자로 입력하세요.")
    if not 1 <= priority <= 3 or not 0 <= lead <= 365:
        raise ValueError("우선순위는 1~3, 준비 기간은 0~365일입니다.")
    s.name, s.start_date, s.end_date, s.category = name[:100], start, end, category
    s.priority, s.lead_days = priority, lead
    s.notes = str(data.get("notes", s.notes or "") or "")[:1000]
    if "monitor" in data:
        s.monitor = bool(data.get("monitor"))
    elif s.monitor is None:
        s.monitor = True
    s.updated_by = user
    s.updated_at = utcnow()
    return s


def import_seasons_csv(text, replace=False, user=""):
    """CSV(name,start_date,end_date,category,priority,lead_days,notes) → 추가한 개수. replace면 기존을 지우고."""
    reader = csv.DictReader(io.StringIO(text))
    need = {"name", "start_date"}
    if not reader.fieldnames or not need.issubset({f.strip() for f in reader.fieldnames}):
        raise ValueError("CSV 첫 줄은 name,start_date,end_date,category,priority,lead_days,notes 이어야 합니다.")
    rows = []
    for i, r in enumerate(reader, start=2):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if not r.get("name"):
            continue
        try:
            rows.append(apply_season(Season(), {
                "name": r["name"], "start": r["start_date"], "end": r.get("end_date") or r["start_date"],
                "category": r.get("category") or "holiday", "priority": r.get("priority") or 2,
                "leadDays": r.get("lead_days") or 30, "notes": r.get("notes", "")}, user))
        except ValueError as e:
            raise ValueError(f"{i}번째 줄: {e}")
    if replace:
        Season.query.delete()
    db.session.add_all(rows)
    db.session.commit()
    return len(rows)


def export_seasons_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "start_date", "end_date", "category", "priority", "lead_days", "notes"])
    for s in Season.query.order_by(Season.start_date).all():
        w.writerow([s.name, s.start_date.isoformat(), s.end_date.isoformat(), s.category, s.priority, s.lead_days, s.notes])
    return buf.getvalue()


# ───────── 팩트시트 ─────────

def _seed_file():
    return json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))


def seed_facts():
    if db.session.query(FactRecord.entity).first() is not None:
        return 0
    data = _seed_file()
    db.session.add(FactRecord(entity="brand", kind="brand", sort=0, data=json.dumps(data["brand"], ensure_ascii=False),
                              updated_by="초기값"))
    for i, p in enumerate(data["products"], start=1):
        db.session.add(FactRecord(entity=p["id"], kind="product", sort=i, data=json.dumps(p, ensure_ascii=False),
                                  updated_by="초기값"))
    db.session.commit()
    return len(data["products"]) + 1


def facts_version():
    row = FactRecord.query.order_by(FactRecord.updated_at.desc()).first()
    base = _seed_file().get("facts_version", "")
    return f"{base}+{row.updated_at:%Y%m%d%H%M%S}" if row and row.updated_by != "초기값" else base


def _with_meta(r):
    d = r.parsed()
    d["updatedAt"], d["updatedBy"] = iso(r.updated_at), r.updated_by
    return d


def get_brand():
    r = db.session.get(FactRecord, "brand")
    return _with_meta(r) if r else {}


def get_products():
    return [_with_meta(r) for r in FactRecord.query.filter_by(kind="product").order_by(FactRecord.sort).all()]


def get_product(pid):
    r = db.session.get(FactRecord, pid)
    return _with_meta(r) if r and r.kind == "product" else None


def update_facts(entity, patch, user=""):
    r = db.session.get(FactRecord, entity)
    if r is None:
        raise KeyError(entity)
    allowed = BRAND_FIELDS if r.kind == "brand" else PRODUCT_FIELDS
    data = r.parsed()
    for k, v in patch.items():
        if k not in allowed:
            continue
        if k == "evidence" and v not in EVIDENCE:
            raise ValueError("근거 상태는 '확인 필요' 또는 '확인 완료'입니다.")
        if k == "flavor_status" and v not in FLAVOR_STATUS:
            raise ValueError("맛 상태는 provisional(임시) 또는 confirmed(확정)입니다.")
        if k in ("price", "cost") and v not in (None, ""):
            try:
                v = round(float(v), 2)
            except (TypeError, ValueError):
                raise ValueError("가격·원가는 숫자(달러)로 입력하세요.")
        if k == "sources":
            if not isinstance(v, list):
                raise ValueError("출처는 목록이어야 합니다.")
            v = [{"field": str(s.get("field", ""))[:40], "url": str(s.get("url", ""))[:500], "note": str(s.get("note", ""))[:200]}
                 for s in v if isinstance(s, dict)][:30]
        elif isinstance(v, str):
            v = v.strip()[:2000]
        data[k] = None if v == "" else v
    # 출처가 없으면 '확인 완료'로 둘 수 없음 (설정 README 2장)
    if r.kind == "product" and data.get("evidence") == "확인 완료" and not data.get("sources"):
        raise ValueError("출처(기사·공식 페이지 URL)가 없으면 '확인 완료'로 바꿀 수 없습니다.")
    r.data = json.dumps(data, ensure_ascii=False)
    r.updated_by, r.updated_at = user, utcnow()
    db.session.commit()
    return _with_meta(r)


def flat_facts():
    """CS 템플릿용 (REBOOT.use('settings').facts()와 같은 모양). 제품별 값은 여러 제품이 섞이지 않게 비워 둠."""
    brand = get_brand()
    return {"brand": brand.get("name") or "REBOOT", "product": None, "flavor": None, "ingredients": None, "allergens": None}


def product_facts(product_ids=None):
    """AI 프롬프트용: 브랜드(항상) + 고른 제품별 사실을 상태와 함께 (콘텐츠 README 3.3·3.4·6.2). 값이 없으면 null."""
    products = get_products()
    if product_ids is not None:
        wanted = set(product_ids)
        products = [p for p in products if p.get("id") in wanted]
    out = {"facts_version": facts_version(), "brand": get_brand(), "products": []}
    for p in products:
        out["products"].append({
            "entity_id": p.get("id"), "internal_name": p.get("name"), "ko": p.get("ko"), "form": p.get("form"),
            "flavors": {"value": p.get("flavors"), "status": p.get("flavor_status", "provisional")},
            "pack": p.get("pack"), "development_direction": p.get("direction"),
            "sugar_reduction": "development_goal" if p.get("sugar_reduced") else None,
            "ingredients": p.get("ingredients"), "volume": p.get("volume"), "usage": p.get("usage"),
            "consumer_name": p.get("consumer_name"), "evidence": p.get("evidence"),
        })
    return out
