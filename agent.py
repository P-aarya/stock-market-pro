import os
from dotenv import load_dotenv
load_dotenv()
from groq import Groq
import groq
from screener import screen_stocks, get_stock_snapshot, get_top_movers, get_sector_summary, get_trending_stocks, find_similar_stocks
import json
import re

# Groq API client. A missing key must not stop the whole API from starting.
MODEL = "openai/gpt-oss-120b"
client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=45, max_retries=2) if os.getenv("GROQ_API_KEY") else None


class LLMUnavailable(Exception):
    """The AI service is not configured or could not be reached."""


def llm_create(**kwargs):
    """Single place every LLM call goes through (model, timeout and missing-key handling)."""
    if client is None:
        raise LLMUnavailable("GROQ_API_KEY is not set")
    return client.chat.completions.create(model=MODEL, **kwargs)

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
    "hubspot": "HUBS", "mongodb": "MDB", "gitlab": "GTLB",
    "palo alto": "PANW", "fortinet": "FTNT", "zscaler": "ZS",
    "block": "SQ", "square": "SQ", "affirm": "AFRM",
    "rivian": "RIVN", "lucid": "LCID", "nio": "NIO", "xpeng": "XPEV", "li auto": "LI",
    "baidu": "BIDU", "alibaba": "BABA", "tencent": "TCEHY", "jd.com": "JD",
    "pinduoduo": "PDD",
    "draft kings": "DKNG", "draftkings": "DKNG", "penn entertainment": "PENN",
    "match group": "MTCH", "bumble": "BMBL",
    "astrazeneca": "AZN", "novartis": "NVS", "roche": "RHHBY",
    "bp": "BP", "shell": "SHEL", "totalenergies": "TTE",
    "bhp": "BHP", "rio tinto": "RIO", "vale": "VALE",
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
                   l.price_change_pct, l.market_cap, f.market_cap_category,
                   COALESCE(p.buy_signal, 'N/A') as buy_signal,
                   COALESCE(p.trend, 'NEUTRAL') as trend,
                   COALESCE(p.target_1w, l.current_price) as target_1w,
                   COALESCE(p.confidence_pct, 0) as confidence_pct,
                   l.last_updated as price_updated,
                   p.run_date as prediction_date
            FROM stocks_live l
            LEFT JOIN stocks_fundamentals f ON l.ticker = f.ticker
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

_COMMODITY_PATTERNS = [
    (re.compile(r"\b" + re.escape(name) + r"\b"), data)
    for name, data in sorted(COMMODITIES.items(), key=lambda kv: len(kv[0]), reverse=True)
]

def detect_commodity(query: str) -> dict:
    """Check if query is about a specific commodity (whole words, longest name first)."""
    q = query.lower()
    best = None
    for pattern, data in _COMMODITY_PATTERNS:  # longest names first, so "crude oil" beats "oil"
        m = pattern.search(q)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), data)
    return best[1] if best else None

# ─────────────────────────────────────────
# SMART AI ROUTER — understands any query
# ─────────────────────────────────────────
VALID_INTENTS = {
    "specific_stock", "filter_stocks", "market_question", "commodity", "commodity_stocks",
    "portfolio", "economy_macro", "education", "compare", "irrelevant",
}
EMPTY_ROUTE = {"intent": "filter_stocks", "ticker_hint": "", "company_name": "", "compare_items": [],
               "filters": {}, "answer": "", "commodity": ""}

def parse_route(raw: str) -> dict:
    """Parse and validate the router's JSON. Returns None if it is unusable."""
    if not raw:
        return None
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)  # JSON buried in surrounding text
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or data.get("intent") not in VALID_INTENTS:
        return None
    route = {**EMPTY_ROUTE, **data}
    for key in ("ticker_hint", "company_name", "answer", "commodity"):
        route[key] = route[key] if isinstance(route[key], str) else ""
    if not isinstance(route["filters"], dict):
        route["filters"] = {}
    if not isinstance(route["compare_items"], list):
        route["compare_items"] = []
    return route


def history_block(chat_history: list, max_messages: int = 6, max_chars: int = 400) -> str:
    """Recent conversation as plain text for prompts ('' when there is none)."""
    lines = []
    for m in (chat_history or [])[-max_messages:]:
        if isinstance(m, dict) and m.get("content"):
            role = "USER" if m.get("role") == "user" else "ASSISTANT"
            lines.append(f"{role}: {str(m['content'])[:max_chars]}")
    return "\n".join(lines)


