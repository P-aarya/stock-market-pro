# ============================================================
# fetch_fundamentals.py - extra company numbers used by the AI Stock Finder,
# Bounty, the screener and the Peers comparison.
#
# Oct 2026 fix (the AI Stock Finder / Bounty error
# "Unknown column 'f.market_cap' in 'field list'"):
#  - Adds any missing columns to stocks_fundamentals first
#    (market_cap, market_cap_category, peg_ratio, revenue_growth, ...).
#    It only ADDS columns - nothing is deleted or renamed.
#  - Fills market cap, size (small/mid/large) and 52-week high/low straight
#    from stocks_live, so the AI works straight away (takes seconds).
#  - Then fetches the rest from Yahoo. A saved value is never replaced by an
#    empty one, and it reconnects by itself if Aiven drops the connection.
#
# Run:  python fetch_fundamentals.py --quick     -> columns + quick fill only (seconds)
#       python fetch_fundamentals.py             -> all of the above + Yahoo for every stock
#       python fetch_fundamentals.py --missing   -> Yahoo only for stocks still missing data
#       python fetch_fundamentals.py AAPL MSFT   -> Yahoo for just these
# ============================================================

import sys
import time
from datetime import datetime

import yfinance as yf
from fetch_stocks import get_connection, live_cursor, is_db_error, upsert, get_info

# every column the screener / AI / peers read from stocks_fundamentals
COLUMNS = [
    ("pe_ratio", "FLOAT"), ("forward_pe", "FLOAT"), ("pb_ratio", "FLOAT"), ("ps_ratio", "FLOAT"),
    ("ev_ebitda", "FLOAT"), ("eps", "FLOAT"), ("revenue", "BIGINT"), ("net_income", "BIGINT"),
    ("profit_margin", "FLOAT"), ("roe", "FLOAT"), ("roa", "FLOAT"), ("debt_to_equity", "FLOAT"),
    ("current_ratio", "FLOAT"), ("quick_ratio", "FLOAT"),
    ("market_cap", "BIGINT"), ("market_cap_category", "VARCHAR(10)"),
    ("revenue_growth", "FLOAT"), ("earnings_growth", "FLOAT"), ("operating_margin", "FLOAT"),
    ("gross_margin", "FLOAT"), ("free_cash_flow", "BIGINT"), ("dividend_yield", "FLOAT"),
    ("payout_ratio", "FLOAT"), ("beta", "FLOAT"), ("fifty_two_week_high", "FLOAT"),
    ("fifty_two_week_low", "FLOAT"), ("shares_outstanding", "BIGINT"), ("float_shares", "BIGINT"),
    ("short_ratio", "FLOAT"), ("recommendation_mean", "FLOAT"), ("target_mean_price", "FLOAT"),
    ("peg_ratio", "FLOAT"), ("last_updated", "DATETIME"),
]


def size_of(market_cap):
    try:
        mc = float(market_cap)
    except (TypeError, ValueError):
        return None
    if mc <= 0:
        return None
    if mc < 2_000_000_000:
        return "small"
    if mc < 10_000_000_000:
        return "mid"
    return "large"


