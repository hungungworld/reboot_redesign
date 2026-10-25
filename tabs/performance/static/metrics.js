/* 성과분석 공용 계산 (README 2·4장) — 서버 tabs/performance/metrics.py와 같은 규칙입니다.
   화면(performance.js) · 보고서 · node 테스트(tabs/performance/tests/perf_metrics.test.js)가 함께 씁니다. DOM은 쓰지 않습니다.
   - 날짜는 'YYYY-MM-DD' 문자열로만 다룸 (UTC로 계산 → 시간대 영향 없음)
   - 비교 기간 = 고른 기간 바로 앞의 같은 길이. CSV 시작일보다 앞이면 비교하지 않음(null → "데이터 없음")
   - 합계의 ROAS·CTR·CVR·CPA는 채널 평균이 아니라 합계로 다시 계산 */
(function (global) {
  'use strict';

  const HEADER = ['날짜', '채널', '광고비', '노출수', '클릭수', '전환수', '매출'];
  const FIELDS = ['date', 'channel', 'spend', 'impressions', 'clicks', 'conversions', 'revenue'];
  const SUM_KEYS = ['spend', 'impressions', 'clicks', 'conversions', 'revenue'];
  const DAY = 86400000;
  const LOWER_BETTER = { cpa: true };   // CPA는 낮을수록 좋음
  const NEUTRAL = { spend: true };      // 광고비 증감은 좋고 나쁨이 없음

  /* ───────── 날짜 ───────── */
  const toTime = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  const fromTime = t => new Date(t).toISOString().slice(0, 10);
  const addDays = (iso, n) => fromTime(toTime(iso) + n * DAY);
  const daysBetween = (a, b) => Math.round((toTime(b) - toTime(a)) / DAY) + 1;   // 양 끝 포함
  function isIso(s) {
    s = String(s || '');
    return /^\d{4}-\d{2}-\d{2}$/.test(s) && fromTime(toTime(s)) === s;
  }
  function weekday(iso) { return new Date(toTime(iso)).getUTCDay(); }   // 0=일

  function dates(start, end) {
    const out = [];
    for (let d = start; d <= end; d = addDays(d, 1)) out.push(d);
    return out;
  }

  /* ───────── CSV (업로드 전 미리보기 · 테스트) ───────── */
  function parseDate(v) {
    const m = String(v == null ? '' : v).trim().match(/^(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*[.일]?$/);
    if (!m) return null;
    const iso = `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`;
    return isIso(iso) ? iso : null;
  }

  /** '1,260,000' · '1260000원' · '₩300000' → 숫자. 빈칸은 0, 읽을 수 없거나 음수면 NaN */
  function parseNumber(v) {
    const s = String(v == null ? '' : v).replace(/[,\s\u00a0원₩￦]|krw/gi, '');
    if (s === '' || s === '-') return 0;
    if (!/^\d+(\.\d+)?$/.test(s)) return NaN;
    return Math.round(parseFloat(s));
  }

  function splitCsv(text) {
    const rows = [];
    let row = [], cell = '', quoted = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (quoted) {
        if (ch === '"') {
          if (text[i + 1] === '"') { cell += '"'; i++; } else quoted = false;
        } else cell += ch;
      } else if (ch === '"') quoted = true;
      else if (ch === ',') { row.push(cell); cell = ''; }
      else if (ch === '\n' || ch === '\r') {
        if (ch === '\r' && text[i + 1] === '\n') i++;
        row.push(cell); rows.push(row); row = []; cell = '';
      } else cell += ch;
    }
    if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }

  function sortRows(rows) {
    return rows.slice().sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.channel.toLowerCase() < b.channel.toLowerCase() ? -1 : a.channel.toLowerCase() > b.channel.toLowerCase() ? 1 : 0));
  }

  /** CSV 글 → { ok, error, errors, rows } — 서버가 최종 확인하고, 화면은 미리보기에만 씀 */
  function parseCsv(text) {
    const lines = splitCsv(String(text || '').replace(/^\ufeff/, ''));
    const head = (lines[0] || []).map(c => c.trim().replace(/^\ufeff/, ''));
    while (head.length && head[head.length - 1] === '') head.pop();
    if (head.join(',') !== HEADER.join(',')) {
      return { ok: false, error: `첫 줄은 "${HEADER.join(',')}" 이어야 합니다.`, errors: [], rows: [] };
    }
    const found = new Map();
    const errors = [];
    lines.slice(1).forEach((cells, i) => {
      const line = i + 2;
      if (!cells.some(c => String(c).trim())) return;
      if (cells.length < HEADER.length) { errors.push(`${line}번째 줄: 칸이 ${cells.length}개입니다 (7개 필요).`); return; }
      if (cells.slice(HEADER.length).some(c => String(c).trim())) { errors.push(`${line}번째 줄: 칸이 7개보다 많습니다.`); return; }
      const date = parseDate(cells[0]);
      const channel = String(cells[1]).trim();
      if (!date) { errors.push(`${line}번째 줄: 날짜를 읽지 못했습니다.`); return; }
      if (!channel) { errors.push(`${line}번째 줄: 채널 이름이 비어 있습니다.`); return; }
      const row = { date, channel: channel.slice(0, 40) };
      for (let k = 2; k < FIELDS.length; k++) {
        const n = parseNumber(cells[k]);
        if (isNaN(n)) { errors.push(`${line}번째 줄: ${HEADER[k]} 값이 숫자가 아닙니다.`); return; }
        row[FIELDS[k]] = n;
      }
      found.set(date + '\u0000' + row.channel, row);
    });
    if (!errors.length && !found.size) errors.push('데이터 행이 없습니다.');
    return { ok: !errors.length, error: errors[0] || '', errors, rows: sortRows(Array.from(found.values())) };
  }

  /* ───────── 지표 ───────── */
  function derive(t) {
    const o = Object.assign({}, t);
    o.roas = t.spend ? t.revenue / t.spend * 100 : null;
    o.ctr = t.impressions ? t.clicks / t.impressions * 100 : null;
    o.cvr = t.clicks ? t.conversions / t.clicks * 100 : null;
    o.cpa = t.conversions ? t.spend / t.conversions : null;
    o.profit = t.revenue - t.spend;
    return o;
  }

  function totals(rows) {
    const t = { spend: 0, impressions: 0, clicks: 0, conversions: 0, revenue: 0 };
    rows.forEach(r => SUM_KEYS.forEach(k => { t[k] += Number(r[k]) || 0; }));
    return derive(t);
  }

  const inPeriod = (rows, start, end) => rows.filter(r => r.date >= start && r.date <= end);

  function range(rows) {
    if (!rows || !rows.length) return null;
    let start = rows[0].date, end = rows[0].date;
    rows.forEach(r => { if (r.date < start) start = r.date; if (r.date > end) end = r.date; });
    return { start, end };
  }

  /** 채널 순서: 처음 나온 순서 (기간이 바뀌어도 색이 채널을 따라가도록) */
  function channels(rows) {
    const seen = [];
    rows.forEach(r => { if (!seen.includes(r.channel)) seen.push(r.channel); });
    return seen;
  }

  /** CSV 마지막 날짜 기준 최근 7일 */
  function latestWeek(rng) {
    if (!rng) return null;
    const start = addDays(rng.end, -6);
    return { start: start < rng.start ? rng.start : start, end: rng.end };
  }

  /** 고른 기간을 CSV 범위 안으로 (앞뒤가 바뀌면 바로잡음) */
  function clampPeriod(start, end, rng) {
    if (!rng) return null;
    let s = isIso(start) ? start : rng.start;
    let e = isIso(end) ? end : rng.end;
    if (s > e) { const t = s; s = e; e = t; }
    if (s < rng.start) s = rng.start;
    if (e > rng.end) e = rng.end;
    if (s > e) return latestWeek(rng);
    return { start: s, end: e };
  }

  function comparePeriod(start, end, dataStart) {
    const n = daysBetween(start, end);
    const pEnd = addDays(start, -1);
    const pStart = addDays(start, -n);
    if (!dataStart || pStart < dataStart) return null;
    return { start: pStart, end: pEnd, days: n, label: n === 7 ? '전주 대비' : '직전 기간 대비' };
  }

  /** 증감률(%) = (이번 − 비교) ÷ 비교 × 100. 비교값이 없거나 0이면 null */
  function change(cur, prev) {
    if (cur == null || prev == null || !isFinite(cur) || !isFinite(prev) || prev === 0) return null;
    return (cur - prev) / Math.abs(prev) * 100;
  }

  /** 증감의 좋고 나쁨: good · bad · neutral(광고비·0%) · none(비교 없음) */
  function tone(metric, delta) {
    if (delta == null || !isFinite(delta)) return 'none';
    if (NEUTRAL[metric] || Math.abs(delta) < 0.05) return 'neutral';
    const up = delta > 0;
    return (LOWER_BETTER[metric] ? !up : up) ? 'good' : 'bad';
  }

  function byChannel(rows, order) {
    const groups = new Map();
    rows.forEach(r => { if (!groups.has(r.channel)) groups.set(r.channel, []); groups.get(r.channel).push(r); });
    const names = (order || []).filter(c => groups.has(c)).concat(Array.from(groups.keys()).filter(c => !(order || []).includes(c)));
    return names.map(c => Object.assign({ channel: c }, totals(groups.get(c))));
  }

  /** 날짜별 합계 (데이터 없는 날은 0). channel을 주면 그 채널만 */
  function daily(rows, start, end, channel) {
    const acc = {};
    rows.forEach(r => {
      if (r.date < start || r.date > end || (channel && r.channel !== channel)) return;
      const d = acc[r.date] || (acc[r.date] = { spend: 0, impressions: 0, clicks: 0, conversions: 0, revenue: 0 });
      SUM_KEYS.forEach(k => { d[k] += Number(r[k]) || 0; });
    });
    return dates(start, end).map(d => Object.assign({ date: d }, acc[d] || { spend: 0, impressions: 0, clicks: 0, conversions: 0, revenue: 0 }));
  }

  /** 기간 요약: 합계 · 비교 · 증감 · 채널별(비교 포함) · ROAS 최고/최저 */
  function summarize(rows, start, end, opts) {
    opts = opts || {};
    const rng = opts.range || range(rows);
    const cur = inPeriod(rows, start, end);
    const t = totals(cur);
    const cmp = rng ? comparePeriod(start, end, rng.start) : null;
    const prevRows = cmp ? inPeriod(rows, cmp.start, cmp.end) : [];
    const p = cmp ? totals(prevRows) : null;
    const keys = ['spend', 'revenue', 'roas', 'conversions', 'cpa', 'profit', 'ctr', 'cvr', 'impressions', 'clicks'];
    const chg = {};
    keys.forEach(k => { chg[k] = p ? change(t[k], p[k]) : null; });
    const prevBy = {};
    if (cmp) byChannel(prevRows).forEach(c => { prevBy[c.channel] = c; });
    const chans = byChannel(cur, opts.order || channels(rows)).map(c => {
      const pc = prevBy[c.channel] || null;
      const cc = {};
      keys.forEach(k => { cc[k] = pc ? change(c[k], pc[k]) : null; });
      return Object.assign(c, { prev: pc, change: cc, share: t.revenue ? c.revenue / t.revenue * 100 : null });
    });
    const ranked = chans.filter(c => c.roas != null).sort((a, b) => b.roas - a.roas);
    return {
      start, end, days: daysBetween(start, end), rows: cur.length, range: rng,
      totals: t, compare: cmp, prevTotals: p, change: chg, channels: chans,
      best: ranked[0] || null, worst: ranked.length > 1 ? ranked[ranked.length - 1] : null
    };
  }

  /* ───────── 표시 · 숫자 기준 해석 ───────── */
  const nf = (d) => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d });
  const f = {
    krw: n => (n == null || !isFinite(n) ? '—' : nf(0).format(Math.round(n)) + '원'),
    pct: (n, d) => (n == null || !isFinite(n) ? '—' : nf(d == null ? 1 : d).format(n) + '%'),
    signed: (n, d) => (n == null || !isFinite(n) ? '—' : (n > 0 ? '+' : '') + nf(d == null ? 1 : d).format(n) + '%')
  };

  function fmtPeriod(start, end) {
    if (!start || !end) return '—';
    const a = start.replace(/-/g, '.');
    const b = end.slice(0, 4) === start.slice(0, 4) ? end.slice(5).replace(/-/g, '.') : end.replace(/-/g, '.');
    return `${a} — ${b}`;
  }

  /** 월~일 주 목록 (CSV 범위 안에 7일이 다 있는 주, 최근 주부터) — 보고서 목록용 */
  function weeks(rng) {
    if (!rng) return [];
    const out = [];
    let mon = rng.start;
    while (weekday(mon) !== 1) mon = addDays(mon, 1);
    for (; addDays(mon, 6) <= rng.end; mon = addDays(mon, 7)) out.push({ start: mon, end: addDays(mon, 6) });
    return out.reverse();
  }

  /** 숫자 기준 문구 (AI 응답 전 · 키가 없을 때). 주어진 숫자만 씀 */
  function insights(sum) {
    const best = sum && sum.best;
    if (!best) return { good: '이 기간에는 광고비가 있는 채널이 없어 비교할 수 없습니다.', improve: '' };
    const t = sum.totals;
    const label = sum.compare ? sum.compare.label : '';
    const good = [`${best.channel}의 ROAS가 ${f.pct(best.roas, 0)}로 가장 높습니다 (광고비 ${f.krw(best.spend)} → 매출 ${f.krw(best.revenue)}).`];
    if (best.change && best.change.roas != null) good.push(`${label} ROAS ${f.signed(best.change.roas)}.`);
    if (best.cvr != null && t.cvr != null && best.cvr > t.cvr) good.push(`전환율(CVR) ${f.pct(best.cvr, 2)}가 전체 ${f.pct(t.cvr, 2)}보다 높아 클릭이 구매로 잘 이어지고 있습니다.`);
    else if (best.ctr != null && t.ctr != null && best.ctr > t.ctr) good.push(`클릭률(CTR) ${f.pct(best.ctr, 2)}가 전체 ${f.pct(t.ctr, 2)}보다 높아 소재 반응이 좋습니다.`);
    good.push('효율이 유지되는지 보면서 예산을 조금씩 늘려 볼 수 있습니다.');

    const worst = sum.worst;
    if (!worst) return { good: good.join(' '), improve: '비교할 다른 채널이 없습니다.' };
    const improve = [`${worst.channel}의 ROAS가 ${f.pct(worst.roas, 0)}로 가장 낮습니다.`];
    const ctrGap = worst.ctr != null && t.ctr ? worst.ctr / t.ctr : null;
    const cvrGap = worst.cvr != null && t.cvr ? worst.cvr / t.cvr : null;
    if (cvrGap != null && cvrGap < 1 && (ctrGap == null || cvrGap <= ctrGap)) {
      improve.push(`클릭 → 전환 단계가 약합니다: CVR ${f.pct(worst.cvr, 2)} (전체 ${f.pct(t.cvr, 2)}). 랜딩 페이지·상품 상세·가격 노출을 점검해 보세요.`);
    } else if (ctrGap != null && ctrGap < 1) {
      improve.push(`노출 → 클릭 단계가 약합니다: CTR ${f.pct(worst.ctr, 2)} (전체 ${f.pct(t.ctr, 2)}). 소재(썸네일·첫 문장)와 타깃을 점검해 보세요.`);
    }
    if (worst.cpa != null && t.cpa != null) improve.push(`전환 1건에 ${f.krw(worst.cpa)}이 들었습니다 (전체 ${f.krw(t.cpa)}).`);
    if (worst.roas != null && worst.roas < 100) improve.push('광고비보다 매출이 적습니다.');
    return { good: good.join(' '), improve: improve.join(' ') };
  }

  const API = {
    HEADER, FIELDS, SUM_KEYS,
    addDays, daysBetween, isIso, weekday, dates,
    parseDate, parseNumber, parseCsv, sortRows,
    derive, totals, inPeriod, range, channels, latestWeek, clampPeriod, comparePeriod,
    change, tone, byChannel, daily, summarize,
    fmtPeriod, weeks, insights
  };

  if (global.REBOOT && typeof global.REBOOT.expose === 'function') global.REBOOT.expose('performance.metrics', API);
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : globalThis);
