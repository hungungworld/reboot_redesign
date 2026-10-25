"""메뉴(파트) 목록 (공통 담당).

왼쪽 메뉴·제목·불러올 파일을 여기서 정합니다. 파트 담당은 자기 파트의 줄만 PR로 고칩니다.
- scripts · styles: tabs/<파트ID>/static/ 안의 파일 이름 (불러오는 순서대로). 파일이 없으면 건너뜀
- title · subtitle: 화면 제목 기본값. 파트 JS의 REBOOT.register({title, subtitle})가 있으면 그 값을 씀
- short · kicker: 시안 v3 (상단 = 업무 영역 알약 메뉴, 왼쪽 = 그 영역의 하위 메뉴)
  왼쪽 하위 메뉴 항목은 파트가 그린 탭(role="tablist")을 core.js가 옮겨 보여 줍니다 (docs/DEV_GUIDE.md)
- 모든 화면에서 모든 파트 파일을 불러옵니다 (REBOOT.use로 서로 기능을 빌려 쓰기 때문)
"""
from dataclasses import dataclass, field

from .config import ROOT


@dataclass(frozen=True)
class Part:
    id: str
    label: str
    icon: str
    owner: str
    priority: str = ""
    title: str = ""
    subtitle: str = ""
    scripts: tuple = field(default_factory=tuple)
    styles: tuple = field(default_factory=tuple)
    nav: bool = True  # False면 상단 메뉴에 넣지 않음 (설정: 오른쪽 위 톱니 · 왼쪽 아래 링크)
    short: str = ""   # 상단 메뉴(알약)에 쓰는 짧은 이름. 비우면 label
    kicker: str = ""  # 왼쪽 하위 메뉴 위의 작은 영문 표시 (시안 v3)
    # 탭 줄이 없는 파트의 왼쪽 메뉴 항목: (이름, 아이콘, ?view= 값). 비우면 파트 이름 하나
    links: tuple = field(default_factory=tuple)
    group: str = ""   # 상단 메뉴 영역 (GROUPS의 id). 비우면 상단 메뉴에 없음


PARTS = (
    Part("home", "홈 · 데일리 브리핑", "home", "이현경", "P1",
         "좋은 하루예요", "오늘의 인사이트와 할 일을 한눈에 확인하세요.",
         scripts=("home.js",), styles=("home.css",), short="홈", kicker="START HERE", group="today"),
    Part("insights", "시장 · 고객 인사이트", "globe", "이현경", "P1",
         "시장의 신호를, 이번 주 할 일로.", "진열대 · 고객 반응 · 검색 수요를 모아 마케터가 할 일을 정리합니다.",
         scripts=("insights.js",), styles=("insights.css",), short="시장·고객 인사이트", kicker="MARKET INTELLIGENCE", group="report"),
    Part("content", "콘텐츠 제작", "spark", "나원", "P0",
         "아이디어에서 콘텐츠까지.", "REBOOT AI와 함께, 브랜드의 다음 이야기를 만들어보세요.",
         scripts=("data.js", "content.js"), styles=("content.css",), short="콘텐츠 제작", kicker="CREATIVE STUDIO", group="studio"),
    Part("compliance", "컴플라이언스", "shield", "소준희", "P0",
         "좋은 카피의 시작, 안전한 표현.", "광고 문구를 점검하고 미국 시장에 맞는 표현을 준비하세요.",
         scripts=("data.js", "monitor-data.js", "compliance.js"), styles=("compliance.css",),
         short="컴플라이언스", kicker="BRAND SAFETY", group="studio"),
    Part("cs", "고객 응대 (CS)", "chat", "소준희", "P1",
         "빠르게 답하고, 안전하게 보내기.", "문의를 분류하고 답변 초안을 컴플라이언스로 재검사한 뒤 담당자가 승인합니다.",
         scripts=("data.js", "cs.js"), styles=("cs.css",), short="고객 응대", kicker="CUSTOMER EXPERIENCE", group="respond"),
    Part("partners", "인플루언서 · 파트너", "users", "혁주", "P1",
         "함께 만드는 브랜드의 다음 장.", "크리에이터와 파트너를 발견하고, 협업의 모든 단계를 연결하세요.",
         scripts=("data-outreach.js", "data-records.js", "outreach.js", "records.js", "partners.js"),
         styles=("partners.css",), short="인플루언서·파트너", kicker="GROW TOGETHER", group="partners"),
    Part("performance", "성과분석", "chart", "혁주", "",
         "숫자에서 찾는, 다음 성장의 힌트.", "채널별 성과를 비교하고, 다음 캠페인을 위한 인사이트를 발견하세요.",
         scripts=("metrics.js", "performance.js"), styles=("performance.css",), short="성과분석", kicker="PERFORMANCE",
         links=(("성과 대시보드", "chart", ""), ("주간 성과 보고서", "doc", "report")), group="report"),
    Part("settings", "설정 및 브랜드 관리", "settings", "이현경", "",
         "우리다운 기준을 만드는 곳.", "브랜드의 기본 정보와 워크스페이스 환경을 관리하세요.",
         scripts=("settings.js",), styles=("settings.css",), nav=False,
         short="설정 및 브랜드 관리", kicker="PREFERENCES"),
)

