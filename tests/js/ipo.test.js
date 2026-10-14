const assert = require('assert');
const { between, SHARED, FORMATTERS, FakePage, suite, vm } = require('./harness');

const ipo = between('// IPO calendar\n', '// News — Sector → Company cascade');
const IDS = ['tb-ipo', 'ipo-week', 'ipo-count', 'ipo-asof', 'ipo-note', 'ipo-mini', 'ipo-terms', 'ipo-nospac', 'ipo-window', 'ipo-exch', 'ipo-srch'];

function env(apiResult) {
  const page = new FakePage(IDS);
  page.el('ipo-window').value = 'upcoming';
  const ctx = { console, Date, Number, String, Array, Object, JSON, Math, document: page.document, api: async () => apiResult };
  vm.createContext(ctx); vm.runInContext(SHARED + FORMATTERS + ipo, ctx);
  return { ctx, els: page.registry, run: s => vm.runInContext(s, ctx) };
}
const row = (o) => ({ ticker: 'AAA', company: 'Alpha Corp', exchange: 'Nasdaq', date: '2026-10-09', days_until: 1, status: 'Expected', price_low: null, price_high: null, shares: null, deal_value: null, is_spac: false, in_database: false, confirmed: false, sources: ['Yahoo Finance'], ...o });
const data = {
  ipos: [
    row({ ticker: 'TRXB', company: 'TRex <b>Bio</b>, Inc.', exchange: 'NASDAQ Global Select', price_low: 14, price_high: 16, shares: 8333334, deal_value: 153333344, confirmed: true, sources: ['Nasdaq'] }),
    row({ ticker: 'SPAC', company: 'Zeta Acquisition Corp.', is_spac: true, date: '2026-10-20', days_until: 12 }),
    row({ ticker: 'OLD', company: 'Old Co', days_until: -5, date: '2026-10-03', status: 'Priced', price_low: 10, price_high: 10, exchange: 'NYSE' }),
    row({ ticker: 'LATE', company: 'Late Co', days_until: -3, date: '2026-10-05' }),
    row({ ticker: 'TRK', company: 'Tracked Inc', in_database: true, days_until: 2, date: '2026-10-10' }),
  ],
  as_of: '2026-10-08T15:26:00+00:00', warnings: ['Price ranges are incomplete right now.'],
};

(async () => {
  const { t, done } = suite('IPO calendar');
  await t('Rows render with company text escaped and terms formatted', async () => {
    const e = env(data); await e.run('loadIPOs()'); const h = e.els['tb-ipo'].innerHTML;
    assert(!h.includes('<b>Bio') && h.includes('TRex &lt;b&gt;Bio') && h.includes('$14.00 – $16.00') && h.includes('8,333,334') && h.includes('$153.3M'));
  });
  await t('The default view shows upcoming IPOs only', async () => {
    const e = env(data); await e.run('loadIPOs()'); const h = e.els['tb-ipo'].innerHTML;
    assert(!h.includes('Old Co') && !h.includes('Late Co') && h.includes('Tracked Inc')); assert.equal(e.els['ipo-count'].textContent, '3 of 5 IPOs shown');
  });
  await t('Past filter: newest first, with Priced and "Date passed" badges', async () => {
    const e = env(data); await e.run('loadIPOs()'); e.els['ipo-window'].value = 'past'; e.run('renderIPOs()'); const h = e.els['tb-ipo'].innerHTML;
    assert(h.indexOf('Late Co') < h.indexOf('Old Co') && h.includes('badge bg">Priced') && h.includes('Date passed'));
  });
  await t('Terms, SPAC, search and exchange filters', async () => {
    const e = env(data); await e.run('loadIPOs()');
    e.els['ipo-terms'].checked = true; e.run('renderIPOs()'); assert.equal(e.els['ipo-count'].textContent, '1 of 5 IPOs shown');
    e.els['ipo-terms'].checked = false; e.els['ipo-nospac'].checked = true; e.run('renderIPOs()'); assert(!e.els['tb-ipo'].innerHTML.includes('Zeta'));
    e.els['ipo-nospac'].checked = false; e.els['ipo-srch'].value = 'tracked'; e.run('renderIPOs()'); assert.equal(e.els['ipo-count'].textContent, '1 of 5 IPOs shown');
    e.els['ipo-srch'].value = ''; e.els['ipo-exch'].value = 'NYSE'; e.els['ipo-window'].value = 'all'; e.run('renderIPOs()');
    assert(e.els['tb-ipo'].innerHTML.includes('Old Co') && !e.els['tb-ipo'].innerHTML.includes('Alpha'));
  });
  await t('Only tickers we already track are clickable', async () => {
    const e = env(data); await e.run('loadIPOs()'); const h = e.els['tb-ipo'].innerHTML;
    assert(h.includes('onclick="openStock(this.dataset.t)"') && (h.match(/openStock/g) || []).length === 1);
  });
  await t('The next-7-days strip and the Market page card', async () => {
    const e = env(data); await e.run('loadIPOs()');
    assert(e.els['ipo-week'].innerHTML.includes('Terms not announced yet') && !e.els['ipo-week'].innerHTML.includes('Zeta'));
    const m = e.els['ipo-mini'].innerHTML; assert(m.includes('Tracked Inc') && m.includes('terms TBA') && !m.includes('Old Co'));
  });
  await t('Server warnings are shown; a failure shows Retry and a failed refresh keeps old data', async () => {
    const e = env(data); await e.run('loadIPOs()'); assert(e.els['ipo-note'].innerHTML.includes('Price ranges are incomplete'));
    e.ctx.api = async () => null; await e.run('loadIPOs()'); assert(e.els['tb-ipo'].innerHTML.includes('Tracked Inc') && !e.run('ipoFailed'));
    const f = env(null); await f.run('loadIPOs()'); assert(f.els['tb-ipo'].innerHTML.includes('Retry') && f.els['ipo-mini'].innerHTML.includes('unavailable'));
  });
  done();
})();
