# 📏 REBOOT 팀 규칙 (RULE.md)

> **사람도 AI도 이 파일 하나만 따릅니다.**
> `AGENTS.md` · `CLAUDE.md` · `GEMINI.md`는 AI 도구에게 "이 파일을 읽어"라고 알려 주는 한 줄짜리 쪽지입니다.
> 이 파일과 다른 문서가 다르면 **이 파일이 우선**합니다.

---

## 0. 꼭 지킬 5가지

1. **내 탭 폴더만 고친다.** (`tabs/<내 탭>/`) 공통 폴더(`server/`, `core/`)는 PM과 상의한다.
2. **내 이니셜 브랜치에서만 작업한다.** (예: `NW`) `main`·`pre_main`에는 직접 올리지 않는다.
3. **작업 시작 전에 항상 `pre_main` 최신 내용을 가져온다.** (GitHub Desktop: Branch → Update from pre_main)
4. **비밀 키는 맨 위 `.env`에만** 넣는다. 커밋·카톡·AI 채팅에 올리지 않는다. (공개 저장소)
5. **AI에게 시킬 때도 이 규칙을 따르게 한다.** 파일을 바꾸기 전에 무엇을 바꿀지 먼저 보여 달라고 한다.

---

## 1. 팀과 담당

| 이니셜 | 이름 | 역할 | 맡은 탭 |
|---|---|---|---|
| **JH** | 준희 | 팀장 | 컴플라이언스(`compliance`), 고객 응대(`cs`) |
| **HK** | 현경 | PM · 저장소 관리자 | 홈(`home`), 시장·고객 인사이트(`insights`), 설정(`settings`), 로그인 |
| **KHS** | 강현서 | PM | 개발 탭 없음 (다른 업무) · PR 검토 |
| **YHS** | 양효선 | 팀원 | 개발 탭 없음 (다른 업무) |
| **NW** | 나원 | 팀원 | 콘텐츠 제작(`content`) |
| **HJ** | 혁주 | 팀원 | 인플루언서·파트너(`partners`), 성과분석(`performance`) |

- **팀장**: 일정, 이슈 우선순위, 담당 조정
- **PM**: PR 검토·병합, `pre_main → main` 승인
- 이니셜이 곧 내 브랜치 이름입니다. 겹치는 사람은 성까지 넣습니다. (KHS, YHS)
- 담당이 바뀌면 팀장이 이 표, `server/registry.py`(화면에 보이는 담당 이름), `.github/CODEOWNERS`, 각 탭 설명서 첫 줄을 한 PR로 고칩니다.

---

## 2. 폴더 구조

```text
reboot/                       ← 저장소 맨 위
├─ README.md                  프로젝트 소개
├─ RULE.md                    이 파일 (모든 규칙)
├─ AGENTS.md · CLAUDE.md · GEMINI.md   AI용 쪽지 ("RULE.md 읽어")
├─ .env                       내 비밀 키 (각자, GitHub에 안 올라감)
├─ .env.example               .env 빈 양식 (키 이름만)
├─ .gitignore                 GitHub에 안 올릴 목록
│
├─ tabs/                      ★ 팀원 작업 공간
│   └─ <탭>/                  예: tabs/content/
│       ├─ routes.py · *.py   탭 서버 (API 주소는 /api/<탭>/...)
│       ├─ static/            탭 화면 (JS · CSS)
│       ├─ tests/             탭 테스트
│       ├─ data/              탭 샘플·더미 데이터
│       └─ docs/README.md     탭 설명서 (첫 줄에 담당자)
│
├─ server/                    공통 서버 (로그인, 메뉴, 저장소, AI 도우미)
├─ core/                      공통 화면 (디자인 base.css, 기능 core.js)
├─ data/                      공용 데이터 (시즌 · 제품 정보)
├─ tests/                     공통 테스트
├─ docs/                      DEV_GUIDE.md(코드 짜는 법) · DASHBOARD.md(대시보드 상세) · 원본/(보관)
└─ 설정 파일                  requirements.txt · render.yaml · wsgi.py 등 — 신경 쓰지 않아도 됨
```

