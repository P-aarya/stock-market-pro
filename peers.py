"""
Peer comparison: pick a stock's closest competitors and build a side-by-side metric table.

Peer selection is a hybrid:
  1. The database builds a candidate pool (same industry, then same sector, nearest market cap).
  2. The LLM picks the closest real competitors, but only from that pool, so it cannot invent tickers.
  3. If the LLM is unavailable, the first candidates in database order are used.
"""
import json
import math
import re
import statistics
import threading
import time
from datetime import date, datetime
from decimal import Decimal

from screener import get_db_connection

POOL_SIZE = 25
MAX_STOCKS = 6                      # the stock itself plus up to five peers
AI_CACHE_TTL = 7 * 24 * 3600        # a company's competitors change slowly
FALLBACK_CACHE_TTL = 10 * 60        # retry the AI soon after a fallback
_peer_cache = {}
_cache_lock = threading.Lock()


# ── database helpers ───────────────────────────────────────

def _plain(value):
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None  # NaN / Infinity are not valid JSON
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _query(sql, params=()):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        return [{k: _plain(v) for k, v in row.items()} for row in cursor.fetchall()]
    finally:
        conn.close()


def _name_key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# ── candidate pool ─────────────────────────────────────────

_POOL_COLUMNS = "l.ticker, l.company_name, l.sector, l.industry, l.market_cap"
_POOL_FILTER = "l.current_price IS NOT NULL AND l.market_cap IS NOT NULL AND (l.sector != 'Indices' OR l.sector IS NULL)"


def get_target(ticker):
    rows = _query(f"SELECT {_POOL_COLUMNS} FROM stocks_live l WHERE l.ticker = %s AND l.current_price IS NOT NULL", (ticker,))
    return rows[0] if rows else None


def candidate_pool(target, size=POOL_SIZE):
    """Same industry first, then same sector, each ordered by closeness in market cap."""
    cap = target.get("market_cap") or 1
    nearest = "ORDER BY ABS(LN(l.market_cap) - LN(%s)) ASC"
    pool, seen = [], {target["ticker"]}
    seen_names = {_name_key(target["company_name"])}

    def add(rows):
        for r in rows:
            name = _name_key(r["company_name"])
            if r["ticker"] in seen or name in seen_names:
                continue  # skip the stock itself and duplicate share classes (GOOG / GOOGL)
            seen.add(r["ticker"])
            seen_names.add(name)
            pool.append(r)

    if target.get("industry"):
        add(_query(f"SELECT {_POOL_COLUMNS} FROM stocks_live l WHERE l.industry = %s AND l.ticker != %s AND {_POOL_FILTER} {nearest} LIMIT %s",
                   (target["industry"], target["ticker"], cap, size + 2)))
    if len(pool) < size and target.get("sector"):
        add(_query(f"SELECT {_POOL_COLUMNS} FROM stocks_live l WHERE l.sector = %s AND l.ticker != %s AND {_POOL_FILTER} {nearest} LIMIT %s",
                   (target["sector"], target["ticker"], cap, size * 2)))
    return pool[:size]


# ── peer selection ─────────────────────────────────────────

def _money(n):
    n = float(n or 0)
    return f"${n / 1e12:.1f}T" if n >= 1e12 else f"${n / 1e9:.0f}B" if n >= 1e9 else f"${n / 1e6:.0f}M"


def _ai_pick(target, pool, n):
    """Ask the LLM for the n closest competitors from the pool. Returns a list of tickers (may be short or empty)."""
    from agent import llm_create  # imported here so peers.py and agent.py can import each other later

    lines = "\n".join(f"{r['ticker']} - {r['company_name']} ({r.get('industry') or r.get('sector') or 'n/a'}, {_money(r['market_cap'])})" for r in pool)
    prompt = f"""You are an equity analyst choosing peer companies for a comparison.

Target: {target['ticker']} - {target['company_name']} ({target.get('industry') or target.get('sector')}, {_money(target['market_cap'])})

Candidates:
{lines}

Choose the {n} candidates that are the closest DIRECT competitors of the target: companies that sell similar products to similar customers. Prefer real competitors over companies that are only similar in size. Use only tickers from the candidate list, closest first.
Return ONLY JSON: {{"peers": ["TICKER", ...]}}"""
    response = llm_create(messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=1500,
                          response_format={"type": "json_object"})
    data = json.loads(response.choices[0].message.content)
    allowed = {r["ticker"] for r in pool}
    picked = []
    for t in data.get("peers", []) if isinstance(data, dict) else []:
        t = str(t).strip().upper()
        if t in allowed and t not in picked:
            picked.append(t)
    return picked[:n]


