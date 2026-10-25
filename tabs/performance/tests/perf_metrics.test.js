/* 성과분석 계산(tabs/performance/static/metrics.js) 테스트 — 실행: node tabs/performance/tests/perf_metrics.test.js */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const M = require('../static/metrics.js');

const HEADER = '날짜,채널,광고비,노출수,클릭수,전환수,매출';
const sampleText = fs.readFileSync(path.join(__dirname, '..', 'data', 'performance_sample.csv'), 'utf8');
const sample = M.parseCsv(sampleText);

test('날짜·숫자 읽기', () => {
  assert.equal(M.parseDate('2026/10/19'), '2026-10-19');
  assert.equal(M.parseDate('2026.1.5'), '2026-01-05');
  assert.equal(M.parseDate('2026. 10. 21.'), '2026-10-21');
  assert.equal(M.parseDate('2026-02-30'), null);
  assert.equal(M.parseDate('10/19/2026'), null);
  assert.equal(M.parseNumber('1,260,000'), 1260000);
  assert.equal(M.parseNumber('1260000원'), 1260000);
  assert.equal(M.parseNumber('₩ 300,000'), 300000);
  assert.equal(M.parseNumber(''), 0);
  assert.ok(Number.isNaN(M.parseNumber('-5')));
  assert.ok(Number.isNaN(M.parseNumber('abc')));
});

test('CSV 헤더 확인 · 따옴표 · 같은 날짜+채널은 아래 줄', () => {
  assert.equal(M.parseCsv('date,channel\n1,2').ok, false);
  assert.match(M.parseCsv('date,channel\n1,2').error, /첫 줄은/);
  const r = M.parseCsv('\ufeff' + HEADER + ',,\r\n2026-10-19,Instagram,"300,000",52000,1300,42,1260000원\r\n2026-10-19,Instagram,1,1,1,1,1\r\n\r\n');
  assert.equal(r.ok, true);
  assert.equal(r.rows.length, 1);
  assert.equal(r.rows[0].spend, 1);
  const bad = M.parseCsv(HEADER + '\n2026-10-19,IG,abc,1,1,1,1');
  assert.equal(bad.ok, false);
  assert.match(bad.error, /2번째 줄/);
});

test('샘플 CSV: 51일 × 4채널, README 예시 행', () => {
  assert.equal(sample.ok, true);
  assert.equal(sample.rows.length, 204);
  assert.deepEqual(M.range(sample.rows), { start: '2026-09-01', end: '2026-10-21' });
  const ex = sample.rows.find(r => r.date === '2026-10-19' && r.channel === 'Instagram');
  assert.deepEqual(ex, { date: '2026-10-19', channel: 'Instagram', spend: 300000, impressions: 52000, clicks: 1300, conversions: 42, revenue: 1260000 });
});

test('기간: 최근 7일 · 비교 기간 · 범위 맞춤', () => {
  const rng = { start: '2026-09-01', end: '2026-10-21' };
  assert.deepEqual(M.latestWeek(rng), { start: '2026-10-15', end: '2026-10-21' });
  assert.deepEqual(M.latestWeek({ start: '2026-10-19', end: '2026-10-21' }), { start: '2026-10-19', end: '2026-10-21' });
  assert.deepEqual(M.comparePeriod('2026-10-15', '2026-10-21', rng.start), { start: '2026-10-08', end: '2026-10-14', days: 7, label: '전주 대비' });
  assert.deepEqual(M.comparePeriod('2026-10-10', '2026-10-12', rng.start), { start: '2026-10-07', end: '2026-10-09', days: 3, label: '직전 기간 대비' });
  assert.equal(M.comparePeriod('2026-09-01', '2026-09-03', rng.start), null);       // CSV 시작일보다 앞 → 비교 안 함
  assert.notEqual(M.comparePeriod('2026-09-04', '2026-09-06', rng.start), null);    // 딱 시작일까지는 비교
  assert.deepEqual(M.clampPeriod('2026-10-30', '2026-10-01', rng), { start: '2026-10-01', end: '2026-10-21' });
  assert.equal(M.daysBetween('2026-10-15', '2026-10-21'), 7);
  assert.equal(M.addDays('2026-11-01', -1), '2026-10-31');
  assert.equal(M.addDays('2026-03-08', 1), '2026-03-09');
});

