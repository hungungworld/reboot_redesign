# 인플루언서 · 파트너 — Flask 이관 메모 (NOTES)

> Streamlit 원본(README.md)을 Flask 대시보드의 한 파트로 옮긴 기록입니다. 기능 설명은 README, 여기는 **어디에 무엇이 있는지 · README와 다른 점 · 남은 일**만 적습니다.

## 1. 파일 구성 (팀 README 4장 — 파일별 담당)

| 파일 | 하는 일 | CSS 접두사 · 저장 키 |
|---|---|---|
| `tabs/partners/static/partners.js` | 대표: 메뉴 등록 · 탭 틀 · 제목 옆 '업체 파일 가져오기' 버튼만 | `ptn-` · `partners.tab.u<id>` |
| `tabs/partners/static/outreach.js` · `data-outreach.js` | 🔎 업체 컨택(3-1) · ✉️ 협업 메일(3-2) · 📘 협업 가이드(3-3) | `ptn-out-` · `outreach.*` |
| `tabs/partners/static/records.js` · `data-records.js` | 🗂️ 협업 이력(3-4) · 협업 보드 · 📈 판매 실적(3-5) · `creatorSales` | `ptn-rec-` · `records.*` |
| `tabs/partners/static/partners.css` | 세 파일의 스타일 (`.part-partners` 안에서만) | — |
| `tabs/partners/routes.py` | `/api/partners/*` 전체 (주소 목록은 파일 맨 위 설명) | — |
| `tabs/partners/models.py` | 표 `partners_contacts` · `partners_history` · `partners_attachments` | — |
| `tabs/partners/importer.py` | 업체 파일 읽기 · 데이터 매핑 · **`SUBCATEGORIES`(세부 카테고리 키워드)** | — |
| `tabs/partners/prompts.py` | 메일·가이드·번역·회신 요약 프롬프트 · **`DEFAULT_PROMPTS`** · **`GUIDE_PURPOSES`** | — |
| `tabs/partners/mailer.py` | Gmail SMTP 발송 · IMAP 답장/반송 확인 (테스트는 `connect_smtp`·`connect_imap`을 바꿔 끼움) | — |
| `tabs/partners/guide_docx.py` | 가이드 Markdown → Word | — |
| `tabs/partners/sales.py` | 크리에이터별 판매 실적 더미 데이터 | — |
| `tabs/partners/data/examples.json` | '예시 데이터로 둘러보기' · 양식 내려받기용 가상 업체 2곳씩 (example.com) | — |
| `tabs/partners/tests/test_partners.py` | 서버 테스트 (OpenAI·SMTP·IMAP 가짜) + JS 문법 확인(`node --check`) | — |

- 기능 파일은 화면을 직접 등록하지 않고 `REBOOT.expose('partners.outreach' | 'partners.records', { render, … })`로 공개하고, `partners.js`가 탭을 누를 때 `render(el, 'contacts'|'mail'|'history'|'sales')`를 부릅니다.
- 다른 탭으로 이동할 때는 `.part-partners` 요소에 `CustomEvent('ptn:go', {detail:{tab}})`를 보냅니다 (탭은 주소 `#contacts · #mail · #history · #sales`로도 열림).
- 같은 파트 안에서 서로 부르는 기능: `partners.outreach` → `compose(업체)` · `openReply(이력)` · `openImport()` / `partners.records` → `board(el, {onPick})` · `refreshSummary()` · `awaitingCount()` · `focus(업체 id)`.
- 기준 데이터는 compliance·cs와 같은 방식으로 `window.REBOOT_DATA.partners.outreach` · `.records`에 둡니다.

## 2. 저장 위치

