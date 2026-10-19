// Accounts in the dashboard: who is signed in, the watchlist kept on the server, and chat history kept per account.
const assert = require('assert');
const { html, between, suite, ok, vm } = require('./harness');
const { t, done } = suite('Accounts (dashboard.html)');

const localStorageFake = (store) => ({
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; },
});

/** The watchlist code with a fake server. `server.list` is what the server holds; `server.puts` collects what is saved. */
function watchlistEnv({ list = [], legacy, getStatus = 200, putStatus = 200, pageOpen = false, signedOutOn = null } = {}) {
  const store = legacy === undefined ? {} : { wl_tickers: typeof legacy === 'string' ? legacy : JSON.stringify(legacy) };
  const server = { list, puts: [], gets: 0 };
  const gates = [];                                              // lets a test hold a save open
  const alerts = [], reloads = [];
  const ctx = {
    API: '', console: { error() {} }, alert: m => alerts.push(m), JSON, Array, Promise, Error,
    localStorage: localStorageFake(store),
    signedOut: res => res.status === signedOutOn,
    loadWatchlist: () => reloads.push(1),
    document: { getElementById: id => id === 'page-watchlist' ? { classList: { contains: c => c === 'on' && pageOpen } } : null },
    fetch: async (url, opts = {}) => {
      if (opts.method === 'PUT') {
        if (gates.length) await gates.shift();
        server.puts.push(JSON.parse(opts.body).tickers);
        if (putStatus >= 400) return { ok: false, status: putStatus, json: async () => ({}) };
        server.list = JSON.parse(opts.body).tickers;
        return ok({ status: 'success', tickers: server.list });
      }
      server.gets++;
      return getStatus >= 400 ? { ok: false, status: getStatus, json: async () => ({}) } : ok({ status: 'success', tickers: server.list });
    },
  };
  vm.createContext(ctx);
  vm.runInContext(between('const WL_LEGACY_KEY', 'function addToWatchlistManual') + '\nthis.api = {get watchlist(){return watchlist}, set watchlist(v){watchlist=v}, initWatchlist, saveWatchlist, readLegacyWatchlist};', ctx);
  return { ctx, api: ctx.api, store, server, gates, alerts, reloads };
}

