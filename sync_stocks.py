"""
sync_stocks.py - brings the database in line with the new ~2000-stock universe.

Put tickers.py, stock_universe.json and this file in your project folder, then:

    python sync_stocks.py                  # 1) DRY RUN - checks everything, changes nothing
    python sync_stocks.py --apply          # 2) adds / updates stocks with prices
    python sync_stocks.py --apply --remove-dead
                                           # 3) also removes the rows that have no price
                                           #    (saved first to removed_stocks_backup.csv)

What it does
  - Every ticker in tickers.py is checked: if it already has a live price in your database it is
    kept as-is; otherwise 1 year of prices is downloaded from Yahoo Finance to prove it trades.
  - Per sector: all verified main tickers + verified CHECK tickers; if a sector is short of its
    target, verified RESERVES fill the gap.
  - New stocks are inserted with sector, company name, market cap and price data.
    Existing stocks get their sector updated; a missing price is filled. It never writes an
    empty value over real data.
  - Live stocks already in your database that are not in tickers.py are kept (and added to it).
  - tickers.py is rewritten with the final verified list (old copy saved as tickers_before_sync.py).
"""
import csv
import json
import os
import shutil
import sys
import time
from datetime import datetime

import mysql.connector
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
APPLY = "--apply" in sys.argv
REMOVE_DEAD = "--remove-dead" in sys.argv
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
    """{ticker: daily OHLCV DataFrame} for tickers that return at least 2 days of prices."""
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        print(f"    Yahoo check {i + 1}-{i + len(chunk)} of {len(tickers)} ...")
        df = None
        for attempt in range(3):
            try:
                df = yf.download(chunk, period="1y", interval="1d", group_by="ticker",
                                 threads=True, progress=False, auto_adjust=False)
                break
            except Exception as e:
                print(f"      retrying ({type(e).__name__}) ...")
                time.sleep(5 * (attempt + 1))
        for t in chunk:
            try:
                if df is not None and isinstance(df.columns, pd.MultiIndex) and t in df.columns.get_level_values(0):
                    sub = df[t]
                elif df is not None and not df.empty and len(chunk) == 1:
                    sub = df
                else:
                    continue
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= 2:
                    out[t] = sub
            except Exception:
                continue
        time.sleep(1)
    # second chance, one by one, for anything that failed in a batch (Yahoo sometimes drops a few)
    missed = [t for t in tickers if t not in out]
    if missed and len(missed) <= 400:
        print(f"    Re-checking {len(missed)} one by one ...")
        for t in missed:
            try:
                h = yf.Ticker(t).history(period="1y", auto_adjust=False).dropna(subset=["Close"])
                if len(h) >= 2:
                    out[t] = h
            except Exception:
                pass
            time.sleep(0.2)
    return out


def price_fields(h):
    last, prev = h.iloc[-1], h.iloc[-2]
    price, pc = float(last["Close"]), float(prev["Close"])
    vol = int(last["Volume"]) if pd.notna(last.get("Volume")) else None
    avg = h["Volume"].tail(63).mean() if "Volume" in h else None
    avg = int(avg) if avg is not None and pd.notna(avg) and avg > 0 else None
    num = lambda v: round(float(v), 4) if pd.notna(v) else None
    return {
        "current_price": round(price, 4), "prev_close": round(pc, 4),
        "price_change": round(price - pc, 4),
        "price_change_pct": round((price - pc) / pc * 100, 4) if pc else None,
        "open_price": num(last.get("Open")), "high": num(last.get("High")), "low": num(last.get("Low")),
        "volume": vol, "avg_volume": avg,
        "relative_volume": round(vol / avg, 2) if vol and avg else None,
        "week_52_high": num(h["High"].max()), "week_52_low": num(h["Low"].min()),
    }