def smart_route(user_query: str, chat_history: list = None, reply_to: str = "") -> dict:
    """
    Uses AI to understand what the user wants and returns routing decision.
    """
    # Build conversation context
    context_str = ""
    history = history_block(chat_history)
    if history:
        context_str = "\n\nRecent conversation:\n" + history
    if reply_to:
        context_str += f'\n\nThe user is replying to this earlier message: "{reply_to[:300]}"'

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
For "specific_stock" — put company name in "company_name" and best ticker guess in "ticker_hint". If the user says "it", "that stock", "the first one" or similar, work out which company they mean from the recent conversation.
For "compare" — list every stock being compared (2 to 5) in "compare_items" as [{{"company_name": "...", "ticker_hint": "..."}}]. If the user says "them", "these" or "both", take the stocks from the recent conversation.
For "filter_stocks" — put filter criteria in "filters" field.
For "irrelevant" — write a polite redirect in "answer" field.

Return ONLY valid JSON:
{{"intent": "education", "ticker_hint": "", "company_name": "", "compare_items": [], "filters": {{}}, "answer": "Your explanation here", "commodity": ""}}

Available filter keys: sector, market_cap_category (small/mid/large), current_price (min/max), pe_ratio (min/max), dividend_yield (min/max), revenue_growth (min/max), buy_signal (BUY/HOLD/SELL), beta (min/max), profit_margin (min/max), pays_dividend (true)

Units: dividend_yield is in PERCENT (3 means 3%). revenue_growth and profit_margin are FRACTIONS (0.15 means 15%).
For any request about dividends or income stocks, include "pays_dividend": true so that stocks paying no dividend are excluded. Only add a dividend_yield min if the user names a minimum yield.

