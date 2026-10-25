"""인플루언서 · 파트너 파트 서버 (담당: 혁주). 설명서: tabs/partners/docs/README.md · NOTES.md

  routes.py      /api/partners/* (업체 · 메일 · 첨부 · 가이드 · 이력 · 답장 확인 · 판매 실적)
  models.py      partners_contacts · partners_history · partners_attachments
  importer.py    업체 파일 가져오기 (케이터링 CSV · 인플루언서 XLSX) + 세부 카테고리 키워드 SUBCATEGORIES
  prompts.py     메일·가이드·번역·회신 요약 프롬프트 + GUIDE_PURPOSES
  mailer.py      Gmail SMTP 발송 · IMAP 답장/반송 확인 (앱 비밀번호)
  guide_docx.py  협업 가이드 Markdown → Word
  sales.py       크리에이터별 판매 실적 더미 데이터 (Shopify 연동 전)
"""
