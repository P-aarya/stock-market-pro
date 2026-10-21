const assert = require('assert');
const { between, SHARED, FORMATTERS, FakePage, suite, ok, vm } = require('./harness');

const code = between('let pfRows=[],pfEditId=null;', '// Transactions\nlet txEditId') ;
const review = between('async function runPfReview(mode)', 'function loadPortfolioTickers()');
const IDS = ['pf-val', 'pf-inv', 'pf-pnl', 'pf-pp', 'pf-day', 'pf-day-s', 'pf-warn', 'tb-pf', 'sec-alloc', 'pf-msg', 'a-tick', 'a-qty', 'a-px', 'a-dt', 'a-notes', 'pf-form-title', 'pf-save-btn',
  'pf-cancel-btn', 'pf-form-card', 'pf-tickers', 'pf-ai-btn', 'pf-hold-btn', 'pf-ai-loading', 'pf-ai-result', 'pf-ai-result-text', 'pf-perf-sub', 'pf-perf-msg', 'pf-perf-body', 'pf-perf-stats', 'pf-risk', 'pf-perf-notes'];

function env({ holdings, summary, performance, onFetch = () => ok({}), confirmAnswer = true }) {
  const page = new FakePage(IDS); ['alloc-c', 'pnl-c', 'perf-c'].forEach(id => page.el(id));
  const fetches = [], charts = {}, state = { holdings, summary, performance };
  const ctx = { console, Date, Number, String, Array, Object, JSON, Math, isFinite, encodeURIComponent, charts, C: ['#1', '#2', '#3'], API: 'http://x', document: page.document,
    Chart: function (c, cfg) { this.cfg = cfg; }, dc: id => { delete charts[id]; }, confirm: () => confirmAnswer,
    api: async u => (u === '/api/portfolio' ? state.holdings : u === '/api/portfolio/summary' ? state.summary : u === '/api/portfolio/performance' ? state.performance : null),
    fetch: async (u, o = {}) => { fetches.push({ url: u, method: o.method || 'GET', body: o.body ? JSON.parse(o.body) : null }); return onFetch(u, o, fetches.length, state); } };
  vm.createContext(ctx); vm.runInContext(SHARED + FORMATTERS + code + review, ctx);
  return { ctx, els: page.registry, fetches, charts, state, run: s => vm.runInContext(s, ctx) };
}
const H = (o = {}) => ({ id: 1, ticker: 'AAPL', company_name: 'Apple Inc.', sector: 'Technology', sector_group: 'Technology', quantity: 20, avg_buy_price: 165.2, buy_date: '2024-02-10', notes: 'Core', invested_value: 3304, current_value: 6882, unrealized_pnl: 3578, pnl_pct: 108.3, current_price: 344.1, day_change_pct: 0.92, analyst_rating: 'buy', target_price: 315, ...o });
const S = (o = {}) => ({ total_value: 6882, total_invested: 3304, total_pnl: 3578, return_pct: 108.3, day_change: 62.4, day_change_pct: 0.92, total_holdings: 1, unpriced_holdings: 0, unpriced_tickers: [], ...o });
const PERF = (o = {}) => ({ status: 'success', empty: false, benchmark_name: 'S&P 500 (SPY)', dates: ['2024-02-10', '2024-02-11'], portfolio: [3304, 3400], invested: [3304, 3304], benchmark: [3304, 3380],
  summary: { cost: 3304, portfolio_value: 6882, benchmark_value: 6000, portfolio_return_pct: 108.3, benchmark_return_pct: 81.6, difference_pp: 26.7, difference_usd: 882 },
  risk: { volatility_pct: 18.6, max_drawdown_pct: -22.4, beta: 1.08, effective_holdings: 8.4, top3_weight_pct: 46.9, diversification: 'Moderate', observations: 691 }, notes: ['Each holding is assumed to have been owned from its buy date.'], ...o });
const FORM = (e, t, q, p, d, notes = '') => Object.assign(e.els['a-tick'], { value: t }) && Object.assign(e.els['a-qty'], { value: q }) && Object.assign(e.els['a-px'], { value: p }) && Object.assign(e.els['a-dt'], { value: d }) && Object.assign(e.els['a-notes'], { value: notes });