Return ONLY the JSON. Nothing else."""

    for attempt in range(2):
        try:
            response = llm_create(messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=2000,  # reasoning tokens count against this limit
                response_format={"type": "json_object"},
            )
            route = parse_route(response.choices[0].message.content)
            if route:
                return route
            print(f"Router attempt {attempt + 1}: unusable reply: {response.choices[0].message.content!r:.300}")
        except (LLMUnavailable, groq.RateLimitError, groq.APIConnectionError):
            raise  # an outage or rate limit is not a parsing problem: let the API report it properly
        except Exception as e:
            print(f"Router attempt {attempt + 1} error: {e}")
    return {**EMPTY_ROUTE, "intent": "router_error"}


def count_tracked_stocks() -> int:
    """Number of stocks currently in the database (for user-facing messages)."""
    try:
        from screener import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stocks_live WHERE current_price IS NOT NULL")
        n = cursor.fetchone()[0]
        cursor.close(); conn.close()
        return int(n)
    except Exception as e:
        print(f"Stock count error: {e}")
        return 0


_NAME_NOISE = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
               "holdings", "holding", "group", "the", "class", "a", "b", "c", "adr", "ads", "nv", "sa", "ag"}

def _name_tokens(name: str) -> list:
    """'The Coca-Cola Company' -> ['coca', 'cola']"""
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in _NAME_NOISE]


def find_ticker_in_db(company_name: str, ticker_hint: str = "") -> str:
    """
    Resolve a company name / ticker hint to a ticker we track.
    The hint is only trusted when it agrees with the name; names match whole words
    (so 'Snap' does not become Snap-on and 'Block' does not become H&R Block).
    """
    try:
        from screener import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            name_tokens = _name_tokens(company_name)

            hint_row = None
            if ticker_hint and 1 <= len(ticker_hint) <= 6:
                cursor.execute("SELECT ticker, company_name FROM stocks_live WHERE ticker = %s AND current_price IS NOT NULL",
                               (ticker_hint.upper(),))
                hint_row = cursor.fetchone()

            # 1. A hint that agrees with the name (or a hint with no name at all)
            if hint_row and (not name_tokens or set(name_tokens) & set(_name_tokens(hint_row["company_name"]))):
                return hint_row["ticker"]

            if name_tokens:
                # 2. Well-known alias (google -> GOOGL, facebook -> META)
                alias = COMPANY_MAP.get(" ".join(name_tokens)) or COMPANY_MAP.get((company_name or "").lower().strip())
                if alias:
                    cursor.execute("SELECT ticker FROM stocks_live WHERE ticker = %s AND current_price IS NOT NULL", (alias,))
                    row = cursor.fetchone()
                    if row:
                        return row["ticker"]

                # 2b. The name is itself a ticker we track (e.g. PLTR)
                bare = (company_name or "").strip().upper()
                if re.fullmatch(r"[A-Z0-9.\-]{1,6}", bare):
                    cursor.execute("SELECT ticker FROM stocks_live WHERE ticker = %s AND current_price IS NOT NULL", (bare,))
                    row = cursor.fetchone()
                    if row:
                        return row["ticker"]

                # 3. Company name: exact name beats "starts with the name"; anything looser is rejected
                cursor.execute("""
                    SELECT ticker, company_name FROM stocks_live
                    WHERE LOWER(company_name) LIKE %s AND current_price IS NOT NULL
                    ORDER BY market_cap DESC LIMIT 25
                """, (f"%{name_tokens[0]}%",))
                best = None
                for row in cursor.fetchall():
                    tokens = _name_tokens(row["company_name"])
                    if tokens == name_tokens:
                        rank = 0
                    elif tokens[:len(name_tokens)] == name_tokens:
                        rank = 1
                    else:
                        continue
                    if best is None or rank < best[0]:
                        best = (rank, row["ticker"])  # rows come largest market cap first
                if best:
                    return best[1]

            # 4. Nothing matched the name: fall back to the model's hint if it exists
            if hint_row:
                return hint_row["ticker"]
            return None
        finally:
            cursor.close(); conn.close()
    except Exception as e:
        print(f"DB search error: {e}")
        return None


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
    div = round(s.get('dividend_yield', 0), 2) if s.get('dividend_yield') else None

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

    prompt = with_history(prompt, chat_history)
    response = llm_create(messages=[{"role": "user", "content": prompt}],
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

    response = llm_create(messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return response.choices[0].message.content.strip()


# ─────────────────────────────────────────
# COMPARE STOCKS
# ─────────────────────────────────────────
MAX_COMPARE = 5

def resolve_compare_tickers(route: dict, user_query: str) -> tuple:
    """Resolve the stocks to compare. Returns (tickers, unresolved_names)."""
    tickers, missing = [], []

    def add(ticker):
        if ticker and ticker not in tickers:
            tickers.append(ticker)

    for item in route.get("compare_items") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("company_name") or "").strip()
        hint = (item.get("ticker_hint") or "").strip()
        ticker = find_ticker_in_db(name, hint)
        if ticker:
            add(ticker)
        elif name or hint:
            missing.append(name or hint)

    # Fallback if the router returned nothing usable: scan the query itself
    if not tickers and not missing:
        q = user_query.lower()
        for name, hint in COMPANY_MAP.items():
            if re.search(r"\b" + re.escape(name) + r"\b", q):
                ticker = find_ticker_in_db("", hint)
                if ticker:
                    add(ticker)
        for word in re.findall(r"\b[A-Z]{2,5}\b", user_query):
            add(find_ticker_in_db("", word))

    return tickers[:MAX_COMPARE], missing


def generate_comparison(user_query: str, snapshots: list, chat_history: list = None) -> str:
    stock_data = "".join(format_stock(s) for s in snapshots)
    tickers = ", ".join(s["ticker"] for s in snapshots)

    prompt = f"""You are a professional stock analyst at Stock Market Pro.

The user asked: "{user_query}"

Here is the live data for the stocks being compared ({tickers}):
{stock_data}

Write a side-by-side comparison in plain text (no markdown tables, no ** bold).

Structure:
1. One sentence saying what is being compared.
2. For each of these areas, one short line per stock, using the actual numbers: Valuation (PE), Growth (revenue growth), Profitability (profit margin), Financial health (debt/equity), Risk (beta and risk level), Model outlook (signal, 7-day estimate, confidence).
3. "Verdict:" 2-3 sentences on which stock looks strongest on this data and for what kind of investor, and what trade-off the other(s) carry. If the data is mixed, say so rather than forcing a winner.
4. A short follow-up question.