| 무엇 | 어디 |
|---|---|
| 업체 목록 | `partners_contacts` — 같은 구분 파일을 다시 올리면 그 구분만 바뀜. id 규칙: `C`+No(3자리) · `I`+번호(3자리) · 숫자가 아니면 `CX01`·`IX01`(예시·테스트 행) |
| 협업 이력 | `partners_history` — 업체 id·이름·구분을 복사해 두므로 업체 파일이 바뀌어도 이력은 남음 |
| 첨부파일 | 파일: `DATA_DIR/uploads/partners/attachments/<uuid>_<secure_filename>.<확장자>` · 화면 이름(한글 포함)은 `partners_attachments.filename` |
| 메일 초안 · 가이드 · 업체 필터 | `REBOOT.store('partners')` 사용자별 키 `outreach.draft.u<id>` · `outreach.guide.u<id>` · `outreach.filters.u<id>` |
| 등록한 생성 프롬프트 | `outreach.prompts` (팀 공용 · 비우거나 ↺ 누르면 서버 기본값) |
| 이력 표 항목·순서 · 이력 필터 · 실적 기간 | `records.columns.u<id>` · `records.filters.u<id>` · `records.sales.u<id>` |
| 메뉴 배지 '회신 대기' 숫자 | `records.awaiting` (이력·보드·발송 때 갱신하는 캐시 — summary가 동기라서) |

## 3. 발송 · 답장 확인 규칙

- 발송은 **'확인' 체크 + 📨 버튼**일 때만 (서버도 `confirm: true`가 아니면 400). 수신자·제목·본문·첨부를 바꾸면 체크가 풀림. 자동 발송 없음.
- Gmail(SMTP SSL `smtp.gmail.com:465`, 앱 비밀번호)이 없으면 버튼이 꺼지고 '설정 › 연결' 안내. 발송을 시도하면 성공·실패 모두 이력에 저장 (첨부 이름 · 발송 계정 또는 오류 · Message-ID).
- 진행 상태 자동 규칙: 새 메일 → '컨택 중'(이미 협의 중 이상이면 그대로) · 회신에 답장 → '협의 중'(협업 중 이상이면 그대로). 그 밖은 편집·수기 기록으로만.
- 📥 답장 확인(IMAP `imap.gmail.com:993`): `\All` 표시가 있는 전체보관함을 **읽기 전용**으로 열고 `BODY.PEEK`로 읽음(읽음 표시 안 바뀜). Message-ID가 있으면 In-Reply-To/References로 정확히, 없으면 수신자 + `Re:` 제목으로 추정(메모에 '추정'). 반송은 mailer-daemon·postmaster 알림의 `X-Failed-Recipients`·`Final-Recipient`. 대상은 '발송 성공 · 회신 대기'(샘플 제외).
- 협업 가이드 '✅ 확인 — 이메일에 첨부'는 `REBOOT_{유형}_협업가이드_YYYYMMDD.docx`로 보관함에 넣고 체크합니다. 같은 날 같은 유형 가이드를 다시 첨부하면 보관함의 예전 파일을 새 파일로 바꿉니다 (같은 이름이 두 번 첨부되지 않게).
- 발송은 됐는데 이력 저장이 실패하면 응답에 기록을 돌려주고, 화면에서 '기록만 다시 저장'(`POST /api/partners/history {restore}`) — 다시 보내지 않음 (README 완료 기준의 미완료 항목 반영).

## 4. README와 다른 점

