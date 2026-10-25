/* 홈 · 데일리 브리핑 (담당: 이현경) — 설명서 tabs/home/docs/README.md · 메모 tabs/home/docs/NOTES.md
   화면 2개 (왼쪽 메뉴): 데일리 브리핑 · 콘텐츠 캘린더 (시작하기는 2026-10-24 삭제)
   콘텐츠 캘린더 맨 아래: 다음 급등 예상 (접기 · 인사이트 검색 수요 탭에서 2026-10-25 옮김)
   모든 화면 오른쪽 아래: 이번 주 To-Do 버튼 → 사이드 패널 (home.js가 모든 페이지에서 불리므로 여기서 붙임)
   데일리 브리핑: 히어로(뉴스 신호 · 할 일 · 다음 시즌) → 시즌 카드 3 → KPI 4 → 왼쪽: 성과 차트 · 업계 뉴스 / 오른쪽: To-Do · 최근 활동
   - 뉴스: 저장된 결과만 읽음 (구글 뉴스는 '지금 수집' 버튼·하루 1회 작업만 부름)
   - To-Do: 그 주 처음 열면 자동 생성 (OpenAI 키가 없으면 규칙), 항목마다 근거 표시
   다른 파트에 공개: REBOOT.use('home').seasons() — 시즌 캘린더(D-day · 준비 기간 포함) */