def write_tickers_py(final, reserves_left):
    def block(name, d):
        out = [f"{name} = {{"]
        for sec, lst in d.items():
            out.append(f'    "{sec}": [')
            line = "       "
            for t in lst:
                piece = f' "{t}",'
                if len(line) + len(piece) > 110:
                    out.append(line); line = "       "
                line += piece
            if line.strip():
                out.append(line)
            out.append("    ],")
        out.append("}")
        return "\n".join(out)
    total = sum(len(v) for v in final.values())
    src = f'''# ============================================================
# tickers.py - Stock Market Pro universe (verified by sync_stocks.py on {datetime.now():%Y-%m-%d %H:%M})
# {total} live tickers in {len(final)} sectors - every one returned prices from Yahoo Finance.
# ============================================================

# Verified list
{block("SECTORS", final)}

# Unused same-sector replacements (verified listed companies)
{block("RESERVES", reserves_left)}

CHECK = {{}}

ALL_TICKERS = []
TICKER_SECTOR_MAP = {{}}
for _sector, _tickers in SECTORS.items():
    for _t in _tickers:
        _t = _t.strip()
        if _t and _t not in TICKER_SECTOR_MAP:
            ALL_TICKERS.append(_t)
            TICKER_SECTOR_MAP[_t] = _sector

if __name__ == "__main__":
    print("Total unique tickers: {{}}".format(len(ALL_TICKERS)))
    print("Total sectors: {{}}".format(len(SECTORS)))
    for _sector, _tickers in SECTORS.items():
        print("  {{:28s}} -> {{}}".format(_sector, len(_tickers)))
'''
    if os.path.exists("tickers.py"):
        shutil.copy2("tickers.py", "tickers_before_sync.py")
    with open("tickers.py", "w", encoding="utf-8", newline="") as fh:
        fh.write(src)


