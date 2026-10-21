# ============================================================
# fetch_missing_stocks.py
# Fetches only the stocks NOT yet in the database
# Uses history() which works with latest yfinance
# ============================================================
import os
from dotenv import load_dotenv
load_dotenv()
import yfinance as yf
import mysql.connector
from datetime import datetime
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tickers import ALL_TICKERS, TICKER_SECTOR_MAP

def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "stock_market_pro_db")
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        config["ssl_disabled"] = False
    return mysql.connector.connect(**config)

def get_existing_tickers():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM stocks_live")
    existing = {r[0] for r in cursor.fetchall()}
    cursor.close(); conn.close()
    return existing

def fetch_stock(ticker):
    t = yf.Ticker(ticker)

    # Use history() — most reliable method
    hist = t.history(period="5d")
    if hist is None or hist.empty:
        raise ValueError("no price history")

    latest = hist.iloc[-1]
    prev = hist.iloc[-2] if len(hist) > 1 else latest

    price = float(latest['Close'])
    prev_close = float(prev['Close'])
    if price <= 0:
        raise ValueError("invalid price")

    change = price - prev_close
    change_pct = (change / prev_close * 100) if prev_close > 0 else 0

    # Get additional info
    try:
        info = t.info
    except:
        info = {}

    return {
        "ticker": ticker,
        "sector": TICKER_SECTOR_MAP.get(ticker, "Unknown"),
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "industry": info.get("industry"),
        "current_price": round(price, 4),
        "open_price": float(latest['Open']) if latest['Open'] else None,
        "high": float(latest['High']) if latest['High'] else None,
        "low": float(latest['Low']) if latest['Low'] else None,
        "prev_close": round(prev_close, 4),
        "price_change": round(change, 4),
        "price_change_pct": round(change_pct, 4),
        "volume": int(latest['Volume']) if latest['Volume'] else None,
        "avg_volume": info.get("averageVolume"),
        "market_cap": info.get("marketCap"),
        "week_52_high": info.get("fiftyTwoWeekHigh"),
        "week_52_low": info.get("fiftyTwoWeekLow"),
    }

def save_stock(d, conn, cursor):
    cursor.execute("""
        INSERT INTO stocks_live (
            ticker, sector, industry, company_name,
            current_price, open_price, high, low, prev_close,
            price_change, price_change_pct,
            week_52_high, week_52_low,
            volume, avg_volume, market_cap, last_updated
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            sector=VALUES(sector), company_name=VALUES(company_name),
            current_price=VALUES(current_price), open_price=VALUES(open_price),
            high=VALUES(high), low=VALUES(low), prev_close=VALUES(prev_close),
            price_change=VALUES(price_change), price_change_pct=VALUES(price_change_pct),
            week_52_high=VALUES(week_52_high), week_52_low=VALUES(week_52_low),
            volume=VALUES(volume), avg_volume=VALUES(avg_volume),
            market_cap=VALUES(market_cap), last_updated=VALUES(last_updated)
    """, (
        d["ticker"], d["sector"], d["industry"], d["company_name"],
        d["current_price"], d["open_price"], d["high"], d["low"], d["prev_close"],
        d["price_change"], d["price_change_pct"],
        d["week_52_high"], d["week_52_low"],
        d["volume"], d["avg_volume"], d["market_cap"],
        datetime.now()
    ))
    conn.commit()

def main():
    print("=" * 60)
    print("  Fetch Missing Stocks")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Get missing tickers
    existing = get_existing_tickers()
    missing = [t for t in ALL_TICKERS if t not in existing]
    print(f"\n  Total tickers in file: {len(ALL_TICKERS)}")
    print(f"  Already in database: {len(existing)}")
    print(f"  Missing (to fetch): {len(missing)}\n")

    if not missing:
        print("  ✅ All tickers already in database!")
        return

    conn = get_db()
    cursor = conn.cursor()
    success, failed = 0, 0
    failed_list = []

    for i, ticker in enumerate(missing, 1):
        try:
            d = fetch_stock(ticker)
            save_stock(d, conn, cursor)
            success += 1
            print(f"  ✅ [{i}/{len(missing)}] {ticker} — ${d['current_price']}")
        except Exception as e:
            failed += 1
            failed_list.append(ticker)
            print(f"  ❌ [{i}/{len(missing)}] {ticker} — {str(e)[:50]}")

        if i % 10 == 0:
            print(f"\n  Progress: {i}/{len(missing)} | ✅ {success} | ❌ {failed}\n")

        time.sleep(0.3)

    cursor.close()
    conn.close()

    print(f"\n{'='*60}")
    print(f"  Done! ✅ {success} added | ❌ {failed} failed")
    if failed_list:
        print(f"  Failed tickers: {', '.join(failed_list[:20])}")
    print("=" * 60)

if __name__ == "__main__":
    main()
