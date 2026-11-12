// The profile page: what it shows (name, join date, plan, what the plan includes), the Upgrade button that opens every plan with its
// perks, how a request for a plan is made (an email, or a note on who to ask), and the way into it (the tab and the padlock message).
const assert = require('assert');
const { html, between, SHARED, FakePage, suite, ok, vm } = require('./harness');
const { t, done } = suite('Profile page (dashboard.html)');

const code = between('// ── Profile page ──', 'function showPage(n,btn){');

const FEATURE = (key, label, description) => ({ key, label, description });
const INITIAL = FEATURE('initial_screen', 'Initial screen', 'The full dashboard');
const MGT = FEATURE('portfolio_mgt', 'Portfolio management', 'Add and edit your own holdings');
const PLANS = [
  { level: 1, name: 'Free', price: 0, account: false, includes: null, adds: [FEATURE('initial_screen', 'Initial screen', 'Live prices'), FEATURE('portfolio_view', 'Portfolio visibility', 'A read-only look')] },
  { level: 2, name: 'Basic', price: 9.99, account: true, includes: 'Free', adds: [INITIAL, MGT] },
  { level: 3, name: 'AI Stock Finder', price: 19.99, account: true, includes: 'Basic', adds: [FEATURE('stock_prediction', 'Stock prediction', 'AI predictions'), FEATURE('ai_stock_finder', 'AI stock finder', 'Ask the AI')] },
  { level: 6, name: 'Premier', price: 49.99, account: true, includes: 'Deluxe', adds: [FEATURE('pre_ipo', 'Pre-IPO deals', 'The IPOs page')] },
];
const data = (over = {}) => ({ status: 'success', email: 'abheek@example.com', joined_at: Date.UTC(2026, 10, 5, 12) / 1000, admin: false,
  plan: { level: 2, name: 'Basic', price: 9.99 }, included: [INITIAL, MGT], plans: PLANS, upgrade_contact: null, ...over });

const DELETE_IDS = ['del-modal', 'del-email', 'del-keep', 'del-gone', 'del-password', 'del-error', 'del-go', 'del-cancel', 'prof-delete'];

/** The profile code running against a fake page. */
function profile({ fetchImpl, signedOut = () => false } = {}) {
  const page = new FakePage(['prof-body', ...DELETE_IDS]), calls = [], requests = [];
  const document = { ...page.document, activeElement: null };
  const location = { href: '' };
  const ctx = { document, location, API: '', console, signedOut, closeUpgrade: () => calls.push('closeUpgrade'), showPage: (n, b) => calls.push(['showPage', n, b && b.id]),
    fetch: async (url, opts) => { requests.push({ url, opts }); return (fetchImpl || (async () => ok(data())))(url, opts); }, encodeURIComponent, Math, Date, Intl, JSON };
  vm.createContext(ctx);
  vm.runInContext(SHARED + '\n' + code, ctx);
  const el = id => page.registry[id];
  return { ctx, page, calls, requests, location, document, el, body: () => page.registry['prof-body'].innerHTML, run: s => vm.runInContext(s, ctx) };
}

