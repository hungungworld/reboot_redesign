/* 인플루언서 · 파트너 — 외주 컨택 · 협업 메일 · 협업 가이드 기본값 (outreach.js가 씀)
   - 생성 프롬프트 기본값 · 세부 카테고리 키워드 · 가이드 목적 선택지는 서버(tabs/partners/)에 있습니다
   - 여기 문구는 화면의 처음 채워진 값이며, 사용자가 바꾼 값은 REBOOT.store('partners')의 outreach.*에 저장됩니다
   - 금지 표현(치료·예방·숙취 등)은 일부러 쓰지 않습니다 (컴플라이언스 점검에 걸리지 않게) */
(function (global) {
  'use strict';
  const NS = global.REBOOT_DATA = global.REBOOT_DATA || {};
  const P = NS.partners = NS.partners || {};

  P.outreach = {
    types: ['케이터링', '인플루언서'],
    typeIcon: { 케이터링: '🍽️', 인플루언서: '📱' },
    typeTone: { 케이터링: 'orange', 인플루언서: 'purple' },
    periodDays: 30,   // 기간 기본값: 오늘 ~ 30일 뒤

    // 📐 메일 구조 (서버 설정을 못 불러올 때 표시용)
    mailSteps: ['인사', '회사 소개', '협업 이유', '제안 (2–3개 항목)', '일정', '다음 단계', '맺음말'],

    // 구분별 기본 문구 — 원하는 파트너 · 협업 목적 · 제안 내용 · 요청사항
    defaults: {
      케이터링: {
        ideal: '미국 페스티벌·나이트라이프·대학가·VIP 이벤트를 운영하며 현장에서 음료·스낵 샘플링을 할 수 있는 케이터링·모바일 바 업체 (21세 이상 행사 운영 경험)',
        purpose: '미국 이벤트 현장에서 REBOOT 스틱 젤리·음료를 소개하고, 21세 이상 참석자의 시음 반응을 확인',
        proposal: '1) 이벤트 현장 샘플링 (애프터파티 리추얼 콘셉트)\n2) 모바일 바 메뉴에 REBOOT 무알코올 음료 페어링\n3) 이벤트 사진·현장 반응을 함께 SNS에 소개',
        requests: '1) 다가오는 이벤트 일정과 예상 참석 인원\n2) 샘플링 운영 조건과 비용\n3) 21세 이상 참석자 확인 방식'
      },
      인플루언서: {
        ideal: '21세 이상 미국 라이프스타일·나이트라이프·푸드 크리에이터와 일하며, 할인코드·제품 시딩 캠페인 경험이 있는 대행사·플랫폼',
        purpose: '미국 21세 이상 라이프스타일 소비자에게 REBOOT 인지도를 높이고, 크리에이터별 할인코드로 판매 반응을 측정',
        proposal: '1) 크리에이터 제품 시딩 (무상 제공)\n2) 크리에이터별 고유 할인코드 + 판매 수수료\n3) 숏폼 콘텐츠 (Instagram Reels · TikTok) 공동 기획',
        requests: '1) 추천 크리에이터 목록과 채널 지표 (21세 이상 오디언스 비율 포함)\n2) 캠페인 단가·수수료 구조\n3) FTC 광고 표기(#ad) 운영 방식'
      }
    },

    // 협업 가이드 입력 기본값 (일정 · 필수 전달사항 · 결과물·제출 기준)
    guideDefaults: {
      인플루언서: {
        schedule: '• 제품 발송: 협업 확정 후 3일 이내\n• 콘텐츠 초안 제출: 제품 수령 후 7일 이내\n• REBOOT 검토·피드백: 초안을 받은 뒤 2영업일\n• 게시: 최종 승인 후 3일 이내 (정확한 날짜는 협의 후 확정)\n• 기간: { }',
        must: '• 21세 이상 팔로워 대상 · 음주 장면은 책임 있는 음주로만\n• 건강 효능을 말하지 않고 애프터파티·모닝 리추얼로 소개 (금지 표현은 Do & Don\'t 표 참고)\n• #ad 또는 Paid partnership 표시를 캡션 첫 줄·영상 첫 화면에\n• 할인코드와 링크는 REBOOT이 준 것만 사용',
        deliverables: '• Instagram Reels 1개 (15–30초) + 스토리 2개 (링크 스티커)\n• 게시 전 초안(영상·캡션) 제출 → 승인 후 게시\n• 게시 7일 뒤 인사이트 캡처 (도달 · 조회 · 링크 클릭)'
      },
      케이터링: {
        schedule: '• 현장 운영 계획 공유: 이벤트 2주 전\n• 제품 입고: 이벤트 5일 전\n• 현장 샘플링: 이벤트 당일\n• 결과 공유: 이벤트 후 7일 이내 (정확한 날짜는 협의 후 확정)\n• 기간: { }',
        must: '• 21세 이상 참석자에게만 제공 · 과음을 권하는 멘트 금지\n• 건강 효능을 말하지 않고 애프터파티 리추얼로 소개 (금지 표현은 Do & Don\'t 표 참고)\n• 제품은 개별 포장 그대로 제공\n• 현장 사진 촬영은 참석자 동의를 받은 경우만',
        deliverables: '• 현장 사진 10장 이상 (브랜드 노출 컷 포함)\n• 샘플링 수량 · 참석 인원 간단 보고\n• 참석자 반응 메모 (자주 나온 질문 3개)'
      }
    },

    // 📎 첨부파일 (서버와 같은 규칙)
    attach: {
      accept: '.pdf,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.zip,.txt',
      exts: ['pdf', 'csv', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'png', 'jpg', 'jpeg', 'zip', 'txt'],
      maxFileMB: 25,
      maxTotalMB: 25
    },

    // 업체 파일 형식 안내
    importHelp: {
      catering: 'top50catering.csv 형식 · No · Company Name · Contact Email · Phone Number · Official Website URL · Office Location · Category Focus · Why Relevant',
      influencer: 'US_Influencer_Public_Contacts_72.xlsx · .csv 형식 · 6행 머리글 · 번호 · 업체명 · 공개 이메일 · 공개 전화/문자 · 공식 사이트 URL · 미국 시장 범위 · 서비스 형태 · 서비스/검토 이유 · 주의/연락처 용도'
    }
  };
})(typeof window !== 'undefined' ? window : globalThis);
