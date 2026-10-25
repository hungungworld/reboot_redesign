# 🛡️ 컴플라이언스 · 💬 고객 응대(CS)

> 담당 **소준희** · REBOOT US 마케팅 대시보드 (무역 AX2기 2차 미니프로젝트 3조)
> 이 문서는 소준희 담당 두 파트만 다룹니다. 팀 공통 규칙(전체 메뉴·담당, 폴더, 이름 규칙, Git 순서)은 **팀 README**를 따릅니다.

> ⚠️ 판정과 답변 초안은 1차 참고용입니다. FDA 공식 판단이나 법률 자문이 아니며, 최종 사용·발송 여부는 사람이 결정합니다. **PASS(규칙 미탐지)는 법적 승인이 아닙니다.**

**목차** [0 한눈에](#0-한눈에-보기) · [1 작업 규칙](#1-작업-규칙) · [2 컴플라이언스](#2-컴플라이언스) · [3 CS](#3-고객-응대-cs) · [4 연결](#4-다른-파트와-연결) · [5 저장·보안](#5-저장과-보안) · [6 규제 모니터링](#6-규제-모니터링-출처와-html-구현-가능성) · [7 수출 규제](#7-수출-규제-조사-요약) · [8 검증](#8-검증) · [9 구현 가능성](#9-기술-구현-가능성-점검) · [10 시연](#10-권장-시연-순서) · [11 주의](#11-주의) · [12 결정](#12-확정된-결정--정할-것) · [13 할 일](#13-할-일) · [14 변경 기록](#14-변경-기록) · [부록 A](#부록-a-키-발급--신청--수동-확인) · [부록 B](#부록-b-컴플라이언스-취합-프롬프트-vs-code--claude-code)

---

## 0. 한눈에 보기

| 파트 | 파트 ID | 우선 | 폴더 (수정 가능) | CSS 접두사 | 저장 키 |
|---|---|---|---|---|---|
| 🛡️ 컴플라이언스 | `compliance` | **P0** | `parts/compliance/` · `server/compliance/` · `docs/compliance/` | `cmp-` | `reboot:compliance:*` |
| 💬 고객 응대 (CS) | `cs` | **P1** | `parts/cs/` · `server/cs/` · `docs/cs/` | `cs-` | `reboot:cs:*` |

- **제품** REBOOT 숙취해소 젤리 스틱 · 미국 분류 **일반식품(Conventional Food)**, 건강기능식품 아님
- **두 파트의 관계** CS 답변 초안은 발송 전에 컴플라이언스로 재검사 → **BLOCK이면 발송 불가**, PASS여도 **담당자 최종 승인** 후에만 Gmail 초안으로
- **기술** HTML · CSS · JavaScript (빌드·npm 없음) · 경고장 크롤링·Gmail·키가 필요한 API만 Python 서버(선택)

### 진행 상황 (2026-10-22)

| 구분 | 상태 |
|---|---|
| 시연본 | 단일 HTML(`aldicom-dashboard.html`)에서 두 파트 동작 확인 (아래 표의 '시연본 완료') |
| `parts/` 코드 | 팀 core 형식(1장: `register({ id, render })` · `REBOOT.store('<파트ID>')` · `reboot-<파트ID>-` 파일명)으로 정리 완료 (2026-10-23) · `dev/preview.html`로 화면 확인 · `node dev/test_parts.js` 통과 · **커밋 전** |
| 데이터 | `parts/`로 옮길 것: 경고장 원문 53개 · 안전 문구 8개 · 표현 매핑 10쌍 |
| 서버 | Gmail 연동 · AI 2차 판정 · 경고장 수집 미착수 |

---

## 1. 작업 규칙

팀 README 4·6장 가운데 두 파트에 해당하는 것만 옮겼습니다.

- **자기 폴더만 수정** (0장 표). `index.html`·`core/`·`base.css`·다른 파트 폴더는 읽기만 → 필요하면 GitHub Issues에 `[공통 요청] …`
- **파일 모양** `(function(){ ... })();`로 감싸고 `REBOOT.register()`로 메뉴 등록, 다른 파트에 줄 기능만 `REBOOT.expose()`

```js
// parts/compliance/compliance.js
(function () {
  const store = REBOOT.store('compliance');        // 저장 키가 자동으로 reboot:compliance:* 가 됨
  const { card, esc, toast } = REBOOT.ui;

  function render(el) {                            // el = 이 파트 전용 화면 영역
    el.innerHTML = card('광고 문구 사전 점검', `
      <textarea class="cmp-input" data-ref="copy"></textarea>
      <button class="btn primary" data-act="run">문구 점검하기</button>
      <div data-ref="result"></div>`);
    el.querySelector('[data-act="run"]').addEventListener('click', () => { /* ... */ });
  }

  function review(text) { /* 판정 로직 */ }
  function bannedTerms() { /* 금지 표현 목록 */ }

  REBOOT.register({
    id: 'compliance', icon: '🛡️', label: '컴플라이언스', priority: 'P0', order: 30,
    title: '좋은 카피의 시작, 안전한 표현.',
    subtitle: '광고 문구를 점검하고 미국 시장에 맞는 표현을 준비하세요.',
    render,                                          // 필수
    summary: () => ({ label: '검토 대기 카피', value: store.get('pending', []).length })  // 선택: 홈 To-Do에 사용
  });

  REBOOT.expose('compliance', { review, bannedTerms });  // 다른 파트가 쓸 기능만 공개
})();
```

| 대상 | 규칙 | 예 |
|---|---|---|
| 전역 이름 | 만들지 않음 (`REBOOT`만 공통) | 함수는 모두 즉시실행 함수 안에 |
| 버튼 | `addEventListener` · `data-act` (`onclick=` 금지) | `data-act="run"` |
| CSS | 접두사 + 파트 영역 안으로 한정 · 공통 클래스(`.card` `.btn` `.badge` 등)는 다시 정의하지 않음 | `.part-compliance .cmp-input`, `.part-cs .cs-draft` |
| HTML id | 가능하면 `data-ref`, id는 접두사 | `id="cmp-copy"` |
| 저장 키 | `REBOOT.store('compliance')` · `REBOOT.store('cs')`만 | `reboot:compliance:history` |
| 다운로드 파일명 | `reboot-<파트ID>-내용.확장자` | `reboot-compliance-review.csv` |
| 키·비밀번호 | HTML·JS에 넣지 않음 → 서버 `.env` | — |

- **더블클릭 실행 유지** `type="module"`·`import`·npm 금지 · 데이터는 `.json` 대신 `data.js` · 원본 CSV는 `docs/<파트ID>/`
- **Git** `git pull` → 브랜치 `compliance/<할일>` · `cs/<할일>` (예: `compliance/rules-r1-r10`, `cs/approval`) → 커밋 `[compliance] …` · `[cs] …` → main으로 PR → CODEOWNERS·공통 담당 검토 → 공통 담당이 Merge. `main` 직접 push 금지, PR 하나에 한 가지 일만
- **main에 올림** `parts/compliance/` · `parts/cs/` · `server/compliance/` · `server/cs/`(키 제외) · `docs/compliance/` · `docs/cs/` / **올리지 않음** `dev/` · `_incoming/` · `.env` · 키가 들어간 HTML
- **CODEOWNERS** `/parts/compliance/` · `/parts/cs/` · `/server/compliance/` · `/server/cs/` → `@{소준희_ID}`

```text
parts/compliance/   compliance.js · compliance.css · data.js · monitor-data.js (예정 · 서버가 만든 모니터링 결과, 6장)
parts/cs/           cs.js · cs.css · data.js
server/compliance/  crawler.py · refresh.py · tests/   (선택 · FDA 경고장·관보 수집)
server/cs/          gmail.py · tests/                  (선택 · Gmail 읽기·답장 초안)
docs/compliance/    term_mapping_draft.csv · sources/ · import_refusals/ · 월간 수동 확인 기록 (부록 A)
docs/cs/            템플릿 원문 · 정책 메모 · 테스트 문의 목록
tools/cross_check.js  판정 엔진 비교 (읽기 전용)
dev/                preview.html · core-stub.js · settings-stub.js · test_parts.js   (로컬 확인 전용 · 커밋 제외)
```

**Claude Code 첫 메시지** (팀 README 기준 · `{파트ID}`에 `compliance` 또는 `cs`)

```text
#작업 범위
나는 {파트ID} 담당이야. parts/{파트ID}/ 와 server/{파트ID}/, docs/{파트ID}/ 안의 파일만 고쳐줘.
#반드시
- index.html, core/, 다른 parts/ 폴더는 읽기만 하고 고치지 마
- 다른 파트 데이터가 필요하면 README 5장 표의 REBOOT.use()로만 불러 써
- 공통 기능이 필요하면 고치지 말고 "공통 요청 Issue 문안"으로 써줘
- README 6-2 이름 규칙(접두사, REBOOT.store, data-act)을 지켜줘
- HTML·JS 안에 API 키를 넣지 마
- 고치기 전에 바꿀 파일과 줄을 먼저 보여 주고 허락받아
```

---

## 2. 컴플라이언스

> P0 · 미국에 내보낼 광고 문구(아마존 상세페이지, SNS, CS 답변, 바이어 자료)가 FDA·FTC 기준에 걸리는지 1차 점검

| 기능 | 자료 방식 | 하는 일 | 상태 |
|---|---|---|---|
| 단건 심의 | 입력 + 규칙 (+AI 선택) | 카피 1개 → R1~R10 판정 → 걸린 표현·이유·대체 방향. AI는 FDA 사례 Few-shot 2차 판정 | 규칙 판정 시연본 완료 · AI는 서버 키 있을 때만 |
| 일괄 심의 | CSV 업로드 | `copy` 열 CSV → 결과 CSV (최대 500행) | 시연본 완료 |
| 판정 근거 사례집 | 크롤링 → 데이터 | FDA 숙취 제품 경고장 7건 원문 인용, 금지 표현 JSON | 현재 53개 · 87개로 확장 예정 |
| 한국→미국 표현 매핑 | 수동 | 검토자·검토일 기록 | 현재 10쌍 · 20쌍으로 확장 예정 |
| 규칙 검증 | 자동 | 경고장 문구는 모두 걸리고, 안전 문구는 모두 통과 | 53개 기준 시연본 완료 · 87개 확장 후 재검증 예정 |
| 심의 이력 | 자동 저장 | 검토 요청 → 승인·반려. **BLOCK은 승인 불가** | 시연본 완료 |
| FDA 규제 모니터링 | API + 크롤링 + 수동 | openFDA 식품 리콜 조회, 새 경고장 → '사례집 후보' (출처별 가능 여부 6장) | 리콜 조회 시연본 완료 · 경고장 수집은 서버 이관 예정 |

**전제** 미국 **일반식품** (건강기능식품 기준은 비교 옵션) · FDA는 2020년 숙취 제품 경고장에서 숙취 예방·치료·완화 표현을 미승인 의약품 표현으로 취급 · 일반식품도 **영양적 가치에 기반한 구조·기능 표현**은 FDA가 인정 → R8 일괄 수정은 법적 금지가 아니라 **팀의 보수적 기준** · 면책 별표(*)를 붙여도 판정은 그대로

### 규칙 R1~R10

근거: ⚖️ 공식 근거(법령·FDA/FTC 문서) · 🏢 팀 내부 정책 · 🔍 문맥 검토 필요 — 초안 {확인 필요: 규제 담당 확인 후 확정}

| 코드 | 이름 | 기본 판정 | 근거 | 걸리는 예 | 대신 쓸 방향 |
|---|---|---|---|---|---|
| R1 | 질병·치료 클레임 | BLOCK | ⚖️🔍 | cure, prevent, relief, detox, toxins | 섭취 시점·맛·포맷 등 사실 묘사 |
| R2 | 'hangover' 직접 언급 | BLOCK | ⚖️🏢 | hangover, hang-over, 숙취 | After-Party, Morning-After Ritual |
| R3 | 안전성 보장·간 보호 | BLOCK | ⚖️🔍 | protect your liver, no side effects, 간 보호 | "라벨 확인 + 전문가 상담" 안내 |
| R4 | 알코올 홍조·ALDH2 | BLOCK | 🏢 | asian glow, alcohol flush, 홍조 | 판매 문구에서 제외 |
| R5 | 효과 보장·과장 | REVISE | ⚖️🔍 | guaranteed, clinically proven, 100% | 용량·포장·맛 같은 검증 가능한 사실 |
| R6 | 음주 조장 | REVISE | 🏢 | drink all night, party harder | 음주량·음주 지속 언급 안 함 |
| R7 | 의약품 오인 | REVISE | 🔍🏢 | hangover pills, doctor-formulated, Nature's Aspirin | jelly stick, shot 등 식품 포맷 명칭 |
| R8 | 구조·기능 클레임 | REVISE | 🏢 | supports liver health, 알코올 대사, 보충제용 면책문구 | 삭제 또는 영양 성분 사실만 (영양 근거가 있으면 규제 담당 검토 후 허용 결정) |
| R9 | 인체적용시험 인용 | REVISE (B2B는 REVIEW) | ⚖️🏢 | clinically tested, 임상, 인체적용시험 | B2C에서는 삭제, B2B 자료에서만 |
| R10 | 후기·리뷰 | REVIEW (보상 조건이면 BLOCK) | ⚖️ | testimonials, 5-star, 후기 | 후기도 R1~R7로 심의, 대가 관계 표시 |

- **R1·R2** 질병 예방·치료 주장 = 의약품 표현 (2020 경고장). `detox`·`relief`는 문맥에 따라 다르고, 'hangover' 단순 언급까지 막는 것은 팀 기준
- **R3·R5·R9** 효과·안전성·임상 주장은 FTC 실증 대상. `100% juice`처럼 효과 주장이 아닌 쓰임은 문맥 검토
- **R4·R6** 직접 금지 조문이 아니라 브랜드·플랫폼 리스크를 줄이는 팀 기준 · **R7** 약처럼 보이는 표현은 '의도된 용도' 판단 요소 (단어 하나로 확정하지 않음)
- **R8** 근거 자료 확보 전까지 보수적으로 REVISE · **R10** FTC 추천·후기 지침과 소비자 리뷰 규칙 (대가 관계 표시, 긍정 후기 조건 보상 금지)
- **아직 규칙에 없음:** 당 함량 표시(`Low sugar`·`저당`). 미국은 "Low sugar"를 허용하지 않음 → 규칙 추가 여부 결정 필요 (12장 정할 것 2, 7장)

**판정** BLOCK 사용 불가(위법 소지 큼 → 문장 교체) · REVISE 수정 필요(고쳐서 재점검) · COND 조건부(건강기능식품 비교용) · REVIEW 검토 필요(후기 등 사람이 판단) · PASS 규칙 미탐지(**법적 승인 아님** → 담당자 최종 승인 필요)

**채널** `b2c` B2C 광고·SNS · `amazon` 아마존 상세페이지 · `cs` CS 답변 · `b2b` B2B 바이어 자료 (R9만 채널별로 다름)

**자료 출처**
- 경고장 7건 (2020-07-23 발행, 07-29 공개): Double Wood, Ebnsol, Vita Heaven, Happy Hour Vitamins, LES Labs, Mind Body & Coal, Purple Biosciences · 크롤링 · 인용 문구는 **원문 그대로** (`[]`, `. . .` 포함)
- 새 경고장: fda.gov 경고장 목록 XLSX + 본문 (서버 크롤링) · API: openFDA `food/enforcement`(리콜) · `drug/label`(숙취 표현 허용 의약품 라벨) · Federal Register · eCFR 21 CFR 101.93 (건강기능식품 기준 · 비교용) · 표현 매핑: 팀 작성 (검토자·검토일) · 그 밖의 출처는 6장

**운영 원칙** 수집한 경고장 문구는 **'사례집 후보'**로만 저장 → 사람이 승인 · 규칙은 자동으로 바꾸지 않고 사람이 바꾼 뒤 '규칙 검증'(8장)

---

## 3. 고객 응대 (CS)

> P1 · 문의 분류 → 답변 초안 → 컴플라이언스 재검사 → 담당자에게 넘김. **자동 발송 없음.**

| 기능 | 자료 방식 | 하는 일 | 상태 |
|---|---|---|---|
| 문의함 | Gmail API (서버) · 더미 | 문의 목록, 긴급(부작용) 우선 정렬, 응답 기한 표시 | 더미 12건 시연본 완료 · Gmail은 서버 연동 예정 |
| 문의 분류 | 규칙 (+AI 예정) | 제목·본문 키워드로 C1~C10, 담당자 수정 가능 | 시연본 완료 |
| 답변 초안 | 템플릿 (+AI 예정) | 유형별 영문 템플릿에 이름·주문번호·배송 정보 | 시연본 완료 |
| 컴플라이언스 재검사 | 자동 | `review(…, {channel:'cs'})`, BLOCK이면 발송 불가 | 시연본 완료 (내장 판정 함수) · `REBOOT.use('compliance')` 연동 예정 |
| 에스컬레이션 | 자동 | 부작용·건강·환불·불만 문의는 지정 담당 확인 체크 필수 | 시연본 완료 |
| 최종 승인 | 입력 | 모든 초안(PASS 포함) 담당자 승인 · `[확인 필요]` 남으면 승인·내보내기 차단 · 승인된 초안만 복사 | `parts/` 완료 |
| 부작용 접수 | 입력 | 신고 양식, 빠진 항목 표시, 심각 여부 판정, 단계별 일시 자동 기록 + 직접 수정(수정 이력·순서 경고) | `parts/` 완료 |
| 답변 템플릿 | 데이터 | 유형별 템플릿 + 템플릿별 컴플라이언스 결과 | 시연본 완료 |

### 문의 유형 C1~C10

| 코드 | 유형 | 처리 | 담당 | 기한 | 팀 태그 |
|---|---|---|---|---|---|
| C1 | 부작용 신고 | 🔴 즉시 에스컬레이션 | 품질·규제 담당 | 1시간 | 불만·건강 |
| C2 | 건강·복용·효능 | 🟠 담당자 필수 | CS 리드(건강) | 24시간 | 효과 |
| C3 | 환불·교환 | 🟠 담당자 필수 | CS 리드 | 24시간 | 환불 |
| C4 | 포장·품질 불량 | 🟠 담당자 필수 | 품질 담당 | 24시간 | 포장 |
| C5 | 배송 | 🟢 자동 초안 | CS 담당 | 24시간 | 배송 |
| C6 | 제품 정보 (맛·성분·알레르기) | 🟢 자동 초안 | CS 담당 | 24시간 | 맛 |
| C7 | 가격·프로모션 | 🟢 자동 초안 | CS 담당 | 24시간 | 가격 |
| C8 | 리뷰·평점 (불만) | 🟠 담당자 필수 | CS 리드 | 24시간 | 불만 |
| C9 | B2B·대량 구매 | 🔵 영업 전달 | 해외영업 {확인 필요} | 48시간 | — |
| C10 | 기타·협업 제안 | ⚪ 다른 파트 전달 | 인플루언서·파트너 (혁주·효선) | 72시간 | — |

분류 순서 C1 → C9 → C8 → C10 → C2 → C3 → C4 → C5 → C7 → C6, 아무것도 안 걸리면 C10. '자동 초안'도 최종 승인은 사람이 합니다.

### 처리 흐름과 발송을 막는 규칙

```text
문의 수신 (Gmail 또는 더미) → 자동 분류 C1~C10 (담당자 수정 가능) → 유형별 템플릿 초안
  → 컴플라이언스 재검사 (channel: cs) ─ BLOCK이면 중단 → [확인 필요] 확인 ─ 남으면 중단
  → [즉시 에스컬레이션·담당자 필수] 지정 담당 추가 검토 → 담당자 최종 승인 (모든 초안, PASS 포함)
  → Gmail 답장 초안 (서버) / 발송 완료 표시 (시연) → 종결
```

상태: `답변 대기` → `검토 중` → `승인` → `Gmail 초안 생성` / `발송 완료` → `종결`

| # | 막는 조건 | 범위 | 상태 |
|---|---|---|---|
| 1 | 컴플라이언스 **BLOCK** 표현이 있으면 발송 불가 (어떤 표현인지 표시) | 모든 초안 | 시연본 완료 |
| 2 | `[확인 필요]`가 남으면 최종 승인·Gmail 초안 내보내기 불가 | 모든 초안 | `parts/` 완료 |
| 3 | 담당자 **최종 승인** 전에는 발송 불가 (PASS여도 동일) | 모든 초안 | `parts/` 완료 |
| 4 | 지정 담당의 **추가 검토 체크** 전에는 승인 불가 | C1·C2·C3·C4·C8 | 시연본 완료 |
| 5 | Gmail은 **답장 초안만**, 실제 발송은 사람이 직접 | 모든 초안 | Gmail 연동 예정 |

**답변 원칙** C2는 개인 의료 조언 없이 라벨 확인·의사·약사 상담 안내 · 효능 약속 표현(숙취 해소, 간 보호 등) 금지 · 확인 안 된 정보는 `[확인 필요: 제품 팩트시트의 맛]`처럼 남김 (남아 있는 동안 승인 불가)

### 부작용 접수

- **필수** 고객 · 연락 방법 · 제품 · 로트 번호 · 증상 (빠지면 표시) / **추가** 섭취일 · 진료 여부(없음·외래·응급진료·입원·모름) · 결과
- **심각** = 결과가 입원·생명 위협·장애·사망·의료 처치 필요했음, 또는 진료 여부가 입원 → 아래 순서 · 심각하지 않으면 기록 보관 (같은 로트 반복 시 품질 이슈로)

| 단계 | 시점 | 하는 일 | 기록 |
|---|---|---|---|
| ① 접수 | 고객 문의 수신 | 접수 양식, C1 분류 | 접수 일시 |
| ② 긴급 검토 | 접수 후 1시간 내 (팀 기준 · C1 기한) | 품질·규제 담당 → 같은 로트 이상, 표시 안 된 알레르기 성분, 오염 확인 | 검토 시작 일시 |
| ③ 보고 대상 판단 | 검토 결과가 나온 때 | 책임 주체가 RFR 보고 대상 식품(섭취 시 심각한 건강상 악영향·사망 가능성이 상당한 식품)인지 판단 | **판단 일시**·판단자 |
| ④ RFR 보고 | ③ 이후 가능한 한 신속히, **늦어도 24시간 내** | FDA Reportable Food Registry 보고 | 보고 일시 |

- 24시간은 접수(①)가 아니라 **판단(③)부터**. 그렇다고 ②·③을 미루지 않도록 단계마다 일시 기록
- 보고 책임 주체는 **식품시설 등록(FFR)을 제출한 시설의 책임자**. 원인 확인에 24시간 넘게 걸릴 것 같으면 그 전에 보고하라는 것이 FDA 권고
- {확인 필요: 우리 구조에서 등록 시설이 제조사(한국 공장)인지 미국 창고·수입자인지, ③의 판단 권한자는 누구인지 규제 담당과 확정}

### Gmail 연동 (예정)
개인 Gmail 계정 + Google Cloud 프로젝트 `reboot-local` · 답장 초안은 `gmail.compose`, 문의 읽기는 읽기 권한 별도 · OAuth 동의 화면이 '테스트'면 **7일마다 재로그인** · `file://`에서는 Google 로그인이 안 되므로 `http://localhost`(VS Code Live Server 등)에서 · 시연은 **테스트 Gmail + 가짜 문의 20건** · `credentials.json`·`token.json`은 깃에 올리지 않음

---

## 4. 다른 파트와 연결

| 방향 | 기능 | 상대 파트 |
|---|---|---|
| 공개 | `compliance.review(text, { channel, category })` → `{ verdict, findings[], codes }` (기본 `b2c`/`FOOD`) | CS · 콘텐츠 (나원) · 파트너 가이드 (혁주·효선) |
| 공개 | `compliance.bannedTerms()` → `[{ term, code, verdict, ko[] }]` | 인플루언서·파트너 가이드 |
| 공개 | `compliance.rules()` → R1~R10 코드·이름·판정·근거 구분·이유·대체 방향 | 화면 안내 |
| 공개 | `cs.stats()` → `{ open, urgent, overdue, owner_required }` | 홈 To-Do (강현서) |
| 공개 | `cs.classify(text)` → `'C1'`~`'C10'` | 분류 테스트 · 참고용 |
| 공개 | `summary()` (register에 포함) | 홈 카드 |
| 불러 씀 | `REBOOT.use('compliance').review(text, { channel: 'cs' })` | CS → 컴플라이언스 |
| 불러 씀 | `REBOOT.use('settings').facts()` → 제품명·맛·성분으로 템플릿 빈칸 | 설정 (이현경) |
| 넘김 | C10 협업 제안 | 인플루언서·파트너 (혁주 · 효선) |
| 넘김 | C9 B2B·대량 구매 | 해외영업 {확인 필요: 팀 내 담당} |

함수 이름이나 돌려주는 모양을 바꿀 때는 쓰는 파트 담당에게 먼저 알리고, 팀 README 5장 표에도 PR로 반영합니다.

---

## 5. 저장과 보안

| 키 | 내용 | 시연본 예전 키 |
|---|---|---|
| `reboot:compliance:history` | 심의 이력 (문구·채널·판정·상태·코멘트) | `aldicom-history` · `aldicom-standalone-v1` |
| `reboot:compliance:monitor` | 경고장 확인 결과·사례집 후보 | `aldicom-standalone-v1` |
| `reboot:compliance:openfda-cache` | openFDA 조회 결과 (1분 캐시) | `aldicom-standalone-v1` |
| `reboot:compliance:fedreg-cache` | Federal Register 조회 결과 (1분 캐시) | — |
| `reboot:cs:inquiries` | 문의·분류·상태·초안·승인자·승인 일시 | `aldicom-standalone-v1` |
| `reboot:cs:adverse-events` | 부작용 접수 (접수·검토·판단·보고 일시) | `aldicom-standalone-v1` |

- 브라우저 저장은 **그 브라우저에만** 남음 (팀원 공유 안 됨, 지우면 사라질 수 있음) · 예전 키 기록은 처음 실행할 때 새 키로 한 번만 옮김
- **HTML·JS에 키를 넣지 않음** → 키가 필요한 호출은 서버 경유 · 키는 **`server/.env`에만**, 설명은 **줄 끝이 아니라 윗줄**에 (FDA 광고 콘솔의 `.env` 읽기 코드는 줄 끝 설명을 값으로 읽음)
- 실제 고객 개인정보 입력 금지 · CS 테스트는 테스트 Gmail + 가짜 문의 메일
- 키가 들어간 파일을 올렸다면 바로 재발급 (FDA 광고 콘솔 zip의 openFDA 키는 재발급 필요 · 13장)

`server/.env` (컴플라이언스·CS 구역):
```text
# --- 컴플라이언스 (소준희) ---
# 선택 · openFDA는 키 없이 먼저 부르고 실패할 때만 사용 (404는 0건)
OPENFDA_API_KEY=
# 선택 · AI 2차 판정. 비우면 규칙 판정만
ANTHROPIC_API_KEY=
# fda.gov 요청 간격 (초)
CRAWL_DELAY_SECONDS=30
# 최근 며칠 경고장까지 볼지
REFRESH_LOOKBACK_DAYS=365
# 1회에 읽을 최대 경고장 수 (1건당 약 30초 → 10건이면 약 5분)
REFRESH_MAX_LETTERS=10
# 무료 키 · Regulations.gov (발급 방법: README 부록 A)
REGULATIONS_GOV_API_KEY=
# 무료 토큰 · CourtListener
COURTLISTENER_TOKEN=

# --- CS (소준희) ---
# 예: to:<CS 전용 주소> newer_than:30d
GMAIL_QUERY=
```

`.gitignore`에 꼭 있어야 할 것: `.env` · `credentials.json` · `token.json` · `*.db` · `__pycache__/` · `_incoming/` · `dev/`

---

## 6. 규제 모니터링 출처와 HTML 구현 가능성

브라우저는 그 사이트가 허용(CORS)해야 읽을 수 있고, 키를 쓰면 HTML에 키가 드러납니다 → 출처마다 **HTML에서 바로 / 서버 경유 / 수동**으로 나눔.

| 출처 | 무엇을 보나 | 방법 | 키 | HTML에서 | 판단 |
|---|---|---|---|---|---|
| openFDA 식품 리콜 | 리콜 동향 | API | 없어도 됨 | ✅ 바로 | 시연본에서 동작 확인 |
| Federal Register | FDA·USTR·CBP·FTC 새 공고 | API | 없음 | ✅ 바로 | CORS 허용(`*`) 확인 2026-10-23 · '규제 모니터링' 탭에서 조회 |
| eCFR | 21 CFR 조문 개정일 | API | 없음 | ⚠️ 확인 필요 | 막히면 서버 |
| USITC HTS | 관세율 변경 | API (`/reststop/search`, `/exportList`) | 없음 | ⚠️ 확인 필요 | 같음 |
| FDA 리콜 RSS | 새 리콜 | RSS | 없음 | ❌ 어려움 | 서버 |
| FDA 수입 경보 (예: 33-15) | 통관 보류 대상 | 크롤링 (업종별 표: 번호·유형·게시일·이름) | 없음 | ❌ 불가 | 서버 |
| FDA 경고장 | 새 숙취 관련 경고장 → 사례집 후보 | XLSX + 본문 크롤링 | 없음 | ❌ 불가 | 서버 (시연본은 CSV 업로드로 대체 중) |
| Regulations.gov | 규칙 제정·의견 수렴 | API | 무료 키 | ⚠️ 비권장 | 키 노출 → 서버 |
| CourtListener | 허위광고 소송 | API · 이메일 알림 | 무료 토큰 | ⚠️ 비권장 | 서버 또는 웹사이트 알림만 |
| FDA 수입 거부 (Data Dashboard) | 실제 거부 사례·사유 | 파일 다운로드 (주간) · API는 인증키 신청 | 신청 | 수동 | 주 1회 파일 저장 |
| CBP CROSS | 품목분류 결정 | 웹 검색 (API 없음) | — | 수동 | 월 1회 |
| FTC 건강 광고 가이드 · NAD | 광고 근거·문제 표현 | 문서·결정 | — | 수동 | 월 1회, 근거 메모에 반영 |
| 캘리포니아 Prop 65 | 납 등 경고 문구 | 목록 다운로드 · 60일 통지 검색 | — | 수동 | 월 1회 |

**서버 결과를 HTML에서 보여 주는 구조 (권장)** 서버(또는 매일 스크립트)가 확인 → `parts/compliance/monitor-data.js`(예: `window.REBOOT_MONITOR = {...}`)로 저장 → HTML은 `<script src>`로 읽기만 (더블클릭 실행 유지, 키 없음) → 첫 확인은 '기준 저장'만, 이후 🆕 새 항목 · 🔄 변경 표시. 키 발급·수동 확인 방법은 [부록 A](#부록-a-키-발급--신청--수동-확인).

---

## 7. 수출 규제 조사 요약

> 2026-10-22 조사 · 참고용 (관세사·미국 식품 규제 전문가 확인 필요) · 규칙과 제품 기획에 영향 주는 내용만

**HS 코드와 관세**
- HS 코드는 고르는 것이 아니라 **제품의 실제 형태·성분으로 정해짐** (다른 코드로 신고하면 오분류 벌금)
- FDA 규제는 HS 코드와 무관. 모든 식품 코드가 FDA 심사 대상이고 **표시·광고 내용**으로 식품/의약품 판단

| 형태 | 예상 HS 코드 (미국) | 일반 관세 → 한국산(KORUS) | 비고 |
|---|---|---|---|
| 짜 먹는 젤리 스틱·컵 젤리 | 2106.90.99 | 6.4% → 무관세 | CBP는 컵 젤리 간식을 2106.90으로 분류한 결정이 있음 |
| 구미·젤리 캔디 (소매 포장) | 1704.90.35 | 5.6% → 무관세 | 소매 포장이 아니고 당이 많으면 설탕 쿼터 구간(1704.90.58·68) 위험 |
| 무설탕 구미 (합성감미료) | 2106.90.99 (.85) | 6.4% → 무관세 | 설탕 과자(1704)가 아니게 됨 |
| 액상 음료 | 2202.99 / 2202.10 | 0.2¢/리터 → 무관세 | 관세는 가장 낮지만 운임 부담 |
| 환·정제·캡슐 | 2106.90 가능 | — | 약처럼 보여 R7·의약품 분류 위험 → 피함 |

- **현재 추가 관세:** 2026-02 미국 대법원이 상호관세 무효 판결 → 2026-07-24부터 한국산 전 품목 Section 301 관세 12.5% (기존 관세와 합쳐 12.5% 상한) → 형태별 실제 관세 차이는 작음. KORUS 무관세와 함께 적용될 때의 계산은 관세사 확인 필요
- 최종 코드는 CBP 사전 품목분류 결정(binding ruling)으로 받아 두는 것을 권장

**형태별 규제 비교**

| 항목 | 젤리 | 저당 젤리 | 음료 | 저당 음료 |
|---|---|---|---|---|
| FDA 질식 위험 젤리 단속 (Import Alert 33-15 · 곤약뿐 아니라 카라기난·한천도, 크기·식감으로 판단) | ⚠️ | ⚠️⚠️ 곤약 사용이 많음 | — | — |
| 산성화식품 공정 등록 (FCE·SID) | 상온 파우치 + 수분활성도 0.85 이상 + 산 첨가로 pH 4.6 이하면 해당 가능 | 같음 | 같음 | 같음 |
| 과즙 HACCP | 과즙(배즙 등)을 넣으면 적용 · 수입자가 기록 보관 | 같음 | 같음 | 같음 |
| '저당' 표시 | — | ❌ 미국은 "Low sugar" 정의 없음 → 사용 불가 | — | ❌ 같음 |
| 감미료 의무 표시 | — | 아스파탐 경고문, 솔비톨(하루 50g 이상 섭취 가능 시) 설사 주의 문구 | — | 같음 |
| **종합 규제 부담** | 중간 | **가장 큼** | **가장 작음** | 작음~중간 |

**당 함량 표시 규칙 (21 CFR 101.60)** — 12장 정할 것 2의 근거
- `Sugar free`·`Zero sugar`: 1회 제공량당 당 0.5g 미만 + 저칼로리가 아니면 "not a low calorie food" 같은 문구 필요
- `Reduced sugar`·`Less sugar`: 비교 대상보다 25% 이상 적음 + 비교 대상과 감소 비율 표기
- `No added sugar`: 당·과즙 농축액 등을 전혀 넣지 않아야 함 (조건 까다로움)
- `Low sugar`: **정의 없음 → 사용 불가** (한국 포장의 '저당'을 그대로 번역하면 안 됨)
- 알룰로스는 총당류·첨가당 계산에서 제외 가능 (FDA 허용) → 저당 제품에 유리

**총 모양 젤리** — **추천하지 않음** (음주 + 무기 연상, 아동 대상 마케팅 비판, R6 책임 음주 원칙과 충돌)
- FDA 식품 규정상 모양 금지 조항은 없음. 연방 모조 총기 표시법(15 U.S.C. §5001, 15 CFR 1150)은 장난감·모조품 대상이며 젤리 적용 여부는 명시 없음 (적용돼도 38×70mm 이하 미니어처 예외, 밝은 색·반투명이면 표시 의무 충족)
- 캘리포니아는 실제 총과 오인할 정도면 모조 총기로 봄 → 실물 같은 검정·회색 디자인 위험
- 진행한다면 만화풍·밝은 색·38×70mm 이하·부드러운 식감·판매 채널 사전 확인

---

## 8. 검증

규칙·데이터·CS 템플릿을 바꿀 때마다 실행하고 결과를 '실행 기록'에 남깁니다.

```bash
node dev/test_parts.js                                            # parts/ 코드 점검 (인터넷·브라우저 불필요)
node tools/cross_check.js dashboard.html _incoming/fda_ad_console # 시연본과 비교 (두 파일이 있어야 실행)
```

| 항목 | 대상 | 목표 |
|---|---|---|
| 경고장 원문 문구 중 PASS | 현재 **53개** (3단계 확장 후 87개) | **0개** |
| 안전 문구 (8개 + 오탐 확인 4개) 중 PASS 아닌 것 | 12개 | **0개** |
| CS 답변 템플릿 C1~C10 중 BLOCK | 10개 | **0개** (막히면 그 유형 발송이 전부 막힘 · 'CS → 답변 템플릿'에서 확인) |

오탐 확인 문장: `Contains Hovenia dulcis extract.` · `Before the first toast.` · `Best served chilled.` · `10 sticks per box.`
`[확인 필요]`가 남은 템플릿은 BLOCK이 아니어도 승인에서 막힘 → 팩트시트 연결 전까지 해당 유형(예: C6)은 사람이 직접 채움.

| 날짜 | 대상 | 경고장 PASS | 안전 문구 비PASS | 템플릿 BLOCK | 실행자 | 비고 |
|---|---|---|---|---|---|---|
| 2026-10-22 | 시연본 `dashboard.html` · 사례집 53 / 취합용 원문 87 / 안전 12 / 템플릿 10 | 53개 중 **0** · 87개 중 **1** | **0** | **0** | Claude (`tools/cross_check.js`) | 87개 중 놓친 1개는 `'Emergency Hang-over Kits'` (하이픈 형태) → 취합 2단계에서 R2 패턴 추가로 해결. 반대로 FDA 광고 콘솔 엔진은 `Nature's Aspirin`을 놓침 |
| 2026-10-22 | 로컬 `parts/` (`dev/test_parts.js`) · 안전 4 / 템플릿 10 | 실행 안 됨 (0개 · 이관 전) | **0** / 4 | **0** / 10 | Claude Code | 엔진 예문 14개·분류 12건 통과 |
| 2026-10-23 | 로컬 `parts/` (core 형식 정리 후) · 안전 4 / 템플릿 10 | 실행 안 됨 (0개 · 이관 전) | **0** / 4 | **0** / 10 | Claude Code | 기존 항목 + core 형식·저장 키·금지 패턴·`monitor-data.js` 형식 점검 통과 |

---

## 9. 기술 구현 가능성 점검

✅ HTML만으로 가능 · 🟡 조건부 (서버·다른 파트·설정 필요) · ❌ HTML만으로 불가

| 구분 | 항목 | 판단 | 방법 · 조건 |
|---|---|---|---|
| CS | `[확인 필요]` 남으면 승인·내보내기 차단 | ✅ | 승인 시 초안에 `[확인 필요` 문자열 검사 |
| CS | 모든 초안 최종 승인 (승인자·일시) | ✅ | `reboot:cs:inquiries`에 추가. 로그인 없이 입력한 이름이라 **본인 확인은 안 됨** (기록용) |
| CS | 부작용 단계별 일시 기록 | ✅ | 단계 버튼 누를 때 현재 시각 자동 기록 + 직접 수정 칸 |
| CS | `REBOOT.use('compliance')` 연동 | 🟡 | 공통 `core/`(register·expose·use)가 먼저 필요. 그 전에는 `dev/core-stub.js`로 확인 |
| CS | 팩트시트 연결 | 🟡 | 설정 파트(이현경)가 `facts()` 공개 필요 |
| CS | Gmail 연동 | 🟡 | **서버(권장):** `server/cs/gmail.py`, `gmail.compose`+읽기 권한, 테스트 모드 7일 재로그인. **브라우저:** `http://localhost`에서만 |
| CS | AI 분류·초안 | 🟡 | AI 키 노출 → 서버 경유 필수 |
| CS | 팀원 간 이력 공유 | ❌ | 브라우저 저장소는 그 브라우저에만 → 서버 DB 필요 |
| 컴플라이언스 | 규제 단어 57개 통합 · 87개 확장 · 매핑 20쌍 | ✅ | `data.js` 정규식·데이터 추가 (취합 2·3단계) |
| 컴플라이언스 | `rules()` 근거 구분 | ✅ | 규칙마다 `basis: ['⚖️','🔍']` 같은 칸 |
| 컴플라이언스 | 당 함량 표시 규칙 | ✅ | 정규식으로 `low sugar`·`저당` 감지 (정할 것 2 결정 후) |
| 컴플라이언스 | 경고장 자동 수집 · 수입 경보 · 리콜 RSS | ❌ → 🟡 | 서버에서 수집 후 `monitor-data.js`로 전달 (6장) |
| 컴플라이언스 | Federal Register · openFDA | ✅ | 브라우저에서 바로 (둘 다 `parts/` 구현 완료) |
| 컴플라이언스 | eCFR · HTS | 🟡 | 브라우저 허용 확인 후 결정, 막히면 서버 |
| 컴플라이언스 | AI 2차 판정 | 🟡 | 서버 키 필요 |
| 컴플라이언스 | 매일 자동 실행 | 🟡 | HTML은 스스로 실행 불가 → 작업 스케줄러·GitHub Actions가 서버 스크립트 실행 후 `monitor-data.js` 갱신 |

**정리** 지금 HTML로 가능: `[확인 필요]` 차단, 최종 승인, 부작용 일시, 규칙 확장, 근거 구분, 당 함량 규칙 · 서버 필요: 경고장·수입 경보·RSS 수집, 키 API, AI, Gmail(권장 방식), 팀 공유 · 다른 파트 대기: `core/` 연동(공통 담당), 팩트시트(이현경)

---

## 10. 권장 시연 순서

- **광고 문구 점검:** 컴플라이언스 → 단건 심의 → `Prevents hangovers.` 입력 → **문구 점검하기** → 감지된 표현 확인 → 심의 이력 확인 ('사용 불가'는 승인 불가). 키워드 미발견 ≠ 안전한 문구로 승인
- **CSV 일괄 점검:** 첫 행 `copy` 열 CSV 업로드 (최대 1MB · 500행 · UTF-8 권장 · 양식은 화면에서 다운로드)

  ```csv
  copy
  Enjoy your evening.
  Prevents hangovers.
  ```
- **CS 답변:** 문의함 → 부작용 문의(맨 위) → 담당자 확인 체크 전 발송이 막히는지 → 초안에 `cures hangovers`를 넣고 '사용 불가'로 막히는지

---

## 11. 주의

- 고객 문의·리뷰는 시연용 예시, 날짜는 2026-10-22 고정
- R1~R10에는 법적 기준과 팀의 보수적 기준이 섞여 있으니 근거 구분을 함께 확인 · 사례집은 2020년 경고장 기준 (새 경고장은 사람이 확인 후 추가)
- 제품 효능·성분·섭취 방법은 공식 자료로 확인
- 고객 답변은 실제 발송되지 않음 (시연은 '발송 완료' 표시만) · 실제 고객 이름·주소·연락처 입력 금지
- API 키, Gmail 인증 파일, `.env`, 키가 들어간 HTML은 깃에 올리지 않음

---

## 12. 확정된 결정 · 정할 것

| 날짜 | 확정된 결정 (두 파트 관련) |
|---|---|
| 2026-10 | 미국 제품 분류는 일반식품 (건강기능식품 아님) |
| 2026-10-22 | 광고 규칙 번호는 R1~R10, 판정은 BLOCK·REVISE·COND·REVIEW·PASS 5단계 |
| 2026-10-22 | 단일 HTML을 `core/` + `parts/<파트ID>/` 구조로 나눠서 작업 · 기준 구현은 HTML 대시보드, main에 PR로 합침 |
| 2026-10-22 | 컴플라이언스·CS를 먼저 완성하고, 나머지 파트는 각자 정리 후 취합 |
| 2026-10-22 | CS Gmail 연동은 우선 개인 메일 계정으로 |

| # | 정할 것 | 팀 안건 # | 관련 | 제안 · 비고 |
|---|---|---|---|---|
| 1 | `before drinking` · `best` 차단 여부, NAC 패턴 포함 여부 | 7 | 소준희 | 경고장은 인용하지만 오탐 위험 · 우리 제품에 NAC가 없으면 제외 |
| 2 | 당 함량 표시 규칙 추가 | 8 | 소준희 | `Low sugar`·`저당` → 사용 불가, `Sugar free`·`Reduced sugar` → 조건 확인. R11로 할지 기존 규칙에 넣을지 |
| 3 | R8 구조·기능 표현을 계속 일괄 REVISE로 둘지 | 9 | 소준희 | 영양 근거가 있는 표현은 허용 가능 (현재 보수적 정책) |
| 4 | REVISE·REVIEW 판정이 남은 초안의 승인 조건 | 10 | 소준희 | 수정 후 재검사 필수 / 검토 의견만 남기면 승인 |
| 5 | C9 B2B 문의를 팀에서 누가 받을지 | 11 | 전체 | 현재 '해외영업' |
| 6 | 심각한 부작용 RFR 보고 책임 주체·판단 권한자 | 12 | 전체 | 등록 시설(제조사·수입사) 확정. 24시간은 판단 시점부터 |
| 7 | 제품 형태 (젤리 / 저당 젤리 / 음료 / 저당 음료) | 13 | 전체 | 규제 부담: 음료 < 저당 음료 < 젤리 < 저당 젤리 (7장) |
| 8 | 젤리라면 곤약·단단한 겔화제를 뺄지 | 14 | 전체 | FDA 수입 경보 33-15 대상 가능 |
| 9 | 총 모양 등 특수 모양 젤리 | 15 | 전체 | 추천하지 않음 (7장) |

결정되면 팀 README '확정된 결정' 표에 PR로 추가합니다.

---

## 13. 할 일

**컴플라이언스** (프롬프트: [부록 B](#부록-b-컴플라이언스-취합-프롬프트-vs-code--claude-code))
- [ ] 1단계 · 검토 보고서 (dashboard.html vs FDA 광고 콘솔, 코드 수정 없음)
- [ ] 2단계 · 규제 단어 57개를 R1~R10에 통합 (패턴 추가 23개, `hang-over` 포함)
- [ ] 3단계 · 경고장 원문 87개로 사례집 확장, 표현 매핑 20쌍 → 87개 기준 재검증
- [ ] 4단계 · 경고장 수집을 `server/compliance/`로 이관 (일반식품 기준 필터, 30초 간격)
- [ ] 5단계 · 4인 관점 최종 검토 (마케터·컴플라이언스·개발자·보안) — 같은 AI가 맡은 관점끼리 동의해도 독립 검증이 아니므로, 공통 판단은 별도 사실 확인 후 반영
- [ ] 6단계 · (선택) 매일 자동 실행 → 결과를 `monitor-data.js`로 (6장)
- [ ] R1~R10 근거 구분을 규제 담당과 확정 → `rules()` 반영
- [ ] 당 함량 표시 규칙 추가 여부 결정 후 반영 (정할 것 2)
- [ ] 무료 키 발급 (Regulations.gov · CourtListener) → `server/.env` ([부록 A](#부록-a-키-발급--신청--수동-확인))
- [ ] 월 1회 수동 확인 시작 (CBP CROSS · FTC · NAD · Prop 65 · FDA 수입 거부 파일)

**CS**
- [ ] 컴플라이언스 재검사를 `REBOOT.use('compliance').review`로 연결 (R1~R10 통합 후)
- [x] 모든 초안 최종 승인 단계 추가 (승인자·승인 일시 저장)
- [x] `[확인 필요]`가 남은 초안은 승인·Gmail 초안 내보내기 차단
- [ ] 제품 팩트시트(설정) 연결 → 템플릿 `[확인 필요]` 빈칸 채우기
- [x] 부작용 접수에 단계별 일시(접수·긴급 검토·보고 대상 판단·보고) 기록 + 직접 수정
- [ ] 템플릿 서명 브랜드명 정리 (시연본은 `ALDICOM Customer Care`)
- [ ] 테스트 Gmail 계정 + 가짜 문의 메일 20건 준비 → `server/cs/gmail.py` (읽기 + 답장 초안)
- [ ] 환불·배송 정책 확정 후 C3·C5 템플릿 수정
- [ ] (다음 단계) AI 분류·초안 — 결과는 항상 재검사 + 담당자 확인

**두 파트 공통**
- [x] `parts/` 코드를 팀 core 형식(1장: `register({ id, render })`, `REBOOT.store('<파트ID>')`, `REBOOT.ui`, 다운로드 파일명)에 맞추기
- [ ] 위 코드 커밋 (실제 `core/`가 나오면 `dev/core-stub.js`와 API가 같은지 확인)
- [ ] 시연본 데이터 이관 (경고장 원문 53개 · 안전 문구 8개 · 표현 매핑 10쌍)
- [ ] 이관 후 이 README의 '시연본 완료'·'예정' 상태를 실제 코드와 대조해 갱신
- [ ] FDA 광고 콘솔 zip의 openFDA 키 재발급 (zip 안 `.env`와 `standalone/hangover-console.html`에 실제 키)

---

## 14. 변경 기록

| 날짜 | 내용 |
|---|---|
| 2026-10-22 | 컴플라이언스·CS README 작성 → 팀 README v0.3에 통합 |
| 2026-10-22 | 팀 README(v0.3.1)에서 소준희 담당 두 파트만 분리한 개인 README 작성 |
| 2026-10-23 | `parts/`를 core 형식(1장)으로 정리 · 규제 모니터링 탭(Federal Register 조회, `monitor-data.js` 기준 저장·🆕/🔄·사례집 후보 승인) · CS 승인 초안 복사·처리 기록 · 부작용 일시 직접 수정 |
| 2026-10-23 | 대시보드 시안(REBOOT Marketing Workspace) 디자인 적용: 라임 포인트(`#E4FA18`)·흰 카드·알약형 탭 · 두 파트 상단 KPI 카드 4장 · 단건 심의 2단 배치(입력 카드 + 검토 흐름·알아두세요) · CS 2단 문의함(이니셜·초안 상자) · `dev/preview.html`을 시안 껍데기(상단 메뉴·왼쪽 하위 메뉴)로 교체 |

---

## 부록 A. 키 발급 · 신청 · 수동 확인

> 키는 `server/.env`에만 (깃·HTML 금지). 넣은 뒤 서버를 다시 켜면 적용됩니다.

| 구분 | 출처 | 할 일 | 걸리는 시간 |
|---|---|---|---|
| 🔑 무료 키 | Regulations.gov | 이메일로 키 받기 → `.env` | 5분 |
| 🔑 무료 토큰 | CourtListener | 가입 → 토큰 복사 → `.env` | 10분 |
| 📝 신청 | FDA Data Dashboard API | 계정·인증키 신청 (승인 필요) · 그 전에는 주 1회 파일 다운로드 | 며칠 이상 |
| 👀 수동 | CBP CROSS · FTC 가이드 · NAD · Prop 65 | 월 1회 사람이 확인 | 월 30분 |

- **A-1. Regulations.gov** (규칙 제정·의견 수렴) · https://api.data.gov/signup/ → 이름·이메일 → **Signup** → 이메일의 **API Key**를 `REGULATIONS_GOV_API_KEY=`에 · 공용 `DEMO_KEY`는 운영에 쓰지 않음 · 설명서 https://open.gsa.gov/api/regulationsgov/
- **A-2. CourtListener** (미국 연방 법원 사건) · https://www.courtlistener.com → **Sign in / Register** → 이메일 인증 → 프로필 메뉴의 **API(Developer) 토큰** 복사 (메뉴 이름은 바뀔 수 있음 · https://www.courtlistener.com/help/api/rest/) → `COURTLISTENER_TOKEN=` · (선택) 웹사이트 **알림(Alert)**으로 새 사건 이메일 수신 (하루 평균 30건 넘는 넓은 검색어는 거절되니 좁게)
- **A-3. FDA Data Dashboard** (수입 거부 기록)
  - **파일 다운로드 (지금 바로 · 권장):** https://datadashboard.fda.gov/oii/cd/imprefusals.htm → 국가 **Korea (South)**·제품 분류 필터 → **Filtered Data** 다운로드 (매주 갱신) → `docs/compliance/import_refusals/수입거부_YYYYMMDD.csv`
  - **API (승인 필요):** 같은 페이지 안내대로 **OII Unified Logon** 계정 → API 인증키 신청 → 승인 후 `server/.env`

**A-4. 수동 확인 출처 (매월 첫째 주)**

| 출처 | 홈페이지 | 무엇을 확인 | 기록 위치 |
|---|---|---|---|
| CBP CROSS (품목분류 결정) | https://rulings.cbp.gov/ | 검색어 `jelly drink`, `jelly snack`, `2106.90.99`, `hangover` → 비슷한 제품의 HS 코드 결정 | `docs/compliance/cross_rulings.csv` |
| FTC 건강 광고 가이드 | ftc.gov에서 "Health Products Compliance Guidance" 검색 | 근거 수준, 후기·추천 광고 규칙 → R5·R10 근거 메모 | `docs/compliance/ftc_notes.md` |
| NAD 광고 심의 결정 | https://bbbprograms.org/ | 검색어 `hangover`, `sugar`, `supplement` → 문제 된 광고 표현 | `docs/compliance/nad_cases.csv` |
| 캘리포니아 Prop 65 | https://oehha.ca.gov/proposition-65 · 캘리포니아 법무부(oag.ca.gov) 60일 통지 검색 | 원료·젤리·사탕 관련 통지 (특히 **납**) → 경고 문구 필요 여부 | `docs/compliance/prop65.md` |

**순서:** 사이트 확인 → 새로 찾은 것만 기록 파일에 한 줄 (날짜·출처·내용·링크·우리 영향) → 규칙·표시를 바꿔야 하면 `data.js` 수정 → 8장 검증 → PR

---

## 부록 B. 컴플라이언스 취합 프롬프트 (VS Code · Claude Code)

dashboard.html(기준)에 FDA 광고 콘솔 zip의 장점을 합치는 순서입니다. **한 번에 한 단계씩** Claude Code 입력창에 붙여 넣고, 결과를 확인한 뒤 다음 단계로 넘어갑니다.

| 단계 | 하는 일 | 코드 수정 | 모드 |
|---|---|---|---|
| 0 | 폴더 준비 | 사람이 직접 | — |
| 1 | 검토만 하고 보고서 쓰기 | ❌ | plan |
| 2 | 규제 단어 57개를 R1~R10으로 다시 묶기 | ✅ | default |
| 3 | 경고장 원문 87개로 사례집·규칙 검증 바꾸기 | ✅ | default |
| 4 | 경고장 크롤링을 서버 버전에 옮기기 | ✅ | default |
| 5 | 최종 검토 (4인 관점 + 보안) | ❌ | plan |
| 6 | (선택) 매일 자동 실행 | ✅ | default |

### 0단계 · 폴더 준비 (사람이 직접)

1. VS Code → **File → Open Folder** → 팀 프로젝트 폴더
2. 아래 모양이 되게 파일을 넣습니다

```
프로젝트폴더/
├─ CLAUDE.md                     ← 받은 파일 (프로젝트 설명서)
├─ dashboard.html                ← 기준 파일
├─ server/                       ← 서버 버전이 있으면 (없어도 됨)
├─ _incoming/
│   └─ fda_ad_console/           ← zip을 풀어서 넣기 (.env 파일은 지우고 넣기!)
├─ docs/compliance/
│   └─ term_mapping_draft.csv    ← 받은 파일 (규제 단어 57개 분류 초안)
└─ tools/
    └─ cross_check.js            ← 받은 파일 (두 판정 엔진 비교 스크립트)
```

3. `.gitignore`에 `.env`와 `_incoming/`이 있는지 확인 (없으면 추가)
4. 왼쪽 **소스 제어** → 브랜치 만들기 → `feat/compliance-merge`
5. 터미널(`Ctrl` + `` ` ``)에서 `claude` 실행 → `Shift`+`Tab`으로 **plan 모드**

> ⚠️ zip 안 `.env`와 `standalone/hangover-console.html`에는 실제 openFDA 키가 들어 있습니다. 깃에 올리지 말고, zip을 이미 공유했다면 키를 새로 발급받으세요.

### 1단계 · 검토만 (코드 수정 금지)

```
#역할
당신은 미국 FDA 식품 광고 규제를 아는 시니어 개발자이자 컴플라이언스 검토자입니다

#작업
CLAUDE.md를 먼저 읽고, dashboard.html(기준)과 _incoming/fda_ad_console/(가져올 자료)을
비교해서 "합치기 전 검토 보고서"를 써 주세요. 이번 단계에서는 파일을 하나도 고치지 마세요.

#입력
- dashboard.html : 첫 번째 <script> 안의 LocalAPI (RULES R1~R10, review 함수, DATA_CLAIMS 53개, SAFE_COPIES 8개, MAPPINGS 10개)
- _incoming/fda_ad_console/static/terms.js : 규제 단어 57개
- _incoming/fda_ad_console/static/app.js : RULES R0~R12, runEngine
- _incoming/fda_ad_console/seed/seed.json : 경고장 7건 원문 문구 87개, 매핑 20쌍
- _incoming/fda_ad_console/crawler.py, refresh.py, db.py, app.py, tests/
- docs/compliance/term_mapping_draft.csv : 규제 단어 57개를 R1~R10에 붙인 초안
- tools/cross_check.js : 두 엔진 비교 스크립트
  실행: node tools/cross_check.js dashboard.html _incoming/fda_ad_console

#확인할 것
1. tools/cross_check.js를 실행해서 아래 숫자가 맞는지 확인
   - 원문 87개 → dashboard 엔진 PASS 1개 ('Emergency Hang-over Kits', 하이픈 형태)
   - 원문 87개 → zip 엔진 '법무 확인' 1개 ('Nature's Aspirin')
   - 안전 문구 8개 → dashboard PASS 8개, zip은 8개 모두 '법무 확인'
2. term_mapping_draft.csv의 57개 분류가 맞는지 한 줄씩 검토
   - status가 '대시보드 규칙에 패턴 추가 필요'인 23개는 어느 규칙에 넣을지, 판정 단계는 무엇인지
   - 우리 제품 성분(헛개 등)을 사실대로 적은 문장까지 막히지 않으려면 어떤 조건이 필요한지
3. zip이 건강기능식품 기준으로 쓴 곳 찾기 (AI 지시문, SUBJECT_FILTER 등) → 일반식품 기준으로 바꿀 곳 목록
4. server/ 폴더가 있는지, 있다면 zip의 crawler.py를 어디에 넣으면 되는지
5. 두 쪽이 서로 부딪히는 이름 (전역 변수 RULES·$, API 주소, localStorage 키, 판정 이름)

#제약
- 파일 수정·생성·삭제 금지 (보고서는 화면에만 출력)
- 원문 문구와 숫자는 파일에서 확인한 것만. 모르면 {확인 필요}
- 사람이 결정해야 할 것은 결정하지 말고 질문으로 남기기

#출력
1. 숫자 확인 표 (예상 / 실제 / 일치)
2. 패턴 추가 23개 표: 단어 | 넣을 규칙 | 판정 | 맥락 조건 | 오탐 위험
3. 일반식품 기준으로 바꿀 곳 목록 (파일:줄)
4. 이름 충돌 목록과 해결 방법
5. 사람이 결정해야 할 질문 (최대 5개)
```

**사람이 할 일:** 5번 질문에 답하기. 예상 질문: `before drinking` 차단 여부 (zip은 반려, dashboard 매핑은 "Korea's pre-toast ritual" 허용) · `best` 차단 여부 ("best flavor" 같은 오탐) · NAC를 R7에 넣을지 (제품에 NAC가 없으면 넣지 않을지)

### 2단계 · 규제 단어 57개를 R1~R10으로 다시 묶기

> `Shift`+`Tab`으로 **default 모드**로 돌아와서 실행

```
#작업
1단계 보고서와 내 답변을 기준으로, zip의 규제 단어 57개를 dashboard.html의 R1~R10 규칙에 합쳐 주세요.

#반드시
- dashboard.html의 메뉴·화면·디자인·CS 기능은 절대 바꾸지 마세요
- 규칙 번호는 R1~R10만 사용. 새 번호를 만들지 마세요
- 판정 5단계(BLOCK·REVISE·COND·REVIEW·PASS)와 채널·제품분류 로직은 그대로
- 고치기 전에 바꿀 줄을 먼저 보여 주고 허락받기

#할 것
1. R2 패턴에 하이픈 형태 추가: hang-over, hang over (zip의 hang[- ]?over 방식)
2. term_mapping_draft.csv에서 '패턴 추가 필요' 23개를 1단계 표대로 각 규칙에 추가
   - 증상(headache·nausea·dizziness·brain fog·the spins)은 같은 문장에 hangover·alcohol·drink·숙취·술이 있을 때만
   - 성분(DHM·헛개·밀크씨슬·칡·활성탄 등)은 숙취·알코올·음주와 같은 문장에 있을 때만
     ("Contains Hovenia dulcis extract"처럼 성분 사실만 적은 문장은 PASS)
3. 규제 단어마다 한국어 대응어(terms.js의 ko)를 같은 규칙에 함께 추가
4. 규칙표 화면(규칙표 R1~R10)에 "zip에서 온 표현" 열을 추가해 어떤 단어가 들어왔는지 보이게

#확인
- node tools/cross_check.js dashboard.html _incoming/fda_ad_console 다시 실행
- 원문 87개 PASS 0개, 안전 문구 8개 PASS 8개가 나와야 함
- 아래 문장은 PASS여야 함 (오탐 확인용)
  "Contains Hovenia dulcis extract." / "Before the first toast." / "Best served chilled." / "10 sticks per box."

#출력
CLAUDE.md의 "작업이 끝날 때마다 보고 형식"대로
```

**확인 후:** 브라우저에서 dashboard.html → 문구 점검에 `Emergency Hang-over Kits` → '사용 불가'면 성공 → Commit (`규제 단어 57개 R1~R10 통합`)

### 3단계 · 경고장 원문 87개로 사례집·규칙 검증 바꾸기

```
#작업
dashboard.html의 경고장 문구 53개(DATA_CLAIMS)를 zip seed.json의 원문 87개로 넓혀 주세요.

#반드시
- 기존 53개의 claim_id(CL-001…)와 case_id(FDA-2020-HO-01…)는 그대로 유지
- 새로 들어오는 34개는 CL-054부터 번호를 이어서 붙이기
- 문구는 seed.json 원문 그대로 (글자 하나도 고치지 않기)
- 기존 53개 중 "…"로 잘린 문구는 seed.json 원문으로 바꾸고, 바꾼 목록을 보여 주기
- 화면 구조·디자인은 그대로

#할 것
1. 53개 ↔ 87개 짝 맞추기 표 만들기 (어느 원문과 같은지)
2. 새 34개를 DATA_CLAIMS에 추가 (expected는 'FLAG', 후기는 page를 'testimonial'로)
3. zip 매핑 20쌍 중 dashboard 매핑 10개에 없는 것을 추가 후보로 보여 주기
   (바로 넣지 말고 표로만. 일반식품 기준에 맞지 않는 safe 문구는 표시)
4. 화면의 숫자 표기 "53"을 실제 개수로 자동 표시되게 (글자로 박힌 53 찾기)
5. "엑셀 내려받기"가 87개로 나오는지 확인

#확인
- 규칙 검증 탭: 놓친 문구 0개, 오탐 0개
- node tools/cross_check.js 결과 붙이기

#출력
짝 맞추기 표 → 매핑 추가 후보 표 → 보고 형식
```

**사람이 할 일:** 매핑 추가 후보 표에서 골라 "M11~M15만 넣어줘"처럼 답하기 → Commit

### 4단계 · 경고장 크롤링을 서버 버전에 옮기기

> dashboard 가이드 '남은 일'의 "② 본문에서 문구 추출(미구현)" 해결 단계. HTML은 fda.gov를 직접 못 읽으므로 크롤링은 **서버(Python)에서만**

```
#작업
zip의 crawler.py와 refresh.py에서 경고장 수집 기능을 가져와 우리 서버 버전에 붙여 주세요.
server/ 폴더가 없으면 만들지 말고 먼저 물어봐 주세요.

#가져올 것 (zip 파일 → 함수)
- crawler.py: list_warning_letters, supplement_letters, read_letter, extract_claims, topic_keywords, openfda_get
- refresh.py: 백그라운드 실행, 실행 중 잠금, 30분 시간 초과, 이미 본 경고장 건너뛰기, 1회 최대 건수, 진행 로그
- tests/test_refresh.py: 인터넷 없이 확인하는 테스트

#반드시 바꿀 것
- 일반식품 기준: SUBJECT_FILTER에 식품 경고장 제목도 걸리게 넓히기
  (예: Unapproved New Drug, Misbranded, Food 관련 제목). 넓힌 이유를 주석으로
- 크롤링 간격 기본값 30초 (CRAWL_DELAY_SECONDS=30). dashboard 안내문과 맞추기
- 수집한 문구는 바로 사례집에 넣지 말고 '후보'로만 저장 → 사람이 '사례집 후보' 버튼으로 승인
- API 주소는 우리 쪽 규칙(/api/compliance/monitor/...)에 맞추기
- openFDA는 키 없이 먼저 → 실패할 때만 .env 키로 재시도 (404는 0건)
- 키는 .env에서만 읽기. 코드·HTML에 키 넣지 않기

#가져오지 않을 것
- zip의 화면(templates, static/app.js 화면 부분), 규칙 R0~R12, AI 지시문의 '건강기능식품' 표현

#확인
- 테스트를 인터넷 없이 실행해서 통과 결과 보여 주기
- 실제 1회 실행은 내가 직접 할 테니 실행 명령어만 알려 주기

#출력
보고 형식 + 실행 명령어 + .env에 넣을 항목 목록 (값은 비워서)
```

**사람이 할 일:** 서버 실행 → '최신 경고장 확인' 1번 → 로그에 "XLSX에서 경고장 링크를 찾지 못함"이 나오면 그 로그를 붙여 넣고 "컬럼 이름에 맞게 고쳐줘" → Commit

### 5단계 · 최종 검토 (코드 수정 금지)

> `Shift`+`Tab`으로 다시 **plan 모드**

```
#역할
아래 4명이 차례로 지금까지 바뀐 내용을 검토합니다
1. 마케터(실제 사용자): 화면이 헷갈리지 않는지, 카피 쓰는 시간이 줄었는지
2. 컴플라이언스 담당: 일반식품 기준에 맞는지, 놓치는 표현·오탐이 없는지
3. 개발자: 기존 기능이 깨지지 않았는지, 조원 코드와 부딪히는 곳이 없는지
4. 보안 담당: 키·개인정보가 코드나 깃에 들어가지 않았는지

#작업
main 브랜치와 지금 브랜치의 차이(git diff)를 보고 4명의 검토 의견을 써 주세요. 파일은 고치지 마세요.

#꼭 확인
- node tools/cross_check.js 결과: 원문 87개 PASS 0개, 안전 문구 8개 PASS 8개
- 서버 테스트 통과 여부
- git에 .env, _incoming/, 키가 들어간 HTML이 올라가지 않는지 (git status, git diff로 확인)
- CS 답장 템플릿 C1~C10이 바뀐 규칙에 걸려서 발송이 막히지 않는지
- dashboard.html 메뉴·디자인이 바뀌지 않았는지

#출력
4명 의견 표 (좋은 점 / 고칠 점 / 심각도 상·중·하)
→ 꼭 고쳐야 할 것 3개 (우선순위 순)
→ Pull Request 설명문 초안 (조원이 읽을 수 있게 쉬운 말로)
```

**사람이 할 일:** 꼭 고칠 것 3개를 default 모드에서 "1번만 고쳐줘"처럼 하나씩 → Push → GitHub에서 PR 만들고 조원에게 검토 요청

### 6단계 · (선택) 매일 자동 실행

> '남은 일'의 "③ 매일 자동 실행" · zip에도 없어서 새로 만들어야 함

```
#작업
4단계에서 만든 경고장 확인 작업을 하루 1번 자동으로 돌리는 방법을 제안해 주세요.

#조건
- 후보: GitHub Actions(schedule) / Render Cron Job / Windows 작업 스케줄러
- 무료로 가능한 것 우선, 키는 각 서비스의 비밀값(Secrets) 기능에만
- 결과는 '후보'로만 쌓이고 사례집 반영은 사람이 승인

#순서
1) 3가지 방법을 표로 비교 (난이도 · 비용 · 팀 공유 · 필요한 설정)
2) 내가 하나를 고르면 그때 파일을 만들기 (고르기 전에는 만들지 않기)
```