(async () => {
  const { t, done } = suite('Portfolio tab');
  await t('Cards use the corrected summary and the day percentage; no warning when all are priced', async () => {
    const e = env({ holdings: [H()], summary: S() }); await e.run('loadPortfolio()');
    assert.equal(e.els['pf-pp'].textContent, '+108.30% return'); assert.equal(e.els['pf-day'].textContent, '+$62'); assert.equal(e.els['pf-day-s'].textContent, '+0.92% today'); assert.equal(e.els['pf-warn'].style.display, 'none');
  });
  await t('Unpriced holdings produce a warning and a "no price" badge', async () => {
    const e = env({ holdings: [H(), H({ id: 2, ticker: 'NOPX', current_price: null, current_value: null, unrealized_pnl: null, pnl_pct: null })], summary: S({ unpriced_holdings: 1, unpriced_tickers: ['NOPX'] }) }); await e.run('loadPortfolio()');
    assert(e.els['pf-warn'].textContent.includes('NOPX') && e.els['pf-warn'].textContent.includes('left out') && e.els['tb-pf'].innerHTML.includes('no price'));
  });
  await t('Table: 14 cells per row, Edit/Remove carry only the id, text escaped, ticker click uses data-t', async () => {
    const e = env({ holdings: [H({ company_name: 'Evil <img src=x onerror=1> Corp', ticker: "A'B" })], summary: S() }); await e.run('loadPortfolio()'); const h = e.els['tb-pf'].innerHTML;
    assert(!h.includes('<img') && h.includes('Evil &lt;img') && h.includes('data-t="A&#39;B"') && h.includes('onclick="openStock(this.dataset.t)"') && h.includes('event.stopPropagation()')); assert.equal((h.match(/<td/g) || []).length, 14);
  });
  await t('Allocation chart groups everything past the top eight into "Other"; sectors use the standard groups', async () => {
    const many = Array.from({ length: 10 }, (_, i) => H({ id: i + 1, ticker: 'T' + i, current_value: 1000 - i * 10, sector: i % 2 ? 'Banking' : 'Cloud & SaaS', sector_group: i % 2 ? 'Financials' : 'Technology' }));
    const e = env({ holdings: many, summary: S() }); await e.run('loadPortfolio()'); const cfg = e.charts['alloc-c'].cfg;
    assert.equal(cfg.data.labels.length, 9); assert(cfg.data.labels[8].startsWith('Other (2)')); assert(Math.abs(cfg.data.datasets[0].data[8] - (1000 - 80 + 1000 - 90)) < 1e-9);
    const sec = e.els['sec-alloc'].innerHTML; assert(sec.includes('Technology') && sec.includes('Financials') && !sec.includes('Cloud &amp; SaaS') && !sec.includes('Banking'));
  });
  await t('Load failure shows Retry; an empty portfolio says so', async () => {
    let e = env({ holdings: null, summary: null }); await e.run('loadPortfolio()'); assert(e.els['tb-pf'].innerHTML.includes('Retry'));
    e = env({ holdings: [], summary: S({ total_value: 0 }) }); await e.run('loadPortfolio()'); assert(e.els['tb-pf'].innerHTML.includes('No holdings yet'));
  });
  await t('Performance card: returns, difference, risk measures and a three-line chart', async () => {
    const e = env({ holdings: [H()], summary: S(), performance: PERF() }); await e.run('loadPortfolio()'); await e.run('loadPortfolioPerformance()');
    const stats = e.els['pf-perf-stats'].innerHTML, risk = e.els['pf-risk'].innerHTML;
    assert(stats.includes('+108.3%') && stats.includes('+81.6%') && stats.includes('+26.7 pts') && stats.includes('S&amp;P 500 (SPY)'));
    assert(risk.includes('18.6%') && risk.includes('-22.4%') && risk.includes('1.08') && risk.includes('Moderate') && risk.includes('top 3 = 47%'));
    assert.equal(e.els['pf-perf-body'].style.display, 'block'); assert.equal(e.charts['perf-c'].cfg.data.datasets.length, 3); assert(e.els['pf-perf-notes'].textContent.includes('assumed'));
  });
  await t('Performance card: missing risk numbers show a dash, failures offer Retry, an empty portfolio explains itself', async () => {
    let e = env({ holdings: [H()], summary: S(), performance: PERF({ risk: { volatility_pct: null, max_drawdown_pct: null, beta: null, effective_holdings: null, diversification: null } }) }); await e.run('loadPortfolioPerformance()'); assert(e.els['pf-risk'].innerHTML.includes('—'));
    e = env({ holdings: [H()], summary: S(), performance: null }); await e.run('loadPortfolioPerformance()'); assert(e.els['pf-perf-msg'].innerHTML.includes('Retry') && e.els['pf-perf-body'].style.display === 'none');
    e = env({ holdings: [], summary: S(), performance: { status: 'success', empty: true } }); await e.run('loadPortfolioPerformance()'); assert(e.els['pf-perf-msg'].innerHTML.includes('Add a holding'));
  });
  await t('Form validation messages appear without any request', async () => {
    const e = env({ holdings: [], summary: S() }); await e.run('saveHolding()'); assert(e.els['pf-msg'].textContent.includes('Please fill in'));
    FORM(e, 'ko', '-3', '60', '2025-01-02'); await e.run('saveHolding()'); assert(e.els['pf-msg'].textContent.includes('Quantity must be greater'));
    e.els['a-qty'].value = '5'; e.els['a-px'].value = '0'; await e.run('saveHolding()'); assert(e.els['pf-msg'].textContent.includes('Average buy price'));
    e.els['a-px'].value = '60'; e.els['a-dt'].value = '2999-01-01'; await e.run('saveHolding()'); assert(e.els['pf-msg'].textContent.includes('future')); assert.equal(e.fetches.length, 0);
  });
  await t('Add posts clean JSON, shows the message, clears the form and reloads', async () => {
    const e = env({ holdings: [H()], summary: S(), onFetch: () => ok({ status: 'success', message: 'Added 5 shares of KO.' }) });
    FORM(e, ' ko ', '5', '60.5', '2025-01-02', 'test'); await e.run('saveHolding()'); const f = e.fetches[0];
    assert.equal(f.method, 'POST'); assert.equal(f.url, 'http://x/api/portfolio'); assert.deepEqual(f.body, { ticker: 'KO', quantity: 5, avg_buy_price: 60.5, buy_date: '2025-01-02', notes: 'test' });
    assert(e.els['pf-msg'].textContent.includes('Added 5 shares of KO.') && e.els['pf-msg'].style.color === 'var(--green)'); assert.equal(e.els['a-tick'].value, ''); assert.equal(e.els['pf-save-btn'].disabled, false);
  });
  await t('Server rejections are shown in plain words (message, 422 detail list, network failure)', async () => {
    let e = env({ holdings: [], summary: S(), onFetch: () => ok({ status: 'error', message: "ZZZZ isn't a stock we track, so it can't be added." }, 400) }); FORM(e, 'ZZZZ', '1', '1', '2025-01-02'); await e.run('saveHolding()');
    assert(e.els['pf-msg'].textContent.includes("isn't a stock we track") && e.els['pf-msg'].style.color === 'var(--red)' && e.els['a-tick'].value === 'ZZZZ');
    e = env({ holdings: [], summary: S(), onFetch: () => ok({ detail: [{ msg: 'Input should be greater than 0' }] }, 422) }); FORM(e, 'KO', '1', '1', '2025-01-02'); await e.run('saveHolding()'); assert(e.els['pf-msg'].textContent.includes('greater than 0'));
    e = env({ holdings: [], summary: S(), onFetch: () => { throw new TypeError('Failed to fetch'); } }); FORM(e, 'KO', '1', '1', '2025-01-02'); await e.run('saveHolding()'); assert(e.els['pf-msg'].textContent.includes("Couldn't reach the server") && !e.els['pf-save-btn'].disabled);
  });
  await t('Edit fills the form and locks the ticker; saving sends a PUT; Cancel resets', async () => {
    const e = env({ holdings: [H()], summary: S(), onFetch: () => ok({ status: 'success', message: 'AAPL updated.' }) }); await e.run('loadPortfolio()'); e.run("editHolding('1')");
    assert.equal(e.els['a-tick'].value, 'AAPL'); assert.equal(e.els['a-tick'].readOnly, true); assert.equal(e.els['a-dt'].value, '2024-02-10'); assert(e.els['pf-form-title'].textContent.includes('Edit AAPL'));
    e.els['a-qty'].value = '25'; await e.run('saveHolding()'); const f = e.fetches[0]; assert.equal(f.method, 'PUT'); assert.equal(f.url, 'http://x/api/portfolio/1'); assert.deepEqual(f.body, { quantity: 25, avg_buy_price: 165.2, buy_date: '2024-02-10', notes: 'Core' });
    e.run("editHolding('1')"); e.run('cancelHoldingEdit()'); assert.equal(e.els['a-tick'].value, ''); assert.equal(e.els['pf-cancel-btn'].style.display, 'none');
  });
  await t('Remove asks first; Cancel sends nothing; OK sends DELETE; a 404 is explained', async () => {
    let e = env({ holdings: [H()], summary: S(), confirmAnswer: false }); await e.run('loadPortfolio()'); await e.run("deleteHolding('1')"); assert.equal(e.fetches.length, 0);
    e = env({ holdings: [H()], summary: S(), onFetch: () => ok({ status: 'success', message: 'AAPL removed.' }) }); await e.run('loadPortfolio()'); await e.run("deleteHolding('1')");
    assert.equal(e.fetches[0].method, 'DELETE'); assert.equal(e.fetches[0].url, 'http://x/api/portfolio/1'); assert(e.els['pf-msg'].textContent.includes('AAPL removed.'));
    e = env({ holdings: [H()], summary: S(), onFetch: () => ok({ status: 'error', message: 'That holding no longer exists.' }, 404) }); await e.run('loadPortfolio()'); await e.run("deleteHolding('1')"); assert(e.els['pf-msg'].textContent.includes('no longer exists'));
  });
  await t('AI review: a typed list is split on commas/newlines and accepts company names', async () => {
    const e = env({ holdings: [], summary: S(), onFetch: () => ok({ status: 'success', review: '**Portfolio snapshot**\n- Value $1' }) });
    e.els['pf-tickers'].value = 'Apple, Tesla;\nnvidia ,, '; await e.run("runPfReview('list')"); const f = e.fetches[0];
    assert.equal(f.url, 'http://x/agent/portfolio-review'); assert.deepEqual(f.body, { tickers: ['Apple', 'Tesla', 'nvidia'], use_holdings: false });
    assert(e.els['pf-ai-result-text'].innerHTML.includes('<strong>Portfolio snapshot</strong>') && !e.els['pf-ai-btn'].disabled && !e.els['pf-hold-btn'].disabled);
  });
  await t('AI review of saved holdings needs no typing, and errors are readable', async () => {
    let e = env({ holdings: [], summary: S(), onFetch: () => ok({ status: 'success', review: 'ok' }) }); await e.run("runPfReview('holdings')"); assert.deepEqual(e.fetches[0].body, { tickers: [], use_holdings: true });
    e = env({ holdings: [], summary: S(), onFetch: () => ok({}) }); e.els['pf-tickers'].value = '  '; await e.run("runPfReview('list')"); assert(e.els['pf-ai-result-text'].textContent.includes('Type at least one') && e.fetches.length === 0);
    e = env({ holdings: [], summary: S(), onFetch: () => ok({ status: 'error', message: 'The AI service is busy. Please try again in a minute.' }, 429) }); e.els['pf-tickers'].value = 'AAPL'; await e.run("runPfReview('list')"); assert(e.els['pf-ai-result-text'].textContent.includes('busy'));
    e = env({ holdings: [], summary: S(), onFetch: () => { throw new TypeError('Failed to fetch'); } }); e.els['pf-tickers'].value = 'AAPL'; await e.run("runPfReview('list')"); assert(e.els['pf-ai-result-text'].textContent.includes("Couldn't reach the server") && !e.els['pf-ai-btn'].disabled);
  });
  done();
})();
