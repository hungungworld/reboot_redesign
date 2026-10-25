# 개발 가이드 — 파트 만들기 (Flask · Render 버전)

> 팀 README(docs/team/README.md) 4·5·6장의 규칙을 Flask 앱에 맞게 옮긴 문서입니다.
> **자기 파트 폴더만 고친다**는 원칙은 그대로입니다. 공통 파일(`core/` · `server/` 바로 아래 파일 · `server/core|auth|catalog/` · 템플릿)은 공통 담당만 고칩니다.

---

## 1. 폴더와 소유권

```
저장소 맨 위/
├─ tabs/<파트ID>/                    ← 파트 담당 (이 폴더 안만 수정)
│   ├─ routes.py · models.py · …     파트 서버 (bp의 url_prefix = /api/<파트ID>)
│   ├─ static/                       파트 화면 (JS·CSS·data.js) — 주소는 /parts/<파트ID>/<파일>
│   ├─ tests/test_<파트ID>.py        파트 테스트
│   ├─ data/                         파트 샘플 데이터
│   └─ docs/README.md · NOTES.md     파트 설명서 · 작업 메모
├─ core/                    공통 화면: base.css(디자인) · core.js(REBOOT) · assets · vendor(Chart.js·marked·DOMPurify)
├─ server/core|auth|catalog 공통 서버 (셸 · 로그인 · 시즌/팩트시트)  ← 공통 담당
├─ server/registry.py       메뉴 목록 · 불러올 파일                  ← 공통 담당 (자기 줄 수정은 PR)
└─ data/                    공용 초기 데이터 (seasons.csv · products.json)
```

| 파트 ID | 메뉴 | 담당 | 화면 폴더 | 서버 폴더 | CSS 접두사 |
|---|---|---|---|---|---|
| home | 홈 · 데일리 브리핑 | 이현경 | tabs/home/static | tabs/home | `home-` |
| insights | 시장 · 고객 인사이트 | 이현경 | tabs/insights/static | tabs/insights | `ins-` |
| content | 콘텐츠 제작 | 나원 | tabs/content/static | tabs/content | `cnt-` |
| compliance | 컴플라이언스 | 소준희 | tabs/compliance/static | tabs/compliance | `cmp-` |
| cs | 고객 응대 (CS) | 소준희 | tabs/cs/static | tabs/cs | `cs-` |
| partners | 인플루언서 · 파트너 | 혁주 | tabs/partners/static | tabs/partners | `ptn-` · `ptn-out-` · `ptn-rec-` |
| performance | 성과분석 | 혁주 | tabs/performance/static | tabs/performance | `perf-` |
| settings | 설정 및 브랜드 관리 | 이현경 | tabs/settings/static | tabs/settings | `set-` |

불러오는 파일 이름은 `server/registry.py`의 `scripts` · `styles`에 적힌 것만 읽습니다 (없으면 건너뜀).

---

## 2. 파트 화면 (JS) — 팀 README 6-1 형식 그대로

```js
// tabs/home/static/home.js
(function (global) {
  'use strict';
  const REBOOT = global.REBOOT;
  const store = REBOOT.store('home');            // 서버(팀 공용)에 저장: reboot:home:*
  const { esc, toast, kpi, badge, card, fmt } = REBOOT.ui;

  function render(root) {
    root.classList.add('part-home');              // ← 필수: CSS 범위 (.part-home …)
    root.innerHTML = `<div class="kpis">${kpi({ label: '이번 주 매출', value: '—', featured: true, icon: 'dollar' })}</div>`;
    root.addEventListener('click', e => {        // 버튼은 data-act + addEventListener (onclick 금지)
      const b = e.target.closest('[data-act]');
      if (b && b.dataset.act === 'refresh') load(root);
    });
    load(root);
  }

  async function load(root) {
    try {
      const data = await REBOOT.api('/api/home/news');   // GET
      // ...
    } catch (e) { toast(e.message, 'error'); }
  }

  REBOOT.register({
    id: 'home', label: '홈 · 데일리 브리핑', order: 10,
    // title/subtitle을 주면 화면 제목이 바뀜 (홈은 인사말이라 생략)
    render,
    summary: () => ({ label: '이번 주 할 일', value: store.get('openTodos', 0) })   // 메뉴 숫자 · 알림
  });
  REBOOT.expose('home', { seasons: () => REBOOT.data.seasons() });
})(typeof window !== 'undefined' ? window : globalThis);
```