(function (global) {
  'use strict';

  const REBOOT = global.REBOOT;
  if (!REBOOT) return;
  const store = REBOOT.store('home');   // 저장 키: todoBadge (메뉴 숫자용 · 이번 주 남은 할 일)
  const ui = REBOOT.ui;
  const { esc, toast, fmt, icon, badge, busy } = ui;
  const doc = global.document;
  const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토'];
  const DAY = 86400000;

  const S = { root: null, view: '', briefing: false, chart: null, range: 'cur', week: null, prevWeek: null, todos: null, news: null, newsOpen: {}, saved: null, poll: null, calMonth: '', watching: false };
  const ref = name => S.root && S.root.querySelector(`[data-ref="${name}"]`);

  /* ───────── 줄바꿈: 문장 · 반점 단위 ─────────
     글 묶음(p · li · .note …) 속 글자를 문장 끝 · 반점 뒤에서 나눠 <span class="home-ph">로 감쌈
     → 문장이 바뀌는 곳에서 먼저 줄이 바뀌고, 한 문장이 길면 그 안에서 줄 길이를 고르게 (home.css)
     숫자(1,234 · 3.5)는 뒤에 띄어쓰기가 없어 안 나뉨. 표 · 버튼 · 배지 · 입력칸 · 한 줄 칸은 건드리지 않음 */
  const PH_BLOCK = 'p, li, dd, small, figcaption, blockquote, .note, .panel-note, .hint';
  const PH_SKIP = 'table, button, .btn, label, .badge, input, textarea, select, code, pre, svg, [contenteditable], .home-ph';
  // 문장 끝: 한글 뒤 . ! ? · 소문자 · 닫는 괄호 뒤 . 만 (U.S. · I.V. 약어, 10. 25. 날짜, Weee! 이름은 안 나눔)
  // 반점: 연결 어미(…하고, …며, …지만, …는데) 뒤만 — 나열(맛, 패키지)은 안 나눔
  const PH_CUT = /(?<=[가-힣][.!?]|[a-z)\]'"”’]\.|(?:고|며|면|서|지만|는데|은데|니|도|게|요|다|해|어|아|죠),)(\s+)/;
  function phrase(el) {
    const blocks = el.matches(PH_BLOCK) ? [el] : [];
    blocks.push(...el.querySelectorAll(PH_BLOCK));
    blocks.forEach(b => {
      if (b.closest(PH_SKIP)) return;
      const cs = global.getComputedStyle(b);
      if (cs.whiteSpace === 'nowrap' || cs.textWrapMode === 'nowrap' || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none')) return;
      const walk = doc.createTreeWalker(b, NodeFilter.SHOW_TEXT);
      const texts = [];
      for (let t = walk.nextNode(); t; t = walk.nextNode()) {
        if (PH_CUT.test(t.nodeValue) && !t.parentElement.closest(PH_SKIP)) texts.push(t);
      }
      texts.forEach(t => {
        const frag = doc.createDocumentFragment();
        t.nodeValue.split(PH_CUT).forEach((s, i) => {
          if (i % 2 || !s.trim()) { frag.append(s); return; }
          const sp = doc.createElement('span');
          sp.className = 'home-ph';
          sp.textContent = s;
          frag.append(sp);
        });
        t.replaceWith(frag);
      });
    });
  }
  // 화면은 innerHTML로 자주 다시 그려지므로, 새로 붙은 노드를 모아 한 번에 처리
  function phraseAll(nodes) {
    const todo = new Set();
    nodes.forEach(n => {
      const el = n.nodeType === 3 ? n.parentElement : n;
      if (el && el.nodeType === 1 && el.isConnected && el.closest('.part-home')) todo.add(el.closest(PH_BLOCK) || el);
    });
    todo.forEach(phrase);
  }
  if (doc && global.MutationObserver) {
    const start = () => {
      new global.MutationObserver(ms => phraseAll(ms.flatMap(m => [...m.addedNodes]))).observe(doc.body, { childList: true, subtree: true });
      phraseAll([...doc.querySelectorAll('.part-home')]);
    };
    if (doc.body) start(); else doc.addEventListener('DOMContentLoaded', start);
  }

  /* ───────── 날짜 (YYYY-MM-DD 문자열, UTC로 계산) ───────── */
  const toTime = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  const fromTime = t => new Date(t).toISOString().slice(0, 10);
  const addDays = (iso, n) => fromTime(toTime(iso) + n * DAY);
  const dow = iso => new Date(toTime(iso)).getUTCDay();
  const md = iso => `${iso.slice(5, 7)}.${iso.slice(8, 10)}`;
  function weekStartOf(iso) { const d = dow(iso); return addDays(iso, d === 0 ? -6 : 1 - d); }

  /* ───────── 다른 파트 값 (없거나 실패하면 빈 값) ───────── */
  async function safe(fn) { try { return await Promise.resolve(fn()); } catch (e) { return null; } }
  function csStats() { try { return REBOOT.use('cs').stats() || null; } catch (e) { return null; } }
  function compliancePending() {
    try {
      const p = REBOOT.parts().find(x => x.id === 'compliance');
      const v = p && typeof p.summary === 'function' ? Number(p.summary().value) : NaN;
      return Number.isFinite(v) ? v : null;
    } catch (e) { return null; }
  }
  const weekSummary = () => safe(() => REBOOT.use('performance').weekSummary());
  const campaigns = () => safe(() => REBOOT.use('content').campaigns());
  function seasons() { try { return REBOOT.data.seasons() || []; } catch (e) { return []; } }
  function partLabel(id) {
    const p = (REBOOT.meta() || []).find(x => x.id === id);
    return p ? p.label : id ? id : '워크스페이스';
  }
  function cssVar(name, fallback) {
    const v = global.getComputedStyle(S.root || doc.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  const isUrl = u => /^https?:\/\//i.test(String(u || ''));
  const arrow = d => (d > 0 ? '▲' : d < 0 ? '▼' : '–');

  /* ───────── KPI ───────── */
  async function paintKpis() {
    const box = ref('kpis');
    const [week, camp] = await Promise.all([weekSummary(), campaigns()]);
    S.week = week && !week.empty ? week : null;
    const w = S.week;
    const tone = d => (d == null ? 'flat' : d > 0 ? '' : d < 0 ? 'down' : 'flat');
    const foot = w ? `${w.compare ? w.compare.label : '비교 없음'} · ${w.sample ? '샘플 데이터' : `${md(w.start)}—${md(w.end)}`}` : '성과 데이터 없음';
    const cards = [];
    const link = (href, html) => `<a class="home-kpi-link" href="${href}">${html}</a>`;
    cards.push(link('/dashboard/performance', ui.kpi({
      label: '이번 주 매출', icon: 'dollar', featured: true, value: w ? fmt.num(w.revenue) : '—', unit: w ? '원' : '',
      change: w && w.change && w.change.revenue != null ? `${arrow(w.change.revenue)} ${fmt.num(Math.abs(w.change.revenue), 1)}%` : '',
      tone: w && w.change ? tone(w.change.revenue) : 'flat', foot
    })));
    cards.push(link('/dashboard/performance', ui.kpi({
      label: '광고 수익률 (ROAS)', icon: 'chart', value: w && w.roas != null ? fmt.num(w.roas, 0) : '—', unit: w && w.roas != null ? '%' : '',
      change: w && w.change && w.change.roas != null ? `${arrow(w.change.roas)} ${fmt.num(Math.abs(w.change.roas), 1)}%` : '',
      tone: w && w.change ? tone(w.change.roas) : 'flat', foot
    })));
    if (camp && Number.isFinite(Number(camp.active))) {
      cards.push(link('/dashboard/content', ui.kpi({ label: '진행 중인 캠페인', icon: 'spark', value: fmt.num(camp.active), unit: '개', foot: `전체 ${fmt.num(camp.total || 0)}개 · 콘텐츠 제작` })));
    } else {
      const list = seasons().filter(s => s.inLead || s.ongoing).sort((a, b) => a.dday - b.dday);
      const near = list[0];
      cards.push(link('/dashboard/content', ui.kpi({
        label: '준비 중인 시즌', icon: 'calendar', value: fmt.num(list.length), unit: '개', tone: 'flat',
        change: near ? `${near.name} ${near.ongoing ? '진행 중' : fmt.dday(near.start)}` : '', foot: '준비 기간에 들어온 시즌'
      })));
    }
    const cs = csStats();
    const comp = compliancePending();
    const parts = [];
    if (cs && Number.isFinite(Number(cs.open))) parts.push(`CS ${cs.open}`);
    if (comp != null) parts.push(`카피 ${comp}`);
    const csOpen = cs && Number(cs.open) || 0;
    const total = csOpen + (comp || 0);
    // 대기 건수가 더 많은 파트로 (같으면 컴플라이언스)
    cards.push(link(csOpen > (comp || 0) ? '/dashboard/cs' : '/dashboard/compliance', ui.kpi({
      label: '검토가 필요한 업무', icon: 'clock', value: parts.length ? fmt.num(total) : '—', unit: parts.length ? '건' : '', tone: 'flat',
      change: parts.join(' · '), foot: parts.length ? '담당자 확인 필요' : '연결된 파트 없음'
    })));
    box.innerHTML = cards.join('');
  }

  /* ───────── 이번 주 성과 차트 ───────── */
  function destroyChart() { if (S.chart) { try { S.chart.destroy(); } catch (e) { /* 이미 없음 */ } S.chart = null; } }
  async function paintChart() {
    const card = ref('chart-card');
    const w = S.week;
    if (!w) {
      destroyChart();
      ref('chart-body').innerHTML = ui.empty('성과 데이터가 없어요. 성과분석에서 매출 CSV를 올리면 여기에 이번 주 매출·광고비가 나옵니다.', 'chart');
      return;
    }
    const prevBtn = card.querySelector('[data-range="prev"]');
    if (prevBtn) prevBtn.disabled = !w.compare;
    let s = w;
    if (S.range === 'prev' && w.compare) {
      if (!S.prevWeek) S.prevWeek = await safe(() => REBOOT.use('performance').summary({ start: w.compare.start, end: w.compare.end }));
      if (S.prevWeek) s = S.prevWeek;
    }
    ref('chart-tag').innerHTML = w.sample ? badge('샘플 데이터', 'orange') : '';
    ref('chart-value').textContent = fmt.krw(s.revenue);
    const ch = s.change && s.change.revenue;
    ref('chart-change').innerHTML = ch != null ? badge(`${arrow(ch)} ${fmt.num(Math.abs(ch), 1)}%`, ch >= 0 ? 'green' : 'red') : '';
    ref('chart-range').textContent = `${s.start.replace(/-/g, '.')} — ${md(s.end)} 매출 합계 · 광고비 ${fmt.krw(s.spend)}`;
    drawChart(s.daily || []);
  }
  function drawChart(daily) {
    destroyChart();
    const C = global.Chart;
    const canvas = ref('chart');
    if (!C || !canvas) return;
    const muted = cssVar('--muted', '#858981'), line = cssVar('--line', '#e9ece4');
    const rev = cssVar('--home-rev', '#759a08'), spend = cssVar('--home-spend', '#334324');
    const fillTop = cssVar('--home-rev-fill', 'rgba(215,255,63,.35)'), surface = cssVar('--surface', '#ffffff');
    const family = global.getComputedStyle(doc.body).fontFamily;
    const font = size => ({ family, size });
    S.chart = new C(canvas, {
      type: 'line',
      data: {
        labels: daily.map(d => `${md(d.date)} ${WEEKDAY[dow(d.date)]}`),
        datasets: [
          {
            label: '매출', data: daily.map(d => d.revenue), borderColor: rev, borderWidth: 2, tension: 0.35,
            pointRadius: 0, pointHoverRadius: 5, pointBackgroundColor: rev, pointBorderColor: surface, pointBorderWidth: 2,
            fill: 'origin',
            backgroundColor: ctx => {
              const area = ctx.chart.chartArea;
              if (!area) return 'transparent';
              const g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
              g.addColorStop(0, fillTop);
              g.addColorStop(1, 'rgba(215,255,63,0)');
              return g;
            }
          },
          {
            label: '광고비', data: daily.map(d => d.spend), borderColor: spend, backgroundColor: spend, borderWidth: 1.6,
            borderDash: [5, 5], tension: 0.35, pointRadius: 0, pointHoverRadius: 4, pointBorderColor: surface, pointBorderWidth: 2, fill: false
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },  // 범례는 카드 위 HTML (매출 · 광고비)
          tooltip: { titleFont: font(11), bodyFont: font(12), padding: 10, callbacks: { label: c => ` ${c.dataset.label}: ${fmt.krw(c.parsed.y)}` } }
        },
        scales: {
          x: { grid: { display: false }, border: { color: line }, ticks: { color: muted, font: font(10), maxRotation: 0 } },
          y: {
            beginAtZero: true, grid: { color: line }, border: { display: false },
            ticks: { color: muted, font: font(10), maxTicksLimit: 5, callback: v => (Math.abs(v) >= 10000 ? fmt.num(Math.round(v / 10000)) + '만' : fmt.num(v)) }
          }
        }
      }
    });
  }

  /* ───────── 시즌 (브리핑 히어로 · 시즌 카드 · 콘텐츠 캘린더) ───────── */
  const seasonKey = s => (s.ongoing ? -1 : s.dday);
  function upcomingSeasons() {
    return seasons().filter(s => s.ongoing || s.dday >= 0).sort((a, b) => seasonKey(a) - seasonKey(b));
  }
  const pad2 = v => (v == null ? '—' : String(v).padStart(2, '0'));
  function paintHero() {
    const title = ref('hero-title');
    if (!title) return;
    const s = upcomingSeasons()[0];
    title.textContent = !s ? '이번 주 우선순위를 정해 보세요.'
      : s.ongoing ? `${s.name}, 지금이 바로 그 시즌.`
        : s.dday === 0 ? `오늘은 ${s.name}.` : `${s.name}까지 ${s.dday}일, 준비는 지금부터.`;
    const n = S.news;
    ref('hero-news').textContent = n ? pad2(topSurges(n).length) : '—';
    ref('hero-todo').textContent = S.todos ? pad2(S.todos.items.filter(i => !i.done).length) : '—';
    ref('hero-season').textContent = s ? (s.ongoing ? 'NOW' : fmt.dday(s.start)) : '—';
  }
  function paintSeasonCards() {
    const box = ref('season-cards');
    const list = upcomingSeasons().slice(0, 3);
    if (!list.length) {
      box.innerHTML = `<div class="card" style="grid-column:1/-1">${ui.empty('다가오는 시즌이 없어요. 설정 › 수집 설정에서 시즌을 추가하세요.', 'calendar')}</div>`;
      return;
    }
    box.innerHTML = list.map((s, i) => {
      const status = s.ongoing ? badge('진행 중', 'green') : s.inLead ? badge('준비 기간', 'green') : badge('다가오는 시즌');
      const range = s.end && s.end !== s.start ? `${s.start.replace(/-/g, '.')} — ${md(s.end)}` : s.start.replace(/-/g, '.');
      const note = s.notes || (s.ongoing ? '게시 일정과 재고를 확인하세요.' : `준비 시작 ${md(addDays(s.start, -s.leadDays))} · 준비 기간 ${s.leadDays}일`);
      return `<div class="season-card${i === 0 && (s.inLead || s.ongoing) ? ' featured' : ''}">
        <div class="between">${status}<span class="days-left">${s.ongoing ? 'NOW' : esc(fmt.dday(s.start))}</span></div>
        <h3>${esc(s.name)}</h3><p>${esc(range)} (${WEEKDAY[dow(s.start)]})</p><div class="rule"></div><p>${esc(note)}</p>
        <a class="link" href="/dashboard/content">캠페인 준비 ${icon('arrow')}</a></div>`;
    }).join('');
  }

  function paintCalendar() {
    const box = ref('calendar');
    const today = REBOOT.today();
    const ym = S.calMonth || today.slice(0, 7);
    const y = +ym.slice(0, 4), m = +ym.slice(5, 7);
    const first = `${ym}-01`;
    const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const lead = dow(first);
    const cells = Math.ceil((lead + days) / 7) * 7;
    const list = seasons();
    const html = [];
    for (let i = 0; i < cells; i++) {
      const iso = addDays(first, i - lead);
      const pills = list.filter(s => iso >= s.start && iso <= s.end)
        .map(s => `<a class="event-pill" href="/dashboard/content" title="${esc(s.name)}${s.notes ? ` · ${esc(s.notes)}` : ''}">${esc(s.name)}</a>`)
        .concat(list.filter(s => s.leadDays > 0 && addDays(s.start, -s.leadDays) === iso)
          .map(s => `<span class="event-pill lead" title="${esc(s.name)} ${esc(fmt.dday(s.start))} · 준비 기간 ${esc(s.leadDays)}일 시작">${esc(s.name)} 준비 시작</span>`));
      html.push(`<div class="day${iso.slice(0, 7) === ym ? '' : ' dim'}${iso === today ? ' today' : ''}">
        <div class="between"><span>${+iso.slice(8, 10)}</span>${iso === today ? badge('오늘', 'green') : ''}</div>${pills.join('')}</div>`);
    }
    const upcoming = upcomingSeasons().slice(0, 6);
    const item = s => {
      const lt = addDays(s.start, -s.leadDays);
      const info = s.ongoing ? `진행 중${s.end !== s.start ? ` · ~${md(s.end)}` : ''}`
        : s.inLead ? `${fmt.dday(s.start)} · 준비 기간 ${s.leadDays}일 (${md(lt)}부터)`
          : `${fmt.dday(s.start)} · 준비 시작 ${md(lt)}`;
      return `<div class="schedule-item home-sched${s.inLead || s.ongoing ? ' is-lead' : ''}">
        <div class="date-box">${+s.start.slice(8, 10)}<small>${+s.start.slice(5, 7)}월 ${WEEKDAY[dow(s.start)]}</small></div>
        <div><p>${esc(s.name)}${s.inLead ? badge('준비 중', 'lime') : s.ongoing ? badge('진행 중', 'green') : ''}</p><span>${esc(info)}</span></div></div>`;
    };
    box.innerHTML = `
      <section class="card">
        <div class="card-head home-cal-head">
          <div class="flex"><h2 class="home-cal-title">${y}년 ${m}월</h2>
            <button type="button" class="btn small" data-act="cal-prev" aria-label="이전 달">‹</button>
            <button type="button" class="btn small" data-act="cal-next" aria-label="다음 달">›</button>
            <button type="button" class="btn small" data-act="cal-today">오늘</button></div>
          <div class="flex">${badge('시즌', 'green')}${badge('준비 시작')}</div></div>
        <div class="calendar-scroll">
          <div class="weekdays home-cal-weekdays" aria-hidden="true">${['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'].map(d => `<span>${d}</span>`).join('')}</div>
          <div class="calendar-large" aria-label="${y}년 ${m}월 콘텐츠 캘린더 (미국 시즌 · 준비 시작일)">${html.join('')}</div>
        </div>
        <p class="source-label">시즌 일정은 설정 › 수집 설정의 시즌 캘린더에서 고칩니다. 준비 시작일 = 시즌 시작일 − 준비 기간.</p>
      </section>
      <section class="card section-gap"><div class="card-head"><h2>다가오는 일정</h2>${badge(`시즌 ${upcoming.length}개`)}</div>
        <div class="home-sched-grid">${upcoming.length ? upcoming.map(item).join('') : '<p class="panel-note">다가오는 시즌이 없어요. 설정 › 수집 설정에서 추가하세요.</p>'}</div></section>`;
  }
  function moveMonth(n) {
    const cur = S.calMonth || REBOOT.today().slice(0, 7);
    let y = +cur.slice(0, 4), m = +cur.slice(5, 7) + n;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    S.calMonth = `${y}-${String(m).padStart(2, '0')}`;
    paintCalendar();
  }

  /* 다음 급등 예상 (캘린더 맨 아래 · 접기) — 인사이트 검색 트렌드(/api/insights/data/trends)의 next */
  async function paintSurge() {
    const box = ref('surge');
    if (!box || box.dataset.loaded) return;
    box.dataset.loaded = '1';
    const fold = inner => `<details class="card section-gap home-surge"><summary><h2>다음 급등 예상</h2>${badge('작년 같은 시기 검색 · Google Trends')}</summary>
      <div class="home-surge-body">${inner}<a class="link" href="/dashboard/settings#collect">시즌 편집 (설정)</a></div></details>`;
    let d;
    try { d = await REBOOT.api('/api/insights/data/trends'); } catch (e) {
      box.innerHTML = fold(`<p class="panel-note">불러오지 못했어요: ${esc(e.message)}</p>`);
      delete box.dataset.loaded;
      return;
    }
    const next = ((d && d.next) || []).map(n => `<li><div class="between"><b>${esc(n.name)}</b><span class="flex">${n.opportunity ? badge('기회', 'green') : ''}<span class="small muted">${esc(fmt.dday(n.start))}</span></span></div>
      <small>${n.lift ? esc(`작년 같은 시기 '${n.lift.keyword}' 검색이 12개월 평균 대비 ${fmt.signed(n.lift.pct, 0, '%')} (${n.lift.date} 주)`) : '작년 같은 시기 데이터 없음'}</small></li>`).join('');
    box.innerHTML = fold(next ? `<ul class="home-surge-list">${next}</ul>` : '<p class="panel-note">모니터링 중인 다가오는 시즌이 없어요.</p>');
  }

  /* ───────── 최근 활동 ───────── */
  async function paintActivity() {
    const box = ref('activity');
    try {
      const { items } = await REBOOT.api('/api/worklog?limit=5');
      box.innerHTML = (items || []).length
        ? items.map(x => `<li>${esc(x.action)}${x.status === '실패' ? ` ${badge('실패', 'red')}` : ''}<small>${esc(partLabel(x.part))} · ${esc(fmt.relative(x.at))} · ${esc(x.user)}</small></li>`).join('')
        : '<li>아직 기록이 없습니다.<small>작업을 하면 여기에 쌓입니다.</small></li>';
    } catch (e) {
      box.innerHTML = `<li>활동을 불러오지 못했습니다.<small>${esc(e.message)}</small></li>`;
    }
  }

  /* ───────── 이번 주 To-Do ───────── */
  async function buildContext() {
    const ctx = {};
    const cs = csStats();
    if (cs) ctx.cs = { open: cs.open, urgent: cs.urgent, overdue: cs.overdue, owner_required: cs.owner_required };
    const comp = compliancePending();
    if (comp != null) ctx.compliance = { pending: comp };
    const w = S.week || (await weekSummary());
    if (w && !w.empty) {
      ctx.performance = { start: w.start, end: w.end, revenue: w.revenue, spend: w.spend, roas: w.roas, conversions: w.conversions,
        change: { revenue: w.change && w.change.revenue, roas: w.change && w.change.roas }, sample: !!w.sample };
    }
    const c = await campaigns();
    if (c && Number.isFinite(Number(c.active))) ctx.content = { active: c.active, total: c.total };
    return ctx;
  }
  function saveBadge() {
    const t = S.todos;
    if (!t) return;
    const v = { week: t.week.start, open: t.items.filter(i => !i.done).length };
    const old = store.get('todoBadge', null);
    if (!old || old.week !== v.week || old.open !== v.open) store.set('todoBadge', v);
    dock.sync(t);
  }
  function todoHtml(t) {
    const tag = t.origin === 'ai' ? badge('AI', 'purple') : t.origin === 'rule' ? badge('규칙') : badge('직접', 'blue');
    const src = t.source && t.source !== 'home' && t.source !== 'manual'
      ? ` · <a class="link" href="/dashboard/${encodeURIComponent(t.source)}">${esc(partLabel(t.source))}</a>` : '';
    return `<div class="task${t.done ? ' done' : ''}" data-id="${esc(t.id)}">
      <label class="home-task-main"><input type="checkbox" data-act="todo-toggle"${t.done ? ' checked' : ''} aria-label="완료 표시: ${esc(t.title)}">
        <span class="grow"><span class="task-title">${esc(t.title)}</span><span class="meta">${tag}근거: ${esc(t.reason || '—').replace(/\bD-(\d)/g, 'D‑$1')}${src}${t.done && t.doneBy ? ` · 완료 ${esc(t.doneBy)}` : ''}</span></span></label>
      <button type="button" class="home-todo-del" data-act="todo-del" aria-label="삭제: ${esc(t.title)}">${icon('x')}</button></div>`;
  }
  function paintTodos() {
    const t = S.todos;
    const list = ref('todos');
    if (!t) return;
    const open = t.items.filter(i => !i.done).length;
    ref('todo-count').textContent = t.items.length ? `${open}개 남음` : '';
    const how = !t.generated ? '아직 만들지 않음' : t.origin === 'ai' ? 'AI가 시즌·뉴스·현황을 종합 (참고용)' : `규칙으로 생성${t.aiReady ? '' : ' (AI 미연결)'}`;
    ref('todo-meta').textContent = `${md(t.week.start)} — ${md(t.week.end)} · ${how}`;
    list.innerHTML = t.items.length ? t.items.map(todoHtml).join('') : ui.empty('이번 주 할 일이 없어요. + 로 추가하거나 다시 만들기를 누르세요.', 'check');
    saveBadge();
    paintHero();
  }
  async function loadTodos() {
    try {
      let data = await REBOOT.api('/api/home/todos');
      if (!data.generated) {
        // 이번 주 처음 열면 자동 생성 (키가 없으면 규칙)
        data = await REBOOT.api('/api/home/todos/generate', { auto: true, context: await buildContext() });
        if (data.warning) toast(data.warning, 'error');
        if (!data.skipped) paintActivity();
      }
      S.todos = data;
      paintTodos();
    } catch (e) {
      ref('todos').innerHTML = `<p class="note error">할 일을 불러오지 못했습니다: ${esc(e.message)}</p>`;
    }
  }
  async function regenerate(btn) {
    const ok = await ui.confirm('완료하지 않은 자동 항목을 새로 만듭니다. 직접 추가한 항목과 완료한 항목은 그대로 둡니다.', { ok: '다시 만들기' });
    if (!ok) return;
    busy(btn, true);
    try {
      const data = await REBOOT.api('/api/home/todos/generate', { auto: false, context: await buildContext() });
      S.todos = data;
      paintTodos();
      toast(data.warning || (data.origin === 'ai' ? 'AI가 이번 주 할 일을 다시 만들었습니다 (참고용).' : '규칙으로 이번 주 할 일을 다시 만들었습니다.'), data.warning ? 'error' : undefined);
    } catch (e) { toast(e.message, 'error'); }
    busy(btn, false);
  }
  async function toggleTodo(id, done, box) {
    const item = S.todos.items.find(i => String(i.id) === String(id));
    if (!item) return;
    try {
      const u = await REBOOT.api(`/api/home/todos/${encodeURIComponent(id)}`, { done }, { method: 'PATCH' });
      Object.assign(item, u);
      S.todos.items.sort((a, b) => (a.done - b.done));
      paintTodos();
    } catch (e) {
      if (box) box.checked = !done;
      toast(e.message, 'error');
    }
  }
  async function deleteTodo(id) {
    const item = S.todos.items.find(i => String(i.id) === String(id));
    if (!item || !(await ui.confirm(`'${item.title}'을(를) 지울까요?`, { ok: '지우기', danger: true }))) return;
    try {
      await REBOOT.api(`/api/home/todos/${encodeURIComponent(id)}`, undefined, { method: 'DELETE' });
      S.todos.items = S.todos.items.filter(i => i !== item);
      paintTodos();
    } catch (e) { toast(e.message, 'error'); }
  }
  async function addTodo(form) {
    const title = form.querySelector('[name="title"]').value.trim();
    const reason = form.querySelector('[name="reason"]').value.trim();
    if (!title) { toast('할 일을 입력하세요.', 'error'); return; }
    const btn = form.querySelector('button[type="submit"]');
    busy(btn, true);
    try {
      const t = await REBOOT.api('/api/home/todos', { title, reason });
      S.todos.items.splice(S.todos.items.findIndex(i => i.done) < 0 ? S.todos.items.length : S.todos.items.findIndex(i => i.done), 0, t);
      form.reset();
      form.hidden = true;
      paintTodos();
    } catch (e) { toast(e.message, 'error'); }
    busy(btn, false);
  }

  /* ───────── 미국 시장 브리핑 (업계 뉴스 · 트렌드) ───────── */
  function newsBadges(n) {
    const out = [];
    if (n.running) out.push(badge('수집 중…', 'blue'));
    else if (n.status === 'failed') out.push(badge('업데이트 실패', 'red'));
    else if (n.status === 'partial') out.push(badge('일부 실패', 'orange'));
    else if (n.status === 'never') out.push(badge('수집 전', 'orange'));
    if (n.ai) out.push(badge('AI 요약 · 참고용', 'purple'));
    else if (!n.aiReady) out.push(badge('AI 요약 미연결'));
    else if (n.aiStatus === 'error') out.push(badge('AI 요약 실패', 'orange'));
    return out.join(' ');
  }
  function articleLink(a, text) {
    return isUrl(a.url) ? `<a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text);
  }
  // 히어로 '뉴스 시그널' 숫자: 업계 핵심 분야(자리 보장: 숙취 · 경쟁사 · 규제)의 보도 급증 수. 분야별 변화는 검색어 점검 안에서 봄
  function topSurges(n) {
    const core = new Set((n.groups || []).filter(g => g.kind === 'signal').map(g => g.id));
    return (n.signals || []).filter(s => s.surge && core.has(s.group));
  }

  /* 기사 보관 (책갈피): /api/home/news/saved — 시장·고객 인사이트 › 데이터 › 뉴스 수집에서 모아 봄 */
  const BOOKMARK = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h12v17l-6-4.2-6 4.2z"/></svg>';
  const savedOf = url => (S.saved || []).find(s => s.url === url);
  function saveBtn(a) {
    if (!isUrl(a.url) || !S.saved) return '';
    const on = !!savedOf(a.url);
    return `<button type="button" class="home-save${on ? ' on' : ''}" data-act="news-save" data-url="${esc(a.url)}" aria-pressed="${on}"
      aria-label="${on ? '보관 해제' : '보관'}" title="${on ? '보관됨 · 누르면 해제' : '보관 (시장·고객 인사이트 › 데이터 › 뉴스 수집)'}">${BOOKMARK}</button>`;
  }
  async function loadSaved() {
    const r = await safe(() => REBOOT.api('/api/home/news/saved'));
    S.saved = r ? r.items || [] : null;
  }
  async function toggleSave(btn) {
    const url = btn.dataset.url;
    const a = ((S.news && S.news.ai && S.news.ai.items) || []).concat((S.news && S.news.articles) || []).find(x => x.url === url);
    const cur = savedOf(url);
    busy(btn, true);
    try {
      if (cur) {
        await REBOOT.api(`/api/home/news/saved/${cur.id}`, undefined, { method: 'DELETE' });
        S.saved = S.saved.filter(s => s.id !== cur.id);
        toast('보관을 해제했어요.');
      } else if (a) {
        const s = await REBOOT.api('/api/home/news/saved', { url, title: a.title, titleKo: a.titleKo || '', summaryKo: a.summaryKo || '',
          whyKo: a.whyKo || '', kind: a.kind || 'flow', areaLabel: a.areaLabel || a.topic || a.groupLabel || '', domain: a.domain || '', date: a.date || '' });
        S.saved = [s].concat(S.saved.filter(x => x.url !== url));
        toast('보관했어요. 시장·고객 인사이트 › 데이터 › 뉴스 수집에서 볼 수 있어요.');
      }
    } catch (e) { toast(e.message, 'error'); }
    paintNews();
  }
  // 목록은 처음 3개만. 나머지는 목록 끝 얇은 한 줄('N건 더 보기')로 그 자리에서 펼침 (펼친 기사도 위와 같은 모양: 제목 · → 마케터의 시선)
  // 업계 · 트렌드 칸마다 펼친 상태를 따로 기억
  const NEWS_SHOW = 3;
  function newsList(list, key = 'news', show = NEWS_SHOW) {
    if (list.length <= show) return list.join('');
    const open = !!S.newsOpen[key];
    return `${list.slice(0, show).join('')}<div class="home-rows-more" ${open ? '' : 'hidden'}>${list.slice(show).join('')}</div>
      <button type="button" class="home-news-more" data-act="news-more" data-key="${key}" aria-expanded="${open}">${open ? '접기' : `${list.length - show}건 더 보기`}<span class="home-news-more-ic" aria-hidden="true">${open ? '⌃' : '⌄'}</span></button>`;
  }

  // 검색어 점검 (최근 60일): 찾은 기사 · 규칙 통과 · 카드로 고른 수 · 교체 후보
  function queryStatsHtml(list, signals) {
    if (!list.length) return '';
    const sig = {};
    (signals || []).forEach(s => { sig[s.group] = s; });
    const change = q => { const s = sig[q.id]; return !s ? '' : `${s.surge ? badge('급증', 'orange') + ' ' : ''}${s.changePct != null ? esc(fmt.signed(s.changePct, 0, '%')) : '—'}`; };
    const flagged = list.filter(q => q.replace).length;
    const rows = list.map(q => `<tr><td>${esc(q.areaLabel)}${q.kind === 'signal' ? ' ' + badge('자리 보장', 'lime') : ''}</td>
      <td>${q.feed ? `${badge('RSS')} ${esc(q.label)}` : `<code>${esc(q.query)}</code>`}</td><td class="num">${fmt.num(q.found)}</td><td class="num">${fmt.num(q.kept)}</td>
      <td class="num">${fmt.num(q.picked)}</td><td class="num">${change(q)}</td><td>${q.replace ? badge('교체 후보', 'orange') : ''}</td></tr>`).join('');
    return `<details class="home-qstats"><summary>검색어 점검 (최근 60일)${flagged ? ` · 교체 후보 ${flagged}개` : ''}</summary>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>분야</th><th>검색어 · 피드</th><th class="num">찾은 기사</th><th class="num">규칙 통과</th><th class="num">카드</th><th class="num" title="최근 7일 vs 그 전 7일 기사 수">보도 변화</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="panel-note">30일 동안 고른 기사가 0개면 교체 후보예요. 검색어 · 피드는 tabs/home/data/queries.json에서 바꿔요.</p></details>`;
  }

  /* 미국 시장 브리핑 — 3초 안에 훑어보기 (2026-10-25 다시 설계)
     헤더 한 줄(제목 · 배송일 · 새 소식 · 업계/트렌드 · 새로고침) → 오늘의 핵심 1개(점수가 가장 높은 기사, 크게)
     → 나머지는 짧은 목록(번호 · 제목 · → 마케터의 시선 · 분야 · 시간 · 책갈피) → 하단 한 줄(보관함 · 수집 정보 · 검색어 점검)
     읽은 기사는 회색 (읽음 기록은 각자 브라우저에만: REBOOT.store('home') 'newsRead', 최근 300건) */
  const READ_MAX = 300;
  // 10.25 09:12 처럼 짧게 (내 컴퓨터 시간)
  function shortStamp(iso) {
    const d = new Date(iso);
    return isNaN(d) ? '' : `${d.getMonth() + 1}.${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
  const readSet = () => new Set(store.get('newsRead', []) || []);
  function markRead(url) {
    if (!isUrl(url)) return;
    const list = (store.get('newsRead', []) || []).filter(u => u !== url);
    list.unshift(url);
    store.set('newsRead', list.slice(0, READ_MAX));
  }
  const newsKind = () => (store.get('newsKind', 'flow') === 'trend' ? 'trend' : 'flow');
  function kindTabs(k, flow, trend) {
    const chip = (id, label, count) => `<button type="button" class="chip${k === id ? ' selected' : ''}" aria-pressed="${k === id}" data-act="news-kind" data-kind="${id}"${count ? '' : ' disabled'}>${label}<span class="n">${count}</span></button>`;
    return `<div class="chips home-kind" role="group" aria-label="뉴스 종류">${chip('flow', '업계', flow)}${chip('trend', '트렌드', trend)}</div>`;
  }
  const viewLabel = a => (a.kind === 'trend' ? '적용점' : '마케터의 시선');
  // 마우스를 올리면 보이는 설명: 요약 · 영어 원제목
  const tipText = a => [a.whyKo ? `${viewLabel(a)}: ${a.whyKo}` : '', a.summaryKo, a.titleKo && !a.fallback ? a.title : ''].filter(Boolean).join('\n');
  function leadHtml(a, read) {
    const meta = ['오늘의 핵심', a.areaLabel || a.topic || a.groupLabel, a.domain, fmt.relative(a.date)].filter(Boolean).map(esc).join(' · ');
    return `<article class="home-lead${isUrl(a.url) ? ' home-news-card' : ''}${read ? ' read' : ''}"${isUrl(a.url) ? ` data-href="${esc(a.url)}"` : ''} title="${esc(a.titleKo && !a.fallback ? a.title : '')}">
        <div class="home-lead-top"><span class="home-lead-meta">${meta}${a.surge ? ` · <b>보도 급증</b>` : ''}</span>${saveBtn(a)}</div>
        <p class="home-lead-title">${articleLink(a, a.titleKo || a.title)}</p>
        ${a.summaryKo ? `<p class="home-lead-sum">${esc(a.summaryKo)}</p>` : ''}
        ${a.whyKo ? `<p class="home-lead-view"><span class="sr-only">${viewLabel(a)}: </span>${esc(a.whyKo)}</p>` : ''}
      </article>`;
  }
  function rowHtml(a, i, read) {
    const side = [a.areaLabel || a.topic || a.groupLabel, fmt.relative(a.date)].filter(Boolean).map(esc).join(' · ');
    return `<article class="home-row${isUrl(a.url) ? ' home-news-card' : ''}${read ? ' read' : ''}"${isUrl(a.url) ? ` data-href="${esc(a.url)}"` : ''} title="${esc(tipText(a))}">
        <span class="home-row-no">${i}</span>
        <div class="home-row-main"><p class="home-row-title">${articleLink(a, a.titleKo || a.title)}${a.fallback ? ' ' + badge('요약 없음') : ''}</p>
          ${a.whyKo ? `<p class="home-row-view"><span aria-hidden="true">→</span> <span class="sr-only">${viewLabel(a)}: </span>${esc(a.whyKo)}</p>` : a.domain && !a.titleKo ? `<p class="home-row-view">${esc(a.domain)}</p>` : ''}</div>
        <span class="home-row-side">${a.surge ? '<b>급증</b> · ' : ''}${side}</span>${saveBtn(a)}</article>`;
  }

  function paintNews() {
    const n = S.news;
    if (!n) return;
    const body = ref('news-body');
    busy(ref('news-collect'), !!n.running);
    const read = readSet();
    const all = n.ai ? (n.ai.items || []) : (n.articles || []);
    const flow = all.filter(a => a.kind !== 'trend'), trend = all.filter(a => a.kind === 'trend');
    const k = !flow.length && trend.length ? 'trend' : !trend.length ? 'flow' : newsKind();
    const list = k === 'trend' ? trend : flow;
    const unread = n.lastSuccessAt ? all.filter(a => isUrl(a.url) && !read.has(a.url)).length : 0;
    const sub = n.running ? '받는 중…' : n.lastSuccessAt ? [`${shortStamp(n.lastSuccessAt)} 배송`, unread ? `새 소식 ${unread}` : ''].filter(Boolean).join(' · ') : '';
    ref('news-sub').textContent = sub;
    if (!n.lastSuccessAt && !(n.articles || []).length) {
      const msg = n.running ? '첫 브리핑을 준비하고 있어요 (보통 1분 안팎).'
        : n.status === 'never' ? '아직 받은 브리핑이 없어요. 오른쪽 위 새로고침을 누르면 보통 1분 안팎 걸려요.'
          : '아직 받은 브리핑이 없어요 (마지막 수집 실패). 잠시 뒤 새로고침을 다시 눌러 주세요.';
      body.innerHTML = ui.empty(msg, 'mail');
    } else if (!list.length) {
      body.innerHTML = `<p class="panel-note home-brief-empty">${n.ai ? '이번에는 마케팅과 관련된 기사를 찾지 못했어요.' : '최근 7일 기사가 없어요.'}</p>`;
    } else {
      // AI 요약이 있으면 점수가 가장 높은 기사를 '오늘의 핵심'으로 크게, 나머지는 짧은 목록
      const lead = n.ai ? list.slice().sort((a, b) => (b.score || 0) - (a.score || 0))[0] : null;
      const rest = list.filter(a => a !== lead);
      const rows = rest.map((a, i) => rowHtml(a, i + (lead ? 2 : 1), read.has(a.url)));
      body.innerHTML = `${lead ? leadHtml(lead, read.has(lead.url)) : ''}${rows.length ? `<div class="home-rows">${newsList(rows, k)}</div>` : ''}
        ${!n.ai ? (n.aiReady ? (n.aiStatus === 'error' ? `<p class="note warn mt-8">AI 요약 실패: ${esc(n.aiError)} — 다음 수집 때 다시 시도합니다.</p>` : '')
          : '<p class="panel-note mt-8">지금은 최근 기사 제목만 보여 줘요(관련 없는 기사가 섞일 수 있어요). 설정 › 연결에 OpenAI 키를 넣으면 관련 기사만 골라 정리해요.</p>') : ''}`;
    }
    ref('news-tabs').innerHTML = n.ai && all.length ? kindTabs(k, flow.length, trend.length) : '';
    const last = n.lastRun;
    const bits = [];
    if (n.issueNo) bits.push(`REBOOT 마켓 레터 제 ${fmt.num(n.issueNo)}호`);
    if (n.running) bits.push('구글 뉴스 · 업계지에서 받는 중… 끝나면 자동으로 바뀌어요');
    else if (last) {
      bits.push(`마지막 수집 ${fmt.datetime(last.finishedAt || last.startedAt)}${last.trigger === 'daily' ? ' (자동)' : ''}`);
      if (n.status === 'failed' && n.lastSuccessAt) bits.push(`마지막 성공 ${fmt.datetime(n.lastSuccessAt)}`);
    }
    if (n.articleCount7d) bits.push(`최근 7일 기사 ${fmt.num(n.articleCount7d)}건`);
    ref('news-saved').innerHTML = S.saved ? `<a href="/dashboard/insights#data/news">${BOOKMARK} 보관함 ${fmt.num(S.saved.length)}건</a>` : '';
    ref('news-badges').innerHTML = newsBadges(n);
    ref('news-meta').textContent = bits.join(' · ');
    const errs = last && !n.running && (n.status === 'failed' || n.status === 'partial') ? (last.errors || []) : [];
    ref('news-errors').innerHTML = errs.length ? `<details class="home-errors"><summary>실패 이유 ${errs.length}건</summary><ul>${errs.slice(0, 6).map(e => `<li>${esc(e)}</li>`).join('')}</ul></details>` : '';
    ref('news-note').textContent = n.limitNote || '';
    ref('news-stats').innerHTML = queryStatsHtml(n.queryStats || [], n.signals);
    paintHero();
  }
  async function loadNews() {
    try {
      const [n] = await Promise.all([REBOOT.api('/api/home/news'), loadSaved()]);
      S.news = n;
      paintNews();
      if (S.news.running) startPolling();
    } catch (e) {
      ref('news-body').innerHTML = `<p class="note error">뉴스를 불러오지 못했습니다: ${esc(e.message)}</p>`;
    }
  }
  function startPolling() {
    if (S.poll) return;
    const started = Date.now();
    S.poll = setInterval(async () => {
      if (!S.root || !doc.body.contains(S.root) || Date.now() - started > 10 * 60 * 1000) { clearInterval(S.poll); S.poll = null; return; }
      const st = await safe(() => REBOOT.api('/api/home/news/status'));
      if (!st || st.running) return;
      clearInterval(S.poll);
      S.poll = null;
      await loadNews();
      const ok = st.lastRun && st.lastRun.status !== 'failed';
      toast(ok ? '뉴스 수집을 마쳤습니다.' : '뉴스를 새로 받지 못했습니다. 마지막 결과를 그대로 보여 줍니다.', ok ? undefined : 'error');
      paintActivity();
    }, 4000);
  }
  async function collectNews(btn) {
    busy(btn, true);
    try {
      await REBOOT.api('/api/home/news/collect', {});
      toast('뉴스 수집을 시작했습니다. 보통 1분 안팎 걸립니다.');
      S.news = Object.assign({}, S.news, { running: true });
      paintNews();
      startPolling();
    } catch (e) {
      toast(e.message, 'error');
      busy(btn, false);
      if (e.status === 409) startPolling();
    }
  }

  /* ───────── 화면 ───────── */
  function watchTheme() {
    if (S.watching) return;
    S.watching = true;
    const redraw = () => { if (S.chart && S.week) paintChart(); };
    if (global.MutationObserver) new global.MutationObserver(redraw).observe(doc.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    if (global.matchMedia) {
      const mq = global.matchMedia('(prefers-color-scheme: dark)');
      if (mq.addEventListener) mq.addEventListener('change', redraw);
    }
  }

  /* ───────── 오늘 할 일 (각 파트 summary()를 시간대별로 모음) ─────────
     파트가 summary().todos를 주면 그대로, 없으면 value > 0일 때 '라벨 N건' 한 줄 (docs/DEV_GUIDE.md)
     성과는 performance.weekSummary()로 아침 확인 한 줄 (성과분석 summary()가 없어서)
     저장: store 'today' = {date, done:[id], items:{id: 할 일}} — 팀 공용 · 날짜가 바뀌면 비움
     오늘 본 할 일이 파트에서 사라지면(문의 처리 등) 완료로 남김 */
  const SLOTS = [['morning', '아침 확인 · 09:00'], ['make', '만들기 · 10:00'], ['send', '보내기 · 16:00']];
  const SLOT_OF = { cs: 'morning', performance: 'morning', compliance: 'make', insights: 'make', content: 'make', partners: 'send' };
  const SOURCE = { cs: ['CS', 'blue'], compliance: ['심의', 'red'], insights: ['인사이트', 'green'], partners: ['협업', 'purple'], performance: ['성과', ''] };
  const T = { perf: null, perfLoaded: false, sel: '' };
  const partUrl = id => (id === 'home' ? '/dashboard' : `/dashboard/${encodeURIComponent(id)}`);
  const safeHref = h => (typeof h === 'string' && h.startsWith('/') && !h.startsWith('//') ? h : null);
  const hasSummary = id => REBOOT.parts().some(p => p.id === id && typeof p.summary === 'function');

  function partTodos() {
    const out = [];
    REBOOT.parts().forEach(p => {
      if (p.id === 'home' || typeof p.summary !== 'function') return;
      let s = null;
      try { s = p.summary(); } catch (e) { return; }
      if (!s) return;
      const [source, tone] = SOURCE[p.id] || [p.label, ''];
      const base = { part: p.id, source, tone, slot: SLOT_OF[p.id] || 'make', href: partUrl(p.id) };
      if (Array.isArray(s.todos)) {
        s.todos.forEach(t => {
          if (!t || !t.id || !t.title) return;
          out.push({ ...base, id: String(t.id), title: String(t.title), detail: t.detail ? String(t.detail) : '',
            source: t.source ? String(t.source) : source, minutes: Number(t.minutes) > 0 ? Number(t.minutes) : null,
            slot: SLOTS.some(x => x[0] === t.slot) ? t.slot : base.slot, href: safeHref(t.href) || base.href });
        });
        return;
      }
      const n = Number(s.value);
      if (s.label && Number.isFinite(n) && n > 0) {
        out.push({ ...base, id: `${p.id}:summary`, title: `${s.label} ${fmt.num(n)}건`, detail: `${p.label}에서 확인하세요.` });
      }
    });
    if (T.perf) out.unshift(T.perf);
    return out;
  }
  function todayState() {
    const s = store.get('today', null);
    return s && s.date === REBOOT.today() ? s : { date: REBOOT.today(), done: [], items: {} };
  }
  function todayTasks() {
    const st = todayState();
    const live = partTodos();
    const ids = new Set(live.map(t => t.id));
    Object.values(st.items).forEach(t => {
      if (ids.has(t.id)) return;
      // summary()가 있는 파트에서 사라짐 = 처리됨. 성과처럼 summary()가 없는 줄은 저장된 그대로
      live.push(hasSummary(t.part) ? { ...t, gone: true } : t);
    });
    const order = t => SLOTS.findIndex(s => s[0] === t.slot);
    return live.map(t => ({ ...t, done: !!t.gone || st.done.includes(t.id) })).sort((a, b) => order(a) - order(b));
  }
  function saveToday(tasks, done) {
    const st = todayState();
    const items = {};
    tasks.forEach(t => {
      items[t.id] = { id: t.id, title: t.title, detail: t.detail || '', part: t.part, source: t.source, tone: t.tone,
        slot: t.slot, href: t.href, minutes: t.minutes || null };
    });
    const next = { date: st.date, done: done || st.done.filter(id => items[id]), items };
    if (JSON.stringify(next) !== JSON.stringify(st)) store.set('today', next);
  }
  async function loadPerf() {
    if (T.perfLoaded) return;
    T.perfLoaded = true;
    const w = await weekSummary();
    if (!w || w.empty) return;
    const ch = w.change && w.change.revenue != null ? ` (${fmt.signed(w.change.revenue, 1, '%')})` : '';
    T.perf = { id: 'performance:week', part: 'performance', source: '성과', tone: '', slot: 'morning', href: '/dashboard/performance',
      title: `최근 7일 성과 확인 · 매출 ${fmt.num(w.revenue)}원${ch}`,
      detail: `${md(w.start)}—${md(w.end)}${w.compare ? ` · ${w.compare.label}` : ''}${w.sample ? ' · 샘플 데이터' : ''}` };
  }

  function todayRow(t) {
    const on = t.id === T.sel;
    return `<div class="home-today-row${t.done ? ' is-done' : ''}${on ? ' is-sel' : ''}" data-tid="${esc(t.id)}">
      <input type="checkbox" data-act="today-toggle"${t.done ? ' checked' : ''}${t.gone ? ' disabled title="파트에서 처리됨"' : ''} aria-label="완료 표시: ${esc(t.title)}">
      <button type="button" class="home-today-pick" data-act="today-pick" aria-pressed="${on}">${esc(t.title)}</button>
      ${badge(t.source, t.tone)}
      <span class="home-today-min">${t.done ? '완료' : t.minutes ? `약 ${fmt.num(t.minutes)}분` : ''}</span></div>`;
  }
  function todayPanel(t, tasks) {
    if (!t) return `<div class="home-today-panel">${ui.empty('오늘 할 일이 없어요. 각 파트에 새 일이 생기면 여기에 모여요.', 'check')}</div>`;
    const open = tasks.filter(x => !x.done);
    const idx = open.findIndex(x => x.id === t.id);
    const slot = (SLOTS.find(s => s[0] === t.slot) || SLOTS[1])[1];
    const next = open.find(x => x.id !== t.id);
    return `<div class="home-today-panel" aria-live="polite">
      <p class="home-today-kicker">${esc(t.source)}${idx >= 0 ? ` · ${idx + 1}/${open.length}` : ''}</p>
      <h2>${esc(t.title)}</h2>
      <p class="panel-note">${esc(slot)}${t.minutes ? ` · 약 ${fmt.num(t.minutes)}분` : ''}</p>
      ${t.detail ? `<p class="home-today-detail">${esc(t.detail)}</p>` : ''}
      <div class="home-today-actions">
        <a class="btn lime" href="${esc(t.href || partUrl(t.part))}">${esc(partLabel(t.part))} 열기 ${icon('arrow')}</a>
        ${t.gone ? '' : `<button type="button" class="btn" data-act="today-mark" data-tid="${esc(t.id)}">${t.done ? '완료 취소' : '완료로 표시'}</button>`}
        ${next ? `<button type="button" class="btn ghost" data-act="today-pick" data-tid="${esc(next.id)}">다음 건 ${icon('arrow')}</button>` : ''}
      </div>
      <p class="panel-note home-today-hint">긴 작업은 파트 화면에서 하고, 처리가 끝나면 이 목록에 완료로 남아요.</p></div>`;
  }
  function paintToday() {
    const box = ref('today');
    if (!box) return;
    const tasks = todayTasks();
    saveToday(tasks);
    const done = tasks.filter(t => t.done).length;
    if (!tasks.some(t => t.id === T.sel)) T.sel = (tasks.find(t => !t.done) || tasks[0] || {}).id || '';
    if (S.view === 'today') REBOOT.page.title(`오늘 할 일 ${tasks.length}개 · ${done}개 완료`);
    const pct = tasks.length ? Math.round(done / tasks.length * 100) : 0;
    const groups = SLOTS.map(([id, label]) => {
      const list = tasks.filter(t => t.slot === id).sort((a, b) => a.done - b.done);
      return list.length ? `<section class="home-today-slot"><h3>${esc(label)}</h3>${list.map(todayRow).join('')}</section>` : '';
    }).join('');
    box.innerHTML = `<div class="home-today-list">
        <div class="home-today-progress" role="progressbar" aria-label="오늘 할 일 진행" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
        ${groups || ui.empty('오늘 모인 할 일이 없어요.', 'check')}
      </div>
      ${todayPanel(tasks.find(t => t.id === T.sel), tasks)}`;
    if (typeof REBOOT.refreshCounts === 'function') REBOOT.refreshCounts();
  }
  function markToday(id, done) {
    const tasks = todayTasks();
    const st = todayState();
    const set = new Set(st.done);
    if (done) set.add(id); else set.delete(id);
    saveToday(tasks, [...set]);
    if (done && id === T.sel) { const next = tasks.find(t => !t.done && t.id !== id); if (next) T.sel = next.id; }
    paintToday();
  }
  function seasonPill() {
    const s = upcomingSeasons()[0];
    if (!s) return '';
    return `<span class="home-today-season">${esc(s.name)} <b>${s.ongoing ? 'NOW' : esc(fmt.dday(s.start))}</b>${s.inLead && !s.ongoing ? ' · 준비 기간' : ''}</span>`;
  }
  async function showToday() {
    paintToday();
    if (!T.perfLoaded) { await loadPerf(); paintToday(); }
  }

  /* 홈의 하위 화면: 오늘 할 일 · 데일리 브리핑 · 콘텐츠 캘린더
     탭 줄(role="tablist")은 core.js가 왼쪽 메뉴로 옮겨 보여 줍니다. 주소 #today · #briefing · #calendar로 바로 열 수 있음 */
  const VIEWS = [['today', '오늘 할 일', 'check'], ['briefing', '데일리 브리핑', 'chart'], ['calendar', '콘텐츠 캘린더', 'calendar']];
  const viewFromHash = () => { const h = (global.location.hash || '').replace('#', ''); return VIEWS.some(v => v[0] === h) ? h : null; };
  const koDate = iso => `${iso.slice(0, 4)}년 ${+iso.slice(5, 7)}월 ${+iso.slice(8, 10)}일 ${WEEKDAY[dow(iso)]}요일`;
  function userName() { const u = REBOOT.user(); return u.name || String(u.email || '').split('@')[0] || ''; }

  function paintHeading(id) {
    const page = REBOOT.page;
    const actions = page.actions();
    const ws = weekStartOf(REBOOT.today());
    page.heading(true);
    if (id === 'today') {
      page.title('오늘 할 일');
      page.subtitle(`${koDate(REBOOT.today())} · ${userName()}님의 하루 순서`);
      if (actions) actions.innerHTML = seasonPill();
    } else if (id === 'briefing') {
      page.title('오늘의 흐름을 읽고, 다음을 준비해요.');
      page.subtitle(`${koDate(REBOOT.today())} · ${userName()}님을 위한 데일리 브리핑`);
      if (actions) actions.innerHTML = `<span class="btn" role="note" aria-label="이번 주">${icon('calendar')} ${esc(ws.replace(/-/g, '. '))} — ${esc(md(addDays(ws, 6)).replace('.', '. '))}</span>
        <button type="button" class="btn" data-go-view="calendar">${icon('calendar')} 콘텐츠 캘린더</button>`;
    } else if (id === 'calendar') {
      page.title('콘텐츠의 타이밍을, 한눈에.');
      page.subtitle('미국 시즌과 준비 기간을 함께 보며 다음 게시물을 준비하세요.');
      if (actions) actions.innerHTML = `<a class="btn" href="/dashboard/settings#collect">${icon('edit')} 시즌 편집</a>
        <button type="button" class="btn lime" data-go-view="briefing">${icon('chart')} 데일리 브리핑</button>`;
    } else if (actions) actions.innerHTML = '';
  }
  function paintBriefing() {
    paintHero();
    paintSeasonCards();
    paintActivity();
    loadNews();
    watchTheme();
    const noop = () => {};
    paintKpis().catch(noop).then(() => paintChart()).catch(noop).then(loadTodos);
  }
  function showView(id, opts) {
    S.view = id;
    S.root.querySelectorAll('[data-view]').forEach(b => {
      const on = b.dataset.view === id;
      b.setAttribute('aria-selected', String(on));
      b.classList.toggle('active', on);
      b.tabIndex = on ? 0 : -1;
    });
    S.root.querySelectorAll('[data-view-panel]').forEach(p => { p.hidden = p.dataset.viewPanel !== id; });
    paintHeading(id);
    if (id === 'today') showToday();
    if (id === 'briefing' && !S.briefing) { S.briefing = true; paintBriefing(); }
    if (id === 'calendar') { paintCalendar(); paintSurge(); }
    if (!opts || opts.hash !== false) {
      const h = `#${id}`;
      if ((global.location.hash || '') !== h && global.history && global.history.replaceState) {
        global.history.replaceState(null, '', global.location.pathname + global.location.search + h);
      }
    }
  }

  function render(root) {
    S.root = root;
    root.classList.add('part-home');
    root.innerHTML = `
      <div class="subtabs" role="tablist" aria-label="홈 화면">${VIEWS.map(([id, label, ic]) =>
        `<button type="button" role="tab" data-view="${id}" data-icon="${ic}" aria-selected="false">${esc(label)}</button>`).join('')}</div>

      <div data-view-panel="today" role="tabpanel" hidden><div class="home-today" data-ref="today"></div></div>

      <div data-view-panel="briefing" role="tabpanel" hidden>
        <section class="brief-hero" data-go-view="calendar" title="콘텐츠 캘린더 보기">
          <div><span class="eyebrow">YOUR DAILY START</span><h2 data-ref="hero-title">—</h2>
            <p>시즌의 타이밍과 업계의 이야기를 연결해 이번 주 우선순위를 정해보세요.</p>
            <div class="flex wrap" style="margin-top:19px"><a class="btn lime" href="/dashboard/content">${icon('spark')} 캠페인 준비하기</a>
              <a class="btn small" href="/dashboard/insights">시장 인사이트 보기</a></div></div>
          <div class="hero-stat">
            <div><strong data-ref="hero-news">—</strong><small>뉴스 시그널</small></div>
            <div><strong data-ref="hero-todo">—</strong><small>이번 주 할 일</small></div>
            <div><strong data-ref="hero-season">—</strong><small>다음 주요 시즌</small></div>
          </div>
        </section>
        <div class="section-title"><h2>다가오는 미국 시즌</h2>${badge('시즌 캘린더 · 설정에서 편집')}</div>
        <div class="three-col" data-ref="season-cards"></div>
        <div class="kpis section-gap" data-ref="kpis" aria-label="이번 주 요약">${'<div class="kpi"><div class="skeleton" style="height:100%"></div></div>'.repeat(4)}</div>
        <div class="home-layout">
          <div class="stack">
            <section class="card" data-ref="chart-card">
              <div class="card-head"><h2>이번 주 성과 <span data-ref="chart-tag"></span></h2>
                <div class="flex wrap">
                  <div class="period" role="group" aria-label="기간">
                    <button type="button" class="selected" data-range="cur" aria-pressed="true">최근 7일</button>
                    <button type="button" data-range="prev" aria-pressed="false">직전 7일</button>
                  </div>
                  <a class="link" href="/dashboard/performance">자세히 보기 ${icon('arrow')}</a>
                </div></div>
              <div data-ref="chart-body">
                <div class="between home-chart-top">
                  <div class="home-chart-summary"><strong data-ref="chart-value">—</strong><span data-ref="chart-change"></span><small data-ref="chart-range"></small></div>
                  <div class="home-legend" aria-hidden="true"><span class="rev">매출</span><span class="spend">광고비</span></div>
                </div>
                <div class="home-chart"><canvas data-ref="chart" role="img" aria-label="최근 7일 일별 매출(실선)과 광고비(점선) — 자세한 숫자는 성과분석"></canvas></div>
              </div>
            </section>
            <section class="card home-brief" aria-label="미국 시장 브리핑">
              <div class="home-brief-head"><div class="home-brief-title"><h2>미국 시장 브리핑</h2><span class="home-brief-sub" data-ref="news-sub"></span></div>
                <button type="button" class="icon-btn home-brief-refresh" data-ref="news-collect" data-act="news-collect" aria-label="지금 받기" title="지금 받기">${icon('refresh')}</button></div>
              <div class="home-brief-tabs" data-ref="news-tabs"></div>
              <div data-ref="news-body"><div class="skeleton" style="height:120px"></div></div>
              <div data-ref="news-errors"></div>
              <div class="home-brief-foot"><span class="home-brief-saved" data-ref="news-saved"></span><span class="flex home-news-badges" data-ref="news-badges"></span>
                <details class="home-brief-info"><summary>수집 정보 · 검색어 점검</summary>
                  <p class="panel-note" data-ref="news-meta"></p><p class="panel-note" data-ref="news-note"></p><div data-ref="news-stats"></div></details></div>
            </section>
          </div>
          <aside class="stack">
            <section class="card">
              <div class="card-head home-todo-head"><h2>이번 주 To-Do <span class="muted small" data-ref="todo-count"></span></h2>
                <div class="flex"><button type="button" class="btn small ghost" data-act="todo-regen" title="완료 안 한 자동 항목을 새로 만들기">${icon('refresh')} 다시 만들기</button>
                <button type="button" class="icon-btn" data-act="todo-add" aria-label="할 일 추가">${icon('plus')}</button></div></div>
              <p class="panel-note home-todo-meta" data-ref="todo-meta"></p>
              <form class="home-todo-form" data-ref="todo-form" hidden>
                <input class="input" name="title" maxlength="200" placeholder="할 일 (예: 핼러윈 릴스 2편 게시)" aria-label="할 일">
                <input class="input" name="reason" maxlength="500" placeholder="근거 (선택)" aria-label="근거">
                <div class="flex"><button type="submit" class="btn small lime">추가</button><button type="button" class="btn small ghost" data-act="todo-cancel">취소</button></div>
              </form>
              <div data-ref="todos"><div class="skeleton" style="height:120px"></div></div>
            </section>
            <section class="card"><h3 class="home-side-title">더 깊은 시장 정보가 필요하다면</h3>
              <p class="panel-note" style="margin:10px 0 16px">진열대 · 구매자 리뷰 · 잠재 고객은 시장·고객 인사이트에서 확인하세요.</p>
              <a class="btn small" href="/dashboard/insights">시장·고객 인사이트 ${icon('arrow')}</a></section>
            <section class="card"><div class="card-head"><h2>최근 워크스페이스 활동</h2></div><ul class="activity" data-ref="activity"><li>불러오는 중…</li></ul></section>
          </aside>
        </div>
      </div>

      <div data-view-panel="calendar" role="tabpanel" hidden><div data-ref="calendar"></div><div data-ref="surge"></div></div>`;

    root.addEventListener('click', e => {
      const v = e.target.closest('a') ? null : e.target.closest('[data-view], [data-go-view]');
      if (v) {
        showView(v.dataset.view || v.dataset.goView);
        if (v.dataset.goView && global.scrollTo) global.scrollTo({ top: 0, behavior: 'smooth' });
        return;
      }
      const r = e.target.closest('[data-range]');
      if (r && !r.disabled) {
        S.range = r.dataset.range;
        root.querySelectorAll('[data-range]').forEach(b => { b.classList.toggle('selected', b === r); b.setAttribute('aria-pressed', String(b === r)); });
        paintChart();
        return;
      }
      const card = e.target.closest('.home-news-card');
      if (card && !e.target.closest('button')) {
        markRead(card.dataset.href);
        if (!e.target.closest('a')) global.open(card.dataset.href, '_blank', 'noopener');
        paintNews();
        return;
      }
      const b = e.target.closest('[data-act]');
      if (!b) return;
      const act = b.dataset.act;
      const row = b.closest('[data-id]');
      if (act === 'today-toggle') markToday(b.closest('[data-tid]').dataset.tid, b.checked);
      else if (act === 'today-pick') { T.sel = b.dataset.tid || b.closest('[data-tid]').dataset.tid; paintToday(); }
      else if (act === 'today-mark') { const t = todayTasks().find(x => x.id === b.dataset.tid); if (t) markToday(t.id, !t.done); }
      else if (act === 'todo-toggle') toggleTodo(row.dataset.id, b.checked, b);
      else if (act === 'todo-del') deleteTodo(row.dataset.id);
      else if (act === 'todo-regen') regenerate(b);
      else if (act === 'todo-add') { const f = ref('todo-form'); f.hidden = !f.hidden; if (!f.hidden) f.querySelector('[name="title"]').focus(); }
      else if (act === 'todo-cancel') { ref('todo-form').hidden = true; }
      else if (act === 'news-collect') collectNews(b);
      else if (act === 'news-save') toggleSave(b);
      else if (act === 'news-kind') { store.set('newsKind', b.dataset.kind); paintNews(); }
      else if (act === 'news-more') { const k = b.dataset.key || 'news'; S.newsOpen[k] = !S.newsOpen[k]; paintNews(); }
      else if (act === 'cal-prev') moveMonth(-1);
      else if (act === 'cal-next') moveMonth(1);
      else if (act === 'cal-today') { S.calMonth = ''; paintCalendar(); }
    });
    const actions = REBOOT.page.actions();
    if (actions) actions.addEventListener('click', e => {
      const v = e.target.closest('[data-go-view]');
      if (v) showView(v.dataset.goView);
    });
    ref('todo-form').addEventListener('submit', e => { e.preventDefault(); addTodo(e.currentTarget); });
    global.addEventListener('hashchange', () => { const v = viewFromHash() || 'today'; if (v !== S.view) showView(v, { hash: false }); });
    showView(viewFromHash() || 'today', { hash: false });
  }

  /* ───────── 이번 주 To-Do 버튼 (모든 화면 오른쪽 아래) ─────────
     버튼 → 오른쪽 사이드 패널: 할 일 확인 · 체크만 (추가 · 삭제 · 다시 만들기는 데일리 브리핑에서)
     홈 화면이 열려 있으면 브리핑의 To-Do 카드와 같은 목록을 함께 고침 */
  const dock = (() => {
    const D = { el: null, todos: null, open: false, loading: false };
    const q = name => D.el && D.el.querySelector(`[data-dock="${name}"]`);

    function itemHtml(t) {
      return `<label class="home-dock-item${t.done ? ' done' : ''}" data-id="${esc(t.id)}">
        <input type="checkbox" data-dock-toggle${t.done ? ' checked' : ''} aria-label="완료 표시: ${esc(t.title)}">
        <span class="grow"><span class="home-dock-title">${esc(t.title)}</span><span class="home-dock-reason">근거: ${esc(t.reason || '—')}</span></span></label>`;
    }
    function paint() {
      if (!D.el) return;
      const t = D.todos;
      const open = t ? t.items.filter(i => !i.done).length : 0;
      const count = q('count');
      count.textContent = open > 99 ? '99+' : String(open);
      count.hidden = !t || open <= 0;
      q('left').textContent = t && t.items.length ? `${open}개 남음` : '';
      q('meta').textContent = t ? `${md(t.week.start)} — ${md(t.week.end)}` : '';
      const list = q('list');
      if (!t) { list.innerHTML = D.loading ? '<div class="skeleton" style="height:120px"></div>' : ''; return; }
      list.innerHTML = !t.generated
        ? ui.empty('이번 주 할 일이 아직 없어요. 데일리 브리핑을 열면 자동으로 만들어져요.', 'check')
        : t.items.length ? t.items.map(itemHtml).join('') : ui.empty('이번 주 할 일이 없어요.', 'check');
    }
    async function load() {
      D.loading = true;
      paint();
      try { D.todos = await REBOOT.api('/api/home/todos'); }
      catch (e) { const list = q('list'); if (list) list.innerHTML = `<p class="note error">할 일을 불러오지 못했습니다: ${esc(e.message)}</p>`; return; }
      finally { D.loading = false; }
      paint();
    }
    function setOpen(on) {
      D.open = on;
      q('panel').hidden = !on;
      q('scrim').hidden = !on;
      q('fab').setAttribute('aria-expanded', String(on));
      if (on) { load(); q('close').focus(); } else q('fab').focus();
    }
    async function toggle(box) {
      const id = box.closest('[data-id]').dataset.id;
      const done = box.checked;
      try {
        const u = await REBOOT.api(`/api/home/todos/${encodeURIComponent(id)}`, { done }, { method: 'PATCH' });
        [D.todos, S.todos].forEach(t => {
          const it = t && t.items.find(i => String(i.id) === String(id));
          if (it) { Object.assign(it, u); t.items.sort((a, b) => (a.done - b.done)); }
        });
        if (S.root && S.todos) paintTodos(); else paint();
      } catch (e) { box.checked = !done; toast(e.message, 'error'); }
    }
    function mount() {
      if (D.el || !doc.body || !REBOOT.user || !REBOOT.user()) return;
      D.el = doc.createElement('div');
      D.el.className = 'home-dock';
      D.el.innerHTML = `
        <button type="button" class="home-fab" data-dock="fab" aria-expanded="false" aria-controls="home-dock-panel" aria-label="이번 주 To-Do 열기">
          ${icon('check')}<span class="home-fab-count" data-dock="count" hidden></span></button>
        <div class="home-dock-scrim" data-dock="scrim" hidden></div>
        <aside class="home-dock-panel" id="home-dock-panel" data-dock="panel" role="dialog" aria-modal="true" aria-labelledby="home-dock-title" hidden>
          <div class="home-dock-head"><h2 id="home-dock-title">이번 주 To-Do <span class="muted small" data-dock="left"></span></h2>
            <button type="button" class="home-dock-close" data-dock="close" aria-label="닫기">${icon('x')}</button></div>
          <p class="panel-note" data-dock="meta"></p>
          <div class="home-dock-list" data-dock="list"></div>
          <a class="btn small home-dock-go" href="/dashboard#briefing">${icon('chart')} 데일리 브리핑에서 보기 · 추가</a>
        </aside>`;
      doc.body.appendChild(D.el);
      D.el.addEventListener('click', e => {
        if (e.target.closest('[data-dock="fab"]')) setOpen(!D.open);
        else if (e.target.closest('[data-dock="close"], [data-dock="scrim"], .home-dock-go')) setOpen(false);
      });
      D.el.addEventListener('change', e => { if (e.target.matches('[data-dock-toggle]')) toggle(e.target); });
      doc.addEventListener('keydown', e => { if (e.key === 'Escape' && D.open) setOpen(false); });
      const b = store.get('todoBadge', null);
      if (b && b.week === weekStartOf(REBOOT.today())) { q('count').textContent = b.open > 99 ? '99+' : String(b.open); q('count').hidden = b.open <= 0; }
    }
    if (doc) {
      if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', mount);
      else mount();
    }
    return { sync(t) { D.todos = t; paint(); } };
  })();

  REBOOT.register({
    id: 'home', label: '홈 · 데일리 브리핑', order: 10, render,
    // 상단 ① 오늘 숫자 · 종 알림 = 오늘 남은 일 (이번 주 To-Do 숫자는 오른쪽 아래 버튼에)
    summary: () => ({ label: '오늘 남은 일', value: todayTasks().filter(t => !t.done).length })
  });
  REBOOT.expose('home', { seasons: () => REBOOT.data.seasons() });
})(typeof window !== 'undefined' ? window : globalThis);
