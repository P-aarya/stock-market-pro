from dotenv import load_dotenv
load_dotenv()
import os
import mysql.connector

conn = mysql.connector.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    ssl_disabled=False
)
cursor = conn.cursor(dictionary=True)

# Test exact batch query
tickers = ['NEM','XOM','ADM','FCX','BHP']
placeholders = ','.join(['%s'] * len(tickers))
cursor.execute(f"""
    SELECT ticker, company_name, current_price, price_change_pct, market_cap, analyst_rating
    FROM stocks_live
    WHERE ticker IN ({placeholders})
""", tickers)
rows = cursor.fetchall()
print(f"Found {len(rows)} rows:")
for r in rows:
    print(r)
conn.close()
