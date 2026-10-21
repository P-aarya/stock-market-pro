const assert = require('assert');
const { between, SHARED, FakePage, suite, ok, streamResponse, sse, vm } = require('./harness');

const code = between('// ─── BOUNTY AI CHATBOT', '// Show badge after 4 seconds');

function makeEnv(fetchImpl, store = {}) {
  const page = new FakePage(['bounty-win', 'bounty-badge', 'bounty-input', 'bounty-send', 'bounty-msgs', 'b-reply-bar', 'b-reply-text', 'b-edit-bar']);
  const calls = [];
  const ctx = {
    console, setTimeout, clearTimeout, AbortController, TextDecoder, JSON, Math, Date, Number, String, Array, Object, parseInt,
    API: 'http://x', LI: { chat: '<svg></svg>', user: '<svg></svg>' }, watchlist: ['AAPL', 'MSFT'], openFS() {},
    document: page.document,
    localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } },
    navigator: {},
    fetch: async (url, opts) => { calls.push({ url, body: JSON.parse(opts.body) }); return fetchImpl(url, opts, calls.length); },
  };
  vm.createContext(ctx); vm.runInContext(code, ctx);
  return { ctx, registry: page.registry, calls, store, run: s => vm.runInContext(s, ctx) };
}
const NORMAL = { status: 'success', intent: 'education', recommendation: 'ans' };
const asNormal = (data = NORMAL, status = 200) => (url) => url.endsWith('/stream') ? ok({}, 404) : ok(data, status);

