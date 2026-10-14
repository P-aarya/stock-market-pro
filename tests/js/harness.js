// Shared helpers for the dashboard tests. The dashboard is a single HTML file, so each test pulls the
// relevant stretch of its JavaScript out of dashboard.html and runs it in a sandbox with a fake page.
const fs = require('fs'), path = require('path'), vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'dashboard.html'), 'utf8');

/** The text of dashboard.html from `start` up to (not including) `end`. */
function between(start, end) {
  const a = html.indexOf(start);
  if (a < 0) throw new Error(`Marker not found in dashboard.html: ${start}`);
  const b = html.indexOf(end, a + start.length);
  if (b < 0) throw new Error(`End marker not found in dashboard.html: ${end}`);
  return html.slice(a, b);
}

// escapeHtml + renderMarkdown, used by nearly every feature
const SHARED = between('function escapeHtml', 'const formatBountyText');

// Small formatting helpers the dashboard defines near the top
const FORMATTERS = `
  const f2=(n,d=2)=>n!=null?Number(n).toFixed(d):'--';
  const fp=n=>n!=null?'$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'--';
  const fb=n=>{if(n==null)return'--';const a=Math.abs(n);if(a>=1e12)return'$'+(n/1e12).toFixed(2)+'T';if(a>=1e9)return'$'+(n/1e9).toFixed(2)+'B';if(a>=1e6)return'$'+(n/1e6).toFixed(2)+'M';return'$'+Number(n).toLocaleString('en-US',{maximumFractionDigits:0});};
  const gc=v=>v>0?'g':v<0?'r':'';
  const mkRat=r=>r?'<rat>'+r+'</rat>':'';
`;

/** A fake page. Elements are created on demand; ids listed in `ids` exist from the start. */
class FakePage {
  constructor(ids = []) {
    this.registry = {};
    const page = this;
    this.El = class {
      constructor(tag) {
        this.tag = tag; this.kids = []; this.parent = null; this.style = {}; this.dataset = {};
        this.value = ''; this.textContent = ''; this.checked = false; this.disabled = false; this.readOnly = false;
        this.className = ''; this.scrollTop = 0; this.scrollHeight = 0; this._html = '';
        const sets = new Set(); this.classList = { s: sets, add: c => sets.add(c), remove: c => sets.delete(c) };
      }
      set id(v) { this._id = v; page.registry[v] = this; }
      get id() { return this._id; }
      set innerHTML(h) {
        this._html = h;
        for (const m of String(h).matchAll(/id="([^"]+)"/g)) if (!page.registry[m[1]]) { const c = new page.El('x'); c._id = m[1]; c.parent = this; page.registry[m[1]] = c; }
      }
      get innerHTML() { return this._html; }
      get childElementCount() { return this.kids.length; }
      appendChild(c) { this.kids.push(c); c.parent = this; if (c.id) page.registry[c.id] = c; this._html += c._html || ''; }
      remove() { if (this.parent && this.parent.kids) this.parent.kids = this.parent.kids.filter(k => k !== this); delete page.registry[this._id]; }
      focus() {} scrollIntoView() {} getContext() { return {}; }
    };
    ids.forEach(id => this.el(id));
  }
  el(id) { if (!this.registry[id]) { const e = new this.El('div'); e.id = id; } return this.registry[id]; }
  get document() {
    return { getElementById: id => this.registry[id] || null, createElement: t => new this.El(t) };
  }
}

/** A tiny test runner: `await t('name', async () => {...})`, then `done()`. */
function suite(title) {
  let pass = 0, fail = 0;
  console.log(title);
  return {
    async t(name, fn) {
      try { await fn(); pass++; console.log('  ✓', name); }
      catch (e) { fail++; console.log('  ✗', name, '\n     ', e.message.split('\n').slice(0, 4).join('\n      ')); }
    },
    done() { console.log(`  ${pass} passed${fail ? `, ${fail} FAILED` : ''}\n`); if (fail) process.exitCode = 1; },
  };
}

/** A fetch response. */
const ok = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

/** A streaming fetch response made of the given text chunks. */
function streamResponse(chunks, status = 200) {
  const enc = new TextEncoder(); let i = 0;
  return { ok: status < 400, status, body: { getReader: () => ({ read: async () => i < chunks.length ? { done: false, value: enc.encode(chunks[i++]) } : { done: true } }) }, json: async () => ({}) };
}
const sse = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

module.exports = { html, between, SHARED, FORMATTERS, FakePage, suite, ok, streamResponse, sse, vm };
