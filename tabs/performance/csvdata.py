"""성과 CSV 읽기 · 검사 · 저장 (README 3장).

- 형식: 날짜,채널,광고비,노출수,클릭수,전환수,매출 (첫 줄 순서 고정)
- 날짜 2026-10-19 · 2026/10/19 · 2026.10.19 · 금액 1,260,000 · 1260000원 · 엑셀(EUC-KR) 파일도 읽음
- 저장: DATA_DIR/uploads/performance/performance.csv (없으면 저장소의 샘플 tabs/performance/data/performance_sample.csv)
- 덮어쓰기 전 이전 파일은 DATA_DIR/backups/performance-YYYYMMDD-HHMMSS.csv 로 보관
- 샘플은 실제 데이터와 섞지 않음: 처음 업로드는 '합치기'여도 새 파일로 시작
"""
import csv
import io
import json
import math
import os
import re
import shutil
from datetime import date
from pathlib import Path

from flask import current_app

from server.config import ROOT
from server.models import iso, utcnow

HEADER = ("날짜", "채널", "광고비", "노출수", "클릭수", "전환수", "매출")
FIELDS = ("date", "channel", "spend", "impressions", "clicks", "conversions", "revenue")
NUM_FIELDS = FIELDS[2:]
HEADER_TEXT = ",".join(HEADER)
HEADER_ERROR = f'첫 줄은 "{HEADER_TEXT}" 이어야 합니다. 양식 받기로 받은 파일에 붙여 넣어 주세요.'
SAMPLE_PATH = ROOT / "tabs" / "performance" / "data" / "performance_sample.csv"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
KEEP_BACKUPS = 60

_DATE = re.compile(r"^(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*[.일]?$")
_NUM_JUNK = re.compile(r"[,\s\u00a0원₩￦]|krw", re.I)
_cache = {}


class CsvError(ValueError):
    """화면에 그대로 보여 줄 CSV 오류 (몇 번째 줄인지 포함)."""


# ───────── 위치 ─────────

def upload_path():
    return Path(current_app.config["DATA_DIR"]) / "uploads" / "performance" / "performance.csv"


def meta_path():
    return upload_path().with_name("meta.json")


def backup_dir():
    return Path(current_app.config["DATA_DIR"]) / "backups"


# ───────── 읽기 · 검사 ─────────

