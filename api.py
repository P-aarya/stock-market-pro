# ============================================================
# STOCK MARKET PRO - api.py (Final Clean Version)
# ============================================================
from fastapi import Depends, FastAPI, Body, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
import mysql.connector
import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import Optional
import hashlib
import hmac
import os
import json
import secrets
import time
import urllib.parse
from collections import defaultdict, deque
from dotenv import load_dotenv
from html import escape as _escape_html
load_dotenv()

import auth
import auth_pages

app = FastAPI(title="Stock Market Pro API")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# ── Accounts ──
# Every page and endpoint needs a signed-in user (see auth.py). The session cookie carries the user's id; the middleware
# below puts it on request.state.user_id, and the endpoints that read or change personal data take it from there.
#   REQUIRE_APPROVAL=false  lets new accounts in at once (the default is that an administrator approves each one)
#   ALLOW_SIGNUP=false  closes registration (accounts can still be made with manage_users.py)
#   SIGNUP_CODE=...     keeps registration open only for people who know the code
#   APP_SECRET=...      the key sessions are signed with (otherwise a random one is kept in .app_secret)
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").strip().lower() not in ("false", "0", "no")
REQUIRE_APPROVAL = os.getenv("REQUIRE_APPROVAL", "true").strip().lower() not in ("false", "0", "no")
SIGNUP_CODE = os.getenv("SIGNUP_CODE", "")
SESSION_COOKIE = "smp_session"
_login_by_client = auth.RateLimiter(5, 300)     # wrong passwords per client in 5 minutes ...
_login_by_email = auth.RateLimiter(10, 900)     # ... and per account in 15 minutes
_signups = auth.RateLimiter(5, 3600)            # sign-up attempts per client in an hour
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Files a phone fetches for "Add to Home Screen" (app icon + manifest). The browser asks for them without the login
# cookie, and they hold nothing private, so they stay reachable before sign-in.
_STATIC_DIR = os.path.join(_BASE_DIR, "static")
_APP_ASSETS = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}
_OPEN_PATHS = {"/login", "/register", "/logout", "/health", "/api/auth/status", "/favicon.ico"} | set(_APP_ASSETS)


_SETUP_HINT = "The accounts tables need a database update. Run: python manage_users.py migrate"


async def _approved_user(request: Request):
    """The account behind the session cookie if it still exists and is approved, else None. Uses the short-lived cache."""
    user_id = auth.read_token(request.cookies.get(SESSION_COOKIE, ""))
    if user_id is None:
        return None
    user = await run_in_threadpool(auth.access_for, user_id)
    return user if user and user["status"] == "approved" else None


def _unavailable(request: Request, status: int, message: str):
    if request.url.path.startswith(("/api/", "/agent/")):
        return JSONResponse({"status": "error", "message": message}, status_code=status)
    return HTMLResponse(auth_pages.notice_page("Temporarily unavailable", message), status_code=status)


@app.middleware("http")
async def require_login(request: Request, call_next):
    request.state.user_id, request.state.is_admin = None, False
    if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
        return await call_next(request)
    try:
        user = await _approved_user(request)
    except auth.AccountsNotSetUp:
        return _unavailable(request, 503, _SETUP_HINT)
    except Exception:
        log.exception("Could not check access")
        return _unavailable(request, 503, "We couldn't check your access just now. Please try again in a moment.")
    if user:
        request.state.user_id, request.state.is_admin = user["id"], bool(user["is_admin"])
        return await call_next(request)
    if request.url.path.startswith(("/api/", "/agent/")):
        return JSONResponse({"status": "error", "message": "Please sign in."}, status_code=401)
    return RedirectResponse("/login", status_code=302)


def current_user_id(request: Request) -> int:
    """The signed-in user's id, for endpoints that read or change personal data (the middleware has already checked it)."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user_id


def require_admin(request: Request) -> int:
    """The signed-in administrator's id. Checked against the database each time, so a block or demotion applies at once."""
    user_id = current_user_id(request)
    user = auth.get_user(user_id)
    if not user or user["status"] != "approved" or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user_id


def require_admin_action(request: Request, admin_id: int = Depends(require_admin)) -> int:
    """Changes made by an administrator must come from the admin page's own script, which sends this header. A page on
    another site can't add it without the browser asking this server's permission first."""
    if request.headers.get("x-requested-with") != "fetch":
        raise HTTPException(status_code=403, detail="This action must be made from the admin page.")
    return admin_id


def _is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _start_session(request: Request, user_id: int) -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, auth.make_token(user_id), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=_is_https(request))
    return response


async def _read_form(request: Request) -> dict:
    body = (await request.body())[:10000]
    return {k: v[0] for k, v in urllib.parse.parse_qs(body.decode("utf-8", "ignore"), keep_blank_values=True).items()}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    cookie = request.cookies.get(SESSION_COOKIE, "")
    user_id = auth.read_token(cookie)
    if user_id is not None:
        try:
            user = auth.access_for(user_id)
            if user and user["status"] == "approved":
                return RedirectResponse("/", status_code=303)
        except Exception:
            return RedirectResponse("/", status_code=303)      # the database hiccuped: don't sign anyone out for that
    response = HTMLResponse(auth_pages.login_page(signup_open=ALLOW_SIGNUP))
    if cookie:
        response.delete_cookie(SESSION_COOKIE)                # expired, forged, blocked, or for an account that no longer exists
    return response


@app.post("/login")
async def login(request: Request):
    form = await _read_form(request)
    email, password = auth.normalize_email(form.get("email")), form.get("password", "")
    client = _client_id(request)
    if _login_by_client.blocked(client) or _login_by_email.blocked(email):
        return HTMLResponse(auth_pages.login_page("Too many failed attempts. Please wait a few minutes and try again.", email, ALLOW_SIGNUP), status_code=429)
    try:
        user = await run_in_threadpool(auth.authenticate, email, password)
    except auth.AccountsNotSetUp:
        return HTMLResponse(auth_pages.notice_page("Accounts aren't set up yet", "The site owner needs to run: python manage_users.py migrate"), status_code=503)
    if user:
        _login_by_client.clear(client)
        _login_by_email.clear(email)
        if user["status"] == "pending":
            return HTMLResponse(auth_pages.pending_page(user["email"]), status_code=403)
        if user["status"] != "approved":
            return HTMLResponse(auth_pages.blocked_page(), status_code=403)
        return _start_session(request, user["id"])
    _login_by_client.record(client)
    _login_by_email.record(email)
    return HTMLResponse(auth_pages.login_page("That email or password isn't right.", email, ALLOW_SIGNUP), status_code=401)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if not ALLOW_SIGNUP:
        return HTMLResponse(auth_pages.register_page(closed=True), status_code=403)
    return HTMLResponse(auth_pages.register_page(needs_code=bool(SIGNUP_CODE)))


@app.post("/register")
async def register(request: Request):
    if not ALLOW_SIGNUP:
        return HTMLResponse(auth_pages.register_page(closed=True), status_code=403)
    form = await _read_form(request)
    email, password = auth.normalize_email(form.get("email")), form.get("password", "")
    client = _client_id(request)

    def again(message, status):
        return HTMLResponse(auth_pages.register_page(message, email, bool(SIGNUP_CODE)), status_code=status)

    if _signups.blocked(client):
        return again("Too many sign-up attempts from your network. Please try again later.", 429)
    _signups.record(client)
    if SIGNUP_CODE and not hmac.compare_digest(form.get("code", "").strip().encode(), SIGNUP_CODE.encode()):
        return again("That invite code isn't right.", 403)
    if password != form.get("confirm", ""):
        return again("The two passwords don't match.", 400)
    try:
        user = await run_in_threadpool(auth.register, email, password, "pending" if REQUIRE_APPROVAL else "approved")
    except auth.AuthError as e:
        return again(e.message, e.status)
    except auth.AccountsNotSetUp:
        return HTMLResponse(auth_pages.notice_page("Accounts aren't set up yet", "The site owner needs to run: python manage_users.py migrate"), status_code=503)
    if user["status"] == "pending":
        return HTMLResponse(auth_pages.pending_page(user["email"]), status_code=200)      # no session until an administrator approves
    return _start_session(request, user["id"])


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/auth/status")
def auth_status(request: Request):
    user_id = auth.read_token(request.cookies.get(SESSION_COOKIE, ""))
    if user_id is None:
        return {"enabled": True, "signed_in": False, "user": None, "admin": False}
    try:
        user = auth.access_for(user_id)
    except Exception:
        return {"enabled": True, "signed_in": True, "user": None, "admin": False}   # a database hiccup shouldn't sign anyone out
    if not user or user["status"] != "approved":
        return {"enabled": True, "signed_in": False, "user": None, "admin": False}
    return {"enabled": True, "signed_in": True, "user": {"id": user["id"], "email": user["email"]}, "admin": bool(user["is_admin"])}


