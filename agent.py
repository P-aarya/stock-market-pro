import os
from dotenv import load_dotenv
load_dotenv()
from groq import Groq
from screener import screen_stocks, get_stock_snapshot, get_top_movers, get_sector_summary, get_trending_stocks, find_similar_stocks
import json
import re

# Groq API client
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ─────────────────────────────────────────
# INTENT CLASSIFIER
# Detects what the user is actually asking for
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# COMPANY NAME → TICKER MAPPING
# ─────────────────────────────────────────
COMPANY_MAP = {
    "apple": "AAPL", "tesla": "TSLA", "nvidia": "NVDA", "microsoft": "MSFT",
    "amazon": "AMZN", "google": "GOOGL", "alphabet": "GOOGL", "meta": "META",
    "facebook": "META", "netflix": "NFLX", "jpmorgan": "JPM", "jp morgan": "JPM",
    "johnson": "JNJ", "visa": "V", "exxon": "XOM", "berkshire": "BRK-B",
    "walmart": "WMT", "disney": "DIS", "coca cola": "KO", "coca-cola": "KO",
    "mcdonalds": "MCD", "mcdonald's": "MCD", "boeing": "BA", "intel": "INTC",
    "amd": "AMD", "advanced micro": "AMD", "salesforce": "CRM", "adobe": "ADBE",
    "paypal": "PYPL", "shopify": "SHOP", "uber": "UBER", "lyft": "LYFT",
    "airbnb": "ABNB", "palantir": "PLTR", "snowflake": "SNOW",
    "coinbase": "COIN", "robinhood": "HOOD", "zoom": "ZM",
    "oracle": "ORCL", "ibm": "IBM", "cisco": "CSCO", "qualcomm": "QCOM",
    "broadcom": "AVGO", "arm": "ARM", "tsmc": "TSM",
    "newmont": "NEM", "barrick": "GOLD", "freeport": "FCX", "exxonmobil": "XOM",
    "chevron": "CVX", "conocophillips": "COP", "caterpillar": "CAT",
    "deere": "DE", "john deere": "DE", "archer daniels": "ADM",
    "goldman sachs": "GS", "morgan stanley": "MS", "bank of america": "BAC",
    "citigroup": "C", "wells fargo": "WFC", "blackrock": "BLK",
    "pfizer": "PFE", "moderna": "MRNA", "johnson & johnson": "JNJ",
    "unitedhealth": "UNH", "abbvie": "ABBV", "merck": "MRK", "eli lilly": "LLY",
    "home depot": "HD", "costco": "COST", "target": "TGT", "nike": "NKE",
    "starbucks": "SBUX", "chipotle": "CMG", "general electric": "GE",
    "3m": "MMM", "honeywell": "HON", "lockheed": "LMT", "raytheon": "RTX",
    "northrop": "NOC", "att": "T", "at&t": "T", "verizon": "VZ", "t-mobile": "TMUS",
    "spotify": "SPOT", "roblox": "RBLX", "natera": "NTRA", "intuitive surgical": "ISRG",
    "crowdstrike": "CRWD", "datadog": "DDOG", "cloudflare": "NET",
    "twilio": "TWLO", "okta": "OKTA", "servicenow": "NOW", "workday": "WDAY",
    "hubspot": "HUBS", "mongodb": "MDB", "gitlab": "GTLB", "hashicorp": "HCP",
    "palo alto": "PANW", "fortinet": "FTNT", "zscaler": "ZS",
    "block": "SQ", "square": "SQ", "stripe": "STRIPE", "affirm": "AFRM",
    "rivian": "RIVN", "lucid": "LCID", "nio": "NIO", "xpeng": "XPEV", "li auto": "LI",
    "baidu": "BIDU", "alibaba": "BABA", "tencent": "TCEHY", "jd.com": "JD",
    "pinduoduo": "PDD", "bytedance": "BDNCE",
    "draft kings": "DKNG", "draftkings": "DKNG", "penn entertainment": "PENN",
    "match group": "MTCH", "bumble": "BMBL",
    "astrazeneca": "AZN", "novartis": "NVS", "roche": "RHHBY",
    "bp": "BP", "shell": "SHEL", "totalenergies": "TTE",
    "bhp": "BHP", "rio tinto": "RIO", "vale": "VALE", "glencore": "GLEN",
    "albemarle": "ALB", "livent": "LTHM", "mp materials": "MP",
}