def suggest_peers(ticker, n=4):
    """Returns {"target", "peers", "source"} or None if the ticker is unknown. source is 'ai' or 'database'."""
    ticker = ticker.upper()
    n = max(1, min(n, MAX_STOCKS - 1))
    key = (ticker, n)
    with _cache_lock:
        hit = _peer_cache.get(key)
        if hit and hit["expires"] > time.time():
            return hit["value"]

    target = get_target(ticker)
    if not target:
        return None
    pool = candidate_pool(target)
    by_ticker = {r["ticker"]: r for r in pool}

    picked, source = [], "database"
    if len(pool) > n:
        try:
            picked = _ai_pick(target, pool, n)
            if picked:
                source = "ai"
        except Exception as e:
            print(f"Peer AI pick failed for {ticker}: {e}")
    # top up from database order (also the whole answer when the AI is unavailable)
    for r in pool:
        if len(picked) >= n:
            break
        if r["ticker"] not in picked:
            picked.append(r["ticker"])

    value = {"target": target, "peers": [by_ticker[t] for t in picked], "source": source}
    ttl = AI_CACHE_TTL if source == "ai" or len(pool) <= n else FALLBACK_CACHE_TTL
    with _cache_lock:
        _peer_cache[key] = {"value": value, "expires": time.time() + ttl}
    return value


# ── comparison table ───────────────────────────────────────

# key, label, format, which direction is better, how to treat non-positive values
#   format: ratio (12.3x), pct (fraction -> %), pp (already a percent), num, usd, text
#   better: "higher" / "lower" / None (shown, but never highlighted)
#   positive_only: zero or negative values are not comparable (e.g. a negative P/E)
METRIC_GROUPS = [
    ("Valuation", [
        ("pe_ratio", "P/E", "ratio", "lower", True),
        ("forward_pe", "Forward P/E", "ratio", "lower", True),
        ("peg_ratio", "PEG", "ratio", "lower", True),
        ("ps_ratio", "Price / Sales", "ratio", "lower", True),
        ("ev_ebitda", "EV / EBITDA", "ratio", "lower", True),
    ]),
    ("Growth", [
        ("revenue_growth", "Revenue growth", "pct", "higher", False),
        ("earnings_growth", "Earnings growth", "pct", "higher", False),
    ]),
    ("Profitability", [
        ("gross_margin", "Gross margin", "pct", "higher", False),
        ("operating_margin", "Operating margin", "pct", "higher", False),
        ("profit_margin", "Profit margin", "pct", "higher", False),
        ("roe", "Return on equity", "pct", "higher", False),
    ]),
    ("Financial health", [
        ("debt_to_equity", "Debt / equity", "num", "lower", True),
        ("current_ratio", "Current ratio", "num", "higher", True),
        ("free_cash_flow", "Free cash flow", "usd", "higher", False),
    ]),
    ("Dividend", [
        ("dividend_yield", "Dividend yield", "pp", "higher", False),
        ("payout_ratio", "Payout ratio", "pct", None, False),
    ]),
    ("Risk", [
        ("beta", "Beta", "num", "lower", False),
        ("volatility", "Volatility", "num", "lower", False),
        ("rsi_14", "RSI (14)", "num", None, False),
    ]),
    ("Model outlook", [
        ("buy_signal", "Model signal", "text", "higher", False),
        ("model_move_1w", "1-week model estimate", "pp", "higher", False),
        ("confidence_pct", "Model confidence", "pp", "higher", False),
        ("risk_level", "Model risk level", "text", "lower", False),
        ("analyst_rating", "Analyst rating", "text", None, False),
        ("upside_pct", "Upside to analyst target", "pp", "higher", False),
    ]),
    ("Size", [
        ("market_cap", "Market cap", "usd", None, False),
    ]),
]

# Text values that have a natural order, so they can still be ranked
TEXT_RANKS = {
    "buy_signal": {"strong sell": 1, "sell": 2, "hold": 3, "buy": 4, "strong buy": 5},
    "risk_level": {"low": 1, "medium": 2, "high": 3},
}

