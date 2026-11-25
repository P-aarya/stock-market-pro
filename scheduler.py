# ============================================================
# STOCK MARKET PRO — scheduler.py (v5, spark route)
# Refreshes live prices for all stocks every 60 seconds.
# Reads ticker list from the DB itself (stocks_live table),
# so it never depends on tickers.py structure.
# Prints FULL error details for failures.
#
# v6 changes:
#  - Safety timer: if no progress for 10 minutes (something hangs), it prints
#    where it is stuck and exits, so Render restarts it at once instead of
#    the site silently showing old prices.
#  - The database connection gives up after 20 s instead of waiting forever.
#
# v5 changes:
#  - Live prices come from Yahoo's "spark" route, which needs no sign-in key
#    ("crumb"). Yahoo sometimes refuses that key to cloud servers for hours;
#    prices now keep flowing anyway. 50 stocks per request.
#  - Market cap and the 52-week range come from the batch quote every 30
#    minutes when Yahoo allows it (one try, then wait 30 min).
#  - Stocks Yahoo has no price for are asked again only once an hour.
#
# v4 changes:
#  - Asks Yahoo for 150 stocks in ONE request (about 20 requests a round
#    instead of 3,000+), so Yahoo no longer blocks us for "too many requests".
#    The same answer has market cap and the 52-week range, so the slow
#    per-stock "details" call is gone.
#  - If Yahoo still says "too many requests" or refuses the request
#    (401 "Invalid Crumb", 403), the round stops and the updater waits
#    5 min (then 10, 20, 30) instead of hammering Yahoo.
#  - Saves each batch of 150 in one go.
#
# v3 changes:
#  - Never blanks market cap / 52-week high-low: if Yahoo sends nothing,
#    the saved value is kept (COALESCE).
#  - Asks Yahoo for the slow "details" call (.info) only every 30 minutes
#    per stock instead of every minute -> far fewer rate-limit failures.
#  - Re-reads the ticker list every cycle, so newly added stocks are
#    picked up without a restart.
#  - Uses one database connection per cycle instead of one per stock.
# ============================================================

import os
from dotenv import load_dotenv
load_dotenv()
from yfinance.data import YfData
from yahoo_batch import reset_yahoo_key
from yfinance.exceptions import YFRateLimitError
import mysql.connector
import math
import time
import traceback
import faulthandler
import sys
import threading
from datetime import datetime

BATCH_SIZE = 150                    # stocks per request to Yahoo
PAUSE_BETWEEN_BATCHES = 2           # seconds
CYCLE_SECONDS = 60                  # a new round starts every 60 seconds
BACKOFF_START = 5 * 60              # if Yahoo says "too many requests": wait 5 min,
BACKOFF_MAX = 30 * 60               # then 10, 20, up to 30 min, until it answers again
QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote?"

def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("DB_PASS") or "",
        "database": os.getenv("DB_NAME", "stock_market_pro_db"),
        "connection_timeout": 20,                    # never wait forever for the database to answer
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        config["ssl_disabled"] = False
    return mysql.connector.connect(**config)

def get_tickers():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM stocks_live ORDER BY market_cap DESC")
    rows = [r[0] for r in cursor.fetchall()]
    cursor.close(); conn.close()
    return rows

def _ok(x):
    try:
        return x is not None and not math.isnan(float(x))
    except (TypeError, ValueError):
        return False

def _num(x, digits=4):
    return round(float(x), digits) if _ok(x) else None

def fetch_quotes(tickers):
    """One request to Yahoo for many stocks. Returns {ticker: quote}. Raises YFRateLimitError if Yahoo refuses."""
    data = YfData()          # yfinance's own connection: handles Yahoo's cookie and browser look-alike
    res = data.get_raw_json(QUOTE_URL, params={"symbols": ",".join(tickers), "formatted": "false"})
    out = {}
    for q in (res.get("quoteResponse") or {}).get("result") or []:
        sym = q.get("symbol")
        if sym:
            out[sym] = q
    return out

def quote_to_row(q):
    """Yahoo's quote -> the values saved in stocks_live. None if there is no usable price."""
    price = q.get("regularMarketPrice")
    prev_close = q.get("regularMarketPreviousClose")
    if not _ok(price) or not _ok(prev_close) or float(prev_close) == 0:
        return None
    price, prev_close = float(price), float(prev_close)
    change = price - prev_close
    volume = q.get("regularMarketVolume")
    return {
        "current_price":    round(price, 4),
        "price_change":     round(change, 4),
        "price_change_pct": round(change / prev_close * 100, 4),
        "open_price":       _num(q.get("regularMarketOpen")),
        "high":             _num(q.get("regularMarketDayHigh")),
        "low":              _num(q.get("regularMarketDayLow")),
        "prev_close":       round(prev_close, 4),
        "volume":           int(volume) if _ok(volume) else None,
        "market_cap":       float(q["marketCap"]) if _ok(q.get("marketCap")) else None,
        "week_52_high":     _num(q.get("fiftyTwoWeekHigh")),
        "week_52_low":      _num(q.get("fiftyTwoWeekLow")),
    }

