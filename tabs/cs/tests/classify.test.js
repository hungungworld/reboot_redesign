/* CS 문의 분류 확인 (인터넷·브라우저 없이) — GitHub 자동 테스트가 `node tabs/cs/tests/classify.test.js`로 실행.
   부작용 신고(C1)를 놓치면 FDA 보고 대상 판단이 늦어지므로, 증상이 들어 있는 문의는 다른 내용이 섞여도 C1이어야 합니다. */
'use strict';
const assert = require('assert');
const path = require('path');

const api = {};
global.window = global;
global.REBOOT = {
  store: () => ({ get: (k, f) => f, set: () => {}, remove: () => {}, keys: () => [] }),
  register: () => {},
  expose: (id, o) => { if (id === 'cs') Object.assign(api, o); },
  use: () => ({}),
  user: () => ({}),
  ui: {}
};
require(path.join(__dirname, '../static/data.js'));
require(path.join(__dirname, '../static/cs.js'));
const fixtures = require(path.join(__dirname, 'fixtures/fake_inquiries.json')).inquiries;

const cases = [
  ['아 진짜 제품 뭐야 이거 배송도 느려 맛도 없고 숙취해소의 효과는 전혀 없고 알러지 올라오는데 어떡할꺼임?', 'C1'],   // 2026-10-24 실제 사례
  ['젤리 먹고 입술이 부었어요', 'C1'],
  ['먹고 나서 토했어요. 환불해 주세요', 'C1'],
  ['I ate one stick and broke out in hives. Also I want a refund.', 'C1'],
  ['My lips got swollen after eating the jelly.', 'C1'],
  ['I had diarrhea after eating your jelly. Please refund me.', 'C1'],   // 2026-10-24 GPT 검토 ④ (예전 C3)
  ['I could not breathe after eating it.', 'C1'],                          // 예전 C10
  ['My son started choking on the jelly stick.', 'C1'],
  ['I felt nauseous and had a headache the next day.', 'C1'],
  ['My throat swelled up after the drink.', 'C1'],
  ['먹고 나서 숨쉬기가 힘들었어요', 'C1'],
  ['젤리가 목에 걸렸어요', 'C1'],
  ['임신 중인데 먹어도 되나요?', 'C2'],
  ["I'd like to send back my unopened box and get a refund.", 'C3']
];

let failed = 0;
const check = (name, fn) => { try { fn(); console.log(`ok   ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}\n     ${e.message}`); } };

check('증상이 있는 문의는 C1 (부작용 신고)', () => {
  const bad = cases.filter(([t, want]) => api.classify(t) !== want).map(([t, want]) => `${t.slice(0, 40)} → ${api.classify(t)} (예상 ${want})`);
  assert.deepStrictEqual(bad, [], bad.join(' | '));
});

check('Gmail 시연 메일 20건은 expect대로 분류', () => {
  const bad = fixtures.filter(x => api.classify(`${x.subject}\n${x.body}`) !== x.expect).map(x => `${x.id} → ${api.classify(`${x.subject}\n${x.body}`)} (예상 ${x.expect})`);
  assert.deepStrictEqual(bad, [], bad.join(' | '));
});

check('제품 추정: 정식 제품명·저당 표현을 먼저 (GPT 검토 ㉚)', () => {
  const guess = t => (global.REBOOT_DATA.cs.productHints.find(h => new RegExp(h.re, 'i').test(t)) || {}).id;
  assert.strictEqual(guess('저당 음료를 샀어요'), 'drink-ls');
  assert.strictEqual(guess('Reboot Drink Balance tastes odd'), 'drink-ls');
  assert.strictEqual(guess('Jelly Balance box'), 'jelly-ls');
  assert.strictEqual(guess('The drink leaked'), 'drink');
  assert.strictEqual(guess('jelly sticks'), 'jelly');
});

if (failed) { console.log(`\n${failed}개 실패`); process.exit(1); }
console.log('\n모두 통과');
