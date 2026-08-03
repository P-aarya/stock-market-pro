# ============================================================
# STOCK MARKET PRO — scheduler.py (v2, rebuilt)
# Refreshes live prices for all stocks every 60 seconds.
# Reads ticker list from the DB itself (stocks_live table),
# so it never depends on tickers.py structure.
# Prints FULL error details for failures.
# ============================================================

import os
from dotenv import load_dotenv
load_dotenv()
import yfinance as yf
import mysql.connector
import time
import traceback
from datetime import datetime

def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", "Aroot092325"),
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

def fetch_live(ticker):
    """Fetch live price data. Returns dict or raises."""
    t = yf.Ticker(ticker)

    # fast_info is quick and reliable for prices
    fi = t.fast_info
    price      = fi.get("lastPrice")
    prev_close = fi.get("previousClose")
    open_price = fi.get("open")
    day_high   = fi.get("dayHigh")
    day_low    = fi.get("dayLow")
    volume     = fi.get("lastVolume")
    market_cap = fi.get("marketCap")
    year_high  = fi.get("yearHigh")
    year_low   = fi.get("yearLow")

    if price is None or prev_close in (None, 0):
        raise ValueError("no price data (possibly delisted)")

    change     = price - prev_close
    change_pct = (change / prev_close) * 100

    return {
        "current_price":    round(float(price), 4),
        "price_change":     round(float(change), 4),
        "price_change_pct": round(float(change_pct), 4),
        "open_price":       round(float(open_price), 4) if open_price else None,
        "high":             round(float(day_high), 4) if day_high else None,
        "low":              round(float(day_low), 4) if day_low else None,
        "prev_close":       round(float(prev_close), 4),
        "volume":           int(volume) if volume else None,
        "market_cap":       float(market_cap) if market_cap else None,
        "week_52_high":     round(float(year_high), 4) if year_high else None,
        "week_52_low":      round(float(year_low), 4) if year_low else None,
    }

def save_live(ticker, d):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE stocks_live SET
            current_price=%s, price_change=%s, price_change_pct=%s,
            open_price=%s, high=%s, low=%s, prev_close=%s,
            volume=%s, market_cap=%s,
            week_52_high=%s, week_52_low=%s,
            last_updated=%s
        WHERE ticker=%s
    """, (d["current_price"], d["price_change"], d["price_change_pct"],
          d["open_price"], d["high"], d["low"], d["prev_close"],
          d["volume"], d["market_cap"],
          d["week_52_high"], d["week_52_low"],
          datetime.now(), ticker))
    conn.commit()
    cursor.close(); conn.close()

def run_cycle(tickers, cycle_num):
    saved, failed = 0, 0
    error_samples = []
    for i, ticker in enumerate(tickers, 1):
        try:
            d = fetch_live(ticker)
            save_live(ticker, d)
            saved += 1
        except Exception as e:
            failed += 1
            # Keep first 3 detailed errors so we can diagnose
            if len(error_samples) < 3:
                error_samples.append(f"{ticker}: {type(e).__name__}: {str(e)[:120]}")
        if i % 50 == 0:
            print(f"  ⏳ Progress: {i}/{len(tickers)} | ✅ {saved} saved | ❌ {failed} failed")
    print(f"\n  Cycle {cycle_num} done: ✅ {saved} saved | ❌ {failed} failed | {datetime.now().strftime('%H:%M:%S')}")
    if error_samples:
        print("  First error details (for debugging):")
        for s in error_samples:
            print(f"    • {s}")
    print()

def main():
    print("="*60)
    print("  STOCK MARKET PRO — Scheduler v2")
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
            run_cycle(tickers, cycle)
        except Exception:
            print("  ❌ Cycle crashed — full error:")
            traceback.print_exc()
        cycle += 1
        time.sleep(60)

if __name__ == "__main__":
    main()
