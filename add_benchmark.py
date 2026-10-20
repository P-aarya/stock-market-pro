"""
add_benchmark.py  -  puts the "Portfolio vs S&P 500 Benchmark" section back
into the Portfolio tab.

Run it from your project folder (the one with api.py and dashboard.html):

    python add_benchmark.py

What it does:
  * api.py         -> adds (or replaces) the /api/portfolio/benchmark endpoint
  * dashboard.html -> adds (or replaces) the benchmark card on the Portfolio
                      page, its JavaScript, and loads it when the tab opens
  * Makes a backup of both files first (api.py.bak, dashboard.html.bak)
  * Safe to run more than once - it replaces its own block instead of
    adding a second copy.
"""
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
API_FILE = os.path.join(HERE, "api.py")
HTML_FILE = os.path.join(HERE, "dashboard.html")

PY_START = "# === PORTFOLIO vs S&P 500 BENCHMARK (start) ==="
PY_END = "# === PORTFOLIO vs S&P 500 BENCHMARK (end) ==="

API_BLOCK = PY_START + r'''
@app.get("/api/portfolio/benchmark")
def portfolio_benchmark():
    """Compare portfolio performance vs S&P 500 (SPY)."""
    try:
        import yfinance as yf
        import pandas as pd
        from datetime import date, timedelta

        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM portfolio")
        rows = cur.fetchall()
        tickers = sorted({str(r.get("ticker") or "").upper() for r in rows if r.get("ticker")})
        live = {}
        if tickers:
            ph = ",".join(["%s"] * len(tickers))
            cur.execute(
                f"SELECT ticker, current_price, company_name, sector FROM stocks_live WHERE ticker IN ({ph})",
                tickers,
            )
            for r in cur.fetchall():
                live[str(r["ticker"]).upper()] = r
        cur.close()
        conn.close()

        def pick(row, *names):
            for n in names:
                if n in row and row[n] is not None:
                    return row[n]
            return None

        holdings = []
        for r in rows:
            t = str(r.get("ticker") or "").upper()
            qty = float(pick(r, "quantity", "shares", "qty") or 0)
            avg = float(pick(r, "avg_buy_price", "avg_cost", "buy_price", "avg_price") or 0)
            if not t or qty <= 0 or avg <= 0:
                continue
            bd = pick(r, "buy_date", "date_added", "purchase_date", "added_at", "added_on", "created_at")
            bd = str(bd)[:10] if bd else None
            info = live.get(t, {})
            holdings.append({
                "ticker": t, "quantity": qty, "avg_buy_price": avg, "buy_date": bd,
                "current_price": float(info["current_price"]) if info.get("current_price") else None,
                "company_name": info.get("company_name") or t,
                "sector": info.get("sector") or "",
            })

        if not holdings:
            return {"error": "No holdings found"}

        dates = [h["buy_date"] for h in holdings if h["buy_date"]]
        start_date = min(dates) if dates else (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
        for h in holdings:
            if not h["buy_date"]:
                h["buy_date"] = start_date

        def clean(hist):
            s = hist["Close"].copy()
            idx = s.index
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            s.index = idx.normalize()
            return s[~s.index.duplicated(keep="last")]

        # S&P 500 via SPY
        spy_hist = yf.Ticker("SPY").history(start=start_date)
        if spy_hist.empty:
            return {"error": "Could not fetch S&P 500 data"}
        spy = clean(spy_hist)
        spy_start = float(spy.iloc[0])
        spy_current = float(spy.iloc[-1])
        spy_return = round((spy_current - spy_start) / spy_start * 100, 2)

        # Real daily prices for every holding, lined up on SPY trading days
        closes = {}
        for t in sorted({h["ticker"] for h in holdings}):
            try:
                hist = yf.Ticker(t).history(start=start_date)
                if not hist.empty:
                    closes[t] = clean(hist).reindex(spy.index).ffill()
            except Exception:
                continue

        total_invested = 0.0
        total_current = 0.0
        holdings_data = []
        for h in holdings:
            price = h["current_price"]
            if price is None and h["ticker"] in closes:
                last = closes[h["ticker"]].dropna()
                price = float(last.iloc[-1]) if len(last) else None
            if price is None:
                continue
            invested = h["quantity"] * h["avg_buy_price"]
            current_val = h["quantity"] * price
            total_invested += invested
            total_current += current_val
            stock_return = round((price - h["avg_buy_price"]) / h["avg_buy_price"] * 100, 2)

            # What SPY did from this stock's own buy date
            try:
                spy_at_buy = float(spy[spy.index >= pd.Timestamp(h["buy_date"])].iloc[0])
                spy_stock_return = round((spy_current - spy_at_buy) / spy_at_buy * 100, 2)
            except Exception:
                spy_stock_return = spy_return

            holdings_data.append({
                "ticker": h["ticker"],
                "company_name": h["company_name"],
                "sector": h["sector"],
                "stock_return": stock_return,
                "spy_return": spy_stock_return,
                "difference": round(stock_return - spy_stock_return, 2),
                "beating": stock_return > spy_stock_return,
                "invested": round(invested, 2),
                "current_value": round(current_val, 2),
            })

        portfolio_return = round((total_current - total_invested) / total_invested * 100, 2) if total_invested > 0 else 0
        difference = round(portfolio_return - spy_return, 2)

        # Daily portfolio line: on each day, value of the holdings bought by then
        # vs what was paid for those holdings
        combined_chart = []
        spy_chart = []
        for d in spy.index:
            ds = d.strftime("%Y-%m-%d")
            s_ret = round((float(spy[d]) - spy_start) / spy_start * 100, 2)
            spy_chart.append({"date": ds, "spy_return": s_ret})
            value = cost = 0.0
            for h in holdings:
                if h["buy_date"] > ds or h["ticker"] not in closes:
                    continue
                px = closes[h["ticker"]].get(d)
                if px is None or pd.isna(px):
                    continue
                value += h["quantity"] * float(px)
                cost += h["quantity"] * h["avg_buy_price"]
            if cost > 0:
                combined_chart.append({
                    "date": ds,
                    "portfolio_return": round((value - cost) / cost * 100, 2),
                    "spy_return": s_ret,
                })

        return {
            "portfolio_return": portfolio_return,
            "spy_return": spy_return,
            "difference": difference,
            "beating_market": portfolio_return > spy_return,
            "total_invested": round(total_invested, 2),
            "total_current": round(total_current, 2),
            "start_date": start_date,
            "holdings": holdings_data,
            "spy_chart": spy_chart[-252:],
            "combined_chart": combined_chart[-252:],
        }
    except Exception as e:
        print(f"Benchmark error: {e}")
        return {"error": str(e)}
''' + PY_END + "\n"

