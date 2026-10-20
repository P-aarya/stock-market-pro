"""
fix_missing_prices.py - brings back stocks that have no price on the dashboard.

Run from your project folder:

    python fix_missing_prices.py              # step 1: fill prices for every stock that still trades
    python fix_missing_prices.py --replace    # step 2 (optional): also add the correct ticker
                                              #         for wrong/renamed ones, then fill those too

What it does:
  1. Finds every row in stocks_live with no price.
  2. Downloads 1 year of daily prices from Yahoo Finance for them (in batches).
  3. For each ticker that returns data: saves price, change, open/high/low, volume,
     average volume, 52-week high/low. It NEVER writes an empty value over real data.
  4. For tickers with no data at all: they are delisted, taken private, or not real
     ticker symbols (e.g. ZOOM instead of ZM). It suggests the correct symbol where it
     can, and writes everything to dead_tickers.csv.
  5. With --replace: adds the suggested correct tickers (same sector) to stocks_live
     and fills their prices. The dead rows are left as they are (they stay hidden).

Nothing is deleted.
"""
import csv
import os
import sys
import time
from datetime import datetime

import mysql.connector
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# Names / old symbols -> correct current ticker (checked against Yahoo before use)
KNOWN_FIXES = {
    "ZOOM": "ZM", "DOCUSIGN": "DOCU", "DROPBOX": "DBX", "ATLASSIAN": "TEAM", "WORKDAY": "WDAY",
    "GARTNER": "IT", "TYLER": "TYL", "AKAMAI": "AKAM", "MACOM": "MTSI", "AXCELIS": "ACLS",
    "SPRINKLR": "CXM", "WEAVE": "WEAV", "CREE": "WOLF", "IIVI": "COHR", "II": "COHR",
    "NLOK": "GEN", "ABC": "COR", "FB": "META", "TWTR": None, "JIRA": "TEAM",
}

BATCH = 80


def get_db():
    cfg = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", os.getenv("DB_PASS", "")),
        "database": os.getenv("DB_NAME", "stock_market_pro_db"),
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        cfg["ssl_disabled"] = False
    return mysql.connector.connect(**cfg)


def download(tickers):
    """{ticker: DataFrame of daily OHLCV} for tickers that returned data."""
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        print(f"  Downloading {i + 1}-{i + len(chunk)} of {len(tickers)} ...")
        try:
            df = yf.download(chunk, period="1y", interval="1d", group_by="ticker",
                             threads=True, progress=False, auto_adjust=False)
        except Exception as e:
            print(f"    batch failed ({type(e).__name__}) - retrying one by one")
            df = None
        for t in chunk:
            try:
                if df is not None and isinstance(df.columns, pd.MultiIndex) and t in df.columns.get_level_values(0):
                    sub = df[t]
                elif df is not None and len(chunk) == 1:
                    sub = df
                else:
                    sub = yf.Ticker(t).history(period="1y", auto_adjust=False)
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= 2:
                    out[t] = sub
            except Exception:
                continue
        time.sleep(1)
    return out


def price_fields(h):
    last, prev = h.iloc[-1], h.iloc[-2]
    price, pc = float(last["Close"]), float(prev["Close"])
    vol = int(last["Volume"]) if pd.notna(last["Volume"]) else None
    avg = h["Volume"].tail(63).mean()
    avg = int(avg) if pd.notna(avg) and avg > 0 else None
    return {
        "current_price": round(price, 4),
        "prev_close": round(pc, 4),
        "price_change": round(price - pc, 4),
        "price_change_pct": round((price - pc) / pc * 100, 4) if pc else None,
        "open_price": round(float(last["Open"]), 4) if pd.notna(last["Open"]) else None,
        "high": round(float(last["High"]), 4) if pd.notna(last["High"]) else None,
        "low": round(float(last["Low"]), 4) if pd.notna(last["Low"]) else None,
        "volume": vol,
        "avg_volume": avg,
        "relative_volume": round(vol / avg, 2) if vol and avg else None,
        "week_52_high": round(float(h["High"].max()), 4),
        "week_52_low": round(float(h["Low"].min()), 4),
    }


def save_prices(cur, ticker, f):
    # COALESCE keeps any existing value if a new one is missing
    cur.execute("""
        UPDATE stocks_live SET
            current_price=%s, prev_close=%s, price_change=%s, price_change_pct=%s,
            open_price=COALESCE(%s, open_price), high=COALESCE(%s, high), low=COALESCE(%s, low),
            volume=COALESCE(%s, volume), avg_volume=COALESCE(%s, avg_volume),
            relative_volume=COALESCE(%s, relative_volume),
            week_52_high=%s, week_52_low=%s, last_updated=%s
        WHERE ticker=%s
    """, (f["current_price"], f["prev_close"], f["price_change"], f["price_change_pct"],
          f["open_price"], f["high"], f["low"], f["volume"], f["avg_volume"], f["relative_volume"],
          f["week_52_high"], f["week_52_low"], datetime.now(), ticker))


