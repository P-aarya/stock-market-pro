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

/** The profile code running against a fake page. */
function profile({ fetchImpl, signedOut = () => false } = {}) {
  const page = new FakePage(['prof-body']), calls = [];
  const document = { ...page.document, activeElement: null };
  const ctx = { document, API: '', console, signedOut, closeUpgrade: () => calls.push('closeUpgrade'), showPage: (n, b) => calls.push(['showPage', n, b && b.id]),
    fetch: fetchImpl || (async () => ok(data())), encodeURIComponent, Math, Date, Intl };
  vm.createContext(ctx);
  vm.runInContext(SHARED + '\n' + code, ctx);
  return { ctx, page, calls, body: () => page.registry['prof-body'].innerHTML, run: s => vm.runInContext(s, ctx) };
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

  await t('"See plans" on the padlock message closes it and opens the Profile tab with the plans showing', async () => {
    const p = profile(); p.page.el('ntab-profile').id = 'ntab-profile';
    p.run('seePlans()');
    assert.deepStrictEqual(p.calls, ['closeUpgrade', ['showPage', 'profile', 'ntab-profile']]);
    assert.strictEqual(p.run('profilePlansOpen'), true);
    await p.ctx.loadProfile(); assert(!/prof-plans"[^>]* hidden>/.test(p.body()), 'and it is open when the data arrives');
    const none = profile(); none.run('seePlans()'); assert.deepStrictEqual(none.calls, ['closeUpgrade'], 'no tab, no error');
  });

  await t('the page, the tab and the message are wired up: tab, page, loader and See plans button exist, and showPage loads the profile', () => {
    assert(/<button class="ntab" id="ntab-profile" onclick="showPage\('profile',this\)">[\s\S]*?<span class="lbl">Profile<\/span><\/button>/.test(html));
    assert(!/id="ntab-profile"[^>]*data-feature/.test(html), 'every account has a profile: the tab is never padlocked');
    assert(html.includes('<div class="page" id="page-profile">') && html.includes('<div id="prof-body">'));
    assert(/if\(n==='profile'\)loadProfile\(\);/.test(between('function showPage(n,btn){', 'function showSub')));
    assert(/<button type="button" class="btn btnp" id="up-plans" onclick="seePlans\(\)">See plans<\/button>\s*<button type="button" class="btn btns" id="up-ok" onclick="closeUpgrade\(\)">Got it<\/button>/.test(html));
  });

  done();
})();
