const assert = require('assert');
const { between, SHARED, FORMATTERS, FakePage, suite, ok, vm } = require('./harness');

const code = between('let txEditId=null;', 'async function checkConn()');
const IDS = ['tx-tot', 'tx-inv', 'tx-sold', 'tx-big', 'tx-real', 'tx-div', 'tx-real-list', 'tx-warn', 'tx-srch', 'tb-tx', 'tx-msg', 'tx-tick', 'tx-type', 'tx-qty', 'tx-px', 'tx-dt', 'tx-notes',
  'tx-apply', 'tx-apply-wrap', 'tx-form-title', 'tx-save-btn', 'tx-cancel-btn', 'tx-form-card'];

function env({ txs = [], summary = null, onFetch = () => ok({}), confirmAnswer = true }) {
  const page = new FakePage(IDS); ['bs-c', 'txm-c'].forEach(id => page.el(id)); page.el('tx-apply-wrap').style.display = 'flex'; page.el('tx-type').value = 'BUY';
  const fetches = [], charts = {}; let reloadedPortfolio = 0;
  const ctx = { console, Date, Number, String, Array, Object, JSON, Math, isFinite, encodeURIComponent, charts, API: 'http://x', document: page.document, Chart: function (c, cfg) { this.cfg = cfg; }, dc: id => { delete charts[id]; },
    confirm: () => confirmAnswer, apiMessage: (d) => (d && d.message) || (d && d.detail && d.detail[0].msg) || 'Something went wrong. Please try again.', loadPortfolio: () => { reloadedPortfolio++; },
    api: async u => (u === '/api/transactions' ? txs : u === '/api/transactions/summary' ? summary : null),
    fetch: async (u, o = {}) => { fetches.push({ url: u, method: o.method || 'GET', body: o.body ? JSON.parse(o.body) : null }); return onFetch(u, o, fetches.length); } };
  vm.createContext(ctx); vm.runInContext(SHARED + FORMATTERS + 'let aTx=[],txF="all";' + code, ctx);
  return { ctx, els: page.registry, fetches, charts, portfolioReloads: () => reloadedPortfolio, run: s => vm.runInContext(s, ctx) };
}
const TX = (o = {}) => ({ id: 1, ticker: 'KO', company_name: 'Coca-Cola', transaction_type: 'BUY', quantity: 5, price: 60, total_value: 300, transaction_date: '2025-02-03', notes: 'n', ...o });
const SUM = (o = {}) => ({ total_trades: 3, total_invested: 600, total_sold: 140, largest_trade: 300, dividends: 1.5, realized_pnl: 20, realized_by_ticker: [{ ticker: 'KO', realized: 20 }], unmatched_sells: [], ...o });
const FILL = (e, o) => { const v = { tick: 'ko', type: 'BUY', qty: '5', px: '60', dt: '2025-02-03', notes: '', ...o }; e.els['tx-tick'].value = v.tick; e.els['tx-type'].value = v.type; e.els['tx-qty'].value = v.qty; e.els['tx-px'].value = v.px; e.els['tx-dt'].value = v.dt; e.els['tx-notes'].value = v.notes; };

