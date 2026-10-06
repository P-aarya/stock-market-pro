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

# Check data completeness across all tables for new vs old stocks
print("=" * 60)
print("DATA COMPLETENESS CHECK")
print("=" * 60)

tables = {
    "stocks_live": "current_price",
    "stocks_fundamentals": "pe_ratio",
    "stocks_technicals": "rsi_14",
    "stocks_analyst": "analyst_rating",
    "stocks_predictions": "buy_signal",
    "stocks_historical": "revenue",
    "stocks_earnings": "eps_actual",
    "stocks_dividends": "dividend_yield",
}

for table, col in tables.items():
    cursor.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE {col} IS NOT NULL")
    r = cursor.fetchone()
    cursor.execute(f"SELECT COUNT(*) as total FROM stocks_live")
    total = cursor.fetchone()['total']
    print(f"{table:25} — {r['cnt']:4} / {total} stocks have data ({r['cnt']/total*100:.0f}%)")

print()
print("SAMPLE — New commodity stocks (NEM, FCX, BHP, ADM):")
for ticker in ['NEM','FCX','BHP','ADM','PLUG','FSLR']:
    cursor.execute("""
        SELECT l.ticker, l.current_price,
               f.pe_ratio, f.profit_margin,
               t.rsi_14,
               a.analyst_rating,
               p.buy_signal,
               (SELECT COUNT(*) FROM stocks_historical WHERE ticker=l.ticker) as hist_years
        FROM stocks_live l
        LEFT JOIN stocks_fundamentals f ON l.ticker=f.ticker
        LEFT JOIN stocks_technicals t ON l.ticker=t.ticker
        LEFT JOIN stocks_analyst a ON l.ticker=a.ticker
        LEFT JOIN stocks_predictions p ON l.ticker=p.ticker
        WHERE l.ticker = %s
    """, (ticker,))
    r = cursor.fetchone()
    if r:
        print(f"  {r['ticker']:6} price=${r['current_price']} PE={r['pe_ratio']} RSI={r['rsi_14']} signal={r['buy_signal']} hist={r['hist_years']}yrs analyst={r['analyst_rating']}")
    else:
        print(f"  {ticker} — NOT IN DATABASE")

conn.close()