### REBOOT 기능 (core/core.js)

| 기능 | 설명 |
|---|---|
| `register({id, render, title?, subtitle?, summary?})` | 메뉴 등록. `summary()`는 `{label, value, todos?}` — 숫자면 상단 메뉴 배지·종 알림에 표시. `todos`(선택)는 홈 › 오늘 할 일에 모임 (아래) |
| `expose(id, {…})` · `use(id)` | 파트 간 기능 공개·사용 (3장 표). 다른 파트 변수에 직접 접근 금지 |
| `store(id).get(key, 기본값)` · `.set(key, 값)` · `.remove(key)` | 서버 DB(팀 공용)에 저장. 읽기는 동기(페이지에 미리 실림), 쓰기는 0.3초 모아서 전송. 키: 영문·숫자·`._-` 80자 |
| `api(path, body?, {method})` | 같은 주소 서버 호출 → JSON. body 없으면 GET, 있으면 POST(JSON). `FormData`면 파일 업로드. 실패 시 `Error(message)` (서버의 한국어 `error`) |
| `upload(path, formData)` | 파일 업로드 (POST multipart) |
| `log(action, {kind, status, approval, detail})` | 작업 로그 (kind: 수집 · AI 프롬프트 · 승인 · 선정 · 기타) |
| `user()` | `{id, email, name, title, initials, isAdmin}` |
| `today()` | 서버 기준 오늘 `YYYY-MM-DD` (Asia/Seoul) |
| `data.seasons()` · `data.facts()` · `data.brand()` · `data.products()` | 공용 기준 데이터 (페이지에 실림) |
| `page.actions()` | 제목 오른쪽 버튼 자리(Element). 예: `REBOOT.page.actions().innerHTML = '<button class="btn lime" data-act="…">…</button>'` (이벤트는 그 요소에 연결) |
| `page.title(t)` · `page.subtitle(s)` · `page.crumb(t)` · `page.heading(false)` | 제목·부제·위치 표시(홈 — 데일리 브리핑) 바꾸기 · 제목 영역 숨기기 (인쇄용 보고서 화면 등) |
| `page.syncNav()` | 왼쪽 하위 메뉴를 지금 다시 맞춤 (보통은 탭이 바뀌면 자동 — 5장) |
| `ui.esc` | HTML 이스케이프 — **사용자·외부 데이터는 항상 esc()로 감싸서 innerHTML에 넣기** |
| `ui.kpi({label, value, unit, change, foot, featured, icon, tone})` | KPI 카드 (시안의 4칸). `tone: 'down'`이면 빨간 증감 |
| `ui.card(title, html, headExtraHtml)` · `ui.badge(text, tone)` · `ui.empty(text, icon)` · `ui.row(label, html)` | 카드 · 배지(green·orange·red·blue·purple·lime·dark) · 빈 상태 · 한 줄 |
| `ui.modal({title, body, actions:[{label, value, tone, onClick}], wide, onOpen})` · `ui.closeModal(value)` | 모달 → Promise(누른 버튼 value / 닫으면 null). `onClick`이 false를 돌려주면 안 닫힘 · 코드에서 맨 위 모달 닫기 |
| `ui.confirm(message, {ok, danger})` | 확인 창 → Promise<boolean> |

**`summary().todos` — 홈 › 오늘 할 일에 내 파트의 할 일 올리기 (선택)**

```js
summary: () => ({ label: '미처리 문의', value: 3,
  todos: [{ id: 'cs:inbox', title: '아마존 부정 리뷰 2건에 답하기', slot: 'morning',
            minutes: 10, href: '/dashboard/cs', detail: '★★☆☆☆ “Tastes too sweet” 외 1건' }] })
```

