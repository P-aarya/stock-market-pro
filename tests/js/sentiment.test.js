const assert = require('assert');
const { between, SHARED, FakePage, suite, vm } = require('./harness');

const code = between('// News sentiment\n', '// Peer comparison\n');
function env(handler) {
  const page = new FakePage(['sent-card']);
  const calls = [], opened = [];
  const ctx = { console, Date, Number, String, Array, Object, Math, encodeURIComponent, curTick: 'AAPL', document: page.document, window: { open: (...a) => opened.push(a) },
    api: async u => { calls.push(u); return handler(u, calls.length); } };
  vm.createContext(ctx); vm.runInContext(SHARED + code, ctx);
  return { ctx, el: page.registry['sent-card'], calls, opened, run: s => vm.runInContext(s, ctx) };
}
const now = Math.floor(Date.now() / 1000);
const data = (o = {}) => ({ status: 'success', ticker: 'AAPL', score: -0.51, label: 'Negative', n_articles: 4, n_relevant: 2, n_stories: 2, counts: { positive: 1, neutral: 0, negative: 1 }, cached: false, stale: false, updated_at: '2026-10-09T15:00:00',
  headlines: [{ title: 'Apple cuts <b>orders</b>', source: 'Src & Co', url: 'https://example.com/a?x=1&y="2"', published: now, score: -0.8 },
              { title: 'Apple edge AI', source: 'S2', url: 'javascript:alert(1)', published: now - 3600, score: 0.7 },
              { title: 'NVIDIA rallies', source: 'S3', url: '', published: now, score: null },
              { title: 'Market update', source: 'S4', url: 'https://example.com/m', published: null, score: null }], ...o });

(async () => {
  const { t, done } = suite('News sentiment card');
  await t('Shows label, score, counts and the explanation', async () => {
    const e = env(() => data()); await e.run('loadSentiment()'); const h = e.el.innerHTML;
    assert(h.includes('badge br') && h.includes('Negative') && h.includes('-0.51') && h.includes('2 of 4 headlines are about AAPL') && h.includes('context, not a forecast'));
    assert(h.includes('left:24.5%'), 'gauge marker at (-0.51+1)/2');
  });
  await t('Mentions distinct stories only when several headlines are the same story', async () => {
    let e = env(() => data({ n_relevant: 5, n_stories: 2 })); await e.run('loadSentiment()'); assert(e.el.innerHTML.includes('(2 distinct stories)'));
    e = env(() => data({ n_relevant: 2, n_stories: 2 })); await e.run('loadSentiment()'); assert(!e.el.innerHTML.includes('distinct stories'));
  });
  await t('Headline and source text is escaped; only http(s) links are clickable', async () => {
    const e = env(() => data()); await e.run('loadSentiment()'); const h = e.el.innerHTML;
    assert(!h.includes('<b>orders') && h.includes('Apple cuts &lt;b&gt;orders&lt;/b&gt;') && h.includes('Src &amp; Co'));
    assert(h.includes('href="https://example.com/a?x=1&amp;y=&quot;2&quot;"') && !h.includes('javascript:'));
  });
  await t('Relevant headlines first; ignored ones tucked into a details block', async () => {
    const e = env(() => data()); await e.run('loadSentiment()'); const h = e.el.innerHTML;
    assert(h.indexOf('Apple cuts') < h.indexOf('<details>') && h.includes('2 headlines not about AAPL (ignored)') && h.includes('-0.80') && h.includes('+0.70') && h.includes('>n/a<'));
  });
  await t('Stale results are flagged; empty states read sensibly', async () => {
    let e = env(() => data({ stale: true })); await e.run('loadSentiment()'); assert(e.el.innerHTML.includes('older result'));
    e = env(() => data({ score: null, label: 'No relevant news', n_relevant: 0, counts: { positive: 0, neutral: 0, negative: 0 }, headlines: [data().headlines[2]] })); await e.run('loadSentiment()'); assert(e.el.innerHTML.includes('None of the recent headlines are about this company'));
    e = env(() => data({ score: null, label: 'No recent news', n_articles: 0, n_relevant: 0, headlines: [] })); await e.run('loadSentiment()'); assert(e.el.innerHTML.includes('No recent headlines found'));
  });
  await t('Loaded once per stock; a failure shows a working Retry', async () => {
    const e = env(() => data()); await e.run('loadSentiment()'); await e.run('loadSentiment()'); assert.equal(e.calls.length, 1);
    e.ctx.curTick = 'MSFT'; await e.run('loadSentiment()'); assert.equal(e.calls[1], '/api/sentiment/MSFT');
    let n = 0; const f = env(() => (++n === 1 ? null : data())); await f.run('loadSentiment()'); assert(f.el.innerHTML.includes('Retry'));
    f.el.dataset.state = ''; await f.run('loadSentiment()'); assert(f.el.innerHTML.includes('Negative'));
  });
  await t('Switching stocks mid-load ignores the stale response', async () => {
    let release; const gate = new Promise(r => { release = r; });
    const e = env(async u => { if (u.endsWith('AAPL')) { await gate; return data(); } return data({ ticker: 'MSFT', score: 0.4, label: 'Positive' }); });
    const first = e.run('loadSentiment()'); await new Promise(r => setTimeout(r, 5)); e.ctx.curTick = 'MSFT'; await e.run('loadSentiment()'); release(); await first;
    assert(e.el.innerHTML.includes('Positive') && !e.el.innerHTML.includes('Negative')); assert.equal(e.el.dataset.t, 'MSFT');
  });
  await t('Unsafe URLs are rejected and safe ones open without opener access', async () => {
    const e = env(() => data());
    assert.equal(e.run("safeHttpUrl('javascript:alert(1)')"), ''); assert.equal(e.run("safeHttpUrl('data:text/html,x')"), ''); assert.equal(e.run("safeHttpUrl('https://a.com')"), 'https://a.com');
    e.run("openNewsLink({dataset:{url:'https://a.com'}})"); e.run("openNewsLink({dataset:{url:''}})"); assert.deepEqual(e.opened, [['https://a.com', '_blank', 'noopener']]);
  });
  done();
})();