def main():
    print("=" * 64)
    print("  STOCK MARKET PRO - sync_stocks.py", "(APPLY)" if APPLY else "(DRY RUN - no changes)")
    print("=" * 64)
    uni = json.load(open("stock_universe.json", encoding="utf-8"))
    SECTORS, RESERVES, CHECK, TARGET, META = uni["SECTORS"], uni["RESERVES"], uni["CHECK"], uni["TARGET"], uni["META"]
    DUPES = set(uni.get("DUPLICATE_CLASSES", []))  # second share class of a company already in the list (e.g. GOOG)

    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT MAX(last_updated) AS m FROM stocks_live")
    latest = cur.fetchone()["m"]
    cur.execute("SELECT ticker, sector, company_name, current_price, last_updated FROM stocks_live")
    db = {r["ticker"]: r for r in cur.fetchall()}
    cur.close()

    def is_live(t):
        r = db.get(t)
        return bool(r and r["current_price"] is not None and r["last_updated"] and latest
                    and (latest - r["last_updated"]).days <= 14)

    live_db = {t for t in db if is_live(t)}
    print(f"\n  Database now: {len(db)} rows, {len(live_db)} live")

    wanted = []
    for blk in (SECTORS, CHECK, RESERVES):
        for lst in blk.values():
            wanted += [t for t in lst if t not in wanted]
    to_check = [t for t in wanted if t not in live_db]
    print(f"  Tickers in the new list: {len(wanted)} (incl. reserves) | already live: {len(wanted) - len(to_check)}"
          f" | to verify on Yahoo: {len(to_check)}\n")
    data = download(to_check) if to_check else {}
    ok = live_db | set(data)

    # ---------------------------------------------- assemble final per sector
    final, used, reserves_left = {}, set(), {}
    report = []
    for sec in SECTORS:
        picked = [t for t in SECTORS[sec] if t in ok and t not in used]
        used.update(picked)
        extra = [t for t in CHECK.get(sec, []) if t in ok and t not in used]
        used.update(extra)
        picked += extra
        res_used = []
        for t in RESERVES.get(sec, []):
            if len(picked) >= TARGET.get(sec, 0):
                break
            if t in ok and t not in used:
                picked.append(t); used.add(t); res_used.append(t)
        reserves_left[sec] = [t for t in RESERVES.get(sec, []) if t not in used and t in ok]
        failed = [t for t in SECTORS[sec] if t not in ok]
        final[sec] = picked
        report.append((sec, len(picked), TARGET.get(sec, 0), failed, extra, res_used))

    # live stocks in the database that the new list does not mention -> keep them
    keep_extra = sorted(t for t in live_db if t not in used and t not in DUPES and (db[t]["sector"] or "") != "Indices")
    for t in keep_extra:
        sec = db[t]["sector"] if db[t]["sector"] in final else "Technology"
        final.setdefault(sec, []).append(t); used.add(t)

    print("\n  Sector                      final  target  | not on Yahoo -> replaced")
    for sec, n, tgt, failed, extra, res_used in report:
        print(f"  {sec:26s} {n:5d}  {tgt:6d}  | {len(failed)} failed, +{len(extra)} from old list, +{len(res_used)} reserves")
        if failed:
            print(f"      failed: {', '.join(failed[:25])}{' ...' if len(failed) > 25 else ''}")
    total = sum(len(v) for v in final.values())
    print(f"\n  Live stocks already in the database but not in the list (kept): {len(keep_extra)}")
    print(f"  FINAL TOTAL: {total} live stocks across {len(final)} sectors")

    dead = sorted(t for t in db if t not in used and not is_live(t) and (db[t]["sector"] or "") != "Indices")
    dupes_in_db = sorted(t for t in db if t in DUPES and t not in used)
    new_rows = [t for t in used if t not in db]
    print(f"\n  Will add new stocks: {len(new_rows)}")
    print(f"  Rows with no price that are not in the final list (dead): {len(dead)}")
    if dupes_in_db:
        print(f"  Second share class of a company already listed (duplicates): {', '.join(dupes_in_db)}")

    if not APPLY:
        print("\n  DRY RUN finished - nothing was changed.")
        print("  When the numbers look right, run:  python sync_stocks.py --apply --remove-dead")
        conn.close()
        return

    # ---------------------------------------------- write to the database
    sec_of = {t: s for s, lst in final.items() for t in lst}
    w = conn.cursor()
    added = updated = priced = 0
    now = datetime.now()
    for t in sorted(used):
        sec = sec_of[t]
        meta = META.get(t, {})
        h = data.get(t)
        try:
            if t not in db:
                w.execute("INSERT INTO stocks_live (ticker, sector, company_name, market_cap, last_updated) "
                          "VALUES (%s,%s,%s,%s,%s)", (t, sec, meta.get("name") or t, meta.get("market_cap"), now))
                added += 1
            else:
                w.execute("UPDATE stocks_live SET sector=%s, company_name=COALESCE(company_name,%s), "
                          "market_cap=COALESCE(market_cap,%s) WHERE ticker=%s",
                          (sec, meta.get("name"), meta.get("market_cap"), t))
                updated += 1
            if h is not None and not is_live(t):
                f = price_fields(h)
                w.execute("""UPDATE stocks_live SET current_price=%s, prev_close=%s, price_change=%s,
                             price_change_pct=%s, open_price=COALESCE(%s,open_price), high=COALESCE(%s,high),
                             low=COALESCE(%s,low), volume=COALESCE(%s,volume), avg_volume=COALESCE(%s,avg_volume),
                             relative_volume=COALESCE(%s,relative_volume), week_52_high=COALESCE(%s,week_52_high),
                             week_52_low=COALESCE(%s,week_52_low), last_updated=%s WHERE ticker=%s""",
                          (f["current_price"], f["prev_close"], f["price_change"], f["price_change_pct"],
                           f["open_price"], f["high"], f["low"], f["volume"], f["avg_volume"],
                           f["relative_volume"], f["week_52_high"], f["week_52_low"], now, t))
                priced += 1
        except Exception as e:
            print(f"    could not save {t}: {e}")
    conn.commit()
    print(f"\n  Added {added} new stocks, updated {updated}, filled prices for {priced}.")

    if REMOVE_DEAD and (dead or dupes_in_db):
        dead = dead + dupes_in_db
        c2 = conn.cursor(dictionary=True)
        ph = ",".join(["%s"] * len(dead))
        c2.execute(f"SELECT * FROM stocks_live WHERE ticker IN ({ph})", dead)
        rows = c2.fetchall()
        c2.close()
        with open("removed_stocks_backup.csv", "w", newline="", encoding="utf-8") as fh:
            if rows:
                wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                wr.writeheader()
                wr.writerows(rows)
        for i in range(0, len(dead), 200):
            part = dead[i:i + 200]
            w.execute(f"DELETE FROM stocks_live WHERE ticker IN ({','.join(['%s'] * len(part))})", part)
        conn.commit()
        print(f"  Removed {len(dead)} rows: dead / duplicate share class (backup: removed_stocks_backup.csv).")
    elif dead or dupes_in_db:
        print(f"  {len(dead)} dead rows left in place (add --remove-dead to remove them).")
    w.close()

    write_tickers_py(final, reserves_left)
    print("  tickers.py rewritten with the verified list (previous copy: tickers_before_sync.py).")

    c3 = conn.cursor()
    c3.execute("""SELECT COUNT(*) FROM stocks_live WHERE (sector!='Indices' OR sector IS NULL)
                  AND current_price IS NOT NULL
                  AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)""")
    print(f"\n  Stocks the dashboard will show now: {c3.fetchone()[0]}")
    c3.close(); conn.close()
    print("\n  Next: run  python fetch_fundamentals.py  and  python fetch_missing_data.py  for the new stocks,")
    print("        then restart scheduler.py and api.py.")


if __name__ == "__main__":
    main()
