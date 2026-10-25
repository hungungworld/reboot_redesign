# 🏠 홈 · 데일리 브리핑

> 담당: 이현경 (HK)

마케터가 매일 아침 대시보드에 들어와 **"지금 어떤 시즌이 다가오고, 미국 주류 업계에서 무슨 일이 있고, 이번 주에 뭘 해야 하는지"**를 한 화면에서 확인하는 페이지입니다.

## 구성

| 섹션 | 데이터 소스 | 처리 방식 | 갱신 |
|---|---|---|---|
| 📅 시즌 캘린더 | `data/seasons.csv` (수동 관리) | CSV 로드 → D-day 계산 | 페이지 로드 시 |
| 📈 다음 급등 예상 (콘텐츠 캘린더 맨 아래 · 접기) | 인사이트 검색 트렌드 (`/api/insights/data/trends`) | 작년 같은 시기 검색 vs 12개월 평균 | 인사이트 데이터 업데이트 (주 1회) |
| 📰 미국 주류 업계 뉴스 | 구글 뉴스 RSS (키 없음) | 수집 → 중복·노이즈 제거 → AI 필터링·기사별 한국어 요약 | 1일 1회 자동 + '지금 수집' |
| ✅ 이번 주 To-Do | 위 두 섹션의 결과 | AI가 종합해 생성 | 주 1회 (월요일) + 수동 재생성 |

---

## 1. 📅 시즌 캘린더

미국 주류 소비가 몰리는 시즌을 수동으로 관리합니다. 다가오는 시즌을 D-day 순으로 보여주고, 준비 기간(`lead_days`)에 들어온 시즌은 강조 표시합니다.

### CSV 스키마 (`data/seasons.csv`)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `name` | str | 시즌 이름 |
| `start_date` | YYYY-MM-DD | 시작일 |
| `end_date` | YYYY-MM-DD | 종료일 (하루짜리면 시작일과 동일) |
| `category` | str | `holiday` / `sports` / `occasion` 등 |
| `priority` | int | 1(최우선) ~ 3 |
| `lead_days` | int | 캠페인 준비 시작 시점 (시즌 N일 전) |
| `notes` | str | 메모 (주력 제품, 지난해 성과 등) |

### 예시

```csv
name,start_date,end_date,category,priority,lead_days,notes
Halloween,2026-10-31,2026-10-31,holiday,2,21,
Thanksgiving Eve,2026-11-25,2026-11-25,holiday,1,30,연중 최대 음주일 중 하나
New Year's Eve,2026-12-31,2026-12-31,holiday,1,30,
Super Bowl LXI,2027-02-14,2027-02-14,sports,1,30,
```

> 날짜가 매년 바뀌는 시즌(추수감사절, 슈퍼볼 등)은 연초에 갱신이 필요합니다.

---

## 2. 📰 미국 주류 업계 뉴스

미국 매체의 주류 관련 보도를 모아, 마케터에게 중요한 기사를 한국어로 정리하고 보도 급증을 표시합니다.

> ⚠️ **한계**: 기사 **제목 기준** 요약이라 본문은 보지 않습니다 (페이월·저작권). 판매량·가격·검색 수요는 인사이트 탭에서 봅니다.

### 데이터 소스: 구글 뉴스 RSS (팀 결정)

- 주소: `https://news.google.com/rss/search?q={검색어} when:14d&hl=en-US&gl=US&ceid=US:en` (키 없음)
- 검색어: `data/queries.json` — 분야 7개 · 검색어 9개 (2026-10-24 팀 결정 · 후보 20개를 실제 검색해 보고 고름)

| 분야 | 종류 | 검색어 |
|---|---|---|
| 숙취 카테고리 | signal | `hangover (supplement OR electrolyte OR DHM OR probiotic)` |
| 경쟁사 | signal | `ZBiotics OR "Liquid I.V." OR DripDrop OR "Cheers Restore"` (경쟁사 분석 상위 브랜드로 월 1회 갱신) |
| 규제 | signal | `FDA "dietary supplement"` |
| 음주 행태 | flow | `"sober curious" OR "drinking less" OR "Gen Z drinking"` · `"alcohol consumption" (survey OR study OR Gallup)` |
| 무알코올 · 기능성 | flow | `"non-alcoholic" (beer OR spirits OR beverage)` · `"functional beverage" OR "functional drinks"` |
| 주류 판매 | flow | `"alcohol sales" OR "spirits sales" OR "beer sales"` |
| K-푸드 | flow | `"Korean hangover" OR "Korean snacks" OR "K-food" (US OR America)` |

  - **signal**: 기사는 적지만 꼭 봐야 하는 분야 → 카드 자리 보장 · **flow**: 기사가 많은 흐름 → AI 점수로 경쟁
- 항목마다 제목 · 매체 · 링크 · 날짜만 읽고, 제목 끝의 " - 매체명"은 뗍니다
- 코드: `rss.py` (읽기 · 노이즈 판정 · 급증 계산) · `news.py` (수집 · 저장 · AI · 화면용 데이터)

### 수집 방식

- **하루 1회 자동**(하루 1회 작업) + 화면의 **"지금 수집"** 버튼. 화면을 열 때는 저장된 결과만 읽습니다
- **규칙으로 먼저 거름 (무료, `queries.json`의 `exclude` · `filters`)**
  - 사고 · 범죄: DUI · drunk driving · crash · arrest · killed · fatal · underage · minors
  - 비유 hangover: AI · tariff · election · market … + hangover (진짜 숙취인 holiday hangover는 남김)
  - 인사 소식: names · appoints · hires … 와 CEO · president · chief … 가 함께 있는 제목
  - 미국 밖 시장: UK · India · China … 가 있고 US · America가 없는 제목
  - 한국어 제목: K-푸드 검색어만 허용
