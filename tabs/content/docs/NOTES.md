# 콘텐츠 제작 — 구현 노트 (v1 · 2026-10-23)

README(설계 명세)와 챗봇 구성 설명서를 Flask 대시보드에 옮긴 첫 구현입니다. **"구현"과 "검증"을 구분해** 적었습니다.
테스트는 모두 키·인터넷 없이(AI·이미지 호출을 바꿔 끼워) 돌렸고, **실제 OpenAI 호출은 검증하지 않았습니다.**

## 1. 파일

| 위치 | 내용 |
| --- | --- |
| `tabs/content/static/data.js` | 콘텐츠 6종 · 채널 6개 · 목적 · 결정 항목 이름 · 빠른 버튼 · 포스터 · 컴플라이언스 채널 연결 (ID는 서버와 같음 — 테스트가 확인) |
| `tabs/content/static/content.js` | 화면 전체 (탭 2개 · 시트 · 대화 · 오른쪽 패널 · 내 캠페인 · 모달). `REBOOT.expose('content', { campaigns })` |
| `tabs/content/static/content.css` | `.part-content .cnt-…`만. 모달 본문에도 `part-content` 클래스를 붙여 같은 범위로 씀 |
| `tabs/content/models.py` | `content_campaigns` · `content_messages` · `content_decision_events` · `content_versions` · `content_jobs` |
| `tabs/content/repository.py` | 시트·결정 검증과 반영(한 경로), 리비전 확인, 직렬화, 멈춘 작업 정리 |
| `tabs/content/conversation.py` | 대화 한 턴 (아래 3장) |
| `tabs/content/heuristics.py` | 명확한 생성 요청 · "좋네" · 제외 매체("이미지는 말고") · 부분 수정 대상("2장만") · 버전 지칭("B안") |
| `tabs/content/prompts.py` · `schemas.py` | 작업별 지침 (conversation_core_v2 · sheet_suggestion_v2 · content_draft_v2 + 작업 템플릿 · creative_edit_v2 · review_v2) · 구조화 출력 |
| `tabs/content/drafts.py` | 텍스트 초안 · 부분 수정 보존(카드뉴스 장 · 숏폼 장면) · 자동 재작성 |
| `tabs/content/review.py` | 키워드 확인 (브랜드 표기 · 제품별 근거 · 금지 내부 표현) |
| `tabs/content/media.py` · `providers.py` | 이미지 작업 (뒤에서 실행) · OpenAI 텍스트/이미지 어댑터 |
| `tabs/content/export.py` | ZIP 내보내기 |
| `tabs/content/tests/test_content.py` | 서버 테스트 45개 |

## 2. API (`/api/content`, 모두 로그인 필요)

`GET meta` · `GET/POST campaigns` · `GET/PATCH/DELETE campaigns/<id>` · `POST campaigns/<id>/decisions` · `POST campaigns/<id>/chat` ·
`POST campaigns/<id>/versions` (직접 작성) · `POST campaigns/<id>/select` · `GET campaigns/<id>/export` · `POST suggest` ·
`POST versions/<id>/final` · `POST versions/<id>/review` (컴플라이언스 결과 기록) · `POST versions/<id>/ai-review` · `GET jobs/<id>` · `GET files/<이름>.png`

- 시트·결정을 바꾸는 요청은 `expected_revision`이 필요하고, 다르면 **409 + 최신 캠페인 상태**를 돌려줍니다 (조용히 덮어쓰지 않음).
  저장 직전에도 `UPDATE … WHERE revision = 예상값`으로 한 번 더 확인합니다 (AI를 부르는 사이 다른 저장이 끼어든 경우).
- 대화 요청의 `client_request_id`는 (캠페인, ID) UNIQUE — 재전송·중복 클릭은 409 `duplicate`로 막고 이미지 작업도 두 번 등록되지 않습니다.
- 키가 없으면 `chat`·`ai-review`는 503(한국어 안내). 캠페인 만들기·시트·결정 직접 입력·초안 직접 작성·컴플라이언스 검사·최종본·내보내기는 키 없이 됩니다.
  `suggest`는 키가 없거나 AI가 실패하면 서버의 **예시 추천**(`source: "example"`, 화면에 '예시 추천' 배지)을 돌려줍니다.

