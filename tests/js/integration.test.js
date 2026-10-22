// Aarya's dashboard work and Abheek's live in one file. These checks keep either from silently undoing the other.
const assert = require('assert');
const { html, suite } = require('./harness');
const { t, done } = suite('Both dashboards in one (dashboard.html)');

const markup = html.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/g, '');
const script = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const ids = new Set([...markup.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
const functions = [...script.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm)].map(m => m[1]);

(async () => {
  await t('everything Abheek built is on the page', () => {
    for (const id of ['page-ipos', 'ntab-ipos', 'fss-peers', 'sent-card', 'pf-perf-card', 'tx-form-card', 'pf-form-card', 'pf-ai-result', 'period-bar', 'pred-period-view',
                      'bounty-btn', 'bounty-win', 'tb-wl', 'wl-add-input'])
      assert(ids.has(id) || new RegExp(`id=\\\\?["']${id}`).test(script), `missing #${id}`);
    for (const fn of ['loadIPOs', 'loadPeers', 'loadNewsSentiment', 'loadPortfolioPerformance', 'applyPeriod', 'sendBounty', 'initWatchlist', 'addAdminLink', 'adoptLegacyBounty'])
      assert(functions.includes(fn), `missing function ${fn}`);
  });

  await t('everything Aarya built is on the page', () => {
    for (const id of ['nt-bell', 'nt-panel', 'nt-badge', 'nt-list', 'alert-modal', 'alerts-list', 'news-grid', 'news-modal', 'eco-controls',
                      'eco-calendar-body', 'btn-eco-cal', 'btn-earn-cal', 'fss-dividends', 'fs-dividends-content', 'pred-sent-strip'])
      assert(ids.has(id) || new RegExp(`id=\\\\?["']${id}`).test(script), `missing #${id}`);
    for (const fn of ['loadWorldNews', 'loadEcoCalendar', 'switchCalTab', 'loadDividends', 'loadSentimentPanel', 'loadSpikeExplorer', 'ntToggleBell', 'saveAlert', 'openNewsModal', 'timeAgo'])
      assert(functions.includes(fn), `missing function ${fn}`);
  });

  await t('no function is defined twice, so none silently replaces another (the Market sentiment gauge used to be replaced by the news-sentiment loader)', () => {
    const twice = [...new Set(functions.filter((f, i) => functions.indexOf(f) !== i))];
    assert.deepStrictEqual(twice, []);
    assert(functions.includes('loadSentiment') && functions.includes('loadNewsSentiment'));
    assert(/loadSentiment\(\)[^;]*loadSectors\(\)/.test(script), 'the Market page start-up still loads its sentiment gauge');
    assert(/if\(t==='news'\)loadNewsSentiment\(\);/.test(script), 'the News tab loads the news-sentiment card');
  });

  await t('every tab in the stock view has a section to show, and every page tab has its page', () => {
    for (const [, key] of html.matchAll(/class="fstab[^"]*" onclick="showFST\('([a-z]+)'/g)) assert(ids.has('fss-' + key), `tab '${key}' has no #fss-${key}`);
    for (const [, key] of html.matchAll(/class="ntab[^"]*"[^>]*onclick="showPage\('([a-z]+)'/g)) assert(ids.has('page-' + key), `tab '${key}' has no #page-${key}`);
    const tabs = [...html.matchAll(/class="fstab[^"]*" onclick="showFST\('([a-z]+)'/g)].map(m => m[1]);
    assert.deepStrictEqual(tabs, ['overview', 'charts', 'company', 'peers', 'financials', 'intelligence', 'earnings', 'dividends', 'news']);
  });

  await t('the Market page start-up loads only things that still have a place on the page', () => {
    const init = script.slice(script.indexOf('async function init(){'), script.indexOf('init();\ncheckConn();')).replace(/\/\/.*$/gm, '');   // code only, no comments
    assert(/loadIPOs\(\)/.test(init) && /ecoInitMonth\(\)/.test(init), 'both sides\' start-up calls are there');
    assert(!/loadPredictions\(\)/.test(init), 'the Market Intelligence grid it fed was replaced by the compact layout');
  });

  await t('the page talks to the server it came from, whichever address it was opened at', () => {
    assert(/const API=\(location\.protocol==='http:'\|\|location\.protocol==='https:'\)\?location\.origin/.test(script));
    assert(!/window\.location\.hostname==='localhost'/.test(script));
  });

  await t('the stock view\'s news list shows Aarya\'s layout (with pictures) with every field escaped', () => {
    const block = script.slice(script.indexOf("fs-news-list').innerHTML"), script.indexOf("fs-news-list').innerHTML") + 1400);
    assert(/fsnews-grid/.test(block) && /n\.image/.test(block), 'her layout');
    for (const field of ['n.title', 'n.source', 'n.url', 'n.image']) assert(new RegExp(`escapeHtml\\((?:safeHttpUrl\\()?${field.replace('.', '\\.')}`).test(block), `${field} is escaped`);
  });

  await t('headlines, sources, links and pictures from outside feeds are never inserted into the page as markup', () => {
    const body = (name) => { const a = script.indexOf(`function ${name}(`); assert(a >= 0, name); const b = script.indexOf('\nfunction ', a + 10); const c = script.indexOf('\nasync function ', a + 10);
      return script.slice(a, Math.min(...[b, c].filter(x => x > 0))); };
    for (const fn of ['renderWorldNews', 'renderStockNews', 'renderModalNews']) {
      const src = body(fn);
      assert(/escapeHtml\(n\.title/.test(src) && /escapeHtml\(n\.source/.test(src), `${fn}: title and source escaped`);
      assert(/data-url="\$\{escapeHtml\(safeHttpUrl\(n\.url\)\)\}" onclick="openNewsLink\(this\)"/.test(src), `${fn}: the link is checked and opened safely`);
      assert(!/window\.open\('\$\{n\.url/.test(src), `${fn}: no URL is written into an inline script`);
    }
    assert(!/src="\$\{n\.image\}"/.test(script), 'pictures go through safeHttpUrl');
    const sent = body('loadSentimentPanel');
    assert(/escapeHtml\(h\.title/.test(sent) && /safeHttpUrl\(h\.url\)/.test(sent) && /escapeHtml\(h\.source\)/.test(sent), 'sentiment headlines');
    const spikes = body('loadSpikeExplorer');
    assert(/escapeHtml\(sp\.reason/.test(spikes) && /safeHttpUrl\(sp\.reason_url\)/.test(spikes) && /escapeHtml\(sp\.driver\)/.test(spikes), 'price-spike headlines');
  });

  done();
})();