SAVE_SQL = """
    UPDATE stocks_live SET
        current_price=%s, price_change=%s, price_change_pct=%s,
        open_price=COALESCE(%s, open_price), high=COALESCE(%s, high),
        low=COALESCE(%s, low), prev_close=%s,
        volume=COALESCE(%s, volume), market_cap=COALESCE(%s, market_cap),
        week_52_high=COALESCE(%s, week_52_high), week_52_low=COALESCE(%s, week_52_low),
        last_updated=%s
    WHERE ticker=%s
"""

def save_rows(conn, rows):
    """rows: list of (ticker, values). Saved together in one go."""
    now = datetime.now()
    params = [(d["current_price"], d["price_change"], d["price_change_pct"],
               d["open_price"], d["high"], d["low"], d["prev_close"],
               d["volume"], d["market_cap"], d["week_52_high"], d["week_52_low"],
               now, t) for t, d in rows]
    cursor = conn.cursor()
    cursor.executemany(SAVE_SQL, params)
    conn.commit()
    cursor.close()

def save_with_retry(conn, rows):
    """Save; if the database connection dropped, reconnect once and save again. Returns the connection."""
    try:
        if conn is None or not conn.is_connected():
            raise ConnectionError("dropped")
        save_rows(conn, rows)
        return conn
    except Exception:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        conn = get_db()
        save_rows(conn, rows)
        return conn

def yahoo_refused(e):
    """True when the error means Yahoo is refusing us (not a one-off network hiccup)."""
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403, 429):
        return True
    text = str(e)
    return any(k in text for k in ("401", "403", "429", "Unauthorized", "Too Many Requests", "Invalid Crumb"))


# ---------- the "spark" route: prices without Yahoo's sign-in key ----------
# Yahoo's batch quote (QUOTE_URL) needs a sign-in key ("crumb"), and Yahoo sometimes refuses to hand that key to cloud
# servers for hours. The spark route (the one Yahoo's own mini-charts use) needs no key, so live prices keep flowing.
# It doesn't send market cap or the P/E; those come from the batch quote every 30 minutes when Yahoo allows it.
SPARK_URL = "https://query1.finance.yahoo.com/v7/finance/spark"
SPARK_SIZES = (50, 20, 10)          # stocks per spark request; drops to the next size if Yahoo says "too many"
SPARK_PAUSE = 0.5                   # seconds between spark requests
EXTRAS_EVERY = 30 * 60              # market cap / 52-week range via the batch quote, at most this often
DEAD_RETRY = 60 * 60                # a stock Yahoo had no price for is asked again only once an hour

_spark_size = [SPARK_SIZES[0]]
_no_price_at = {}                   # ticker -> when Yahoo last had no price for it
_last_extras = [0.0]
_shown_shape = [False]


class Refused(Exception):
    """Yahoo refused the request (401 / 403 / 429)."""


def _http():
    try:
        from curl_cffi import requests as creq            # comes with yfinance; looks like a real browser to Yahoo
        return creq.Session(impersonate="chrome")
    except Exception:
        import requests
        s = requests.Session()
        s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        return s


_session = [None]


def _get(url, params):
    if _session[0] is None:
        _session[0] = _http()
    return _session[0].get(url, params=params, timeout=20)


def _last(seq):
    for v in reversed(seq or []):
        if _ok(v):
            return float(v)
    return None