(async () => {
  await t('the page learns who is signed in from the tags the server writes into <head>', () => {
    assert.strictEqual((html.match(/<!--SMP_USER-->/g) || []).length, 1, 'one placeholder for the server to fill');
    const src = between('const ME=', '\nconst C=[');
    const metas = { 'smp-user-id': '12', 'smp-user-email': 'a@b.co' };
    const make = (m) => { const ctx = { document: { querySelector: sel => { const k = sel.match(/name="([^"]+)"/)[1]; return k in m ? { content: m[k] } : null; } } }; vm.createContext(ctx); vm.runInContext(src + ';this.ME=ME', ctx); return ctx.ME; };
    assert.deepStrictEqual({ ...make(metas) }, { id: '12', email: 'a@b.co' });
    assert.deepStrictEqual({ ...make({}) }, { id: '', email: '' });
  });

  await t('the top bar shows the email and a Sign out link, and a removed account is sent to the login page', () => {
    assert(/u\.className='nuser';u\.textContent=ME\.email/.test(html), 'the email is set with textContent, never as markup');
    assert(/a\.href='\/logout';a\.className='nsignout';a\.textContent='Sign out'/.test(html));
    assert(/d&&d\.signed_in===false\)location\.href='\/login'/.test(html));
  });

  await t('the API is always called at the address the page was opened at, so the sign-in cookie goes with it', () => {
    const base = (loc) => { const ctx = { location: loc }; vm.createContext(ctx); vm.runInContext(between('const API=', '\n') + ';this.API=API', ctx); return ctx.API; };
    const at = (protocol, host, port) => ({ protocol, origin: `${protocol}//${host}${port ? ':' + port : ''}` });
    assert.strictEqual(base(at('http:', '127.0.0.1', 8000)), 'http://127.0.0.1:8000', '127.0.0.1 must not be sent to "localhost"');
    assert.strictEqual(base(at('http:', 'localhost', 8000)), 'http://localhost:8000');
    assert.strictEqual(base(at('http:', 'localhost', 8010)), 'http://localhost:8010', 'whatever port the server runs on');
    assert.strictEqual(base(at('https:', 'abc.trycloudflare.com')), 'https://abc.trycloudflare.com');
    assert.strictEqual(base({ protocol: 'file:', origin: 'null' }), 'http://localhost:8000', 'a page opened from disk has no server of its own');
  });

  await t('a refused request sends you to sign in once; if it keeps being refused the page says so instead of looping', () => {
    const run = (res, { path = '/', stored = null, now = 1e6, noStorage = false } = {}) => {
      const store = stored === null ? {} : { smp_login_redirect: String(stored) }, appended = [];
      const ctx = { location: { pathname: path, href: '' }, Date: { now: () => now }, document: { getElementById: id => appended.find(e => e.id === id) || null,
        createElement: () => ({ style: {}, set innerHTML(v) { this._h = v; } }), body: { appendChild: e => appended.push(e) } },
        sessionStorage: noStorage ? { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } } : { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } } };
      vm.createContext(ctx); vm.runInContext(between('function signedOut', 'async function api(url)') + ';this.out = signedOut(' + JSON.stringify(res) + ')', ctx);
      return { handled: ctx.out, href: ctx.location.href, banner: appended.length, stored: store.smp_login_redirect };
    };
    assert.deepStrictEqual({ ...run({ status: 200 }) }, { handled: false, href: '', banner: 0, stored: undefined });
    assert.strictEqual(run({ status: 401 }, { path: '/login' }).handled, false, 'the login page handles its own 401s');
    const first = run({ status: 401 }); assert.deepStrictEqual([first.handled, first.href, first.banner], [true, '/login', 0]);
    const again = run({ status: 401 }, { stored: 1e6 - 3000 });                      // refused 3 seconds after being sent to /login
    assert.deepStrictEqual([again.handled, again.href, again.banner], [true, '', 1]);
    const later = run({ status: 401 }, { stored: 1e6 - 60000 }); assert.deepStrictEqual([later.href, later.banner], ['/login', 0]);
    assert.strictEqual(run({ status: 401 }, { noStorage: true }).href, '/login', 'still redirects when the browser blocks storage');
  });

  await t('the watchlist is loaded from the server, not from this browser', async () => {
    const e = watchlistEnv({ list: ['AAPL', 'MSFT'] });
    await e.api.initWatchlist();
    assert.deepStrictEqual([...e.api.watchlist], ['AAPL', 'MSFT']); assert.strictEqual(e.server.gets, 1);
  });

  await t('a list saved in this browser before accounts existed moves to the account, once, and is then deleted', async () => {
    const e = watchlistEnv({ list: [], legacy: ['aapl', ' msft ', '', 5] });
    await e.api.initWatchlist();
    assert.deepStrictEqual([...e.api.watchlist], ['AAPL', 'MSFT']);
    assert.deepStrictEqual(e.server.puts, [['AAPL', 'MSFT']]);
    assert.strictEqual('wl_tickers' in e.store, false, 'the browser copy must go, or a second person here would inherit it');
  });

  await t('an account that already has a watchlist keeps it, and the browser copy is still deleted', async () => {
    const e = watchlistEnv({ list: ['NVDA'], legacy: ['AAPL'] });
    await e.api.initWatchlist();
    assert.deepStrictEqual([...e.api.watchlist], ['NVDA']); assert.deepStrictEqual(e.server.puts, []);
    assert.strictEqual('wl_tickers' in e.store, false);
  });

  await t('if the server cannot be reached the browser copy is kept for next time', async () => {
    const e = watchlistEnv({ list: [], legacy: ['AAPL'], getStatus: 500 });
    await e.api.initWatchlist();
    assert.deepStrictEqual([...e.api.watchlist], []); assert.strictEqual(e.store.wl_tickers, '["AAPL"]');
  });

  await t('damaged browser data is ignored rather than breaking the page', async () => {
    for (const bad of ['not json', '{"a":1}', '"AAPL"']) {
      const e = watchlistEnv({ list: ['MSFT'], legacy: bad });
      await e.api.initWatchlist();
      assert.deepStrictEqual([...e.api.watchlist], ['MSFT'], bad);
    }
  });

  await t('a signed-out session is handed to the login redirect and nothing is changed', async () => {
    const e = watchlistEnv({ list: ['AAPL'], getStatus: 401, signedOutOn: 401, legacy: ['X'] });
    await e.api.initWatchlist();
    assert.deepStrictEqual([...e.api.watchlist], []); assert.strictEqual(e.store.wl_tickers, '["X"]');
  });

  await t('the watchlist page refreshes itself if it is already showing when the list arrives', async () => {
    const open = watchlistEnv({ list: ['AAPL'], pageOpen: true }); await open.api.initWatchlist(); assert.strictEqual(open.reloads.length, 1);
    const shut = watchlistEnv({ list: ['AAPL'], pageOpen: false }); await shut.api.initWatchlist(); assert.strictEqual(shut.reloads.length, 0);
  });

  await t('every change is saved to the server as the whole list, in the order it was made', async () => {
    const e = watchlistEnv();
    const release = []; e.gates.push(new Promise(r => release.push(r)));       // hold the first save open
    e.api.watchlist = ['AAPL']; const first = e.api.saveWatchlist();
    e.api.watchlist = ['AAPL', 'MSFT']; const second = e.api.saveWatchlist();
    await new Promise(r => setImmediate(r));
    assert.deepStrictEqual(e.server.puts, [], 'the second save must wait for the first');
    release[0](); await Promise.all([first, second]);
    assert.deepStrictEqual(e.server.puts, [['AAPL'], ['AAPL', 'MSFT']]); assert.deepStrictEqual(e.server.list, ['AAPL', 'MSFT']);
  });

  await t('a failed save tells the person and does not block the saves after it', async () => {
    const e = watchlistEnv({ putStatus: 500 });
    e.api.watchlist = ['AAPL']; await e.api.saveWatchlist();
    assert.strictEqual(e.alerts.length, 1); assert(/Could not save/.test(e.alerts[0]));
    e.ctx.fetch = async (u, o) => (o && o.method === 'PUT' ? (e.server.puts.push(JSON.parse(o.body).tickers), ok({})) : ok({}));
    e.api.watchlist = ['AAPL', 'TSLA']; await e.api.saveWatchlist();
    assert.deepStrictEqual(e.server.puts.at(-1), ['AAPL', 'TSLA']);
  });

  await t('nothing writes the watchlist to this browser any more', () => {
    assert(!/localStorage\.setItem\(\s*'wl_tickers'/.test(html));
    assert.strictEqual((html.match(/(?<!await )saveWatchlist\(\);/g) || []).length, 4, 'add (typed), add (suggestion), add (card), remove');
  });

  await t('chat history is kept per account, in a key that includes the account id', () => {
    const src = between('const BOUNTY_LEGACY_KEY', 'const BOUNTY_MAX_SAVED');
    const key = (me) => { const ctx = me === undefined ? {} : { ME: me }; vm.createContext(ctx); vm.runInContext(src + ';this.k=BOUNTY_STORE', ctx); return ctx.k; };
    assert.strictEqual(key({ id: '12' }), 'bounty_chat_v1:12');
    assert.strictEqual(key({ id: '13' }), 'bounty_chat_v1:13');
    assert.strictEqual(key(undefined), 'bounty_chat_v1:anon'); assert.strictEqual(key({ id: '' }), 'bounty_chat_v1:anon');
  });

  await t('the old chat history is claimed when the page loads, not when the chat is first opened', () => {
    const startup = html.slice(html.indexOf('// Browser-only data from before accounts'), html.lastIndexOf('</script>'));
    assert(/adoptLegacyBounty\(\);/.test(startup) && /initWatchlist\(\);/.test(startup));
  });

  await t('chat history from before accounts goes to the first account to open the page, never to a second', () => {
    const adopt = (store, id) => {
      const ctx = { ME: { id }, localStorage: localStorageFake(store) }; vm.createContext(ctx);
      vm.runInContext(between('const BOUNTY_LEGACY_KEY', 'const BOUNTY_MAX_SAVED') + between('function adoptLegacyBounty', 'function loadBounty') + ';adoptLegacyBounty()', ctx);
    };
    const store = { bounty_chat_v1: '[{"role":"user","text":"private"}]' };
    adopt(store, '1');
    assert.strictEqual(store['bounty_chat_v1:1'], '[{"role":"user","text":"private"}]'); assert.strictEqual('bounty_chat_v1' in store, false);
    adopt(store, '2');
    assert.strictEqual('bounty_chat_v1:2' in store, false, 'the second account must start with an empty chat');
    const mine = { bounty_chat_v1: '[{"old":1}]', 'bounty_chat_v1:1': '[{"mine":1}]' };
    adopt(mine, '1');
    assert.strictEqual(mine['bounty_chat_v1:1'], '[{"mine":1}]', 'existing history is never overwritten'); assert.strictEqual('bounty_chat_v1' in mine, false);
  });

  done();
})();
