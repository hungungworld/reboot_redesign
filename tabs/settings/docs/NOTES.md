# 설정 및 브랜드 관리 — 구현 노트 (v1 · 2026-10-23)

README(설정 README)를 Flask 버전으로 옮긴 결과와 알아둘 점이에요.

## 화면 (tabs/settings/static/settings.js · settings.css)

| 탭 | 하는 일 | 저장 |
|---|---|---|
| 브랜드 · 제품 | 브랜드 기본 정보 폼 · 컬러 견본(HEX를 적으면 표시) · 제품 4종 시트(근거 상태 · 맛 임시 · 가격/원가 `가상값` 배지) · 제품 상세 창(출처 목록 포함) · 규칙 카드 | 브랜드: 오른쪽 위 **변경 사항 적용** → `PUT /api/facts/brand` · 제품: 창의 저장 → `PUT /api/facts/<id>` (출처 없이 '확인 완료'면 서버 오류를 창에 그대로 표시) |
| 수집 설정 | 검색어 세트 A · B · K · 영상 최소 조회수 · 검색어당 영상 수 · 영상 언어 · Amazon 검색어 · 상세 조회 수 · 추적 ASIN(10자리 검사 · 자사 표시) · 자사 상품 표시 단어 · Weee! 주소 · 제외 단어 · 트렌드 키워드 · 예상 사용량 · 모니터링 시즌 표 | 설정: **변경 사항 적용** → `PUT /api/settings/collect` · 시즌: 창에서 바로 (`/api/seasons` 추가 · 수정 · 삭제 · monitor 스위치 · CSV 받기/가져오기) |
| 연결 | API 키 표(쓰는 곳 · 무료 한도 · 상태 · 출처 · 끝 4자리) · 설정값 표 · Google 로그인(CS Gmail) · 처음 한 번만(개발 담당) | 키 입력은 관리자만 (창에서 바로 저장 → 테스트 가능한 키면 자동으로 연결 테스트) |
| 작업 로그 | 구분 · 파트 필터 · 더 보기(50건씩) · 상세 창(AI 프롬프트는 모델 · 토큰 · 프롬프트 · 응답) · CSV · Excel(구분별 시트) | 보기만 (고치기 · 지우기 없음) |
| 계정 (`#account`) | 내 정보(이름 · 소속) · 비밀번호 변경 · 계정 관리(관리자: 추가 · 관리자/사용 스위치 · 비밀번호 재설정 · 잠금 해제 · 마지막 로그인) | 내 정보: **변경 사항 적용** · 나머지: 바로 |

- 주소 끝 `#brand` `#collect` `#connections` `#worklog` `#account`로 탭을 바로 열 수 있어요. Google 로그인에서 돌아오면(`?gmail=connected|failed|denied`) 연결 탭이 열리고 알림이 떠요.
- 저장 안 된 변경이 있으면 제목 옆에 주황 배지가 보이고, 페이지를 떠날 때 브라우저가 한 번 물어봐요.
- 메뉴 숫자: 연결 안 된 핵심 서비스 수(OpenAI · Anthropic · SerpApi · YouTube · Firecrawl) — 관리자에게만 보여요 (`/api/health`의 키 상태만 씀).
- `REBOOT.expose('settings', { facts, brand, products })` — 동기 함수. `facts()`는 CS 템플릿 모양 `{brand, product:null, flavor:null, ingredients:null, allergens:null}` (제품별 값은 CS가 제품마다 채움). 저장하면 바로 새 값이 공개돼요.

## 서버 (tabs/settings/)

