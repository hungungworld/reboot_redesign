/* 인플루언서 · 파트너 — 협업 이력 · 판매 실적 기준값 (records.js가 쓰고, outreach.js는 배지 표시에만 읽음)
   상태 이름은 서버(tabs/partners/routes.py)의 값과 같아야 합니다. */
(function (global) {
  'use strict';
  const NS = global.REBOOT_DATA = global.REBOOT_DATA || {};
  const P = NS.partners = NS.partners || {};

  P.records = {
    // 진행 상태(협업 단계) — 편집 창·수기 입력으로만 바꿈 (메일 발송 시 '컨택 중', 회신에 답장하면 '협의 중')
    progress: [
      { v: '컨택 중', emoji: '⚪', tone: '' },
      { v: '협의 중', emoji: '🟡', tone: 'orange' },
      { v: '협업 중', emoji: '🔵', tone: 'blue' },
      { v: '마케팅 진행 중', emoji: '🟣', tone: 'purple' },
      { v: '협업 완료', emoji: '🟢', tone: 'green' },
      { v: '보류', emoji: '🟤', tone: '' },
      { v: '협업 중단', emoji: '⚫', tone: 'dark' }
    ],
    // 상태(메일) — 발송 · 📥 답장 확인으로 자동 갱신. '회신 대기'는 편집 창에서 직접 지정
    mailStatus: [
      { v: '발송 성공', emoji: '🔵', tone: 'blue' },
      { v: '발송 실패', emoji: '🔴', tone: 'red' },
      { v: '반송', emoji: '🟠', tone: 'orange' },
      { v: '회신 받음', emoji: '🟢', tone: 'green' },
      { v: '회신 대기', emoji: '🟡', tone: 'lime' }
    ],
    kinds: [
      { v: '자동', tone: 'blue', help: '메일 발송 · 답장 확인으로 저장된 기록' },
      { v: '수기', tone: 'green', help: '직접 입력한 기록' },
      { v: '샘플', tone: 'orange', help: '예시 데이터 (실제 기록 아님)' }
    ],
    activities: ['전화', '미팅', '가이드 전달', '메모', '기타'],   // 수기 기록 활동 유형

    // 이력 표 항목 (⚙️ 표시 항목 · 순서의 기본값)
    columns: [
      { key: 'at', label: '일시' },
      { key: 'partner', label: '업체' },
      { key: 'kind', label: '출처' },
      { key: 'activity', label: '활동' },
      { key: 'mailStatus', label: '상태(메일)' },
      { key: 'progress', label: '진행 상태' },
      { key: 'owner', label: '담당자' },
      { key: 'subject', label: '제목' },
      { key: 'memo', label: '메모' },
      { key: 'to', label: '수신자', hidden: true },
      { key: 'attachments', label: '첨부', hidden: true }
    ],

    // 협업 보드 열 (업체별 가장 최근 기록의 진행 상태)
    board: [
      { title: '⚪ 컨택 중', match: ['컨택 중'] },
      { title: '🟡 협의 중', match: ['협의 중'] },
      { title: '🔵 협업 중', match: ['협업 중'] },
      { title: '🟣 마케팅 진행 중', match: ['마케팅 진행 중'] },
      { title: '🟢 협업 완료', match: ['협업 완료'] },
      { title: '🟤 보류 · ⚫ 중단', match: ['보류', '협업 중단'] }
    ],

    // 판매 실적 (🧪 더미) — 조회 기간 버튼
    salesPresets: [7, 14, 30, 60]
  };
})(typeof window !== 'undefined' ? window : globalThis);
