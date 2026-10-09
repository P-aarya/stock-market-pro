import os
from dotenv import load_dotenv
load_dotenv()
import yfinance as yf
import mysql.connector
import db
from datetime import datetime
import time

load_dotenv()

# Database connection
conn = db.get_connection()
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

        # Get financials for revenue and net income
        try:
            fin = yf.Ticker(ticker).financials
            revenue = float(fin.loc['Total Revenue'].iloc[0]) if fin is not None and not fin.empty and 'Total Revenue' in fin.index else None
            net_income = float(fin.loc['Net Income'].iloc[0]) if fin is not None and not fin.empty and 'Net Income' in fin.index else None
        except:
            revenue = None
            net_income = None

        cursor.execute("""
            INSERT INTO stocks_fundamentals (
                ticker, pe_ratio, forward_pe, pb_ratio, ps_ratio, ev_ebitda,
                eps, revenue, net_income, profit_margin, roe, roa,
                debt_to_equity, current_ratio, quick_ratio,
                market_cap, market_cap_category, revenue_growth, earnings_growth,
                operating_margin, gross_margin, free_cash_flow,
                dividend_yield, payout_ratio, beta,
                fifty_two_week_high, fifty_two_week_low,
                shares_outstanding, float_shares, short_ratio,
                recommendation_mean, target_mean_price, peg_ratio, last_updated
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            ON DUPLICATE KEY UPDATE
                pe_ratio=VALUES(pe_ratio), forward_pe=VALUES(forward_pe),
                pb_ratio=VALUES(pb_ratio), ps_ratio=VALUES(ps_ratio),
                ev_ebitda=VALUES(ev_ebitda), eps=VALUES(eps),
                revenue=VALUES(revenue), net_income=VALUES(net_income),
                profit_margin=VALUES(profit_margin), roe=VALUES(roe), roa=VALUES(roa),
                debt_to_equity=VALUES(debt_to_equity), current_ratio=VALUES(current_ratio),
                quick_ratio=VALUES(quick_ratio), market_cap=VALUES(market_cap),
                market_cap_category=VALUES(market_cap_category),
                revenue_growth=VALUES(revenue_growth), earnings_growth=VALUES(earnings_growth),
                operating_margin=VALUES(operating_margin), gross_margin=VALUES(gross_margin),
                free_cash_flow=VALUES(free_cash_flow), dividend_yield=VALUES(dividend_yield),
                payout_ratio=VALUES(payout_ratio), beta=VALUES(beta),
                fifty_two_week_high=VALUES(fifty_two_week_high),
                fifty_two_week_low=VALUES(fifty_two_week_low),
                shares_outstanding=VALUES(shares_outstanding), float_shares=VALUES(float_shares),
                short_ratio=VALUES(short_ratio), recommendation_mean=VALUES(recommendation_mean),
                target_mean_price=VALUES(target_mean_price), peg_ratio=VALUES(peg_ratio),
                last_updated=VALUES(last_updated)
        """, (
            ticker,
            info.get("trailingPE"), info.get("forwardPE"),
            info.get("priceToBook"), info.get("priceToSalesTrailing12Months"),
            info.get("enterpriseToEbitda"), info.get("trailingEps"),
            revenue, net_income,
            info.get("profitMargins"), info.get("returnOnEquity"), info.get("returnOnAssets"),
            info.get("debtToEquity"), info.get("currentRatio"), info.get("quickRatio"),
            market_cap, category,
            info.get("revenueGrowth"), info.get("earningsGrowth"),
            info.get("operatingMargins"), info.get("grossMargins"),
            info.get("freeCashflow"), info.get("dividendYield"), info.get("payoutRatio"),
            info.get("beta"), info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow"),
            info.get("sharesOutstanding"), info.get("floatShares"), info.get("shortRatio"),
            info.get("recommendationMean"), info.get("targetMeanPrice"),
            info.get("trailingPegRatio"), datetime.now()
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