- `id`·`title`은 꼭 넣습니다. `id`는 `<파트>:<무엇>`처럼 하루 동안 바뀌지 않게 (체크 상태를 이 값으로 저장)
- `slot`: `morning`(아침 확인 · 09:00) · `make`(만들기 · 10:00) · `send`(보내기 · 16:00). 비우면 파트 기본값
- `minutes`: 예상 시간(분). 근거가 없으면 비웁니다 (숫자를 지어내지 않기)
- `href`: `/`로 시작하는 같은 사이트 주소만. 비우면 파트 화면 · `detail`: 오른쪽 패널에 보일 한두 줄 · `source`: 출처 칩 이름 (비우면 파트 기본값)
- `todos`가 없으면 `value > 0`일 때 "`label` N건" 한 줄이 자동으로 생깁니다. 할 일이 처리돼 목록에서 빠지면 홈에서 완료로 남깁니다
| `ui.toast(msg, 'error'?)` · `ui.busy(btn, true/false)` | 알림 · 버튼 로딩 표시 |
| `ui.download(파일명, 내용, mime)` · `ui.csv(rows)` | 내려받기 (파일명은 `reboot-<파트ID>-내용.확장자`) · CSV 문자열(BOM 포함) |
| `ui.markdown(md)` | 마크다운 → 소독된 HTML (`.prose` 안에 넣기) |
| `ui.fmt.num/krw/usd/pct/signed/date/datetime/relative/dday` | 숫자·날짜 표시 |
| `ui.icon(name)` | 선 아이콘 SVG (grid home spark shield chat users chart calendar settings bell arrow up plus image video doc mail search globe check download clock filter edit dollar refresh trash upload link user x) |
| `global.Chart` | Chart.js 4.4 (차트) |

### 이름 규칙 (팀 README 6-2)
- 전역 변수 금지 (`REBOOT`만 공통) · 즉시실행 함수로 감싸기 · `type="module"`·`import`·npm 금지
- CSS: `.part-<ID> .<접두사>-…` 로만. `button {…}`처럼 전체 스타일 금지. **base.css 클래스는 다시 정의하지 않고 가져다 씀**
- HTML id는 가능하면 `data-ref`, 꼭 필요하면 접두사 (`id="home-news"`)
- 다운로드 파일명 `reboot-<파트ID>-내용.확장자`
- 키·비밀번호는 JS에 절대 넣지 않음 → 서버(`envkeys.get`)에서만

---

## 3. 파트끼리 공개하는 기능 (팀 README 5장 · Flask 버전)

함수가 Promise를 돌려줄 수 있으니 쓰는 쪽은 `await Promise.resolve(REBOOT.use('x').f())`로 받습니다. 공개한 파트가 없거나 실패하면 쓰는 쪽은 빈 값으로 넘어가야 합니다 (`try/catch`).

| 파트 | 공개 기능 | 돌려주는 모양 | 쓰는 파트 |
|---|---|---|---|
| compliance | `review(text, {channel:'b2c'\|'amazon'\|'cs'\|'b2b', category:'FOOD', product:'jelly'\|'jelly-ls'\|'drink'\|'drink-ls'})` | 동기 · `{ verdict:'BLOCK'\|'REVISE'\|'COND'\|'REVIEW'\|'PASS', findings:[{code, name, verdict, terms:[…], reason, alt, basis}], codes:[…], channel, category, product }` — 제품 ID는 팩트시트의 `compliance_id` | CS · 콘텐츠 · 파트너 |
| compliance | `bannedTerms()` · `rules()` · `translate(text)` | `[{term, code, verdict, ko:[…]}]` · `[{code, name, verdict, basis, reason, alt}]` · 번역(Promise) | 파트너 가이드 |
| cs | `stats()` · `classify(text)` | `{open, urgent, overdue, owner_required}` · `'C1'`~`'C10'` | 홈 |
| settings | `facts()` · `brand()` · `products()` | CS 템플릿용 `{brand, product, flavor, ingredients, allergens}` · 브랜드 · 제품 4종 | CS · 콘텐츠 |
| home | `seasons()` | `[{id, name, start, end, category, priority, leadDays, notes, dday, inLead, ongoing}]` | 콘텐츠 · 인사이트 |
| performance | `weekSummary()` | Promise `{start, end, revenue, spend, roas, conversions, cpa, profit, change:{revenue, spend, roas, conversions}, compare:{start,end}\|null, sample:true\|false}` | 홈 KPI |
| content | `campaigns()` | Promise `{total, active, items:[{id, title, status, updatedAt}]}` | 홈 KPI |
| partners | `creatorSales({start, end})` | Promise `{dummy:true, start, end, source, unitPrice, currency, creators:[{name, code, visits, orders, units, revenue, conversion}], totals}` | 성과분석 |
| insights | `searchTrend()` · `personas()` | Promise `{keywords:[{keyword, points:[{date, value}]}], updatedAt}` · Promise `[{name, oneLine, needs, message}]` | 홈 · 콘텐츠 |