### 내가 고쳐도 되는 곳 / 상의해야 하는 곳
| 구분 | 위치 |
|---|---|
| ✅ 내 탭 | `tabs/<내 탭>/` 안의 모든 파일 |
| ⚠️ 공통 (PM과 상의 후 PR) | `server/`, `core/`, `data/`, `tests/`, `requirements.txt`, `.env.example`, `render.yaml`, `.github/`, 이 파일과 README, AI 쪽지 3개, `.claude/`, `.geminiignore` |
| ❌ 다른 사람 탭 | 고치지 않는다. 필요하면 담당자에게 요청한다 |

- 다른 탭의 기능이 필요하면 **가져다 쓰기만** 한다. 화면은 `REBOOT.use('<탭>')`, 서버는 공용 API(`/api/seasons`, `/api/facts` …)로. (자세히: `docs/DEV_GUIDE.md`)
- 공통 파일 변경이 필요하면 GitHub Issue에 `[공통 요청] 무엇이 필요한지`로 올려도 된다.

---

## 3. 처음 설정 (Windows · 한 번만)

```cmd
git clone https://github.com/hungungworld/reboot-dashboard.git
cd reboot-dashboard
conda activate myenv          (또는: python -m venv .venv → .venv\Scripts\activate)
pip install -r requirements-dev.txt
copy .env.example .env        (메모장으로 .env를 열어 키·관리자 계정 채우기)
python -m server              → 크롬에서 http://127.0.0.1:5000
```

- Python 버전은 3.14입니다. (`.python-version`)
- 처음 실행할 때 계정이 없으면 `.env`의 `ADMIN_EMAIL` · `ADMIN_PASSWORD`(8자 이상)로 관리자가 만들어집니다. 팀원 계정은 관리자가 **설정 › 계정**에서 만듭니다.
- `.env`를 고치면 서버를 **Ctrl+C로 끄고 다시 켜야** 반영됩니다. 새로고침만으로는 안 됩니다.
- 서버를 끌 때는 꼭 Ctrl+C. 창만 닫으면 예전 서버가 남아 새 서버가 안 뜰 수 있습니다.

**push 차단 장치 (각자 PC에 한 번)** — `.git\hooks\pre-push` 파일(확장자 없음)을 만들고 아래를 붙여 넣습니다.

```sh
#!/bin/sh
while read local_ref local_sha remote_ref remote_sha; do
  case "$remote_ref" in
    refs/heads/main|refs/heads/pre_main)
      echo "main/pre_main에는 직접 push할 수 없습니다. PR을 이용하세요."
      exit 1 ;;
  esac
done
exit 0
```

---

## 4. 브랜치

| 브랜치 | 뜻 |
|---|---|
| `main` | 최종 안정 버전. **배포는 main에서만** |
| `pre_main` | 모두의 작업이 모이는 곳 (기본 브랜치) |
| `내 이니셜` (예: `NW`) | 내 작업 공간. 한 번 만들고 **계속 사용**, 병합돼도 지우지 않음 |
| `hotfix/이니셜-내용` | `main`이 고장 났을 때만. 고친 뒤 지움 |

```text
내 브랜치(NW) ── PR ──▶ pre_main ── PR (PM 승인) ──▶ main ──▶ 배포
```

---

## 5. 매일 작업 순서

| 순서 | GitHub Desktop | 명령어 |
|---|---|---|
| ① 최신 내용 가져오기 | 내 브랜치 선택 → Fetch origin → **Branch › Update from pre_main** | `git fetch origin` → `git merge origin/pre_main` |
| ② 작업 | 내 탭 폴더만 수정 → 확인(§8) | |
| ③ 커밋 | 바꾼 파일만 체크 → 메시지 `feat: …` | `git add 파일` → `git commit -m "feat: …"` |
| ④ 올리기 | **Push origin** | `git push` |
| ⑤ PR | **Create Pull Request** → base가 **pre_main**인지 확인 | |