| 주소 | 설명 |
|---|---|
| `GET/PUT /api/settings/collect` | 수집 설정 (기본값은 코드 `config.DEFAULTS`, 고친 값은 `settings_collect_config` 한 줄) |
| `GET /api/settings/connections` | `envkeys.status_list()` + 상태(정상 · 오류 · 미연결 · 확인 전) + 마지막 테스트 + Google 로그인 준비 상태 · 리디렉션 URI |
| `PUT /api/settings/connections/<NAME>` | 관리자만. `{value}` 빈 값이면 화면 입력 삭제 → .env로. 키는 공백 · 줄바꿈 거절, Gmail 앱 비밀번호는 띄어쓰기 제거. 작업 로그에는 값 없이 '연결 값 저장: SerpApi'만 |
| `POST /api/settings/connections/<NAME>/test` | 연결 테스트 (누구나). 결과는 `settings_connection_checks`에 저장 — 키 값이 아니라 테스트할 때의 출처 · 끝 4자리(설정값은 짧은 해시)만 저장해서, 키가 바뀌면 '확인 전'으로 돌아감 |
| `GET /api/settings/worklog.xlsx` | 작업 로그 Excel (시트: 전체 · 수집 · AI 프롬프트 · 승인 · 선정 · 기타). '='로 시작하는 글자는 수식이 아닌 글자로 저장 |

`from server.settings.config import get_collect_config` — 인사이트가 쓰는 접근 함수 (항상 모든 항목이 있는 dict).

### 연결 테스트 방식

| 서비스 | 확인 방법 (요금 거의 없음) |
|---|---|
| OpenAI | `GET /v1/models` (설정한 모델이 목록에 있는지도) |
| Anthropic | `GET /v1/models` (`x-api-key` · `anthropic-version: 2023-06-01`) |
| SerpApi | `account.json` → 플랜 · 이번 달 남은 검색 (검색 횟수를 쓰지 않음) |
| YouTube | `videos?chart=mostPopular&maxResults=1` (1 unit) |
| Firecrawl | `GET /v1/team/credit-usage` → 남은 크레딧. 주소가 없으면(404) `fc-` 형식만 확인 |
| 한국수출입은행 | 오늘 환율(AP01) — 휴일 · 오전 11시 전이면 빈 목록이라 최대 5일 전까지 |
| Gmail SMTP | `smtp.gmail.com:465` 로그인만 (메일은 보내지 않음) |
| 그 밖 | 테스트 미지원 (버튼 대신 안내) |

외부 호출은 `connections._get` · `_smtp_login`만 써서 테스트에서 바꿔 끼워요. 예외 문장에는 주소(=키)가 섞일 수 있어 예외 이름만 보여줘요.

## 테스트

`tabs/settings/tests/test_settings.py` — 로그인 필요 · 수집 설정 저장/검사 · 연결 목록에 값이 안 나옴 · 키 입력 관리자 전용 · 로그에 값 없음 · 서비스별 연결 테스트(가짜 응답) · 네트워크 오류에 키가 안 섞임 · Gmail SMTP · 작업 로그 Excel 시트 · 수식 주입.

## 실제 키 · 인터넷으로는 확인하지 못한 것

- 연결 테스트의 실제 응답 모양 (특히 Firecrawl `team/credit-usage`, 한국수출입은행 새 주소 `oapi.koreaexim.go.kr`, Anthropic models). 가짜 응답으로만 확인했어요.
- Google 로그인 왕복 (CS 파트 서버 기능을 그대로 부름).

## 알아둘 점 · 가정

- 수집 설정 기본값(검색어 세트 · 트렌드 키워드 · Weee! 검색 주소 `https://www.sayweee.com/en/search?keyword=hangover` · 자사 표시 단어 `aldicom`/`알디콤`)은 출발점일 뿐이에요. 실제 Weee! 주소 · 알디콤 ASIN은 확인해서 넣어 주세요 (지어내지 않음).
- 제품 상세 창은 `name` · `ko` · `code`(내부 이름)는 보기만 하고 고치지 않아요. 알레르기 성분(`allergens`)은 CS 템플릿에 쓰여서 함께 넣었어요.
- 시즌 표의 D-day · '진행 중'은 서버 기준 오늘(Asia/Seoul)이에요.
