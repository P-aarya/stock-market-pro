import os
from dotenv import load_dotenv
load_dotenv()
import mysql.connector

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "Aroot092325"),
        database=os.getenv("DB_NAME", "stock_market_pro_db")
    )

def screen_stocks(filters: dict, limit: int = 30):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT 
            f.ticker,
            l.company_name,
            l.sector,
            l.current_price,
            l.price_change_pct,
            f.market_cap,
            f.market_cap_category,
            f.pe_ratio,
            f.forward_pe,
            f.peg_ratio,
            f.pb_ratio,
            f.revenue_growth,
            f.earnings_growth,
            f.profit_margin,
            f.operating_margin,
            f.gross_margin,
            f.roe,
            f.roa,
            f.debt_to_equity,
            f.current_ratio,
            f.free_cash_flow,
            f.dividend_yield,
            f.beta,
            f.fifty_two_week_high,
            f.fifty_two_week_low,
            f.recommendation_mean,
            f.target_mean_price,
            f.short_ratio,
            COALESCE(p.target_1w, l.current_price) as target_1w,
            COALESCE(p.target_1m, l.current_price) as target_1m,
            COALESCE(p.buy_signal, 'N/A') as buy_signal,
            COALESCE(p.trend, 'NEUTRAL') as trend,
            COALESCE(p.momentum_score, 0) as momentum_score,
            COALESCE(p.confidence_pct, 0) as confidence_pct,
            COALESCE(p.prob_up_1w, 0.5) as prob_up_1w,
            COALESCE(p.risk_level, 'Medium') as risk_level
        FROM stocks_fundamentals f
        JOIN stocks_live l ON f.ticker = l.ticker
        LEFT JOIN stocks_predictions p ON f.ticker = p.ticker
        WHERE l.current_price IS NOT NULL
        AND l.current_price > 0
    """

    params = []

    # Current price filter
    if "current_price" in filters:
        if "min" in filters["current_price"]:
            query += " AND l.current_price >= %s"
            params.append(filters["current_price"]["min"])
        if "max" in filters["current_price"]:
            query += " AND l.current_price <= %s"
            params.append(filters["current_price"]["max"])

    # Market cap category
    if "market_cap_category" in filters:
        query += " AND f.market_cap_category = %s"
        params.append(filters["market_cap_category"])

    # Sector
    if "sector" in filters:
        query += " AND l.sector LIKE %s"
        params.append(f"%{filters['sector']}%")

    # PE Ratio
    if "pe_ratio" in filters:
        if "min" in filters["pe_ratio"]:
            query += " AND f.pe_ratio >= %s"
            params.append(filters["pe_ratio"]["min"])
        if "max" in filters["pe_ratio"]:
            query += " AND f.pe_ratio <= %s AND f.pe_ratio > 0"
            params.append(filters["pe_ratio"]["max"])

    # Forward PE
    if "forward_pe" in filters:
        if "min" in filters["forward_pe"]:
            query += " AND f.forward_pe >= %s"
            params.append(filters["forward_pe"]["min"])
        if "max" in filters["forward_pe"]:
            query += " AND f.forward_pe <= %s AND f.forward_pe > 0"
            params.append(filters["forward_pe"]["max"])

    # PEG Ratio
    if "peg_ratio" in filters:
        if "min" in filters["peg_ratio"]:
            query += " AND f.peg_ratio >= %s"
            params.append(filters["peg_ratio"]["min"])
        if "max" in filters["peg_ratio"]:
            query += " AND f.peg_ratio <= %s AND f.peg_ratio > 0"
            params.append(filters["peg_ratio"]["max"])

    # Revenue Growth
    if "revenue_growth" in filters:
        if "min" in filters["revenue_growth"]:
            query += " AND f.revenue_growth >= %s"
            params.append(filters["revenue_growth"]["min"])
        if "max" in filters["revenue_growth"]:
            query += " AND f.revenue_growth <= %s"
            params.append(filters["revenue_growth"]["max"])

    # Earnings Growth
    if "earnings_growth" in filters:
        if "min" in filters["earnings_growth"]:
            query += " AND f.earnings_growth >= %s"
            params.append(filters["earnings_growth"]["min"])
        if "max" in filters["earnings_growth"]:
            query += " AND f.earnings_growth <= %s"
            params.append(filters["earnings_growth"]["max"])

    # Profit Margin
    if "profit_margin" in filters:
        if "min" in filters["profit_margin"]:
            query += " AND f.profit_margin >= %s"
            params.append(filters["profit_margin"]["min"])
        if "max" in filters["profit_margin"]:
            query += " AND f.profit_margin <= %s"
            params.append(filters["profit_margin"]["max"])

    # Operating Margin
    if "operating_margin" in filters:
        if "min" in filters["operating_margin"]:
            query += " AND f.operating_margin >= %s"
            params.append(filters["operating_margin"]["min"])
        if "max" in filters["operating_margin"]:
            query += " AND f.operating_margin <= %s"
            params.append(filters["operating_margin"]["max"])

    # ROE
    if "roe" in filters:
        if "min" in filters["roe"]:
            query += " AND f.roe >= %s"
            params.append(filters["roe"]["min"])
        if "max" in filters["roe"]:
            query += " AND f.roe <= %s"
            params.append(filters["roe"]["max"])

    # Debt to Equity
    if "debt_to_equity" in filters:
        if "min" in filters["debt_to_equity"]:
            query += " AND f.debt_to_equity >= %s"
            params.append(filters["debt_to_equity"]["min"])
        if "max" in filters["debt_to_equity"]:
            query += " AND f.debt_to_equity <= %s"
            params.append(filters["debt_to_equity"]["max"])

    # Dividend Yield
    if "dividend_yield" in filters:
        if "min" in filters["dividend_yield"]:
            query += " AND f.dividend_yield >= %s"
            params.append(filters["dividend_yield"]["min"])
        if "max" in filters["dividend_yield"]:
            query += " AND f.dividend_yield <= %s"
            params.append(filters["dividend_yield"]["max"])

    # Beta
    if "beta" in filters:
        if "min" in filters["beta"]:
            query += " AND f.beta >= %s"
            params.append(filters["beta"]["min"])
        if "max" in filters["beta"]:
            query += " AND f.beta <= %s"
            params.append(filters["beta"]["max"])

    # Market cap USD range
    if "market_cap" in filters:
        if "min" in filters["market_cap"]:
            query += " AND f.market_cap >= %s"
            params.append(filters["market_cap"]["min"])
        if "max" in filters["market_cap"]:
            query += " AND f.market_cap <= %s"
            params.append(filters["market_cap"]["max"])

    # Price change today (trending up/down)
    if "price_change_pct" in filters:
        if "min" in filters["price_change_pct"]:
            query += " AND l.price_change_pct >= %s"
            params.append(filters["price_change_pct"]["min"])
        if "max" in filters["price_change_pct"]:
            query += " AND l.price_change_pct <= %s"
            params.append(filters["price_change_pct"]["max"])

    # Buy signal
    if "buy_signal" in filters:
        query += " AND p.buy_signal = %s"
        params.append(filters["buy_signal"])

    # Trend
    if "trend" in filters:
        query += " AND p.trend = %s"
        params.append(filters["trend"])

    # Free cash flow positive
    if filters.get("positive_fcf"):
        query += " AND f.free_cash_flow > 0"

    # Pays dividends
    if filters.get("pays_dividend"):
        query += " AND f.dividend_yield > 0"

    # Order: prioritise stocks with prediction data, then by momentum, then price change
    query += """
        ORDER BY 
            CASE WHEN p.momentum_score IS NOT NULL THEN 0 ELSE 1 END,
            COALESCE(p.momentum_score, 0) DESC,
            l.price_change_pct DESC
    """

    query += f" LIMIT {limit}"

    cursor.execute(query, params)
    results = cursor.fetchall()

    # Auto-broaden: if fewer than 5 results, retry with relaxed filters
    if len(results) < 5 and filters:
        relaxed = {}
        # Keep only the most important filter
        for key in ["current_price", "sector", "market_cap_category"]:
            if key in filters:
                relaxed[key] = filters[key]
                break
        if relaxed != filters:
            cursor.execute(query.replace(
                "WHERE l.current_price IS NOT NULL\n        AND l.current_price > 0",
                "WHERE l.current_price IS NOT NULL AND l.current_price > 0"
            ), params)

        # Full fallback — just return best stocks broadly
        cursor.execute("""
            SELECT 
                f.ticker, l.company_name, l.sector, l.current_price, l.price_change_pct,
                f.market_cap, f.market_cap_category, f.pe_ratio, f.forward_pe, f.peg_ratio,
                f.pb_ratio, f.revenue_growth, f.earnings_growth, f.profit_margin,
                f.operating_margin, f.gross_margin, f.roe, f.roa, f.debt_to_equity,
                f.current_ratio, f.free_cash_flow, f.dividend_yield, f.beta,
                f.fifty_two_week_high, f.fifty_two_week_low, f.recommendation_mean,
                f.target_mean_price, f.short_ratio,
                COALESCE(p.target_1w, l.current_price) as target_1w,
                COALESCE(p.target_1m, l.current_price) as target_1m,
                COALESCE(p.buy_signal, 'N/A') as buy_signal,
                COALESCE(p.trend, 'NEUTRAL') as trend,
                COALESCE(p.momentum_score, 0) as momentum_score,
                COALESCE(p.confidence_pct, 0) as confidence_pct,
                COALESCE(p.prob_up_1w, 0.5) as prob_up_1w,
                COALESCE(p.risk_level, 'Medium') as risk_level
            FROM stocks_fundamentals f
            JOIN stocks_live l ON f.ticker = l.ticker
            LEFT JOIN stocks_predictions p ON f.ticker = p.ticker
            WHERE l.current_price IS NOT NULL AND l.current_price > 0
            AND f.profit_margin > 0
            ORDER BY COALESCE(p.momentum_score, 0) DESC, l.price_change_pct DESC
            LIMIT %s
        """, (limit,))
        results = cursor.fetchall()

    conn.close()
    return results


def get_trending_stocks(direction: str = "up", limit: int = 20):
    """Get stocks trending up or down today."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    order = "DESC" if direction == "up" else "ASC"
    cursor.execute(f"""
        SELECT l.ticker, l.company_name, l.sector, l.current_price, l.price_change_pct,
               f.market_cap_category, f.pe_ratio, f.profit_margin, f.beta,
               COALESCE(p.buy_signal,'N/A') as buy_signal,
               COALESCE(p.trend,'NEUTRAL') as trend,
               COALESCE(p.momentum_score,0) as momentum_score,
               COALESCE(p.confidence_pct,0) as confidence_pct,
               COALESCE(p.target_1w, l.current_price) as target_1w,
               COALESCE(p.risk_level,'Medium') as risk_level
        FROM stocks_live l
        JOIN stocks_fundamentals f ON l.ticker = f.ticker
        LEFT JOIN stocks_predictions p ON l.ticker = p.ticker
        WHERE l.price_change_pct IS NOT NULL AND l.current_price > 0
        ORDER BY l.price_change_pct {order}
        LIMIT %s
    """, (limit,))
    results = cursor.fetchall()
    conn.close()
    return results


