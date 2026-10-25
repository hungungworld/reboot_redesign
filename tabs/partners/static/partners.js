/* 인플루언서 · 파트너 (대표 파일) — 메뉴 등록 · 탭 틀만 둡니다. 기능 코드는 넣지 않습니다 (팀 README 4장).
   🔎 업체 컨택 · ✉️ 협업 메일  → outreach.js  REBOOT.use('partners.outreach').render(el, 'contacts' | 'mail')
   🗂️ 협업 이력 · 📈 판매 실적  → records.js   REBOOT.use('partners.records').render(el, 'history' | 'sales')
   다른 파일은 탭을 바꿀 때 .part-partners 요소에 CustomEvent('ptn:go', {detail:{tab}})를 보냅니다.
   저장 키: partners.tab.u<사용자 id> (마지막으로 본 탭) */
(function (global) {
  'use strict';

  const REBOOT = global.REBOOT;
  const doc = global.document;
  const { esc, icon } = REBOOT.ui;
  const store = REBOOT.store('partners');
  const KEY_TAB = `partners.tab.u${(REBOOT.user() || {}).id || 0}`;
  const TABS = [
    { id: 'contacts', label: '🔎 업체 컨택', file: 'partners.outreach', view: 'contacts' },
    { id: 'mail', label: '✉️ 협업 메일', file: 'partners.outreach', view: 'mail' },
    { id: 'history', label: '🗂️ 협업 이력', file: 'partners.records', view: 'history' },
    { id: 'sales', label: '📈 판매 실적', file: 'partners.records', view: 'sales' }
  ];
  const tabOf = id => TABS.find(t => t.id === id);

  function render(root) {
    root.classList.add('part-partners');
    root.innerHTML = `
      <div class="tabs ptn-tabs" role="tablist" aria-label="인플루언서 · 파트너">
        ${TABS.map(t => `<button type="button" role="tab" id="ptn-tab-${t.id}" data-tab="${t.id}" aria-selected="false" aria-controls="ptn-panel" tabindex="-1">${esc(t.label)}</button>`).join('')}
      </div>
      <div class="ptn-panel" id="ptn-panel" role="tabpanel"></div>`;

    let current = '';
    function show(id, opts) {
      const t = tabOf(id) || TABS[0];
      current = t.id;
      root.querySelectorAll('[data-tab]').forEach(b => {
        const on = b.dataset.tab === t.id;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', String(on));
        b.tabIndex = on ? 0 : -1;
      });
      // 이전 탭 화면의 이벤트가 남지 않게 새 요소로 바꿈
      const old = root.querySelector('.ptn-panel');
      const panel = old.cloneNode(false);
      panel.setAttribute('aria-labelledby', `ptn-tab-${t.id}`);
      old.replaceWith(panel);
      if (store.get(KEY_TAB, '') !== t.id) store.set(KEY_TAB, t.id);
      if (global.history && global.history.replaceState && global.location.hash !== '#' + t.id) {
        global.history.replaceState(null, '', '#' + t.id);
      }
      if (opts && opts.scroll) global.scrollTo({ top: 0, behavior: 'smooth' });   // 메뉴를 바꾸면 맨 위로 (README 3장)
      try {
        REBOOT.use(t.file).render(panel, t.view);
      } catch (e) {
        panel.innerHTML = `<p class="part-error">${esc(t.label)} 화면을 그리지 못했습니다.\n${esc(e && e.message)}</p>`;
      }
    }

    const tablist = root.querySelector('.ptn-tabs');
    tablist.addEventListener('click', e => {
      const b = e.target.closest('[data-tab]');
      if (b) show(b.dataset.tab);
    });
    tablist.addEventListener('keydown', e => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      const i = TABS.findIndex(t => t.id === current);
      const next = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length;
      show(TABS[next].id);
      root.querySelector(`[data-tab="${TABS[next].id}"]`).focus();
    });
    root.addEventListener('ptn:go', e => show(e.detail && e.detail.tab, { scroll: true }));
    global.addEventListener('hashchange', () => {
      const id = global.location.hash.slice(1);
      if (tabOf(id) && id !== current) show(id);
    });

    // 제목 오른쪽 버튼: 업체 파일 가져오기 (업체 컨택 탭의 가져오기 카드를 엶)
    const actions = REBOOT.page.actions();
    if (actions) {
      actions.innerHTML = `<button type="button" class="btn lime" data-act="ptn-import">${icon('upload')} 업체 파일 가져오기</button>`;
      actions.addEventListener('click', e => {
        if (!e.target.closest('[data-act="ptn-import"]')) return;
        try { REBOOT.use('partners.outreach').openImport(); } catch (err) { /* outreach.js 없음 */ }
        show('contacts');
      });
    }

    const hash = (global.location.hash || '').slice(1);
    show(tabOf(hash) ? hash : (tabOf(store.get(KEY_TAB, '')) ? store.get(KEY_TAB, '') : 'contacts'));
  }

  REBOOT.register({
    id: 'partners', label: '인플루언서 · 파트너', icon: '🤝', priority: 'P1', order: 60,
    title: '함께 만드는 브랜드의 다음 장.',
    subtitle: '크리에이터와 파트너를 발견하고, 협업의 모든 단계를 연결하세요.',
    render,
    // 메뉴 배지 · 종 알림: 회신을 기다리는 발송 기록 수 (records.js가 저장소에 캐시한 값 — 동기)
    summary: () => {
      let n = 0;
      try { n = REBOOT.use('partners.records').awaitingCount(); } catch (e) { n = 0; }
      return { label: '회신 대기', value: n };
    }
  });
})(typeof window !== 'undefined' ? window : globalThis);