# ─────────────────────────────────────────
# COMMODITY → LINKED STOCKS MAPPING
# ─────────────────────────────────────────
COMMODITY_STOCKS = {
    "metals": ["NEM", "GOLD", "AEM", "FNV", "WPM", "FCX", "SCCO", "AA", "ALB"],
    "gold": ["NEM", "GOLD", "AEM", "FNV", "WPM", "AG", "PAAS"],
    "silver": ["AG", "PAAS", "WPM", "NEM", "GOLD"],
    "copper": ["FCX", "SCCO", "TECK", "BHP", "RIO"],
    "aluminium": ["AA", "CENX", "RIO", "BHP"],
    "aluminum": ["AA", "CENX", "RIO", "BHP"],
    "energy": ["XOM", "CVX", "COP", "SLB", "HAL", "EQT", "LNG"],
    "oil": ["XOM", "CVX", "COP", "SLB", "HAL", "PXD", "OXY"],
    "crude oil": ["XOM", "CVX", "COP", "SLB", "HAL", "PXD"],
    "wti": ["XOM", "CVX", "COP", "SLB", "HAL"],
    "brent": ["XOM", "CVX", "COP", "BP", "SHEL"],
    "natural gas": ["EQT", "LNG", "AR", "CHK", "RRC"],
    "gas": ["EQT", "LNG", "AR", "CHK"],
    "agriculture": ["ADM", "BG", "MOS", "DE", "NTR", "CTVA"],
    "corn": ["ADM", "BG", "MOS", "DE"],
    "wheat": ["ADM", "BG", "MOS"],
    "soybeans": ["ADM", "BG", "MOS"],
    "coffee": ["SBUX", "ADM", "BG"],
    "sugar": ["ADM", "BG"],
    "crypto": ["COIN", "MSTR", "MARA", "RIOT", "HUT"],
    "bitcoin": ["MSTR", "MARA", "RIOT", "COIN", "HUT"],
    "btc": ["MSTR", "MARA", "RIOT", "COIN"],
    "ethereum": ["COIN", "MARA", "RIOT"],
    "lithium": ["ALB", "LAC", "MP", "LTHM"],
    "mining": ["NEM", "GOLD", "FCX", "BHP", "RIO", "VALE", "TECK"],
}

def get_commodity_linked_stocks(commodity_name: str, category: str = None) -> list:
    """Get stocks linked to a commodity from stocks_live directly."""
    tickers = []
    commodity_lower = commodity_name.lower() if commodity_name else ""

    if commodity_lower in COMMODITY_STOCKS:
        tickers = COMMODITY_STOCKS[commodity_lower]
    elif category and category in COMMODITY_STOCKS:
        tickers = COMMODITY_STOCKS[category]

    if not tickers:
        return []

    try:
        from screener import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        placeholders = ','.join(['%s'] * len(tickers))
        cursor.execute(f"""
            SELECT l.ticker, l.company_name, l.sector, l.current_price, 
                   l.price_change_pct, l.market_cap, l.market_cap_category,
                   COALESCE(p.buy_signal, 'N/A') as buy_signal,
                   COALESCE(p.trend, 'NEUTRAL') as trend,
                   COALESCE(p.target_1w, l.current_price) as target_1w,
                   COALESCE(p.confidence_pct, 0) as confidence_pct
            FROM stocks_live l
            LEFT JOIN stocks_predictions p ON l.ticker = p.ticker
            WHERE l.ticker IN ({placeholders})
            AND l.current_price IS NOT NULL
        """, tickers)
        stocks = cursor.fetchall()
        cursor.close()
        conn.close()
        # Convert decimals to float
        result = []
        for row in stocks:
            r = {}
            for k, v in row.items():
                if v is None: r[k] = None
                elif isinstance(v, (int, float)): r[k] = float(v)
                elif hasattr(v, 'isoformat'): r[k] = str(v)
                else: r[k] = v
            result.append(r)
        return result
    except Exception as e:
        print(f"Commodity stocks error: {e}")
        return []

