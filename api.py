# ============================================================
# STOCK MARKET PRO - api.py (Final Clean Version)
# ============================================================
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import mysql.connector
import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import Optional
import os
import json
from dotenv import load_dotenv
load_dotenv()

app = FastAPI(title="Stock Market Pro API")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# ── Serve dashboard.html directly from port 8000 ──
@app.get("/")
def serve_root():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"status": "running", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

@app.get("/dashboard.html")
def serve_dashboard():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    return FileResponse(path)

@app.get("/health")
def health():
    return {"status": "running", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

# ── DB ──
def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "stock_market_pro_db")
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        config["ssl_disabled"] = False
    return mysql.connector.connect(**config)

def query(sql, params=None):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, params or [])
    result = cursor.fetchall()
    cursor.close()
    conn.close()
    clean = []
    for row in result:
        r = {}
        for k, v in row.items():
            if hasattr(v, 'isoformat'):
                r[k] = str(v)
            elif hasattr(v, '__float__'):
                try: r[k] = float(v)
                except: r[k] = v
            else:
                r[k] = v
        clean.append(r)
    return clean

# ── Parse yfinance news (1.4.x format) ──
def parse_news(raw):
    articles = []
    for item in (raw or []):
        try:
            content = item.get('content', {})
            if content:
                title  = content.get('title', '')
                source = (content.get('provider', {}) or {}).get('displayName', '') or 'Yahoo Finance'
                url    = (content.get('canonicalUrl', {}) or {}).get('url', '') or (content.get('clickThroughUrl', {}) or {}).get('url', '')
                pub    = content.get('pubDate', '')
                pub_ts = None
                if pub:
                    try: pub_ts = int(datetime.fromisoformat(pub.replace('Z','+00:00')).timestamp())
                    except: pass
            else:
                title  = item.get('title', '')
                src    = item.get('source', {})
                source = src.get('displayName','') if isinstance(src,dict) else str(src or 'Yahoo Finance')
                url    = item.get('link','') or item.get('url','')
                pub_ts = item.get('providerPublishTime')
            if title and title.strip():
                articles.append({'title':title.strip(),'source':source,'url':url,'published':pub_ts})
        except: continue
    return articles

# ══════════════════════════════════════════════════════
# STOCKS
# ══════════════════════════════════════════════════════
@app.get("/api/stocks")
def get_stocks(sector: Optional[str]=None, search: Optional[str]=None, limit: int=1000, offset: int=0):
    sql = """
        SELECT l.ticker, l.sector, l.industry, l.company_name,
               l.current_price, l.price_change, l.price_change_pct,
               l.open_price, l.high, l.low, l.prev_close,
               l.week_52_high, l.week_52_low,
               l.volume, l.avg_volume, l.relative_volume,
               l.market_cap, l.last_updated,
               f.pe_ratio, f.forward_pe, f.pb_ratio, f.ps_ratio,
               f.ev_ebitda, f.eps, f.revenue, f.net_income,
               f.profit_margin, f.roe, f.roa, f.debt_to_equity,
               f.current_ratio, f.quick_ratio,
               d.dividend_yield, d.dividend_rate, d.payout_ratio,
               a.target_price, a.analyst_rating, a.num_analysts, a.upside_pct,
               t.ma_50, t.ma_200, t.rsi_14, t.macd, t.beta,
               t.golden_cross, t.death_cross, t.volatility,
               sm.institutional_ownership, sm.short_interest, sm.short_ratio, sm.insider_ownership
        FROM stocks_live l
        LEFT JOIN stocks_fundamentals f ON l.ticker=f.ticker
        LEFT JOIN stocks_dividends d ON l.ticker=d.ticker
        LEFT JOIN stocks_analyst a ON l.ticker=a.ticker
        LEFT JOIN stocks_technicals t ON l.ticker=t.ticker
        LEFT JOIN stocks_smartmoney sm ON l.ticker=sm.ticker
        WHERE (l.sector != 'Indices' OR l.sector IS NULL)
        AND l.current_price IS NOT NULL
    """
    params = []
    if sector: sql += " AND l.sector=%s"; params.append(sector)
    if search:
        sql += " AND (l.ticker LIKE %s OR l.company_name LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])
    sql += " ORDER BY l.market_cap DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    return query(sql, params)

@app.get("/api/stocks/{ticker}")
def get_stock(ticker: str):
    r = query("""
        SELECT l.*,f.*,d.*,a.*,t.*,sm.*
        FROM stocks_live l
        LEFT JOIN stocks_fundamentals f ON l.ticker=f.ticker
        LEFT JOIN stocks_dividends d ON l.ticker=d.ticker
        LEFT JOIN stocks_analyst a ON l.ticker=a.ticker
        LEFT JOIN stocks_technicals t ON l.ticker=t.ticker
        LEFT JOIN stocks_smartmoney sm ON l.ticker=sm.ticker
        WHERE l.ticker=%s
    """, [ticker.upper()])
    return r[0] if r else {}

@app.get("/api/stocks/{ticker}/historical")
def get_historical(ticker: str):
    return query("SELECT * FROM stocks_historical WHERE ticker=%s ORDER BY year ASC", [ticker.upper()])

@app.get("/api/stocks/{ticker}/price-history")
def get_price_history(ticker: str, period: str="5y"):
    try:
        hist = yf.Ticker(ticker.upper()).history(period=period)
        if hist.empty: return []
        hist = hist.reset_index()
        result = []
        for _, row in hist.iterrows():
            result.append({
                "date":   str(row["Date"])[:10],
                "open":   round(float(row["Open"]),2),
                "high":   round(float(row["High"]),2),
                "low":    round(float(row["Low"]),2),
                "close":  round(float(row["Close"]),2),
                "volume": int(row["Volume"]),
            })
        return result
    except: return []

@app.get("/api/stocks/{ticker}/intraday")
def get_intraday(ticker: str):
    try:
        hist = yf.Ticker(ticker.upper()).history(period="1d", interval="5m")
        if hist.empty:
            hist = yf.Ticker(ticker.upper()).history(period="2d", interval="5m")
        if hist.empty:
            return {"data":[],"market_status":"closed","message":"No intraday data available"}
        hist = hist.reset_index()
        result = []
        for _, row in hist.iterrows():
            try:
                dt = str(row["Datetime"])[:16]
                result.append({
                    "time":     dt[11:16],
                    "datetime": dt,
                    "open":     round(float(row["Open"]),2),
                    "high":     round(float(row["High"]),2),
                    "low":      round(float(row["Low"]),2),
                    "close":    round(float(row["Close"]),2),
                    "volume":   int(row["Volume"])
                })
            except: continue
        if not result:
            return {"data":[],"market_status":"closed","message":"No data"}
        first = result[0]["close"]
        last  = result[-1]["close"]
        chg   = round((last-first)/first*100,2) if first else 0
        return {
            "data": result,
            "market_status": "open",
            "open_price": first,
            "current_price": last,
            "day_high":  max(r["high"] for r in result),
            "day_low":   min(r["low"]  for r in result),
            "change_pct": chg,
            "data_points": len(result)
        }
    except Exception as e:
        return {"data":[],"market_status":"error","message":str(e)}

