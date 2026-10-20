"""check_portfolio.py - read-only check of the portfolio and transactions tables.
Run from your project folder:  python check_portfolio.py
It only reads - it does not change anything in the database."""
import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()
cfg = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", os.getenv("DB_PASS", "")),
    "database": os.getenv("DB_NAME", "stock_market_pro_db"),
}
if os.getenv("DB_SSL", "false").lower() == "true":
    cfg["ssl_disabled"] = False

print("Database:", cfg["host"], "/", cfg["database"])
conn = mysql.connector.connect(**cfg)
cur = conn.cursor(dictionary=True)

for table in ("portfolio", "transactions"):
    print("\n==========", table, "==========")
    try:
        cur.execute(f"SHOW COLUMNS FROM {table}")
        print("Columns:", ", ".join(c["Field"] for c in cur.fetchall()))
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        print("Rows:", cur.fetchone()["n"])
        cur.execute(f"SELECT * FROM {table} LIMIT 25")
        for row in cur.fetchall():
            print("  ", {k: str(v) for k, v in row.items()})
    except Exception as e:
        print("Could not read this table:", e)

cur.close()
conn.close()
