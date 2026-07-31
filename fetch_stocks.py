# ============================================================
# STOCK MARKET PRO - fetch_stocks.py
# Fetches all metrics for 955 stocks and saves to MySQL
# ============================================================

import yfinance as yf
import mysql.connector
import pandas as pd
import time
from datetime import datetime
from tickers import ALL_TICKERS, TICKER_SECTOR_MAP

# ─────────────────────────────────────────
# MySQL Connection
# ─────────────────────────────────────────
def get_connection():
    return mysql.connector.connect(
        host="localhost",
        port=3306,
        user="root",
        password="Aroot092325",
        database="stock_market_pro_db"
    )

# ─────────────────────────────────────────
# Create All Tables
# ─────────────────────────────────────────
def create_tables(cursor):
    print("📦 Creating tables...")

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

    print("✅ All tables created!")

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
# Generate Prediction (rule-based for now)
# ─────────────────────────────────────────
def generate_prediction(current_price, rsi, macd, ma_50, ma_200, analyst_target):
    try:
        score = 50  # neutral baseline

        # RSI signal
        if rsi:
            if rsi < 30: score += 20   # oversold = bullish
            elif rsi > 70: score -= 20  # overbought = bearish
            elif rsi < 50: score += 5
            else: score -= 5

        # MACD signal
        if macd:
            if macd > 0: score += 10
            else: score -= 10

        # Moving average signal
        if ma_50 and ma_200 and current_price:
            if current_price > ma_50 > ma_200: score += 15   # golden cross zone
            elif current_price < ma_50 < ma_200: score -= 15  # death cross zone

        # Analyst target
        if analyst_target and current_price:
            upside = ((analyst_target - current_price) / current_price) * 100
            if upside > 20: score += 15
            elif upside > 10: score += 8
            elif upside < -10: score -= 15

        score = max(0, min(100, score))

        # Signal
        if score >= 75:   buy_signal = "Strong Buy"
        elif score >= 60: buy_signal = "Buy"
        elif score >= 40: buy_signal = "Hold"
        elif score >= 25: buy_signal = "Sell"
        else:             buy_signal = "Strong Sell"

        # Trend
        if ma_50 and ma_200:
            if ma_50 > ma_200: trend = "Uptrend"
            elif ma_50 < ma_200: trend = "Downtrend"
            else: trend = "Sideways"
        else:
            trend = "Sideways"

        # Price targets (simple projection)
        multiplier = (score - 50) / 500  # small % move
        target_1w = round(current_price * (1 + multiplier), 2) if current_price else None
        target_1m = round(current_price * (1 + multiplier * 3), 2) if current_price else None
        target_3m = round(current_price * (1 + multiplier * 8), 2) if current_price else None

        # Risk level
        if score >= 60: risk = "Low"
        elif score >= 40: risk = "Medium"
        else: risk = "High"

        confidence = round(abs(score - 50) * 1.5 + 40, 1)
        confidence = min(95, confidence)

        return buy_signal, trend, round(score, 1), risk, confidence, target_1w, target_1m, target_3m
    except:
        return "Hold", "Sideways", 50.0, "Medium", 50.0, None, None, None

