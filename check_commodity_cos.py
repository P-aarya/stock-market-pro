from dotenv import load_dotenv
load_dotenv()
import os, mysql.connector

conn = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT", 3306)),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    ssl_disabled=False
)
cursor = conn.cursor(dictionary=True)

# Check commodity companies specifically
tickers = ['NEM','FCX','BHP','ADM','XOM','CVX','COIN','FSLR','PLUG','ZIM','AWK','ALB']
print("Commodity Company Data Check:")
print("="*70)
for t in tickers:
    cursor.execute("""
        SELECT l.ticker, l.current_price, l.company_name, l.sector,
               f.pe_ratio, f.profit_margin, f.revenue_growth,
               t.rsi_14, t.macd,
               a.analyst_rating, a.target_price,
               sm.institutional_ownership
        FROM stocks_live l
        LEFT JOIN stocks_fundamentals f ON l.ticker=f.ticker
        LEFT JOIN stocks_technicals t ON l.ticker=t.ticker
        LEFT JOIN stocks_analyst a ON l.ticker=a.ticker
        LEFT JOIN stocks_smartmoney sm ON l.ticker=sm.ticker
        WHERE l.ticker=%s
    """, (t,))
    r = cursor.fetchone()
    if r:
        print(f"{r['ticker']:6} | price=${r['current_price']} | PE={r['pe_ratio']} | RSI={r['rsi_14']} | analyst={r['analyst_rating']} | margin={r['profit_margin']}")
    else:
        print(f"{t:6} | NOT IN DATABASE")

print("\nTotal in stocks_fundamentals:")
cursor.execute("SELECT COUNT(*) as cnt FROM stocks_fundamentals WHERE pe_ratio IS NOT NULL")
print(f"  With PE ratio: {cursor.fetchone()['cnt']}")

conn.close()
