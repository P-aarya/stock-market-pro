"""check_stocks.py - read-only check: why the dashboard shows fewer stocks than before.
Run from your project folder:  python check_stocks.py
It only reads the database (and tickers.py if present). It changes nothing."""
import os
import io
import contextlib
from collections import Counter, defaultdict
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

conn = mysql.connector.connect(**cfg)
cur = conn.cursor(dictionary=True)
cur.execute("SELECT ticker, sector, current_price, last_updated FROM stocks_live")
rows = cur.fetchall()
cur.execute("SELECT MAX(last_updated) AS m FROM stocks_live")
latest = cur.fetchone()["m"]
cur.close()
conn.close()

print("Database:", cfg["host"], "/", cfg["database"])
print("Most recent price update in the table:", latest)
print()

shown, reasons = [], defaultdict(list)
for r in rows:
    t, sec, px, upd = r["ticker"], r["sector"], r["current_price"], r["last_updated"]
    if sec == "Indices":
        reasons["Index ETF (SPY/QQQ/DIA/IWM) - shown in the index cards, not counted"].append(r)
    elif px is None:
        reasons["No price saved at all"].append(r)
    elif upd is None:
        reasons["Never updated (no last_updated time)"].append(r)
    elif latest and (latest - upd).days > 14:
        reasons["Price older than 14 days - dashboard hides stale stocks"].append(r)
    else:
        shown.append(r)

print(f"Rows in stocks_live table:        {len(rows)}")
print(f"Shown on the dashboard:           {len(shown)}")
print(f"Hidden:                           {len(rows) - len(shown)}")
print()
for why, items in reasons.items():
    print(f"--- {why}: {len(items)}")
    by_sec = Counter(i["sector"] or "Unknown" for i in items)
    print("    by sector:", ", ".join(f"{s} {n}" for s, n in by_sec.most_common()))
    sample = sorted(items, key=lambda i: str(i["last_updated"] or ""))
    print("    tickers:", ", ".join(
        f"{i['ticker']}" + (f" ({str(i['last_updated'])[:10]})" if i["last_updated"] else "")
        for i in sample[:60]) + (" ..." if len(items) > 60 else ""))
    print()

# Compare with tickers.py, if it is in this folder
try:
    ns = {}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(open("tickers.py", encoding="utf-8").read(), ns)
    listed = set(ns.get("ALL_TICKERS") or [])
    if not listed and isinstance(ns.get("SECTORS"), dict):
        listed = {t for v in ns["SECTORS"].values() for t in v}
    in_db = {r["ticker"] for r in rows}
    missing = sorted(listed - in_db)
    print(f"tickers.py lists {len(listed)} tickers; {len(missing)} of them are not in the database at all.")
    if missing:
        print("    not in database:", ", ".join(missing[:80]) + (" ..." if len(missing) > 80 else ""))
except FileNotFoundError:
    print("(tickers.py not found in this folder - skipped that comparison)")
except Exception as e:
    print("(could not read tickers.py:", e, ")")