# ─────────────────────────────────────────
# COMMODITY DATA
# ─────────────────────────────────────────
COMMODITIES = {
    "gold": {"ticker": "GC=F", "name": "Gold", "category": "metals"},
    "silver": {"ticker": "SI=F", "name": "Silver", "category": "metals"},
    "platinum": {"ticker": "PL=F", "name": "Platinum", "category": "metals"},
    "palladium": {"ticker": "PA=F", "name": "Palladium", "category": "metals"},
    "copper": {"ticker": "HG=F", "name": "Copper", "category": "metals"},
    "aluminium": {"ticker": "ALI=F", "name": "Aluminium", "category": "metals"},
    "aluminum": {"ticker": "ALI=F", "name": "Aluminium", "category": "metals"},
    "crude oil": {"ticker": "CL=F", "name": "Crude Oil (WTI)", "category": "energy"},
    "oil": {"ticker": "CL=F", "name": "Crude Oil (WTI)", "category": "energy"},
    "wti": {"ticker": "CL=F", "name": "Crude Oil (WTI)", "category": "energy"},
    "brent": {"ticker": "BZ=F", "name": "Brent Crude", "category": "energy"},
    "natural gas": {"ticker": "NG=F", "name": "Natural Gas", "category": "energy"},
    "gas": {"ticker": "NG=F", "name": "Natural Gas", "category": "energy"},
    "gasoline": {"ticker": "RB=F", "name": "Gasoline", "category": "energy"},
    "corn": {"ticker": "ZC=F", "name": "Corn", "category": "agriculture"},
    "wheat": {"ticker": "ZW=F", "name": "Wheat", "category": "agriculture"},
    "soybeans": {"ticker": "ZS=F", "name": "Soybeans", "category": "agriculture"},
    "coffee": {"ticker": "KC=F", "name": "Coffee", "category": "agriculture"},
    "sugar": {"ticker": "SB=F", "name": "Sugar", "category": "agriculture"},
    "cotton": {"ticker": "CT=F", "name": "Cotton", "category": "agriculture"},
    "cocoa": {"ticker": "CC=F", "name": "Cocoa", "category": "agriculture"},
    "bitcoin": {"ticker": "BTC=F", "name": "Bitcoin Futures", "category": "crypto"},
    "btc": {"ticker": "BTC=F", "name": "Bitcoin Futures", "category": "crypto"},
    "ethereum": {"ticker": "ETH=F", "name": "Ethereum Futures", "category": "crypto"},
    "eth": {"ticker": "ETH=F", "name": "Ethereum Futures", "category": "crypto"},
}

def get_commodity_price(ticker: str) -> dict:
    """Fetch live commodity price from yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")
        if hist is not None and not hist.empty:
            latest = float(hist['Close'].iloc[-1])
            prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else latest
            change = latest - prev
            change_pct = (change / prev * 100) if prev > 0 else 0
            return {
                "price": round(latest, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2)
            }
    except:
        pass
    return None

def detect_commodity(query: str) -> dict:
    """Check if query is about a specific commodity."""
    q = query.lower()
    for name, data in COMMODITIES.items():
        if name in q:
            return data
    return None

# ─────────────────────────────────────────
# SMART AI ROUTER — understands any query
# ─────────────────────────────────────────
def smart_route(user_query: str, chat_history: list = None) -> dict:
    """
    Uses AI to understand what the user wants and returns routing decision.
    """
    # Build conversation context
    context_str = ""
    if chat_history and len(chat_history) > 0:
        recent = chat_history[-4:]  # last 4 messages
        context_str = "\n\nRecent conversation:\n" + "\n".join([f"{m['role'].upper()}: {m['content'][:200]}" for m in recent])

    prompt = f"""You are an AI financial assistant router for AYEIM Stock Market Pro — a US stock market and commodities dashboard.
{context_str}
A user typed: "{user_query}"

Classify their intent into ONE of these categories and return JSON:

INTENTS:
- "specific_stock": Asking about ONE specific company (Apple, Palantir, Tesla, PLTR, NVDA etc)
- "filter_stocks": Wants to FIND stocks by criteria (show me, find me, best stocks for X, stocks above $Y)
- "market_question": Wants stock recommendations or what to buy right now
- "commodity": Asking about a commodity (gold, silver, oil, gas, wheat, bitcoin, copper, etc)
- "commodity_stocks": Asking which stocks are linked to a commodity (gold mining stocks, oil companies)
- "portfolio": Asking about portfolio strategy, diversification, should I hold/sell/buy
- "economy_macro": Asking about economy, inflation, interest rates, Fed, GDP, VIX, DXY, recession
- "education": Asking HOW something works, WHY something happens, WHAT a term means — needs explanation not stock list
- "compare": Comparing two or more specific stocks or assets
- "irrelevant": Not related to finance or investing at all

STRICT RULES:
- Questions with "how", "why", "what is", "explain", "difference", "affect", "impact", "meaning" → "education" UNLESS they also ask for stocks/companies
- Any commodity name (gold, silver, oil, bitcoin, wheat, copper) without asking for stocks → "commodity"
- "gold stocks", "oil companies", "which stocks benefit from gold" → "commodity_stocks"
- Company name or stock ticker → "specific_stock"
- Portfolio/investment strategy questions → "portfolio"
- Inflation, interest rates, Fed, GDP, VIX, recession, economy → "economy_macro"
- Unrelated to finance → "irrelevant"

