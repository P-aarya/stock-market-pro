// The phone layout is mostly CSS, so these tests read dashboard.html and check the promises the layout depends on:
// the viewport/app tags, the bottom tab bar, a mobile override for every fixed grid, 16px form fields, and the
// two small bits of script (new tab starts at the top, the stock view keeps its chosen tab in sight).
const assert = require('assert');
const { html, between, suite, vm } = require('./harness');
const { t, done } = suite('Phone layout (dashboard.html)');

/** The CSS inside the last `@media(<query>){ ... }` (the phone rules come after the older ones), found by matching braces. */
function mediaBlock(query) {
  const start = html.lastIndexOf(`@media(${query}){`);
  if (start < 0) throw new Error(`No @media(${query}) block in dashboard.html`);
  let depth = 0;
  for (let i = html.indexOf('{', start); i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}' && --depth === 0) return html.slice(start, i + 1);
  }
  throw new Error(`Unbalanced braces after @media(${query})`);
}
const tablet = mediaBlock('max-width:900px'), phone = mediaBlock('max-width:700px');

(async () => {
  await t('the viewport fits the device width, uses the full screen, and never blocks pinch-zoom', () => {
    const meta = html.match(/<meta name="viewport" content="([^"]+)"/)[1];
    assert(/width=device-width/.test(meta) && /initial-scale=1(\.0)?/.test(meta));
    assert(/viewport-fit=cover/.test(meta), 'content should extend under the notch (safe-area padding handles the rest)');
    assert(!/user-scalable=no|maximum-scale/.test(meta), 'zoom must stay available for accessibility');
  });

  await t('a home-screen app has a manifest, an iOS icon and a theme colour', () => {
    assert(/<link rel="manifest" href="\/manifest\.webmanifest"/.test(html));
    assert(/<link rel="apple-touch-icon" href="\/apple-touch-icon\.png"/.test(html));
    assert(/<meta name="theme-color" content="#[0-9a-f]{6}"/i.test(html));
    assert(/<meta name="apple-mobile-web-app-capable" content="yes"/.test(html));
  });

  await t('all six tabs have an icon and a label, so the bottom bar can stack them', () => {
    const tabs = [...html.matchAll(/<button class="ntab[^"]*"[^>]*>(.*?)<\/button>/g)].map(m => m[1]);
    assert.strictEqual(tabs.length, 6);
    for (const inner of tabs) assert(/<span class="ico">.+<\/span><span class="lbl"/.test(inner), inner);
  });

  await t('the long tab names have a short form for narrow phones, and the full text is kept', () => {
    for (const [full, short] of [['Commodities', 'Commod.'], ['Transactions', 'Trades']])
      assert(html.includes(`<span class="lbl" data-short="${short}">${full}</span>`), full);
    assert(/\.ntab \.lbl\[data-short\]::after\{content:attr\(data-short\)/.test(html));
  });

  await t('the desktop top bar never wraps: it drops items in steps as the window narrows', () => {
    assert(/\.ntab,\.brand-title,\.nuser,\.nsignout,\.ntime,#nconn\{white-space:nowrap;\}/.test(html));
    const steps = [['1500px', /#nconn\{display:none/], ['1400px', /\.nuser\{display:none/], ['1200px', /\.ntime\{display:none/], ['1130px', /\.ntab\{padding:7px 12px/], ['1000px', /\.lpill\{display:none/]];
    for (const [width, rule] of steps) assert(rule.test(mediaBlock(`max-width:${width}`)), `@media(max-width:${width})`);
  });

  await t('on a phone the tabs sit in a fixed bottom bar that respects the home indicator', () => {
    assert(/\.ntabs\{position:fixed;left:0;right:0;bottom:0/.test(tablet));
    assert(/\.ntabs\{[^}]*safe-area-inset-bottom/.test(tablet));
    assert(/\.page\{padding:[^}]*safe-area-inset-bottom/.test(tablet), 'pages need room above the bar');
    assert(/#bounty-btn\{[^}]*bottom:calc\(78px/.test(tablet), 'the chat button must clear the bar');
  });

  await t('every fixed grid in the markup is collapsed on a phone', () => {
    // Only grids written inline (style="...") need an override here; the stylesheet's own grids are handled by the media queries.
    const found = new Set([...html.matchAll(/style="[^"]*?grid-template-columns:\s*((?:repeat\(\d+,\s*1fr\))|(?:1fr 1fr))/g)].map(m => m[1].replace(/,\s+/g, ',')));
    // These are the fixed layouts that exist today. A new one fails here until someone decides how it should look on a phone.
    assert.deepStrictEqual([...found].sort(), ['1fr 1fr', 'repeat(3,1fr)', 'repeat(4,1fr)', 'repeat(6,1fr)'].sort());
    assert(/#currency-grid,#macro-grid\{grid-template-columns:repeat\(2,1fr\)!important/.test(phone));
    assert(/#heatmap-wrap\{grid-template-columns:repeat\(3,1fr\)!important/.test(phone));
    assert(/\[style\*="repeat\(3,1fr\)"\]\{grid-template-columns:repeat\(2,1fr\)!important/.test(phone));
    assert(/\[style\*="grid-template-columns:1fr 1fr"\]\{grid-template-columns:1fr!important/.test(phone));
    assert(/#currency-grid\{grid-template-columns:repeat\(3,1fr\)!important/.test(tablet), 'six across overflows a tablet');
    assert(/\.acgrid,\.ai-stocks-grid\{grid-template-columns:minmax\(0,1fr\)/.test(mediaBlock('max-width:400px')), 'one card per row on a very small phone');
    for (const id of ['currency-grid', 'macro-grid', 'heatmap-wrap']) assert(html.includes(`id="${id}"`), id);
  });

  await t('form fields use 16px text on a phone (smaller makes iOS zoom in) and fixed widths give way', () => {
    assert(/input,select,textarea\{font-size:16px!important/.test(phone));
    assert(/\.srch\[style\*="width"\]\{width:100%!important/.test(phone));
    assert(/\.ai-input\{min-width:0/.test(phone) && /#ai-stock-select\{min-width:0!important/.test(phone));
  });

  await t('the stock view and its tabs fit a phone: tabs scroll sideways, header wraps, body keeps clear of the notch', () => {
    assert(/\.fstabs\{flex-wrap:nowrap;overflow-x:auto/.test(phone));
    assert(/\.fstab\{flex:0 0 auto;white-space:nowrap/.test(phone));
    assert(/\.fsnav\{height:auto/.test(phone) && /\.fsnav\{[^}]*safe-area-inset-top/.test(phone));
    assert(/\.fsbody\{padding:[^}]*safe-area-inset-bottom/.test(phone));
  });

  await t('tables scroll sideways with the first column pinned, and the peer group rows keep their own shade', () => {
    assert(/\.tw table td:first-child\{position:sticky;left:0/.test(phone));
    assert(/\.peer-table \.peer-grp td:first-child\{background:var\(--bg3\)/.test(phone));
  });

  await t('Bounty opens as a full-screen sheet on a phone, and hides its round button while open', () => {
    assert(/#bounty-win\{top:0;left:0;right:0;bottom:0;width:100%;height:100%/.test(phone));
    assert(/body:has\(#bounty-win\.open\) #bounty-btn\{display:none/.test(phone));
    assert(/#bounty-hdr\{[^}]*safe-area-inset-top/.test(phone));
    assert(/#bounty-input-wrap\{[^}]*safe-area-inset-bottom/.test(phone));
  });

  await t('touch screens get what used to appear only on hover (message actions) and no stuck hover colours', () => {
    const touch = mediaBlock('hover:none');
    assert(/\.bm-actions\{display:flex/.test(touch));
    assert(/\.fbtn:not\(\.on\):hover/.test(touch) && /\.ntab:not\(\.on\):hover/.test(touch));
  });

  await t('showPage starts each tab at the top, and still works where there is no window', () => {
    const src = between('function showPage(n,btn){', 'function showSub');
    const make = (win) => {
      const calls = [], mk = () => ({ classList: { add() {}, remove() {} } });
      const ctx = { document: { querySelectorAll: () => [mk()], getElementById: () => mk() }, loadPortfolio() { calls.push('pf'); }, loadTx() {}, loadCommodities() {}, loadMacro() {}, loadComCo() {}, loadWatchlist() {}, loadIPOs() {}, ipoFetchedAt: 1, IPO_REFRESH_MS: 1e12, Date };
      if (win) ctx.window = win;
      vm.createContext(ctx); vm.runInContext(src, ctx);
      return { ctx, calls };
    };
    const scrolled = [];
    const a = make({ scrollTo: (x, y) => scrolled.push([x, y]) });
    a.ctx.showPage('portfolio', mk0());
    assert.deepStrictEqual(scrolled, [[0, 0]]); assert.deepStrictEqual(a.calls, ['pf']);
    const b = make(null);                                        // no window at all: must not throw
    b.ctx.showPage('portfolio', mk0()); assert.deepStrictEqual(b.calls, ['pf']);
    function mk0() { return { classList: { add() {}, remove() {} } }; }
  });

  await t('the stock view keeps the chosen tab in sight, and tolerates a button that cannot scroll', () => {
    const src = between('function showFST(t,btn){', 'async function loadCompanyTab');
    assert(/scrollIntoView\(\{inline:'center',block:'nearest'/.test(src));
    const seen = [], el = () => ({ classList: { add() {}, remove() {} } });
    const ctx = { document: { querySelectorAll: () => [el()], getElementById: () => el() }, loadPxChart() {}, drawHistCharts() {}, loadPrediction() {}, loadCompanyTab() {}, loadPeers() {}, loadSentiment() {}, loadQuarterly() {} };
    vm.createContext(ctx); vm.runInContext(src, ctx);
    ctx.showFST('overview', { ...el(), scrollIntoView: o => seen.push(o) });
    assert.strictEqual(seen.length, 1); assert.strictEqual(seen[0].inline, 'center');
    ctx.showFST('overview', el());                              // no scrollIntoView on this button: must not throw
  });

  done();
})();
