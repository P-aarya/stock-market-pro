# ============================================================
# STOCK MARKET PRO — scheduler.py (v4, batched)
# Refreshes live prices for all stocks every 60 seconds.
# Reads ticker list from the DB itself (stocks_live table),
# so it never depends on tickers.py structure.
# Prints FULL error details for failures.
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
        "database": os.getenv("DB_NAME", "stock_market_pro_db")
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


def run_cycle(tickers, cycle_num):
    """One round over every stock, BATCH_SIZE at a time. Returns True if Yahoo rate-limited us."""
    saved, no_data, failed = 0, [], 0
    error_samples = []
    rate_limited = False
    conn = get_db()
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    for b, batch in enumerate(batches, 1):
        try:
            quotes = fetch_quotes(batch)
        except YFRateLimitError:
            reset_yahoo_key()
            rate_limited = True
            failed += sum(len(x) for x in batches[b - 1:])
            error_samples.append("Yahoo: Too Many Requests (rate limited) - stopping this round")
            break
        except Exception as e:
            reset_yahoo_key()                           # e.g. "Invalid Crumb": get a fresh Yahoo key next time
            if yahoo_refused(e):
                # Yahoo is refusing us (401 / 403 / 429). Asking again straight away only keeps the block going,
                # so stop this round; main() then waits 5 minutes (then 10, 20, up to 30) before trying again.
                rate_limited = True
                failed += sum(len(x) for x in batches[b - 1:])
                error_samples.append(f"Yahoo refused the request ({type(e).__name__}: {str(e)[:80].strip()}) - stopping this round")
                break
            failed += len(batch)
            if len(error_samples) < 3:
                error_samples.append(f"batch {b} ({batch[0]}...): {type(e).__name__}: {str(e)[:120]}")
            time.sleep(PAUSE_BETWEEN_BATCHES)
            continue
        rows = []
        for t in batch:
            row = quote_to_row(quotes[t]) if t in quotes else None
            if row:
                rows.append((t, row))
            else:
                no_data.append(t)
        if rows:
            try:
                conn = save_with_retry(conn, rows)
                saved += len(rows)
            except Exception as e:
                failed += len(rows)
                if len(error_samples) < 3:
                    error_samples.append(f"saving batch {b}: {type(e).__name__}: {str(e)[:120]}")
        if b % 5 == 0:
            print(f"  ⏳ Progress: {min(b * BATCH_SIZE, len(tickers))}/{len(tickers)} | ✅ {saved} saved")
        if b < len(batches):
            time.sleep(PAUSE_BETWEEN_BATCHES)
    try:
        conn.close()
    except Exception:
        pass
    print(f"\n  Cycle {cycle_num} done: ✅ {saved} saved | ⚪ {len(no_data)} no price from Yahoo | ❌ {failed} failed"
          f" | {datetime.now().strftime('%H:%M:%S')}")
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
    print("  STOCK MARKET PRO — Scheduler v4 (batched)")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    # Test DB connection upfront with clear error
    try:
        tickers = get_tickers()
        print(f"  ✅ MySQL connected — {len(tickers)} tickers loaded from DB\n")
    except Exception as e:
        print("  ❌ CANNOT CONNECT TO MYSQL — scheduler cannot run.")
        print(f"  Error: {e}")
        print("  Fix: start MySQL80 service (Windows key → services → MySQL80 → Start)")
        return

    cycle = 1
    backoff = 0
    while True:
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
            time.sleep(backoff)
        else:
            backoff = 0
            time.sleep(max(5, CYCLE_SECONDS - (time.time() - started)))

if __name__ == "__main__":
    main()