## 3. LangGraph 없이 상태를 다루는 방법 (README 5.1과 다른 점)

README는 LangGraph + 공식 SDK를 권장했지만, v1은 **의존성을 늘리지 않고 같은 흐름을 서버 함수의 고정 단계로** 구현했습니다.

1. **읽기** — 매 턴 DB에서 캠페인 최신 리비전 · 시트 · 결정 · 버전을 다시 읽음 (첫 메시지에만 시트를 붙이지 않음)
2. **맥락** — 브랜드 사실(항상) + 선택 제품의 사실(`catalog.product_facts`, 상태 포함) + 최근 대화 12개(서버 반영 결과 포함) + 다가오는 시즌 + 서버 신호
3. **판단** — 텍스트 모델 하나가 `intent · assistant_text · decision_patch · candidates · clarification_questions · action_request`를 strict JSON으로 돌려줌
4. **검증** — 허용 필드만(`sheet.*` 6개, `decisions.<영문 키>`), 등록된 제품 ID만, 대상 조건(브랜드 또는 제품 1개 이상) 유지, 사실 키(가격·성분·맛 라인업 등)는 거절하고 '확인 대기'로 기록,
   AI 제안이 확정값을 덮지 못함, "좋네"면 확정 → 제안으로 낮춤, 시트 변경은 명확한 확정일 때만(아니면 후보 카드), 이미 시트에 있는 걸 묻는 질문은 버림, 질문 최대 2개
5. **실행** — 모델의 실행 요청안 **그리고** 사용자 발화의 명확한 요청(또는 버튼)이 있을 때만: 텍스트 초안·수정은 같은 요청 안에서, 이미지는 작업으로 등록(뒤에서), 이전 버전 선택
6. **저장** — AI를 부르는 동안에는 DB에 쓰지 않고(SQLite 잠금 · 실패 시 상태 유지) 마지막에 짧은 트랜잭션 한 번. 실패(AI 오류·형식 오류·거절)면 아무것도 저장하지 않음

재개 지점은 DB 자체입니다 (체크포인트 표 없음). "반영했어요/만들었어요"는 서버가 실제로 저장·생성했을 때만 남기고(모델 문장에서 근거 없는 완료 문장은 제거),
반영 결과는 대화 아래 '반영 · 반영 안 함 · 확인 대기 · 초안' 줄로 따로 보여 줍니다.

## 4. 동작 요약

