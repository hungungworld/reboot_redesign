/* 컴플라이언스 파트: 판정 엔진 + 화면.
   다른 파트에는 REBOOT.use('compliance')로 review · bannedTerms · rules만 공개합니다.
   판정은 1차 참고용이며 FDA 공식 판단이나 법률 자문이 아닙니다. PASS는 법적 승인이 아닙니다. */
(function (global) {
  'use strict';

  const REBOOT = global.REBOOT;
  const DATA = global.REBOOT_DATA && global.REBOOT_DATA.compliance;
  if (!REBOOT || !DATA) throw new Error('compliance: REBOOT core와 tabs/compliance/static/data.js를 먼저 불러와야 합니다');

  const store = REBOOT.store('compliance');   // 저장 키: reboot:compliance:*
  const KEY = { history: 'history', openfda: 'openfda-cache', fedreg: 'fedreg-cache', monitor: 'monitor', journey: 'journey', persona: 'persona' };
  const PERSONA_MAX = 600;
  const ALL = 'all';   // 채널·제품 '전체' (모든 조합으로 점검 → 가장 엄격한 판정)
  const RULE_SPAN = `R1~R${DATA.rules.length}`;   // 화면 문구용 (규칙이 늘면 자동으로 바뀜)   // 서버(ai_review.py)도 600자에서 자름
  const RANK = { PASS: 0, COND: 1, REVIEW: 2, REVISE: 3, BLOCK: 4 };
  const LABEL = { BLOCK: '사용 불가', REVISE: '수정 필요', COND: '조건부', REVIEW: '검토 필요', PASS: '규칙 미탐지' };
  const BASIS = { official: '⚖️ 공식 근거', platform: '📱 플랫폼 정책', internal: '🏢 내부 정책', context: '🔍 문맥 검토' };
  const MAX_ROWS = 500;
  const MAX_CSV_BYTES = 1024 * 1024;
  const CACHE_TTL = 60 * 1000;
  const STALE_HOURS = 48;
  const FR_AGENCIES = {
    'food-and-drug-administration': 'FDA',
    'federal-trade-commission': 'FTC',
    'u-s-customs-and-border-protection': 'CBP',
    'trade-representative-office-of-united-states': 'USTR'
  };

  /* ───────── 판정 엔진 ───────── */

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  // 하이픈·띄어쓰기·붙여쓰기, 곧은/굽은 따옴표, 복수형·과거형을 함께 잡고 단어 중간은 잡지 않습니다.
  // 자음 + y로 끝나면 ies·ied·ying도 (detoxify → detoxifies · 2026-10-24)
  function termPattern(term) {
    const yEnd = /[^aeiou\s-]y$/i.test(term);
    const body = escapeRe(yEnd ? term.slice(0, -1) : term).replace(/-|\s+/g, '[\\s-]*').replace(/'/g, "['’]?");
    return '(?<![A-Za-z0-9])' + body + (yEnd ? '(?:y|ys|ies|ied|ying)' : '(?:s|es|d|ed|ing)?') + '(?![A-Za-z0-9])';
  }

  // off: true인 규칙(예: 당 함량 R11·R12)은 판정·공개 목록에서 빼고 규칙 표에만 '사용 안 함'으로 보여 줍니다.
  const ACTIVE_RULES = DATA.rules.filter(r => !r.off);
  const RULES = ACTIVE_RULES.map(r => Object.assign({}, r, {
    _re: r.terms.map(t => new RegExp(termPattern(t), 'gi')).concat((r.patterns || []).map(s => new RegExp(s, 'gi'))),
    _comp: (r.compensated || []).map(s => new RegExp(s, 'i')),
    _inc: (r.incentive || []).map(s => new RegExp(s, 'i')),
    _allow: (r.allow || []).map(s => new RegExp(s, 'gi'))
  }));

  // 꺼 둔 규칙(당 함량 R11·R12): 판정은 하지 않지만, 해당 표현이 있으면 'PASS'가 아니라 '점검하지 않은 표현'이라고 알려 줌 (GPT 검토 ⑥)
  const OFF_RULES = DATA.rules.filter(r => r.off).map(r => Object.assign({}, r, {
    _re: r.terms.map(t => new RegExp(termPattern(t), 'gi')).concat((r.patterns || []).map(s => new RegExp(s, 'gi')))
  }));
  function uncheckedOf(src) {
    return OFF_RULES.map(r => ({ code: r.code, name: r.name, terms: collect(r, src) })).filter(x => x.terms.length);
  }

  // allow: 이 규칙에서는 괜찮은 문맥(예: prevent spoilage · 부정문 '리뷰 대가를 주지 않습니다')을 지우고 판정 (2026-10-24)
  function maskAllowed(rule, src) {
    return rule._allow.reduce((s, re) => { re.lastIndex = 0; return s.replace(re, m => ' '.repeat(m.length)); }, src);
  }

  // 같은 규칙에서 겹치는 표현은 긴 것 하나만 남깁니다 (예: liver health ⊂ supports liver health).
  function dedupe(list) {
    const seen = new Set();
    const out = list.filter(h => { const k = h.toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; });
    return out.filter(h => {
      const k = h.toLowerCase();
      return !out.some(o => { const ok = o.toLowerCase(); return ok !== k && ok.includes(k); });
    });
  }

  function collect(rule, src) {
    const hits = [];
    rule._re.forEach(re => {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(src)) !== null) hits.push(m[0].replace(/^[.!?]\s+/, '').trim());   // patterns는 앞 문장부호까지 잡으므로 떼어 냄
    });
    (rule.ko || []).forEach(k => { if (src.includes(k)) hits.push(k); });
    return dedupe(hits);
  }

  /**
   * @param {string} text 점검할 문구
   * @param {{channel?: 'b2c'|'amazon'|'cs'|'b2b', category?: 'FOOD'|'SUPPLEMENT'}} [opts]
   * @returns {{verdict: string, findings: object[], codes: string[], channel: string, category: string}}
   */
  function review(text, opts) {
    opts = opts || {};
    if (opts.channel === ALL || opts.product === ALL) return reviewAll(text, opts);
    const channel = DATA.channels[opts.channel] ? opts.channel : 'b2c';
    const category = opts.category === 'SUPPLEMENT' ? 'SUPPLEMENT' : 'FOOD';
    // 제품 (젤리·저당 젤리·음료·저당 음료). 모르면 '' → 제품별 판정 없이 기본 판정
    const product = (DATA.products || []).some(p => p.id === opts.product) ? opts.product : '';
    const src = String(text == null ? '' : text).replace(/\*/g, ' ');   // 면책 별표는 판정에 영향 없음
    const findings = [];

    RULES.forEach(rule => {
      const text = maskAllowed(rule, src);
      const hits = collect(rule, text);
      const compensated = rule._comp.some(re => re.test(text));               // 좋은 후기를 조건으로 한 보상 → BLOCK
      const incentive = !compensated && rule._inc.some(re => re.test(text));  // 조건 없는 후기 보상 → incentiveVerdict
      if (!hits.length && !compensated && !incentive) return;
      let verdict = rule.verdict;
      if (rule.byChannel && rule.byChannel[channel]) verdict = rule.byChannel[channel];
      if (rule.byCategory && rule.byCategory[category]) verdict = rule.byCategory[category];
      if (product && rule.byProduct && rule.byProduct[product]) verdict = rule.byProduct[product];
      if (incentive && RANK[rule.incentiveVerdict] > RANK[verdict]) verdict = rule.incentiveVerdict;
      if (compensated) verdict = 'BLOCK';
      findings.push({
        code: rule.code,
        name: rule.name,
        verdict,
        terms: hits.length ? hits : [compensated ? '(보상 조건 문구)' : '(후기 대가 문구)'],
        reason: compensated ? rule.compensatedReason : incentive ? rule.incentiveReason : rule.reason,
        alt: rule.alt,
        basis: rule.basis.slice()
      });
    });

    const verdict = findings.reduce((v, f) => (RANK[f.verdict] > RANK[v] ? f.verdict : v), 'PASS');
    return { verdict, findings, codes: findings.map(f => f.code), channel, category, product, unchecked: uncheckedOf(src) };
  }

  /** '전체 채널'·'전체 제품': 모든 조합으로 점검 → 가장 엄격한 판정 · 규칙마다 가장 엄격한 쪽을 남김.
      variants에 조합별 판정을 담아, 채널·제품에 따라 판정이 다르면 화면에 따로 알려 줌 */
  function reviewAll(text, opts) {
    const channels = opts.channel === ALL ? (DATA.pickChannels || Object.keys(DATA.channels)) : [DATA.channels[opts.channel] ? opts.channel : 'b2c'];
    const products = opts.product === ALL ? (DATA.products || []).map(p => p.id) : [opts.product || ''];
    const variants = [];
    const byCode = {};
    channels.forEach(channel => products.forEach(product => {
      const r = review(text, Object.assign({}, opts, { channel, product }));
      variants.push({ channel, product, verdict: r.verdict });
      r.findings.forEach(f => {
        const cur = byCode[f.code];
        if (!cur) byCode[f.code] = Object.assign({}, f, { terms: f.terms.slice() });
        else {
          if (RANK[f.verdict] > RANK[cur.verdict]) Object.assign(cur, { verdict: f.verdict, reason: f.reason });
          cur.terms = dedupe(cur.terms.concat(f.terms));
        }
      });
    }));
    const findings = RULES.map(r => byCode[r.code]).filter(Boolean);
    const verdict = variants.reduce((v, x) => (RANK[x.verdict] > RANK[v] ? x.verdict : v), 'PASS');
    return { verdict, findings, codes: findings.map(f => f.code), channel: opts.channel === ALL ? ALL : channels[0],
      category: opts.category === 'SUPPLEMENT' ? 'SUPPLEMENT' : 'FOOD', product: opts.product === ALL ? ALL : products[0], variants,
      unchecked: uncheckedOf(String(text == null ? '' : text).replace(/\*/g, ' ')) };
  }

  /** 전체로 점검했을 때 조합에 따라 판정이 갈리면 한 줄 안내 (예: 'B2B 바이어 자료에서는 REVIEW') */
  function variantNote(r) {
    const vs = r.variants || [];
    if (vs.length < 2 || vs.every(x => x.verdict === r.verdict)) return '';
    const lighter = vs.filter(x => x.verdict !== r.verdict);
    const names = [...new Set(lighter.map(x => [r.channel === ALL ? DATA.channels[x.channel] : '', r.product === ALL ? productLabel(x.product) : ''].filter(Boolean).join(' · ')))];
    return `<p class="cmp-note">전체 중 가장 엄격한 판정은 ${badge(r.verdict)}입니다. ${esc(names.slice(0, 6).join(', '))}${names.length > 6 ? ' 등' : ''}에서는 ${[...new Set(lighter.map(x => x.verdict))].map(badge).join(' ')}입니다.</p>`;
  }

  function bannedTerms() {
    const out = [];
    ACTIVE_RULES.forEach(r => r.terms.forEach(term => out.push({ term, code: r.code, verdict: r.verdict, ko: r.ko.slice() })));
    return out;
  }

  function rules() {
    return ACTIVE_RULES.map(r => ({ code: r.code, name: r.name, verdict: r.verdict, basis: r.basis.slice(), reason: r.reason, alt: r.alt,
      law: (r.law || []).map(l => `${l.agency} ${l.cite}`) }));
  }

  /* ───────── 저장 ───────── */

  function loadHistory() {
    const h = store.get(KEY.history, []);
    return Array.isArray(h) ? h : [];
  }

  function saveHistory(list) { store.set(KEY.history, list.slice(0, 500)); }

  // 점검 기록: 마케터가 자기 대시보드에서 쓰므로 승인·반려 없이 [문구 점검하기]를 누를 때마다 저장합니다.
  // 같은 문구·채널·제품·기준을 연달아 점검하면 맨 위 한 줄만 갱신합니다. (예전 '검토 요청' 이력도 그대로 읽힘)
  function recordCheck(text, r) {
    const list = loadHistory();
    const top = list[0];
    const same = top && top.text === text && top.channel === r.channel
      && (top.category || 'FOOD') === r.category && (top.product || '') === (r.product || '');
    const entry = { id: same ? top.id : uid(), text, channel: r.channel, category: r.category, product: r.product || '',
      verdict: r.verdict, codes: r.codes, checkedAt: Date.now(),
      // 당시 규칙 버전과 걸린 표현 (나중에 규칙이 바뀌어도 '왜 그렇게 판정했는지' 재현 · GPT 검토 ㉛)
      rulesVersion: DATA.version, hits: r.findings.map(f => ({ code: f.code, verdict: f.verdict, terms: f.terms.slice(0, 5) })) };
    if (same) list[0] = entry; else list.unshift(entry);
    saveHistory(list);
  }

  // 기록은 저장 당시 판정을 갖고 있지만, 규칙이 바뀌면(예: R11·R12 끄기) 지금 규칙으로 다시 판정해 보여 줍니다.
  const recheckCache = new Map();
  function recheck(item) {
    const opts = { channel: item.channel, category: item.category, product: item.product };
    const k = JSON.stringify([item.text, opts.channel, opts.category, opts.product]);
    if (!recheckCache.has(k)) recheckCache.set(k, review(item.text, opts));
    return recheckCache.get(k);
  }

  /* ───────── 한글 번역 (참고용) ─────────
     순서: 서버 AI(Claude, /api/compliance/translate-ko · 키가 있을 때) → 브라우저 내장 번역(Chrome Translator API ·
     기기에 언어 팩이 없거나 설정이 꺼져 있으면 실패) → fallbackUrl(Google 번역 · 여는 것은 사람이 결정).
     AI를 먼저 쓰는 이유: 내장 번역은 브랜드명(REBOOT → 재부팅)·미국식 표현('the morning after' = 숙취)을 놓침 (2026-10-24)
     판정은 언제나 영어 원문 기준입니다. CS 파트도 REBOOT.use('compliance').translate로 씁니다.
     한→영(한국어 카피를 영어로)은 아래 translateToEnglish가 서버(Claude)로 합니다. */

  const trCache = new Map();
  let translatorP = null;

  function mostlyKorean(s) {
    return (s.match(/[가-힣]/g) || []).length > (s.match(/[A-Za-z]/g) || []).length;
  }

  function translateFallbackUrl(text) {
    return 'https://translate.google.com/?sl=auto&tl=ko&op=translate&text=' + encodeURIComponent(String(text).slice(0, 4500));
  }

  /**
   * @param {string} text 영어 문구
   * @param {(loaded:number)=>void} [onProgress] 번역 모델을 처음 내려받을 때 진행률 (0~1)
   * @returns {Promise<{ok:boolean, text?:string, engine?:string, reason?:string, message?:string, fallbackUrl?:string}>}
   */
  async function translate(text, onProgress) {
    const src = String(text == null ? '' : text).trim();
    if (!src) return { ok: false, reason: 'empty' };
    if (mostlyKorean(src)) return { ok: true, text: src, engine: 'already-ko' };
    if (trCache.has(src)) return { ok: true, text: trCache.get(src), engine: 'cache' };
    const fallbackUrl = translateFallbackUrl(src);
    // 서버 AI가 있으면 먼저 (브랜드명·미국식 표현 뜻까지 옮김 · 크롬 내장 번역은 기기마다 안 되는 경우가 있음)
    const health = await serverHealth();
    if (health && health.ai) {
      const viaAi = await translateByServer(src, fallbackUrl, '');
      if (viaAi.ok) return viaAi;
    }
    const T = global.Translator;
    if (!T || typeof T.create !== 'function') return translateByServer(src, fallbackUrl, '');
    try {
      const avail = await T.availability({ sourceLanguage: 'en', targetLanguage: 'ko' });
      if (avail === 'unavailable') return translateByServer(src, fallbackUrl, '');
      if (!translatorP) {
        translatorP = T.create({
          sourceLanguage: 'en', targetLanguage: 'ko',
          monitor(m) { m.addEventListener('downloadprogress', e => { if (onProgress) onProgress(e.loaded); }); }
        }).catch(e => { translatorP = null; throw e; });
      }
      const tr = await translatorP;
      const out = [];
      for (const line of src.split('\n')) out.push(line.trim() ? await tr.translate(line) : line);   // 줄바꿈 유지
      const result = out.join('\n');
      trCache.set(src, result);
      return { ok: true, text: result, engine: 'on-device' };
    } catch (e) {
      return translateByServer(src, fallbackUrl, e.message);
    }
  }

  /** 크롬 내장 번역을 못 쓸 때: 서버 AI(Claude)로 영→한. 서버도 안 되면(키 없음 등) Google 번역 링크 */
  async function translateByServer(src, fallbackUrl, why) {
    try {
      const res = await api('/api/compliance/translate-ko', { text: src.slice(0, 8000) });
      trCache.set(src, res.text);
      return { ok: true, text: res.text, note: res.note_ko || '', engine: 'ai', model: res.cached ? '전에 번역한 것 재사용 · 비용 없음' : res.model || '' };
    } catch (e) {
      return { ok: false, reason: 'error', message: [why, `AI 번역: ${e.message}`].filter(Boolean).join(' · '), fallbackUrl };
    }
  }

  /* ───────── 화면 도우미 ───────── */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function fmt(ts) {
    return ts ? new Date(ts).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
  }
  function badge(v) {
    if (!LABEL[v]) return `<span class="cmp-badge">${esc(v)}</span>`;   // 저장소에서 읽은 알 수 없는 값
    return `<span class="cmp-badge cmp-v-${v}">${v} · ${LABEL[v]}</span>`;
  }
  // 선 아이콘 (시안과 같은 1.65 두께 스트로크)
  const ICON_PATH = {
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    pencil: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r=".6"/><circle cx="4.5" cy="12" r=".6"/><circle cx="4.5" cy="18" r=".6"/>',
    shield: '<path d="M12 3 5 6v5.5c0 4.4 2.9 8.3 7 9.5 4.1-1.2 7-5.1 7-9.5V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><path d="m12 8 1.5 2.5L16 12l-2.5 1.5L12 16l-1.5-2.5L8 12l2.5-1.5L12 8Z"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>'
  };
  function icon(name) {
    return `<svg class="cmp-icon" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATH[name] || ''}</svg>`;
  }

  /* ───────── 서버 (Flask · tabs/compliance/) ─────────
     키가 필요한 기능(AI 2차 판정 · 모니터링 수집)은 같은 주소의 /api로만 부릅니다. HTML·JS에는 키가 없습니다.
     file://로 열었거나 서버가 꺼져 있으면 health()가 null → 해당 버튼만 꺼짐 */
  let healthP = null;
  function serverHealth() {
    if (!healthP) {
      healthP = /^https?:$/.test(global.location ? global.location.protocol : '')
        ? fetch('/api/health', { cache: 'no-store', signal: AbortSignal.timeout(3000) })
          .then(r => (r.ok ? r.json() : null)).catch(() => null)
        : Promise.resolve(null);
    }
    return healthP;
  }
  async function api(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {})
      });
    } catch (e) {
      throw new Error('서버에 연결하지 못했습니다. 인터넷 연결을 확인하고 새로고침해 주세요.');
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `서버 오류 (${res.status})`);
    return data;
  }
  function channelLabel(c) { return c === ALL ? '전체 채널' : DATA.channels[c] || c; }
  // 표처럼 좁은 칸에는 괄호 설명을 뺀 이름 ('B2C 광고·SNS')
  function channelShort(c) { return channelLabel(c).replace(/\s*\(.*\)$/, ''); }
  const PICK = DATA.pickChannels || Object.keys(DATA.channels);
  function channelOptions(selected) {
    return `<option value="${ALL}"${selected === ALL ? ' selected' : ''}>전체 채널 (B2C·B2B 모두 점검)</option>` + PICK.map(k => [k, DATA.channels[k]]).map(([k, v]) => `<option value="${k}"${k === selected ? ' selected' : ''}>${esc(v)}</option>`).join('');
  }
  // 제품 4종 (data.js products)
  function productOf(id) { return (DATA.products || []).find(p => p.id === id) || null; }
  function productLabel(id) { if (id === ALL) return '전체 제품'; const p = productOf(id); return p ? p.ko : '제품 미지정'; }
  function productOptions(selected) {
    return `<option value="${ALL}"${selected === ALL ? ' selected' : ''}>전체 제품 (${(DATA.products || []).length}종 모두 점검)</option>` + (DATA.products || []).map(p => `<option value="${p.id}"${p.id === selected ? ' selected' : ''}>REBOOT ${esc(p.ko)}${p.name ? ` (${esc(p.name)})` : ''} · ${esc(p.form)}</option>`).join('');
  }
  // AI 2차 판정에 넘기는 제품 설명 (영문)
  function productBrief(id) {
    if (id === ALL) return 'all four products (Reboot Jelly Original, Jelly Balance, Drink Original, Drink Balance): flag anything that is a problem for any of them';
    const p = productOf(id);
    if (!p) return '';
    const form = p.id.startsWith('drink') ? 'non-carbonated drink in small bottles (6 per box)' : 'stick jelly, individually wrapped sticks in one pack';
    return `REBOOT ${form}${p.sugarReduced ? ', sugar-reduced recipe (target: less sugar than the original; criteria for a "reduced sugar" claim not yet verified)' : ', original recipe (not sugar-reduced)'}`;
  }
  function toast(msg) {
    if (REBOOT.ui && typeof REBOOT.ui.toast === 'function') REBOOT.ui.toast(msg);
  }
  async function copyText(text) {
    try {
      await global.navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      ta.remove();
      return ok;
    }
  }

  // 번역 블록: 버튼을 누르면 data-tr-text의 영어 원문을 번역해 바로 아래에 보여 줌 (render의 위임 처리)
  function trBlock(text, label) {
    return `<div class="cmp-trblock" data-tr-text="${esc(text)}">
  <button type="button" class="cmp-btn cmp-btn-sm cmp-btn-tr" data-act="translate">${esc(label || '한글로 번역')}</button>
  <div data-tr-out aria-live="polite"></div></div>`;
  }

  async function runTranslate(block) {
    const btn = block.querySelector('[data-act="translate"]');
    const out = block.querySelector('[data-tr-out]');
    btn.disabled = true;
    out.innerHTML = '<p class="cmp-muted">번역 중…</p>';
    const r = await translate(block.dataset.trText, loaded => {
      out.innerHTML = `<p class="cmp-muted">번역 모델 내려받는 중… ${Math.round(loaded * 100)}% (처음 한 번만)</p>`;
    });
    btn.disabled = false;
    if (r.ok) {
      out.innerHTML = `<div class="cmp-tr"><span class="cmp-tr-tag">${r.engine === 'already-ko' ? '이미 한국어 문구' : r.engine === 'ai' ? `AI 번역 · 참고용${r.model ? ` · ${esc(r.model)}` : ''}` : '기계 번역 · 참고용'}</span>
  <p class="cmp-tr-text">${esc(r.text)}</p>${r.note ? `<p class="cmp-muted">${esc(r.note)}</p>` : ''}
  <p class="cmp-muted">판정은 영어 원문 기준입니다. 번역문을 광고에 쓰지 마세요.</p></div>`;
    } else {
      out.innerHTML = `<p class="cmp-note">번역하지 못했습니다 (크롬 내장 번역·서버 AI 번역 모두 실패)${r.message ? ` · ${esc(r.message)}` : ''}.
  ${r.fallbackUrl ? `${link(r.fallbackUrl, 'Google 번역에서 열기 ↗')} <b>문구가 Google로 전송됩니다.</b> 실제 고객 정보가 들어 있으면 열지 마세요.` : ''}</p>`;
    }
  }

  // 접고 펴는 칸 (규제 모니터링 출처와 같은 모양). sum = 접혀 있어도 제목 줄 오른쪽에 보이는 한 줄 요약
  function fold(id, title, sum, body, open) {
    return `<details class="cmp-fold cmp-sec" data-src="${esc(id)}"${open ? ' open' : ''}>
  <summary><span class="cmp-fold-title">${title}</span>${sum ? `<span class="cmp-fold-latest cmp-muted">${sum}</span>` : ''}</summary>
  <div class="cmp-fold-body">${body}</div></details>`;
  }

  // 서버·외부 자료의 링크는 https만 엽니다.
  function link(url, text) {
    return /^https:\/\//i.test(String(url || '')) ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text);
  }

  function highlight(text, terms) {
    const list = terms.filter(t => !t.startsWith('(')).sort((a, b) => b.length - a.length).map(escapeRe);
    if (!list.length) return esc(text);
    const re = new RegExp(list.join('|'), 'gi');
    let out = '';
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      out += esc(text.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
      last = m.index + m[0].length;
    }
    return out + esc(text.slice(last));
  }

  // 다운로드 파일명은 reboot-compliance-내용.확장자 (팀 README 1장)
  function download(what, text) {
    const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `reboot-compliance-${what}.csv`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  /* ───────── CSV ───────── */

  function parseCSV(text) {
    text = String(text).replace(/^\uFEFF/, '');
    const rows = [];
    let row = [];
    let field = '';
    let quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
        } else field += c;
      } else if (c === '"') quoted = true;
      else if (c === ',') { row.push(field); field = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        row.push(field); rows.push(row); row = []; field = '';
      } else field += c;
    }
    if (field !== '' || row.length) { row.push(field); rows.push(row); }
    return rows.filter(r => r.some(v => v.trim() !== ''));
  }

  function toCSV(rows) {
    const cell = v => {
      v = String(v == null ? '' : v);
      if (/^[=+\-@]/.test(v)) v = "'" + v;   // 엑셀 수식으로 실행되지 않게
      return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
    };
    return '\uFEFF' + rows.map(r => r.map(cell).join(',')).join('\r\n');
  }

  /* ───────── 화면 ───────── */

  const TABS = [['single', '단건 심의'], ['batch', '일괄 심의'], ['history', '점검 기록'], ['rules', '규칙·검증'], ['monitor', '규제 모니터링'], ['agencies', '기관 안내']];

  function render(root) {
    root.classList.add('part-compliance');
    root.innerHTML = `
<div class="cmp-wrap">
  <div class="cmp-kpis" data-el="kpis" aria-label="심의 현황"></div>
  <div class="cmp-tabs" role="tablist">
    ${TABS.map(([id, label], i) => `<button type="button" role="tab" class="cmp-tab" data-tab="${id}" aria-selected="${i === 0}">${label}</button>`).join('')}
  </div>
  <section class="cmp-panel" data-panel="single">${singleHTML()}</section>
  <section class="cmp-panel" data-panel="batch" hidden>${batchHTML()}</section>
  <section class="cmp-panel" data-panel="history" hidden></section>
  <section class="cmp-panel" data-panel="rules" hidden>${rulesHTML()}</section>
  <section class="cmp-panel" data-panel="monitor" hidden>${monitorHTML()}</section>
  <section class="cmp-panel" data-panel="agencies" hidden>${agenciesHTML()}</section>
</div>`;

    const showTab = id => {
      root.querySelectorAll('.cmp-tab').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === id)));
      root.querySelectorAll('.cmp-panel').forEach(p => { p.hidden = p.dataset.panel !== id; });
      if (id === 'single') paintSingle(root);
      if (id === 'history') renderHistory(root);
      if (id === 'monitor') renderServer(root);
    };
    root.querySelectorAll('.cmp-tab').forEach(btn => btn.addEventListener('click', () => showTab(btn.dataset.tab)));

    // 모든 탭 공통: 한글 번역 버튼, 걸린 표현 → 자세히 창, 다른 탭으로 이동하는 버튼
    root.addEventListener('click', e => {
      const tr = e.target.closest('[data-act="translate"]');
      if (tr && !tr.disabled) { runTranslate(tr.closest('[data-tr-text]')); return; }
      const td = e.target.closest('[data-act="term-detail"]');
      if (td) { openDetail(root, td.dataset.code, td.dataset.term, ctxOf(td)); return; }
      const kpi = e.target.closest('[data-act="kpi"]');
      if (kpi) {
        const hp = root.querySelector('[data-panel="history"]');
        hp.dataset.filter = kpi.dataset.filter;
        showTab('history');
        hp.scrollIntoView({ block: 'start', behavior: 'smooth' });
        return;
      }
      const sub = e.target.closest('[data-subtab]');
      if (sub) { showMonSub(root, sub.dataset.subtab); return; }
      const btn = e.target.closest('[data-act="goto"]');
      if (!btn) return;
      showTab(btn.dataset.goto);
      if (btn.dataset.agency) {
        showMonSub(root, 'manual');   // Federal Register 조회는 '사람이 확인' 메뉴에 있음
        const sel = root.querySelector('[data-el="fra"]');
        if (sel) {
          sel.value = btn.dataset.agency;
          const d = sel.closest('details');   // 접혀 있던 Federal Register 조회 칸을 펼쳐서 보여 줌
          if (d) { d.open = true; d.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
        }
      }
      root.querySelector(`.cmp-tab[data-tab="${btn.dataset.goto}"]`).focus();
    });

    wireSingle(root);
    wirePersona(root);
    wireBatch(root);
    renderHistory(root);
    wireRules(root);
    wireMonitor(root);
  }

  /* 단건 심의 — 로드맵: ① 문구 입력 → ② 자동 점검 → ③ 수정·재점검 → ④ 사용
     마케터가 자기 대시보드에서 쓰므로 검토 요청·승인 단계는 없습니다. BLOCK 문구만 복사를 막습니다.
     진행 상태(문구·채널·점검한 문구)는 reboot:compliance:journey에 저장해 탭을 옮겨도 이어집니다. */

  const STEPS = [['input', '문구 입력'], ['check', '자동 점검'], ['fix', '수정·재점검'], ['use', '사용']];
  const STEP_ICON = { done: '✓', skip: '–', blocked: '✗' };

  function loadJourney() {
    const j = store.get(KEY.journey, {}) || {};
    return {
      text: String(j.text || ''), channel: PICK.includes(j.channel) || j.channel === ALL ? j.channel : 'b2c',
      // 제품이 일반식품으로 확정돼 단건 심의는 언제나 일반식품 기준 (건강기능식품 비교 칸은 2026-10-23 화면에서 뺌)
      category: 'FOOD', checkedKey: j.checkedKey || '', ko: String(j.ko || ''),
      product: productOf(j.product) || j.product === ALL ? j.product : 'jelly',
      ai: j.ai && typeof j.ai === 'object' && j.ai.result ? j.ai : null   // { key, at, result } — 같은 문구·채널·기준일 때만 보여 줌
    };
  }
  function saveJourney(j) { store.set(KEY.journey, j); }
  function journeyKey(j) { return JSON.stringify([j.text.trim(), j.channel, j.category, j.product || '']); }

  /** 지금 문구가 로드맵 어느 단계인지 계산 */
  function journeyState(j) {
    const text = j.text.trim();
    const r = text && j.checkedKey === journeyKey(j) ? review(text, { channel: j.channel, category: j.category, product: j.product }) : null;
    const st = { input: 'todo', check: 'todo', fix: 'todo', use: 'todo' };
    let msg = '';
    let tone = 'info';
    let canCopy = false;

    if (!text) {
      st.input = 'current';
      msg = '미국에 쓸 영어 문구를 입력하고, 쓰일 채널을 고르세요. 한국어로 먼저 써서 영어로 번역해 넣을 수도 있습니다.';
    } else if (!r) {
      st.input = 'done'; st.check = 'current';
      msg = j.checkedKey ? '문구가 바뀌었습니다. [문구 점검하기]를 다시 눌러 주세요.' : '[문구 점검하기]를 눌러 규칙으로 점검하세요.';
    } else {
      st.input = st.check = 'done';
      if (r.verdict === 'BLOCK') {
        st.fix = 'current'; st.use = 'blocked'; tone = 'block';
        msg = '사용 불가(BLOCK) 표현이 있습니다 → 아래 "대신 쓸 방향"을 참고해 문장을 바꾼 뒤 다시 점검하세요. BLOCK 문구는 복사할 수 없습니다.';
      } else if (r.verdict === 'REVISE') {
        st.fix = 'current'; tone = 'warn'; canCopy = true;
        msg = '수정 필요(REVISE) 표현이 있습니다 → 고쳐서 다시 점검하는 것을 권장합니다. 그대로 쓰려면 아래 이유를 확인한 뒤 복사하세요.';
      } else if (r.verdict === 'PASS') {
        st.fix = 'skip'; st.use = 'current'; tone = 'ok'; canCopy = true;
        msg = '걸린 표현이 없습니다. 다만 PASS는 규칙에서 찾지 못했다는 뜻이지 법적 승인이 아니므로, 최종 사용 여부는 직접 확인하세요.';
      } else {
        st.fix = 'skip'; st.use = 'current'; tone = 'warn'; canCopy = true;
        msg = `${r.verdict} · ${LABEL[r.verdict]} 판정입니다. 아래 이유를 읽고 조건·문맥을 직접 확인한 뒤 사용하세요.`;
      }
      if ((r.unchecked || []).length && r.verdict !== 'BLOCK') {
        tone = tone === 'ok' ? 'warn' : tone;
        msg += ' 당 함량 표현은 규칙이 점검하지 않았습니다(당류 표시 미검토) → 실제 함량을 확인하세요.';
      }
    }
    // AI 2차 판정이 BLOCK인데 규칙은 통과시킨 경우 → 복사 전에 한 번 더 확인 (GPT 검토 ⑨)
    const aiVerdict = r && j.ai && j.ai.key === journeyKey(j) && j.ai.result ? j.ai.result.verdict : '';
    const needConfirm = canCopy && aiVerdict === 'BLOCK';
    if (needConfirm) {
      tone = 'block';
      st.use = 'current';
      msg = `규칙 판정은 ${r.verdict}지만 <b>AI 2차 판정이 사용 불가(BLOCK)</b>로 봤습니다 → 아래 AI 이유를 확인하고 문장을 고치는 것을 권장합니다. 그래도 쓰려면 복사할 때 한 번 더 확인합니다.`;
    }
    const action = canCopy
      ? `<button type="button" class="cmp-btn ${needConfirm ? '' : 'cmp-primary'}" data-act="copy">${needConfirm ? 'AI 경고 확인 후 복사' : '문구 복사'}</button> <button type="button" class="cmp-btn" data-act="new">새 문구 시작</button>`
      : '';
    return { r, st, msg, tone, canCopy, needConfirm, action };
  }

  function roadHTML(st) {
    const current = STEPS.findIndex(([id]) => st[id] === 'current');
    return STEPS.map(([id, label], i) => `
  <li class="cmp-road-step is-${st[id]}"${st[id] === 'current' ? ' aria-current="step"' : ''}>
    <span class="cmp-road-dot" aria-hidden="true">${STEP_ICON[st[id]] || i + 1}</span>
    <span class="cmp-road-label">${label}</span></li>`).join('') +
      (current >= 0 ? `<li class="cmp-sr">현재 ${current + 1}단계 · ${STEPS[current][1]}</li>` : '');
  }

  function singleHTML() {
    return `
<div class="cmp-bench">
  <div class="cmp-card">
    <div class="cmp-card-head"><h3>점검할 문구를 입력하세요</h3><span class="cmp-tag" data-el="cattag">일반식품 기준</span></div>
    <div class="cmp-grid2">
      <label class="cmp-field"><span>사용 채널</span><select class="cmp-input" data-el="channel">${channelOptions('b2c')}</select></label>
      <label class="cmp-field"><span>제품</span><select class="cmp-input" data-el="product">${productOptions('jelly')}</select></label>
    </div>
    <label class="cmp-field"><span>한국어로 쓰기 (선택)</span>
      <textarea class="cmp-input" rows="2" data-el="ko" placeholder="예: 파티 전에 가볍게 챙기는 상큼한 젤리 스틱"></textarea></label>
    <div class="cmp-actions">
      <button type="button" class="cmp-btn" data-act="tr-en" disabled title="한국어 문구를 먼저 입력하세요">${icon('spark')} 영어로 번역해 넣기</button>
    </div>
    <div data-el="trmsg" aria-live="polite"></div>
    <label class="cmp-field"><span>광고 카피 (영어 · 점검 대상)</span>
      <textarea class="cmp-input" rows="5" data-el="text" placeholder="예: A Korean after-party ritual in a jelly stick."></textarea></label>
    <div class="cmp-foot">
      <p class="cmp-muted">입력한 문구와 점검 기록은 팀 공용 서버에 저장됩니다 (팀원 모두 볼 수 있음). 규칙 점검은 이 화면에서 하고, [영어로 번역해 넣기]·[AI 2차 판정]을 누를 때만 문구를 Claude로 보냅니다.</p>
      <div class="cmp-actions">
        <button type="button" class="cmp-btn" data-act="ai" disabled title="먼저 [문구 점검하기]를 누르세요">${icon('spark')} AI 2차 판정</button>
        <button type="button" class="cmp-btn cmp-primary" data-act="run">${icon('shield')} 문구 점검하기</button>
      </div>
    </div>
    <div class="cmp-road-msg" data-el="roadmsg" aria-live="polite"></div>
    <div data-el="result"></div>
    <div data-el="ai" aria-live="polite"></div>
  </div>
  <aside class="cmp-side">
    <div class="cmp-card"><div class="cmp-card-head"><h3>검토 흐름</h3></div>
      <ol class="cmp-road" data-el="road" aria-label="문구 심의 진행 단계"></ol></div>
    <details class="cmp-card cmp-notes cmp-notes-fold"><summary class="cmp-card-head"><h3>알아두세요</h3><span class="cmp-muted">4가지 · 눌러서 보기</span></summary>
      <p><b>PASS</b>는 규칙에서 위험 표현을 찾지 못했다는 뜻이며 법적 승인을 의미하지 않습니다.</p>
      <p><b>BLOCK</b> 문구는 복사할 수 없습니다. 표현을 바꾼 뒤 다시 점검하세요.</p>
      <p>이 점검은 1차 참고용입니다. FDA 공식 판단이나 법률 자문이 아니며, 최종 사용 여부는 문구를 쓰는 사람이 확인합니다.</p>
      <p>당 함량 표시(low·zero·less sugar 등)는 팀 결정에 따라 점검하지 않습니다 (규칙·검증 탭의 R11·R12 '사용 안 함').</p></details>
    <details class="cmp-card cmp-notes cmp-notes-fold cmp-persona"><summary class="cmp-card-head"><h3>AI 검토자 페르소나</h3><span class="cmp-muted" data-el="persona-sum"></span></summary>
      <p class="cmp-muted">[AI 2차 판정]을 누를 때 AI가 이 관점으로 검토합니다. 관점·말투만 바뀌고, FDA·FTC 기준과 ${RULE_SPAN} 판정 단계는 그대로입니다. 저장하면 팀 모두에게 적용됩니다.</p>
      <textarea class="cmp-input" rows="8" maxlength="${PERSONA_MAX}" data-el="persona"></textarea>
      <p class="cmp-muted" data-el="persona-count" aria-live="polite"></p>
      <div class="cmp-actions">
        <button type="button" class="cmp-btn" data-act="persona-reset">기본값으로</button>
        <button type="button" class="cmp-btn cmp-primary" data-act="persona-save">저장</button>
      </div></details>
  </aside>
</div>`;
  }

  /** 상단 KPI 카드 5장 (판정 5단계: COND·REVIEW는 한 장으로) — 점검 기록을 지금 규칙으로 다시 판정한 개수. 누르면 점검 기록 탭에서 그 판정만 보여 줌 */
  const HIST_FILTERS = [['all', '전체'], ['BLOCK', 'BLOCK · 사용 불가'], ['REVISE', 'REVISE · 수정 필요'], ['OTHER', 'COND·REVIEW · 조건부·검토 필요'], ['PASS', 'PASS · 규칙 미탐지']];
  function matchFilter(verdict, f) {
    if (!f || f === 'all') return true;
    if (f === 'OTHER') return verdict === 'COND' || verdict === 'REVIEW';
    return verdict === f;
  }

  function paintKpis(root) {
    const box = root.querySelector('[data-el="kpis"]');
    if (!box) return;
    const list = loadHistory();
    const verdicts = list.map(x => recheck(x).verdict);
    const n = v => verdicts.filter(x => x === v).length;
    const cards = [
      { label: '사용 불가', icon: 'shield', value: n('BLOCK'), sub: '<b>BLOCK</b>표현을 바꾼 뒤 다시 점검', featured: true, filter: 'BLOCK' },
      { label: '수정 필요', icon: 'pencil', value: n('REVISE'), sub: '<b>REVISE</b>고쳐서 다시 점검 권장', filter: 'REVISE' },
      { label: '조건부·검토 필요', icon: 'clock', value: n('COND') + n('REVIEW'), sub: '<b>COND·REVIEW</b>확인 후 사용', filter: 'OTHER',
        tip: 'REVIEW: 후기·리뷰(R10) · 성분 강조(R13) · B2B 자료의 인체적용시험 인용(R9) / COND: 건강기능식품 기준으로 비교할 때만 나옴' },
      { label: '규칙 미탐지', icon: 'check', value: n('PASS'), sub: '<b>PASS</b>법적 승인은 아님', filter: 'PASS' },
      { label: '전체 점검', icon: 'list', value: list.length, sub: '<b>누적</b>팀 공용 점검 기록', filter: 'all' }
    ];
    box.innerHTML = cards.map(c => `
<button type="button" class="cmp-kpi${c.featured ? ' is-featured' : ''}" data-act="kpi" data-filter="${c.filter}" title="점검 기록에서 '${c.label}' 문구 보기${c.tip ? `\n${c.tip}` : ''}">
  <span class="cmp-kpi-top"><span>${c.label}</span><span class="cmp-kpi-icon">${icon(c.icon)}</span></span>
  <span class="cmp-kpi-num">${c.value}<small>건</small></span>
  <span class="cmp-kpi-sub">${c.sub}</span>
</button>`).join('');
  }

  function resultHTML(text, r) {
    return `
<div class="cmp-result">
  <div class="cmp-result-head">${badge(r.verdict)}
    <span class="cmp-muted">${esc(channelLabel(r.channel))} · ${esc(productLabel(r.product))} · ${r.category === 'FOOD' ? '일반식품' : '건강기능식품 기준 비교'}</span></div>
  ${variantNote(r)}
  <p class="cmp-quote">${highlightHits(text, r.findings)}</p>
  ${r.findings.length ? '<p class="cmp-muted cmp-hint">밑줄 친 표현을 누르면 이유·대신 쓸 표현·관련 기관을 자세히 볼 수 있습니다.</p>' : ''}
  ${uncheckedHTML(r)}
  ${trBlock(text)}
  ${r.findings.map(f => `
  <article class="cmp-finding">
    <div class="cmp-finding-head"><b>${f.code} ${esc(f.name)}</b> ${badge(f.verdict)}
      <span class="cmp-basis">${f.basis.map(b => BASIS[b]).join(' · ')}</span>
      <button type="button" class="cmp-btn cmp-btn-sm cmp-more" data-act="term-detail" data-code="${f.code}" data-term="${esc(f.terms[0])}">자세히</button></div>
    <p><span class="cmp-label">걸린 표현</span> ${f.terms.map(t => hitButton(f, t, `<code>${esc(t)}</code>`)).join(' ')}</p>
    <p><span class="cmp-label">이유</span> ${esc(f.reason)}</p>
    <p><span class="cmp-label">대신 쓸 방향</span> ${esc(f.alt)}</p>
  </article>`).join('')}
</div>`;
  }

  // 당 함량 표현: 팀 결정으로 점검하지 않음 → 'PASS(규칙 미탐지)'와 구분해 보여 줌
  function uncheckedHTML(r) {
    const u = r.unchecked || [];
    if (!u.length) return '';
    return `<p class="cmp-note cmp-note-warn"><b>당류 표시 미검토</b> · ${u.map(x => x.terms.map(t => `<code>${esc(t)}</code>`).join(' ')).join(' ')}
  — 당 함량 표현은 팀 결정(2026-10-23)으로 <b>규칙이 점검하지 않습니다</b>. 이 부분은 PASS가 아니라 '보지 않은 표현'이니,
  미국에서는 'low sugar' 정의가 없고 sugar free·reduced sugar는 수치 조건이 있으므로(21 CFR 101.60(c)) 실제 함량을 확인한 뒤 쓰세요.</p>`;
  }

  /* ── 걸린 표현 자세히 보기 (단건 심의) ── */

  function hitButton(f, term, inner) {
    return `<button type="button" class="cmp-hit cmp-hit-${f.verdict}" data-act="term-detail" data-code="${f.code}" data-term="${esc(term)}"
      aria-haspopup="dialog" title="${esc(`${f.code} ${f.name} · ${LABEL[f.verdict]} — 눌러서 자세히 보기`)}">${inner}</button>`;
  }

  // 단건 심의 밖(일괄 심의 표 · 점검 기록 · 규칙 표)에서 연 자세히 창이 어떤 문구 기준인지 알려 주는 속성
  function ctxAttrs(text, opts) {
    opts = opts || {};
    return `data-ctx="1" data-ctx-text="${esc(text || '')}" data-ctx-channel="${esc(opts.channel || 'b2c')}"`
      + ` data-ctx-product="${esc(opts.product || '')}" data-ctx-category="${esc(opts.category || 'FOOD')}"`;
  }
  function ctxOf(el) {
    const box = el.closest('[data-ctx]');
    if (!box) return null;   // 단건 심의: 지금 입력 중인 문구 기준
    const d = box.dataset;
    return { text: d.ctxText || '', channel: d.ctxChannel || 'b2c', product: d.ctxProduct || '', category: d.ctxCategory || 'FOOD' };
  }

  /** 문구 안의 걸린 표현을 누를 수 있는 버튼으로 표시 (한 표현이 여러 규칙에 걸리면 더 엄격한 규칙 기준) */
  function highlightHits(text, findings) {
    const byTerm = new Map();
    findings.forEach(f => f.terms.filter(t => !t.startsWith('(')).forEach(t => {
      const k = t.toLowerCase();
      if (!byTerm.has(k) || RANK[f.verdict] > RANK[byTerm.get(k).verdict]) byTerm.set(k, f);
    }));
    if (!byTerm.size) return esc(text);
    const re = new RegExp([...byTerm.keys()].sort((a, b) => b.length - a.length).map(escapeRe).join('|'), 'gi');
    let out = '';
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      const f = byTerm.get(m[0].toLowerCase());
      out += esc(text.slice(last, m.index)) + (f ? hitButton(f, m[0], esc(m[0])) : `<mark>${esc(m[0])}</mark>`);
      last = m.index + m[0].length;
    }
    return out + esc(text.slice(last));
  }

  /** 기관 안내 데이터에서 이 규칙을 맡는 기관 (예: R1 → FDA) */
  function agenciesFor(code) {
    return (DATA.agencies || []).filter(a => (String(a.rules || '').match(/R\d+/g) || []).includes(code));
  }

  /** 문구에서 표현이 들어 있는 문장 (앞뒤 문맥) */
  function contextOf(text, term) {
    const i = text.toLowerCase().indexOf(String(term).toLowerCase());
    if (i < 0) return '';
    const start = Math.max(text.lastIndexOf('\n', i), Math.max(...['. ', '! ', '? '].map(s => text.lastIndexOf(s, i)))) + 1;
    const endCands = ['\n', '. ', '! ', '? '].map(s => text.indexOf(s, i + term.length)).filter(x => x >= 0);
    const end = endCands.length ? Math.min(...endCands) + 1 : text.length;
    const sentence = text.slice(start, end).trim();
    const j = sentence.toLowerCase().indexOf(String(term).toLowerCase());
    return j < 0 ? esc(sentence)
      : esc(sentence.slice(0, j)) + `<mark>${esc(sentence.slice(j, j + term.length))}</mark>` + esc(sentence.slice(j + term.length));
  }

  const BASIS_HELP = {
    official: 'FDA·FTC 규정이나 경고장 등 공식 문서에 근거가 있습니다.',
    platform: 'Meta·Google 같은 광고 플랫폼의 광고 정책이나 업계 자율 규약에 근거가 있습니다. 법은 아니지만 어기면 광고가 거절됩니다.',
    internal: '법 기준보다 보수적으로 정한 팀 내부 기준입니다.',
    context: '같은 단어라도 문맥에 따라 문제가 되지 않을 수 있어 사람이 판단합니다.'
  };

  /** 자세히 창 내용. c = 문구가 쓰인 맥락 {text, channel, product, category} (규칙 표에서 열면 text 없음)
      r = 그 문구의 판정, ai = 관련 AI 2차 판정 의견, fromSingle = 단건 심의에서 열었는지 (문구 칸 고치기 버튼) */
  function detailHTML(f, term, c, r, ai, fromSingle) {
    const rule = DATA.rules.find(x => x.code === f.code) || {};
    const text = String(c.text || '').trim();
    const ctx = text ? contextOf(text, term) : '';
    const changed = text && rule.verdict && rule.verdict !== f.verdict;
    const agencies = agenciesFor(f.code);
    const maps = (DATA.mappings || []).filter(m => m.code === f.code);
    const quotes = (DATA.warningQuotes || []).filter(q => String(q.text || '').toLowerCase().includes(String(term).toLowerCase())).slice(0, 3);
    const siblings = (rule.terms || []).concat(rule.ko || []).filter(t => t.toLowerCase() !== String(term).toLowerCase());
    const others = r ? r.findings.filter(x => x.code !== f.code && x.terms.some(t => t.toLowerCase() === String(term).toLowerCase())) : [];
    return `
<div class="cmp-dlg-head">
  <div>
    <p class="cmp-dlg-eyebrow">${esc(f.code)} · ${esc(f.name)}</p>
    <h3 id="cmp-dlg-title" class="cmp-dlg-title"><code>${esc(term)}</code> ${badge(f.verdict)}</h3>
  </div>
  <button type="button" class="cmp-dlg-x" data-act="dlg-close" aria-label="닫기">×</button>
</div>
<div class="cmp-dlg-body">
  ${ctx ? `<section><h4>문구 속 위치</h4><p class="cmp-quote">${ctx}</p></section>` : ''}
  <section><h4>왜 문제인가요?</h4><p>${esc(f.reason)}</p>
    ${changed ? `<p class="cmp-note">이 규칙의 기본 판정은 ${badge(rule.verdict)}이고, 이 문구의 채널·제품·기준(${esc(channelLabel(c.channel))} · ${esc(productLabel(c.product))} · ${c.category === 'SUPPLEMENT' ? '건강기능식품 비교' : '일반식품'})에서는 ${badge(f.verdict)}입니다.</p>` : ''}
    ${others.length ? `<p class="cmp-muted">이 표현은 ${others.map(x => `${x.code} ${esc(x.name)}`).join(', ')}에도 걸립니다.</p>` : ''}</section>
  <section><h4>근거 구분</h4><ul class="cmp-dlg-list">${f.basis.map(b => `<li><b>${BASIS[b]}</b> — ${BASIS_HELP[b] || ''}</li>`).join('')}</ul>
    ${(rule.law || []).length ? `<h4>근거 조문</h4><ul class="cmp-dlg-list">${rule.law.map(l => `<li><b>${esc(l.agency)}</b> ${esc(l.cite)}<br><span class="cmp-muted">${esc(l.note || '')}</span></li>`).join('')}</ul>` : ''}</section>
  <section><h4>대신 이렇게 써 보세요</h4><p>${esc(f.alt)}</p>
    ${maps.length ? `<ul class="cmp-dlg-list">${maps.map(m => `<li>${esc(m.ko)} → <b>${esc(m.us)}</b>${m.note ? ` <span class="cmp-muted">(${esc(m.note)})</span>` : ''}</li>`).join('')}</ul>` : ''}</section>
  ${siblings.length ? `<section><h4>같은 규칙에 걸리는 다른 표현</h4><p class="cmp-dlg-chips">${siblings.slice(0, 16).map(t => `<code>${esc(t)}</code>`).join(' ')}</p></section>` : ''}
  ${agencies.length ? `<section><h4>관련 기관</h4>${agencies.map(a => `<p><b>${esc(a.icon || '')} ${esc(a.code)} · ${esc(a.ko)}</b><br><span class="cmp-muted">${esc(a.oneLine || '')}</span>${a.risk ? `<br><span class="cmp-muted">어기면: ${esc(a.risk)}</span>` : ''}</p>`).join('')}</section>` : ''}
  ${quotes.length ? `<section><h4>FDA 경고장 사례</h4>${quotes.map(q => `<div class="cmp-quote">${esc(q.text)}${q.ko ? `<p class="cmp-quote-ko"><span class="cmp-b-tag">한국어</span>${esc(q.ko)}</p>` : '<br>'}<span class="cmp-muted">${esc(q.firm || '')} ${esc(q.issued || '')}${q.kind === 'testimonial' ? ' · 경고장이 인용한 후기' : ''}</span></div>`).join('')}</section>` : ''}
  ${ai.length ? `<section><h4>${icon('spark')} AI 2차 판정 의견 <span class="cmp-muted">(참고용)</span></h4>${ai.map(x => `<p>${badge(LABEL[x.verdict] ? x.verdict : 'REVIEW')} ${esc(x.reason)}${x.suggestion ? `<br><span class="cmp-muted">제안: ${esc(x.suggestion)}</span>` : ''}</p>`).join('')}</section>` : ''}
</div>
<div class="cmp-dlg-foot">
  <button type="button" class="cmp-btn" data-act="goto" data-goto="rules">규칙·검증 탭에서 보기</button>
  ${agencies.length ? `<button type="button" class="cmp-btn" data-act="goto" data-goto="agencies">기관 안내 보기</button>` : ''}
  ${fromSingle ? `<button type="button" class="cmp-btn cmp-primary" data-act="select-term" data-term="${esc(term)}">문구 칸에서 이 표현 고치기</button>` : ''}
</div>`;
  }

  /** 걸린 표현 자세히 보기 창 열기 (<dialog>: Esc·바깥 클릭·닫기 버튼으로 닫힘)
      ctx가 없으면 단건 심의의 지금 문구 기준, 있으면 그 문구(일괄 심의·점검 기록) 또는 규칙 기본값(규칙 표) 기준 */
  function openDetail(root, code, term, ctx) {
    let c;
    let r;
    let ai = [];
    if (ctx) {
      c = ctx;
      r = c.text ? review(c.text, { channel: c.channel, category: c.category, product: c.product }) : null;
    } else {
      const j = loadJourney();
      c = j;
      r = journeyState(j).r;
      if (j.ai && j.ai.key === journeyKey(j) && j.ai.result && Array.isArray(j.ai.result.issues)) {
        ai = j.ai.result.issues.filter(x => x.code === code || String(x.phrase || '').toLowerCase().includes(String(term).toLowerCase()));
      }
    }
    let f = r && r.findings.find(x => x.code === code);
    if (!f) {   // 규칙 표의 예시 표현: 규칙 기본값으로 보여 줌
      const rule = DATA.rules.find(x => x.code === code);
      if (!rule) return;
      f = { code: rule.code, name: rule.name, verdict: rule.verdict, terms: [term], reason: rule.reason, alt: rule.alt, basis: rule.basis.slice() };
    }
    let dlg = root.querySelector('dialog.cmp-dialog');
    if (!dlg) {
      dlg = document.createElement('dialog');
      dlg.className = 'cmp-dialog';
      dlg.setAttribute('aria-labelledby', 'cmp-dlg-title');
      root.querySelector('.cmp-wrap').appendChild(dlg);
      dlg.addEventListener('click', e => {
        if (e.target === dlg) return dlg.close();   // 바깥(배경) 클릭
        const btn = e.target.closest('[data-act]');
        if (!btn) return;
        if (btn.dataset.act === 'dlg-close') dlg.close();
        else if (btn.dataset.act === 'goto') { dlg._opener = null; dlg.close(); }   // 탭 이동은 render의 공통 처리기가 함
        else if (btn.dataset.act === 'select-term') {
          dlg._opener = null;   // 포커스는 문구 칸으로
          dlg.close();
          const ta = root.querySelector('[data-panel="single"] [data-el="text"]');
          const i = ta.value.toLowerCase().indexOf(btn.dataset.term.toLowerCase());
          ta.focus();
          if (i >= 0) ta.setSelectionRange(i, i + btn.dataset.term.length);
        }
      });
      dlg.addEventListener('close', () => { if (dlg._opener && dlg._opener.isConnected) dlg._opener.focus(); });
    }
    dlg.innerHTML = detailHTML(f, term, c, r, ai, !ctx);
    dlg._opener = document.activeElement;
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    // 창은 언제나 맨 위(문구 속 위치 · 왜 문제인가요?)부터 보이게
    const body = dlg.querySelector('.cmp-dlg-body');
    const x = dlg.querySelector('[data-act="dlg-close"]');
    if (x) x.focus({ preventScroll: true });
    if (body) {
      body.scrollTop = 0;
      requestAnimationFrame(() => { body.scrollTop = 0; });
    }
  }

  /** 저장된 진행 상태로 로드맵·안내·결과를 다시 그림 */
  function paintSingle(root) {
    const p = root.querySelector('[data-panel="single"]');
    const el = n => p.querySelector(`[data-el="${n}"]`);
    const j = loadJourney();
    const s = journeyState(j);
    el('road').innerHTML = roadHTML(s.st);
    el('roadmsg').className = `cmp-road-msg cmp-road-${s.tone}`;
    el('roadmsg').innerHTML = `<p>${s.msg}</p>${s.action ? `<div class="cmp-actions">${s.action}</div>` : ''}`;
    el('cattag').textContent = `REBOOT ${productLabel(j.product)} · ${j.category === 'SUPPLEMENT' ? '건강기능식품 기준 비교' : '일반식품 기준'}`;
    const key = s.r ? journeyKey(j) : '-';
    if (el('result').dataset.key !== key) {   // 결과가 같으면 다시 그리지 않음 (열어 둔 번역 유지)
      el('result').innerHTML = s.r ? resultHTML(j.text.trim(), s.r)
        : `<div class="cmp-empty">${icon('shield')}<span>점검 결과와 대신 쓸 표현이 여기에 표시됩니다.</span></div>`;
      el('result').dataset.key = key;
    }
    // AI 2차 판정: 지금 문구·채널·기준으로 받은 결과만 보여 줌
    const ai = s.r && j.ai && j.ai.key === journeyKey(j) ? j.ai : null;
    const aiKey = ai ? `${ai.key}|${ai.at}` : '-';
    const aiErrShown = el('ai').dataset.key === 'error' && el('ai').dataset.errFor === journeyKey(j);   // 실패 안내는 문구가 바뀔 때까지 유지
    if (p.dataset.aiBusy !== '1' && !aiErrShown && el('ai').dataset.key !== aiKey) {
      el('ai').innerHTML = ai ? aiHTML(ai, s.r.verdict) : '';
      el('ai').dataset.key = aiKey;
    }
    const aiBtn = p.querySelector('[data-act="ai"]');
    const trBtn = p.querySelector('[data-act="tr-en"]');
    serverHealth().then(h => {
      const busy = p.dataset.aiBusy === '1';
      aiBtn.disabled = busy || !s.r || !(h && h.ai);
      aiBtn.title = !h ? '서버 상태를 확인하지 못했습니다. 새로고침해 주세요 (키는 서버에만 있음)'
        : !h.ai ? 'ANTHROPIC_API_KEY가 없습니다 (설정 › 연결에서 입력)'
        : !s.r ? '먼저 [문구 점검하기]를 누르세요' : 'Claude가 규칙이 놓친 위험과 더 안전한 문장을 참고용으로 제안합니다';
      const hasKo = !!el('ko').value.trim();
      trBtn.disabled = p.dataset.trBusy === '1' || !hasKo || !(h && h.ai);
      trBtn.title = !h ? '서버 상태를 확인하지 못했습니다. 새로고침해 주세요 (키는 서버에만 있음)'
        : !h.ai ? 'ANTHROPIC_API_KEY가 없습니다 (설정 › 연결에서 입력)'
        : !hasKo ? '한국어 문구를 먼저 입력하세요' : 'Claude가 뜻을 그대로 영어 광고 문구로 옮겨 아래 칸에 넣고 바로 점검합니다';
    });
    paintKpis(root);
  }

  function aiHTML(ai, ruleVerdict) {
    const r = ai.result || {};
    const v = LABEL[r.verdict] ? r.verdict : 'REVIEW';
    const conf = { low: '낮음', medium: '보통', high: '높음' }[r.confidence] || '—';
    const cmp = RANK[v] > RANK[ruleVerdict]
      ? `<p class="cmp-note cmp-note-warn">AI가 규칙 판정(${ruleVerdict})보다 엄격하게 봤습니다 → 문구를 쓰기 전에 AI 의견도 함께 확인하세요.</p>`
      : RANK[v] < RANK[ruleVerdict]
        ? `<p class="cmp-note">규칙 판정(${ruleVerdict})이 우선합니다. AI가 더 느슨하게 봐도 규칙에 걸린 표현은 고쳐야 합니다.</p>` : '';
    const issues = Array.isArray(r.issues) ? r.issues : [];
    return `
<div class="cmp-ai">
  <div class="cmp-result-head"><b class="cmp-ai-title">${icon('spark')} AI 2차 판정</b>${badge(v)}
    <span class="cmp-tag">확신 ${conf}</span><span class="cmp-muted">${esc(ai.model || '')} · ${fmt(ai.at)} · 참고용</span></div>
  ${r.summary ? `<p class="cmp-ai-summary">${esc(r.summary)}</p>` : ''}
  ${Array.isArray(r.checks) && r.checks.length ? `<p class="cmp-muted"><span class="cmp-label">AI가 확인한 것</span> ${r.checks.map(esc).join(' · ')}</p>` : ''}
  ${r.uncertain ? `<p class="cmp-muted"><span class="cmp-label">확인하지 못한 것</span> ${esc(r.uncertain)}</p>` : ''}
  ${cmp}
  ${issues.map(x => `
  <article class="cmp-finding">
    <div class="cmp-finding-head"><code>${esc(x.phrase)}</code> ${badge(LABEL[x.verdict] ? x.verdict : 'REVIEW')} <span class="cmp-basis">${esc(x.code)}</span></div>
    <p><span class="cmp-label">이유</span> ${esc(x.reason)}</p>
    ${x.suggestion ? `<p><span class="cmp-label">제안</span> ${esc(x.suggestion)}</p>` : ''}
  </article>`).join('')}
  ${r.rewrite ? `
  <div class="cmp-ai-rewrite">
    <span class="cmp-label">더 안전한 문장 (제안)</span>
    <p class="cmp-quote">${esc(r.rewrite)}</p>
    <div class="cmp-actions"><button type="button" class="cmp-btn" data-act="ai-apply">이 문장으로 바꾸고 다시 점검</button></div>
  </div>` : ''}
  <p class="cmp-muted">AI 판정은 규칙 판정을 대신하지 않으며 법률 자문이 아닙니다. 제안 문장도 규칙 점검을 다시 거쳐야 합니다.</p>
</div>`;
  }

  function wireSingle(root) {
    const p = root.querySelector('[data-panel="single"]');
    const el = n => p.querySelector(`[data-el="${n}"]`);
    const saved = loadJourney();
    el('text').value = saved.text;
    el('channel').value = saved.channel;
    el('product').value = saved.product;
    el('ko').value = saved.ko;

    // 입력값을 진행 상태에 반영
    const sync = () => {
      const cur = loadJourney();
      const next = { text: el('text').value, ko: el('ko').value, channel: el('channel').value, product: el('product').value, category: 'FOOD', checkedKey: cur.checkedKey, ai: cur.ai };
      saveJourney(next);
      return next;
    };

    // 점검: 결과를 보여 주고 점검 기록에 남김
    const run = () => {
      const j = sync();
      const text = j.text.trim();
      j.checkedKey = text ? journeyKey(j) : '';
      saveJourney(j);
      if (text) recordCheck(text, review(text, { channel: j.channel, category: j.category, product: j.product }));
      paintSingle(root);
    };

    p.querySelector('[data-act="run"]').addEventListener('click', run);
    el('text').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) run(); });
    el('text').addEventListener('input', () => { sync(); paintSingle(root); });
    el('ko').addEventListener('input', () => { sync(); paintSingle(root); });
    el('channel').addEventListener('change', () => { sync(); paintSingle(root); });
    el('product').addEventListener('change', () => { sync(); paintSingle(root); });

    p.addEventListener('click', e => {
      const btn = e.target.closest('[data-act]');
      if (!btn || btn.disabled) return;
      const j = loadJourney();
      if (btn.dataset.act === 'copy') {
        // BLOCK 문구는 복사 버튼이 나오지 않지만, 혹시 상태가 바뀌었으면 한 번 더 막음
        const s = journeyState(j);
        if (!s.canCopy) return toast('BLOCK(사용 불가) 문구는 복사할 수 없습니다. 표현을 바꾼 뒤 다시 점검하세요.');
        if (s.needConfirm && !global.confirm('AI 2차 판정이 이 문구를 사용 불가(BLOCK)로 봤습니다.\n규칙에는 걸리지 않았지만 AI가 위험하다고 본 이유를 확인했고, 그래도 복사할까요?')) return;
        copyText(j.text.trim()).then(ok => toast(ok
          ? (s.r.verdict === 'PASS' ? '문구를 복사했습니다. PASS는 법적 승인이 아니니 최종 확인은 직접 해 주세요.' : `문구를 복사했습니다. ${s.r.verdict} 판정 이유를 확인하고 쓰세요.`)
          : '복사하지 못했습니다. 문구 칸에서 직접 복사하세요.'));
      } else if (btn.dataset.act === 'new') {
        saveJourney({ text: '', ko: '', channel: j.channel, product: j.product, category: j.category, checkedKey: '' });
        el('text').value = '';
        el('ko').value = '';
        el('trmsg').innerHTML = '';
        paintSingle(root);
        el('ko').focus();
      } else if (btn.dataset.act === 'tr-en') {
        runTranslateEn(root, run);
      } else if (btn.dataset.act === 'ai') {
        runAi(root, j);
      } else if (btn.dataset.act === 'ai-apply') {
        const rw = j.ai && j.ai.result && j.ai.result.rewrite;
        if (!rw) return;
        el('text').value = rw;
        run();   // 바꾼 문장은 규칙으로 다시 점검 (AI 결과는 문구가 바뀌어 자동으로 숨김)
        el('text').focus();
      }
    });

    paintSingle(root);
  }

  /** 한→영 번역 (Claude · 서버): 한국어 칸의 문구를 영어로 옮겨 광고 카피 칸에 넣고 바로 점검 */
  async function runTranslateEn(root, run) {
    const p = root.querySelector('[data-panel="single"]');
    const el = n => p.querySelector(`[data-el="${n}"]`);
    const ko = el('ko').value.trim();
    if (!ko || p.dataset.trBusy === '1') return;
    const ta = el('text');
    if (ta.value.trim() && ta.value.trim() !== (p.dataset.trLast || '')
      && !global.confirm('광고 카피 칸에 있는 영어 문구를 번역 결과로 바꿉니다. 계속할까요?')) return;
    p.dataset.trBusy = '1';
    el('trmsg').innerHTML = `<p class="cmp-muted">${icon('spark')} 영어로 옮기는 중입니다… (보통 5~15초)</p>`;
    paintSingle(root);
    try {
      const res = await api('/api/compliance/translate', { text: ko });
      ta.value = res.text || '';
      p.dataset.trLast = ta.value.trim();
      el('trmsg').innerHTML = `<div class="cmp-tr"><span class="cmp-tr-tag">기계 번역 · ${esc(res.model || 'Claude')}</span>
  <p class="cmp-muted">뜻을 그대로 옮긴 영어 문구를 아래 칸에 넣고 바로 점검했습니다. 번역은 효능 표현을 걸러 주지 않으니 점검 결과를 꼭 확인하세요.${res.note_ko ? ` · ${esc(res.note_ko)}` : ''}</p></div>`;
      p.dataset.trBusy = '';
      run();
    } catch (e) {
      p.dataset.trBusy = '';
      el('trmsg').innerHTML = `<p class="cmp-note cmp-note-block">번역 실패: ${esc(e.message)}</p>`;
      paintSingle(root);
    }
  }

  /** AI 검토자 페르소나: 팀이 저장한 값 → 없으면 data.js 기본값 */
  function persona() {
    const saved = store.get(KEY.persona, null);
    return String(saved && typeof saved.text === 'string' ? saved.text : DATA.aiPersona || '').slice(0, PERSONA_MAX);
  }

  function wirePersona(root) {
    const p = root.querySelector('[data-panel="single"]');
    const box = p.querySelector('[data-el="persona"]');
    const count = p.querySelector('[data-el="persona-count"]');
    const sum = p.querySelector('[data-el="persona-sum"]');
    const paint = () => {
      const saved = store.get(KEY.persona, null);
      count.textContent = `${box.value.length} / ${PERSONA_MAX}자` + (box.value.trim() !== persona() ? ' · 저장 안 됨' : '');
      sum.textContent = saved && saved.text != null ? `팀 수정본 · ${saved.by || ''} ${fmt(saved.at)}`.trim() : '기본값';
    };
    box.value = persona();
    paint();
    box.addEventListener('input', paint);
    p.querySelector('[data-act="persona-save"]').addEventListener('click', () => {
      const text = box.value.trim();
      if (!text) return toast('페르소나가 비어 있습니다. [기본값으로]를 누르면 기본 문안으로 돌아갑니다.');
      store.set(KEY.persona, { text, by: (REBOOT.user() || {}).name || '', at: Date.now() });
      box.value = text;
      paint();
      toast('페르소나를 저장했습니다. 다음 AI 2차 판정부터 적용됩니다.');
    });
    p.querySelector('[data-act="persona-reset"]').addEventListener('click', () => {
      if (!global.confirm('팀이 저장한 페르소나를 지우고 기본 문안으로 돌아갈까요?')) return;
      store.remove(KEY.persona);
      box.value = persona();
      paint();
      toast('기본 페르소나로 돌아갔습니다.');
    });
  }

  /** AI 2차 판정 요청 → 진행 상태(journey.ai)에 저장 */
  async function runAi(root, j) {
    const p = root.querySelector('[data-panel="single"]');
    const box = p.querySelector('[data-el="ai"]');
    const s = journeyState(j);
    if (!s.r || p.dataset.aiBusy === '1') return;
    const key = journeyKey(j);
    p.dataset.aiBusy = '1';
    p.querySelector('[data-act="ai"]').disabled = true;
    box.innerHTML = `<div class="cmp-ai cmp-ai-wait"><p class="cmp-muted">${icon('spark')} AI가 문구를 검토하는 중입니다… (보통 10~40초)</p></div>`;
    box.dataset.key = 'busy';
    try {
      const result = await api('/api/compliance/ai-review', {
        text: j.text.trim(), channel: j.channel === ALL ? 'all channels (B2C ads, social and Amazon listing; B2B buyer deck): flag anything that is a problem in any of them' : DATA.channels[j.channel] || j.channel, category: j.category, product: productBrief(j.product),
        rule: { verdict: s.r.verdict, codes: s.r.codes, terms: s.r.findings.flatMap(f => f.terms) },
        rules: rules().map(r => ({ code: r.code, name: r.name, verdict: r.verdict, reason: r.reason })),
        persona: persona()
      });
      const cur = loadJourney();
      if (journeyKey(cur) === key) {   // 기다리는 동안 문구가 바뀌었으면 버림
        cur.ai = { key, at: Date.now(), model: result.model || '', result };
        saveJourney(cur);
      }
      p.dataset.aiBusy = '';
      box.dataset.key = '';
      paintSingle(root);
    } catch (e) {
      p.dataset.aiBusy = '';
      box.innerHTML = `<p class="cmp-note cmp-note-block">AI 2차 판정 실패: ${esc(e.message)}</p>`;
      box.dataset.key = 'error';
      box.dataset.errFor = key;
      paintSingle(root);
    }
  }

  /* 일괄 심의 */

  function batchHTML() {
    const n = (DATA.batchSample || []).length;
    return `
<div class="cmp-card-head"><h3>CSV 일괄 심의</h3><span class="cmp-tag">${RULE_SPAN} · 1차 참고용</span></div>
<p class="cmp-muted"><code>copy</code> 열이 있는 CSV를 올리면 한 줄씩 판정합니다 (최대 1MB · ${MAX_ROWS}행 · UTF-8 권장). <b>PASS(규칙 미탐지)는 법적 승인이 아닙니다.</b><br>
  <b>한국어 문구</b>는 서버 AI(Claude)가 영어로 옮긴 뒤 <b>영어와 한국어를 둘 다</b> 점검합니다 (뜻 그대로 번역 · 1회 소액 과금). 결과에는 한국어 원문 아래에 영어 번역과 걸린 표현이 함께 나옵니다.<br>
  <code>channel</code>·<code>product</code> 열이 있으면 줄마다 그 채널·제품으로 점검합니다 (값: <code>${Object.keys(DATA.channels).join(' · ')} · all</code> / <code>${(DATA.products || []).map(p => p.id).join(' · ')} · all</code>, 한국어 이름도 됨). 없으면 아래 선택값을 씁니다.</p>
<div class="cmp-row">
  <label class="cmp-field"><span>CSV 파일</span><input type="file" accept=".csv,text/csv" class="cmp-input" data-el="file"></label>
  <label class="cmp-field"><span>채널 (열이 없을 때)</span><select class="cmp-input" data-el="channel">${channelOptions(ALL)}</select></label>
  <label class="cmp-field"><span>제품 (열이 없을 때)</span><select class="cmp-input" data-el="product">${productOptions(ALL)}</select></label>
</div>
<div class="cmp-actions">
  <button type="button" class="cmp-btn" data-act="sample">양식 CSV 받기</button>
  <button type="button" class="cmp-btn" data-act="dummy-csv">더미 문구 ${n}개 CSV 받기 (한국어 · 채널·제품 전체)</button>
  <button type="button" class="cmp-btn" data-act="dummy-run">더미 ${n}개로 바로 심의</button>
  <button type="button" class="cmp-btn cmp-primary" data-act="download" disabled>결과 CSV 받기</button>
</div>
<div data-el="trmsg" aria-live="polite"></div>
<div data-el="out" aria-live="polite"></div>`;
  }

  const HANGUL = /[가-힣]/;
  const TR_CHUNK = 200;   // 서버 /translate-batch 한 번에 받는 줄 수

  // CSV의 channel·product 칸: 코드(b2c·jelly…) · 'all' · '전체…' · 한국어 이름을 받음. 모르면 기본값
  function rowChannel(v, fallback) {
    v = String(v || '').trim();
    if (!v) return fallback;
    if (v.toLowerCase() === ALL || v.startsWith('전체')) return ALL;
    if (DATA.channels[v.toLowerCase()]) return v.toLowerCase();
    return Object.keys(DATA.channels).find(k => DATA.channels[k] === v) || fallback;
  }
  function rowProduct(v, fallback) {
    v = String(v || '').trim();
    if (!v) return fallback;
    if (v.toLowerCase() === ALL || v.startsWith('전체')) return ALL;
    const p = (DATA.products || []).find(x => x.id === v.toLowerCase() || x.ko === v || x.name === v || `REBOOT ${x.ko}` === v);
    return p ? p.id : fallback;
  }

  /** 한국어 판정과 영어 판정 합치기: 규칙마다 더 엄격한 쪽, 전체 판정도 더 엄격한 쪽 */
  function mergeResults(a, b) {
    if (!b) return a;
    const byCode = {};
    a.findings.concat(b.findings).forEach(f => {
      const cur = byCode[f.code];
      if (!cur) byCode[f.code] = Object.assign({}, f, { terms: f.terms.slice() });
      else {
        if (RANK[f.verdict] > RANK[cur.verdict]) Object.assign(cur, { verdict: f.verdict, reason: f.reason });
        cur.terms = dedupe(cur.terms.concat(f.terms));
      }
    });
    const findings = RULES.map(r => byCode[r.code]).filter(Boolean);
    const verdict = RANK[b.verdict] > RANK[a.verdict] ? b.verdict : a.verdict;
    return Object.assign({}, a, { verdict, findings, codes: findings.map(f => f.code), variants: (a.variants || []).concat(b.variants || []) });
  }

  function wireBatch(root) {
    const p = root.querySelector('[data-panel="batch"]');
    const el = n => p.querySelector(`[data-el="${n}"]`);
    const dlBtn = p.querySelector('[data-act="download"]');
    let rows = null;      // [{ src, ko, en, note, channel, product, colChannel, colProduct }]
    let results = null;

    function judge(x) {
      const opts = { channel: x.channel, product: x.product };
      const rEn = x.en ? review(x.en, opts) : null;
      const rKo = x.ko ? review(x.src, opts) : null;
      return { en: rEn, ko: rKo, r: rEn && rKo ? mergeResults(rEn, rKo) : rEn || rKo || review(x.src, opts) };
    }

    function textCell(x, j) {
      if (!x.ko) return highlightHits(x.src, j.r.findings);
      return `<div class="cmp-b-ko">${highlightHits(x.src, j.ko.findings)}</div>
        <div class="cmp-b-en"><span class="cmp-b-tag">영어 번역</span>${x.en ? highlightHits(x.en, j.en.findings) : `<span class="cmp-muted">번역 안 됨 · 한국어 표현만 점검</span>`}
        ${x.note ? `<span class="cmp-b-note">${esc(x.note)}</span>` : ''}</div>`;
    }

    function run() {
      if (!rows) return;
      rows.forEach(x => {
        if (!x.colChannel) x.channel = el('channel').value;
        if (!x.colProduct) x.product = el('product').value;
      });
      results = rows.map(x => Object.assign({ x }, judge(x)));
      const count = { BLOCK: 0, REVISE: 0, COND: 0, REVIEW: 0, PASS: 0 };
      results.forEach(y => { count[y.r.verdict]++; });
      el('out').innerHTML = `
<p class="cmp-summary">${results.length}행 판정 · ${Object.keys(count).map(v => `${badge(v)} ${count[v]}`).join(' ')}</p>
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>#</th><th>문구${rows.some(x => x.ko) ? ' (한국어 원문 · 아래 영어 번역)' : ''}</th><th>채널 · 제품</th><th>판정</th><th>규칙</th><th>걸린 표현</th></tr></thead>
  <tbody>${results.map((y, i) => `<tr ${ctxAttrs(y.x.en || y.x.src, { channel: y.x.channel, product: y.x.product })}><td>${i + 1}</td><td>${textCell(y.x, y)}</td>
    <td class="cmp-nowrap">${esc(channelShort(y.x.channel))}<br><span class="cmp-muted">${esc(productLabel(y.x.product))}</span></td>
    <td>${badge(y.r.verdict)}${variantNote(y.r) ? '<br><span class="cmp-muted" title="채널·제품에 따라 판정이 다름">조합별 차이 있음</span>' : ''}${(y.r.unchecked || []).length ? '<br><span class="cmp-chip cmp-chip-manual" title="당 함량 표현은 팀 결정으로 규칙이 점검하지 않음 · 실제 함량 확인">당류 미검토</span>' : ''}</td>
    <td>${y.r.codes.join(', ') || '—'}</td><td>${y.r.findings.flatMap(f => f.terms.map(t => t.startsWith('(') ? esc(t) : hitButton(f, t, `<code>${esc(t)}</code>`))).join(' ') || '—'}</td></tr>`).join('')}</tbody>
</table></div>
${results.some(y => y.r.findings.length) ? '<p class="cmp-muted cmp-hint">걸린 표현을 누르면 이유·대신 쓸 표현·관련 기관을 자세히 볼 수 있습니다.</p>' : ''}`;
      dlBtn.disabled = false;
    }

    /** 한국어 줄만 모아 서버에서 한 번에 번역 → 실패하면 fallback(더미의 예시 번역) 또는 한국어만 점검 */
    async function translateRows(fallback) {
      const need = rows.filter(x => x.ko && !x.en);
      if (!need.length) return;
      el('trmsg').innerHTML = `<p class="cmp-muted">${icon('spark')} 한국어 ${need.length}줄을 영어로 옮기는 중입니다… (보통 10~40초)</p>`;
      try {
        let model = '';
        for (let i = 0; i < need.length; i += TR_CHUNK) {
          const part = need.slice(i, i + TR_CHUNK);
          const res = await api('/api/compliance/translate-batch', { texts: part.map(x => x.src) });
          model = res.model || model;
          res.items.forEach((t, k) => { part[k].en = t.text; part[k].note = t.note_ko; });
        }
        el('trmsg').innerHTML = `<div class="cmp-tr"><span class="cmp-tr-tag">기계 번역 · ${esc(model || 'Claude')}</span>
  <p class="cmp-muted">한국어 ${need.length}줄을 뜻 그대로 영어로 옮겨 점검했습니다. 판정은 영어·한국어 중 더 엄격한 쪽입니다.</p></div>`;
      } catch (e) {
        if (fallback) need.forEach(x => { x.en = fallback.get(x) || ''; });
        el('trmsg').innerHTML = `<p class="cmp-note cmp-note-block">AI 번역 실패: ${esc(e.message)} · ${fallback ? '더미에 들어 있는 예시 번역으로 점검했습니다.' : '한국어 표현만 점검했습니다.'}</p>`;
      }
    }

    function reject(msg) {
      rows = null; results = null; dlBtn.disabled = true;
      el('out').innerHTML = `<p class="cmp-note cmp-note-block">${msg}</p>`;
    }

    el('file').addEventListener('change', async () => {
      const file = el('file').files[0];
      if (!file) return;
      if (file.size > MAX_CSV_BYTES) return reject(`파일이 1MB를 넘습니다 (${Math.ceil(file.size / 1024)}KB). 나눠서 올려 주세요.`);
      const table = parseCSV(await file.text());
      const header = (table[0] || []).map(h => h.trim().toLowerCase());
      const col = header.indexOf('copy');
      if (col === -1) return reject('첫 줄에 <code>copy</code> 열이 없습니다.');
      const cCh = header.indexOf('channel'), cPr = header.indexOf('product');
      const body = table.slice(1).filter(r => (r[col] || '').trim());
      rows = body.slice(0, MAX_ROWS).map(r => {
        const src = (r[col] || '').trim();
        const colChannel = cCh >= 0 && (r[cCh] || '').trim() ? rowChannel(r[cCh], null) : null;
        const colProduct = cPr >= 0 && (r[cPr] || '').trim() ? rowProduct(r[cPr], null) : null;
        return { src, ko: HANGUL.test(src), en: '', note: '', colChannel, colProduct, channel: colChannel, product: colProduct };
      });
      el('trmsg').innerHTML = '';
      await translateRows(null);
      run();
      if (body.length > MAX_ROWS) {
        el('out').insertAdjacentHTML('afterbegin', `<p class="cmp-note">${body.length}행 중 앞의 ${MAX_ROWS}행만 판정했습니다.</p>`);
      }
    });
    el('channel').addEventListener('change', run);
    el('product').addEventListener('change', run);

    dlBtn.addEventListener('click', () => {
      if (!results) return;
      const out = [['copy', 'english', 'channel', 'product', 'verdict', 'label', 'codes', 'terms']].concat(results.map(y => [
        y.x.src, y.x.en || '', y.x.channel, y.x.product, y.r.verdict, LABEL[y.r.verdict], y.r.codes.join(' '), y.r.findings.flatMap(f => f.terms).join('; ')]));
      download('review', toCSV(out));
    });

    p.querySelector('[data-act="sample"]').addEventListener('click', () => {
      const out = [['copy', 'channel', 'product']].concat(DATA.safePhrases.map(s => [s.text, '', '']), DATA.selfTest.filter(t => !t.channel && !t.category).map(t => [t.text, '', '']));
      download('sample', toCSV(out));
    });
    // 더미 문구: 한국어(copy) · 줄마다 채널·제품(전체 포함) · 예상 판정 · 예시 영어 번역 (판정에는 copy·channel·product만 씀)
    p.querySelector('[data-act="dummy-csv"]').addEventListener('click', () => {
      download('batch-dummy-20', toCSV([['copy', 'channel', 'product', 'expect', 'note', 'english_example']].concat(
        (DATA.batchSample || []).map(x => [x.ko, x.channel, x.product, x.expect, x.note, x.copy]))));
    });
    p.querySelector('[data-act="dummy-run"]').addEventListener('click', async () => {
      const sample = DATA.batchSample || [];
      rows = sample.map(x => ({ src: x.ko, ko: true, en: '', note: '', colChannel: x.channel, colProduct: x.product, channel: x.channel, product: x.product }));
      const fallback = new Map(rows.map((r, i) => [r, sample[i].copy]));
      el('out').innerHTML = '';
      await translateRows(fallback);
      run();
    });
  }

  /* 점검 기록 (승인·반려 없음 · [문구 점검하기]를 누를 때마다 자동 저장) */

  function historyItemHTML(item) {
    const r = recheck(item);   // 지금 규칙 기준 판정 (걸린 표현을 누르면 자세히 창)
    const changed = LABEL[item.verdict] && item.verdict !== r.verdict;
    return `
<article class="cmp-hist" data-id="${esc(item.id)}" ${ctxAttrs(item.text, r)}>
  <div class="cmp-finding-head">${badge(r.verdict)}
    <span class="cmp-muted">${esc(channelLabel(r.channel))}${r.product ? ` · ${esc(productLabel(r.product))}` : ''} · 점검 ${fmt(item.checkedAt || item.requestedAt)}${item.rulesVersion ? ` · 규칙 ${esc(item.rulesVersion)}` : ''}</span></div>
  <p class="cmp-quote">${highlightHits(item.text, r.findings)}</p>
  ${changed ? `<p class="cmp-muted">기록할 때 판정은 ${badge(item.verdict)}였습니다${item.rulesVersion ? ` (규칙 ${esc(item.rulesVersion)}${(item.hits || []).length ? ` · 당시 걸린 표현: ${item.hits.map(h => `${esc(h.code)} ${h.terms.map(esc).join(', ')}`).join(' / ')}` : ''})` : ''}. 규칙이 바뀌어 지금 기준으로 다시 판정해 보여 줍니다.</p>` : ''}
  ${trBlock(item.text)}
</article>`;
  }

  function renderHistory(root) {
    const p = root.querySelector('[data-panel="history"]');
    const all = loadHistory();
    const filter = HIST_FILTERS.some(([id]) => id === p.dataset.filter) ? p.dataset.filter : 'all';
    const list = all.filter(x => matchFilter(recheck(x).verdict, filter));
    paintKpis(root);
    p.innerHTML = `
<div class="cmp-card-head"><h3>점검 기록</h3><span class="cmp-tag">[문구 점검하기]를 누를 때마다 자동 저장</span></div>
<p class="cmp-muted">팀 공용 서버에 최근 500건까지 남습니다. 걸린 표현을 누르면 자세한 설명 창이 열립니다. 위 숫자 카드를 누르면 그 판정만 볼 수 있습니다.</p>
<div class="cmp-row">
  <label class="cmp-field"><span>판정</span><select class="cmp-input" data-el="hist-filter">
    ${HIST_FILTERS.map(([id, label]) => `<option value="${id}"${id === filter ? ' selected' : ''}>${label}</option>`).join('')}</select></label>
  <p class="cmp-muted">${list.length}건${filter === 'all' ? '' : ` / 전체 ${all.length}건`}</p>
</div>
${list.length ? '' : `<p class="cmp-muted">${all.length ? '이 판정의 기록이 없습니다.' : '아직 기록이 없습니다. 단건 심의에서 [문구 점검하기]를 누르면 여기에 쌓입니다.'}</p>`}
<div class="cmp-list">${list.map(historyItemHTML).join('')}</div>`;
    p.querySelector('[data-el="hist-filter"]').onchange = e => { p.dataset.filter = e.target.value; renderHistory(root); };
  }

  /* 규칙·검증 */

  function rulesHTML() {
    const active = DATA.rules.filter(r => !r.off).length;
    return `<div class="cmp-folds">` +
      fold('rules-table', `규칙 ${RULE_SPAN}`, `사용 ${active}개 · 사용 안 함 ${DATA.rules.length - active}개`, rulesTableHTML(), true) +
      fold('rules-map', `한국 → 미국 표현 매핑 · ${DATA.mappings.length}쌍`, '한국식 표현을 미국에서 쓸 대안으로', mappingHTML(), false) +
      fold('rules-validate', '규칙 검증 · 검사기 시험', 'FDA 문장을 놓치지 않는지 · 괜찮은 문장을 막지 않는지 · 처음 보는 문장 점수', validateHTML(), true) +
      fold('ai-usage', 'AI 사용량 · 예상 비용 (이번 달)', '컴플라이언스·CS의 Claude 호출 · 팀 API 예산 관리', `
<p class="cmp-muted">AI를 부를 때마다 서버가 모델·토큰을 기록하고 예상 비용을 계산합니다 (단가: Claude API 공식 · 환율은 1달러 ≈ 1,400원으로 어림).
  이번 달 합계가 한도를 넘으면 AI 기능만 멈추고 규칙 판정은 그대로 씁니다. 실제 청구액은 Claude Console의 Usage에서 확인하세요.</p>
<div class="cmp-actions"><button type="button" class="cmp-btn" data-act="ai-usage">새로 보기</button></div>
<div data-el="aiusage" aria-live="polite"></div>`, false) +
      `</div>`;
  }

  /** 규칙 표의 근거 조문 칸: 기관 + 조문 한 줄씩 (설명은 마우스를 올리면) */
  function lawHTML(law) {
    return (law || []).map(l => `<span class="cmp-law-line" title="${esc(l.note || '')}"><b>${esc(l.agency)}</b> ${esc(l.cite)}</span>`).join('') || '<span class="cmp-muted">—</span>';
  }

  function rulesTableHTML() {
    return `
<p class="cmp-muted">근거 구분과 근거 조문은 FDA·FTC 공식 문서 기준으로 정리했습니다 (2026-10-24 · 참고용이며 법률 자문이 아님). '팀'으로 표시된 줄은 법 조항이 아니라 팀이 보수적으로 정한 기준입니다.
  면책 별표(*)를 붙여도 판정은 바뀌지 않습니다. 걸리는 예를 누르면 자세한 설명 창이 열립니다.
  '사용 안 함' 규칙은 팀 결정으로 판정에 쓰지 않습니다 (R11·R12 당 함량 표시 · 2026-10-23).</p>
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>코드</th><th>이름</th><th>기본 판정</th><th>근거 구분</th><th>근거 조문</th><th>걸리는 예</th><th>대신 쓸 방향</th></tr></thead>
  <tbody>${DATA.rules.map(r => `<tr ${ctxAttrs('')}><td class="cmp-nowrap"><b>${r.code}</b></td><td>${esc(r.name)}${r.off ? ' <span class="cmp-chip">사용 안 함</span>' : ''}</td>
    <td class="cmp-nowrap">${r.off ? '<span class="cmp-muted">판정 안 함</span>' : badge(r.verdict)}</td>
    <td class="cmp-nowrap">${r.basis.map(b => BASIS[b]).join('<br>')}</td>
    <td class="cmp-law">${lawHTML(r.law)}</td>
    <td>${r.terms.slice(0, 4).concat(r.ko.slice(0, 2)).map(t => r.off ? `<code>${esc(t)}</code>` : hitButton(r, t, `<code>${esc(t)}</code>`)).join(' ')}</td><td>${esc(r.alt)}</td></tr>`).join('')}</tbody>
</table></div>`;
  }

  function mappingHTML() {
    return `
<p class="cmp-muted">${RULE_SPAN} 기준 초안입니다 (영어 대안은 모두 규칙 점검 PASS 확인). 검토자·검토일이 빈 항목은 규제 담당 검토 전이며, <code>[확인 필요]</code>는 제품 팩트시트로 채웁니다.</p>
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>한국 표현</th><th>미국 대안</th><th>규칙</th><th>검토자</th><th>검토일</th></tr></thead>
  <tbody>${DATA.mappings.map(m => `<tr><td>${esc(m.ko)}</td><td>${esc(m.us)}${m.note ? `<br><span class="cmp-muted">${esc(m.note)}</span>` : ''}</td>
    <td>${esc(m.code)}</td><td>${esc(m.reviewer) || '—'}</td><td>${esc(m.reviewedAt) || '—'}</td></tr>`).join('')}</tbody>
</table></div>`;
  }

  function validateHTML() {
    return `
<p class="cmp-muted"><b>검사기 시험 보기.</b> 위 세 줄은 규칙을 만들 때 본 문장이라 <b>규칙을 고치다 망가뜨리지 않았는지</b> 확인하는 용도입니다 (모두 0이어야 통과).
  마지막 줄 <b>처음 보는 문장 시험</b>은 규칙을 만들 때 쓰지 않은 문장으로 본 <b>진짜 실력 점수</b>입니다.</p>
<div class="cmp-actions"><button type="button" class="cmp-btn cmp-primary" data-act="validate">시험 다시 보기</button></div>
<div data-el="validation" aria-live="polite"></div>`;
  }

  function validate() {
    const quotes = DATA.warningQuotes.map(q => ({ q, r: review(q.text, { channel: 'b2c' }) }));
    const safe = DATA.safePhrases.map(s => ({ s, r: review(s.text, { channel: 'b2c' }) }));
    const self = DATA.selfTest.map(t => ({ t, r: review(t.text, { channel: t.channel || 'b2c', category: t.category, product: t.product }) }));
    // 처음 보는 문장 시험: 위험 문장을 놓친 것(정답이 PASS 아닌데 PASS) · 괜찮은 문장을 막은 것(정답이 PASS인데 다른 판정)
    const evals = (DATA.evalSet || []).map(t => ({ t, r: review(t.text, { channel: 'b2c' }) }));
    const score = list => ({
      n: list.length,
      ok: list.filter(x => x.r.verdict === x.t.expect).length,
      miss: list.filter(x => x.t.expect !== 'PASS' && x.r.verdict === 'PASS'),
      over: list.filter(x => x.t.expect === 'PASS' && x.r.verdict !== 'PASS'),
      wrong: list.filter(x => x.r.verdict !== x.t.expect)
    });
    return {
      quotes, quotesPass: quotes.filter(x => x.r.verdict === 'PASS'),
      safe, safeBad: safe.filter(x => x.r.verdict !== 'PASS'),
      self, selfBad: self.filter(x => x.r.verdict !== x.t.expect),
      gpt: score(evals.filter(x => x.t.src === 'gpt')),
      fresh: score(evals.filter(x => x.t.src !== 'gpt'))
    };
  }

  async function paintUsage(box) {
    box.innerHTML = '<p class="cmp-muted">불러오는 중…</p>';
    try {
      const res = await fetch('/api/compliance/ai-usage', { cache: 'no-store' });
      const u = await res.json();
      if (!res.ok) throw new Error(u.error || `서버 오류 (${res.status})`);
      const won = usd => `${Math.round(usd * u.krw_per_usd).toLocaleString()}원`;
      const pct = u.budget_usd ? Math.min(100, Math.round(u.used_ratio * 100)) : 0;
      box.innerHTML = `
<p class="cmp-summary">${esc(u.month)} · 호출 ${u.calls}회 · 예상 <b>$${u.usd.toFixed(2)}</b> (≈ ${won(u.usd)})
  ${u.budget_usd ? ` · 한도 $${u.budget_usd.toFixed(0)} (≈ ${won(u.budget_usd)}) 중 <b class="${pct >= 80 ? 'cmp-fail' : ''}">${pct}%</b>` : ' · 한도 없음'}</p>
${pct >= 80 ? '<p class="cmp-note cmp-note-block">한도의 80%를 넘었습니다. 시연 전에는 번역 재사용(캐시)을 활용하고 불필요한 AI 호출을 줄이세요.</p>' : ''}
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>파트</th><th>기능</th><th>호출</th><th>입력 토큰</th><th>출력 토큰</th><th>캐시 읽기</th><th>예상 비용</th></tr></thead>
  <tbody>${u.features.length ? u.features.map(f => `<tr><td>${f.part === 'cs' ? '고객 응대' : '컴플라이언스'}</td><td>${esc(f.feature)}</td><td>${f.calls}</td>
    <td>${f.input_tokens.toLocaleString()}</td><td>${f.output_tokens.toLocaleString()}</td><td>${f.cache_read_input_tokens.toLocaleString()}</td>
    <td>$${f.usd.toFixed(3)} <span class="cmp-muted">≈ ${won(f.usd)}</span></td></tr>`).join('') : '<tr><td colspan="7" class="cmp-muted">이번 달 기록 없음</td></tr>'}</tbody>
</table></div>`;
    } catch (e) {
      box.innerHTML = `<p class="cmp-note cmp-note-block">사용량을 불러오지 못했습니다: ${esc(e.message)}</p>`;
    }
  }

  function wireRules(root) {
    const p = root.querySelector('[data-panel="rules"]');
    const usageBox = p.querySelector('[data-el="aiusage"]');
    p.querySelector('[data-act="ai-usage"]').addEventListener('click', () => paintUsage(usageBox));
    const usageFold = p.querySelector('details[data-src="ai-usage"]');
    if (usageFold) usageFold.addEventListener('toggle', () => { if (usageFold.open) paintUsage(usageBox); });
    p.querySelector('[data-act="validate"]').addEventListener('click', () => {
      const v = validate();
      const ok = b => (b ? '<span class="cmp-ok">통과</span>' : '<span class="cmp-fail">실패</span>');
      p.querySelector('[data-el="validation"]').innerHTML = `
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>시험</th><th>문제 수</th><th>결과</th><th>상태</th></tr></thead>
  <tbody>
    <tr><td>FDA가 실제로 문제 삼은 문장 중 <b>놓친 것</b><br><span class="cmp-muted">숙취 제품 경고장 원문 · 하나도 놓치면 안 됨</span></td><td>${v.quotes.length}개</td>
      <td>${v.quotes.length ? `${v.quotesPass.length}개` : '자료 없음'}</td>
      <td>${v.quotes.length ? ok(v.quotesPass.length === 0) : '<span class="cmp-fail">실행 불가 · 시연본 경고장 문구 이관 필요</span>'}</td></tr>
    <tr><td>써도 되는 문장 중 <b>잘못 막은 것</b><br><span class="cmp-muted">팀이 정한 안전 문구</span></td><td>${v.safe.length}개</td><td>${v.safeBad.length}개</td><td>${ok(v.safeBad.length === 0)}</td></tr>
    <tr><td>정답을 정해 둔 연습 문제 중 <b>틀린 것</b><br><span class="cmp-muted">팀 작성 예문 (BLOCK·REVISE·PASS 등)</span></td><td>${v.self.length}개</td><td>${v.selfBad.length}개</td><td>${ok(v.selfBad.length === 0)}</td></tr>
    <tr><td>GPT 검토가 찾은 문제 문장<br><span class="cmp-muted">이번에 규칙을 고친 대상 (2026-10-24)</span></td><td>${v.gpt.n}개</td>
      <td>맞음 ${v.gpt.ok}개</td><td>${ok(v.gpt.ok === v.gpt.n)}</td></tr>
    <tr class="cmp-row-hot"><td><b>처음 보는 문장 시험</b> (진짜 실력)<br><span class="cmp-muted">규칙을 만들 때 쓰지 않은 문장 · 이 문장에 맞춰 규칙을 고치지 않음</span></td><td>${v.fresh.n}개</td>
      <td><b>${v.fresh.n ? Math.round(v.fresh.ok / v.fresh.n * 100) : 0}점</b> (맞음 ${v.fresh.ok}개)<br>위험 문장 놓침 ${v.fresh.miss.length} · 괜찮은 문장 막음 ${v.fresh.over.length}</td>
      <td><span class="cmp-muted">참고 점수</span></td></tr>
  </tbody>
</table></div>
${v.fresh.wrong.length ? `<h4 class="cmp-h4">처음 보는 문장 시험에서 틀린 문장 (규칙이 아직 못 잡는 것 · 사람이 확인)</h4><ul class="cmp-dlg-list">${v.fresh.wrong.map(x => `<li>${esc(x.t.text)} → 정답 ${x.t.expect}, 검사기 ${x.r.verdict}${x.r.codes.length ? ` (${x.r.codes.join(', ')})` : ''}${x.t.note ? ` <span class="cmp-muted">· ${esc(x.t.note)}</span>` : ''}</li>`).join('')}</ul>` : ''}
${v.gpt.wrong.length ? `<h4 class="cmp-h4">GPT가 찾은 문장 중 아직 틀린 것</h4><ul class="cmp-dlg-list">${v.gpt.wrong.map(x => `<li>${esc(x.t.text)} → 정답 ${x.t.expect}, 검사기 ${x.r.verdict}</li>`).join('')}</ul>` : ''}
${v.quotesPass.concat(v.safeBad).length ? `<h4 class="cmp-h4">확인할 문구</h4><ul class="cmp-dlg-list">${v.quotesPass.map(x => `<li>경고장 문구가 PASS: ${esc(x.q.text)}</li>`).join('')}
  ${v.safeBad.map(x => `<li>안전 문구가 ${x.r.verdict}: ${esc(x.s.text)} (${x.r.codes.join(', ')})</li>`).join('')}</ul>` : ''}
${v.selfBad.length ? `<h4 class="cmp-h4">기대와 다른 예문</h4><ul class="cmp-dlg-list">${v.selfBad.map(x => `<li>${esc(x.t.text)} → 기대 ${x.t.expect}, 결과 ${x.r.verdict}</li>`).join('')}</ul>` : ''}
<p class="cmp-muted">CS 답변 템플릿 C1~C10 점검은 '고객 응대 → 답변 템플릿'에서 확인합니다.</p>`;
    });
  }

  /* ───────── 규제 모니터링 ─────────
     ① 서버 수집 결과: tabs/compliance/가 만든 tabs/compliance/static/monitor-data.js(window.REBOOT_MONITOR)를 읽기만 함
        첫 확인은 '기준 저장'만, 이후 🆕 새 항목 · 🔄 변경 표시. 경고장 문구는 '사례집 후보' → 사람이 승인
     ② Federal Register: 키 없이 브라우저에서 바로 (CORS 허용 확인 2026-10-23)
     ③ openFDA 식품 리콜: 키 없이 브라우저에서 바로 (404는 0건) */

  /* 세부 메뉴 (영역별): 이 탭 줄은 core가 왼쪽 메뉴 '규제 모니터링' 아래에 들여 써서 보여 줌 */
  const MON_TABS = [
    ['auto', '자동 수집', 'filter', '서버가 하루 1회 기관 공식 데이터에서 가져옴 · 기관별로 묶어 보여 줌'],
    ['guide', '출처 안내', 'link', '어디서 무엇을 가져와 어디에 쓰나 · 필요한 키 · 지금 상태'],
    ['upload', '직접 올리기', 'doc', '사람이 받은 FDA 경고장 파일·본문을 올림 · 자동 수집 결과에 합쳐짐'],
    ['manual', '사람이 확인', 'user', '서버가 자동으로 못 읽는 사이트 바로가기 · 저장되지 않는 즉석 조회']
  ];
  let MON_TAB = 'auto';

  function monHead(id, extra) {
    const t = MON_TABS.find(x => x[0] === id);
    return `<div class="cmp-card-head"><h3>${t[1]} <span class="cmp-muted cmp-h-sub">${t[3]}</span></h3>${extra || ''}</div>`;
  }

  function showMonSub(root, id) {
    MON_TAB = MON_TABS.some(t => t[0] === id) ? id : 'auto';
    const p = root.querySelector('[data-panel="monitor"]');
    p.querySelectorAll('.cmp-subtab').forEach(b => b.setAttribute('aria-selected', String(b.dataset.subtab === MON_TAB)));
    p.querySelectorAll('.cmp-subpanel').forEach(s => { s.hidden = s.dataset.sub !== MON_TAB; });
  }

  function monitorHTML() {
    const auto = (DATA.monitorSources || []).filter(s => s.modes.includes('auto')).length;
    const hid = id => (id === MON_TAB ? '' : ' hidden');
    return `
<div class="cmp-subtabs" role="tablist" aria-label="규제 모니터링 세부 메뉴">
  ${MON_TABS.map(([id, label, ic]) => `<button type="button" role="tab" class="cmp-subtab" data-subtab="${id}" data-icon="${ic}" aria-selected="${id === MON_TAB}">${label}</button>`).join('')}
</div>
<div class="cmp-subpanel" data-sub="guide"${hid('guide')}>
${monHead('guide')}
<div class="cmp-mon-map">
  <button type="button" data-subtab="auto"><b>자동 수집</b><span class="cmp-muted">서버가 하루 1회 · [지금 수집] · 공식 API ${auto}곳</span></button>
  <button type="button" data-subtab="upload"><b>직접 올리기</b><span class="cmp-muted">사람이 받은 파일·본문을 올림 · FDA 경고장</span></button>
  <button type="button" data-subtab="manual"><b>사람이 확인</b><span class="cmp-muted">자동으로 못 읽는 사이트 · 즉석 조회</span></button>
</div>
<div data-el="mon-guide">${monGuideHTML(null, [])}</div>
</div>
<div class="cmp-subpanel" data-sub="auto"${hid('auto')}>
${monHead('auto', `<button type="button" class="cmp-btn" data-act="monitor-refresh" disabled>${icon('refresh')} 지금 수집</button>`)}
<div data-el="refreshmsg" aria-live="polite"></div>
<div data-el="server"></div>
<div class="cmp-folds">` + fold('setup', '자동 수집 키 신청 · FDA Data Dashboard', '무료 · 경고장 목록과 수입 거부를 매일 자동으로', `
<p class="cmp-muted">FDA 공식 Data Dashboard API 키가 있으면 <b>식품 경고장 목록</b>과 <b>한국산 식품 수입 거부</b>를 매일 자동으로 모읍니다 (키 1개로 둘 다). 무료이며 이메일로 받습니다.</p>
<ol class="cmp-steps">
  <li>${link('https://www.accessdata.fda.gov/scripts/oul', 'OII Unified Logon 열기 ↗')} → 오른쪽 <b>New User</b>의 <b>Create Account</b> 버튼 (이미 계정이 있으면 왼쪽 Email·Password로 Login)</li>
  <li>계정 정보 입력: 이메일 · 이름(First Name) · 성(Last Name) · 소속(Organization, 없으면 <code>Self</code>) · 비밀번호</li>
  <li>신청할 프로그램 목록에서 <b>Data Dashboard API</b>를 골라 접근 요청 (계정을 만든 뒤 로그인해서 요청해도 됨)</li>
  <li>FDA 메일 기다리기: 요청 접수 → 승인·안내 메일. 키는 메일로 오고, 로그인 후 <b>Data Dashboard API 옆 돋보기</b>를 눌러도 보임 · 신청한 이메일이 사용자 이름(Authorization-User)</li>
  <li><code>.env</code>에 <code>FDA_DASHBOARD_USER=신청한 이메일</code> · <code>FDA_DASHBOARD_KEY=받은 키</code>를 넣고 서버를 껐다 켜기</li>
  <li>위 [지금 수집]을 눌러 'FDA 경고장' · 'FDA 수입 거부'에 항목이 생기는지 확인</li>
</ol>
<p class="cmp-muted">안내 문서: ${link('https://datadashboard.fda.gov/oii/api/index.htm', 'Data Dashboard API 사용 안내 ↗')} · 문의 <code>FDADataDashboard@fda.hhs.gov</code>.
  이 API는 경고장 <b>본문 문구는 주지 않으므로</b> 사례집 후보는 '직접 올리기' 메뉴에서 넣습니다.</p>`, false) + `</div>
</div>
<div class="cmp-subpanel" data-sub="upload"${hid('upload')}>
${monHead('upload')}
<div class="cmp-folds">` + fold('letters', 'FDA 경고장 직접 올리기', 'fda.gov가 자동 수집을 막을 때 · XLSX 목록 · 본문 붙여넣기', `
<p class="cmp-muted">fda.gov는 서버의 자동 요청을 봇 차단으로 막습니다 (2026-10-24 확인). 사람이 브라우저로 여는 것은 됩니다. 올린 경고장은 '자동 수집' 결과의 FDA 묶음에 <b>직접 올림</b>으로 합쳐지고, 본문에서 뽑은 인용 문구는 '사례집 후보'로만 들어갑니다 (사람이 승인).</p>
<h4 class="cmp-h4">① 경고장 목록 XLSX</h4>
<ol class="cmp-steps">
  <li>${link('https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/compliance-actions-and-activities/warning-letters', 'FDA 경고장 목록 페이지 열기 ↗')}
    <span class="cmp-muted">(fda.gov → Inspections, Compliance, Enforcement, and Criminal Investigations → Compliance Actions and Activities → Warning Letters)</span></li>
  <li>표 위의 검색 칸에 <code>hangover</code>·<code>dietary supplement</code> 등을 넣어 좁히거나 그대로 둠</li>
  <li>표 위의 <b>Export Excel</b>(엑셀 내보내기) 버튼으로 파일 받기 · 버튼 이름이 다르면 담당에게 알려 주세요</li>
  <li>받은 파일을 아래에 올림 → 최근 365일(REFRESH_LOOKBACK_DAYS) 식품·건강기능식품 경고장만 골라 넣음</li>
</ol>
<div class="cmp-row">
  <label class="cmp-field cmp-grow"><span>XLSX 파일</span><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" class="cmp-input" data-el="wlfile"></label>
  <div class="cmp-actions"><button type="button" class="cmp-btn cmp-primary" data-act="wl-xlsx" disabled>XLSX 올리기</button></div>
</div>
<h4 class="cmp-h4">② 경고장 본문 붙여넣기 → 인용 문구 추출</h4>
<ol class="cmp-steps">
  <li>'자동 수집' 결과의 FDA 경고장 목록에서 제목 링크를 누르거나, 경고장 목록 페이지 표에서 회사 이름을 눌러 경고장을 엶</li>
  <li>주소창의 주소를 복사해 아래 '경고장 주소'에 붙여 넣음 (같은 경고장과 자동으로 묶임)</li>
  <li>경고장 화면에서 <b>Ctrl+A → Ctrl+C</b>로 전체 복사 → '경고장 본문'에 붙여 넣고 [본문에서 문구 뽑기]</li>
</ol>
<p class="cmp-muted">따옴표로 인용된 광고 문구만 뽑습니다 (숙취·음주 관련 문구가 있으면 그것만).</p>
<div class="cmp-grid2">
  <label class="cmp-field"><span>경고장 주소 (선택)</span><input class="cmp-input" data-el="wlurl" placeholder="https://www.fda.gov/…/warning-letters/…"></label>
  <label class="cmp-field"><span>회사 (선택)</span><input class="cmp-input" data-el="wlfirm"></label>
  <label class="cmp-field"><span>발행일 (선택)</span><input class="cmp-input" type="date" data-el="wldate"></label>
  <label class="cmp-field"><span>제목 (선택)</span><input class="cmp-input" data-el="wlsubject" placeholder="예: Unapproved New Drugs/Misbranded"></label>
</div>
<label class="cmp-field"><span>경고장 본문</span><textarea class="cmp-input" rows="6" data-el="wltext"></textarea></label>
<div class="cmp-actions"><button type="button" class="cmp-btn cmp-primary" data-act="wl-text" disabled>본문에서 문구 뽑기</button></div>
<div data-el="wlmsg" aria-live="polite"></div>`, true) + `</div>
</div>
<div class="cmp-subpanel" data-sub="manual"${hid('manual')}>
${monHead('manual')}
<div class="cmp-folds">` + fold('manual', '바로가기로 확인하는 곳', `${(DATA.manualChecks || []).length}곳 · 서버가 자동으로 못 읽는 사이트`, `
<p class="cmp-muted">아래 사이트는 서버 자동 수집이 막혀 있어 버튼으로 열어 확인합니다 (2026-10-24 확인). 나머지 출처(FDA 경고장·수입 거부·리콜, CBP CROSS, Federal Register, Regulations.gov, CourtListener)는 '자동 수집' 메뉴에서 [지금 수집]과 하루 1회 작업으로 모읍니다.</p>
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>사이트</th><th>무엇을 보나</th><th>자동으로 못 읽는 이유</th></tr></thead>
  <tbody>${(DATA.manualChecks || []).map(m => `<tr><td class="cmp-nowrap">${link(m.url, m.name + ' ↗')}</td><td>${esc(m.what)}</td><td class="cmp-muted">${esc(m.why)}</td></tr>`).join('')}</tbody>
</table></div>`, false) + fold('fr', 'Federal Register 새 공고 조회', 'FDA·FTC·CBP·USTR 공고 검색 (키 없음)', `
<p class="cmp-muted">FDA·FTC·CBP·USTR 공고를 최신순으로 조회합니다. 키 없이 조회하며 결과는 1분 동안 캐시합니다.
  <button type="button" class="cmp-btn cmp-btn-sm" data-act="goto" data-goto="agencies">각 기관이 하는 일 보기</button></p>
<div class="cmp-row">
  <label class="cmp-field cmp-grow"><span>검색어</span><input class="cmp-input" data-el="frq" value="food labeling"></label>
  <label class="cmp-field"><span>기관</span><select class="cmp-input" data-el="fra">
    <option value="">4개 기관 전체</option>${Object.entries(FR_AGENCIES).map(([slug, name]) => `<option value="${slug}">${name}</option>`).join('')}</select></label>
  <div class="cmp-actions"><button type="button" class="cmp-btn cmp-primary" data-act="fr-search">조회</button></div>
</div>
<div data-el="frout" aria-live="polite"></div>`, false) + fold('openfda', 'openFDA 식품 리콜 조회', '리콜 사유로 검색 (키 없음)', `
<p class="cmp-muted">openFDA 식품 리콜(<code>food/enforcement</code>)을 리콜 사유로 조회합니다. 키 없이 조회하며 결과는 1분 동안 캐시합니다.</p>
<div class="cmp-row">
  <label class="cmp-field cmp-grow"><span>리콜 사유 검색어</span><input class="cmp-input" data-el="q" value="undeclared"></label>
  <div class="cmp-actions"><button type="button" class="cmp-btn cmp-primary" data-act="search">조회</button></div>
</div>
<div data-el="out" aria-live="polite"></div>`, false) + `</div>
</div>`;
  }

  /* ① 서버 수집 결과 */

  function monitorData() {
    const m = global.REBOOT_MONITOR;
    return m && Array.isArray(m.sources) && m.sources.length ? m : null;
  }

  // 항목 내용이 바뀌었는지 비교하는 짧은 지문 (보안 용도 아님)
  function sig(item) {
    const s = JSON.stringify([item.title || '', item.date || '', item.summary || '', item.quotes || []]);
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36);
  }

  function loadMonitor() {
    const m = store.get(KEY.monitor, {}) || {};
    return { baseline: m.baseline || null, baselineAt: m.baselineAt || null, candidates: m.candidates || {} };
  }
  function saveMonitor(m) { store.set(KEY.monitor, m); }

  /** 기준과 비교해 항목마다 mark: 'new' | 'changed' | 'same' | null(기준 없음) */
  function diffMonitor(data, baseline) {
    return data.sources.map(src => {
      const base = baseline && baseline[src.id];
      const items = (Array.isArray(src.items) ? src.items : []).map(item => {
        let mark = null;
        if (base) mark = !(item.id in base) ? 'new' : base[item.id] !== sig(item) ? 'changed' : 'same';
        return Object.assign({}, item, { mark });
      });
      return { id: src.id, label: src.label || src.id, items, note: src.note || '', updatedAt: src.updatedAt || '', stale: !!src.stale };
    });
  }

  function baselineOf(data) {
    const out = {};
    data.sources.forEach(src => {
      out[src.id] = {};
      (Array.isArray(src.items) ? src.items : []).forEach(item => { out[src.id][item.id] = sig(item); });
    });
    return out;
  }

  /** 경고장 원문 문구 → 사례집 후보. 판정 결과가 PASS면 규칙 보강이 필요하다는 뜻 */
  // 사례집 후보 번호 = 출처 · 경고장 · '문구 내용'의 지문 (2026-10-24 · GPT 검토 ㉒)
  // 예전처럼 순번(0, 1, 2…)으로 붙이면 다시 수집해 문구 순서가 바뀔 때 승인 표시가 다른 문구로 옮겨 붙음
  function candidatesOf(sources, saved) {
    const list = [];
    const seen = new Set();
    sources.forEach(src => src.items.forEach(item => (Array.isArray(item.quotes) ? item.quotes : []).forEach(q => {
      const text = String(q);
      const id = `${src.id}:${item.id}:${sig({ title: text })}`;
      if (seen.has(id)) return;
      seen.add(id);
      // 승인한 문구와 글자까지 같을 때만 승인 상태를 씀 (다르면 다시 '후보')
      const st = saved[id] && saved[id].text === text ? saved[id] : {};
      list.push({ id, text, item, source: src.label, r: review(text, { channel: 'b2c' }), status: st.status || '후보', by: st.by || '', at: st.at || null });
    })));
    return list;
  }

  const MARK = { new: '🆕 새 항목', changed: '🔄 변경', same: '', null: '' };
  // 한글 제목·요약 (서버가 Claude로 만든 참고용 · 원문이 기준)
  function koLine(s) { return s ? `<span class="cmp-ko" lang="ko">${esc(s)}</span>` : ''; }

  /** 조회 결과 표(브라우저에서 바로 부른 Federal Register·openFDA)에 한글 제목·요약을 나중에 채움 */
  async function fillKo(out, rows) {
    const h = await serverHealth();
    if (!h || !h.ai || !rows.length) return;
    const note = out.querySelector('[data-ko-note]');
    if (note) note.textContent = ' · 한글 요약 만드는 중…';
    try {
      const { items } = await api('/api/compliance/ko', { items: rows.map(r => ({ id: r.id, source: r.source, title: r.title, summary: r.summary })) });
      items.forEach(k => {
        const cells = out.querySelectorAll(`[data-ko-id="${CSS.escape(k.id)}"]`);
        cells.forEach(c => { c.innerHTML = koLine(c.dataset.koField === 'title' ? k.title_ko : k.summary_ko); });
      });
      if (note) note.textContent = ' · 한글 요약: AI 참고용';
    } catch (e) {
      if (note) note.textContent = ` · 한글 요약 실패 (${e.message})`;
    }
  }
  const FOLD_OPEN = new Map();   // 출처 id → 펼침 여부 (이 화면을 연 동안만 기억)

  /* 수집 결과 정리 (2026-10-24): 기관(FDA·FTC·CBP·법원)별로 묶고, 출처마다 통로·방식·가져오는 정보·쓰는 곳을 보여 줌
     필터: 기관 · 방식(자동 수집 / 직접 올림) · 기간 · 검색어 · 새 항목만 */
  const MON_F = { agency: 'all', product: 'all', mode: 'all', days: 'all', q: '', hot: false };
  // 우리 제품군 태그 (data.js monitorProductTags): 젤리 · 음료 · 당·저당 · 숙취·성분 · 해당 없으면 '공통'
  const PTAGS = (DATA.monitorProductTags || []).map(t => Object.assign({}, t, { rx: new RegExp(t.re, 'i') }));
  const TAG_SKIP = DATA.monitorTagSkip ? new RegExp(DATA.monitorTagSkip, 'i') : null;
  function itemTags(item) {
    const text = [item.title, item.firm, item.summary, item.title_ko, item.summary_ko].join(' ');
    return TAG_SKIP && TAG_SKIP.test(text) ? [] : PTAGS.filter(t => t.rx.test(text)).map(t => t.id);
  }
  function tagChips(tags) {
    return tags.length ? tags.map(id => `<span class="cmp-chip cmp-ptag cmp-ptag-${id}">${esc(PTAGS.find(t => t.id === id).ko)}</span>`).join('') : '<span class="cmp-chip cmp-ptag">공통</span>';
  }
  const MODE_LABEL = { auto: '자동 수집', manual: '직접 올림' };

  function monMeta(id) { return (DATA.monitorSources || []).find(s => s.id === id) || { id, agency: '기타', name: id, via: '', modes: ['auto'], key: '', gets: '', gives: '' }; }
  // 'FDA·FTC'처럼 두 기관이 섞인 출처는 항목의 firm 앞부분(FTC · Notice / FDA · Rule)으로 나눔
  function itemAgency(meta, item) {
    if (!meta.agency.includes('·')) return meta.agency;
    return /^FTC\b/.test(String(item.firm || '')) ? 'FTC' : 'FDA';
  }
  function itemMode(item) { return item.via === 'xlsx' || item.via === 'text' ? 'manual' : 'auto'; }

  function monGuideHTML(data, sources) {
    const errs = data && Array.isArray(data.errors) ? data.errors : [];
    const rows = (DATA.monitorSources || []).map(s => {
      const src = sources.find(x => x.id === s.id);
      const err = errs.filter(e => e.id === s.id).map(e => e.error);
      const when = src && src.updatedAt ? `<br><span class="cmp-muted">정상 수집 ${fmt(Date.parse(src.updatedAt))}</span>` : '';
      const state = !src ? '<span class="cmp-muted">아직 수집 안 함</span>'
        : err.length && src.stale ? `<span class="cmp-fail">이번 수집 실패 · 이전 자료 ${src.items.length}건 표시</span>${when}<br><span class="cmp-muted">${esc(String(err[0]).slice(0, 80))}</span>`
          : err.length ? `<span class="cmp-fail">일부 실패 · ${esc(String(err[0]).slice(0, 80))}</span><br>${src.items.length}건${when}`
            : `${src.items.length}건${when}${src.note ? `<br><span class="cmp-muted">${esc(String(src.note).slice(0, 70))}…</span>` : ''}`;
      return `<tr><td class="cmp-nowrap"><span class="cmp-agency-chip cmp-ag-${esc(s.agency.split('·')[0])}">${esc(s.agency)}</span></td>
        <td><b>${esc(s.name)}</b><br><span class="cmp-muted">${esc(s.via)}</span></td>
        <td class="cmp-nowrap">${s.modes.map(x => `<span class="cmp-chip">${MODE_LABEL[x]}</span>`).join(' ')}</td>
        <td>${esc(s.gets)}${s.range ? `<br><span class="cmp-muted">범위: ${esc(s.range)}</span>` : ''}</td><td>${esc(s.gives)}</td>
        <td class="cmp-nowrap"><code>${esc(s.key)}</code></td><td>${state}</td></tr>`;
    }).join('');
    return `
<p class="cmp-muted"><b>가져오는 정보</b>는 서버가 각 기관 공식 데이터에서 받아 오는 내용, <b>대시보드에서 쓰는 곳</b>은 그 정보가 이 화면과 규칙·사례집에 어떻게 쓰이는지입니다.
  각 출처는 모든 결과가 아니라 <b>최근 최대 N건</b>만 가져옵니다 (범위 칸 참고) → 빠진 것이 없다는 뜻이 아닙니다.
  openFDA·Data Dashboard는 둘 다 <b>FDA 공식 데이터 통로</b>이고, Regulations.gov·Federal Register는 여러 기관의 공고가 모이는 정부 사이트라 항목마다 FDA·FTC로 나눠 보여 줍니다.</p>
<div class="cmp-table-wrap"><table class="cmp-table cmp-mon-guide">
  <thead><tr><th>기관</th><th>출처 · 통로</th><th>방식</th><th>가져오는 정보</th><th>대시보드에서 쓰는 곳</th><th>필요한 키</th><th>지금 상태</th></tr></thead>
  <tbody>${rows}</tbody></table></div>`;
  }

  function monFilterHTML() {
    const ag = (DATA.monitorAgencies || []);
    return `
<div class="cmp-row cmp-mon-filter">
  <label class="cmp-field"><span>기관</span><select class="cmp-input" data-monf="agency">
    <option value="all">전체 기관</option>${ag.map(a => `<option value="${esc(a.code)}"${MON_F.agency === a.code ? ' selected' : ''}>${esc(a.code)} · ${esc(a.ko)}</option>`).join('')}</select></label>
  <label class="cmp-field"><span>제품군</span><select class="cmp-input" data-monf="product">
    ${[['all', '전체 제품군']].concat(PTAGS.map(t => [t.id, t.ko]), [['common', '공통 (제품군 표시 없음)']]).map(([v, l]) => `<option value="${v}"${MON_F.product === v ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
  <label class="cmp-field"><span>방식</span><select class="cmp-input" data-monf="mode">
    ${[['all', '전체'], ['auto', '자동 수집'], ['manual', '직접 올림']].map(([v, l]) => `<option value="${v}"${MON_F.mode === v ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
  <label class="cmp-field"><span>기간</span><select class="cmp-input" data-monf="days">
    ${[['all', '전체'], ['7', '최근 7일'], ['30', '최근 30일'], ['90', '최근 90일'], ['365', '최근 1년']].map(([v, l]) => `<option value="${v}"${MON_F.days === v ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
  <label class="cmp-field cmp-grow"><span>검색어 (제목·회사·요약·한글 요약)</span><input class="cmp-input" data-monf="q" value="${esc(MON_F.q)}" placeholder="예: jelly · hangover · 젤리 · Korea"></label>
  <label class="cmp-check cmp-mon-hot"><input type="checkbox" data-monf="hot"${MON_F.hot ? ' checked' : ''}> 🆕·🔄 새 항목만</label>
</div>`;
  }

  function monResultsHTML(sources) {
    const since = MON_F.days === 'all' ? '' : new Date(Date.now() - Number(MON_F.days) * 86400000).toISOString().slice(0, 10);
    const q = MON_F.q.trim().toLowerCase();
    const rows = [];
    sources.forEach(src => {
      const meta = monMeta(src.id);
      src.items.forEach(item => {
        const agency = itemAgency(meta, item);
        const mode = itemMode(item);
        if (MON_F.agency !== 'all' && agency !== MON_F.agency) return;
        if (MON_F.mode !== 'all' && mode !== MON_F.mode) return;
        const tags = itemTags(item);
        if (MON_F.product === 'common' ? tags.length : MON_F.product !== 'all' && !tags.includes(MON_F.product)) return;
        if (since && (!item.date || item.date < since)) return;
        if (MON_F.hot && item.mark !== 'new' && item.mark !== 'changed') return;
        if (q && ![item.title, item.firm, item.summary, item.title_ko, item.summary_ko].join(' ').toLowerCase().includes(q)) return;
        rows.push({ item, src, meta, agency, mode, tags });
      });
    });
    // 최신순: 표 안의 항목도, 기관 묶음 안의 출처 순서도 가장 최근 날짜가 위 (날짜 없는 항목은 맨 아래)
    rows.sort((a, b) => String(b.item.date || '').localeCompare(String(a.item.date || '')));
    const agencies = (DATA.monitorAgencies || []).map(a => a.code).concat(['기타']);
    const groups = agencies.map(code => ({ code, rows: rows.filter(r => r.agency === code) })).filter(g => g.rows.length);
    if (!groups.length) return '<p class="cmp-muted">조건에 맞는 항목이 없습니다. 필터를 넓혀 보세요.</p>';
    const tagCount = PTAGS.map(t => `${esc(t.ko)} ${rows.filter(r => r.tags.includes(t.id)).length}`).concat(`공통 ${rows.filter(r => !r.tags.length).length}`).join(' · ');
    return `<p class="cmp-muted">${rows.length}건 · 최신순 · 기관별로 묶음 (FDA 안에는 경고장·수입 거부·openFDA 리콜·FDA 규칙·공고가 함께 들어감)<br>제품군: ${tagCount} <span>(한 항목이 여러 제품군에 들 수 있음 · 제목·요약 단어로 자동 분류)</span></p>
<div class="cmp-folds">${groups.map(g => {
      const ag = (DATA.monitorAgencies || []).find(a => a.code === g.code) || { ko: '' };
      const hot = g.rows.filter(r => r.item.mark === 'new' || r.item.mark === 'changed').length;
      const key = `ag-${g.code}`;
      const open = FOLD_OPEN.has(key) ? FOLD_OPEN.get(key) : hot > 0 || groups.length === 1;
      const bySrc = [...new Set(g.rows.map(r => r.src.id))];
      return `
<details class="cmp-fold" data-src="${esc(key)}"${open ? ' open' : ''}>
  <summary><span class="cmp-agency-chip cmp-ag-${esc(g.code)}">${esc(g.code)}</span><span class="cmp-fold-title">${esc(ag.ko)}</span>
    <span class="cmp-tag">${g.rows.length}개</span>${hot ? `<span class="cmp-chip">🆕·🔄 ${hot}</span>` : ''}
    <span class="cmp-fold-latest cmp-muted">${bySrc.map(id => esc(monMeta(id).name)).join(' · ')}</span></summary>
  ${bySrc.map(id => {
        const meta = monMeta(id);
        const src = g.rows.find(r => r.src.id === id).src;
        const list = g.rows.filter(r => r.src.id === id);
        return `
  <div class="cmp-mon-src">
    <h4 class="cmp-h4">${esc(meta.name)} <span class="cmp-muted">· ${esc(meta.via)}</span> <span class="cmp-tag">${list.length}개</span>${src.stale ? ` <span class="cmp-fail">이번 수집 실패 · 이전 자료${src.updatedAt ? ` (${fmt(Date.parse(src.updatedAt))})` : ''}</span>` : ''}</h4>
    <p class="cmp-muted cmp-mon-io"><b>가져옴</b> ${esc(meta.gets)}<br><b>쓰는 곳</b> ${esc(meta.gives)}</p>
    ${src.note ? `<p class="cmp-muted cmp-src-note">ℹ️ ${esc(src.note)}</p>` : ''}
    <div class="cmp-table-wrap"><table class="cmp-table">
      <thead><tr><th></th><th>날짜</th><th>제목</th><th>요약</th><th>방식</th></tr></thead>
      <tbody>${list.map(({ item: i, mode, tags }) => `<tr${i.mark === 'new' || i.mark === 'changed' ? ' class="cmp-row-hot"' : ''}>
        <td class="cmp-nowrap">${MARK[i.mark]}</td><td class="cmp-nowrap">${esc(i.date || '—')}</td>
        <td>${link(i.url, i.title || i.id)}${koLine(i.title_ko)}${i.firm ? `<span class="cmp-firm cmp-muted">${esc(i.firm)}</span>` : ''}<span class="cmp-ptags">${tagChips(tags)}</span></td>
        <td>${esc(String(i.summary || '').slice(0, 240))}${koLine(i.summary_ko)}${(i.quotes || []).length ? `<br><span class="cmp-chip">인용 문구 ${i.quotes.length}개 → 사례집 후보</span>` : ''}</td>
        <td class="cmp-nowrap"><span class="cmp-chip ${mode === 'manual' ? 'cmp-chip-manual' : ''}">${MODE_LABEL[mode]}</span></td></tr>`).join('')}</tbody>
    </table></div>
  </div>`;
      }).join('')}
</details>`;
    }).join('')}</div>`;
  }

  function renderServer(root) {
    const box = root.querySelector('[data-panel="monitor"] [data-el="server"]');
    const guide = root.querySelector('[data-panel="monitor"] [data-el="mon-guide"]');
    const data = monitorData();
    if (!data) {
      guide.innerHTML = monGuideHTML(null, []);
      box.innerHTML = `<p class="cmp-note">아직 서버 수집 결과가 없습니다. 위의 [지금 수집]을 누르면(30초~1분) 서버에 저장되어 여기에 표시됩니다. 하루 1회 자동 수집을 켜 두었다면 매일 08:00에도 모읍니다. 그 전에는 '사람이 확인' 메뉴의 Federal Register·openFDA 즉석 조회를 쓰세요.</p>`;
      return;
    }
    const m = loadMonitor();
    const sources = diffMonitor(data, m.baseline);
    guide.innerHTML = monGuideHTML(data, sources);
    const all = sources.flatMap(s => s.items);
    const nNew = all.filter(i => i.mark === 'new').length;
    const nChanged = all.filter(i => i.mark === 'changed').length;
    const gen = data.generatedAt ? Date.parse(data.generatedAt) : NaN;
    const stale = !isNaN(gen) && Date.now() - gen > STALE_HOURS * 3600 * 1000;
    const cands = candidatesOf(sources, m.candidates);
    const approved = cands.filter(c => c.status === '승인');
    const reviewer = box.dataset.reviewer || '';

    box.innerHTML = `
<p class="cmp-summary">서버 수집 ${isNaN(gen) ? '시각 미상' : fmt(gen)}${stale ? ` <span class="cmp-fail">· ${STALE_HOURS}시간 넘게 갱신 안 됨</span>` : ''}
  · 항목 ${all.length}개${m.baseline ? ` · 🆕 ${nNew} · 🔄 ${nChanged} <span class="cmp-muted">(기준 ${fmt(m.baselineAt)})</span>` : ''}</p>
${m.baseline ? '' : '<p class="cmp-note">첫 확인입니다. 지금 목록을 기준으로 저장하면 다음부터 새 항목·변경 항목이 표시됩니다.</p>'}
<div class="cmp-actions">
  <button type="button" class="cmp-btn${m.baseline ? '' : ' cmp-primary'}" data-act="baseline">${m.baseline ? '확인 완료 · 기준 다시 저장' : '기준 저장'}</button>
  <button type="button" class="cmp-btn" data-act="fold-all" data-open="1">모두 펼치기</button>
  <button type="button" class="cmp-btn" data-act="fold-all" data-open="">모두 접기</button>
</div>
${monFilterHTML()}
<div data-el="mon-results">${monResultsHTML(sources)}</div>

${fold('cands', '사례집 후보', `${cands.length}개 · 승인 ${approved.length}개`, `
<p class="cmp-muted">수집한 경고장 문구는 자동으로 사례집이나 규칙에 들어가지 않습니다. 사람이 승인한 문구만 내려받아 <code>data.js</code>의 <code>warningQuotes</code>에 옮기고 규칙 검증을 다시 실행합니다. <b>PASS로 나온 후보</b>는 규칙이 놓친 문구이니 규칙 보강이 필요합니다.</p>
${cands.length ? `
<div class="cmp-row">
  <label class="cmp-field"><span>검토자</span><input class="cmp-input" data-el="reviewer" value="${esc(reviewer)}" placeholder="이름"></label>
  <div class="cmp-actions"><button type="button" class="cmp-btn" data-act="export-cands" ${approved.length ? '' : 'disabled'}>승인된 후보 CSV 받기</button></div>
</div>
<p data-el="cmsg" aria-live="polite"></p>
<div class="cmp-list">${cands.map(c => `
  <article class="cmp-hist" data-cid="${esc(c.id)}">
    <div class="cmp-finding-head">${badge(c.r.verdict)} <span class="cmp-chip">${esc(c.status)}</span>
      ${c.r.verdict === 'PASS' ? '<span class="cmp-fail">규칙이 놓친 문구 → 규칙 보강 필요</span>' : ''}
      <span class="cmp-muted">${esc(c.source)} · ${esc(c.item.firm || c.item.title || '')} · ${esc(c.item.date || '')}</span></div>
    <p class="cmp-quote">${highlight(c.text, c.r.findings.flatMap(f => f.terms))}</p>
    ${trBlock(c.text)}
    ${c.status === '후보' ? `<div class="cmp-actions">
      <button type="button" class="cmp-btn cmp-primary" data-act="cand-approve">사례집 후보 승인</button>
      <button type="button" class="cmp-btn" data-act="cand-reject">제외</button></div>` :
      `<p class="cmp-muted">${esc(c.status)} · ${esc(c.by)} · ${fmt(c.at)} <button type="button" class="cmp-btn cmp-btn-sm" data-act="cand-reset">되돌리기</button></p>`}
  </article>`).join('')}</div>` : '<p class="cmp-muted">이번 수집 결과에 경고장 원문 문구(quotes)가 없습니다.</p>'}`,
      FOLD_OPEN.has('cands') ? FOLD_OPEN.get('cands') : cands.length > 0)}`;

    const keepFolds = () => box.querySelectorAll('details.cmp-fold').forEach(d => d.addEventListener('toggle', () => FOLD_OPEN.set(d.dataset.src, d.open)));
    // 필터를 바꾸면 결과만 다시 그림 (출처 안내·사례집 후보는 그대로)
    const refilter = () => {
      box.querySelector('[data-el="mon-results"]').innerHTML = monResultsHTML(sources);
      keepFolds();
    };
    box.oninput = e => {
      if (e.target.dataset.el === 'reviewer') box.dataset.reviewer = e.target.value;
      if (e.target.dataset.monf === 'q') { MON_F.q = e.target.value; refilter(); }
    };
    box.onchange = e => {
      const k = e.target.dataset.monf;
      if (!k || k === 'q') return;
      MON_F[k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
      refilter();
    };
    keepFolds();
    box.onclick = e => {
      const btn = e.target.closest('[data-act]');
      if (!btn || btn.disabled || btn.dataset.act === 'translate') return;   // 번역 버튼은 render에서 처리
      const act = btn.dataset.act;
      if (act === 'fold-all') {
        box.querySelectorAll('details.cmp-fold').forEach(d => { d.open = !!btn.dataset.open; });
        return;
      }
      const cur = loadMonitor();

      if (act === 'baseline') {
        cur.baseline = baselineOf(data);
        cur.baselineAt = Date.now();
        saveMonitor(cur);
        toast('지금 목록을 기준으로 저장했습니다');
        return renderServer(root);
      }
      if (act === 'export-cands') {
        const rows = [['text', 'firm', 'issued', 'url', 'source', 'verdict', 'codes', 'approvedBy', 'approvedAt']].concat(
          approved.map(c => [c.text, c.item.firm || '', c.item.date || '', c.item.url || '', c.source, c.r.verdict, c.r.codes.join(' '), c.by, c.at ? new Date(c.at).toISOString() : '']));
        return download('casebook-candidates', toCSV(rows));
      }
      const card = btn.closest('[data-cid]');
      if (!card) return;
      const id = card.dataset.cid;
      if (act === 'cand-reset') {
        delete cur.candidates[id];
      } else {
        const name = (box.dataset.reviewer || '').trim();
        if (!name) { box.querySelector('[data-el="cmsg"]').innerHTML = '<span class="cmp-note cmp-note-block">검토자 이름을 먼저 입력하세요.</span>'; return; }
        const cand = cands.find(c => c.id === id);
        if (!cand) return;
        cur.candidates[id] = { status: act === 'cand-approve' ? '승인' : '제외', by: name, at: Date.now(), text: cand.text };
      }
      saveMonitor(cur);
      renderServer(root);
    };
  }

  /* ② ③ 외부 API 조회 (키 없음) */

  async function cached(cacheKey, q, fetcher) {
    const cache = store.get(cacheKey, {}) || {};
    const hit = cache[q];
    if (hit && Date.now() - hit.at < CACHE_TTL) return { results: hit.results, cached: true };
    const results = await fetcher();
    const next = {};
    Object.keys(cache).sort((a, b) => cache[b].at - cache[a].at).slice(0, 9).forEach(k => { next[k] = cache[k]; });
    next[q] = { at: Date.now(), results };
    store.set(cacheKey, next);
    return { results, cached: false };
  }

  function searchFedReg(term, agency) {
    const q = term.trim();
    const agencies = FR_AGENCIES[agency] ? [agency] : Object.keys(FR_AGENCIES);
    return cached(KEY.fedreg, `${agency || 'all'}|${q}`, async () => {
      const params = ['per_page=10', 'order=newest', 'conditions[term]=' + encodeURIComponent(q)]
        .concat(agencies.map(a => 'conditions[agencies][]=' + a))
        .concat(['title', 'type', 'publication_date', 'html_url', 'document_number', 'agencies', 'abstract'].map(f => 'fields[]=' + f));
      const res = await fetch('https://www.federalregister.gov/api/v1/documents.json?' + params.join('&'));
      if (!res.ok) throw new Error(`Federal Register 응답 ${res.status}`);
      return ((await res.json()).results || []).map(x => ({
        id: x.document_number, title: x.title, type: x.type, date: x.publication_date, url: x.html_url,
        agencies: (x.agencies || []).map(a => FR_AGENCIES[a.slug]).filter(Boolean),
        abstract: x.abstract || ''
      }));
    });
  }

  function searchRecalls(query) {
    const q = query.replace(/["\\]/g, '').trim();
    return cached(KEY.openfda, q, async () => {
      const url = 'https://api.fda.gov/food/enforcement.json?search=' +
        encodeURIComponent(`reason_for_recall:"${q}"`) + '&sort=report_date:desc&limit=10';
      const res = await fetch(url);
      if (res.status === 404) return [];          // openFDA는 결과 0건을 404로 돌려줍니다
      if (!res.ok) throw new Error(`openFDA 응답 ${res.status}`);
      return ((await res.json()).results || []).map(x => ({
        recall_number: x.recall_number, firm: x.recalling_firm, product: x.product_description,
        reason: x.reason_for_recall, classification: x.classification, date: x.report_date, status: x.status
      }));
    });
  }

  function wireMonitor(root) {
    const p = root.querySelector('[data-panel="monitor"]');
    const el = n => p.querySelector(`[data-el="${n}"]`);
    const fail = (out, e) => {
      out.innerHTML = `<p class="cmp-note cmp-note-block">조회하지 못했습니다: ${esc(e.message)}. 인터넷 연결을 확인하세요.</p>`;
    };

    const goFR = async () => {
      const q = el('frq').value.trim();
      if (!q) return;
      const out = el('frout');
      out.innerHTML = '<p class="cmp-muted">조회 중…</p>';
      try {
        const { results, cached: fromCache } = await searchFedReg(q, el('fra').value);
        out.innerHTML = `<p class="cmp-muted">${results.length}건${fromCache ? ' · 캐시' : ''}<span data-ko-note></span></p>` + (results.length ? `
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>게시일</th><th>기관</th><th>유형</th><th>제목</th></tr></thead>
  <tbody>${results.map((x, i) => `<tr><td class="cmp-nowrap">${esc(x.date)}</td><td>${esc(x.agencies.join(', '))}</td><td>${esc(x.type)}</td>
    <td>${link(x.url, x.title)}<span data-ko-id="fr-${i}" data-ko-field="title"></span>
      ${x.abstract ? `<br><span class="cmp-muted">${esc(x.abstract.slice(0, 220))}${x.abstract.length > 220 ? '…' : ''}</span><span data-ko-id="fr-${i}" data-ko-field="summary"></span>` : ''}
      ${trBlock(x.abstract ? `${x.title}\n${x.abstract}` : x.title, '제목·요약 번역')}</td></tr>`).join('')}</tbody>
</table></div>` : '');
        fillKo(out, results.map((x, i) => ({ id: `fr-${i}`, source: 'Federal Register', title: x.title, summary: x.abstract || '' })));
      } catch (e) { fail(out, e); }
    };

    const goRecall = async () => {
      const q = el('q').value.trim();
      if (!q) return;
      const out = el('out');
      out.innerHTML = '<p class="cmp-muted">조회 중…</p>';
      try {
        const { results, cached: fromCache } = await searchRecalls(q);
        const date = d => (d && d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : esc(d));
        out.innerHTML = `<p class="cmp-muted">${results.length}건${fromCache ? ' · 캐시' : ''}<span data-ko-note></span></p>` + (results.length ? `
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>보고일</th><th>등급</th><th>업체</th><th>제품</th><th>리콜 사유</th></tr></thead>
  <tbody>${results.map((x, i) => `<tr><td class="cmp-nowrap">${date(x.date)}</td><td>${esc(x.classification)}</td><td>${esc(x.firm)}</td>
    <td>${esc((x.product || '').slice(0, 160))}<span data-ko-id="fda-${i}" data-ko-field="title"></span></td>
    <td>${esc((x.reason || '').slice(0, 200))}<span data-ko-id="fda-${i}" data-ko-field="summary"></span></td></tr>`).join('')}</tbody>
</table></div>` : '');
        fillKo(out, results.map((x, i) => ({ id: `fda-${i}`, source: 'openFDA food recall', title: x.product || '', summary: `${x.firm || ''} · ${x.reason || ''}` })));
      } catch (e) { fail(out, e); }
    };

    p.querySelector('[data-act="fr-search"]').addEventListener('click', goFR);
    el('frq').addEventListener('keydown', e => { if (e.key === 'Enter') goFR(); });
    p.querySelector('[data-act="search"]').addEventListener('click', goRecall);
    el('q').addEventListener('keydown', e => { if (e.key === 'Enter') goRecall(); });

    // 서버에서 지금 수집 (키가 필요한 Regulations.gov · CourtListener 포함) → monitor-data.js 저장 + 화면 갱신
    const refreshBtn = p.querySelector('[data-act="monitor-refresh"]');
    const wlXlsx = p.querySelector('[data-act="wl-xlsx"]');
    const wlText = p.querySelector('[data-act="wl-text"]');
    serverHealth().then(h => {
      refreshBtn.disabled = !h;
      refreshBtn.title = h ? 'FDA 경고장·수입 거부 · openFDA · Regulations.gov · CourtListener · CBP CROSS · Federal Register를 지금 수집합니다 (1~2분)'
        : '서버 상태를 확인하지 못했습니다. 새로고침해 주세요';
      wlXlsx.disabled = wlText.disabled = !h;
    });

    // FDA 경고장 직접 올리기: 결과(수집 데이터 전체)로 화면을 다시 그림
    const wlDone = (data, text) => {
      global.REBOOT_MONITOR = data;
      FOLD_OPEN.set('fda-warning-letters', true);
      el('wlmsg').innerHTML = `<p class="cmp-note">${esc(text)}</p>`;
      renderServer(root);
    };
    const wlFail = e => { el('wlmsg').innerHTML = `<p class="cmp-note cmp-note-block">${esc(e.message)}</p>`; };
    wlXlsx.addEventListener('click', async () => {
      const file = el('wlfile').files[0];
      if (!file) return wlFail(new Error('XLSX 파일을 먼저 고르세요.'));
      const fd = new global.FormData();
      fd.append('file', file);
      wlXlsx.disabled = true;
      el('wlmsg').innerHTML = '<p class="cmp-muted">올리는 중…</p>';
      try {
        const res = await fetch('/api/compliance/letters/xlsx', { method: 'POST', body: fd, cache: 'no-store' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `서버 오류 (${res.status})`);
        const n = data.letters || {};
        wlDone(data, `경고장 ${n.rows}건 중 식품 관련 ${n.food}건 · 새로 ${n.added}건 추가했습니다.`);
      } catch (e) { wlFail(e); }
      wlXlsx.disabled = false;
    });
    wlText.addEventListener('click', async () => {
      wlText.disabled = true;
      el('wlmsg').innerHTML = '<p class="cmp-muted">문구를 뽑는 중…</p>';
      try {
        const data = await api('/api/compliance/letters/text', {
          text: el('wltext').value, url: el('wlurl').value.trim(), firm: el('wlfirm').value.trim(),
          issued: el('wldate').value, subject: el('wlsubject').value.trim()
        });
        FOLD_OPEN.set('cands', true);
        wlDone(data, `인용 문구 ${(data.letters || {}).quotes}개를 '사례집 후보'에 넣었습니다. 판정을 확인하고 승인·제외하세요.`);
        el('wltext').value = '';
      } catch (e) { wlFail(e); }
      wlText.disabled = false;
    });
    refreshBtn.addEventListener('click', async () => {
      const msg = el('refreshmsg');
      refreshBtn.disabled = true;
      msg.innerHTML = '<p class="cmp-muted">수집 중… 출처 7곳을 차례로 확인합니다 (1~2분).</p>';
      try {
        const data = await api('/api/compliance/monitor/refresh');
        global.REBOOT_MONITOR = data;
        const errs = Array.isArray(data.errors) ? data.errors : [];
        msg.innerHTML = errs.length
          ? `<p class="cmp-note cmp-note-block">일부 출처 수집 실패: ${errs.map(x => `${esc(x.label)} (${esc(x.error)})`).join(' · ')}</p>`
          : '<p class="cmp-note">수집 완료 · tabs/compliance/static/monitor-data.js에 저장했습니다.</p>';
        renderServer(root);
      } catch (e) {
        msg.innerHTML = `<p class="cmp-note cmp-note-block">수집 실패: ${esc(e.message)}</p>`;
      }
      refreshBtn.disabled = false;
    });
  }

  /* ───────── 기관 안내 ───────── */

  const FR_SLUG = {};
  Object.entries(FR_AGENCIES).forEach(([slug, code]) => { FR_SLUG[code] = slug; });

  function agenciesHTML() {
    const list = DATA.agencies || [];
    const byCode = {};
    list.forEach(a => { byCode[a.code] = a; });
    const chip = code => `<span class="cmp-agency-chip cmp-ag-${esc(code)}">${esc(code)}</span>`;

    return `
<p class="cmp-muted">미국에 제품을 팔 때 만나는 기관 4곳과 광고 플랫폼 ${(DATA.platforms || []).length}곳을 쉬운 말로 정리했습니다 (2026-10-23 작성 · 플랫폼 2026-10-24 추가). 참고용 요약이며 법률 자문이 아닙니다. 정확한 기준은 각 기관 공식 사이트와 관세사·규제 전문가에게 확인하세요.</p>
<div class="cmp-folds">` + fold('ag-summary', '한눈에 보기', `기관 ${list.length}곳`, `
<div class="cmp-table-wrap"><table class="cmp-table">
  <thead><tr><th>기관</th><th>한 줄 요약</th><th>어기면</th><th>대시보드 규칙</th></tr></thead>
  <tbody>${list.map(a => `<tr><td class="cmp-nowrap">${chip(a.code)}<br><span class="cmp-muted">${esc(a.ko)}</span></td>
    <td>${esc(a.oneLine)}</td><td>${esc(a.risk)}</td><td>${esc(a.rules)}</td></tr>`).join('')}</tbody>
</table></div>`, true) + fold('ag-flow', '수출 단계별로 보면', `${(DATA.agencyFlow || []).length}단계`, `
<ol class="cmp-flow">${(DATA.agencyFlow || []).map((f, i) => `
  <li class="cmp-flow-step"><span class="cmp-flow-no">${i + 1}</span>
    <b>${esc(f.step)}</b><span class="cmp-flow-who">${f.who.map(chip).join(' ')}</span>
    <span class="cmp-muted">${esc(f.what)}</span></li>`).join('')}</ol>`, true) + fold('ag-detail', '기관별 자세히', list.map(a => a.code).join(' · '), `
<div class="cmp-agencies">${list.map(a => `
  <article class="cmp-agency cmp-ag-border-${esc(a.code)}">
    <header class="cmp-agency-head"><span class="cmp-agency-icon" aria-hidden="true">${esc(a.icon)}</span>
      <div><h4>${esc(a.code)} · ${esc(a.ko)}</h4><p class="cmp-muted">${esc(a.en)}</p></div></header>
    <p class="cmp-agency-one">${esc(a.oneLine)}</p>
    <h5>이 기관이 하는 일</h5>
    <ul>${a.role.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <h5>REBOOT에 해당하는 것</h5>
    <ul>${a.forUs.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    ${a.bad ? `<h5>문구 예시</h5>
    <p class="cmp-ex"><span class="cmp-badge cmp-v-BLOCK">피할 문구</span> <code>${esc(a.bad)}</code></p>
    <p class="cmp-ex"><span class="cmp-badge cmp-v-PASS">쓸 수 있는 방향</span> <code>${esc(a.good)}</code></p>` : ''}
    <p class="cmp-muted"><b>어기면</b> ${esc(a.risk)}</p>
    <div class="cmp-actions">
      ${(a.goto || []).map(([tab, label]) => `<button type="button" class="cmp-btn" data-act="goto" data-goto="${esc(tab)}"${tab === 'monitor' && FR_SLUG[a.code] ? ` data-agency="${FR_SLUG[a.code]}"` : ''}>${esc(label)}</button>`).join('')}
      ${link(a.url, '공식 사이트 ↗')}
    </div>
  </article>`).join('')}</div>`, false) + fold('ag-platforms', '광고 플랫폼 (정부 기관 아님 · 회사 정책)', (DATA.platforms || []).map(x => x.code).join(' · '), `
<p class="cmp-muted">Meta·Google·TikTok·Amazon은 법을 만드는 기관이 아니라 회사입니다. 하지만 자기 광고 정책을 어기면 <b>광고가 거절되거나 계정이 제한</b>되므로 FDA·FTC와 함께 봐야 합니다.
  정책은 자주 바뀌니 실제 집행 전에는 각 공식 페이지로 확인하세요. 규칙 표에서는 '📱 플랫폼 정책'으로 표시합니다.<br>
  <b>판정에서는 플랫폼을 따로 나누지 않습니다.</b> Meta·Google·TikTok·Amazon 정책은 'B2C 광고·SNS·아마존' 채널 하나에 공통 기준으로 들어가 있습니다 (2026-10-24 결정). CS 답변은 고객 응대 파트가 보내기 전에 자동으로 다시 점검합니다. TikTok의 '숙취 해소 제품 광고 거절'처럼 한 플랫폼만의 기준은 아래 카드를 보고 사람이 확인합니다.</p>
<div class="cmp-agencies">${(DATA.platforms || []).map(a => `
  <article class="cmp-agency">
    <header class="cmp-agency-head"><span class="cmp-agency-icon" aria-hidden="true">${esc(a.icon)}</span>
      <div><h4>${esc(a.code)} · ${esc(a.ko)}</h4><p class="cmp-muted">광고 플랫폼 정책</p></div></header>
    <p class="cmp-agency-one">${esc(a.oneLine)}</p>
    <h5>REBOOT에 해당하는 것</h5>
    <ul>${a.forUs.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
    <p class="cmp-muted"><b>어기면</b> ${esc(a.risk)} · <b>관련 규칙</b> ${esc(a.rules)}</p>
    <div class="cmp-actions">${link(a.url, '공식 정책 ↗')}</div>
  </article>`).join('')}</div>`, false) + fold('ag-terms', '자주 나오는 용어', `${(DATA.agencyTerms || []).length}개`, `
<dl class="cmp-terms">${(DATA.agencyTerms || []).map(t => `<div><dt>${esc(t.term)}</dt><dd>${esc(t.desc)}</dd></div>`).join('')}</dl>`, false) + `</div>`;
  }

  /* ───────── 등록 ───────── */

  // summary(검토 대기 카피 수)는 승인 절차를 없애면서 뺐습니다 → 홈 '검토가 필요한 업무'에는 CS만 표시됩니다.
  REBOOT.register({
    id: 'compliance', icon: '🛡️', label: '컴플라이언스', priority: 'P0', order: 30,
    title: '좋은 카피의 시작, 안전한 표현.',
    subtitle: '광고 문구를 점검하고 미국 시장에 맞는 표현을 준비하세요.',
    render
  });
  REBOOT.expose('compliance', { review, bannedTerms, rules, translate });
})(typeof window !== 'undefined' ? window : globalThis);