`settings`·`home`의 기본값은 core.js가 미리 공개해 둡니다 (서버 데이터 그대로). 파트가 같은 이름으로 다시 `expose`하면 그 함수가 쓰입니다.

> 저장 위치 변경 (팀 README 7장 대비): 브라우저 저장 키 대신 서버 표를 씁니다 — 협업 이력 `reboot:partners:records` → `partners_history`, 팩트시트 `reboot:settings:facts` → `facts`(`/api/facts`), 작업 로그 `reboot:core:logs` → `work_logs`(`/api/worklog`). 컴플라이언스·CS는 `REBOOT.store`를 그대로 쓰되 값이 서버(`store_items`)에 저장됩니다.

---

## 4. 파트 서버 (Flask)

```python
# tabs/home/routes.py
from flask import Blueprint, jsonify
from server import envkeys, worklog, jobs
from server.core.ai import openai_json, openai_ready, AIError
from server.security import ApiError, json_body, clip
from server.catalog import service as catalog      # 시즌 · 팩트시트

bp = Blueprint("home", __name__, url_prefix="/api/home")   # 이름 = 파트 ID, 주소 = /api/<파트ID>

@bp.get("/news")
def news():
    return jsonify(items=[])

@bp.post("/todos")
def add_todo():
    data = json_body()                      # JSON 객체가 아니면 400
    title = clip(data.get("title"), 200).strip()
    if not title:
        raise ApiError("할 일을 입력하세요.")  # → 400 {"error": "..."} 화면에 그대로 표시
    ...
```

- `tabs/<파트ID>/routes.py`의 `bp`를 앱이 자동으로 등록합니다 (`server/__init__.py`의 MODULES). `models.py`가 있으면 표를 자동으로 만듭니다 (`db.create_all`). **표 이름은 `<파트ID>_`로 시작** (예: `home_news_articles`)
- `/api/*`는 로그인한 사용자만 (자동). 쓰기 요청의 CSRF 토큰은 core.js가 자동으로 붙임 → 파트 코드는 신경 쓰지 않아도 됨
- 오류는 `raise ApiError("화면에 보여 줄 한국어 문장", 상태코드)`. 키·내부 정보·예외 원문을 메시지에 넣지 않기
- 키·설정값: `envkeys.get("SERPAPI_KEY")` / `envkeys.has(...)` (설정 › 연결 화면 입력 → .env 순서). 새 키가 필요하면 공통 담당에게 `envkeys.SERVICES`에 추가 요청
- OpenAI: `openai_json(system, user, schema=…, part="home", action="뉴스 요약")` → dict. strict JSON Schema는 모든 속성을 `required`에 넣고 `additionalProperties: false`. 실패하면 `AIError`(자동으로 JSON 오류 응답). 성공 호출은 작업 로그에 자동 기록
- 파일 저장: `Path(current_app.config["DATA_DIR"]) / "uploads" / "<파트ID>" / …` (Render 영구 디스크). 저장소 폴더(코드)에 쓰지 않기
- 오래 걸리는 작업: `jobs.run_background(current_app._get_current_object(), "<이름>", 함수)` · 하루 1회: `@jobs.daily("<이름>", "설명")`
- 작업 로그: `worklog.add("<파트ID>", "수집", "주간 데이터 수집", detail={...})` (이메일·키처럼 보이는 값은 자동으로 가림)
- 외부 호출은 `requests` + `timeout` 필수. 실패해도 화면은 마지막 결과 + "업데이트 실패" 표시
- 시간: 서버 기준 오늘 `catalog._today()` (Asia/Seoul). DB에는 UTC(`server.models.utcnow()`), 내보낼 때 `server.models.iso(dt)`

