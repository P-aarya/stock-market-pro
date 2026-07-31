import os
from dotenv import load_dotenv
load_dotenv()
import yfinance as yf
import mysql.connector
from datetime import datetime
import time

# Database connection
conn = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", "Aroot092325"),
    database=os.getenv("DB_NAME", "stock_market_pro_db")
)
cursor = conn.cursor()

# Get all tickers from your existing table
cursor.execute("SELECT ticker FROM stocks_live")
tickers = [row[0] for row in cursor.fetchall()]

print(f"Total tickers to process: {len(tickers)}")

success = 0
failed = 0

for i, ticker in enumerate(tickers):
    try:
        info = yf.Ticker(ticker).info

        # Calculate market cap category
        market_cap = info.get("marketCap")
        if market_cap:
            if market_cap < 2_000_000_000:
                category = "small"
            elif market_cap < 10_000_000_000:
                category = "mid"
            else:
                category = "large"
        else:
            category = None

        cursor.execute("""
            UPDATE stocks_fundamentals SET
                market_cap = %s,
                market_cap_category = %s,
                revenue_growth = %s,
                earnings_growth = %s,
                operating_margin = %s,
                gross_margin = %s,
                free_cash_flow = %s,
                dividend_yield = %s,
                payout_ratio = %s,
                beta = %s,
                fifty_two_week_high = %s,
                fifty_two_week_low = %s,
                shares_outstanding = %s,
                float_shares = %s,
                short_ratio = %s,
                recommendation_mean = %s,
                target_mean_price = %s,
                peg_ratio = %s,
                last_updated = %s
            WHERE ticker = %s
        """, (
            market_cap,
            category,
            info.get("revenueGrowth"),
            info.get("earningsGrowth"),
            info.get("operatingMargins"),
            info.get("grossMargins"),
            info.get("freeCashflow"),
            info.get("dividendYield"),
            info.get("payoutRatio"),
            info.get("beta"),
            info.get("fiftyTwoWeekHigh"),
            info.get("fiftyTwoWeekLow"),
            info.get("sharesOutstanding"),
            info.get("floatShares"),
            info.get("shortRatio"),
            info.get("recommendationMean"),
            info.get("targetMeanPrice"),
            info.get("trailingPegRatio"),
            datetime.now(),
            ticker
        ))

        conn.commit()
        success += 1

        # Print progress every 50 stocks
        if (i + 1) % 50 == 0:
            print(f"Progress: {i + 1}/{len(tickers)} done")

        # Small delay to avoid overwhelming yfinance
        time.sleep(0.5)

    except Exception as e:
        failed += 1
        print(f"Failed: {ticker} — {e}")
        continue

conn.close()
print(f"\nDone. Success: {success} | Failed: {failed}")
