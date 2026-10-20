# ============================================================
# STOCK MARKET PRO — scheduler.py (v3, safe)
# Refreshes live prices for all stocks every 60 seconds.
# Reads ticker list from the DB itself (stocks_live table),
# so it never depends on tickers.py structure.
# Prints FULL error details for failures.
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
import yfinance as yf
import mysql.connector
import math
import time
import traceback
from datetime import datetime

INFO_REFRESH_SECONDS = 30 * 60      # market cap / 52-week range refresh
_last_info = {}                     # ticker -> time of last .info call

def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("DB_PASS") or "Aroot092325",
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

def fetch_live(ticker):
    """Fetch live price data using history() which is stable."""
    t = yf.Ticker(ticker)
    hist = t.history(period="5d")

    if hist is None or hist.empty:
        raise ValueError("no price data returned")
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        raise ValueError("no price data returned")

    latest = hist.iloc[-1]
    prev = hist.iloc[-2] if len(hist) > 1 else hist.iloc[-1]

    price = float(latest['Close'])
    prev_close = float(prev['Close'])
    open_price = float(latest['Open']) if _ok(latest['Open']) else None
    day_high = float(latest['High']) if _ok(latest['High']) else None
    day_low = float(latest['Low']) if _ok(latest['Low']) else None
    volume = int(latest['Volume']) if _ok(latest['Volume']) else None

    if not _ok(price) or not _ok(prev_close) or prev_close == 0:
        raise ValueError("no valid price data")

    change = price - prev_close
    change_pct = (change / prev_close) * 100

    # Market cap and 52 week range: only every 30 min per stock.
    # None = "no new value" -> the saved value is kept.
    market_cap = year_high = year_low = None
    now = time.time()
    if now - _last_info.get(ticker, 0) >= INFO_REFRESH_SECONDS:
        try:
            info = t.info or {}
            market_cap = info.get("marketCap")
            year_high = info.get("fiftyTwoWeekHigh")
            year_low = info.get("fiftyTwoWeekLow")
            if market_cap or year_high:
                _last_info[ticker] = now
        except Exception:
            pass

    return {
        "current_price":    round(price, 4),
        "price_change":     round(change, 4),
        "price_change_pct": round(change_pct, 4),
        "open_price":       round(open_price, 4) if open_price is not None else None,
        "high":             round(day_high, 4) if day_high is not None else None,
        "low":              round(day_low, 4) if day_low is not None else None,
        "prev_close":       round(prev_close, 4),
        "volume":           volume,
        "market_cap":       float(market_cap) if _ok(market_cap) else None,
        "week_52_high":     round(float(year_high), 4) if _ok(year_high) else None,
        "week_52_low":      round(float(year_low), 4) if _ok(year_low) else None,
    }

def save_live(conn, ticker, d):
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE stocks_live SET
            current_price=%s, price_change=%s, price_change_pct=%s,
            open_price=COALESCE(%s, open_price), high=COALESCE(%s, high),
            low=COALESCE(%s, low), prev_close=%s,
            volume=COALESCE(%s, volume), market_cap=COALESCE(%s, market_cap),
            week_52_high=COALESCE(%s, week_52_high), week_52_low=COALESCE(%s, week_52_low),
            last_updated=%s
        WHERE ticker=%s
    """, (d["current_price"], d["price_change"], d["price_change_pct"],
          d["open_price"], d["high"], d["low"], d["prev_close"],
          d["volume"], d["market_cap"],
          d["week_52_high"], d["week_52_low"],
          datetime.now(), ticker))
    conn.commit()
    cursor.close()

def run_cycle(tickers, cycle_num):
    saved, failed = 0, 0
    error_samples = []
    conn = get_db()
    for i, ticker in enumerate(tickers, 1):
        try:
            d = fetch_live(ticker)
            try:
                if conn is None or not conn.is_connected():
                    raise ConnectionError("dropped")
                save_live(conn, ticker, d)
            except Exception:
                # connection dropped (Aiven does this sometimes) -> reconnect and save again
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
                conn = get_db()
                save_live(conn, ticker, d)
            saved += 1
        except Exception as e:
            failed += 1
            # Keep first 3 detailed errors so we can diagnose
            if len(error_samples) < 3:
                error_samples.append(f"{ticker}: {type(e).__name__}: {str(e)[:120]}")
        if i % 50 == 0:
            print(f"  ⏳ Progress: {i}/{len(tickers)} | ✅ {saved} saved | ❌ {failed} failed")
    try:
        conn.close()
    except Exception:
        pass
    print(f"\n  Cycle {cycle_num} done: ✅ {saved} saved | ❌ {failed} failed | {datetime.now().strftime('%H:%M:%S')}")
    if error_samples:
        print("  First error details (for debugging):")
        for s in error_samples:
            print(f"    • {s}")
    print()

def main():
    print("="*60)
    print("  STOCK MARKET PRO — Scheduler v3")
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
    while True:
        try:
            if cycle > 1:
                try:
                    new_list = get_tickers()
                    if len(new_list) != len(tickers):
                        print(f"  Ticker list changed: {len(tickers)} -> {len(new_list)}")
                    tickers = new_list
                except Exception:
                    pass    # keep the old list if the DB hiccups
            run_cycle(tickers, cycle)
        except Exception:
            print("  ❌ Cycle crashed — full error:")
            traceback.print_exc()
        cycle += 1
        time.sleep(60)

if __name__ == "__main__":
    main()