IMPORTANT: For "education", "commodity", "portfolio", "economy_macro" — write a helpful answer in the "answer" field (2-4 sentences, plain English).
For "specific_stock" — put company name in "company_name" and best ticker guess in "ticker_hint".
For "filter_stocks" — put filter criteria in "filters" field.
For "irrelevant" — write a polite redirect in "answer" field.

Return ONLY valid JSON:
{{"intent": "education", "ticker_hint": "", "company_name": "", "filters": {{}}, "answer": "Your explanation here", "commodity": ""}}

Available filter keys: sector, market_cap_category (small/mid/large), current_price (min/max), pe_ratio (min/max), dividend_yield (min/max), revenue_growth (min/max), buy_signal (BUY/HOLD/SELL), beta (min/max), profit_margin (min/max)

Return ONLY the JSON. Nothing else."""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"Router error: {e}")
        return {"intent": "filter_stocks", "ticker_hint": "", "company_name": "", "filters": {}, "answer": "", "commodity": ""}


def find_ticker_in_db(company_name: str, ticker_hint: str = "") -> str:
    """Search database for ticker by company name or ticker hint."""
    try:
        from screener import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Try ticker hint first
        if ticker_hint and 1 <= len(ticker_hint) <= 6:
            cursor.execute("SELECT ticker FROM stocks_live WHERE ticker = %s AND current_price IS NOT NULL", (ticker_hint.upper(),))
            row = cursor.fetchone()
            if row:
                cursor.close(); conn.close()
                return row['ticker']
        
        # Search by company name
        if company_name:
            cursor.execute("""
                SELECT ticker FROM stocks_live 
                WHERE LOWER(company_name) LIKE %s AND current_price IS NOT NULL
                ORDER BY market_cap DESC LIMIT 1
            """, (f"%{company_name.lower()}%",))
            row = cursor.fetchone()
            if row:
                cursor.close(); conn.close()
                return row['ticker']
        
        cursor.close(); conn.close()
        return None
    except Exception as e:
        print(f"DB search error: {e}")
        return None


# Keep these for backward compatibility
def classify_intent(user_query: str) -> str:
    route = smart_route(user_query)
    return route.get("intent", "filter_stocks")

def translate_to_filters(user_query: str) -> dict:
    route = smart_route(user_query)
    return route.get("filters", {})



# ─────────────────────────────────────────
# FORMAT STOCK DATA FOR PROMPT
# ─────────────────────────────────────────
def format_stock(s: dict) -> str:
    price = s.get('current_price') or 0
    target = s.get('target_1w') or price
    change_today = s.get('price_change_pct') or 0
    target_chg = ((target - price) / price * 100) if price > 0 else 0
    rev_growth = round(s.get('revenue_growth', 0) * 100, 1) if s.get('revenue_growth') else None
    margin = round(s.get('profit_margin', 0) * 100, 1) if s.get('profit_margin') else None
    div = round(s.get('dividend_yield', 0) * 100, 2) if s.get('dividend_yield') else None

    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━
{s.get('ticker')} — {s.get('company_name')}
Sector: {s.get('sector')} | Size: {s.get('market_cap_category', 'N/A')} cap
Current Price: ${price:.2f} | Today: {'+' if change_today >= 0 else ''}{change_today:.2f}%
7-Day Model Estimate: ${target:.2f} ({'+' if target_chg >= 0 else ''}{target_chg:.1f}%)
PE Ratio: {s.get('pe_ratio') or 'N/A'} | Revenue Growth: {f'{rev_growth}%' if rev_growth is not None else 'N/A'}
Profit Margin: {f'{margin}%' if margin is not None else 'N/A'} | Debt/Equity: {s.get('debt_to_equity') or 'N/A'}
Dividend Yield: {f'{div}%' if div else 'None'} | Beta: {s.get('beta') or 'N/A'}
Model Signal: {s.get('buy_signal')} | Trend: {s.get('trend')} | Risk: {s.get('risk_level')}
Momentum Score: {s.get('momentum_score')} | Confidence: {s.get('confidence_pct')}%"""


