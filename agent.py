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
def classify_intent(user_query: str) -> str:
    q = user_query.lower()

    # Trending / movers
    if any(x in q for x in ["trending up", "going up today", "best performers", "top gainers", "rising today", "up today", "gaining today"]):
        return "trending_up"
    if any(x in q for x in ["trending down", "falling today", "losers today", "dropping today", "down today"]):
        return "trending_down"

    # Similar stocks
    if any(x in q for x in ["like ", "similar to", "alternative to", "instead of", "better than"]):
        return "similar"

    # What to buy today — vague broad queries
    if any(x in q for x in ["what should i buy", "best stocks to buy", "what to buy", "good stocks", "recommend me", "suggest me", "what looks good", "what's good", "top stocks today"]):
        return "broad_recommend"

    # Safe / low risk
    if any(x in q for x in ["safe", "low risk", "stable", "defensive", "conservative", "steady"]):
        return "safe"

    # Dividend focused
    if any(x in q for x in ["dividend", "income", "yield", "passive income", "pays dividend"]):
        return "dividend"

    # Growth focused
    if any(x in q for x in ["growth", "fast growing", "high growth", "growing fast", "revenue growth"]):
        return "growth"

    # Undervalued
    if any(x in q for x in ["undervalued", "cheap", "bargain", "value stock", "low pe", "discount"]):
        return "undervalued"

    # Sector based
    sectors = ["tech", "technology", "healthcare", "energy", "finance", "financial", "retail",
               "biotech", "semiconductor", "real estate", "utilities", "consumer", "industrial",
               "material", "banking", "insurance", "automotive", "media", "cloud", "crypto"]
    if any(x in q for x in sectors):
        return "sector"

    # Price based
    if any(x in q for x in ["under $", "below $", "above $", "more than $", "less than $", "price", "dollar", "cheap stock", "penny"]):
        return "price"

    # Default — use LLM to translate
    return "filter"


# ─────────────────────────────────────────
# EXTRACT SIMILAR TICKER FROM QUERY
# ─────────────────────────────────────────
def extract_ticker(query: str) -> str:
    # Look for known patterns like "like Apple", "similar to TSLA", "better than NVDA"
    patterns = [
        r"like ([A-Z]{1,5})\b",
        r"similar to ([A-Z]{1,5})\b",
        r"alternative to ([A-Z]{1,5})\b",
        r"instead of ([A-Z]{1,5})\b",
        r"better than ([A-Z]{1,5})\b",
    ]
    for p in patterns:
        m = re.search(p, query.upper())
        if m:
            return m.group(1)

    # Company name to ticker mapping for common ones
    name_map = {
        "apple": "AAPL", "tesla": "TSLA", "nvidia": "NVDA", "microsoft": "MSFT",
        "amazon": "AMZN", "google": "GOOGL", "meta": "META", "netflix": "NFLX",
        "jpmorgan": "JPM", "johnson": "JNJ", "visa": "V", "exxon": "XOM",
        "berkshire": "BRK-B", "walmart": "WMT", "disney": "DIS"
    }
    q = query.lower()
    for name, ticker in name_map.items():
        if name in q:
            return ticker
    return None


# ─────────────────────────────────────────
# TRANSLATE PLAIN ENGLISH TO FILTERS
# ─────────────────────────────────────────
def translate_to_filters(user_query: str) -> dict:
    prompt = f"""
You are a stock screening assistant. Convert the user's request into database filter conditions.

User said: "{user_query}"

Available filter fields:
- current_price: {{"min": number, "max": number}} — USE THIS for any price mention e.g. "above $100" = {{"min": 100}}, "under $50" = {{"max": 50}}, "between $20 and $100" = {{"min": 20, "max": 100}}
- market_cap_category: "small" (under $2B), "mid" ($2B-$10B), "large" (above $10B)
- pe_ratio: {{"min": number, "max": number}} — "low PE" = {{"max": 20}}, "high PE" = {{"min": 30}}
- peg_ratio: {{"min": number, "max": number}} — "undervalued growth" = {{"max": 1.0}}
- revenue_growth: {{"min": number, "max": number}} — decimals e.g. 0.10 = 10%, "strong growth" = {{"min": 0.10}}
- earnings_growth: {{"min": number, "max": number}}
- profit_margin: {{"min": number, "max": number}} — "high margin" = {{"min": 0.15}}, "profitable" = {{"min": 0.05}}
- operating_margin: {{"min": number, "max": number}}
- roe: {{"min": number, "max": number}} — "strong ROE" = {{"min": 0.15}}
- debt_to_equity: {{"min": number, "max": number}} — "low debt" = {{"max": 0.5}}, "no debt" = {{"max": 0.1}}
- dividend_yield: {{"min": number, "max": number}} — decimals e.g. 0.02 = 2%, "high dividend" = {{"min": 0.03}}
- beta: {{"min": number, "max": number}} — "low volatility" = {{"max": 0.8}}, "high volatility" = {{"min": 1.5}}
- market_cap: {{"min": number, "max": number}} — in USD
- sector: string — map common words: "tech" = "Technology", "health" = "Healthcare", "bank" = "Banking", "oil" = "Energy", "pharma" = "Healthcare"
- price_change_pct: {{"min": number, "max": number}} — "up today" = {{"min": 0}}, "down today" = {{"max": 0}}
- buy_signal: "BUY", "HOLD", or "SELL"
- pays_dividend: true — use when user asks for dividend paying stocks
- positive_fcf: true — use when user asks for cash flow positive companies

Common phrase mappings:
- "cheap" or "affordable" = current_price max 50 OR pe_ratio max 15
- "expensive" = current_price min 200
- "penny stocks" = current_price max 5
- "blue chip" = market_cap_category large, pe_ratio max 30
- "safe" or "stable" = beta max 0.8, debt_to_equity max 0.5
- "growth stocks" = revenue_growth min 0.15, earnings_growth min 0.10
- "value stocks" = pe_ratio max 20, peg_ratio max 1.0
- "profitable" = profit_margin min 0.05
- "no debt" = debt_to_equity max 0.1
- "strong cash flow" = positive_fcf true

Rules:
- ALWAYS use current_price filter when user mentions any dollar amount or price
- Be generous with filters — better to return more results than too few
- Return ONLY valid JSON, no explanation, no markdown backticks
- If completely unsure, return empty object {{}}

Return only JSON like: {{"current_price": {{"min": 100}}, "profit_margin": {{"min": 0.10}}}}
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        filters = json.loads(raw)
    except:
        filters = {}

    return filters


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
def generate_recommendation(user_query: str, stocks: list, context: str = "") -> str:
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

    prompt = f"""
