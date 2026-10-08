"""
fetch_missing_data.py
Fetches technicals, analyst, historical and earnings data
for stocks that are missing it. Run this once for new stocks.
"""
import os
from dotenv import load_dotenv
load_dotenv()
import yfinance as yf
import mysql.connector
from datetime import datetime
import time
import pandas as pd

def get_db():
    config = {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME"),
        "ssl_disabled": False
    }
    return mysql.connector.connect(**config)

def calculate_rsi(prices, period=14):
    try:
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi.iloc[-1]), 2)
    except:
        return None

def calculate_macd(prices):
    try:
        ema12 = prices.ewm(span=12, adjust=False).mean()
        ema26 = prices.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        return round(float(macd.iloc[-1]), 4), round(float(signal.iloc[-1]), 4), round(float(hist.iloc[-1]), 4)
    except:
        return None, None, None

# Get list of stocks missing technicals
conn = get_db()
cursor = conn.cursor(dictionary=True)
cursor.execute("""
    SELECT l.ticker FROM stocks_live l
    LEFT JOIN stocks_technicals t ON l.ticker = t.ticker
    WHERE t.ticker IS NULL AND l.current_price IS NOT NULL
    ORDER BY l.market_cap DESC
""")
tickers = [r['ticker'] for r in cursor.fetchall()]
cursor.close()
conn.close()
print(f"Stocks missing technicals: {len(tickers)}")

success = 0
failed = 0

for i, ticker in enumerate(tickers):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1y")
        
        if hist is None or hist.empty or len(hist) < 30:
            failed += 1
            continue

        closes = hist['Close']
        ma50 = round(float(closes.rolling(50).mean().iloc[-1]), 4) if len(closes) >= 50 else None
        ma200 = round(float(closes.rolling(200).mean().iloc[-1]), 4) if len(closes) >= 200 else None
        rsi = calculate_rsi(closes)
        macd, macd_signal, macd_hist = calculate_macd(closes)
        
        try:
            vol = round(float(closes.pct_change().std() * (252**0.5) * 100), 2)
        except:
            vol = None

        golden_cross = 1 if (ma50 and ma200 and ma50 > ma200) else 0

        info = {}
        try:
            info = t.info or {}
        except:
            pass

        # Fresh connection for each stock
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SET innodb_lock_wait_timeout=5")
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")

        cursor.execute("""
            INSERT INTO stocks_technicals 
            (ticker, rsi_14, macd, macd_signal, macd_hist, ma_50, ma_200, 
             golden_cross, volatility, last_updated)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
            rsi_14=VALUES(rsi_14), macd=VALUES(macd), macd_signal=VALUES(macd_signal),
            macd_hist=VALUES(macd_hist), ma_50=VALUES(ma_50), ma_200=VALUES(ma_200),
            golden_cross=VALUES(golden_cross), volatility=VALUES(volatility),
            last_updated=VALUES(last_updated)
        """, (ticker, rsi, macd, macd_signal, macd_hist, ma50, ma200,
              golden_cross, vol, datetime.now()))

        if info.get('recommendationKey') or info.get('targetMeanPrice'):
            cursor.execute("""
                INSERT INTO stocks_analyst
                (ticker, analyst_rating, target_price, num_analysts, upside_pct, last_updated)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                analyst_rating=VALUES(analyst_rating), target_price=VALUES(target_price),
                num_analysts=VALUES(num_analysts), upside_pct=VALUES(upside_pct),
                last_updated=VALUES(last_updated)
            """, (
                ticker,
                info.get('recommendationKey'),
                info.get('targetMeanPrice'),
                info.get('numberOfAnalystOpinions'),
                round((info.get('targetMeanPrice', 0) - info.get('currentPrice', 0)) / max(info.get('currentPrice', 1), 1) * 100, 2) if info.get('targetMeanPrice') and info.get('currentPrice') else None,
                datetime.now()
            ))

        try:
            fin = t.financials
            if fin is not None and not fin.empty:
                for col in fin.columns[:5]:
                    year = str(col)[:4]
                    def safe(key):
                        try: return float(fin.loc[key, col]) if key in fin.index else None
                        except: return None
                    rev = safe('Total Revenue')
                    ni = safe('Net Income')
                    if rev or ni:
                        cursor.execute("""
                            INSERT INTO stocks_historical (ticker, year, revenue, net_income, last_updated)
                            VALUES (%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE revenue=VALUES(revenue), net_income=VALUES(net_income)
                        """, (ticker, year, rev, ni, datetime.now()))
        except:
            pass

        conn.commit()
        cursor.close()
        conn.close()
        success += 1

        if (i + 1) % 25 == 0:
            print(f"Progress: {i+1}/{len(tickers)} | Success: {success} | Failed: {failed}")

        time.sleep(0.3)

    except Exception as e:
        failed += 1
        try: cursor.close(); conn.close()
        except: pass
        if failed <= 5:
            print(f"Failed {ticker}: {e}")
        continue

print(f"\nDone! Success: {success} | Failed: {failed}")