(async () => {
  const { t, done } = suite('Chat (Bounty)');

  await t('Text is HTML-escaped; **bold**, bullets and paragraphs still render', async () => {
    const e = makeEnv(asNormal());
    const out = e.ctx.renderMarkdown('<img src=x onerror=alert(1)> **hi**\n- one\n- two');
    assert(!out.includes('<img') && out.includes('&lt;img') && out.includes('<strong>hi</strong>') && out.includes('<ul><li>one</li><li>two</li></ul>'), out);
  });

  await t('Reply/Edit buttons carry only the message id, so quotes and newlines cannot break them', async () => {
    const e = makeEnv(asNormal());
    e.run(`addBountyMsg('ai','He said "hi" and\\nit\\'s <b>bold</b> ' + 'x'.repeat(80))`);
    const h = e.registry['bm-1']._html;
    const onclicks = [...h.matchAll(/onclick="([^"]*)"/g)].map(m => m[1]);
    assert(onclicks.includes("replyBountyMsg('bm-1')") && onclicks.every(o => !o.includes('He said') && !o.includes('<b>')), JSON.stringify(onclicks));
    assert(!h.includes('<b>bold'));
  });

  await t('Enter sends, Shift+Enter does not', async () => {
    const e = makeEnv(asNormal());
    e.registry['bounty-input'].value = 'hello';
    e.run(`bountyKey({key:'Enter',shiftKey:true,isComposing:false,preventDefault(){}})`); assert.equal(e.calls.length, 0);
    e.run(`bountyKey({key:'Enter',shiftKey:false,isComposing:false,preventDefault(){}})`); await new Promise(r => setTimeout(r, 30));
    assert(e.calls.length >= 1);
  });

  await t('Streaming: text appears as it arrives, then is replaced by the final answer with stock cards', async () => {
    const e = makeEnv((url) => streamResponse([
      sse({ delta: 'Gold is ' }), sse({ delta: '**up**' }).slice(0, 12),   // an event split across two chunks
      sse({ delta: '**up**' }).slice(12) + sse({ delta: ' today.' }),
      sse({ final: { status: 'success', intent: 'commodity', recommendation: 'Gold is **up** today.\n\n🕒 note', top_stocks: [{ ticker: 'NEM', company_name: 'Newmont', current_price: 100, price_change_pct: 1 }] } }),
    ]));
    const seen = [];
    const orig = e.ctx.showLiveBountyText;
    e.ctx.showLiveBountyText = (txt) => { seen.push(txt); return orig(txt); };
    e.registry['bounty-input'].value = 'tell me about gold';
    await e.run('sendBounty()');
    assert.equal(e.calls[0].url, 'http://x/agent/recommend/stream');
    assert(seen.length >= 3 && seen[0] === 'Gold is ' && seen[seen.length - 1] === 'Gold is **up** today.', JSON.stringify(seen));
    assert(!e.registry['bounty-live'], 'live bubble removed');
    const last = e.run('bountyMsgs[bountyMsgs.length-1]');
    assert(last.role === 'ai' && last.text.includes('🕒 note') && last.stocks.length === 1, JSON.stringify(last));
    assert.equal(e.run('bountySending'), false);
  });

  await t('Streaming: an error event becomes a readable message, and nothing half-written is kept', async () => {
    const e = makeEnv(() => streamResponse([sse({ delta: 'Partial ' }), sse({ error: { status: 429, message: 'The AI service is busy. Please try again in a minute.' } })]));
    e.registry['bounty-input'].value = 'q'; await e.run('sendBounty()');
    const last = e.run('bountyMsgs[bountyMsgs.length-1]');
    assert(last.error && last.text.includes('busy') && !e.registry['bounty-live']);
  });

  await t('Streaming: a dropped connection (no final event) is reported', async () => {
    const e = makeEnv(() => streamResponse([sse({ delta: 'Half an ans' })]));
    e.registry['bounty-input'].value = 'q'; await e.run('sendBounty()');
    const last = e.run('bountyMsgs[bountyMsgs.length-1]'); assert(last.error && last.text.includes('connection dropped'));
  });

  await t('Falls back to the normal endpoint when the server has no streaming route', async () => {
    const e = makeEnv(asNormal({ status: 'success', intent: 'education', recommendation: 'plain answer' }));
    e.registry['bounty-input'].value = 'q'; await e.run('sendBounty()');
    assert.deepEqual(e.calls.map(c => c.url), ['http://x/agent/recommend/stream', 'http://x/agent/recommend']);
    assert.equal(e.run('bountyMsgs[bountyMsgs.length-1].text'), 'plain answer');
  });

  await t('Request: history excludes the greeting, reply_to is separate, watchlist is sent', async () => {
    const e = makeEnv(asNormal({ status: 'success', intent: 'commodity', recommendation: 'Gold answer', top_stocks: [{ ticker: 'NEM', company_name: 'Newmont', current_price: 100, price_change_pct: 1 }] }));
    e.run('showBountyGreeting()');
    e.registry['bounty-input'].value = 'tell me about gold'; await e.run('sendBounty()');
    const first = e.calls[0].body;
    assert.deepEqual(first.chat_history, []); assert.equal(first.query, 'tell me about gold'); assert.equal(first.reply_to, ''); assert.deepEqual(first.portfolio_tickers, ['AAPL', 'MSFT']);
    e.run("replyBountyMsg('bm-3')"); e.registry['bounty-input'].value = 'and silver?'; await e.run('sendBounty()');
    const second = e.calls[e.calls.length - 1].body;
    assert.equal(second.reply_to, 'Gold answer'); assert.deepEqual(second.chat_history.map(m => m.role), ['user', 'assistant']);
    assert(!JSON.stringify(second).includes('Replying to'));
    assert(e.registry['bm-3']._html.includes('NEM'), 'commodity answers show stock cards');
  });

  await t('Server errors show the server message (not "rephrase") and stay out of later history', async () => {
    let n = 0;
    const e = makeEnv((url) => url.endsWith('/stream') ? ok({ status: 'error', message: 'The AI service is busy. Please try again in a minute.' }, ++n === 1 ? 429 : 200) : ok(NORMAL));
    e.registry['bounty-input'].value = 'q1'; await e.run('sendBounty()');
    const err = e.run('bountyMsgs[bountyMsgs.length-1]'); assert(err.error && err.text.includes('busy') && !err.text.includes('rephrase'));
    e.registry['bounty-input'].value = 'q2'; await e.run('sendBounty()');
    assert.deepEqual(e.calls[e.calls.length - 1].body.chat_history.map(m => m.content), ['q1']);
  });

  await t('Double send is ignored while a request is pending', async () => {
    let release; const gate = new Promise(r => { release = r; });
    const e = makeEnv(async (url) => { await gate; return url.endsWith('/stream') ? streamResponse([sse({ final: NORMAL })]) : ok(NORMAL); });
    e.registry['bounty-input'].value = 'first'; const p1 = e.run('sendBounty()');
    await new Promise(r => setTimeout(r, 5)); assert.equal(e.registry['bounty-send'].disabled, true);
    e.registry['bounty-input'].value = 'second'; await e.run('sendBounty()');
    release(); await p1; assert.equal(e.calls.length, 1); assert.equal(e.registry['bounty-send'].disabled, false); assert.equal(e.registry['bounty-input'].value, 'second');
  });

  await t('Edit re-asks: later messages are dropped and the edited text is sent with the right history', async () => {
    const e = makeEnv(() => streamResponse([sse({ final: NORMAL })]));
    for (const q of ['first', 'second']) { e.registry['bounty-input'].value = q; await e.run('sendBounty()'); }
    assert.equal(e.run('bountyMsgs.length'), 4);
    e.run("editBountyMsg('bm-1')"); e.registry['bounty-input'].value = 'first (edited)'; await e.run('sendBounty()');
    assert.equal(e.run('bountyMsgs.map(m=>m.text).join("|")'), 'first (edited)|ans');
    const last = e.calls[e.calls.length - 1].body; assert.equal(last.query, 'first (edited)'); assert.deepEqual(last.chat_history, []);
  });

  await t('Delete removes a message from the history the server sees', async () => {
    const e = makeEnv(() => streamResponse([sse({ final: NORMAL })]));
    e.registry['bounty-input'].value = 'secret'; await e.run('sendBounty()');
    e.run("deleteBountyMsg('bm-1')"); e.registry['bounty-input'].value = 'next'; await e.run('sendBounty()');
    assert(!JSON.stringify(e.calls[e.calls.length - 1].body.chat_history).includes('secret'));
  });

  await t('The chat survives a reload, and Clear wipes it', async () => {
    const store = {};
    const a = makeEnv(() => streamResponse([sse({ final: { ...NORMAL, recommendation: 'remember me' } })]), store);
    a.registry['bounty-input'].value = 'hi there'; await a.run('sendBounty()');
    const b = makeEnv(() => ok({}), store); b.run('initBountyChat()');
    assert.equal(b.run('bountyMsgs.map(m=>m.text).join("|")'), 'hi there|remember me');
    b.run('clearBounty()'); assert.equal(b.run('bountyMsgs.length'), 1); assert.equal(JSON.parse(store['bounty_chat_v1:anon']).length, 1);
  });

  await t('A timeout or network failure shows a friendly error and unlocks the UI', async () => {
    const e = makeEnv(() => { const x = new Error('aborted'); x.name = 'AbortError'; throw x; });
    e.registry['bounty-input'].value = 'slow'; await e.run('sendBounty()');
    assert(e.run('bountyMsgs[1].text').includes('took too long')); assert.equal(e.run('bountySending'), false);
    const f = makeEnv(() => { throw new TypeError('Failed to fetch'); }); f.registry['bounty-input'].value = 'x'; await f.run('sendBounty()');
    assert(f.run('bountyMsgs[1].text').includes('trouble connecting'));
  });

  done();
})();