def find_similar_stocks(ticker: str, limit: int = 10):
    """Find stocks similar to a given ticker."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get reference stock data
    cursor.execute("""
        SELECT l.sector, f.market_cap_category, f.pe_ratio, f.profit_margin, l.current_price
        FROM stocks_live l
        JOIN stocks_fundamentals f ON l.ticker = f.ticker
        WHERE l.ticker = %s
    """, (ticker.upper(),))
    ref = cursor.fetchone()

    if not ref:
        conn.close()
        return []

    cursor.execute("""
        SELECT l.ticker, l.company_name, l.sector, l.current_price, l.price_change_pct,
               f.market_cap_category, f.pe_ratio, f.profit_margin, f.beta, f.dividend_yield,
               COALESCE(p.buy_signal,'N/A') as buy_signal,
               COALESCE(p.trend,'NEUTRAL') as trend,
               COALESCE(p.momentum_score,0) as momentum_score,
               COALESCE(p.confidence_pct,0) as confidence_pct,
               COALESCE(p.target_1w, l.current_price) as target_1w,
               COALESCE(p.risk_level,'Medium') as risk_level,
               f.revenue_growth, f.earnings_growth, f.debt_to_equity,
               f.roe, f.free_cash_flow
        FROM stocks_live l
        JOIN stocks_fundamentals f ON l.ticker = f.ticker
        LEFT JOIN stocks_predictions p ON l.ticker = p.ticker
        WHERE l.sector = %s
        AND l.ticker != %s
        AND l.current_price > 0
        ORDER BY COALESCE(p.momentum_score,0) DESC, l.price_change_pct DESC
        LIMIT %s
    """, (ref['sector'], ticker.upper(), limit))

    results = cursor.fetchall()
    conn.close()
    return results


def get_stock_snapshot(ticker: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT f.*, l.company_name, l.sector, l.current_price, l.price_change_pct,
               COALESCE(p.target_1w, l.current_price) as target_1w,
               COALESCE(p.target_1m, l.current_price) as target_1m,
               COALESCE(p.buy_signal,'N/A') as buy_signal,
               COALESCE(p.trend,'NEUTRAL') as trend,
               COALESCE(p.momentum_score,0) as momentum_score,
               COALESCE(p.confidence_pct,0) as confidence_pct,
               COALESCE(p.prob_up_1w,0.5) as prob_up_1w,
               COALESCE(p.risk_level,'Medium') as risk_level,
               p.key_factors
        FROM stocks_fundamentals f
        JOIN stocks_live l ON f.ticker = l.ticker
        LEFT JOIN stocks_predictions p ON f.ticker = p.ticker
        WHERE f.ticker = %s
    """, (ticker,))
    result = cursor.fetchone()
    conn.close()
    return result


def get_top_movers(limit: int = 10):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT ticker, company_name, sector, current_price, price_change_pct
        FROM stocks_live WHERE price_change_pct IS NOT NULL
        ORDER BY price_change_pct DESC LIMIT %s
    """, (limit,))
    gainers = cursor.fetchall()
    cursor.execute("""
        SELECT ticker, company_name, sector, current_price, price_change_pct
        FROM stocks_live WHERE price_change_pct IS NOT NULL
        ORDER BY price_change_pct ASC LIMIT %s
    """, (limit,))
    losers = cursor.fetchall()
    conn.close()
    return {"gainers": gainers, "losers": losers}


def get_sector_summary():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT sector, COUNT(*) as stock_count,
               ROUND(AVG(price_change_pct),2) as avg_change,
               ROUND(AVG(f.pe_ratio),2) as avg_pe,
               ROUND(AVG(f.profit_margin),2) as avg_margin
        FROM stocks_live l
        JOIN stocks_fundamentals f ON l.ticker = f.ticker
        WHERE l.price_change_pct IS NOT NULL
        GROUP BY sector ORDER BY avg_change DESC
    """)
    results = cursor.fetchall()
    conn.close()
    return results