# ══════════════════════════════════════════════════════
# MARKET
# ══════════════════════════════════════════════════════
@app.get("/api/market/summary")
def get_summary():
    r = query("""
        SELECT COUNT(*) as total_stocks,
               SUM(CASE WHEN price_change_pct>0 THEN 1 ELSE 0 END) as gainers,
               SUM(CASE WHEN price_change_pct<0 THEN 1 ELSE 0 END) as losers,
               ROUND(AVG(price_change_pct),2) as avg_change_pct,
               MAX(last_updated) as last_updated
        FROM stocks_live
        WHERE (sector!='Indices' OR sector IS NULL) AND current_price IS NOT NULL
    """)
    return r[0] if r else {}

@app.get("/api/market/gainers")
def get_gainers(limit: int=20):
    return query("""SELECT ticker,company_name,sector,current_price,price_change,price_change_pct,volume
        FROM stocks_live WHERE price_change_pct IS NOT NULL AND current_price IS NOT NULL
        AND (sector!='Indices' OR sector IS NULL) ORDER BY price_change_pct DESC LIMIT %s""", [limit])

@app.get("/api/market/losers")
def get_losers(limit: int=20):
    return query("""SELECT ticker,company_name,sector,current_price,price_change,price_change_pct,volume
        FROM stocks_live WHERE price_change_pct IS NOT NULL AND current_price IS NOT NULL
        AND (sector!='Indices' OR sector IS NULL) ORDER BY price_change_pct ASC LIMIT %s""", [limit])

@app.get("/api/market/most-active")
def get_active(limit: int=20):
    return query("""SELECT ticker,company_name,sector,current_price,price_change_pct,volume,market_cap
        FROM stocks_live WHERE volume IS NOT NULL AND current_price IS NOT NULL
        AND (sector!='Indices' OR sector IS NULL) ORDER BY volume DESC LIMIT %s""", [limit])

@app.get("/api/market/sectors")
def get_sectors():
    return query("""
        SELECT sector, COUNT(*) as stock_count,
               ROUND(AVG(price_change_pct),2) as avg_change_pct,
               SUM(CASE WHEN price_change_pct>0 THEN 1 ELSE 0 END) as gainers,
               SUM(CASE WHEN price_change_pct<0 THEN 1 ELSE 0 END) as losers,
               ROUND(SUM(market_cap)/1e9,2) as total_market_cap_bn
        FROM stocks_live WHERE sector IS NOT NULL AND sector!='Indices' AND current_price IS NOT NULL
        GROUP BY sector ORDER BY avg_change_pct DESC
    """)

@app.get("/api/stocks/{ticker}/quarterly")
def get_quarterly(ticker: str):
    """Fetch quarterly earnings results from yfinance."""
    try:
        t = yf.Ticker(ticker.upper())
        stmt = t.quarterly_income_stmt
        if stmt is None or stmt.empty:
            stmt = t.quarterly_financials
        if stmt is None or stmt.empty:
            return []

        # Find correct row names (yfinance changes these)
        def find_row(keys):
            for k in keys:
                for idx in stmt.index:
                    if k.lower() in str(idx).lower():
                        return str(idx)
            return None

        rev_key = find_row(['total revenue','revenue'])
        ni_key = find_row(['net income from continuing operation net minori','net income'])
        gp_key = find_row(['gross profit'])
        op_key = find_row(['operating income','ebit'])

        results = []
        for col in stmt.columns[:8]:
            try:
                year = str(col)[:4]
                month = int(str(col)[5:7]) if len(str(col)) > 6 else 1
                quarter = f"Q{((month-1)//3)+1} {year}"

                def safe(key):
                    try:
                        if key and key in stmt.index:
                            v = stmt.loc[key, col]
                            return float(v) if v is not None and str(v) != 'nan' else None
                    except: pass
                    return None

                results.append({
                    "quarter": quarter,
                    "date": str(col)[:10],
                    "revenue": safe(rev_key),
                    "net_income": safe(ni_key),
                    "gross_profit": safe(gp_key),
                    "operating_income": safe(op_key),
                    "eps_actual": None,
                    "eps_estimate": None,
                    "surprise_pct": None,
                    "revenue_yoy": None
                })
            except: continue

        # Add EPS from earnings history
        try:
            eh = t.earnings_history
            if eh is not None and not eh.empty:
                for i, (_, row) in enumerate(eh.iterrows()):
                    if i < len(results):
                        ea = row.get('epsActual') or row.get('EPS Actual')
                        ee = row.get('epsEstimate') or row.get('EPS Estimate')
                        results[i]['eps_actual'] = float(ea) if ea and str(ea)!='nan' else None
                        results[i]['eps_estimate'] = float(ee) if ee and str(ee)!='nan' else None
                        if results[i]['eps_actual'] and results[i]['eps_estimate'] and results[i]['eps_estimate'] != 0:
                            results[i]['surprise_pct'] = round((results[i]['eps_actual'] - results[i]['eps_estimate']) / abs(results[i]['eps_estimate']) * 100, 1)
        except: pass

        # YoY revenue growth
        for i in range(len(results)):
            if i + 4 < len(results) and results[i]['revenue'] and results[i+4]['revenue']:
                results[i]['revenue_yoy'] = round((results[i]['revenue'] - results[i+4]['revenue']) / abs(results[i+4]['revenue']) * 100, 1)

        return [r for r in results if r['revenue'] or r['net_income']]
    except Exception as e:
        print(f"Quarterly error for {ticker}: {e}")
        return []