# ── Serve dashboard.html directly from port 8000 ──
def _dashboard_response(request: Request):
    path = os.path.join(_BASE_DIR, "dashboard.html")
    if not os.path.exists(path):
        return {"status": "running", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    with open(path, encoding="utf-8") as f:
        page = f.read()
    user_id = getattr(request.state, "user_id", None)
    meta = ""
    if user_id is not None:
        try:
            user = auth.get_user(user_id)
        except Exception:
            user = None
        meta = (f'<meta name="smp-user-id" content="{int(user_id)}">'
                f'<meta name="smp-user-email" content="{_escape_html((user or {}).get("email", ""), quote=True)}">'
                f'<meta name="smp-user-admin" content="{1 if getattr(request.state, "is_admin", False) else 0}">')
    # The page is different for every person, so it must never be stored by a browser cache or a shared proxy.
    return HTMLResponse(page.replace("<!--SMP_USER-->", meta, 1), headers={"Cache-Control": "private, no-store"})


@app.get("/")
def serve_root(request: Request):
    return _dashboard_response(request)


@app.get("/dashboard.html")
def serve_dashboard(request: Request):
    return _dashboard_response(request)


def _app_asset_route(filename: str, media_type: str):
    def serve():
        path = os.path.join(_STATIC_DIR, filename)
        if not os.path.exists(path):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
    return serve

for _route, (_file, _type) in _APP_ASSETS.items():
    app.add_api_route(_route, _app_asset_route(_file, _type), methods=["GET"], include_in_schema=False)

@app.get("/health")
def health():
    return {"status": "running", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

# -- DB --
def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("DB_PASS", ""),
        "database": os.getenv("DB_NAME", "stock_market_pro_db")
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        config["ssl_disabled"] = False
    # The web server opens many connections at once (one per request, on separate threads). The connector's C
    # extension is not safe for that: concurrent first connections fail with "2059 Authentication plugin ... is
    # already loaded" and can crash the whole process. The pure-Python connector has no such problem.
    config["use_pure"] = True
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

# -- Parse yfinance news (1.4.x format) --
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

# ======================================================
# STOCKS
# ======================================================
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
        AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
    """
    params = []
    if sector: sql += " AND l.sector=%s"; params.append(sector)
    if search:
        sql += " AND (l.ticker LIKE %s OR l.company_name LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])
    sql += " ORDER BY l.market_cap DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    return query(sql, params)

# Must be declared before /api/stocks/{ticker}, otherwise that route captures 'search'.
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
            AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
            ORDER BY
                CASE WHEN ticker LIKE %s THEN 0 ELSE 1 END,
                market_cap DESC
            LIMIT %s
        """, (f"{q.upper()}%", f"%{q}%", f"{q.upper()}%", limit))
        return results or []
    except:
        return []

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

# ======================================================
# MARKET
# ======================================================
@app.get("/api/market/summary")
def get_summary():
    r = query("""
        SELECT COUNT(*) as total_stocks,
               SUM(CASE WHEN price_change_pct>0 THEN 1 ELSE 0 END) as gainers,
               SUM(CASE WHEN price_change_pct<0 THEN 1 ELSE 0 END) as losers,
               ROUND(AVG(price_change_pct),2) as avg_change_pct,
               MAX(last_updated) as last_updated
        FROM stocks_live
        WHERE (sector!='Indices' OR sector IS NULL) AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
    """)
    return r[0] if r else {}

@app.get("/api/market/gainers")
def get_gainers(limit: int=20):
    return query("""SELECT ticker,company_name,sector,current_price,price_change,price_change_pct,volume
        FROM stocks_live WHERE price_change_pct IS NOT NULL AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        AND (sector!='Indices' OR sector IS NULL) ORDER BY price_change_pct DESC LIMIT %s""", [limit])

@app.get("/api/market/losers")
def get_losers(limit: int=20):
    return query("""SELECT ticker,company_name,sector,current_price,price_change,price_change_pct,volume
        FROM stocks_live WHERE price_change_pct IS NOT NULL AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        AND (sector!='Indices' OR sector IS NULL) ORDER BY price_change_pct ASC LIMIT %s""", [limit])

@app.get("/api/market/most-active")
def get_active(limit: int=20):
    return query("""SELECT ticker,company_name,sector,current_price,price_change_pct,volume,market_cap
        FROM stocks_live WHERE volume IS NOT NULL AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        AND (sector!='Indices' OR sector IS NULL) ORDER BY volume DESC LIMIT %s""", [limit])

@app.get("/api/market/sectors")
def get_sectors():
    return query("""
        SELECT sector, COUNT(*) as stock_count,
               ROUND(AVG(price_change_pct),2) as avg_change_pct,
               SUM(CASE WHEN price_change_pct>0 THEN 1 ELSE 0 END) as gainers,
               SUM(CASE WHEN price_change_pct<0 THEN 1 ELSE 0 END) as losers,
               ROUND(SUM(market_cap)/1e9,2) as total_market_cap_bn
        FROM stocks_live WHERE sector IS NOT NULL AND sector!='Indices' AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        GROUP BY sector ORDER BY avg_change_pct DESC
    """)

@app.post("/api/stocks/batch_commodity")
def batch_commodity_companies(request: dict = Body(...)):
    """Fetch all commodity company data in one query."""
    try:
        tickers = request.get("tickers", [])
        if not tickers:
            return []
        placeholders = ','.join(['%s'] * len(tickers))
        results = query(f"""
            SELECT l.ticker, l.company_name, l.sector, l.current_price,
                   l.price_change_pct, l.market_cap, f.market_cap_category,
                   COALESCE(p.buy_signal, 'N/A') as buy_signal,
                   COALESCE(p.trend, 'NEUTRAL') as trend,
                   COALESCE(a.analyst_rating, 'N/A') as analyst_rating
            FROM stocks_live l
            LEFT JOIN stocks_fundamentals f ON l.ticker = f.ticker
            LEFT JOIN stocks_predictions p ON l.ticker = p.ticker
            LEFT JOIN stocks_analyst a ON l.ticker = a.ticker
            WHERE l.ticker IN ({placeholders})
            AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        """, tickers)
        return results or []
    except Exception as e:
        print(f"Batch commodity error: {e}")
        return []

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

@app.get("/api/market/indices")
def get_indices():
    # Try DB first
    db_result = query("""SELECT ticker,company_name,current_price,price_change,price_change_pct,last_updated
        FROM stocks_live WHERE ticker IN ('SPY','QQQ','DIA','IWM') AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)""")
    
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
    """Fetch live commodity prices  100 commodities across all categories."""
    commodities = [
        # -- PRECIOUS METALS --
        {"ticker": "GC=F",  "name": "Gold",              "category": "precious_metals"},
        {"ticker": "SI=F",  "name": "Silver",             "category": "precious_metals"},
        {"ticker": "PL=F",  "name": "Platinum",           "category": "precious_metals"},
        {"ticker": "PA=F",  "name": "Palladium",          "category": "precious_metals"},
        {"ticker": "MGC=F", "name": "Micro Gold",         "category": "precious_metals"},
        {"ticker": "SIL=F", "name": "Micro Silver",       "category": "precious_metals"},
        # -- INDUSTRIAL METALS --
        {"ticker": "HG=F",  "name": "Copper",             "category": "metals"},
        {"ticker": "ALI=F", "name": "Aluminium",          "category": "metals"},
        {"ticker": "ZN=F",  "name": "Zinc",               "category": "metals"},
        {"ticker": "PB=F",  "name": "Lead",               "category": "metals"},
        {"ticker": "NI=F",  "name": "Nickel",             "category": "metals"},
        {"ticker": "TIN=F", "name": "Tin",                "category": "metals"},
        {"ticker": "STEEL", "name": "Steel",              "category": "metals"},
        {"ticker": "IRN=F", "name": "Iron Ore",           "category": "metals"},
        # -- ENERGY --
        {"ticker": "CL=F",  "name": "Crude Oil (WTI)",    "category": "energy"},
        {"ticker": "BZ=F",  "name": "Brent Crude",        "category": "energy"},
        {"ticker": "NG=F",  "name": "Natural Gas",        "category": "energy"},
        {"ticker": "RB=F",  "name": "Gasoline (RBOB)",    "category": "energy"},
        {"ticker": "HO=F",  "name": "Heating Oil",        "category": "energy"},
        {"ticker": "QM=F",  "name": "E-mini Crude Oil",   "category": "energy"},
        {"ticker": "NG=F",  "name": "Henry Hub Gas",      "category": "energy"},
        {"ticker": "TTF=F", "name": "EU Natural Gas",     "category": "energy"},
        {"ticker": "XRB=F", "name": "RBOB Gasoline",      "category": "energy"},
        # -- AGRICULTURE  GRAINS --
        {"ticker": "ZC=F",  "name": "Corn",               "category": "agriculture"},
        {"ticker": "ZW=F",  "name": "Wheat",              "category": "agriculture"},
        {"ticker": "ZS=F",  "name": "Soybeans",           "category": "agriculture"},
        {"ticker": "ZM=F",  "name": "Soybean Meal",       "category": "agriculture"},
        {"ticker": "ZL=F",  "name": "Soybean Oil",        "category": "agriculture"},
        {"ticker": "ZR=F",  "name": "Rough Rice",         "category": "agriculture"},
        {"ticker": "ZO=F",  "name": "Oats",               "category": "agriculture"},
        {"ticker": "KE=F",  "name": "Hard Red Wheat",     "category": "agriculture"},
        {"ticker": "MWE=F", "name": "Spring Wheat",       "category": "agriculture"},
        # -- AGRICULTURE  SOFT COMMODITIES --
        {"ticker": "KC=F",  "name": "Coffee (Arabica)",   "category": "softs"},
        {"ticker": "SB=F",  "name": "Sugar No. 11",       "category": "softs"},
        {"ticker": "CT=F",  "name": "Cotton",             "category": "softs"},
        {"ticker": "CC=F",  "name": "Cocoa",              "category": "softs"},
        {"ticker": "OJ=F",  "name": "Orange Juice",       "category": "softs"},
        {"ticker": "LBS=F", "name": "Lumber",             "category": "softs"},
        {"ticker": "RC=F",  "name": "Coffee (Robusta)",   "category": "softs"},
        {"ticker": "RS=F",  "name": "Canola",             "category": "softs"},
        # -- LIVESTOCK --
        {"ticker": "LE=F",  "name": "Live Cattle",        "category": "livestock"},
        {"ticker": "GF=F",  "name": "Feeder Cattle",      "category": "livestock"},
        {"ticker": "HE=F",  "name": "Lean Hogs",          "category": "livestock"},
        {"ticker": "DA=F",  "name": "Class III Milk",     "category": "livestock"},
        # -- CRYPTO --
        {"ticker": "BTC=F", "name": "Bitcoin Futures",    "category": "crypto"},
        {"ticker": "ETH=F", "name": "Ethereum Futures",   "category": "crypto"},
        {"ticker": "MBT=F", "name": "Micro Bitcoin",      "category": "crypto"},
        {"ticker": "MET=F", "name": "Micro Ether",        "category": "crypto"},
        # -- ENERGY  CLEAN/RENEWABLES (ETF proxies) --
        {"ticker": "UNG",   "name": "Natural Gas ETF",    "category": "clean_energy"},
        {"ticker": "URA",   "name": "Uranium ETF",        "category": "clean_energy"},
        {"ticker": "ICLN",  "name": "Clean Energy ETF",   "category": "clean_energy"},
        {"ticker": "FSLR",  "name": "Solar (First Solar)","category": "clean_energy"},
        {"ticker": "PLUG",  "name": "Hydrogen (Plug Power)","category": "clean_energy"},
        {"ticker": "LIT",   "name": "Lithium ETF",        "category": "clean_energy"},
        {"ticker": "REMX",  "name": "Rare Earth ETF",     "category": "clean_energy"},
        # -- INDICES (commodity related) --
        {"ticker": "DJP",   "name": "Bloomberg Commodity","category": "indices"},
        {"ticker": "GSG",   "name": "S&P GSCI Commodity", "category": "indices"},
        {"ticker": "PDBC",  "name": "Commodity Index ETF","category": "indices"},
        {"ticker": "DBC",   "name": "DB Commodity ETF",   "category": "indices"},
        {"ticker": "COMT",  "name": "iShares Commodity",  "category": "indices"},
        # -- AGRICULTURE  FERTILIZERS --
        {"ticker": "MOS",   "name": "Mosaic (Fertilizer)","category": "agriculture"},
        {"ticker": "NTR",   "name": "Nutrien (Potash)",   "category": "agriculture"},
        {"ticker": "CF",    "name": "CF Industries (N)",  "category": "agriculture"},
        {"ticker": "IPI",   "name": "Intrepid Potash",    "category": "agriculture"},
        # -- WATER --
        {"ticker": "PHO",   "name": "Water ETF",          "category": "water"},
        {"ticker": "AWK",   "name": "American Water Works","category": "water"},
        {"ticker": "XYL",   "name": "Xylem (Water Tech)", "category": "water"},
        {"ticker": "WTRG",  "name": "Essential Utilities","category": "water"},
        {"ticker": "WM",    "name": "Waste Management",   "category": "water"},
        # -- TIMBER & PAPER --
        {"ticker": "LBS=F", "name": "Lumber Futures",     "category": "timber"},
        {"ticker": "WOOD",  "name": "Timber ETF",         "category": "timber"},
        {"ticker": "PCH",   "name": "PotlatchDeltic",     "category": "timber"},
        {"ticker": "RYN",   "name": "Rayonier (Timber)",  "category": "timber"},
        {"ticker": "WY",    "name": "Weyerhaeuser",        "category": "timber"},
        # -- SHIPPING & FREIGHT --
        {"ticker": "BDRY",  "name": "Dry Bulk Shipping",  "category": "shipping"},
        {"ticker": "ZIM",   "name": "ZIM Shipping",        "category": "shipping"},
        {"ticker": "SBLK",  "name": "Star Bulk Carriers", "category": "shipping"},
        {"ticker": "GOGL",  "name": "Golden Ocean Group", "category": "shipping"},
        {"ticker": "DHT",   "name": "DHT Holdings",        "category": "shipping"},
        # -- CARBON & SUSTAINABILITY --
        {"ticker": "KRBN",  "name": "Carbon Credits ETF", "category": "carbon"},
        {"ticker": "KCCA",  "name": "California Carbon",  "category": "carbon"},
        {"ticker": "NETZ",  "name": "Net Zero ETF",        "category": "carbon"},
        {"ticker": "SMOG",  "name": "Clean Cars ETF",      "category": "carbon"},
        # -- PHYSICAL AI & ROBOTICS --
        {"ticker": "BOTZ",  "name": "Robotics & AI ETF",  "category": "physical_ai"},
        {"ticker": "ROBO",  "name": "Robo Global ETF",    "category": "physical_ai"},
        {"ticker": "IRBO",  "name": "iShares Robotics",   "category": "physical_ai"},
        {"ticker": "ARKQ",  "name": "ARK Autonomous ETF", "category": "physical_ai"},
        {"ticker": "ISRG",  "name": "Intuitive Surgical", "category": "physical_ai"},
        {"ticker": "ABB",   "name": "ABB Robotics",        "category": "physical_ai"},
        {"ticker": "TER",   "name": "Teradyne (Robots)",  "category": "physical_ai"},
        {"ticker": "CGNX",  "name": "Cognex (Machine Vision)","category": "physical_ai"},
        # -- FOOD & BEVERAGE --
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
        WHERE l.price_change_pct IS NOT NULL AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
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
        AND l.market_cap>1000000000 AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
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
        AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY) GROUP BY sector ORDER BY total_volume DESC""")

@app.get("/api/market/most-volatile")
def get_volatile(limit: int=12):
    return query("""
        SELECT l.ticker,l.company_name,l.sector,l.current_price,l.price_change_pct,t.volatility,t.beta,a.analyst_rating
        FROM stocks_technicals t JOIN stocks_live l ON t.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON t.ticker=a.ticker
        WHERE t.volatility IS NOT NULL AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        AND (l.sector!='Indices' OR l.sector IS NULL) ORDER BY t.volatility DESC LIMIT %s
    """, [limit])

@app.get("/api/market/dividend-leaders")
def get_divs(limit: int=12):
    return query("""
        SELECT l.ticker,l.company_name,l.sector,l.current_price,l.price_change_pct,d.dividend_yield,d.dividend_rate,a.analyst_rating
        FROM stocks_dividends d JOIN stocks_live l ON d.ticker=l.ticker
        LEFT JOIN stocks_analyst a ON d.ticker=a.ticker
        WHERE d.dividend_yield IS NOT NULL AND d.dividend_yield>0 AND d.dividend_yield<50
        AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY) AND (l.sector!='Indices' OR l.sector IS NULL)
        ORDER BY d.dividend_yield DESC LIMIT %s
    """, [limit])

@app.get("/api/market/52week-proximity")
def get_52w(limit: int=15):
    return query("""
        SELECT ticker,company_name,sector,current_price,week_52_high,week_52_low,
               ROUND((week_52_high-current_price)/NULLIF(week_52_high,0)*100,1) as distance_from_high_pct,
               price_change_pct
        FROM stocks_live WHERE week_52_high IS NOT NULL AND week_52_low IS NOT NULL
        AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY) AND market_cap>5000000000
        AND (sector!='Indices' OR sector IS NULL) ORDER BY market_cap DESC LIMIT %s
    """, [limit])

@app.get("/api/market/breadth")
def get_breadth():
    return query("""
        SELECT l.sector,COUNT(*) as total,
               SUM(CASE WHEN l.current_price>t.ma_50 THEN 1 ELSE 0 END) as above_ma50,
               SUM(CASE WHEN l.current_price<=t.ma_50 THEN 1 ELSE 0 END) as below_ma50
        FROM stocks_live l JOIN stocks_technicals t ON l.ticker=t.ticker
        WHERE t.ma_50 IS NOT NULL AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        AND l.sector IS NOT NULL AND l.sector!='Indices'
        GROUP BY l.sector ORDER BY above_ma50 DESC
    """)

@app.get("/api/market/volume-spikes")
def get_vspikes(limit: int=12):
    return query("""
        SELECT ticker,company_name,sector,current_price,price_change_pct,
               volume,avg_volume,ROUND(volume/NULLIF(avg_volume,0),2) as volume_ratio
        FROM stocks_live WHERE volume IS NOT NULL AND avg_volume IS NOT NULL AND avg_volume>0
        AND current_price IS NOT NULL AND last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY) AND (sector!='Indices' OR sector IS NULL)
        AND volume>avg_volume*1.5 ORDER BY volume_ratio DESC LIMIT %s
    """, [limit])

# ======================================================
# ANALYST
# ======================================================
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
        AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY) AND a.target_price IS NOT NULL
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
        WHERE a.analyst_rating IS NOT NULL AND a.num_analysts>0 AND l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
        AND (l.sector!='Indices' OR l.sector IS NULL)
        GROUP BY l.sector ORDER BY avg_upside_pct DESC
    """)

# ======================================================
# PREDICTIONS
# ======================================================
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
        WHERE l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY) AND p.target_1m IS NOT NULL
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
    for field in ['key_factors','price_history','predicted_path','sentiment_json']:
        if d.get(field) and isinstance(d[field],str):
            try: d[field]=json.loads(d[field])
            except: d[field]=[]
    # Outcome of the most recent prediction that has been scored (the current one is still pending)
    try:
        o = query("""SELECT run_date, price_at_run, target_1w, actual_price, eval_date, direction_correct
                     FROM stocks_prediction_log WHERE ticker=%s AND eval_date IS NOT NULL
                     ORDER BY run_date DESC LIMIT 1""", [ticker.upper()])
        d['last_outcome'] = o[0] if o else None
    except Exception:
        d['last_outcome'] = None  # log table not created yet (run predict.py once)
    return d

