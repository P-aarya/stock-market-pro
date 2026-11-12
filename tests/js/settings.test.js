// Settings (settings.html), the Help Desk part: on a plan without the Help Desk the page says so and offers no form that cannot work.
const assert = require('assert');
const fs = require('fs'), path = require('path');
const { suite, ok, vm } = require('./harness');
const { t, done } = suite('Settings page: Help Desk and plans');

const page = fs.readFileSync(path.join(__dirname, '..', '..', 'settings.html'), 'utf8');
const cut = (start, end) => { const a = page.indexOf(start); assert(a >= 0, start); const b = page.indexOf(end, a + start.length); assert(b > a, end); return page.slice(a, b); };
const code = cut('async function call(path', '// ── sections ──') + '\n' + cut('function renderTickets(){', 'function showNewTicket(show){');

/** The ticket code against a fake page; `reply` is what the server answers every request with. */
function env(reply) {
  const els = {};
  const el = id => els[id] || (els[id] = { id, hidden: false, innerHTML: '', textContent: '', options: [], classList: { toggle() {}, add() {}, remove() {} }, appendChild() {}, set innerHTMLValue(v) {} });
  const ctx = { console, state: { tickets: [], categories: {}, ticket: null }, location: { href: '' }, fetch: async () => reply, document: { getElementById: el, createElement: () => ({}) },
    escapeHtml: x => String(x).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])), ticketRow: () => '', Object, Error };
  el('tk-category').options = [1, 2]; el('help-dot'); el('new-ticket-btn'); el('new-ticket'); el('tickets');
  vm.createContext(ctx); vm.runInContext(code, ctx);
  return { ctx, els };
}

(async () => {
  await t('a refused request carries its code, so the page can tell "not in your plan" from a real failure', async () => {
    const { ctx } = env(ok({ status: 'error', code: 'upgrade_required', message: "Raising tickets isn't included in the Free plan. It comes with the Basic plan." }, 403));
    await assert.rejects(() => ctx.call('/api/support/tickets'), e => e.code === 'upgrade_required' && /Basic plan/.test(e.message));
    const broken = env(ok({ status: 'error', message: 'Something broke' }, 500));
    await assert.rejects(() => broken.ctx.call('/x'), e => e.code === undefined && e.message === 'Something broke');
  });

  await t('on a plan without the Help Desk the page shows the message and hides the Raise a ticket button and form', async () => {
    const message = "Raising tickets isn't included in the Free plan. It comes with the Basic plan.";
    const { ctx, els } = env(ok({ status: 'error', code: 'upgrade_required', message }, 403));
    await ctx.loadTickets();
    assert.strictEqual(els['new-ticket-btn'].hidden, true); assert.strictEqual(els['new-ticket'].hidden, true);
    assert(els.tickets.innerHTML.includes('class="empty"') && els.tickets.innerHTML.includes("Raising tickets isn&#39;t included in the Free plan. It comes with the Basic plan."), els.tickets.innerHTML);
  });

  await t('any other failure keeps the button and just shows what went wrong', async () => {
    const { ctx, els } = env(ok({ status: 'error', message: 'The Help Desk is busy.' }, 503));
    await ctx.loadTickets();
    assert.strictEqual(els['new-ticket-btn'].hidden, false);
    assert(els.tickets.innerHTML.includes('The Help Desk is busy.'));
  });

  await t('on a plan with the Help Desk the list loads as before and the button stays', async () => {
    const { ctx, els } = env(ok({ status: 'success', tickets: [], categories: { bug: 'A problem' } }));
    await ctx.loadTickets();
    assert.strictEqual(els['new-ticket-btn'].hidden, false);
    assert(els.tickets.innerHTML.includes('No tickets yet'), els.tickets.innerHTML);
  });

  await t('a message from the server is shown as text, never as markup', async () => {
    const { ctx, els } = env(ok({ status: 'error', code: 'upgrade_required', message: '<img src=x onerror=alert(1)>' }, 403));
    await ctx.loadTickets();
    assert(!els.tickets.innerHTML.includes('<img') && els.tickets.innerHTML.includes('&lt;img'));
  });

  done();
})();
