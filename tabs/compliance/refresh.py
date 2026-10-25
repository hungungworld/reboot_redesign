"""규제 모니터링 수집 → monitor-data.js 저장.

HTML은 이 파일을 <script src>로 읽기만 합니다 (키·개인정보 없음). 출처별로 실패해도 나머지는 저장합니다.
Flask 통합: 저장 위치는 DATA_DIR/parts_override/compliance/monitor-data.js (Render 영구 디스크).
/parts/compliance/monitor-data.js 요청은 이 파일이 있으면 이것을, 없으면 저장소의 기본 파일을 줍니다.

  python -m tabs.compliance.refresh          수집 후 저장
  python -m tabs.compliance.refresh --dry    저장하지 않고 개수만 출력

출처
- openFDA 식품 리콜 (food/enforcement): 젤리·곤약·숙취 · 한국산 음료 · 헛개. 키 없이 먼저 부르고 실패하면 OPENFDA_API_KEY로 다시
- Regulations.gov: FDA·FTC 규칙·규칙안 (FDA는 식품·표시·광고 관련 제목만)
- CourtListener: 숙취 제품·건강식품 허위광고 판결 (관련성 낮은 결과가 섞일 수 있음 → 참고용)
- FDA 수입 거부: Data Dashboard API (FDA_DASHBOARD_USER · FDA_DASHBOARD_KEY) · 한국산 식품만
- CBP CROSS: 품목분류 결정 검색 API (키 없음) · 젤리·2106.90·곤약 · 음료 2202.10·2202.99 · 헛개
- Federal Register: FTC 전체 + FDA 식품 관련 공고 (키 없음). FTC 보도자료 RSS는 봇 차단이라 대신 씀
- 자동으로 못 읽는 곳 (2026-10-24 확인): NAD(화면을 자바스크립트로 그림 · 피드 없음) · Prop 65(OEHHA 봇 차단) → 화면의 바로가기 버튼
- FDA 경고장: warning_letters.py (Data Dashboard API · fda.gov XLSX · 사람이 올린 XLSX·본문). fda.gov는 자동 요청을
  봇 차단으로 막을 수 있어, 막히면 이유를 errors에 남기고 올려 둔 경고장은 그대로 보여 줌
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

from server.envkeys import get as env


def out_path():
    return Path(current_app.config["DATA_DIR"]) / "parts_override" / "compliance" / "monitor-data.js"


UA = "REBOOT-compliance-monitor/0.1 (local research)"
FDA_FOOD_WORDS = re.compile(
    r"food|dietary|label|nutrition|beverage|sugar|sodium|allergen|ingredient|additive|claim|import|traceab|gras|infant formula",
    re.I,
)

HEADER = """/* 규제 모니터링 결과 — tabs/compliance/refresh.py가 덮어쓰는 파일입니다 (사람이 고치지 않음).
   HTML은 <script src>로 읽기만 합니다. 키·개인정보는 넣지 않습니다.
   형식: window.REBOOT_MONITOR = { generatedAt, sources: [{ id, label, items: [{ id, date, title, firm, url, summary, quotes? }] }] }
   항목의 id는 바뀌지 않아야 합니다 (🆕·🔄 표시 기준). */