(async () => {
  await t('it shows the profile name, the date they joined, the plan and its price', () => {
    const p = profile(), out = p.ctx.profileHtml(data(), false);
    assert(out.includes('id="prof-name">abheek@example.com<'), 'name');
    assert(out.includes('Profile name') && out.includes('Member since') && out.includes('Current plan'));
    assert(out.includes('id="prof-joined">Nov 5, 2026<'), 'joined');
    assert(out.includes('id="prof-plan">Basic<') && out.includes('$9.99 per month'), 'plan');
    assert(out.includes('prof-avatar') && out.includes('>a<'), 'the avatar shows the first letter');
  });

  await t('the profile name is the full name when there is one (with the email under it), and the email otherwise', () => {
    const ctx = profile().ctx;
    const named = ctx.profileHtml(data({ full_name: 'Abheek Das' }), false);
    assert(named.includes('id="prof-name">Abheek Das<') && named.includes('id="prof-email">abheek@example.com<'), 'name big, email small');
    assert(named.includes('prof-avatar') && /prof-avatar" aria-hidden="true">A</.test(named), 'the avatar shows the first letter of the name');
    const plain = ctx.profileHtml(data({ full_name: null }), false);
    assert(plain.includes('id="prof-name">abheek@example.com<') && !plain.includes('id="prof-email"'));
    assert(!ctx.profileHtml(data({ full_name: '<b onmouseover=alert(1)>x</b>' }), false).includes('<b onmouseover'), 'a name is shown as text');
  });

  await t('the profile links to Settings, where the name, email, password, notifications and Help Desk are managed', () => {
    const out = profile().ctx.profileHtml(data(), false);
    assert(/<a class="prof-btn" id="prof-settings" href="\/settings"/.test(out) && out.includes('>Settings</a>'));
  });

  await t('the Free plan says there is no monthly charge instead of repeating its own name', () => {
    const out = profile().ctx.profileHtml(data({ plan: { level: 1, name: 'Free', price: 0 } }), false);
    assert(out.includes('id="prof-plan">Free<') && out.includes('No monthly charge') && !/>Free<\/div><div class="prof-note">Free</.test(out));
  });

  await t('what the plan includes is listed with each perk explained', () => {
    const out = profile().ctx.profileHtml(data(), false);
    const incl = out.slice(out.indexOf('prof-incl'), out.indexOf('id="prof-plans"'));
    assert(incl.includes('Your plan includes') && incl.includes('<b>Initial screen</b><span>The full dashboard</span>') && incl.includes('<b>Portfolio management</b><span>Add and edit your own holdings</span>'));
  });

  await t('the Upgrade button opens the plans; on the top plan or for an administrator it reads View plans, and when open it reads Hide plans', () => {
    const ctx = profile().ctx;
    const btn = h => h.match(/id="prof-upgrade"[^>]*>([^<]+)</)[1];
    assert.strictEqual(btn(ctx.profileHtml(data(), false)), 'Upgrade plan');
    assert.strictEqual(btn(ctx.profileHtml(data(), true)), 'Hide plans');
    const top = ctx.profileHtml(data({ plan: { level: 6, name: 'Premier', price: 49.99 } }), false);
    assert.strictEqual(btn(top), 'View plans'); assert(top.includes('You are on our top plan.'));
    const admin = ctx.profileHtml(data({ admin: true, plan: { level: null, name: 'Administrator', price: null } }), false);
    assert.strictEqual(btn(admin), 'View plans'); assert(admin.includes('Full access to everything') && admin.includes('id="prof-plan">Administrator<') && !admin.includes('top plan'));
    assert(/aria-expanded="false" aria-controls="prof-plans"/.test(ctx.profileHtml(data(), false)) && /aria-expanded="true"/.test(ctx.profileHtml(data(), true)));
  });

  await t('the plans are hidden until the button is pressed, and each lists its price and perks', () => {
    const ctx = profile().ctx, closed = ctx.profileHtml(data(), false), open = ctx.profileHtml(data(), true);
    assert(/id="prof-plans"[^>]* hidden>/.test(closed)); assert(!/id="prof-plans"[^>]* hidden>/.test(open));
    for (const p of PLANS) {
      const card = open.slice(open.indexOf(`data-plan="${p.level}"`), open.indexOf('</article>', open.indexOf(`data-plan="${p.level}"`)));
      assert(card.includes(`>${p.name}<`), p.name);
      assert(card.includes(`$${p.price.toFixed(2)} <small>/ month</small>`), p.name + ' price');
      for (const f of p.adds) assert(card.includes(`<b>${f.label}</b><span>${f.description}</span>`), `${p.name}: ${f.label}`);
      assert.strictEqual(card.includes(`Everything in ${p.includes}, plus:`), p.includes != null, p.name + ' builds on');
    }
  });

  await t('each plan card knows where the person stands: Your plan, Included in your plan, or an Upgrade button', () => {
    const open = profile().ctx.profileHtml(data(), true);
    const card = level => open.slice(open.indexOf(`data-plan="${level}"`), open.indexOf('</article>', open.indexOf(`data-plan="${level}"`)));
    assert(card(1).includes('Included in your plan') && !card(1).includes('Upgrade to'));
    assert(card(2).includes('Your plan') && /class="plan-card current" data-plan="2"/.test(open) && !card(2).includes('Upgrade to'));
    assert(card(3).includes('Upgrade to AI Stock Finder') && card(6).includes('Upgrade to Premier'));
    assert(!/class="plan-card current" data-plan="(1|3|6)"/.test(open));
    const admin = profile().ctx.profileHtml(data({ admin: true, plan: { level: null, name: 'Administrator', price: null } }), true);
    assert(!admin.includes('Upgrade to') && !admin.includes('Your plan</span>') && !admin.includes('plan-card current'), 'an administrator is nobody\'s customer');
  });

  await t('with an address set up the Upgrade button writes an email asking for that plan; without one it says who to ask', () => {
    const ctx = profile().ctx;
    const withMail = ctx.profileHtml(data({ upgrade_contact: 'help@aymetrix.com' }), true);
    const href = decodeURIComponent(withMail.match(/href="(mailto:[^"]+)"/)[1].replace(/&amp;/g, '&'));
    assert(href.startsWith('mailto:help@aymetrix.com?subject=Upgrade to the AI Stock Finder plan&body=') && href.includes('move my account (abheek@example.com) to the AI Stock Finder plan ($19.99 a month)'), href);
    assert(withMail.includes('opens an email to help@aymetrix.com') && !withMail.includes('onclick="profileHowToUpgrade'));
    const without = ctx.profileHtml(data(), true);
    assert(!without.includes('mailto:') && without.includes('onclick="profileHowToUpgrade(3)"') && without.includes('ask them to move your account'));
    assert.strictEqual(ctx.profileUpgradeHref(data(), PLANS[2]), '');
  });

  await t('asking for a plan without an address names the plan, its price and the account to move', () => {
    const p = profile(); p.ctx.profileData = null; p.run('profileData = ' + JSON.stringify(data()));
    p.page.el('prof-howto');
    p.run("profileHowToUpgrade(3)");
    assert.strictEqual(p.page.registry['prof-howto'].textContent, 'To switch to AI Stock Finder ($19.99 a month), ask an administrator to move abheek@example.com to that plan.');
    p.page.registry['prof-howto'].textContent = ''; p.run('profileHowToUpgrade(99)');
    assert.strictEqual(p.page.registry['prof-howto'].textContent, '', 'an unknown plan changes nothing');
  });

  await t('nothing from the server can inject markup, in the name, the plans or the contact address', () => {
    const evil = '<img src=x onerror=alert(1)>';
    const out = profile().ctx.profileHtml(data({ email: `a${evil}@x.co`, upgrade_contact: 'a@b.co', plans: [{ ...PLANS[2], name: evil, adds: [FEATURE('x', evil, evil)] }], included: [FEATURE('x', evil, evil)] }), true);
    assert(!out.includes('<img'), 'no raw tag survives'); assert(out.includes('&lt;img src=x onerror=alert(1)&gt;'));
  });

  await t('a missing join date or odd data still draws the page', () => {
    const p = profile(), ctx = p.ctx;
    assert(ctx.profileHtml(data({ joined_at: null }), false).includes('id="prof-joined">—<'));
    assert.strictEqual(p.run('profileDate(0)'), '—'); assert.strictEqual(p.run('profileDate(null)'), '—');
    assert(ctx.profileHtml(data({ email: '' }), false).includes('>?<'), 'the avatar copes with no name');
    assert.doesNotThrow(() => ctx.profileHtml(data({ plans: [] }), true), 'an empty plan list is not an error');
  });

  await t('opening the page loads the profile, draws it, and a failed load says so with a way to retry', async () => {
    const p = profile(); await p.ctx.loadProfile();
    assert(p.body().includes('abheek@example.com') && p.body().includes('Member since'));
    const bad = profile({ fetchImpl: async () => ok({ status: 'error', message: 'nope' }, 503) });
    await bad.ctx.loadProfile();
    assert(bad.body().includes('couldn’t load your profile') && bad.body().includes('onclick="loadProfile()"'));
    const thrown = profile({ fetchImpl: async () => { throw new Error('offline'); } }); await thrown.ctx.loadProfile();
    assert(thrown.body().includes('Try again'));
  });

  await t('a failed refresh keeps the profile that is already showing; a sign-out redirects instead of drawing', async () => {
    let fail = false; const p = profile({ fetchImpl: async () => (fail ? ok({}, 500) : ok(data())) });
    await p.ctx.loadProfile(); const first = p.body(); fail = true; await p.ctx.loadProfile();
    assert.strictEqual(p.body(), first);
    const out = profile({ signedOut: () => true }); await out.ctx.loadProfile();
    assert.strictEqual(out.body(), '', 'nothing drawn for a signed-out person');
  });

  await t('the button toggles the comparison and keeps it open across a reload of the data', async () => {
    const p = profile(); await p.ctx.loadProfile();
    assert(/prof-plans"[^>]* hidden>/.test(p.body()));
    p.run('toggleProfilePlans()'); assert(!/prof-plans"[^>]* hidden>/.test(p.body()) && p.body().includes('Hide plans'));
    await p.ctx.loadProfile(); assert(!/prof-plans"[^>]* hidden>/.test(p.body()), 'still open after the page is opened again');
    p.run('toggleProfilePlans()'); assert(/prof-plans"[^>]* hidden>/.test(p.body()) && p.body().includes('Upgrade plan'));
  });

  await t('"See plans" on the padlock message closes it and opens the Profile page, with no tab involved, and the plans showing', async () => {
    const p = profile();
    p.run('seePlans()');
    assert.deepStrictEqual(p.calls, ['closeUpgrade', ['showPage', 'profile', undefined]]);
    assert.strictEqual(p.run('profilePlansOpen'), true);
    await p.ctx.loadProfile(); assert(!/prof-plans"[^>]* hidden>/.test(p.body()), 'and it is open when the data arrives');
  });

  await t('openProfile shows the Profile page without naming a tab to highlight', () => {
    const p = profile(); p.run('openProfile()');
    assert.deepStrictEqual(p.calls, [['showPage', 'profile', undefined]]);
  });

  /** A tiny fake page: just enough elements for the account menu and showPage. */
  class El {
    constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.className = ''; this.textContent = ''; this.innerHTML = ''; this.classes = new Set();
      this.classList = { toggle: (c, on) => { if (on === undefined ? !this.classes.has(c) : on) this.classes.add(c); else this.classes.delete(c); }, contains: c => this.classes.has(c), add: c => this.classes.add(c), remove: c => this.classes.delete(c) }; }
    setAttribute(k, v) { this.attrs[k] = v; } appendChild(c) { this.children.push(c); return c; }
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); } fire(ev, e = {}) { (this.listeners[ev] || []).forEach(fn => fn({ stopPropagation() {}, target: this, ...e })); }
    contains(t) { return t === this || this.children.some(c => c.contains(t)); }
  }
  function accountMenu({ name = '' } = {}) {
    const docListeners = {}, calls = [];
    const document = { createElement: tag => new El(tag), addEventListener: (ev, fn) => { (docListeners[ev] = docListeners[ev] || []).push(fn); } };
    const ctx = { document, ME: { name, email: 'abheek@example.com' }, openProfile: () => calls.push('openProfile') };
    vm.createContext(ctx); vm.runInContext(between('function addAccountMenu(nav){', "(()=>{const nav=document.querySelector('.nav-r')"), ctx);
    const nav = new El('nav'), parts = ctx.addAccountMenu(nav);
    return { nav, parts, calls, docListeners, press: (ev, e) => (docListeners[ev] || []).forEach(fn => fn(e)) };
  }

  await t('the account menu lists the name, the email, then Profile and plan, then Sign out', () => {
    const { parts } = accountMenu({ name: 'Abheek Das' });
    assert.deepStrictEqual(parts.menu.children.map(c => c.className), ['nprof-name', 'nprof-email', 'nprof-hr', 'nmenu-item', 'nsignout']);
    assert.strictEqual(parts.btn.id, 'nprof-btn', 'the account icon can be found, so it can be lit while Profile is open');
    const [name, email, , prof, out] = parts.menu.children;
    assert.strictEqual(name.textContent, 'Abheek Das'); assert.strictEqual(email.textContent, 'abheek@example.com');
    assert.deepStrictEqual([prof.tag, prof.type, prof.id, prof.attrs.role], ['button', 'button', 'nprof-profile', 'menuitem']);
    assert(prof.innerHTML.includes('<span>Profile and plan</span>'));
    assert.deepStrictEqual([out.tag, out.href, out.attrs.role], ['a', '/logout', 'menuitem']);
    assert.strictEqual(accountMenu().parts.menu.children[0].textContent, 'Your account', 'with no name the menu says Your account');
  });

  await t('choosing Profile and plan closes the menu and opens the page', () => {
    const { parts, calls } = accountMenu();
    parts.btn.fire('click'); assert(parts.menu.classList.contains('open')); assert.strictEqual(parts.btn.attrs['aria-expanded'], 'true');
    parts.menu.children[3].fire('click');
    assert(!parts.menu.classList.contains('open')); assert.strictEqual(parts.btn.attrs['aria-expanded'], 'false');
    assert.deepStrictEqual(calls, ['openProfile']);
  });

  await t('the menu still closes on Escape and on a click elsewhere, and opens only from its button', () => {
    const a = accountMenu(); a.parts.btn.fire('click'); a.press('keydown', { key: 'Enter' }); assert(a.parts.menu.classList.contains('open'));
    a.press('keydown', { key: 'Escape' }); assert(!a.parts.menu.classList.contains('open'));
    a.parts.btn.fire('click'); a.press('click', { target: new El('div') }); assert(!a.parts.menu.classList.contains('open'), 'a click outside closes it');
    a.parts.btn.fire('click'); a.press('click', { target: a.parts.menu }); assert(a.parts.menu.classList.contains('open'), 'a click inside does not');
    assert(a.calls.length === 0, 'none of that opened the page');
  });

  await t('while the Profile page is open no tab is highlighted and the account icon is; any other page clears it', () => {
    const tabs = ['home', 'market'].map(n => Object.assign(new El('button'), { id: 'ntab-' + n })); tabs[0].classes.add('on');
    const pages = Object.fromEntries(['home', 'market', 'profile'].map(n => [n, Object.assign(new El('div'), { id: 'page-' + n })])); pages.home.classes.add('on');
    const acct = new El('button');
    const ctx = { can: () => true, showUpgrade() {}, pageChrome() {}, loadProfile: () => ctx.loaded.push('profile'), loadHome() {}, loaded: [], window: { scrollTo() {} },
      document: { querySelectorAll: sel => (sel === '.ntab' ? tabs : sel === '.page' ? Object.values(pages) : []),
        getElementById: id => (id.startsWith('page-') ? pages[id.slice(5)] : id === 'nprof-btn' ? acct : null) }, mktSuggest: null };
    vm.createContext(ctx); vm.runInContext(between('function showPage(n,btn){', 'function showSub'), ctx);
    ctx.showPage('profile');
    assert(pages.profile.classes.has('on') && !pages.home.classes.has('on')); assert(tabs.every(t => !t.classes.has('on')), 'no tab is lit'); assert(acct.classes.has('on'));
    assert.deepStrictEqual(ctx.loaded, ['profile']);
    ctx.showPage('market', tabs[1]);
    assert(tabs[1].classes.has('on') && !acct.classes.has('on') && pages.market.classes.has('on') && !pages.profile.classes.has('on'));
  });

  await t('the profile has a Delete account section that says how long they can change their mind, using the server\'s number of days', () => {
    const ctx = profile().ctx;
    const section = h => h.slice(h.indexOf('prof-danger'));
    const normal = section(ctx.profileHtml(data({ grace_days: 30 }), false));
    assert(normal.includes('Delete account') && normal.includes('id="prof-delete"') && normal.includes('onclick="openDelete()"') && normal.includes('Delete my account'));
    assert(normal.includes('kept for 30 days') && normal.includes('sign in again within 30 days and choose Restore') && normal.includes('permanently deleted'));
    assert(section(ctx.profileHtml(data({ grace_days: 14 }), false)).includes('kept for 14 days'));
    assert(section(ctx.profileHtml(data(), false)).includes('kept for 30 days'), 'a missing number falls back to 30');
    for (const d of [data({ admin: true, plan: { level: null, name: 'Administrator', price: null } }), data({ plan: { level: 6, name: 'Premier', price: 49.99 } })])
      assert(ctx.profileHtml(d, true).includes('id="prof-delete"'), 'every account can delete itself (the server decides)');
  });

  await t('opening the box shows whose account it is and what will happen, with an empty password and the delete button off', () => {
    const p = profile(); p.run('profileData = ' + JSON.stringify(data({ grace_days: 30 })));
    p.el('del-password').value = 'left over'; p.el('del-error').textContent = 'old error';
    const opener = { focus() { this.focused = true; } }; p.document.activeElement = opener;
    let focused = false; p.el('del-password').focus = () => { focused = true; };
    p.run('openDelete()');
    assert(p.el('del-modal').classList.contains('open'));
    assert.strictEqual(p.el('del-email').textContent, 'abheek@example.com');
    assert(p.el('del-keep').textContent.includes('For 30 days you can change your mind') && p.el('del-keep').textContent.includes('choose Restore'));
    assert(p.el('del-gone').textContent.includes('After 30 days') && p.el('del-gone').textContent.includes('permanently deleted'));
    assert.strictEqual(p.el('del-password').value, ''); assert.strictEqual(p.el('del-error').textContent, '');
    assert.strictEqual(p.el('del-go').disabled, true); assert(focused, 'the password box is focused');
    p.run('closeDelete()'); assert(opener.focused, 'focus goes back to the button that opened it');
    const none = profile(); none.run('openDelete()'); assert(!none.el('del-modal').classList.contains('open'), 'nothing to confirm until the profile has loaded');
  });

  await t('the account name is put in as text, never as markup', () => {
    assert(/delEl\('del-email'\)\.textContent=profileData\.email/.test(code) && !/del-email[^;]*innerHTML/.test(code));
    const p = profile(); p.run('profileData = ' + JSON.stringify(data({ email: '<img src=x onerror=alert(1)>@x.co' }))); p.run('openDelete()');
    assert.strictEqual(p.el('del-email').textContent, '<img src=x onerror=alert(1)>@x.co');
  });

  await t('the delete button only works once a password is typed, and never while a delete is under way', () => {
    const p = profile(); p.run('profileData = ' + JSON.stringify(data())); p.run('openDelete()');
    assert.strictEqual(p.el('del-go').disabled, true);
    p.el('del-password').value = 'x'; p.run('delSync()'); assert.strictEqual(p.el('del-go').disabled, false);
    p.el('del-password').value = ''; p.run('delSync()'); assert.strictEqual(p.el('del-go').disabled, true);
    p.el('del-password').value = 'x'; p.run('delBusy = true; delSync()'); assert.strictEqual(p.el('del-go').disabled, true);
  });

  await t('confirming sends the password to the server with the header that proves it came from the app, then goes to the sign-in page', async () => {
    const p = profile({ fetchImpl: async () => ok({ status: 'success', deleted_at: 1, remove_at: 2, grace_days: 30 }) });
    p.run('profileData = ' + JSON.stringify(data())); p.run('openDelete()');
    await p.ctx.confirmDelete(); assert.strictEqual(p.requests.length, 0, 'no password, no request');
    p.el('del-password').value = 'my secret';
    await p.ctx.confirmDelete();
    assert.strictEqual(p.requests.length, 1);
    const { url, opts } = p.requests[0];
    assert.strictEqual(url, '/api/profile/delete'); assert.strictEqual(opts.method, 'POST');
    assert.strictEqual(opts.headers['X-Requested-With'], 'fetch'); assert.strictEqual(opts.headers['Content-Type'], 'application/json');
    assert.deepStrictEqual(JSON.parse(opts.body), { password: 'my secret' });
    assert.strictEqual(p.location.href, '/login?notice=deleted');
    assert.strictEqual(p.el('del-password').value, '', 'the password is not left in the page');
    assert.strictEqual(p.el('del-go').disabled, true, 'still busy while the page changes');
    await p.ctx.confirmDelete(); assert.strictEqual(p.requests.length, 1, 'a second press does nothing');
  });

  await t('pressing it twice quickly sends one request', async () => {
    let release; const gate = new Promise(r => { release = r; });
    const p = profile({ fetchImpl: async () => { await gate; return ok({ status: 'success' }); } });
    p.run('profileData = ' + JSON.stringify(data())); p.run('openDelete()'); p.el('del-password').value = 'pw';
    const first = p.ctx.confirmDelete(), second = p.ctx.confirmDelete();
    assert.strictEqual(p.el('del-go').textContent, 'Deleting…'); assert.strictEqual(p.requests.length, 1);
    release(); await Promise.all([first, second]); assert.strictEqual(p.requests.length, 1);
  });

  await t('a refusal is shown in the box, nothing is deleted, and they can try again', async () => {
    for (const reply of [ok({ status: 'error', message: 'That password isn\'t right.' }, 403), ok({ status: 'error', message: 'You are the only administrator.' }, 409)]) {
      const p = profile({ fetchImpl: async () => reply });
      p.run('profileData = ' + JSON.stringify(data())); p.run('openDelete()'); p.el('del-password').value = 'wrong';
      let refocused = false; p.el('del-password').focus = () => { refocused = true; };
      await p.ctx.confirmDelete();
      assert.strictEqual(p.el('del-error').textContent, reply.json ? (await reply.json()).message : '');
      assert.strictEqual(p.location.href, '', 'not signed out');
      assert.strictEqual(p.el('del-go').textContent, 'Yes, delete my account'); assert.strictEqual(p.el('del-go').disabled, false);
      assert(refocused, 'the cursor is back in the password box');
      assert(p.el('del-modal').classList.contains('open'), 'the box stays open');
    }
  });

  await t('an unreadable answer or a dead connection says the account was not deleted', async () => {
    const garbled = profile({ fetchImpl: async () => ({ ok: false, status: 502, json: async () => { throw new Error('not json'); } }) });
    garbled.run('profileData = ' + JSON.stringify(data())); garbled.run('openDelete()'); garbled.el('del-password').value = 'pw';
    await garbled.ctx.confirmDelete();
    assert(garbled.el('del-error').textContent.includes('your account was not deleted')); assert.strictEqual(garbled.location.href, '');
    const offline = profile({ fetchImpl: async () => { throw new Error('Failed to fetch'); } });
    offline.run('profileData = ' + JSON.stringify(data())); offline.run('openDelete()'); offline.el('del-password').value = 'pw';
    await offline.ctx.confirmDelete();
    assert(offline.el('del-error').textContent.includes('couldn’t reach the server') && offline.el('del-error').textContent.includes('not deleted'));
    assert.strictEqual(offline.el('del-go').disabled, false);
    const odd = profile({ fetchImpl: async () => ok({ status: 'error' }, 200) });                    // a 200 that is not a success is not a success
    odd.run('profileData = ' + JSON.stringify(data())); odd.run('openDelete()'); odd.el('del-password').value = 'pw';
    await odd.ctx.confirmDelete(); assert.strictEqual(odd.location.href, '');
  });

  await t('Cancel and Escape close the box and wipe the password, but not while a delete is under way', () => {
    const p = profile(); p.run('profileData = ' + JSON.stringify(data())); p.run('openDelete()');
    p.el('del-password').value = 'typed'; p.el('del-error').textContent = 'x';
    p.run('closeDelete()');
    assert(!p.el('del-modal').classList.contains('open')); assert.strictEqual(p.el('del-password').value, ''); assert.strictEqual(p.el('del-error').textContent, '');
    p.run('openDelete()'); p.run('delBusy = true'); p.run('closeDelete()');
    assert(p.el('del-modal').classList.contains('open'), 'it cannot be dismissed half-way through');
    assert(/Escape'\)\{closeUpgrade\(\);if\(typeof closeDelete==='function'\)closeDelete\(\);\}/.test(html), 'Escape closes it, and the plan code works without it');
    assert(html.includes('<div id="del-modal" role="dialog" aria-modal="true" aria-labelledby="del-title" aria-describedby="del-desc" onclick="if(event.target===this)closeDelete()">'), 'a click outside closes it');
  });

  await t('the box is built for the job: a password field, Keep as the main button and Delete as the red one that starts off', () => {
    const box = html.slice(html.indexOf('<div id="del-modal"'), html.indexOf('<div id="up-modal"'));
    assert(/<input type="password" class="del-input" id="del-password" autocomplete="current-password"/.test(box));
    assert(/class="btn btnp" id="del-cancel" onclick="closeDelete\(\)">Keep my account</.test(box));
    assert(/class="btn btnd" id="del-go" onclick="confirmDelete\(\)" disabled>Yes, delete my account</.test(box));
    assert(box.indexOf('id="del-cancel"') > 0 && box.indexOf('id="del-cancel"') < box.indexOf('id="del-go"'), 'the safe choice comes first');
    assert(/onkeydown="if\(event\.key==='Enter'\)confirmDelete\(\)"/.test(box) && /oninput="delSync\(\)"/.test(box));
    assert(/role="alert"/.test(box.slice(box.indexOf('id="del-error"') - 40, box.indexOf('id="del-error"') + 40)));
    assert(/\.del-input\{[^}]*font-size:16px/.test(html), '16px so a phone does not zoom in');
  });

  await t('the page, the menu entry and the message are wired up: page and loader exist, Profile is not a tab, and showPage loads it', () => {
    const tabs = between('<div class="ntabs">', '<div class="nav-r">');
    assert(!/profile/i.test(tabs), 'Profile is not one of the tabs');
    assert.strictEqual((tabs.match(/<button class="ntab/g) || []).length, 8, 'the eight tabs of the app');
    assert(!/showPage\('profile',this\)/.test(html), 'nothing opens the Profile page as if it were a tab');
    assert(html.includes('<div class="page" id="page-profile">') && html.includes('<div id="prof-body">'));
    assert(/if\(n==='profile'\)loadProfile\(\);/.test(between('function showPage(n,btn){', 'function showSub')));
    assert(/<button type="button" class="btn btnp" id="up-plans" onclick="seePlans\(\)">See plans<\/button>\s*<button type="button" class="btn btns" id="up-ok" onclick="closeUpgrade\(\)">Got it<\/button>/.test(html));
  });

  done();
})();
