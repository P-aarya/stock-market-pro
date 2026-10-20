# ============================================================
# STOCK MARKET PRO - fetch_stocks.py
# Fetches all metrics for ~2000 stocks and saves to Aiven MySQL
#
# SAFE VERSION (Oct 2026):
#  - Never replaces good data with empty data. If Yahoo sends nothing
#    for a field, the value already in the database is kept.
#  - If Yahoo sends no price at all, the stock is skipped (nothing written).
#  - Price comes from .info, then fast_info, then price history.
#  - Does NOT touch next_earnings_date (fetch_earnings.py owns it).
#  - Does NOT write stocks_predictions (predict.py owns it).
#
# Run:  python fetch_stocks.py              -> all tickers in tickers.py
#       python fetch_stocks.py --missing    -> only stocks with no details yet
#       python fetch_stocks.py AAPL MSFT    -> just these tickers
# ============================================================

import yfinance as yf
import mysql.connector
import pandas as pd
import math
import sys
import time
import os
from datetime import datetime
from dotenv import load_dotenv
from tickers import ALL_TICKERS, TICKER_SECTOR_MAP

try:
    from yfinance.exceptions import YFRateLimitError
except Exception:
    class YFRateLimitError(Exception):
        pass

load_dotenv()

# Futures tickers (=F suffix) - only price data, no fundamentals
FUTURES_TICKERS = [t for t in ALL_TICKERS if "=F" in t]

def is_futures(ticker):
    return "=F" in ticker

# ─────────────────────────────────────────
# MySQL Connection (Aiven)
# ─────────────────────────────────────────
def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD") or os.getenv("DB_PASS"),
        database=os.getenv("DB_NAME"),
        ssl_disabled=False
    )