# ======================================================
# EARNINGS
# ======================================================
@app.get("/api/earnings/calendar")
def get_earnings(limit: int=500):
    return query("""
        SELECT e.ticker,l.company_name,l.sector,e.next_earnings_date,
               e.eps_estimate,e.eps_actual,e.earnings_surprise_pct,
               l.current_price,l.price_change_pct,l.market_cap
        FROM stocks_earnings e JOIN stocks_live l ON e.ticker=l.ticker
        WHERE l.current_price IS NOT NULL AND l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY)
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

# ======================================================
# NEWS
# ======================================================
def _fetch_news(ticker, count=10):
    """Live news for a ticker: yfinance + Yahoo Finance RSS + Google News, merged and cached."""
    try:
        t = ticker.upper()
        return _nu_cached(f"stock:{t}:{count}", lambda: _build_stock_news(t, count))
    except Exception as e:
        print(f"Stock news error for {ticker}: {e}")
        return []

@app.get("/api/news/market")
def get_market_news():
    all_news=[]; seen=set()
    for sym in ["SPY","AAPL","MSFT","NVDA","GOOGL","AMZN","TSLA","JPM","META"]:
        for a in _fetch_news(sym,5):
            if a['title'] not in seen:
                seen.add(a['title']); all_news.append(a)
    all_news.sort(key=lambda x: x.get('published') or 0, reverse=True)
    return all_news[:15]

# === LIVE NEWS (start) ===
# World news from RSS feeds (Market page) and per-stock news (stock popup).
# Feeds are fetched in parallel and cached for a few minutes so pages load fast.
import urllib.request as _nu_req
import xml.etree.ElementTree as _nu_ET
import email.utils as _nu_email
import html as _nu_html
import re as _nu_re
import threading as _nu_threading
import time as _nu_time
from concurrent.futures import ThreadPoolExecutor as _NuPool

WORLD_NEWS_SOURCES = [
    # Markets & Economy
    {"url": "https://www.cnbc.com/id/20910258/device/rss/rss.html",  "source": "CNBC Economy", "category": "economy"},
    {"url": "https://www.cnbc.com/id/100727362/device/rss/rss.html", "source": "CNBC World",   "category": "global"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "source": "CNBC",         "category": "markets"},
    {"url": "https://feeds.marketwatch.com/marketwatch/topstories",  "source": "MarketWatch",  "category": "markets"},
    {"url": "https://feeds.marketwatch.com/marketwatch/marketpulse", "source": "MarketWatch",  "category": "markets"},
    # Business & Finance
    {"url": "https://feeds.bbci.co.uk/news/business/rss.xml",              "source": "BBC Business", "category": "economy"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",   "source": "NY Times",     "category": "economy"},
    # Tech & Crypto
    {"url": "https://techcrunch.com/feed/",            "source": "TechCrunch",    "category": "tech"},
    {"url": "https://feeds.feedburner.com/CoinDesk",   "source": "CoinDesk",      "category": "crypto"},
    {"url": "https://cointelegraph.com/rss",           "source": "CoinTelegraph", "category": "crypto"},
    # Commodities & Energy
    {"url": "https://oilprice.com/rss/main",           "source": "OilPrice",      "category": "commodities"},
    # India & NRI focused
    {"url": "https://economictimes.indiatimes.com/markets/rss.cms", "source": "Economic Times", "category": "global"},
    {"url": "https://www.livemint.com/rss/markets",                 "source": "Mint",           "category": "global"},
]

_NEWS_PER_SOURCE = 8
_NEWS_CACHE = {}
_NEWS_LOCK = _nu_threading.Lock()
_NEWS_TTL = 600  # seconds
_NU_MEDIA = "{http://search.yahoo.com/mrss/}"
_NU_ATOM = "{http://www.w3.org/2005/Atom}"
_NU_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_NU_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          "Accept": "application/rss+xml, application/xml, text/xml, */*"}


def _nu_cached(key, builder):
    now = _nu_time.time()
    with _NEWS_LOCK:
        hit = _NEWS_CACHE.get(key)
        if hit and now - hit[0] < _NEWS_TTL:
            return hit[1]
    value = builder()
    if value:  # never cache an empty result, so a failed fetch is retried next time
        with _NEWS_LOCK:
            _NEWS_CACHE[key] = (now, value)
    return value


def _nu_text(s):
    s = _nu_re.sub(r"<[^>]+>", " ", s or "")
    return _nu_re.sub(r"\s+", " ", _nu_html.unescape(s)).strip()


def _nu_ts(s):
    if not s:
        return None
    try:
        return int(_nu_email.parsedate_to_datetime(s.strip()).timestamp())
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _nu_image(item):
    for tag in (_NU_MEDIA + "content", _NU_MEDIA + "thumbnail"):
        for el in item.iter(tag):
            url = el.get("url")
            if url and (el.get("medium") in (None, "image") or tag.endswith("thumbnail")):
                return url
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image") and enc.get("url"):
        return enc.get("url")
    for raw in (item.findtext("description"), item.findtext(_NU_CONTENT + "encoded"),
                item.findtext(_NU_ATOM + "summary"), item.findtext(_NU_ATOM + "content")):
        m = _nu_re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", raw or "")
        if m:
            return _nu_html.unescape(m.group(1))
    return None


def _nu_parse_feed(xml_bytes, source, category, limit):
    root = _nu_ET.fromstring(xml_bytes)
    items = root.findall("./channel/item") or root.findall(".//item") or root.findall(_NU_ATOM + "entry")
    out = []
    for item in items:
        if len(out) >= limit:
            break
        title = _nu_text(item.findtext("title") or item.findtext(_NU_ATOM + "title"))
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        if not link:
            a = item.find(_NU_ATOM + "link")
            link = a.get("href", "") if a is not None else ""
        desc = _nu_text(item.findtext("description") or item.findtext(_NU_ATOM + "summary"))[:200]
        src_el = item.find("source")
        src_name = (src_el.text or "").strip() if src_el is not None and src_el.text else source
        out.append({
            "title": title,
            "source": src_name,
            "category": category,
            "url": link,
            "description": desc,
            "image": _nu_image(item),
            "published": _nu_ts(item.findtext("pubDate") or item.findtext(_NU_ATOM + "published")
                                or item.findtext(_NU_ATOM + "updated")),
        })
    return out


def _nu_fetch_feed(url, source, category, limit=_NEWS_PER_SOURCE):
    try:
        req = _nu_req.Request(url, headers=_NU_UA)
        with _nu_req.urlopen(req, timeout=6) as resp:
            data = resp.read()
        return _nu_parse_feed(data, source, category, limit)
    except Exception as e:
        print(f"[news] {source}: could not load feed ({type(e).__name__})")
        return []


def _nu_merge(lists, limit):
    seen, merged = set(), []
    for lst in lists:
        for a in lst:
            key = _nu_re.sub(r"[^a-z0-9]", "", a["title"].lower())[:80]
            if key and key not in seen:
                seen.add(key)
                merged.append(a)
    merged.sort(key=lambda a: a.get("published") or 0, reverse=True)
    return merged[:limit]


def _build_world_news():
    with _NuPool(max_workers=8) as pool:
        results = list(pool.map(lambda s: _nu_fetch_feed(s["url"], s["source"], s["category"]),
                                WORLD_NEWS_SOURCES))
    ok = sum(1 for r in results if r)
    print(f"[news] world news: {ok}/{len(WORLD_NEWS_SOURCES)} feeds loaded, "
          f"{sum(len(r) for r in results)} articles")
    return _nu_merge(results, 100)


def _yf_stock_news(ticker, count):
    """News from yfinance (includes thumbnails when Yahoo provides them)."""
    out = []
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return out
    for item in raw:
        try:
            c = item.get("content") or {}
            if c:
                title = c.get("title", "")
                source = (c.get("provider") or {}).get("displayName") or "Yahoo Finance"
                url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or ""
                ts = _nu_ts(c.get("pubDate") or c.get("displayTime"))
                thumb = c.get("thumbnail") or {}
            else:
                title = item.get("title", "")
                source = item.get("publisher") or "Yahoo Finance"
                url = item.get("link") or ""
                ts = item.get("providerPublishTime")
                thumb = item.get("thumbnail") or {}
            image = None
            res = thumb.get("resolutions") or []
            if res:
                best = sorted(res, key=lambda r: abs((r.get("width") or 0) - 240))[0]
                image = best.get("url")
            image = image or thumb.get("originalUrl")
            if title and title.strip():
                out.append({"title": title.strip(), "source": source, "url": url,
                            "published": ts, "image": image, "category": "markets"})
        except Exception:
            continue
    return out[:count]


def _build_stock_news(ticker, count):
    q = urllib.parse.quote(f"{ticker} stock")
    jobs = [
        lambda: _yf_stock_news(ticker, count),
        lambda: _nu_fetch_feed(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
                               "Yahoo Finance", "markets", count),
        lambda: _nu_fetch_feed(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
                               "Google News", "markets", count),
    ]
    with _NuPool(max_workers=3) as pool:
        results = list(pool.map(lambda f: f(), jobs))
    for a in results[2]:  # Google News titles end with " - Publisher"
        a["title"] = _nu_re.sub(r"\s+-\s+[^-]{2,60}$", "", a["title"]).strip() or a["title"]
    return _nu_merge(results, count)


@app.get("/api/news/world")
def get_world_news():
    """Live world finance news from RSS feeds, newest first, with category and image."""
    try:
        return _nu_cached("world", _build_world_news)
    except Exception as e:
        print(f"World news error: {e}")
        return []
# === LIVE NEWS (end) ===


@app.get("/api/news/{ticker}")
def get_stock_news(ticker: str):
    return _fetch_news(ticker.upper(), 10)

# ══════════════════════════════════════════════════════
# PORTFOLIO
# ══════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════
# AI AGENT ENDPOINTS
# ══════════════════════════════════════════════════════
import logging
import queue
import re
import threading
import time
from collections import defaultdict, deque
from typing import List, Literal

import groq
from fastapi import Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import date as Date
from pydantic import BaseModel, Field, field_validator

from agent import run_agent, explain_single_stock, LLMUnavailable, set_stream_sink, clear_stream_sink
import ipo_data
import news_feed
import peers
import portfolio
import sentiment
import stock_period
import watchlist
from screener import get_top_movers, get_sector_summary, screen_stocks

log = logging.getLogger("uvicorn.error")

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,10}$")


def _valid_tickers(values):
    return [t.strip() for t in values if isinstance(t, str) and TICKER_RE.match(t.strip())]


# ── Rate limit for the endpoints that spend LLM quota (per signed-in user, sliding 60s window) ──
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
    user_id = getattr(request.state, "user_id", None)
    key = f"user:{user_id}" if user_id is not None else _client_id(request)
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


def describe_ai_error(e):
    """(HTTP status, short safe message) for a failure inside an AI call. Details go to the server log, not the client."""
    if isinstance(e, LLMUnavailable):
        return 503, "The AI assistant isn't available right now."
    if isinstance(e, groq.RateLimitError):
        return 429, "The AI service is busy. Please try again in a minute."
    if isinstance(e, groq.APIConnectionError):  # includes timeouts
        return 504, "I couldn't reach the AI service in time. Please try again."
    log.error("AI endpoint failed", exc_info=e)
    return 500, "Something went wrong on our side. Please try again."


def run_ai(call):
    """Run an AI call and turn failures into short, safe messages."""
    try:
        return call()
    except Exception as e:
        status, message = describe_ai_error(e)
        return agent_error(status, message)


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
    tickers: List[str] = Field(default_factory=list, max_length=30)   # ticker symbols or company names
    use_holdings: bool = False                                        # review the saved holdings instead

    @field_validator("tickers")
    @classmethod
    def sane_entries(cls, v):
        return [t.strip()[:60] for t in v if isinstance(t, str) and t.strip()]


class ExplainRequest(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$")
    query: str = Field(default="", max_length=500)


class HoldingIn(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$")
    quantity: float = Field(gt=0, le=1e9)
    avg_buy_price: float = Field(gt=0, le=1e7)
    buy_date: Date
    notes: str = Field(default="", max_length=200)


class HoldingEdit(BaseModel):
    quantity: float = Field(gt=0, le=1e9)
    avg_buy_price: float = Field(gt=0, le=1e7)
    buy_date: Date
    notes: str = Field(default="", max_length=200)


def holding_call(fn, user_id, *args):
    try:
        return {"status": "success", **fn(user_id, *args)}
    except portfolio.PortfolioError as e:
        return agent_error(e.status, e.message)
    except Exception:
        log.exception("Portfolio change failed")
        return agent_error(500, "Something went wrong saving that. Please try again.")


@app.get("/api/portfolio")
def get_portfolio(user_id: int = Depends(current_user_id)):
    return portfolio.list_holdings(user_id)


@app.get("/api/portfolio/summary")
def get_portfolio_summary(user_id: int = Depends(current_user_id)):
    return portfolio.summarize(portfolio.list_holdings(user_id))


@app.post("/api/portfolio")
def add_portfolio_holding(body: HoldingIn, user_id: int = Depends(current_user_id)):
    """Add a purchase. Buying more of a stock you already hold merges into one position at the weighted-average cost."""
    return holding_call(portfolio.add_holding, user_id, body.ticker, body.quantity, body.avg_buy_price, body.buy_date, body.notes)


@app.put("/api/portfolio/{holding_id}")
def edit_portfolio_holding(holding_id: int, body: HoldingEdit, user_id: int = Depends(current_user_id)):
    return holding_call(portfolio.update_holding, user_id, holding_id, body.quantity, body.avg_buy_price, body.buy_date, body.notes)


@app.delete("/api/portfolio/{holding_id}")
def delete_portfolio_holding(holding_id: int, user_id: int = Depends(current_user_id)):
    return holding_call(portfolio.delete_holding, user_id, holding_id)


class TxIn(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$")
    transaction_type: Literal["BUY", "SELL", "DIVIDEND"]
    quantity: float = Field(gt=0, le=1e9)
    price: float = Field(gt=0, le=1e7)          # for a DIVIDEND, the amount per share
    transaction_date: Date
    notes: str = Field(default="", max_length=200)
    apply_to_holdings: bool = False             # also add the shares to / remove them from Holdings


class TxEdit(BaseModel):
    quantity: float = Field(gt=0, le=1e9)
    price: float = Field(gt=0, le=1e7)
    transaction_date: Date
    notes: str = Field(default="", max_length=200)


@app.get("/api/transactions")
def get_transactions(user_id: int = Depends(current_user_id)):
    return portfolio.list_transactions(user_id)


@app.get("/api/transactions/summary")
def get_transactions_summary(user_id: int = Depends(current_user_id)):
    return portfolio.transaction_summary(user_id)


@app.post("/api/transactions")
def add_transaction_entry(body: TxIn, user_id: int = Depends(current_user_id)):
    return holding_call(portfolio.add_transaction, user_id, body.ticker, body.transaction_type, body.quantity, body.price,
                        body.transaction_date, body.notes, body.apply_to_holdings)


@app.put("/api/transactions/{tx_id}")
def edit_transaction_entry(tx_id: int, body: TxEdit, user_id: int = Depends(current_user_id)):
    return holding_call(portfolio.update_transaction, user_id, tx_id, body.quantity, body.price, body.transaction_date, body.notes)


@app.delete("/api/transactions/{tx_id}")
def delete_transaction_entry(tx_id: int, user_id: int = Depends(current_user_id)):
    return holding_call(portfolio.delete_transaction, user_id, tx_id)


@app.get("/api/portfolio/performance")
def portfolio_performance(user_id: int = Depends(current_user_id)):
    """Portfolio value against the same dollars invested in the S&P 500, plus risk measures."""
    try:
        return {"status": "success", **portfolio.performance(user_id)}
    except portfolio.PortfolioError as e:
        return agent_error(e.status, e.message)
    except Exception:
        log.exception("Portfolio performance failed")
        return agent_error(500, "Something went wrong working out performance.")


# ── Administration: who is allowed in ──
@app.get("/admin")
def admin_page(request: Request):
    user = auth.get_user(request.state.user_id)
    if not user or user["status"] != "approved" or not user["is_admin"]:
        return HTMLResponse(auth_pages.notice_page("Administrators only", "You don't have access to this page.", "/", "Back to the dashboard"), status_code=403)
    with open(os.path.join(_BASE_DIR, "admin.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "private, no-store"})


class StatusBody(BaseModel):
    status: Literal["approved", "blocked"]


@app.get("/api/admin/users")
def admin_list_users(admin_id: int = Depends(require_admin)):
    try:
        return {"status": "success", "you": admin_id, **auth.list_users()}
    except Exception:
        log.exception("Listing users failed")
        return agent_error(500, "Something went wrong loading the accounts.")


@app.get("/api/admin/summary")
def admin_summary(admin_id: int = Depends(require_admin)):
    try:
        return {"status": "success", "pending": auth.pending_count()}
    except Exception:
        log.exception("Counting pending accounts failed")
        return agent_error(500, "Something went wrong counting the pending accounts.")


@app.post("/api/admin/users/{user_id}/status")
def admin_set_status(user_id: int, body: StatusBody, admin_id: int = Depends(require_admin_action)):
    """Approve or block an account. The change applies to that person's very next request on this server."""
    try:
        return {"status": "success", "user": auth.set_user_status(admin_id, user_id, body.status)}
    except auth.AuthError as e:
        return agent_error(e.status, e.message)
    except Exception:
        log.exception("Changing an account's status failed")
        return agent_error(500, "Something went wrong saving that change.")


class WatchlistBody(BaseModel):
    tickers: List[str] = Field(default_factory=list, max_length=200)


@app.get("/api/watchlist")
def get_watchlist(user_id: int = Depends(current_user_id)):
    try:
        return {"status": "success", "tickers": watchlist.get_watchlist(user_id)}
    except Exception:
        log.exception("Watchlist load failed")
        return agent_error(500, "Something went wrong loading your watchlist.")


@app.put("/api/watchlist")
def put_watchlist(body: WatchlistBody, user_id: int = Depends(current_user_id)):
    """Replace the whole watchlist (the dashboard sends the full list after each change)."""
    try:
        return {"status": "success", "tickers": watchlist.save_watchlist(user_id, body.tickers)}
    except watchlist.WatchlistError as e:
        return agent_error(e.status, e.message)
    except Exception:
        log.exception("Watchlist save failed")
        return agent_error(500, "Something went wrong saving your watchlist.")


def _tracked_stock(ticker: str) -> bool:
    return bool(query("SELECT 1 FROM stocks_live WHERE ticker=%s LIMIT 1", [ticker.upper()]))


@app.get("/api/stocks/{ticker}/price-range")
def stock_price_range(ticker: str = Path(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$")):
    """First and last trading day we have prices for, so the period picker only offers years that exist."""
    if not _tracked_stock(ticker):
        return agent_error(404, f"{ticker.upper()} isn't in our database.")
    try:
        return {"status": "success", **stock_period.price_range(ticker)}
    except stock_period.PeriodError as e:
        return agent_error(e.status, e.message)
    except Exception:
        log.exception("Price range failed")
        return agent_error(500, "Something went wrong loading the price history.")


@app.get("/api/stocks/{ticker}/period")
def stock_period_performance(ticker: str = Path(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$"), start: Date = Query(...), end: Date = Query(...)):
    """How the stock performed between two dates (a year, a quarter or anything else), against the S&P 500."""
    if not _tracked_stock(ticker):
        return agent_error(404, f"{ticker.upper()} isn't in our database.")
    try:
        return {"status": "success", **stock_period.period_performance(ticker, start, end)}
    except stock_period.PeriodError as e:
        return agent_error(e.status, e.message)
    except Exception:
        log.exception("Period performance failed")
        return agent_error(500, "Something went wrong loading the price history.")



# Big one-day moves inside a chosen period, each with what happened: the S&P 500 that day, whether it was an
# earnings day, and the closest headline. Yahoo only has recent news, so older days use a dated Google News search.
_SPIKE_NAME_RE = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|sa|nv|ag|se)\.?$", re.I)


def _spike_company(ticker):
    try:
        rows = query("SELECT company_name FROM stocks_live WHERE ticker=%s LIMIT 1", [ticker])
        name = (rows[0].get("company_name") if rows else "") or ""
    except Exception:
        name = ""
    for _ in range(2):
        name = _SPIKE_NAME_RE.sub("", name.strip())
    return name.strip()


def _spike_earnings_days(ticker):
    """Past earnings dates (YYYY-MM-DD) from Yahoo, cached for a day. Empty if Yahoo has none."""
    key = "earndays:" + ticker
    hit = _NEWS_CACHE.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    days = set()
    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=60)
        if df is not None:
            days = {str(ix)[:10] for ix in df.index}
    except Exception:
        days = set()
    _NEWS_CACHE[key] = (time.time(), days)
    return days


def _spike_headline(ticker, name, day):
    """Closest headline to `day` (YYYY-MM-DD) from a Google News search limited to the days around it."""
    d0 = datetime.strptime(day, "%Y-%m-%d")
    after = datetime.fromtimestamp(d0.timestamp() - 2 * 86400).strftime("%Y-%m-%d")
    before = datetime.fromtimestamp(d0.timestamp() + 2 * 86400).strftime("%Y-%m-%d")
    who = f'({ticker} OR "{name}")' if name and name.upper() != ticker else ticker
    q = urllib.parse.quote(f"{who} stock after:{after} before:{before}")
    items = _nu_fetch_feed(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
                           "Google News", "markets", 8)
    if not items:
        return None
    target = d0.timestamp() + 12 * 3600
    items.sort(key=lambda a: abs(float(a.get("published") or target) - target))
    a = items[0]
    title = _nu_re.sub(r"\s+-\s+[^-]{2,60}$", "", a["title"]).strip() or a["title"]
    return {"reason": title, "reason_url": a.get("url"), "reason_source": a.get("source")}


@app.get("/api/stocks/{ticker}/period-spikes")
def stock_period_spikes(ticker: str = Path(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$"), start: Date = Query(...), end: Date = Query(...)):
    """The biggest one-day moves in a period (for the markers on the Performance period chart)."""
    t = ticker.upper()
    if not _tracked_stock(t):
        return agent_error(404, f"{t} isn't in our database.")
    try:
        start, end = stock_period.validate_dates(start, end)
    except stock_period.PeriodError as e:
        return agent_error(e.status, e.message)
    key = f"pspikes:{t}:{start}:{end}"
    hit = _NEWS_CACHE.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    try:
        closes = stock_period.full_history(t)
        try:
            bench = stock_period.full_history(stock_period.BENCHMARK)
        except Exception:
            bench = None
        spikes = stock_period.find_spikes(closes, bench, start, end)
        if spikes:
            earn = _spike_earnings_days(t)
            for sp in spikes:
                d = datetime.strptime(sp["date"], "%Y-%m-%d")
                near = {datetime.fromtimestamp(d.timestamp() - k * 86400).strftime("%Y-%m-%d") for k in range(0, 4)}
                sp["earnings"] = bool(earn & near)      # results usually come out the evening before the move
                sp["reason"] = sp["reason_url"] = sp["reason_source"] = None
            # recent days: today's news feed; older days: dated search
            recent = []
            if any((datetime.now() - datetime.strptime(sp["date"], "%Y-%m-%d")).days <= 7 for sp in spikes):
                recent = _fetch_news(t, 20) or []
            for sp in spikes:
                st = datetime.strptime(sp["date"], "%Y-%m-%d").timestamp() + 12 * 3600
                best, delta = None, 2 * 86400
                for a in recent:
                    if a.get("published") and abs(float(a["published"]) - st) < delta:
                        best, delta = a, abs(float(a["published"]) - st)
                if best:
                    sp["reason"], sp["reason_url"], sp["reason_source"] = best["title"], best.get("url"), best.get("source")
            todo = [sp for sp in spikes if not sp["reason"]]
            if todo:
                name = _spike_company(t)
                with _NuPool(max_workers=6) as pool:
                    for sp, res in zip(todo, pool.map(lambda x: _spike_headline(t, name, x["date"]), todo)):
                        if res:
                            sp.update(res)
        out = {"status": "success", "ticker": t, "spikes": spikes}
        _NEWS_CACHE[key] = (time.time(), out)
        return out
    except stock_period.PeriodError as e:
        return agent_error(e.status, e.message)
    except Exception:
        log.exception("Period spikes failed")
        return agent_error(500, "Something went wrong finding the big moves.")

def _parse_compare_tickers(tickers: str):
    parts = [t.strip() for t in tickers.split(",") if t.strip()]
    valid = _valid_tickers(parts)
    if len(valid) != len(parts):
        return None, agent_error(400, "One of the tickers is not valid.")
    if not 2 <= len({t.upper() for t in valid}) <= peers.MAX_STOCKS:
        return None, agent_error(400, f"Compare between 2 and {peers.MAX_STOCKS} different stocks.")
    return valid, None


@app.get("/api/compare/prices")
def compare_prices(tickers: str = Query(..., min_length=1, max_length=120)):
    """One-year closing prices rebased to 100, so the stocks' relative performance can be charted."""
    valid, error = _parse_compare_tickers(tickers)
    if error:
        return error
    try:
        return {"status": "success", **peers.rebased_prices(valid)}
    except Exception:
        log.exception("Price comparison failed")
        return agent_error(500, "Something went wrong loading prices.")


@app.get("/api/compare/verdict")
def compare_verdict(request: Request, tickers: str = Query(..., min_length=1, max_length=120)):
    """A short AI-written comparison of the stocks, based only on the stored data (cached for an hour)."""
    limited = check_rate_limit(request)
    if limited:
        return limited
    valid, error = _parse_compare_tickers(tickers)
    if error:
        return error

    def call():
        result = peers.verdict(valid)
        if result is None:
            return agent_error(404, "We need at least two stocks we track to compare.")
        return {"status": "success", **result}
    return run_ai(call)


def recommend_payload(body: RecommendRequest) -> dict:
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


@app.post("/agent/recommend")
def agent_recommend(request: Request, body: RecommendRequest):
    limited = check_rate_limit(request)
    if limited:
        return limited
    return run_ai(lambda: recommend_payload(body))


@app.post("/agent/recommend/stream")
def agent_recommend_stream(request: Request, body: RecommendRequest):
    """
    The same answer as /agent/recommend, sent as server-sent events so the text appears as it is written:
      data: {"delta": "..."}   a piece of the answer
      data: {"final": {...}}   the complete result (same shape as /agent/recommend), including stock cards
      data: {"error": {"status": 429, "message": "..."}}
    """
    limited = check_rate_limit(request)
    if limited:
        return limited
    events = queue.Queue()

    def worker():
        token = set_stream_sink(lambda text: events.put(("delta", text)))
        try:
            events.put(("final", jsonable_encoder(recommend_payload(body))))
        except Exception as e:
            status, message = describe_ai_error(e)
            events.put(("error", {"status": status, "message": message}))
        finally:
            clear_stream_sink(token)
            events.put(("done", None))

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            kind, value = events.get()
            if kind == "done":
                return
            yield f"data: {json.dumps({kind: value})}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/agent/explain-stock")
def agent_explain_stock(request: Request, body: ExplainRequest):
    limited = check_rate_limit(request)
    if limited:
        return limited
    return run_ai(lambda: {"status": "success", "ticker": body.ticker,
                           "explanation": explain_single_stock(body.ticker, body.query)})


@app.post("/agent/portfolio-review")
def agent_portfolio_review(request: Request, body: PortfolioRequest, user_id: int = Depends(current_user_id)):
    limited = check_rate_limit(request)
    if limited:
        return limited
    if not body.use_holdings and not body.tickers:
        return agent_error(400, "Type at least one ticker or company name, or review your saved holdings.")

    def call():
        try:
            result = portfolio.review(user_id, body.tickers, body.use_holdings)
        except portfolio.PortfolioError as e:
            return agent_error(e.status, e.message)
        return {"status": "success", **result}
    return run_ai(call)

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


@app.get("/api/peers/{ticker}")
def get_stock_peers(request: Request, ticker: str = Path(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$"), n: int = Query(4, ge=1, le=5)):
    """Suggested competitors for a stock (database pool, closest picked by AI, cached for a week)."""
    limited = check_rate_limit(request)  # an uncached lookup makes an LLM call
    if limited:
        return limited
    try:
        result = peers.suggest_peers(ticker, n)
    except Exception:
        log.exception("Peer lookup failed")
        return agent_error(500, "Something went wrong finding peers.")
    if result is None:
        return agent_error(404, f"{ticker.upper()} isn't in our database.")
    return {"status": "success", **result}


@app.get("/api/sentiment/{ticker}")
def get_stock_sentiment(request: Request, ticker: str = Path(pattern=r"^[A-Za-z0-9.\-^=]{1,10}$")):
    """AI-scored sentiment of a stock's recent headlines (cached for a few hours)."""
    limited = check_rate_limit(request)  # a refresh makes an LLM call
    if limited:
        return limited

    def call():
        result = sentiment.get_sentiment(ticker)
        if result is None:
            return agent_error(404, f"{ticker.upper()} isn't in our database.")
        return {"status": "success", **result}
    return run_ai(call)


@app.get("/api/compare")
def compare_stocks(tickers: str = Query(..., min_length=1, max_length=120)):
    """Side-by-side metrics. The first ticker is the stock the others are compared against."""
    parts = [t.strip() for t in tickers.split(",") if t.strip()]
    valid = _valid_tickers(parts)
    if len(valid) != len(parts):
        return agent_error(400, "One of the tickers is not valid.")
    if not 2 <= len({t.upper() for t in valid}) <= peers.MAX_STOCKS:
        return agent_error(400, f"Compare between 2 and {peers.MAX_STOCKS} different stocks.")
    try:
        result = peers.build_comparison(valid)
    except Exception:
        log.exception("Comparison failed")
        return agent_error(500, "Something went wrong building the comparison.")
    if valid[0].upper() not in result["tickers"] or len(result["tickers"]) < 2:
        return agent_error(404, "We need at least two stocks we track, including the first one.")
    return {"status": "success", **result}


@app.get("/api/market/signals")
async def market_signals():
    try:
        conn = get_db()
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
def prediction_accuracy():
    """Hit rate of the 1-week (7 trading day) direction call, next to what always guessing 'up' would have scored."""
    try:
        r = query("""SELECT COUNT(*) AS total, COALESCE(SUM(direction_correct),0) AS correct,
                            AVG(actual_price > price_at_run)*100 AS always_up_pct,
                            MIN(run_date) AS first_run, MAX(run_date) AS last_run
                     FROM stocks_prediction_log WHERE eval_date IS NOT NULL""")[0]
        total, correct = int(r['total'] or 0), int(r['correct'] or 0)
        if not total:
            return {"total": 0, "correct": 0, "horizon": "7 trading days"}
        pct = round(correct / total * 100, 1)
        up = round(float(r['always_up_pct'] or 0), 1)
        return {"total": total, "correct": correct, "accuracy_pct": pct, "always_up_pct": up,
                "edge_pp": round(pct - up, 1), "first_run": str(r['first_run']), "last_run": str(r['last_run']),
                "horizon": "7 trading days"}
    except Exception:
        log.exception("Accuracy summary failed")
        return {"total": 0, "correct": 0, "error": "Accuracy data is not available yet."}

# ======================================================
# NEWS SENTIMENT  (4-factor: News 40% + Momentum 25% + Volume 15% + Analyst 20%)
# ======================================================

def _score_sentiment(headlines: list) -> dict:
    """Score a list of headline dicts using VADER. Returns weighted sentiment."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "vaderSentiment", "-q"])
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    analyzer = SentimentIntensityAnalyzer()
    scored = []
    for item in headlines:
        text     = item.get("title", "")
        age_days = item.get("age_days", 0)
        if not text:
            continue
        compound = analyzer.polarity_scores(text)["compound"]  # -1..+1
        # Recency weight: today=1.0, 3d=0.7, 7d=0.4, 14d+=0.2
        if age_days <= 1:   weight = 1.0
        elif age_days <= 3: weight = 0.7
        elif age_days <= 7: weight = 0.4
        else:               weight = 0.2
        scored.append({
            "title":    text,
            "score":    round(compound, 3),
            "weight":   weight,
            "age_days": age_days,
            "source":   item.get("source", ""),
            "url":      item.get("url", ""),
        })

    if not scored:
        return {"score": 0.0, "label": "Neutral", "headlines": [], "count": 0}

    total_weight = sum(s["weight"] for s in scored)
    weighted_sum = sum(s["score"] * s["weight"] for s in scored)
    final_score  = round(weighted_sum / total_weight, 3) if total_weight > 0 else 0.0

    if final_score >= 0.3:   label = "Bullish"
    elif final_score <= -0.2: label = "Bearish"
    else:                     label = "Neutral"

    scored.sort(key=lambda x: abs(x["score"]) * x["weight"], reverse=True)
    return {"score": final_score, "label": label, "headlines": scored[:3], "count": len(scored)}


@app.get("/api/stocks/{ticker}/news-sentiment")
def get_news_sentiment(ticker: str):
    """
    Two-layer sentiment for the bar under the prediction chart (same engine predict.py uses):
      Stock layer (60%):  company news 40%, momentum 25%, volume 15%, analyst 20%
      Market layer (40%): VIX, S&P 500 trend, sector trend, rates, oil, dollar, gold, world news
    """
    t_upper = ticker.upper()
    try:
        import sentiment_engine as se
    except Exception as e:
        return {"ticker": t_upper, "score": 0.0, "label": "Neutral", "error": f"sentiment_engine.py missing: {e}",
                "context_line": "Sentiment data unavailable.", "headlines": [], "count": 0}
    try:
        live = _pf_rows("""SELECT l.sector, l.volume, l.avg_volume, l.price_change_pct,
                                  CAST(l.last_updated AS CHAR) AS updated_at,
                                  a.analyst_rating, a.upside_pct
                           FROM stocks_live l LEFT JOIN stocks_analyst a ON l.ticker=a.ticker
                           WHERE l.ticker=%s""", [t_upper])
        row = live[0] if live else {}
        vr, vnote = None, ""
        if row.get("volume") and row.get("avg_volume"):
            as_of = None
            try:
                as_of = datetime.fromisoformat(str(row.get("updated_at")))
            except Exception:
                pass
            # During market hours today's volume is only partial - adjust for time of day
            vr, vnote = se.adjust_volume_ratio(float(row["volume"]) / float(row["avg_volume"]), as_of=as_of)
        s = se.full_sentiment(
            t_upper, sector=row.get("sector"),
            headlines=_fetch_news(t_upper, 15),
            volume_ratio=vr, volume_note=vnote, day_change_pct=row.get("price_change_pct"),
            analyst_rating=row.get("analyst_rating"),
            upside_pct=row.get("upside_pct") if row.get("analyst_rating") else None,
        )

        # Plain-English line: does sentiment agree with the 1-month prediction?
        pred_up = None
        try:
            p = _pf_rows("""SELECT p.target_1m, l.current_price FROM stocks_predictions p
                            JOIN stocks_live l ON p.ticker=l.ticker WHERE p.ticker=%s""", [t_upper])
            if p and p[0].get("target_1m") and p[0].get("current_price"):
                pred_up = float(p[0]["target_1m"]) > float(p[0]["current_price"])
        except Exception:
            pass
        label = s["label"]
        mkt = s["market_label"].lower()
        if pred_up is None:
            ctx = {"Bullish": "Sentiment is Bullish - company and market signals lean positive.",
                   "Bearish": "Sentiment is Bearish - company and market signals lean negative."}.get(
                   label, "Sentiment is Neutral - no strong signal from news, the market or analysts.")
        elif pred_up and label == "Bullish":
            ctx = "Bullish sentiment supports the upward prediction - company and market signals are aligned."
        elif pred_up and label == "Bearish":
            ctx = "Bearish sentiment contradicts the upward prediction - treat it with caution, signals are mixed."
        elif pred_up:
            ctx = f"Neutral sentiment - the upward prediction isn't strongly backed right now (market mood: {mkt})."
        elif label == "Bearish":
            ctx = "Bearish sentiment aligns with the downward prediction - signals consistently point lower."
        elif label == "Bullish":
            ctx = "Bullish sentiment contradicts the downward prediction - positive signals may limit the fall."
        else:
            ctx = f"Neutral sentiment alongside the downward prediction (market mood: {mkt}) - watch for a change in momentum."
        s["context_line"] = ctx
        return s
    except Exception as e:
        print(f"Sentiment error for {t_upper}: {e}")
        return {"ticker": t_upper, "score": 0.0, "label": "Neutral", "context_line": "Sentiment data unavailable.",
                "headlines": [], "count": 0, "error": str(e)}


@app.get("/api/stocks/{ticker}/price-spikes")
def get_price_spikes(ticker: str, threshold_pct: float = 3.0):
    """
    Big daily moves (> threshold_pct) in the last year, each with:
      - what the whole market (S&P 500) did that day
      - the closest news headline (recent news, or a dated Google News search for older spikes)
      - a plain-English 'driver' line
    """
    t = ticker.upper()
    key = f"spikes:{t}:{threshold_pct}"
    hit = _NEWS_CACHE.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        hist = yf.Ticker(t).history(period="1y")
        if hist.empty:
            return {"spikes": [], "ticker": t}
        closes = hist["Close"]
        dates = [str(d)[:10] for d in hist.index]
        spy_map = {}
        try:
            spy = yf.Ticker("SPY").history(period="1y")["Close"]
            sd = [str(d)[:10] for d in spy.index]
            for i in range(1, len(spy)):
                spy_map[sd[i]] = (float(spy.iloc[i]) - float(spy.iloc[i - 1])) / float(spy.iloc[i - 1]) * 100
        except Exception:
            pass

        spikes = []
        for i in range(1, len(closes)):
            prev, cur = float(closes.iloc[i - 1]), float(closes.iloc[i])
            if prev <= 0:
                continue
            chg = (cur - prev) / prev * 100
            if abs(chg) < threshold_pct:
                continue
            mkt = spy_map.get(dates[i])
            spikes.append({"date": dates[i], "price": round(cur, 2), "prev_price": round(prev, 2),
                           "change_pct": round(chg, 2), "direction": "up" if chg > 0 else "down",
                           "market_change_pct": round(mkt, 2) if mkt is not None else None,
                           "reason": None, "reason_url": None, "reason_source": None})
        spikes.sort(key=lambda x: x["date"], reverse=True)
        spikes = spikes[:20]

        # 1) recent spikes: match against current news (within 2 days)
        recent = _fetch_news(t, 20)
        for sp in spikes:
            st = datetime.strptime(sp["date"], "%Y-%m-%d").timestamp()
            best, delta = None, 2 * 86400
            for a in recent:
                if a.get("published") and abs(float(a["published"]) - st) < delta:
                    best, delta = a, abs(float(a["published"]) - st)
            if best:
                sp["reason"], sp["reason_url"], sp["reason_source"] = best["title"], best.get("url"), best.get("source")

        # 2) older spikes: dated Google News search around the spike day
        def dated_search(sp):
            d0 = datetime.strptime(sp["date"], "%Y-%m-%d")
            after = datetime.fromtimestamp(d0.timestamp() - 2 * 86400).strftime("%Y-%m-%d")
            before = datetime.fromtimestamp(d0.timestamp() + 2 * 86400).strftime("%Y-%m-%d")
            q = urllib.parse.quote(f"{t} stock after:{after} before:{before}")
            items = _nu_fetch_feed(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
                                   "Google News", "markets", 5)
            if items:
                a = items[0]
                title = _nu_re.sub(r"\s+-\s+[^-]{2,60}$", "", a["title"]).strip() or a["title"]
                return title, a.get("url"), a.get("source")
            return None
        todo = [sp for sp in spikes if not sp["reason"]][:12]
        if todo:
            with _NuPool(max_workers=6) as pool:
                for sp, res in zip(todo, pool.map(dated_search, todo)):
                    if res:
                        sp["reason"], sp["reason_url"], sp["reason_source"] = res

        # 3) plain-English driver
        for sp in spikes:
            m = sp["market_change_pct"]
            c = sp["change_pct"]
            if m is None:
                drv = ""
            elif (m > 0) != (c > 0) and abs(m) >= 0.5:
                drv = f"Stock-specific - it moved against the market (S&P 500 {m:+.1f}% that day)"
            elif abs(m) >= 1.5 and abs(m) >= 0.5 * abs(c):
                drv = f"Market-wide move - the S&P 500 was {m:+.1f}% that day"
            elif abs(c) - abs(m) >= 2:
                drv = f"Mostly stock-specific (S&P 500 only {m:+.1f}% that day)"
            else:
                drv = f"Mixed - market (S&P 500 {m:+.1f}%) and company news both played a part"
            sp["driver"] = drv

        out = {"spikes": spikes, "ticker": t}
        _NEWS_CACHE[key] = (time.time(), out)
        return out
    except Exception as e:
        return {"spikes": [], "error": str(e)}


# === ECONOMIC CALENDAR (start) ===
# Key US economic events for any month (YYYY-MM). Dates follow the official release
# patterns (FOMC dates are the Fed's published schedule). "Previous" and "Actual"
# values come live from FRED (St. Louis Fed) - free, no API key.
from datetime import date as _ed_date, timedelta as _ed_td
import calendar as _ed_cal
import csv as _ed_csv
import io as _ed_io
import urllib.request as _ed_req
from concurrent.futures import ThreadPoolExecutor as _EdPool

FOMC_DECISIONS = {  # second day of each meeting (decision at 2:00 PM ET)
    2026: ["01-28", "03-18", "04-29", "06-17", "07-29", "09-16", "10-28", "12-09"],
    2027: ["01-27", "03-17", "04-28", "06-09", "07-28", "09-15", "10-27", "12-08"],
}

ECO_INFO = {
    "nfp": ("Non-Farm Payrolls", "high", "employment",
            "How many jobs the US economy added last month (excluding farm workers). The single biggest monthly market mover: strong numbers can push yields and the dollar up and reduce the chance of Fed rate cuts; weak numbers do the opposite."),
    "unemp": ("Unemployment Rate", "high", "employment",
              "The share of people who want a job but can't find one. A rising rate signals a slowing economy and makes Fed rate cuts more likely; a falling rate signals strength."),
    "adp": ("ADP Employment Change", "medium", "employment",
            "Private-sector job gains measured by payroll company ADP, released two days before the official jobs report. Traders use it as an early hint for Non-Farm Payrolls."),
    "cpi": ("CPI Inflation (YoY)", "high", "inflation",
            "How much consumer prices rose compared with a year ago. The Fed targets about 2% inflation, so a hotter-than-expected CPI can push rate cuts further away and hit stocks, especially tech."),
    "core_cpi": ("Core CPI (YoY)", "high", "inflation",
                 "CPI without food and energy, which jump around a lot. The Fed watches core inflation closely because it shows the underlying trend."),
    "ppi": ("PPI - Producer Prices (MoM)", "medium", "inflation",
            "Change in prices businesses receive for their goods and services. Rising producer prices often feed through to consumer prices a few months later."),
    "retail": ("Retail Sales (MoM)", "high", "consumer",
               "Monthly change in what Americans spend in shops and online. Consumer spending is about two-thirds of the US economy, so this is a key growth signal."),
    "housing": ("Housing Starts", "low", "housing",
                "Number of new homes builders started (annual rate). A gauge of the housing market, which is very sensitive to mortgage rates."),
    "claims": ("Initial Jobless Claims", "medium", "employment",
               "How many people filed for unemployment benefits for the first time last week. A rising trend is an early warning of layoffs."),
    "ism_mfg": ("ISM Manufacturing PMI", "high", "pmi",
                "Survey of factory purchasing managers. Above 50 means manufacturing is growing, below 50 means it is shrinking."),
    "ism_svc": ("ISM Services PMI", "medium", "pmi",
                "Same survey for the services sector, which is most of the US economy. Above 50 = growing, below 50 = shrinking."),
    "cb_conf": ("CB Consumer Confidence", "medium", "consumer",
                "The Conference Board's survey of how confident households feel about the economy and jobs. Confident consumers tend to spend more."),
    "umich": ("UMich Consumer Sentiment (Prelim)", "medium", "consumer",
              "University of Michigan survey of consumer mood and inflation expectations. The Fed watches the inflation-expectations part closely."),
    "gdp": ("GDP Growth Rate (QoQ, annualised)", "high", "gdp",
            "How fast the US economy grew last quarter, at an annual rate. Two negative quarters in a row is a common definition of recession."),
    "pce": ("PCE Price Index (YoY)", "high", "inflation",
            "The Fed's preferred inflation measure - prices of everything consumers buy. It feeds directly into Fed rate decisions."),
    "core_pce": ("Core PCE Price Index (YoY)", "high", "inflation",
                 "PCE without food and energy. This is the exact number the Fed's 2% inflation target refers to."),
    "fomc_begin": ("FOMC Meeting Begins", "medium", "central_bank",
                   "First day of the Federal Reserve's two-day policy meeting. No announcement today - markets often trade cautiously ahead of the decision."),
    "fomc": ("FOMC Rate Decision", "high", "central_bank",
             "The Federal Reserve announces its interest-rate target, followed by the Fed Chair's press conference. The most important scheduled event for markets: rate moves affect borrowing costs, the dollar and stock valuations."),
    "fomc_min": ("FOMC Meeting Minutes", "medium", "central_bank",
                 "Detailed notes from the Fed's meeting three weeks earlier. Markets read them for hints about the next rate move."),
    "holiday": ("", "low", "holiday",
                "US stock markets (NYSE and Nasdaq) are closed today. No trading and usually no economic data releases."),
}

# FRED series for live Previous / Actual values
FRED_SERIES = {"nfp": "PAYEMS", "unemp": "UNRATE", "adp": "ADPMNUSNERSA", "cpi": "CPIAUCSL",
               "core_cpi": "CPILFESL", "ppi": "PPIFIS", "retail": "RSAFS", "housing": "HOUST",
               "claims": "ICSA", "umich": "UMCSENT", "gdp": "A191RL1Q225SBEA", "pce": "PCEPI",
               "core_pce": "PCEPILFE", "fomc": "DFEDTARU", "fomc_low": "DFEDTARL"}

_FRED_CACHE = {}
_FRED_TTL = 6 * 3600
_FRED_FAIL = {}          # series_id -> time of last failed download
_FRED_RETRY = 30 * 60   # wait 30 min before trying an unreachable FRED series again
_ECO_CACHE = {}
_ECO_TTL = 3 * 3600


def _fred_series(series_id):
    """{ 'YYYY-MM-DD': float } for a FRED series (cached)."""
    hit = _FRED_CACHE.get(series_id)
    if hit and time.time() - hit[0] < _FRED_TTL:
        return hit[1]
    fail = _FRED_FAIL.get(series_id)
    if fail and time.time() - fail < _FRED_RETRY:
        return hit[1] if hit else {}   # FRED was unreachable a moment ago - don't wait on it again yet
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        req = _ed_req.Request(url, headers={"User-Agent": "Mozilla/5.0 (StockMarketPro)"})
        with _ed_req.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", "ignore")
        rows = list(_ed_csv.reader(_ed_io.StringIO(text)))
        data = {}
        for r in rows[1:]:
            if len(r) >= 2:
                try:
                    data[r[0][:10]] = float(r[1])
                except ValueError:
                    continue
        if data:
            _FRED_CACHE[series_id] = (time.time(), data)
        return data
    except Exception as e:
        print(f"[eco] FRED {series_id}: could not load ({type(e).__name__})")
        _FRED_FAIL[series_id] = time.time()
        return hit[1] if hit else {}


def _ed_month_shift(y, m, k):
    m2 = m + k
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return y2, m2


def _ed_obs(y, m):
    return f"{y}-{m:02d}-01"


def _ed_holidays(y):
    def observed(d):
        if d.weekday() == 5:
            return d - _ed_td(days=1)
        if d.weekday() == 6:
            return d + _ed_td(days=1)
        return d

    def nth(m, wd, n):
        d = _ed_date(y, m, 1)
        d += _ed_td(days=(wd - d.weekday()) % 7)
        return d + _ed_td(weeks=n - 1)

    def last(m, wd):
        d = _ed_date(y, m, _ed_cal.monthrange(y, m)[1])
        return d - _ed_td(days=(d.weekday() - wd) % 7)

    a = y % 19; b = y // 100; c = y % 100; d_ = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d_ - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7; mm = (a + 11 * h + 22 * l) // 451
    easter = _ed_date(y, (h + l - 7 * mm + 114) // 31, ((h + l - 7 * mm + 114) % 31) + 1)
    return {
        observed(_ed_date(y, 1, 1)): "New Year's Day (Market Closed)",
        nth(1, 0, 3): "Martin Luther King Jr. Day (Market Closed)",
        nth(2, 0, 3): "Presidents' Day (Market Closed)",
        easter - _ed_td(days=2): "Good Friday (Market Closed)",
        last(5, 0): "Memorial Day (Market Closed)",
        observed(_ed_date(y, 6, 19)): "Juneteenth (Market Closed)",
        observed(_ed_date(y, 7, 4)): "Independence Day (Market Closed)",
        nth(9, 0, 1): "Labor Day (Market Closed)",
        nth(11, 3, 4): "Thanksgiving (Market Closed)",
        observed(_ed_date(y, 12, 25)): "Christmas Day (Market Closed)",
    }


def _build_eco_month(y, m):
    hol = {}
    for yy in (y - 1, y, y + 1):
        hol.update(_ed_holidays(yy))

    def bizday(d):
        return d.weekday() < 5 and d not in hol

    def nth_wd(wd, n):
        d = _ed_date(y, m, 1)
        d += _ed_td(days=(wd - d.weekday()) % 7)
        return d + _ed_td(weeks=n - 1)

    def last_wd(wd):
        d = _ed_date(y, m, _ed_cal.monthrange(y, m)[1])
        return d - _ed_td(days=(d.weekday() - wd) % 7)

    def nth_biz(n):
        d, c = _ed_date(y, m, 1), 0
        while True:
            if bizday(d):
                c += 1
                if c == n:
                    return d
            d += _ed_td(days=1)

    def on_or_after(day, wd=None):
        d = _ed_date(y, m, day)
        while (wd is not None and d.weekday() != wd) or not bizday(d):
            d += _ed_td(days=1)
        return d

    def biz_back(d):
        while not bizday(d):
            d -= _ed_td(days=1)
        return d

    ev = []  # (date, time, key, ref)

    nfp_day = biz_back(nth_wd(4, 1))
    ev += [(nth_biz(1), "10:00 AM ET", "ism_mfg", None),
           (nth_biz(3), "10:00 AM ET", "ism_svc", None),
           (biz_back(nfp_day - _ed_td(days=2)), "8:15 AM ET", "adp", ("m", -1)),
           (nfp_day, "8:30 AM ET", "nfp", ("m", -1)),
           (nfp_day, "8:30 AM ET", "unemp", ("m", -1))]
    # ADP comes out 2 days before NFP; next month's ADP can fall at the end of this month
    ny, nm = _ed_month_shift(y, m, 1)
    nd = _ed_date(ny, nm, 1)
    nd += _ed_td(days=(4 - nd.weekday()) % 7)
    nd = biz_back(nd)
    adp_next = biz_back(nd - _ed_td(days=2))
    if (adp_next.year, adp_next.month) == (y, m):
        ev.append((adp_next, "8:15 AM ET", "adp", ("m", 0)))
    cpi_day = on_or_after(10, 2)
    ev += [(cpi_day, "8:30 AM ET", "cpi", ("m", -1)),
           (cpi_day, "8:30 AM ET", "core_cpi", ("m", -1)),
           (on_or_after(cpi_day.day + 1), "8:30 AM ET", "ppi", ("m", -1)),
           (on_or_after(15), "8:30 AM ET", "retail", ("m", -1)),
           (on_or_after(17), "8:30 AM ET", "housing", ("m", -1)),
           (biz_back(nth_wd(4, 2)), "10:00 AM ET", "umich", ("m", 0)),
           (biz_back(last_wd(1)), "10:00 AM ET", "cb_conf", None),
           (biz_back(last_wd(4)), "8:30 AM ET", "pce", ("m", -1)),
           (biz_back(last_wd(4)), "8:30 AM ET", "core_pce", ("m", -1))]
    stage = {1: "Advance", 4: "Advance", 7: "Advance", 10: "Advance",
             2: "Second Estimate", 5: "Second Estimate", 8: "Second Estimate", 11: "Second Estimate"}.get(m, "Third Estimate")
    back = {"Advance": -3, "Second Estimate": -4, "Third Estimate": -5}[stage]
    ev.append((biz_back(last_wd(3)), "8:30 AM ET", "gdp", ("q", back, stage)))
    d = nth_wd(3, 1)
    while d.month == m:  # weekly jobless claims, every Thursday
        if bizday(d):
            ev.append((d, "8:30 AM ET", "claims", ("w", d - _ed_td(days=5))))
        d += _ed_td(days=7)

    for yy in (y - 1, y):
        for md in FOMC_DECISIONS.get(yy, []):
            dec = _ed_date(yy, int(md[:2]), int(md[3:]))
            if (dec.year, dec.month) == (y, m):
                ev.append((dec - _ed_td(days=1), "All day", "fomc_begin", None))
                ev.append((dec, "2:00 PM ET", "fomc", ("fed", dec)))
            mins = dec + _ed_td(days=21)
            if (mins.year, mins.month) == (y, m):
                ev.append((mins, "2:00 PM ET", "fomc_min", None))
    # FOMC "begins" can fall in the previous month's last day - handle next month's first decision too
    for md in FOMC_DECISIONS.get(y + (1 if m == 12 else 0), []):
        dec = _ed_date(y + (1 if m == 12 else 0), int(md[:2]), int(md[3:]))
        begin = dec - _ed_td(days=1)
        if (begin.year, begin.month) == (y, m) and (dec.year, dec.month) != (y, m):
            ev.append((begin, "All day", "fomc_begin", None))

    ev = [e for e in ev if (e[0].year, e[0].month) == (y, m)]
    holiday_rows = [(d, name) for d, name in hol.items() if (d.year, d.month) == (y, m)]

    # Live values from FRED (fetched in parallel, only what this month needs)
    keys = {k for _, _, k, ref in ev if ref and k in FRED_SERIES}
    if "fomc" in keys:
        keys.add("fomc_low")
    with _EdPool(max_workers=8) as pool:
        series = dict(zip(keys, pool.map(lambda k: _fred_series(FRED_SERIES[k]), keys)))

    today = _ed_date.today()

    def val(key, obs):
        return series.get(key, {}).get(obs)

    def fmt_value(key, ref):
        """Return (value for this release, value for the release before) as display strings."""
        kind = ref[0]
        if kind == "m":
            ry, rm = _ed_month_shift(y, m, ref[1])
            py, pm = _ed_month_shift(ry, rm, -1)
            cur, prev = (ry, rm), (py, pm)

            def one(yy, mm):
                o = _ed_obs(yy, mm)
                if key in ("cpi", "core_cpi", "pce", "core_pce"):
                    a, b = val(key, o), val(key, _ed_obs(yy - 1, mm))
                    return f"{(a / b - 1) * 100:.1f}%" if a and b else None
                if key in ("ppi", "retail"):
                    py2, pm2 = _ed_month_shift(yy, mm, -1)
                    a, b = val(key, o), val(key, _ed_obs(py2, pm2))
                    return f"{(a / b - 1) * 100:+.1f}%" if a and b else None
                if key in ("nfp", "adp"):
                    py2, pm2 = _ed_month_shift(yy, mm, -1)
                    a, b = val(key, o), val(key, _ed_obs(py2, pm2))
                    if a is None or b is None:
                        return None
                    diff = (a - b) if key == "nfp" else (a - b) / 1000
                    return f"{diff:+,.0f}K"
                if key == "unemp":
                    a = val(key, o)
                    return f"{a:.1f}%" if a is not None else None
                if key == "housing":
                    a = val(key, o)
                    return f"{a:,.0f}K" if a is not None else None
                if key == "umich":
                    a = val(key, o)
                    return f"{a:.1f}" if a is not None else None
                return None
            return one(*cur), one(*prev)
        if kind == "q":
            ry, rm = _ed_month_shift(y, m, ref[1])
            py, pm = _ed_month_shift(ry, rm, -3)
            f = lambda yy, mm: (f"{val('gdp', _ed_obs(yy, mm)):.1f}%" if val('gdp', _ed_obs(yy, mm)) is not None else None)
            return f(ry, rm), f(py, pm)
        if kind == "w":
            sat = ref[1]
            f = lambda d: (f"{val('claims', d.isoformat()) / 1000:,.0f}K" if val('claims', d.isoformat()) is not None else None)
            return f(sat), f(sat - _ed_td(days=7))
        if kind == "fed":
            dec = ref[1]
            up, lo = series.get("fomc", {}), series.get("fomc_low", {})

            def rate_on(d, after):
                for i in range(0, 10 if after else 400):
                    dd = (d + _ed_td(days=i)) if after else (d - _ed_td(days=i))
                    k = dd.isoformat()
                    if k in up and k in lo:
                        return f"{lo[k]:.2f}-{up[k]:.2f}%"
                return None
            return rate_on(dec + _ed_td(days=1), True), rate_on(dec - _ed_td(days=1), False)
        return None, None

    out = []
    for d, t, key, ref in ev:
        name, impact, cat, desc = ECO_INFO[key]
        if key == "gdp":
            name = f"GDP Growth Rate QoQ ({ref[2]})"
        actual = previous = None
        if ref:
            cur, prev = fmt_value(key, ref)
            previous = prev
            if d <= today:
                actual = cur
        out.append({
            "date": d.isoformat(), "time": t, "event": name, "country": "US",
            "impact": impact, "category": cat, "description": desc,
            "actual": actual or "\u2014", "estimate": "\u2014", "previous": previous or "\u2014",
            "result": None, "unit": "", "key": key,
        })
    for d, name in holiday_rows:
        out.append({
            "date": d.isoformat(), "time": "All day", "event": name, "country": "US",
            "impact": "low", "category": "holiday", "description": ECO_INFO["holiday"][3],
            "actual": "\u2014", "estimate": "\u2014", "previous": "\u2014", "result": None, "unit": "", "key": "holiday",
        })
    order = {"high": 0, "medium": 1, "low": 2}
    tkey = lambda s: (0, 0) if s == "All day" else (1, int(s.split(":")[0]) % 12 + (12 if "PM" in s else 0))
    out.sort(key=lambda e: (e["date"], tkey(e["time"]), order[e["impact"]]))
    return out


@app.get("/api/economic-calendar")
def get_economic_calendar(month: str = ""):
    """Economic events for a month (YYYY-MM). Defaults to the current month."""
    try:
        if month:
            y, m = (int(x) for x in month.split("-"))
            if not (1 <= m <= 12 and 2000 <= y <= 2100):
                raise ValueError
        else:
            t = _ed_date.today()
            y, m = t.year, t.month
    except Exception:
        return {"events": [], "error": "Invalid month - use YYYY-MM, e.g. 2026-10"}
    key = f"{y}-{m:02d}"
    hit = _ECO_CACHE.get(key)
    if hit and time.time() - hit[0] < _ECO_TTL:
        return {"events": hit[1], "month": key}
    try:
        events = _build_eco_month(y, m)
        _ECO_CACHE[key] = (time.time(), events)
        return {"events": events, "month": key}
    except Exception as e:
        print(f"Economic calendar error: {e}")
        return {"events": [], "error": "Could not build the economic calendar right now."}
# === ECONOMIC CALENDAR (end) ===


# === DIVIDENDS (start) ===
# Feeds the Dividends tab in the stock popup: verdict banner, 5 tiles,
# annual bar chart, recent payments table.
_DIV_CACHE = {}
_DIV_TTL = 3600  # seconds


def _div_safe(v):
    try:
        f = float(v)
        return f if f == f else None  # drop NaN
    except Exception:
        return None


def _build_dividends(t):
    tk = yf.Ticker(t)
    try:
        info = tk.info or {}
    except Exception:
        info = {}
    try:
        divs = tk.dividends
    except Exception:
        divs = None

    payments = []
    if divs is not None and len(divs):
        for dt, amt in divs.items():
            a = _div_safe(amt)
            if a and a > 0:
                payments.append((dt.strftime("%Y-%m-%d"), a))
    payments.sort()

    today = datetime.now()
    year_ago = datetime.fromtimestamp(today.timestamp() - 365 * 86400).strftime("%Y-%m-%d")
    recent_12m = [a for d, a in payments if d >= year_ago]
    # Still paying? (a payment within roughly the last 15 months)
    active_cutoff = datetime.fromtimestamp(today.timestamp() - 450 * 86400).strftime("%Y-%m-%d")
    active = bool(payments) and payments[-1][0] >= active_cutoff

    if not payments or not active:
        return {"ticker": t, "pays_dividend": False,
                "verdict": f"{t} does not currently pay a dividend.", "verdict_type": "none"}

    # Annual totals for complete calendar years (the current year is still in progress)
    by_year = {}
    for d, a in payments:
        y = int(d[:4])
        by_year[y] = by_year.get(y, 0.0) + a
    full_years = sorted(y for y in by_year if y < today.year)
    annual_history = [{"year": y, "total": round(by_year[y], 4)} for y in full_years][-10:]

    # Consecutive years of increases, counting back from the last full year
    streak = 0
    for i in range(len(full_years) - 1, 0, -1):
        y, prev = full_years[i], full_years[i - 1]
        if y - prev != 1 or by_year[y] <= by_year[prev] * 1.001:
            break
        streak += 1

    n12 = len(recent_12m)
    frequency = ("Monthly" if n12 >= 10 else "Quarterly" if n12 >= 3 else
                 "Semi-annual" if n12 == 2 else "Annual" if n12 == 1 else "Irregular")

    annual_rate = _div_safe(info.get("dividendRate")) or (round(sum(recent_12m), 4) if recent_12m else None)
    price = (_div_safe(info.get("currentPrice")) or _div_safe(info.get("regularMarketPrice"))
             or _div_safe(info.get("previousClose")))
    if not price:
        try:
            h = tk.history(period="5d")
            price = float(h["Close"].iloc[-1]) if not h.empty else None
        except Exception:
            price = None
    yield_pct = round(annual_rate / price * 100, 2) if annual_rate and price else None
    five_yr = _div_safe(info.get("fiveYearAvgDividendYield"))  # Yahoo already gives this in %
    payout = _div_safe(info.get("payoutRatio"))
    payout_pct = round(payout * 100, 1) if payout is not None and payout > 0 else None

    ex_ts = info.get("exDividendDate")
    ex_date = None
    if ex_ts:
        try:
            ex_date = datetime.fromtimestamp(int(ex_ts)).strftime("%Y-%m-%d")
        except Exception:
            ex_date = None
    if not ex_date:
        ex_date = payments[-1][0]

    # Change in the last full year vs the year before
    last_change = None
    if len(full_years) >= 2 and full_years[-1] - full_years[-2] == 1 and by_year[full_years[-2]] > 0:
        last_change = (by_year[full_years[-1]] - by_year[full_years[-2]]) / by_year[full_years[-2]] * 100

    # Plain-English verdict
    amt = f"${annual_rate:.2f} per share a year" if annual_rate else "a dividend"
    yld = f" (a {yield_pct:.2f}% yield)" if yield_pct else ""
    base = f"{t} pays {amt}{yld}, {frequency.lower()}."
    if payout_pct is not None and payout_pct > 100:
        vtype = "warning"
        why = f" It is paying out {payout_pct:.0f}% of its profits - more than it earns - so the dividend could be cut if profits don't recover."
    elif last_change is not None and last_change < -10:
        vtype = "warning"
        why = f" It cut its dividend by {abs(last_change):.0f}% in {full_years[-1]}, which usually signals pressure on profits or cash flow."
    elif streak >= 10:
        vtype = "strong"
        why = f" Dividend champion - it has raised its dividend {streak} years in a row"
        why += f" and pays out {payout_pct:.0f}% of profits, so the payment looks well covered." if payout_pct is not None and payout_pct < 80 else "."
    elif streak >= 3:
        vtype = "good"
        why = f" Reliable grower - it has raised its dividend {streak} years in a row"
        why += f" while paying out {payout_pct:.0f}% of profits." if payout_pct is not None else "."
    elif last_change is not None and abs(last_change) <= 2:
        vtype = "flat"
        why = " The dividend has stayed about the same as last year - steady income, but not growing."
    else:
        vtype = "ok"
        why = " It pays a regular dividend"
        why += f" and paid out {payout_pct:.0f}% of profits." if payout_pct is not None else "."
        if payout_pct is not None and payout_pct >= 80:
            why = f" It pays a regular dividend, but uses {payout_pct:.0f}% of profits to do it, which leaves little room if earnings fall."

    recent_payments = [{"date": d, "amount": round(a, 4)} for d, a in reversed(payments[-8:])]

    return {
        "ticker": t,
        "company": info.get("longName") or info.get("shortName") or t,
        "pays_dividend": True,
        "verdict": base + why,
        "verdict_type": vtype,
        "streak_years": streak,
        "annual_rate": round(annual_rate, 4) if annual_rate else None,
        "yield_pct": yield_pct,
        "five_yr_avg_yield": round(five_yr, 2) if five_yr else None,
        "payout_ratio": payout_pct,
        "ex_date": ex_date,
        "frequency": frequency,
        "annual_history": annual_history,
        "recent_payments": recent_payments,
    }


@app.get("/api/stocks/{ticker}/dividends")
def get_stock_dividends(ticker: str):
    t = ticker.strip().upper()
    hit = _DIV_CACHE.get(t)
    if hit and time.time() - hit[0] < _DIV_TTL:
        return hit[1]
    try:
        data = _build_dividends(t)
        _DIV_CACHE[t] = (time.time(), data)
        return data
    except Exception as e:
        print(f"Dividends error for {t}: {e}")
        return {"ticker": t, "error": str(e)}
# === DIVIDENDS (end) ===


# === PORTFOLIO & TRANSACTIONS (start) ===
# Reads the portfolio / transactions tables (quantity, avg_buy_price, buy_date ...)
# and returns the fields the Portfolio and Transactions tabs use.
def _pf_db():
    return get_db()      # the one connection path (see get_db): same settings, and safe when many requests connect at once


def _pf_rows(sql, params=None):
    conn = _pf_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cur.close()
    conn.close()
    out = []
    for row in rows:
        r = {}
        for k, v in row.items():
            if v is None:
                r[k] = None
            elif hasattr(v, "isoformat"):
                r[k] = str(v)[:10]
            elif hasattr(v, "__float__") and not isinstance(v, (int, bool)):
                r[k] = float(v)
            else:
                r[k] = v
        out.append(r)
    return out


# GET /api/portfolio, /api/portfolio/summary, /api/transactions and /api/transactions/summary are answered by the
# account-aware versions earlier in this file. They return every field this tab reads (and more), for the signed-in
# person only. The two forms below keep their original URLs and replies, but save through the same validated,
# per-account code as POST /api/portfolio and POST /api/transactions.
@app.post("/api/portfolio/add")
def add_portfolio(ticker: str, quantity: float, avg_buy_price: float, buy_date: str, user_id: int = Depends(current_user_id)):
    try:
        result = portfolio.add_holding(user_id, ticker, quantity, avg_buy_price, buy_date)
        return {"success": True, "message": result["message"]}
    except portfolio.PortfolioError as e:
        return {"success": False, "message": e.message}
    except Exception:
        log.exception("Add holding failed")
        return {"success": False, "message": "Could not add: something went wrong saving that."}


@app.post("/api/transactions/add")
def add_transaction(ticker: str, transaction_type: str, quantity: float, price: float, transaction_date: str, notes: str = "",
                    user_id: int = Depends(current_user_id)):
    ttype = transaction_type.strip().upper()
    if ttype not in ("BUY", "SELL"):
        return {"success": False, "message": "Type must be BUY or SELL"}
    try:
        result = portfolio.add_transaction(user_id, ticker, ttype, quantity, price, transaction_date, notes)
        return {"success": True, "message": result["message"]}
    except portfolio.PortfolioError as e:
        return {"success": False, "message": e.message}
    except Exception:
        log.exception("Add transaction failed")
        return {"success": False, "message": "Could not add: something went wrong saving that."}
# === PORTFOLIO & TRANSACTIONS (end) ===


# === PORTFOLIO vs S&P 500 BENCHMARK (start) ===
@app.get("/api/portfolio/benchmark")
def portfolio_benchmark(user_id: int = Depends(current_user_id)):
    """Compare portfolio performance vs S&P 500 (SPY)."""
    try:
        import yfinance as yf
        import pandas as pd
        from datetime import date, timedelta

        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM portfolio WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        tickers = sorted({str(r.get("ticker") or "").upper() for r in rows if r.get("ticker")})
        live = {}
        if tickers:
            ph = ",".join(["%s"] * len(tickers))
            cur.execute(
                f"SELECT ticker, current_price, company_name, sector FROM stocks_live WHERE ticker IN ({ph})",
                tickers,
            )
            for r in cur.fetchall():
                live[str(r["ticker"]).upper()] = r
        cur.close()
        conn.close()

        def pick(row, *names):
            for n in names:
                if n in row and row[n] is not None:
                    return row[n]
            return None

        holdings = []
        for r in rows:
            t = str(r.get("ticker") or "").upper()
            qty = float(pick(r, "quantity", "shares", "qty") or 0)
            avg = float(pick(r, "avg_buy_price", "avg_cost", "buy_price", "avg_price") or 0)
            if not t or qty <= 0 or avg <= 0:
                continue
            bd = pick(r, "buy_date", "date_added", "purchase_date", "added_at", "added_on", "created_at")
            bd = str(bd)[:10] if bd else None
            info = live.get(t, {})
            holdings.append({
                "ticker": t, "quantity": qty, "avg_buy_price": avg, "buy_date": bd,
                "current_price": float(info["current_price"]) if info.get("current_price") else None,
                "company_name": info.get("company_name") or t,
                "sector": info.get("sector") or "",
            })

        if not holdings:
            return {"error": "No holdings found"}

        dates = [h["buy_date"] for h in holdings if h["buy_date"]]
        start_date = min(dates) if dates else (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
        for h in holdings:
            if not h["buy_date"]:
                h["buy_date"] = start_date

        def clean(hist):
            s = hist["Close"].copy()
            idx = s.index
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            s.index = idx.normalize()
            return s[~s.index.duplicated(keep="last")]

        # S&P 500 via SPY
        spy_hist = yf.Ticker("SPY").history(start=start_date)
        if spy_hist.empty:
            return {"error": "Could not fetch S&P 500 data"}
        spy = clean(spy_hist)
        spy_start = float(spy.iloc[0])
        spy_current = float(spy.iloc[-1])
        spy_return = round((spy_current - spy_start) / spy_start * 100, 2)

        # Real daily prices for every holding, lined up on SPY trading days
        closes = {}
        for t in sorted({h["ticker"] for h in holdings}):
            try:
                hist = yf.Ticker(t).history(start=start_date)
                if not hist.empty:
                    closes[t] = clean(hist).reindex(spy.index).ffill()
            except Exception:
                continue

        total_invested = 0.0
        total_current = 0.0
        holdings_data = []
        for h in holdings:
            price = h["current_price"]
            if price is None and h["ticker"] in closes:
                last = closes[h["ticker"]].dropna()
                price = float(last.iloc[-1]) if len(last) else None
            if price is None:
                continue
            invested = h["quantity"] * h["avg_buy_price"]
            current_val = h["quantity"] * price
            total_invested += invested
            total_current += current_val
            stock_return = round((price - h["avg_buy_price"]) / h["avg_buy_price"] * 100, 2)

            # What SPY did from this stock's own buy date
            try:
                spy_at_buy = float(spy[spy.index >= pd.Timestamp(h["buy_date"])].iloc[0])
                spy_stock_return = round((spy_current - spy_at_buy) / spy_at_buy * 100, 2)
            except Exception:
                spy_stock_return = spy_return

            holdings_data.append({
                "ticker": h["ticker"],
                "company_name": h["company_name"],
                "sector": h["sector"],
                "stock_return": stock_return,
                "spy_return": spy_stock_return,
                "difference": round(stock_return - spy_stock_return, 2),
                "beating": stock_return > spy_stock_return,
                "invested": round(invested, 2),
                "current_value": round(current_val, 2),
            })

        portfolio_return = round((total_current - total_invested) / total_invested * 100, 2) if total_invested > 0 else 0
        difference = round(portfolio_return - spy_return, 2)

        # Daily portfolio line: on each day, value of the holdings bought by then
        # vs what was paid for those holdings
        combined_chart = []
        spy_chart = []
        for d in spy.index:
            ds = d.strftime("%Y-%m-%d")
            s_ret = round((float(spy[d]) - spy_start) / spy_start * 100, 2)
            spy_chart.append({"date": ds, "spy_return": s_ret})
            value = cost = 0.0
            for h in holdings:
                if h["buy_date"] > ds or h["ticker"] not in closes:
                    continue
                px = closes[h["ticker"]].get(d)
                if px is None or pd.isna(px):
                    continue
                value += h["quantity"] * float(px)
                cost += h["quantity"] * h["avg_buy_price"]
            if cost > 0:
                combined_chart.append({
                    "date": ds,
                    "portfolio_return": round((value - cost) / cost * 100, 2),
                    "spy_return": s_ret,
                })

        return {
            "portfolio_return": portfolio_return,
            "spy_return": spy_return,
            "difference": difference,
            "beating_market": portfolio_return > spy_return,
            "total_invested": round(total_invested, 2),
            "total_current": round(total_current, 2),
            "start_date": start_date,
            "holdings": holdings_data,
            "spy_chart": spy_chart[-252:],
            "combined_chart": combined_chart[-252:],
        }
    except Exception as e:
        print(f"Benchmark error: {e}")
        return {"error": str(e)}
# === PORTFOLIO vs S&P 500 BENCHMARK (end) ===


# === PRICE ALERTS & NOTIFICATIONS (start) ===
# Price alerts (set / list / delete / check) and the notification feed used by
# the dashboard for desktop notifications:
#   - price alert set + price alert reached
#   - important market news
#   - economic events: today's list, 15-minute reminder, result released
from datetime import timezone as _al_tz, timedelta as _al_td

_AL_COLS = {
    "company_name": "VARCHAR(200)",
    "condition_type": "VARCHAR(10) NOT NULL DEFAULT 'above'",
    "price_at_creation": "FLOAT",
    "triggered": "TINYINT(1) NOT NULL DEFAULT 0",
    "triggered_price": "FLOAT",
    "triggered_at": "DATETIME",
    "created_at": "DATETIME",
    "user_id": "INT NULL",          # whose alert it is; alerts made before accounts have none until claimed
}
_AL_STATE = {"ready": False, "cols": set()}


def _al_db():
    return get_db()


def _al_setup():
    """Create price_alerts if missing and add any missing columns (works with older table layouts)."""
    if _AL_STATE["ready"]:
        return
    db = _al_db()
    cur = db.cursor()
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS price_alerts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticker VARCHAR(20) NOT NULL,
            company_name VARCHAR(200),
            condition_type VARCHAR(10) NOT NULL DEFAULT 'above',
            target_price FLOAT NOT NULL,
            price_at_creation FLOAT,
            triggered TINYINT(1) NOT NULL DEFAULT 0,
            triggered_price FLOAT,
            triggered_at DATETIME,
            created_at DATETIME
        )""")
        cur.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                       WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'price_alerts'""")
        have = {r[0].lower() for r in cur.fetchall()}
        for col, typ in _AL_COLS.items():
            if col not in have:
                cur.execute(f"ALTER TABLE price_alerts ADD COLUMN {col} {typ}")
                have.add(col)
                if col == "condition_type" and "alert_type" in have:
                    # older layout stored the direction in alert_type
                    cur.execute("UPDATE price_alerts SET condition_type = LOWER(alert_type) "
                                "WHERE LOWER(alert_type) IN ('above','below')")
        cur.execute("UPDATE price_alerts SET triggered = 0 WHERE triggered IS NULL")
        db.commit()
        _AL_STATE["cols"] = have
        _AL_STATE["ready"] = True
    finally:
        cur.close()
        db.close()


def _al_live_prices(tickers):
    """{ticker: (price, last_updated)} from stocks_live (kept fresh by scheduler.py)."""
    if not tickers:
        return {}
    db = _al_db()
    cur = db.cursor()
    try:
        ph = ",".join(["%s"] * len(tickers))
        cur.execute(f"SELECT ticker, current_price, last_updated, company_name FROM stocks_live WHERE ticker IN ({ph})",
                    list(tickers))
        return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    finally:
        cur.close()
        db.close()


def _al_price_now(ticker, live):
    """Latest price: stocks_live if updated in the last 20 minutes, otherwise ask Yahoo directly."""
    row = live.get(ticker)
    if row and row[0] is not None and row[1] is not None:
        try:
            if (datetime.now() - row[1]).total_seconds() < 20 * 60:
                return float(row[0])
        except Exception:
            pass
    try:
        p = yf.Ticker(ticker).fast_info.last_price
        if p and p == p:
            return float(p)
    except Exception:
        pass
    return float(row[0]) if row and row[0] is not None else None


def _al_row(r, live=None):
    price = None
    if live is not None and r["ticker"] in live and live[r["ticker"]][0] is not None:
        price = float(live[r["ticker"]][0])
    tgt = float(r["target_price"])
    return {
        "id": r["id"],
        "ticker": r["ticker"],
        "company_name": r.get("company_name") or (live.get(r["ticker"], (None, None, None))[2] if live else None) or r["ticker"],
        "condition_type": (r.get("condition_type") or "above").lower(),
        "target_price": round(tgt, 4),
        "price_at_creation": r.get("price_at_creation"),
        "current_price": price,
        "distance_pct": round((tgt - price) / price * 100, 2) if price else None,
        "triggered": bool(r.get("triggered")),
        "triggered_price": r.get("triggered_price"),
        "triggered_at": str(r["triggered_at"]) if r.get("triggered_at") else None,
        "created_at": str(r["created_at"]) if r.get("created_at") else None,
    }


@app.get("/api/alerts")
def get_alerts(user_id: int = Depends(current_user_id)):
    """All price alerts, newest first, with today's price and distance to target."""
    try:
        _al_setup()
        db = _al_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM price_alerts WHERE user_id = %s ORDER BY triggered ASC, id DESC", (user_id,))
        rows = cur.fetchall()
        cur.close()
        db.close()
        live = _al_live_prices({r["ticker"] for r in rows})
        return {"alerts": [_al_row(r, live) for r in rows]}
    except Exception as e:
        print(f"[alerts] list error: {e}")
        return {"alerts": [], "error": str(e)}


@app.post("/api/alerts/create")
def create_alert(payload: dict = Body(...), user_id: int = Depends(current_user_id)):
    """Body: {ticker, target_price, condition_type: 'above'|'below'}."""
    try:
        _al_setup()
        ticker = str(payload.get("ticker") or "").strip().upper()
        cond = str(payload.get("condition_type") or "above").strip().lower()
        try:
            target = float(payload.get("target_price"))
        except (TypeError, ValueError):
            target = 0.0
        if not ticker:
            return {"status": "error", "success": False, "message": "No stock selected."}
        if cond not in ("above", "below"):
            return {"status": "error", "success": False, "message": "Condition must be 'above' or 'below'."}
        if not target or target <= 0:
            return {"status": "error", "success": False, "message": "Please enter a valid target price."}

        live = _al_live_prices({ticker})
        if ticker not in live:
            return {"status": "error", "success": False, "message": f"{ticker} is not in the database."}
        price = _al_price_now(ticker, live)
        company = live[ticker][2] or payload.get("company_name") or ticker
        if price:
            if cond == "above" and target <= price:
                return {"status": "error", "success": False,
                        "message": f"{ticker} is already at ${price:,.2f}. For an 'above' alert, pick a price higher than that."}
            if cond == "below" and target >= price:
                return {"status": "error", "success": False,
                        "message": f"{ticker} is already at ${price:,.2f}. For a 'below' alert, pick a price lower than that."}

        cols = ["ticker", "company_name", "condition_type", "target_price", "price_at_creation", "triggered", "created_at", "user_id"]
        vals = [ticker, company, cond, target, price, 0, datetime.now(), user_id]
        if "alert_type" in _AL_STATE["cols"]:
            cols.append("alert_type"); vals.append(cond)
        if "current_price" in _AL_STATE["cols"]:
            cols.append("current_price"); vals.append(price)
        db = _al_db()
        cur = db.cursor()
        cur.execute(f"INSERT INTO price_alerts ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals)
        db.commit()
        new_id = cur.lastrowid
        cur.close()
        db.close()
        alert = {"id": new_id, "ticker": ticker, "company_name": company, "condition_type": cond,
                 "target_price": target, "price_at_creation": price, "current_price": price}
        word = "rises above" if cond == "above" else "falls below"
        return {"status": "success", "success": True, "id": new_id, "alert": alert,
                "message": f"You'll be notified when {ticker} {word} ${target:,.2f}"
                           + (f" (now ${price:,.2f})" if price else "")}
    except Exception as e:
        print(f"[alerts] create error: {e}")
        return {"status": "error", "success": False, "message": "Could not save the alert: " + str(e)}


@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: int, user_id: int = Depends(current_user_id)):
    try:
        _al_setup()
        db = _al_db()
        cur = db.cursor()
        cur.execute("DELETE FROM price_alerts WHERE id=%s AND user_id=%s", (alert_id, user_id))
        db.commit()
        n = cur.rowcount
        cur.close()
        db.close()
        return {"status": "success", "success": True, "deleted": n}
    except Exception as e:
        return {"status": "error", "success": False, "message": str(e)}


@app.get("/api/alerts/check")
def check_alerts(user_id: int = Depends(current_user_id)):
    """Checks every active alert against the latest price. Each alert fires exactly once."""
    try:
        _al_setup()
        db = _al_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM price_alerts WHERE triggered = 0 AND user_id = %s", (user_id,))
        rows = cur.fetchall()
        cur.close()
        db.close()
        if not rows:
            return {"triggered": [], "checked": 0}
        live = _al_live_prices({r["ticker"] for r in rows})
        fired = []
        db = _al_db()
        cur = db.cursor()
        for r in rows:
            price = _al_price_now(r["ticker"], live)
            if price is None:
                continue
            cond = (r.get("condition_type") or "above").lower()
            tgt = float(r["target_price"])
            if (cond == "above" and price >= tgt) or (cond == "below" and price <= tgt):
                # only the first caller flips triggered 0 -> 1, so two open tabs can't both notify
                cur.execute("UPDATE price_alerts SET triggered=1, triggered_price=%s, triggered_at=%s "
                            "WHERE id=%s AND triggered=0 AND user_id=%s", (price, datetime.now(), r["id"], user_id))
                if cur.rowcount == 1:
                    item = _al_row(r, live)
                    item.update({"current_price": round(price, 4), "triggered": True, "distance_pct": None,
                                 "triggered_price": round(price, 4),
                                 "triggered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    fired.append(item)
        db.commit()
        cur.close()
        db.close()
        return {"triggered": fired, "checked": len(rows)}
    except Exception as e:
        print(f"[alerts] check error: {e}")
        return {"triggered": [], "error": str(e)}


# ---- notification feed: important market news + economic events ----
_NT_KEYWORDS = [
    # central banks & rates
    r"\bfed\b", r"federal reserve", r"\bpowell\b", r"\bfomc\b", r"rate cut", r"rate hike", r"interest rate",
    r"\becb\b", r"bank of england", r"bank of japan", r"central bank", r"treasury yield", r"bond yield",
    # macro data
    r"inflation", r"\bcpi\b", r"\bpce\b", r"jobs report", r"payrolls", r"unemployment", r"\bgdp\b",
    r"recession", r"retail sales", r"jobless claims",
    # market moves
    r"s&p 500", r"\bdow\b", r"nasdaq", r"wall street", r"stock market", r"stocks? (fall|fell|drop|slide|sink|tumble|plunge|surge|soar|jump|rally|rise|climb)",
    r"sell-?off", r"\bcrash", r"record high", r"all-time high", r"bear market", r"correction", r"\bvix\b", r"volatility",
    # policy & world
    r"tariff", r"trade war", r"sanction", r"shutdown", r"debt ceiling", r"stimulus",
    # commodities
    r"oil price", r"crude", r"\bopec", r"gold price", r"gold (hits|record|surges|falls)",
    # earnings season
    r"earnings season",
]
_NT_RX = _nu_re.compile("|".join(_NT_KEYWORDS), _nu_re.I)
_NT_CATS = {"markets", "economy", "global", "commodities"}
_NT_EVENT_REFRESH = {}   # event id -> last forced refresh time (for release results)


def _nt_id(*parts):
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _nt_ny_offset(utc_dt):
    y = utc_dt.year
    mar = datetime(y, 3, 1)
    start = mar + _al_td(days=(6 - mar.weekday()) % 7 + 7, hours=7)       # 2nd Sun Mar, 2am EST
    nov = datetime(y, 11, 1)
    end = nov + _al_td(days=(6 - nov.weekday()) % 7, hours=6)             # 1st Sun Nov, 2am EDT
    naive = utc_dt.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


def _nt_now_ny():
    u = datetime.now(_al_tz.utc)
    return (u + _al_td(hours=_nt_ny_offset(u))).replace(tzinfo=None)


def _nt_release_ts(date_str, time_str):
    """'2026-10-15' + '8:30 AM ET' -> unix seconds (UTC). 'All day' -> 9:30 AM ET."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        t = (time_str or "").replace("ET", "").strip()
        if not t or t.lower().startswith("all"):
            hh, mm = 9, 30
        else:
            tt = datetime.strptime(t, "%I:%M %p")
            hh, mm = tt.hour, tt.minute
        ny = d.replace(hour=hh, minute=mm)
        guess = ny + _al_td(hours=5)
        off = _nt_ny_offset(guess)
        return int((ny - _al_td(hours=off)).replace(tzinfo=_al_tz.utc).timestamp())
    except Exception:
        return None


def _nt_market_news():
    try:
        items = _nu_cached("world", _build_world_news) or []
    except Exception:
        items = []
    cutoff = time.time() - 6 * 3600
    out = []
    for a in items:
        if a.get("category") not in _NT_CATS:
            continue
        if (a.get("published") or 0) < cutoff:
            continue
        title = a.get("title") or ""
        if not _NT_RX.search(title):
            continue
        out.append({"id": "n" + _nt_id(_nu_re.sub(r"[^a-z0-9]", "", title.lower())[:80]),
                    "title": title, "source": a.get("source"), "url": a.get("url"),
                    "published": a.get("published"), "category": a.get("category")})
    return out[:15]


def _nt_events():
    now_ny = _nt_now_ny()
    today = now_ny.date().isoformat()
    month = now_ny.strftime("%Y-%m")
    data = get_economic_calendar(month)
    events = data.get("events") or []
    now_ts = time.time()

    # Results: once a release time has passed and the value is still missing, refresh FRED
    # every 15 minutes (the normal cache is hours long) so the result shows up quickly.
    need = []
    for e in events:
        if e.get("category") == "holiday" or e.get("actual") not in (None, "", "\u2014"):
            continue
        key = e.get("key")
        rts = _nt_release_ts(e["date"], e.get("time"))
        if key in FRED_SERIES and rts and 0 < now_ts - rts < 36 * 3600 and e["date"] <= today:
            eid = _nt_id(e["date"], e["event"])
            if now_ts - _NT_EVENT_REFRESH.get(eid, 0) > 15 * 60:
                _NT_EVENT_REFRESH[eid] = now_ts
                need.append(key)
    if need:
        for k in set(need) | ({"fomc_low"} if "fomc" in need else set()):
            _FRED_CACHE.pop(FRED_SERIES.get(k), None)
        _ECO_CACHE.pop(month, None)
        events = (get_economic_calendar(month).get("events") or events)

    out = []
    for e in events:
        if e.get("impact") not in ("high", "medium") and e.get("category") != "holiday":
            continue
        rts = _nt_release_ts(e["date"], e.get("time"))
        actual = e.get("actual")
        has_actual = actual not in (None, "", "\u2014")
        recent = rts is not None and -86400 < now_ts - rts < 36 * 3600
        if e["date"] != today and not (has_actual and recent):
            continue
        out.append({
            "id": "e" + _nt_id(e["date"], e["event"]),
            "date": e["date"], "time_et": e.get("time"), "release_ts": rts,
            "event": e["event"], "impact": e.get("impact"), "category": e.get("category"),
            "actual": actual if has_actual else None,
            "previous": e.get("previous") if e.get("previous") not in (None, "", "\u2014") else None,
            "is_today": e["date"] == today,
        })
    return out, today


@app.get("/api/notifications/feed")
def notifications_feed():
    """Candidates for desktop notifications. The dashboard remembers what it already showed."""
    try:
        news = _nt_market_news()
    except Exception as e:
        print(f"[notify] news error: {e}")
        news = []
    try:
        events, today = _nt_events()
    except Exception as e:
        print(f"[notify] events error: {e}")
        events, today = [], _nt_now_ny().date().isoformat()
    return {"server_time": int(time.time()), "today_ny": today, "news": news, "events": events}
# === PRICE ALERTS & NOTIFICATIONS (end) ===


if __name__ == "__main__":
    import uvicorn
    print("="*60)
    print("  STOCK MARKET PRO  API Server")
    print("  http://localhost:8000")
    print("  http://localhost:8000/docs")
    print("="*60)
    uvicorn.run(app, host="0.0.0.0", port=8000)

# -------------------------------------------------------------
#  MISSING ENDPOINTS  added to fix 404s
# -------------------------------------------------------------

# -- 1. WORLD NEWS --------------------------------------------
# (world news moved above "if __name__" - see LIVE NEWS block)

# -- 2. DIVIDENDS ----------------------------------------------
# (dividends moved above "if __name__" - see DIVIDENDS block)


# -- 3. ECONOMIC CALENDAR --------------------------------------
# (moved above "if __name__" - see ECONOMIC CALENDAR block)


# -- 4. PORTFOLIO ----------------------------------------------
# (An old get_db() and ensure_portfolio_tables() used to live here. On Render the app is
#  imported, so code below "if __name__" DOES run there - that old get_db() replaced the
#  real one with a version that ignored DB_PORT/DB_SSL, and every stock query timed out.
#  Removed. Tables are created by the PORTFOLIO and PRICE ALERTS blocks above.)

# (portfolio endpoints moved above "if __name__" - see PORTFOLIO & TRANSACTIONS block)

# -- 5. TRANSACTIONS -------------------------------------------
# (transaction endpoints moved above "if __name__" - see PORTFOLIO & TRANSACTIONS block)

# -- 6. PRICE ALERTS -------------------------------------------
# (moved above "if __name__" - see PRICE ALERTS & NOTIFICATIONS block)



