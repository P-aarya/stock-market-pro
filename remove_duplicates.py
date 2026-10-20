"""
remove_duplicates.py - removes the same company listed twice (two share classes),
e.g. GOOG + GOOGL (Alphabet), BRK-A + BRK-B (Berkshire), FOX + FOXA, NWS + NWSA.

Keeps the "original" one:
  1. the ticker that is in tickers.py (your main list), otherwise
  2. the one with the higher normal trading volume (the class everyone trades).

    python remove_duplicates.py            # 1) CHECK ONLY - shows what it would remove, changes nothing
    python remove_duplicates.py --apply    # 2) removes them (backup saved first)

Before deleting, every removed row (from every stocks_* table) is saved to
removed_duplicates_backup.csv. Stocks that are in your portfolio, transactions or
price alerts are never removed - they are listed so you can decide.
"""
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

import mysql.connector
from dotenv import load_dotenv

load_dotenv()
APPLY = "--apply" in sys.argv

# tables that hold one row (or several) per ticker
STOCK_TABLES = ["stocks_live", "stocks_fundamentals", "stocks_dividends", "stocks_analyst",
                "stocks_technicals", "stocks_earnings", "stocks_smartmoney", "stocks_predictions",
                "stocks_historical"]
# tables with the user's own data - never delete a ticker that appears here
USER_TABLES = ["portfolio", "transactions", "price_alerts"]


def get_db():
    cfg = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("DB_PASS", ""),
        "database": os.getenv("DB_NAME", "stock_market_pro_db"),
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        cfg["ssl_disabled"] = False
    return mysql.connector.connect(**cfg)


_DROP = re.compile(
    r"\b(class|series)\s+[a-z0-9]\b|\b(common|ordinary|capital)\s+(stock|shares?)\b|\bnon[- ]?voting\b|"
    r"\bvoting\b|\bsubordinate\b|\bshares?\b|\bthe\b", re.I)


def company_key(name):
    """'Alphabet Inc. Class C Capital Stock' and 'Alphabet Inc.' -> 'alphabet inc'."""
    if not name:
        return None
    n = name.lower().replace("&", " and ")
    n = re.sub(r"\(.*?\)", " ", n)
    n = _DROP.sub(" ", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n or None


def main_list():
    try:
        from tickers import ALL_TICKERS
        return set(ALL_TICKERS)
    except Exception:
        return set()


def main():
    print("=" * 64)
    print("  REMOVE DUPLICATE COMPANIES", "(APPLY)" if APPLY else "(CHECK ONLY - nothing is changed)")
    print("=" * 64)
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT ticker, company_name, sector, current_price, avg_volume, volume, market_cap FROM stocks_live")
    rows = cur.fetchall()
    keep_list = main_list()

    groups = defaultdict(list)
    for r in rows:
        t = r["ticker"] or ""
        if "=F" in t or t.startswith("^") or (r["sector"] or "") == "Indices":
            continue
        k = company_key(r["company_name"])
        if k:
            groups[k].append(r)
    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    # tickers used in the user's own data
    protected = set()
    for tb in USER_TABLES:
        try:
            cur.execute(f"SELECT DISTINCT ticker FROM {tb}")
            protected |= {(x["ticker"] or "").upper() for x in cur.fetchall()}
        except Exception:
            pass

    remove, held = [], []
    if not dupes:
        print("\n  No duplicate companies found. Nothing to do.")
    else:
        print(f"\n  Found {len(dupes)} companies listed more than once:\n")
    for k, lst in sorted(dupes.items()):
        def rank(r):
            return (r["ticker"] in keep_list, r["current_price"] is not None,
                    r["avg_volume"] or r["volume"] or 0, -len(r["ticker"]))
        lst = sorted(lst, key=rank, reverse=True)
        keep, extra = lst[0], lst[1:]
        names = ", ".join(r["ticker"] for r in extra)
        print(f"   {lst[0]['company_name'][:45]:45s}  keep {keep['ticker']:7s} remove {names}")
        for r in extra:
            if r["ticker"].upper() in protected:
                held.append(r["ticker"])
            else:
                remove.append(r["ticker"])

    if held:
        print(f"\n  NOT removed (used in your portfolio / transactions / alerts): {', '.join(held)}")
    print(f"\n  To remove: {len(remove)} ticker(s)")

    if not APPLY:
        print("\n  CHECK ONLY - nothing was changed.")
        if remove:
            print("  If the list looks right, run:  python remove_duplicates.py --apply")
        conn.close()
        return
    if not remove:
        conn.close()
        return

    # backup, then delete from every stock table
    ph = ",".join(["%s"] * len(remove))
    backup_rows = []
    for tb in STOCK_TABLES:
        try:
            cur.execute(f"SELECT * FROM {tb} WHERE ticker IN ({ph})", remove)
            for r in cur.fetchall():
                backup_rows.append({"table": tb, "data": {k: (str(v) if v is not None else "") for k, v in r.items()}})
        except Exception:
            pass
    with open("removed_duplicates_backup.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["table", "row"])
        for b in backup_rows:
            w.writerow([b["table"], repr(b["data"])])

    w = conn.cursor()
    total = 0
    for tb in STOCK_TABLES:
        try:
            w.execute(f"DELETE FROM {tb} WHERE ticker IN ({ph})", remove)
            total += w.rowcount
        except Exception:
            pass
    conn.commit()
    w.close()

    # keep tickers.py in line too (so fetch_stocks.py won't add them back)
    try:
        if os.path.exists("tickers.py"):
            src = open("tickers.py", encoding="utf-8").read()
            new = src
            for t in remove:
                new = re.sub(r'\s*"' + re.escape(t) + r'",', "", new)
            if new != src:
                open("tickers_before_dedupe.py", "w", encoding="utf-8").write(src)
                open("tickers.py", "w", encoding="utf-8").write(new)
                print("  tickers.py updated (old copy: tickers_before_dedupe.py)")
    except Exception as e:
        print(f"  (could not update tickers.py: {e})")

    print(f"\n  Removed {len(remove)} duplicate ticker(s) - {total} rows across the stock tables.")
    print("  Backup: removed_duplicates_backup.csv")
    print(f"  Done at {datetime.now():%Y-%m-%d %H:%M}. Restart api.py and scheduler.py to see the change.")
    conn.close()


if __name__ == "__main__":
    main()