# ─────────────────────────────────────────
# GENERATE RECOMMENDATION OUTPUT
# ─────────────────────────────────────────
def generate_recommendation(user_query: str, stocks: list, context: str = "", chat_history: list = None) -> str:
    if not stocks:
        return "I couldn't find stocks matching that criteria in our database. Try a broader search — for example 'show me profitable technology stocks' or 'find dividend paying stocks'."

    top_stocks = stocks[:8]
    stock_summary = "".join([format_stock(s) for s in top_stocks])

    try:
        sectors = get_sector_summary()
        top_sector = sectors[0]['sector'] if sectors else "N/A"
        worst_sector = sectors[-1]['sector'] if sectors else "N/A"
        market_context = f"Today's best performing sector: {top_sector}. Weakest sector: {worst_sector}."
    except:
        market_context = ""

    prompt = f"""You are Bounty, a friendly and knowledgeable AI finance assistant for AYEIM Stock Market Pro.

The user asked: "{user_query}"

Here are the matching stocks from our live database:
{stock_summary}

Market context: {market_context}
{f'Additional context: {context}' if context else ''}

Respond conversationally like a smart financial advisor. Be natural and helpful.

Format your response as:
1. One friendly opening sentence about what you found
2. A numbered list of top 3-5 stocks with: ticker, company name, price, change%, and one key insight
3. End with a follow-up question like "Would you like me to explain any of these in more detail?" or "Want me to compare any two of these?"

Rules:
- Only use numbers from the data above — never invent figures
- Keep it concise and conversational — not overly formal
- Plain English — no jargon
- Always end with a helpful follow-up question
- Add: "⚠️ Model-based estimates, not financial advice." at the very end
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return response.choices[0].message.content.strip()


# ─────────────────────────────────────────
# PORTFOLIO REVIEW
# ─────────────────────────────────────────
def review_portfolio(tickers: list) -> str:
    portfolio_data = ""

    for ticker in tickers:
        s = get_stock_snapshot(ticker.upper().strip())
        if not s:
            portfolio_data += f"\n{ticker}: Not found in our database\n---"
            continue

        price = s.get('current_price') or 0
        target = s.get('target_1w') or price
        target_chg = ((target - price) / price * 100) if price > 0 else 0
        rev_growth = round(s.get('revenue_growth', 0) * 100, 1) if s.get('revenue_growth') else None
        margin = round(s.get('profit_margin', 0) * 100, 1) if s.get('profit_margin') else None

        portfolio_data += f"""
━━━━━━━━━━━━━━━━━━━━━━━━
{s.get('ticker')} — {s.get('company_name')}
Sector: {s.get('sector')}
Current Price: ${price:.2f} | Today: {s.get('price_change_pct') or 0:.2f}%
7-Day Estimate: ${target:.2f} ({'+' if target_chg >= 0 else ''}{target_chg:.1f}%)
PE Ratio: {s.get('pe_ratio') or 'N/A'} | Revenue Growth: {f'{rev_growth}%' if rev_growth is not None else 'N/A'}
Profit Margin: {f'{margin}%' if margin is not None else 'N/A'} | Debt/Equity: {s.get('debt_to_equity') or 'N/A'}
Model Signal: {s.get('buy_signal')} | Trend: {s.get('trend')} | Risk: {s.get('risk_level')}
Momentum Score: {s.get('momentum_score')} | Confidence: {s.get('confidence_pct')}%"""

    prompt = f"""
You are a professional portfolio analyst at Stock Market Pro.

The user's portfolio:
{portfolio_data}

Review each stock in detail. For each one write:

📌 [TICKER] — [Company Name]
Action: HOLD / ADD MORE / CAUTION / CONSIDER SWAPPING
Current Price: $X.XX | 7-Day Model Estimate: $X.XX ([+/-]X.X%)

Analysis:
Write 3-5 sentences explaining the action in plain English. Be specific and honest:
- What the model signal and trend say about this stock right now
- What the key financial data (margins, growth, debt) reveals
- Whether current price movement supports or contradicts holding
- If CONSIDER SWAPPING: explain clearly why this stock may not be the best use of capital right now, and what type of replacement would be better suited (do not invent specific tickers)
- If CAUTION: explain what specific risk the user should watch for and what would be a trigger to reconsider the position
- If HOLD or ADD MORE: explain what makes this stock worth keeping or increasing

Make it feel like a real analyst wrote it — honest, specific, and backed by the numbers. Avoid vague phrases like "the stock looks uncertain". Say exactly WHY using the actual data provided.

Rules:
- Only use numbers from the data above. Never invent figures.
- Plain English — no financial jargon
- Every action must be clearly justified with data points
- End with: "⚠️ These are model-based estimates, not financial advice. Always invest within your risk tolerance."
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return response.choices[0].message.content.strip()