- **시트 5항목**: 콘텐츠 · 채널(기획서만 여러 개) · 대상(`brand_focus`와 `product_ids` 분리) 필수, 목적 · 추가 요청 선택. 참고 문구(기존 카피)는 접어 둠. 캠페인명 자동(예: `인스타 이미지 · 10.23`)
- **AI 추천**: 모달에 후보 3개 + 한 줄 이유. 고를 때만 적용(목적은 교체, 추가 요청은 덧붙임). 추천 뒤 콘텐츠·채널·대상이 바뀌었으면 적용하지 않음
- **결정 상태**: 확정(사용자 입력·선택·명확한 요청) · 제안(AI 후보) · 미정(값 없음). 값마다 출처·사용자·시간·리비전·출처 메시지. 방금 바뀐 항목 강조 + '최근 변경'에서 되돌리기
- **명확한 요청 판단** (`heuristics.py`): "바로 만들어줘", "카피만 먼저", "B안 더 짧게", 기준 버전이 있을 때 "배경만 검정으로" → 실행 / "좋네", "왜 이 구도야?", "검정도 괜찮겠다" → 실행 안 함
- **초안**: 기본 3안(A·B·C, 기획서 1개), 수정은 지정 버전만 새 버전(부모 연결). 카드뉴스 "2장만" · 숏폼 "장면 3만"은 **서버가 다른 장·장면을 원문 그대로 보존**
- **생성 직전 근거 리마인더**(PL06) · 키워드 확인에서 '수정 필요'(당 표현·숙취·슬로건 표기·내부 표시 '저당')가 나오면 설정 횟수(기본 1)만큼 자동 재작성 (요청 시간 150초가 넘으면 생략)
- **변경 사항 미반영**: 버전마다 생성 시점 시트·결정 스냅샷(캠페인명·상태 제외)을 저장하고 현재와 비교해 바뀐 항목을 표시. 늦게 끝난 작업도 최종본을 바꾸지 않음
- **이미지**: 명확한 요청 → `content_jobs` 등록 → 뒤에서 실행 (`queued → running → saving → succeeded/failed/submission_unknown`).
  프롬프트는 creative_edit_v2로 만들고(실패 시 결정 사항으로 만든 기본 프롬프트), **이미지 안에는 글자·로고를 그리지 않게** 하고 문구는 텍스트 레이어(미리보기 위에 겹쳐 보임)로 따로 둠.
  "배경만 흰색으로"는 기준 PNG를 `images.edit`에 실제로 넣어 편집(부모 연결, 원본 보존). 편집 미지원 모델이면 '새 시안(원본 편집 아님)'.
  비율은 모델 지원 크기(1024², 1024×1536, 1536×1024)로 근사하고 버전에 적음. 유료 호출은 자동 재시도·재제출하지 않음 (응답 시간 초과 → `submission_unknown`)
- **이미지 제공사**: AI 설정 `image_provider` — OpenAI(기본) 또는 Higgsfield `marketing-studio/image`(1k · 제품·Rebu 사진을 `image_urls` 참고 이미지로).
  Higgsfield 편집은 기준 이미지를 참고 이미지 1번으로 넣어 **다시 생성**(픽셀 편집 아님). 비율은 모델이 받는 값 중 가장 가까운 값(예: 4:5 → 3:4)으로 적음.
  결과는 PNG가 아닐 수 있어 형식대로 저장(png · jpg · webp). Higgsfield를 골랐는데 키가 없으면 만들지 않고 안내 (OpenAI로 자동 전환 없음)
- **영상**: AI 설정 `video_mode` — `plan_only`(기본: 숏폼 **구성안**만 · 실제 영상 아님) 또는 `higgsfield`(키가 있을 때만).
  실제 영상은 Higgsfield **Seedance 2.0 · 720p 고정** · 4~15초(결정 '영상 길이'에서, 없으면 5초) · 비율은 결정 '비율'(없으면 9:16) · 소리 없음.
  기준 구성안(가리킨 버전 → 기준 버전 → 최근 구성안)을 video_v1 프롬프트로 영어 장면 흐름으로 바꾸고, 제품·Rebu 사진이 있으면 `reference-to-video`, 없으면 `text-to-video`.
  자막·로고·대사는 넣지 않음. 결과는 `영상 N` 버전(mp4)으로 저장되고 키워드 확인 대상이 아님 (영상 속 장면은 사람이 확인)
- **Higgsfield 흐름** (`higgsfield.py`): 참고 사진 업로드(`/files/generate-upload-url` → PUT) → 제출(요청 ID를 작업 snapshot에 기록) → 5초마다 상태 확인
  (`completed · failed · nsfw · canceled`) → 결과 다운로드·저장. 이미지 5분 · 영상 15분 안에 안 끝나거나 제출 응답을 못 받으면 `submission_unknown`(재제출 없음).
  취소는 `POST /jobs/<id>/cancel` → Higgsfield에 실제 취소 요청 (시작 전 요청만 가능, 시작 후면 그렇게 안내)
- **검토 3층 분리**: 키워드 확인(서버 규칙, 버전 저장 시 자동) · 컴플라이언스(화면에서 `REBOOT.use('compliance').review` — 채널 Amazon→amazon, 그 밖(CRM 포함)→b2c,
  제품은 팩트시트 `compliance_id`, 제품이 여럿이면 제품별로 돌려 가장 엄한 판정) · AI 보조 검토(review_v2, 버튼). 기록이 없으면 '미검토', 컴플라이언스 파트가 없으면 '미연동'