def add_missing_columns(conn):
    cur = conn.cursor()
    cur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'stocks_fundamentals'""")
    have = {r[0].lower() for r in cur.fetchall()}
    if not have:
        cur.close()
        raise SystemExit("  stocks_fundamentals table not found - run fetch_stocks.py first.")
    missing = [(c, t) for c, t in COLUMNS if c not in have]
    if missing:
        cur.execute("ALTER TABLE stocks_fundamentals " +
                    ", ".join("ADD COLUMN {} {} NULL".format(c, t) for c, t in missing))
        conn.commit()
        print("  Added {} missing column(s): {}".format(len(missing), ", ".join(c for c, _ in missing)))
    else:
        print("  All columns already there.")
    cur.close()


def quick_fill(conn):
    """Copy market cap and 52-week range from stocks_live (only where empty), and set the size."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE stocks_fundamentals f JOIN stocks_live l ON l.ticker = f.ticker
        SET f.market_cap          = COALESCE(f.market_cap, l.market_cap),
            f.fifty_two_week_high = COALESCE(f.fifty_two_week_high, l.week_52_high),
            f.fifty_two_week_low  = COALESCE(f.fifty_two_week_low, l.week_52_low)
    """)
    cur.execute("""
        UPDATE stocks_fundamentals
        SET market_cap_category = CASE
              WHEN market_cap < 2000000000  THEN 'small'
              WHEN market_cap < 10000000000 THEN 'mid'
              ELSE 'large' END
        WHERE market_cap IS NOT NULL AND market_cap > 0
    """)
    try:   # dividend yield from the dividends table, where we have it
        cur.execute("""
            UPDATE stocks_fundamentals f JOIN stocks_dividends d ON d.ticker = f.ticker
            SET f.dividend_yield = COALESCE(f.dividend_yield, d.dividend_yield),
                f.payout_ratio   = COALESCE(f.payout_ratio, d.payout_ratio)
        """)
    except Exception:
        pass
    conn.commit()
    cur.execute("SELECT COUNT(*), SUM(market_cap IS NOT NULL), SUM(market_cap_category IS NOT NULL) FROM stocks_fundamentals")
    total, mc, cat = cur.fetchone()
    cur.close()
    print("  Quick fill done: {} stocks, {} with market cap, {} with a size (small/mid/large).".format(
        total, int(mc or 0), int(cat or 0)))


def fetch_one(ticker, cursor, conn):
    if "=F" in ticker or ticker.startswith("^"):
        return True, "skipped (futures / index)"
    try:
        info = get_info(yf.Ticker(ticker))
        if not info:
            return False, "Yahoo sent nothing - old data kept"
        mc = info.get("marketCap")
        wrote = upsert(cursor, "stocks_fundamentals", {
            "ticker": ticker,
            "pe_ratio": info.get("trailingPE"), "forward_pe": info.get("forwardPE"),
            "pb_ratio": info.get("priceToBook"), "ps_ratio": info.get("priceToSalesTrailing12Months"),
            "ev_ebitda": info.get("enterpriseToEbitda"), "eps": info.get("trailingEps"),
            "revenue": info.get("totalRevenue"), "net_income": info.get("netIncomeToCommon"),
            "profit_margin": info.get("profitMargins"), "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"), "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"), "quick_ratio": info.get("quickRatio"),
            "market_cap": mc, "market_cap_category": size_of(mc),
            "revenue_growth": info.get("revenueGrowth"), "earnings_growth": info.get("earningsGrowth"),
            "operating_margin": info.get("operatingMargins"), "gross_margin": info.get("grossMargins"),
            "free_cash_flow": info.get("freeCashflow"), "dividend_yield": info.get("dividendYield"),
            "payout_ratio": info.get("payoutRatio"), "beta": info.get("beta"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"), "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "shares_outstanding": info.get("sharesOutstanding"), "float_shares": info.get("floatShares"),
            "short_ratio": info.get("shortRatio"), "recommendation_mean": info.get("recommendationMean"),
            "target_mean_price": info.get("targetMeanPrice"),
            "peg_ratio": info.get("trailingPegRatio") or info.get("pegRatio"),
            "last_updated": datetime.now(),
        })
        conn.commit()
        return (True, "") if wrote else (False, "Yahoo sent nothing - old data kept")
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return False, "{}: {}".format(type(e).__name__, str(e)[:100])


def pick_tickers(conn):
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        return args
    cur = conn.cursor()
    if "--missing" in sys.argv:
        cur.execute("""SELECT l.ticker FROM stocks_live l
                       LEFT JOIN stocks_fundamentals f ON f.ticker = l.ticker
                       WHERE f.ticker IS NULL OR (f.revenue_growth IS NULL AND f.peg_ratio IS NULL
                             AND f.operating_margin IS NULL)""")
    else:
        cur.execute("SELECT ticker FROM stocks_live")
    tickers = [r[0] for r in cur.fetchall()]
    cur.close()
    return [t for t in tickers if t and "=F" not in t and not t.startswith("^")]


def main():
    print("=" * 60)
    print("  FUNDAMENTALS (database: Aiven / .env)")
    print("=" * 60)
    conn, cursor = live_cursor(None)
    cursor.close()

    add_missing_columns(conn)
    quick_fill(conn)
    if "--quick" in sys.argv:
        print("\n  --quick: done. The AI Stock Finder and Bounty can run now.")
        print("  Run  python fetch_fundamentals.py --missing  later to fill growth, margins etc.")
        conn.close()
        return

    tickers = pick_tickers(conn)
    print("\n  Fetching from Yahoo for {} stocks...\n".format(len(tickers)))
    success = failed = 0
    failed_list = []
    for i, ticker in enumerate(tickers, 1):
        conn, cursor = live_cursor(conn)
        ok, note = fetch_one(ticker, cursor, conn)
        if not ok and is_db_error(note):
            conn, cursor = live_cursor(None)
            ok, note = fetch_one(ticker, cursor, conn)
        try: cursor.close()
        except Exception: pass
        if ok:
            success += 1
            print("  OK   [{}/{}] {} {}".format(i, len(tickers), ticker, ("- " + note) if note else ""))
        else:
            failed += 1
            failed_list.append(ticker)
            print("  FAIL [{}/{}] {} - {}".format(i, len(tickers), ticker, note))
        if i % 50 == 0:
            print("\n  Pausing 5s... {} done, {} failed\n".format(success, failed))
            time.sleep(5)
        else:
            time.sleep(0.3)

    quick_fill(conn)   # sizes for anything Yahoo just filled in
    try: conn.close()
    except Exception: pass
    print("\n" + "=" * 60)
    print("  Done! {} updated, {} failed".format(success, failed))
    if failed_list:
        print("  Failed: {}".format(", ".join(failed_list[:60])))
        print("  (Run  python fetch_fundamentals.py --missing  later to retry.)")
    print("=" * 60)


if __name__ == "__main__":
    main()