# ─────────────────────────────────────────
# Fetch & Save One Stock
# ─────────────────────────────────────────
def fetch_and_save(ticker, cursor, conn):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        sector = TICKER_SECTOR_MAP.get(ticker, "Unknown")
        now = datetime.now()

        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        prev_close    = info.get("previousClose")
        price_change  = round(current_price - prev_close, 4) if current_price and prev_close else None
        price_chg_pct = round((price_change / prev_close) * 100, 4) if price_change and prev_close else None
        avg_vol       = info.get("averageVolume")
        volume        = info.get("volume")
        rel_vol       = round(volume / avg_vol, 2) if volume and avg_vol else None

        # ── stocks_live ──
        cursor.execute("""
            INSERT INTO stocks_live VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                sector=VALUES(sector), industry=VALUES(industry), company_name=VALUES(company_name),
                current_price=VALUES(current_price), open_price=VALUES(open_price),
                high=VALUES(high), low=VALUES(low), prev_close=VALUES(prev_close),
                price_change=VALUES(price_change), price_change_pct=VALUES(price_change_pct),
                week_52_high=VALUES(week_52_high), week_52_low=VALUES(week_52_low),
                volume=VALUES(volume), avg_volume=VALUES(avg_volume),
                relative_volume=VALUES(relative_volume), market_cap=VALUES(market_cap),
                last_updated=VALUES(last_updated)
        """, (
            ticker, sector, info.get("industry"), info.get("longName"),
            current_price, info.get("open"), info.get("dayHigh"), info.get("dayLow"),
            prev_close, price_change, price_chg_pct,
            info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow"),
            volume, avg_vol, rel_vol, info.get("marketCap"), now
        ))

        # ── stocks_fundamentals ──
        cursor.execute("""
            INSERT INTO stocks_fundamentals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                pe_ratio=VALUES(pe_ratio), forward_pe=VALUES(forward_pe),
                pb_ratio=VALUES(pb_ratio), ps_ratio=VALUES(ps_ratio),
                ev_ebitda=VALUES(ev_ebitda), eps=VALUES(eps),
                revenue=VALUES(revenue), net_income=VALUES(net_income),
                profit_margin=VALUES(profit_margin), roe=VALUES(roe),
                roa=VALUES(roa), debt_to_equity=VALUES(debt_to_equity),
                current_ratio=VALUES(current_ratio), quick_ratio=VALUES(quick_ratio),
                last_updated=VALUES(last_updated)
        """, (
            ticker,
            info.get("trailingPE"), info.get("forwardPE"),
            info.get("priceToBook"), info.get("priceToSalesTrailing12Months"),
            info.get("enterpriseToEbitda"), info.get("trailingEps"),
            info.get("totalRevenue"), info.get("netIncomeToCommon"),
            info.get("profitMargins"), info.get("returnOnEquity"),
            info.get("returnOnAssets"), info.get("debtToEquity"),
            info.get("currentRatio"), info.get("quickRatio"), now
        ))

        # ── stocks_dividends ──
        ex_div = info.get("exDividendDate")
        ex_div_date = datetime.fromtimestamp(ex_div).date() if ex_div else None
        cursor.execute("""
            INSERT INTO stocks_dividends VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                dividend_yield=VALUES(dividend_yield), dividend_rate=VALUES(dividend_rate),
                payout_ratio=VALUES(payout_ratio), ex_dividend_date=VALUES(ex_dividend_date),
                last_updated=VALUES(last_updated)
        """, (
            ticker, info.get("dividendYield"), info.get("dividendRate"),
            info.get("payoutRatio"), ex_div_date, now
        ))

        # ── stocks_analyst ──
        target = info.get("targetMeanPrice")
        upside = round(((target - current_price) / current_price) * 100, 2) if target and current_price else None
        cursor.execute("""
            INSERT INTO stocks_analyst VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                target_price=VALUES(target_price), analyst_rating=VALUES(analyst_rating),
                num_analysts=VALUES(num_analysts), upside_pct=VALUES(upside_pct),
                recommendation=VALUES(recommendation), last_updated=VALUES(last_updated)
        """, (
            ticker, target, info.get("recommendationKey"),
            info.get("numberOfAnalystOpinions"), upside,
            info.get("recommendationMean"), now
        ))

        # ── stocks_smartmoney ──
        cursor.execute("""
            INSERT INTO stocks_smartmoney VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                institutional_ownership=VALUES(institutional_ownership),
                insider_ownership=VALUES(insider_ownership),
                short_interest=VALUES(short_interest),
                short_ratio=VALUES(short_ratio),
                shares_short=VALUES(shares_short),
                last_updated=VALUES(last_updated)
        """, (
            ticker,
            info.get("institutionOwnership"), info.get("insidersPercentHeld"),
            info.get("shortPercentOfFloat"), info.get("shortRatio"),
            info.get("sharesShort"), now
        ))

        # ── stocks_earnings ──
        cursor.execute("""
            INSERT INTO stocks_earnings VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                next_earnings_date=VALUES(next_earnings_date),
                eps_estimate=VALUES(eps_estimate), eps_actual=VALUES(eps_actual),
                earnings_surprise_pct=VALUES(earnings_surprise_pct),
                revenue_estimate=VALUES(revenue_estimate),
                revenue_actual=VALUES(revenue_actual),
                last_updated=VALUES(last_updated)
        """, (
            ticker, None,
            info.get("forwardEps"), info.get("trailingEps"),
            None, None, info.get("totalRevenue"), now
        ))

        # ── stocks_technicals + predictions (need historical prices) ──
        try:
            hist = stock.history(period="1y")
            if not hist.empty and len(hist) > 50:
                closes = hist["Close"]
                ma50  = round(float(closes.rolling(50).mean().iloc[-1]), 4)
                ma200 = round(float(closes.rolling(200).mean().iloc[-1]), 4) if len(closes) >= 200 else None
                rsi   = calculate_rsi(closes)
                macd_val, macd_sig, macd_hist = calculate_macd(closes)
                golden = bool(ma50 and ma200 and ma50 > ma200)
                death  = bool(ma50 and ma200 and ma50 < ma200)
                returns = closes.pct_change().dropna()
                volatility = round(float(returns.std() * (252 ** 0.5) * 100), 2)

                cursor.execute("""
                    INSERT INTO stocks_technicals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        ma_50=VALUES(ma_50), ma_200=VALUES(ma_200), beta=VALUES(beta),
                        rsi_14=VALUES(rsi_14), macd=VALUES(macd), macd_signal=VALUES(macd_signal),
                        macd_hist=VALUES(macd_hist), golden_cross=VALUES(golden_cross),
                        death_cross=VALUES(death_cross), volatility=VALUES(volatility),
                        last_updated=VALUES(last_updated)
                """, (
                    ticker, ma50, ma200, info.get("beta"),
                    rsi, macd_val, macd_sig, macd_hist,
                    golden, death, volatility, now
                ))

                # predictions
                buy_signal, trend, momentum, risk, confidence, t1w, t1m, t3m = generate_prediction(
                    current_price, rsi, macd_val, ma50, ma200, target
                )
                cursor.execute("""
                    INSERT INTO stocks_predictions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        current_price=VALUES(current_price),
                        target_1w=VALUES(target_1w), target_1m=VALUES(target_1m),
                        target_3m=VALUES(target_3m), buy_signal=VALUES(buy_signal),
                        trend=VALUES(trend), momentum_score=VALUES(momentum_score),
                        risk_level=VALUES(risk_level), confidence_pct=VALUES(confidence_pct),
                        last_updated=VALUES(last_updated)
                """, (
                    ticker, current_price, t1w, t1m, t3m,
                    buy_signal, trend, momentum, risk, confidence, now
                ))
        except Exception as e:
            pass

        # ── stocks_historical (last 5 years) ──
        try:
            financials = stock.financials
            if financials is not None and not financials.empty:
                for col in financials.columns[:5]:
                    year = col.year
                    def safe(key):
                        try: return int(financials.loc[key, col]) if key in financials.index else None
                        except: return None
                    cursor.execute("""
                        INSERT INTO stocks_historical
                            (ticker, year, revenue, net_income, ebitda, operating_income, last_updated)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                            revenue=VALUES(revenue), net_income=VALUES(net_income),
                            ebitda=VALUES(ebitda), operating_income=VALUES(operating_income),
                            last_updated=VALUES(last_updated)
                    """, (
                        ticker, year,
                        safe("Total Revenue"), safe("Net Income"),
                        safe("EBITDA"), safe("Operating Income"), now
                    ))
        except:
            pass

        conn.commit()
        return True

    except Exception as e:
        return False

