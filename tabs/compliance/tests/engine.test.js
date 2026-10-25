/* 컴플라이언스 판정 엔진 확인 (인터넷·브라우저 없이) — GitHub 자동 테스트가 `node tabs/compliance/tests/engine.test.js`로 실행.
   규칙(data.js)을 고친 뒤 아래 중 하나라도 어긋나면 실패합니다.
   ① FDA 경고장 원문 87개 중 PASS 0개 (놓친 표현 없음)   ② 안전 문구는 모두 PASS (오탐 없음)
   ③ 자체 점검 예문 · 일괄 심의 더미 20개는 예상 판정대로   ④ 표현 매핑의 영어 대안은 PASS
   ⑤ CS 답변 템플릿 C1~C10은 BLOCK 없음   ⑥ '전체 채널·전체 제품'은 가장 엄격한 판정 */
'use strict';
const assert = require('assert');
const path = require('path');

const api = {};
global.window = global;
global.REBOOT = {
  store: () => ({ get: () => null, set: () => {}, remove: () => {}, keys: () => [] }),
  register: () => {},
  expose: (id, o) => { if (id === 'compliance') Object.assign(api, o); },
  use: () => ({}),
  user: () => ({}),
  ui: {}
};
const here = p => path.join(__dirname, p);
require(here('../static/data.js'));
require(here('../static/compliance.js'));
require(here('../../cs/static/data.js'));
const C = global.REBOOT_DATA.compliance;
const CS = global.REBOOT_DATA.cs;

let failed = 0;
function check(name, fn) {
  try { fn(); console.log(`ok   ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}\n     ${e.message}`); }
}
const verdict = (text, opts) => api.review(text, opts || {}).verdict;

check('경고장 원문 87개 중 PASS 0개', () => {
  assert.strictEqual(C.warningQuotes.length, 87, `경고장 문구 수 ${C.warningQuotes.length}`);
  const missed = C.warningQuotes.filter(q => verdict(q.text, { channel: 'b2c' }) === 'PASS').map(q => q.text);
  assert.deepStrictEqual(missed, [], `규칙이 놓친 문구: ${missed.join(' | ')}`);
});

check('안전 문구는 모두 PASS', () => {
  const bad = C.safePhrases.filter(p => verdict(p.text, { channel: 'b2c' }) !== 'PASS').map(p => p.text);
  assert.deepStrictEqual(bad, [], `잘못 걸린 안전 문구: ${bad.join(' | ')}`);
});

check('자체 점검 예문은 예상 판정대로', () => {
  const bad = C.selfTest.filter(t => verdict(t.text, t) !== t.expect).map(t => `${t.text} → ${verdict(t.text, t)} (예상 ${t.expect})`);
  assert.deepStrictEqual(bad, [], bad.join(' | '));
});

check('일괄 심의 더미 20개는 예상 판정대로 (예시 영어 · 줄마다 채널·제품)', () => {
  assert.strictEqual(C.batchSample.length, 20);
  const bad = C.batchSample.filter(x => verdict(x.copy, { channel: x.channel, product: x.product }) !== x.expect)
    .map(x => `${x.copy} → ${verdict(x.copy, x)} (예상 ${x.expect})`);
  assert.deepStrictEqual(bad, [], bad.join(' | '));
  assert.ok(C.batchSample.every(x => /[가-힣]/.test(x.ko)), '더미 문구는 한국어(ko)가 있어야 함');
});

check('표현 매핑의 영어 대안은 PASS', () => {
  const bad = C.mappings.filter(m => /^[A-Za-z]/.test(m.us) && verdict(m.us, { channel: 'b2c' }) !== 'PASS').map(m => m.us);
  assert.deepStrictEqual(bad, [], bad.join(' | '));
});

check('CS 답변 템플릿 C1~C10은 BLOCK 없음', () => {
  const bad = CS.types.filter(t => verdict([].concat(t.template).join('\n'), { channel: 'cs' }) === 'BLOCK').map(t => t.code);
  assert.deepStrictEqual(bad, [], `BLOCK 템플릿: ${bad.join(', ')}`);
});