test('합계는 채널 평균이 아니라 합계로 다시 계산', () => {
  const rows = [
    { date: '2026-10-19', channel: 'A', spend: 100, impressions: 1000, clicks: 10, conversions: 1, revenue: 1000 },
    { date: '2026-10-19', channel: 'B', spend: 300, impressions: 1000, clicks: 30, conversions: 3, revenue: 300 }
  ];
  const t = M.totals(rows);
  assert.equal(t.roas, 1300 / 400 * 100);        // 325% (채널 평균 550%가 아님)
  assert.equal(t.ctr, 40 / 2000 * 100);
  assert.equal(t.cvr, 4 / 40 * 100);
  assert.equal(t.cpa, 100);
  assert.equal(t.profit, 900);
  const z = M.totals([{ date: '2026-10-19', channel: 'A', spend: 0, impressions: 0, clicks: 0, conversions: 0, revenue: 0 }]);
  assert.equal(z.roas, null);
  assert.equal(z.cpa, null);
});

test('증감률 · 좋고 나쁨 (CPA는 낮을수록 좋음, 광고비는 회색)', () => {
  assert.equal(M.change(120, 100), 20);
  assert.equal(M.change(80, 100), -20);
  assert.equal(M.change(5, 0), null);
  assert.equal(M.change(5, null), null);
  assert.equal(M.tone('revenue', 10), 'good');
  assert.equal(M.tone('revenue', -10), 'bad');
  assert.equal(M.tone('cpa', -10), 'good');
  assert.equal(M.tone('cpa', 10), 'bad');
  assert.equal(M.tone('spend', 30), 'neutral');
  assert.equal(M.tone('roas', null), 'none');
});

test('기간 요약: 샘플 최근 7일 — 서버 요약과 같은 숫자', () => {
  const rows = sample.rows;
  const s = M.summarize(rows, '2026-10-15', '2026-10-21');
  const cur = rows.filter(r => r.date >= '2026-10-15' && r.date <= '2026-10-21');
  const prev = rows.filter(r => r.date >= '2026-10-08' && r.date <= '2026-10-14');
  const sum = (list, k) => list.reduce((a, r) => a + r[k], 0);
  assert.equal(s.totals.revenue, sum(cur, 'revenue'));
  assert.equal(s.totals.spend, sum(cur, 'spend'));
  assert.equal(s.prevTotals.revenue, sum(prev, 'revenue'));
  assert.ok(Math.abs(s.change.revenue - (sum(cur, 'revenue') - sum(prev, 'revenue')) / sum(prev, 'revenue') * 100) < 1e-9);
  assert.equal(s.channels.length, 4);
  assert.equal(s.best.channel, 'Instagram');
  assert.equal(s.worst.channel, 'TikTok');
  const shares = s.channels.reduce((a, c) => a + c.share, 0);
  assert.ok(Math.abs(shares - 100) < 1e-9);
  const d = M.daily(rows, '2026-10-15', '2026-10-21');
  assert.equal(d.length, 7);
  assert.equal(d.reduce((a, x) => a + x.revenue, 0), s.totals.revenue);
  assert.equal(M.daily(rows, '2026-10-21', '2026-10-21', 'Google')[0].spend, 172000);
});

test('숫자 기준 문구: 최고·최저 채널과 퍼널 단계', () => {
  const s = M.summarize(sample.rows, '2026-10-15', '2026-10-21');
  const t = M.insights(s);
  assert.match(t.good, /^Instagram의 ROAS가/);
  assert.match(t.good, /전주 대비/);
  assert.match(t.improve, /^TikTok의 ROAS가/);
  assert.match(t.improve, /클릭 → 전환 단계/);   // TikTok은 CVR이 가장 약함
  const empty = M.insights(M.summarize(sample.rows, '2027-01-01', '2027-01-07'));
  assert.match(empty.good, /비교할 수 없습니다/);
});

test('보고서용 주 목록 · 기간 표시', () => {
  const w = M.weeks({ start: '2026-09-01', end: '2026-10-21' });
  assert.deepEqual(w[0], { start: '2026-10-12', end: '2026-10-18' });
  assert.deepEqual(w[w.length - 1], { start: '2026-09-07', end: '2026-09-13' });
  assert.ok(w.every(x => M.weekday(x.start) === 1));
  assert.equal(M.fmtPeriod('2026-10-15', '2026-10-21'), '2026.10.15 — 10.21');
  assert.equal(M.fmtPeriod('2026-12-28', '2027-01-03'), '2026.12.28 — 2027.01.03');
});
