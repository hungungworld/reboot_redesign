# 📊 REBOOT US 마케팅 대시보드 — Flask · Render 통합본

> 무역 AX2기 2차 미니프로젝트 3조 · 알디콤을 모티브로 한 숙취해소 젤리·음료 **REBOOT**의 미국 마케팅 업무를 한곳에서 처리하는 대시보드입니다.
> 각 담당이 만든 README(`tabs/<파트ID>/docs/README.md`)를 하나의 Flask 앱으로 합치고, 시안 「REBOOT — Marketing Workspace」 디자인으로 통일했습니다.

> ⚠️ 판정·초안·AI 결과는 1차 참고용입니다. **PASS(규칙 미탐지)는 법적 승인이 아니며**, 발송·게시는 담당자가 최종 확인합니다. 샘플·더미 데이터는 화면에 배지로 표시됩니다.

---

## 0. 한눈에 보기

| 항목 | 내용 |
|---|---|
| 구조 | Flask 서버(`server/`) + 공통 화면(`core/`) + 파트 화면(`tabs/<파트ID>/static/`) — 팀 README의 파트 규칙(`REBOOT.register/expose/use/store`) 그대로 |
| 로그인 | 이메일·비밀번호 (Flask-Login · CSRF · 5회 실패 시 10분 잠금 · 로그인 유지 14일) — 로그인 README 그대로 |
| 저장 | 서버 DB(SQLite, Render 영구 디스크). 예전에 브라우저에만 남던 `REBOOT.store` 값도 **서버에 저장되어 팀원 모두 같은 데이터**를 봄 |
| 키 | `.env` / Render Environment / **설정 › 연결 화면**(암호화 저장, 화면 입력이 우선). HTML·JS에는 키가 없음 |
| AI | 홈·인사이트·콘텐츠·파트너·성과분석 = OpenAI, 컴플라이언스·CS = Claude (각 README 기준) |
| 배포 | Render Blueprint (`render.yaml`) — gunicorn · 영구 디스크 1GB · 상태 확인 `/healthz` |
| 디자인 | 시안 v3 (https://superlative-rugelach-18811d.netlify.app/) — 상단 업무 영역 알약 메뉴 + 흰 왼쪽 하위 메뉴 + 무채색 바탕 + 라임(`#E4FA18`) + 흰 카드 (`core/base.css`) |

---

## 1. 내 PC에서 실행 (Windows)

```powershell
cd reboot-dashboard
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env        # 메모장으로 열어 ADMIN_EMAIL · ADMIN_PASSWORD(8자 이상) 채우기
python -m server              # → http://127.0.0.1:5000
```

- 처음 실행할 때 계정이 하나도 없으면 `.env`의 `ADMIN_EMAIL`·`ADMIN_PASSWORD`로 **관리자 계정**이 만들어집니다. 팀원 계정은 **설정 › 계정**에서 추가합니다.
- 터미널로 만들 수도 있습니다: `flask --app server create-user 이메일 --name 이름 --admin`
- 데이터는 `instance/`(DB·업로드·캐시)에 저장되고 깃에는 올라가지 않습니다.
- 테스트: `python -m pytest` (인터넷·키 없이 통과)

---

## 2. Render 배포

1. **GitHub 저장소** — https://github.com/hungungworld/reboot-dashboard (대시보드 코드가 저장소 맨 위에 있음). `.env` · `instance/` · `dev/`는 `.gitignore`로 제외됩니다.
   저장소 맨 위의 `render.yaml`(Blueprint)과 `.github/workflows/`를 그대로 씁니다.
2. **Render → New → Blueprint** → 저장소 선택 → **브랜치는 `main`** (저장소 기본 브랜치 `pre_main`이 골라져 있으면 바꿈) → `render.yaml`이 인식됩니다. 파이썬 버전은 `PYTHON_VERSION`(3.14.8)으로 고정되어 있습니다.
3. 처음 만들 때 묻는 값 입력: `ADMIN_EMAIL`, `ADMIN_PASSWORD`(8자 이상), `ADMIN_NAME` — 나머지 키는 비워도 됩니다 (배포 후 화면에서 입력 가능).
   `SECRET_KEY` · `APP_SECRET_KEY` · `CRON_TOKEN`은 Render가 자동으로 만듭니다.
4. **Deploy** → 발급된 주소(`https://<서비스이름>.onrender.com`)로 접속 → 관리자 계정으로 로그인
5. **설정 › 연결**에서 OpenAI · Claude · SerpApi · YouTube · Gmail 등 키 입력 → 연결 테스트
6. (선택) **CS Gmail 문의함** — Google Cloud에서 *웹 애플리케이션* OAuth 클라이언트를 만들고 승인된 리디렉션 URI에 `https://<서비스주소>/api/cs/gmail/callback`을 등록 → `GOOGLE_CLIENT_ID`·`GOOGLE_CLIENT_SECRET` 입력 → 설정 › 연결 › Google로 로그인
7. (선택) **하루 1회 수집**(홈 뉴스·규제 모니터링) — GitHub 저장소 Secrets에 `REBOOT_URL`(배포 주소)·`CRON_TOKEN`(Render 값과 동일)을 넣으면 `.github/workflows/daily-jobs.yml`이 매일 08:00(KST)에 실행합니다.

### 플랜과 비용

| 플랜 | 이 저장소 기본값 | 무료(free) |
|---|---|---|
| `render.yaml` (저장소 맨 위) | `plan: 0.5c-512mb` + 영구 디스크 1GB (`/var/data`) | `plan: free`로 바꾸고 `disk` 블록 삭제 |
| 로그인 계정·이력·업로드 | 재배포·재시작 후에도 유지 | **재배포·재시작·잠들 때마다 지워짐** |
| Gmail SMTP 발송 (파트너 메일) | 가능 | **불가** (465/587 포트 차단) |
| 잠들기 | 없음 | 15분 접속 없으면 잠들고, 다음 접속에 1분 정도 걸림 |

> 무료 플랜은 시연·시험용으로만 권장합니다. 운영은 유료 플랜 + 디스크(또는 `DATABASE_URL`로 PostgreSQL)를 쓰세요.

---

## 3. 메뉴 · 담당 · 상태

각 담당 README를 기준으로 만든 **v1 초안**입니다. 담당자가 이어받아 고칩니다 (팀 README 4장 「초안 → 수정 인계」). 파트별 상세는 `tabs/<파트ID>/docs/NOTES.md`.

| 메뉴 | 파트 ID | 담당 | 구현된 것 | 실제 키·파일로 확인할 것 |
|---|---|---|---|---|
| 로그인 | `auth` | 이현경 | 로그인 README 명세 그대로 (테스트 체크리스트 11개 통과) · 계정 관리 | — |
| 홈 · 데일리 브리핑 | `home` | 이현경 | 화면 3개: 시작하기 · 데일리 브리핑(히어로 · 시즌 카드 · KPI 4 · 이번 주 성과 차트 · To-Do(AI/규칙, 근거 표시) · 미국 주류 업계 뉴스(구글 뉴스 RSS → AI 기사별 한국어 요약·보도 급증) · 최근 활동) · 콘텐츠 캘린더(시즌·준비 시작일) | 구글 뉴스 RSS 실수집 · OpenAI 요약 |
| 시장 · 고객 인사이트 | `insights` | 이현경 | 주간 리뷰(규칙 6개로 이번 주 액션) · 월간 리뷰 · 데이터 6탭 · 수집기(SerpApi Amazon·Trends · YouTube · Firecrawl Weee!) · 1회분 단가 · Word/Excel | 각 API 실제 응답 형식 · Weee! 주소·알디콤 ASIN 입력 · 정확도 검증 탭은 자리만 |
| 콘텐츠 제작 | `content` | 나원 | 5항목 시트 · AI 대화(서버가 변경안 검증) · 확정/제안/미정 · A/B/C 버전·부분 수정 · 이미지 작업 · 컴플라이언스 점검 · 내 캠페인 · ZIP | OpenAI 실호출(JSON 스키마·이미지 모델) · LangGraph 대신 순수 Python 흐름 · 영상은 구성안만 |
| 컴플라이언스 | `compliance` | 소준희 | 기존 `tabs/compliance/static` + 서버 API(AI 2차 판정 · 한글 요약 · 모니터링 수집) · 시연본 경고장 원문 53개 · 안전 문구 7개 이관 | Claude 키 · 규칙 검증에서 PASS로 나오는 경고장 문구 5개 → 규칙 보강(파트 할 일 2단계) |
| 고객 응대 (CS) | `cs` | 소준희 | 기존 `tabs/cs/static` 그대로 + Gmail 문의·답장 초안 · AI 초안 API · 웹 Google 로그인 | Google OAuth 클라이언트 · 테스트 Gmail |
| 인플루언서 · 파트너 | `partners` | 혁주 | 업체 파일 가져오기 · 업체 컨택 · 협업 메일(AI·번역·문구 점검·Gmail 발송) · 협업 가이드(Word) · 협업 이력(IMAP 답장 확인·AI 요약) · 판매 실적(🧪 더미) | 실제 업체 CSV·XLSX · Gmail 앱 비밀번호 (유료 플랜) |
| 성과분석 | `performance` | 혁주 | 샘플 CSV(표시됨) · 업로드(합치기/바꾸기·백업) · 기간 비교 · KPI 6 · 채널 표(원/달러·환율) · 차트 · AI 예산 제안(±20%) · A4 보고서 | 실제 성과 CSV · KEXIM 키 · OpenAI |
| 설정 및 브랜드 관리 | `settings` | 이현경 | 브랜드·제품 팩트시트 · 수집 설정(시즌 편집 포함) · 연결(키 입력·연결 테스트·Google 로그인) · 작업 로그(CSV/Excel) · 계정 | 각 서비스 연결 테스트 |

공용 데이터(공통 담당): 시즌 캘린더 `/api/seasons` (`data/seasons.csv`로 시작) · 브랜드·제품 팩트시트 `/api/facts` (`data/products.json`, 콘텐츠 README 3장의 2026-10-23 기준) · 작업 로그 `/api/worklog` · 계정 `/api/users`

**확인한 것**: pytest 221개 · JS 문법 18개 파일 · core.js·성과 계산 단위 확인 · 컴플라이언스·CS 점검(소준희 로컬 `node dev/test_parts.js` — 경고장 문구 PASS 5개만 남음) · 8개 메뉴 브라우저 화면(콘솔 오류 0) · 모바일 폭 · 다크 테마 · openFDA 실호출(키 없이 리콜 8건).
키가 필요한 외부 연동(OpenAI · Claude · SerpApi · YouTube · Firecrawl · KEXIM · Gmail · Regulations.gov · CourtListener)은 **가짜 응답으로만 시험**했습니다. 실제 키로 한 번씩 확인이 필요합니다.

---

## 4. 폴더 구조

```text
저장소 맨 위/
├─ tabs/<파트ID>/          파트 폴더: routes.py · models.py(서버) · static/(화면 JS·CSS) · tests/ · data/ · docs/
├─ server/                 Flask 앱 (공통)
│   ├─ __init__.py         create_app · __main__.py 로컬 실행
│   ├─ config.py · extensions.py · models.py · envkeys.py · security.py · jobs.py · worklog.py · registry.py
│   ├─ core/               화면 셸 · 파트 파일 · 공용 저장소 · 작업 로그 · 상태 API · OpenAI 도우미
│   ├─ auth/               로그인 · 로그아웃 · 계정 관리 API · 터미널 명령
│   ├─ catalog/            시즌 캘린더 · 팩트시트 (공용 기준 데이터)
│   └─ templates/          base · shell(상단 바·왼쪽 하위 메뉴) · 로그인 · 오류
├─ core/                   base.css(디자인) · core.js(REBOOT) · assets(로고) · vendor(Chart.js · marked · DOMPurify)
├─ data/                   공용 초기 데이터 (seasons.csv · products.json)
├─ tests/                  공통 테스트
├─ docs/                   DEV_GUIDE.md(개발 규칙) · DASHBOARD.md(이 문서) · login/ · 원본/(보관)
├─ render.yaml · wsgi.py · gunicorn.conf.py · requirements.txt · .python-version · .env.example
├─ .github/workflows/      tests.yml(PR 자동 확인) · daily-jobs.yml(하루 1회 수집)
└─ README.md · RULE.md · AGENTS/CLAUDE/GEMINI.md   프로젝트 소개 · 팀 규칙 · AI용 쪽지
```

---

## 5. 데이터 저장과 보안

- **DB**: `DATA_DIR/reboot.db` (SQLite · WAL). `DATABASE_URL`을 주면 PostgreSQL 사용
- **파트 저장소**: `REBOOT.store('<파트ID>')`는 그대로 쓰되 값은 서버에 저장 → 팀원 공유 (읽기는 페이지를 열 때 받아 둠, 쓰기는 0.3초 모아 전송)
- **업로드·캐시**: `DATA_DIR/uploads` · `cache` · `backups` · `parts_override`(서버가 만든 `monitor-data.js` 등)
- **키**: 화면 입력(암호화, 끝 4자리만 표시) → 환경변수 순서로 읽음. 예전 이름(`open_api`, `gmail_api` …)도 읽음
- **로그인 보안**: 비밀번호 해시 · CSRF(같은 주소 요청에 core.js가 토큰 자동 첨부) · 세션 쿠키 HttpOnly/Secure/Lax · 오픈 리다이렉트 차단 · IP당 분당 10회 · 비밀번호 변경 시 다른 기기 로그인 해제
- **작업 로그**: AI 호출·수집·승인 기록 자동 저장 (이메일·키는 가림 · 수정·삭제 없음)

---

## 6. 팀 작업 방법

- 개발 규칙: **[DEV_GUIDE.md](DEV_GUIDE.md)** (파트 모양 · 서버 규칙 · 디자인 클래스 · 파트 간 공개 기능 표)
- 팀 규칙 전부: [RULE.md](../RULE.md) (담당 · 브랜치 · PR · 코드 규칙 · AI 사용 규칙)
- Git: 개인 이니셜 브랜치(예: `NW`) → **Update from pre_main** → 자기 폴더만 수정 → `python -m pytest` → 커밋 `feat:`/`fix:` → `pre_main`으로 PR (GitHub Actions가 테스트 자동 실행)

---

## 7. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 로그인 화면에 "등록된 계정이 없습니다" | `ADMIN_EMAIL`·`ADMIN_PASSWORD`(8자 이상) 넣고 다시 시작, 또는 `flask --app server create-user` |
| "로그인 시도가 너무 많습니다" | 10분 뒤 다시 시도 · 관리자가 설정 › 계정에서 잠금 해제 |
| AI 버튼이 꺼져 있음 | 설정 › 연결에서 해당 키 입력 → 연결 테스트 |
| "보안 토큰이 맞지 않습니다" | 페이지 새로고침 (오래 열어 둔 화면) |
| Render에서 재배포 후 데이터가 사라짐 | 무료 플랜(디스크 없음)인지 확인 → 유료 플랜 + 디스크 |
| 파트너 메일 발송 실패 | Gmail 앱 비밀번호(16자리) · 무료 플랜은 SMTP 차단 |
| 코드 수정이 화면에 안 보임 | `Ctrl + F5` (파일 주소에 버전이 붙어 보통은 자동 갱신) |