check("'전체 채널·전체 제품'은 가장 엄격한 판정 + 조합 8개 (B2C·B2B × 제품 4)", () => {
  const r = api.review('Clinically tested formula.', { channel: 'all', product: 'all' });
  assert.strictEqual(r.verdict, 'REVISE');
  assert.strictEqual(r.variants.length, 8);
  assert.ok(r.variants.some(v => v.channel === 'b2b' && v.verdict === 'REVIEW'), 'B2B는 REVIEW');
  assert.strictEqual(verdict('Say goodbye to hangovers.', { channel: 'all', product: 'all' }), 'BLOCK');
});

check('규칙마다 근거 조문(law)이 있음', () => {
  const none = C.rules.filter(r => !(r.law || []).length).map(r => r.code);
  assert.deepStrictEqual(none, [], `근거 조문 없는 규칙: ${none.join(', ')}`);
});

check('규제 모니터링 제품군 태그 (젤리·음료·당·숙취)', () => {
  const tags = text => C.monitorProductTags.filter(t => new RegExp(t.re, 'i').test(text)).map(t => t.id);
  assert.deepStrictEqual(tags('Konjac jelly cups'), ['jelly']);
  assert.deepStrictEqual(tags('HTS 2202.99.9000'), ['drink']);
  assert.deepStrictEqual(tags('저당 음료 표시'), ['drink', 'sugar']);
  assert.deepStrictEqual(tags('Hovenia dulcis extract'), ['hangover']);
  assert.deepStrictEqual(tags('Rules of Practice'), []);
  assert.deepStrictEqual(tags('HTS 2106.90.9997'), ['jelly']);
  assert.ok(new RegExp(C.monitorTagSkip, 'i').test('Food Additives Permitted in Feed and Drinking Water of Animals'), '동물 사료 공고는 태그 제외');
});

check('당 함량 표현은 판정하지 않지만 미검토로 알려 줌 (PASS와 구분)', () => {
  const r = api.review('Low sugar jelly. Sugar free too.', { channel: 'b2c' });
  assert.strictEqual(r.verdict, 'PASS');
  assert.deepStrictEqual(r.unchecked.map(x => x.code).sort(), ['R11', 'R12']);
  assert.deepStrictEqual(api.review('Tastes like lime.', { channel: 'b2c' }).unchecked, []);
  assert.strictEqual(api.review('Low sugar jelly.', { channel: 'all', product: 'all' }).unchecked.length, 1);
});

check('처음 보는 문장 시험: GPT가 찾은 문장은 모두 맞고, 새 문장 점수는 떨어지지 않음', () => {
  const run = list => list.map(t => ({ t, v: verdict(t.text, { channel: 'b2c' }) }));
  const gpt = run(C.evalSet.filter(t => t.src === 'gpt'));
  const fresh = run(C.evalSet.filter(t => t.src !== 'gpt'));
  const bad = gpt.filter(x => x.v !== x.t.expect).map(x => `${x.t.text} → ${x.v}`);
  assert.deepStrictEqual(bad, [], `GPT 문장 틀림: ${bad.join(' | ')}`);
  const ok = fresh.filter(x => x.v === x.t.expect).length;
  console.log(`     새 문장 ${ok}/${fresh.length} (${Math.round(ok / fresh.length * 100)}점) · 규칙 보강 전 11/23`);
  assert.ok(ok >= 17, `새 문장 점수가 떨어짐: ${ok}/${fresh.length}`);   // 2026-10-24 기준 17/23
});

check('서버 재검사 규칙(server_rules.json)이 data.js와 같음 — 다르면 node tabs/compliance/tools/export_server_rules.js', () => {
  const fs = require('fs');
  const { build, OUT } = require(here('../tools/export_server_rules.js'));
  assert.deepStrictEqual(JSON.parse(fs.readFileSync(OUT, 'utf8')), JSON.parse(JSON.stringify(build())));
});

if (failed) { console.log(`\n${failed}개 실패`); process.exit(1); }
console.log('\n모두 통과');
