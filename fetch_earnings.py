# ============================================================
# fetch_earnings.py — Fetches proper earnings dates for all stocks
# Run this separately — faster than full fetch_stocks.py
# ============================================================

import yfinance as yf
import mysql.connector
from datetime import datetime
import time
from tickers import ALL_TICKERS

def get_connection():
    return mysql.connector.connect(
        host="localhost", port=3306, user="root",
        password="Aroot092325", database="stock_market_pro_db"
    )

def fetch_earnings(ticker, cursor, conn):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        now = datetime.now()

        # Get next earnings date
        next_date = None
        earn_ts = info.get("earningsTimestamp") or info.get("earningsCallTimestampStart")
        if earn_ts:
            next_date = datetime.fromtimestamp(earn_ts).date()

        eps_est   = info.get("forwardEps")
        eps_act   = info.get("trailingEps")
        rev_est   = info.get("revenueEstimate") or info.get("totalRevenue")
        rev_act   = info.get("totalRevenue")

        # Surprise %
        surprise = None
        if eps_est and eps_act:
            surprise = round(((eps_act - eps_est) / abs(eps_est)) * 100, 2)

        cursor.execute("""
            INSERT INTO stocks_earnings
                (ticker, next_earnings_date, eps_estimate, eps_actual,
                 earnings_surprise_pct, revenue_estimate, revenue_actual, last_updated)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                next_earnings_date=VALUES(next_earnings_date),
                eps_estimate=VALUES(eps_estimate),
                eps_actual=VALUES(eps_actual),
                earnings_surprise_pct=VALUES(earnings_surprise_pct),
                revenue_estimate=VALUES(revenue_estimate),
                revenue_actual=VALUES(revenue_actual),
                last_updated=VALUES(last_updated)
        """, (ticker, next_date, eps_est, eps_act, surprise, rev_est, rev_act, now))
        conn.commit()
        return True
    except Exception as e:
        return False

def main():
    print("=" * 60)
    print("  EARNINGS FETCH — All Stocks")
    print(f"  Total: {len(ALL_TICKERS)} stocks")
    print("=" * 60)

    conn = get_connection()
    cursor = conn.cursor()
    success = 0
    failed  = 0

    for i, ticker in enumerate(ALL_TICKERS, 1):
        result = fetch_earnings(ticker, cursor, conn)
        if result:
            success += 1
            print(f"  ✅ [{i}/{len(ALL_TICKERS)}] {ticker}")
        else:
            failed += 1
            print(f"  ❌ [{i}/{len(ALL_TICKERS)}] {ticker} failed")

        if i % 50 == 0:
            print(f"\n  ⏳ Pausing... {success} done, {failed} failed\n")
            time.sleep(5)

    cursor.close()
    conn.close()
    print(f"\n✅ Done! {success} updated, {failed} failed")

if __name__ == "__main__":
    main()