Rules:
- Only use numbers from the data above. Never invent figures. If a value is N/A, say it is unavailable.
- Plain English, no jargon.
- End with: "⚠️ Model-based estimates, not financial advice."
"""
    prompt = with_history(prompt, chat_history)
    response = llm_create(messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content.strip()


# ─────────────────────────────────────────
# MAIN AGENT — handles all query types
# ─────────────────────────────────────────
def generate_single_stock_response(ticker: str, user_query: str, chat_history: list = None) -> str:
    """Generate focused response for a specific stock query."""
    s = get_stock_snapshot(ticker.upper())
    if not s:
        return f"I couldn't find {ticker} in our database. It may not be tracked yet or the ticker may be different. Try searching with the exact ticker symbol."

    price = s.get('current_price') or 0
    target = s.get('target_1w') or price
    target_chg = ((target - price) / price * 100) if price > 0 else 0
    rev_growth = round(s.get('revenue_growth', 0) * 100, 1) if s.get('revenue_growth') else None
    margin = round(s.get('profit_margin', 0) * 100, 1) if s.get('profit_margin') else None
    div = round(s.get('dividend_yield', 0), 2) if s.get('dividend_yield') else None

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

    prompt = with_history(prompt, chat_history)
    response = llm_create(messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content.strip()


def with_history(prompt: str, chat_history: list) -> str:
    """Append the recent conversation to a prompt so follow-up questions make sense."""
    block = history_block(chat_history, max_messages=4, max_chars=300)
    return f"{prompt}\n\nRecent conversation (for context only):\n{block}" if block else prompt


def llm_text(prompt: str, temperature: float = 0.3) -> str:
    response = llm_create(messages=[{"role": "user", "content": prompt}], temperature=temperature)
    return response.choices[0].message.content.strip()


def holdings_context(tickers: list) -> str:
    """One line per watchlist stock (sector, signal, risk) for tailoring portfolio advice."""
    rows = []
    for t in (tickers or [])[:15]:
        snap = get_stock_snapshot(str(t).upper().strip())
        if snap:
            rows.append(f"{snap['ticker']} ({snap.get('sector')}, signal {snap.get('buy_signal')}, risk {snap.get('risk_level')}, beta {snap.get('beta') or 'N/A'})")
    return "; ".join(rows)


MACRO_INDICATORS = [("^VIX", "VIX fear index", ""), ("DX-Y.NYB", "US Dollar index", ""),
                    ("^TNX", "10-year Treasury yield", "%"), ("GC=F", "Gold", "")]
_macro_cache = {"at": 0.0, "text": ""}

def get_macro_context() -> str:
    """Live market indicators and sector performance as text (cached for 5 minutes)."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    if _macro_cache["text"] and time.time() - _macro_cache["at"] < 300:
        return _macro_cache["text"]

    with ThreadPoolExecutor(max_workers=len(MACRO_INDICATORS)) as pool:
        prices = list(pool.map(lambda ind: get_commodity_price(ind[0]), MACRO_INDICATORS))
    lines = []
    for (_, name, unit), data in zip(MACRO_INDICATORS, prices):
        if data:
            lines.append(f"- {name}: {data['price']:,.2f}{unit} ({data['change_pct']:+.2f}% today)")
    try:
        sectors = get_sector_summary()
        if sectors:
            best, worst = sectors[0], sectors[-1]
            lines.append(f"- Best sector today: {best['sector']}; weakest: {worst['sector']}")
    except Exception as e:
        print(f"Sector summary error: {e}")
    text = "\n".join(lines)
    if text:
        _macro_cache.update(at=time.time(), text=text)
    return text


def _as_datetime(value):
    from datetime import datetime, date
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value[:19], fmt)
            except ValueError:
                pass
    return None


STALE_AFTER_DAYS = 3  # covers a normal weekend

def freshness_note(stocks: list) -> str:
    """Tell the user how old the numbers are, and flag stocks whose price has gone stale."""
    from datetime import datetime
    prices = [(st.get("ticker"), _as_datetime(st.get("price_updated"))) for st in stocks or []]
    prices = [(t, d) for t, d in prices if d]
    if not prices:
        return ""
    newest = max(d for _, d in prices)
    note = f"🕒 Prices as of {newest:%b %d, %H:%M}"
    preds = [d for d in (_as_datetime(st.get("prediction_date")) for st in stocks) if d]
    if preds:
        note += f"; model estimates from {max(preds):%b %d}"
    stale = [(t, d) for t, d in prices if (datetime.now() - d).days >= STALE_AFTER_DAYS]
    if stale:
        names = ", ".join(f"{t} ({d:%b %d})" for t, d in stale[:3]) + ("…" if len(stale) > 3 else "")
        note += f"\n⚠️ These prices may be out of date: {names}."
    return note


STOCK_INTENTS = {"specific_stock", "filter_stocks", "market_question", "commodity", "commodity_stocks", "compare", "portfolio"}


