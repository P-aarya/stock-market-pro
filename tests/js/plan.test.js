// Subscription plans on the dashboard: which parts are padlocked, that pressing a locked tab explains instead of opening it,
// that the page doesn't ask the server for what the plan leaves out, and that every tag in the markup names a real feature.
const assert = require('assert');
const fs = require('fs'), path = require('path');
const { html, between, suite, vm } = require('./harness');
const { t, done } = suite('Subscription plan (dashboard.html)');

const planCode = between('// ── Subscription plan ──', "document.addEventListener('DOMContentLoaded',applyPlan);") + "document.addEventListener('DOMContentLoaded',applyPlan);";
const tiersSource = fs.readFileSync(path.join(__dirname, '..', '..', 'tiers.py'), 'utf8');
const FEATURES = [...tiersSource.slice(tiersSource.indexOf('FEATURES = {'), tiersSource.indexOf('}', tiersSource.indexOf('FEATURES = {'))).matchAll(/^\s+"([a-z_]+)":/gm)].map(m => m[1]);

/** A bare-bones element: just what the plan code touches. */
function el(tag = 'div', data = {}) {
  const classes = new Set();
  return { tag, dataset: { ...data }, kids: [], attrs: {}, listeners: {}, textContent: '', innerHTML: '', style: { props: {}, setProperty(k, v, p) { this.props[k] = [v, p]; } },
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c), toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), has: c => classes.has(c) },
    setAttribute(k, v) { this.attrs[k] = v; }, append(...n) { this.kids.push(...n); }, appendChild(n) { this.kids.push(n); }, addEventListener(ev, fn) { this.listeners[ev] = fn; },
    focus() { this.focused = true; } };
}