- URL · 제목 기준 중복 제거, 최근 7일 기사로 정리
- 실패하면 마지막 결과를 그대로 보여 주고 "업데이트 실패" 배지 표시. 연달아 3번 실패하면 남은 검색어는 건너뜀

### AI 처리 (OpenAI 1회)

1. **관련성 필터링**: 최근 7일 기사 최대 150개의 제목 · 매체 · 날짜 · 분야를 보고 관련 기사만 고름
2. **점수 (후보 최대 15개)**: REBOOT와의 거리 0~2 · 실행 가능성 0~2 · 출처 0~2 · 새로움 0~1

| 기준 | 2점 | 1점 | 0점 |
|---|---|---|---|
| REBOOT와의 거리 | 숙취 제품·성분 · 경쟁 브랜드 · 영양제/식품 규제 | 음주 행태 · 무알코올·기능성 · K-푸드 | 주류 업계 일반 |
| 실행 가능성 | 이번 주 카피·캠페인·채널에 바로 반영 | 참고 | 없음 |
| 출처 | 공공기관 · 업계지 · 연구·조사 | 주요 언론 | 보도자료 · 블로그 · 쇼핑 추천글 |
| 새로움 | – | 처음 나온 소식 | 반복 |

3. **최종 8개 (서버가 고름)**: signal 분야는 2개씩 먼저 자리 보장 → 한 분야는 3개까지 → 나머지는 점수순
4. **기사별 정리**: 한국어 제목 · 한 줄 요약(제목에서 알 수 있는 사실만) · **마케터의 시선** 한 줄 · 주제
5. **한 줄 정리**: 전체를 2문장 이내로 — 무엇이 일어나고 REBOOT에 무슨 의미인지

### 검색어 점검 (월 1회)

뉴스 카드 아래 **"검색어 점검 (최근 60일)"**: 검색어마다 찾은 기사 · 규칙 통과 · 카드로 고른 수. 30일 넘게 돌렸는데 카드가 0개면 **교체 후보**로 표시합니다. 교체는 `queries.json`을 고치고 '지금 수집'으로 확인합니다.

### 보도 급증 시그널

검색어별로 **최근 7일 기사 수 vs 그 전 7일 기사 수**를 비교합니다. 최근 7일이 **5건 이상이고 1.5배 이상**이면 "보도 급증"으로 표시하고, 그 검색어로 들어온 기사 카드에도 태그를 붙입니다. To-Do 추천의 근거로도 씁니다.

### 화면

데일리 브리핑의 **미국 시장 브리핑**(성과 차트 아래)은 3초 안에 훑어보게 짰어요: 헤더 한 줄(제목 · 배송 시각 · 새 소식 N · 업계/트렌드 버튼 · 새로고침) → **오늘의 핵심** 1개(점수가 가장 높은 기사: 큰 제목 · 요약 · 마케터의 시선) → 짧은 목록(번호 · 제목 · → 마케터의 시선 · 분야 · 시간 · 책갈피, 처음 3개 + 더보기, 요약 · 영어 원제목은 마우스를 올리면) → 하단 한 줄(보관함 N건 · AI 배지 · 수집 정보 · 검색어 점검 접기). 읽은 기사는 회색 (읽음 기록은 각자 브라우저에만: REBOOT.store('home') newsRead, 최근 300건). OpenAI 키가 없으면 규칙을 통과한 최근 기사 제목만 목록으로 보여 줍니다.

---

## 3. ✅ 이번 주 To-Do

시즌 캘린더(다가오는 시즌·준비 기간)와 뉴스 동향 요약·급증 시그널을 AI에 함께 넘겨 이번 주 실행 항목을 생성합니다.

- 각 항목에 **근거**(어떤 시즌/뉴스 때문인지)를 함께 표시
- 매주 월요일 자동 생성, 필요 시 수동 재생성
- 체크 완료 상태 저장

---

## 데이터 흐름

```
seasons.csv ───────────────────────────────────────────┐
                                                       ├──▶ AI 종합 ──▶ 이번 주 To-Do
구글 뉴스 RSS ──▶ 수집 ──▶ DB(home_news_articles) ──▶ AI 필터링·기사별 요약 ─┘
                   └──▶ 날짜로 보도 급증 계산 ──────────┘
```

## 파일

```
tabs/home/
├── routes.py      # API (/api/home/...) · 하루 1회 작업
├── rss.py         # 구글 뉴스 RSS 읽기 · 노이즈 판정 · 보도 급증 계산
├── news.py        # 수집 · 저장 · AI 필터링·요약 · 화면용 데이터
├── todos.py       # 이번 주 To-Do
├── models.py      # DB 표 (home_news_articles · home_news_runs · home_todos …)
├── data/queries.json   # 뉴스 검색어 · 노이즈 단어 · 급증 기준
├── static/home.js · home.css
└── tests/test_home.py
```

시즌 캘린더는 공용 `data/seasons.csv`(설정 탭에서 편집)를 씁니다.

## 환경 변수 (`.env`)

```
OPENAI_API_KEY=     # 없으면 AI 요약 없이 최근 기사 제목만 표시
```

> 구글 뉴스 RSS는 키가 필요 없습니다.

## 실행 · 확인

```cmd
python -m server                          # 데일리 브리핑 → 뉴스 '지금 수집'
python -m pytest tabs\home               # 홈 테스트 (인터넷 · 키 없이)
```

## 로드맵

- [x] 구글 뉴스 RSS로 수집 · 기사별 한국어 요약 · 보도 급증
- [ ] 시즌 알림 팝업 (D-10 이내)
- [ ] 뉴스 검색어를 화면에서 바꾸기
- [ ] 보도 급증을 검색어 단위가 아니라 기사 묶음(주제) 단위로
