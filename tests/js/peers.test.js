const assert = require('assert');
const { between, SHARED, FORMATTERS, FakePage, suite, ok, vm } = require('./harness');

const code = between('// Peer comparison\n', '// IPO calendar\n');
const periodCode = between('// Performance period (under the prediction chart)', '// News sentiment\nfunction safeHttpUrl');     // quickRange, todayIso, periodFetch: the chart's picker reuses them
const IDS = ['peer-body', 'peer-chips', 'peer-note', 'peer-reset', 'peer-search', 'peer-suggest'];

function env(handler) {
  const page = new FakePage(IDS); const calls = [], charts = {}; const state = { fetches: [] };
  const ctx = { console, Date, Number, String, Array, Object, JSON, Math, isFinite, encodeURIComponent, setTimeout, clearTimeout, curTick: 'AAPL', API: '', charts, C: ['#1', '#2', '#3', '#4', '#5', '#6'],
    document: page.document, Chart: function (c, cfg) { this.cfg = cfg; }, dc: id => { delete charts[id]; }, signedOut: () => false,
    apiMessage: (d) => (d && d.message) || 'Something went wrong. Please try again.',
    api: async url => { calls.push(url); return handler(url, calls.length); },
    fetch: async (url) => { state.fetches.push(url); return handler(url, state.fetches.length, true); } };
  vm.createContext(ctx); vm.runInContext(SHARED + FORMATTERS + periodCode + code, ctx);
  return { ctx, els: page.registry, calls, state, charts, run: s => vm.runInContext(s, ctx) };
}
const metric = (key, label, format, better, values, o = {}) => ({ key, label, format, better, values, best: null, median: null, position: null, outliers: [], ...o });
const cmp = (tickers) => ({ status: 'success', target: tickers[0], tickers, missing: [], prices_as_of: '2026-10-08T15:41:00',
  companies: Object.fromEntries(tickers.map(t => [t, { company_name: t === 'MSFT' ? 'Micro<script>x</script>soft' : t + ' Inc', current_price: 100, price_change_pct: t === 'MSFT' ? -1.5 : 2 }])),
  groups: [
    { name: 'Valuation', metrics: [metric('pe_ratio', 'P/E', 'ratio', 'lower', Object.fromEntries(tickers.map((t, i) => [t, 20 + i * 5])), { best: tickers[0], median: 27.5, position: 'better' })] },
    { name: 'Growth', metrics: [metric('earnings_growth', 'Earnings growth', 'pct', 'higher', Object.fromEntries(tickers.map((t, i) => [t, i === 1 ? 2.94 : 0.1])), { outliers: [tickers[1]] })] },
    { name: 'News', metrics: [metric('news_sentiment', 'News sentiment (AI)', 'sent', 'higher', Object.fromEntries(tickers.map((t, i) => [t, i === 0 ? -0.38 : 0.2])))] },
    { name: 'Model outlook', metrics: [metric('buy_signal', 'Model signal', 'text', 'higher', Object.fromEntries(tickers.map(t => [t, 'Strong Buy']))), metric('model_move_1w', '7-day model estimate', 'pp', 'higher', Object.fromEntries(tickers.map((t, i) => [t, i - 1.5])))] },
  ] });
const peersResp = (list, source = 'ai') => ({ status: 'success', source, peers: list.map(t => ({ ticker: t, company_name: t + ' Inc' })) });
const prices = (tickers, o = {}) => ({ status: 'success', dates: ['2025-10-14', '2025-10-15', '2025-10-16'], series: Object.fromEntries(tickers.map((t, i) => [t, [100, 101 + i, 103 + i]])),
  changes: Object.fromEntries(tickers.map((t, i) => [t, i === 1 ? -1.5 : 3 + i])), missing: [], start: '2025-10-14', end: '2025-10-16', requested_start: '2025-10-14', requested_end: '2025-10-16', starts_late: false, limited_by: [], ...o });
const std = (url, n, viaFetch) => {
  if (viaFetch && url.includes('/api/compare/verdict')) return ok({ status: 'success', verdict: '**Verdict:** AAPL is pricier.\n\n- point one' });
  if (url.startsWith('/api/peers/')) return peersResp(['MSFT', 'GOOGL', 'META', 'DELL']);
  if (url.startsWith('/api/compare/prices')) { const p = prices(decodeURIComponent(url.split('tickers=')[1].split('&')[0]).split(',')); return viaFetch ? ok(p) : p; }
  if (url.startsWith('/api/compare')) return cmp(decodeURIComponent(url.split('tickers=')[1]).split(','));
  return null;
};
const settle = () => new Promise(r => setTimeout(r, 15));

