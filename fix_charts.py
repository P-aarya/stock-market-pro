# ============================================================
# fix_charts.py — Fixes price_history/predicted_path for stocks
# that have predictions but missing chart data (tz bug fix)
# Fast: only rebuilds chart JSON, doesn't retrain models
# ============================================================

import yfinance as yf
import mysql.connector
import pandas as pd
import json
from datetime import datetime, timedelta
import time

def get_db():
    return mysql.connector.connect(
        host="localhost", port=3306, user="root",
        password="Aroot092325", database="stock_market_pro_db"
    )

def query(sql, params=None):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, params or [])
    result = cursor.fetchall()
    cursor.close()
    conn.close()
    return result

def build_paths(hist, current_price, e7, e30, e90):
    """Build price_history (1y) + predicted_path. tz-safe version."""
    try:
        # Remove timezone info entirely - convert index to naive
        hist = hist.copy()
        hist.index = pd.to_datetime(hist.index).tz_localize(None)

        one_year_ago = datetime.now() - timedelta(days=365)
        hist_1y = hist[hist.index >= one_year_ago]

        price_history = [
            {"date": str(idx)[:10], "price": round(float(row['Close']), 2)}
            for idx, row in hist_1y.iterrows()
        ]

        today = datetime.now()
        predicted_path = [{
            "date": today.strftime("%Y-%m-%d"),
            "price": round(current_price, 2),
            "upper": round(current_price, 2),
            "lower": round(current_price, 2),
            "predicted": False
        }]

        # Build predicted points from saved targets
        mapping = [
            (7,  "1W", e7),
            (30, "1M", e30),
            (90, "3M", e90),
        ]
        for days, label, vals in mapping:
            target, upper, lower = vals
            if target is None:
                continue
            future_date = today + timedelta(days=days)
            predicted_path.append({
                "date": future_date.strftime("%Y-%m-%d"),
                "price": round(float(target), 2),
                "upper": round(float(upper), 2) if upper is not None else round(float(target)*1.05, 2),
                "lower": round(float(lower), 2) if lower is not None else round(float(target)*0.95, 2),
                "predicted": True,
                "label": label
            })

        return price_history, predicted_path
    except Exception as e:
        print(f"      build_paths error: {e}")
        return [], []

def main():
    print("="*60)
    print("  FIX CHARTS — Rebuilding missing price_history/predicted_path")
    print("="*60)

    # Get stocks with predictions but missing/empty price_history
    stocks = query("""
        SELECT p.ticker, p.current_price,
               p.target_1w, p.upper_1w, p.lower_1w,
               p.target_1m, p.upper_1m, p.lower_1m,
               p.target_3m, p.upper_3m, p.lower_3m
        FROM stocks_predictions p
        WHERE p.target_1m IS NOT NULL
        AND (p.price_history IS NULL OR JSON_LENGTH(p.price_history) = 0
             OR p.predicted_path IS NULL OR JSON_LENGTH(p.predicted_path) = 0)
    """)

    print(f"  Stocks to fix: {len(stocks)}\n")

    success, failed = 0, 0
    for i, s in enumerate(stocks, 1):
        ticker = s['ticker']
        try:
            hist = yf.Ticker(ticker).history(period="1y")
            if hist.empty:
                print(f"  ❌ [{i}/{len(stocks)}] {ticker}: no price data")
                failed += 1
                continue

            current_price = float(s['current_price'])
            e7  = (s['target_1w'], s['upper_1w'], s['lower_1w'])
            e30 = (s['target_1m'], s['upper_1m'], s['lower_1m'])
            e90 = (s['target_3m'], s['upper_3m'], s['lower_3m'])

            price_history, predicted_path = build_paths(hist, current_price, e7, e30, e90)

            if not price_history or not predicted_path:
                print(f"  ❌ [{i}/{len(stocks)}] {ticker}: build failed")
                failed += 1
                continue

            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE stocks_predictions SET price_history=%s, predicted_path=%s WHERE ticker=%s",
                (json.dumps(price_history[-252:]), json.dumps(predicted_path), ticker)
            )
            conn.commit()
            cursor.close()
            conn.close()

            print(f"  ✅ [{i}/{len(stocks)}] {ticker}: fixed ({len(price_history)} pts)")
            success += 1

        except Exception as e:
            print(f"  ❌ [{i}/{len(stocks)}] {ticker}: {str(e)[:60]}")
            failed += 1

        if i % 50 == 0:
            time.sleep(3)

    print(f"\n  ✅ Fixed: {success}  ❌ Failed: {failed}")
    print("  Done! Refresh dashboard to see charts.")

if __name__ == "__main__":
    main()
