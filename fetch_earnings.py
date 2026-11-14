# ============================================================
# fetch_earnings.py - Fetches proper earnings dates for all stocks
# Run this separately - faster than full fetch_stocks.py
#
# Nov 2026 (Render): Yahoo refuses cloud servers that ask for each stock's
#  details one by one ("Invalid Crumb"). Dates and EPS now come from ONE
#  batch request per 150 stocks (yahoo_batch.py). The per-stock details
#  (revenue, the earnings calendar) are still tried, but the job stops asking
#  for them if Yahoo keeps refusing. Stocks Yahoo no longer has are skipped.
#
# Oct 2026 fix:
#  - Connects to the SAME database as everything else (Aiven, from .env)
#    instead of the old local MySQL (localhost).
#  - Never replaces a saved value with an empty one.
#  - Picks the NEXT upcoming earnings date (Yahoo .info, then the
#    earnings calendar as a backup).
#  - Reconnects by itself if Aiven drops the connection.
#
# Run:  python fetch_earnings.py              -> all tickers
#       python fetch_earnings.py --missing    -> only stocks with no date yet
#       python fetch_earnings.py AAPL MSFT    -> just these
# ============================================================

import sys
import time
from datetime import datetime, date

import yfinance as yf
from tickers import ALL_TICKERS
from fetch_stocks import get_connection, live_cursor, is_db_error, upsert
from yahoo_batch import fetch_quotes, DetailGate


def _to_date(v):
    try:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        if hasattr(v, "date") and callable(v.date):      # pandas Timestamp
            return v.date()
        return datetime.fromtimestamp(int(v)).date()     # unix seconds
    except Exception:
        return None


def next_earnings_date(stock, info, gate):
    """Earliest earnings date from today onwards; if none, the most recent one."""
    cands = []
    for k in ("earningsTimestampStart", "earningsTimestamp", "earningsCallTimestampStart", "earningsTimestampEnd"):
        d = _to_date(info.get(k))
        if d:
            cands.append(d)
    today = date.today()
    cal = None
    if not any(d >= today for d in cands) and not gate.closed:
        try:
            cal = stock.calendar or {}
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            for v in (ed if isinstance(ed, (list, tuple)) else [ed]):
                d = _to_date(v)
                if d:
                    cands.append(d)
        except Exception:
            cal = None
    future = sorted(d for d in cands if d >= today)
    if future:
        return future[0], cal
    return (max(cands) if cands else None), cal


QUOTE_TO_INFO = {      # batch answer name -> the .info name used below
    "earningsTimestamp": "earningsTimestamp", "earningsTimestampStart": "earningsTimestampStart",
    "earningsTimestampEnd": "earningsTimestampEnd", "earningsCallTimestampStart": "earningsCallTimestampStart",
    "epsForward": "forwardEps", "epsTrailingTwelveMonths": "trailingEps",
}


def fetch_earnings(ticker, quote, gate, cursor, conn):
    if "=F" in ticker:
        return True, "futures - no earnings"
    try:
        stock = yf.Ticker(ticker)
        info = {QUOTE_TO_INFO[k]: v for k, v in quote.items() if k in QUOTE_TO_INFO and v is not None}
        details = gate.info(stock)                      # {} if Yahoo refuses
        info.update({k: v for k, v in details.items() if v is not None})
        now = datetime.now()

        next_date, cal = next_earnings_date(stock, info, gate)

        eps_est = info.get("forwardEps")
        eps_act = info.get("trailingEps")
        rev_est = info.get("revenueEstimate")
        if isinstance(cal, dict):
            eps_est = cal.get("Earnings Average") or eps_est
            rev_est = cal.get("Revenue Average") or rev_est
        rev_est = rev_est or info.get("totalRevenue")
        rev_act = info.get("totalRevenue")

        # Surprise %
        surprise = None
        try:
            if eps_est and eps_act:
                surprise = round(((float(eps_act) - float(eps_est)) / abs(float(eps_est))) * 100, 2)
        except Exception:
            surprise = None

        wrote = upsert(cursor, "stocks_earnings", {
            "ticker": ticker,
            "next_earnings_date": next_date,
            "eps_estimate": eps_est, "eps_actual": eps_act,
            "earnings_surprise_pct": surprise,
            "revenue_estimate": rev_est, "revenue_actual": rev_act,
            "last_updated": now,
        })
        conn.commit()
        if not wrote:
            return False, "Yahoo sent nothing - old data kept"
        return True, "" if next_date else "no date from Yahoo"
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return False, "{}: {}".format(type(e).__name__, str(e)[:100])


def pick_tickers(conn):
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        return args
    if "--missing" in sys.argv:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks_earnings WHERE next_earnings_date IS NOT NULL")
        have = {r[0] for r in cur.fetchall()}
        cur.close()
        return [t for t in ALL_TICKERS if t not in have and "=F" not in t]
    return list(ALL_TICKERS)


def main():
    conn, cursor = live_cursor(None)
    cursor.close()
    tickers = [t for t in pick_tickers(conn) if "=F" not in t]

    print("=" * 60)
    print("  EARNINGS FETCH - {} stocks".format(len(tickers)))
    print("=" * 60)

    quotes, asked = fetch_quotes(tickers)
    gone = [t for t in tickers if t in asked and t not in quotes]
    refused = [t for t in tickers if t not in asked]
    tickers = [t for t in tickers if t in quotes]
    gate = DetailGate()
    print()

    success = failed = no_date = 0
    failed_list = []
    for i, ticker in enumerate(tickers, 1):
        conn, cursor = live_cursor(conn)
        ok, note = fetch_earnings(ticker, quotes[ticker], gate, cursor, conn)
        if not ok and is_db_error(note):
            conn, cursor = live_cursor(None)
            ok, note = fetch_earnings(ticker, quotes[ticker], gate, cursor, conn)
        try: cursor.close()
        except Exception: pass

        if ok:
            success += 1
            if note == "no date from Yahoo":
                no_date += 1
            print("  OK   [{}/{}] {} {}".format(i, len(tickers), ticker, ("- " + note) if note else ""))
        else:
            failed += 1
            failed_list.append(ticker)
            print("  FAIL [{}/{}] {} - {}".format(i, len(tickers), ticker, note))

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM stocks_earnings WHERE next_earnings_date >= CURDATE()")
    upcoming = cur.fetchone()[0]
    cur.close()
    try: conn.close()
    except Exception: pass

    print("\n" + "=" * 60)
    print("  Done! {} updated ({} with no date from Yahoo), {} failed".format(success, no_date, failed))
    print("  Extra details (revenue, calendar) from Yahoo for {} stocks{}".format(
        gate.got, " - Yahoo refused the rest, saved values kept" if gate.closed else ""))
    if refused:
        print("  Yahoo refused {} stocks this run (old data kept) - run the job again later to update them.".format(len(refused)))
    if gone:
        print("  Not on Yahoo any more (skipped, old data kept): {}{}".format(
            ", ".join(gone[:30]), " ... ({} in total)".format(len(gone)) if len(gone) > 30 else ""))
    if failed_list:
        print("  Failed: {}".format(", ".join(failed_list[:60])))
    print("  Stocks with an upcoming earnings date in the database: {}".format(upcoming))
    print("=" * 60)


if __name__ == "__main__":
    main()
