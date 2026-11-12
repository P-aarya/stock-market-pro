// "View plan" for administrators: the bar on the dashboard (which plan is being looked at, switch plan, exit) and the View plan section
// of the admin page (pick a plan, open the dashboard as it, stop).
const assert = require('assert');
const fs = require('fs'), path = require('path');
const { html, between, suite, ok, vm } = require('./harness');
const { t, done } = suite('View plan (dashboard.html and admin.html)');

const PLANS = [
  { level: 1, name: 'Free', price: 0 }, { level: 2, name: 'Basic', price: 9.99 }, { level: 3, name: 'AI Stock Finder', price: 19.99 },
  { level: 4, name: 'AI Interaction', price: 29.99 }, { level: 5, name: 'Deluxe', price: 39.99 }, { level: 6, name: 'Premier', price: 49.99 },
];

/** A bare-bones element that registers the ids written into its innerHTML and keeps the listeners it is given. */
function makePage(metas) {
  const registry = {};
  class El {
    constructor(tag) { this.tag = tag; this.kids = []; this.listeners = {}; this.attrs = {}; this.value = ''; this.textContent = ''; this._html = ''; this.className = ''; this.hidden = false; this.disabled = false; }
    set id(v) { this._id = v; registry[v] = this; } get id() { return this._id; }
    set innerHTML(h) {
      this._html = h;
      for (const m of String(h).matchAll(/<(\w+)[^>]*\sid="([^"]+)"/g)) { const c = new El(m[1]); c._id = m[2]; registry[m[2]] = c; }
      const sel = registry['pv-select'];
      if (sel && String(h).includes('<option')) { const picked = String(h).match(/<option value="(\d+)" selected/); sel.value = picked ? picked[1] : ''; }
    }
    get innerHTML() { return this._html; }
    setAttribute(k, v) { this.attrs[k] = v; }
    appendChild(c) { this.kids.push(c); if (c.id) registry[c.id] = c; }
    addEventListener(ev, fn) { this.listeners[ev] = fn; }
  }
  const body = new El('body');
  const document = { querySelector: sel => { const m = sel.match(/meta\[name="([^"]+)"\]/); return m && metas[m[1]] !== undefined ? { content: metas[m[1]] } : null; },
    getElementById: id => registry[id] || null, createElement: tag => new El(tag), body };
  return { document, registry, body };
}

/** The dashboard's preview code on a page the server wrote `metas` into; `reply` is what the server answers a plan change with. */
function dashboard(metas, reply = ok({ status: 'success' })) {
  const page = makePage(metas), requests = [], state = { reloads: 0, signedOut: 0 };
  const ctx = {
    console, JSON, Number, Array, String, document: page.document, API: '',
    location: { reload: () => { state.reloads++; } },
    signedOut: r => { if (r.status === 401) { state.signedOut++; return true; } return false; },
    fetch: async (url, opts = {}) => { requests.push({ url, opts, body: opts.body ? JSON.parse(opts.body) : null }); if (reply instanceof Error) throw reply; return reply; },
  };
  vm.createContext(ctx);
  // no helpers from the rest of the page are given: this code runs while the page is still loading, so it may only use what it defines itself
  vm.runInContext(between('// ── Administrators: looking at the app as a plan ──', '// ── Profile page ──') + '\nthis.PREVIEW = PREVIEW; this.setPlanPreview = setPlanPreview;', ctx);
  return { ...page, ctx, requests, state };
}
const previewing = (name = 'Basic', level = 2) => ({ 'smp-preview': name, 'smp-preview-level': String(level), 'smp-plans': JSON.stringify(PLANS) });
const tick = () => new Promise(r => setImmediate(r));

(async () => {
  await t('with nothing in the page about a preview (everyone but a previewing administrator) there is no bar', () => {
    for (const metas of [{}, { 'smp-plan': 'Basic' }, { 'smp-plans': JSON.stringify(PLANS) }]) {
      const d = dashboard(metas);
      assert.strictEqual(d.ctx.PREVIEW.name, ''); assert.strictEqual(d.body.kids.length, 0); assert.strictEqual(d.registry.pvbar, undefined);
    }
  });

  await t('when the server says which plan is being viewed, a bar names it, lists every plan with the current one selected, and has the way back', () => {
    const d = dashboard(previewing('Basic', 2));
    assert.strictEqual(d.body.kids.length, 1);
    const bar = d.registry.pvbar;
    assert.strictEqual(bar.className, 'pvbar'); assert.strictEqual(bar.attrs.role, 'region'); assert.strictEqual(bar.attrs['aria-label'], 'Plan preview');
    assert(/Viewing as <b id="pv-name">Basic<\/b>/.test(bar.innerHTML), bar.innerHTML);
    const options = [...bar.innerHTML.matchAll(/<option value="(\d+)"( selected)?>([^<]*)<\/option>/g)].map(m => [Number(m[1]), !!m[2], m[3]]);
    assert.deepStrictEqual(options.map(o => o[0]), [1, 2, 3, 4, 5, 6]);
    assert.deepStrictEqual(options.filter(o => o[1]).map(o => o[0]), [2]);                       // only the plan being viewed is selected
    assert.strictEqual(options[0][2], 'Free'); assert.strictEqual(options[1][2], 'Basic · $9.99');   // no "Free · Free"
    assert(/<button type="button" class="pv-exit" id="pv-exit">Exit preview<\/button>/.test(bar.innerHTML));
    assert(/<a class="pv-admin" id="pv-admin" href="\/admin#plan">Admin Center<\/a>/.test(bar.innerHTML));
    assert(/<select id="pv-select" aria-label="Plan to view as">/.test(bar.innerHTML));
    assert(/role="alert"/.test(bar.innerHTML));                                                 // a failure is announced
  });

  await t('a plan name from the page is shown as text, never as markup', () => {
    const evil = [{ level: 1, name: '<img src=x onerror=alert(1)>', price: 0 }];
    const d = dashboard({ 'smp-preview': '<b onclick=x>', 'smp-preview-level': '1', 'smp-plans': JSON.stringify(evil) });
    assert(!/<img|<b onclick/.test(d.registry.pvbar.innerHTML), d.registry.pvbar.innerHTML);
  });

  await t('a garbled plan list does not break the page: the bar still has Exit', () => {
    const d = dashboard({ 'smp-preview': 'Basic', 'smp-preview-level': '2', 'smp-plans': '{not json' });
    assert(d.registry.pvbar && /pv-exit/.test(d.registry.pvbar.innerHTML));
    assert.deepStrictEqual(Array.from(d.ctx.PREVIEW.plans), []);
  });

  await t('choosing another plan asks the server (with the header only the app\'s own script sends) and reloads', async () => {
    const d = dashboard(previewing('Basic', 2));
    d.registry['pv-select'].value = '5'; d.registry['pv-select'].listeners.change(); await tick();
    assert.strictEqual(d.requests.length, 1);
    const r = d.requests[0];
    assert.strictEqual(r.url, '/api/admin/preview'); assert.strictEqual(r.opts.method, 'POST');
    assert.strictEqual(r.opts.headers['X-Requested-With'], 'fetch'); assert.strictEqual(r.opts.headers['Content-Type'], 'application/json');
    assert.deepStrictEqual(r.body, { plan: 5 });
    assert.strictEqual(d.state.reloads, 1);                                                      // the page itself is different for each plan
  });

  await t('choosing the plan already being viewed does nothing', async () => {
    const d = dashboard(previewing('Basic', 2));
    d.registry['pv-select'].value = '2'; d.registry['pv-select'].listeners.change(); await tick();
    assert.strictEqual(d.requests.length, 0); assert.strictEqual(d.state.reloads, 0);
  });

  await t('Exit preview sends plan null and reloads, back to the administrator view', async () => {
    const d = dashboard(previewing('Free', 1));
    d.registry['pv-exit'].listeners.click(); await tick();
    assert.deepStrictEqual(d.requests.map(r => r.body), [{ plan: null }]); assert.strictEqual(d.state.reloads, 1);
  });

  await t('if the server refuses, the bar says so, keeps the plan on show and does not reload', async () => {
    const d = dashboard(previewing('Basic', 2), ok({ status: 'error', message: 'no' }, 403));
    d.registry['pv-select'].value = '6'; d.registry['pv-select'].listeners.change(); await tick();
    assert.strictEqual(d.state.reloads, 0);
    assert(/couldn.t switch plans/.test(d.registry['pv-msg'].textContent), d.registry['pv-msg'].textContent);
    assert.strictEqual(d.registry['pv-select'].value, '2');                                      // the box shows the plan that is really being viewed
  });

  await t('if the server cannot be reached the bar says so and does not reload', async () => {
    const d = dashboard(previewing('Basic', 2), new Error('offline'));
    d.registry['pv-exit'].listeners.click(); await tick();
    assert.strictEqual(d.state.reloads, 0); assert(/couldn.t switch plans/.test(d.registry['pv-msg'].textContent));
  });

  await t('a signed-out session is sent to sign in instead of reloading', async () => {
    const d = dashboard(previewing('Basic', 2), ok({}, 401));
    d.registry['pv-exit'].listeners.click(); await tick();
    assert.strictEqual(d.state.signedOut, 1); assert.strictEqual(d.state.reloads, 0);
  });

  await t('the bar sits clear of the phone tab bar and the Bounty button, and is never padlocked or hidden by a plan', () => {
    const css = html.slice(html.indexOf('.pvbar{position:fixed'), html.indexOf('.pvbar{position:fixed') + 900);
    assert(/\.pvbar\{position:fixed;left:16px;bottom:16px;z-index:350;/.test(css));            // above the tab bar (300), below every modal
    const at = html.indexOf('.pvbar{left:calc(10px');
    assert(at > 0 && html.slice(html.lastIndexOf('@media(', at), at).startsWith('@media(max-width:960px)'), 'the phone rule sits in the phone block');
    assert(/\.pvbar\{left:calc\(10px[^}]*right:calc\(76px[^}]*bottom:calc\(68px/.test(html.slice(at, at + 300)), 'a phone keeps the bar above the tab bar and beside the Bounty button');
    assert(!/id="pvbar"[^>]*data-feature/.test(html) && !/pv-select[^>]*data-feature/.test(html));
  });

  // ───── the admin page ─────
  const adminHtml = fs.readFileSync(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');
  const script = adminHtml.slice(adminHtml.indexOf('<script>') + 8, adminHtml.lastIndexOf('</script>'));
  const from = (a, b) => { const i = script.indexOf(a), j = script.indexOf(b, i); assert(i >= 0 && j > i, a); return script.slice(i, j); };
  const helpers = from('function escapeHtml', 'function ago(') + from('let toastTimer;', 'async function load(quiet)');
  const previewCode = from('// ── View plan: look at the dashboard', '// ── Learn tab editor ──');
  const INCLUDED = id => ({ 1: ['initial_screen', 'portfolio_view'], 2: ['initial_screen', 'portfolio_view', 'market', 'portfolio_mgt', 'raise_ticket'] }[id] || ['initial_screen']);
  const LABEL = { initial_screen: 'Home tab', portfolio_view: 'Portfolio visibility', market: 'Market tab', portfolio_mgt: 'Portfolio management', raise_ticket: 'Raising ticket issues' };
  const catalog = PLANS.map(p => ({ ...p, included: INCLUDED(p.level).map(k => ({ key: k, label: LABEL[k], description: `about ${k}` })) }));

  /** The admin page's View plan code with a fake page and server. */
  function adminPage({ current = null, replies = {} } = {}) {
    const els = {}, toasts = [], requests = [], nav = { href: '' };
    // like a real <select>: writing its options makes the one marked selected (else the first) its value
    const makeEl = id => {
      const e = { id, textContent: '', className: '', value: '', hidden: false, disabled: false, _html: '' };
      Object.defineProperty(e, 'innerHTML', { get() { return this._html; }, set(h) {
        this._html = h; const opts = [...String(h).matchAll(/<option value="(\d+)"( selected)?/g)];
        if (opts.length) this.value = (opts.find(m => m[2]) || opts[0])[1];
      } });
      return e;
    };
    const el = id => els[id] || (els[id] = makeEl(id));
    const ctx = {
      console, JSON, Number, Array, String, Promise, Error, Set, Date, Math,
      document: { getElementById: el }, location: nav, setTimeout: () => 0, clearTimeout: () => {},
      fetch: async (url, opts = {}) => {
        const key = `${opts.method || 'GET'} ${url}`; requests.push({ key, body: opts.body ? JSON.parse(opts.body) : null, headers: opts.headers });
        if (replies[key] instanceof Error) throw replies[key];
        return replies[key] || ok({ status: 'success', plans: catalog, current });
      },
    };
    vm.createContext(ctx);
    vm.runInContext(helpers + previewCode + '\nthis.api = {preview, pvPicked, renderPreview, loadPreview, openPreview, stopPreview};', ctx);
    el('pv-plan').value = ''; el('pv-stop'); el('pv-open');
    return { ctx, api: ctx.api, els, el, requests, nav, toast: () => els.toast };
  }

  await t('the View plan section lists every plan, and with no preview on it offers the first, hides Stop and says you see everything', async () => {
    const a = adminPage(); await a.api.loadPreview();
    const options = [...a.els['pv-plan'].innerHTML.matchAll(/<option value="(\d+)"( selected)?>([^<]*)<\/option>/g)];
    assert.deepStrictEqual(options.map(m => m[3]), ['Free', 'Basic · $9.99 a month', 'AI Stock Finder · $19.99 a month', 'AI Interaction · $29.99 a month', 'Deluxe · $39.99 a month', 'Premier · $49.99 a month']);
    assert.deepStrictEqual(options.filter(m => m[2]).map(m => m[1]), ['1']);
    assert.strictEqual(a.els['pv-stop'].hidden, true);
    assert(/administrator/.test(a.els['pv-state'].textContent), a.els['pv-state'].textContent);
  });

  await t('with a plan being viewed it is the one selected, Stop is offered and the message names it', async () => {
    const a = adminPage({ current: PLANS[2] }); await a.api.loadPreview();
    assert.deepStrictEqual([...a.els['pv-plan'].innerHTML.matchAll(/<option value="(\d+)" selected/g)].map(m => m[1]), ['3']);
    assert.strictEqual(a.els['pv-stop'].hidden, false);
    assert(/AI Stock Finder plan/.test(a.els['pv-state'].textContent), a.els['pv-state'].textContent);
  });

  await t('the list says what the chosen plan includes, from the server\'s own labels, and follows the choice', async () => {
    const a = adminPage(); await a.api.loadPreview();
    assert.deepStrictEqual([...a.els['pv-included'].innerHTML.matchAll(/<li[^>]*>([^<]*)<\/li>/g)].map(m => m[1]), ['Home tab', 'Portfolio visibility']);
    a.els['pv-plan'].value = '2'; a.api.renderPreview(true);
    assert.deepStrictEqual([...a.els['pv-included'].innerHTML.matchAll(/<li[^>]*>([^<]*)<\/li>/g)].map(m => m[1]), ['Home tab', 'Portfolio visibility', 'Market tab', 'Portfolio management', 'Raising ticket issues']);
    assert.deepStrictEqual([...a.els['pv-plan'].innerHTML.matchAll(/<option value="(\d+)" selected/g)].map(m => m[1]), ['2']);      // the choice is kept
  });

  await t('"View dashboard as this plan" sends the plan with the admin header and opens the dashboard', async () => {
    const a = adminPage(); await a.api.loadPreview();
    a.els['pv-plan'].value = '2'; a.api.renderPreview(true);
    await a.api.openPreview();
    const post = a.requests.find(r => r.key === 'POST /api/admin/preview');
    assert.deepStrictEqual(post.body, { plan: 2 }); assert.strictEqual(post.headers['X-Requested-With'], 'fetch');
    assert.strictEqual(a.nav.href, '/');
  });

  await t('if the server refuses, the admin page says so, stays put, and the button works again', async () => {
    const a = adminPage({ replies: { 'POST /api/admin/preview': ok({ status: 'error', message: 'That isn\'t one of the plans.' }, 400) } });
    await a.api.loadPreview(); a.els['pv-plan'].value = '2'; a.api.renderPreview(true);
    await a.api.openPreview();
    assert.strictEqual(a.nav.href, ''); assert.strictEqual(a.els['pv-state'].textContent, 'That isn\'t one of the plans.'); assert.strictEqual(a.els['pv-state'].className, 'pv-state err');
    assert.strictEqual(a.els['pv-open'].disabled, false);
    assert(/err/.test(a.els.toast.className));
  });

  await t('Stop viewing as a plan sends null, hides itself and stays on the admin page', async () => {
    const a = adminPage({ current: PLANS[0] }); await a.api.loadPreview();
    assert.strictEqual(a.els['pv-stop'].hidden, false);
    await a.api.stopPreview();
    assert.deepStrictEqual(a.requests.find(r => r.key === 'POST /api/admin/preview').body, { plan: null });
    assert.strictEqual(a.els['pv-stop'].hidden, true); assert.strictEqual(a.api.preview.current, null); assert.strictEqual(a.nav.href, '');
    assert(/administrator/.test(a.els['pv-state'].textContent));
  });

  await t('a failed load says so instead of showing an empty list', async () => {
    const a = adminPage({ replies: { 'GET /api/admin/preview': new Error('offline') } });
    await a.api.loadPreview();
    assert(/Could not reach the server/.test(a.els['pv-state'].textContent), a.els['pv-state'].textContent); assert.strictEqual(a.els['pv-state'].className, 'pv-state err');
  });

  await t('plan names and feature labels are shown as text, never as markup', async () => {
    const evil = [{ level: 1, name: '<img src=x onerror=1>', price: 0, included: [{ key: 'k', label: '<script>x</script>', description: '"><b>' }] }];
    const a = adminPage({ replies: { 'GET /api/admin/preview': ok({ status: 'success', plans: evil, current: null }) } });
    await a.api.loadPreview();
    assert(!/<img|<script|<b>/.test(a.els['pv-plan'].innerHTML + a.els['pv-included'].innerHTML), a.els['pv-plan'].innerHTML + a.els['pv-included'].innerHTML);
  });

  await t('the page has a View plan button in its navigation and understands the #plan address the dashboard bar links to', () => {
    assert(/<button class="view-btn" type="button" data-view="plan">View plan<\/button>/.test(adminHtml));
    assert(/<section class="view" id="view-plan"/.test(adminHtml));
    const route = (hash) => { const ctx = { location: { hash } }; vm.createContext(ctx); vm.runInContext(from('function viewFromHash(){', 'function route(){') + '\nthis.v = viewFromHash();', ctx); return ctx.v.view; };
    assert.strictEqual(route('#plan'), 'plan'); assert.strictEqual(route('#users'), 'users'); assert.strictEqual(route('#nonsense'), 'overview'); assert.strictEqual(route(''), 'overview');
    assert(/else if\(view === 'plan'\) loadPreview\(\);/.test(script));
  });

  done();
})();