def spark_to_row(sym_data):
    """One stock's spark answer -> the values saved in stocks_live, or None. Handles both shapes Yahoo uses."""
    meta, close = {}, None
    opens = highs = lows = vols = None
    if isinstance(sym_data, dict) and "response" in sym_data:            # {"symbol":..., "response":[{meta, indicators}]}
        resp = (sym_data.get("response") or [{}])[0] or {}
        meta = resp.get("meta") or {}
        q = ((resp.get("indicators") or {}).get("quote") or [{}])[0] or {}
        close, opens, highs, lows, vols = q.get("close"), q.get("open"), q.get("high"), q.get("low"), q.get("volume")
    elif isinstance(sym_data, dict):                                     # {"symbol":..., "close":[...], "chartPreviousClose":...}
        meta = sym_data
        close = sym_data.get("close")
    price = meta.get("regularMarketPrice")
    price = float(price) if _ok(price) else _last(close)
    prev = meta.get("chartPreviousClose")
    if not _ok(prev):
        prev = meta.get("previousClose")
    if not _ok(price) or not _ok(prev) or float(prev) == 0:
        return None
    price, prev = float(price), float(prev)
    change = price - prev
    vol = meta.get("regularMarketVolume")
    vol = vol if _ok(vol) else _last(vols)
    return {
        "current_price":    round(price, 4),
        "price_change":     round(change, 4),
        "price_change_pct": round(change / prev * 100, 4),
        "open_price":       _num(_last(opens)),
        "high":             _num(meta.get("regularMarketDayHigh") if _ok(meta.get("regularMarketDayHigh")) else _last(highs)),
        "low":              _num(meta.get("regularMarketDayLow") if _ok(meta.get("regularMarketDayLow")) else _last(lows)),
        "prev_close":       round(prev, 4),
        "volume":           int(vol) if _ok(vol) else None,
        "market_cap":       None,                                        # not in spark; the saved value is kept
        "week_52_high":     _num(meta.get("fiftyTwoWeekHigh")),
        "week_52_low":      _num(meta.get("fiftyTwoWeekLow")),
    }


def fetch_spark(symbols):
    """{ticker: row} for one spark request. Raises Refused if Yahoo refuses, ValueError('too many') if the batch is too big."""
    r = _get(SPARK_URL, {"symbols": ",".join(symbols), "range": "1d", "interval": "1d"})
    if r.status_code in (401, 403, 429):
        raise Refused(f"HTTP {r.status_code}")
    if r.status_code == 400 and len(symbols) > SPARK_SIZES[-1]:
        raise ValueError("too many")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    data = r.json()
    if isinstance(data, dict) and isinstance(data.get("spark"), dict):  # {"spark": {"result": [...]}}
        items = {d.get("symbol"): d for d in (data["spark"].get("result") or []) if isinstance(d, dict)}
    else:                                                                # {"AAPL": {...}, "MSFT": {...}}
        items = data if isinstance(data, dict) else {}
    out = {}
    for sym, d in items.items():
        row = spark_to_row(d)
        if sym and row:
            out[sym] = row
    if not out and not _shown_shape[0]:
        _shown_shape[0] = True
        print(f"  (spark answer had no usable prices; first part of it: {r.text[:300]!r})", flush=True)
    return out


# ---------- safety timer ----------
# If the updater stops making progress (for example a request to Yahoo or the database that never answers), it would
# otherwise sit silent forever while the site shows old prices. Every batch marks progress; a watchdog thread checks
# once a minute and, after STALL_LIMIT without progress, prints where the program is stuck and exits, so Render
# restarts it straight away (and marks the restart in Events).
STALL_LIMIT = 10 * 60
_last_beat = [time.time()]


def beat():
    _last_beat[0] = time.time()


def rest(seconds):
    """time.sleep that keeps marking progress, for the long waits after Yahoo refuses us."""
    end = time.time() + seconds
    while time.time() < end:
        beat()
        time.sleep(min(30, max(0.0, end - time.time())))
    beat()


def _watchdog():
    while True:
        time.sleep(60)
        idle = time.time() - _last_beat[0]
        if idle > STALL_LIMIT:
            print(f"\n  ⛔ No progress for {idle / 60:.0f} minutes - the price updater is stuck. Where it is stuck:", flush=True)
            faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
            print("  Exiting so Render restarts the price updater.\n", flush=True)
            os._exit(1)


def run_extras(tickers, conn):
    """Market cap and 52-week range through the batch quote, which needs Yahoo's sign-in key. Stops at the first refusal
    (and tries again in 30 minutes), so a refused key never turns into many requests. Returns (conn, saved)."""
    _last_extras[0] = time.time()
    saved = 0
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        try:
            quotes = fetch_quotes(batch)
        except Exception as e:
            reset_yahoo_key()
            print(f"  (market cap update skipped - Yahoo refused its key: {type(e).__name__}; next try in 30 min)", flush=True)
            break
        rows = [(t, quote_to_row(quotes[t])) for t in batch if t in quotes]
        rows = [(t, r) for t, r in rows if r]
        if rows:
            conn = save_with_retry(conn, rows)
            saved += len(rows)
        beat()
        time.sleep(PAUSE_BETWEEN_BATCHES)
    return conn, saved