# ─────────────────────────────────────────
# Main Run
# ─────────────────────────────────────────
def main():
    print("=" * 60)
    print("  STOCK MARKET PRO - Data Fetch Started")
    print(f"  Total stocks to fetch: {len(ALL_TICKERS)}")
    print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    conn = get_connection()
    cursor = conn.cursor()

    create_tables(cursor)
    conn.commit()

    success = 0
    failed  = 0
    failed_list = []

    for i, ticker in enumerate(ALL_TICKERS, 1):
        result = fetch_and_save(ticker, cursor, conn)
        if result:
            success += 1
            print(f"  ✅ [{i}/{len(ALL_TICKERS)}] {ticker:<10} saved")
        else:
            failed += 1
            failed_list.append(ticker)
            print(f"  ❌ [{i}/{len(ALL_TICKERS)}] {ticker:<10} failed")

        # small pause every 50 stocks to avoid rate limiting
        if i % 50 == 0:
            print(f"\n  ⏳ Pausing 5 seconds... ({success} saved, {failed} failed)\n")
            time.sleep(5)

    print("\n" + "=" * 60)
    print(f"  ✅ Successfully saved: {success} stocks")
    print(f"  ❌ Failed: {failed} stocks")
    if failed_list:
        print(f"  Failed tickers: {', '.join(failed_list)}")
    print(f"  Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
