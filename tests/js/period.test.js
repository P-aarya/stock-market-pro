const assert = require('assert');
const { between, SHARED, FORMATTERS, FakePage, suite, ok, vm } = require('./harness');

const code = between('// Performance period (under the prediction chart)', '// News sentiment\nfunction safeHttpUrl');
const IDS = ['period-bar', 'period-year', 'period-quarter', 'period-from', 'period-to', 'period-back', 'period-msg', 'pred-period-view', 'pred-wrap', 'period-apply'];

function env(handler, { ticker = 'NVDA' } = {}) {
  const page = new FakePage(IDS); page.el('period-c'); page.el('pred-wrap').style.display = '';
  const requests = [], charts = {}; const quick = ['YTD', '1Y', '3Y', '5Y', '10Y', 'MAX'].map(k => { const b = new page.El('button'); b.dataset.quick = k; b.classList = { s: new Set(), add() {}, remove() {}, toggle(c, on) { on ? this.s.add(c) : this.s.delete(c); } }; return b; });
  const doc = { ...page.document, querySelectorAll: () => quick };
  const ctx = { console, Date, Number, String, Array, Object, JSON, Math, encodeURIComponent, charts, curTick: ticker, API: '', document: doc, Chart: function (c, cfg) { this.cfg = cfg; }, dc: id => { delete charts[id]; },
    signedOut: () => false, apiMessage: (d) => (d && d.message) || 'Something went wrong. Please try again.',
    fetch: async (url) => { requests.push(url); return handler(url, requests.length); } };
  vm.createContext(ctx); vm.runInContext(SHARED + FORMATTERS + code, ctx);
  return { ctx, els: page.registry, requests, charts, quick, run: s => vm.runInContext(s, ctx) };
}
const RANGE = { status: 'success', first_date: '2005-03-10', last_date: '2026-10-12' };
const PERIOD = (o = {}) => ({ status: 'success', ticker: 'NVDA', dates: ['2024-04-01', '2024-04-02', '2024-06-28'], prices: [90.11, 91, 123.2], benchmark: [90.11, 90.5, 94.2], benchmark_name: 'S&P 500 (SPY)',
  adjusted: 'Prices are adjusted for splits and dividends.', stats: { start_price: 90.11, end_price: 123.2, actual_start: '2024-04-01', actual_end: '2024-06-28', change_pct: 36.73, change_usd: 33.09, high: 135.21, high_date: '2024-06-18', low: 75.98, low_date: '2024-04-19',
    max_drawdown_pct: -15.91, max_drawdown_peak_date: '2024-03-08', max_drawdown_low_date: '2024-04-19', volatility_pct: 54.2, up_days_pct: 53.2, best_day_pct: 9.5, best_day: '2024-05-23', worst_day_pct: -7.1, worst_day: '2024-04-19',
    trading_days: 63, starts_late: false, ends_early: false, benchmark_change_pct: 4.56, difference_pp: 32.17, ...(o.stats || {}) }, ...o, ...(o.stats ? { stats: { ...PERIOD().stats, ...o.stats } } : {}) });
const std = (url) => url.includes('/price-range') ? ok(RANGE) : url.includes('/period?') ? ok(PERIOD()) : ok({}, 404);
const open = async (e) => { await e.run("initPeriodBar('NVDA')"); };

