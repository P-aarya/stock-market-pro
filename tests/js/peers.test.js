const assert = require('assert');
const { between, SHARED, FORMATTERS, FakePage, suite, ok, vm } = require('./harness');

const code = between('// Peer comparison\n', '// IPO calendar\n');
const IDS = ['peer-body', 'peer-chips', 'peer-note', 'peer-reset', 'peer-search', 'peer-suggest'];

function env(handler) {
  const page = new FakePage(IDS); const calls = [], charts = {}; const state = { fetches: [] };
  const ctx = { console, Date, Number, String, Array, Object, JSON, Math, isFinite, encodeURIComponent, setTimeout, clearTimeout, curTick: 'AAPL', API: '', charts, C: ['#1', '#2', '#3', '#4', '#5', '#6'],
    document: page.document, Chart: function (c, cfg) { this.cfg = cfg; }, dc: id => { delete charts[id]; }, signedOut: () => false,
    apiMessage: (d) => (d && d.message) || 'Something went wrong. Please try again.',
    api: async url => { calls.push(url); return handler(url, calls.length); },
    fetch: async (url) => { state.fetches.push(url); return handler(url, state.fetches.length, true); } };
  vm.createContext(ctx); vm.runInContext(SHARED + FORMATTERS + code, ctx);
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
const prices = (tickers) => ({ status: 'success', dates: ['2025-10-14', '2025-10-15', '2025-10-16'], series: Object.fromEntries(tickers.map((t, i) => [t, [100, 101 + i, 103 + i]])), missing: [] });
const std = (url, n, viaFetch) => {
  if (viaFetch && url.includes('/api/compare/verdict')) return ok({ status: 'success', verdict: '**Verdict:** AAPL is pricier.\n\n- point one' });
  if (url.startsWith('/api/peers/')) return peersResp(['MSFT', 'GOOGL', 'META', 'DELL']);
  if (url.startsWith('/api/compare/prices')) return prices(decodeURIComponent(url.split('tickers=')[1]).split(','));
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
    const e = env(std); await e.run('loadPeers()'); const h = e.els['peer-body'].innerHTML; assert.equal((h.match(/⚠️/g) || []).length, 1); assert(h.includes('294.0%') && h.includes('Unusually large'));
  });
  await t('A one-year price chart is drawn for the compared stocks (target line thicker)', async () => {
    const e = env(std); await e.run('loadPeers()'); await settle();
    assert(e.calls.some(c => c.startsWith('/api/compare/prices?tickers=AAPL%2CMSFT')));
    const cfg = e.charts['peer-px-c'].cfg; assert.deepEqual(cfg.data.datasets.map(d => d.label), ['AAPL', 'MSFT', 'GOOGL', 'META', 'DELL']); assert(cfg.data.datasets[0].borderWidth > cfg.data.datasets[1].borderWidth);
    assert.equal(e.els['peer-chart-box'].style.display, 'block');
  });
  await t('If prices cannot be loaded the table stays and the chart says so', async () => {
    const e = env((u, n, f) => (u.startsWith('/api/compare/prices') ? null : std(u, n, f))); await e.run('loadPeers()'); await settle();
    assert(e.els['peer-chart-msg'].innerHTML.includes("isn't available") && e.els['peer-body'].innerHTML.includes('Peer median'));
  });
  await t('AI summary: renders markdown, disables the button while it works, and reports errors', async () => {
    const e = env(std); await e.run('loadPeers()'); await settle(); await e.run('loadPeerVerdict()');
    assert(e.state.fetches[0].includes('/api/compare/verdict?tickers=AAPL%2CMSFT') && e.els['peer-verdict'].innerHTML.includes('<strong>Verdict:</strong>') && e.els['peer-verdict-btn'].disabled === false);
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
  done();
})();
