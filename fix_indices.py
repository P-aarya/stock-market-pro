# ============================================================
# fix_indices.py — Fixes SPY, QQQ, DIA, IWM in MySQL
# ============================================================
import yfinance as yf
import mysql.connector
import db
from datetime import datetime

conn = db.get_connection()
cursor = conn.cursor()

INDICES = {
    "SPY": "S&P 500 ETF",
    "QQQ": "NASDAQ 100 ETF",
    "DIA": "Dow Jones ETF",
    "IWM": "Russell 2000 ETF"
}

for ticker, name in INDICES.items():
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="2d")
        if hist.empty:
            print(f"❌ {ticker} — no history data")
            continue

        current_price = round(float(hist["Close"].iloc[-1]), 2)
        prev_close    = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else None
        price_change  = round(current_price - prev_close, 2) if prev_close else None
        price_chg_pct = round((price_change / prev_close) * 100, 2) if price_change and prev_close else None
        volume        = int(hist["Volume"].iloc[-1])
        high          = round(float(hist["High"].iloc[-1]), 2)
        low           = round(float(hist["Low"].iloc[-1]), 2)
        open_p        = round(float(hist["Open"].iloc[-1]), 2)

        cursor.execute("""
            INSERT INTO stocks_live 
                (ticker, sector, industry, company_name, current_price, open_price,
                 high, low, prev_close, price_change, price_change_pct,
                 volume, last_updated)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                current_price=VALUES(current_price),
                open_price=VALUES(open_price),
                high=VALUES(high), low=VALUES(low),
                prev_close=VALUES(prev_close),
                price_change=VALUES(price_change),
                price_change_pct=VALUES(price_change_pct),
                volume=VALUES(volume),
                last_updated=VALUES(last_updated)
        """, (
            ticker, "Indices", "Market ETF", name,
            current_price, open_p, high, low,
            prev_close, price_change, price_chg_pct,
            volume, datetime.now()
        ))
        conn.commit()
        print(f"✅ {ticker} — ${current_price} ({'+' if price_chg_pct >= 0 else ''}{price_chg_pct}%)")
    except Exception as e:
        print(f"❌ {ticker} — {e}")

cursor.close()
conn.close()
print("\n✅ Indices fixed! Refresh your dashboard.")