- 처음 한 번: `pre_main` 선택 → **New branch** → 이름에 내 이니셜 → **Publish branch**
- 한 브랜치에는 열린 PR이 하나만 있을 수 있습니다. **PR 올리기 → 병합 → 다음 작업** 순서로 합니다.
- 두 탭을 맡은 사람도 **PR 하나에는 한 탭의 작업만** 담습니다.
- rebase, force push는 쓰지 않습니다. 충돌은 merge로 풀고, 남의 코드면 그 사람과 상의합니다.

---

## 6. 커밋 메시지

형식: `타입: 무엇을 했는지` (한국어)

| 타입 | 언제 | 예 |
|---|---|---|
| `feat` | 기능 추가·변경 | `feat: 채널별 카피 카드 추가` |
| `fix` | 버그 수정 | `fix: 복사 버튼 오류 수정` |
| `docs` | 문서 | `docs: 콘텐츠 설명서 보완` |
| `chore` | 설정·정리 | `chore: requirements 정리` |

브랜치 이름에는 종류가 없으니, **기능인지 수정인지는 이 타입으로만 구분**됩니다. 꼭 붙입니다.

---

## 7. PR · 검토 · 병합

**PR 본문에 적을 것**
```markdown
## 무엇을 바꿨나요
## 바꾼 파일
## 확인한 것 (테스트 통과 수, 직접 본 화면)
## 공통 영역·다른 탭 영향 (없음 / 있음)
## 패키지·환경변수 변경 (없음 / 있음)
## AI 도구 사용 (없음 / 있음: 도구 이름, 쓴 부분)
```

| 경로 | 병합 조건 | 누가 병합 |
|---|---|---|
| 내 브랜치 → `pre_main` | **자동 테스트 통과** + 작성자가 아닌 1명 승인 | PM |
| 관리자(HK)의 PR → `pre_main` | 자동 테스트 통과를 **직접 확인**하고 예외 병합 가능 | 본인 |
| `pre_main` → `main` | 아래 확인 + **PM 1명 승인** (연 사람 말고 다른 PM) | PM |

- 병합 방식은 모두 **Create a merge commit**입니다. (Squash·Rebase는 꺼져 있음)
- 병합 후 **내 이니셜 브랜치는 지우지 않습니다.** (Delete branch 누르지 않기)
- GitHub이 탭 담당자에게 자동으로 검토를 요청합니다. (`.github/CODEOWNERS`)
- 검토는 24시간 안에 승인 또는 코멘트를 남기는 것을 목표로 합니다.

**`pre_main → main` 전에 확인할 것**
- [ ] `pre_main`을 새로 받아 `pip install -r requirements-dev.txt`
- [ ] `python -m server` 로 오류 없이 실행, 이번에 들어간 화면을 직접 눌러 봄
- [ ] 로그인 · 홈 등 주요 화면이 깨지지 않음
- [ ] `python -m pytest` 통과
- [ ] `.env`, DB, 업로드 파일이 들어가지 않음

**긴급 수정(hotfix)**: `main`에서 `hotfix/이니셜-내용` 브랜치 → `main`으로 PR(PM 승인) → 병합 직후 `main → pre_main` PR로 같은 수정을 반영 → hotfix 브랜치 삭제

---

## 8. 코드 규칙

**확인 (작업이 끝날 때마다, 저장소 맨 위에서)**
```cmd
python -m pytest                                  서버 테스트 (인터넷·키 없이 통과해야 함)
python -m pytest tabs\content\tests                내 탭 테스트만
node --check tabs\<탭>\static\<파일>.js            JS 문법
python -m server                                  화면 확인 http://127.0.0.1:5000
```