(async () => {
  const { t, done } = suite('Transactions tab');
  await t('Cards show realized P&L, dividends and the per-stock list', async () => {
    const e = env({ txs: [TX()], summary: SUM() }); await e.run('loadTx()');
    assert.equal(e.els['tx-real'].textContent, '+$20'); assert.equal(e.els['tx-div'].textContent, '$2'); assert(e.els['tx-real-list'].innerHTML.includes('KO') && e.els['tx-real-list'].innerHTML.includes('+$20')); assert.equal(e.els['tx-warn'].style.display, 'none');
  });
  await t('A sale larger than the recorded purchases triggers a visible warning', async () => {
    const e = env({ txs: [TX()], summary: SUM({ unmatched_sells: [{ ticker: 'CCC', quantity: 3 }] }) }); await e.run('loadTx()'); assert(e.els['tx-warn'].textContent.includes('CCC: 3') && e.els['tx-warn'].style.display === 'block');
  });
  await t('Rows are escaped, typed with badges, and carry Edit / Remove buttons holding only the id', async () => {
    const e = env({ txs: [TX({ company_name: 'Evil <img src=x onerror=1>', notes: '<b>x</b>' }), TX({ id: 2, transaction_type: 'DIVIDEND' })], summary: SUM() }); await e.run('loadTx()'); const h = e.els['tb-tx'].innerHTML;
    assert(!h.includes('<img') && !h.includes('<b>x') && h.includes('badge bc') && h.includes('data-id="1" onclick="editTx(this.dataset.id)"') && h.includes('onclick="deleteTx(this.dataset.id)"'));
  });
  await t('The monthly chart ignores dividends', async () => {
    const e = env({ txs: [TX({ total_value: 300 }), TX({ id: 2, transaction_type: 'DIVIDEND', total_value: 99, transaction_date: '2025-02-20' })], summary: SUM() }); await e.run('loadTx()');
    assert.deepEqual(e.charts['txm-c'].cfg.data.datasets[0].data, [300]);
  });
  await t('Validation messages appear without any request', async () => {
    const e = env({}); await e.run('saveTx()'); assert(e.els['tx-msg'].textContent.includes('Please fill in'));
    FILL(e, { qty: '-1' }); await e.run('saveTx()'); assert(e.els['tx-msg'].textContent.includes('Quantity')); FILL(e, { px: '0' }); await e.run('saveTx()'); assert(e.els['tx-msg'].textContent.includes('Price'));
    FILL(e, { dt: '2999-01-01' }); await e.run('saveTx()'); assert(e.els['tx-msg'].textContent.includes('future')); assert.equal(e.fetches.length, 0);
  });
  await t('Add sends clean JSON; "update my holdings" is passed through and refreshes the portfolio', async () => {
    const e = env({ txs: [], summary: SUM(), onFetch: () => ok({ status: 'success', message: 'Recorded BUY 5 KO at $60.00. Added 5 shares of KO.' }) });
    FILL(e, {}); e.els['tx-apply'].checked = true; await e.run('saveTx()'); const f = e.fetches[0];
    assert.equal(f.method, 'POST'); assert.equal(f.url, 'http://x/api/transactions'); assert.deepEqual(f.body, { ticker: 'KO', transaction_type: 'BUY', quantity: 5, price: 60, transaction_date: '2025-02-03', notes: '', apply_to_holdings: true });
    assert(e.els['tx-msg'].textContent.includes('Recorded BUY') && e.els['tx-tick'].value === '' && e.portfolioReloads() === 1);
    FILL(e, { type: 'DIVIDEND', qty: '3', px: '0.5' }); e.els['tx-apply'].checked = false; await e.run('saveTx()'); assert.equal(e.fetches[1].body.apply_to_holdings, false); assert.equal(e.portfolioReloads(), 1);
  });
  await t('Server rejections and network failures are shown in plain words', async () => {
    let e = env({ onFetch: () => ok({ status: 'error', message: "You can't sell 10 KO: your records show only 3 shares." }, 400) }); FILL(e, { type: 'SELL', qty: '10' }); await e.run('saveTx()');
    assert(e.els['tx-msg'].textContent.includes("can't sell 10 KO") && e.els['tx-msg'].style.color === 'var(--red)' && e.els['tx-tick'].value === 'ko');
    e = env({ onFetch: () => { throw new TypeError('x'); } }); FILL(e, {}); await e.run('saveTx()'); assert(e.els['tx-msg'].textContent.includes("Couldn't reach the server") && !e.els['tx-save-btn'].disabled);
  });
  await t('Edit locks ticker and type, hides the holdings option, and sends a PUT without them', async () => {
    const e = env({ txs: [TX({ id: 7 })], summary: SUM(), onFetch: () => ok({ status: 'success', message: 'KO transaction updated.' }) }); await e.run('loadTx()'); e.run("editTx('7')");
    assert(e.els['tx-tick'].readOnly && e.els['tx-type'].disabled && e.els['tx-apply-wrap'].style.display === 'none' && e.els['tx-form-title'].textContent.includes('Edit BUY KO'));
    e.els['tx-qty'].value = '6'; await e.run('saveTx()'); const f = e.fetches[0]; assert.equal(f.method, 'PUT'); assert.equal(f.url, 'http://x/api/transactions/7'); assert.deepEqual(f.body, { quantity: 6, price: 60, transaction_date: '2025-02-03', notes: 'n' });
    assert.equal(e.els['tx-apply-wrap'].style.display, 'flex'); assert.equal(e.els['tx-type'].disabled, false);
  });
  await t('Remove asks first, sends DELETE, and explains a 404', async () => {
    let e = env({ txs: [TX()], summary: SUM(), confirmAnswer: false }); await e.run('loadTx()'); await e.run("deleteTx('1')"); assert.equal(e.fetches.length, 0);
    e = env({ txs: [TX()], summary: SUM(), onFetch: () => ok({ status: 'success', message: 'KO transaction removed. (Holdings are not changed.)' }) }); await e.run('loadTx()'); await e.run("deleteTx('1')"); assert.equal(e.fetches[0].method, 'DELETE'); assert(e.els['tx-msg'].textContent.includes('removed'));
    e = env({ txs: [TX()], summary: SUM(), onFetch: () => ok({ status: 'error', message: 'That transaction no longer exists.' }, 404) }); await e.run('loadTx()'); await e.run("deleteTx('1')"); assert(e.els['tx-msg'].textContent.includes('no longer exists'));
  });
  done();
})();
