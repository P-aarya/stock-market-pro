// The admin page (admin.html) and the Admin link in the dashboard's top bar.
const assert = require('assert');
const fs = require('fs'), path = require('path');
const { html, between, suite, ok, vm } = require('./harness');
const { t, done } = suite('Admin page and link');

const adminHtml = fs.readFileSync(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');
const script = adminHtml.slice(adminHtml.indexOf('<script>') + 8, adminHtml.lastIndexOf('</script>'));
const slice = (start, end) => { const a = script.indexOf(start); assert(a >= 0, start); const b = script.indexOf(end, a); assert(b > a, end); return script.slice(a, b); };
// Everything up to the first line that touches the page: the state, formatting and the functions that talk to the server.
const logic = script.slice(0, script.indexOf("document.getElementById('list').addEventListener"));

const USERS = [
  { id: 4, email: 'blocked@x.co', status: 'blocked', is_admin: false, created_at: 1000, last_login_at: null },
  { id: 3, email: 'Member@X.co', status: 'approved', is_admin: false, created_at: 1000, last_login_at: 2000 },
  { id: 2, email: 'waiting@x.co', status: 'pending', is_admin: false, created_at: 1000, last_login_at: null },
  { id: 1, email: 'boss@x.co', status: 'approved', is_admin: true, created_at: 1000, last_login_at: 2000 },
];

/** The admin page's script with a fake page and server. `replies` maps "METHOD path" to a response. */
function pageEnv({ replies = {}, confirmAnswer = true } = {}) {
  const els = {}, toasts = [], requests = [], confirms = [];
  const el = (id) => els[id] || (els[id] = { id, innerHTML: '', textContent: '', className: '' });
  const ctx = {
    console, JSON, Math, Date, Number, String, Set, Promise, Error, Intl,
    document: { getElementById: el, title: '' }, confirm: m => { confirms.push(m); return confirmAnswer; },
    location: { href: '' }, setTimeout: () => 0, clearTimeout: () => {},
    fetch: async (url, opts = {}) => {
      const key = `${opts.method || 'GET'} ${url}`; requests.push({ key, opts });
      const r = replies[key]; if (r instanceof Error) throw r;
      return r || ok({ status: 'success', users: USERS, counts: { pending: 1, approved: 2, blocked: 1, total: 4 }, you: 1 });
    },
  };
  vm.createContext(ctx);
  vm.runInContext(logic + '\nthis.api = {state, escapeHtml, ago, exact, visibleUsers, actionsFor, rowHtml, emptyMessage, render, load, setStatus, call};', ctx);
  return { ctx, api: ctx.api, els, requests, confirms, toasts };
}

(async () => {
  await t('times are read as seconds since 1970, so they are right in any time zone', () => {
    const { api } = pageEnv();
    const now = 1_000_000 * 1000;
    assert.strictEqual(api.ago(1_000_000 - 20, now), 'just now');
    assert.strictEqual(api.ago(1_000_000 - 5 * 60, now), '5 minutes ago');
    assert.strictEqual(api.ago(1_000_000 - 3600, now), '1 hour ago');
    assert.strictEqual(api.ago(1_000_000 - 3 * 86400, now), '3 days ago');
    assert.strictEqual(api.ago(null, now), null); assert.strictEqual(api.ago(0, now), null);
    assert.strictEqual(api.ago(1_000_000 + 500, now), 'just now', 'a clock a little ahead never shows a negative time');
  });

  await t('an email or status containing HTML is shown as text, never run', () => {
    const { api } = pageEnv();
    const evil = { id: 9, email: '"><img src=x onerror=alert(1)>@x.co', status: 'pending"><script>x</script>', is_admin: false, created_at: 1, last_login_at: null };
    const out = api.rowHtml(evil);
    assert(!/<img|<script/i.test(out), out);
    assert(out.includes('&lt;img src=x onerror=alert(1)&gt;@x.co'));
  });

  await t('the buttons offered depend on the status, and an administrator has none', () => {
    const { api } = pageEnv();
    const acts = (u) => [...api.actionsFor(u).matchAll(/data-act="(\w+)"[^>]*>([^<]+)/g)].map(m => m[1] + ':' + m[2]);
    assert.deepStrictEqual(acts(USERS[2]), ['approved:Approve', 'blocked:Decline']);   // pending
    assert.deepStrictEqual(acts(USERS[1]), ['blocked:Block']);                          // approved
    assert.deepStrictEqual(acts(USERS[0]), ['approved:Unblock']);                       // blocked
    assert.deepStrictEqual(acts(USERS[3]), []); assert(api.actionsFor(USERS[3]).includes('Administrator'));
  });

  await t('filters and search narrow the list', async () => {
    const { api } = pageEnv();
    api.state.users = USERS;
    const ids = () => api.visibleUsers().map(u => u.id);
    api.state.filter = 'pending'; assert.deepStrictEqual(ids(), [2]);
    api.state.filter = 'approved'; assert.deepStrictEqual(ids(), [3, 1]);
    api.state.filter = 'all'; assert.deepStrictEqual(ids(), [4, 3, 2, 1]);
    api.state.query = '  MEMBER '; assert.deepStrictEqual(ids(), [3], 'case-insensitive, ignoring stray spaces');
    api.state.query = 'nobody'; assert.deepStrictEqual(ids(), []); assert(/match that search/.test(api.emptyMessage()));
    api.state.query = ''; api.state.filter = 'pending'; api.state.users = []; assert(/caught up/.test(api.emptyMessage()));
  });

  await t('the page opens on the people waiting, or on everyone when nobody is', async () => {
    const waiting = pageEnv(); await waiting.api.load(false);
    assert.strictEqual(waiting.api.state.filter, 'pending');
    const calm = pageEnv({ replies: { 'GET /api/admin/users': ok({ status: 'success', users: [USERS[3]], counts: { pending: 0, approved: 1, blocked: 0, total: 1 }, you: 1 }) } });
    await calm.api.load(false); assert.strictEqual(calm.api.state.filter, 'all');
    await calm.api.load(true);                                  // a refresh keeps whichever tab you are on
    calm.api.state.filter = 'blocked'; await calm.api.load(true); assert.strictEqual(calm.api.state.filter, 'blocked');
  });

  await t('every request carries the header the server insists on, and a signed-out session goes to the login page', async () => {
    const e = pageEnv({ replies: { 'GET /api/admin/users': { ok: false, status: 401, json: async () => ({}) } } });
    await assert.rejects(() => e.api.call('/api/admin/users'), /sign in/);
    assert.strictEqual(e.ctx.location.href, '/login');
    assert.strictEqual(e.requests[0].opts.headers['X-Requested-With'], 'fetch');
    const forbidden = pageEnv({ replies: { 'GET /x': { ok: false, status: 403, json: async () => ({ detail: 'Administrator access required.' }) } } });
    await assert.rejects(() => forbidden.api.call('/x'), /Administrator access required/);
    const down = pageEnv({ replies: { 'GET /x': new Error('network') } });
    await assert.rejects(() => down.api.call('/x'), /Could not reach the server/);
  });

  await t('approving sends the right request, tells you, and refreshes the list', async () => {
    const e = pageEnv(); await e.api.load(false);
    await e.api.setStatus(2, 'approved');
    const post = e.requests.find(r => r.key.startsWith('POST'));
    assert.strictEqual(post.key, 'POST /api/admin/users/2/status'); assert.deepStrictEqual(JSON.parse(post.opts.body), { status: 'approved' });
    assert.strictEqual(post.opts.headers['X-Requested-With'], 'fetch');
    assert.strictEqual(e.confirms.length, 0, 'approving needs no confirmation');
    assert(/waiting@x\.co can now sign in/.test(e.els.toast.textContent));
    assert.strictEqual(e.requests.filter(r => r.key === 'GET /api/admin/users').length, 2, 'the list is reloaded after the change');
    assert.strictEqual(e.api.state.busy.size, 0);
  });

  await t('blocking or declining asks first, and cancelling changes nothing', async () => {
    const no = pageEnv({ confirmAnswer: false }); await no.api.load(false);
    await no.api.setStatus(3, 'blocked');
    assert.strictEqual(no.requests.filter(r => r.key.startsWith('POST')).length, 0); assert(/Block Member@X\.co/.test(no.confirms[0]));
    await no.api.setStatus(2, 'blocked'); assert(/Decline waiting@x\.co/.test(no.confirms[1]));
    const yes = pageEnv(); await yes.api.load(false); await yes.api.setStatus(3, 'blocked');
    assert.strictEqual(yes.requests.filter(r => r.key === 'POST /api/admin/users/3/status').length, 1);
  });

  await t('a refused change shows the server\'s reason and leaves the page usable', async () => {
    const e = pageEnv({ replies: { 'POST /api/admin/users/3/status': { ok: false, status: 409, json: async () => ({ message: 'Administrators can\'t be blocked here.' }) } } });
    await e.api.load(false); await e.api.setStatus(3, 'blocked');
    assert(/Administrators can't be blocked/.test(e.els.toast.textContent)); assert(/err/.test(e.els.toast.className));
    assert.strictEqual(e.api.state.busy.size, 0, 'the button is free again');
  });

  await t('a second click on a button that is already working is ignored', async () => {
    const e = pageEnv(); await e.api.load(false);
    const first = e.api.setStatus(2, 'approved'); const second = e.api.setStatus(2, 'approved');
    await Promise.all([first, second]);
    assert.strictEqual(e.requests.filter(r => r.key.startsWith('POST')).length, 1);
  });

  await t('the page refreshes itself while open, and every email goes through escapeHtml', () => {
    assert(/setInterval\(\(\) => \{ if\(!document\.hidden\) load\(true\); \}, 30000\)/.test(script));
    assert(/visibilitychange/.test(script));
    assert(!/innerHTML\s*=\s*[^;]*\bu\.email\b(?!.*escapeHtml)/.test(script.replace(/escapeHtml\(u\.email\)/g, 'SAFE')), 'every email goes through escapeHtml');
  });

  // ── the dashboard's Admin link
  function linkEnv({ pending = 3, fails = false, admin = true } = {}) {
    const made = [], classes = [], intervals = [];
    const mk = (tag) => { const e = { tag, children: [], className: '', title: '', textContent: '', innerHTML: '', parent: null,
      appendChild(c) { this.children.push(c); c.parent = this; }, querySelector(sel) { return this.children.find(c => '.' + c.className === sel) || null; },
      remove() { if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this); } }; made.push(e); return e; };
    const nav = mk('div'); nav.closest = () => ({ classList: { add: c => classes.push(c) } });
    const ctx = { API: '', document: { createElement: mk, hidden: false }, setInterval: (fn, ms) => intervals.push([fn, ms]),
      fetch: async () => { if (fails) throw new Error('down'); return ok({ status: 'success', pending }); } };
    vm.createContext(ctx);
    vm.runInContext(between('function addAdminLink', '// Who is signed in') + ';this.addAdminLink = addAdminLink', ctx);
    return { ctx, nav, classes, intervals, link: () => nav.children[0] };
  }
  const settle = () => new Promise(r => setImmediate(r));

  await t('the Admin link links to the admin page and shows how many people are waiting', async () => {
    const e = linkEnv({ pending: 3 }); e.ctx.addAdminLink(e.nav); await settle();
    assert.strictEqual(e.link().href, '/admin'); assert.strictEqual(e.link().className, 'nadmin');
    assert.strictEqual(e.link().querySelector('.nbadge').textContent, '3'); assert.strictEqual(e.link().title, '3 waiting for approval');
    assert.deepStrictEqual(e.classes, ['has-admin']);
    assert.deepStrictEqual(e.intervals.map(i => i[1]), [60000], 'the count refreshes every minute');
  });

  await t('the badge disappears when nobody is waiting, and big numbers are capped', async () => {
    const none = linkEnv({ pending: 0 }); none.ctx.addAdminLink(none.nav); await settle();
    assert.strictEqual(none.link().querySelector('.nbadge'), null); assert.strictEqual(none.link().title, 'User access');
    const many = linkEnv({ pending: 250 }); many.ctx.addAdminLink(many.nav); await settle();
    assert.strictEqual(many.link().querySelector('.nbadge').textContent, '99+');
    const again = linkEnv({ pending: 2 }); again.ctx.addAdminLink(again.nav); await settle();
    again.ctx.fetch = async () => ok({ status: 'success', pending: 0 }); await again.intervals[0][0](); await settle();
    assert.strictEqual(again.link().querySelector('.nbadge'), null, 'a refresh removes the old badge');
  });

  await t('if the count cannot be fetched the link still works', async () => {
    const e = linkEnv({ fails: true }); e.ctx.addAdminLink(e.nav); await settle();
    assert.strictEqual(e.link().href, '/admin'); assert.strictEqual(e.link().querySelector('.nbadge'), null);
  });

  await t('only administrators get the link, and the page learns it from the server', () => {
    assert(/if\(ME\.admin\)addAdminLink\(nav\);/.test(html));
    const ME = (admin) => { const ctx = { document: { querySelector: sel => { const k = sel.match(/name="([^"]+)"/)[1]; return k === 'smp-user-admin' && admin !== null ? { content: admin } : null; } } };
      vm.createContext(ctx); vm.runInContext(between('const ME=', '\nconst C=[') + ';this.ME=ME', ctx); return ctx.ME.admin; };
    assert.strictEqual(ME('1'), true); assert.strictEqual(ME('0'), false); assert.strictEqual(ME(null), false);
  });

  done();
})();