SIMILAR_WITHIN = 0.05   # within 5% of the peer median counts as "in line"

_COMPARE_SQL = """
    SELECT l.ticker, l.company_name, l.sector, l.industry, l.current_price, l.price_change_pct,
           l.market_cap, l.last_updated AS price_updated,
           f.pe_ratio, f.forward_pe, f.peg_ratio, f.ps_ratio, f.ev_ebitda,
           f.revenue_growth, f.earnings_growth, f.gross_margin, f.operating_margin, f.profit_margin, f.roe,
           f.debt_to_equity, f.current_ratio, f.free_cash_flow, f.dividend_yield, f.payout_ratio, f.beta,
           t.rsi_14, t.volatility,
           p.buy_signal, p.risk_level, p.confidence_pct, p.target_1w, p.run_date AS prediction_date,
           a.analyst_rating, a.upside_pct
    FROM stocks_live l
    LEFT JOIN stocks_fundamentals f ON f.ticker = l.ticker
    LEFT JOIN stocks_technicals t ON t.ticker = l.ticker
    LEFT JOIN stocks_predictions p ON p.ticker = l.ticker
    LEFT JOIN stocks_analyst a ON a.ticker = l.ticker
    WHERE l.ticker IN ({placeholders}) AND l.current_price IS NOT NULL
"""


def _number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _comparable(key, v, positive_only):
    """The value as a rankable number, or None if it should be left out of rankings and medians."""
    if key in TEXT_RANKS:
        return TEXT_RANKS[key].get(str(v).strip().lower()) if v else None
    v = _number(v)
    if v is None or (positive_only and v <= 0):
        return None
    return v


def build_comparison(tickers):
    """Side-by-side table for 2+ tickers. The first ticker is the target the others are compared against."""
    wanted = []
    for t in tickers:
        t = t.strip().upper()
        if t and t not in wanted:
            wanted.append(t)
    wanted = wanted[:MAX_STOCKS]
    rows = {r["ticker"]: r for r in _query(_COMPARE_SQL.format(placeholders=",".join(["%s"] * len(wanted))), wanted)} if wanted else {}

    found = [t for t in wanted if t in rows]
    missing = [t for t in wanted if t not in rows]
    for r in rows.values():
        price, target_price = r.get("current_price"), r.get("target_1w")
        r["model_move_1w"] = round((target_price - price) / price * 100, 2) if _number(price) and _number(target_price) else None
        r["upside_pct"] = _number(r.get("upside_pct"))

    target = found[0] if found else None
    groups = []
    for group_name, metrics in METRIC_GROUPS:
        out = []
        for key, label, fmt, better, positive_only in metrics:
            values = {t: rows[t].get(key) for t in found}
            comparable = {t: _comparable(key, v, positive_only) for t, v in values.items()}
            usable = {t: c for t, c in comparable.items() if c is not None}

            best = None
            if better and len(usable) >= 2:
                pick = max if better == "higher" else min
                top = pick(usable.values())
                winners = [t for t, c in usable.items() if c == top]
                best = winners[0] if len(winners) == 1 else None  # a tie highlights no one

            median = position = None
            peer_values = [c for t, c in usable.items() if t != target]
            if key not in TEXT_RANKS and len(peer_values) >= 2:
                median = statistics.median(peer_values)
                mine = usable.get(target)
                if better and mine is not None:
                    # "in line" = within 5% of the median, or within 1 percentage point for percentages
                    tolerance = max(SIMILAR_WITHIN * abs(median), {"pct": 0.01, "pp": 1.0}.get(fmt, 0))
                    if abs(mine - median) <= tolerance:
                        position = "similar"
                    else:
                        position = "better" if (mine > median) == (better == "higher") else "worse"

            out.append({"key": key, "label": label, "format": fmt, "better": better,
                        "values": values, "best": best, "median": median, "position": position})
        groups.append({"name": group_name, "metrics": out})

    companies = {t: {k: rows[t][k] for k in ("company_name", "sector", "industry", "current_price", "price_change_pct", "market_cap")} for t in found}
    stamps = [rows[t]["price_updated"] for t in found if rows[t].get("price_updated")]
    return {"target": target, "tickers": found, "missing": missing, "companies": companies,
            "groups": groups, "prices_as_of": max(stamps) if stamps else None}
