/* 인플루언서 · 파트너 — 협업 이력(3-4) · 협업 보드 · 크리에이터별 판매 실적(3-5, 🧪 더미)
   대표 파일(partners.js)이 REBOOT.use('partners.records').render(el, 'history' | 'sales')로 부릅니다.
   저장 키(REBOOT.store('partners')): records.columns.u<id> · records.filters.u<id> · records.sales.u<id> (사용자별),
   records.awaiting (팀 공용 — 메뉴 배지 '회신 대기' 숫자 캐시)
   다른 파트 공개: REBOOT.use('partners').creatorSales({start, end}) → Promise {dummy:true, creators, totals} (성과분석) */
(function (global) {
  'use strict';

  const REBOOT = global.REBOOT;
  const P = global.REBOOT_DATA && global.REBOOT_DATA.partners;
  const R = P && P.records;
  if (!REBOOT || !R) throw new Error('partners.records: core.js와 data-records.js를 먼저 불러와야 합니다');

  const doc = global.document;
  const ui = REBOOT.ui;
  const { esc, toast, badge, fmt, icon } = ui;
  const store = REBOOT.store('partners');
  const me = REBOOT.user() || {};
  const myName = me.name || String(me.email || '').split('@')[0] || '';
  const K = {
    columns: `records.columns.u${me.id || 0}`,
    filters: `records.filters.u${me.id || 0}`,
    sales: `records.sales.u${me.id || 0}`,
    widths: `records.widths.u${me.id || 0}`,
    awaiting: 'records.awaiting'
  };
  const PROG = {};
  R.progress.forEach(x => { PROG[x.v] = x; });
  const MAILS = {};
  R.mailStatus.forEach(x => { MAILS[x.v] = x; });
  const KINDS = {};
  R.kinds.forEach(x => { KINDS[x.v] = x; });
  const pad = n => String(n).padStart(2, '0');
  let charts = [];
  let focusId = '';

  /* ───────── 도우미 ───────── */

  const progressBadge = v => (v ? badge(`${PROG[v] ? PROG[v].emoji + ' ' : ''}${v}`, PROG[v] ? PROG[v].tone : '') : '<span class="faint">—</span>');
  const mailBadge = v => (v ? badge(`${MAILS[v] ? MAILS[v].emoji + ' ' : ''}${v}`, MAILS[v] ? MAILS[v].tone : '') : '<span class="faint">—</span>');
  const kindBadge = v => badge(v === '샘플' ? '샘플 · 예시' : v, KINDS[v] ? KINDS[v].tone : '');
  const settingsLink = '<a class="link" href="/dashboard/settings">설정 › 연결</a>';
  function initials(name) {
    const words = String(name || '').replace(/^예시\)\s*/, '').split(/[\s·,&/()\-]+/).filter(w => /[A-Za-z0-9가-힣]/.test(w));
    if (!words.length) return '?';
    if (/^[A-Za-z0-9]/.test(words[0])) return (words[0][0] + (words[1] ? words[1][0] : (words[0][1] || ''))).toUpperCase();
    return words[0].slice(0, 2);
  }
  function avIndex(key) {
    let h = 0;
    for (const ch of String(key)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return h % 5;
  }
  function toLocalInput(iso) {
    const d = iso ? new Date(iso) : new Date();
    if (isNaN(d)) return '';
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function fromLocalInput(v) {
    const d = v ? new Date(v) : null;
    return d && !isNaN(d) ? d.toISOString() : null;
  }
  function replyAddress(from) {
    const m = String(from || '').match(/<([^<>]+)>/);
    const s = (m ? m[1] : String(from || '')).trim();
    return /^[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}$/.test(s) ? s : '';
  }
  function isMail(h) { return /메일|답장/.test(h.activity || '') || !!h.subject || !!h.messageId; }
  function go(tab) {
    const root = doc && doc.querySelector('.part-partners');
    if (root) root.dispatchEvent(new CustomEvent('ptn:go', { detail: { tab } }));
  }
  function closeModal(bodyEl) {
    const wrap = bodyEl && bodyEl.closest('.modal-backdrop');
    const x = wrap && wrap.querySelector('[data-modal-close]');
    if (x) x.click();
  }
  function errorBox(message) {
    return `<section class="card"><p class="note error">${esc(message)}</p><button type="button" class="btn small soft mt-16" data-rec-retry>${icon('refresh')} 다시 불러오기</button></section>`;
  }
  function destroyCharts() {
    charts.forEach(c => { try { c.destroy(); } catch (e) { /* 무시 */ } });
    charts = [];
  }

  /* ───────── 메뉴 배지 '회신 대기' (동기로 읽도록 저장소에 캐시) ───────── */

  function awaitingCount() { return Number(store.get(K.awaiting, 0)) || 0; }
  function setAwaiting(n) {
    n = Number(n) || 0;
    if (n !== awaitingCount()) store.set(K.awaiting, n);
    try { REBOOT.refreshCounts(); } catch (e) { /* 무시 */ }
  }
  function refreshSummary() {
    return REBOOT.api('/api/partners/overview').then(ov => { setAwaiting(ov.awaiting); return ov; }).catch(() => null);
  }
  /** 다른 탭(업체 상세)에서 '이력 모두 보기' → 그 업체로 걸러서 열기 */
  function focus(partnerId) { focusId = String(partnerId || ''); }

  /* ═════════ 3-4 협업 이력 ═════════ */

  function getFilters() {
    const f = store.get(K.filters, null);
    return Object.assign({ partner: '', kind: '', view: 'table' }, f && typeof f === 'object' ? f : {});
  }
  function saveFilters(f) { store.set(K.filters, f); }

  function columnConfig() {
    const saved = store.get(K.columns, null);
    const defs = {};
    R.columns.forEach(c => { defs[c.key] = c; });
    const out = [];
    if (Array.isArray(saved)) {
      saved.forEach(s => { if (s && defs[s.key] && !out.some(o => o.key === s.key)) out.push({ key: s.key, label: defs[s.key].label, on: !!s.on }); });
    }
    R.columns.forEach(c => { if (!out.some(o => o.key === c.key)) out.push({ key: c.key, label: c.label, on: !c.hidden }); });
    return out;
  }

  async function renderHistory(el) {
    el.innerHTML = '<div class="skeleton ptn-skel"></div>';
    let data;
    let status;
    try {
      [data, status] = await Promise.all([REBOOT.api('/api/partners/history'), REBOOT.api('/api/partners/status').catch(() => ({}))]);
    } catch (e) {
      if (!el.isConnected) return;
      el.innerHTML = errorBox(e.message);
      el.querySelector('[data-rec-retry]').addEventListener('click', () => renderHistory(el));
      return;
    }
    if (!el.isConnected) return;
    setAwaiting(data.awaiting);
    const st = { el, items: data.items || [], status: status || {}, f: getFilters(), sel: new Set(), contacts: null };
    if (focusId) {
      Object.assign(st.f, { partner: focusId, kind: '', view: 'table' });
      focusId = '';
      saveFilters(st.f);
    }
    const smtp = !!st.status.smtp;
    el.innerHTML = `
      <div class="filters ptn-rec-bar">
        <div class="flex wrap">
          <label class="ptn-rec-sel"><span class="sr-only">업체</span><select class="input" data-ref="partner" aria-label="업체로 거르기"></select></label>
          <div class="chips" data-ref="kinds" role="group" aria-label="출처"></div>
          <button type="button" class="btn small dark" data-rec-act="check"${smtp ? '' : ' disabled'}>📥 답장 확인 (Gmail)</button>
        </div>
        <div class="flex wrap">
          <div class="seg" role="group" aria-label="보기">
            <button type="button" data-rec-view="table" aria-pressed="${st.f.view !== 'board'}">이력 표</button>
            <button type="button" data-rec-view="board" aria-pressed="${st.f.view === 'board'}">협업 보드</button>
          </div>
          <button type="button" class="btn small ghost" data-rec-act="columns">⚙️ 표시 항목</button>
          <button type="button" class="btn small soft" data-rec-act="add" aria-expanded="false">➕ 수기 기록 추가</button>
          <button type="button" class="btn small danger" data-rec-act="delete" disabled>🗑️ 선택 삭제</button>
        </div>
      </div>
      ${smtp ? '' : `<p class="note mb-16">📥 답장 확인은 Gmail(앱 비밀번호)이 연결되면 쓸 수 있습니다 — ${settingsLink}. 그 전에는 편집 창에서 상태를 직접 바꿀 수 있습니다.</p>`}
      <div data-ref="addbox"></div>
      <div data-ref="body"></div>`;
    wireHistory(st);
    paintFiltersBar(st);
    paintBody(st);
    if (smtp) autoCheckReplies(st);
  }

  function partnerOptions(st) {
    const seen = new Map();
    st.items.forEach(h => {
      const key = h.partnerId || 'name:' + (h.partnerName || '').toLowerCase();
      if (!seen.has(key)) seen.set(key, h.partnerName || h.partnerId || '(이름 없음)');
    });
    return Array.from(seen.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }

  function paintFiltersBar(st) {
    const opts = partnerOptions(st);
    if (st.f.partner && !opts.some(o => o[0] === st.f.partner)) opts.unshift([st.f.partner, st.f.partner + ' (기록 없음)']);
    st.el.querySelector('[data-ref="partner"]').innerHTML = `<option value="">전체 업체 (${opts.length})</option>` +
      opts.map(([k, name]) => `<option value="${esc(k)}"${k === st.f.partner ? ' selected' : ''}>${esc(name)}</option>`).join('');
    st.el.querySelector('[data-ref="kinds"]').innerHTML = [['', '전체']].concat(R.kinds.map(k => [k.v, k.v])).map(([v, label]) =>
      `<button type="button" class="chip" data-rec-kind="${esc(v)}" aria-pressed="${st.f.kind === v}" title="${esc((KINDS[v] || {}).help || '모든 출처')}">${esc(label)}<span class="n">${fmt.num(v ? st.items.filter(h => h.kind === v).length : st.items.length)}</span></button>`).join('');
  }

  function filteredItems(st) {
    const f = st.f;
    return st.items.filter(h => (!f.kind || h.kind === f.kind) &&
      (!f.partner || (h.partnerId || 'name:' + (h.partnerName || '').toLowerCase()) === f.partner));
  }

  /* 협업 이력 표: 같은 업체는 한 줄로 — 줄의 값은 가장 최근 기록, 상태만 바뀜 (기록별 내역은 업체 이름 → 메일 히스토리) */
  function partnerKey(h) { return h.partnerId || 'name:' + (h.partnerName || '').toLowerCase(); }
  function groupItems(list) {
    const map = new Map();
    list.forEach(h => {
      const k = partnerKey(h);
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(h);
    });
    return Array.from(map.values()).map(recs => {
      recs.sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')));
      const latest = recs[0];
      // 최근 기록 이후에 온 회신이 있으면(아직 답하지 않은 회신) 상태(메일)에 🟢 회신 받음을 계속 보여 줌
      const replied = recs.filter(x => x.mailStatus === '회신 받음' && x.reply)
        .sort((a, b) => String(b.reply.at || '').localeCompare(String(a.reply.at || '')))[0];
      const open = replied && replied !== latest && String(replied.reply.at || '') > String(latest.at || '') ? replied : null;
      return { latest, recs, ids: recs.map(x => x.id), open };
    }).sort((a, b) => String(b.latest.at || '').localeCompare(String(a.latest.at || '')));
  }

  function paintBody(st) {
    st.el.querySelectorAll('[data-rec-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.recView === (st.f.view === 'board' ? 'board' : 'table'))));
    const body = st.el.querySelector('[data-ref="body"]');
    const host = doc.createElement('div');
    body.replaceChildren(host);
    if (st.f.view === 'board') {
      board(host, {
        onPick: (id, p) => {
          Object.assign(st.f, { partner: p.key, kind: '', view: 'table' });
          saveFilters(st.f);
          paintFiltersBar(st);
          paintBody(st);
        }
      });
      return;
    }
    const cols = columnConfig().filter(c => c.on);
    const list = filteredItems(st);
    const groups = groupItems(list);
    st.sel.forEach(id => { if (!list.some(h => h.id === id)) st.sel.delete(id); });
    host.innerHTML = `<section class="card flush ptn-rec-tablecard">
      <div class="table-wrap"><table class="data-table ptn-rec-table">
        <thead><tr><th class="ptn-rec-chk"><input type="checkbox" data-ref="all" aria-label="보이는 기록 모두 선택"></th>${cols.map(c => headCell(c)).join('')}<th class="ptn-rec-edit" scope="col">편집</th></tr></thead>
        <tbody>${list.length ? groups.map(g => row(g, cols, st)).join('') : `<tr><td colspan="${cols.length + 2}">${ui.empty('기록이 없습니다. 협업 메일을 보내거나 ➕ 수기 기록 추가로 남기세요.', 'clock')}</td></tr>`}</tbody>
      </table></div>
      <p class="panel-note ptn-rec-foot">업체 ${fmt.num(groups.length)}곳 · 기록 ${fmt.num(list.length)}건 · 같은 업체는 한 줄에 최근 상태로 보이고, 업체 이름을 누르면 주고받은 메일 전체가 열립니다 · 표는 읽기 전용이며 수정은 ✏️ 편집에서 합니다. 🟢 회신 받음을 누르면 회신 창이 열립니다.</p>
    </section>`;
    updateDeleteBtn(st);
    bindResize(host);
  }

  /* 표 칸 너비: 머리글 오른쪽 끝을 좌우로 끌어 조절 · 더블클릭하면 원래대로 (브라우저에 기억) */
  const MIN_COL = 56;
  // 크리에이터별 실적 표 머리글 (키에 sales. 을 붙여 협업 이력 표 너비와 섞이지 않게)
  const SALES_COLS = [
    { key: 'sales.name', label: '크리에이터' }, { key: 'sales.code', label: '할인코드' },
    { key: 'sales.visits', label: '접속량', num: true }, { key: 'sales.orders', label: '주문 수', num: true },
    { key: 'sales.units', label: '판매량', num: true }, { key: 'sales.revenue', label: '매출', num: true },
    { key: 'sales.conversion', label: '전환율', num: true }
  ];
  function savedWidths() {
    const w = store.get(K.widths, null);
    return w && typeof w === 'object' ? w : {};
  }
  function headCell(c, cls) {
    const w = savedWidths()[c.key];
    return `<th${cls ? ` class="${cls}"` : ''} scope="col" data-col="${esc(c.key)}"${w ? ` style="width:${w}px;min-width:${w}px"` : ''}>${esc(c.label)}<span class="ptn-col-grip" data-grip="${esc(c.key)}" title="끌어서 너비 조절 · 더블클릭하면 원래대로" aria-hidden="true"></span></th>`;
  }
  function bindResize(host) {
    host.querySelectorAll('[data-grip]').forEach(g => {
      const th = g.parentElement;
      const key = g.dataset.grip;
      g.addEventListener('pointerdown', e => {
        e.preventDefault();
        g.setPointerCapture(e.pointerId);
        const x0 = e.clientX, w0 = th.getBoundingClientRect().width;
        th.classList.add('resizing');
        const move = ev => {
          const w = Math.max(MIN_COL, Math.round(w0 + ev.clientX - x0));
          th.style.width = th.style.minWidth = w + 'px';
        };
        const up = () => {
          g.removeEventListener('pointermove', move);
          th.classList.remove('resizing');
          const all = savedWidths();
          all[key] = Math.round(th.getBoundingClientRect().width);
          store.set(K.widths, all);
        };
        g.addEventListener('pointermove', move);
        g.addEventListener('pointerup', up, { once: true });
        g.addEventListener('pointercancel', up, { once: true });
      });
      g.addEventListener('dblclick', () => {
        const all = savedWidths();
        delete all[key];
        store.set(K.widths, all);
        th.style.width = th.style.minWidth = '';
      });
      g.addEventListener('click', e => e.stopPropagation());
    });
  }

  function row(g, cols, st) {
    const h = g.latest;
    const on = g.ids.every(id => st.sel.has(id));
    return `<tr data-hid="${h.id}"${on ? ' class="selected"' : ''}>
      <td class="ptn-rec-chk"><input type="checkbox" data-rec-sel="${g.ids.join(',')}"${on ? ' checked' : ''} aria-label="${esc(h.partnerName)} 기록 ${g.ids.length}건 선택"></td>
      ${cols.map(c => cell(h, c.key, g)).join('')}
      <td class="ptn-rec-edit"><button type="button" class="btn small ghost" data-rec-edit="${h.id}" aria-label="${esc(h.partnerName)} 기록 편집">✏️ 편집</button></td>
    </tr>`;
  }

  function cell(h, key, g) {
    const n = g ? g.recs.length : 1;
    if (key === 'mailStatus' && g && g.open) h = g.open;
    switch (key) {
      case 'at': return `<td class="nowrap">${esc(fmt.datetime(h.at))}</td>`;
      case 'partner': return `<td><button type="button" class="ptn-rec-who" data-rec-thread="${h.id}" title="이 업체와 주고받은 메일 전체 보기"><strong>${esc(h.partnerName || '—')}</strong></button>${n > 1 ? `<span class="ptn-rec-count">기록 ${fmt.num(n)}건</span>` : ''}<small class="ptn-rec-sub">${esc(h.partnerType || '구분 없음')}${h.partnerId ? ' · ' + esc(h.partnerId) : ''}</small></td>`;
      case 'kind': return `<td>${kindBadge(h.kind)}</td>`;
      case 'activity': return `<td class="nowrap">${esc(h.activity || '—')}</td>`;
      case 'mailStatus': return `<td class="nowrap">${h.mailStatus === '회신 받음' && h.reply
        ? `<button type="button" class="ptn-rec-replybtn" data-rec-reply="${h.id}" title="회신 창 열기">${mailBadge(h.mailStatus)}</button>` : mailBadge(h.mailStatus)}</td>`;
      case 'progress': return `<td class="nowrap">${progressBadge(h.progress)}</td>`;
      case 'owner': return `<td class="nowrap">${esc(h.owner || '—')}</td>`;
      case 'subject': return `<td class="ptn-rec-clip" title="${esc(h.subject)}">${esc(h.subject || '—')}</td>`;
      case 'memo': return `<td class="ptn-rec-memo">${esc(h.memo || '')}${h.error && !String(h.memo || '').includes(h.error) ? `<span class="ptn-rec-err">${esc(h.error)}</span>` : ''}</td>`;
      case 'to': return `<td>${esc(h.to || '—')}</td>`;
      case 'attachments': return `<td>${(h.attachments || []).map(esc).join('<br>') || '—'}</td>`;
      default: return '<td></td>';
    }
  }

  function updateDeleteBtn(st) {
    const btn = st.el.querySelector('[data-rec-act="delete"]');
    if (btn) {
      btn.disabled = !st.sel.size;
      btn.textContent = st.sel.size ? `🗑️ 선택 삭제 (${st.sel.size})` : '🗑️ 선택 삭제';
    }
    const all = st.el.querySelector('[data-ref="all"]');
    if (all) {
      const visible = filteredItems(st);
      all.checked = visible.length > 0 && visible.every(h => st.sel.has(h.id));
      all.indeterminate = !all.checked && visible.some(h => st.sel.has(h.id));
    }
  }

  async function reload(st) {
    try {
      const data = await REBOOT.api('/api/partners/history');
      st.items = data.items || [];
      setAwaiting(data.awaiting);
    } catch (e) {
      toast(e.message, 'error');
    }
    if (!st.el.isConnected) return;
    paintFiltersBar(st);
    paintBody(st);
  }

  function wireHistory(st) {
    const el = st.el;
    el.addEventListener('click', e => {
      const t = e.target;
      const view = t.closest('[data-rec-view]');
      if (view) {
        st.f.view = view.dataset.recView === 'board' ? 'board' : 'table';
        saveFilters(st.f);
        paintBody(st);
        return;
      }
      const kind = t.closest('[data-rec-kind]');
      if (kind) {
        st.f.kind = kind.dataset.recKind;
        saveFilters(st.f);
        paintFiltersBar(st);
        paintBody(st);
        return;
      }
      const ed = t.closest('[data-rec-edit]');
      if (ed) return editRecord(st, st.items.find(h => h.id === Number(ed.dataset.recEdit)));
      const th = t.closest('[data-rec-thread]');
      if (th) return openThread(st, st.items.find(h => h.id === Number(th.dataset.recThread)));
      const rp = t.closest('[data-rec-reply]');
      if (rp) return replyPopover(st, rp, st.items.find(h => h.id === Number(rp.dataset.recReply)));
      const act = t.closest('[data-rec-act]');
      if (!act || act.disabled) return;
      const a = act.dataset.recAct;
      if (a === 'check') checkReplies(st, act);
      else if (a === 'columns') columnsModal(st);
      else if (a === 'add') toggleAdd(st, act);
      else if (a === 'delete') deleteSelected(st);
      else if (a === 'add-save') saveManual(st, act);
      else if (a === 'add-cancel') toggleAdd(st, el.querySelector('[data-rec-act="add"]'), false);
    });
    el.addEventListener('change', e => {
      const t = e.target;
      if (t.matches('[data-ref="partner"]')) {
        st.f.partner = t.value;
        saveFilters(st.f);
        paintBody(st);
      } else if (t.matches('[data-rec-sel]')) {
        t.dataset.recSel.split(',').map(Number).forEach(id => { if (t.checked) st.sel.add(id); else st.sel.delete(id); });
        const tr = t.closest('tr');
        if (tr) tr.classList.toggle('selected', t.checked);
        updateDeleteBtn(st);
      } else if (t.matches('[data-ref="all"]')) {
        filteredItems(st).forEach(h => { if (t.checked) st.sel.add(h.id); else st.sel.delete(h.id); });
        el.querySelectorAll('[data-rec-sel]').forEach(c => { c.checked = c.dataset.recSel.split(',').every(id => st.sel.has(Number(id))); c.closest('tr').classList.toggle('selected', c.checked); });
        updateDeleteBtn(st);
      } else if (t.matches('[data-add="partner"]')) {
        const name = el.querySelector('[data-add="name"]');
        if (name) { name.hidden = !!t.value; if (!t.value) name.focus(); }
      }
    });
  }

  /* 📥 협업 이력을 열 때 자동 답장 확인 — 5분에 한 번 (조용히: 갱신이 있을 때만 알림) */
  const AUTO_CHECK_MS = 5 * 60 * 1000;
  let lastAutoCheck = 0;
  async function autoCheckReplies(st) {
    if (Date.now() - lastAutoCheck < AUTO_CHECK_MS) return;
    lastAutoCheck = Date.now();
    const btn = st.el.querySelector('[data-rec-act="check"]');
    if (btn) ui.busy(btn, true);
    try {
      const out = await REBOOT.api('/api/partners/replies/check', {});
      if (out.replies || out.bounces) {
        toast(`새 답장 확인 — 회신 ${out.replies}건 · 반송 ${out.bounces}건`);
        if (st.el.isConnected) await reload(st);
      }
    } catch (e) {
      console.warn('[partners] 자동 답장 확인 실패:', e.message);
    } finally {
      if (btn && btn.isConnected) ui.busy(btn, false);
    }
  }

  /* 📥 답장 확인 (Gmail IMAP) */
  async function checkReplies(st, btn) {
    ui.busy(btn, true);
    try {
      const out = await REBOOT.api('/api/partners/replies/check', {});
      toast(out.checked ? `발송 기록 ${out.checked}건 확인 — 회신 ${out.replies}건 · 반송 ${out.bounces}건 갱신` : '확인할 발송 기록(발송 성공 · 회신 대기)이 없습니다.');
      await reload(st);
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      if (btn.isConnected) ui.busy(btn, false);
    }
  }

  /* ✏️ 편집 */
  async function editRecord(st, h) {
    if (!h) return;
    const body = doc.createElement('div');
    body.className = 'part-partners ptn-rec-form';
    body.innerHTML = `
      ${h.kind === '샘플' ? '<p class="note warn">예시(샘플) 기록입니다. 실제 업체 기록이 아닙니다.</p>' : ''}
      <div class="field-grid">
        <label class="field"><span>활동</span><input class="input" data-e="activity" value="${esc(h.activity)}" maxlength="40" list="ptn-rec-acts"></label>
        <label class="field"><span>담당자</span><input class="input" data-e="owner" value="${esc(h.owner)}" maxlength="60"></label>
        <label class="field"><span>상태(메일)</span><select class="input" data-e="mail_status">
          <option value="">— 메일 기록 아님</option>
          ${R.mailStatus.map(m => `<option value="${esc(m.v)}"${m.v === h.mailStatus ? ' selected' : ''}>${m.emoji} ${esc(m.v)}</option>`).join('')}</select>
          <small>🟡 회신 대기로 두면 📥 답장 확인 대상이 됩니다.</small></label>
        <label class="field"><span>진행 상태</span><select class="input" data-e="progress">
          ${R.progress.map(p => `<option value="${esc(p.v)}"${p.v === h.progress ? ' selected' : ''}>${p.emoji} ${esc(p.v)}</option>`).join('')}</select></label>
      </div>
      <label class="field"><span>일시</span><input class="input" type="datetime-local" data-e="at" value="${esc(toLocalInput(h.at))}"></label>
      <label class="field"><span>메모</span><textarea class="input" rows="4" data-e="memo">${esc(h.memo)}</textarea></label>
      ${isMail(h) ? `<label class="field"><span>제목</span><input class="input" data-e="subject" value="${esc(h.subject)}" maxlength="300"></label>
        <label class="field"><span>본문</span><textarea class="input" rows="8" data-e="body">${esc(h.body)}</textarea></label>` : ''}
      <datalist id="ptn-rec-acts">${['메일 발송', '답장 발송'].concat(R.activities).map(a => `<option value="${esc(a)}">`).join('')}</datalist>`;
    let patch = null;
    const v = await ui.modal({
      title: `✏️ 기록 편집 — ${h.partnerName || ''}`, body, wide: true,
      actions: [{ label: '취소', value: false }, {
        label: '저장', value: 'save', tone: 'lime',
        onClick: b => {
          const val = name => { const x = b.querySelector(`[data-e="${name}"]`); return x ? x.value : undefined; };
          patch = {};
          ['activity', 'owner', 'mail_status', 'progress', 'memo', 'subject', 'body'].forEach(k => {
            const now = val(k);
            const old = { activity: h.activity, owner: h.owner, mail_status: h.mailStatus, progress: h.progress, memo: h.memo, subject: h.subject, body: h.body }[k];
            if (now !== undefined && now !== (old || '')) patch[k] = now;
          });
          const at = val('at');
          if (at && at !== toLocalInput(h.at)) patch.at = fromLocalInput(at);
          return true;
        }
      }]
    });
    if (v !== 'save' || !patch) return;
    if (!Object.keys(patch).length) return toast('바뀐 내용이 없습니다.');
    try {
      await REBOOT.api(`/api/partners/history/${h.id}`, patch, { method: 'PATCH' });
      toast('기록을 저장했습니다.');
      reload(st);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  /* ➕ 수기 기록 추가 */
  async function toggleAdd(st, btn, open) {
    const box = st.el.querySelector('[data-ref="addbox"]');
    const show = open === undefined ? !box.innerHTML : open;
    if (btn) btn.setAttribute('aria-expanded', String(show));
    if (!show) { box.innerHTML = ''; return; }
    if (!st.contacts) {
      try { st.contacts = (await REBOOT.api('/api/partners/contacts')).items || []; } catch (e) { st.contacts = []; }
    }
    const pre = st.f.partner && !st.f.partner.startsWith('name:') ? st.f.partner : '';
    box.innerHTML = `<section class="card ptn-rec-add mb-16">
      <div class="card-head"><h2>➕ 수기 기록 추가</h2>${badge('수기', 'green')}</div>
      <div class="field-grid three">
        <label class="field"><span>업체</span><select class="input" data-add="partner">
          <option value="">직접 입력</option>
          ${st.contacts.map(c => `<option value="${esc(c.id)}"${c.id === pre ? ' selected' : ''}>${esc(c.name)} (${esc(c.type)})</option>`).join('')}</select>
          <input class="input mt-8" data-add="name" placeholder="업체 이름" maxlength="200"${pre ? ' hidden' : ''}></label>
        <label class="field"><span>활동 유형</span><select class="input" data-add="activity">${R.activities.map(a => `<option>${esc(a)}</option>`).join('')}</select></label>
        <label class="field"><span>진행 상태</span><select class="input" data-add="progress">${R.progress.map(p => `<option value="${esc(p.v)}">${p.emoji} ${esc(p.v)}</option>`).join('')}</select></label>
        <label class="field"><span>담당자</span><input class="input" data-add="owner" value="${esc(myName)}" maxlength="60"></label>
        <label class="field"><span>일시</span><input class="input" type="datetime-local" data-add="at" value="${esc(toLocalInput())}"></label>
      </div>
      <label class="field"><span>내용</span><textarea class="input" rows="3" data-add="memo" placeholder="예: 담당자와 통화 — 11월 이벤트 샘플링 조건 확인, 다음 주 견적 회신 예정"></textarea></label>
      <div class="flex mt-16"><button type="button" class="btn lime" data-rec-act="add-save">저장</button><button type="button" class="btn ghost" data-rec-act="add-cancel">닫기</button></div>
    </section>`;
  }

  async function saveManual(st, btn) {
    const box = st.el.querySelector('[data-ref="addbox"]');
    const val = k => { const x = box.querySelector(`[data-add="${k}"]`); return x ? x.value : ''; };
    const pid = val('partner');
    const name = pid ? '' : val('name').trim();
    if (!pid && !name) return toast('업체를 고르거나 업체 이름을 입력하세요.', 'error');
    ui.busy(btn, true);
    try {
      await REBOOT.api('/api/partners/history', {
        partner_id: pid, partner_name: name, activity: val('activity'), progress: val('progress'),
        owner: val('owner').trim(), at: fromLocalInput(val('at')), memo: val('memo').trim()
      });
      toast('수기 기록을 추가했습니다.');
      box.innerHTML = '';
      const add = st.el.querySelector('[data-rec-act="add"]');
      if (add) add.setAttribute('aria-expanded', 'false');
      reload(st);
    } catch (e) {
      toast(e.message, 'error');
      ui.busy(btn, false);
    }
  }

  /* 🗑️ 선택 삭제 */
  async function deleteSelected(st) {
    const ids = Array.from(st.sel);
    if (!ids.length) return;
    const firms = new Set(st.items.filter(h => st.sel.has(h.id)).map(partnerKey)).size;
    const ok = await ui.confirm(`선택한 업체 ${firms}곳의 기록 ${ids.length}건을 삭제할까요? 삭제하면 되돌릴 수 없습니다.`, { title: '삭제 확인', ok: '삭제 확인', danger: true });
    if (!ok) return;
    try {
      const out = await REBOOT.api('/api/partners/history/delete', { ids });
      toast(`${out.deleted}건을 삭제했습니다.`);
      st.sel.clear();
      reload(st);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  /* ⚙️ 표시 항목 · 순서 (내 화면에만 적용) */
  async function columnsModal(st) {
    const cols = columnConfig();
    const body = doc.createElement('div');
    body.className = 'part-partners ptn-rec-form';
    const paint = focusSel => {
      body.innerHTML = `<p>보일 항목을 고르고 ↑ ↓로 순서를 바꿉니다. 내 화면에만 적용됩니다.</p>
        <ul class="ptn-rec-cols">${cols.map((c, i) => `<li>
          <label class="check"><input type="checkbox" data-col="${i}"${c.on ? ' checked' : ''}> ${esc(c.label)}</label>
          <span class="flex">
            <button type="button" class="icon-btn ptn-rec-mv" data-mv="${i}" data-dir="-1" aria-label="${esc(c.label)} 위로"${i === 0 ? ' disabled' : ''}>↑</button>
            <button type="button" class="icon-btn ptn-rec-mv" data-mv="${i}" data-dir="1" aria-label="${esc(c.label)} 아래로"${i === cols.length - 1 ? ' disabled' : ''}>↓</button>
          </span></li>`).join('')}</ul>`;
      if (focusSel) { const f = body.querySelector(focusSel); if (f && !f.disabled) f.focus(); }
    };
    paint();
    body.addEventListener('click', e => {
      const b = e.target.closest('[data-mv]');
      if (!b || b.disabled) return;
      const i = Number(b.dataset.mv);
      const dir = Number(b.dataset.dir);
      const j = i + dir;
      if (j < 0 || j >= cols.length) return;
      [cols[i], cols[j]] = [cols[j], cols[i]];
      paint(`[data-mv="${j}"][data-dir="${dir}"]`);
    });
    body.addEventListener('change', e => {
      const c = e.target.closest('[data-col]');
      if (c) cols[Number(c.dataset.col)].on = c.checked;
    });
    const v = await ui.modal({
      title: '⚙️ 표시 항목 · 순서', body,
      actions: [{ label: '↺ 초기화', value: 'reset' }, { label: '취소', value: false }, {
        label: '저장', value: 'save', tone: 'lime',
        onClick: () => {
          if (!cols.some(c => c.on)) { toast('항목을 하나 이상 고르세요.', 'error'); return false; }
          return true;
        }
      }]
    });
    if (v === 'reset') store.remove(K.columns);
    else if (v === 'save') store.set(K.columns, cols.map(c => ({ key: c.key, on: c.on })));
    if (v && st.el.isConnected) paintBody(st);
  }

  /* 🟢 회신 받음 클릭 → 작은 미리보기 (보낸 사람 · 제목 · 앞부분) → '답장 내용 전체 보기'로 회신 창 */
  let popClose = null;
  function replyPopover(st, anchor, h) {
    if (popClose) popClose();
    if (!h || !h.reply) return;
    const r = h.reply;
    const text = String(r.body || '').replace(/\s+/g, ' ').trim();
    const pop = doc.createElement('div');
    pop.className = 'card ptn-rec-pop';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', `${h.partnerName || ''} 회신 미리보기`);
    pop.innerHTML = `
      <div class="between"><strong>${esc(h.partnerName || '')}</strong>${mailBadge('회신 받음')}</div>
      <p class="small muted">${esc(r.from || '')} · ${esc(fmt.datetime(r.at))}</p>
      <p class="small"><b>${esc(r.subject || '(제목 없음)')}</b></p>
      <p class="small ptn-rec-pop-text">${esc(text.slice(0, 240) || '(본문 없음)')}${text.length > 240 ? '…' : ''}</p>
      <div class="flex wrap mt-8">
        <button type="button" class="btn small dark" data-pop="open">📨 답장 내용 전체 보기</button>
        <button type="button" class="btn small ghost" data-pop="close">닫기</button>
      </div>`;
    doc.body.appendChild(pop);
    const a = anchor.getBoundingClientRect();
    const w = Math.min(360, window.innerWidth - 32);
    pop.style.width = w + 'px';
    pop.style.left = Math.max(16, Math.min(a.left + window.scrollX, window.scrollX + window.innerWidth - w - 16)) + 'px';
    pop.style.top = (a.bottom + window.scrollY + 6) + 'px';
    const onDoc = e => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    function close() {
      pop.remove();
      doc.removeEventListener('mousedown', onDoc);
      doc.removeEventListener('keydown', onKey);
      popClose = null;
    }
    popClose = close;
    setTimeout(() => { doc.addEventListener('mousedown', onDoc); doc.addEventListener('keydown', onKey); });
    pop.addEventListener('click', e => {
      const b = e.target.closest('[data-pop]');
      if (!b) return;
      close();
      if (b.dataset.pop === 'open') openReplyPanel(st, h, { showOriginal: true });
    });
    pop.querySelector('[data-pop="open"]').focus();
  }

  /* 업체 이름 클릭 → 그 업체와 주고받은 메일 전체 (보냄 · 받음 · 답장) 시간순 */
  function threadOf(st, h) {
    const same = x => (h.partnerId ? x.partnerId === h.partnerId : x.partnerName === h.partnerName);
    const out = [];
    st.items.filter(same).forEach(x => {
      if (x.subject || x.body) out.push({ dir: 'out', at: x.at, who: x.owner || 'REBOOT', to: x.to, label: x.activity || '메일 발송', subject: x.subject, body: x.body, status: x.mailStatus, error: x.error });
      if (x.reply) out.push({ dir: 'in', at: x.reply.at, who: x.reply.from, label: '회신 받음', subject: x.reply.subject, body: x.reply.body, estimated: x.reply.estimated });
    });
    return out.sort((a, b) => String(a.at || '').localeCompare(String(b.at || '')));
  }
  function openThread(st, h) {
    if (!h) return;
    const list = threadOf(st, h);
    const body = doc.createElement('div');
    body.className = 'part-partners';
    body.innerHTML = list.length ? `
      <p class="small muted mb-16">${esc(h.partnerType || '')}${h.partnerId ? ' · ' + esc(h.partnerId) : ''} · 보냄 ${fmt.num(list.filter(m => m.dir === 'out').length)}통 · 받음 ${fmt.num(list.filter(m => m.dir === 'in').length)}통 · 오래된 순</p>
      <ol class="ptn-thread">${list.map((m, i) => `
        <li class="ptn-thread-item ${m.dir}">
          <div class="between"><span><b>${m.dir === 'in' ? '📥 받음' : '📤 보냄'}</b> · ${esc(m.label)}${m.dir === 'out' && m.status ? ' ' + mailBadge(m.status) : ''}${m.estimated ? ' ' + badge('추정 매칭', 'orange') : ''}</span>
            <span class="small muted nowrap">${esc(fmt.datetime(m.at))}</span></div>
          <p class="small muted">${m.dir === 'in' ? 'From ' + esc(m.who || '') : esc(m.who || '') + (m.to ? ' → ' + esc(m.to) : '')}</p>
          <details${i === list.length - 1 ? ' open' : ''}><summary class="small"><b>${esc(m.subject || '(제목 없음)')}</b></summary>
            <pre class="ptn-rec-pre">${esc(m.body || '(본문 없음)')}</pre></details>
          ${m.error ? `<span class="ptn-rec-err">${esc(m.error)}</span>` : ''}
        </li>`).join('')}</ol>` : ui.empty('주고받은 메일이 없습니다. (수기 기록만 있음)', 'mail');
    ui.modal({ title: `✉️ ${h.partnerName || ''} — 메일 히스토리`, body, wide: true, actions: [{ label: '닫기', value: false }] });
  }

  /* 🟢 회신 창: AI 요약 · AI 답장 초안 · 같은 대화로 답장 발송 */
  async function openReplyPanel(st, h, opts = {}) {
    if (!h || !h.reply) return;
    const r = h.reply;
    const s = st.status || {};
    const reSubject = 'Re: ' + String(r.subject || h.subject || '').replace(/^\s*((re|fw|fwd)\s*:\s*)+/i, '');
    const body = doc.createElement('div');
    body.className = 'part-partners ptn-rec-replybox';
    body.innerHTML = `
      <div class="grid-2 ptn-rec-reply">
        <div>
          <div class="ptn-rec-rmeta">
            <div class="between"><strong>${esc(h.partnerName || '')}</strong>${mailBadge('회신 받음')}</div>
            <p class="small muted">${esc(r.from || '')} · ${esc(fmt.datetime(r.at))}${r.estimated ? ' · ' + badge('추정 매칭', 'orange') : ''}${h.kind === '샘플' ? ' · ' + badge('예시', 'orange') : ''}</p>
            <p class="small"><b>${esc(r.subject || '')}</b></p>
          </div>
          <div class="ptn-rec-summary mt-16" data-ref="summary">${s.openai ? '<div class="skeleton ptn-skel-sm"></div>' : `<p class="note">${settingsLink}에서 OPENAI_API_KEY를 넣으면 AI 요약(핵심 · 요청사항 · 분위기 · 다음 액션)을 볼 수 있습니다.</p>`}</div>
          <details class="mt-16"${s.openai && !opts.showOriginal ? '' : ' open'}><summary class="small">회신 원문</summary><pre class="ptn-rec-pre">${esc(r.body || '(본문 없음)')}</pre></details>
          <details class="mt-8"><summary class="small">우리가 보낸 메일</summary><pre class="ptn-rec-pre">${esc(h.subject || '')}\n\n${esc(h.body || '')}</pre></details>
        </div>
        <div>
          <label class="field"><span>답장에 담을 내용 (한국어)</span><textarea class="input" rows="4" data-r="points" placeholder="예: 11월 15일 이벤트에 샘플 제공 가능, 수량과 조건은 다음 주에 안내"></textarea></label>
          <div class="flex wrap mt-8"><button type="button" class="btn soft small" data-r-act="draft"${s.openai ? '' : ' disabled'}>🤖 AI 답장 초안</button><span data-ref="draft-badge"></span></div>
          <label class="field"><span>제목</span><input class="input" data-r="subject" value="${esc(reSubject)}" maxlength="300"></label>
          <label class="field"><span>본문 (영어)</span><textarea class="input" rows="9" data-r="body"></textarea></label>
          <label class="field"><span>담당자</span><input class="input" data-r="owner" value="${esc(myName)}" maxlength="60"></label>
          <label class="check mt-8"><input type="checkbox" data-r="confirm"${s.smtp ? '' : ' disabled'}> 내용을 확인했고 같은 Gmail 대화로 보냅니다.</label>
          <div class="flex wrap mt-16">
            <button type="button" class="btn dark" data-r-act="send" disabled>📨 답장 발송</button>
            <button type="button" class="btn ghost" data-r-act="detail">✉️ 메일 화면에서 자세히 쓰기</button>
          </div>
          <p class="hint" data-ref="rhint">${s.smtp ? '' : `Gmail이 연결되지 않았습니다 — ${settingsLink}`}</p>
        </div>
      </div>`;
    let changed = false;
    const val = k => body.querySelector(`[data-r="${k}"]`);
    const sync = () => {
      const ok = !!s.smtp && val('confirm').checked && val('subject').value.trim() && val('body').value.trim() && val('owner').value.trim();
      body.querySelector('[data-r-act="send"]').disabled = !ok;
    };
    body.addEventListener('input', e => {
      if (e.target.matches('[data-r="subject"], [data-r="body"]')) val('confirm').checked = false;
      sync();
    });
    body.addEventListener('change', sync);
    body.addEventListener('click', async e => {
      const b = e.target.closest('[data-r-act]');
      if (!b || b.disabled) return;
      const a = b.dataset.rAct;
      if (a === 'resum') return loadSummary(body, h, true);
      if (a === 'detail') {
        try { REBOOT.use('partners.outreach').openReply(h); } catch (err) { toast('메일 화면을 열지 못했습니다.', 'error'); return; }
        closeModal(body);
        return;
      }
      if (a === 'draft') {
        ui.busy(b, true);
        try {
          const out = await REBOOT.api(`/api/partners/history/${h.id}/reply-draft`, { points: val('points').value });
          val('subject').value = out.subject;
          val('body').value = out.body;
          val('confirm').checked = false;
          body.querySelector('[data-ref="draft-badge"]').innerHTML = badge('AI 초안 · 참고용', 'purple');
        } catch (err) {
          toast(err.message, 'error');
        } finally {
          ui.busy(b, false);
          sync();
        }
        return;
      }
      if (a === 'send') {
        if (!val('confirm').checked) return;   // 확인 체크 없이는 보내지 않음
        ui.busy(b, true);
        try {
          const out = await REBOOT.api('/api/partners/email/send', {
            to: replyAddress(r.from) || h.to, subject: val('subject').value, body: val('body').value, owner: val('owner').value,
            confirm: true, partner_id: h.partnerId, partner_name: h.partnerName, partner_type: h.partnerType,
            attachment_ids: [], reply_to: h.id
          });
          changed = true;
          if (out.ok) {
            toast('답장을 보냈습니다 — 같은 Gmail 대화 · 진행 상태 협의 중');
            closeModal(body);
            return;
          }
          toast(`답장 발송 실패: ${out.error} (이력에 저장했습니다)`, 'error');
          val('confirm').checked = false;
        } catch (err) {
          toast(err.message, 'error');
        }
        ui.busy(b, false);
        sync();
      }
    });
    const done = ui.modal({ title: `🟢 회신 — ${h.partnerName || ''}`, body, wide: true, actions: [{ label: '닫기', value: false }] });
    if (s.openai) loadSummary(body, h, false);
    await done;
    if (changed && st.el.isConnected) reload(st);
  }

  async function loadSummary(body, h, force) {
    const box = body.querySelector('[data-ref="summary"]');
    if (!box) return;
    box.innerHTML = '<div class="skeleton ptn-skel-sm"></div>';
    try {
      const out = await REBOOT.api(`/api/partners/history/${h.id}/reply-summary`, { force: !!force });
      const sm = out.summary || {};
      box.innerHTML = `<div class="between"><strong class="small">🤖 AI 요약</strong>${badge('AI · 참고용', 'purple')}</div>
        <dl class="ptn-rec-dl">
          <dt>핵심</dt><dd>${esc(sm.core)}</dd><dt>요청사항</dt><dd>${esc(sm.requests)}</dd>
          <dt>분위기</dt><dd>${esc(sm.mood)}</dd><dt>다음 액션</dt><dd>${esc(sm.next_action)}</dd>
        </dl>
        <button type="button" class="link" data-r-act="resum">↻ 다시 요약</button>`;
    } catch (e) {
      box.innerHTML = `<p class="note error">AI 요약을 만들지 못했습니다: ${esc(e.message)}</p><button type="button" class="link mt-8" data-r-act="resum">↻ 다시 시도</button>`;
    }
  }

  /* ═════════ 협업 보드 (업체별 최근 진행 상태) — 업체 컨택 · 협업 이력에서 함께 씀 ═════════ */

  async function board(el, opts) {
    opts = opts || {};
    el.innerHTML = '<div class="skeleton ptn-skel"></div>';
    let ov;
    try {
      ov = await REBOOT.api('/api/partners/overview');
    } catch (e) {
      if (el.isConnected) el.innerHTML = `<p class="note error">${esc(e.message)}</p>`;
      return;
    }
    if (!el.isConnected) return;
    setAwaiting(ov.awaiting);
    const partners = ov.partners || [];
    if (!partners.length) {
      el.innerHTML = ui.empty('아직 협업 기록이 없습니다. 협업 메일을 보내거나 수기 기록을 추가하면 보드에 나타납니다.', 'grid');
      return;
    }
    el.innerHTML = `<div class="ptn-rec-kanban">${R.board.map(col => {
      const items = partners.filter(p => col.match.includes(p.progress));
      return `<section class="ptn-rec-col" aria-label="${esc(col.title)} ${items.length}곳">
        <h3><span>${esc(col.title)}</span>${badge(String(items.length))}</h3>
        ${items.map(miniCard).join('') || '<p class="ptn-rec-colempty">없음</p>'}
      </section>`;
    }).join('')}</div>
    <p class="panel-note mt-8">업체마다 가장 최근 기록의 진행 상태입니다. 진행 상태는 협업 이력의 ✏️ 편집 · 수기 기록으로 바꿉니다. 카드를 누르면 그 업체로 이동합니다.</p>`;
    el.addEventListener('click', e => {
      const b = e.target.closest('[data-pick]');
      if (!b) return;
      const p = partners.find(x => x.key === b.dataset.pick);
      if (p && opts.onPick) opts.onPick(p.id, p);
    });
  }

  function miniCard(p) {
    return `<button type="button" class="ptn-rec-card" data-pick="${esc(p.key)}">
      <div class="flex ptn-rec-cardhead"><span class="initial ptn-av${avIndex(p.key)}" aria-hidden="true">${esc(initials(p.name))}</span><strong>${esc(p.name || '(이름 없음)')}</strong></div>
      <p>${esc(p.lastActivity || '기록')}${p.sample ? ' ' + badge('예시', 'orange') : ''}</p>
      <small>${esc(p.type || '구분 없음')} · 기록 ${fmt.num(p.count)}건 · ${esc(fmt.date(p.lastAt))}</small>
      ${p.mailStatus ? `<div class="ptn-rec-cardfoot">${mailBadge(p.mailStatus)}</div>` : ''}
    </button>`;
  }

  /* ═════════ 3-5 크리에이터별 판매 실적 (🧪 더미) ═════════ */

  const usd = n => fmt.usd(n);
  const usdCompact = n => {
    try { return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 }).format(n); } catch (e) { return '$' + n; }
  };
  const shortDate = iso => { const p = String(iso || '').split('-'); return p.length === 3 ? `${Number(p[1])}/${Number(p[2])}` : String(iso || ''); };
  const clipName = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s));

  function getSalesPref() {
    const p = store.get(K.sales, null);
    return p && typeof p === 'object' ? p : { days: 30 };
  }

  function chartTheme(el) {
    const cs = global.getComputedStyle(el);
    const v = (name, fb) => (cs.getPropertyValue(name) || '').trim() || fb;
    return { series: v('--ptn-chart', '#637c13'), accent: v('--red', '#a5432e'), grid: v('--line', '#e9ece4'), text: v('--muted', '#858981'), surface: v('--surface', '#ffffff') };
  }
  function alpha(hex, a) {
    const m = String(hex).trim().match(/^#?([0-9a-f]{6})$/i);
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  // 선 그래프: 마우스 위치에 세로 안내선 (crosshair)
  function crosshair(t) {
    return {
      id: 'ptnCrosshair',
      afterDatasetsDraw(chart) {
        const act = chart.tooltip && chart.tooltip.getActiveElements ? chart.tooltip.getActiveElements() : [];
        if (!act.length) return;
        const x = act[0].element.x;
        const { top, bottom } = chart.chartArea;
        const ctx = chart.ctx;
        ctx.save();
        ctx.strokeStyle = t.text;
        ctx.globalAlpha = 0.4;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, top);
        ctx.lineTo(x, bottom);
        ctx.stroke();
        ctx.restore();
      }
    };
  }

  async function renderSales(el) {
    const st = { el, data: null, pref: getSalesPref(), picked: [] };
    el.innerHTML = `
      <div class="filters ptn-rec-salesbar">
        <div class="flex wrap">${badge('🧪 더미 데이터', 'orange')}<span class="panel-note">Shopify 할인코드 주문 · UTM 방문 연동 전 가상값입니다 (환불 주문 제외).</span></div>
        <div class="flex wrap">
          <div class="period" role="group" aria-label="조회 기간">${R.salesPresets.map(n => `<button type="button" data-days="${n}">최근 ${n}일</button>`).join('')}</div>
          <input type="date" class="input ptn-rec-date" data-ref="start" aria-label="시작일"><span aria-hidden="true">~</span><input type="date" class="input ptn-rec-date" data-ref="end" aria-label="종료일">
          <button type="button" class="btn small soft" data-sales-act="csv">${icon('download')} CSV</button>
        </div>
      </div>
      <div data-ref="out"><div class="skeleton ptn-skel"></div></div>`;
    el.addEventListener('click', e => {
      const b = e.target.closest('[data-days]');
      if (b && st.data) {
        st.pref = { days: Number(b.dataset.days) };
        store.set(K.sales, st.pref);
        loadSales(st);
        return;
      }
      if (e.target.closest('[data-sales-act="csv"]')) downloadCsv(st);
      if (e.target.closest('[data-sales-act="all"]')) { st.picked = []; updateLine(st); }
    });
    el.addEventListener('change', e => {
      if (!e.target.matches('[data-ref="start"], [data-ref="end"]')) return;
      const start = el.querySelector('[data-ref="start"]').value;
      const end = el.querySelector('[data-ref="end"]').value;
      if (!start || !end) return;
      st.pref = start <= end ? { start, end } : { start: end, end: start };
      store.set(K.sales, st.pref);
      loadSales(st);
    });
    loadSales(st);
  }

  function presetRange(days, max) {
    const p = String(max).split('-').map(Number);
    const d = new Date(p[0], p[1] - 1, p[2]);
    d.setDate(d.getDate() - (days - 1));
    return { start: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, end: max };
  }

  async function loadSales(st) {
    const el = st.el;
    const q = new URLSearchParams();
    const range = st.data && st.data.range;
    const want = st.pref.days ? (range ? presetRange(st.pref.days, range.max) : null) : st.pref;
    if (want) { q.set('start', want.start); q.set('end', want.end); }
    try {
      const data = await REBOOT.api('/api/partners/sales' + (q.toString() ? '?' + q : ''));
      if (!el.isConnected) return;
      st.data = data;
      if (st.pref.days && !want) {   // 처음: 기간 범위를 알고 나서 프리셋을 다시 적용
        const r = presetRange(st.pref.days, data.range.max);
        if (r.start !== data.start || r.end !== data.end) return loadSales(st);
      }
      paintSales(st);
    } catch (e) {
      if (el.isConnected) el.querySelector('[data-ref="out"]').innerHTML = `<p class="note error">${esc(e.message)}</p>`;
    }
  }

  function paintSales(st) {
    const d = st.data;
    const el = st.el;
    const s = el.querySelector('[data-ref="start"]');
    const e = el.querySelector('[data-ref="end"]');
    [s, e].forEach(x => { x.min = d.range.min; x.max = d.range.max; });
    s.value = d.start;
    e.value = d.end;
    el.querySelectorAll('[data-days]').forEach(b => b.classList.toggle('selected', Number(b.dataset.days) === st.pref.days));
    const t = d.totals;
    destroyCharts();
    el.querySelector('[data-ref="out"]').innerHTML = `
      <div class="kpis">
        ${ui.kpi({ label: '접속량', value: fmt.num(t.visits), unit: '회', featured: true, icon: 'users', foot: '할인코드 링크 방문 (더미)' })}
        ${ui.kpi({ label: '주문', value: fmt.num(t.orders), unit: '건', icon: 'check', foot: `환불 ${fmt.num(t.refunds)}건 제외 · 전환율 ${fmt.pct(t.conversion, 2)}` })}
        ${ui.kpi({ label: '판매량', value: fmt.num(t.units), unit: '개', icon: 'chart', foot: '주문 수량 합계 (더미)' })}
        ${ui.kpi({ label: '매출', value: usd(t.revenue), icon: 'dollar', foot: `가상 단가 $${fmt.num(d.unitPrice, 2)} 기준 (더미)` })}
      </div>
      <p class="panel-note mb-16">${esc(d.start)} ~ ${esc(d.end)} · 크리에이터 ${fmt.num(d.creators.length)}명 ${d.source === 'placeholder' ? '(가져온 인플루언서가 없어 Creator A~L 가상 이름)' : '(가져온 인플루언서 상위 ' + fmt.num(d.creators.length) + '곳 · 예시 행 제외)'}</p>
      <div class="grid-2 ptn-rec-charts">
        <section class="card"><div class="card-head"><h2>크리에이터별 매출</h2>${badge('더미', 'orange')}</div>
          <div class="ptn-rec-chart" data-ref="bar-box"><canvas data-ref="bar" role="img" aria-label="크리에이터별 매출 막대 그래프 (표는 아래)"></canvas></div></section>
        <section class="card"><div class="card-head"><h2>일별 매출 추이<span data-ref="line-who"></span></h2>
            <div class="flex"><button type="button" class="btn small soft" data-sales-act="all" data-ref="line-all" hidden>전체 보기</button>${badge('더미', 'orange')}</div></div>
          <p class="ptn-rec-change" data-ref="line-change"></p>
          <div class="ptn-rec-chart ptn-rec-chart-line"><canvas data-ref="line" role="img" aria-label="일별 매출 선 그래프 (일별 표는 아래)"></canvas></div>
          <details class="mt-8"><summary class="small">일별 표 보기</summary>
            <div class="table-wrap ptn-rec-daily"><table class="data-table"><thead><tr><th scope="col">날짜</th><th class="num" scope="col">접속량</th><th class="num" scope="col">주문</th><th class="num" scope="col">판매량</th><th class="num" scope="col">매출</th></tr></thead>
            <tbody>${d.daily.map(x => `<tr><td>${esc(x.date)}</td><td class="num">${fmt.num(x.visits)}</td><td class="num">${fmt.num(x.orders)}</td><td class="num">${fmt.num(x.units)}</td><td class="num">${usd(x.revenue)}</td></tr>`).join('')}</tbody></table></div>
          </details></section>
      </div>
      <details class="card mt-24 ptn-rec-salestable" data-ref="sales-table"${st.tableOpen === false ? '' : ' open'}>
        <summary class="card-head"><h2>크리에이터별 실적</h2>${badge('🧪 더미 데이터', 'orange')}</summary>
        <div class="table-wrap"><table class="data-table">
          <thead><tr>${SALES_COLS.map(c => headCell(c, c.num ? 'num' : '')).join('')}</tr></thead>
          <tbody>${d.creators.map(c => `<tr><td>${esc(c.name)}</td><td><code>${esc(c.code)}</code></td><td class="num">${fmt.num(c.visits)}</td><td class="num">${fmt.num(c.orders)}</td><td class="num">${fmt.num(c.units)}</td><td class="num">${usd(c.revenue)}</td><td class="num">${fmt.pct(c.conversion, 2)}</td></tr>`).join('')}</tbody>
          <tfoot><tr><td>합계</td><td></td><td class="num">${fmt.num(t.visits)}</td><td class="num">${fmt.num(t.orders)}</td><td class="num">${fmt.num(t.units)}</td><td class="num">${usd(t.revenue)}</td><td class="num">${fmt.pct(t.conversion, 2)}</td></tr></tfoot>
        </table></div>
        <p class="panel-note mt-8">${esc(d.note || '')} 연동 후에는 Shopify Orders API(discount_codes) · UTM 집계로 바뀝니다.</p>
      </details>`;
    bindResize(el.querySelector('[data-ref="sales-table"]'));
    el.querySelector('[data-ref="sales-table"]').addEventListener('toggle', ev => { st.tableOpen = ev.target.open; });
    st.picked = (st.picked || []).filter(n => d.creators.some(c => c.name === n));
    drawCharts(st);
  }

  function drawCharts(st) {
    const Chart = global.Chart;
    const d = st.data;
    const el = st.el;
    if (!Chart) {
      el.querySelectorAll('.ptn-rec-chart').forEach(x => { x.innerHTML = ui.empty('차트 라이브러리를 불러오지 못했습니다. 아래 표를 보세요.', 'chart'); });
      return;
    }
    const t = chartTheme(el);
    const rows = d.creators;
    el.querySelector('[data-ref="bar-box"]').style.height = `${Math.max(220, rows.length * 30 + 48)}px`;
    st.bar = new Chart(el.querySelector('[data-ref="bar"]'), {
      type: 'bar',
      data: {
        labels: rows.map(r => r.name),
        datasets: [{ label: '매출', data: rows.map(r => r.revenue), backgroundColor: t.series, hoverBackgroundColor: t.series,
          borderRadius: 4, borderSkipped: 'start', maxBarThickness: 22 }]
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: items => rows[items[0].dataIndex].name, label: c => `매출 ${usd(c.parsed.x)} · 주문 ${fmt.num(rows[c.dataIndex].orders)}건 · ${rows[c.dataIndex].code}`, footer: () => '누르면 일별 매출 추이' } }
        },
        // 막대(또는 이름)를 누르면 오른쪽 선 그래프가 그 크리에이터의 일별 매출로 바뀜 · 다시 누르면 전체
        onClick: (evt, els, chart) => {
          const hit = els.length ? els[0].index : chart.scales.y.getValueForPixel(evt.y);
          if (hit == null || !rows[hit]) return;
          const name = rows[hit].name;   // 여러 명 고를 수 있음 · 다시 누르면 빠짐
          st.picked = st.picked.includes(name) ? st.picked.filter(n => n !== name) : st.picked.concat(name);
          updateLine(st);
        },
        onHover: (evt, els) => { evt.native.target.style.cursor = els.length ? 'pointer' : 'default'; },
        scales: {
          x: { beginAtZero: true, grid: { color: t.grid }, border: { display: false }, ticks: { color: t.text, maxTicksLimit: 5, callback: v => usdCompact(v) } },
          y: { grid: { display: false }, border: { color: t.grid }, ticks: { color: t.text, autoSkip: false, callback: (v, i) => clipName(rows[i] ? rows[i].name : '', 18) } }
        }
      }
    });
    charts.push(st.bar);
    const days = d.daily;
    st.line = new Chart(el.querySelector('[data-ref="line"]'), {
      type: 'line',
      data: {
        labels: days.map(x => x.date),
        datasets: [{ label: '일별 매출', data: days.map(x => x.revenue), borderColor: t.series, backgroundColor: alpha(t.series, 0.1), fill: 'origin',
          borderWidth: 2, borderJoinStyle: 'round', borderCapStyle: 'round', tension: 0.25, pointRadius: 0, pointHitRadius: 12,
          pointHoverRadius: 5, pointHoverBorderWidth: 2, pointHoverBorderColor: t.surface, pointHoverBackgroundColor: t.series }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false, position: 'top', align: 'end', labels: { color: t.text, boxWidth: 10, boxHeight: 2, filter: i => !!i.text } },
          tooltip: { callbacks: { title: items => days[items[0].dataIndex].date, label: c => {
            const x = (c.dataset.creator ? c.dataset.creator.daily : days)[c.dataIndex];
            return `${c.dataset.label} ${usd(c.parsed.y)} · 주문 ${fmt.num(x.orders)}건 · 접속 ${fmt.num(x.visits)}`;
          } } }
        },
        scales: {
          x: { grid: { display: false }, border: { color: t.grid }, ticks: { color: t.text, maxTicksLimit: 8, maxRotation: 0, callback: (v, i) => shortDate(days[i] ? days[i].date : '') } },
          y: { beginAtZero: true, grid: { color: t.grid }, border: { display: false }, ticks: { color: t.text, maxTicksLimit: 5, callback: v => usdCompact(v) } }
        }
      },
      plugins: [crosshair(t)]
    });
    charts.push(st.line);
    updateLine(st);
  }

  // 크리에이터별 비교선 색 (전체 매출 초록과 겹치지 않게, 순서대로 돌려 씀)
  const CREATOR_COLORS = ['#d9534f', '#3b7dd8', '#e8902a', '#8e5cc9', '#1aa39a', '#d64f9b', '#6d8a1f', '#c9a227', '#4a5190', '#b5651d', '#2f9e44', '#7a7d85'];
  const creatorColor = (st, name) => CREATOR_COLORS[Math.max(0, st.data.creators.findIndex(x => x.name === name)) % CREATOR_COLORS.length];

  // 막대에서 고른 크리에이터들 (고른 순서)
  function pickedOf(st) {
    return (st.picked || []).map(n => st.data.creators.find(x => x.name === n)).filter(Boolean);
  }
  // 상승률 한 줄: ▲ 빨강 · ▼ 파랑, 크리에이터 이름 앞에 선 색 점
  function changeItem(name, change, revenue, color) {
    if (change == null) return '';
    const dot = color ? `<i style="background:${color}" aria-hidden="true"></i>` : '';
    return `<li class="${change < 0 ? 'down' : ''}">${dot}<b>${change >= 0 ? '▲' : '▼'} ${fmt.num(Math.abs(change), 1)}%</b> <span>${esc(name)} · ${usd(revenue)}</span></li>`;
  }
  function updateLine(st) {
    const el = st.el;
    const picked = pickedOf(st);
    const t = chartTheme(el);
    const days = st.data.end ? (new Date(st.data.end) - new Date(st.data.start)) / 864e5 + 1 : 0;
    el.querySelector('[data-ref="line-who"]').textContent = picked.length ? ` · ${picked.length}명 비교` : '';
    el.querySelector('[data-ref="line-all"]').hidden = !picked.length;
    el.querySelector('[data-ref="line-change"]').innerHTML = `<ul>${changeItem('전체', st.data.totals.change, st.data.totals.revenue)}${
      picked.map(c => changeItem(c.name, c.change, c.revenue, creatorColor(st, c.name))).join('')}</ul><small>이전 ${fmt.num(days)}일 대비 매출</small>`;
    if (st.line) {
      const sets = st.line.data.datasets;
      sets.length = 1;   // 전체 매출 선은 그대로 두고 비교선만 다시 만듦
      picked.forEach(c => {
        const col = creatorColor(st, c.name);
        sets.push({ label: c.name, creator: c, data: c.daily.map(x => x.revenue), borderColor: col, backgroundColor: col, fill: false,
          borderWidth: 2, tension: 0.25, pointRadius: 0, pointHitRadius: 12,
          pointHoverRadius: 5, pointHoverBorderWidth: 2, pointHoverBorderColor: t.surface, pointHoverBackgroundColor: col });
      });
      sets[0].label = picked.length ? '전체 매출' : '일별 매출';
      st.line.options.plugins.legend.display = picked.length > 0;
      st.line.update('none');
    }
    if (st.bar) {   // 고른 크리에이터 막대만 선과 같은 색으로
      st.bar.data.datasets[0].backgroundColor = st.bar.data.datasets[0].hoverBackgroundColor =
        st.data.creators.map(x => (st.picked.includes(x.name) ? creatorColor(st, x.name) : t.series));
      st.bar.update('none');
    }
  }

  function downloadCsv(st) {
    const d = st.data;
    if (!d) return;
    const rows = [['구분', '기간', '크리에이터', '할인코드', '접속량', '주문 수', '판매량', '매출(USD)', '전환율(%)', '환불(건)']];
    d.creators.forEach(c => rows.push(['더미', `${d.start}~${d.end}`, c.name, c.code, c.visits, c.orders, c.units, c.revenue, c.conversion, c.refunds]));
    const t = d.totals;
    rows.push(['더미', `${d.start}~${d.end}`, '합계', '', t.visits, t.orders, t.units, t.revenue, t.conversion, t.refunds]);
    ui.download(`reboot-partners-creator-sales-dummy-${d.start}_${d.end}.csv`, ui.csv(rows), 'text/csv;charset=utf-8');
  }

  /** 성과분석 파트용: Promise {dummy:true, creators:[{name, code, visits, orders, units, revenue, conversion}], totals} */
  function creatorSales(o) {
    o = o || {};
    const q = new URLSearchParams();
    if (o.start) q.set('start', o.start);
    if (o.end) q.set('end', o.end);
    return REBOOT.api('/api/partners/sales' + (q.toString() ? '?' + q : '')).then(d => ({
      dummy: true, start: d.start, end: d.end, source: d.source, unitPrice: d.unitPrice, currency: d.currency,
      creators: d.creators.map(c => ({ name: c.name, code: c.code, visits: c.visits, orders: c.orders, units: c.units, revenue: c.revenue, conversion: c.conversion })),
      totals: d.totals
    }));
  }

  /* ───────── 공개 ───────── */

  function render(el, view) {
    destroyCharts();
    el.classList.add('ptn-rec');
    if (view === 'sales') return renderSales(el);
    return renderHistory(el);
  }

  REBOOT.expose('partners.records', { render, board, refreshSummary, awaitingCount, focus });
  REBOOT.expose('partners', { creatorSales });
})(typeof window !== 'undefined' ? window : globalThis);
