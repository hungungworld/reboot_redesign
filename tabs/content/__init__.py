"""콘텐츠 제작 파트 서버 (담당: 나원) — tabs/content/docs/README.md · 챗봇_구성_설명서.md.

시트 → 대화 → 초안·이미지 → 버전·최종본 → 내보내기.
상태 원본은 업무 DB(content_* 표)이고, 매 턴 서버가 최신 시트·결정·제품 사실을 다시 읽어 AI에 넘깁니다.
LangGraph 없이 conversation.run_turn()의 고정 단계(읽기 → 맥락 → AI 판단 → 검증 → 반영·실행 → 저장)로 처리합니다.

  options.py     콘텐츠·채널·결정 항목 목록, 예시 추천 (화면 data.js와 같은 ID)
  models.py      표: content_campaigns · content_messages · content_decision_events · content_versions · content_jobs
  repository.py  시트·결정 검증과 반영(한 경로), 리비전 확인, 직렬화
  heuristics.py  명확한 생성 요청 · 감상("좋네") · 제외 매체 · 부분 수정 대상
  prompts.py     작업별 지침 (conversation_core_v2 · sheet_suggestion_v2 · content_draft_v2 · creative_edit_v2 · review_v2)
  schemas.py     AI 구조화 출력(JSON Schema strict)
  providers.py   OpenAI 텍스트·이미지 어댑터
  drafts.py      텍스트 초안·부분 수정·자동 재작성
  review.py      키워드 확인 (브랜드 표기 · 제품별 근거 · 금지 내부 표현)
  media.py       이미지 생성·편집 작업 (뒤에서 실행)
  conversation.py 대화 한 턴
  export.py      ZIP 내보내기
  routes.py      /api/content/*
"""