# ─────────────────────────────────────────
# Create All Tables
# ─────────────────────────────────────────
def create_tables(cursor):
    print("Creating tables...")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_live (
            ticker VARCHAR(20) PRIMARY KEY,
            sector VARCHAR(50),
            industry VARCHAR(100),
            company_name VARCHAR(200),
            current_price FLOAT,
            open_price FLOAT,
            high FLOAT,
            low FLOAT,
            prev_close FLOAT,
            price_change FLOAT,
            price_change_pct FLOAT,
            week_52_high FLOAT,
            week_52_low FLOAT,
            volume BIGINT,
            avg_volume BIGINT,
            relative_volume FLOAT,
            market_cap BIGINT,
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_fundamentals (
            ticker VARCHAR(20) PRIMARY KEY,
            pe_ratio FLOAT,
            forward_pe FLOAT,
            pb_ratio FLOAT,
            ps_ratio FLOAT,
            ev_ebitda FLOAT,
            eps FLOAT,
            revenue BIGINT,
            net_income BIGINT,
            profit_margin FLOAT,
            roe FLOAT,
            roa FLOAT,
            debt_to_equity FLOAT,
            current_ratio FLOAT,
            quick_ratio FLOAT,
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_dividends (
            ticker VARCHAR(20) PRIMARY KEY,
            dividend_yield FLOAT,
            dividend_rate FLOAT,
            payout_ratio FLOAT,
            ex_dividend_date DATE,
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_analyst (
            ticker VARCHAR(20) PRIMARY KEY,
            target_price FLOAT,
            analyst_rating VARCHAR(50),
            num_analysts INT,
            upside_pct FLOAT,
            recommendation VARCHAR(50),
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_technicals (
            ticker VARCHAR(20) PRIMARY KEY,
            ma_50 FLOAT,
            ma_200 FLOAT,
            beta FLOAT,
            rsi_14 FLOAT,
            macd FLOAT,
            macd_signal FLOAT,
            macd_hist FLOAT,
            golden_cross BOOLEAN,
            death_cross BOOLEAN,
            volatility FLOAT,
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_earnings (
            ticker VARCHAR(20) PRIMARY KEY,
            next_earnings_date DATE,
            eps_estimate FLOAT,
            eps_actual FLOAT,
            earnings_surprise_pct FLOAT,
            revenue_estimate BIGINT,
            revenue_actual BIGINT,
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_historical (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticker VARCHAR(20),
            year INT,
            revenue BIGINT,
            net_income BIGINT,
            eps FLOAT,
            ebitda BIGINT,
            operating_income BIGINT,
            free_cash_flow BIGINT,
            total_assets BIGINT,
            total_debt BIGINT,
            equity BIGINT,
            rd_expense BIGINT,
            employee_count INT,
            last_updated DATETIME,
            UNIQUE KEY unique_ticker_year (ticker, year)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_smartmoney (
            ticker VARCHAR(20) PRIMARY KEY,
            institutional_ownership FLOAT,
            insider_ownership FLOAT,
            short_interest FLOAT,
            short_ratio FLOAT,
            shares_short BIGINT,
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks_predictions (
            ticker VARCHAR(20) PRIMARY KEY,
            current_price FLOAT,
            target_1w FLOAT,
            target_1m FLOAT,
            target_3m FLOAT,
            buy_signal VARCHAR(20),
            trend VARCHAR(20),
            momentum_score FLOAT,
            risk_level VARCHAR(20),
            confidence_pct FLOAT,
            last_updated DATETIME
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticker VARCHAR(20),
            company_name VARCHAR(200),
            quantity FLOAT,
            avg_buy_price FLOAT,
            buy_date DATE,
            sector VARCHAR(50),
            notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticker VARCHAR(20),
            company_name VARCHAR(200),
            transaction_type VARCHAR(10),
            quantity FLOAT,
            price FLOAT,
            total_value FLOAT,
            transaction_date DATE,
            notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticker VARCHAR(20),
            alert_type VARCHAR(20),
            target_price FLOAT,
            current_price FLOAT,
            triggered BOOLEAN DEFAULT FALSE,
            created_at DATETIME,
            triggered_at DATETIME
        )
    """)

    print("All tables created!")

# ─────────────────────────────────────────
# Calculate RSI
# ─────────────────────────────────────────
def calculate_rsi(prices, period=14):
    try:
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi.iloc[-1]), 2)
    except:
        return None

# ─────────────────────────────────────────
# Calculate MACD
# ─────────────────────────────────────────
def calculate_macd(prices):
    try:
        ema12 = prices.ewm(span=12, adjust=False).mean()
        ema26 = prices.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        return round(float(macd.iloc[-1]), 4), round(float(signal.iloc[-1]), 4), round(float(hist.iloc[-1]), 4)
    except:
        return None, None, None

# ─────────────────────────────────────────
# Safe-save helpers
# ─────────────────────────────────────────
def clean(v):
    """Turn NaN / Infinity / numpy values into normal Python values (or None)."""
    if v is None:
        return None
    if isinstance(v, str):
        return None if v.strip().lower() in ("", "nan", "infinity", "-infinity", "inf", "-inf", "none") else v
    if isinstance(v, bool):
        return v
    try:
        if hasattr(v, "item"):          # numpy number
            v = v.item()
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    except Exception:
        return None
    return v


def upsert(cursor, table, row, keys=("ticker",)):
    """
    Insert or update one row.  Empty (None) values NEVER overwrite what is
    already saved: COALESCE(new, old) keeps the old value.
    If every data field is empty, nothing is written at all.
    """
    row = {k: clean(v) for k, v in row.items()}
    data_cols = [c for c in row if c not in keys and c != "last_updated"]
    if not any(row[c] is not None for c in data_cols):
        return False
    cols = list(row)
    sql = "INSERT INTO {} ({}) VALUES ({}) ON DUPLICATE KEY UPDATE {}".format(
        table,
        ", ".join(cols),
        ", ".join(["%s"] * len(cols)),
        ", ".join(
            ["{0}=COALESCE(VALUES({0}), {0})".format(c) for c in data_cols]
            + (["last_updated=VALUES(last_updated)"] if "last_updated" in row else [])
        ),
    )
    cursor.execute(sql, [row[c] for c in cols])
    return True


def get_info(stock):
    """stock.info with one retry if Yahoo rate-limits us. Returns {} on failure."""
    for attempt in (1, 2):
        try:
            info = stock.info
            return info if isinstance(info, dict) else {}
        except YFRateLimitError:
            if attempt == 1:
                print("      Yahoo rate limit - waiting 30s and retrying...")
                time.sleep(30)
        except Exception:
            return {}
    return {}


def get_history(stock, period="1y"):
    try:
        h = stock.history(period=period)
        if h is not None and not h.empty:
            return h.dropna(subset=["Close"])
    except Exception:
        pass
    return pd.DataFrame()


def num(v):
    v = clean(v)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def price_block(stock, info, hist, futures=False):
    """
    Works out today's price numbers.  Order: .info -> fast_info -> price history.
    Returns a dict, or None if Yahoo gave no price at all.
    """
    if futures:
        cp = num(info.get("regularMarketPrice") or info.get("currentPrice") or info.get("ask") or info.get("bid"))
        op = num(info.get("regularMarketOpen") or info.get("open"))
        hi = num(info.get("regularMarketDayHigh") or info.get("dayHigh"))
        lo = num(info.get("regularMarketDayLow") or info.get("dayLow"))
    else:
        cp = num(info.get("currentPrice") or info.get("regularMarketPrice"))
        op = num(info.get("open") or info.get("regularMarketOpen"))
        hi = num(info.get("dayHigh") or info.get("regularMarketDayHigh"))
        lo = num(info.get("dayLow") or info.get("regularMarketDayLow"))
    pc   = num(info.get("previousClose") or info.get("regularMarketPreviousClose"))
    vol  = num(info.get("volume") or info.get("regularMarketVolume"))
    avgv = num(info.get("averageVolume") or info.get("averageDailyVolume10Day"))
    w52h = num(info.get("fiftyTwoWeekHigh"))
    w52l = num(info.get("fiftyTwoWeekLow"))

    # fallback 1: fast_info
    if not cp:
        try:
            fi = stock.fast_info
            cp   = num(fi.last_price)
            pc   = pc or num(fi.previous_close)
            op   = op or num(fi.open)
            hi   = hi or num(fi.day_high)
            lo   = lo or num(fi.day_low)
            vol  = vol or num(fi.last_volume)
            w52h = w52h or num(fi.year_high)
            w52l = w52l or num(fi.year_low)
        except Exception:
            pass

    # fallback 2: price history (most reliable)
    if not hist.empty:
        last = hist.iloc[-1]
        if not cp:
            cp  = num(last["Close"])
            op  = op or num(last["Open"])
            hi  = hi or num(last["High"])
            lo  = lo or num(last["Low"])
            vol = vol or num(last["Volume"])
        if not pc and len(hist) > 1:
            pc = num(hist["Close"].iloc[-2])
        if not avgv and "Volume" in hist:
            avgv = num(hist["Volume"].tail(63).mean())
        w52h = w52h or num(hist["High"].max())
        w52l = w52l or num(hist["Low"].min())

    if not cp:
        return None

    chg     = round(cp - pc, 4) if pc else None
    chg_pct = round(chg / pc * 100, 4) if (chg is not None and pc) else None
    rel_vol = round(vol / avgv, 2) if (vol and avgv) else None
    return {
        "current_price": cp, "open_price": op, "high": hi, "low": lo,
        "prev_close": pc, "price_change": chg, "price_change_pct": chg_pct,
        "week_52_high": w52h, "week_52_low": w52l,
        "volume": int(vol) if vol else None, "avg_volume": int(avgv) if avgv else None,
        "relative_volume": rel_vol,
    }


def save_technicals(cursor, ticker, hist, beta, now, min_rows):
    """stocks_technicals from 1 year of prices (same maths as before)."""
    if hist.empty or len(hist) <= min_rows:
        return
    closes = hist["Close"]
    ma50  = round(float(closes.rolling(50).mean().iloc[-1]), 4) if len(closes) >= 50 else None
    ma200 = round(float(closes.rolling(200).mean().iloc[-1]), 4) if len(closes) >= 200 else None
    rsi   = calculate_rsi(closes)
    macd_val, macd_sig, macd_hist_v = calculate_macd(closes)
    golden = bool(ma50 and ma200 and ma50 > ma200)
    death  = bool(ma50 and ma200 and ma50 < ma200)
    returns = closes.pct_change().dropna()
    volatility = round(float(returns.std() * (252 ** 0.5) * 100), 2) if len(returns) > 1 else None
    upsert(cursor, "stocks_technicals", {
        "ticker": ticker, "ma_50": ma50, "ma_200": ma200, "beta": beta,
        "rsi_14": rsi, "macd": macd_val, "macd_signal": macd_sig, "macd_hist": macd_hist_v,
        "golden_cross": golden, "death_cross": death, "volatility": volatility,
        "last_updated": now,
    })


# ─────────────────────────────────────────
# Fetch & Save Futures (commodity futures)
# Only saves price data - no fundamentals
# ─────────────────────────────────────────
def fetch_and_save_futures(ticker, cursor, conn):
    try:
        stock = yf.Ticker(ticker)
        info  = get_info(stock)
        hist  = get_history(stock)
        now   = datetime.now()

        p = price_block(stock, info, hist, futures=True)
        if not p:
            return False, "no price from Yahoo - old data kept"

        upsert(cursor, "stocks_live", dict(
            ticker=ticker,
            sector=TICKER_SECTOR_MAP.get(ticker, "Commodities"),
            industry="Commodity Futures",
            company_name=info.get("longName") or info.get("shortName"),
            last_updated=now, **p))

        try:
            save_technicals(cursor, ticker, hist, None, now, min_rows=30)
        except Exception:
            pass

        conn.commit()
        return True, ""
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return False, "{}: {}".format(type(e).__name__, str(e)[:100])


# ─────────────────────────────────────────
# Fetch & Save One Stock (equities/ETFs)
# ─────────────────────────────────────────
def fetch_and_save(ticker, cursor, conn):
    # Route futures to separate handler
    if is_futures(ticker):
        return fetch_and_save_futures(ticker, cursor, conn)

    try:
        stock = yf.Ticker(ticker)
        info  = get_info(stock)
        hist  = get_history(stock)
        now   = datetime.now()

        p = price_block(stock, info, hist)
        if not p:
            # Nothing usable from Yahoo -> write NOTHING, keep what we had
            return False, "no price from Yahoo - old data kept"
        current_price = p["current_price"]

        # stocks_live
        upsert(cursor, "stocks_live", dict(
            ticker=ticker,
            sector=TICKER_SECTOR_MAP.get(ticker),
            industry=info.get("industry"),
            company_name=info.get("longName") or info.get("shortName"),
            market_cap=info.get("marketCap"),
            last_updated=now, **p))

        # stocks_fundamentals
        upsert(cursor, "stocks_fundamentals", {
            "ticker": ticker,
            "pe_ratio": info.get("trailingPE"), "forward_pe": info.get("forwardPE"),
            "pb_ratio": info.get("priceToBook"), "ps_ratio": info.get("priceToSalesTrailing12Months"),
            "ev_ebitda": info.get("enterpriseToEbitda"), "eps": info.get("trailingEps"),
            "revenue": info.get("totalRevenue"), "net_income": info.get("netIncomeToCommon"),
            "profit_margin": info.get("profitMargins"), "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"), "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"), "quick_ratio": info.get("quickRatio"),
            "last_updated": now,
        })

        # stocks_dividends
        ex_div_date = None
        try:
            ex_div = info.get("exDividendDate")
            if ex_div:
                ex_div_date = datetime.fromtimestamp(int(ex_div)).date()
        except Exception:
            pass
        upsert(cursor, "stocks_dividends", {
            "ticker": ticker,
            "dividend_yield": info.get("dividendYield"), "dividend_rate": info.get("dividendRate"),
            "payout_ratio": info.get("payoutRatio"), "ex_dividend_date": ex_div_date,
            "last_updated": now,
        })

        # stocks_analyst
        target = num(info.get("targetMeanPrice"))
        upside = round(((target - current_price) / current_price) * 100, 2) if target and current_price else None
        upsert(cursor, "stocks_analyst", {
            "ticker": ticker,
            "target_price": target, "analyst_rating": info.get("recommendationKey"),
            "num_analysts": info.get("numberOfAnalystOpinions"), "upside_pct": upside,
            "recommendation": info.get("recommendationMean"),
            "last_updated": now,
        })

        # stocks_smartmoney  (Yahoo's real field names are heldPercent...)
        upsert(cursor, "stocks_smartmoney", {
            "ticker": ticker,
            "institutional_ownership": info.get("heldPercentInstitutions") or info.get("institutionOwnership"),
            "insider_ownership": info.get("heldPercentInsiders") or info.get("insidersPercentHeld"),
            "short_interest": info.get("shortPercentOfFloat"), "short_ratio": info.get("shortRatio"),
            "shares_short": info.get("sharesShort"),
            "last_updated": now,
        })

        # stocks_earnings - only EPS / revenue.  next_earnings_date is left
        # alone so the dates from fetch_earnings.py are never wiped.
        upsert(cursor, "stocks_earnings", {
            "ticker": ticker,
            "eps_estimate": info.get("forwardEps"), "eps_actual": info.get("trailingEps"),
            "revenue_actual": info.get("totalRevenue"),
            "last_updated": now,
        })

        # stocks_technicals  (predictions are written by predict.py only)
        try:
            save_technicals(cursor, ticker, hist, info.get("beta"), now, min_rows=50)
        except Exception:
            pass

        # stocks_historical
        try:
            financials = stock.financials
            if financials is not None and not financials.empty:
                for col in financials.columns[:5]:
                    year = col.year
                    def safe(key):
                        try: return int(financials.loc[key, col]) if key in financials.index else None
                        except: return None
                    upsert(cursor, "stocks_historical", {
                        "ticker": ticker, "year": year,
                        "revenue": safe("Total Revenue"), "net_income": safe("Net Income"),
                        "ebitda": safe("EBITDA"), "operating_income": safe("Operating Income"),
                        "last_updated": now,
                    }, keys=("ticker", "year"))
        except Exception:
            pass

        conn.commit()
        got_details = any(info.get(k) for k in ("longName", "shortName", "marketCap", "trailingPE", "totalRevenue"))
        return True, "" if got_details else "price only (Yahoo sent no details this time)"

    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return False, "{}: {}".format(type(e).__name__, str(e)[:100])


# ─────────────────────────────────────────
# Database connection that survives drop-outs
# ─────────────────────────────────────────
DB_ERROR_WORDS = ("OperationalError", "InterfaceError", "DatabaseError", "Lost connection",
                  "not available", "gone away", "2013", "2055", "2006")

def is_db_error(note):
    return any(w in (note or "") for w in DB_ERROR_WORDS)

def live_cursor(conn):
    """Returns (conn, cursor) on a working connection - reconnects if it was dropped."""
    for attempt in range(1, 6):
        try:
            if conn is not None and conn.is_connected():
                return conn, conn.cursor()
        except Exception:
            pass
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        try:
            conn = get_connection()
            return conn, conn.cursor()
        except Exception as e:
            conn = None
            wait = 10 * attempt
            print("      database reconnect failed ({}) - trying again in {}s".format(type(e).__name__, wait))
            time.sleep(wait)
    raise RuntimeError("Cannot reach the database - check your internet / Aiven, then run again with --missing")


# ─────────────────────────────────────────
# Which tickers to run
# ─────────────────────────────────────────
def pick_tickers(conn):
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        return [a.upper() for a in args]
    if "--missing" in sys.argv:
        cur = conn.cursor()
        cur.execute("""
            SELECT l.ticker FROM stocks_live l
            LEFT JOIN stocks_fundamentals f ON f.ticker = l.ticker
            WHERE l.company_name IS NULL OR l.current_price IS NULL OR f.ticker IS NULL
        """)
        have_gaps = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT ticker FROM stocks_live")
        in_db = {r[0] for r in cur.fetchall()}
        cur.close()
        return [t for t in ALL_TICKERS if t in have_gaps or t not in in_db]
    return list(ALL_TICKERS)


# ─────────────────────────────────────────
# Main Run
# ─────────────────────────────────────────
def main():
    conn, cursor = live_cursor(None)
    create_tables(cursor)
    conn.commit()
    cursor.close()

    tickers = pick_tickers(conn)

    print("=" * 60)
    print("  STOCK MARKET PRO - Data Fetch Started (safe mode)")
    print("  Tickers to fetch: {}".format(len(tickers)))
    print("  Futures tickers: {}".format(sum(1 for t in tickers if is_futures(t))))
    print("  Started at: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 60)

    success = 0
    partial = 0
    failed  = 0
    failed_list = []

    for i, ticker in enumerate(tickers, 1):
        # Aiven drops connections now and then -> always make sure we have a working one
        conn, cursor = live_cursor(conn)
        ok, note = fetch_and_save(ticker, cursor, conn)
        if not ok and is_db_error(note):
            # the connection died while saving this stock -> reconnect and try it once more
            conn, cursor = live_cursor(None)
            ok, note = fetch_and_save(ticker, cursor, conn)
        try: cursor.close()
        except Exception: pass

        tag = "[F]" if is_futures(ticker) else "   "
        if ok:
            success += 1
            if note:
                partial += 1
            print("  OK  {} [{}/{}] {} {}".format(tag, i, len(tickers), ticker, ("- " + note) if note else ""))
        else:
            failed += 1
            failed_list.append(ticker)
            print("  FAIL{} [{}/{}] {} - {}".format(tag, i, len(tickers), ticker, note))

        if i % 50 == 0:
            print("\n  Pausing 5s... ({} saved, {} failed)\n".format(success, failed))
            time.sleep(5)

    print("\n" + "=" * 60)
    print("  Successfully saved: {} tickers".format(success))
    if partial:
        print("    (of which {} got price only - run again later for their details)".format(partial))
    print("  Failed: {} tickers  (their old data was NOT touched)".format(failed))
    if failed_list:
        print("  Failed: {}".format(", ".join(failed_list)))
    print("  Finished at: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 60)

    try: conn.close()
    except Exception: pass

if __name__ == "__main__":
    main()