CARD_START = "<!-- BENCHMARK-CARD-START -->"
CARD_END = "<!-- BENCHMARK-CARD-END -->"
CARD_BLOCK = CARD_START + '''
  <!-- Portfolio vs S&P 500 Benchmark -->
  <div class="card" style="margin-bottom:16px;" id="benchmark-card">
    <div class="chdr">
      <div class="ctitle">📊 Portfolio vs S&amp;P 500 Benchmark</div>
      <button onclick="loadBenchmark()" style="background:var(--bg3);border:1px solid var(--b2);border-radius:7px;padding:5px 12px;color:var(--cyan);font-size:11px;font-weight:700;cursor:pointer;">🔄 Refresh</button>
    </div>
    <div id="benchmark-content"><div class="load"><div class="spin"></div>Loading benchmark data...</div></div>
  </div>
  ''' + CARD_END + "\n"

JS_START = "<!-- BENCHMARK-JS-START -->"
JS_END = "<!-- BENCHMARK-JS-END -->"
JS_BLOCK = JS_START + r'''
<script>
// ─── PORTFOLIO vs S&P 500 BENCHMARK ───────────────────────────
let _benchLoading = false, _benchChart = null;
async function loadBenchmark(){
  const el = document.getElementById('benchmark-content');
  if(!el || _benchLoading) return;
  _benchLoading = true;
  el.innerHTML = '<div class="load"><div class="spin"></div>Fetching S&P 500 comparison data... (takes a few seconds)</div>';
  try {
    const base = (typeof API !== 'undefined') ? API : '';
    const res = await fetch(base + '/api/portfolio/benchmark');
    const data = await res.json();
    if(!data || data.error || data.portfolio_return === undefined){
      const msg = data && data.error === 'No holdings found'
        ? 'Add stocks to your portfolio to see benchmark comparison.'
        : 'Error loading benchmark: ' + ((data && data.error) || 'restart api.py and try again');
      el.innerHTML = `<div style="color:var(--t3);padding:20px;text-align:center;">${msg}</div>`;
      return;
    }

    const beating = data.beating_market;
    const diff = data.difference;
    const diffCol = diff >= 0 ? 'var(--green)' : 'var(--red)';
    const diffIcon = diff >= 0 ? '▲' : '▼';
    const sg = v => (v>=0?'+':'') + Number(v).toFixed(2) + '%';

    el.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:16px;border-bottom:1px solid var(--b2);">
        <div style="text-align:center;">
          <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;margin-bottom:6px;">Your Portfolio</div>
          <div style="font-size:28px;font-weight:900;color:${data.portfolio_return>=0?'var(--green)':'var(--red)'};">${sg(data.portfolio_return)}</div>
          <div style="font-size:11px;color:var(--t3);">Since ${data.start_date}</div>
        </div>
        <div style="text-align:center;border-left:1px solid var(--b2);border-right:1px solid var(--b2);">
          <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;margin-bottom:6px;">S&amp;P 500</div>
          <div style="font-size:28px;font-weight:900;color:${data.spy_return>=0?'var(--green)':'var(--red)'};">${sg(data.spy_return)}</div>
          <div style="font-size:11px;color:var(--t3);">Same period</div>
        </div>
        <div style="text-align:center;">
          <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;margin-bottom:6px;">${beating?'Beating Market 🏆':'Lagging Market'}</div>
          <div style="font-size:28px;font-weight:900;color:${diffCol};">${diffIcon} ${Math.abs(diff).toFixed(2)}%</div>
          <div style="font-size:11px;color:var(--t3);">${beating?'Above S&amp;P 500':'Below S&amp;P 500'}</div>
        </div>
      </div>

      <div style="padding:16px;border-bottom:1px solid var(--b2);">
        <div style="height:220px;"><canvas id="benchmark-chart"></canvas></div>
      </div>

      <div style="padding:16px;">
        <div style="font-size:12px;font-weight:800;color:var(--t3);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px;">Holdings vs S&amp;P 500</div>
        <div class="tw"><table>
          <thead><tr><th>Stock</th><th>Company</th><th>Your Return</th><th>S&amp;P 500 Return</th><th>Difference</th><th>Status</th></tr></thead>
          <tbody>
            ${(data.holdings||[]).map(h=>`
              <tr onclick="if(typeof openFS==='function')openFS('${h.ticker}')" style="cursor:pointer;">
                <td class="sym">${h.ticker}</td>
                <td style="color:var(--t2);font-size:12px;">${h.company_name||''}</td>
                <td class="${h.stock_return>=0?'g':'r'} mono">${sg(h.stock_return)}</td>
                <td class="${h.spy_return>=0?'g':'r'} mono">${sg(h.spy_return)}</td>
                <td class="${h.difference>=0?'g':'r'} mono" style="font-weight:800;">${sg(h.difference)}</td>
                <td>${h.beating?'<span style="color:var(--green);font-weight:800;">✅ Beating</span>':'<span style="color:var(--red);font-weight:700;">⚠️ Lagging</span>'}</td>
              </tr>`).join('')}
          </tbody>
        </table></div>
      </div>`;

    setTimeout(()=>{
      const canvas = document.getElementById('benchmark-chart');
      if(!canvas) return;
      const chartData = data.combined_chart || [];
      if(!chartData.length){
        canvas.parentElement.innerHTML = '<div style="color:var(--t3);text-align:center;padding:60px;">Not enough data to draw chart. Add holdings with buy dates.</div>';
        return;
      }
      const cfg = {
        type:'line',
        data:{
          labels: chartData.map(d=>d.date),
          datasets:[
            {label:'Your Portfolio',data:chartData.map(d=>d.portfolio_return),borderColor:'#00c8ff',backgroundColor:'rgba(0,200,255,0.05)',borderWidth:2,pointRadius:0,tension:0.3,fill:true},
            {label:'S&P 500',data:chartData.map(d=>d.spy_return),borderColor:'#00e676',backgroundColor:'rgba(0,230,118,0.05)',borderWidth:2,pointRadius:0,tension:0.3,fill:true,borderDash:[5,5]}
          ]
        },
        options:{
          responsive:true,maintainAspectRatio:false,
          plugins:{
            legend:{labels:{color:'#c0cce0',font:{size:11}}},
            tooltip:{mode:'index',intersect:false,callbacks:{label:c=>c.dataset.label+': '+(c.raw>=0?'+':'')+c.raw.toFixed(2)+'%'}}
          },
          scales:{
            x:{ticks:{color:'#58697a',maxTicksLimit:8,font:{size:10}},grid:{color:'rgba(28,28,48,0.3)'}},
            y:{ticks:{color:'#58697a',callback:v=>v.toFixed(1)+'%'},grid:{color:'rgba(28,28,48,0.3)'}}
          },
          interaction:{mode:'index',intersect:false}
        }
      };
      if(typeof mkC === 'function'){ mkC('benchmark-chart', cfg); }
      else { if(_benchChart) _benchChart.destroy(); _benchChart = new Chart(canvas.getContext('2d'), cfg); }
    }, 100);
  } catch(e){
    console.error('Benchmark:', e);
    el.innerHTML = '<div style="color:var(--t3);padding:20px;text-align:center;">Error loading benchmark. Make sure api.py is running and you have holdings added.</div>';
  } finally {
    _benchLoading = false;
  }
}

// Load the benchmark whenever the Portfolio tab is opened
(function(){
  if(typeof showPage !== 'function' || showPage.__bench) return;
  const orig = showPage;
  showPage = function(n, btn){
    const r = orig.apply(this, arguments);
    if(n === 'portfolio') loadBenchmark();
    return r;
  };
  showPage.__bench = true;
  const pg = document.getElementById('page-portfolio');
  if(pg && pg.classList.contains('on')) loadBenchmark();
})();
</script>
''' + JS_END + "\n"


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def strip_block(text, start, end):
    pattern = re.escape(start) + r".*?" + re.escape(end) + r"\n?"
    return re.sub(pattern, "", text, flags=re.S)


