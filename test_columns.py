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
cursor = conn.cursor()

# Check columns in stocks_live
cursor.execute("DESCRIBE stocks_live")
cols = cursor.fetchall()
print("stocks_live columns:")
for c in cols:
    print(f"  {c[0]} — {c[1]}")

# Simple test
cursor.execute("SELECT ticker, company_name, current_price FROM stocks_live WHERE ticker = 'NEM'")
print("\nNEM row:", cursor.fetchall())

# Test IN clause with list
tickers = ['NEM', 'XOM', 'ADM']
fmt = ','.join(['%s']*len(tickers))
cursor.execute(f"SELECT ticker FROM stocks_live WHERE ticker IN ({fmt})", tickers)
print("IN clause result:", cursor.fetchall())

conn.close()
