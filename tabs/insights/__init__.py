"""시장 · 고객 인사이트 (담당: 이현경) — 3번째 대시보드: 주간 리뷰 · 월간 리뷰 · 데이터.

  http.py       외부 호출 (requests · timeout · 테스트에서 바꿔 끼움)
  amazon.py     Amazon 진열대 · 대표 리뷰 (SerpApi)
  trends.py     Google 검색 트렌드 (SerpApi · CSV 올리기)
  weee.py       Weee! 진열대 (Firecrawl)
  youtube.py    유튜브 댓글 · 영상 (YouTube Data API)
  classify.py   규칙 분류 (리뷰 주제 · 댓글 질문 · 자주 쓰는 표현)
  rules.py      이번 주 액션 규칙 (판단은 규칙, AI는 문장만)
  ai.py         OpenAI 호출 (문장 다듬기 · 분류 · 월간 요약)
  service.py    화면용 데이터 묶기
  update.py     데이터 업데이트 (항목 선택 · 비용 · 뒤에서 실행)
  reports.py    Word · Excel 내려받기
  routes.py     /api/insights/…
수집 기준은 설정 파트의 tabs.settings.config.get_collect_config()를 씁니다.
"""