**하는 것**
- 새 기능·파일을 만들기 전에 내 탭과 `core/core.js`, `server/`에 같은 역할이 있는지 먼저 찾는다.
- 탭 화면·서버 만드는 법, 이름 규칙(CSS 접두사, `REBOOT.store('<탭>')`, 파일명)은 `docs/DEV_GUIDE.md`를 따른다.
- DB 표 이름은 탭 이름으로 시작한다. (예: `content_campaigns`)
- 키는 서버에서 `envkeys.get("키이름")`으로만 읽는다. 새 키가 필요하면 `server/envkeys.py`에 추가해 달라고 PM에게 요청한다.
- AI: 홈 · 인사이트 · 콘텐츠 · 파트너 · 성과분석은 OpenAI, 컴플라이언스 · CS는 Claude(Anthropic). OpenAI는 `server/core/ai.py` 도우미를 쓴다.
- 새 패키지는 PM과 상의 후 `requirements.txt`에 버전을 고정해 한 줄 추가한다. (`pip freeze`로 덮어쓰지 않기)

**하지 않는 것**
- npm · React · 빌드 도구, `type="module"` · `import`
- API 키·비밀번호를 코드·HTML·JS에 넣기
- 사실·숫자·FDA 문구 지어내기 → 모르면 `null` · `{확인 필요}`. 더미 데이터는 화면에 `예시`·`더미` 배지
- 컴플라이언스 PASS를 법적 승인처럼 표시하기, 사람 확인 없는 자동 발송
- 저장할 때 포매터로 **파일 전체** 정렬 (`black .`, "저장 시 서식 지정") — 바뀐 줄이 많아져 충돌남

---

## 9. 비밀 정보와 데이터

- `.env`는 **저장소 맨 위에 하나만** 둡니다. 서버는 이 파일만 읽습니다.
- 새 환경 변수는 `.env.example`에 **이름만** 추가하고(값은 비움) PR에 적습니다. `.env.example`에 실제 키를 넣지 않습니다. (GitHub에 올라가는 파일)
- 이 저장소는 **공개 저장소**입니다. 한 번 올린 비밀은 지워도 기록에 남습니다. 실수로 올렸다면 즉시 PM에게 알리고 그 키를 **폐기 후 재발급**합니다.
- 개인정보(이름·연락처·학번 등), 실제 고객 데이터, 회의록은 올리지 않습니다. 크롤링은 robots.txt·이용약관을 확인한 공개 페이지만, 연락처는 공식 연락 페이지 URL만 저장합니다.
- 테스트에는 가짜(합성) 데이터를 씁니다.

---

## 10. AI 사용 규칙

**AI가 쓴 코드의 책임은 커밋한 사람에게 있습니다.** 위 규칙(§0~§9)은 AI에게도 똑같이 적용됩니다.

### 10.1 대화를 시작할 때 이렇게 말한다
```text
"저장소의 RULE.md와 내 탭 설명서 tabs/<탭>/docs/README.md를 먼저 읽고, 지킬 규칙을 3줄로 요약해줘."
"나는 ○○(이니셜)이고 내 탭은 tabs/<탭>/ 야. 이 폴더 밖은 고치지 말고 필요하면 보고만 해줘."
"파일을 바꾸기 전에 비슷한 기능이 있는지 먼저 찾고, 바꿀 파일과 내용을 보여준 다음 내 승인을 기다려줘."
"지금 브랜치가 내 이니셜 브랜치인지 확인해줘. commit·push는 하지 마."
"명령은 Windows cmd 기준으로 알려주고, .env나 API 키 내용은 절대 읽거나 출력하지 마."
```

### 10.2 AI가 파일을 직접 고치는 도구 (Claude Code · Codex · Gemini CLI · Cursor 등)
- **읽기는 바로, 고치기는 승인 후.** 고치기 전에 아래 형식으로 보고하고 기다린다.
  ```text
  [변경 제안]
  - 수정: tabs/content/routes.py — 무엇을 왜
  - 영향: 공통 영역·다른 탭 영향 없음 / 있음(누구)
  - 확인: python -m pytest, 화면 어디
  진행할까요?
  ```