def run_agent(user_query: str, portfolio_tickers: list = None, chat_history: list = None, reply_to: str = ""):
    """Answer a chat query. Adds a data-age note whenever the answer is based on stock data."""
    result = _run_agent(user_query, portfolio_tickers, chat_history, reply_to)
    if result.get("intent") in STOCK_INTENTS:
        note = freshness_note(result.get("top_stocks"))
        if note:
            result["recommendation"] = f"{result['recommendation']}\n\n{note}"
    return result


def _run_agent(user_query: str, portfolio_tickers: list = None, chat_history: list = None, reply_to: str = ""):
    print(f"\nUser query: {user_query}")

    # Use smart AI router to understand intent
    route = smart_route(user_query, chat_history or [], reply_to)
    intent = route.get("intent", "filter_stocks")
    print(f"Intent: {intent} | Route: {route}")

    # ── ROUTER FAILED: say so rather than guessing with an empty screen ──
    if intent == "router_error":
        return {
            "intent": "router_error",
            "stocks_found": 0,
            "recommendation": "Sorry, I had trouble understanding that request. Could you rephrase it? For example: 'show me profitable tech stocks under $50' or 'compare AAPL and MSFT'.",
            "portfolio_review": None,
            "top_stocks": [],
            "all_stocks": [],
        }

    # ── EDUCATION: explain a financial concept ──
    if intent == "education":
        answer = route.get("answer", "")
        if not answer:
            answer = llm_text(with_history(f"As a financial expert, explain this in simple plain English (3-5 sentences, no jargon): {user_query}", chat_history))
        return {"intent": "education", "stocks_found": 0, "recommendation": answer, "portfolio_review": None, "top_stocks": [], "all_stocks": []}

    # ── IRRELEVANT ──
    if intent == "irrelevant":
        answer = route.get("answer", "I'm AYEIM's financial AI assistant. I can help you with stocks, commodities, market analysis, investment questions, and financial concepts. What would you like to know?")
        return {"intent": "irrelevant", "stocks_found": 0, "recommendation": answer, "portfolio_review": None, "top_stocks": [], "all_stocks": []}

    # ── COMMODITY: user asking about a specific commodity ──
    if intent == "commodity":
        com = detect_commodity(user_query)
        price_data = get_commodity_price(com["ticker"]) if com else None
        price_info = ""
        if com and price_data:
            chg = price_data['change_pct']
            price_info = f"\n\n📊 **{com['name']} — Live Price**\nCurrent: ${price_data['price']:,.2f} | Today: {'▲ +' if chg >= 0 else '▼ '}{abs(chg):.2f}%"
            price_fact = f"Live data: {com['name']} is at ${price_data['price']:,.2f} ({chg:+.2f}% today)."
        else:
            price_fact = "No live price is available, so do not quote any prices."

        answer = llm_text(with_history(
            f"""As a commodity market expert, answer this question: {user_query}

{price_fact}
Explain what drives this commodity's price and the investment outlook in 4-5 plain-English sentences.
Do not quote any price or percentage other than the live data above, and do not predict exact future prices.""", chat_history))

        # Get linked stocks to show as cards
        linked_stocks = get_commodity_linked_stocks(com["name"].lower(), com["category"]) if com else []

        full_answer = answer + price_info
        if linked_stocks:
            full_answer += f"\n\n📈 **{com['name']}-Linked Stocks** — showing top companies below:"

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
        recommendation = generate_recommendation(user_query, linked_stocks, context, chat_history)
        return {
            "intent": "commodity_stocks",
            "stocks_found": len(linked_stocks),
            "recommendation": recommendation,
            "portfolio_review": None,
            "top_stocks": linked_stocks[:10],
            "all_stocks": linked_stocks
        }

    # ── PORTFOLIO: portfolio strategy questions, tailored to the user's watchlist ──
    if intent == "portfolio":
        holdings = holdings_context(portfolio_tickers)
        if holdings:
            answer = llm_text(with_history(
                f"""As a portfolio management expert, answer this question: {user_query}

The user's watchlist (stocks they follow): {holdings}

Give practical, actionable advice in plain English (4-6 sentences) that refers to these actual stocks, for example sector concentration or risk. Only use the facts listed above. End with a note that this is not financial advice.""", chat_history))
        else:
            answer = route.get("answer", "") or llm_text(with_history(
                f"As a portfolio management expert, answer this question: {user_query}. Give practical, actionable advice in plain English (4-5 sentences). End with a note that this is not financial advice.", chat_history))
        # Also show some recommended stocks (not ones already on the watchlist)
        owned = {str(t).upper() for t in (portfolio_tickers or [])}
        stocks = [x for x in screen_stocks({"buy_signal": "BUY", "beta": {"max": 1.2}}, limit=20) if x["ticker"] not in owned][:10]
        return {"intent": "portfolio", "stocks_found": len(stocks), "recommendation": answer, "portfolio_review": None, "top_stocks": stocks[:5], "all_stocks": stocks}

    # ── ECONOMY/MACRO: economic questions, grounded in live market data ──
    if intent == "economy_macro":
        market_data = get_macro_context()
        data_block = f"Live market data:\n{market_data}" if market_data else "No live market data is available right now."
        answer = llm_text(with_history(
            f"""As a macro economist, answer this question: {user_query}

{data_block}

Explain in plain English (4-5 sentences) and mention which types of stocks or assets are affected.
Quote current numbers only from the live data above and describe what they show. Do not call a figure higher or lower than "expected" and do not state causes you cannot see in the data. If the question needs figures you were not given (inflation rate, interest-rate decisions, GDP), say you don't have live figures for those and explain the mechanism instead.""", chat_history))
        return {"intent": "economy_macro", "stocks_found": 0, "recommendation": answer, "portfolio_review": None, "top_stocks": [], "all_stocks": []}

    # ── SPECIFIC STOCK: user asked about one company ──
    if intent == "specific_stock":
        company_name = route.get("company_name", "")
        ticker_hint = route.get("ticker_hint", "")
        print(f"Looking for: {company_name} ({ticker_hint})")

        ticker = find_ticker_in_db(company_name, ticker_hint)
        print(f"Found ticker: {ticker}")

        if not ticker:
            not_found_msg = f"I couldn't find **{company_name or ticker_hint}** in our database. We track {count_tracked_stocks():,} US-listed stocks, but this company may not be one of them. Try searching with the exact ticker symbol."
            return {
                "intent": "specific_stock",
                "stocks_found": 0,
                "recommendation": not_found_msg,
                "portfolio_review": None,
                "top_stocks": [],
                "all_stocks": []
            }

        recommendation = generate_single_stock_response(ticker, user_query, chat_history)
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

    # ── COMPARE: side-by-side comparison of specific stocks ──
    if intent == "compare":
        tickers, missing = resolve_compare_tickers(route, user_query)
        snapshots = [s for s in (get_stock_snapshot(t) for t in tickers) if s]

        if len(snapshots) < 2:
            if missing:
                msg = f"I couldn't find {', '.join(missing)} in our database, so I can't compare them. Try the exact ticker symbols, for example 'compare AAPL and MSFT'."
            elif snapshots:
                msg = f"I only found {snapshots[0]['ticker']} to compare. Tell me which other stock(s) to put next to it, for example 'compare {snapshots[0]['ticker']} and MSFT'."
            else:
                msg = "Tell me which stocks to compare, for example 'compare AAPL and MSFT' or 'Tesla vs Rivian'."
            return {"intent": "compare", "stocks_found": len(snapshots), "recommendation": msg, "portfolio_review": None, "top_stocks": snapshots, "all_stocks": snapshots}

        recommendation = generate_comparison(user_query, snapshots, chat_history)
        if missing:
            recommendation = f"Note: I couldn't find {', '.join(missing)} in our database, so it's left out.\n\n" + recommendation
        return {
            "intent": "compare",
            "stocks_found": len(snapshots),
            "recommendation": recommendation,
            "portfolio_review": None,
            "top_stocks": snapshots,
            "all_stocks": snapshots
        }

    # ── MARKET QUESTION: general investment/market query ──
    if intent == "market_question":
        stocks = screen_stocks({"buy_signal": "BUY"}, limit=30)
        context = "User is asking a general market or investment question."
        recommendation = generate_recommendation(user_query, stocks, context, chat_history)
        return {
            "intent": "market_question",
            "stocks_found": len(stocks),
            "recommendation": recommendation,
            "portfolio_review": None,
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

    recommendation = generate_recommendation(user_query, stocks, "", chat_history)

    return {
        "intent": intent,
        "stocks_found": len(stocks),
        "recommendation": recommendation,
        "portfolio_review": None,
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
    div = round(s.get('dividend_yield', 0), 2) if s.get('dividend_yield') else None

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

    response = llm_create(messages=[{"role": "user", "content": prompt}],
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