1. **`compliance_terms.csv` 대신 컴플라이언스 파트 공용 엔진**을 씁니다: 메일·가이드 점검은 브라우저에서 `REBOOT.use('compliance').review(text, {channel:'b2b'})`, 가이드의 Do & Don't는 `bannedTerms()` · `rules()`의 BLOCK·REVISE 표현 40개를 AI에 넘겨 만듭니다. 그래서 `/api/compliance` 주소는 파트너 서버에 없습니다. 가이드 점검은 기본으로 'Do & Don't' 절을 빼고 점검합니다 (README 7장 '가이드 점검 범위' 결정 대기 → 체크로 바꿀 수 있게 둠).
2. `history.db` → 앱 DB의 `partners_history` · 첨부 `data/attachments/` → 위 2장 위치. 로그인은 `APP_PASSWORD` 대신 앱 계정 로그인, Gmail 설정은 사이드바 대신 **설정 › 연결**(예전 이름 `gmail_address`·`gmail_api`도 읽음).
3. REST 주소는 `/api/partners/*`로 모았습니다 (`/api/partners?type=&q=`는 README 주소 그대로 둠). 모든 주소가 로그인 필요, 발송 API는 첨부파일 지원.
4. 예시 샘플 4건을 DB 생성 때 넣지 않습니다. 업체가 없을 때 **'예시 데이터로 둘러보기'**를 누르면 가상 업체 2곳씩(예시 배지 · example.com) + 샘플 이력 2건을 넣습니다. 샘플은 상단 숫자·메뉴 배지·답장 확인·판매 실적에서 빠집니다.
5. 번역은 서버 OpenAI(`POST /api/partners/translate`)로 합니다 (컴플라이언스의 `translate()`는 브라우저 내장 영→한만 가능해서 양방향이 안 됨).
6. 시안의 클래스 이름은 접두사 규칙에 맞춰 바꿨습니다: `partner-hero`→`ptn-out-hero` · `partner-detail`→`ptn-out-detail` · `partner-stats`→`ptn-out-stats` · `kanban`→`ptn-rec-kanban` · `kanban-col`→`ptn-rec-col` · `mini-card`→`ptn-rec-card` (값은 시안 그대로, 다크 모드는 변수). 협업 보드 열은 시안의 4열 대신 README 진행 상태 6열(보류·중단 한 열).
7. 상단 숫자(어두운 띠): 컨택 업체 수 · 진행 중인 협업(협의·협업·마케팅) · 회신 받음 — 모두 실제 이력 기준 업체 수(샘플 제외).
8. 판매 실적 더미: 이름·날짜마다 고정 시드라 언제 불러도 같은 값, **가상 단가 $24**(제품 가격 미정이라 더미임을 화면에 표시), 환불 약 5%는 합계에서 제외, 기간은 오늘(Asia/Seoul)까지 60일.
9. 첨부 한도는 README대로 원본 합계 25MB로 막습니다. Gmail은 인코딩 후 크기(약 1.37배)로 세므로 실제로는 약 18MB부터 거절될 수 있습니다.

## 5. 키 · 실제 파일이 있어야 확인되는 것 (테스트는 가짜로만 확인)

- OpenAI 실제 결과 품질: 메일(150–200단어 · 모든 항목 반영), 번역의 서식 유지, 가이드 8개 절 구성, 회신 요약.
- Gmail 실제 발송(앱 비밀번호 · 465 포트 — Render는 starter 이상) · IMAP 실제 검색(HEADER In-Reply-To/References 지원, 언어별 전체보관함 이름, 반송 알림 형식).
- 실제 `top50catering.csv` · `US_Influencer_Public_Contacts_72.xlsx`의 열 이름 (README 매핑 + 비슷한 이름도 받도록 함 · 6행이 아니면 앞 30행에서 머리글을 찾고 안내). 세부 카테고리 키워드는 실제 파일을 보고 `SUBCATEGORIES`를 다듬어야 함.
- Word에서 열었을 때 모양 (맑은 고딕 · 페이지 번호 필드) — 테스트는 python-docx로 다시 여는 것까지만 확인.
- 실제 브라우저 화면 (이번 작업은 jsdom으로 화면 흐름·콘솔 오류만 확인, 차트는 그려 보지 못함).

## 6. 공통 담당에게 요청

1. `server/envkeys.py` `SERVICES`에 `REGULATION_DB_URL`(설정값) 추가 · `.env.example` 파트너 칸에 `REGULATION_DB_URL=` 추가 — 지금은 `envkeys.get`의 환경변수 대체로만 읽힘. 통합 DB가 정해지면 `routes._regulation_db()`에 실제 접속 확인을 넣습니다.
2. `core/core.js` `updateCounts()`: 값이 0이 되면 메뉴 배지를 숨기기 (지금은 0보다 클 때만 바꿔서 `refreshCounts()` 뒤에도 예전 숫자가 남음).
3. `core/core.js` `ui.modal`: 코드에서 닫는 방법(예: `onOpen(body, close)`) — 지금은 `[data-modal-close]` 버튼을 눌러 닫습니다.
4. DEV_GUIDE 3장 표: `creatorSales` 반환값에 `start · end · source · unitPrice · currency`가 더 있음 (기존 모양은 그대로). 팀 README 7장 표의 `reboot:partners:records`는 서버 표 `partners_history`로 바뀜.

## 7. 확인 방법

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tabs/partners/tests/test_partners.py tests/test_core.py
node --check tabs/partners/static/partners.js   # 나머지 js도 같은 방법 (pytest에도 포함)
```