def run_cycle(tickers, cycle_num):
    """One round over every stock through the spark route. Returns True if Yahoo refused us (main() then waits)."""
    now = time.time()
    ask = [t for t in tickers if now - _no_price_at.get(t, 0) >= DEAD_RETRY]
    skipped = len(tickers) - len(ask)
    saved, no_data, failed = 0, [], 0
    error_samples = []
    rate_limited = False
    conn = get_db()
    i = 0
    while i < len(ask):
        size = _spark_size[0]
        batch = ask[i:i + size]
        try:
            rows_by_t = fetch_spark(batch)
        except ValueError:                                  # batch too big for Yahoo: use a smaller size from now on
            smaller = [n for n in SPARK_SIZES if n < size]
            if smaller:
                _spark_size[0] = smaller[0]
                print(f"  (spark: Yahoo wants fewer stocks per request - using {smaller[0]})", flush=True)
                continue
            rows_by_t = {}
        except Refused as e:
            rate_limited = True
            failed += len(ask) - i
            error_samples.append(f"Yahoo refused the request ({e}) - stopping this round")
            break
        except Exception as e:
            failed += len(batch)
            if len(error_samples) < 3:
                error_samples.append(f"{batch[0]}...: {type(e).__name__}: {str(e)[:120]}")
            rows_by_t = None
        i += len(batch)
        if rows_by_t is not None:
            rows = []
            for t in batch:
                if t in rows_by_t:
                    rows.append((t, rows_by_t[t]))
                    _no_price_at.pop(t, None)
                else:
                    no_data.append(t)
                    _no_price_at[t] = now
            if rows:
                try:
                    conn = save_with_retry(conn, rows)
                    saved += len(rows)
                except Exception as e:
                    failed += len(rows)
                    if len(error_samples) < 3:
                        error_samples.append(f"saving: {type(e).__name__}: {str(e)[:120]}")
        beat()
        if i < len(ask):
            time.sleep(SPARK_PAUSE)
    extras = 0
    if not rate_limited and time.time() - _last_extras[0] >= EXTRAS_EVERY:
        try:
            conn, extras = run_extras([t for t in tickers if t not in _no_price_at], conn)
        except Exception as e:
            print(f"  (market cap update failed: {type(e).__name__}: {str(e)[:100]})", flush=True)
    try:
        conn.close()
    except Exception:
        pass
    print(f"\n  Cycle {cycle_num} done: ✅ {saved} saved | ⚪ {len(no_data)} no price from Yahoo"
          f"{f' | {skipped} skipped (no price lately, retried hourly)' if skipped else ''} | ❌ {failed} failed"
          f"{f' | market cap updated for {extras}' if extras else ''} | {datetime.now().strftime('%H:%M:%S')}")
    if no_data:
        print(f"  No price for: {', '.join(no_data[:10])}{' ...' if len(no_data) > 10 else ''}")
    if error_samples:
        print("  First error details (for debugging):")
        for s in error_samples[:3]:
            print(f"    • {s}")
    print()
    return rate_limited

def main():
    print("="*60)
    print("  STOCK MARKET PRO — Scheduler v6 (spark route + safety timer)")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    # Test DB connection upfront with clear error
    try:
        tickers = get_tickers()
        print(f"  ✅ MySQL connected — {len(tickers)} tickers loaded from DB\n")
    except Exception as e:
        print("  ❌ CANNOT CONNECT TO MYSQL — scheduler cannot run.")
        print(f"  Error: {e}")
        print("  Check that the database service (aymetrix-db on Render) is running.")
        sys.exit(1)                                      # a failed exit makes Render retry and show the failure

    threading.Thread(target=_watchdog, name="watchdog", daemon=True).start()
    try:                                                 # News tab: headlines every 30 min in their own thread, so a
        import news_store                                # slow news site never holds up prices (and can't stop them)
        news_store.start(get_db)
        print("  📰 News collection started (every 30 minutes, kept 30 days)\n")
    except Exception as e:
        print(f"  📰 News collection could not start: {e}\n")

    cycle = 1
    backoff = 0
    while True:
        beat()
        started = time.time()
        rate_limited = False
        try:
            if cycle > 1:
                try:
                    new_list = get_tickers()
                    if len(new_list) != len(tickers):
                        print(f"  Ticker list changed: {len(tickers)} -> {len(new_list)}")
                    tickers = new_list
                except Exception:
                    pass    # keep the old list if the DB hiccups
            rate_limited = run_cycle(tickers, cycle)
        except Exception:
            print("  ❌ Cycle crashed — full error:")
            traceback.print_exc()
        cycle += 1
        if rate_limited:
            backoff = min(backoff * 2, BACKOFF_MAX) if backoff else BACKOFF_START
            print(f"  ⏸  Yahoo asked us to slow down - waiting {backoff // 60} min before the next round\n", flush=True)
            rest(backoff)
        else:
            backoff = 0
            rest(max(5, CYCLE_SECONDS - (time.time() - started)))

if __name__ == "__main__":
    main()
