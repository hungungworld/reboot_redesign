/* 서버 재검사용 규칙 내보내기 — `node tabs/compliance/tools/export_server_rules.js`
   판정 엔진은 브라우저(compliance.js)에 있지만, CS 답장 승인·발송 때 서버도 같은 규칙으로 다시 점검합니다 (2026-10-24).
   data.js 규칙을 고친 뒤 이 명령으로 tabs/compliance/server_rules.json을 다시 만드세요.
   engine.test.js가 파일이 최신인지 확인하고, pytest가 서버 판정이 JS 판정과 같은지(golden) 확인합니다. */
'use strict';
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, '..', 'server_rules.json');

function load() {
  const api = {};
  const prev = global.REBOOT;   // 테스트가 쓰던 REBOOT는 끝나고 되돌림
  global.window = global;
  global.REBOOT = {
    store: () => ({ get: () => null, set: () => {}, remove: () => {}, keys: () => [] }),
    register: () => {}, expose: (id, o) => { if (id === 'compliance') Object.assign(api, o); },
    use: () => ({}), user: () => ({}), ui: {}
  };
  const files = ['data.js', 'compliance.js'].map(f => path.join(__dirname, '..', 'static', f));
  files.forEach(f => { delete require.cache[require.resolve(f)]; require(f); });   // 테스트에서 이미 불러왔어도 다시 실행
  if (prev) global.REBOOT = prev;
  return { C: global.REBOOT_DATA.compliance, api };
}

// compliance.js termPattern과 같은 식 (하이픈·띄어쓰기·따옴표·복수형·과거형 · 자음+y → ies)
function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function termPattern(term) {
  const yEnd = /[^aeiou\s-]y$/i.test(term);
  const body = escapeRe(yEnd ? term.slice(0, -1) : term).replace(/-|\s+/g, '[\\s-]*').replace(/'/g, "['’]?");
  return '(?<![A-Za-z0-9])' + body + (yEnd ? '(?:y|ys|ies|ied|ying)' : '(?:s|es|d|ed|ing)?') + '(?![A-Za-z0-9])';
}

// 서버 판정과 브라우저 판정이 같아야 하는 문장들 (경고장 원문 · 안전 문구 · 예문 · 대체 문구 · CS 템플릿)
const EXTRA = [
  'Prevents hangovers.', 'We give a free sample if you leave a 5-star review.', 'Tell us what you think.',
  'Thank you for your message. We are sorry to hear that. Please stop using the product and contact a doctor.',
  'Contains Hovenia dulcis extract.', 'Clinically tested formula.', 'Supports liver health.',
  'Rich in Hovenia dulcis.', 'Doctor recommended.', 'Drink all night with Reboot.', 'I felt great the next morning!',
  "Say goodbye to hang-overs", 'No more Asian glow.', '숙취 해소에 좋아요', '간 보호 젤리', 'Guaranteed results, 100% safe.'
];

function build() {
  const { C, api } = load();
  const rules = C.rules.filter(r => !r.off).map(r => ({
    code: r.code, name: r.name, verdict: r.verdict,
    byChannel: r.byChannel || {},
    regex: r.terms.map(termPattern).concat(r.patterns || []),
    ko: r.ko || [],
    compensated: r.compensated || [],
    incentive: r.incentive || [],
    incentiveVerdict: r.incentiveVerdict || '',
    allow: r.allow || []
  }));
  const texts = [...new Set([].concat(
    C.warningQuotes.map(q => q.text), C.safePhrases.map(p => p.text), (C.selfTest || []).map(t => t.text),
    (C.evalSet || []).map(t => t.text),
    (C.mappings || []).map(m => m.us), EXTRA))].filter(Boolean);
  const golden = texts.map(t => [t, api.review(t, { channel: 'cs' }).verdict]);
  return { version: C.version, channel: 'cs', rules, golden };
}

if (require.main === module) {
  fs.writeFileSync(OUT, JSON.stringify(build(), null, 1) + '\n', 'utf8');
  console.log('저장:', path.relative(process.cwd(), OUT));
}

module.exports = { build, OUT };