@app.get("/api/stocks/{ticker}/company")
def get_company_data(ticker: str):
    """Fetch comprehensive company data including financials and shareholders."""
    try:
        t = yf.Ticker(ticker.upper())
        info = t.info or {}

        # Get balance sheet - multi year
        balance_sheet_years = []
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                for col in bs.columns[:3]:  # last 3 years
                    year = str(col)[:4]
                    def safe_get(key):
                        try: return float(bs.loc[key, col]) if key in bs.index else None
                        except: return None
                    ta = safe_get('Total Assets')
                    ca = safe_get('Current Assets')
                    balance_sheet_years.append({
                        "year": year,
                        "total_assets": ta,
                        "total_liabilities": safe_get('Total Liabilities Net Minority Interest'),
                        "total_equity": safe_get('Stockholders Equity'),
                        "total_debt": safe_get('Total Debt'),
                        "cash": safe_get('Cash And Cash Equivalents'),
                        "current_assets": ca,
                        "current_liabilities": safe_get('Current Liabilities'),
                        "non_current_assets": (ta-ca) if ta and ca else None,
                    })
        except:
            pass

        total_assets = balance_sheet_years[0].get('total_assets') if balance_sheet_years else None
        total_debt = balance_sheet_years[0].get('total_debt') if balance_sheet_years else None
        cash = balance_sheet_years[0].get('cash') if balance_sheet_years else None
        total_liabilities = balance_sheet_years[0].get('total_liabilities') if balance_sheet_years else None
        total_equity = balance_sheet_years[0].get('total_equity') if balance_sheet_years else None
        current_assets = balance_sheet_years[0].get('current_assets') if balance_sheet_years else None
        current_liabilities = balance_sheet_years[0].get('current_liabilities') if balance_sheet_years else None

        # Get financials
        try:
            fin = t.financials
            revenue = float(fin.loc['Total Revenue'].iloc[0]) if 'Total Revenue' in fin.index else None
            gross_profit = float(fin.loc['Gross Profit'].iloc[0]) if 'Gross Profit' in fin.index else None
            operating_income = float(fin.loc['Operating Income'].iloc[0]) if 'Operating Income' in fin.index else None
            net_income = float(fin.loc['Net Income'].iloc[0]) if 'Net Income' in fin.index else None
            cost_of_revenue = float(fin.loc['Cost Of Revenue'].iloc[0]) if 'Cost Of Revenue' in fin.index else None
            operating_expenses = float(fin.loc['Operating Expense'].iloc[0]) if 'Operating Expense' in fin.index else None
            ebitda = info.get('ebitda')
        except:
            revenue = gross_profit = operating_income = net_income = cost_of_revenue = operating_expenses = ebitda = None

        # Get major holders
        insider_pct = None
        institution_pct = None
        public_float_pct = None
        try:
            mh = t.major_holders
            if mh is not None and not mh.empty:
                for _, row in mh.iterrows():
                    val_str = str(row.iloc[0]).replace('%','').strip()
                    label = str(row.iloc[1]).lower()
                    try:
                        val = float(val_str) / 100
                        if 'insider' in label:
                            insider_pct = val
                        elif 'institution' in label:
                            institution_pct = val
                        elif 'float' in label:
                            public_float_pct = val
                    except:
                        pass
        except:
            pass

        # Get institutional holders with shares
        top_holders = []
        try:
            ih = t.institutional_holders
            if ih is not None and not ih.empty:
                for _, row in ih.head(10).iterrows():
                    top_holders.append({
                        "name": str(row.get('Holder', row.iloc[0])),
                        "pct_held": float(row.get('% Out', row.iloc[3])) if len(row) > 3 else None,
                        "shares": int(row.get('Shares', row.iloc[1])) if len(row) > 1 else None
                    })
        except:
            pass

        # Get CEO from company officers
        ceo = None
        try:
            officers = info.get('companyOfficers', [])
            for officer in officers:
                if 'CEO' in officer.get('title', '') or 'Chief Executive' in officer.get('title', ''):
                    ceo = officer.get('name')
                    break
        except:
            pass

        return {
            "ticker": ticker.upper(),
            "company_name": info.get('longName'),
            "description": info.get('longBusinessSummary'),
            "sector": info.get('sector'),
            "industry": info.get('industry'),
            "country": info.get('country'),
            "city": info.get('city'),
            "website": info.get('website'),
            "employees": info.get('fullTimeEmployees'),
            "founded": info.get('founded'),
            "ceo": ceo,
            "exchange": info.get('exchange'),
            "shares_outstanding": info.get('sharesOutstanding'),
            "float_shares": info.get('floatShares'),
            "revenue": revenue or info.get('totalRevenue'),
            "gross_profit": gross_profit,
            "operating_income": operating_income,
            "net_income": net_income or info.get('netIncomeToCommon'),
            "cost_of_revenue": cost_of_revenue,
            "operating_expenses": operating_expenses,
            "ebitda": ebitda,
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "total_debt": total_debt,
            "cash": cash or info.get('totalCash'),
            "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "debt_to_equity": info.get('debtToEquity'),
            "current_ratio": info.get('currentRatio'),
            "profit_margin": info.get('profitMargins'),
            "gross_margin": info.get('grossMargins'),
            "insider_pct": insider_pct or info.get('heldPercentInsiders'),
            "institution_pct": institution_pct or info.get('heldPercentInstitutions'),
            "public_float_pct": public_float_pct,
            "top_holders": top_holders,
            "balance_sheet_years": balance_sheet_years
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/stocks/batch")
def get_stocks_batch(tickers: str = ""):
    if not tickers:
        return []
    ticker_list = [t.strip().upper() for t in tickers.split(',') if t.strip()]
    if not ticker_list:
        return []
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        placeholders = ','.join(['%s'] * len(ticker_list))
        sql = f"SELECT ticker, company_name, current_price, price_change_pct, market_cap FROM stocks_live WHERE ticker IN ({placeholders})"
        print(f"DEBUG batch SQL: {sql}")
        print(f"DEBUG batch params: {ticker_list}")
        cursor.execute(sql, ticker_list)
        rows = cursor.fetchall()
        print(f"DEBUG batch rows: {len(rows)}")
        cursor.close()
        conn.close()
        result = []
        for row in rows:
            r = {}
            for k, v in row.items():
                if v is None:
                    r[k] = None
                elif isinstance(v, (int, float)):
                    r[k] = float(v)
                elif hasattr(v, 'isoformat'):
                    r[k] = str(v)
                else:
                    r[k] = v
            result.append(r)
        print(f"DEBUG batch result: {result[:2]}")
        return result
    except Exception as e:
        print(f"Batch error: {e}")
        return []

@app.get("/api/stocks/search")
def search_stocks(q: str = "", limit: int = 10):
    """Search stocks by ticker or company name for autocomplete."""
    if not q:
        return []
    try:
        results = query("""
            SELECT ticker, company_name, sector, current_price, price_change_pct
            FROM stocks_live
            WHERE (ticker LIKE %s OR LOWER(company_name) LIKE LOWER(%s))
            AND current_price IS NOT NULL
            ORDER BY
                CASE WHEN ticker LIKE %s THEN 0 ELSE 1 END,
                market_cap DESC
            LIMIT %s
        """, (f"{q.upper()}%", f"%{q}%", f"{q.upper()}%", limit))
        return results or []
    except:
        return []

@app.get("/api/market/indices")
def get_indices():
    # Try DB first
    db_result = query("""SELECT ticker,company_name,current_price,price_change,price_change_pct,last_updated
        FROM stocks_live WHERE ticker IN ('SPY','QQQ','DIA','IWM') AND current_price IS NOT NULL""")
    
    if db_result and len(db_result) >= 4:
        return db_result
    
    # Fallback: fetch live from yfinance
    indices = {
        'SPY': 'S&P 500 ETF',
        'QQQ': 'NASDAQ 100 ETF', 
        'DIA': 'Dow Jones ETF',
        'IWM': 'Russell 2000 ETF'
    }
    result = []
    for ticker, name in indices.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if hist is not None and not hist.empty:
                price = float(hist['Close'].iloc[-1])
                prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else price
                change = price - prev
                change_pct = (change/prev*100) if prev > 0 else 0
                result.append({
                    "ticker": ticker,
                    "company_name": name,
                    "current_price": round(price, 2),
                    "price_change": round(change, 2),
                    "price_change_pct": round(change_pct, 2),
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
        except:
            pass
    return result or db_result or []

@app.get("/api/market/currencies")
def get_currencies():
    """Fetch live currency exchange rates."""
    pairs = {
        "USD/GBP": "GBPUSD=X",  # invert rate
        "EUR/USD": "EURUSD=X",
        "USD/INR": "USDINR=X",
        "GBP/INR": "GBPINR=X",
        "USD/JPY": "USDJPY=X",
        "EUR/GBP": "EURGBP=X",
    }
    result = []
    for name, ticker in pairs.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if hist is not None and not hist.empty:
                latest = float(hist['Close'].iloc[-1])
                prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else latest
                # Invert rate for USD/GBP (GBPUSD=X gives GBP per USD, we want USD per GBP)
                if name == "USD/GBP":
                    latest = round(1/latest, 4) if latest else latest
                    prev = round(1/prev, 4) if prev else prev
                change_pct = ((latest - prev) / prev * 100) if prev > 0 else 0
                result.append({"pair": name, "ticker": ticker, "rate": round(latest, 4), "change_pct": round(change_pct, 4)})
        except:
            result.append({"pair": name, "ticker": ticker, "rate": None, "change_pct": None})
    return result

@app.get("/api/market/commodities")
def get_commodities():
    """Fetch live commodity prices — 100 commodities across all categories."""
    commodities = [
        # ── PRECIOUS METALS ──
        {"ticker": "GC=F",  "name": "Gold",              "category": "precious_metals"},
        {"ticker": "SI=F",  "name": "Silver",             "category": "precious_metals"},
        {"ticker": "PL=F",  "name": "Platinum",           "category": "precious_metals"},
        {"ticker": "PA=F",  "name": "Palladium",          "category": "precious_metals"},
        {"ticker": "MGC=F", "name": "Micro Gold",         "category": "precious_metals"},
        {"ticker": "SIL=F", "name": "Micro Silver",       "category": "precious_metals"},
        # ── INDUSTRIAL METALS ──
        {"ticker": "HG=F",  "name": "Copper",             "category": "metals"},
        {"ticker": "ALI=F", "name": "Aluminium",          "category": "metals"},
        {"ticker": "ZN=F",  "name": "Zinc",               "category": "metals"},
        {"ticker": "PB=F",  "name": "Lead",               "category": "metals"},
        {"ticker": "NI=F",  "name": "Nickel",             "category": "metals"},
        {"ticker": "TIN=F", "name": "Tin",                "category": "metals"},
        {"ticker": "STEEL", "name": "Steel",              "category": "metals"},
        {"ticker": "IRN=F", "name": "Iron Ore",           "category": "metals"},
        # ── ENERGY ──
        {"ticker": "CL=F",  "name": "Crude Oil (WTI)",    "category": "energy"},
        {"ticker": "BZ=F",  "name": "Brent Crude",        "category": "energy"},
        {"ticker": "NG=F",  "name": "Natural Gas",        "category": "energy"},
        {"ticker": "RB=F",  "name": "Gasoline (RBOB)",    "category": "energy"},
        {"ticker": "HO=F",  "name": "Heating Oil",        "category": "energy"},
        {"ticker": "QM=F",  "name": "E-mini Crude Oil",   "category": "energy"},
        {"ticker": "NG=F",  "name": "Henry Hub Gas",      "category": "energy"},
        {"ticker": "TTF=F", "name": "EU Natural Gas",     "category": "energy"},
        {"ticker": "XRB=F", "name": "RBOB Gasoline",      "category": "energy"},
        # ── AGRICULTURE — GRAINS ──
        {"ticker": "ZC=F",  "name": "Corn",               "category": "agriculture"},
        {"ticker": "ZW=F",  "name": "Wheat",              "category": "agriculture"},
        {"ticker": "ZS=F",  "name": "Soybeans",           "category": "agriculture"},
        {"ticker": "ZM=F",  "name": "Soybean Meal",       "category": "agriculture"},
        {"ticker": "ZL=F",  "name": "Soybean Oil",        "category": "agriculture"},
        {"ticker": "ZR=F",  "name": "Rough Rice",         "category": "agriculture"},
        {"ticker": "ZO=F",  "name": "Oats",               "category": "agriculture"},
        {"ticker": "KE=F",  "name": "Hard Red Wheat",     "category": "agriculture"},
        {"ticker": "MWE=F", "name": "Spring Wheat",       "category": "agriculture"},
        # ── AGRICULTURE — SOFT COMMODITIES ──
        {"ticker": "KC=F",  "name": "Coffee (Arabica)",   "category": "softs"},
        {"ticker": "SB=F",  "name": "Sugar No. 11",       "category": "softs"},
        {"ticker": "CT=F",  "name": "Cotton",             "category": "softs"},
        {"ticker": "CC=F",  "name": "Cocoa",              "category": "softs"},
        {"ticker": "OJ=F",  "name": "Orange Juice",       "category": "softs"},
        {"ticker": "LBS=F", "name": "Lumber",             "category": "softs"},
        {"ticker": "RC=F",  "name": "Coffee (Robusta)",   "category": "softs"},
        {"ticker": "RS=F",  "name": "Canola",             "category": "softs"},
        # ── LIVESTOCK ──
        {"ticker": "LE=F",  "name": "Live Cattle",        "category": "livestock"},
        {"ticker": "GF=F",  "name": "Feeder Cattle",      "category": "livestock"},
        {"ticker": "HE=F",  "name": "Lean Hogs",          "category": "livestock"},
        {"ticker": "DA=F",  "name": "Class III Milk",     "category": "livestock"},
        # ── CRYPTO ──
        {"ticker": "BTC=F", "name": "Bitcoin Futures",    "category": "crypto"},
        {"ticker": "ETH=F", "name": "Ethereum Futures",   "category": "crypto"},
        {"ticker": "MBT=F", "name": "Micro Bitcoin",      "category": "crypto"},
        {"ticker": "MET=F", "name": "Micro Ether",        "category": "crypto"},
        # ── ENERGY — CLEAN/RENEWABLES (ETF proxies) ──
        {"ticker": "UNG",   "name": "Natural Gas ETF",    "category": "clean_energy"},
        {"ticker": "URA",   "name": "Uranium ETF",        "category": "clean_energy"},
        {"ticker": "ICLN",  "name": "Clean Energy ETF",   "category": "clean_energy"},
        {"ticker": "FSLR",  "name": "Solar (First Solar)","category": "clean_energy"},
        {"ticker": "PLUG",  "name": "Hydrogen (Plug Power)","category": "clean_energy"},
        {"ticker": "LIT",   "name": "Lithium ETF",        "category": "clean_energy"},
        {"ticker": "REMX",  "name": "Rare Earth ETF",     "category": "clean_energy"},
        # ── INDICES (commodity related) ──
        {"ticker": "DJP",   "name": "Bloomberg Commodity","category": "indices"},
        {"ticker": "GSG",   "name": "S&P GSCI Commodity", "category": "indices"},
        {"ticker": "PDBC",  "name": "Commodity Index ETF","category": "indices"},
        {"ticker": "DBC",   "name": "DB Commodity ETF",   "category": "indices"},
        {"ticker": "COMT",  "name": "iShares Commodity",  "category": "indices"},
        # ── AGRICULTURE — FERTILIZERS ──
        {"ticker": "MOS",   "name": "Mosaic (Fertilizer)","category": "agriculture"},
        {"ticker": "NTR",   "name": "Nutrien (Potash)",   "category": "agriculture"},
        {"ticker": "CF",    "name": "CF Industries (N)",  "category": "agriculture"},
        {"ticker": "IPI",   "name": "Intrepid Potash",    "category": "agriculture"},
        # ── WATER ──
        {"ticker": "PHO",   "name": "Water ETF",          "category": "water"},
        {"ticker": "AWK",   "name": "American Water Works","category": "water"},
        {"ticker": "XYL",   "name": "Xylem (Water Tech)", "category": "water"},
        {"ticker": "WTRG",  "name": "Essential Utilities","category": "water"},
        {"ticker": "WM",    "name": "Waste Management",   "category": "water"},
        # ── TIMBER & PAPER ──
        {"ticker": "LBS=F", "name": "Lumber Futures",     "category": "timber"},
        {"ticker": "WOOD",  "name": "Timber ETF",         "category": "timber"},
        {"ticker": "PCH",   "name": "PotlatchDeltic",     "category": "timber"},
        {"ticker": "RYN",   "name": "Rayonier (Timber)",  "category": "timber"},
        {"ticker": "WY",    "name": "Weyerhaeuser",        "category": "timber"},
        # ── SHIPPING & FREIGHT ──
        {"ticker": "BDRY",  "name": "Dry Bulk Shipping",  "category": "shipping"},
        {"ticker": "ZIM",   "name": "ZIM Shipping",        "category": "shipping"},
        {"ticker": "SBLK",  "name": "Star Bulk Carriers", "category": "shipping"},
        {"ticker": "GOGL",  "name": "Golden Ocean Group", "category": "shipping"},
        {"ticker": "DHT",   "name": "DHT Holdings",        "category": "shipping"},
        # ── CARBON & SUSTAINABILITY ──
        {"ticker": "KRBN",  "name": "Carbon Credits ETF", "category": "carbon"},
        {"ticker": "KCCA",  "name": "California Carbon",  "category": "carbon"},
        {"ticker": "NETZ",  "name": "Net Zero ETF",        "category": "carbon"},
        {"ticker": "SMOG",  "name": "Clean Cars ETF",      "category": "carbon"},
        # ── PHYSICAL AI & ROBOTICS ──
        {"ticker": "BOTZ",  "name": "Robotics & AI ETF",  "category": "physical_ai"},
        {"ticker": "ROBO",  "name": "Robo Global ETF",    "category": "physical_ai"},
        {"ticker": "IRBO",  "name": "iShares Robotics",   "category": "physical_ai"},
        {"ticker": "ARKQ",  "name": "ARK Autonomous ETF", "category": "physical_ai"},
        {"ticker": "ISRG",  "name": "Intuitive Surgical", "category": "physical_ai"},
        {"ticker": "ABB",   "name": "ABB Robotics",        "category": "physical_ai"},
        {"ticker": "TER",   "name": "Teradyne (Robots)",  "category": "physical_ai"},
        {"ticker": "CGNX",  "name": "Cognex (Machine Vision)","category": "physical_ai"},
        # ── FOOD & BEVERAGE ──
        {"ticker": "CORN",  "name": "Corn ETF",            "category": "food"},
        {"ticker": "WEAT",  "name": "Wheat ETF",           "category": "food"},
        {"ticker": "SOYB",  "name": "Soybean ETF",         "category": "food"},
        {"ticker": "CANE",  "name": "Sugar ETF",           "category": "food"},
        {"ticker": "JO",    "name": "Coffee ETF",          "category": "food"},
        {"ticker": "NIB",   "name": "Cocoa ETF",           "category": "food"},
        {"ticker": "BAL",   "name": "Cotton ETF",          "category": "food"},
    ]
    result = []
    for com in commodities:
        try:
            t = yf.Ticker(com["ticker"])
            hist = t.history(period="2d")
            if hist is not None and not hist.empty:
                latest = float(hist['Close'].iloc[-1])
                prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else latest
                change = latest - prev
                change_pct = (change / prev * 100) if prev > 0 else 0
                result.append({
                    "ticker": com["ticker"],
                    "name": com["name"],
                    "category": com["category"],
                    "price": round(latest, 2),
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                    "updated": datetime.now().strftime("%H:%M")
                })
        except:
            result.append({
                "ticker": com["ticker"],
                "name": com["name"],
                "category": com["category"],
                "price": None,
                "change": None,
                "change_pct": None,
                "updated": "--"
            })
    return result

@app.get("/api/market/macro")
def get_macro():
    """Fetch macro economic indicators."""
    indicators = [
        {"ticker": "^VIX", "name": "VIX Fear Index"},
        {"ticker": "DX-Y.NYB", "name": "Dollar Index"},
        {"ticker": "^TNX", "name": "10Y Treasury Yield"},
        {"ticker": "GC=F", "name": "Gold"},
    ]
    result = []
    for ind in indicators:
        try:
            t = yf.Ticker(ind["ticker"])
            hist = t.history(period="2d")
            if hist is not None and not hist.empty:
                latest = float(hist['Close'].iloc[-1])
                prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else latest
                change_pct = ((latest - prev) / prev * 100) if prev > 0 else 0
                result.append({
                    "ticker": ind["ticker"],
                    "name": ind["name"],
                    "price": round(latest, 2),
                    "change_pct": round(change_pct, 2)
                })
        except:
            result.append({"ticker": ind["ticker"], "name": ind["name"], "price": None, "change_pct": None})
    return result

@app.get("/api/market/sentiment")
def get_sentiment():
    r = query("""
        SELECT ROUND(AVG(t.rsi_14),2) as avg_rsi, ROUND(AVG(l.price_change_pct),2) as avg_change,
               SUM(CASE WHEN l.price_change_pct>0 THEN 1 ELSE 0 END) as gainers,
               SUM(CASE WHEN l.price_change_pct<0 THEN 1 ELSE 0 END) as losers,
               COUNT(*) as total
        FROM stocks_live l LEFT JOIN stocks_technicals t ON l.ticker=t.ticker
        WHERE l.price_change_pct IS NOT NULL AND l.current_price IS NOT NULL
        AND (l.sector!='Indices' OR l.sector IS NULL)
    """)
    if not r or not r[0]: return {"score":50,"label":"Neutral","avg_rsi":None,"avg_change":None}
    d = r[0]
    def f(v):
        try: return float(v) if v is not None else None
        except: return None
    avg_rsi=f(d["avg_rsi"]); avg_change=f(d["avg_change"])
    gainers=f(d["gainers"]) or 0; total=f(d["total"]) or 1
    score=50.0
    if avg_rsi: score+=(avg_rsi-50)*0.30
    if total and gainers: score+=((gainers/total)*100-50)*0.40
    if avg_change: score+=min(max(avg_change*5,-15),15)
    score=round(max(0,min(100,score)),1)
    if score>=75: label="Extreme Greed"
    elif score>=60: label="Greed"
    elif score>=45: label="Neutral"
    elif score>=30: label="Fear"
    else: label="Extreme Fear"
    return {"score":score,"label":label,"avg_rsi":avg_rsi,"avg_change":avg_change,
            "gainers":int(gainers),"losers":int(f(d["losers"]) or 0),"total":int(total)}

@app.get("/api/market/pe-vs-growth")
def get_pe():
    return query("""
        SELECT l.ticker,l.company_name,l.sector,f.pe_ratio,l.market_cap,l.price_change_pct,a.analyst_rating
        FROM stocks_fundamentals f JOIN stocks_live l ON f.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON f.ticker=a.ticker
        WHERE f.pe_ratio IS NOT NULL AND f.pe_ratio>0 AND f.pe_ratio<200
        AND l.market_cap>1000000000 AND l.current_price IS NOT NULL
        AND (l.sector!='Indices' OR l.sector IS NULL)
        ORDER BY l.market_cap DESC LIMIT 300
    """)

@app.get("/api/market/rsi-distribution")
def get_rsi():
    return query("""
        SELECT CASE
            WHEN rsi_14<20 THEN 'Extremely Oversold (<20)'
            WHEN rsi_14<30 THEN 'Oversold (20-30)'
            WHEN rsi_14<40 THEN 'Below Neutral (30-40)'
            WHEN rsi_14<50 THEN 'Slightly Below (40-50)'
            WHEN rsi_14<60 THEN 'Slightly Above (50-60)'
            WHEN rsi_14<70 THEN 'Above Neutral (60-70)'
            WHEN rsi_14<80 THEN 'Overbought (70-80)'
            ELSE 'Extreme Overbought (>80)' END as rsi_zone,
            COUNT(*) as stock_count
        FROM stocks_technicals WHERE rsi_14 IS NOT NULL
        GROUP BY rsi_zone ORDER BY MIN(rsi_14)
    """)

@app.get("/api/market/volume-by-sector")
def get_vol_sec():
    return query("""SELECT sector,SUM(volume) as total_volume,COUNT(*) as stock_count
        FROM stocks_live WHERE volume IS NOT NULL AND sector IS NOT NULL AND sector!='Indices'
        AND current_price IS NOT NULL GROUP BY sector ORDER BY total_volume DESC""")

@app.get("/api/market/most-volatile")
def get_volatile(limit: int=12):
    return query("""
        SELECT l.ticker,l.company_name,l.sector,l.current_price,l.price_change_pct,t.volatility,t.beta,a.analyst_rating
        FROM stocks_technicals t JOIN stocks_live l ON t.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON t.ticker=a.ticker
        WHERE t.volatility IS NOT NULL AND l.current_price IS NOT NULL
        AND (l.sector!='Indices' OR l.sector IS NULL) ORDER BY t.volatility DESC LIMIT %s
    """, [limit])

@app.get("/api/market/dividend-leaders")
def get_divs(limit: int=12):
    return query("""
        SELECT l.ticker,l.company_name,l.sector,l.current_price,l.price_change_pct,d.dividend_yield,d.dividend_rate,a.analyst_rating
        FROM stocks_dividends d JOIN stocks_live l ON d.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON d.ticker=a.ticker
        WHERE d.dividend_yield IS NOT NULL AND d.dividend_yield>0 AND d.dividend_yield<50
        AND l.current_price IS NOT NULL AND (l.sector!='Indices' OR l.sector IS NULL)
        ORDER BY d.dividend_yield DESC LIMIT %s
    """, [limit])

@app.get("/api/market/52week-proximity")
def get_52w(limit: int=15):
    return query("""
        SELECT ticker,company_name,sector,current_price,week_52_high,week_52_low,
               ROUND((week_52_high-current_price)/NULLIF(week_52_high,0)*100,1) as distance_from_high_pct,
               price_change_pct
        FROM stocks_live WHERE week_52_high IS NOT NULL AND week_52_low IS NOT NULL
        AND current_price IS NOT NULL AND market_cap>5000000000
        AND (sector!='Indices' OR sector IS NULL) ORDER BY market_cap DESC LIMIT %s
    """, [limit])

@app.get("/api/market/breadth")
def get_breadth():
    return query("""
        SELECT l.sector,COUNT(*) as total,
               SUM(CASE WHEN l.current_price>t.ma_50 THEN 1 ELSE 0 END) as above_ma50,
               SUM(CASE WHEN l.current_price<=t.ma_50 THEN 1 ELSE 0 END) as below_ma50
        FROM stocks_live l JOIN stocks_technicals t ON l.ticker=t.ticker
        WHERE t.ma_50 IS NOT NULL AND l.current_price IS NOT NULL
        AND l.sector IS NOT NULL AND l.sector!='Indices'
        GROUP BY l.sector ORDER BY above_ma50 DESC
    """)

@app.get("/api/market/volume-spikes")
def get_vspikes(limit: int=12):
    return query("""
        SELECT ticker,company_name,sector,current_price,price_change_pct,
               volume,avg_volume,ROUND(volume/NULLIF(avg_volume,0),2) as volume_ratio
        FROM stocks_live WHERE volume IS NOT NULL AND avg_volume IS NOT NULL AND avg_volume>0
        AND current_price IS NOT NULL AND (sector!='Indices' OR sector IS NULL)
        AND volume>avg_volume*1.5 ORDER BY volume_ratio DESC LIMIT %s
    """, [limit])

# ══════════════════════════════════════════════════════
# ANALYST
# ══════════════════════════════════════════════════════
@app.get("/api/analyst/consensus")
def get_consensus(sector: Optional[str]=None, rating: Optional[str]=None, limit: int=1000):
    sql = """
        SELECT l.ticker,l.company_name,l.sector,l.industry,l.current_price,l.price_change_pct,
               l.market_cap,l.week_52_high,l.week_52_low,
               a.analyst_rating,a.target_price,a.num_analysts,a.upside_pct,
               t.rsi_14,t.beta,t.volatility,t.ma_50,t.ma_200,t.macd,
               f.pe_ratio,f.eps,f.profit_margin,f.roe,d.dividend_yield
        FROM stocks_analyst a JOIN stocks_live l ON a.ticker=l.ticker
        LEFT JOIN stocks_technicals t ON a.ticker=t.ticker
        LEFT JOIN stocks_fundamentals f ON a.ticker=f.ticker
        LEFT JOIN stocks_dividends d ON a.ticker=d.ticker
        WHERE a.analyst_rating IS NOT NULL AND a.num_analysts IS NOT NULL AND a.num_analysts>0
        AND l.current_price IS NOT NULL AND a.target_price IS NOT NULL
        AND (l.sector!='Indices' OR l.sector IS NULL)
    """
    params=[]
    if sector: sql+=" AND l.sector=%s"; params.append(sector)
    if rating: sql+=" AND a.analyst_rating=%s"; params.append(rating)
    sql+=" ORDER BY a.num_analysts DESC,l.market_cap DESC LIMIT %s"; params.append(limit)
    return query(sql,params)

@app.get("/api/analyst/sectors")
def get_analyst_sectors():
    return query("""
        SELECT l.sector,COUNT(*) as total_covered,
               SUM(CASE WHEN a.analyst_rating IN ('strong_buy','buy') THEN 1 ELSE 0 END) as buy_count,
               SUM(CASE WHEN a.analyst_rating='hold' THEN 1 ELSE 0 END) as hold_count,
               SUM(CASE WHEN a.analyst_rating IN ('sell','strong_sell') THEN 1 ELSE 0 END) as sell_count,
               ROUND(AVG(a.upside_pct),1) as avg_upside_pct
        FROM stocks_analyst a JOIN stocks_live l ON a.ticker=l.ticker
        WHERE a.analyst_rating IS NOT NULL AND a.num_analysts>0 AND l.current_price IS NOT NULL
        AND (l.sector!='Indices' OR l.sector IS NULL)
        GROUP BY l.sector ORDER BY avg_upside_pct DESC
    """)

# ══════════════════════════════════════════════════════
# PREDICTIONS
# ══════════════════════════════════════════════════════
@app.get("/api/ai-predictions")
def get_predictions(sector: Optional[str]=None, limit: int=1000):
    sql = """
        SELECT p.ticker,p.buy_signal,p.trend,p.momentum_score,p.risk_level,
               p.confidence_pct,p.model_used,p.target_1w,p.target_1m,p.target_3m,
               p.prob_up_1w,p.prob_up_1m,p.prob_up_3m,p.upper_1m,p.lower_1m,p.last_updated,
               p.run_date,p.actual_1w,p.actual_1w_date,p.direction_correct_1w,
               l.company_name,l.sector,l.current_price,l.price_change_pct,l.market_cap,
               a.analyst_rating,a.target_price,a.upside_pct
        FROM stocks_predictions p
        JOIN stocks_live l ON p.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON p.ticker=a.ticker
        WHERE l.current_price IS NOT NULL AND p.target_1m IS NOT NULL
        AND (l.sector!='Indices' OR l.sector IS NULL)
    """
    params=[]
    if sector: sql+=" AND l.sector=%s"; params.append(sector)
    sql+=" ORDER BY p.confidence_pct DESC,l.market_cap DESC LIMIT %s"; params.append(limit)
    return query(sql,params)

@app.get("/api/ai-predictions/{ticker}")
def get_prediction(ticker: str):
    r = query("""
        SELECT p.*,l.company_name,l.sector,l.current_price as live_price,l.price_change_pct,l.market_cap,
               a.analyst_rating,a.target_price as analyst_target,a.num_analysts,a.upside_pct
        FROM stocks_predictions p
        JOIN stocks_live l ON p.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON p.ticker=a.ticker
        WHERE p.ticker=%s
    """, [ticker.upper()])
    if not r: return {}
    d = r[0]
    for field in ['key_factors','price_history','predicted_path']:
        if d.get(field) and isinstance(d[field],str):
            try: d[field]=json.loads(d[field])
            except: d[field]=[]
    return d

# ══════════════════════════════════════════════════════
# EARNINGS
# ══════════════════════════════════════════════════════
@app.get("/api/earnings/calendar")
def get_earnings(limit: int=500):
    return query("""
        SELECT e.ticker,l.company_name,l.sector,e.next_earnings_date,
               e.eps_estimate,e.eps_actual,e.earnings_surprise_pct,
               l.current_price,l.price_change_pct,l.market_cap
        FROM stocks_earnings e JOIN stocks_live l ON e.ticker=l.ticker
        WHERE l.current_price IS NOT NULL
        AND (l.sector!='Indices' OR l.sector IS NULL)
        AND (e.next_earnings_date IS NOT NULL OR e.eps_actual IS NOT NULL)
        ORDER BY e.next_earnings_date ASC,l.market_cap DESC LIMIT %s
    """, [limit])

@app.get("/api/earnings/{ticker}")
def get_stock_earnings(ticker: str):
    try:
        stock = yf.Ticker(ticker.upper())
        info  = stock.info
        hist_data=[]
        try:
            eh = stock.earnings_history
            if eh is not None and not eh.empty:
                for _, row in eh.iterrows():
                    ea=float(row.get("epsActual",0) or 0)
                    ee=float(row.get("epsEstimate",0) or 0)
                    surprise=round(((ea-ee)/abs(ee))*100,2) if ee!=0 else None
                    hist_data.append({"date":str(row.name)[:10],"eps_estimate":round(ee,4),"eps_actual":round(ea,4),"beat":ea>ee,"surprise_pct":surprise})
        except: pass
        next_ts=info.get("earningsTimestamp") or info.get("earningsCallTimestampStart")
        next_date=datetime.fromtimestamp(next_ts).strftime("%Y-%m-%d") if next_ts else None
        return {"ticker":ticker.upper(),"next_earnings_date":next_date,"eps_estimate":info.get("forwardEps"),"eps_actual":info.get("trailingEps"),"history":hist_data}
    except: return {"ticker":ticker.upper(),"next_earnings_date":None,"history":[]}

# ══════════════════════════════════════════════════════
# NEWS
# ══════════════════════════════════════════════════════
def _fetch_news(ticker, count=10):
    try:
        import urllib.request
        import xml.etree.ElementTree as ET
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        articles = []
        for item in root.findall('.//item')[:count]:
            title = item.findtext('title','').strip()
            link = item.findtext('link','').strip()
            pub = item.findtext('pubDate','')
            source = item.findtext('source', 'Yahoo Finance')
            pub_ts = None
            if pub:
                try:
                    from email.utils import parsedate_to_datetime
                    pub_ts = int(parsedate_to_datetime(pub).timestamp())
                except: pass
            if title:
                articles.append({'title':title,'source':source,'url':link,'published':pub_ts,'ticker':ticker})
        return articles
    except: return []

@app.get("/api/news/market")
def get_market_news():
    all_news=[]; seen=set()
    for sym in ["SPY","AAPL","MSFT","NVDA","GOOGL","AMZN","TSLA","JPM","META"]:
        for a in _fetch_news(sym,5):
            if a['title'] not in seen:
                seen.add(a['title']); all_news.append(a)
    all_news.sort(key=lambda x: x.get('published') or 0, reverse=True)
    return all_news[:15]

@app.get("/api/news/{ticker}")
def get_stock_news(ticker: str):
    return _fetch_news(ticker.upper(), 10)

# ══════════════════════════════════════════════════════
# PORTFOLIO
# ══════════════════════════════════════════════════════
@app.get("/api/portfolio")
def get_portfolio():
    return query("""
        SELECT p.*,l.current_price,l.price_change_pct as day_change_pct,l.sector,
               ROUND((l.current_price-p.avg_buy_price)*p.quantity,2) as unrealized_pnl,
               ROUND(((l.current_price-p.avg_buy_price)/p.avg_buy_price)*100,2) as pnl_pct,
               ROUND(l.current_price*p.quantity,2) as current_value,
               ROUND(p.avg_buy_price*p.quantity,2) as invested_value,
               a.analyst_rating,a.target_price,a.upside_pct
        FROM portfolio p LEFT JOIN stocks_live l ON p.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON p.ticker=a.ticker ORDER BY current_value DESC
    """)

@app.post("/api/portfolio/add")
def add_portfolio(ticker: str, quantity: float, avg_buy_price: float, buy_date: str, notes: str=""):
    conn=get_db(); cursor=conn.cursor(dictionary=True)
    cursor.execute("SELECT company_name,sector FROM stocks_live WHERE ticker=%s",[ticker.upper()])
    s=cursor.fetchone()
    cursor.execute("INSERT INTO portfolio (ticker,company_name,quantity,avg_buy_price,buy_date,sector,notes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (ticker.upper(),s["company_name"] if s else ticker,quantity,avg_buy_price,buy_date,s["sector"] if s else "Unknown",notes))
    conn.commit(); cursor.close(); conn.close()
    return {"message":f"✅ {ticker.upper()} added"}

@app.get("/api/portfolio/summary")
def get_portfolio_summary():
    r=query("""SELECT ROUND(SUM(l.current_price*p.quantity),2) as total_value,
               ROUND(SUM(p.avg_buy_price*p.quantity),2) as total_invested,
               ROUND(SUM((l.current_price-p.avg_buy_price)*p.quantity),2) as total_pnl,
               ROUND(SUM(l.current_price*p.quantity*l.price_change_pct/100),2) as day_change,
               COUNT(*) as total_holdings
        FROM portfolio p LEFT JOIN stocks_live l ON p.ticker=l.ticker""")
    return r[0] if r else {}

# ══════════════════════════════════════════════════════
# TRANSACTIONS
# ══════════════════════════════════════════════════════
@app.get("/api/transactions")
def get_transactions():
    return query("SELECT * FROM transactions ORDER BY transaction_date DESC")

@app.post("/api/transactions/add")
def add_transaction(ticker: str, transaction_type: str, quantity: float, price: float, transaction_date: str, notes: str=""):
    conn=get_db(); cursor=conn.cursor(dictionary=True)
    cursor.execute("SELECT company_name FROM stocks_live WHERE ticker=%s",[ticker.upper()])
    s=cursor.fetchone()
    cursor.execute("INSERT INTO transactions (ticker,company_name,transaction_type,quantity,price,total_value,transaction_date,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (ticker.upper(),s["company_name"] if s else ticker,transaction_type.upper(),quantity,price,round(quantity*price,2),transaction_date,notes))
    conn.commit(); cursor.close(); conn.close()
    return {"message":f"✅ Added"}

@app.get("/api/transactions/summary")
def get_tx_summary():
    r=query("""SELECT COUNT(*) as total_trades,
               SUM(CASE WHEN transaction_type='BUY' THEN total_value ELSE 0 END) as total_invested,
               SUM(CASE WHEN transaction_type='SELL' THEN total_value ELSE 0 END) as total_sold,
               MAX(total_value) as largest_trade FROM transactions""")
    return r[0] if r else {}

# ══════════════════════════════════════════════════════
# AI AGENT ENDPOINTS
# ══════════════════════════════════════════════════════
import logging
import re
import threading
import time
from collections import defaultdict, deque
from typing import List, Literal

import groq
from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from agent import run_agent, review_portfolio, explain_single_stock, LLMUnavailable
import ipo_data
from screener import get_top_movers, get_sector_summary, screen_stocks

log = logging.getLogger("uvicorn.error")

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,10}$")


def _valid_tickers(values):
    return [t.strip() for t in values if isinstance(t, str) and TICKER_RE.match(t.strip())]


# ── Rate limit for the endpoints that spend LLM quota (per client, sliding 60s window) ──
AGENT_RATE_LIMIT = int(os.getenv("AGENT_RATE_LIMIT", "20"))
_agent_hits = defaultdict(deque)
_agent_hits_lock = threading.Lock()


def _client_id(request: Request) -> str:
    # Behind a proxy or tunnel every request comes from the proxy, so trust its forwarded header
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def agent_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"status": "error", "message": message}, status_code=status_code)


def check_rate_limit(request: Request):
    """Returns an error response when the client is over the limit, else None."""
    now = time.time()
    key = _client_id(request)
    with _agent_hits_lock:
        hits = _agent_hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= AGENT_RATE_LIMIT:
            return agent_error(429, f"You're sending messages too quickly. Please wait a moment (limit {AGENT_RATE_LIMIT} per minute).")
        hits.append(now)
        if len(_agent_hits) > 5000:  # drop clients with no recent activity
            for k in [k for k, v in _agent_hits.items() if not v or now - v[-1] > 60]:
                del _agent_hits[k]
    return None


def run_ai(call):
    """Run an AI call and turn failures into short, safe messages (details go to the server log)."""
    try:
        return call()
    except LLMUnavailable:
        return agent_error(503, "The AI assistant isn't available right now.")
    except groq.RateLimitError:
        return agent_error(429, "The AI service is busy. Please try again in a minute.")
    except groq.APIConnectionError:  # includes timeouts
        return agent_error(504, "I couldn't reach the AI service in time. Please try again.")
    except Exception:
        log.exception("AI endpoint failed")
        return agent_error(500, "Something went wrong on our side. Please try again.")


class ScreenerRequest(BaseModel):
    filters: dict = {}
    limit: int = Field(default=50, ge=1, le=200)


# Plain `def` endpoints run in FastAPI's thread pool, so slow LLM / database calls
# do not freeze every other request the way they did inside `async def`.
@app.post("/api/screener")
def run_screener(request: ScreenerRequest):
    try:
        results = screen_stocks(request.filters, limit=request.limit)
        return results or []
    except Exception:
        log.exception("Screener failed")
        return []


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class RecommendRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    portfolio_tickers: List[str] = Field(default_factory=list, max_length=30)
    chat_history: List[ChatMessage] = Field(default_factory=list, max_length=20)
    reply_to: str = Field(default="", max_length=300)

    @field_validator("portfolio_tickers")
    @classmethod
    def only_valid_tickers(cls, v):
        return _valid_tickers(v)


class PortfolioRequest(BaseModel):
    tickers: List[str] = Field(max_length=30)

    @field_validator("tickers")
    @classmethod
    def only_valid_tickers(cls, v):
        return _valid_tickers(v)


class ExplainRequest(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$")
    query: str = Field(default="", max_length=500)


@app.post("/agent/recommend")
def agent_recommend(request: Request, body: RecommendRequest):
    limited = check_rate_limit(request)
    if limited:
        return limited

    def call():
        result = run_agent(
            user_query=body.query.strip(),
            portfolio_tickers=body.portfolio_tickers,
            chat_history=[m.model_dump() for m in body.chat_history],
            reply_to=body.reply_to.strip(),
        )
        return {
            "status": "success",
            "query": body.query,
            "intent": result.get("intent"),
            "stocks_found": result["stocks_found"],
            "recommendation": result["recommendation"],
            "top_stocks": result["top_stocks"],
            "all_stocks": result["all_stocks"]
        }
    return run_ai(call)


@app.post("/agent/explain-stock")
def agent_explain_stock(request: Request, body: ExplainRequest):
    limited = check_rate_limit(request)
    if limited:
        return limited
    return run_ai(lambda: {"status": "success", "ticker": body.ticker,
                           "explanation": explain_single_stock(body.ticker, body.query)})


@app.post("/agent/portfolio-review")
def agent_portfolio_review(request: Request, body: PortfolioRequest):
    limited = check_rate_limit(request)
    if limited:
        return limited
    return run_ai(lambda: {"status": "success", "tickers_reviewed": body.tickers,
                           "review": review_portfolio(tickers=body.tickers)})

@app.get("/api/ipos")
def get_ipo_calendar(days_back: int = Query(14, ge=0, le=30), days_ahead: int = Query(60, ge=0, le=90)):
    """Upcoming and recently priced US IPOs (cached for 30 minutes)."""
    try:
        return ipo_data.get_ipos(days_back, days_ahead)
    except ipo_data.IPOUnavailable:
        return agent_error(503, "The IPO calendar is unavailable right now. Please try again shortly.")
    except Exception:
        log.exception("IPO calendar failed")
        return agent_error(500, "Something went wrong loading the IPO calendar.")


@app.get("/api/market/signals")
async def market_signals():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT l.sector,
                SUM(CASE WHEN p.buy_signal IN ('Buy','Strong Buy') THEN 1 ELSE 0 END) as buy_count,
                SUM(CASE WHEN p.buy_signal = 'Hold' THEN 1 ELSE 0 END) as hold_count,
                SUM(CASE WHEN p.buy_signal IN ('Sell','Strong Sell') THEN 1 ELSE 0 END) as sell_count
            FROM stocks_live l
            JOIN stocks_predictions p ON l.ticker = p.ticker
            WHERE l.sector IS NOT NULL
            GROUP BY l.sector
            ORDER BY buy_count DESC
            LIMIT 10
        """)
        signals = cursor.fetchall()
        conn.close()
        return {"signals": signals}
    except Exception as e:
        return {"signals": [], "error": str(e)}

@app.get("/api/predictions/accuracy")
async def prediction_accuracy():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN direction_correct_1w = 1 THEN 1 ELSE 0 END) as correct
            FROM stocks_predictions
            WHERE direction_correct_1w IS NOT NULL
        """)
        result = cursor.fetchone()
        conn.close()
        return {
            "total": result['total'] or 0,
            "correct": result['correct'] or 0
        }
    except Exception as e:
        return {"total": 0, "correct": 0, "error": str(e)}
    try:
        movers = get_top_movers(5)
        sectors = get_sector_summary()
        return {
            "status": "success",
            "top_gainers": movers["gainers"],
            "top_losers": movers["losers"],
            "sector_summary": sectors
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    print("="*60)
    print("  STOCK MARKET PRO — API Server")
    print("  http://localhost:8000")
    print("  http://localhost:8000/docs")
    print("="*60)
    uvicorn.run(app, host="0.0.0.0", port=8000)
