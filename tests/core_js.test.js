/* core/core.js 단위 확인 (브라우저 없이): node tests/core_js.test.js
   - 페이지에 실린 저장소 값을 동기로 읽기
   - set → 0.3초 뒤 PUT /api/store/<파트>/<키> (CSRF 토큰 자동 첨부)
   - 다른 주소(외부 API) 요청에는 CSRF 토큰을 붙이지 않음
   - register/expose/use · settings/home 기본 공개 기능 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const BOOT = {
  part: 'cs', csrf: 'tok-123', today: '2026-10-23',
  user: { id: 1, name: '테스터', email: 't@x.test' },
  store: { cs: { inquiries: [{ id: 'A' }] } },
  seasons: [{ id: 1, name: 'Halloween', start: '2026-10-31', dday: 8, inLead: true }],
  facts: { brand: { name: 'REBOOT' }, products: [{ id: 'jelly_original' }], flat: { brand: 'REBOOT', product: null }, version: 'v1' },
  parts: []
};

const calls = [];
const listeners = {};
const document = {
  readyState: 'complete',
  getElementById: id => (id === 'reboot-boot' ? { textContent: JSON.stringify(BOOT) } : null),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
  createElement: () => ({ classList: { add() {}, toggle() {}, remove() {} }, setAttribute() {}, appendChild() {} }),
  body: { appendChild() {} },
  documentElement: { setAttribute() {}, removeAttribute() {} },
  visibilityState: 'visible'
};
const window = {
  document,
  location: { href: 'http://127.0.0.1:5000/dashboard/cs', origin: 'http://127.0.0.1:5000', pathname: '/dashboard/cs', search: '' },
  localStorage: { getItem: () => null, setItem() {} },
  addEventListener() {},
  fetch: (input, init) => {
    calls.push({ url: String(input), init: init || {} });
    return Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve({ ok: true }) });
  },
  setTimeout, clearTimeout, URL, Headers: globalThis.Headers, Intl, JSON, Promise, console
};
window.window = window;
window.globalThis = window;

const src = fs.readFileSync(path.join(__dirname, '..', 'core', 'core.js'), 'utf8');
vm.createContext(window);
vm.runInContext(src, window);
const R = window.REBOOT;

(async () => {
  // 동기 읽기 + 복사본
  const st = R.store('cs');
  const got = st.get('inquiries', []);
  assert.deepStrictEqual(got, [{ id: 'A' }]);
  got.push({ id: 'B' });
  assert.strictEqual(st.get('inquiries').length, 1, 'get은 복사본을 돌려줘야 함');
  assert.strictEqual(st.get('missing', 'fallback'), 'fallback');

  // set → 서버 PUT (CSRF 토큰 포함)
  st.set('inquiries', [{ id: 'A' }, { id: 'C' }]);
  st.set('inquiries', [{ id: 'A' }, { id: 'D' }]);   // 0.3초 안의 두 번째 값만 전송
  await new Promise(r => setTimeout(r, 450));
  const puts = calls.filter(c => c.url === '/api/store/cs/inquiries');
  assert.strictEqual(puts.length, 1, '0.3초 안의 쓰기는 한 번으로 묶여야 함');
  assert.strictEqual(puts[0].init.method, 'PUT');
  assert.strictEqual(puts[0].init.headers.get('X-CSRFToken'), 'tok-123');
  assert.deepStrictEqual(JSON.parse(puts[0].init.body), [{ id: 'A' }, { id: 'D' }]);

  // remove → DELETE
  st.remove('inquiries');
  await new Promise(r => setTimeout(r, 450));
  assert.ok(calls.some(c => c.url === '/api/store/cs/inquiries' && c.init.method === 'DELETE'));

  // 외부 주소에는 토큰을 붙이지 않음 (CORS 사전 요청 방지)
  await window.fetch('https://api.fda.gov/food/enforcement.json', { method: 'POST', body: '{}' });
  const ext = calls[calls.length - 1];
  assert.ok(!ext.init.headers || !(ext.init.headers.get && ext.init.headers.get('X-CSRFToken')), '외부 요청에 CSRF 토큰 금지');

  // 잘못된 이름
  assert.throws(() => R.store('BAD'));
  assert.throws(() => st.set('bad key!', 1));

  // 파트 등록 · 공개 기능
  R.register({ id: 'cs', render() {} });
  R.expose('cs', { stats: () => ({ open: 1 }) });
  assert.strictEqual(R.use('cs').stats().open, 1);
  assert.throws(() => R.use('nothing'));
  assert.strictEqual(R.use('settings').facts().brand, 'REBOOT', 'settings.facts 기본 공개');
  assert.strictEqual(R.use('home').seasons()[0].name, 'Halloween', 'home.seasons 기본 공개');
  assert.strictEqual(R.today(), '2026-10-23');

  // 표시 도우미
  assert.strictEqual(R.ui.esc('<b>"x"</b>'), '&lt;b&gt;&quot;x&quot;&lt;/b&gt;');
  assert.strictEqual(R.ui.fmt.dday('2026-10-31'), 'D-8');
  assert.ok(R.ui.csv([['a', 'b,c']]).endsWith('a,"b,c"'));

  console.log('core.js 확인 통과');
})().catch(e => { console.error(e); process.exit(1); });