### 공용 API (공통 담당 제공)
| 주소 | 설명 |
|---|---|
| `GET /api/health` | 키 연결 상태 `{ai, openai, gmail, smtp, keys:{이름: true/false}}` |
| `GET·POST·PUT·DELETE /api/seasons` | 시즌 캘린더 (D-day · `inLead` 포함) · `GET /api/seasons/export.csv` · `POST /api/seasons/import` |
| `GET /api/facts` · `PUT /api/facts/<brand\|제품ID>` | 브랜드·제품 팩트시트 (출처 없으면 '확인 완료' 불가) |
| `GET·POST /api/worklog` | 작업 로그 (`?kind=&part=&limit=&before=`) |
| `GET /api/store` · `PUT·DELETE /api/store/<파트>/<키>` | 파트 저장소 |
| `GET /api/users` · `POST /api/users` · `PATCH /api/users/<id>` | 계정 관리 (관리자) |
| `PATCH /api/me` · `POST /api/me/password` | 내 이름·소속 · 비밀번호 |

---

## 5. 디자인 (core/base.css · 시안 v3 「REBOOT — Marketing Workspace」)

기준 시안: https://superlative-rugelach-18811d.netlify.app/ (2026-10-23 확정)
- **상단 바** = 업무 영역 알약 메뉴 (홈 · 시장·고객 인사이트 · 콘텐츠 제작 · 컴플라이언스 · 고객 응대 · 인플루언서·파트너 · 성과분석). 고른 영역은 검은 알약
- **왼쪽 하위 메뉴** = 고른 영역의 화면들 (흰 바탕 · 고른 항목은 연한 라임 + 점). 위에 영문 표시(`kicker`)와 영역 이름
- 바탕 `#F5F5F8` · 글자 `#191A1D` · 포인트 라임 `#E4FA18` — 컴플라이언스(`--cmp-*`) · CS(`--cs-*`)와 같은 값

**왼쪽 하위 메뉴 만드는 법** — 파트가 탭 줄을 `role="tablist"` / `role="tab"`(+ `aria-selected` 또는 `.active`)으로 그리면
core.js가 그 탭을 왼쪽 메뉴로 옮기고, 화면 안의 탭 줄은 숨깁니다. 파트 코드는 그대로 둡니다.
- 왼쪽 항목을 누르면 원래 탭 버튼을 누른 것과 같음 · 탭이 바뀌면 왼쪽 표시와 위치 표시(`홈 — 데일리 브리핑`)도 따라 바뀜
- 탭 패널(`role="tabpanel"` · `[data-panel]`) 안의 두 번째 탭 줄은 고른 항목 아래에 들여 써서 보임 (인사이트 › 데이터)
- 아이콘: 탭 버튼에 `data-icon="mail"` (없으면 이름으로 고름) · 숫자: 탭 안의 `.muted`/`.count` 숫자 (0은 숨김)
- 왼쪽으로 옮기지 않을 탭 줄: `data-subnav="off"`
- 탭이 없는 파트: `server/registry.py`의 `links=(("이름", "아이콘", "view값"), …)` → `?view=값` 주소 링크 (성과분석: 대시보드 · 주간 보고서)

화면은 이 순서가 기본입니다: **(위치 표시 → 제목) → KPI 4칸(첫 칸 라임) → 2단(작업 카드 + 오른쪽 안내/결과)**. 탭은 왼쪽 메뉴로 갑니다.

```html
<div class="kpis">…ui.kpi() 4개…</div>
<div class="tabs" role="tablist"><button role="tab" aria-selected="true" data-tab="a">탭 1</button><button role="tab" aria-selected="false" data-tab="b">탭 2</button></div>  <!-- 왼쪽 메뉴로 옮겨짐 -->
<div class="workbench">                      <!-- 왼쪽 넓게 + 오른쪽 315px -->
  <section class="card"><div class="card-head"><h2>제목</h2><span class="badge green">상태</span></div> … </section>
  <aside class="stack"><section class="card">…</section></aside>
</div>
```