- **최종본**: 최신 버전 선택과 별개. 지정해도 승인·사실 확인이 아님을 화면·이벤트·내보내기에 적음
- **내보내기 ZIP**: `00_README.txt`(초안·미승인 안내) · `campaign.md`(시트·결정·버전 표) · `versions/vNN-라벨-종류.md|png`(상태·검토·미반영·facts_version 머리말) · `facts.json` · `versions.json` · `settings.json`. 키 없음
- **작업 로그**: AI 호출은 `openai_json`이 자동 기록. 그 밖에 캠페인 생성·삭제, 최종본 지정/해제(승인 · 선정), 이미지 생성/실패, 내보내기
- **복원**: 주소 `#campaign=<id>` — 새로고침해도 시트·대화·결정·버전·작업이 돌아옴. 진행 중 작업은 화면이 3초마다 확인

## 5. 키 · 환경변수

| 이름 | 쓰는 곳 | 없을 때 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 대화 · 추천 · 초안 · 보조 검토 · 이미지 | 대화·AI 검토 503, 추천은 예시, 직접 작성은 됨 |
| `CONTENT_TEXT_MODEL` (선택) | 대화·제작 AI 모델 하나 | 공통 `OPENAI_MODEL`(기본 gpt-4o-mini) |
| `CONTENT_IMAGE_MODEL` (선택) | 이미지 모델 | `gpt-image-1` |
| `HIGGSFIELD_API_KEY` | Higgsfield 이미지 · 실제 영상 (`KEY_ID:KEY_SECRET` 형식 그대로) | 영상은 구성안만, Higgsfield 이미지는 만들지 않고 안내 |

**실제 키로 확인할 것 (미검증)**: strict JSON Schema(anyOf 포함)가 선택 모델에서 통과하는지, `gpt-image-1` 생성·편집(조직 인증 필요 여부), 응답 시간, 비용.
Higgsfield: 요청 본문 칸 이름(모델 문서 기준 — 실제 계정 호출 전), 결과 이미지 형식, 영상 생성 시간, 크레딧 차감. Seedance 2.0 공식 단가는 1천 토큰당 $0.014(720p · 할인 전)로 5초 영상 약 $1.5 수준이지만 앱에는 비용을 표시하지 않음.
API에는 Higgsfield 웹의 'Fast' 모드가 없어 Seedance 2.0 일반 모델을 씀.

## 6. 완료 기준 (README 11장) — 현재 상태

| ID | 상태 | 비고 |
| --- | --- | --- |
| A01 · A02 · A05 · A13 | 구현 · 화면 확인 | 탭 2개, 시트 5항목(필수 3), 추천 3개(선택 전 유지), 텍스트 모델 하나 |
| A03 · A04 · A07 | 구현 · 테스트 | 브랜드만 시작, 복수 제품 저장·칩·복원, 시트·채팅 양방향 같은 경로 |
| A06 | 부분 | 프롬프트 + 서버 질문 필터(목적·채널·콘텐츠·대상). 모델 문장 자체는 실제 호출로 확인 필요 |
| A08 · A09 · A10 · A12 | 구현 · 테스트(가짜 AI) | 명확한 요청만 실행, 질문은 답만, 카피만, B안만 새 버전, 2장만 수정 시 다른 장 보존 |
| A11 | 구현 · 테스트(가짜 이미지) | 기준 PNG를 편집 API에 전달, 부모 연결. 실제 모델 편집 품질은 미검증 |
| A14 · A15 · A16 | 부분 | 프롬프트 규칙 + 키워드 확인 + 근거 연결 검사 + 제품 근거 배지. 모델 출력 사실성은 실제 호출로 평가 필요 |
| A17 · A19 · A20 · A21 | 구현 · 테스트 | 복원, 멈춘 작업 → 결과 불명/실패(재제출 없음), 미반영 표시·최종본 유지, 미검토/미연동, ZIP |
| A18 | 부분 | 시간 초과 → `submission_unknown`, 파일 저장만 3번 재시도. OpenAI Images는 요청 조회가 없어 '대조'는 못 함 |
| A22 | 부분 | 동시 수정 409. 조직 구분은 없음(팀 공용 대시보드) — 삭제만 만든 사람·관리자 |
| A23 | 미검증 | 키 없음 |
| A24 | 부분 | 응답 payload에 `promptVersion`, 작업 로그에 프롬프트·모델·토큰. PromptRun 표·평가 기록은 없음 |