"""


def _get(url, headers=None, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _lookback():
    try:
        days = int(env("REFRESH_LOOKBACK_DAYS", "365"))
    except ValueError:
        days = 365
    return date.today() - timedelta(days=max(1, days))


def _iso(d8):
    # openFDA 날짜 20260513 → 2026-05-13
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}" if re.fullmatch(r"\d{8}", d8 or "") else (d8 or "")


# 우리 제품군 전체 (2026-10-24): 젤리 · 음료(한국산으로 좁힘 — 음료만으로는 수백 건) · 숙취 연상 성분(헛개)
RECALL_SEARCHES = (
    "product_description:(jelly OR konjac OR hangover OR gummy)",
    "product_description:(drink OR beverage) AND product_description:(korea OR korean)",
    'product_description:(hovenia OR "raisin tree")',
)


def _recall_search(search):
    base = "https://api.fda.gov/food/enforcement.json?" + urllib.parse.urlencode({"search": search, "sort": "report_date:desc", "limit": 30})
    try:
        return _get(base).get("results", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []  # openFDA는 결과 0건을 404로 돌려줌
        key = env("OPENFDA_API_KEY")
        if not key:
            raise
        return _get(base + "&api_key=" + urllib.parse.quote(key)).get("results", [])


def openfda_recalls():
    period = f"report_date:[{_lookback():%Y%m%d} TO {date.today():%Y%m%d}]"
    seen, items = set(), []
    for search in RECALL_SEARCHES:
        for x in _recall_search(f"{search} AND {period}"):
            rid = x.get("recall_number") or x.get("event_id")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            items.append(_recall_item(rid, x))
    return sorted(items, key=lambda i: i["date"], reverse=True)[:40]


def _recall_item(rid, x):
    return {
        "id": rid,
        "date": _iso(x.get("report_date", "")),
        "title": (x.get("product_description") or "")[:200],
        "firm": x.get("recalling_firm", ""),
        "url": "https://www.accessdata.fda.gov/scripts/ires/index.cfm",
        "summary": f'{x.get("classification", "")} · {x.get("status", "")} · {x.get("reason_for_recall", "")}'[:400],
    }


def regulations_gov():
    key = env("REGULATIONS_GOV_API_KEY")
    if not key:
        raise RuntimeError("REGULATIONS_GOV_API_KEY 없음")
    q = urllib.parse.urlencode({
        "filter[agencyId]": "FDA,FTC",
        "filter[documentType]": "Rule,Proposed Rule",
        "filter[postedDate][ge]": _lookback().isoformat(),
        "sort": "-postedDate",
        "page[size]": 100,
    })
    data = _get("https://api.regulations.gov/v4/documents?" + q, {"X-Api-Key": key})
    items = []
    for x in data.get("data", []):
        a = x.get("attributes", {})
        title = a.get("title") or ""
        agency = a.get("agencyId", "")
        if agency == "FDA" and not FDA_FOOD_WORDS.search(title):
            continue  # 의료기기·동물약 등은 제외
        items.append({
            "id": x.get("id"),
            "date": (a.get("postedDate") or "")[:10],
            "title": title[:240],
            "firm": f'{agency} · {a.get("documentType", "")}',
            "url": f'https://www.regulations.gov/document/{x.get("id")}',
            "summary": ("의견 접수 마감 " + a["commentEndDate"][:10]) if a.get("commentEndDate") else "",
        })
    return items[:30]


def courtlistener():
    token = env("COURTLISTENER_TOKEN")
    if not token:
        raise RuntimeError("COURTLISTENER_TOKEN 없음")
    queries = [
        ('"hangover" AND ("false advertising" OR "Lanham Act" OR misbranded OR "unfair competition")', None),
        ('("dietary supplement" OR "functional beverage") AND ("false advertising" OR misbranded)', _lookback().strftime("%m/%d/%Y")),
        # 저당 제품 표시 (2026-10-24 · 저당 젤리·저당 음료)
        ('("low sugar" OR "sugar free" OR "no sugar added") AND ("false advertising" OR misleading OR misbranded)', _lookback().strftime("%m/%d/%Y")),
    ]
    seen, items = set(), []
    for q, filed_after in queries:
        params = {"q": q, "type": "o", "order_by": "dateFiled desc"}
        if filed_after:
            params["filed_after"] = filed_after
        data = _get("https://www.courtlistener.com/api/rest/v4/search/?" + urllib.parse.urlencode(params),
                    {"Authorization": "Token " + token}, timeout=60)
        for x in data.get("results", [])[:15]:
            cid = str(x.get("cluster_id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            items.append({
                "id": cid,
                "date": x.get("dateFiled") or "",
                "title": (x.get("caseName") or "")[:200],
                "firm": x.get("court_citation_string") or x.get("court") or "",
                "url": "https://www.courtlistener.com" + (x.get("absolute_url") or ""),
                "summary": "판결문 검색 결과 · 관련성은 사람이 확인",
            })
        time.sleep(1)
    return items


CROSS_FOOD = re.compile(r"jelly|konjac|gumm|confection|candy|beverage|drink|food preparation|dietary supplement|hangover|hovenia|raisin tree", re.I)
# 젤리(2106.90 · 곤약) + 비탄산 음료 HS 후보(2202.10 · 2202.99 — 실제 코드는 관세사 확인) + 헛개 (2026-10-24 제품군 전체)
CROSS_TERMS = ("jelly", "2106.90", "konjac", "2202.10", "2202.99", "hovenia")
CROSS_HTS = re.compile(r"\b(1704|2106|2202|2009)\.")   # 설탕 과자 · 기타 조제 식료품 · 음료 · 주스


def cbp_rulings():
    """CBP CROSS 품목분류 결정 (공식 검색 API · 키 없음). 젤리·음료·헛개 관련 최신 결정."""
    seen, items = set(), []
    for term in CROSS_TERMS:
        q = urllib.parse.urlencode({"term": term, "collection": "ALL", "commodityGrouping": "ALL",
                                    "pageSize": 10, "page": 1, "sortBy": "DATE_DESC"})
        for x in _get("https://rulings.cbp.gov/api/search?" + q).get("rulings", []):
            no = str(x.get("rulingNumber") or "")
            # 전문 검색이라 'jelly roll 배터리' 같은 결과가 섞임 → 제목·HS 코드가 식품인 것만
            if not no or no in seen or not (CROSS_FOOD.search(x.get("subject") or "") or CROSS_HTS.search(x.get("tariffs") or "")):
                continue
            seen.add(no)
            items.append({
                "id": no,
                "date": (x.get("rulingDate") or "")[:10],
                "title": (x.get("subject") or "")[:240],
                "firm": f'{str(x.get("collection") or "").upper()} · {x.get("categories") or ""}',
                "url": f"https://rulings.cbp.gov/ruling/{urllib.parse.quote(no)}",
                "summary": ("HTS " + x["tariffs"])[:300] if x.get("tariffs") else "",
            })
        time.sleep(1)
    return sorted(items, key=lambda i: i["date"], reverse=True)[:30]


def federal_register():
    """Federal Register 공고 (키 없음): FTC 전체 + FDA 식품·표시·광고 관련 제목. FTC 보도자료 RSS는 봇 차단이라 대신 씀."""
    items = []
    for slug, label in (("federal-trade-commission", "FTC"), ("food-and-drug-administration", "FDA")):
        params = [("conditions[agencies][]", slug), ("conditions[publication_date][gte]", _lookback().isoformat()),
                  ("order", "newest"), ("per_page", "40")] + [("fields[]", f) for f in
                  ("document_number", "title", "type", "publication_date", "html_url", "abstract")]
        for x in _get("https://www.federalregister.gov/api/v1/documents.json?" + urllib.parse.urlencode(params)).get("results", []):
            title = x.get("title") or ""
            if label == "FDA" and not FDA_FOOD_WORDS.search(title):
                continue
            items.append({
                "id": x.get("document_number"),
                "date": x.get("publication_date") or "",
                "title": title[:240],
                "firm": f'{label} · {x.get("type") or ""}',
                "url": x.get("html_url") or "",
                "summary": (x.get("abstract") or "")[:400],
            })
    return sorted(items, key=lambda i: i["date"], reverse=True)[:30]


CHARGES_URL = "https://datadashboard.fda.gov/oii/download/ACT_SECTION_CHARGES.CSV"   # FDA 공식 거부 사유 코드표
CHARGE_KIND = {"ADULTERATION": "불량(위생·유해)", "MISBRANDING": "표시 위반"}


def charge_table():
    """거부 사유 코드 → {short, section, kind}. 30일마다 FDA에서 다시 받음 (실패하면 이전 파일 · 없으면 빈 표)."""
    import csv
    import io
    cache = out_path().parent / "act_section_charges.csv"
    fresh = cache.exists() and time.time() - cache.stat().st_mtime < 30 * 86400
    if not fresh:
        try:
            req = urllib.request.Request(CHARGES_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            if b"ASC_ID" in raw[:20]:   # 코드표가 맞을 때만 저장 (앞에 BOM이 붙어 있어도 20바이트 안)
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
        except Exception:
            pass
    if not cache.exists():
        return {}
    table = {}
    for row in csv.DictReader(io.StringIO(cache.read_text(encoding="utf-8-sig", errors="replace"))):
        sec = (row.get("SCTN_NAME") or "").strip()
        kind = next((v for k, v in CHARGE_KIND.items() if k in sec.upper()), "")
        table[(row.get("ASC_ID") or "").strip()] = {"short": (row.get("CHRG_CODE") or "").strip(),
                                                     "section": sec.split(";")[0].strip(), "kind": kind}
    return table


def _charge_text(codes, table):
    """['251', '9'] → '251 POISONOUS · 불량(위생·유해) §402(a)(1), 801(a)(3) / 9 SALMONELLA …'"""
    out = []
    for c in codes:
        t = table.get(c)
        out.append(f'{c} {t["short"]} · {t["kind"] or "기타"} §{t["section"]}' if t else c)
    return " / ".join(out)


def fda_import_refusals():
    """FDA 수입 거부 (Data Dashboard API · 경고장과 같은 키). 한국산 식품만."""
    user, key = env("FDA_DASHBOARD_USER"), env("FDA_DASHBOARD_KEY")
    if not (user and key):
        raise RuntimeError("FDA_DASHBOARD_USER · FDA_DASHBOARD_KEY 없음 (Data Dashboard 키 신청 필요)")
    body = json.dumps({"start": 1, "rows": 2000, "sort": "RefusalDate", "sortorder": "DESC",
                       "columns": ["FEINumber", "FirmName", "City", "ProductCode", "ProductCodeDescription", "IndustryCodeDescription",
                                   "RefusalDate", "RefusalCharges", "FirmProfile"],
                       "filters": {"ProductCategory": ["Human Foods"], "CountryName": ["South Korea"],   # 2026-10-24 실제 값 확인
                                   "RefusalDateFrom": [_lookback().isoformat()]}}).encode()
    req = urllib.request.Request("https://api-datadashboard.fda.gov/v1/import_refusals", data=body, method="POST", headers={
        "User-Agent": UA, "Content-Type": "application/json", "Authorization-User": user, "Authorization-Key": key})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    # 같은 날 같은 회사·제품은 선적 줄마다 한 행씩 오므로 한 항목으로 묶고 건수만 셈
    groups = {}
    for x in data.get("result") or []:
        gid = f'{x.get("FEINumber") or ""}-{str(x.get("RefusalDate") or "")[:10]}-{x.get("ProductCode") or ""}'
        g = groups.setdefault(gid, {"x": x, "n": 0, "charges": set()})
        g["n"] += 1
        g["charges"].update(c.strip() for c in str(x.get("RefusalCharges") or "").split(",") if c.strip())
    items = []
    table = charge_table()
    for gid, g in groups.items():
        x = g["x"]
        # 사유 코드 뜻은 FDA 공식 코드표(ACT_SECTION_CHARGES.CSV)로 풀어 씀
        codes = sorted(g["charges"], key=lambda c: (len(c), c))
        bits = [x.get("IndustryCodeDescription") or "", f'거부 사유 {_charge_text(codes, table)}' if codes else "",
                f'{g["n"]}줄' if g["n"] > 1 else "", x.get("City") or ""]
        items.append({
            "id": gid,
            "date": str(x.get("RefusalDate") or "")[:10],
            "title": str(x.get("ProductCodeDescription") or x.get("ProductCode") or "")[:240],
            "firm": str(x.get("FirmName") or "")[:160],
            "url": x.get("FirmProfile") if str(x.get("FirmProfile") or "").startswith("https://") else "https://datadashboard.fda.gov/oii/cd/imprefusals.htm",
            "summary": " · ".join(b for b in bits if b)[:600],
        })
    return sorted(items, key=lambda i: i["date"], reverse=True)[:40]


def fda_warning_letters(bodies=False):
    from . import warning_letters
    return warning_letters.collect(bodies=bodies)


SOURCES = [
    ("fda-warning-letters", "FDA 경고장 (식품 분야 전체 · 🍺 표시만 숙취·음주 관련)", fda_warning_letters),
    ("openfda-food-recalls", "openFDA 식품 리콜 (젤리·음료·헛개)", openfda_recalls),
    ("regulations-gov", "Regulations.gov FDA·FTC 규칙·규칙안", regulations_gov),
    ("courtlistener", "CourtListener 허위광고 판결 (참고)", courtlistener),
    ("fda-import-refusals", "FDA 수입 거부 (한국산 식품)", fda_import_refusals),
    ("cbp-cross", "CBP CROSS 품목분류 결정 (젤리·음료·헛개)", cbp_rulings),
    ("federal-register", "Federal Register FTC·FDA 공고", federal_register),
]


def collect(bodies=False):
    """bodies=True면 경고장 본문까지 읽음 (30초 간격 · 하루 1회 작업에서만).
    출처가 실패하면 빈 목록 대신 지난번 정상 자료를 그대로 두고 stale 표시 (2026-10-24).
    출처마다 updatedAt = 마지막으로 정상 수집한 시각."""
    sources, errors = [], []
    now = _now_iso()
    prev = {s.get("id"): s for s in _current().get("sources", [])}
    for sid, label, fn in SOURCES:
        try:
            items = fn(bodies=bodies) if fn is fda_warning_letters else fn()
            note = ""
            if isinstance(items, tuple):   # (항목, 실패[, 참고 안내]) — 일부 경로만 실패해도 항목은 저장
                items, warns, note = (items + ("",))[:3]
                errors.extend({"id": sid, "label": label, "error": w} for w in warns)
            sources.append({"id": sid, "label": label, "items": items, "updatedAt": now, **({"note": note} if note else {})})
        except Exception as e:  # 한 출처가 실패해도 나머지는 저장
            msg = f"{type(e).__name__}: {getattr(e, 'code', '') or str(e)[:120]}"
            errors.append({"id": sid, "label": label, "error": msg})
            old = prev.get(sid) or {}
            sources.append({"id": sid, "label": label, "items": old.get("items") or [], "stale": True,
                            "updatedAt": old.get("updatedAt") or "", **({"note": old["note"]} if old.get("note") else {})})
    # 제목·요약 한글 (참고용). 실패해도 영어 원문은 그대로 저장
    try:
        from . import ko_summary
        if ko_summary.available():
            ko_summary.enrich_sources(sources)
    except Exception as e:
        errors.append({"id": "ko", "label": "한글 요약", "error": f"{type(e).__name__}: {str(e)[:120]}"})
    return {
        "generatedAt": now,
        "sources": sources,
        "errors": errors,
    }


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(data):
    body = json.dumps(data, ensure_ascii=False, indent=1)
    js = HEADER + "(function (global) {\n  'use strict';\n  global.REBOOT_MONITOR = " + body + ";\n})(typeof window !== 'undefined' ? window : globalThis);\n"
    out = out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    for path, text in ((out.with_suffix(".json"), body), (out, js)):   # .json은 출처 하나만 바꿀 때 읽는 사본
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def _current():
    """지금 화면이 읽는 수집 결과: 서버 사본(.json) → 서버 저장본(.js) → 저장소 기본 파일 순서."""
    out = out_path()
    try:
        return json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    for path in (out, Path(__file__).parent / "static" / "monitor-data.js"):
        try:
            js = path.read_text(encoding="utf-8")
            return json.loads(js[js.index("REBOOT_MONITOR = ") + len("REBOOT_MONITOR = "):js.rindex(";\n})(")])
        except (OSError, ValueError):
            continue
    return {"sources": [], "errors": []}


def update_source(source):
    """출처 하나만 바꿔 다시 저장 (경고장 XLSX·본문을 올렸을 때 · 다른 출처는 다시 수집하지 않음)."""
    data = _current()
    old = next((s for s in data.get("sources", []) if s.get("id") == source["id"]), {})
    if old.get("note") and "note" not in source:
        source = dict(source, note=old["note"])   # 참고 안내는 다음 수집 때까지 유지
    sources = [s for s in data.get("sources", []) if s.get("id") != source["id"]]
    # 이 출처만 갱신 시각을 바꿈. 전체 수집 시각(generatedAt)은 그대로 → 다른 출처의 '오래됨' 경고가 사라지지 않음
    data["sources"] = [dict(source, updatedAt=_now_iso())] + sources
    data["errors"] = [e for e in data.get("errors", []) if e.get("id") != source["id"]]
    write(data)
    return data


if __name__ == "__main__":
    from server import create_app
    with create_app().app_context():
        data = collect()
        for s in data["sources"]:
            print(f'{s["label"]}: {len(s["items"])}개')
        for e in data["errors"]:
            print(f'  실패 {e["label"]}: {e["error"]}')
        if "--dry" not in sys.argv:
            write(data)
            print("저장:", out_path())