(async () => {
  const { t, done } = suite('Peer comparison');
  await t('Formatting helpers', async () => {
    const e = env(std); const f = (m, v) => e.ctx.peerFmt(m, v);
    assert.equal(f({ format: 'ratio' }, 12.34), '12.3x'); assert.equal(f({ format: 'pct' }, 0.1634), '16.3%'); assert.equal(f({ format: 'pp', key: 'dividend_yield' }, 2.456), '2.46%');
    assert.equal(f({ format: 'pp', key: 'model_move_1w' }, 0.06), '+0.06%'); assert.equal(f({ format: 'usd' }, -1.5e9), '-$1.50B'); assert.equal(f({ format: 'sent' }, 0.2), '+0.20'); assert.equal(f({ format: 'sent' }, -0.38), '-0.38');
    assert.equal(f({ format: 'text' }, 'strong_buy'), 'strong buy'); assert.equal(f({ format: 'num' }, null), '—'); assert.equal(f({ format: 'text' }, '<b>x</b>'), '&lt;b&gt;x&lt;/b&gt;');
  });
  await t('Loads suggested peers and the comparison once, and shows them as chips', async () => {
    const e = env(std); await e.run('loadPeers()');
    assert.deepEqual(e.calls.slice(0, 2), ['/api/peers/AAPL', '/api/compare?tickers=AAPL%2CMSFT%2CGOOGL%2CMETA%2CDELL']);
    const chips = e.els['peer-chips'].innerHTML; assert(chips.includes('peer-chip target') && ['MSFT', 'GOOGL', 'META', 'DELL'].every(x => chips.includes('>' + x)));
    assert(e.els['peer-note'].textContent.includes('chosen by AI')); await e.run('loadPeers()'); assert.equal(e.calls.filter(c => c.startsWith('/api/peers/')).length, 1);
  });
  await t('Table: target highlighted, best starred, medians, markers, signed estimates, news row, escaped names', async () => {
    const e = env(std); await e.run('loadPeers()'); const h = e.els['peer-body'].innerHTML;
    assert(h.includes('Peer median') && h.includes('peer-target') && h.includes('20.0x ★') && h.includes('27.5x') && h.includes('▲') && h.includes('-1.50%') && h.includes('News sentiment (AI)') && h.includes('-0.38') && h.includes('+0.20'));
    assert(!h.includes('<script>') && h.includes('Micro&lt;script&gt;x&lt;/script&gt;soft'));
  });
  await t('Unusually large growth figures carry a warning marker', async () => {
    const e = env(std); await e.run('loadPeers()'); const h = e.els['peer-body'].innerHTML; assert.equal((h.match(/class="peer-odd"/g) || []).length, 1); assert(h.includes('294.0%') && h.includes('Unusually large'));
  });
  await t('By default the chart covers the last year, with the target line thicker and a change figure for every stock', async () => {
    const e = env(std); await e.run('loadPeers()'); await settle();
    const url = e.state.fetches.find(u => u.startsWith('/api/compare/prices?tickers=AAPL%2CMSFT'));
    const year = e.run("quickRange('1Y', todayIso(), null)"); assert(url.endsWith(`&start=${year.start}&end=${year.end}`), url);
    const cfg = e.charts['peer-px-c'].cfg; assert.deepEqual(cfg.data.datasets.map(d => d.label), ['AAPL', 'MSFT', 'GOOGL', 'META', 'DELL']); assert(cfg.data.datasets[0].borderWidth > cfg.data.datasets[1].borderWidth);
    assert.equal(e.els['peer-chart-box'].style.display, 'block');
  });
  await t('If prices cannot be loaded the table stays and the chart says so', async () => {
    const e = env((u, n, f) => (u.startsWith('/api/compare/prices') ? null : std(u, n, f))); await e.run('loadPeers()'); await settle();
    assert(e.els['peer-chart-msg'].innerHTML.includes('reach the server') && e.els['peer-body'].innerHTML.includes('Peer median'));
    assert.equal(e.els['peer-chart-box'].style.display, 'none'); assert.equal(e.els['peer-perf'].innerHTML, '');
  });
  await t('AI summary: renders markdown, disables the button while it works, and reports errors', async () => {
    const e = env(std); await e.run('loadPeers()'); await settle(); await e.run('loadPeerVerdict()');
    assert(e.state.fetches.find(u => u.includes('/api/compare/verdict')).includes('?tickers=AAPL%2CMSFT') && e.els['peer-verdict'].innerHTML.includes('<strong>Verdict:</strong>') && e.els['peer-verdict-btn'].disabled === false);
    const f = env((u, n, v) => (v ? ok({ status: 'error', message: 'The AI service is busy. Please try again in a minute.' }, 429) : std(u, n, v))); await f.run('loadPeers()'); await settle(); await f.run('loadPeerVerdict()');
    assert(f.els['peer-verdict'].textContent.includes('busy'));
  });
  await t('Remove a peer refetches without it; Reset restores the suggestion', async () => {
    const e = env(std); await e.run('loadPeers()'); await e.run("peerRemove('META')");
    const last = e.calls.filter(c => c.startsWith('/api/compare?')).pop(); assert(last.includes('AAPL%2CMSFT%2CGOOGL%2CDELL') && !last.includes('META')); assert.equal(e.els['peer-reset'].style.display, 'inline');
    await e.run('peerReset()'); assert(e.calls.filter(c => c.startsWith('/api/compare?')).pop().includes('META')); assert.equal(e.els['peer-reset'].style.display, 'none');
  });
  await t('Adding: duplicates, the stock itself and a sixth peer are rejected', async () => {
    const e = env(std); await e.run('loadPeers()'); await e.run("peerRemove('DELL')");
    await e.run("peerAdd('AMD','AMD Inc')"); assert.equal(e.run('peerState.list.length'), 4); await e.run("peerAdd('AMD','x')"); await e.run("peerAdd('AAPL','x')"); assert.equal(e.run('peerState.list.length'), 4);
    await e.run("peerAdd('NVDA','NVIDIA')"); assert.equal(e.run('peerState.list.length'), 5); await e.run("peerAdd('INTC','Intel')"); assert.equal(e.run('peerState.list.length'), 5); assert(e.els['peer-note'].textContent.includes('up to 5'));
  });
  await t('Removing every peer asks for one instead of calling the API', async () => {
    const e = env(std); await e.run('loadPeers()'); for (const x of ['MSFT', 'GOOGL', 'META', 'DELL']) await e.run(`peerRemove('${x}')`); assert(e.els['peer-body'].innerHTML.includes('Add at least one'));
  });
  await t('Switching stocks mid-load, or editing mid-comparison, ignores the stale response', async () => {
    let release; const gate = new Promise(r => { release = r; });
    const e = env(async u => { if (u === '/api/peers/AAPL') { await gate; return peersResp(['MSFT']); } return u.startsWith('/api/peers/') ? peersResp(['AMD']) : std(u); });
    const first = e.run('loadPeers()'); await new Promise(r => setTimeout(r, 5)); e.ctx.curTick = 'NVDA'; await e.run('loadPeers()'); release(); await first;
    assert.equal(e.run('peerState.target'), 'NVDA'); assert.deepEqual(JSON.parse(e.run('JSON.stringify(peerState.list)')), ['AMD']);
    let slow; const gate2 = new Promise(r => { slow = r; }); let n = 0;
    const g = env(async u => { if (u.startsWith('/api/peers/')) return peersResp(['MSFT', 'GOOGL']); if (u.startsWith('/api/compare/prices')) return prices(['AAPL']); n++; if (n === 1) { await gate2; return cmp(['AAPL', 'MSFT', 'GOOGL']); } return cmp(['AAPL', 'MSFT']); });
    const p = g.run('loadPeers()'); await new Promise(r => setTimeout(r, 5)); await g.run("peerRemove('GOOGL')"); slow(); await p; assert(!g.els['peer-body'].innerHTML.includes('GOOGL</th>'));
  });
  await t('Failures show a retry; the database fallback is explained', async () => {
    const a = env(() => null); await a.run('loadPeers()'); assert(a.els['peer-body'].innerHTML.includes('Retry'));
    const b = env((u) => (u.startsWith('/api/peers/') ? peersResp(['MSFT']) : null)); await b.run('loadPeers()'); assert(b.els['peer-body'].innerHTML.includes('Could not load the comparison'));
    const c = env((u, n, v) => (u.startsWith('/api/peers/') ? peersResp(['MSFT'], 'database') : std(u, n, v))); await c.run('loadPeers()'); assert(c.els['peer-note'].textContent.includes('AI selection was unavailable'));
  });
  await t('Search hides the stock and chosen peers, and escapes names', async () => {
    const e = env((u, n, v) => (u.startsWith('/api/stocks/search') ? [{ ticker: 'AAPL', company_name: 'Apple' }, { ticker: 'MSFT', company_name: 'Micro' }, { ticker: 'ZZ"X', company_name: '<img onerror=1>', sector: 'Tech' }] : std(u, n, v)));
    await e.run('loadPeers()'); e.els['peer-search'].value = 'a'; await e.run('peerSearch()'); const h = e.els['peer-suggest'].innerHTML;
    assert(!h.includes('data-t="AAPL"') && !h.includes('data-t="MSFT"') && h.includes('&quot;') && !h.includes('<img')); e.els['peer-search'].value = ''; await e.run('peerSearch()'); assert.equal(e.els['peer-suggest'].style.display, 'none');
  });

  // ── the period picker ──
  const priceUrls = (e) => e.state.fetches.filter(u => u.startsWith('/api/compare/prices'));
  const q = (u, k) => (u.match(new RegExp('[?&]' + k + '=([^&]*)')) || [])[1];
  const loaded = async (handler = std) => { const e = env(handler); await e.run('loadPeers()'); await settle(); return e; };

  await t('Presets turn into the right dates, counting back from today and never landing on a day that does not exist', () => {
    const e = env(std), p = (kind, today) => e.run(`peerPeriodParams({kind:'${kind}',start:'',end:''}, '${today}')`);
    assert.equal(p('1M', '2026-10-20'), '&start=2026-09-20&end=2026-10-20'); assert.equal(p('3M', '2026-10-20'), '&start=2026-07-20&end=2026-10-20');
    assert.equal(p('6M', '2026-10-20'), '&start=2026-04-20&end=2026-10-20'); assert.equal(p('1Y', '2026-10-20'), '&start=2025-10-20&end=2026-10-20');
    assert.equal(p('YTD', '2026-10-20'), '&start=2026-01-01&end=2026-10-20'); assert.equal(p('3Y', '2026-10-20'), '&start=2023-10-20&end=2026-10-20');
    assert.equal(p('5Y', '2026-10-20'), '&start=2021-10-20&end=2026-10-20'); assert.equal(p('10Y', '2026-10-20'), '&start=2016-10-20&end=2026-10-20');
    assert.equal(p('1M', '2026-03-31'), '&start=2026-02-28&end=2026-03-31');                // no 31 February
    assert.equal(p('3M', '2026-01-15'), '&start=2025-10-15&end=2026-01-15');                // back across New Year
    assert.equal(p('1Y', '2028-02-29'), '&start=2027-02-28&end=2028-02-29');                // leap day
    assert.equal(p('MAX', '2026-10-20'), '&period=max');
    assert.equal(e.run("peerPeriodParams({kind:'custom',start:'2024-01-02',end:'2024-03-29'})"), '&start=2024-01-02&end=2024-03-29');
  });

  await t('The picker offers 1M to Max and two date boxes, and shows the current choice', async () => {
    const e = await loaded(); const bar = e.els['peer-body'].innerHTML;                  // the whole card, as first drawn
    for (const label of ['>1M<', '>3M<', '>6M<', '>YTD<', '>1Y<', '>3Y<', '>5Y<', '>10Y<', '>Max<']) assert(bar.includes(label), label);
    assert(bar.includes('id="peer-from"') && bar.includes('id="peer-to"') && bar.includes('aria-label="From date"') && bar.includes('aria-label="To date"'));
    assert.equal((bar.match(/class="fbtn on"/g) || []).length, 1); assert(bar.includes('class="fbtn on" onclick="peerSetPeriod(\'1Y\')"'));
  });

  await t('Choosing a period reloads only the chart: the table and the peers are not asked for again', async () => {
    const e = await loaded(); const before = { calls: e.calls.length, charts: priceUrls(e).length };
    await e.run("peerSetPeriod('3M')"); await settle();
    assert.equal(e.calls.length, before.calls, 'no other request was made'); assert.equal(priceUrls(e).length, before.charts + 1);
    const url = priceUrls(e).pop(); const r = e.run("quickRange('3M', todayIso(), null)");
    assert.equal(q(url, 'start'), r.start); assert.equal(q(url, 'end'), r.end); assert(url.includes('tickers=AAPL%2CMSFT%2CGOOGL%2CMETA%2CDELL'));
    assert(e.els['peer-period'].innerHTML.includes('class="fbtn on" onclick="peerSetPeriod(\'3M\')"')); assert(!e.els['peer-period'].innerHTML.includes('class="fbtn on" onclick="peerSetPeriod(\'1Y\')"'));
    await e.run("peerSetPeriod('MAX')"); await settle(); assert(priceUrls(e).pop().endsWith('&period=max'));
  });

  await t('A custom range needs both dates, in order, and not in the future, and says so without asking the server', async () => {
    const e = await loaded(); const n = priceUrls(e).length, say = () => e.els['peer-period-msg'].textContent;
    e.els['peer-from'].value = '2024-01-02'; e.els['peer-to'].value = ''; await e.run('peerApplyDates()'); assert.equal(say(), 'Pick both a start and an end date.');
    e.els['peer-from'].value = '2024-05-01'; e.els['peer-to'].value = '2024-01-02'; await e.run('peerApplyDates()'); assert.equal(say(), 'The start date must be before the end date.');
    e.els['peer-from'].value = '2024-05-01'; e.els['peer-to'].value = '2024-05-01'; await e.run('peerApplyDates()'); assert.equal(say(), 'The start date must be before the end date.');
    e.els['peer-from'].value = '2999-01-01'; e.els['peer-to'].value = '2999-02-01'; await e.run('peerApplyDates()'); assert.equal(say(), "That period hasn't started yet.");
    assert.equal(priceUrls(e).length, n, 'nothing was requested'); assert.equal(e.run('peerPeriod.kind'), '1Y');
  });

  await t('Valid custom dates are sent as they are, marked as the active choice, and kept in the boxes; a preset clears them', async () => {
    const e = await loaded();
    e.els['peer-from'].value = '2024-01-02'; e.els['peer-to'].value = '2024-03-29'; await e.run('peerApplyDates()'); await settle();
    const url = priceUrls(e).pop(); assert.equal(q(url, 'start'), '2024-01-02'); assert.equal(q(url, 'end'), '2024-03-29');
    const bar = e.els['peer-period'].innerHTML; assert(bar.includes('value="2024-01-02"') && bar.includes('value="2024-03-29"') && bar.includes('id="peer-apply"'));
    assert(bar.includes('class="fbtn on" id="peer-apply"')); assert.equal((bar.match(/class="fbtn on"/g) || []).length, 1);
    await e.run("peerSetPeriod('1Y')"); const after = e.els['peer-period'].innerHTML; assert(!after.includes('value="2024-01-02"') && !after.includes('on" id="peer-apply"'));
  });

  await t('The chosen period survives adding or removing a peer, and the next stock opened', async () => {
    const e = await loaded(); await e.run("peerSetPeriod('6M')"); await settle();
    await e.run("peerRemove('META')"); await settle();
    const r = e.run("quickRange('6M', todayIso(), null)"), url = priceUrls(e).pop();
    assert(!url.includes('META') && q(url, 'start') === r.start, url); assert(e.els['peer-period'].innerHTML.includes('class="fbtn on" onclick="peerSetPeriod(\'6M\')"'));
    e.ctx.curTick = 'NVDA'; await e.run('loadPeers()'); await settle(); assert(priceUrls(e).pop().includes('tickers=NVDA') && q(priceUrls(e).pop(), 'start') === r.start);
  });

  await t('The title names the dates, every stock shows its change over the period, and the target comes first', async () => {
    const e = await loaded(); await e.run("peerSetPeriod('3M')"); await settle();
    assert.equal(e.els['peer-chart-title'].textContent, 'Price performance, Oct 14, 2025 – Oct 16, 2025 (rebased to 100)');
    const h = e.els['peer-perf'].innerHTML; assert(h.includes('Change over this period'));
    assert(h.indexOf('<b>AAPL</b>') < h.indexOf('<b>MSFT</b>') && h.includes('peer-chg tgt'));
    assert(h.includes('<b>AAPL</b><span class="g">+3.00%</span>') && h.includes('<b>MSFT</b><span class="r">-1.50%</span>'));
    assert(h.includes('background:#1') && h.includes('background:#2'), 'each figure carries its line colour');
  });

  await t('When a stock began trading after the period started, the chart says where it starts and why; missing stocks are named', async () => {
    const e = await loaded((u, n, v) => (u.startsWith('/api/compare/prices') ? (v ? ok(prices(['AAPL', 'MSFT'], { starts_late: true, limited_by: ['ARM'], start: '2025-03-04', missing: ['ZZZ'] })) : null) : std(u, n, v)));
    const h = e.els['peer-chart-msg'].innerHTML;
    assert(h.includes('The chart starts on Mar 4, 2025') && h.includes('ARM began trading then') && h.includes('No price history for: ZZZ')); assert.equal(e.els['peer-chart-msg'].style.display, 'block');
    const f = await loaded(); assert.equal(f.els['peer-chart-msg'].style.display, 'none');
  });

  await t('Tickers in the notes and figures are escaped', async () => {
    const e = await loaded((u, n, v) => (u.startsWith('/api/compare/prices') ? (v ? ok(prices(['AAPL', 'M<b>X'], { starts_late: true, limited_by: ['<i>Z</i>'], missing: ['<u>Q</u>'] })) : null) : std(u, n, v)));
    const h = e.els['peer-chart-msg'].innerHTML + e.els['peer-perf'].innerHTML; assert(!h.includes('<i>Z') && !h.includes('<u>Q') && !h.includes('M<b>X')); assert(h.includes('&lt;i&gt;Z') && h.includes('M&lt;b&gt;X'));
  });

  await t('While a new period loads the old chart is dimmed and a spinner shows; afterwards it is back to normal', async () => {
    let release; const gate = new Promise(r => { release = r; });
    const e = await loaded(async (u, n, v) => { if (v && u.startsWith('/api/compare/prices') && u.includes('period=max')) { await gate; return ok(prices(['AAPL', 'MSFT'])); } return std(u, n, v); });
    const pending = e.run("peerSetPeriod('MAX')"); await settle();
    assert.equal(e.els['peer-chart-box'].style.opacity, '.35'); assert(e.els['peer-chart-msg'].innerHTML.includes('Loading prices'));
    release(); await pending; await settle(); assert.equal(e.els['peer-chart-box'].style.opacity, ''); assert(!e.els['peer-chart-msg'].innerHTML.includes('Loading'));
  });

  await t('If the server turns a period down, its own words are shown, the chart is hidden, and another period still works', async () => {
    const e = await loaded((u, n, v) => (u.startsWith('/api/compare/prices') && v && u.includes('period=max') ? ok({ status: 'error', message: 'Please choose a period of 40 years or less.' }, 400) : std(u, n, v)));
    await e.run("peerSetPeriod('MAX')"); await settle();
    assert(e.els['peer-chart-msg'].innerHTML.includes('Please choose a period of 40 years or less.')); assert.equal(e.els['peer-chart-box'].style.display, 'none'); assert.equal(e.els['peer-perf'].innerHTML, '');
    await e.run("peerSetPeriod('1Y')"); await settle(); assert.equal(e.els['peer-chart-box'].style.display, 'block'); assert(e.els['peer-perf'].innerHTML.includes('+3.00%'));
  });

  await t('When the period has no prices at all it says so, naming the stocks that have none', async () => {
    const e = await loaded((u, n, v) => (u.startsWith('/api/compare/prices') && v && u.includes('start=2024') ? ok({ status: 'success', dates: [], series: {}, changes: {}, missing: ['AAPL', 'MSFT'] }) : std(u, n, v)));
    e.els['peer-from'].value = '2024-01-02'; e.els['peer-to'].value = '2024-01-03'; await e.run('peerApplyDates()'); await settle();
    const h = e.els['peer-chart-msg'].innerHTML; assert(h.includes('in this period') && h.includes('AAPL, MSFT')); assert.equal(e.els['peer-chart-box'].style.display, 'none');
  });

  await t('An older answer arriving after a newer choice is ignored', async () => {
    let releaseOld; const old = new Promise(r => { releaseOld = r; });
    const e = await loaded(async (u, n, v) => { if (v && u.startsWith('/api/compare/prices') && u.includes('period=max')) { await old; return ok(prices(['AAPL', 'MSFT'], { start: '2001-01-02', end: '2025-10-16' })); } return std(u, n, v); });
    const slow = e.run("peerSetPeriod('MAX')"); await settle(); await e.run("peerSetPeriod('1M')"); await settle();
    const title = e.els['peer-chart-title'].textContent; releaseOld(); await slow; await settle();
    assert.equal(e.els['peer-chart-title'].textContent, title); assert(!title.includes('2001'));
  });

  await t('Choosing a period before any chart exists does nothing harmful and is used when the chart loads', async () => {
    const e = env(std); e.run("peerSetPeriod('5Y')"); assert.equal(priceUrls(e).length, 0);
    await e.run('loadPeers()'); await settle(); const r = e.run("quickRange('5Y', todayIso(), null)"); assert.equal(q(priceUrls(e)[0], 'start'), r.start);
  });
  done();
})();
