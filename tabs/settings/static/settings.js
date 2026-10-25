/* 설정 및 브랜드 관리 (담당: 이현경) — tabs/settings/docs/README.md 기준.
   탭: 브랜드 · 제품 / 수집 설정 / 연결 / 작업 로그 / 계정 (주소 끝 #account 등으로 바로 열림)
   - 제목 오른쪽 '변경 사항 적용' = 지금 탭에서 고친 입력 저장 (제품 상세·시즌·키·계정 관리는 창에서 바로 저장)
   - 팩트시트·시즌·계정은 공용 API, 수집 설정·연결은 /api/settings/…
   - 다른 파트에는 REBOOT.use('settings')로 facts · brand · products 공개 (제품별 값은 비워 둠) */
(function (global) {
  'use strict';

  const REBOOT = global.REBOOT;
  if (!REBOOT) throw new Error('settings: REBOOT core를 먼저 불러와야 합니다');
  const ui = REBOOT.ui;
  const { esc, badge, icon, fmt, toast } = ui;
  const api = REBOOT.api;

  const TABS = [['brand', '브랜드 · 제품'], ['collect', '수집 설정'], ['connections', '연결'], ['worklog', '작업 로그'], ['account', '계정']];
  const TAB_IDS = TABS.map(t => t[0]);
  const BRAND_FIELDS = ['name', 'base_company', 'market', 'founded', 'intro', 'slogan', 'colors', 'character', 'cm_song', 'category'];
  const PRODUCT_EDIT = ['category', 'form', 'flavors', 'flavor_status', 'pack', 'direction', 'ingredients', 'allergens', 'volume',
    'usage', 'consumer_name', 'price', 'cost', 'evidence', 'notes'];
  const SRC_FIELDS = [['flavors', '맛'], ['ingredients', '성분'], ['allergens', '알레르기 성분'], ['volume', '용량'], ['pack', '포장'],
    ['usage', '섭취법'], ['form', '제형'], ['category', '제품 분류'], ['etc', '기타']];
  const CORE_KEYS = ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'SERPAPI_KEY', 'YOUTUBE_API_KEY', 'FIRECRAWL_API_KEY'];
  const KINDS = ['수집', 'AI 프롬프트', '승인 · 선정', '기타'];
  const KIND_TONE = { '수집': 'blue', 'AI 프롬프트': 'purple', '승인 · 선정': 'lime', '기타': '' };
  const STATUS_TONE = { '완료': 'green', '검토 대기': 'orange', '실패': 'red' };
  const CONN_TONE = { '정상': 'green', '오류': 'red', '미연결': '', '확인 전': 'blue' };
  const SEASON_CAT = { holiday: '공휴일 · 명절', sports: '스포츠', occasion: '행사 · 기념일' };
  const SEASON_PRI = { 1: '1 · 최우선', 2: '2 · 보통', 3: '3 · 참고' };
  const LISTS = {
    'search_sets.A': { label: '검색어 세트 A · 제품 리뷰', ph: '예) hangover jelly review', max: 10 },
    'search_sets.B': { label: '검색어 세트 B · 숙취 일반', ph: '예) hangover remedy', max: 10 },
    'search_sets.K': { label: '검색어 세트 K · 한국 숙취해소제', ph: '예) korean hangover drink', max: 10 },
    amazon_queries: { label: 'Amazon 검색어', ph: '예) korean hangover', max: 15 },
    trend_keywords: { label: '트렌드 키워드', ph: '예) hangover drink', max: 10 },
    own_terms: { label: '자사 상품 표시 단어', ph: '예) aldicom', max: 10, len: 60 },
    weee_urls: { label: 'Weee! 주소', ph: 'https://www.sayweee.com/en/…', max: 15, len: 500, url: true },
    exclude_terms: { label: '제외 단어', ph: '예) soup', max: 20, len: 60 }
  };
  const ASIN_RE = /^[A-Z0-9]{10}$/;

  const $ = (el, sel) => (el ? el.querySelector(sel) : null);
  const $$ = (el, sel) => (el ? Array.from(el.querySelectorAll(sel)) : []);
  const copy = v => JSON.parse(JSON.stringify(v == null ? null : v));
  const val = v => (v == null ? '' : v);

  /* ───────── 팩트시트 (다른 파트에 공개) ───────── */
  const FACTS = { brand: REBOOT.data.brand() || {}, products: REBOOT.data.products() || [] };

  // CS 템플릿용 모양. 제품별 값(제품명·맛·성분·알레르기)은 여러 제품이 섞이지 않게 비워 둠 → CS가 제품별로 채움
  function flatFacts() {
    return { brand: (FACTS.brand && FACTS.brand.name) || 'REBOOT', product: null, flavor: null, ingredients: null, allergens: null };
  }
  REBOOT.expose('settings', {
    facts: flatFacts,
    brand: () => copy(FACTS.brand || {}),
    products: () => copy(FACTS.products || [])
  });

  /* ───────── 메뉴 숫자: 연결 안 된 핵심 서비스 (키 상태만 · 값은 받지 않음) ───────── */
  let missingKeys = null;
  function watchHealth(force) {
    if (typeof REBOOT.health !== 'function') return;
    Promise.resolve(REBOOT.health(force)).then(h => {
      if (!h || !h.keys) return;
      missingKeys = CORE_KEYS.filter(k => h.keys[k] === false).length;
      if (typeof REBOOT.refreshCounts === 'function') REBOOT.refreshCounts();
    }).catch(() => {});
  }
  watchHealth(false);

  /* ───────── 상태 ───────── */
  const S = {
    root: null, tab: 'brand', rendered: {}, dirty: { brand: false, collect: false, account: false },
    collect: null, collectMeta: {}, seasons: [], showPast: false,
    conns: null, setup: {}, isAdmin: !!REBOOT.user().isAdmin, gmail: null,
    wl: { kind: '', part: '', items: [], done: false, loading: false, seq: 0 },
    users: null
  };
  const panel = id => $(S.root, `[data-panel="${id}"]`);

  /* ───────── 작은 도우미 ───────── */
  function field(label, control, hintHtml) {
    return `<label class="set-fld"><span class="field-label">${esc(label)}</span>${control}${hintHtml ? `<small class="hint">${hintHtml}</small>` : ''}</label>`;
  }
  function group(label, inner, hintHtml) {
    return `<div class="set-fld"><span class="field-label">${esc(label)}</span>${inner}${hintHtml ? `<small class="hint">${hintHtml}</small>` : ''}</div>`;
  }
  function inp(name, value, attrs) {
    return `<input class="input" name="${name}" value="${esc(val(value))}" ${attrs || 'maxlength="300"'}>`;
  }
  function area(name, value, rows) {
    return `<textarea class="input" name="${name}" rows="${rows || 3}" maxlength="2000">${esc(val(value))}</textarea>`;
  }
  function showErr(box, msg) {
    if (!box) return toast(msg, 'error');
    box.textContent = msg;
    box.hidden = !msg;
    if (msg && box.scrollIntoView) box.scrollIntoView({ block: 'nearest' });
  }
  function closeModal(bodyEl) {
    const wrap = bodyEl && bodyEl.closest('.modal-backdrop');
    const x = wrap && wrap.querySelector('[data-modal-close]');
    if (x) x.click();
  }
  /** 저장 버튼이 있는 창: onSave(bodyEl)가 끝나면 닫힘, 실패하면 [data-ref="err"]에 문장 */
  function formModal(o) {
    return ui.modal({
      title: o.title, body: o.body, wide: o.wide, onOpen: o.onOpen,
      actions: (o.extra || []).concat([{ label: '취소', value: null }, {
        label: o.saveLabel || '저장', tone: o.danger ? 'danger' : 'lime',
        onClick: (bodyEl, btn) => {
          const err = $(bodyEl, '[data-ref="err"]');
          showErr(err, '');
          ui.busy(btn, true);
          Promise.resolve().then(() => o.onSave(bodyEl)).then(ok => {
            ui.busy(btn, false);
            if (ok !== false) closeModal(bodyEl);
          }).catch(e => { ui.busy(btn, false); showErr(err, e.message || String(e)); });
          return false;
        }
      }])
    });
  }
  function stamp(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return String(iso);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function partLabel(id) {
    const m = (REBOOT.meta() || []).find(p => p.id === id);
    return m ? m.label : (id || '공통');
  }
  function getPath(obj, path) { return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj); }
  function setPath(obj, path, v) {
    const ks = path.split('.');
    const last = ks.pop();
    const o = ks.reduce((a, k) => (a[k] = a[k] || {}), obj);
    o[last] = v;
  }

  /* ───────── 화면 ───────── */
  function render(root) {
    S.root = root;
    root.classList.add('part-settings');
    root.innerHTML = `
      <div class="tabs" role="tablist" aria-label="설정 메뉴">${TABS.map(([id, label]) =>
        `<button type="button" role="tab" data-tab="${id}" aria-selected="false">${esc(label)}</button>`).join('')}</div>
      ${TABS.map(([id]) => `<div class="set-panel" data-panel="${id}" role="tabpanel" hidden></div>`).join('')}`;
    setupActions();
    root.addEventListener('click', onClick);
    root.addEventListener('input', onInput);
    root.addEventListener('change', onChange);
    root.addEventListener('keydown', onKeydown);
    global.addEventListener('beforeunload', e => {
      if (Object.values(S.dirty).some(Boolean)) { e.preventDefault(); e.returnValue = ''; }
    });
    global.addEventListener('hashchange', () => { const t = tabFromHash(); if (t && t !== S.tab) show(t); });
    const gmail = gmailParam();
    show(gmail ? 'connections' : (tabFromHash() || 'brand'));
    if (gmail) gmailToast(gmail);
  }

  function tabFromHash() {
    const h = String(global.location ? global.location.hash : '').replace(/^#/, '');
    return TAB_IDS.includes(h) ? h : '';
  }

  function show(tab) {
    if (!TAB_IDS.includes(tab)) tab = 'brand';
    S.tab = tab;
    $$(S.root, '[data-tab]').forEach(b => {
      const on = b.dataset.tab === tab;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', String(on));
    });
    $$(S.root, '[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== tab; });
    if (!S.rendered[tab]) {
      S.rendered[tab] = true;
      ({ brand: renderBrand, collect: renderCollect, connections: renderConnections, worklog: renderWorklog, account: renderAccount })[tab](panel(tab));
    }
    try {
      const u = new URL(global.location.href);
      u.searchParams.delete('gmail');
      u.hash = tab;
      global.history.replaceState(null, '', u.pathname + u.search + u.hash);
    } catch (e) { /* 주소 바꾸기 실패는 무시 */ }
    updateDirty();
  }

  function setupActions() {
    const box = REBOOT.page.actions();
    if (!box) return;
    box.innerHTML = `<span class="badge orange" data-ref="set-dirty" hidden></span>` +
      `<button type="button" class="btn lime" data-act="set-apply">${icon('check')}변경 사항 적용</button>`;
    box.addEventListener('click', e => {
      const b = e.target.closest('[data-act="set-apply"]');
      if (b) apply(b);
    });
  }

  function updateDirty() {
    const box = REBOOT.page.actions();
    const el = $(box, '[data-ref="set-dirty"]');
    const names = Object.keys(S.dirty).filter(k => S.dirty[k]).map(k => (TABS.find(t => t[0] === k) || [k, k])[1]);
    if (el) {
      el.hidden = !names.length;
      el.textContent = names.length ? `저장 안 된 변경 · ${names.join(', ')}` : '';
    }
  }
  function setDirty(tab, on) { S.dirty[tab] = !!on; updateDirty(); }

  async function apply(btn) {
    const tab = S.tab;
    const fn = { brand: saveBrand, collect: saveCollect, account: saveMe }[tab];
    if (!fn) {
      toast(tab === 'connections' ? '연결 값은 「키 입력」 창에서 바로 저장돼요.' : '작업 로그는 자동으로 기록돼요. 고치거나 지울 수 없어요.');
      return;
    }
    if (!S.dirty[tab]) { toast('이 탭에는 저장할 변경 사항이 없어요.'); return; }
    ui.busy(btn, true);
    try {
      await fn();
      const others = Object.keys(S.dirty).filter(k => S.dirty[k]);
      if (others.length) toast(`저장했어요. ${others.map(k => (TABS.find(t => t[0] === k) || [k, k])[1]).join(', ')} 탭에도 저장 안 된 변경이 있어요.`);
    } catch (e) {
      toast(e.message || String(e), 'error');
    } finally {
      ui.busy(btn, false);
    }
  }

  /* ───────── 이벤트 (탭 안 버튼은 data-act로) ───────── */
  function onClick(e) {
    const tabBtn = e.target.closest('[data-tab]');
    if (tabBtn && S.root.contains(tabBtn)) { show(tabBtn.dataset.tab); return; }
    const b = e.target.closest('[data-act]');
    if (!b || !S.root.contains(b) || b.disabled) return;
    const id = b.dataset.id;
    switch (b.dataset.act) {
      case 'product': openProduct(id); break;
      case 'list-add': addToList(b.dataset.list); break;
      case 'list-del': removeFromList(b.dataset.list, +b.dataset.i); break;
      case 'lang': setLang(b.dataset.v); break;
      case 'asin-add': addAsin(); break;
      case 'asin-del': removeAsin(+b.dataset.i); break;
      case 'collect-reset': resetCollect(); break;
      case 'season-add': openSeason(null); break;
      case 'season-edit': openSeason(S.seasons.find(s => String(s.id) === id)); break;
      case 'season-del': deleteSeason(S.seasons.find(s => String(s.id) === id)); break;
      case 'season-monitor': toggleMonitor(b, S.seasons.find(s => String(s.id) === id)); break;
      case 'season-import': importSeasons(); break;
      case 'conn-key': openKey(b.dataset.name); break;
      case 'conn-test': testConn(b, b.dataset.name); break;
      case 'conn-reload': loadConnections(); break;
      case 'gmail-login': global.location.href = '/api/cs/gmail/connect'; break;
      case 'gmail-off': gmailDisconnect(b); break;
      case 'copy': copyText(b.dataset.text); break;
      case 'wl-kind': S.wl.kind = b.dataset.v || ''; drawWlFilters(); loadWorklog(true); break;
      case 'wl-more': loadWorklog(false); break;
      case 'wl-open': openLog(S.wl.items.find(x => String(x.id) === id)); break;
      case 'wl-csv': worklogCsv(b); break;
      case 'wl-xlsx': worklogXlsx(); break;
      case 'pw-change': changePassword(b); break;
      case 'user-add': addUser(); break;
      case 'user-flag': toggleUser(b); break;
      case 'user-pw': resetUserPassword((S.users || []).find(u => String(u.id) === id)); break;
      case 'user-unlock': patchUser((S.users || []).find(u => String(u.id) === id), { unlock: true }, '잠금을 풀었어요.'); break;
      case 'users-reload': loadUsers(); break;
      default: break;
    }
  }

  function onInput(e) {
    const t = e.target;
    if (t.closest('[data-form="brand"]')) {
      setDirty('brand', true);
      if (t.name === 'colors') drawSwatches();
      return;
    }
    if (t.closest('[data-form="account"]')) { setDirty('account', true); return; }
    if (t.dataset.num) {
      S.collect[t.dataset.num] = t.value;
      setDirty('collect', true);
      drawEstimate();
      return;
    }
    if (t.dataset.asin != null) {
      const row = S.collect.tracked_asins[+t.dataset.asin];
      if (!row) return;
      if (t.dataset.k === 'asin') {
        const v = t.value.toUpperCase().replace(/\s+/g, '');
        if (v !== t.value) t.value = v;
        row.asin = v;
        const bad = !!v && !ASIN_RE.test(v);
        t.classList.toggle('set-invalid', bad);
        t.setAttribute('aria-invalid', String(bad));
      } else if (t.dataset.k === 'label') row.label = t.value;
      setDirty('collect', true);
      drawEstimate();
    }
  }

  function onChange(e) {
    const t = e.target;
    if (t.dataset.asin != null && t.dataset.k === 'own') {
      const row = S.collect.tracked_asins[+t.dataset.asin];
      if (row) { row.own = t.checked; setDirty('collect', true); }
      return;
    }
    if (t.dataset.act === 'season-past') { S.showPast = t.checked; drawSeasons(); return; }
    if (t.dataset.ref === 'wl-part') { S.wl.part = t.value; loadWorklog(true); }
  }

  function onKeydown(e) {
    const t = e.target;
    if (e.key === 'Enter' && t.dataset && t.dataset.add) { e.preventDefault(); addToList(t.dataset.add); }
  }

  /* ═════════ 1. 브랜드 · 제품 ═════════ */
  function renderBrand(el) {
    el.innerHTML = `
    <div class="workbench">
      <div class="stack">
        <section class="card">
          <div class="card-head"><h2>브랜드 기본 정보</h2><span class="small muted" data-ref="brand-meta"></span></div>
          <form class="set-form" data-form="brand" autocomplete="off"></form>
        </section>
        <section class="card">
          <div class="card-head"><h2>제품 정보 시트</h2>${badge('REBOOT 4종 · 가상 출시', 'dark')}</div>
          <div data-ref="products"></div>
        </section>
      </div>
      <aside class="stack">
        <section class="card tinted">
          <div class="card-head"><h2>규칙</h2>${badge('설정 README 2장', 'lime')}</div>
          <ul class="set-rules">
            <li><b>실제와 가상을 섞지 않아요.</b> 성분 · 용량 · 맛 · 섭취법은 알디콤 기존 제품의 기사 · 공식 정보로만 채우고, 가격 · 원가만 <span class="badge purple">가상값</span>이에요.</li>
            <li><b>가상 가격은 실제 시장을 참고해요.</b> <a class="link" href="/dashboard/insights#data/shelf">인사이트 › 진열대</a>의 1회분 단가 구간($3 미만 / $3~6 / $6 이상)을 보고 정해요.</li>
            <li>알디콤의 <b>실제 판매가</b>는 팩트시트에 넣지 않아요. 인사이트가 Weee! · 아마존에서 모아 보여줘요.</li>
            <li><b>내부 제품명은 광고 문구에 자동으로 쓰지 않아요.</b> "저당" 같은 이름이 효능 · 당류 주장처럼 보일 수 있어서, 소비자에게 보이는 문구는 컴플라이언스 검토를 거쳐요.</li>
          </ul>
        </section>
        <section class="card">
          <h3>근거 상태</h3>
          <div class="set-legend">
            <p>${badge('제품 근거 확인 필요', 'orange')} 출처가 없거나 아직 확인하지 않음</p>
            <p>${badge('근거 확인 완료', 'green')} 항목별 출처(기사 · 공식 페이지 URL)를 확인함</p>
            <p>${badge('맛 임시', 'blue')} 맛 구성이 확정되지 않음</p>
          </div>
          <p class="panel-note mt-16">출처가 하나도 없으면 '확인 완료'로 바꿀 수 없어요. 모르는 값은 비워 두세요 (지어내지 않기).</p>
        </section>
      </aside>
    </div>`;
    fillBrand();
    renderProducts();
    refreshFacts();
  }

  async function refreshFacts() {
    try {
      const d = await api('/api/facts');
      FACTS.products = d.products || [];
      if (!S.dirty.brand) { FACTS.brand = d.brand || {}; fillBrand(); }
      renderProducts();
    } catch (e) { /* 페이지에 실린 값으로 계속 */ }
  }

  function fillBrand() {
    const el = panel('brand');
    const form = $(el, '[data-form="brand"]');
    if (!form) return;
    const b = FACTS.brand || {};
    const f = (k, label, hint) => field(label, inp(k, b[k]), hint);
    form.innerHTML = `
      <div class="field-grid">${f('name', '브랜드명')}${f('market', '주요 시장')}</div>
      <div class="field-grid">${f('base_company', '기준 기업', '실제 데이터(진열대 · 리뷰)를 모으는 기업')}${f('founded', '설립')}</div>
      ${field('브랜드 소개', area('intro', b.intro, 3))}
      <div class="field-grid">${f('slogan', '슬로건')}${f('category', '식품 분류')}</div>
      ${field('브랜드 컬러', inp('colors', b.colors, 'maxlength="500"'), 'HEX 코드(예: #D7FF3F)를 적으면 아래 견본에 보여요.')}
      <div class="set-swatches" data-ref="swatches"></div>
      <div class="field-grid">${f('character', '캐릭터')}${f('cm_song', 'CM송')}</div>`;
    drawSwatches();
    const meta = $(el, '[data-ref="brand-meta"]');
    if (meta) meta.textContent = b.updatedAt ? `최종 수정 ${fmt.datetime(b.updatedAt)} · ${b.updatedBy || ''}` : '';
    setDirty('brand', false);
  }

  function parseColors(text) {
    const out = [];
    const re = /([A-Za-z][A-Za-z\- ]{0,30}?)?\s*#([0-9a-fA-F]{6})\b/g;
    let m;
    while ((m = re.exec(String(text || ''))) && out.length < 8) out.push({ name: (m[1] || '').trim(), hex: '#' + m[2].toUpperCase() });
    return out;
  }

  function drawSwatches() {
    const el = panel('brand');
    const box = $(el, '[data-ref="swatches"]');
    const input = $(el, '[name="colors"]');
    if (!box || !input) return;
    const list = parseColors(input.value);
    box.innerHTML = list.length
      ? list.map(c => `<div class="set-swatch"><i data-hex="${c.hex}" aria-hidden="true"></i><span>${esc(c.name || '색상')}<small>${c.hex}</small></span></div>`).join('')
      : '<span class="small muted">HEX 코드가 없어 견본을 그리지 않았어요.</span>';
    $$(box, 'i[data-hex]').forEach(i => { if (/^#[0-9A-F]{6}$/.test(i.dataset.hex)) i.style.background = i.dataset.hex; });
  }

  async function saveBrand() {
    const form = $(panel('brand'), '[data-form="brand"]');
    const body = {};
    BRAND_FIELDS.forEach(k => { const f = form.querySelector(`[name="${k}"]`); if (f) body[k] = f.value.trim(); });
    if (!body.name) throw new Error('브랜드명을 입력하세요.');
    const saved = await api('/api/facts/brand', body, { method: 'PUT' });
    FACTS.brand = saved;
    fillBrand();
    toast('브랜드 기본 정보를 저장했어요.');
  }

  function evidenceBadge(p) {
    return p.evidence === '확인 완료' ? badge('근거 확인 완료', 'green') : badge('제품 근거 확인 필요', 'orange');
  }
  function money(v) {
    return v == null || v === '' ? '<span class="muted">미정</span>' : `${esc(fmt.usd(Number(v)))} ${badge('가상값', 'purple')}`;
  }

  function renderProducts() {
    const box = $(panel('brand'), '[data-ref="products"]');
    if (!box) return;
    const list = FACTS.products || [];
    box.innerHTML = list.length ? list.map(p => `
      <div class="setting-row set-product">
        <div class="grow">
          <strong class="set-pname">${esc(p.ko || p.name)}</strong>
          <p>${esc(p.code || p.id)} · 내부 제품명 ${esc(p.name || '—')} · 소비자용 영문명 ${esc(p.consumer_name || '미정')}</p>
          <div class="set-pmeta">
            <span>${esc(p.form || '제형 미정')}</span>
            <span>맛 ${esc(p.flavors || '미정')} ${p.flavor_status === 'confirmed' ? '' : badge('임시', 'blue')}</span>
            <span>가격 ${money(p.price)}</span>
            <span>원가 ${money(p.cost)}</span>
            <span class="muted">${(p.sources || []).length ? `출처 ${(p.sources || []).length}개` : '출처 없음'}${p.updatedAt ? ` · 최종 수정 ${esc(fmt.date(p.updatedAt))}` : ''}</span>
          </div>
        </div>
        <div class="set-row-actions">${evidenceBadge(p)}<button type="button" class="btn small" data-act="product" data-id="${esc(p.id)}">${icon('edit')}상세 보기/수정</button></div>
      </div>`).join('') : ui.empty('등록된 제품이 없어요.', 'doc');
  }

  function sourceRow(s) {
    s = s || {};
    const known = SRC_FIELDS.some(([k]) => k === s.field);
    return `<div class="set-src-row">
      <select class="input" data-k="field" aria-label="출처 항목">${SRC_FIELDS.map(([k, l]) => `<option value="${k}"${k === s.field ? ' selected' : ''}>${l}</option>`).join('')}${!known && s.field ? `<option value="${esc(s.field)}" selected>${esc(s.field)}</option>` : ''}</select>
      <input class="input" data-k="url" value="${esc(val(s.url))}" placeholder="https://… (기사 · 공식 페이지)" maxlength="500" aria-label="출처 주소">
      <input class="input" data-k="note" value="${esc(val(s.note))}" placeholder="메모 (기사 제목 · 날짜)" maxlength="200" aria-label="출처 메모">
      <button type="button" class="btn small ghost" data-src="del" aria-label="출처 지우기">${icon('trash')}</button>
    </div>`;
  }

  function openProduct(id) {
    const p = (FACTS.products || []).find(x => x.id === id);
    if (!p) return;
    const body = `
      <div class="part-settings set-modal">
        <div class="flex wrap mb-16">${badge(p.code || p.id, 'dark')}${badge('내부 제품명 · ' + (p.name || '—'))}${evidenceBadge(p)}${p.flavor_status === 'confirmed' ? badge('맛 확정', 'green') : badge('맛 임시', 'blue')}</div>
        <div class="note error mb-16" data-ref="err" hidden></div>
        <div class="field-grid">${field('제품 분류', inp('category', p.category))}${field('제형', inp('form', p.form))}</div>
        <div class="field-grid">${field('맛', inp('flavors', p.flavors), '기사 · 공식 정보 기준')}${field('맛 상태', `<select class="input" name="flavor_status"><option value="provisional"${p.flavor_status !== 'confirmed' ? ' selected' : ''}>임시</option><option value="confirmed"${p.flavor_status === 'confirmed' ? ' selected' : ''}>확정</option></select>`)}</div>
        <div class="field-grid">${field('포장', inp('pack', p.pack))}${field('용량', inp('volume', p.volume))}</div>
        ${field('개발 방향', inp('direction', p.direction))}
        ${field('성분', area('ingredients', p.ingredients, 2), '모르면 비워 두세요 (출처 필수)')}
        ${field('알레르기 성분', inp('allergens', p.allergens))}
        ${field('섭취법', area('usage', p.usage, 2))}
        <div class="field-grid">${field('소비자용 영문명', inp('consumer_name', p.consumer_name), '미정이면 "미정"')}${field('근거 상태', `<select class="input" name="evidence"><option${p.evidence !== '확인 완료' ? ' selected' : ''}>확인 필요</option><option${p.evidence === '확인 완료' ? ' selected' : ''}>확인 완료</option></select>`)}</div>
        <div class="field-grid">
          ${field('가격 (USD)', inp('price', p.price, 'type="number" min="0" step="0.01" inputmode="decimal"'), `${badge('가상값', 'purple')} 실제 판매가가 아니에요`)}
          ${field('원가 (USD)', inp('cost', p.cost, 'type="number" min="0" step="0.01" inputmode="decimal"'), `${badge('가상값', 'purple')} 발표용 가정`)}
        </div>
        <div class="set-fld">
          <span class="field-label">출처 (항목별)</span>
          <div class="set-sources" data-ref="sources">${(p.sources || []).map(sourceRow).join('')}</div>
          <button type="button" class="btn small mt-8" data-src="add">${icon('plus')}출처 추가</button>
        </div>
        ${field('메모', area('notes', p.notes, 2))}
        <p class="panel-note">${p.updatedAt ? `최종 수정 ${esc(fmt.datetime(p.updatedAt))} · ${esc(p.updatedBy || '')}` : ''}</p>
      </div>`;
    formModal({
      title: `${p.ko || p.name} · 상세 보기/수정`, body, wide: true,
      onOpen: el => {
        el.addEventListener('click', e => {
          const b = e.target.closest('[data-src]');
          if (!b) return;
          const box = $(el, '[data-ref="sources"]');
          if (b.dataset.src === 'add') {
            box.insertAdjacentHTML('beforeend', sourceRow({ field: 'ingredients' }));
            const urls = $$(box, '[data-k="url"]');
            if (urls.length) urls[urls.length - 1].focus();
          } else if (b.dataset.src === 'del') {
            const row = b.closest('.set-src-row');
            if (row) row.remove();
          }
        });
      },
      onSave: el => saveProduct(p, el)
    });
  }

  async function saveProduct(p, el) {
    const body = {};
    PRODUCT_EDIT.forEach(k => { const f = el.querySelector(`[name="${k}"]`); if (f) body[k] = f.value.trim(); });
    for (const k of ['price', 'cost']) {
      if (body[k] !== '' && !(Number(body[k]) >= 0)) throw new Error('가격 · 원가는 0 이상의 숫자(달러)로 입력하세요.');
    }
    const sources = $$(el, '.set-src-row').map(r => ({
      field: $(r, '[data-k="field"]').value, url: $(r, '[data-k="url"]').value.trim(), note: $(r, '[data-k="note"]').value.trim()
    })).filter(s => s.url || s.note);
    const bad = sources.find(s => s.url && !/^https?:\/\//i.test(s.url));
    if (bad) throw new Error('출처 주소는 http:// 또는 https://로 시작해야 해요.');
    body.sources = sources;
    const saved = await api('/api/facts/' + encodeURIComponent(p.id), body, { method: 'PUT' });
    const i = (FACTS.products || []).findIndex(x => x.id === p.id);
    if (i >= 0) FACTS.products[i] = saved;
    renderProducts();
    toast(`${saved.ko || saved.name} 정보를 저장했어요.`);
  }

  /* ═════════ 2. 수집 설정 ═════════ */
  function renderCollect(el) {
    el.innerHTML = `
      <div class="workbench">
        <div class="stack" data-ref="collect-main"><div class="skeleton set-skel"></div></div>
        <aside class="stack" data-ref="collect-side"></aside>
      </div>
      <section class="card mt-24" data-ref="seasons"><div class="skeleton set-skel"></div></section>`;
    loadCollect();
    loadSeasons();
  }

  async function loadCollect() {
    const main = $(panel('collect'), '[data-ref="collect-main"]');
    try {
      const d = await api('/api/settings/collect');
      S.collect = copy(d.config);
      S.collectDefaults = d.defaults;
      S.collectMeta = { updatedAt: d.updatedAt, updatedBy: d.updatedBy };
      drawCollect();
      setDirty('collect', false);
    } catch (e) {
      main.innerHTML = `<div class="note error">수집 설정을 불러오지 못했어요: ${esc(e.message)}</div>`;
    }
  }

  function listEditor(path) {
    const d = LISTS[path];
    return `<div class="set-list" data-list="${path}">
      <div class="set-chiprow" data-chips="${path}">${chipsHtml(path)}</div>
      <div class="set-addrow">
        <input class="input" data-add="${path}" placeholder="${esc(d.ph)}" maxlength="${d.len || 100}" aria-label="${esc(d.label)} 추가">
        <button type="button" class="btn small" data-act="list-add" data-list="${path}">${icon('plus')}추가</button>
      </div>
    </div>`;
  }

  function chipsHtml(path) {
    const items = getPath(S.collect, path) || [];
    if (!items.length) return '<span class="small muted">비어 있어요</span>';
    return items.map((v, i) => `<span class="set-chip${LISTS[path].url ? ' url' : ''}"><span>${esc(v)}</span>` +
      `<button type="button" data-act="list-del" data-list="${path}" data-i="${i}" aria-label="${esc(v)} 지우기">${icon('x')}</button></span>`).join('');
  }

  function drawCollect() {
    const el = panel('collect');
    const c = S.collect;
    const num = (k, label, attrs, hint) => field(label, `<input class="input" type="number" data-num="${k}" value="${esc(val(c[k]))}" ${attrs}>`, hint);
    $(el, '[data-ref="collect-main"]').innerHTML = `
      <section class="card">
        <div class="card-head"><h2>잠재 고객 (유튜브)</h2>${badge('월 1회 · 월간 분석', 'blue')}</div>
        <div class="set-form">
          ${group(LISTS['search_sets.A'].label, listEditor('search_sets.A'))}
          ${group(LISTS['search_sets.B'].label, listEditor('search_sets.B'))}
          ${group(LISTS['search_sets.K'].label, listEditor('search_sets.K'))}
          <div class="field-grid three">
            ${num('min_views', '영상 최소 조회수', 'min="0" step="1000"', '이보다 조회수가 낮은 영상은 제외')}
            ${num('videos_per_query', '검색어당 영상 수', 'min="1" max="50"', '검색 결과에서 살펴볼 영상 수')}
            <div class="set-fld"><span class="field-label">영상 언어</span>
              <div class="seg set-seg" role="group" aria-label="영상 언어">
                <button type="button" data-act="lang" data-v="en" aria-pressed="${c.video_language === 'en'}">영어</button>
                <button type="button" data-act="lang" data-v="ko_en" aria-pressed="${c.video_language === 'ko_en'}">한국어 · 영어</button>
              </div>
            </div>
          </div>
        </div>
      </section>
      <section class="card">
        <div class="card-head"><h2>진열대 · 구매자 리뷰</h2>${badge('주 1회 · 데이터 업데이트', 'green')}</div>
        <div class="set-form">
          ${group(LISTS.amazon_queries.label, listEditor('amazon_queries'), '검색마다 광고를 뺀 상위 8개 상품을 모아요.')}
          ${num('amazon_product_lookups', '상세 조회 상품 수 (리뷰 많은 순)', 'min="0" max="10"', '상품마다 SerpApi 1회 · 재고 · 대표 리뷰를 가져와요.')}
          <div class="set-fld">
            <span class="field-label">추적 ASIN</span>
            <div class="table-wrap"><table class="data-table set-asins"><thead><tr><th>ASIN</th><th>메모</th><th>자사 (알디콤)</th><th></th></tr></thead>
              <tbody data-ref="asins"></tbody></table></div>
            <button type="button" class="btn small mt-8" data-act="asin-add">${icon('plus')}ASIN 추가</button>
            <small class="hint">검색에 안 나와도 꼭 따라볼 아마존 상품 번호 (영문 대문자 · 숫자 10자리, 예: B0ABCDEFGH). 알디콤은 아마존 검색에 나오지 않아요 — ASIN을 알면 넣고 '자사'를 켜세요.</small>
          </div>
          ${group(LISTS.own_terms.label, listEditor('own_terms'), '상품명에 이 단어가 있으면 자사(알디콤)로 표시해요.')}
          ${group(LISTS.weee_urls.label, listEditor('weee_urls'), 'Firecrawl로 읽을 Weee! 목록 · 상품 페이지 (주소마다 약 1 크레딧). 알디콤 상품 페이지를 따로 넣으면 재고를 더 정확히 봐요.')}
          ${group(LISTS.exclude_terms.label, listEditor('exclude_terms'), '상품명에 이 단어가 있으면 진열대에서 빼요 (예: 해장국 같은 음식).')}
        </div>
      </section>
      <section class="card">
        <div class="card-head"><h2>검색 수요</h2>${badge('Google Trends · 미국 · 12개월', 'blue')}</div>
        ${group(LISTS.trend_keywords.label, listEditor('trend_keywords'), '키워드마다 따로 조회해요 (같이 조회하면 큰 키워드가 작은 키워드를 납작하게 만들어요). 키워드마다 SerpApi 1회.')}
      </section>`;
    drawAsins();
    drawCollectSide();
  }

  function drawCollectSide() {
    const el = panel('collect');
    const m = S.collectMeta || {};
    $(el, '[data-ref="collect-side"]').innerHTML = `
      <section class="card tinted">
        <h3>다음 데이터 업데이트부터 반영</h3>
        <p class="panel-note mt-8">수집 설정을 바꾸면 <b>다음 데이터 업데이트부터</b> 반영돼요. 이미 모은 데이터는 그대로예요. 오른쪽 위 <b>변경 사항 적용</b>을 눌러야 저장돼요.</p>
        <p class="small muted mt-8">${m.updatedAt ? `마지막 저장 ${esc(fmt.datetime(m.updatedAt))} · ${esc(m.updatedBy || '')}` : '아직 기본값을 쓰고 있어요.'}</p>
        <button type="button" class="btn small mt-16" data-act="collect-reset">${icon('refresh')}기본값으로 되돌리기</button>
      </section>
      <section class="card">
        <h3>예상 사용량</h3>
        <div data-ref="estimate" class="mt-8"></div>
        <p class="panel-note mt-8">한 번 돌릴 때 대략이에요. 남은 사용량은 연결 탭의 연결 테스트로 확인하세요.</p>
      </section>
      <section class="card">
        <h3>어디에 쓰이나요</h3>
        <div class="mt-8">
          ${ui.row('검색어 세트 · 영상 기준', '잠재 고객 (유튜브)')}
          ${ui.row('Amazon 검색어 · 추적 ASIN', '진열대 · 구매자 리뷰')}
          ${ui.row('Weee! 주소', '진열대')}
          ${ui.row('트렌드 키워드', '홈 › 콘텐츠 캘린더 (다음 급등 예상)')}
          ${ui.row('모니터링 시즌', '주간 리뷰 · 다음 급등 예상')}
        </div>
      </section>`;
    drawEstimate();
  }

  function drawEstimate() {
    const box = $(panel('collect'), '[data-ref="estimate"]');
    if (!box || !S.collect) return;
    const c = S.collect;
    const n = v => Math.max(0, Number(v) || 0);
    const queries = ['A', 'B', 'K'].reduce((t, k) => t + ((c.search_sets || {})[k] || []).length, 0);
    const serp = (c.amazon_queries || []).length + n(c.amazon_product_lookups) + (c.tracked_asins || []).filter(a => a.asin).length +
      (c.trend_keywords || []).length;
    const yt = queries * 100 + queries + queries * n(c.videos_per_query);
    box.innerHTML = `
      ${ui.row('SerpApi (주간)', `약 ${fmt.num(serp)}회 <span class="muted">/ 월 250회</span>`)}
      ${ui.row('Firecrawl (주간)', `약 ${fmt.num((c.weee_urls || []).length)} 크레딧 <span class="muted">/ 월 1,000</span>`)}
      ${ui.row('YouTube (월간)', `최대 약 ${fmt.num(yt)} units <span class="muted">/ 하루 10,000</span>`)}`;
  }

  function addToList(path) {
    const d = LISTS[path];
    const input = $(panel('collect'), `[data-add="${path}"]`);
    if (!d || !input) return;
    const v = input.value.replace(/\s+/g, ' ').trim();
    if (!v) { input.focus(); return; }
    const items = getPath(S.collect, path) || [];
    if (d.url) {
      let ok = false;
      try { const u = new URL(v); ok = u.protocol === 'https:' && /(^|\.)sayweee\.com$/i.test(u.hostname); } catch (e) { ok = false; }
      if (!ok) { toast('Weee! 주소는 https://www.sayweee.com/… 형식만 넣을 수 있어요.', 'error'); return; }
    }
    if (items.some(x => String(x).toLowerCase() === v.toLowerCase())) { toast('이미 있어요.', 'error'); return; }
    if (items.length >= d.max) { toast(`${d.label}은(는) ${d.max}개까지 넣을 수 있어요.`, 'error'); return; }
    setPath(S.collect, path, items.concat([v]));
    input.value = '';
    redrawList(path);
    input.focus();
  }

  function removeFromList(path, i) {
    const items = (getPath(S.collect, path) || []).slice();
    items.splice(i, 1);
    setPath(S.collect, path, items);
    redrawList(path);
  }

  function redrawList(path) {
    const box = $(panel('collect'), `[data-chips="${path}"]`);
    if (box) box.innerHTML = chipsHtml(path);
    setDirty('collect', true);
    drawEstimate();
  }

  function setLang(v) {
    S.collect.video_language = v;
    $$(panel('collect'), '[data-act="lang"]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === v)));
    setDirty('collect', true);
  }

  function drawAsins() {
    const body = $(panel('collect'), '[data-ref="asins"]');
    if (!body) return;
    const list = S.collect.tracked_asins || [];
    body.innerHTML = list.length ? list.map((a, i) => `<tr>
      <td><input class="input mono set-asin${a.asin && !ASIN_RE.test(a.asin) ? ' set-invalid' : ''}" data-asin="${i}" data-k="asin" value="${esc(a.asin)}" maxlength="10" placeholder="B0ABCDEFGH" aria-label="ASIN"></td>
      <td><input class="input" data-asin="${i}" data-k="label" value="${esc(val(a.label))}" maxlength="60" placeholder="예) 알디콤 젤리" aria-label="메모"></td>
      <td><label class="check"><input type="checkbox" data-asin="${i}" data-k="own"${a.own ? ' checked' : ''}>자사</label></td>
      <td><button type="button" class="btn small ghost" data-act="asin-del" data-i="${i}" aria-label="ASIN 지우기">${icon('trash')}</button></td>
    </tr>`).join('') : `<tr><td colspan="4" class="muted small">추적하는 ASIN이 없어요.</td></tr>`;
  }

  function addAsin() {
    if ((S.collect.tracked_asins || []).length >= 30) { toast('추적 ASIN은 30개까지 넣을 수 있어요.', 'error'); return; }
    S.collect.tracked_asins = (S.collect.tracked_asins || []).concat([{ asin: '', label: '', own: false }]);
    drawAsins();
    const inputs = $$(panel('collect'), '[data-k="asin"]');
    if (inputs.length) inputs[inputs.length - 1].focus();
    setDirty('collect', true);
  }

  function removeAsin(i) {
    S.collect.tracked_asins.splice(i, 1);
    drawAsins();
    setDirty('collect', true);
    drawEstimate();
  }

  async function resetCollect() {
    if (!S.collectDefaults) return;
    const ok = await ui.confirm('수집 설정을 기본값으로 되돌릴까요? 변경 사항 적용을 눌러야 저장돼요.', { ok: '되돌리기' });
    if (!ok) return;
    S.collect = copy(S.collectDefaults);
    drawCollect();
    setDirty('collect', true);
  }

  async function saveCollect() {
    const c = copy(S.collect);
    c.tracked_asins = (c.tracked_asins || []).filter(a => a.asin || a.label);
    const bad = c.tracked_asins.find(a => !ASIN_RE.test(a.asin || ''));
    if (bad) throw new Error(`ASIN '${bad.asin || '(빈칸)'}'이(가) 올바르지 않아요 (영문 대문자 · 숫자 10자리).`);
    const d = await api('/api/settings/collect', { config: c }, { method: 'PUT' });
    S.collect = copy(d.config);
    S.collectMeta = { updatedAt: d.updatedAt, updatedBy: d.updatedBy };
    drawCollect();
    setDirty('collect', false);
    toast(d.changed && d.changed.length ? '수집 설정을 저장했어요. 다음 데이터 업데이트부터 반영돼요.' : '바뀐 항목이 없어요.');
  }

  /* 모니터링 시즌 (공용 /api/seasons) */
  async function loadSeasons() {
    try {
      const d = await api('/api/seasons?all=1');
      S.seasons = d.items || [];
    } catch (e) {
      toast('시즌을 불러오지 못했어요: ' + e.message, 'error');
    }
    drawSeasons();
  }

  function drawSeasons() {
    const box = $(panel('collect'), '[data-ref="seasons"]');
    if (!box) return;
    const list = S.seasons.filter(s => S.showPast || !s.past);
    const watching = S.seasons.filter(s => s.monitor && !s.past).length;
    box.innerHTML = `
      <div class="card-head"><h2>모니터링 시즌 <span class="muted small">모니터링 ${watching}개</span></h2>
        <div class="flex wrap">
          <label class="check"><input type="checkbox" data-act="season-past"${S.showPast ? ' checked' : ''}>지난 시즌도 보기</label>
          <a class="btn small" href="/api/seasons/export.csv" download="reboot-settings-seasons.csv">${icon('download')}CSV 받기</a>
          <button type="button" class="btn small" data-act="season-import">${icon('upload')}CSV 가져오기</button>
          <button type="button" class="btn small lime" data-act="season-add">${icon('plus')}시즌 추가</button>
        </div>
      </div>
      <p class="panel-note mb-16">모니터링을 켠 시즌만 인사이트 주간 리뷰와 홈 › 콘텐츠 캘린더의 다음 급등 예상에 나와요. 시즌은 여기에서만 고쳐요 (홈 · 콘텐츠 캘린더에도 반영돼요).</p>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>시즌</th><th>기간</th><th class="num">D-day</th><th>구분</th><th>우선순위</th><th class="num">준비 기간</th><th>모니터링</th><th></th></tr></thead>
        <tbody>${list.length ? list.map(seasonRow).join('') : `<tr><td colspan="8">${ui.empty('시즌이 없어요. 시즌 추가 또는 CSV 가져오기를 누르세요.', 'calendar')}</td></tr>`}</tbody></table></div>`;
  }

  function seasonRow(s) {
    const range = s.start === s.end ? fmt.date(s.start) : `${fmt.date(s.start)} ~ ${fmt.date(s.end)}`;
    const dd = s.past ? badge('지남') : s.ongoing ? badge('진행 중', 'lime') : s.inLead ? badge(fmt.dday(s.start), 'orange') : esc(fmt.dday(s.start));
    return `<tr>
      <td><strong>${esc(s.name)}</strong>${s.notes ? `<small class="set-sub">${esc(s.notes)}</small>` : ''}</td>
      <td class="nowrap">${esc(range)}</td>
      <td class="num">${dd}</td>
      <td>${esc(SEASON_CAT[s.category] || s.category)}</td>
      <td>${esc(SEASON_PRI[s.priority] || s.priority)}</td>
      <td class="num">${esc(s.leadDays)}일 전</td>
      <td><button type="button" class="switch${s.monitor ? '' : ' off'}" role="switch" aria-checked="${s.monitor ? 'true' : 'false'}" aria-label="${esc(s.name)} 모니터링" data-act="season-monitor" data-id="${esc(s.id)}"></button></td>
      <td class="nowrap"><button type="button" class="btn small ghost" data-act="season-edit" data-id="${esc(s.id)}">${icon('edit')}수정</button><button type="button" class="btn small ghost" data-act="season-del" data-id="${esc(s.id)}">${icon('trash')}삭제</button></td>
    </tr>`;
  }

  async function toggleMonitor(btn, s) {
    if (!s) return;
    btn.disabled = true;
    try {
      const saved = await api(`/api/seasons/${s.id}`, { monitor: !s.monitor }, { method: 'PUT' });
      Object.assign(s, saved);
      drawSeasons();
      toast(`${s.name} 모니터링을 ${s.monitor ? '켰어요' : '껐어요'}.`);
    } catch (e) {
      btn.disabled = false;
      toast(e.message, 'error');
    }
  }

  function openSeason(s) {
    const isNew = !s;
    s = s || { name: '', start: REBOOT.today(), end: '', category: 'holiday', priority: 2, leadDays: 30, notes: '', monitor: true };
    const opt = (map, cur) => Object.keys(map).map(k => `<option value="${k}"${String(k) === String(cur) ? ' selected' : ''}>${esc(map[k])}</option>`).join('');
    const body = `<div class="part-settings set-modal">
      <div class="note error mb-16" data-ref="err" hidden></div>
      ${field('시즌 이름', inp('name', s.name, 'maxlength="100" placeholder="예) Halloween"'))}
      <div class="field-grid">${field('시작일', inp('start', s.start, 'type="date"'))}${field('종료일', inp('end', s.end === s.start ? '' : s.end, 'type="date"'), '하루짜리면 비워 두세요')}</div>
      <div class="field-grid three">
        ${field('구분', `<select class="input" name="category">${opt(SEASON_CAT, s.category)}</select>`)}
        ${field('우선순위', `<select class="input" name="priority">${opt(SEASON_PRI, s.priority)}</select>`)}
        ${field('준비 기간 (일 전)', inp('leadDays', s.leadDays, 'type="number" min="0" max="365"'))}
      </div>
      ${field('메모', area('notes', s.notes, 2))}
      <label class="check mt-16"><input type="checkbox" name="monitor"${s.monitor ? ' checked' : ''}>모니터링 (인사이트 주간 리뷰 · 홈 다음 급등 예상)</label>
    </div>`;
    formModal({
      title: isNew ? '시즌 추가' : `시즌 수정 · ${s.name}`, body,
      onSave: async el => {
        const g = n => el.querySelector(`[name="${n}"]`);
        const data = {
          name: g('name').value.trim(), start: g('start').value, end: g('end').value || g('start').value,
          category: g('category').value, priority: +g('priority').value, leadDays: +g('leadDays').value,
          notes: g('notes').value.trim(), monitor: g('monitor').checked
        };
        if (!data.name) throw new Error('시즌 이름을 입력하세요.');
        if (!data.start) throw new Error('시작일을 고르세요.');
        const saved = isNew ? await api('/api/seasons', data) : await api(`/api/seasons/${s.id}`, data, { method: 'PUT' });
        if (isNew) S.seasons.push(saved); else Object.assign(s, saved);
        S.seasons.sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : a.priority - b.priority));
        drawSeasons();
        toast(isNew ? `${saved.name} 시즌을 추가했어요.` : '시즌을 저장했어요.');
      }
    });
  }

  async function deleteSeason(s) {
    if (!s) return;
    const ok = await ui.confirm(`${s.name} (${s.start}) 시즌을 지울까요? 홈 · 콘텐츠 캘린더에서도 사라져요.`, { ok: '지우기', danger: true });
    if (!ok) return;
    try {
      await api(`/api/seasons/${s.id}`, undefined, { method: 'DELETE' });
      S.seasons = S.seasons.filter(x => x.id !== s.id);
      drawSeasons();
      toast('시즌을 지웠어요.');
    } catch (e) { toast(e.message, 'error'); }
  }

  function importSeasons() {
    const body = `<div class="part-settings set-modal">
      <p>첫 줄은 <code>name,start_date,end_date,category,priority,lead_days,notes</code> 이어야 해요. 'CSV 받기'로 받은 파일을 고쳐서 올리면 편해요.</p>
      <div class="note error mb-16" data-ref="err" hidden></div>
      ${field('CSV 파일', '<input type="file" accept=".csv,text/csv" data-ref="file">')}
      ${field('또는 CSV 내용 붙여 넣기', '<textarea class="input mono" rows="7" data-ref="csv" spellcheck="false"></textarea>')}
      <div class="set-fld"><span class="field-label">가져오는 방법</span>
        <label class="check"><input type="radio" name="mode" value="merge" checked>합치기 (지금 시즌에 더하기)</label>
        <label class="check set-ml"><input type="radio" name="mode" value="replace">전체 바꾸기 (지금 시즌을 모두 지우고 바꾸기)</label>
      </div>
    </div>`;
    formModal({
      title: '시즌 CSV 가져오기', body, saveLabel: '가져오기',
      onOpen: el => {
        $(el, '[data-ref="file"]').addEventListener('change', async e => {
          const f = e.target.files && e.target.files[0];
          if (!f) return;
          if (f.size > 500 * 1024) { showErr($(el, '[data-ref="err"]'), 'CSV 파일이 너무 커요 (500KB 이하).'); return; }
          $(el, '[data-ref="csv"]').value = (await f.text()).replace(/^﻿/, '');
        });
      },
      onSave: async el => {
        const csv = $(el, '[data-ref="csv"]').value.trim();
        if (!csv) throw new Error('CSV 파일을 고르거나 내용을 붙여 넣으세요.');
        const mode = (el.querySelector('[name="mode"]:checked') || {}).value || 'merge';
        if (mode === 'replace' && !(await ui.confirm('지금 시즌을 모두 지우고 CSV로 바꿀까요?', { ok: '전체 바꾸기', danger: true }))) return false;
        const d = await api('/api/seasons/import', { csv, mode });
        S.seasons = d.items || [];
        drawSeasons();
        toast(`시즌 ${d.count}개를 가져왔어요.`);
      }
    });
  }

  /* ═════════ 3. 연결 ═════════ */
  function renderConnections(el) {
    el.innerHTML = `
      <div class="workbench">
        <div class="stack">
          <section class="card" data-ref="keys"><div class="skeleton set-skel"></div></section>
          <section class="card" data-ref="gmail"><div class="skeleton set-skel"></div></section>
          <section class="card" data-ref="values"></section>
        </div>
        <aside class="stack">
          <section class="card tinted">
            <h3>알아두세요</h3>
            <ul class="set-rules mt-8">
              <li>저장한 키는 다시 볼 수 없고 <b>끝 4자리만</b> 보여요 (<span class="mono">••••abcd</span>). 바꿀 때는 새로 입력해요.</li>
              <li>키는 서버에 <b>암호화</b>해서 저장해요. 브라우저에는 저장하지 않아요.</li>
              <li>화면에서 입력한 키가 <b>.env보다 우선</b>이에요. 화면에서 지우면 .env 값으로 돌아가요.</li>
              <li>발표 자료 · 스크린샷에 키가 보이지 않게 해요.</li>
            </ul>
          </section>
          <section class="card" data-ref="setup"></section>
        </aside>
      </div>`;
    loadConnections();
    loadGmail();
  }

  async function loadConnections() {
    try {
      const d = await api('/api/settings/connections');
      S.conns = d.items || [];
      S.isAdmin = !!d.isAdmin;
      S.setup = d.setup || {};
    } catch (e) {
      $(panel('connections'), '[data-ref="keys"]').innerHTML = `<div class="note error">연결 목록을 불러오지 못했어요: ${esc(e.message)}</div>`;
      return;
    }
    drawConnections();
  }

  function statusCell(it) {
    const label = it.status === '확인 전' ? '연결됨 · 테스트 전' : it.status;
    const last = it.lastCheck;
    const src = it.source === 'screen' ? '화면 입력' : it.source === 'env' ? '.env' : '';
    return `${badge(label, CONN_TONE[it.status])}${src ? ` <span class="small muted">${esc(src)}</span>` : ''}` +
      (last ? `<small class="set-sub">${esc(last.message)} · ${esc(fmt.relative(last.at))}</small>` : '');
  }

  function drawConnections() {
    const el = panel('connections');
    const keys = S.conns.filter(i => i.group === 'key');
    const values = S.conns.filter(i => i.group !== 'key');
    const admin = S.isAdmin;
    const actions = it => {
      const test = it.testable
        ? `<button type="button" class="btn small" data-act="conn-test" data-name="${esc(it.name)}"${it.connected ? '' : ' disabled title="키를 먼저 넣으세요"'}>${icon('refresh')}연결 테스트</button>`
        : '<span class="small muted">테스트 미지원</span>';
      const key = admin ? `<button type="button" class="btn small${it.connected ? '' : ' lime'}" data-act="conn-key" data-name="${esc(it.name)}">${icon('edit')}${it.group === 'key' ? '키 입력' : '값 입력'}</button>` : '';
      return `<div class="set-row-actions">${test}${key}</div>`;
    };
    const connected = keys.filter(i => i.connected).length;
    $(el, '[data-ref="keys"]').innerHTML = `
      <div class="card-head"><h2>API 키 <span class="muted small">${connected}/${keys.length} 연결</span></h2>
        <div class="flex">${admin ? badge('관리자 · 키 입력 가능', 'lime') : badge('보기 전용')}<button type="button" class="btn small ghost" data-act="conn-reload" aria-label="새로고침">${icon('refresh')}</button></div></div>
      ${admin ? '' : '<div class="note mb-16">키 입력은 관리자만 할 수 있어요. 키가 필요하면 관리자에게 요청하세요. 연결 테스트는 누구나 할 수 있어요.</div>'}
      <div class="table-wrap"><table class="data-table set-conn"><thead><tr><th>서비스</th><th>쓰는 곳</th><th>무료 한도</th><th>상태</th><th>값</th><th></th></tr></thead>
        <tbody>${keys.map(it => `<tr>
          <td><strong>${esc(it.label)}</strong><small class="set-sub mono">${esc(it.name)}</small></td>
          <td class="set-used">${esc(it.usedBy)}</td>
          <td class="nowrap">${esc(it.limit || '—')}</td>
          <td>${statusCell(it)}</td>
          <td class="mono nowrap">${esc(it.masked || '—')}</td>
          <td>${actions(it)}</td></tr>`).join('')}</tbody></table></div>`;
    $(el, '[data-ref="values"]').innerHTML = `
      <div class="card-head"><h2>설정값</h2>${badge('키가 아닌 값 · 그대로 보임')}</div>
      <div class="table-wrap"><table class="data-table set-conn"><thead><tr><th>항목</th><th>쓰는 곳</th><th>지금 값</th><th></th></tr></thead>
        <tbody>${values.map(it => `<tr>
          <td><strong>${esc(it.label)}</strong><small class="set-sub mono">${esc(it.name)}</small></td>
          <td class="set-used">${esc(it.usedBy)}</td>
          <td>${it.masked ? `<span class="set-value">${esc(it.masked)}</span>` : `<span class="muted">${it.default ? `기본값 · ${esc(it.default)}` : '없음'}</span>`}${it.source ? ` <span class="small muted">${it.source === 'screen' ? '화면 입력' : '.env'}</span>` : ''}</td>
          <td>${admin ? `<button type="button" class="btn small" data-act="conn-key" data-name="${esc(it.name)}">${icon('edit')}값 입력</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
    drawSetup();
  }

  function drawSetup() {
    const box = $(panel('connections'), '[data-ref="setup"]');
    if (!box) return;
    const st = S.setup || {};
    const ok = on => (on ? badge('설정됨', 'green') : badge('없음', 'orange'));
    box.innerHTML = `
      <h3>처음 한 번만 (개발 담당)</h3>
      <p class="panel-note mt-8">배포할 때 서버 환경변수(.env · Render Environment)에 넣어야 화면 입력과 Google 로그인이 작동해요.</p>
      <div class="mt-8">
        ${ui.row('APP_SECRET_KEY', `${ok(st.appSecretKey)}`)}
        ${ui.row('GOOGLE_CLIENT_ID · SECRET', `${ok(st.googleClient)}`)}
      </div>
      <pre class="set-code">APP_SECRET_KEY=...        # 키 암호화용 (바꾸면 화면에서 넣은 키를 다시 입력)
GOOGLE_CLIENT_ID=...      # Gmail(Google) 로그인용
GOOGLE_CLIENT_SECRET=...</pre>
      <p class="small mt-8">Google Cloud 콘솔 › OAuth 클라이언트(웹 애플리케이션)의 <b>승인된 리디렉션 URI</b>:</p>
      <div class="set-copy"><code>${esc(st.redirectUri || '')}</code><button type="button" class="btn small" data-act="copy" data-text="${esc(st.redirectUri || '')}">복사</button></div>
      ${st.appSecretKey ? '' : '<p class="panel-note mt-8">APP_SECRET_KEY가 없으면 SECRET_KEY로 암호화해요. 어느 쪽이든 바꾸면 화면에서 넣은 키를 다시 입력해야 해요.</p>'}`;
  }

  async function copyText(text) {
    try { await global.navigator.clipboard.writeText(text || ''); toast('복사했어요.'); } catch (e) { toast('복사하지 못했어요. 직접 선택해 복사하세요.', 'error'); }
  }

  function openKey(name) {
    const it = (S.conns || []).find(i => i.name === name);
    if (!it) return;
    const isKey = it.group === 'key' || it.secret;
    const now = it.masked ? `${it.masked} (${it.source === 'screen' ? '화면 입력' : '.env'})` : '없음';
    const control = !isKey && name === 'PRODUCT_DESC'
      ? `<textarea class="input" rows="4" data-ref="val" maxlength="4000">${esc(it.masked || '')}</textarea>`
      : `<input class="input mono" data-ref="val" type="${it.secret ? 'password' : 'text'}" autocomplete="new-password" spellcheck="false" maxlength="4000" value="${it.secret ? '' : esc(it.masked || '')}" placeholder="${it.secret ? '여기에 붙여 넣기' : ''}">`;
    const body = `<div class="part-settings set-modal">
      <p>${esc(it.label)} · ${esc(it.usedBy)}</p>
      <div class="note error mb-16" data-ref="err" hidden></div>
      ${field(isKey ? '새 키' : '값', control, `지금 값: <span class="mono">${esc(now)}</span>${it.secret ? ' · 저장한 키는 다시 볼 수 없어요.' : ''}`)}
      <div class="note mt-16">비워 두고 저장하면 화면 입력 값을 지우고 .env 값(있으면)으로 돌아가요.${name === 'GMAIL_APP_PASSWORD' ? ' 앱 비밀번호는 띄어 써도 돼요.' : ''}</div>
    </div>`;
    const extra = it.source === 'screen' ? [{
      label: '화면 입력 지우기', tone: 'danger', onClick: bodyEl => { saveKey(it, '', bodyEl); return false; }
    }] : [];
    formModal({ title: `${it.label} · ${isKey ? '키 입력' : '값 입력'}`, body, extra, onSave: el => saveKey(it, $(el, '[data-ref="val"]').value, el, true) });
  }

  async function saveKey(it, value, el, viaSave) {
    try {
      const saved = await api(`/api/settings/connections/${encodeURIComponent(it.name)}`, { value: value.trim() }, { method: 'PUT' });
      const i = S.conns.findIndex(x => x.name === it.name);
      if (i >= 0) S.conns[i] = saved;
      drawConnections();
      watchHealth(true);
      if (!viaSave) closeModal(el);
      toast(saved.connected ? `${it.label} 값을 저장했어요.` : `${it.label} 화면 입력을 지웠어요.`);
      if (saved.connected && saved.testable) {
        const btn = $(panel('connections'), `[data-act="conn-test"][data-name="${it.name}"]`);
        testConn(btn, it.name);
      }
    } catch (e) {
      if (viaSave) throw e;
      showErr($(el, '[data-ref="err"]'), e.message);
    }
  }

  async function testConn(btn, name) {
    if (btn) ui.busy(btn, true);
    try {
      const r = await api(`/api/settings/connections/${encodeURIComponent(name)}/test`, {});
      const pair = ['GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD'];
      const names = pair.includes(name) ? pair : [name];
      (S.conns || []).forEach(it => {
        if (!names.includes(it.name) || !it.connected) return;
        it.status = r.status;
        it.lastCheck = { status: r.status, message: r.message, detail: r.detail, at: r.at };
      });
      drawConnections();
      toast(`${r.status} · ${r.message}`, r.status === '오류' ? 'error' : undefined);
    } catch (e) {
      if (btn) ui.busy(btn, false);
      toast(e.message, 'error');
    }
  }

  /* Google 로그인 (CS Gmail 문의함) — /api/cs/gmail/* 는 CS 파트 서버 */
  async function loadGmail() {
    try { S.gmail = await api('/api/cs/gmail/status'); } catch (e) { S.gmail = { connected: false, error: e.message }; }
    drawGmail();
  }

  function drawGmail() {
    const box = $(panel('connections'), '[data-ref="gmail"]');
    if (!box) return;
    const g = S.gmail || {};
    const action = g.connected
      ? `<button type="button" class="btn danger" data-act="gmail-off">${icon('x')}연결 해제</button>`
      : `<button type="button" class="btn lime" data-act="gmail-login"${g.webLogin ? '' : ' disabled'}>${icon('mail')}Google로 로그인</button>`;
    box.innerHTML = `
      <div class="card-head"><h2>Google 로그인 <span class="muted small">CS Gmail 문의함 · 답장 초안</span></h2>${g.connected ? badge('정상', 'green') : badge('미연결')}</div>
      <div class="setting-row">
        <div><strong class="set-pname">${g.connected ? esc(g.email || '연결된 계정') : '연결된 Google 계정이 없어요'}</strong>
          <p>${g.connected ? 'CS 문의함이 이 계정의 메일을 읽고 답장 초안을 만들어요. 발송은 사람이 Gmail에서 직접 해요.' : 'Google로 로그인을 누르고 문의 메일을 받을 계정을 고르면 연결돼요.'}</p></div>
        ${action}
      </div>
      ${g.error ? `<div class="note warn mt-8">${esc(g.error)}</div>` : ''}
      ${!g.connected && !g.webLogin ? '<div class="note warn mt-8">GOOGLE_CLIENT_ID · GOOGLE_CLIENT_SECRET이 있어야 Google 로그인을 쓸 수 있어요 (위 표에서 입력하거나 .env).</div>' : ''}
      <p class="panel-note mt-8">Google 앱 검수 전이라 OAuth 동의 화면에 테스트 사용자로 등록한 계정만 로그인할 수 있어요. 테스트 모드면 7일마다 다시 로그인해요. 인플루언서 · 파트너 메일 발송은 위 표의 Gmail 주소 · 앱 비밀번호(SMTP)를 써요.</p>`;
  }

  async function gmailDisconnect(btn) {
    const ok = await ui.confirm('Google 계정 연결을 해제할까요? CS 문의함에서 Gmail을 읽을 수 없게 돼요.', { ok: '연결 해제', danger: true });
    if (!ok) return;
    ui.busy(btn, true);
    try {
      await api('/api/cs/gmail/disconnect', {});
      toast('Google 계정 연결을 해제했어요.');
      await loadGmail();
    } catch (e) {
      ui.busy(btn, false);
      toast(e.message, 'error');
    }
  }

  function gmailParam() {
    try { return new URL(global.location.href).searchParams.get('gmail') || ''; } catch (e) { return ''; }
  }
  function gmailToast(v) {
    const msg = {
      connected: ['Google 계정을 연결했어요. CS 문의함에서 Gmail을 읽을 수 있어요.', undefined],
      failed: ['Google 로그인에 실패했어요. 잠시 뒤 다시 시도하세요.', 'error'],
      denied: ['Google 로그인을 취소했어요.', 'error']
    }[v];
    if (msg) setTimeout(() => toast(msg[0], msg[1]), 300);
  }

  /* ═════════ 4. 작업 로그 ═════════ */
  function renderWorklog(el) {
    const parts = REBOOT.meta() || [];
    el.innerHTML = `
      <section class="card">
        <div class="filters">
          <div class="flex wrap">
            <div class="chips" role="group" aria-label="구분" data-ref="wl-kinds"></div>
            <select class="input set-select" data-ref="wl-part" aria-label="파트">
              <option value="">모든 파트</option>${parts.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('')}
            </select>
          </div>
          <div class="flex wrap">
            <button type="button" class="btn small" data-act="wl-csv">${icon('download')}CSV 받기</button>
            <button type="button" class="btn small" data-act="wl-xlsx">${icon('download')}Excel 받기 (구분별 시트)</button>
          </div>
        </div>
        <div class="table-wrap"><table class="data-table set-wl"><thead><tr><th>일시</th><th>구분</th><th>작업</th><th>상태</th><th>승인</th><th>상세</th></tr></thead>
          <tbody data-ref="wl-body"></tbody></table></div>
        <div class="set-more" data-ref="wl-more"></div>
        <p class="panel-note mt-16">대시보드에서 일어난 일을 자동으로 기록해요 (과제 제출용). 로그는 고치거나 지울 수 없고, API 키 · 이메일 주소는 가려서 저장돼요.</p>
      </section>`;
    drawWlFilters();
    loadWorklog(true);
  }

  function drawWlFilters() {
    const box = $(panel('worklog'), '[data-ref="wl-kinds"]');
    if (!box) return;
    box.innerHTML = [['', '전체']].concat(KINDS.map(k => [k, k])).map(([v, l]) =>
      `<button type="button" class="chip${v === S.wl.kind ? ' selected' : ''}" data-act="wl-kind" data-v="${esc(v)}" aria-pressed="${v === S.wl.kind}">${esc(l)}</button>`).join('');
  }

  function wlQuery() {
    const qs = new URLSearchParams();
    if (S.wl.kind) qs.set('kind', S.wl.kind);
    if (S.wl.part) qs.set('part', S.wl.part);
    return qs;
  }

  async function loadWorklog(reset) {
    if (!reset && S.wl.loading) return;
    const seq = ++S.wl.seq;   // 필터를 빨리 바꿔도 마지막 요청만 반영
    S.wl.loading = true;
    if (reset) { S.wl.items = []; S.wl.done = false; drawWorklog(true); }
    const qs = wlQuery();
    qs.set('limit', '50');
    if (!reset && S.wl.items.length) qs.set('before', S.wl.items[S.wl.items.length - 1].id);
    try {
      const d = await api('/api/worklog?' + qs.toString());
      if (seq !== S.wl.seq) return;
      const items = d.items || [];
      S.wl.items = S.wl.items.concat(items);
      S.wl.done = items.length < 50;
    } catch (e) {
      if (seq !== S.wl.seq) return;
      toast('작업 로그를 불러오지 못했어요: ' + e.message, 'error');
      S.wl.done = true;
    }
    S.wl.loading = false;
    drawWorklog(false);
  }

  function drawWorklog(loading) {
    const el = panel('worklog');
    const body = $(el, '[data-ref="wl-body"]');
    const more = $(el, '[data-ref="wl-more"]');
    if (!body) return;
    if (loading) { body.innerHTML = '<tr><td colspan="6"><div class="skeleton set-skel"></div></td></tr>'; more.innerHTML = ''; return; }
    body.innerHTML = S.wl.items.length ? S.wl.items.map(r => `<tr>
      <td class="nowrap">${esc(fmt.datetime(r.at))}<small class="set-sub">${esc(r.user || '')}</small></td>
      <td>${badge(r.kind, KIND_TONE[r.kind])}</td>
      <td>${esc(r.action)}<small class="set-sub">${esc(partLabel(r.part))}</small></td>
      <td>${badge(r.status, STATUS_TONE[r.status])}</td>
      <td>${esc(r.approval || '–')}</td>
      <td><button type="button" class="btn small ghost" data-act="wl-open" data-id="${esc(r.id)}">${r.kind === 'AI 프롬프트' ? '프롬프트 보기' : '보기'}</button></td>
    </tr>`).join('') : `<tr><td colspan="6">${ui.empty('조건에 맞는 기록이 없어요.', 'clock')}</td></tr>`;
    more.innerHTML = S.wl.done ? (S.wl.items.length ? `<span class="small muted">모두 ${fmt.num(S.wl.items.length)}건을 불러왔어요.</span>` : '')
      : `<button type="button" class="btn small" data-act="wl-more">더 보기</button>`;
  }

  const DETAIL_LABEL = { count: '건수', fields: '바꾼 항목', message: '메시지', name: '항목', source: '출처', model: '모델',
    tokens: '사용량 (토큰)', items: '항목', results: '결과', calls: '호출 수', error: '오류', week: '주차', month: '월' };

  function detailValue(v) {
    if (v == null || v === '') return '<span class="muted">—</span>';
    if (Array.isArray(v) && v.every(x => typeof x !== 'object' || x === null)) return esc(v.join(', '));
    if (typeof v === 'object') return `<pre class="set-pre">${esc(JSON.stringify(v, null, 2))}</pre>`;
    return esc(String(v));
  }

  function openLog(item) {
    if (!item) return;
    const d = item.detail || {};
    let body;
    if (item.kind === 'AI 프롬프트') {
      const t = d.tokens || {};
      body = `<div class="part-settings set-modal">
        ${ui.row('탭 · 기능', esc(`${partLabel(item.part)} · ${item.action}`))}
        ${ui.row('모델', esc(d.model || '—'))}
        ${ui.row('사용량 (토큰)', esc(`입력 ${fmt.num(t.prompt)} · 출력 ${fmt.num(t.completion)} · 합계 ${fmt.num(t.total)}`))}
        ${ui.row('일시 · 실행', esc(`${fmt.datetime(item.at)} · ${item.user || ''}`))}
        <h3 class="set-h3">보낸 프롬프트 (입력)</h3><pre class="set-pre">${esc(d.prompt || '—')}</pre>
        <h3 class="set-h3">응답 요약</h3><pre class="set-pre">${esc(d.response || '—')}</pre>
      </div>`;
    } else {
      const keys = Object.keys(d);
      body = `<div class="part-settings set-modal">
        ${ui.row('파트', esc(partLabel(item.part)))}
        ${ui.row('구분 · 상태', `${badge(item.kind, KIND_TONE[item.kind])} ${badge(item.status, STATUS_TONE[item.status])}`)}
        ${ui.row('승인', esc(item.approval || '–'))}
        ${ui.row('일시 · 실행', esc(`${fmt.datetime(item.at)} · ${item.user || ''}`))}
        ${keys.length ? keys.map(k => `<div class="set-kv"><span>${esc(DETAIL_LABEL[k] || k)}</span><div>${detailValue(d[k])}</div></div>`).join('') : '<p class="muted mt-16">상세 기록이 없어요.</p>'}
      </div>`;
    }
    ui.modal({ title: item.action, body, wide: item.kind === 'AI 프롬프트' });
  }

  async function worklogCsv(btn) {
    ui.busy(btn, true);
    try {
      let all = [];
      let before = '';
      for (let i = 0; i < 10; i++) {
        const qs = wlQuery();
        qs.set('limit', '500');
        if (before) qs.set('before', before);
        const d = await api('/api/worklog?' + qs.toString());
        const items = d.items || [];
        all = all.concat(items);
        if (items.length < 500) break;
        before = items[items.length - 1].id;
      }
      const safe = v => { const s = v == null ? '' : String(v); return /^[=+\-@]/.test(s) ? "'" + s : s; };
      const rows = [['일시', '구분', '파트', '작업', '상태', '승인', '작성자', '상세']].concat(all.map(r =>
        [stamp(r.at), r.kind, partLabel(r.part), r.action, r.status, r.approval, r.user, JSON.stringify(r.detail || {})].map(safe)));
      ui.download(`reboot-settings-worklog-${REBOOT.today()}.csv`, ui.csv(rows), 'text/csv;charset=utf-8');
      toast(`작업 로그 ${fmt.num(all.length)}건을 CSV로 받았어요.`);
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      ui.busy(btn, false);
    }
  }

  function worklogXlsx() {
    const qs = wlQuery().toString();
    const a = global.document.createElement('a');
    a.href = '/api/settings/worklog.xlsx' + (qs ? '?' + qs : '');
    a.download = `reboot-settings-worklog-${REBOOT.today()}.xlsx`;
    global.document.body.appendChild(a);
    a.click();
    a.remove();
  }

  /* ═════════ 5. 계정 ═════════ */
  function renderAccount(el) {
    const me = REBOOT.user();
    el.innerHTML = `
      <div class="grid-2">
        <section class="card">
          <div class="card-head"><h2>내 정보</h2>${me.isAdmin ? badge('관리자', 'lime') : badge('팀원')}</div>
          <form class="set-form" data-form="account" autocomplete="off">
            ${field('이메일 (로그인 아이디)', `<input class="input" value="${esc(me.email || '')}" readonly>`)}
            <div class="field-grid">${field('이름', inp('name', me.name, 'maxlength="100"'))}${field('소속 · 역할', inp('title', me.title, 'maxlength="100" placeholder="예) 글로벌사업팀 · 미국 채널"'))}</div>
          </form>
          <p class="panel-note mt-16">고친 뒤 오른쪽 위 <b>변경 사항 적용</b>을 누르세요. 상단 프로필에는 새로고침 후 보여요.</p>
        </section>
        <section class="card">
          <div class="card-head"><h2>비밀번호 변경</h2></div>
          <div class="set-form" data-ref="pw">
            ${field('현재 비밀번호', '<input class="input" type="password" name="current" autocomplete="current-password">')}
            <div class="field-grid">
              ${field('새 비밀번호', '<input class="input" type="password" name="new" autocomplete="new-password" minlength="8" maxlength="128">', '8자 이상')}
              ${field('새 비밀번호 확인', '<input class="input" type="password" name="confirm" autocomplete="new-password" maxlength="128">')}
            </div>
            <div class="note error" data-ref="pw-err" hidden></div>
            <button type="button" class="btn dark" data-act="pw-change">${icon('check')}비밀번호 바꾸기</button>
          </div>
        </section>
      </div>
      <section class="card mt-24" data-ref="users"></section>`;
    if (S.isAdmin) loadUsers();
    else $(el, '[data-ref="users"]').innerHTML = `<div class="card-head"><h2>계정 관리</h2>${badge('관리자 전용')}</div><p class="muted">팀원 계정 추가 · 잠금 해제 · 비밀번호 재설정은 관리자만 할 수 있어요. 필요하면 관리자에게 요청하세요.</p>`;
  }

  async function saveMe() {
    const form = $(panel('account'), '[data-form="account"]');
    const nameEl = $(form, '[name="name"]');
    const titleEl = $(form, '[name="title"]');
    const name = nameEl.value.trim();
    if (!name) throw new Error('이름을 입력하세요.');
    const u = await api('/api/me', { name, title: titleEl.value.trim() }, { method: 'PATCH' });
    nameEl.value = u.name || '';
    titleEl.value = u.title || '';
    setDirty('account', false);
    toast('내 정보를 저장했어요. 상단 프로필에는 새로고침하면 보여요.');
  }

  async function changePassword(btn) {
    const box = $(panel('account'), '[data-ref="pw"]');
    const err = $(box, '[data-ref="pw-err"]');
    const g = n => box.querySelector(`[name="${n}"]`);
    showErr(err, '');
    if (!g('current').value) return showErr(err, '현재 비밀번호를 입력하세요.');
    if (g('new').value.length < 8) return showErr(err, '새 비밀번호는 8자 이상이에요.');
    if (g('new').value !== g('confirm').value) return showErr(err, '새 비밀번호가 서로 달라요.');
    ui.busy(btn, true);
    try {
      await api('/api/me/password', { current: g('current').value, new: g('new').value });
      ['current', 'new', 'confirm'].forEach(n => { g(n).value = ''; });
      toast('비밀번호를 바꿨어요. 다른 기기에서는 다시 로그인해야 해요.');
    } catch (e) {
      showErr(err, e.message);
    } finally {
      ui.busy(btn, false);
    }
  }

  async function loadUsers() {
    const box = $(panel('account'), '[data-ref="users"]');
    if (!box) return;
    try {
      const d = await api('/api/users');
      S.users = d.items || [];
    } catch (e) {
      box.innerHTML = `<div class="note error">계정 목록을 불러오지 못했어요: ${esc(e.message)}</div>`;
      return;
    }
    drawUsers();
  }

  function drawUsers() {
    const box = $(panel('account'), '[data-ref="users"]');
    const me = REBOOT.user();
    const list = S.users || [];
    const sw = (u, flag, on, label, disabled) => `<button type="button" class="switch${on ? '' : ' off'}" role="switch" aria-checked="${on ? 'true' : 'false'}" aria-label="${esc(u.name)} ${label}" data-act="user-flag" data-id="${esc(u.id)}" data-flag="${flag}"${disabled ? ' disabled title="내 계정은 여기서 끌 수 없어요"' : ''}></button>`;
    box.innerHTML = `
      <div class="card-head"><h2>계정 관리 <span class="muted small">${list.length}명</span></h2>
        <div class="flex"><button type="button" class="btn small ghost" data-act="users-reload" aria-label="새로고침">${icon('refresh')}</button><button type="button" class="btn small lime" data-act="user-add">${icon('plus')}계정 추가</button></div></div>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>이름 · 이메일</th><th>소속</th><th>관리자</th><th>사용</th><th>마지막 로그인</th><th></th></tr></thead>
        <tbody>${list.map(u => `<tr>
          <td><strong>${esc(u.name)}</strong>${u.id === me.id ? ` ${badge('나', 'lime')}` : ''}${u.locked ? ` ${badge('잠김', 'red')}` : ''}<small class="set-sub">${esc(u.email)}</small></td>
          <td>${esc(u.title || '—')}</td>
          <td>${sw(u, 'isAdmin', u.isAdmin, '관리자', false)}</td>
          <td>${sw(u, 'isActive', u.isActive, '사용', u.id === me.id)}${u.isActive ? '' : ` <span class="small muted">꺼짐</span>`}</td>
          <td class="nowrap">${u.lastLoginAt ? esc(fmt.relative(u.lastLoginAt)) : '<span class="muted">없음</span>'}</td>
          <td class="nowrap">${u.locked ? `<button type="button" class="btn small" data-act="user-unlock" data-id="${u.id}">잠금 해제</button>` : ''}<button type="button" class="btn small ghost" data-act="user-pw" data-id="${u.id}">비밀번호 재설정</button></td>
        </tr>`).join('')}</tbody></table></div>
      <p class="panel-note mt-16">계정을 끄면 그 사람의 로그인이 바로 풀려요. 로그인 5번 실패하면 10분 잠겨요 — 잠금 해제로 바로 풀 수 있어요. 마지막 관리자는 끌 수 없어요.</p>`;
  }

  async function patchUser(u, body, msg) {
    if (!u) return;
    try {
      const saved = await api(`/api/users/${u.id}`, body, { method: 'PATCH' });
      Object.assign(u, saved);
      drawUsers();
      if (msg) toast(msg);
    } catch (e) { toast(e.message, 'error'); drawUsers(); }
  }

  async function toggleUser(btn) {
    const u = (S.users || []).find(x => String(x.id) === btn.dataset.id);
    if (!u) return;
    const flag = btn.dataset.flag;
    const next = !u[flag];
    if (flag === 'isActive' && !next && !(await ui.confirm(`${u.name} 계정을 끌까요? 바로 로그아웃되고 다시 로그인할 수 없어요.`, { ok: '끄기', danger: true }))) return;
    if (flag === 'isAdmin' && !next && u.id === REBOOT.user().id && !(await ui.confirm('내 관리자 권한을 끌까요? 키 입력 · 계정 관리를 더 할 수 없어요.', { ok: '끄기', danger: true }))) return;
    btn.disabled = true;
    patchUser(u, { [flag]: next }, flag === 'isAdmin' ? (next ? `${u.name}님을 관리자로 바꿨어요.` : `${u.name}님의 관리자 권한을 껐어요.`)
      : (next ? `${u.name} 계정을 켰어요.` : `${u.name} 계정을 껐어요.`));
  }

  function resetUserPassword(u) {
    if (!u) return;
    formModal({
      title: `비밀번호 재설정 · ${u.name}`,
      body: `<div class="part-settings set-modal"><p>새 비밀번호를 정해 본인에게 따로 알려 주세요. 잠김도 함께 풀려요.</p>
        <div class="note error mb-16" data-ref="err" hidden></div>
        ${field('새 비밀번호', '<input class="input" type="password" name="pw" autocomplete="new-password" minlength="8" maxlength="128">', '8자 이상')}</div>`,
      onSave: async el => {
        const pw = el.querySelector('[name="pw"]').value;
        if (pw.length < 8) throw new Error('비밀번호는 8자 이상이에요.');
        const saved = await api(`/api/users/${u.id}`, { password: pw }, { method: 'PATCH' });
        Object.assign(u, saved);
        drawUsers();
        toast(`${u.name}님의 비밀번호를 바꿨어요.`);
      }
    });
  }

  function addUser() {
    formModal({
      title: '계정 추가',
      body: `<div class="part-settings set-modal"><p>팀원 계정을 만들어요. 임시 비밀번호는 본인에게 따로 알려 주고, 로그인 후 계정 탭에서 바꾸게 하세요.</p>
        <div class="note error mb-16" data-ref="err" hidden></div>
        ${field('이메일', '<input class="input" type="email" name="email" maxlength="255" autocomplete="off">')}
        <div class="field-grid">${field('이름', '<input class="input" name="name" maxlength="100">')}${field('소속 · 역할', '<input class="input" name="title" maxlength="100">')}</div>
        ${field('임시 비밀번호', '<input class="input" type="password" name="password" autocomplete="new-password" minlength="8" maxlength="128">', '8자 이상')}
        <label class="check mt-16"><input type="checkbox" name="isAdmin">관리자 (키 입력 · 계정 관리 가능)</label></div>`,
      saveLabel: '만들기',
      onSave: async el => {
        const g = n => el.querySelector(`[name="${n}"]`);
        const body = { email: g('email').value.trim(), name: g('name').value.trim(), title: g('title').value.trim(),
          password: g('password').value, isAdmin: g('isAdmin').checked };
        if (!body.email) throw new Error('이메일을 입력하세요.');
        if (body.password.length < 8) throw new Error('임시 비밀번호는 8자 이상이에요.');
        const u = await api('/api/users', body);
        (S.users = S.users || []).push(u);
        drawUsers();
        toast(`${u.name} 계정을 만들었어요.`);
      }
    });
  }

  REBOOT.register({
    id: 'settings', label: '설정 및 브랜드 관리', order: 90,
    render,
    // 키는 관리자만 넣을 수 있어서 관리자에게만 숫자를 보여 줌
    summary: () => (missingKeys == null || !REBOOT.user().isAdmin ? null : { label: '연결 안 된 핵심 서비스', value: missingKeys })
  });
})(typeof window !== 'undefined' ? window : globalThis);