# 다른 파트가 먼저 공개해야 하는 기능이 있으면 앞에 둠 (settings.facts → cs, compliance.review → cs·content·partners)
LOAD_ORDER = ("settings", "compliance", "cs", "home", "insights", "content", "partners", "performance")

# 상단 메뉴 = 업무 영역 (마케터의 하루 순서). 파트 폴더·주소는 그대로, 들어가는 위치만 묶음
GROUPS = (
    # id, 상단 이름, 대표 파트(알약 클릭 시 이동), 왼쪽 메뉴 kicker
    ("today",    "① 오늘",         "home",     "START HERE"),
    ("respond",  "② 응대",         "cs",       "CUSTOMER EXPERIENCE"),
    ("studio",   "③ 카피 스튜디오", "content",  "CHECK & CREATE"),
    ("partners", "④ 협업",         "partners", "GROW TOGETHER"),
    ("report",   "⑤ 리포트",       "insights", "REPORT"),
)

_BY_ID = {p.id: p for p in PARTS}
_GROUP_BY_ID = {
    gid: {"id": gid, "label": label, "title": label.split(" ", 1)[-1], "lead": lead, "kicker": kicker,
          "parts": [p for p in PARTS if p.group == gid]}
    for gid, label, lead, kicker in GROUPS
}
TABS_DIR = ROOT / "tabs"


def static_dir(part_id):
    """파트 화면 파일(JS·CSS) 폴더: tabs/<파트ID>/static/ — 주소는 그대로 /parts/<파트ID>/<파일>."""
    return TABS_DIR / part_id / "static"


def get_part(part_id):
    return _BY_ID.get(part_id)


def nav_parts():
    return [p for p in PARTS if p.nav]


def nav_groups():
    """상단 메뉴 영역 목록 (GROUPS 순서): [{id, label, title, lead, kicker, parts:[Part...]}]."""
    return [_GROUP_BY_ID[gid] for gid, *_ in GROUPS]


def group_of(part_id):
    """파트가 속한 영역 dict. 영역이 없으면 None (설정 등)."""
    p = _BY_ID.get(part_id)
    return _GROUP_BY_ID.get(p.group) if p else None


def assets():
    """모든 화면에서 불러올 파트 파일 (있는 것만): ([css 경로], [js 경로])."""
    css, js = [], []
    for pid in LOAD_ORDER:
        p = _BY_ID[pid]
        for f in p.styles:
            if (static_dir(pid) / f).is_file():
                css.append(f"{pid}/{f}")
        for f in p.scripts:
            if (static_dir(pid) / f).is_file():
                js.append(f"{pid}/{f}")
    return css, js