def suggest(ticker, name):
    """Correct current ticker for a wrong/renamed symbol, or None.
    Only known renames, or 'tickers' that are really company names (e.g. SPRINKLR).
    A search result is accepted only if its company name matches the word searched for,
    so a delisted company is never swapped for an unrelated one."""
    if ticker in KNOWN_FIXES:
        return KNOWN_FIXES[ticker]
    if len(ticker) <= 5 or not hasattr(yf, "Search"):
        return None  # a normal-looking symbol with no data = delisted / taken private
    word = ticker.lower()
    try:
        for q in (yf.Search(ticker, max_results=5).quotes or []):
            sym = q.get("symbol") or ""
            names = f"{q.get('shortname', '')} {q.get('longname', '')}".lower().replace(" ", "")
            if q.get("quoteType") == "EQUITY" and "." not in sym and sym != ticker and word[:6] in names:
                return sym
    except Exception:
        return None
    return None


def main():
    replace = "--replace" in sys.argv
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT ticker, sector, company_name FROM stocks_live WHERE current_price IS NULL AND (sector!='Indices' OR sector IS NULL)")
    missing = cur.fetchall()
    cur.execute("SELECT ticker FROM stocks_live")
    in_db = {r["ticker"] for r in cur.fetchall()}
    cur.close()
    print(f"Stocks with no price: {len(missing)}\n")
    if not missing:
        return

    data = download([m["ticker"] for m in missing])
    wcur = conn.cursor()
    fixed = 0
    for m in missing:
        h = data.get(m["ticker"])
        if h is not None:
            try:
                save_prices(wcur, m["ticker"], price_fields(h))
                fixed += 1
            except Exception as e:
                print(f"  could not save {m['ticker']}: {e}")
    conn.commit()
    print(f"\nPrices restored: {fixed}")

    dead = [m for m in missing if m["ticker"] not in data]
    print(f"No data on Yahoo (delisted / taken private / wrong symbol): {len(dead)}")
    print("Looking up the correct symbols where possible ...")
    rows = []
    for m in dead:
        s = suggest(m["ticker"], m.get("company_name"))
        rows.append({"ticker": m["ticker"], "sector": m["sector"] or "", "company_name": m.get("company_name") or "",
                     "suggested_ticker": s or "", "already_in_db": "yes" if s and s in in_db else ("" if not s else "no")})
    with open("dead_tickers.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "sector", "company_name", "suggested_ticker", "already_in_db"])
        w.writeheader()
        w.writerows(rows)
    new_candidates = {}
    for r in rows:
        s = r["suggested_ticker"]
        if s and s not in in_db and s not in new_candidates:
            new_candidates[s] = r["sector"]
    print(f"  Saved the full list to dead_tickers.csv")
    print(f"  Correct symbols found that are NOT yet in your database: {len(new_candidates)}")
    if new_candidates:
        print("   ", ", ".join(f"{r['ticker']}->{r['suggested_ticker']}" for r in rows
                               if r["suggested_ticker"] in new_candidates)[:1500])

    added = 0
    if replace and new_candidates:
        print("\nAdding the correct tickers ...")
        new_data = download(list(new_candidates))
        for sym, sector in new_candidates.items():
            h = new_data.get(sym)
            if h is None:
                continue
            try:
                wcur.execute("INSERT INTO stocks_live (ticker, sector, last_updated) VALUES (%s,%s,%s)",
                             (sym, sector or None, datetime.now()))
                save_prices(wcur, sym, price_fields(h))
                added += 1
            except Exception as e:
                print(f"  could not add {sym}: {e}")
        conn.commit()
        print(f"Added: {added}")
    elif new_candidates:
        print("\nTo add these correct tickers, run:  python fix_missing_prices.py --replace")

    c2 = conn.cursor()
    c2.execute("""SELECT COUNT(*) FROM stocks_live WHERE (sector!='Indices' OR sector IS NULL)
                  AND current_price IS NOT NULL
                  AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)""")
    total = c2.fetchone()[0]
    c2.close(); wcur.close(); conn.close()
    print(f"\nStocks the dashboard will now show: {total}")
    print("Restart api.py (and scheduler.py) and refresh the dashboard.")


if __name__ == "__main__":
    main()