## 7. README와 다른 점 · 남은 일

- LangGraph·체크포인트 없음 (3장). BriefRevision 표 대신 결정 JSON + `content_decision_events`(이전/새 값) + 버전 스냅샷
- 작업 큐·outbox 없음: 작업마다 서버 스레드(`jobs.run_background`). 서버가 꺼지면 멈춘 작업은 12분 뒤 '실패(보내기 전)' 또는 '결과 불명'으로 바뀌고 다시 제출하지 않음
- OpenAI 이미지는 취소 없음 (동기 호출이라 실제 취소 요청을 보낼 수 없음). Higgsfield는 시작 전 요청만 취소 가능, `cancel_requested` 상태는 따로 두지 않음
- 사용자가 직접 올리는 참고 **이미지** 첨부 · 로고 원본 합성 · 카드뉴스 장별 이미지 파일 · 영상 편집(기존 영상 수정) 없음. 문구는 텍스트 레이어(미리보기 겹침 · 내보내기 텍스트)까지만
- 사용량은 작업 로그의 토큰까지만. 비용 추정은 표시하지 않음
- 페르소나(인사이트 파트)는 '타깃 참고' 버튼으로 입력창에 넣기만 함 (자동 전송·자동 확정 없음). 시즌은 시트 추가 요청 칩으로 덧붙이기만 함

## 8. 공통 담당에게 요청 (이 파트에서 고치지 않음)

1. `server/envkeys.py` `SERVICES`에 `CONTENT_TEXT_MODEL` · `CONTENT_IMAGE_MODEL`(group `setting`, secret False) 추가 → 설정 › 연결에서 바꿀 수 있게 (지금은 .env만 읽힘)
2. `.env.example` 콘텐츠 제작 칸에 `CONTENT_TEXT_MODEL=` · `CONTENT_IMAGE_MODEL=` 추가
3. `server/core/ai.py` `openai_json`에 `max_retries`(또는 `retries`) 인자 — 긴 초안 호출이 시간 초과 때 SDK 재시도로 두 배가 되지 않게 (지금은 호출 시간 제한을 짧게 잡아 대응)
4. (선택) `core.js` `ui.modal`에 닫기 함수 노출 — 지금은 모달 안 X 버튼을 눌러 닫음
5. 홈 파트: `REBOOT.use('content').campaigns()` → `{total, active, items}` 사용 가능 (실패 시 reject — try/catch)

## 9. 확인 방법

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tabs/content/tests/test_content.py tests/test_core.py
node --check tabs/content/static/content.js && node --check tabs/content/static/data.js
```

v1 작업 때 저장소 밖에서(jsdom + 실제 Flask 서버 + 가짜 AI/이미지) 화면 흐름 두 가지를 끝까지 눌러 봤습니다:
키 있음(시트 → 추천 → 시작 → 후보 선택 → 결정 변경 → 질문 → 초안 3안 → 컴플라이언스 → 최종본 → B안 수정 → 비교 → 이미지 작업 → 시트 수정·미반영 → 내 캠페인 검색·필터·열기 → 새로고침 복원 → 되돌리기)
과 키 없음(예시 추천 → 시작 → 입력창 꺼짐 → 결정 직접 추가 → 초안 직접 작성 → 키워드·컴플라이언스 → 직접 수정 → 내보내기). JS 오류 0건. 실제 브라우저 렌더링(레이아웃·다크 모드)은 사람 확인이 필요합니다.