# ─────────────────────────────────────────
# MAIN AGENT — handles all query types
# ─────────────────────────────────────────
def generate_single_stock_response(ticker: str, user_query: str) -> str:
    """Generate focused response for a specific stock query."""
    s = get_stock_snapshot(ticker.upper())
    if not s:
        return f"I couldn't find {ticker} in our database. It may not be tracked yet or the ticker may be different. Try searching with the exact ticker symbol."

    price = s.get('current_price') or 0
    target = s.get('target_1w') or price
    target_chg = ((target - price) / price * 100) if price > 0 else 0
    rev_growth = round(s.get('revenue_growth', 0) * 100, 1) if s.get('revenue_growth') else None
    margin = round(s.get('profit_margin', 0) * 100, 1) if s.get('profit_margin') else None
    div = round(s.get('dividend_yield', 0) * 100, 2) if s.get('dividend_yield') else None

    stock_data = format_stock(s)

    prompt = f"""You are a professional stock analyst at Stock Market Pro.

The user asked: "{user_query}"

Here is the live data for {ticker} — {s.get('company_name')}:
{stock_data}

Write a clear, focused analysis responding directly to what the user asked.

Structure your response as:

📊 {ticker} — {s.get('company_name')}
Current Price: ${price:.2f} | Today: {s.get('price_change_pct', 0):.2f}%
7-Day Model Estimate: ${target:.2f} ({'+' if target_chg >= 0 else ''}{target_chg:.1f}%)
Model Signal: {s.get('buy_signal', 'N/A')} | Trend: {s.get('trend', 'N/A')} | Risk: {s.get('risk_level', 'N/A')}

What this company does:
[1-2 sentences about the business]

Financial health:
[2-3 sentences covering revenue growth, margins, debt — use the actual numbers]

What our model says:
[1-2 sentences about the prediction, signal and confidence]

Key risk to watch:
[1 sentence about the main risk]

Rules:
- Only use numbers from the data provided. Never invent figures.
- Respond directly to what the user asked about this specific stock
- Plain English — no jargon
- End with: "⚠️ Model-based estimate, not financial advice."
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content.strip()


def run_agent(user_query: str, portfolio_tickers: list = None, chat_history: list = None):
    print(f"\nUser query: {user_query}")

    # Use smart AI router to understand intent
    route = smart_route(user_query, chat_history or [])
    intent = route.get("intent", "filter_stocks")
    print(f"Intent: {intent} | Route: {route}")

    # ── EDUCATION: explain a financial concept ──
    if intent == "education":
        answer = route.get("answer", "")
        if not answer:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": f"As a financial expert, explain this in simple plain English (3-5 sentences, no jargon): {user_query}"}],
                temperature=0.3
            )
            answer = response.choices[0].message.content.strip()
        return {"intent": "education", "stocks_found": 0, "recommendation": answer, "portfolio_review": None, "top_stocks": [], "all_stocks": []}

    # ── IRRELEVANT ──
    if intent == "irrelevant":
        answer = route.get("answer", "I'm AYEIM's financial AI assistant. I can help you with stocks, commodities, market analysis, investment questions, and financial concepts. What would you like to know?")
        return {"intent": "irrelevant", "stocks_found": 0, "recommendation": answer, "portfolio_review": None, "top_stocks": [], "all_stocks": []}

    # ── COMMODITY: user asking about a specific commodity ──
    if intent == "commodity":
        answer = route.get("answer", "")
        com = detect_commodity(user_query)
        price_info = ""
        commodity_name = ""

        if com:
            commodity_name = com["name"]
            price_data = get_commodity_price(com["ticker"])
            if price_data:
                chg = price_data['change_pct']
                price_info = f"\n\n📊 **{com['name']} — Live Price**\nCurrent: ${price_data['price']:,.2f} | Today: {'▲ +' if chg >= 0 else '▼ '}{abs(chg):.2f}%"

        if not answer:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": f"As a commodity market expert, answer this about {user_query}. Give current context, what drives this commodity's price, and investment outlook (4-5 sentences plain English)."}],
                temperature=0.3
            )
            answer = response.choices[0].message.content.strip()

        # Get linked stocks to show as KPI cards
        linked_stocks = []
        if com:
            linked_stocks = get_commodity_linked_stocks(com["name"].lower(), com["category"])
        
        full_answer = answer + price_info
        if linked_stocks:
            full_answer += f"\n\n📈 **{commodity_name}-Linked Stocks** — showing top companies below:"

        return {
            "intent": "commodity",
            "stocks_found": len(linked_stocks),
            "recommendation": full_answer,
            "portfolio_review": None,
            "top_stocks": linked_stocks[:10],
            "all_stocks": linked_stocks
        }

    # ── COMMODITY STOCKS: stocks linked to a commodity ──
    if intent == "commodity_stocks":
        answer = route.get("answer", "")
        com = detect_commodity(user_query)

        # Get linked stocks
        linked_stocks = []
        if com:
            linked_stocks = get_commodity_linked_stocks(com["name"].lower(), com["category"])

        # Fallback to sector screen if no commodity detected
        if not linked_stocks:
            filters = route.get("filters", {})
            linked_stocks = screen_stocks(filters, limit=30) if filters else screen_stocks({"buy_signal": "BUY"}, limit=30)

        context = f"User is asking about stocks linked to commodities. {answer}"
        recommendation = generate_recommendation(user_query, linked_stocks, context)
        return {
            "intent": "commodity_stocks",
            "stocks_found": len(linked_stocks),
            "recommendation": recommendation,
            "portfolio_review": None,
            "top_stocks": linked_stocks[:10],
            "all_stocks": linked_stocks
        }

    # ── PORTFOLIO: portfolio strategy questions ──
    if intent == "portfolio":
        answer = route.get("answer", "")
        if not answer:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": f"As a portfolio management expert, answer this question: {user_query}. Give practical, actionable advice in plain English (4-5 sentences). End with a note that this is not financial advice."}],
                temperature=0.3
            )
            answer = response.choices[0].message.content.strip()
        # Also show some recommended stocks
        stocks = screen_stocks({"buy_signal": "BUY", "beta": {"max": 1.2}}, limit=10)
        return {"intent": "portfolio", "stocks_found": len(stocks), "recommendation": answer, "portfolio_review": None, "top_stocks": stocks[:5], "all_stocks": stocks}

    # ── ECONOMY/MACRO: economic questions ──
    if intent == "economy_macro":
        answer = route.get("answer", "")
        if not answer:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": f"As a macro economist, answer this question: {user_query}. Explain in plain English with current context (4-5 sentences). Mention which types of stocks or assets are affected."}],
                temperature=0.3
            )
            answer = response.choices[0].message.content.strip()
        return {"intent": "economy_macro", "stocks_found": 0, "recommendation": answer, "portfolio_review": None, "top_stocks": [], "all_stocks": []}

    # ── SPECIFIC STOCK: user asked about one company ──
    if intent == "specific_stock":
        company_name = route.get("company_name", "")
        ticker_hint = route.get("ticker_hint", "")
        print(f"Looking for: {company_name} ({ticker_hint})")

        ticker = find_ticker_in_db(company_name, ticker_hint)
        print(f"Found ticker: {ticker}")

        if not ticker:
            not_found_msg = f"I couldn't find **{company_name or ticker_hint}** in our database. We currently track 959 US-listed stocks. This company may not be tracked yet, or try searching with the exact ticker symbol."
            return {
                "intent": "specific_stock",
                "stocks_found": 0,
                "recommendation": not_found_msg,
                "portfolio_review": None,
                "top_stocks": [],
                "all_stocks": []
            }

        recommendation = generate_single_stock_response(ticker, user_query)
        s = get_stock_snapshot(ticker.upper())
        top_stocks = [s] if s else []
        return {
            "intent": "specific_stock",
            "stocks_found": 1,
            "recommendation": recommendation,
            "portfolio_review": None,
            "top_stocks": top_stocks,
            "all_stocks": top_stocks
        }

    # ── COMPARE: user wants to compare multiple stocks ──
    if intent == "compare":
        ticker_hint = route.get("ticker_hint", "")
        # For now treat as filter — TODO: build compare feature
        filters = route.get("filters", {})
        stocks = screen_stocks(filters, limit=30) if filters else screen_stocks({"buy_signal": "BUY"}, limit=10)
        recommendation = generate_recommendation(user_query, stocks, "User wants to compare stocks")
        return {
            "intent": "compare",
            "stocks_found": len(stocks),
            "recommendation": recommendation,
            "portfolio_review": None,
            "top_stocks": stocks[:10],
            "all_stocks": stocks
        }

    # ── MARKET QUESTION: general investment/market query ──
    if intent == "market_question":
        stocks = screen_stocks({"buy_signal": "BUY"}, limit=30)
        context = "User is asking a general market or investment question."
        recommendation = generate_recommendation(user_query, stocks, context)
        portfolio_review = None
        if portfolio_tickers:
            portfolio_review = review_portfolio(portfolio_tickers)
        return {
            "intent": "market_question",
            "stocks_found": len(stocks),
            "recommendation": recommendation,
            "portfolio_review": portfolio_review,
            "top_stocks": stocks[:10],
            "all_stocks": stocks
        }

    # ── FILTER STOCKS: user wants to find stocks by criteria ──
    filters = route.get("filters", {})
    if not filters:
        filters = {}

    print(f"Filters: {filters}")
    stocks = screen_stocks(filters, limit=30)
    print(f"Stocks found: {len(stocks)}")

    recommendation = generate_recommendation(user_query, stocks, "")

    portfolio_review = None
    if portfolio_tickers:
        portfolio_review = review_portfolio(portfolio_tickers)

    return {
        "intent": intent,
        "stocks_found": len(stocks),
        "recommendation": recommendation,
        "portfolio_review": portfolio_review,
        "top_stocks": stocks[:10],
        "all_stocks": stocks
    }


# ─────────────────────────────────────────
# EXPLAIN SINGLE STOCK
# ─────────────────────────────────────────
def explain_single_stock(ticker: str, original_query: str = "") -> str:
    """
    Generate a full AI explanation for a single stock.
    Called when user selects a stock from the dropdown.
    """
    s = get_stock_snapshot(ticker.upper())
    if not s:
        return f"Sorry, we couldn't find data for {ticker} in our database."

    price = s.get('current_price') or 0
    target = s.get('target_1w') or price
    target_chg = ((target - price) / price * 100) if price > 0 else 0
    rev_growth = round(s.get('revenue_growth', 0) * 100, 1) if s.get('revenue_growth') else None
    margin = round(s.get('profit_margin', 0) * 100, 1) if s.get('profit_margin') else None
    div = round(s.get('dividend_yield', 0) * 100, 2) if s.get('dividend_yield') else None

    stock_data = f"""