def decode(raw):
    """UTF-8(BOM 포함) → 엑셀 한글(CP949·EUC-KR) 순서로 읽음."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise CsvError("파일 글자 인코딩을 읽지 못했습니다. UTF-8 또는 엑셀 CSV(쉼표로 분리)로 저장해 주세요.")


def parse_date(value, line):
    s = str(value or "").strip()
    m = _DATE.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    raise CsvError(f"{line}번째 줄: 날짜 '{s[:20]}'을(를) 읽지 못했습니다 (예: 2026-10-19).")


def parse_number(value, label, line):
    raw = str(value if value is not None else "").strip()
    s = _NUM_JUNK.sub("", raw)
    if s in ("", "-"):
        return 0
    try:
        n = float(s)
    except ValueError:
        raise CsvError(f"{line}번째 줄: {label} '{raw[:20]}'은(는) 숫자가 아닙니다.")
    if not math.isfinite(n) or n < 0:
        raise CsvError(f"{line}번째 줄: {label}은(는) 0 이상의 숫자여야 합니다.")
    return int(round(n))


def parse_csv(text):
    """CSV 글 → (행 목록, 정보). 행: {date, channel, spend, impressions, clicks, conversions, revenue}."""
    if len(text) > MAX_BYTES * 2:
        raise CsvError("파일이 너무 큽니다 (5MB 이하).")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    header = next(reader, None)
    cells = [c.strip().lstrip("\ufeff") for c in (header or [])]
    while cells and cells[-1] == "":
        cells.pop()  # 엑셀이 붙이는 빈 열
    if tuple(cells) != HEADER:
        raise CsvError(HEADER_ERROR)
    found, dupes = {}, 0
    for line, row in enumerate(reader, start=2):
        if not any(str(c).strip() for c in row):
            continue
        if len(row) < len(HEADER):
            raise CsvError(f"{line}번째 줄: 칸이 {len(row)}개입니다 (7개 필요: {HEADER_TEXT}).")
        extra = [c for c in row[len(HEADER):] if str(c).strip()]
        if extra:
            raise CsvError(f"{line}번째 줄: 칸이 7개보다 많습니다. 금액의 쉼표(,)는 큰따옴표로 감싸거나 빼 주세요.")
        channel = row[1].strip()
        if not channel:
            raise CsvError(f"{line}번째 줄: 채널 이름이 비어 있습니다.")
        item = {"date": parse_date(row[0], line), "channel": channel[:40]}
        for i, f in enumerate(NUM_FIELDS, start=2):
            item[f] = parse_number(row[i], HEADER[i], line)
        key = (item["date"], item["channel"])
        if key in found:
            dupes += 1  # 같은 파일 안에서 같은 날짜+채널은 아래 줄 값으로
        found[key] = item
        if len(found) > MAX_ROWS:
            raise CsvError(f"행이 너무 많습니다 ({MAX_ROWS:,}행 이하).")
    if not found:
        raise CsvError("데이터 행이 없습니다. 첫 줄(제목) 아래에 날짜별·채널별 값을 넣어 주세요.")
    return sort_rows(found.values()), {"duplicates": dupes}


def sort_rows(rows):
    return sorted(rows, key=lambda r: (r["date"], r["channel"].lower()))


def to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r[f] for f in FIELDS])
    return buf.getvalue()


def _read_file(path):
    """파일 내용(파싱 결과)을 수정 시각 기준으로 잠깐 기억해 둠."""
    st = path.stat()
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == (st.st_mtime_ns, st.st_size):
        return hit[1]
    rows, _info = parse_csv(decode(path.read_bytes()))
    _cache[key] = ((st.st_mtime_ns, st.st_size), rows)
    return rows


def read_meta():
    try:
        return json.loads(meta_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load():
    """현재 데이터: 업로드한 파일 → 없으면 샘플. {rows, source: upload|sample|none, meta}"""
    path = upload_path()
    if path.is_file():
        try:
            return {"rows": _read_file(path), "source": "upload", "meta": read_meta()}
        except (OSError, CsvError):
            current_app.logger.exception("업로드한 성과 CSV를 읽지 못했습니다")
            return {"rows": [], "source": "upload", "meta": read_meta(), "error": "저장된 CSV를 읽지 못했습니다. 다시 올려 주세요."}
    if SAMPLE_PATH.is_file():
        return {"rows": _read_file(SAMPLE_PATH), "source": "sample", "meta": {}}
    return {"rows": [], "source": "none", "meta": {}}


def date_range(rows):
    if not rows:
        return None
    return {"start": rows[0]["date"], "end": rows[-1]["date"]}


def channels(rows):
    """처음 나온 순서 (색이 채널을 따라가도록 항상 같은 순서)."""
    seen = []
    for r in rows:
        if r["channel"] not in seen:
            seen.append(r["channel"])
    return seen


# ───────── 저장 ─────────

def _backup(path):
    if not path.is_file():
        return None
    from server.core.templating import local_now
    bdir = backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = local_now().strftime("%Y%m%d-%H%M%S")
    target = bdir / f"performance-{stamp}.csv"
    n = 2
    while target.exists():
        target = bdir / f"performance-{stamp}-{n}.csv"
        n += 1
    shutil.copy2(path, target)
    olds = sorted(bdir.glob("performance-*.csv"), key=lambda p: p.stat().st_mtime)
    for p in olds[:-KEEP_BACKUPS]:
        p.unlink(missing_ok=True)
    return target.name


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\ufeff" + text, encoding="utf-8", newline="")  # BOM: 엑셀에서 한글이 깨지지 않게
    os.replace(tmp, path)


def save(new_rows, mode, user="", filename=""):
    """mode: merge(같은 날짜+채널은 새 값) · replace(전체 바꾸기). 돌려줌: 결과 요약."""
    path = upload_path()
    had_upload = path.is_file()
    base = []
    if mode == "merge" and had_upload:
        try:
            base = _read_file(path)
        except CsvError:
            base = []
    merged = {(r["date"], r["channel"]): r for r in base}
    updated = sum(1 for r in new_rows if (r["date"], r["channel"]) in merged)
    for r in new_rows:
        merged[(r["date"], r["channel"])] = r
    rows = sort_rows(merged.values())
    backup = _backup(path)
    _write_atomic(path, to_csv(rows))
    _cache.pop(str(path), None)
    meta = {"updatedAt": iso(utcnow()), "updatedBy": str(user or "")[:100], "filename": str(filename or "")[:120],
            "mode": "merge" if mode == "merge" else "replace", "rows": len(rows),
            "added": len(new_rows) - updated, "updated": updated, "backup": backup,
            "fromSample": not had_upload}
    meta_path().write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def reset_to_sample():
    """업로드한 파일을 백업하고 지움 → 샘플로 돌아감."""
    path = upload_path()
    backup = _backup(path)
    path.unlink(missing_ok=True)
    meta_path().unlink(missing_ok=True)
    _cache.pop(str(path), None)
    return backup