- 승인은 보고한 파일에만 유효하다. 목록에 없던 파일을 고쳐야 하면 다시 묻는다.
- `commit`, `push`, `merge`, `reset`, `checkout` 등 Git 쓰기 명령은 **매번 허락**을 받는다. force push, `reset --hard`, `main`/`pre_main` 직접 push는 허락해도 하지 않는다.
- 끝나면 바꾼 파일, 실행한 확인과 결과, 못 한 확인을 보고한다. 직접 실행해 보기 전에는 "완료"가 아니다.
- 요청하지 않은 리팩터링, 이름 일괄 변경, README·테스트·설정 파일 추가를 하지 않는다.

| 도구 | 자동으로 읽는 파일 | 안전 설정 |
|---|---|---|
| Claude Code | `CLAUDE.md` | 팀 설정 `.claude/settings.json`이 위험 명령을 막고 Git 쓰기는 묻게 함. 모드는 **기본/수동**, "권한 무시(--dangerously-skip-permissions)"는 금지 |
| Codex | `AGENTS.md` | 개인 설정 `%USERPROFILE%\.codex\config.toml`에 `sandbox_mode = "read-only"`, `approval_policy = "on-request"`. `--yolo`·`--full-auto`는 쓰지 않음 |
| Gemini CLI | `GEMINI.md` | `.geminiignore`가 `.env`·DB를 막음. 승인 모드는 기본, `--yolo` 금지 |
| Cursor · Copilot 등 | `AGENTS.md` | 자동 적용 모드 끄기. 새 도구는 PM에게 먼저 알리기 |

- "항상 허용(always allow)"은 `git commit`, `git push`, 파일 삭제에 쓰지 않는다.
- 클라우드에서 PR을 만드는 기능(Codex 클라우드, Copilot coding agent)도 base는 `pre_main`, 본문은 §7 형식. 실제 키를 클라우드 환경에 넣지 않는다.

### 10.3 대화형 AI (ChatGPT, Claude·Gemini 웹)에서 코드를 가져올 때
- [ ] 코드를 읽고 이해했다 (설명 못 하는 코드는 커밋하지 않는다)
- [ ] 우리 폴더 구조·이름에 맞다, 이미 있는 기능과 겹치지 않는다
- [ ] 지어낸 함수·옵션이 없다 (실행해서 확인했다)
- [ ] 새 패키지가 없다, 또는 PR에 적었다
- [ ] 키·비밀번호·`.env` 내용·개인정보를 대화창에 붙여 넣지 않았다 (필요하면 `API_KEY=xxxx`처럼 가림)
- 긴 답변을 파일 통째로 덮어쓰지 않는다. 바뀐 부분만 옮긴다.
- 팀 공용 지침(ChatGPT 프로젝트·Gemini Gem)은 PM이 만들어 공유한다. 지식 파일로 `RULE.md`, `README.md`를 올린다.

---

## 11. GitHub 설정 (PM이 관리)

| 설정 | `main` | `pre_main` |
|---|---|---|
| PR 필수 · 승인 1명 · 새 커밋 시 승인 다시 | ✅ | ✅ |
| 자동 테스트(test) 통과 필수 | — | ✅ |
| CODEOWNERS(PM) 승인 필수 | ✅ | — |
| force push · 삭제 금지 | ✅ | ✅ |
| 병합 방식 | Merge commit | Merge commit |
| 예외 | 없음 | **저장소 관리자(HK)**는 PR 병합 때 규칙 예외 가능 |

- 기본 브랜치: `pre_main` · Squash/Rebase 병합 끔 · 병합 후 브랜치 자동 삭제 끔
- Secret scanning · Push protection 켬
- **배포(Render)**: 저장소 맨 위 `render.yaml`로 만들고, **Branch는 반드시 `main`**. 키는 Render Environment 또는 배포 후 설정 › 연결 화면에 넣는다.
- 외부 사람의 PR은 병합하지 않고 닫는다.

---

## 12. 규칙 바꾸기

- 이 파일은 `docs:` PR로만 고칩니다. 팀장 또는 PM이 검토합니다.
- 규칙이 실제 작업과 맞지 않으면 언제든 Issue로 제안합니다.