Company: {s.get('company_name')} ({ticker})
Sector: {s.get('sector')} | Size: {s.get('market_cap_category', 'N/A')} cap
Current Price: ${price:.2f} | Today's Change: {s.get('price_change_pct') or 0:.2f}%
7-Day Model Estimate: ${target:.2f} ({'+' if target_chg >= 0 else ''}{target_chg:.1f}%)
PE Ratio: {s.get('pe_ratio') or 'N/A'} | Forward PE: {s.get('forward_pe') or 'N/A'}
Revenue Growth: {f'{rev_growth}%' if rev_growth is not None else 'N/A'}
Profit Margin: {f'{margin}%' if margin is not None else 'N/A'}
Debt to Equity: {s.get('debt_to_equity') or 'N/A'}
Dividend Yield: {f'{div}%' if div else 'None'}
Beta: {s.get('beta') or 'N/A'}
52-Week High: ${s.get('fifty_two_week_high') or 'N/A'} | Low: ${s.get('fifty_two_week_low') or 'N/A'}
Model Signal: {s.get('buy_signal')} | Trend: {s.get('trend')} | Risk: {s.get('risk_level')}
Momentum Score: {s.get('momentum_score')} | Confidence: {s.get('confidence_pct')}%
Analyst Target: ${s.get('target_mean_price') or 'N/A'} | Rating: {s.get('recommendation_mean') or 'N/A'}
"""

    context = f"The user was searching for: '{original_query}'" if original_query else ""

    prompt = f"""