def remove_old_endpoint(text):
    """Remove any older /api/portfolio/benchmark function (not ours)."""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("@app.") and "/api/portfolio/benchmark" in lines[i]:
            i += 1
            # skip def line and indented body (and blank lines inside it)
            while i < len(lines) and (lines[i].startswith("def ") or lines[i].startswith((" ", "\t")) or lines[i].strip() == ""):
                if lines[i].strip() == "" and i + 1 < len(lines) and lines[i + 1] and not lines[i + 1].startswith((" ", "\t")):
                    break
                i += 1
            print("  - removed an older /api/portfolio/benchmark endpoint")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def patch_api():
    text = read(API_FILE)
    text = strip_block(text, PY_START, PY_END)
    text = remove_old_endpoint(text)
    m = re.search(r"^if __name__ == ['\"]__main__['\"]:", text, flags=re.M)
    if m:
        text = text[:m.start()] + API_BLOCK + "\n\n" + text[m.start():]
    else:
        text = text.rstrip("\n") + "\n\n\n" + API_BLOCK
    write(API_FILE, text)
    print("  - api.py: /api/portfolio/benchmark endpoint added")


def patch_html():
    text = read(HTML_FILE)
    text = strip_block(text, CARD_START, CARD_END)
    text = strip_block(text, JS_START, JS_END)

    # Remove any older benchmark card that was not added by this script
    old = re.search(r'[ \t]*(<!-- Portfolio vs S&P 500 Benchmark -->\s*)?<div class="card"[^>]*id="benchmark-card"', text)
    if old:
        depth, j = 0, old.start()
        for tag in re.finditer(r"<div\b|</div>", text[old.start():]):
            depth += 1 if tag.group().startswith("<div") else -1
            if depth == 0:
                j = old.start() + tag.end()
                break
        text = text[:old.start()] + text[j:].lstrip("\n")
        print("  - removed an older benchmark card")

    page = re.search(r'<div class="page[^"]*" id="page-portfolio">', text)
    if not page:
        sys.exit("Could not find the Portfolio page (id=\"page-portfolio\") in dashboard.html - nothing changed.")
    next_page = re.search(r'<div class="page[^"]*" id="page-', text[page.end():])
    limit = page.end() + (next_page.start() if next_page else len(text))
    g2 = text.find('<div class="g2">', page.end(), limit)
    if g2 != -1:
        line_start = text.rfind("\n", 0, g2) + 1
        text = text[:line_start] + "  " + CARD_BLOCK + text[line_start:]
        print("  - dashboard.html: benchmark card added above Allocation")
    else:
        text = text[:page.end()] + "\n  " + CARD_BLOCK + text[page.end():]
        print("  - dashboard.html: benchmark card added at top of Portfolio page")

    body = text.rfind("</body>")
    if body == -1:
        text = text + "\n" + JS_BLOCK
    else:
        text = text[:body] + JS_BLOCK + text[body:]
    write(HTML_FILE, text)
    print("  - dashboard.html: benchmark JavaScript added")


if __name__ == "__main__":
    for f in (API_FILE, HTML_FILE):
        if not os.path.exists(f):
            sys.exit(f"Not found: {f}\nPut add_benchmark.py in the same folder as api.py and dashboard.html.")
    shutil.copy2(API_FILE, API_FILE + ".bak")
    shutil.copy2(HTML_FILE, HTML_FILE + ".bak")
    print("Backups saved: api.py.bak, dashboard.html.bak")
    patch_api()
    patch_html()
    print("Done. Restart api.py, then open the Portfolio tab.")
