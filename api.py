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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
        "password": os.getenv("DB_PASSWORD", "Aroot092325"),
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

@app.get("/api/market/indices")
def get_indices():
    return query("""SELECT ticker,company_name,current_price,price_change,price_change_pct,last_updated
        FROM stocks_live WHERE ticker IN ('SPY','QQQ','DIA','IWM') AND current_price IS NOT NULL""")

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
        WHERE d.dividend_yield IS NOT NULL AND d.dividend_yield>0 AND d.dividend_yield<0.5
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
        raw = yf.Ticker(ticker).get_news(count=count)
        articles = parse_news(raw)
        for a in articles: a['ticker']=ticker
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
from agent import run_agent, review_portfolio
from screener import get_top_movers, get_sector_summary
from pydantic import BaseModel
from typing import List

class RecommendRequest(BaseModel):
    query: str

class PortfolioRequest(BaseModel):
    tickers: List[str]

class ExplainRequest(BaseModel):
    ticker: str
    query: str = ""

@app.post("/agent/recommend")
async def agent_recommend(request: RecommendRequest):
    try:
        result = run_agent(user_query=request.query)
        return {
            "status": "success",
            "query": request.query,
            "intent": result.get("intent"),
            "stocks_found": result["stocks_found"],
            "recommendation": result["recommendation"],
            "top_stocks": result["top_stocks"],
            "all_stocks": result["all_stocks"]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/agent/explain-stock")
async def agent_explain_stock(request: ExplainRequest):
    try:
        from agent import explain_single_stock
        explanation = explain_single_stock(request.ticker, request.query)
        return {"status": "success", "ticker": request.ticker, "explanation": explanation}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/agent/portfolio-review")
async def agent_portfolio_review(request: PortfolioRequest):
    try:
        review = review_portfolio(tickers=request.tickers)
        return {
            "status": "success",
            "tickers_reviewed": request.tickers,
            "review": review
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/market/signals")
async def market_signals():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT l.sector,
                SUM(CASE WHEN p.buy_signal = 'BUY' THEN 1 ELSE 0 END) as buy_count,
                SUM(CASE WHEN p.buy_signal = 'HOLD' THEN 1 ELSE 0 END) as hold_count,
                SUM(CASE WHEN p.buy_signal = 'SELL' THEN 1 ELSE 0 END) as sell_count
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