You are a professional stock analyst at Stock Market Pro.

The user selected this stock to learn more about:
{stock_data}

{context}

Write a clear, detailed explanation of this stock in plain English. Structure it like this:

📊 {s.get('company_name')} ({ticker})
Current Price: ${price:.2f} | 7-Day Estimate: ${target:.2f} ({'+' if target_chg >= 0 else ''}{target_chg:.1f}%)
Sector: {s.get('sector')} | Signal: {s.get('buy_signal')}

What this company does:
[1-2 sentences about the business in very simple terms]

Why it matched your search:
[2-3 sentences explaining what makes this stock relevant to what the user was looking for, using the actual data]

Financial health:
[2-3 sentences about the key financial metrics — growth, margins, debt — in plain English]

What our model says:
[1-2 sentences about the model signal, trend, momentum, and 7-day estimate]

Risk to watch:
[1-2 sentences about the main risk or concern with this stock]

Rules:
- Only use numbers from the data provided above. Never invent figures.
- Plain English — no jargon
- Be honest about both positives and risks
- End with: "⚠️ Model-based estimate, not financial advice. Always invest within your risk tolerance."
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return response.choices[0].message.content.strip()


if __name__ == "__main__":
    result = run_agent("show me stocks with price above $100")
    print("\n" + "="*50)
    print("RECOMMENDATION:")
    print("="*50)
    print(result["recommendation"])
    print(f"\nStocks found: {result['stocks_found']}")