You are a professional stock market analyst at an investment research platform called Stock Market Pro.

The user asked: "{user_query}"

Here are the matching stocks from our live database and AI prediction model:
{stock_summary}

Market context: {market_context}
{f'Additional context: {context}' if context else ''}

Write a clear, structured stock analysis response. Format it EXACTLY like this for each stock:

📊 [TICKER] — [Company Name]
Current Price: $X.XX | 7-Day Estimate: $X.XX ([+/-]X.X%)
Sector: [sector] | Signal: [BUY/HOLD/SELL]

Why this stands out:
[2-3 sentences explaining why based on the data — mention specific numbers like revenue growth, margins, price movement]

Risk to watch:
[1 sentence about the main risk or concern]

──────────────

Write this for the top 3-5 stocks only. Start with 1 sentence summarising what you found.

Rules:
- Only use numbers from the data above. Never invent figures.
- Say "our model estimates" not "the stock will reach"
- Keep each stock section concise — 4-6 lines max
- Plain English — no jargon
- End with: "⚠️ These are model-based estimates, not financial advice. Always invest within your risk tolerance."
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
def run_agent(user_query: str, portfolio_tickers: list = None):
    print(f"\nUser query: {user_query}")

    intent = classify_intent(user_query)
    print(f"Intent: {intent}")

    stocks = []
    context = ""

    if intent == "trending_up":
        stocks = get_trending_stocks("up", limit=20)
        context = "User wants stocks that are trending upward today."

    elif intent == "trending_down":
        stocks = get_trending_stocks("down", limit=20)
        context = "User wants stocks that are falling today."

    elif intent == "similar":
        ticker = extract_ticker(user_query)
        if ticker:
            stocks = find_similar_stocks(ticker, limit=20)
            context = f"User wants stocks similar to {ticker}."
        else:
            filters = translate_to_filters(user_query)
            stocks = screen_stocks(filters, limit=30)

    elif intent == "broad_recommend":
        # Best stocks right now — high momentum, bullish signal
        stocks = screen_stocks({"buy_signal": "BUY"}, limit=30)
        context = "User wants general stock recommendations based on current market conditions."

    elif intent == "safe":
        stocks = screen_stocks({
            "beta": {"max": 0.8},
            "debt_to_equity": {"max": 0.5},
            "profit_margin": {"min": 0.05}
        }, limit=30)
        context = "User wants low risk, stable stocks."

    elif intent == "dividend":
        stocks = screen_stocks({
            "pays_dividend": True,
            "dividend_yield": {"min": 0.02}
        }, limit=30)
        context = "User wants dividend paying stocks."

    elif intent == "growth":
        stocks = screen_stocks({
            "revenue_growth": {"min": 0.10},
            "earnings_growth": {"min": 0.05}
        }, limit=30)
        context = "User wants high growth stocks."

    elif intent == "undervalued":
        stocks = screen_stocks({
            "pe_ratio": {"max": 20},
            "profit_margin": {"min": 0.05}
        }, limit=30)
        context = "User wants undervalued stocks with solid fundamentals."

    elif intent == "sector":
        filters = translate_to_filters(user_query)
        stocks = screen_stocks(filters, limit=30)
        context = f"User wants stocks from a specific sector."

    elif intent == "price":
        filters = translate_to_filters(user_query)
        stocks = screen_stocks(filters, limit=30)
        context = "User is filtering by price range."

    else:
        # Default — use LLM to translate
        filters = translate_to_filters(user_query)
        print(f"Filters: {filters}")
        stocks = screen_stocks(filters, limit=30)

    print(f"Stocks found: {len(stocks)}")

    recommendation = generate_recommendation(user_query, stocks, context)

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