| 쓰임 | 클래스 |
|---|---|
| 레이아웃 | `kpis`(4칸) `kpis three`·`six` · `workbench` · `home-layout`(본문+288px) · `bottom-grid` · `grid-2` · `grid-3` · `stack` · `filters` · `flex` · `between` |
| 카드 | `card` · `card-head` · `card tinted`(연한 라임) · `card dark` · `season`(어두운 시즌 카드) · `brief-hero` + `hero-stat`(어두운 띠) · `season-card`(`.featured`) + `days-left` |
| 버튼 | `btn` · `btn lime`(=`btn primary`) · `btn dark` · `btn soft` · `btn danger` · `btn ghost` · `btn small` · `btn pill` · `btn block` · `link` · `quiet-link` |
| 상태 | `badge` + `green`·`orange`·`red`·`blue`·`purple`·`lime`·`dark` |
| 선택 | `chips`/`chip`(`.selected`) · `tabs`·`subtabs`(`.active`) · `period`/`seg`(`.selected`) · `choice-grid`/`choice` · `switch` |
| 입력 | `field`(라벨+입력 묶음) · `field-label` · `input` · `field-grid` · `check` · `search` · `dropzone` |
| 표 | `table-wrap` > `data-table` (`.num` 오른쪽 정렬 · `tr.clickable` · `tr.selected` · `tfoot`) |
| 안내 | `note` (`.lime`·`.warn`·`.error`) · `panel-note` · `empty-art` · `result` · `skeleton` · `spinner` |
| 진행 | `workflow-step`(`.current`·`.done`) + `stepnum` · `progress-track > i` · `decision` · `row` · `activity` |
| 홈 조각 | `task` + `task-evidence` · `market-item`/`market-symbol` · `weekdays`/`month-grid` · `calendar-large` > `day` > `event-pill` · `schedule-item`/`date-box` · `section-title` · `three-col` · `news-row` · `insight-strip` · `intro-*`·`launch-card`·`continue-card`(홈 시작하기) |
| 대화·시안 | `message`(`.user` > `.bubble`) · `bot-symbol` · `composer` · `send` · `poster` · `initial` |
| 문서 | `prose` (마크다운 미리보기) |

색은 변수만: `var(--lime)` `var(--ink)` `var(--muted)` `var(--line)` `var(--surface)` `var(--soft)` `var(--green)` … (다크 모드 자동)
`var(--dark)`(검은 버튼·알약)은 다크 모드에서 밝게 뒤집히므로 글자는 `var(--dark-ink)`. 늘 어두워야 하는 보드는 `var(--night)` + 흰 글자

---

## 6. 데이터 원칙 (모든 파트)
- **사실·숫자·FDA 문구를 지어내지 않습니다.** 모르면 `null` · `확인 필요` · 빈 상태로 둡니다
- 더미·샘플·가상값은 화면에 **`예시`·`더미`·`가상값` 배지**로 표시하고, 실제 데이터처럼 보이게 하지 않습니다
- AI 결과는 `AI` 배지 + "참고용". PASS는 법적 승인이 아님. 발송·게시는 사람이 최종 확인
- 실제 고객 개인정보 입력 금지 (시연은 가짜 이름·`example.com`)
- 키가 없으면 기능을 숨기지 말고 **"설정 › 연결에서 키를 넣으면 사용 가능"** 안내 + 가능한 대체 동작(CSV 업로드 등)

## 7. 확인
```bash
.venv\Scripts\python -m pytest                 # 서버 테스트 (인터넷·키 없이)
node --check parts\<파트ID>\<파일>.js           # JS 문법
node dev/test_parts.js                          # 컴플라이언스·CS 판정 엔진·데이터 점검 (소준희 로컬 도구 · dev/는 깃에 안 올라감 · 실패가 있으면 종료 코드 1)
python -m server --port 5000                    # 화면 확인 → http://127.0.0.1:5000
```