/** The plan code running against a fake page. `features` null means the page had no plan information at all. */
function plan({ features, locks = {}, name = 'Basic', tagged = [], hidden = [] }) {
  const metas = { 'smp-plan': name, 'smp-locks': JSON.stringify(locks) };
  if (features) metas['smp-features'] = features.join(',');
  const registry = { 'up-modal': el(), 'up-text': el(), 'up-ok': el() }, handlers = {}, body = el('body');
  const document = {
    querySelector: sel => { const m = sel.match(/meta\[name="([^"]+)"\]/); return m && metas[m[1]] !== undefined ? { content: metas[m[1]] } : null; },
    querySelectorAll: sel => (sel === '[data-feature]' ? tagged : hidden), getElementById: id => registry[id] || null,
    addEventListener: (ev, fn) => { handlers[ev] = fn; }, createElement: tag => el(tag), body, activeElement: null };
  const ctx = { document, console };
  vm.createContext(ctx); vm.runInContext(planCode, ctx);
  return { ctx, registry, handlers, body, document, run: s => vm.runInContext(s, ctx) };
}

(async () => {
  await t('with no plan information nothing is hidden; with a list, only what is on it is allowed', () => {
    assert.strictEqual(plan({ features: null }).run("can('portfolio')"), true);          // the server decides what data comes back
    const p = plan({ features: ['prices', 'market'] });
    assert.strictEqual(p.run("can('market')"), true);
    assert.strictEqual(p.run("can('portfolio')"), false);
    assert.strictEqual(p.run("can('')"), true); assert.strictEqual(p.run('can(undefined)'), true);          // nothing asked for, nothing to refuse
  });

  await t('a locked card is covered by a padlock that names the feature and the plan that has it', () => {
    const card = el('div', { feature: 'portfolio' });
    const p = plan({ features: ['market'], locks: { portfolio: { label: 'Portfolio and transactions', plan: null } }, tagged: [card] });
    p.handlers.DOMContentLoaded();
    assert(card.classList.has('is-locked'));
    const cover = card.kids[0];
    assert.strictEqual(cover.className, 'lockcover'); assert.strictEqual(cover.type, 'button');
    assert.deepStrictEqual(cover.kids.map(k => k.textContent), ['Portfolio and transactions', 'Included in higher plans']);
    const named = plan({ features: ['market'], locks: { news: { label: 'News and sentiment', plan: 'Pro' } }, tagged: [el('div', { feature: 'news' })] });
    assert.strictEqual(named.run("lockText('news').where"), 'Included in the Pro plan');
  });

  await t('what the plan includes is left alone, and a card is only covered once', () => {
    const open = el('div', { feature: 'market' }), shut = el('div', { feature: 'portfolio' });
    const p = plan({ features: ['market'], tagged: [open, shut] });
    p.handlers.DOMContentLoaded(); p.handlers.DOMContentLoaded();
    assert(!open.classList.has('is-locked')); assert.strictEqual(open.kids.length, 0);
    assert.strictEqual(shut.kids.length, 1);
  });

  await t('tabs only get a padlock mark and a tooltip, not a cover', () => {
    const tab = el('button', { feature: 'portfolio' }); tab.classList.add('ntab');
    const p = plan({ features: ['market'], locks: { portfolio: { label: 'Portfolio and transactions', plan: null } }, tagged: [tab] });
    p.handlers.DOMContentLoaded();
    assert(tab.classList.has('is-locked')); assert.strictEqual(tab.kids.length, 0);
    assert.strictEqual(tab.attrs.title, 'Portfolio and transactions — Included in higher plans');
  });

  await t('the chat button is hidden, not padlocked, on plans without AI interaction, and nothing is hidden by research any more', () => {
    const chat = el('button', { featureHide: 'ai_interaction' }), other = el('button', { featureHide: 'initial_screen' });
    const basic = plan({ features: ['initial_screen', 'portfolio_mgt'], hidden: [chat, other] });
    basic.handlers.DOMContentLoaded();
    assert.deepStrictEqual(chat.style.props.display, ['none', 'important']); assert.strictEqual(other.style.props.display, undefined);
    assert(!basic.body.classList.has('no-research'), 'the stock table is the same on every plan');
    const talk = el('button', { featureHide: 'ai_interaction' });
    const full = plan({ features: ['initial_screen', 'ai_interaction'], hidden: [talk] }); full.handlers.DOMContentLoaded();
    assert.strictEqual(talk.style.props.display, undefined);
  });

  await t('pressing a padlock explains in plain words, Escape or the button closes it, and focus goes back', () => {
    const p = plan({ features: ['market'], locks: { portfolio: { label: 'Portfolio and transactions', plan: null } } });
    const opener = el('button'); p.document.activeElement = opener;
    p.run("showUpgrade('portfolio')");
    assert(p.registry['up-modal'].classList.has('open')); assert(p.registry['up-ok'].focused);
    assert.strictEqual(p.registry['up-text'].textContent, 'Portfolio and transactions isn’t part of your Basic plan. Included in higher plans.');
    p.handlers.keydown({ key: 'Enter' }); assert(p.registry['up-modal'].classList.has('open'));
    p.handlers.keydown({ key: 'Escape' });
    assert(!p.registry['up-modal'].classList.has('open')); assert(opener.focused);
    p.run("showUpgrade('portfolio')"); p.run('closeUpgrade()'); assert(!p.registry['up-modal'].classList.has('open'));
  });

  await t('pressing a locked tab shows the message and opens nothing; an allowed tab opens as before', () => {
    for (const [fn, start, end, args] of [['showPage', 'function showPage(n,btn){', 'function showSub', "'portfolio'"], ['showFST', 'function showFST(t,btn){', 'async function loadDividends', "'news'"]]) {
      const src = between(start, end), shown = [], touched = [];
      const ctx = { can: f => f === 'market', showUpgrade: f => shown.push(f), document: { querySelectorAll: () => { touched.push('qsa'); return []; }, getElementById: () => { touched.push('gid'); return { classList: { add() {}, remove() {} } }; } } };
      vm.createContext(ctx); vm.runInContext(src, ctx);
      ctx[fn](eval(args), { dataset: { feature: 'portfolio' }, classList: { add() {}, remove() {} } });
      assert.deepStrictEqual(shown, ['portfolio'], fn); assert.deepStrictEqual(touched, [], fn + ' must not switch anything');
      shown.length = 0;
      const ok = { dataset: { feature: 'market' }, classList: { add() {}, remove() {} } };
      try { ctx[fn](eval(args), ok); } catch (e) { /* the rest of the function needs a real page */ }
      assert.deepStrictEqual(shown, [], fn + ' (allowed)'); assert(touched.length > 0, fn + ' (allowed) should carry on');
    }
  });

  await t('the page asks only for what the plan includes when it starts: the currency card, the IPO list and the top-5 predictions are the optional loads', () => {
    const src = between('async function init(){', 'initStockSuggest();');
    const run = (allowed) => {
      const calls = [], stub = n => () => { calls.push(n); return Promise.resolve(); };
      const ctx = { can: f => allowed.has(f) };
      for (const n of ['loadIPOs', 'ecoInitMonth', 'loadTop5', 'loadSummary', 'loadIndices', 'loadSentiment', 'loadSectors', 'loadGL', 'loadEarnings', 'loadMarketNews', 'loadStocks', 'loadCurrencies']) ctx[n] = stub(n);
      vm.createContext(ctx); vm.runInContext(src.replace(/\/\/.*$/gm, ''), ctx);
      return ctx.init().then(() => calls.sort());
    };
    return Promise.all([run(new Set(['initial_screen', 'portfolio_view'])), run(new Set(['initial_screen', 'market'])), run(new Set(['initial_screen', 'market', 'stock_prediction'])),
                        run(new Set(['initial_screen', 'market', 'pre_ipo'])), run(new Set(FEATURES))]).then(([free, basic, predicting, ipos, all]) => {
      assert.deepStrictEqual(free, ['ecoInitMonth', 'loadEarnings', 'loadGL', 'loadIndices', 'loadMarketNews', 'loadSectors', 'loadSentiment', 'loadStocks', 'loadSummary'],
        'a Free account loads everything the Home tab is drawn from, but not the Market tab\'s own currency card');
      assert.deepStrictEqual(basic, [...free, 'loadCurrencies'].sort());
      assert.deepStrictEqual(predicting, [...basic, 'loadTop5'].sort()); assert.deepStrictEqual(ipos, [...basic, 'loadIPOs'].sort());
      assert.strictEqual(all.length, 12);
    });
  });

  await t('alerts and notifications run on every plan, the Help Desk poll only where tickets can be raised, and Bounty will not open without AI interaction', () => {
    const timers = between('ntRenderBell();', '// ─── QUARTERLY RESULTS');
    const guard = timers.indexOf("if(can('raise_ticket')){");
    assert(guard > 0 && timers.lastIndexOf('ntPollSupport') > guard, 'the Help Desk poll sits inside the Basic check');
    const others = timers.slice(0, guard);                                              // everything before it: not tied to any plan
    assert(!/can\(/.test(others), 'the alert, news and reminder timers are not tied to a plan');
    for (const timer of ['checkAlertsForNotifications', 'ntPollFeed', 'ntEventReminders']) assert(others.includes(timer), timer);
    const bounty = between('function toggleBounty(){', 'bountyOpen = !bountyOpen;');
    assert(/can\('ai_interaction'\)/.test(bounty) && /showUpgrade\('ai_interaction'\)/.test(bounty));
  });

  await t('a refusal for a feature outside the plan is silent, but real errors are still reported', async () => {
    const src = between('async function api(url){', '\n\n// ── Subscription plan ──');
    const run = async (status, body) => {
      const errors = [], ctx = { API: '', console: { error: (...a) => errors.push(a) }, signedOut: () => false,
        fetch: async () => ({ status, ok: status >= 200 && status < 300, json: async () => body }) };
      vm.createContext(ctx); vm.runInContext(src, ctx);
      return { result: await ctx.api('/x'), errors };
    };
    assert.deepStrictEqual(await run(200, { a: 1 }), { result: { a: 1 }, errors: [] });
    const refused = await run(403, { code: 'upgrade_required' }); assert.strictEqual(refused.result, null); assert.strictEqual(refused.errors.length, 0);
    const broken = await run(500, {}); assert.strictEqual(broken.result, null); assert.strictEqual(broken.errors.length, 1);
  });

  await t('the Home tab\'s Commodities card is padlocked on plans without Commodities, and the page does not ask for prices it would be refused', async () => {
    assert(html.includes('<div class="card hm-half" data-feature="commodities">\n    <div class="chdr"><div class="ctitle">Commodities</div>'), 'the Home card is tagged like the Commodities tab');
    const src = between('async function loadHomeCommodities(){', '// ── News: the five latest headlines');
    const run = async (allowed) => {
      const calls = [], row = { innerHTML: '', querySelector: () => null };
      const ctx = { can: () => allowed, document: { getElementById: () => row }, api: async u => { calls.push(u); return []; }, homeComAt: 0, Date, Array, HOME_COMS: [], rowArrows() {}, f2: x => x, fp: x => x, escapeHtml: x => x };
      vm.createContext(ctx); vm.runInContext(src, ctx);
      await ctx.loadHomeCommodities(); return calls;
    };
    assert.deepStrictEqual(await run(false), [], 'a plan without Commodities asks for nothing');
    assert.deepStrictEqual(await run(true), ['/api/market/commodities']);
  });

  await t('every tag in the markup names a feature the server knows, and every plan feature is used somewhere on the page', () => {
    assert.deepStrictEqual(FEATURES, ['initial_screen', 'portfolio_view', 'market', 'portfolio_mgt', 'raise_ticket', 'stock_prediction', 'ai_stock_finder', 'peer_comparison',
                                       'ai_interaction', 'learn', 'commodities', 'pre_ipo', 'insider_dealings', 'filings'], 'the rows of the plan table, in its order');
    const tags = [...html.matchAll(/data-feature(?:-hide)?="([^"]+)"/g)].map(m => m[1]);
    const checks = [...html.matchAll(/\bcan\('([a-z_]+)'\)/g)].map(m => m[1]);
    assert(tags.length >= 14, 'the page is tagged');
    for (const f of [...tags, ...checks]) assert(FEATURES.includes(f), `"${f}" is not a feature in tiers.py`);
    for (const f of FEATURES.filter(f => f !== 'initial_screen')) assert(tags.includes(f) || checks.includes(f), `${f} is a plan feature the page never mentions`);
    assert(!tags.includes('initial_screen'), 'the Home tab is what every account has: nothing on it is padlocked');
  });

  await t('the right parts are tagged: the tabs, the stock-page tabs, the AI and prediction boxes, the portfolio forms and the Bounty chat', () => {
    const tagOf = re => { const m = html.match(re); assert(m, String(re)); return m[1]; };
    assert.strictEqual(tagOf(/data-feature="([a-z_]+)" onclick="showPage\('ipos',this\)"/), 'pre_ipo');
    assert.strictEqual(tagOf(/data-feature="([a-z_]+)" onclick="showPage\('commodities',this\)"/), 'commodities');
    assert.strictEqual(tagOf(/data-feature="([a-z_]+)" onclick="showPage\('portfolio',this\)"/), 'portfolio_view');
    assert.strictEqual(tagOf(/data-feature="([a-z_]+)" onclick="showPage\('transactions',this\)"/), 'portfolio_view');
    assert.strictEqual(tagOf(/data-feature="([a-z_]+)" onclick="showPage\('market',this\)"/), 'market');
    assert.strictEqual(tagOf(/data-feature="([a-z_]+)" onclick="showPage\('learn',this\)"/), 'learn');
    for (const open of ['home', 'watchlist']) assert(!new RegExp(`class="ntab[^"]*"[^>]*data-feature[^>]*showPage\\('${open}'`).test(html), `${open} is open to every account`);
    const stockTabs = { intelligence: 'stock_prediction', peers: 'peer_comparison', insiders: 'insider_dealings', filings: 'filings' };
    for (const [tab, feature] of Object.entries(stockTabs)) assert.strictEqual(tagOf(new RegExp(`class="fstab" data-feature="([a-z_]+)" onclick="showFST\\('${tab}',this\\)"`)), feature, tab);
    for (const open of ['overview', 'charts', 'company', 'financials', 'earnings', 'dividends', 'news'])
      assert(!new RegExp(`class="fstab[^"]*"[^>]*data-feature[^>]*showFST\\('${open}'`).test(html), `${open} stays open to every account`);
    const cards = { 'ai-wrap': 'ai_stock_finder', 'top5-card': 'stock_prediction' };
    for (const [id, feature] of Object.entries(cards)) assert(new RegExp(`(class="${id}"|id="${id}")[^>]*data-feature="${feature}"|data-feature="${feature}"[^>]*(class="${id}"|id="${id}")`).test(html), id);
    assert(html.includes('<div class="ai-wrap" data-feature="ai_interaction" style="margin-bottom:16px;">\n    <div class="ai-header">\n      <div>\n        <div class="ai-title">AI Portfolio Review</div>'), 'the AI portfolio review is AI interaction');
    for (const id of ['pf-form-card', 'tx-form-card']) assert(html.includes(`<div class="card" id="${id}" data-feature="portfolio_mgt">`), id);
    for (const id of ['bounty-btn', 'bounty-win']) assert(new RegExp(`id="${id}"[^>]*data-feature-hide="ai_interaction"|data-feature-hide="ai_interaction"[^>]*id="${id}"`).test(html), id);
    // the Market page's other cards, the stock detail cards, the alert box and the bell are all initial screen
    for (const title of ['Market Sentiment', 'RSI Distribution', 'P/E Ratio vs Market Cap', '52-Week High/Low Proximity', 'Most Volatile Stocks', 'Top Dividend Yield Leaders', 'Volume Spikes', 'Technical Indicators', 'Fundamentals', 'Ownership & Short Interest', 'Dividend Info', 'Set Price Alert', 'RSI (14-day)', 'MACD'])
      assert(!new RegExp(`data-feature="[a-z_]+"[^>]*>\\s*<div class="chdr">\\s*<div class="ctitle">${title.replace(/[/.()&]/g, '\\$&')}`).test(html), `${title} is not padlocked`);
    assert(!/id="nt-bell"[^>]*data-feature/.test(html) && !/data-feature[^>]*id="nt-bell"/.test(html), 'the bell is not hidden on any plan');
  });

  await t('nothing about the stock table or the stock page is tucked away any more: no research class is left behind', () => {
    assert(!html.includes('col-research') && !html.includes('no-research'));
    const header = between('<thead><tr>\n        <th onclick="srt(\'ticker\')">', '<tbody id="tb-stocks">');
    assert.strictEqual((header.match(/<th/g) || []).length, 14, 'every column is still there');
    assert(html.includes('<select class="sel" id="s-rat"'), 'the rating filter is unchanged');
    assert(html.includes('<span id="fs-badges"></span>'));
  });

  done();
})();
