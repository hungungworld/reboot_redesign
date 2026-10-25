/* 콘텐츠 제작 파트 (담당: 나원) — tabs/content/docs/README.md · 챗봇_구성_설명서.md
   탭 두 개: AI와 함께 제작 (5항목 시트 → 대화 → 초안·이미지 → 버전·최종본) · 내 캠페인 (목록·검색·복원)
   상태 원본은 서버(DB)입니다. 화면은 서버가 돌려준 캠페인 상태를 그대로 그리고, "반영"은 서버 저장 뒤에만 표시합니다.
   다른 파트에는 REBOOT.use('content').campaigns()만 공개합니다. */
(function (global) {
  'use strict';

  const REBOOT = global.REBOOT;
  const DATA = global.REBOOT_DATA && global.REBOOT_DATA.content;
  if (!REBOOT || !DATA) throw new Error('content: REBOOT core와 tabs/content/static/data.js를 먼저 불러와야 합니다');

  const ui = REBOOT.ui;
  const { esc, badge, icon, fmt, toast } = ui;
  const API = '/api/content';
  const LS = 'reboot:content:';           // 브라우저 임시 저장 (작성 중 시트 · 새 캠페인 기본 설정)
  const RANK = { PASS: 0, COND: 1, REVIEW: 2, REVISE: 3, BLOCK: 4 };
  const ACTIVE_JOB = ['queued', 'running', 'saving'];

  const S = {
    root: null,
    tab: 'create',
    sheet: emptySheet(),     // 새 캠페인 시트
    edit: null,              // 열린 캠페인의 시트 수정본
    camp: null,              // 열린 캠페인 (서버 상태)
    list: null, listMeta: null, listErr: '', filter: '전체', query: '', folder: '전체',
    meta: null, health: null,
    sending: false, pending: null, retryId: null, retryText: '',
    baseOff: false, showLayers: true,
    reviews: {},             // versionId → 컴플라이언스 판정 (이번 화면에서 실행)
    unitReviews: {},         // 'versionId:문구 번호' → 문구별 컴플라이언스 판정 (이번 화면에서만 · 서버에 기록 안 함)
    sheetBox: null,          // 열린 '기본 정보 시트 수정' 팝업의 시트 칸
    pollTimer: null, searchTimer: null,
    personas: null,
    apiCheck: null, apiChecking: false,   // 연결 테스트 결과
    ivBusy: false, ivDraft: '',          // 인터뷰: 보내는 중 · 직접 입력 칸
    imgDraft: '',                        // 이미지 AI 수정 입력 칸
    vidDraft: ''                         // 영상 AI 수정 입력 칸
  };
  const CIRCLED = ['①', '②', '③'];

  /* ───────── 작은 도우미 ───────── */

  function emptySheet() {
    return { content_type: '', channels: [], brand_focus: false, product_ids: [], goal: '', extra_request: '', reference_text: '',
      use_character: 'AI 판단', language: '영어' };
  }
  const SHEET_LANGUAGES = ['한국어', '영어'];  // 새 시트의 콘텐츠 언어 (서버 routes.SHEET_LANGUAGES와 같은 이름)
  const rid = () => 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  function lsGet(k, d) { try { const v = global.localStorage.getItem(LS + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } }
  function lsSet(k, v) { try { global.localStorage.setItem(LS + k, JSON.stringify(v)); } catch (e) { /* 저장 못 해도 화면은 동작 */ } }
  function lsDel(k) { try { global.localStorage.removeItem(LS + k); } catch (e) { /* 무시 */ } }
  const ref = name => (S.root ? S.root.querySelector(`[data-ref="${name}"]`) : null);
  function products() { try { return REBOOT.data.products() || []; } catch (e) { return []; } }
  const productById = id => products().find(p => p.id === id) || null;
  const typeOf = id => DATA.contentTypes.find(t => t.id === id) || null;
  const typeLabel = id => (typeOf(id) ? typeOf(id).label : id || '—');
  const channelLabel = id => { const c = DATA.channels.find(x => x.id === id); return c ? c.label : id; };
  const decisionLabel = k => DATA.decisionLabels[k] || k;
  function fieldLabel(f) {
    f = String(f || '');
    if (f.startsWith('sheet.')) return DATA.sheetLabels[f.slice(6)] || f.slice(6);
    if (f.startsWith('decisions.')) return decisionLabel(f.slice(10));
    return f;
  }
  function shortValue(v) {
    if (Array.isArray(v)) {
      return v.map(x => (DATA.channels.some(c => c.id === x) ? channelLabel(x) : productById(x) ? productById(x).name : typeOf(x) ? typeLabel(x) : x)).join(', ') || '없음';
    }
    if (v === true) return '예';
    if (v === false) return '아니요';
    if (v == null || v === '') return '비움';
    return typeOf(v) ? typeLabel(v) : String(v);
  }
  function statusBadge(st) {
    if (st === 'confirmed') return badge('정함', 'green');
    if (st === 'proposed') return badge('AI 추천');
    return '<span class="badge cnt-undecided">아직</span>';
  }
  const statusTone = st => DATA.statusTone[st] || '';
  const textReady = () => !!(S.meta ? S.meta.textReady : S.health && S.health.openai);
  const textModel = () => (S.meta && S.meta.textModel) || (S.health && S.health.openaiModel) || '—';
  function targetLabel(s) {
    const names = (s.product_ids || []).map(id => { const p = productById(id); return p ? (p.ko || p.name) : id; });
    return (s.brand_focus ? ['Reboot 브랜드 자체'] : []).concat(names).join(' + ') || '미선택';
  }
  function sheetMissing(s) {
    const out = [];
    if (!s.content_type) out.push('콘텐츠');
    if (!s.channels.length) out.push('게시 채널');
    if (!s.brand_focus && !s.product_ids.length) out.push('제작 대상');
    return out;
  }
  function sheetPayload(s) {
    const out = { content_type: s.content_type, channels: s.channels.slice(), brand_focus: !!s.brand_focus,
      product_ids: s.product_ids.slice(), goal: s.goal || '', extra_request: s.extra_request || '' };
    if (usesCharacter(s.content_type)) out.use_character = s.use_character || 'AI 판단';
    if (SHEET_LANGUAGES.includes(s.language)) out.language = s.language;
    return out;
  }
  // 저장해 둔 옛 시트(상품 설명·기획서 · 채널 여러 개)를 지금 규칙에 맞춤: 채널 하나 · CRM끼리만
  function fitSheet(s) {
    if (!typeOf(s.content_type)) s.content_type = '';
    s.channels = (s.channels || []).filter(id => DATA.channels.some(c => c.id === id)).slice(0, 1);
    if (s.content_type && s.channels.length && (s.content_type === DATA.crm.type) !== (s.channels[0] === DATA.crm.channel)) s.channels = [];
    return s;
  }
  const usesCharacter = type => DATA.character.contentTypes.includes(type);
  // 브랜드 키트(콘텐츠 탭 기준)의 제품 형태 — 없으면 공용 팩트시트 값
  function productForm(p) {
    const kit = S.meta && S.meta.productKit && S.meta.productKit[p.id];
    return (kit && kit.form) || p.form || '';
  }
  // 모달 본문도 .part-content 범위로 (CSS는 .part-content .cnt-… 로만)
  function modal(o) {
    const userOpen = o.onOpen;
    return ui.modal(Object.assign({}, o, { onOpen: el => { el.classList.add('part-content'); if (userOpen) userOpen(el); } }));
  }
  function selectRange(el, text) {
    const a = text.indexOf('['), b = text.indexOf(']');
    el.focus();
    if (a >= 0 && b > a) el.setSelectionRange(a + 1, b); else el.setSelectionRange(text.length, text.length);
  }

  /* ───────── 렌더 진입 ───────── */

  function render(root) {
    S.root = root;
    root.classList.add('part-content');
    const saved = lsGet('sheet', null);
    if (saved && typeof saved === 'object') S.sheet = fitSheet(Object.assign(emptySheet(), saved));
    const hash = parseHash();
    if (hash.tab) S.tab = hash.tab;
    root.innerHTML = '<div data-ref="tabs"></div><div data-ref="main"></div>';
    bind(root);
    setupActions();
    renderTabs();
    renderMain();
    loadMeta();
    loadList();
    loadPersonas();
    if (hash.campaign) openCampaign(hash.campaign, { silent: true });
  }

  function parseHash() {
    const h = String(global.location && global.location.hash || '').replace(/^#/, '');
    const m = h.match(/^campaign=(\d+)$/);
    if (m) return { tab: 'create', campaign: +m[1] };
    if (h === 'campaigns') return { tab: 'campaigns' };
    return {};
  }
  function setHash(h) {
    try { global.history.replaceState(null, '', h ? '#' + h : global.location.pathname + global.location.search); } catch (e) { /* 무시 */ }
  }

  function setupActions() {
    const el = REBOOT.page.actions();
    if (!el) return;
    el.innerHTML = `<button type="button" class="btn" data-act="facts">${icon('shield')}제품 근거</button>
      <button type="button" class="btn" data-act="ai-settings">${icon('settings')}AI 설정</button>`;
    el.addEventListener('click', e => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      if (b.dataset.act === 'ai-settings') aiSettingsModal();
      else if (b.dataset.act === 'facts') factsModal();
    });
  }

  function factsModal() {
    modal({
      title: '제품 근거',
      wide: true,
      body: S.camp ? factsInner()
        : '<p class="panel-note">제작 대상을 고르고 <b>이 정보로 시작</b>하면, 선택한 브랜드·제품의 근거가 여기에 보여요. AI는 이 값만 사실 근거로 써요.</p>',
      actions: [{ label: '닫기', value: null }]
    });
  }

  async function loadMeta() {
    try { S.meta = await REBOOT.api(`${API}/meta`); } catch (e) { S.meta = null; }
    try { S.health = await REBOOT.health(); } catch (e) { S.health = null; }
    if (S.tab === 'create') {
      renderApi(); renderAsideParts(); updateComposer(); renderMessages();
      const f = ref('sheet-footer');  // '질문 없이 바로 3안'은 키 확인 뒤에 켜짐
      if (f) f.innerHTML = sheetFooterHtml();
    }
  }

  /* ───────── API 연결 상태 (OpenAI · Higgsfield) ───────── */

  function apiStatusHtml() {
    const con = S.meta && S.meta.connections;
    const keys = (S.health && S.health.keys) || {};
    const oa = con ? con.openai : { key: !!(S.health && S.health.openai), textModel: textModel(), imageModel: '—' };
    const hf = con ? con.higgsfield : { key: !!keys.HIGGSFIELD_API_KEY };
    const chk = S.apiCheck;
    const oaSt = !oa.key ? ['off', '키 없음'] : chk ? (chk.openai.ok ? ['on', '연결 확인됨'] : ['bad', '확인 실패']) : ['on', '키 있음'];
    const hfSt = !hf.key ? ['off', '키 없음'] : chk && chk.higgsfield.checked ? (chk.higgsfield.ok ? ['on', '연결 확인됨'] : ['bad', '확인 실패']) : ['on', '키 있음'];
    const item = (st, name, sub, title) => `<span class="cnt-api-item ${st[0]}" title="${esc(title)}"><i aria-hidden="true"></i><b>${esc(name)}</b>${sub ? `<span class="cnt-api-sub">${esc(sub)}</span>` : ''}<span class="cnt-api-st">${esc(st[1])}</span></span>`;
    return `<span class="cnt-api-label">API 연결</span>
      ${item(oaSt, 'OpenAI', oa.textModel || '', `대화·제작 ${oa.textModel || '—'} · 이미지 ${oa.imageModel || '—'}${oa.imageEdit ? ' (기준 이미지 편집 가능)' : ''}`)}
      ${item(hfSt, 'Higgsfield', '', `이미지 ${hf.imageModel || '—'} · 영상 ${hf.videoModel || '—'} (AI 설정에서 선택)`)}
      <button type="button" class="link cnt-api-test" data-act="api-check" ${S.apiChecking ? 'disabled' : ''}>${S.apiChecking ? '확인 중…' : '연결 테스트'}</button>
      ${chk ? `<span class="cnt-api-msg">${esc(chk.openai.message)}${chk.higgsfield.key ? ` · ${esc(chk.higgsfield.message)}` : ''}</span>` : ''}`;
  }
  function renderApi() { const el = ref('api'); if (el) el.innerHTML = apiStatusHtml(); }

  async function apiCheck() {
    S.apiChecking = true;
    renderApi();
    try {
      S.apiCheck = await REBOOT.api(`${API}/meta/check`);
      const hf = S.apiCheck.higgsfield;
      toast(S.apiCheck.openai.message + (hf.key ? ` · ${hf.message}` : ''), S.apiCheck.openai.ok && (!hf.key || hf.ok) ? '' : 'error');
    } catch (e) { toast(e.message, 'error'); } finally { S.apiChecking = false; renderApi(); }
  }

  async function loadList() {
    const qs = new URLSearchParams();
    if (S.query) qs.set('q', S.query);
    if (S.filter && S.filter !== '전체') qs.set('status', S.filter);
    try {
      const d = await REBOOT.api(`${API}/campaigns${qs.toString() ? '?' + qs : ''}`);
      S.list = d.items;
      S.listMeta = { total: d.total, counts: d.counts, active: d.active };
      S.listErr = '';
    } catch (e) {
      S.listErr = e.message;
      if (!S.list) S.list = [];
    }
    renderTabs();
    if (S.tab === 'campaigns') renderCampaignGrid();
    REBOOT.refreshCounts();
  }

  async function loadPersonas() {
    try {
      const api = REBOOT.use('insights');
      if (!api || typeof api.personas !== 'function') return;
      const list = await Promise.resolve(api.personas());
      S.personas = Array.isArray(list) ? list.filter(p => p && p.name).slice(0, 6) : null;
      if (S.camp) renderQuick();
    } catch (e) { S.personas = null; }
  }

  function upcomingSeasons() {
    try {
      const list = REBOOT.use('home').seasons();
      if (!Array.isArray(list)) return [];
      return list.filter(s => s && !s.past && (s.inLead || s.ongoing || s.dday >= 0)).slice(0, 3);
    } catch (e) { return []; }
  }

  /* ───────── 탭 ───────── */

  function renderTabs() {
    const el = ref('tabs');
    if (!el) return;
    const n = S.listMeta ? S.listMeta.total : '';
    el.innerHTML = `<div class="tabs" role="tablist">
      <button type="button" role="tab" data-act="tab" data-value="create" class="${S.tab === 'create' ? 'active' : ''}" aria-selected="${S.tab === 'create'}">AI와 함께 제작</button>
      <button type="button" role="tab" data-act="tab" data-value="campaigns" class="${S.tab === 'campaigns' ? 'active' : ''}" aria-selected="${S.tab === 'campaigns'}">내 캠페인 <span class="muted small">${esc(n)}</span></button>
    </div>`;
  }

  function renderMain() {
    const el = ref('main');
    if (!el) return;
    if (S.tab === 'campaigns') {
      el.innerHTML = campaignsHtml();
      renderCampaignGrid();
      return;
    }
    // 대화 중에는 '진행 과정 & 결과'를 대화창 바로 아래에 넓게 (오른쪽은 답 정리 · 결정 사항 · 접이식)
    el.innerHTML = S.camp
      ? `<div class="workbench cnt-bench">
          <div class="cnt-main-col">
            <section class="card cnt-studio" data-ref="studio">${studioHtml()}</section>
            <section class="card cnt-results-card" data-ref="results">${resultsInner()}</section>
          </div>
          <aside class="stack cnt-aside" data-ref="aside">${studioAsideHtml()}</aside>
        </div>`
      : `<div class="workbench cnt-bench">
          <section class="card cnt-studio" data-ref="studio">${sheetStudioHtml()}</section>
          <aside class="stack cnt-aside" data-ref="aside">${sheetAsideHtml()}</aside>
        </div>`;
    if (S.camp) { scrollMessages(); updateComposer(); }
  }

  /* ───────── 1단계: 기본 정보 시트 ───────── */

  function sheetStudioHtml() {
    const s = S.sheet;
    return `<div class="cnt-studio-bar"><div class="flex">${icon('spark')}<strong>새 콘텐츠 만들기</strong></div>${badge('STEP 01 / 03')}</div>
      <div class="cnt-api" data-ref="api">${apiStatusHtml()}</div>
      <div class="cnt-studio-body">
        <div class="cnt-welcome"><div class="cnt-spark" aria-hidden="true">✳</div><h2>어떤 이야기를 만들어볼까요?</h2>
          <p>콘텐츠와 채널, 제작 대상만 고르면 시작할 수 있어요. 나머지는 대화하면서 정해요.</p></div>
        ${sheetFormHtml(s, 'new')}
      </div>
      <div class="cnt-sheet-footer" data-ref="sheet-footer">${sheetFooterHtml()}</div>`;
  }

  function sheetFooterHtml() {
    const s = S.sheet;
    const missing = sheetMissing(s);
    const summary = missing.length ? `${missing.join(' · ')}을(를) 골라 주세요.`
      : `${typeLabel(s.content_type)} · ${s.channels.map(channelLabel).join(', ')} · ${targetLabel(s)} · ${s.language || '영어'}${s.goal ? ' · ' + s.goal : ''}`;
    return `<div class="cnt-sheet-summary"><span class="panel-note">${missing.length ? '필수 3개' : '선택한 정보'}</span><span class="cnt-summary-text">${esc(summary)}</span>
        <span class="panel-note">미정인 내용은 AI와 함께 정해요.</span></div>
      <div class="flex wrap cnt-start-btns">
        <button type="button" class="btn" data-act="start-quick" ${missing.length || !textReady() ? 'disabled' : ''} title="${textReady() ? '질문 없이 지금 정보로 초안 3안을 바로 만들어요' : 'OpenAI 키가 필요해요'}">${icon('spark')}질문 없이 바로 3안</button>
        <button type="button" class="btn lime" data-act="start" ${missing.length ? 'disabled' : ''}>이 정보로 시작${icon('arrow')}</button>
      </div>`;
  }

  function sheetFormHtml(s, ctx) {
    const t = typeOf(s.content_type);
    const goalIsPreset = DATA.goals.includes(s.goal);
    const seasons = upcomingSeasons();
    const isNew = ctx === 'new';  // 콘텐츠 언어는 새 시트에서만 · 시작 뒤에는 대화에서 바꿔요
    const num = n => '0' + (isNew ? n + 1 : n);
    return `<div class="cnt-sheet ${ctx === 'edit' ? 'compact' : ''}" data-sheet="${ctx}">
      <div class="field-label cnt-q">01 &nbsp; 만들 콘텐츠 <span class="cnt-req">필수</span></div>
      <div class="choice-grid cnt-types">${DATA.contentTypes.map(x => `<button type="button" class="choice" data-act="sheet-type" data-value="${esc(x.id)}" aria-pressed="${s.content_type === x.id}">${icon(x.icon)}<span>${esc(x.label)}</span>${s.content_type === x.id ? '<span class="cnt-check" aria-hidden="true">✓</span>' : ''}</button>`).join('')}</div>
      ${t ? `<p class="hint">${esc(t.hint)}</p>` : ''}
      <div class="field-label cnt-q">02 &nbsp; 게시 채널 <span class="cnt-req">필수 · 하나</span><span class="cnt-opt">CRM 메시지 ↔ CRM 채널끼리만</span></div>
      <div class="chips">${DATA.channels.map(c => `<button type="button" class="chip" data-act="sheet-channel" data-value="${esc(c.id)}" aria-pressed="${s.channels.includes(c.id)}">${esc(c.label)}</button>`).join('')}</div>
      <div class="field-label cnt-q">03 &nbsp; 제작 대상 <span class="cnt-req">필수 · 하나 이상</span><span class="cnt-opt">제품은 여러 개 선택 가능</span></div>
      <div class="cnt-targets">
        <label class="cnt-target${s.brand_focus ? ' on' : ''}"><input type="checkbox" data-sheet-check="brand" ${s.brand_focus ? 'checked' : ''}>
          <span><b>Reboot 브랜드 자체</b><small>브랜드가 주제 · 제품 자동 선택 아님</small></span></label>
        ${products().map(p => { const on = s.product_ids.includes(p.id); return `<label class="cnt-target${on ? ' on' : ''}"><input type="checkbox" data-sheet-check="product" value="${esc(p.id)}" ${on ? 'checked' : ''}>
          <span><b>${esc(p.ko || p.name)}</b><small>${esc(p.name)} · ${esc(productForm(p))}</small></span></label>`; }).join('')}
      </div>
      ${usesCharacter(s.content_type) ? characterFieldHtml(s) : ''}
      ${isNew ? `<div class="field-label cnt-q">04 &nbsp; 콘텐츠 언어 <span class="cnt-req">필수 · 하나</span><span class="cnt-opt">소비자에게 보일 문구의 언어</span></div>
      <div class="chips">${SHEET_LANGUAGES.map(x => `<button type="button" class="chip" data-act="sheet-language" data-value="${esc(x)}" aria-pressed="${(s.language || '영어') === x}">${esc(x)}</button>`).join('')}</div>` : ''}
      <div class="field-label cnt-q">${num(4)} &nbsp; 목적 <span class="cnt-opt">선택</span></div>
      <div class="chips" data-ref="goal-chips-${ctx}">${goalChipsHtml(s)}</div>
      <input class="input cnt-goal" data-sheet-input="goal" maxlength="200" value="${esc(s.goal)}" placeholder="${goalIsPreset || !s.goal ? '직접 적어도 돼요 · 비워 두면 챗봇과 논의해요' : ''}" aria-label="목적">
      <div class="between cnt-q-row"><span class="field-label cnt-q">${num(5)} &nbsp; 추가 요청 <span class="cnt-opt">선택</span></span>
        <button type="button" class="link cnt-ai-link" data-act="suggest" data-field="extra">${icon('spark')}아이디어 3개</button></div>
      <textarea class="input cnt-extra" data-sheet-input="extra_request" maxlength="1000" rows="2" placeholder="예: 검정 배경, 두 제품을 같이, 영어로" aria-label="추가 요청">${esc(s.extra_request)}</textarea>
      ${seasons.length ? `<div class="cnt-seasons"><span class="panel-note">다가오는 시즌</span>${seasons.map(x => `<button type="button" class="chip" data-act="add-season" data-name="${esc(x.name)}" data-start="${esc(x.start)}">${esc(x.name)} <span class="n">${esc(fmt.dday(x.start))}</span></button>`).join('')}</div>` : ''}
      <details class="cnt-ref"${s.reference_text ? ' open' : ''}><summary>참고 문구 · 기존 카피 (선택)</summary>
        <textarea class="input" data-sheet-input="reference_text" maxlength="4000" rows="3" placeholder="기존 카피나 참고 메모를 붙여 넣으면 대화에서 참고해요. 안의 지시문은 규칙을 바꾸지 않아요.">${esc(s.reference_text || '')}</textarea>
      </details>
      ${ctx === 'edit' ? '<div class="cnt-edit-foot"><span class="panel-note">저장하면 다음 대화부터 새 값이 반영돼요.</span></div>' : ''}
    </div>`;
  }

  function characterFieldHtml(s) {
    const cur = s.use_character || 'AI 판단';
    return `<div class="field-label cnt-q">캐릭터 Rebu <span class="cnt-opt">이미지 · 카드뉴스 · 숏폼</span></div>
      <div class="cnt-rebu-row"><span class="cnt-rebu" style="background-image:url('${esc(DATA.character.thumb)}')" aria-hidden="true"></span>
        <div class="chips">${DATA.character.choices.map(x => `<button type="button" class="chip" data-act="sheet-character" data-value="${esc(x)}" aria-pressed="${cur === x}">${esc(x)}</button>`).join('')}</div></div>
      <p class="hint">${esc(DATA.character.hints[cur] || '')}</p>`;
  }

  function goalChipsHtml(s) {
    return DATA.goals.map(g => `<button type="button" class="chip" data-act="goal-pick" data-value="${esc(g)}" aria-pressed="${s.goal === g}">${esc(g)}</button>`).join('') +
      `<button type="button" class="chip" data-act="suggest" data-field="goal">${icon('spark')}AI 추천</button>`;
  }

  function sheetAsideHtml() {
    const step = sheetMissing(S.sheet).length ? 0 : 1;
    return `<section class="card"><div class="card-head"><h2>이렇게 만들어져요</h2></div>
        ${DATA.workflow.map((t, i) => `<div class="workflow-step ${i === step ? 'current' : i < step ? 'done' : ''}"><span class="stepnum">${i < step ? '✓' : '0' + (i + 1)}</span>${esc(t)}</div>`).join('')}
        <div class="empty-art cnt-empty">${icon('image')}<span>완성된 콘텐츠가 여기에 표시됩니다.</span></div>
        <p class="panel-note mt-8">AI 결과는 참고용 초안이에요. 게시 전 담당자가 확인합니다.</p>
      </section>`;
  }

  /* 시트 입력 처리 (새 시트 · 수정 시트 공통) */
  function sheetCtx(el) {
    const box = el.closest('[data-sheet]');
    if (!box) return null;
    const ctx = box.dataset.sheet;
    return { ctx, box, s: ctx === 'edit' ? S.edit : S.sheet };
  }

  // 다시 그려도 키보드 초점이 같은 버튼·체크박스에 남게
  function rerenderKeepFocus(container, html) {
    const a = global.document.activeElement;
    let sel = null;
    if (a && container.contains(a)) {
      const v = s => (global.CSS && global.CSS.escape ? global.CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'));
      if (a.dataset.act) sel = `[data-act="${v(a.dataset.act)}"]` + (a.dataset.value ? `[data-value="${v(a.dataset.value)}"]` : '') + (a.dataset.field ? `[data-field="${v(a.dataset.field)}"]` : '');
      else if (a.dataset.sheetCheck) sel = `[data-sheet-check="${v(a.dataset.sheetCheck)}"]` + (a.dataset.sheetCheck === 'product' ? `[value="${v(a.value)}"]` : '');
    }
    container.innerHTML = html;
    if (sel) { const el = container.querySelector(sel); if (el) el.focus(); }
  }

  function afterSheetChange(ctx, rerenderForm) {
    if (ctx === 'new') {
      lsSet('sheet', S.sheet);
      if (rerenderForm) {
        const studio = ref('studio');
        if (studio) {
          const body = studio.querySelector('.cnt-studio-body');
          const y = body ? body.scrollTop : 0;
          rerenderKeepFocus(studio, sheetStudioHtml());
          const b = studio.querySelector('.cnt-studio-body');
          if (b) b.scrollTop = y;
        }
      } else {
        const f = ref('sheet-footer');
        if (f) f.innerHTML = sheetFooterHtml();
        const chips = ref('goal-chips-new');
        if (chips) chips.innerHTML = goalChipsHtml(S.sheet);
      }
      const aside = ref('aside');
      if (aside && !S.camp) aside.innerHTML = sheetAsideHtml();
    } else if (S.edit && S.sheetBox) {
      if (rerenderForm) rerenderKeepFocus(S.sheetBox, foldSheetInner());
      else { const chips = S.sheetBox.querySelector('[data-ref="goal-chips-edit"]'); if (chips) chips.innerHTML = goalChipsHtml(S.edit); }
    }
  }

  function onSheetClick(act, b) {
    const c = sheetCtx(b);
    if (!c || !c.s) return false;
    const s = c.s;
    if (act === 'sheet-type') {
      // CRM 메시지 ↔ CRM 채널: 한쪽을 고르면 다른 쪽을 맞추고, 안 맞는 쪽은 비움
      s.content_type = b.dataset.value;
      if (s.content_type === DATA.crm.type) s.channels = [DATA.crm.channel];
      else if (s.channels[0] === DATA.crm.channel) s.channels = [];
    } else if (act === 'sheet-channel') {
      const id = b.dataset.value;
      s.channels = s.channels.includes(id) ? [] : [id];
      if (id === DATA.crm.channel && s.channels.length) s.content_type = DATA.crm.type;
      else if (id !== DATA.crm.channel && s.content_type === DATA.crm.type) s.content_type = '';
    } else if (act === 'sheet-character') {
      s.use_character = b.dataset.value;
    } else if (act === 'sheet-language') {
      s.language = b.dataset.value;
    } else if (act === 'goal-pick') {  // 고른 것을 다시 누르면 해제 → 비우면 챗봇과 논의
      s.goal = s.goal === b.dataset.value ? '' : b.dataset.value;
    } else if (act === 'add-season') {
      const add = `시즌: ${b.dataset.name} (${b.dataset.start})`;
      if (!s.extra_request.includes(b.dataset.name)) s.extra_request = s.extra_request ? `${s.extra_request}, ${add}` : add;
    } else return false;
    afterSheetChange(c.ctx, true);
    return true;
  }

  function onSheetInput(el) {
    const c = sheetCtx(el);
    if (!c || !c.s) return;
    if (el.dataset.sheetInput) {
      c.s[el.dataset.sheetInput] = el.value;
      afterSheetChange(c.ctx, false);
    }
  }

  function onSheetCheck(el) {
    const c = sheetCtx(el);
    if (!c || !c.s) return;
    if (el.dataset.sheetCheck === 'brand') c.s.brand_focus = el.checked;
    else {
      const id = el.value;
      const set = new Set(c.s.product_ids);
      if (el.checked) set.add(id); else set.delete(id);
      c.s.product_ids = products().map(p => p.id).filter(x => set.has(x));
    }
    afterSheetChange(c.ctx, true);
  }

  // quick: 질문(인터뷰) 없이 바로 초안 — 결과 칸의 '초안 만들기'와 같은 경로 (안 수는 AI 설정의 제안 수)
  async function startCampaign(btn, quick) {
    const s = S.sheet;
    if (sheetMissing(s).length) return;
    ui.busy(btn, true);
    try {
      const body = Object.assign(sheetPayload(s), { reference_text: s.reference_text || '', settings: lsGet('settings', {}) });
      const res = await REBOOT.api(`${API}/campaigns`, body);
      S.sheet = emptySheet();
      lsDel('sheet');
      S.reviews = {};
      setCampaign(res.campaign);
      renderMain();
      loadList();
      if (quick) send('질문 없이 지금 정보로 바로 초안 만들어줘', { force: 'draft' });
      else interviewAct('start');  // 질문 카드 하나씩 · 추천 답 ①②③ (키가 없으면 예시 질문)
    } catch (e) {
      toast(e.message, 'error');
    } finally { ui.busy(btn, false); }
  }

  /* ───────── 2단계: 대화 스튜디오 ───────── */

  function setCampaign(c, opts) {
    const prev = S.camp && S.camp.id;
    S.camp = c;
    if (prev !== c.id) {
      S.edit = editCopy(c);
      S.baseOff = false;
      S.reviews = {};
    }
    setHash('campaign=' + c.id);
    if (c.jobs.some(j => ACTIVE_JOB.includes(j.status))) startPolling(); else stopPolling();
  }

  function editCopy(c) {
    const ch = c.decisions && c.decisions.use_character;
    return fitSheet(Object.assign(emptySheet(), JSON.parse(JSON.stringify(c.sheet)), { reference_text: c.referenceText || '',
      use_character: (ch && ch.value) || 'AI 판단' }));
  }

  async function openCampaign(id, opts) {
    try {
      const res = await REBOOT.api(`${API}/campaigns/${id}`);
      S.tab = 'create';
      setCampaign(res.campaign);
      renderTabs();
      renderMain();
    } catch (e) {
      if (!(opts && opts.silent)) toast(e.message, 'error');
      else { setHash(''); }
    }
  }

  function applyCampaign(c, opts) {
    setCampaign(c, opts);
    refreshStudio();
  }

  function refreshStudio() {
    if (S.tab !== 'create' || !S.camp) return;
    const studio = ref('studio');
    if (!studio || !studio.querySelector('[data-ref="messages"]')) { renderMain(); return; }
    const bar = ref('bar'); if (bar) bar.innerHTML = barHtml();
    const head = ref('head'); if (head) head.innerHTML = headHtml();
    renderMessages();
    renderQuick();
    renderRefs();
    updateComposer();
    renderAsideParts();
  }

  function renderAsideParts() {
    if (S.tab !== 'create') return;
    const aside = ref('aside');
    if (!aside) return;
    if (!S.camp) { aside.innerHTML = sheetAsideHtml(); return; }
    if (!ref('results')) { renderMain(); return; }
    if (!aside.querySelector('[data-ref="brief"]')) aside.innerHTML = studioAsideHtml();
    else renderBrief();
    renderResults();
  }

  function studioHtml() {
    return `<div class="cnt-studio-bar" data-ref="bar">${barHtml()}</div>
      <div class="cnt-api" data-ref="api">${apiStatusHtml()}</div>
      <div class="cnt-studio-head" data-ref="head">${headHtml()}</div>
      <div class="cnt-messages" data-ref="messages" aria-live="polite">${messagesHtml()}</div>
      <div class="cnt-dock">
        <div class="cnt-quick" data-ref="quick">${quickHtml()}</div>
        <div class="cnt-refs-bar" data-ref="refs">${refsHtml()}</div>
        <input type="file" data-ref="ref-file" accept="image/jpeg,image/png,image/webp" hidden>
        <div class="composer cnt-composer">
          <textarea data-ref="input" rows="2" aria-label="AI에게 보낼 메시지"></textarea>
          <div class="cnt-composer-bottom">
            <div class="flex wrap cnt-tools" data-ref="tools">${toolsHtml()}</div>
            <button type="button" class="send" data-act="send" aria-label="메시지 보내기">${icon('arrow')}</button>
          </div>
        </div>
      </div>`;
  }

  function barHtml() {
    const c = S.camp;
    return `<div class="flex cnt-bar-title">${icon('spark')}<strong title="${esc(c.title)}">${esc(c.title)}</strong>
        <button type="button" class="link" data-act="rename" aria-label="캠페인명 바꾸기 · 삭제">${icon('edit')}</button></div>
      <div class="flex cnt-bar-right">
        <span class="cnt-status ${statusTone(c.status)}" title="완성하기 → 완료 · 더 수정하기 → 제작 중 (자동)">${esc(c.status)}</span>
        <span class="cnt-saved" title="서버에 저장됨 · 리비전 ${esc(c.revision)}">${icon('check')}저장됨 · ${esc(fmt.relative(c.updatedAt))}</span>
        <button type="button" class="btn small ghost" data-act="new-campaign">${icon('plus')}새로 만들기</button>
      </div>`;
  }

  function headHtml() {
    const c = S.camp, s = c.sheet;
    const chips = [`<span class="cnt-tag strong">${esc(typeLabel(s.content_type))}</span>`]
      .concat(s.channels.map(ch => `<span class="cnt-tag">${esc(channelLabel(ch))}</span>`))
      .concat(s.brand_focus ? ['<span class="cnt-tag lime">Reboot 브랜드</span>'] : [])
      .concat(s.product_ids.map(id => { const p = productById(id); return `<span class="cnt-tag lime" title="${esc(p ? p.name : id)}">${esc(p ? p.ko || p.name : id)}</span>`; }));
    return `<div class="cnt-tags">${chips.join('')}</div><span class="faint small">r${esc(c.revision)} · 만든 사람 ${esc(c.createdBy || '—')}</span>`;
  }

  function messagesHtml() {
    const c = S.camp;
    const out = [];
    if (c.messagesOmitted) out.push(`<div class="cnt-event">${icon('clock')}이전 대화 ${esc(c.messagesOmitted)}개는 줄여서 보여요.</div>`);
    c.messages.forEach(m => out.push(messageHtml(m)));
    if (!textReady() && !S.sending) out.push(noKeyHtml());
    if (S.pending) {
      const pd = S.pending;
      if (!pd.kickoff && pd.text) out.push(`<div class="message user cnt-pending"><div class="bubble">${esc(pd.text)}</div></div>`);
      const wait = pd.interview ? (pd.kickoff ? '시트를 읽고 첫 질문을 준비하는 중…' : '답을 저장하고 다음 질문을 준비하는 중…')
        : pd.kickoff ? '시트를 읽고 첫 질문을 준비하는 중…' : pd.force === 'image' ? '이미지 작업을 등록하는 중…'
        : pd.force === 'video' ? '영상 작업을 등록하는 중…' : '생각하는 중… 초안을 쓰면 30초쯤 걸려요';
      out.push(`<div class="message"><div class="bot-symbol">✳</div><div class="cnt-bot"><div class="cnt-typing"><span></span><span></span><span></span>
        <em>${wait}</em></div></div></div>`);
    }
    return out.join('');
  }

  function noKeyHtml() {
    return `<div class="note warn cnt-nokey">${icon('settings')}<div><b>AI 대화를 쓰려면 OpenAI 키가 필요해요.</b>
      설정 › 연결에서 OPENAI_API_KEY를 넣으면 사용 가능합니다. 지금은 정한 내용을 적어 두고 초안을 직접 작성할 수 있어요.
      <div class="flex wrap mt-8"><button type="button" class="btn small" data-act="manual-version">${icon('edit')}초안 직접 작성</button>
      <button type="button" class="btn small" data-act="add-decision">${icon('plus')}정한 내용 추가</button></div></div></div>`;
  }

  function messageHtml(m) {
    const time = `<span class="faint small">${esc(fmt.datetime(m.createdAt))}</span>`;
    if (m.role === 'user') {
      const p = m.payload || {};
      const tag = p.forceAction ? `<span class="cnt-msg-tag">${{ image: '이미지 생성 버튼', video: '영상 만들기 버튼' }[p.forceAction] || '초안 만들기 버튼'}</span>` : '';
      return `<div class="message user"><div class="bubble">${esc(m.text)}</div></div>${tag ? `<div class="cnt-user-meta">${tag}</div>` : ''}`;
    }
    if (m.role === 'event') {
      const p = m.payload || {};
      const tone = p.kind === 'job_failed' ? ' error' : p.kind === 'job_done' ? ' done' : '';
      const view = p.versionId ? ` <button type="button" class="link" data-act="select-version" data-id="${esc(p.versionId)}">보기</button>` : '';
      return `<div class="cnt-event${tone}">${icon(p.kind === 'job_failed' ? 'x' : p.kind === 'job_done' ? 'image' : 'clock')}<span>${esc(m.text)}</span>${view}${time}</div>`;
    }
    const p = m.payload || {};
    if (p.interview) return interviewHtml(m, time);
    if (p.interviewDone) return interviewDoneHtml(m, time);
    return `<div class="message cnt-msg-bot"><div class="bot-symbol" aria-hidden="true">✳</div><div class="cnt-bot">
      <div class="cnt-bot-head"><strong>REBOOT AI</strong>${badge('AI', 'lime')}${time}</div>
      ${p.referenceImages ? `<div class="cnt-ref-seen">${icon('image')}레퍼런스 이미지 ${esc(p.referenceImages)}장을 보고 답했어요</div>` : ''}
      <div class="prose cnt-bot-text">${ui.markdown(m.text)}</div>
      ${resultLinesHtml(p)}
      ${(p.questions || []).length ? `<ul class="cnt-questions">${p.questions.map(q => `<li><button type="button" class="link" data-act="answer" data-q="${esc(q)}">${icon('chat')}${esc(q)}</button></li>`).join('')}</ul>` : ''}
      ${candidatesHtml(m)}
      ${chipsHtml(m)}
      ${offerHtml(m)}
    </div></div>`;
  }

  /* 합쳐진 대화: 보기 칩 · 만들기 제안 — 가장 최근 AI 답에서만 누를 수 있음 */
  function lastBot() {
    const ms = S.camp.messages;
    for (let i = ms.length - 1; i >= 0; i--) if (ms[i].role !== 'event') return ms[i].role === 'assistant' ? ms[i] : null;
    return null;
  }
  const isLatest = m => { const lb = lastBot(); return !!lb && lb.id === m.id; };

  // AI 질문의 추천 답 ①②③ — 질문 카드와 같은 모양 · 고르면 그 답을 보냄
  function chipsHtml(m) {
    const chips = (m.payload && m.payload.chips) || [];
    if (!chips.length || !isLatest(m)) return '';
    const off = S.sending || !textReady() ? 'disabled' : '';
    return `<div class="cnt-concepts">${chips.map((ch, i) => `<button type="button" class="cnt-concept cnt-iv-opt" data-act="chip" data-msg="${esc(m.id)}" data-i="${i}" title="${esc(ch.value)}" ${off}>
        <small>${CIRCLED[i] || ''}</small><strong>${esc(ch.label)}</strong>${ch.reason ? `<span>${esc(ch.reason)}</span>` : ''}</button>`).join('')}</div>
      <p class="panel-note">골라도 되고 아래 입력창에 직접 써도 돼요</p>`;
  }

  const OFFER_LABEL = { generate_image: '이미지 만들기', edit_image: '이미지 수정', generate_video: '영상 만들기', draft_copy: '카피 초안 만들기',
    draft_cardnews: '카드뉴스 만들기', draft_script: '숏폼 구성안 만들기', draft_crm: 'CRM 메시지 만들기' };

  function offerHtml(m) {
    const o = m.payload && m.payload.offer;
    if (!o) return '';
    if (!isLatest(m)) return `<p class="panel-note cnt-offer-old">${icon('spark')}만들기 제안: ${esc(o.summary || OFFER_LABEL[o.type] || '')}</p>`;
    const off = S.sending || !textReady() ? 'disabled' : '';
    return `<div class="cnt-offer">${icon('spark')}<div><b>이대로 만들까요?</b>${o.summary ? `<span>${esc(o.summary)}</span>` : ''}
      <div class="flex wrap mt-8"><button type="button" class="btn small lime" data-act="offer-make" ${off}>${icon('spark')}${esc(OFFER_LABEL[o.type] || '만들기')}</button>
      <span class="panel-note">"좋아요"라고 답해도 시작해요 · 더 고칠 점은 아래에 적어 주세요</span></div></div></div>`;
  }

  /* 레퍼런스 이미지 — 대화 AI가 보고 요약 · 이미지 생성 참고 사진으로는 안 씀 */
  function refsHtml() {
    const refs = (S.camp && S.camp.references) || [];
    if (!refs.length) return '';
    return refs.map(r => `<span class="cnt-ref-chip" title="${esc(r.summary || 'AI 요약 없음')}">
        <button type="button" class="cnt-ref-img" data-act="ref-view" data-id="${esc(r.id)}" aria-label="레퍼런스 보기"><img src="${esc(r.url)}" alt="레퍼런스 ${esc(r.id)}" loading="lazy"></button>
        <button type="button" class="cnt-ref-x" data-act="ref-del" data-id="${esc(r.id)}" aria-label="레퍼런스 지우기">✕</button></span>`).join('') +
      `<span class="panel-note">레퍼런스 ${refs.length}/${(S.meta && S.meta.references && S.meta.references.max) || 3} · 대화에서 참고해요</span>`;
  }
  function renderRefs() { const el = ref('refs'); if (el) el.innerHTML = refsHtml(); }

  // 올리기 전에 브라우저에서 긴 쪽 1024px JPEG로 줄임 (서버에 이미지 처리 패키지가 없어도 되게)
  function shrinkImage(file) {
    return new Promise((resolve, reject) => {
      const url = global.URL.createObjectURL(file);
      const img = new global.Image();
      img.onload = () => {
        const k = Math.min(1, 1024 / Math.max(img.naturalWidth, img.naturalHeight));
        const cv = global.document.createElement('canvas');
        cv.width = Math.max(16, Math.round(img.naturalWidth * k));
        cv.height = Math.max(16, Math.round(img.naturalHeight * k));
        const ctx = cv.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, cv.width, cv.height);
        ctx.drawImage(img, 0, 0, cv.width, cv.height);
        global.URL.revokeObjectURL(url);
        cv.toBlob(b => (b ? resolve(b) : reject(new Error('이미지를 줄이지 못했어요.'))), 'image/jpeg', 0.88);
      };
      img.onerror = () => { global.URL.revokeObjectURL(url); reject(new Error('이미지를 읽지 못했어요 (JPG · PNG · WebP).')); };
      img.src = url;
    });
  }

  async function uploadReference(file) {
    const c = S.camp;
    if (!c || !file) return;
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast('JPG · PNG · WebP 이미지만 올릴 수 있어요.', 'error'); return; }
    const max = (S.meta && S.meta.references && S.meta.references.max) || 3;
    if ((c.references || []).length >= max) { toast(`레퍼런스는 ${max}장까지예요. 하나를 지우고 올려 주세요.`, 'error'); return; }
    const el = ref('refs');
    if (el) el.insertAdjacentHTML('beforeend', '<span class="panel-note cnt-ref-busy"><span class="spinner"></span>올리고 AI가 보는 중…</span>');
    try {
      const fd = new global.FormData();
      fd.append('file', await shrinkImage(file), 'reference.jpg');
      const res = await REBOOT.api(`${API}/campaigns/${c.id}/references`, fd);
      applyCampaign(res.campaign);
      toast(res.warning || '레퍼런스를 올렸어요. 다음 대화에서 AI가 이 이미지를 보고 답해요.', res.warning ? 'error' : undefined);
    } catch (e) { renderRefs(); toast(e.message, 'error'); }
  }

  async function deleteReference(id) {
    const c = S.camp;
    if (!c) return;
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}/references/${id}`, undefined, { method: 'DELETE' });
      applyCampaign(res.campaign);
    } catch (e) { toast(e.message, 'error'); }
  }

  function referenceViewModal(id) {
    const r = (S.camp.references || []).find(x => String(x.id) === String(id));
    if (!r) return;
    modal({
      title: '레퍼런스 이미지',
      body: `<img class="cnt-ref-full" src="${esc(r.url)}" alt="레퍼런스">
        <p class="field-label mt-8">AI가 본 요약</p><div class="prose">${r.summary ? ui.markdown(r.summary) : '<p class="faint">요약 없음 — 다음 대화에서 AI가 이미지를 직접 봐요.</p>'}</div>
        <p class="panel-note">분위기 · 구도 · 색감만 참고하고 다른 브랜드의 로고 · 문구 · 디자인은 그대로 쓰지 않아요. 이미지 생성의 참고 사진으로는 쓰지 않아요.</p>`,
      actions: [{ label: '닫기', value: null }]
    });
  }

  function resultLinesHtml(p) {
    const lines = [];
    (p.applied || []).forEach(a => {
      lines.push(`<li class="ok">${icon('check')}반영: ${esc(a.label)} → ${esc(a.new ? shortValue(a.new.value) : '아직')} ${a.new ? statusBadge(a.new.status) : ''}</li>`);
    });
    (p.rejected || []).forEach(r => lines.push(`<li class="no">반영 안 함: ${esc(r.label || r.field)} — ${esc(r.reason)}</li>`));
    if ((p.factRequests || []).length) lines.push(`<li class="wait">${icon('shield')}제품·브랜드 사실 변경 요청 ${esc(p.factRequests.length)}건은 확인 대기로 기록했어요 (설정 › 제품 정보에서 확인).</li>`);
    const a = p.action || {};
    if (a.status === 'done' && (a.versionIds || []).length) {
      const btns = a.versionIds.map((id, i) => `<button type="button" class="link" data-act="select-version" data-id="${esc(id)}">${esc((a.versionLabels || [])[i] || 'v' + id)}</button>`).join(' · ');
      lines.push(`<li class="ok">${icon('doc')}${a.type === 'select_version' ? '선택한 버전' : a.mode === 'edit' ? '수정본' : '초안'}: ${btns}</li>`);
      if (String(a.type || '').startsWith('draft_')) lines.push(`<li class="info">${videoButtonHtml('btn small dark')}</li>`);
    } else if (a.status === 'queued') {
      lines.push(`<li class="run"><span class="spinner"></span>${a.type === 'generate_video' ? '영상' : '이미지'} 작업을 등록했어요 — 진행 상황은 아래 '진행 과정 & 결과'에서 볼 수 있어요.</li>`);
    }
    if (a.note) lines.push(`<li class="${a.status === 'failed' ? 'no' : 'info'}">${esc(a.note)}</li>`);
    return lines.length ? `<ul class="cnt-results">${lines.join('')}</ul>` : '';
  }

  function candidatesHtml(m) {
    const cands = (m.payload && m.payload.candidates) || [];
    if (!cands.length) return '';
    return `<div class="cnt-concepts">${cands.map((cd, i) => {
      const chosen = isChosen(cd.field, cd.value);
      return `<button type="button" class="cnt-concept${chosen ? ' selected' : ''}" data-act="pick-candidate" data-msg="${esc(m.id)}" data-i="${i}" aria-pressed="${chosen}">
        <small>제안 0${i + 1} · ${esc(fieldLabel(cd.field))}</small><strong>${esc(cd.label)}</strong><span>${esc(cd.reason)}</span>${chosen ? '<em>✓ 선택됨 · 다시 누르면 취소</em>' : ''}</button>`;
    }).join('')}</div><p class="panel-note">카드를 고르면 '정함'으로 저장돼요. 고른 카드를 다시 누르면 선택이 취소돼요.</p>`;
  }

  /* ───────── 인터뷰: 질문 하나 · 보기 ①②③ · 직접 입력 (개수는 AI 판단 · 윤곽이 잡히면 만들기 제안) ───────── */

  function interviewHtml(m, time) {
    const iv = m.payload.interview;
    const open = iv.status === 'open';
    const custom = iv.answer && iv.answer.option == null ? iv.answer.value : null;
    const opts = iv.options.map((o, i) => {
      const chosen = !open && isChosen(iv.field, o.value);
      return `<button type="button" class="cnt-concept cnt-iv-opt${chosen ? ' selected' : ''}" data-act="iv-pick" data-msg="${esc(m.id)}" data-i="${i}" aria-pressed="${chosen}" ${S.ivBusy ? 'disabled' : ''}>
        <small>${CIRCLED[i]}</small><strong>${esc(o.label)}</strong><span>${esc(o.reason)}</span>${chosen ? '<em>✓ 선택됨 · 다시 누르면 취소</em>' : ''}</button>`;
    }).join('');
    const foot = open
      ? `<div class="cnt-iv-custom"><input class="input" data-ref="iv-input" maxlength="300" value="${esc(S.ivDraft)}" placeholder="직접 입력해도 돼요 (Enter)" aria-label="직접 입력" ${S.ivBusy ? 'disabled' : ''}>
          <button type="button" class="btn small lime" data-act="iv-custom" ${S.ivBusy ? 'disabled' : ''}>답하기</button></div>
         <div class="flex wrap cnt-iv-links"><button type="button" class="link" data-act="iv-skip" ${S.ivBusy ? 'disabled' : ''}>이 질문 건너뛰기</button>
          <button type="button" class="link" data-act="iv-finish" ${S.ivBusy ? 'disabled' : ''}>질문 그만하고 정리하기</button></div>`
      : iv.status === 'skipped' ? '<p class="panel-note">건너뛴 질문이에요. 오른쪽 표에서 언제든 답할 수 있어요.</p>'
        : custom != null ? `<p class="cnt-iv-answer${isChosen(iv.field, custom) ? ' on' : ''}">직접 입력: ${esc(custom)}</p>` : '';
    return `<div class="message cnt-msg-bot"><div class="bot-symbol" aria-hidden="true">✳</div><div class="cnt-bot cnt-iv${open ? ' open' : ''}">
      <div class="cnt-bot-head"><strong>REBOOT AI</strong>${badge(`질문 ${iv.step}`, 'lime')}${iv.source === 'example' ? badge('예시 질문') : ''}${time}</div>
      ${iv.intro ? `<div class="prose cnt-bot-text">${ui.markdown(iv.intro)}</div>` : ''}
      ${open && iv.ready ? `<div class="cnt-iv-ready">${icon('spark')}<div><b>윤곽이 잡혔어요.</b> 지금 바로 만들어 봐도 되고, 아래 질문에 더 답하면 더 정교해져요.
        ${makeHintHtml()}</div></div>` : ''}
      <p class="cnt-iv-q"><span>${esc(iv.label)}</span>${esc(iv.question)}</p>
      <div class="cnt-concepts">${opts}</div>
      ${foot}
    </div></div>`;
  }

  function interviewDoneHtml(m, time) {
    return `<div class="message cnt-msg-bot"><div class="bot-symbol" aria-hidden="true">✳</div><div class="cnt-bot">
      <div class="cnt-bot-head"><strong>REBOOT AI</strong>${badge('정리 완료', 'green')}${time}</div>
      <div class="prose cnt-bot-text">${ui.markdown(m.text)}</div>
      ${makeHintHtml()}
    </div></div>`;
  }

  // 이미지 글자 방식: bake = AI가 그림과 어울리게 문구를 직접 그림 (기본) · none = 글자 없이 (이 브라우저에 기억)
  const imgTextMode = () => lsGet('imgText', 'bake') === 'none' ? 'none' : 'bake';
  function imgTextHtml() {
    const cur = imgTextMode();
    return `<div class="chips cnt-img-text" role="group" aria-label="이미지 글자">${[['bake', '글자 넣기'], ['none', '글자 없이']]
      .map(([k, l]) => `<button type="button" class="chip" data-act="img-text" data-value="${k}" aria-pressed="${cur === k}">${l}</button>`).join('')}
      <span class="panel-note">${cur === 'bake' ? '새 이미지에 헤드라인·슬로건을 그림과 어울리게 넣어요.' : '새 이미지는 글자 없이 만들어요.'}</span></div>`;
  }
  function imageFootNote(v, layers) {
    const img = v.image || {};
    if (img.image_text && img.image_text.length) return `AI가 문구를 이미지에 직접 그렸어요 (${esc(img.image_text.map(t => t.text).join(' / '))}). 철자는 게시 전 확인하세요.`;
    if (img.text_mode === 'none') return '글자 없이 만든 이미지예요.';
    if (img.baked) return '글자를 이미지에 합성한 버전이에요.';
    return layers.length ? '예전 방식 버전 — 문구를 텍스트 레이어로 따로 얹어 보여 줘요.' : '이미지 속 글자는 검사하지 않아요.';
  }

  // 카피 초안이 있으면 새 이미지는 그 초안 문구로 만들어짐 (서버가 가장 최근 텍스트 버전을 넘김)
  const hasDraft = () => !!(S.camp && S.camp.versions.some(v => v.kind !== 'image' && v.kind !== 'video'));
  function imageButtonLabel() {
    return `${hasDraft() ? '이 초안으로 이미지 만들기' : '이미지만 먼저 만들기'} · ${imageProviderName()}`;
  }

  // 만들기 버튼은 아래 '진행 과정 & 결과'와 대화의 '이대로 만들까요?' 카드에만 — 여기서는 안내만
  function makeHintHtml() {
    return `<p class="panel-note cnt-iv-make">${icon('spark')}만들기는 아래 '진행 과정 &amp; 결과'에서 할 수 있어요.</p>`;
  }

  function interviewQs() {
    return S.camp.messages.filter(m => m.role === 'assistant' && m.payload && m.payload.interview);
  }
  function interviewDone() {
    return S.camp.messages.some(m => m.payload && m.payload.interviewDone);
  }

  /* 브리프: 콘텐츠별 흐름 항목(서버 campaign.flow)마다 지금 값 — 순서는 권장일 뿐, 대화에서 자유롭게 채움 */
  function briefInner() {
    const c = S.camp;
    const keys = c.flow || [];
    const filled = keys.filter(k => c.decisions[k] && c.decisions[k].value).length;
    const rows = keys.map((k, i) => {
      const d = c.decisions[k];
      const val = d && d.value ? esc(d.value) : '<span class="faint">대화에서 정해요</span>';
      return `<tr class="${d && d.value ? '' : 'todo'}"><td class="cnt-brief-n">${i + 1}</td><td>${esc(decisionLabel(k))}${DATA.flowHints[k] ? `<small>${esc(DATA.flowHints[k])}</small>` : ''}</td>
        <td>${val}${d && d.value ? ' ' + statusBadge(d.status) : ''}</td>
        <td><button type="button" class="link" data-act="brief-say" data-key="${esc(k)}">${d && d.value ? '고치기' : '말하기'}</button></td></tr>`;
    }).join('');
    const ivOpen = interviewQs().some(m => m.payload.interview.status === 'open');
    // 만들기 버튼은 대화의 '이대로 만들까요?' 카드와 아래 '진행 과정 & 결과'에만 둠
    return `<div class="card-head"><h2>기획 메모</h2><span class="flex">${badge(`${filled}/${keys.length}`, filled === keys.length && keys.length ? 'green' : 'lime')}
        <button type="button" class="link" data-act="sheet-edit">${icon('edit')}기본 정보 시트 수정</button></span></div>
      <p class="panel-note">${esc(typeLabel(c.sheet.content_type))} · ${esc(c.sheet.channels.map(channelLabel).join(', '))} 기준으로 AI가 질문하며 채워요.</p>
      <table class="cnt-brief"><thead><tr><th>#</th><th>항목</th><th>지금 값</th><th></th></tr></thead><tbody>${rows}</tbody></table>
      ${makeHintHtml()}
      ${ivOpen ? '<p class="panel-note">왼쪽 질문 카드에서 ①②③을 고르거나 직접 입력하면 다음 질문으로 넘어가요.</p>' : ''}
      <p class="panel-note">값은 정한 내용으로 저장돼요. 고치면 다음 생성부터 반영돼요.</p>`;
  }

  function briefSay(key) {
    const inp = ref('input');
    if (!inp || inp.disabled) return;
    const d = S.camp.decisions[key];
    const text = d && d.value ? `${decisionLabel(key)}을(를) [새 내용]으로 바꿔줘` : `${decisionLabel(key)}은(는) [원하는 내용]으로 하고 싶어`;
    inp.value = text;
    selectRange(inp, text);
  }
  function renderBrief() { const el = ref('brief'); if (el && S.camp) el.innerHTML = briefInner(); }

  async function interviewAct(action, body, shown) {
    const c = S.camp;
    if (!c || S.ivBusy || S.sending) return;
    S.ivBusy = true;
    S.pending = { interview: true, kickoff: action === 'start', text: shown || '' };
    renderMessages();
    renderBrief();
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}/interview`,
        Object.assign({ action, expected_revision: c.revision, client_request_id: rid() }, body || {}));
      if (action === 'answer') S.ivDraft = '';
      S.ivBusy = false; S.pending = null;
      applyCampaign(res.campaign, { applied: res.applied || [] });
    } catch (e) {
      S.ivBusy = false; S.pending = null;
      if (e.status === 409 && e.data && e.data.campaign) applyCampaign(e.data.campaign);
      else { renderMessages(); renderBrief(); }
      toast(e.message, 'error');
    }
    const inp = ref('iv-input');
    if (inp) inp.focus();
  }

  function ivPick(b) {
    const m = S.camp.messages.find(x => String(x.id) === b.dataset.msg);
    const iv = m && m.payload && m.payload.interview;
    const o = iv && iv.options[+b.dataset.i];
    if (!o) return;
    if (iv.status === 'open') interviewAct('answer', { option: +b.dataset.i }, `${CIRCLED[+b.dataset.i]} ${o.label}`);
    else toggleChoice(iv.field, o.value, m.id);  // 지난 질문: 후보 카드처럼 고르기 · 다시 누르면 취소
  }

  function ivCustom() {
    const v = S.ivDraft.trim();
    if (!v) { toast('보기를 고르거나 직접 입력하세요.', 'error'); return; }
    interviewAct('answer', { value: v }, v);
  }

  function briefEditModal(m) {
    const iv = m.payload.interview;
    const cur = currentValue(iv.field);
    const isOpt = cur && iv.options.some(o => o.value === cur.value);
    let value = null;
    modal({
      title: `${iv.label} 답 고치기`,
      body: `<p>${esc(iv.question)}</p>
        <div class="cnt-suggest-list">${iv.options.map((o, i) => `<button type="button" class="cnt-concept${cur && cur.value === o.value ? ' selected' : ''}" data-pick="${i}"><small>${CIRCLED[i]}</small><strong>${esc(o.label)}</strong><span>${esc(o.reason)}</span></button>`).join('')}</div>
        <label class="field mt-8"><span>직접 입력</span><input data-m="value" maxlength="300" value="${esc(cur && !isOpt ? cur.value : '')}"></label>`,
      actions: [{ label: '취소', value: null }].concat(cur ? [{ label: '아직 안 정함으로', value: 'clear' }] : []).concat([{
        label: '저장', value: 'save', tone: 'lime',
        onClick: el => {
          const typed = el.querySelector('[data-m="value"]').value.trim();
          if (typed) value = typed;
          if (!value) { toast('보기를 고르거나 직접 입력하세요.', 'error'); return false; }
          return true;
        }
      }]),
      onOpen: el => {
        el.addEventListener('click', e => {
          const b = e.target.closest('[data-pick]');
          if (!b) return;
          value = iv.options[+b.dataset.pick].value;
          el.querySelectorAll('[data-pick]').forEach(x => x.classList.toggle('selected', x === b));
          el.querySelector('[data-m="value"]').value = '';
        });
      }
    }).then(v => {
      if (v === 'save' && value) changeDecisions([{ field: iv.field, value, status: 'confirmed' }], 'manual', m.id);
      else if (v === 'clear' && cur) {
        changeDecisions([iv.field.startsWith('sheet.') ? { field: iv.field, value: '', status: 'confirmed' }
          : { field: iv.field, value: null, status: 'undecided' }], 'manual', m.id);
      }
    });
  }

  /* 후보·보기 선택 상태 (지금 저장된 값과 같으면 선택됨) */
  function currentValue(field) {
    const c = S.camp;
    if (field.startsWith('decisions.')) return c.decisions[field.slice(10)] || null;
    const v = c.sheet[field.slice(6)];
    return v ? { value: v, status: 'confirmed' } : null;
  }
  function isChosen(field, value) {
    const cur = currentValue(field);
    return !!(cur && cur.value === value && cur.status === 'confirmed');
  }
  // 고르기 전 값으로: 이 값을 넣은 가장 최근 변경의 이전 값 (없으면 미정 · 시트 항목은 빈 값)
  function revertChange(field, value) {
    const ev = (S.camp.recentChanges || []).find(e => e.field === field && e.new && e.new.value === value);
    const old = ev && ev.old && ev.old.value !== value ? ev.old : null;
    if (field.startsWith('sheet.')) return { field, value: old ? old.value : '', status: 'confirmed' };
    return old ? { field, value: old.value, status: old.status || 'confirmed' } : { field, value: null, status: 'undecided' };
  }
  function toggleChoice(field, value, messageId) {
    if (isChosen(field, value)) changeDecisions([revertChange(field, value)], 'undo', messageId);
    else changeDecisions([{ field, value, status: 'confirmed' }], 'candidate', messageId);
  }

  function renderMessages() {
    const el = ref('messages');
    if (!el || !S.camp) return;
    const typing = global.document.activeElement && global.document.activeElement.dataset.ref === 'iv-input';
    el.innerHTML = messagesHtml();
    scrollMessages();
    const inp = typing && ref('iv-input');
    if (inp && !inp.disabled) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
  }
  function scrollMessages() {
    const el = ref('messages');
    if (el) el.scrollTop = el.scrollHeight;
  }

  function quickHtml() {
    const ct = S.camp ? S.camp.sheet.content_type : '';
    const set = ct === 'image' ? DATA.quick.image : DATA.quick.text;
    const persona = S.personas && S.personas.length ? `<button type="button" class="chip" data-act="persona">${icon('users')}타깃 참고</button>` : '';
    return `<div class="chips">${set.map((q, i) => `<button type="button" class="chip" data-act="quick" data-i="${i}">${esc(q.label)}</button>`).join('')}${persona}</div>`;
  }
  function renderQuick() { const el = ref('quick'); if (el) el.innerHTML = quickHtml(); }

  function currentBase() {
    const c = S.camp;
    if (!c || S.baseOff || !c.selectedVersionId) return null;
    return c.versions.find(v => v.id === c.selectedVersionId) || null;
  }

  function toolsHtml() {
    const base = currentBase();
    const baseChip = base ? `<span class="cnt-base" title="수정 요청은 이 안을 고쳐요">고치는 중: ${esc(base.label)} · v${esc(base.seq)}
      <button type="button" data-act="base-off" aria-label="고치는 안 해제">✕</button></span>` : '';
    const mode = textReady() ? `AI 대화 · ${esc(textModel())}` : '키 없음 · 직접 작성 모드';
    return `${baseChip}<button type="button" class="cnt-tool" data-act="ref-upload" title="JPG · PNG · WebP · 최대 3장 · AI가 보고 대화에 참고해요">${icon('image')}레퍼런스 이미지</button>
      <button type="button" class="cnt-tool" data-act="reference">${icon('plus')}참고 문구</button>
      <button type="button" class="cnt-tool" data-act="ai-settings">${icon('settings')}설정</button><span class="cnt-mode">${mode}</span>`;
  }

  function updateComposer() {
    const input = ref('input');
    if (!input || !S.camp) return;
    const ok = textReady();
    input.disabled = !ok || S.sending;
    input.placeholder = ok ? '원하는 방향이나 수정할 내용을 편하게 말해주세요. (Enter 보내기 · Shift+Enter 줄바꿈)'
      : 'OpenAI 키가 없어 AI 대화를 쓸 수 없어요 — 설정 › 연결에서 키를 넣으면 사용 가능';
    const send = S.root.querySelector('[data-act="send"]');
    if (send) send.disabled = !ok || S.sending;
    const tools = ref('tools');
    if (tools) tools.innerHTML = toolsHtml();
  }

  async function send(text, opts) {
    opts = opts || {};
    const c = S.camp;
    if (!c || S.sending || S.ivBusy) return;
    text = String(text || '').trim();
    if (!text && !opts.kickoff) return;
    if (!textReady()) { toast('OpenAI 키가 없어 AI 대화를 쓸 수 없어요. 설정 › 연결에서 키를 넣어 주세요.', 'error'); return; }
    if (!opts.kickoff && (!S.retryId || S.retryText !== text)) { S.retryId = rid(); S.retryText = text; }
    const base = opts.noBase ? null : currentBase();
    const body = { message: text, expected_revision: c.revision, base_version_id: base ? base.id : null };
    if (!opts.kickoff) body.client_request_id = S.retryId;
    if (opts.force) body.force_action = opts.force;
    if (opts.kickoff) body.kickoff = true;
    body.image_text = imgTextMode();
    const input = ref('input');
    S.sending = true;
    S.pending = opts.kickoff ? { kickoff: true } : { text, force: opts.force };
    if (input && !opts.kickoff && input.value.trim() === text) input.value = '';
    renderMessages();
    renderResults();
    updateComposer();
    let restore = '';
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}/chat`, body);
      S.retryId = null; S.retryText = '';
      S.sending = false; S.pending = null;
      applyCampaign(res.campaign, { applied: (res.reply && res.reply.payload && res.reply.payload.applied) || [] });
      if (res.job) startPolling();
      loadList();
    } catch (e) {
      S.sending = false; S.pending = null;
      if (e.status === 409 && e.data && e.data.campaign) {
        applyCampaign(e.data.campaign);
        if (e.data.duplicate) S.retryId = null; else { restore = text; S.retryId = null; }
      } else {
        restore = text;
        if (e.status === 503 && S.meta) S.meta.textReady = false;
      }
      toast(e.message, 'error');
    } finally {
      S.sending = false; S.pending = null;
      renderMessages();
      renderResults();
      updateComposer();
      const inp = ref('input');
      if (inp && restore && !inp.value.trim()) { inp.value = restore; }
      if (inp && !inp.disabled) inp.focus();
    }
  }

  /* ───────── 오른쪽: 브리프 · 진행 과정 & 결과 ───────── */

  function studioAsideHtml() {
    return `<section class="card cnt-brief-card" data-ref="brief">${briefInner()}</section>`;
    // 기본 정보 시트 수정 · AI 설정 · 제품 근거는 버튼(모달)으로
  }

  function renderResults() { const el = ref('results'); if (el && S.camp) el.innerHTML = resultsInner(); }
  function renderSheetEdit() { if (S.sheetBox && S.camp) S.sheetBox.innerHTML = foldSheetInner(); }

  function selectedVersion() {
    const c = S.camp;
    if (!c || !c.versions.length) return null;
    return c.versions.find(v => v.id === c.selectedVersionId) || c.versions[0];
  }

  function resultsInner() {
    const c = S.camp;
    const vs = c.versions;
    const sel = selectedVersion();
    const hasDec = Object.values(c.decisions).some(d => d.status === 'confirmed');
    const finalV = vs.find(v => v.id === c.finalVersionId) || null;
    const complete = c.status === '완료' && !!finalV;
    const steps = [
      ['기획 메모 정리', '완료'],
      ['방향 정하기', hasDec || vs.length ? '완료' : '진행 중'],
      ['초안 · 시안', vs.length ? '완료' : hasDec ? '진행 중' : '대기'],
      ['검토 · 최종본', complete ? '완성' : c.finalVersionId ? '완료' : vs.length ? '진행 중' : '대기']
    ];
    const jobs = c.jobs.filter(j => ACTIVE_JOB.includes(j.status) || j.status === 'failed' || j.status === 'submission_unknown').slice(0, 3);
    const canImage = ['image', 'cardnews'].includes(c.sheet.content_type);
    const ready = textReady();
    const draftBusy = !!(S.sending && S.pending && S.pending.force === 'draft');
    const head = sel ? (sel.stale ? badge('변경 사항 미반영', 'orange') : badge('최신 결정 반영', 'green')) : badge(vs.length ? '결과 있음' : '방향 정하는 중', 'green');
    return `<div class="card-head"><h2>진행 과정 & 결과</h2>${head}</div>
      <div class="cnt-steps">${steps.map(([t, st], i) => { const ok = st === '완료' || st === '완성'; return `<div class="workflow-step ${st === '진행 중' ? 'current' : ok ? 'done' : ''}"><span class="stepnum">${ok ? '✓' : i + 1}</span>${esc(t)}<span class="cnt-step-st">${esc(st)}</span></div>`; }).join('')}</div>
      ${complete ? completeHtml(finalV) : ''}
      <div class="cnt-results-grid">
        <div class="cnt-results-main">
          ${sel ? previewHtml(sel) : `<div class="empty-art cnt-empty">${icon(canImage ? 'image' : 'doc')}<span>아직 결과물이 없어요.<br>대화에서 "만들어줘"라고 하거나 오른쪽 버튼을 눌러 보세요.</span></div>`}
        </div>
        <div class="cnt-results-side">
          ${jobs.map(jobHtml).join('')}
          <div class="flex wrap cnt-make">
            <button type="button" class="btn small ${canImage && hasDraft() ? '' : 'lime'}" data-act="make-draft" ${!ready ? 'disabled title="OpenAI 키가 필요해요"' : S.sending ? 'disabled' : ''}>${icon('spark')}초안 만들기</button>
            ${draftBusy ? '<span class="cnt-typing cnt-make-busy" role="status"><span></span><span></span><span></span><em>만드는 중…</em></span>' : ''}
            ${canImage ? `<button type="button" class="btn small ${hasDraft() ? 'lime' : 'dark'}" data-act="make-image" ${ready ? '' : 'disabled title="OpenAI 키가 필요해요"'}>${icon('image')}${esc(imageButtonLabel())}</button>` : ''}
            ${vs.some(v => v.kind !== 'image' && v.kind !== 'video') ? videoButtonHtml('btn small dark') : ''}
            <button type="button" class="btn small" data-act="manual-version">${icon('edit')}직접 작성</button>
          </div>
          ${canImage ? imgTextHtml() : ''}
          ${sel && (!complete || sel.id !== c.finalVersionId) ? `<button type="button" class="btn small lime cnt-complete-btn" data-act="complete">${icon('check')}${complete ? '이 버전으로 완성하기' : '완성하기'} · ${esc(sel.label)} v${esc(sel.seq)}</button>
            <p class="panel-note">보고 있는 버전을 최종본으로 정하고 상태를 '완료'로 바꿔요. 나중에 더 수정할 수 있어요.</p>` : ''}
          ${vs.length ? `<div class="cnt-vhead"><span class="field-label">버전 ${esc(vs.length)}개</span><button type="button" class="link" data-act="export">${icon('download')}전체 내보내기 (ZIP)</button></div>
            <div class="cnt-versions">${vs.map(v => versionRowHtml(v, sel)).join('')}</div>` : '<p class="panel-note">만든 결과물은 버전으로 여기에 쌓여요.</p>'}
        </div>
      </div>`;
  }

  /* 완성: 최종본 + 상태 '완료' → 저장 · 더 수정하기 · 새 콘텐츠 */
  function completeHtml(v) {
    const isImg = v.kind === 'image' && v.imageUrl;
    return `<div class="cnt-done">
      <div class="cnt-done-msg">${icon('check')}<div><b>완성됐어요 — ${esc(v.label)} · v${esc(v.seq)}</b>
        <small>최종본은 팀이 고른 버전이라는 뜻이에요. 법적 승인이나 사실 확인 완료가 아니에요.</small></div></div>
      <div class="flex wrap cnt-done-btns">
        ${isImg ? `<button type="button" class="btn small lime" data-act="save-image" data-id="${esc(v.id)}">${icon('download')}이미지 저장하기</button>`
          : v.videoUrl ? `<a class="btn small lime" href="${esc(v.videoUrl)}" download>${icon('download')}영상 저장하기</a>`
          : `<button type="button" class="btn small lime" data-act="export-one" data-id="${esc(v.id)}">${icon('download')}파일 저장하기</button>`}
        <button type="button" class="btn small" data-act="reopen">${icon('edit')}더 수정하기</button>
        ${S.camp.sheet.content_type !== DATA.crm.type ? `<button type="button" class="btn small" data-act="copy-channel">${icon('refresh')}다른 채널로 만들기</button>` : ''}
        <button type="button" class="btn small dark" data-act="new-campaign">${icon('plus')}새 콘텐츠 만들기</button>
      </div></div>`;
  }

  // 다른 채널로 만들기: 시트 · 정한 내용을 복사한 새 캠페인 → 열자마자 그 채널에 맞는 초안 (CRM은 CRM 채널 전용이라 없음)
  function copyChannelModal() {
    const c = S.camp;
    const options = DATA.channels.filter(ch => ch.id !== DATA.crm.channel && !c.sheet.channels.includes(ch.id));
    modal({
      title: '다른 채널로 만들기',
      body: `<p>콘텐츠 · 대상 · 목적과 정한 내용(컨셉 · 스토리 · 톤 등)을 그대로 가져가요. 비율은 새 채널 기본값으로 바뀌고,
          캡션 · 멘트와 대화 · 버전은 가져가지 않아요. 지금 최종본 문구는 참고 문구로 넘겨 새 채널에 맞게 고쳐 써요.</p>
        <div class="chips">${options.map(ch => `<button type="button" class="chip" data-copy-ch="${esc(ch.id)}">${esc(ch.label)}</button>`).join('')}</div>`,
      actions: [{ label: '닫기', value: null }],
      onOpen: el => {
        el.addEventListener('click', async e => {
          const b = e.target.closest('[data-copy-ch]');
          if (!b || b.disabled) return;
          el.querySelectorAll('[data-copy-ch]').forEach(x => { x.disabled = true; });
          try {
            const res = await REBOOT.api(`${API}/campaigns/${c.id}/copy`, { channel: b.dataset.copyCh });
            const close = el.closest('.modal-backdrop') && el.closest('.modal-backdrop').querySelector('[data-modal-close]');
            if (close) close.click();
            S.reviews = {};
            setCampaign(res.campaign);
            renderMain();
            loadList();
            toast(`${channelLabel(b.dataset.copyCh)}용 캠페인을 만들었어요.`);
            if (textReady()) send('같은 컨셉으로 이 채널에 맞게 초안 만들어줘', { force: 'draft' });
          } catch (err) {
            el.querySelectorAll('[data-copy-ch]').forEach(x => { x.disabled = false; });
            toast(err.message, 'error');
          }
        });
      }
    });
  }

  async function completeCampaign(btn) {
    const c = S.camp;
    const v = selectedVersion();
    if (!c || !v) return;
    ui.busy(btn, true);
    try {
      let camp = c;
      if (camp.finalVersionId !== v.id) camp = (await REBOOT.api(`${API}/versions/${v.id}/final`, { final: true })).campaign;
      if (camp.status !== '완료') {
        camp = (await REBOOT.api(`${API}/campaigns/${c.id}`, { expected_revision: camp.revision, status: '완료' }, { method: 'PATCH' })).campaign;
      }
      applyCampaign(camp);
      loadList();
      toast('완성했어요. [더 수정하기]를 누르면 언제든 이어서 고칠 수 있어요.');
    } catch (e) { conflictOr(e); } finally { ui.busy(btn, false); }
  }

  // 더 수정하기: 상태만 '제작 중'으로 (최종본 지정은 그대로 — 새 버전을 고르면 다시 완성하기)
  async function reopenCampaign() {
    const c = S.camp;
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}`, { expected_revision: c.revision, status: '제작 중' }, { method: 'PATCH' });
      applyCampaign(res.campaign);
      loadList();
      const inp = ref('input');
      if (inp && !inp.disabled) { inp.focus(); inp.placeholder = '어떻게 더 고칠까요? 예: 배경만 더 어둡게, 헤드라인 짧게'; }
    } catch (e) { conflictOr(e); }
  }

  // 이미지 저장: 따로 둔 텍스트 레이어가 있으면 합성해서 PNG로 (원본 파일은 그대로)
  async function saveImage(v) {
    const name = `reboot-${S.camp.id}-${String(v.label).replace(/[^\w가-힣-]+/g, '')}-v${v.seq}.png`;
    const layers = v.image && !v.image.baked ? (v.image.text_layers || []) : [];
    try {
      const res = await REBOOT.api(v.imageUrl, undefined, { raw: true });
      const blob = await res.blob();
      if (!layers.length) { ui.download(name, blob); toast('이미지를 저장했어요.'); return; }
      const url = global.URL.createObjectURL(blob);
      const img = new global.Image();
      img.onload = () => {
        const out = global.document.createElement('canvas');
        drawEditor(out, img, { crop: 'original', offset: 0.5, brightness: 100, contrast: 100, saturation: 100, layers: layers.map(normLayer), sel: -1 }, 0, { text: true });
        global.URL.revokeObjectURL(url);
        out.toBlob(b => { if (b) { ui.download(name, b); toast('문구를 합성한 이미지를 저장했어요.'); } else toast('이미지를 만들지 못했어요.', 'error'); }, 'image/png');
      };
      img.onerror = () => { global.URL.revokeObjectURL(url); toast('이미지를 읽지 못했어요.', 'error'); };
      img.src = url;
    } catch (e) { toast(e.message, 'error'); }
  }

  function jobHtml(j) {
    const active = ACTIVE_JOB.includes(j.status);
    const label = DATA.jobStatus[j.status] || j.status;
    const what = j.kind === 'video' ? '영상 생성' : j.kind === 'image_edit' ? '이미지 편집' : '이미지 생성';
    return `<div class="cnt-job ${active ? 'run' : 'bad'}">${active ? '<span class="spinner"></span>' : icon('x')}
      <div><b>${what} · ${esc(label)}</b>
      <small>${j.provider === 'higgsfield' ? 'Higgsfield · ' : ''}${esc(j.model)} · ${esc(j.size)} · ${esc(fmt.relative(j.createdAt))}</small>
      ${active && j.kind === 'video' ? '<small>영상은 몇 분 걸릴 수 있어요. 페이지를 닫아도 서버에서 계속 진행돼요.</small>' : ''}
      ${j.error ? `<small class="cnt-job-err">${esc(j.error)}</small>` : ''}
      ${active && j.cancelable ? `<button type="button" class="link" data-act="cancel-job" data-job="${esc(j.id)}">취소 요청</button>` : ''}
      ${!active ? `<button type="button" class="link" data-act="retry-image" data-kind="${esc(j.kind)}">다시 요청하기</button>` : ''}</div></div>`;
  }

  function seqOf(id) {
    const v = S.camp.versions.find(x => x.id === id);
    return v ? v.seq : '?';
  }

  function versionRowHtml(v, sel) {
    return `<button type="button" class="cnt-version${sel && sel.id === v.id ? ' on' : ''}" data-act="select-version" data-id="${esc(v.id)}">
      <span class="cnt-vlabel">${esc(v.label)}</span>
      <span class="cnt-vmeta">v${esc(v.seq)} · ${esc(v.kindLabel)}${v.parentId ? ` · ← v${esc(seqOf(v.parentId))}` : ''} · ${esc(v.source === 'ai' ? 'AI' : '직접')}</span>
      <span class="cnt-vbadges">${v.isFinal ? badge('최종본', 'lime') : ''}${v.stale ? badge('미반영', 'orange') : ''}${keywordBadge(v, true)}</span></button>`;
  }

  function keywordBadge(v, mini) {
    const kw = v.review && v.review.keyword;
    if (!kw) return mini ? '' : badge('키워드 미확인');
    const st = DATA.keywordStatus[kw.status] || DATA.keywordStatus.clear;
    if (mini) return kw.status === 'clear' ? '' : badge(kw.status === 'error' ? '수정 필요' : '확인 필요', st.tone);
    return badge(st.label, st.tone);
  }

  // 게시 전 체크 한 줄 — 키워드 · 컴플라이언스(전체 · 문구별) · 확인 필요 중 가장 나쁜 결과
  // level: block(사용 불가) · revise(수정 필요) · check(확인 필요) · ok(걸린 항목 없음) · none(검사 전)
  function checkSummary(v) {
    let block = 0, revise = 0, check = 0, ran = false;
    const addVerdict = (r) => {
      if (!r || r.verdict === 'UNAVAILABLE' || r.verdict === 'ERROR') return;
      ran = true;
      const fs = r.findings || [];
      if (!fs.length && RANK[r.verdict] > 0) fs.push({ verdict: r.verdict });
      fs.forEach(f => { if (f.verdict === 'BLOCK') block++; else if (f.verdict === 'REVISE') revise++; else if (RANK[f.verdict] > 0) check++; });
    };
    addVerdict(complianceOf(v));
    cmpUnits(v).forEach((u, i) => addVerdict(S.unitReviews[`${v.id}:${i}`]));
    const kw = v.review && v.review.keyword;
    ((kw && kw.items) || []).forEach(i => { if (i.level === 'error') revise++; else check++; });
    check += (v.needsCheck || []).length;
    const level = block ? 'block' : revise ? 'revise' : check ? 'check' : ran ? 'ok' : 'none';
    return { level, block, revise, check };
  }

  function checkSummaryHtml(v, chk) {
    const label = {
      block: [`사용 불가 ${chk.block}곳`, 'red'], revise: [`수정 필요 ${chk.revise}곳`, 'orange'],
      check: [`확인 필요 ${chk.check}곳`, 'blue'], ok: ['걸린 항목 없음', 'green'], none: ['아직 검사 전', '']
    }[chk.level];
    const note = chk.level === 'ok' ? '규칙에 걸린 문구는 없어요. 사람이 확인한 뒤 게시하세요.'
      : chk.level === 'none' ? '[모두 검사]는 비용 없이 키워드 · 컴플라이언스 규칙으로 문구를 확인해요.'
      : '아래 세부 결과에서 걸린 문구와 대체 문구를 확인하세요.';
    return `<div class="cnt-check-sum lv-${chk.level}"><span class="cnt-check-title">게시 전 체크</span>${badge(label[0], label[1])}
        <button type="button" class="btn small ghost" data-act="check-all" data-id="${esc(v.id)}">${chk.level === 'none' ? '모두 검사' : '다시 검사'}</button>
        <p class="panel-note">${esc(note)}</p></div>`;
  }

  function previewHtml(v) {
    const c = S.camp;
    const img = v.kind === 'image' && v.imageUrl;
    const vid = v.kind === 'video' && v.videoUrl;
    const media = v.kind === 'image' || v.kind === 'video';
    const baked = !!(v.image && v.image.baked);  // 편집기에서 글자를 이미지에 합성함 → 겹쳐 그리지 않음
    const layers = baked ? [] : (v.image && v.image.text_layers) || [];
    const body = img
      ? `<div class="cnt-preview${S.showLayers && layers.length ? ' layers' : ''}"><img src="${esc(v.imageUrl)}" alt="${esc(v.label)} 이미지 시안" loading="lazy">
          ${layers.length ? `<div class="cnt-layer-box" aria-hidden="${!S.showLayers}">${layers.map(layerHtml).join('')}</div>` : ''}</div>
         <div class="between cnt-img-foot"><span class="panel-note">${badge('시안')} ${imageFootNote(v, layers)}</span>
          ${layers.length ? `<button type="button" class="link" data-act="toggle-layers">텍스트 레이어 ${S.showLayers ? '숨기기' : '보기'}</button>` : ''}</div>
         ${referencesHtml(v)}
         ${socialHtml(v)}
         ${imageEditHtml(v)}`
      : vid
      ? `<div class="cnt-preview cnt-video"><video src="${esc(v.videoUrl)}" controls playsinline preload="metadata" aria-label="${esc(v.label)} 영상 시안"></video></div>
         <p class="panel-note">${badge('시안')} 자막·로고는 영상에 넣지 않았어요. 제품 모양·라벨·장면은 게시 전에 직접 확인하세요.</p>
         ${v.body ? `<p class="panel-note">${esc(v.body)}</p>` : ''}
         ${referencesHtml(v)}
         ${socialHtml(v)}
         ${videoEditHtml(v)}`
      : `<div class="prose cnt-doc">${ui.markdown(v.body)}</div>`;
    const cmp = complianceOf(v);
    const kw = v.review && v.review.keyword;
    const chk = checkSummary(v);
    return `<div class="cnt-selected">
      <div class="between cnt-sel-head"><div><strong>${esc(v.label)} · v${esc(v.seq)}</strong> <span class="muted small">${esc(v.kindLabel)} · ${esc(v.source === 'ai' ? 'AI 생성' : '직접 작성')} · ${esc(fmt.datetime(v.createdAt))}</span></div>
        <div class="flex">${v.isFinal ? badge('최종본', 'lime') : ''}${v.source === 'ai' ? badge('AI · 참고용', 'purple') : ''}</div></div>
      ${v.stale ? `<p class="note warn cnt-stale">${icon('refresh')}변경 사항 미반영 — 이 버전을 만든 뒤 바뀐 항목: ${esc(v.staleFields.join(', '))}. 다시 만들면 반영돼요.</p>` : ''}
      ${v.note ? `<p class="panel-note">${esc(v.note)}</p>` : ''}
      ${v.approach && !media ? `<p class="panel-note">접근: ${esc(v.approach)}</p>` : ''}
      ${body}
      <div class="cnt-review">
        ${checkSummaryHtml(v, chk)}
        <details class="cnt-review-more"${chk.level === 'ok' || chk.level === 'none' ? '' : ' open'}><summary>세부 검사 결과</summary>
        ${(v.needsCheck || []).length ? `<div class="note cnt-check-list"><b>확인 필요</b><ul>${v.needsCheck.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
        ${unitReviewsHtml(v)}
        <div class="cnt-review-row"><span>키워드 확인</span>${keywordBadge(v, false)}</div>
        ${kw && kw.items && kw.items.length ? `<ul class="cnt-review-items">${kw.items.map(i => `<li class="${esc(i.level)}">${esc(i.label)}${i.terms && i.terms.length ? ` <code>${esc(i.terms.join(', '))}</code>` : ''}</li>`).join('')}</ul>` : ''}
        <div class="cnt-review-row"><span>컴플라이언스</span>${cmp ? badge(cmp.label || cmp.verdict, DATA.complianceTone[cmp.verdict] || '') : badge('미검토')}</div>
        ${cmp && cmp.findings && cmp.findings.length ? `<ul class="cnt-review-items">${cmp.findings.map(f => `<li class="${f.verdict === 'BLOCK' ? 'error' : 'warn'}">${esc(f.code)} ${esc(f.name)}${f.terms && f.terms.length ? ` <code>${esc(f.terms.join(', '))}</code>` : ''}${f.alt ? ` → ${esc(f.alt)}` : ''}</li>`).join('')}</ul>` : ''}
        </details>
        <p class="panel-note">${esc((v.review && v.review.videoScope) || (kw && kw.scope) || '텍스트만 확인')} · 결과는 참고용이며 법적 승인이 아니에요.</p>
      </div>
      <div class="flex wrap cnt-sel-actions">
        <button type="button" class="btn small" data-act="use-base" data-id="${esc(v.id)}">${icon('chat')}이 안 고치기</button>
        ${compareCandidates(v).length ? `<button type="button" class="btn small" data-act="compare" data-id="${esc(v.id)}">비교</button>` : ''}
        ${!media ? `<button type="button" class="btn small" data-act="manual-edit" data-id="${esc(v.id)}">${icon('edit')}직접 수정</button>
          <button type="button" class="btn small" data-act="view-version" data-id="${esc(v.id)}">크게 보기</button>
          <button type="button" class="btn small ghost" data-act="copy" data-id="${esc(v.id)}">복사</button>` : `<a class="btn small ghost" href="${esc(v.imageUrl || v.videoUrl)}" target="_blank" rel="noopener">원본 열기</a>`}
        <button type="button" class="btn small ghost" data-act="export-one" data-id="${esc(v.id)}">${icon('download')}이 버전만</button>
      </div>
      ${c.finalVersionId === v.id ? '<p class="panel-note">최종본 지정은 팀이 고른 버전이라는 뜻이에요. 법적 승인이나 사실 확인 완료가 아니에요.</p>' : ''}
    </div>`;
  }

  // 이미지·영상 생성 직후 AI가 추천한 캡션 3안 · 해시태그 (서버 media.suggest_social · 고르기 전까지 제안)
  function socialHtml(v) {
    const s = v.social;
    if (!s) return '';
    const caps = s.captions || [];
    if (!caps.length) return `<div class="cnt-social"><span class="panel-note">캡션·해시태그 추천 없음 — ${esc(s.note || '추천을 받지 못했어요.')}</span></div>`;
    const basis = s.basis === 'image' ? '완성된 이미지를 보고 추천했어요' : '영상 장면 설명을 바탕으로 추천했어요 (영상 파일 자체는 보지 않음)';
    const kw = s.review;
    const st = kw ? (DATA.keywordStatus[kw.status] || DATA.keywordStatus.clear) : null;
    const tags = (s.hashtags || []).join(' ');
    return `<div class="cnt-social">
      <div class="between"><strong>AI 추천 캡션 · 해시태그</strong>${st ? badge(st.label, st.tone) : ''}</div>
      <p class="panel-note">${badge('AI 제안')} ${esc(basis)}${s.note ? ` · ${esc(s.note)}` : ''}</p>
      <ol class="cnt-social-caps">${caps.map((c, i) => `<li>
        <div class="between"><span class="small muted">${esc(c.angle || `${i + 1}안`)}</span>
          <button type="button" class="btn small ghost" data-act="copy-text" data-text="${esc(c.text + (tags ? '\n\n' + tags : ''))}">복사</button></div>
        <p>${esc(c.text)}</p></li>`).join('')}</ol>
      ${tags ? `<div class="between cnt-social-tags"><span>${esc(tags)}</span><button type="button" class="btn small ghost" data-act="copy-text" data-text="${esc(tags)}">해시태그 복사</button></div>` : ''}
      ${kw && kw.items && kw.items.length ? `<ul class="cnt-review-items">${kw.items.map(i => `<li class="${esc(i.level)}">${esc(i.label)}${i.terms && i.terms.length ? ` <code>${esc(i.terms.join(', '))}</code>` : ''}</li>`).join('')}</ul>` : ''}
      <p class="panel-note">게시 전에 사람이 확인하세요. 키워드 확인은 참고용이며 법적 승인이 아니에요.</p>
    </div>`;
  }

  // 이미지 생성 때 함께 넣은 최종 패키지·캐릭터 사진 (assets/manifest.json의 ID)
  function referencesHtml(v) {
    const ids = ((v.image || v.video) && (v.image || v.video).references) || [];
    if (!ids.length) return '';
    return `<div class="cnt-refs"><span class="panel-note">참고한 최종 자료 ${ids.length}장</span>
      <div class="cnt-ref-thumbs">${ids.map(id => `<a href="${API}/assets/${encodeURIComponent(id)}.jpg" target="_blank" rel="noopener" title="${esc(id)}"><img src="${API}/assets/${encodeURIComponent(id)}.jpg" alt="${esc(id)}" loading="lazy"></a>`).join('')}</div></div>`;
  }

  // 편집기에서 위치를 정한 레이어는 그 자리에 (x·y·크기는 이미지 폭 대비 비율)
  function layerHtml(t) {
    if (typeof t.x !== 'number') return `<span class="cnt-layer ${esc(t.role)}">${esc(t.text)}</span>`;
    const shift = t.align === 'center' ? '-50%' : t.align === 'right' ? '-100%' : '0';
    return `<span class="cnt-layer free" style="left:${t.x * 100}%;top:${t.y * 100}%;font-size:${t.size * 100}cqw;color:${esc(t.color)};transform:translateX(${shift})">${esc(t.text)}</span>`;
  }

  /* 이미지 수정: A) AI로 수정 (기준 이미지를 넣어 편집 · 새 버전) · C) 직접 편집기 (AI 없음) */
  function imageEditHtml(v) {
    const ready = textReady();
    const off = ready ? '' : 'disabled title="OpenAI 키가 필요해요"';
    const quick = DATA.quick.image.filter(q => q.label !== '다른 시안');
    return `<div class="cnt-img-edit">
      <div class="field-label">${icon('spark')}이 이미지 수정하기</div>
      <div class="chips">${quick.map((q, i) => `<button type="button" class="chip" data-act="img-quick" data-i="${i}">${esc(q.label)}</button>`).join('')}</div>
      <div class="cnt-img-edit-row"><textarea class="input" data-ref="img-prompt" rows="2" maxlength="1000" placeholder="어떻게 고칠까요? 예: 배경만 흰색으로, 제품은 그대로" aria-label="AI 수정 요청">${esc(S.imgDraft)}</textarea>
        <button type="button" class="btn small dark" data-act="img-ai-edit" data-id="${esc(v.id)}" ${off}>${icon('spark')}AI로 수정</button></div>
      <div class="flex wrap cnt-img-edit-btns">
        <button type="button" class="btn small" data-act="img-editor" data-id="${esc(v.id)}">${icon('edit')}직접 편집 (자르기 · 색)</button>
        <button type="button" class="btn small ghost" data-act="img-new" ${off}>AI로 다른 시안</button>
      </div>
      ${imgTextHtml()}
      <p class="panel-note">문구를 바꾸려면 [문구 수정]처럼 AI에게 말해 주세요. AI 수정은 이 이미지를 기준으로 넣어 새 버전을 만들어요 (원본 유지 · 이미지 생성 비용). ${S.meta && S.meta.imageEdit === false ? '지금 이미지 모델은 편집을 지원하지 않아 새 시안으로 만들어요. ' : ''}직접 편집은 AI 없이 브라우저에서 해요.</p>
    </div>`;
  }

  function imageProviderName() {
    return S.camp && S.camp.settings && S.camp.settings.image_provider === 'higgsfield' ? 'Higgsfield' : 'OpenAI';
  }

  /* 영상 (Higgsfield Seedance 2.0) — 초안·구성안 기준으로 만들기 · 만든 영상 수정(새로 생성 · 원본 유지) */
  function hfReady() {
    return !!(S.meta && S.meta.video && S.meta.video.higgsfieldKey);
  }

  function videoButtonHtml(cls) {
    const off = !textReady() ? 'disabled title="OpenAI 키가 필요해요 (영상 프롬프트 작성)"'
      : !hfReady() ? 'disabled title="Higgsfield 키가 필요해요 (설정 › 연결)"' : '';
    return `<button type="button" class="${cls}" data-act="make-video" ${off}>${icon('video')}영상 만들기 · Higgsfield</button>`;
  }

  function videoEditHtml(v) {
    const off = textReady() && hfReady() ? '' : 'disabled title="OpenAI · Higgsfield 키가 필요해요"';
    return `<div class="cnt-img-edit">
      <div class="field-label">${icon('spark')}이 영상 수정하기</div>
      <div class="chips">${DATA.quick.video.map((q, i) => `<button type="button" class="chip" data-act="vid-quick" data-i="${i}">${esc(q.label)}</button>`).join('')}</div>
      <div class="cnt-img-edit-row"><textarea class="input" data-ref="vid-prompt" rows="2" maxlength="1000" placeholder="어떻게 고칠까요? 예: 카메라를 더 천천히, 마지막에 제품 클로즈업" aria-label="영상 수정 요청">${esc(S.vidDraft)}</textarea>
        <button type="button" class="btn small dark" data-act="vid-ai-edit" data-id="${esc(v.id)}" ${off}>${icon('spark')}AI로 다시 만들기</button></div>
      <div class="flex wrap cnt-img-edit-btns">
        <button type="button" class="btn small ghost" data-act="vid-new" ${off}>새 영상 (처음부터)</button>
      </div>
      <p class="panel-note">영상은 부분 수정이 안 돼서, 이 영상의 프롬프트에 수정 요청을 반영해 새로 만들어요. 원본 영상은 그대로 남고 새 버전으로 저장돼요 (Higgsfield 유료 · 몇 분 걸려요).</p>
    </div>`;
  }

  async function makeVideo(text, opts) {
    const ok = await ui.confirm('Higgsfield로 실제 영상을 만들어요. 720p · 4~15초 · 소리 없음 · 유료이고 몇 분 걸려요. 진행할까요?', { ok: '영상 만들기' });
    if (ok) send(text, Object.assign({ force: 'video' }, opts || {}));
  }

  async function videoAiEdit(v) {
    const text = S.vidDraft.trim();
    if (!text) { toast('어떻게 고칠지 적어 주세요. 위 버튼을 누르면 예시가 채워져요.', 'error'); return; }
    if (S.camp.selectedVersionId !== v.id) await selectVersion(v.id);
    S.baseOff = false;
    const ok = await ui.confirm('수정 요청을 반영해 영상을 새로 만들어요 (원본 유지 · Higgsfield 유료 · 몇 분). 진행할까요?', { ok: '다시 만들기' });
    if (!ok) return;
    S.vidDraft = '';
    send(text, { force: 'video' });
  }

  async function imageAiEdit(v) {
    const text = S.imgDraft.trim();
    if (!text) { toast('어떻게 고칠지 적어 주세요. 위 버튼을 누르면 예시가 채워져요.', 'error'); return; }
    if (S.camp.selectedVersionId !== v.id) await selectVersion(v.id);
    S.baseOff = false;
    S.imgDraft = '';
    send(text, { force: 'image' });
  }

  /* 직접 편집기 — 캔버스에서 자르기 · 밝기/대비/채도 → PNG를 새 버전으로 (문구는 AI 수정으로 · 예전 텍스트 레이어는 그대로 이어감) */
  const CROPS = [['original', '원본'], ['1:1', '1:1'], ['4:5', '4:5'], ['9:16', '9:16'], ['16:9', '16:9']];
  const LAYER_DEFAULT = {
    headline: { x: 0.06, y: 0.06, size: 0.07, color: '#ffffff', align: 'left' },
    slogan: { x: 0.94, y: 0.9, size: 0.045, color: '#d7ff3f', align: 'right' },
    cta: { x: 0.94, y: 0.8, size: 0.04, color: '#d7ff3f', align: 'right' },
    text: { x: 0.06, y: 0.5, size: 0.05, color: '#ffffff', align: 'left' }
  };

  function normLayer(t) {
    const role = LAYER_DEFAULT[t.role] ? t.role : 'text';
    return Object.assign({}, LAYER_DEFAULT[role], { role, text: String(t.text || '') },
      typeof t.x === 'number' ? { x: t.x, y: t.y, size: t.size, color: t.color, align: t.align } : {});
  }

  function cropRect(w, h, crop, offset) {
    if (crop === 'original') return { x: 0, y: 0, w, h };
    const [a, b] = crop.split(':').map(Number);
    const r = a / b;
    if (w / h > r) { const cw = Math.round(h * r); return { x: Math.round((w - cw) * offset), y: 0, w: cw, h }; }
    const ch = Math.round(w / r);
    return { x: 0, y: Math.round((h - ch) * offset), w, h: ch };
  }

  function adjustPixels(ctx, w, h, st) {
    if (st.brightness === 100 && st.contrast === 100 && st.saturation === 100) return;
    const d = ctx.getImageData(0, 0, w, h);
    const a = d.data;
    const br = st.brightness / 100, ct = st.contrast / 100, sat = st.saturation / 100;
    for (let i = 0; i < a.length; i += 4) {
      let r = (a[i] * br - 128) * ct + 128, g = (a[i + 1] * br - 128) * ct + 128, b = (a[i + 2] * br - 128) * ct + 128;
      const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      a[i] = l + (r - l) * sat; a[i + 1] = l + (g - l) * sat; a[i + 2] = l + (b - l) * sat;  // Uint8ClampedArray가 0~255로 자름
    }
    ctx.putImageData(d, 0, 0);
  }

  function layerFont(t, W) { return `800 ${Math.max(8, t.size * W)}px Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif`; }

  function drawEditor(canvas, img, st, maxW, opts) {
    const crop = cropRect(img.naturalWidth, img.naturalHeight, st.crop, st.offset);
    const scale = maxW ? Math.min(1, maxW / crop.w) : 1;
    canvas.width = Math.round(crop.w * scale);
    canvas.height = Math.round(crop.h * scale);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, crop.x, crop.y, crop.w, crop.h, 0, 0, canvas.width, canvas.height);
    adjustPixels(ctx, canvas.width, canvas.height, st);
    if (!opts.text) return;
    const W = canvas.width, H = canvas.height;
    st.layers.forEach(t => {
      if (!t.text) return;
      ctx.save();
      ctx.font = layerFont(t, W);
      ctx.textAlign = t.align;
      ctx.textBaseline = 'top';
      ctx.fillStyle = t.color;
      ctx.shadowColor = 'rgba(0,0,0,.35)';
      ctx.shadowBlur = Math.max(2, t.size * W * 0.15);
      ctx.fillText(t.text, t.x * W, t.y * H);
      ctx.restore();
    });
  }

  function imageEditor(v) {
    const c = S.camp;
    const baked = !!(v.image && v.image.baked);
    const layers = baked ? [] : ((v.image && v.image.text_layers) || []).map(normLayer);  // 예전 버전의 레이어는 고치지 않고 그대로 넘김
    const init = () => ({ crop: 'original', offset: 0.5, brightness: 100, contrast: 100, saturation: 100, layers, sel: -1 });
    let st = init();
    const start = JSON.stringify(st);
    const img = new global.Image();
    let save = false;
    modal({
      title: `${v.label} · v${v.seq} 직접 편집`,
      wide: true,
      body: `<div class="cnt-editor">
        <div class="cnt-ed-stage"><canvas data-e="canvas" aria-label="편집 미리보기"></canvas>
          <p class="panel-note">저장하면 새 버전이 되고 원본은 그대로 남아요. 문구는 [이 이미지 수정하기]에서 AI에게 고쳐 달라고 하세요.</p></div>
        <div class="cnt-ed-panel">
          <div class="field-label">자르기 · 비율</div>
          <div class="chips" data-e="crops">${CROPS.map(([k, l]) => `<button type="button" class="chip" data-e-crop="${k}" aria-pressed="${k === 'original'}">${l}</button>`).join('')}</div>
          <label class="field"><span>자르는 위치</span><input type="range" min="0" max="100" data-e="offset" value="50"></label>
          <div class="field-label">색 보정</div>
          <label class="field"><span>밝기</span><input type="range" min="50" max="150" data-e="brightness" value="100"></label>
          <label class="field"><span>대비</span><input type="range" min="50" max="150" data-e="contrast" value="100"></label>
          <label class="field"><span>채도</span><input type="range" min="0" max="200" data-e="saturation" value="100"></label>
          <button type="button" class="link" data-e-reset>처음 상태로</button>
        </div></div>`,
      actions: [{ label: '취소', value: null }, {
        label: '새 버전으로 저장', value: 'save', tone: 'lime',
        onClick: () => {
          if (!img.naturalWidth) { toast('이미지를 아직 불러오지 못했어요.', 'error'); return false; }
          if (JSON.stringify(st) === start) { toast('바뀐 내용이 없어요.'); return false; }
          save = true;
          return true;
        }
      }],
      onOpen: el => {
        const canvas = el.querySelector('[data-e="canvas"]');
        let frame = 0;
        const redraw = () => {
          if (frame || !img.naturalWidth) return;
          frame = global.requestAnimationFrame(() => { frame = 0; drawEditor(canvas, img, st, 720, { text: true }); });
        };
        const syncControls = () => {
          el.querySelectorAll('[data-e-crop]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.eCrop === st.crop)));
          ['offset', 'brightness', 'contrast', 'saturation'].forEach(k => { el.querySelector(`[data-e="${k}"]`).value = k === 'offset' ? st.offset * 100 : st[k]; });
        };
        img.onload = redraw;
        img.onerror = () => { canvas.replaceWith(Object.assign(global.document.createElement('p'), { className: 'note error', textContent: '이미지를 불러오지 못했어요.' })); };
        img.src = v.imageUrl;
        el.addEventListener('click', e => {
          const t = e.target;
          const crop = t.closest('[data-e-crop]');
          if (crop) { st.crop = crop.dataset.eCrop; syncControls(); redraw(); return; }
          if (t.closest('[data-e-reset]')) { st = init(); syncControls(); redraw(); }
        });
        el.addEventListener('input', e => {
          const k = e.target.dataset.e;
          if (['brightness', 'contrast', 'saturation'].includes(k)) st[k] = +e.target.value;
          else if (k === 'offset') st.offset = +e.target.value / 100;
          else return;
          redraw();
        });
      }
    }).then(r => {
      if (r !== 'save' || !save) return;
      const out = global.document.createElement('canvas');
      drawEditor(out, img, st, 0, { text: false });
      out.toBlob(async blob => {
        if (!blob) { toast('이미지를 만들지 못했어요.', 'error'); return; }
        const fd = new global.FormData();
        fd.append('file', blob, 'edit.png');
        fd.append('parent_id', String(v.id));
        fd.append('text_layers', JSON.stringify(st.layers.filter(t => t.text.trim()).map(t => ({ role: t.role, text: t.text, x: t.x, y: t.y, size: t.size, color: t.color, align: t.align }))));
        fd.append('edits', JSON.stringify({ crop: st.crop, offset: Math.round(st.offset * 100) + '%', brightness: st.brightness + '%', contrast: st.contrast + '%', saturation: st.saturation + '%' }));
        fd.append('baked', '0');
        try {
          const res = await REBOOT.api(`${API}/campaigns/${c.id}/images`, fd);
          applyCampaign(res.campaign);
          toast('편집한 이미지를 새 버전으로 저장했어요. 원본은 그대로예요.');
        } catch (e) { toast(e.message, 'error'); }
      }, 'image/png');
    });
  }

  function foldSheetInner() {
    if (!S.edit) S.edit = editCopy(S.camp);
    return sheetFormHtml(S.edit, 'edit');
  }

  function factsInner() {
    const f = S.camp.facts || {};
    const b = f.brand || {};
    const prods = f.products || [];
    const cell = (label, v, extra) => `<div class="row"><span>${esc(label)}</span><span>${v == null || v === '' ? `<em class="cnt-missing">미제공 · 확인 필요</em>` : esc(v)}${extra || ''}</span></div>`;
    return `<p class="panel-note">AI는 아래 값만 사실 근거로 써요 (기준 ${esc(f.facts_version || '—')}). 바꾸려면 설정 › 제품 정보에서 고칩니다.</p>
      <div class="cnt-fact"><b>Reboot 브랜드 ${badge('항상 포함')}</b>
        ${cell('슬로건', (b.slogans || []).join(' · ') || b.slogan)}${cell('브랜드 색', b.colors)}${cell('캐릭터', b.character)}${cell('CM송', b.cm_song)}${cell('구분', b.category)}</div>
      ${prods.length ? prods.map(p => `<div class="cnt-fact"><b>${esc(p.internal_name)} ${badge(p.evidence || '확인 필요', p.evidence === '확인 완료' ? 'green' : 'orange')}</b>
        ${cell('제형', p.form)}
        ${cell('맛', p.flavors && p.flavors.value, p.flavors && p.flavors.status === 'provisional' ? ' ' + badge('임시', 'orange') : '')}
        ${cell('포장', p.pack)}
        ${cell('개발 방향', p.development_direction, p.sugar_reduction === 'development_goal' ? ' ' + badge('개발 목표 · 저당 표현 금지', 'red') : '')}
        ${cell('성분', p.ingredients)}${cell('용량', p.volume)}${cell('외부 출시명', p.consumer_name === '미정' ? null : p.consumer_name)}
      </div>`).join('') : '<p class="panel-note">선택한 제품이 없어요 — 브랜드 정보만 근거로 써요.</p>'}`;
  }

  function aiSettingsInner(inModal) {
    const meta = S.meta || {};
    const camp = !inModal || S.camp ? S.camp : null;
    const st = camp ? camp.settings : Object.assign({}, (meta.defaults || { answer_length: 'short', candidates: 3, rewrite_max: 1, video_mode: 'plan_only', image_provider: 'openai' }), lsGet('settings', {}));
    const video = meta.video || {};
    const providersImg = meta.imageProviders || { openai: 'OpenAI', higgsfield: 'Higgsfield' };
    const modes = video.modes || { plan_only: '구성안만 (실제 영상 아님)' };
    const hfKey = !!video.higgsfieldKey;
    const opts = (map, cur) => Object.keys(map).map(k => `<option value="${esc(k)}" ${cur === k ? 'selected' : ''}>${esc(map[k])}</option>`).join('');
    const ready = textReady();
    const lengths = meta.answerLengths || { short: '짧게', normal: '보통', long: '자세히' };
    return `<div class="cnt-ai-settings">
      <div class="setting-row"><div><b>대화·제작 AI</b><p>대화 · 추천 · 기획 · 카피 · 보조 검토를 모델 하나로 해요.</p></div>
        <div class="cnt-model">${esc(textModel())} ${ready ? badge('연결됨', 'green') : badge('키 없음', 'orange')}</div></div>
      <div class="setting-row"><div><b>이미지 AI</b><p>이미지 작업 때만 써요. OpenAI · ${esc(meta.imageModel || 'gpt-image-2.5-sunburst')} 또는 Higgsfield · ${esc(meta.higgsfieldImageModel || 'marketing-studio/image')} (제품·Rebu 사진을 참고 이미지로 넣어요).</p></div>
        <select class="cnt-model" data-setting="image_provider" aria-label="이미지 AI">${opts(providersImg, st.image_provider || 'openai')}</select></div>
      <div class="setting-row"><div><b>영상 AI</b><p>실제 영상은 Higgsfield ${esc(video.model || 'Seedance 2.0')} · ${esc(video.resolution || '720p')} · 4~15초 · 소리 없음 (유료 · 명확히 요청할 때만).${hfKey ? '' : ' HIGGSFIELD_API_KEY가 없으면 구성안만 만들어요.'}</p></div>
        <select class="cnt-model" data-setting="video_mode" aria-label="영상 AI">${opts(modes, st.video_mode || 'plan_only')}</select></div>
      ${hfKey ? '' : `<p class="note warn">${icon('settings')}Higgsfield 키가 없어요. Higgsfield를 고르면 키를 넣기 전까지 이미지·영상을 만들지 않아요 (OpenAI로 자동 전환하지 않음).</p>`}
      <p class="panel-note">모델은 서버 설정이에요: 대화·제작 = CONTENT_TEXT_MODEL 또는 공통 OPENAI_MODEL(설정 › 연결) · OpenAI 이미지 = CONTENT_IMAGE_MODEL · Higgsfield = HIGGSFIELD_API_KEY.</p>
      <details class="cnt-adv"${inModal ? ' open' : ''}><summary>고급 옵션 ${camp ? '(이 캠페인)' : '(새 캠페인 기본값)'}</summary>
        <div class="field-grid three">
          <label class="field"><span>답변 길이</span><select data-setting="answer_length">${Object.keys(lengths).map(k => `<option value="${esc(k)}" ${st.answer_length === k ? 'selected' : ''}>${esc(lengths[k])}</option>`).join('')}</select></label>
          <label class="field"><span>콘셉트·카피 제안 수</span><select data-setting="candidates">${[1, 2, 3].map(n => `<option ${+st.candidates === n ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
          <label class="field"><span>텍스트 자동 재작성</span><select data-setting="rewrite_max">${[0, 1, 2].map(n => `<option value="${n}" ${+st.rewrite_max === n ? 'selected' : ''}>최대 ${n}회</option>`).join('')}</select></label>
        </div>
        <p class="hint">자동 재작성은 키워드 확인에서 '수정 필요'가 나온 텍스트에만 써요. 유료 이미지 재생성은 자동으로 하지 않아요.</p>
      </details>
    </div>`;
  }

  function readSettings(el) {
    const out = {};
    el.querySelectorAll('[data-setting]').forEach(s => { out[s.dataset.setting] = ['candidates', 'rewrite_max'].includes(s.dataset.setting) ? +s.value : s.value; });
    return out;
  }

  async function saveSettings(values) {
    if (!S.camp) {
      lsSet('settings', values);
      toast('새 캠페인 기본값으로 저장했어요 (이 브라우저).');
      return true;
    }
    try {
      const res = await REBOOT.api(`${API}/campaigns/${S.camp.id}`, { expected_revision: S.camp.revision, settings: values }, { method: 'PATCH' });
      applyCampaign(res.campaign);
      toast('AI 설정을 저장했어요.');
      return true;
    } catch (e) { conflictOr(e); return false; }
  }

  function aiSettingsModal() {
    modal({
      title: 'AI 제작 설정',
      wide: false,
      body: `<p>모델은 하나만 골라 쓰고, 이미지·영상 설정은 해당 작업 때만 펼쳐요.</p>${aiSettingsInner(true)}`,
      actions: [{ label: '닫기', value: null }, { label: '설정 저장', value: 'save', tone: 'lime', onClick: el => { pendingSettings = readSettings(el); } }]
    }).then(v => { if (v === 'save' && pendingSettings) saveSettings(pendingSettings); });
  }
  let pendingSettings = null;

  /* ───────── 버전 · 검토 · 내보내기 ───────── */

  function complianceOf(v) {
    return S.reviews[v.id] || (v.review && v.review.compliance) || null;
  }

  function complianceProducts() {
    const ids = [];
    (S.camp.sheet.product_ids || []).forEach(id => { const p = productById(id); if (p && p.compliance_id) ids.push(p.compliance_id); });
    return ids;
  }

  // 컴플라이언스 탭의 review()로 문구 하나 검사 — 채널·선택 제품 조합 중 가장 엄격한 판정
  function complianceCheck(text) {
    let api = null;
    try { api = REBOOT.use('compliance'); } catch (e) { api = null; }
    if (!api || typeof api.review !== 'function') return { verdict: 'UNAVAILABLE', label: '미연동', findings: [] };
    const channels = Array.from(new Set(S.camp.sheet.channels.map(ch => DATA.complianceChannel[ch] || 'b2c')));
    const prods = complianceProducts();
    try {
      const runs = [];
      channels.forEach(ch => (prods.length ? prods : ['']).forEach(pid => runs.push(api.review(text, pid ? { channel: ch, category: 'FOOD', product: pid } : { channel: ch, category: 'FOOD' }))));
      const byCode = {};
      let verdict = 'PASS';
      runs.forEach(r => {
        if (RANK[r.verdict] > RANK[verdict]) verdict = r.verdict;
        (r.findings || []).forEach(f => { if (!byCode[f.code] || RANK[f.verdict] > RANK[byCode[f.code].verdict]) byCode[f.code] = f; });
      });
      return { verdict, label: DATA.complianceLabels[verdict] || verdict, findings: Object.values(byCode), channel: channels.join(','), products: prods };
    } catch (e) {
      return { verdict: 'ERROR', label: '미검토 (검사 오류)', findings: [] };
    }
  }

  async function runCompliance(v) {
    const text = v.kind === 'image' ? ((v.image && v.image.text_layers) || []).map(t => t.text).join('\n') : v.body;
    if (!String(text || '').trim()) { toast('검사할 문구가 없어요 (이미지 속 글자는 검사하지 않아요).', 'error'); return; }
    const result = complianceCheck(text);
    S.reviews[v.id] = result;
    renderResults();
    try {
      const res = await REBOOT.api(`${API}/versions/${v.id}/review`, {
        verdict: result.verdict, label: result.label, channel: result.channel || '', products: result.products || [],
        findings: result.findings.map(f => ({ code: f.code, name: f.name, verdict: f.verdict, terms: f.terms || [] }))
      });
      setCampaign(res.campaign);
      renderResults();
    } catch (e) { toast('검사 결과를 서버에 기록하지 못했어요: ' + e.message, 'error'); }
  }

  /* 문구별 컴플라이언스 — 초안을 소비자에게 보일 문구 단위(헤드라인 · 캡션 · CTA · 장 · 자막 · 이미지 레이어)로 나눠 검사.
     화면 안에서만 판정하고 서버에는 기록하지 않음 (버전 전체 검사 결과는 기존대로 기록). */
  const UNIT_SKIP = /^(visual( note)?|비주얼|화면|움직임|카메라|기준 ?자산|asset|시간|장면|목적|간격|발송 ?조건|조건|오디오|audio|note|메모|approach|접근|확인 ?필요|총 ?길이)/i;
  const SUB_COL = /(자막|caption|subtitle|텍스트|text|카피|문구)/i;

  function cmpUnits(v) {
    if (v.kind === 'image') {
      return ((v.image && (v.image.image_text || v.image.text_layers)) || []).filter(t => String(t.text || '').trim())
        .map((t, i) => ({ label: t.role ? t.role.toUpperCase() : `문구 ${i + 1}`, text: String(t.text).trim() }));
    }
    if (v.kind === 'video') return [];
    const units = [];
    let section = '', cur = null, table = null, para = 0;
    const clean = s => s.replace(/\*\*/g, '').replace(/^\s*(?:[-*]|\d+\.)\s+/, '').trim();
    const close = () => { if (cur && cur.text.trim()) units.push({ label: cur.label, text: cur.text.trim() }); cur = null; };
    String(v.body || '').split('\n').forEach(raw => {
      const line = raw.trim();
      if (!line) { close(); table = null; return; }
      if (line.startsWith('|')) {  // 숏폼 구성안 표 — 자막 칸만
        close();
        const cells = line.replace(/^\||\|$/g, '').split('|').map(c => clean(c));
        if (!table) { table = { col: cells.findIndex(c => SUB_COL.test(c)) }; return; }
        if (cells.every(c => /^:?-{2,}:?$/.test(c) || !c)) return;
        const t = table.col >= 0 ? cells[table.col] : '';
        if (t && t !== '-') units.push({ label: `${cells[0] || '장면'} · 자막`, text: t });
        return;
      }
      table = null;
      const h = line.match(/^#{1,6}\s+(.*)$/);
      if (h) { close(); section = clean(h[1]); return; }
      const plain = clean(line);
      const kv = plain.match(/^([A-Za-z가-힣][A-Za-z가-힣 /()·-]{0,28}?)\s*[:：]\s*(.*)$/);
      const boldOnly = /^\*\*[^*]+\*\*:?$/.test(line);
      if (kv || boldOnly) {
        close();
        const key = (kv ? kv[1] : plain.replace(/:$/, '')).trim();
        const label = section ? `${section.split(/\s*[·:-]\s*/)[0]} · ${key}` : key;
        cur = UNIT_SKIP.test(key) ? { label, text: '', skip: true } : { label, text: kv ? kv[2] : '' };
        return;
      }
      if (cur && cur.skip) return;
      if (cur) { cur.text += (cur.text ? ' ' : '') + plain; return; }
      if (UNIT_SKIP.test(plain)) return;  // '총 길이 15초' · '오디오 메모' 같은 담당자용 줄
      para += 1;
      cur = { label: section ? `${section.split(/\s*[·:-]\s*/)[0]} · 문단 ${para}` : `문단 ${para}`, text: plain };
    });
    close();
    return units.slice(0, 40);
  }

  // 걸린 단어를 문구 안에서 표시 (escape 뒤에 찾아서 HTML이 깨지지 않게)
  function markTerms(text, findings) {
    let html = esc(text);
    const terms = [];
    (findings || []).forEach(f => (f.terms || []).forEach(t => { if (t && !t.startsWith('(') && !terms.includes(t)) terms.push(t); }));
    terms.sort((a, b) => b.length - a.length).forEach(t => {
      const re = new RegExp(esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      html = html.replace(re, m => `<mark class="cnt-cmp-hit">${m}</mark>`);
    });
    return html;
  }

  function unitReviewsHtml(v) {
    const units = cmpUnits(v);
    if (!units.length) return '';
    const done = units.filter((u, i) => S.unitReviews[`${v.id}:${i}`]).length;
    return `<details class="cnt-units" open><summary>문구별 컴플라이언스 <span class="muted small">${units.length}개 문구${done ? ` · ${done}개 검사함` : ''}</span></summary>
      <div class="cnt-units-head"><span class="panel-note">채널(${esc(S.camp.sheet.channels.map(channelLabel).join(', '))}) · 선택 제품 기준 1차 참고 판정이에요. 법적 승인이 아니에요.</span></div>
      <ul class="cnt-unit-list">${units.map((u, i) => {
        const r = S.unitReviews[`${v.id}:${i}`];
        const bad = r && r.findings && r.findings.length;
        return `<li class="cnt-unit${r ? (bad ? ' hit' : ' ok') : ''}">
          <div class="between"><b>${esc(u.label)}</b>${r ? badge(r.label || r.verdict, DATA.complianceTone[r.verdict] || '') : ''}</div>
          <p class="cnt-unit-text">${r ? markTerms(u.text, r.findings) : esc(u.text)}</p>
          ${bad ? `<ul class="cnt-review-items">${r.findings.map(f => `<li class="${f.verdict === 'BLOCK' ? 'error' : 'warn'}">${esc(f.code)} ${esc(f.name)}${f.terms && f.terms.length ? ` <code>${esc(f.terms.join(', '))}</code>` : ''}${f.alt ? ` → ${esc(f.alt)}` : ''}</li>`).join('')}</ul>` : ''}
        </li>`;
      }).join('')}</ul></details>`;
  }

  function runUnitCompliance(v, idx) {
    const units = cmpUnits(v);
    (idx == null ? units.map((u, i) => i) : [idx]).forEach(i => {
      if (units[i]) S.unitReviews[`${v.id}:${i}`] = complianceCheck(units[i].text);
    });
    renderResults();
  }

  async function selectVersion(id) {
    const c = S.camp;
    if (!c) return;
    S.baseOff = false;
    if (c.selectedVersionId === id) { renderResults(); updateComposer(); return; }
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}/select`, { version_id: id });
      setCampaign(res.campaign);
      renderResults();
      updateComposer();
    } catch (e) { toast(e.message, 'error'); }
  }


  function manualModal(parent) {
    const c = S.camp;
    if (!c) return;
    const defKind = parent ? parent.kind : (typeOf(c.sheet.content_type) || {}).kind || 'copy';
    let values = null;
    modal({
      title: parent ? `${parent.label} · v${parent.seq} 직접 수정` : '초안 직접 작성',
      wide: true,
      body: `<p>${parent ? '고친 내용은 새 버전으로 저장되고, 원래 버전은 그대로 남아요.' : '키가 없어도 초안을 직접 쓰고 버전으로 관리할 수 있어요.'} 저장할 때 키워드 확인을 거쳐요.</p>
        <div class="field-grid three">
          <label class="field"><span>종류</span><select data-m="kind">${DATA.textKinds.map(k => `<option value="${esc(k)}" ${k === defKind ? 'selected' : ''}>${esc(DATA.kinds[k])}</option>`).join('')}</select></label>
          <label class="field"><span>라벨</span><input data-m="label" maxlength="30" value="${esc(parent ? parent.label : '직접 작성')}"></label>
          <label class="field"><span>제목 (선택)</span><input data-m="title" maxlength="200" value="${esc(parent ? parent.title : '')}"></label>
        </div>
        <label class="field"><span>본문 (마크다운)</span><textarea data-m="body" rows="14" maxlength="20000">${esc(parent ? parent.body : '')}</textarea></label>`,
      actions: [{ label: '취소', value: null }, {
        label: '새 버전으로 저장', value: 'save', tone: 'lime',
        onClick: el => {
          const get = k => el.querySelector(`[data-m="${k}"]`).value;
          if (!get('body').trim()) { toast('본문을 입력하세요.', 'error'); return false; }
          values = { kind: get('kind'), label: get('label'), title: get('title'), body: get('body'), parent_id: parent ? parent.id : null };
          return true;
        }
      }]
    }).then(async v => {
      if (v !== 'save' || !values) return;
      try {
        const res = await REBOOT.api(`${API}/campaigns/${c.id}/versions`, values);
        S.baseOff = false;
        applyCampaign(res.campaign);
        toast('새 버전으로 저장했어요.');
      } catch (e) { toast(e.message, 'error'); }
    });
  }

  function compareCandidates(v) {
    const group = x => (x.kind === 'image' || x.kind === 'video' ? x.kind : 'text');
    return S.camp.versions.filter(x => x.id !== v.id && group(x) === group(v));
  }

  function compareBody(v) {
    const head = `<div class="cnt-cmp-head"><b>${esc(v.label)} · v${esc(v.seq)}</b> <span class="muted small">${esc(v.kindLabel)} · ${esc(fmt.datetime(v.createdAt))}</span>
      ${v.isFinal ? badge('최종본', 'lime') : ''}${v.stale ? badge('미반영', 'orange') : ''}</div>`;
    const body = v.kind === 'image' && v.imageUrl
      ? `<img src="${esc(v.imageUrl)}" alt="${esc(v.label)} 이미지 시안">`
      : v.kind === 'video' && v.videoUrl
      ? `<video src="${esc(v.videoUrl)}" controls playsinline preload="metadata" aria-label="${esc(v.label)} 영상 시안"></video>`
      : `<div class="prose">${ui.markdown(v.body)}</div>`;
    return head + (v.approach ? `<p class="panel-note">접근: ${esc(v.approach)}</p>` : '') + body;
  }

  function compareModal(v) {
    const others = compareCandidates(v);
    if (!others.length) return;
    const parent = others.find(x => x.id === v.parentId);
    const first = parent || others[0];
    modal({
      title: '버전 비교',
      wide: true,
      body: `<label class="field"><span>비교할 버전</span><select data-m="other">${others.map(x => `<option value="${esc(x.id)}" ${x.id === first.id ? 'selected' : ''}>${esc(x.label)} · v${esc(x.seq)} · ${esc(x.kindLabel)}</option>`).join('')}</select></label>
        <div class="cnt-compare"><div class="cnt-cmp">${compareBody(v)}</div><div class="cnt-cmp" data-m="right">${compareBody(first)}</div></div>`,
      actions: [{ label: '닫기', value: null }],
      onOpen: el => {
        el.querySelector('[data-m="other"]').addEventListener('change', e => {
          const o = others.find(x => String(x.id) === e.target.value);
          if (o) el.querySelector('[data-m="right"]').innerHTML = compareBody(o);
        });
      }
    });
  }

  function viewVersion(v) {
    modal({
      title: `${v.label} · v${v.seq} — ${v.kindLabel}`,
      wide: true,
      body: `${v.approach ? `<p>접근: ${esc(v.approach)}</p>` : ''}${v.stale ? `<p class="note warn">변경 사항 미반영: ${esc(v.staleFields.join(', '))}</p>` : ''}
        <div class="prose">${ui.markdown(v.body)}</div>`,
      actions: [{ label: '닫기', value: null }]
    });
  }

  async function exportZip(versionId) {
    const c = S.camp;
    if (!c) return;
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}/export${versionId ? '?version=' + versionId : ''}`, undefined, { raw: true });
      const blob = await res.blob();
      const cd = res.headers.get('Content-Disposition') || '';
      const m = cd.match(/filename="?([^";]+)"?/);
      ui.download(m ? m[1] : `reboot-content-${c.id}.zip`, blob);
      toast('내보냈어요. 파일 머리말에 미검토·임시 상태가 함께 적혀 있어요.');
    } catch (e) { toast(e.message, 'error'); }
  }

  /* ───────── 결정 변경 (후보 · 직접 · 되돌리기) ───────── */

  async function changeDecisions(changes, source, messageId) {
    const c = S.camp;
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}/decisions`, { expected_revision: c.revision, changes, source, message_id: messageId || null });
      applyCampaign(res.campaign, { applied: res.applied || [] });
      return true;
    } catch (e) { conflictOr(e); return false; }
  }

  function conflictOr(e) {
    if (e.status === 409 && e.data && e.data.campaign) {
      applyCampaign(e.data.campaign);
      S.edit = editCopy(e.data.campaign);
      renderSheetEdit();
    }
    toast(e.message, 'error');
  }

  function pickCandidate(b) {
    const m = S.camp.messages.find(x => String(x.id) === b.dataset.msg);
    const cd = m && m.payload && (m.payload.candidates || [])[+b.dataset.i];
    if (!cd) return;
    toggleChoice(cd.field, cd.value, m.id);
  }

  function addDecisionModal() {
    const keys = Object.keys(DATA.decisionLabels);
    let values = null;
    modal({
      title: '정한 내용 추가',
      body: `<p>대화 없이도 정한 내용을 적어 둘 수 있어요. '정함'은 내가 정한 값, 'AI 추천'은 아직 논의 중인 값이에요.</p>
        <label class="field"><span>항목</span><select data-m="key">${keys.map(k => `<option value="${esc(k)}">${esc(DATA.decisionLabels[k])}</option>`).join('')}</select></label>
        <label class="field"><span>값</span><input data-m="value" maxlength="300" placeholder="예: 검정 배경에 네온 라임 포인트"></label>
        <label class="field"><span>상태</span><select data-m="status"><option value="confirmed">정함</option><option value="proposed">AI 추천 (논의 중)</option></select></label>
        <p class="hint">제품 사실(성분·가격·용량·맛 라인업 등)은 여기서 바꿀 수 없어요 — 설정 › 제품 정보.</p>`,
      actions: [{ label: '취소', value: null }, {
        label: '저장', value: 'save', tone: 'lime',
        onClick: el => {
          const get = k => el.querySelector(`[data-m="${k}"]`).value;
          if (!get('value').trim()) { toast('값을 입력하세요.', 'error'); return false; }
          values = { field: 'decisions.' + get('key'), value: get('value').trim(), status: get('status') };
          return true;
        }
      }]
    }).then(v => { if (v === 'save' && values) changeDecisions([values], 'manual'); });
  }

  // 기본 정보 시트 수정 팝업 — 팝업은 body에 붙어 화면 이벤트가 닿지 않으므로 같은 시트 처리 함수를 여기서 연결
  function sheetEditModal() {
    if (!S.camp) return;
    S.edit = editCopy(S.camp);
    modal({
      title: '기본 정보 시트 수정',
      wide: true,
      body: `<div data-ref="fold-sheet">${foldSheetInner()}</div>`,
      actions: [
        { label: '취소', value: null },
        { label: '되돌리기', onClick: () => { S.edit = editCopy(S.camp); renderSheetEdit(); return false; } },
        { label: '시트 저장', tone: 'lime', onClick: (el, btn) => {
          saveSheetEdit(btn).then(ok => { if (ok) { const x = btn.closest('.modal').querySelector('[data-modal-close]'); if (x) x.click(); } });
          return false;
        } }
      ],
      onOpen: el => {
        S.sheetBox = el.querySelector('[data-ref="fold-sheet"]');
        el.addEventListener('click', e => {
          const b = e.target.closest('[data-act]');
          if (!b || b.disabled || !S.sheetBox.contains(b)) return;
          if (!onSheetClick(b.dataset.act, b) && b.dataset.act === 'suggest') suggest(b.dataset.field, b);
        });
        el.addEventListener('input', e => { if (e.target.dataset.sheetInput) onSheetInput(e.target); });
        el.addEventListener('change', e => { if (e.target.dataset.sheetCheck) onSheetCheck(e.target); });
      }
    }).then(() => { S.sheetBox = null; S.edit = S.camp ? editCopy(S.camp) : null; });
  }

  async function saveSheetEdit(btn) {
    const c = S.camp, e = S.edit;
    if (!c || !e) return false;
    const missing = sheetMissing(e);
    if (missing.length) { toast(`${missing.join(' · ')}을(를) 골라 주세요.`, 'error'); return false; }
    const sheet = {};
    const cur = c.sheet;
    ['content_type', 'brand_focus', 'goal', 'extra_request'].forEach(k => { if (e[k] !== cur[k]) sheet[k] = e[k]; });
    ['channels', 'product_ids'].forEach(k => { if (e[k].join(',') !== cur[k].join(',')) sheet[k] = e[k].slice(); });
    const curChar = (c.decisions.use_character && c.decisions.use_character.value) || 'AI 판단';
    if (usesCharacter(e.content_type) && e.use_character !== curChar) sheet.use_character = e.use_character;
    const body = { expected_revision: c.revision };
    if (Object.keys(sheet).length) body.sheet = sheet;
    if ((e.reference_text || '') !== (c.referenceText || '')) body.reference_text = e.reference_text || '';
    if (Object.keys(body).length === 1) { toast('바뀐 내용이 없어요.'); return false; }
    ui.busy(btn, true);
    try {
      const res = await REBOOT.api(`${API}/campaigns/${c.id}`, body, { method: 'PATCH' });
      applyCampaign(res.campaign);
      S.edit = editCopy(res.campaign);
      toast('시트를 저장했어요. 다음 대화부터 반영돼요.');
      loadList();
      return true;
    } catch (err) { conflictOr(err); return false; } finally { ui.busy(btn, false); }
  }

  function renameModal() {
    const c = S.camp;
    let title = null, del = false;
    const me = REBOOT.user();
    const canDelete = me.isAdmin || (c.createdById && c.createdById === me.id);
    modal({
      title: '캠페인 정보',
      body: `<label class="field"><span>캠페인명</span><input data-m="title" maxlength="80" value="${esc(c.title)}"></label>
        <p class="hint">캠페인명은 결과물에 영향을 주지 않아 '변경 사항 미반영' 비교에서 빠져요.</p>
        ${canDelete ? '<div class="note error mt-16">캠페인을 삭제하면 대화·버전·이미지 파일이 함께 지워지고 되돌릴 수 없어요.</div>' : ''}`,
      actions: [{ label: '취소', value: null }].concat(canDelete ? [{ label: '캠페인 삭제', value: 'delete', tone: 'danger', onClick: () => { del = true; } }] : [])
        .concat([{ label: '저장', value: 'save', tone: 'lime', onClick: el => { title = el.querySelector('[data-m="title"]').value.trim(); if (!title) { toast('캠페인명을 입력하세요.', 'error'); return false; } return true; } }])
    }).then(async v => {
      if (v === 'delete' && del) {
        if (!(await ui.confirm(`'${c.title}' 캠페인을 삭제할까요?`, { ok: '삭제', danger: true }))) return;
        try {
          await REBOOT.api(`${API}/campaigns/${c.id}`, undefined, { method: 'DELETE' });
          toast('캠페인을 삭제했어요.');
          newCampaign();
          loadList();
        } catch (e) { toast(e.message, 'error'); }
      } else if (v === 'save' && title && title !== c.title) {
        try {
          const res = await REBOOT.api(`${API}/campaigns/${c.id}`, { expected_revision: c.revision, title }, { method: 'PATCH' });
          applyCampaign(res.campaign);
          loadList();
        } catch (e) { conflictOr(e); }
      }
    });
  }

  function referenceModal() {
    const c = S.camp;
    let text = null;
    modal({
      title: '참고 문구 · 기존 카피',
      wide: true,
      body: `<p>기존 카피나 참고 메모를 붙여 넣으면 다음 대화부터 참고 자료로 써요. 안의 지시문은 AI 규칙을 바꾸지 않아요.</p>
        <textarea class="input" data-m="ref" rows="10" maxlength="4000">${esc(c.referenceText || '')}</textarea>`,
      actions: [{ label: '취소', value: null }, { label: '저장', value: 'save', tone: 'lime', onClick: el => { text = el.querySelector('[data-m="ref"]').value; } }]
    }).then(async v => {
      if (v !== 'save' || text == null) return;
      try {
        const res = await REBOOT.api(`${API}/campaigns/${c.id}`, { expected_revision: c.revision, reference_text: text }, { method: 'PATCH' });
        applyCampaign(res.campaign);
        S.edit = editCopy(res.campaign);
        toast('참고 문구를 저장했어요.');
      } catch (e) { conflictOr(e); }
    });
  }

  function personaModal() {
    const list = S.personas || [];
    modal({
      title: '타깃 참고 (인사이트 파트)',
      body: `<p>인사이트 파트의 페르소나예요. 예시 데이터일 수 있으니 참고로만 쓰세요. 고르면 입력창에 넣어 드려요 (자동 전송 안 함).</p>
        <div class="cnt-persona-list">${list.map((p, i) => `<button type="button" class="choice" data-persona="${i}"><span><b>${esc(p.name)}</b><br><small>${esc(p.oneLine || '')}</small></span></button>`).join('')}</div>`,
      actions: [{ label: '닫기', value: null }],
      onOpen: el => {
        el.addEventListener('click', e => {
          const b = e.target.closest('[data-persona]');
          if (!b) return;
          const p = list[+b.dataset.persona];
          const input = ref('input');
          if (input && p) { input.value = `타깃은 '${p.name}'(${p.oneLine || ''})으로 잡아 줘`; }
          const close = el.closest('.modal-backdrop') && el.closest('.modal-backdrop').querySelector('[data-modal-close]');
          if (close) close.click();
          if (input) input.focus();
        });
      }
    });
  }

  /* ───────── 시트 AI 추천 (모달 · 고를 때만 적용) ───────── */

  function basisKey(s) {
    return JSON.stringify([s.content_type, s.channels, !!s.brand_focus, s.product_ids]);
  }

  function suggest(field, b) {
    const c = sheetCtx(b);
    const s = c ? c.s : S.sheet;
    const ctx = c ? c.ctx : 'new';
    if (!s.content_type) { toast('만들 콘텐츠를 먼저 골라 주세요.', 'error'); return; }
    const basis = basisKey(s);
    const title = field === 'goal' ? '목적 AI 추천' : '추가 요청 아이디어';
    let items = [];
    modal({
      title,
      body: `<div class="cnt-suggest" data-ref-s="box"><div class="flex"><span class="spinner"></span>현재 콘텐츠·채널·대상에 맞는 후보를 만드는 중…</div></div>`,
      actions: [{ label: '닫기', value: null }],
      onOpen: el => {
        const box = el.querySelector('[data-ref-s="box"]');
        REBOOT.api(`${API}/suggest`, { field, sheet: sheetPayload(s) }).then(res => {
          items = res.items || [];
          const cur = field === 'goal' ? s.goal : s.extra_request;
          box.innerHTML = `<div class="flex wrap cnt-suggest-meta">${res.source === 'ai' ? badge('AI 추천', 'purple') : badge('예시 추천')}<span class="panel-note">${esc(res.note || '제작 아이디어예요. 시장 분석 결과가 아니에요.')}</span></div>
            ${cur ? `<p class="hint">지금 입력: "${esc(cur)}" — ${field === 'goal' ? '고르면 이 값을 바꿔요' : '고르면 뒤에 덧붙여요'}. 고르기 전에는 바뀌지 않아요.</p>` : ''}
            <div class="cnt-suggest-list">${items.map((it, i) => `<button type="button" class="cnt-concept" data-pick="${i}"><small>후보 0${i + 1}</small><strong>${esc(it.value)}</strong><span>${esc(it.reason)}</span></button>`).join('')}</div>`;
        }).catch(e => { box.innerHTML = `<p class="note error">${esc(e.message)}</p>`; });
        el.addEventListener('click', e => {
          const pb = e.target.closest('[data-pick]');
          if (!pb) return;
          const it = items[+pb.dataset.pick];
          const live = ctx === 'edit' ? S.edit : S.sheet;
          if (!it || !live) return;
          if (basisKey(live) !== basis) {
            toast('추천을 받은 뒤 콘텐츠·채널·대상이 바뀌었어요. 다시 추천받아 주세요.', 'error');
            return;
          }
          if (field === 'goal') live.goal = it.value;
          else live.extra_request = live.extra_request ? `${live.extra_request}, ${it.value}` : it.value;
          afterSheetChange(ctx, true);
          const close = el.closest('.modal-backdrop') && el.closest('.modal-backdrop').querySelector('[data-modal-close]');
          if (close) close.click();
        });
      }
    });
  }

  /* ───────── 이미지 작업 확인 ───────── */

  function startPolling() {
    if (S.pollTimer) return;
    S.pollTimer = setInterval(pollJobs, 3000);
  }
  function stopPolling() {
    clearInterval(S.pollTimer);
    S.pollTimer = null;
  }
  async function cancelJob(b) {
    b.disabled = true;
    try {
      const res = await REBOOT.api(`${API}/jobs/${b.dataset.job}/cancel`, {});
      toast(res.message, res.ok ? '' : 'error');
    } catch (e) { toast(e.message, 'error'); } finally { b.disabled = false; }
  }

  async function pollJobs() {
    const c = S.camp;
    const active = c ? c.jobs.filter(j => ACTIVE_JOB.includes(j.status)) : [];
    if (!active.length) { stopPolling(); return; }
    try {
      let changed = false;
      for (const j of active) {
        const res = await REBOOT.api(`${API}/jobs/${j.id}`);
        if (res.job.status !== j.status) changed = true;
      }
      if (changed && S.camp && S.camp.id === c.id) {
        const res = await REBOOT.api(`${API}/campaigns/${c.id}`);
        if (S.sending) { S.camp.jobs = res.campaign.jobs; return; }  // 보내는 중이면 응답이 상태를 다시 줌
        applyCampaign(res.campaign);
      }
    } catch (e) { /* 다음 확인 때 다시 */ }
  }

  /* ───────── 내 캠페인 ───────── */

  function campaignsHtml() {
    return `<div class="cnt-folders" data-ref="folder-chips" role="tablist" aria-label="채널별 폴더">${folderChipsHtml()}</div>
      <div class="filters cnt-filters">
        <div class="chips" data-ref="filter-chips">${filterChipsHtml()}</div>
        <div class="flex wrap"><label class="search cnt-search">${icon('search')}<input class="input" data-ref="search" type="search" placeholder="캠페인명 · 제품 · 채널 · 콘텐츠 검색" value="${esc(S.query)}" aria-label="캠페인 검색"></label>
          <button type="button" class="btn lime" data-act="new-campaign">${icon('plus')}새 캠페인</button></div>
      </div>
      <div data-ref="grid"></div>`;
  }

  function filterChipsHtml() {
    const counts = (S.listMeta && S.listMeta.counts) || {};
    const total = S.listMeta ? S.listMeta.total : '';
    return ['전체'].concat(DATA.statuses).map(st => `<button type="button" class="chip" data-act="filter" data-value="${esc(st)}" aria-pressed="${S.filter === st}">${esc(st)} <span class="n">${esc(st === '전체' ? total : counts[st] || 0)}</span></button>`).join('');
  }

  // 채널별 폴더 — 여러 채널 기획서는 해당 폴더마다 보임 (개수는 지금 상태 필터·검색 결과 기준)
  const inFolder = (c, id) => (c.channels || []).includes(id);
  function folderChipsHtml() {
    const list = S.list || [];
    const folders = [['전체', '전체', list.length]].concat(DATA.channels.map(ch => [ch.id, ch.label, list.filter(c => inFolder(c, ch.id)).length]));
    return folders.map(([id, label, n]) => `<button type="button" class="cnt-folder" role="tab" data-act="folder" data-value="${esc(id)}" aria-selected="${S.folder === id}">
      ${icon('grid')}<span>${esc(label)}</span><span class="n">${esc(n)}</span></button>`).join('');
  }

  function renderCampaignGrid() {
    const chips = ref('filter-chips');
    if (chips) chips.innerHTML = filterChipsHtml();
    const folders = ref('folder-chips');
    if (folders) folders.innerHTML = folderChipsHtml();
    const el = ref('grid');
    if (!el) return;
    if (S.list == null) { el.innerHTML = '<div class="skeleton" style="height:220px"></div>'; return; }
    if (S.listErr) { el.innerHTML = `<p class="note error">캠페인 목록을 불러오지 못했어요: ${esc(S.listErr)}</p>`; return; }
    if (!S.list.length) {
      el.innerHTML = ui.empty(S.query || S.filter !== '전체' ? '조건에 맞는 캠페인이 없어요.' : "아직 캠페인이 없어요. 'AI와 함께 제작'에서 시작해 보세요.", 'spark');
      return;
    }
    if (S.folder !== '전체') {
      const items = S.list.filter(c => inFolder(c, S.folder));
      el.innerHTML = items.length ? `<div class="cnt-campaign-grid">${items.map(cardHtml).join('')}</div>`
        : ui.empty(`${channelLabel(S.folder)} 폴더에 캠페인이 없어요.`, 'grid');
      return;
    }
    el.innerHTML = DATA.channels.map(ch => {
      const items = S.list.filter(c => inFolder(c, ch.id));
      return items.length ? `<section class="cnt-folder-group"><div class="cnt-folder-head">${icon('grid')}<h3>${esc(ch.label)}</h3><span class="muted small">${esc(items.length)}개</span>
          <button type="button" class="link" data-act="folder" data-value="${esc(ch.id)}">폴더 열기</button></div>
        <div class="cnt-campaign-grid">${items.map(cardHtml).join('')}</div></section>` : '';
    }).join('');
  }

  function canDelete(c) {
    const me = REBOOT.user() || {};
    return !!(me.isAdmin || (c.createdBy && c.createdBy === (me.name || String(me.email || '').split('@')[0])));
  }

  function cardHtml(c) {
    const p = DATA.posters[c.contentType] || { en: 'CONTENT' };
    // 대표 파일이 영상(mp4)이면 첫 장면을 썸네일로 (소리 없음 · 재생 안 함)
    const head = c.cover && /\.mp4(\?|$)/i.test(c.cover)
      ? `<div class="cnt-cover cnt-cover-video"><video src="${esc(c.cover)}#t=0.1" muted playsinline preload="metadata" tabindex="-1" aria-label="${esc(c.title)} 대표 영상"></video><span class="cnt-cover-play" aria-hidden="true">▶</span></div>`
      : c.cover
      ? `<div class="cnt-cover"><img src="${esc(c.cover)}" alt="${esc(c.title)} 대표 이미지" loading="lazy"></div>`
      : `<div class="poster cnt-poster t-${esc(c.contentType)}"><div class="wordmark">REB∞T</div><strong>${esc(p.en)}</strong><small>${esc(c.channelLabels.join(' · ').toUpperCase())}</small></div>`;
    const targets = (c.brandFocus ? ['브랜드'] : []).concat((c.products || []).map(x => x.ko || x.name));
    return `<div class="card cnt-campaign-card">
      <button type="button" class="cnt-card-open" data-act="open-campaign" data-id="${esc(c.id)}">
        ${head}
        <h3>${esc(c.title)}</h3>
        <p>${esc(c.channelLabels.join(', '))} · ${esc(c.contentTypeLabel)} · ${esc(fmt.date(c.updatedAt))}</p>
        <div class="cnt-tags">${targets.map(t => `<span class="cnt-tag">${esc(t)}</span>`).join('')}${c.versionCount ? `<span class="cnt-tag strong">버전 ${esc(c.versionCount)}</span>` : ''}</div>
        <div class="between cnt-card-foot">${badge(c.status, statusTone(c.status))}<span class="small muted">작업 열기 ↗</span></div>
      </button>
      ${canDelete(c) ? `<button type="button" class="cnt-card-del" data-act="delete-campaign" data-id="${esc(c.id)}" data-title="${esc(c.title)}" aria-label="${esc(c.title)} 삭제" title="삭제">${icon('trash')}</button>` : ''}
    </div>`;
  }

  async function deleteCampaign(id, title) {
    if (!(await ui.confirm(`'${title}' 캠페인을 삭제할까요? 대화·버전·이미지 파일이 함께 지워지고 되돌릴 수 없어요.`, { ok: '삭제', danger: true }))) return;
    try {
      await REBOOT.api(`${API}/campaigns/${id}`, undefined, { method: 'DELETE' });
      if (S.camp && S.camp.id === id) { stopPolling(); S.camp = null; S.edit = null; setHash('campaigns'); }
      toast('캠페인을 삭제했어요.');
      loadList();
    } catch (e) { toast(e.message, 'error'); }
  }

  function newCampaign() {
    stopPolling();
    S.camp = null;
    S.edit = null;
    S.tab = 'create';
    setHash('');
    renderTabs();
    renderMain();
  }

  /* ───────── 이벤트 (data-act · onclick 없음) ───────── */

  function bind(root) {
    root.addEventListener('click', e => {
      const b = e.target.closest('[data-act]');
      if (!b || !root.contains(b) || b.disabled) return;
      const act = b.dataset.act;
      if (onSheetClick(act, b)) return;
      const v = b.dataset.id ? (S.camp && S.camp.versions.find(x => String(x.id) === b.dataset.id)) : null;
      switch (act) {
        case 'tab':
          S.tab = b.dataset.value;
          setHash(S.tab === 'campaigns' ? 'campaigns' : S.camp ? 'campaign=' + S.camp.id : '');
          renderTabs(); renderMain();
          if (S.tab === 'campaigns') loadList();
          break;
        case 'start': startCampaign(b); break;
        case 'start-quick': startCampaign(b, true); break;
        case 'copy-channel': copyChannelModal(); break;
        case 'suggest': suggest(b.dataset.field, b); break;
        case 'send': { const inp = ref('input'); if (inp) send(inp.value); break; }
        case 'quick': {
          const set = S.camp && S.camp.sheet.content_type === 'image' ? DATA.quick.image : DATA.quick.text;
          const q = set[+b.dataset.i];
          const inp = ref('input');
          if (q && inp && !inp.disabled) { inp.value = q.text; selectRange(inp, q.text); }
          break;
        }
        case 'persona': personaModal(); break;
        case 'answer': { const inp = ref('input'); if (inp && !inp.disabled) { inp.value = ''; inp.placeholder = b.dataset.q; inp.focus(); } break; }
        case 'pick-candidate': pickCandidate(b); break;
        case 'add-decision': if (S.camp) addDecisionModal(); break;
        case 'sheet-edit': sheetEditModal(); break;
        case 'ai-settings': aiSettingsModal(); break;
        case 'reference': if (S.camp) referenceModal(); break;
        case 'rename': renameModal(); break;
        case 'new-campaign': newCampaign(); break;
        case 'open-campaign': openCampaign(+b.dataset.id); break;
        case 'filter': S.filter = b.dataset.value; loadList(); renderCampaignGrid(); break;
        case 'select-version': selectVersion(+b.dataset.id); break;
        case 'use-base': if (v) { selectVersion(v.id); const inp = ref('input'); if (inp && !inp.disabled) { inp.focus(); inp.placeholder = `${v.label} · v${v.seq}을 어떻게 고칠까요? 예: 더 짧게, CTA만 바꿔줘`; } } break;
        case 'base-off': S.baseOff = true; updateComposer(); break;
        case 'check-all': if (v) { runUnitCompliance(v, null); runCompliance(v); } break;  // 비용 없는 규칙 검사만 · 검사 버튼은 이것 하나
        case 'manual-version': manualModal(null); break;
        case 'manual-edit': if (v) manualModal(v); break;
        case 'view-version': if (v) viewVersion(v); break;
        case 'compare': if (v) compareModal(v); break;
        case 'copy':
          if (v && global.navigator && global.navigator.clipboard) {
            global.navigator.clipboard.writeText(v.body).then(() => toast('복사했어요.'), () => toast('복사하지 못했어요.', 'error'));
          }
          break;
        case 'copy-text':
          if (global.navigator && global.navigator.clipboard) {
            global.navigator.clipboard.writeText(b.dataset.text || '').then(() => toast('복사했어요.'), () => toast('복사하지 못했어요.', 'error'));
          }
          break;
        case 'export': exportZip(null); break;
        case 'export-one': if (v) exportZip(v.id); break;
        case 'toggle-layers': S.showLayers = !S.showLayers; renderResults(); break;
        case 'make-draft': send('초안 만들어줘', { force: 'draft' }); break;
        case 'make-image': send('이미지 시안 만들어줘', { force: 'image' }); break;
        case 'make-video': makeVideo('이 초안으로 영상 만들어줘'); break;
        case 'vid-quick': {
          const q = DATA.quick.video[+b.dataset.i];
          const ta = ref('vid-prompt');
          if (q && ta) { ta.value = q.text; S.vidDraft = q.text; selectRange(ta, q.text); }
          break;
        }
        case 'vid-ai-edit': if (v) videoAiEdit(v); break;
        case 'vid-new': makeVideo('지금 정한 내용으로 새 영상 만들어줘', { noBase: true }); break;
        case 'retry-image': { const inp = ref('input'); if (inp && !inp.disabled) { inp.value = b.dataset.kind === 'video' ? '영상 다시 만들어줘' : '이미지 시안 다시 만들어줘'; inp.focus(); } break; }
        case 'cancel-job': cancelJob(b); break;
        case 'api-check': apiCheck(); break;
        case 'complete': completeCampaign(b); break;
        case 'reopen': reopenCampaign(); break;
        case 'save-image': if (v) saveImage(v); break;
        case 'folder': S.folder = b.dataset.value; renderCampaignGrid(); break;
        case 'delete-campaign': deleteCampaign(+b.dataset.id, b.dataset.title); break;
        case 'chip': {
          const m = S.camp && S.camp.messages.find(x => String(x.id) === b.dataset.msg);
          const ch = m && m.payload && (m.payload.chips || [])[+b.dataset.i];
          if (ch) send(ch.value);
          break;
        }
        case 'offer-make': send('좋아요, 이대로 만들어줘'); break;
        case 'brief-say': briefSay(b.dataset.key); break;
        case 'ref-upload': { const f = ref('ref-file'); if (f) { f.value = ''; f.click(); } break; }
        case 'ref-del': deleteReference(b.dataset.id); break;
        case 'ref-view': referenceViewModal(b.dataset.id); break;
        case 'iv-pick': ivPick(b); break;
        case 'iv-custom': ivCustom(); break;
        case 'iv-skip': interviewAct('skip', {}, '이 질문은 건너뛸게요'); break;
        case 'iv-finish': interviewAct('finish'); break;
        case 'brief-edit': { const m = S.camp && S.camp.messages.find(x => String(x.id) === b.dataset.msg); if (m) briefEditModal(m); break; }
        case 'img-quick': {
          const q = DATA.quick.image.filter(x => x.label !== '다른 시안')[+b.dataset.i];
          const ta = ref('img-prompt');
          if (q && ta) { ta.value = q.text; S.imgDraft = q.text; selectRange(ta, q.text); }
          break;
        }
        case 'img-ai-edit': if (v) imageAiEdit(v); break;
        case 'img-new': send('지금 정한 내용으로 다른 이미지 시안 만들어줘', { force: 'image', noBase: true }); break;
        case 'img-editor': if (v) imageEditor(v); break;
        case 'img-text': lsSet('imgText', b.dataset.value === 'none' ? 'none' : 'bake'); renderResults(); break;
        default: break;
      }
    });

    root.addEventListener('input', e => {
      const el = e.target;
      if (el.dataset.sheetInput) { onSheetInput(el); return; }
      if (el.dataset.ref === 'iv-input') { S.ivDraft = el.value; return; }
      if (el.dataset.ref === 'img-prompt') { S.imgDraft = el.value; return; }
      if (el.dataset.ref === 'vid-prompt') { S.vidDraft = el.value; return; }
      if (el.dataset.ref === 'search') {
        clearTimeout(S.searchTimer);
        S.searchTimer = setTimeout(() => { S.query = el.value.trim(); loadList(); }, 300);
      }
    });

    root.addEventListener('change', e => {
      const el = e.target;
      if (el.dataset.sheetCheck) { onSheetCheck(el); return; }
      if (el.dataset.ref === 'ref-file') { if (el.files && el.files[0]) uploadReference(el.files[0]); return; }
    });

    root.addEventListener('keydown', e => {
      const el = e.target;
      if ((el.dataset.ref !== 'input' && el.dataset.ref !== 'iv-input') || e.key !== 'Enter' || e.shiftKey) return;
      if (e.isComposing || e.keyCode === 229) return;  // 한글 조합 중에는 보내지 않음
      e.preventDefault();
      if (el.dataset.ref === 'iv-input') { S.ivDraft = el.value; ivCustom(); } else send(el.value);
    });
  }

  /* ───────── 등록 · 공개 ───────── */

  function campaigns() {
    return REBOOT.api(`${API}/campaigns`).then(d => ({
      total: d.total,
      active: d.active,
      items: (d.items || []).map(c => ({ id: c.id, title: c.title, status: c.status, updatedAt: c.updatedAt }))
    }));
  }

  REBOOT.register({
    id: 'content', label: '콘텐츠 제작', order: 30,
    title: '아이디어에서 콘텐츠까지.',
    subtitle: 'REBOOT AI와 함께, 브랜드의 다음 이야기를 만들어보세요.',
    render,
    summary: () => (S.listMeta ? { label: '진행 중 캠페인', value: S.listMeta.active } : null)
  });
  REBOOT.expose('content', { campaigns });
})(typeof window !== 'undefined' ? window : globalThis);
