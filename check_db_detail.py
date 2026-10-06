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

# Total in stocks_live
cursor.execute("SELECT COUNT(*) as total FROM stocks_live")
print(f"Total in stocks_live: {cursor.fetchone()['total']}")

# With price
cursor.execute("SELECT COUNT(*) as cnt FROM stocks_live WHERE current_price IS NOT NULL")
print(f"With live price: {cursor.fetchone()['cnt']}")

# Without price
cursor.execute("SELECT COUNT(*) as cnt FROM stocks_live WHERE current_price IS NULL")
print(f"Without price (failed to fetch): {cursor.fetchone()['cnt']}")

# Sector breakdown
cursor.execute("SELECT sector, COUNT(*) as cnt FROM stocks_live GROUP BY sector ORDER BY cnt DESC")
print("\nBy sector:")
for r in cursor.fetchall():
    print(f"  {r['sector']:30} {r['cnt']}")

# Check if commodity ETF tickers are in DB
print("\nChecking commodity company tickers in DB:")
commodity_cos = ['NEM','FCX','BHP','ADM','ZIM','SBLK','AWK','FSLR','PLUG','BOTZ','ROBO']
for t in commodity_cos:
    cursor.execute("SELECT ticker, current_price, sector FROM stocks_live WHERE ticker=%s", (t,))
    r = cursor.fetchone()
    if r:
        print(f"  {r['ticker']:6} ✅ price=${r['current_price']} sector={r['sector']}")
    else:
        print(f"  {t:6} ❌ NOT IN DB")

conn.close()