(async () => {
  const { t, done } = suite('Performance period (under the prediction chart)');

  await t('Years and quarters turn into the right dates; the end is capped at today and future periods are refused', async () => {
    const e = env(std); const r = (y, q, today = '2026-10-13') => e.run(`periodRange(${JSON.stringify(y)},${JSON.stringify(q)},${JSON.stringify(today)})`);
    assert.deepEqual(JSON.parse(JSON.stringify(r(2023, '', '2026-10-13'))), { start: '2023-01-01', end: '2023-12-31', partial: false, label: '2023' });
    assert.deepEqual(JSON.parse(JSON.stringify(r(2024, 'Q1'))), { start: '2024-01-01', end: '2024-03-31', partial: false, label: 'Q1 2024' });
    assert.equal(r(2024, 'Q2').end, '2024-06-30'); assert.equal(r(2024, 'Q3').end, '2024-09-30'); assert.equal(r(2024, 'Q4').end, '2024-12-31'); assert.equal(r(2025, 'Q4').start, '2025-10-01');
    const now = r(2026, 'Q4'); assert.equal(now.end, '2026-10-13'); assert.equal(now.partial, true); assert(now.label.includes('to date'));
    assert.equal(r(2026, '').end, '2026-10-13'); assert.equal(r(2027, ''), null); assert.equal(r(2026, 'Q4', '2026-09-30'), null); assert.equal(r('', ''), null); assert.equal(r('abc', 'Q1'), null);
  });

  await t('Quick ranges: year to date, 1/3/5/10 years, and since listing; a young stock starts when it started', async () => {
    const e = env(std); const q = (k, today = '2026-10-13', first = '2005-03-10') => JSON.parse(JSON.stringify(e.run(`quickRange(${JSON.stringify(k)},${JSON.stringify(today)},${JSON.stringify(first)})`)));
    assert.equal(q('YTD').start, '2026-01-01'); assert.equal(q('1Y').start, '2025-10-13'); assert.equal(q('3Y').start, '2023-10-13'); assert.equal(q('5Y').start, '2021-10-13'); assert.equal(q('10Y').start, '2016-10-13');
    assert.equal(q('MAX').start, '2005-03-10'); assert.equal(q('10Y', '2026-10-13', '2023-09-14').start, '2023-09-14'); assert.equal(q('1Y', '2024-02-29').start, '2023-02-28'); assert.equal(e.run("quickRange('nope','2026-10-13','2005-01-01')"), null);
    assert(q('5Y').end === '2026-10-13' && q('MAX').label === 'Since listing');
  });

  await t('The bar appears once the price range is known, with years from listing to now and date limits', async () => {
    const e = env(std); await open(e);
    assert.equal(e.requests[0], '/api/stocks/NVDA/price-range'); assert.equal(e.els['period-bar'].style.display, 'flex');
    const years = [...e.els['period-year'].innerHTML.matchAll(/value="(\d+)"/g)].map(m => Number(m[1])); const thisYear = new Date().getFullYear();
    assert.equal(years[0], thisYear); assert.equal(years[years.length - 1], 2005); assert.deepEqual(years, [...years].sort((a, b) => b - a));
    assert.equal(e.els['period-quarter'].disabled, true); assert.equal(e.els['period-from'].min, '2005-03-10'); assert.equal(e.els['period-to'].max.length, 10);
  });

  await t('If the price history is unavailable the bar stays hidden, and a stale answer for another stock is ignored', async () => {
    const e = env(() => ok({ status: 'error', message: 'No price history' }, 404)); await open(e); assert.notEqual(e.els['period-bar'].style.display, 'flex');
    let release; const gate = new Promise(r => { release = r; }); const f = env(async () => { await gate; return ok(RANGE); });
    const p = f.run("initPeriodBar('NVDA')"); f.ctx.curTick = 'AAPL'; release(); await p; assert.notEqual(f.els['period-bar'].style.display, 'flex');
  });

  await t('Choosing a year applies it straight away; quarters that have not started or predate the stock are disabled', async () => {
    const e = env((u) => (u.includes('/price-range') ? ok({ ...RANGE, first_date: '2005-08-10' }) : std(u))); await open(e);      // listed in August 2005
    e.els['period-year'].value = '2005'; await e.run('periodYearChanged()');
    const q05 = e.els['period-quarter'].innerHTML; assert(q05.includes('value="Q1" disabled') && q05.includes('value="Q2" disabled') && !q05.includes('value="Q3" disabled') && !q05.includes('value="Q4" disabled'));
    assert.equal(e.els['period-quarter'].disabled, false); assert(e.requests[e.requests.length - 1].endsWith('/period?start=2005-01-01&end=2005-12-31'));
    const thisYear = new Date().getFullYear(); e.els['period-year'].value = String(thisYear); await e.run('periodYearChanged()');
    const month = new Date().getMonth() + 1; const qNow = Math.ceil(month / 3), qCurrent = e.els['period-quarter'].innerHTML;
    for (let n = 1; n <= 4; n++) assert.equal(qCurrent.includes(`value="Q${n}" disabled`), n > qNow, `Q${n}`);
  });

  await t('Choosing a quarter requests exactly that quarter', async () => {
    const e = env(std); await open(e); e.els['period-year'].value = '2024'; await e.run('periodYearChanged()');
    e.els['period-quarter'].value = 'Q2'; await e.run('periodQuarterChanged()'); assert(e.requests[e.requests.length - 1].endsWith('/period?start=2024-04-01&end=2024-06-30'));
    e.els['period-quarter'].value = ''; await e.run('periodQuarterChanged()'); assert(e.requests[e.requests.length - 1].endsWith('start=2024-01-01&end=2024-12-31'));
  });

  await t('The period view shows the change, the comparison, eight stat cards, a two-line chart, and hides the forecast', async () => {
    const e = env(std); await open(e); e.els['period-year'].value = '2024'; e.els['period-quarter'].value = 'Q2'; await e.run("applyPeriod(periodRange(2024,'Q2','2026-10-13'))");
    const h = e.els['pred-period-view'].innerHTML;
    assert(h.includes('Q2 2024') && h.includes('+36.73%') && h.includes('S&amp;P 500 +4.56%') && h.includes('+32.2 pts') && h.includes('Jun 28, 2024') && h.includes('-15.9%') && h.includes('54.2%') && h.includes('63 trading days') && h.includes('Past performance does not predict'));
    assert.equal((h.match(/class="sc"/g) || []).length, 8); assert.equal(e.els['pred-wrap'].style.display, 'none'); assert.equal(e.els['pred-period-view'].style.display, 'block'); assert.equal(e.els['period-back'].style.display, 'inline');
    const cfg = e.charts['period-c'].cfg; assert.equal(cfg.data.datasets.length, 2); assert.equal(cfg.data.datasets[0].borderColor, '#00e676'); assert.equal(cfg.data.datasets[1].borderDash.length, 2);
  });

  await t('A falling stock is drawn red; notes explain a late start, an unfinished period and a missing index', async () => {
    const e = env((u) => u.includes('/price-range') ? ok(RANGE) : ok(PERIOD({ benchmark: null, benchmark_name: null, stats: { change_pct: -12.5, change_usd: -10, starts_late: true, actual_start: '2023-09-14', benchmark_change_pct: null, difference_pp: null } })));
    await open(e); await e.run("applyPeriod({start:'2023-01-01',end:'2026-10-13',partial:true,label:'2023 (to date)'})"); const h = e.els['pred-period-view'].innerHTML;
    assert(h.includes('-12.50%') && h.includes('Trading began on Sep 14, 2023') && h.includes('still in progress') && h.includes('S&amp;P 500 comparison isn&#39;t available'));
    const cfg = e.charts['period-c'].cfg; assert.equal(cfg.data.datasets.length, 1); assert.equal(cfg.data.datasets[0].borderColor, '#ff4444');
  });

  await t('Quick buttons highlight the active one; Back restores the forecast and clears everything', async () => {
    const e = env(std); await open(e); await e.run("periodQuick('5Y')");
    assert(e.requests[e.requests.length - 1].includes('/period?start=')); assert(e.quick.find(b => b.dataset.quick === '5Y').classList.s.has('on') && !e.quick.find(b => b.dataset.quick === '1Y').classList.s.has('on'));
    e.run('backToForecast()'); assert.equal(e.els['pred-wrap'].style.display, ''); assert.equal(e.els['pred-period-view'].style.display, 'none'); assert.equal(e.els['pred-period-view'].innerHTML, ''); assert.equal(e.els['period-back'].style.display, 'none'); assert(!e.charts['period-c']);
    assert(e.quick.every(b => !b.classList.s.has('on')));
  });

  await t('Custom dates: both are required and the start must be earlier; valid dates are sent unchanged', async () => {
    const e = env(std); await open(e); const before = e.requests.length;
    e.run('periodApplyCustom()'); assert(e.els['period-msg'].textContent.includes('both a start and an end')); e.els['period-from'].value = '2024-05-01'; e.els['period-to'].value = '2024-04-01'; e.run('periodApplyCustom()');
    assert(e.els['period-msg'].textContent.includes('must be before')); assert.equal(e.requests.length, before);
    e.els['period-to'].value = '2024-05-31'; await e.run('periodApplyCustom()'); assert(e.requests[e.requests.length - 1].endsWith('start=2024-05-01&end=2024-05-31')); assert.equal(e.els['period-msg'].textContent, '');
  });

  await t('A refused period shows the server\'s own explanation and returns to the forecast, not a blank chart', async () => {
    const e = env((u) => u.includes('/price-range') ? ok(RANGE) : ok({ status: 'error', message: "There isn't enough price data in that period. The stock may not have been trading yet, or the dates are too close together." }, 404));
    await open(e); await e.run("applyPeriod({start:'2001-01-01',end:'2001-12-31',partial:false,label:'2001'})");
    assert(e.els['period-msg'].textContent.includes("isn't enough price data")); assert.equal(e.els['pred-wrap'].style.display, ''); assert.equal(e.els['pred-period-view'].style.display, 'none'); assert.equal(e.els['period-back'].style.display, 'none');
    const n = env((u) => { if (u.includes('/price-range')) return ok(RANGE); throw new TypeError('Failed to fetch'); }); await open(n); await n.run("applyPeriod({start:'2024-01-01',end:'2024-12-31',partial:false,label:'2024'})");
    assert(n.els['period-msg'].textContent.includes("Couldn't reach the server"));
  });

  await t('When choices overlap, only the latest one is shown', async () => {
    let releaseFirst; const gate = new Promise(r => { releaseFirst = r; });
    const e = env(async (u) => { if (u.includes('/price-range')) return ok(RANGE); if (u.includes('start=2023-01-01')) { await gate; return ok(PERIOD({ ticker: 'OLD' })); } return ok(PERIOD()); });
    await open(e); const first = e.run("applyPeriod({start:'2023-01-01',end:'2023-12-31',partial:false,label:'2023'})"); await new Promise(r => setTimeout(r, 5));
    await e.run("applyPeriod({start:'2024-01-01',end:'2024-12-31',partial:false,label:'2024'})"); releaseFirst(); await first;
    assert(e.els['pred-period-view'].innerHTML.includes('2024') && !e.els['pred-period-view'].innerHTML.includes('2023'));
  });

  await t('Opening another stock hides and clears the period view; labels and dates are escaped', async () => {
    const e = env(std); await open(e); await e.run("applyPeriod({start:'2024-01-01',end:'2024-12-31',partial:false,label:'<img src=x onerror=1>'})");
    assert(!e.els['pred-period-view'].innerHTML.includes('<img') && e.els['pred-period-view'].innerHTML.includes('&lt;img'));
    e.run('resetPeriodBar()'); assert.equal(e.els['period-bar'].style.display, 'none'); assert.equal(e.els['pred-period-view'].innerHTML, ''); assert(!e.charts['period-c']);
  });

  done();
})();
