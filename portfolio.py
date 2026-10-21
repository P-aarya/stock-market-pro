"""
Portfolio holdings: validated add / edit / delete, correct summary maths, and an AI review that uses the
user's real positions (quantity, cost basis, weight) instead of rating each ticker in isolation.

Everything here belongs to one account. Every function that reads or changes holdings or transactions takes the
signed-in `user_id` first, and every SQL statement on those tables filters by it, so one person can never see or
touch another person's records.
"""
import math
import re
from datetime import date, datetime
from decimal import Decimal

from screener import get_db_connection

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,10}$")
MAX_REVIEW_STOCKS = 15
CONCENTRATED_POSITION = 25.0   # % of portfolio value in a single stock
CONCENTRATED_SECTOR = 50.0     # % of portfolio value in a single sector
RISKY_SHARE = 30.0             # % of portfolio value in stocks the model rates High risk


# The database labels stocks with 23 sector names that overlap (Banking vs Financials, Cloud & SaaS vs Technology).
# For the portfolio breakdown they are folded into standard groups. This follows the app's own labels, so a stock the
# app files under "Cloud & SaaS" (such as AMZN) lands in Technology.
SECTOR_GROUPS = {
    "Technology": "Technology", "Semiconductors": "Technology", "Cloud & SaaS": "Technology", "Physical AI & Robotics": "Technology",
    "Media & Entertainment": "Communication Services",
    "Healthcare": "Healthcare", "Biotech": "Healthcare",
    "Financials": "Financials", "Banking": "Financials", "Insurance": "Financials", "Crypto & Fintech": "Financials",
    "Consumer Discretionary": "Consumer Discretionary", "Retail": "Consumer Discretionary", "Automotive": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Energy": "Energy", "Clean Energy & ESG": "Energy",
    "Industrials": "Industrials", "Aerospace & Defense": "Industrials",
    "Materials": "Materials", "Real Estate": "Real Estate",
    "Utilities": "Utilities", "Water & Environment": "Utilities",
}


def sector_group(sector):
    return SECTOR_GROUPS.get(sector or "", "Other")


class PortfolioError(Exception):
    """A problem the user can act on. `status` is the HTTP status to return."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


# ── database helpers ──────────────────────────────────────

def _plain(v):
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _query(sql, params=()):
    conn = get_db_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return [{k: _plain(v) for k, v in row.items()} for row in cur.fetchall()]
    finally:
        conn.close()


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


# ── holdings and summary ──────────────────────────────────

def with_values(rows):
    """Add invested / current value and P&L to raw holding rows. A holding without a live price has no value."""
    out = []
    for r in rows:
        qty, avg, px = _num(r.get("quantity")), _num(r.get("avg_buy_price")), _num(r.get("current_price"))
        invested = round(qty * avg, 2) if qty is not None and avg is not None else None
        priced = qty is not None and px is not None and px > 0
        value = round(px * qty, 2) if priced else None
        out.append({**r,
                    "invested_value": invested,
                    "current_value": value,
                    "unrealized_pnl": round((px - avg) * qty, 2) if priced and avg is not None else None,
                    "pnl_pct": round((px - avg) / avg * 100, 2) if priced and avg else None,
                    "sector_group": sector_group(r.get("sector"))})
    return out


def list_holdings(user_id):
    rows = _query("""
        SELECT p.id, p.ticker, p.company_name, p.quantity, p.avg_buy_price, p.buy_date, p.notes,
               IF(l.last_updated >= DATE_SUB((SELECT MAX(last_updated) FROM stocks_live), INTERVAL 14 DAY), l.current_price, NULL) AS current_price,
               l.price_change_pct AS day_change_pct,
               COALESCE(l.sector, p.sector) AS sector,
               a.analyst_rating, a.target_price, a.upside_pct
        FROM portfolio p
        LEFT JOIN stocks_live l ON p.ticker = l.ticker
        LEFT JOIN stocks_analyst a ON p.ticker = a.ticker
        WHERE p.user_id = %s
    """, (user_id,))
    rows = with_values(rows)
    rows.sort(key=lambda r: (r["current_value"] is None, -(r["current_value"] or 0)))  # biggest first, unpriced last
    return rows


def summarize(rows):
    """
    Totals for the dashboard cards.
      * Value, P&L and the return only count holdings that have a live price, so an unpriced holding
        cannot inflate the cost basis they are compared with.
      * Today's change uses yesterday's value (value / (1 + day%)), the amount that actually moved.
    """
    priced = [r for r in rows if r["current_value"] is not None]
    value = sum(r["current_value"] for r in priced)
    priced_cost = sum(r["invested_value"] or 0 for r in priced)
    pnl = sum(r["unrealized_pnl"] or 0 for r in priced)
    day = 0.0
    for r in priced:
        pct = _num(r.get("day_change_pct"))
        if pct is not None and pct > -100:
            day += r["current_value"] - r["current_value"] / (1 + pct / 100)
    return {
        "total_value": round(value, 2),
        "total_invested": round(sum(r["invested_value"] or 0 for r in rows), 2),
        "priced_invested": round(priced_cost, 2),
        "total_pnl": round(pnl, 2),
        "return_pct": round(pnl / priced_cost * 100, 2) if priced_cost else None,
        "day_change": round(day, 2),
        "day_change_pct": round(day / (value - day) * 100, 2) if value - day else None,
        "total_holdings": len(rows),
        "unpriced_holdings": len(rows) - len(priced),
        "unpriced_tickers": [r["ticker"] for r in rows if r["current_value"] is None],
    }


# ── validation ────────────────────────────────────────────

def _check_position(quantity, avg_buy_price, buy_date, notes, price_label="Average buy price", date_label="Buy date"):
    q, p = _num(quantity), _num(avg_buy_price)
    if q is None or not 0 < q <= 1e9:
        raise PortfolioError("Quantity must be a positive number.")
    if p is None or not 0 < p <= 1e7:
        raise PortfolioError(f"{price_label} must be a positive number.")
    if isinstance(buy_date, str):
        try:
            buy_date = datetime.strptime(buy_date, "%Y-%m-%d").date()
        except ValueError:
            raise PortfolioError(f"{date_label} must look like 2025-03-14.")
    if buy_date > date.today():
        raise PortfolioError(f"{date_label} can't be in the future.")
    if buy_date.year < 1950:
        raise PortfolioError(f"{date_label} is too far in the past.")
    return q, p, buy_date, (notes or "").strip()[:200]


def merge_lots(lots, quantity, price):
    """Combine existing lots with a new purchase: total quantity at the weighted-average cost."""
    total_q = sum(l["quantity"] for l in lots) + quantity
    total_cost = sum(l["quantity"] * l["avg_buy_price"] for l in lots) + quantity * price
    return total_q, total_cost / total_q


# ── add / edit / delete ───────────────────────────────────

def add_holding(user_id, ticker, quantity, avg_buy_price, buy_date, notes=""):
    ticker = (ticker or "").strip().upper()
    if not TICKER_RE.match(ticker):
        raise PortfolioError("That doesn't look like a ticker symbol.")
    q, p, d, notes = _check_position(quantity, avg_buy_price, buy_date, notes)

    conn = get_db_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT company_name, sector FROM stocks_live WHERE ticker = %s AND current_price IS NOT NULL", (ticker,))
        stock = cur.fetchone()
        if not stock:
            raise PortfolioError(f"{ticker} isn't a stock we track, so it can't be added.")
        cur.execute("SELECT id, quantity, avg_buy_price, buy_date, notes FROM portfolio WHERE user_id = %s AND ticker = %s ORDER BY id", (user_id, ticker))
        lots = [{k: _plain(v) for k, v in row.items()} for row in cur.fetchall()]

        if lots:  # buying more of a stock you hold: one position at the weighted-average cost
            total_q, avg = merge_lots(lots, q, p)
            first = lots[0]
            earliest = min([d] + [l["buy_date"] for l in lots if l["buy_date"]])
            note = notes or first.get("notes") or ""
            cur.execute("UPDATE portfolio SET quantity=%s, avg_buy_price=%s, buy_date=%s, notes=%s WHERE id=%s AND user_id=%s",
                        (total_q, round(avg, 4), earliest, note, first["id"], user_id))
            for extra in lots[1:]:
                cur.execute("DELETE FROM portfolio WHERE id = %s AND user_id = %s", (extra["id"], user_id))
            holding_id, message = first["id"], f"Added {q:g} more {ticker}. Position is now {total_q:g} shares at ${avg:,.2f}."
        else:
            cur.execute("""INSERT INTO portfolio (user_id, ticker, company_name, quantity, avg_buy_price, buy_date, sector, notes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (user_id, ticker, stock["company_name"] or ticker, q, p, d, stock["sector"] or "Unknown", notes))
            holding_id, message = cur.lastrowid, f"Added {q:g} shares of {ticker}."
        conn.commit()
        return {"id": holding_id, "ticker": ticker, "message": message}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_holding(user_id, holding_id, quantity, avg_buy_price, buy_date, notes=""):
    q, p, d, notes = _check_position(quantity, avg_buy_price, buy_date, notes)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM portfolio WHERE id = %s AND user_id = %s", (holding_id, user_id))
        row = cur.fetchone()
        if not row:
            raise PortfolioError("That holding no longer exists.", 404)
        cur.execute("UPDATE portfolio SET quantity=%s, avg_buy_price=%s, buy_date=%s, notes=%s WHERE id=%s AND user_id=%s",
                    (q, p, d, notes, holding_id, user_id))
        conn.commit()
        return {"id": holding_id, "ticker": row[0], "message": f"{row[0]} updated."}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_holding(user_id, holding_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM portfolio WHERE id = %s AND user_id = %s", (holding_id, user_id))
        row = cur.fetchone()
        if not row:
            raise PortfolioError("That holding no longer exists.", 404)
        cur.execute("DELETE FROM portfolio WHERE id = %s AND user_id = %s", (holding_id, user_id))
        conn.commit()
        return {"id": holding_id, "ticker": row[0], "message": f"{row[0]} removed."}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── AI review ─────────────────────────────────────────────

def resolve_entries(raw):
    """Turn a user's list of tickers and/or company names into tracked tickers. Returns (tickers, not_found)."""
    from agent import find_ticker_in_db  # imported here so the modules can import each other

    entries, seen = [], set()
    for item in raw:
        s = str(item).strip()
        if s and s.upper() not in seen:
            seen.add(s.upper())
            entries.append(s)
    entries = entries[:MAX_REVIEW_STOCKS]

    tickers_like = [e.upper() for e in entries if TICKER_RE.match(e)]
    known = set()
    if tickers_like:
        rows = _query("SELECT ticker FROM stocks_live WHERE current_price IS NOT NULL AND ticker IN (%s)" % ",".join(["%s"] * len(tickers_like)), tickers_like)
        known = {r["ticker"] for r in rows}

    resolved, not_found = [], []
    for e in entries:
        t = e.upper() if e.upper() in known else find_ticker_in_db(e, "")
        if t and t not in resolved:
            resolved.append(t)
        elif not t:
            not_found.append(e)
    return resolved, not_found


def _market_rows(tickers):
    """One query for everything the review needs about these stocks."""
    return {r["ticker"]: r for r in _query("""
        SELECT l.ticker, l.company_name, l.sector, l.current_price, l.price_change_pct,
               f.pe_ratio, f.revenue_growth, f.profit_margin, f.debt_to_equity, f.beta,
               p.buy_signal, p.trend, p.risk_level, p.target_1w,
               a.analyst_rating, a.target_price
        FROM stocks_live l
        LEFT JOIN stocks_fundamentals f ON f.ticker = l.ticker
        LEFT JOIN stocks_predictions p ON p.ticker = l.ticker
        LEFT JOIN stocks_analyst a ON a.ticker = l.ticker
        WHERE l.ticker IN (%s)""" % ",".join(["%s"] * len(tickers)), tickers)}


def portfolio_facts(positions):
    """Numbers the review is built on, computed here so the AI never has to do arithmetic."""
    priced = [p for p in positions if p.get("current_value")]
    total = sum(p["current_value"] for p in priced)
    facts = {"count": len(positions), "total_value": round(total, 2), "flags": []}
    if not priced or not total:
        return facts
    for p in priced:
        p["weight"] = p["current_value"] / total * 100
    ranked = sorted(priced, key=lambda p: -p["weight"])
    facts["largest"] = (ranked[0]["ticker"], ranked[0]["weight"])
    facts["top3"] = sum(p["weight"] for p in ranked[:3])
    sectors = {}
    for p in priced:
        group = sector_group(p.get("sector"))
        sectors[group] = sectors.get(group, 0) + p["weight"]
    facts["sectors"] = sorted(sectors.items(), key=lambda kv: -kv[1])
    cost = sum(p["invested_value"] for p in priced if p.get("invested_value"))
    if cost:
        facts["cost"] = round(cost, 2)
        facts["return_pct"] = round((total - cost) / cost * 100, 1)

    flags = facts["flags"]
    for p in ranked:
        if p["weight"] > CONCENTRATED_POSITION:
            flags.append(f"{p['ticker']} is {p['weight']:.0f}% of the portfolio (more than {CONCENTRATED_POSITION:.0f}% in one stock)")
    for name, w in facts["sectors"]:
        if w > CONCENTRATED_SECTOR:
            flags.append(f"{w:.0f}% of the portfolio is in {name}")
    risky = sum(p["weight"] for p in priced if str(p.get("risk_level")) == "High")
    if risky > RISKY_SHARE:
        flags.append(f"{risky:.0f}% of the portfolio is in stocks the model rates High risk")
    weak = [p for p in ranked if str(p.get("buy_signal")) in ("Sell", "Strong Sell") and p["weight"] >= 5]
    if weak:
        flags.append("Model sell signals on: " + ", ".join(f"{p['ticker']} ({p['weight']:.0f}%)" for p in weak))
    if len(positions) < 5:
        flags.append(f"Only {len(positions)} holding{'s' if len(positions) != 1 else ''}, so a single stock moves the whole portfolio")
    return facts


def snapshot_markdown(facts, with_positions):
    if not facts.get("total_value") and not with_positions:
        return ""
    lines = ["**Portfolio snapshot**"]
    if with_positions and facts.get("total_value"):
        ret = f" (cost ${facts['cost']:,.0f}, {facts['return_pct']:+.1f}%)" if facts.get("cost") else ""
        lines.append(f"- Value ${facts['total_value']:,.0f} across {facts['count']} position{'s' if facts['count'] != 1 else ''}{ret}")
        lines.append(f"- Largest position: {facts['largest'][0]} at {facts['largest'][1]:.1f}%; top 3 together {facts['top3']:.0f}%")
        lines.append("- Sector mix: " + " · ".join(f"{n} {w:.0f}%" for n, w in facts["sectors"][:5]))
    else:
        lines.append(f"- {facts['count']} stocks reviewed (no quantities given, so position sizes aren't assessed)")
    lines += [f"- ⚠️ {f}" for f in facts["flags"]]
    return "\n".join(lines)


def _position_line(p, with_positions):
    px = p.get("current_price")
    bits = [f"{p['ticker']} — {p.get('company_name')} | sector {p.get('sector') or 'n/a'}"]
    if with_positions and p.get("quantity") is not None:
        bits.append(f"weight {p.get('weight', 0):.1f}% | {p['quantity']:g} shares, cost ${p['avg_buy_price']:,.2f}, now ${px:,.2f}"
                    + (f" (P&L {p['pnl_pct']:+.1f}%)" if p.get("pnl_pct") is not None else ""))
    elif px:
        bits.append(f"price ${px:,.2f}")
    if p.get("price_change_pct") is not None:
        bits.append(f"today {p['price_change_pct']:+.2f}%")
    est = None
    if px and p.get("target_1w"):
        est = (p["target_1w"] - px) / px * 100
    bits.append(f"signal {p.get('buy_signal') or 'n/a'}, trend {p.get('trend') or 'n/a'}, risk {p.get('risk_level') or 'n/a'}"
                + (f", 7-day model estimate {est:+.1f}%" if est is not None else ""))
    fundamentals = []
    if p.get("pe_ratio"): fundamentals.append(f"P/E {p['pe_ratio']:.1f}")
    if p.get("revenue_growth") is not None: fundamentals.append(f"revenue growth {p['revenue_growth'] * 100:.0f}%")
    if p.get("profit_margin") is not None: fundamentals.append(f"profit margin {p['profit_margin'] * 100:.0f}%")
    if p.get("debt_to_equity") is not None: fundamentals.append(f"debt/equity {p['debt_to_equity']:.0f}")
    if p.get("beta") is not None: fundamentals.append(f"beta {p['beta']:.2f}")
    if fundamentals:
        bits.append(", ".join(fundamentals))
    if p.get("analyst_rating"):
        bits.append(f"analysts {str(p['analyst_rating']).replace('_', ' ')}" + (f", target ${p['target_price']:,.2f}" if p.get("target_price") else ""))
    return "- " + " | ".join(bits)


def review(user_id, entries=None, use_holdings=False):
    """
    AI review of the user's saved holdings (use_holdings=True) or of an ad-hoc list of tickers / company names.
    Returns {"review", "tickers_reviewed", "not_found"}.
    """
    from agent import llm_text  # imported here so the modules can import each other

    not_found = []
    if use_holdings:
        held = list_holdings(user_id)
        if not held:
            raise PortfolioError("You have no holdings yet. Add some below, or type a few stocks to review.")
        held = [h for h in held if h["current_value"] is not None][:MAX_REVIEW_STOCKS]
        if not held:
            raise PortfolioError("None of your holdings have a live price right now, so there is nothing to review.")
        market = _market_rows([h["ticker"] for h in held])
        positions = [{**market.get(h["ticker"], {}), **h} for h in held]
        with_positions = True
    else:
        tickers, not_found = resolve_entries(entries or [])
        if not tickers:
            raise PortfolioError("I couldn't match any of those to a stock we track. Try ticker symbols such as AAPL, TSLA.")
        market = _market_rows(tickers)
        positions = [market[t] for t in tickers if t in market]
        with_positions = False

    facts = portfolio_facts(positions) if with_positions else {"count": len(positions), "flags": [], "total_value": 0}
    if with_positions:
        positions.sort(key=lambda p: -p.get("weight", 0))
    else:
        sectors = {}
        for p in positions:
            sectors[p.get("sector") or "Unknown"] = sectors.get(p.get("sector") or "Unknown", 0) + 1
        if sectors and max(sectors.values()) / len(positions) > 0.6 and len(positions) >= 4:
            top = max(sectors, key=sectors.get)
            facts["flags"].append(f"{sectors[top]} of {len(positions)} stocks are in {top}")

    snapshot = snapshot_markdown(facts, with_positions)
    prompt = f"""You are a professional portfolio analyst at Stock Market Pro.

{snapshot or 'No portfolio-level figures are available.'}

Positions{' (largest first)' if with_positions else ''}:
{chr(10).join(_position_line(p, with_positions) for p in positions)}

Write a review in plain English.

First a short "Overall" paragraph (3-4 sentences) about diversification, concentration and the main risks. Use only the snapshot above.

Then, for each position:
📌 TICKER — Company
Action: HOLD / ADD MORE / CAUTION / CONSIDER SWAPPING
2-3 sentences explaining the action with the numbers given.{' Take the position size and the user\'s gain or loss into account: a stock that is a large share of the portfolio deserves a concentration comment, and do not suggest selling only because it has gone up.' if with_positions else ' No position sizes were given, so judge each stock on its own merits.'}

Rules:
- Use only numbers from the data above. Never invent figures and do not do your own arithmetic on weights.
- Do not give tax advice.
- End with: "⚠️ These are model-based estimates, not financial advice. Always invest within your risk tolerance."
"""
    text = llm_text(prompt)
    parts = [snapshot] if snapshot else []
    if not_found:
        parts.append("Couldn't match: " + ", ".join(not_found) + ". Try their ticker symbols.")
    parts.append(text)
    return {"review": "\n\n".join(parts), "tickers_reviewed": [p["ticker"] for p in positions], "not_found": not_found}


# ══════════════════════════════════════════════════════════
# TRANSACTIONS
# ══════════════════════════════════════════════════════════

TX_TYPES = ("BUY", "SELL", "DIVIDEND")
_TX_COLUMNS = "id, ticker, company_name, transaction_type, quantity, price, total_value, transaction_date, notes"


def list_transactions(user_id):
    return _query(f"SELECT {_TX_COLUMNS} FROM transactions WHERE user_id = %s ORDER BY transaction_date DESC, id DESC", (user_id,))


def realized_pnl(txs):
    """
    Realized profit from sales, by the average-cost method: each sale is matched against the average cost of the shares
    bought so far, in date order. Sales larger than the tracked position cannot be matched and are reported separately.
    A DIVIDEND counts its total_value (quantity x per-share amount).
    """
    pos, realized, unmatched, dividends = {}, {}, {}, 0.0
    for t in sorted(txs, key=lambda t: (str(t["transaction_date"]), t.get("id") or 0)):
        tk, typ = t["ticker"], t["transaction_type"]
        q, px = float(t["quantity"]), float(t["price"])
        qty0, cost0 = pos.get(tk, (0.0, 0.0))
        if typ == "BUY":
            pos[tk] = (qty0 + q, cost0 + q * px)
        elif typ == "SELL":
            matched = min(q, qty0)
            if matched > 0:
                avg = cost0 / qty0
                realized[tk] = realized.get(tk, 0.0) + matched * (px - avg)
                pos[tk] = (qty0 - matched, cost0 - matched * avg)
            if q > matched + 1e-9:
                unmatched[tk] = unmatched.get(tk, 0.0) + (q - matched)
        elif typ == "DIVIDEND":
            dividends += float(t.get("total_value") or q * px)
    return {"realized_by_ticker": {k: round(v, 2) for k, v in realized.items()},
            "realized_total": round(sum(realized.values()), 2),
            "dividends": round(dividends, 2),
            "unmatched_sells": {k: round(v, 4) for k, v in unmatched.items()}}


def transaction_summary(user_id):
    rows = _query(f"SELECT {_TX_COLUMNS} FROM transactions WHERE user_id = %s", (user_id,))
    trades = [r for r in rows if r["transaction_type"] in ("BUY", "SELL")]
    value = lambda r: round((r["quantity"] or 0) * (r["price"] or 0), 2)
    pnl = realized_pnl(rows)
    return {
        "total_trades": len(trades),
        "total_invested": round(sum(value(r) for r in trades if r["transaction_type"] == "BUY"), 2),
        "total_sold": round(sum(value(r) for r in trades if r["transaction_type"] == "SELL"), 2),
        "largest_trade": max([value(r) for r in trades], default=0),
        "dividends": pnl["dividends"],
        "realized_pnl": pnl["realized_total"],
        "realized_by_ticker": [{"ticker": k, "realized": v} for k, v in sorted(pnl["realized_by_ticker"].items(), key=lambda kv: -abs(kv[1]))],
        "unmatched_sells": [{"ticker": k, "quantity": v} for k, v in pnl["unmatched_sells"].items()],
    }


def _check_tx(tx_type, quantity, price, tx_date, notes):
    tx_type = (tx_type or "").strip().upper()
    if tx_type not in TX_TYPES:
        raise PortfolioError("Type must be BUY, SELL or DIVIDEND.")
    q, px, d, notes = _check_position(quantity, price, tx_date, notes, price_label="Price", date_label="Date")
    return tx_type, q, px, d, notes


def _net_shares(user_id, ticker, up_to, exclude_id=None):
    """Shares of `ticker` the transaction history says you held on `up_to`."""
    net = 0.0
    for r in _query("SELECT id, transaction_type, quantity, transaction_date FROM transactions WHERE user_id = %s AND ticker = %s", (user_id, ticker)):
        if r["id"] == exclude_id or r["transaction_date"] is None or str(r["transaction_date"]) > str(up_to):
            continue
        net += r["quantity"] if r["transaction_type"] == "BUY" else -r["quantity"] if r["transaction_type"] == "SELL" else 0
    return net


def _held_quantity(user_id, ticker):
    rows = _query("SELECT quantity FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
    return sum(r["quantity"] for r in rows)


def _reduce_holding(user_id, ticker, quantity):
    conn = get_db_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, quantity FROM portfolio WHERE user_id = %s AND ticker = %s ORDER BY id", (user_id, ticker))
        lots = cur.fetchall()
        held = sum(float(l["quantity"]) for l in lots)
        if not lots:
            raise PortfolioError(f"You don't hold {ticker} in Holdings, so there is nothing to reduce. Untick \"Also update my holdings\".")
        if quantity > held + 1e-9:
            raise PortfolioError(f"Holdings only show {held:g} shares of {ticker}, so {quantity:g} can't be sold from them.")
        remaining = held - quantity
        keep, rest = lots[0], lots[1:]
        for l in rest:
            cur.execute("DELETE FROM portfolio WHERE id = %s AND user_id = %s", (l["id"], user_id))
        if remaining <= 1e-9:
            cur.execute("DELETE FROM portfolio WHERE id = %s AND user_id = %s", (keep["id"], user_id))
        else:
            cur.execute("UPDATE portfolio SET quantity = %s WHERE id = %s AND user_id = %s", (remaining, keep["id"], user_id))
        conn.commit()
        return remaining
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_transaction(user_id, ticker, tx_type, quantity, price, tx_date, notes="", apply_to_holdings=False):
    ticker = (ticker or "").strip().upper()
    if not TICKER_RE.match(ticker):
        raise PortfolioError("That doesn't look like a ticker symbol.")
    tx_type, q, px, d, notes = _check_tx(tx_type, quantity, price, tx_date, notes)
    stock = _query("SELECT company_name FROM stocks_live WHERE ticker = %s AND current_price IS NOT NULL", (ticker,))
    if not stock:
        raise PortfolioError(f"{ticker} isn't a stock we track, so it can't be recorded.")
    if tx_type == "SELL":
        allowed = max(_net_shares(user_id, ticker, d), _held_quantity(user_id, ticker))
        if q > allowed + 1e-9:
            raise PortfolioError(f"You can't sell {q:g} {ticker}: your records show only {allowed:g} shares.")

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO transactions (user_id, ticker, company_name, transaction_type, quantity, price, total_value, transaction_date, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (user_id, ticker, stock[0]["company_name"] or ticker, tx_type, q, px, round(q * px, 2), d, notes))
        tx_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    message = f"Recorded {tx_type} {q:g} {ticker} at ${px:,.2f}."
    if apply_to_holdings and tx_type != "DIVIDEND":
        try:
            if tx_type == "BUY":
                message += " " + add_holding(user_id, ticker, q, px, d, notes)["message"]
            else:
                left = _reduce_holding(user_id, ticker, q)
                message += f" Holdings now show {left:g} shares." if left > 0 else f" {ticker} is no longer in your holdings."
        except Exception:
            delete_transaction(user_id, tx_id)  # keep the two records consistent: undo the entry we just made
            raise
    return {"id": tx_id, "ticker": ticker, "message": message}


def update_transaction(user_id, tx_id, quantity, price, tx_date, notes=""):
    rows = _query("SELECT ticker, transaction_type FROM transactions WHERE id = %s AND user_id = %s", (tx_id, user_id))
    if not rows:
        raise PortfolioError("That transaction no longer exists.", 404)
    _, q, px, d, notes = _check_tx(rows[0]["transaction_type"], quantity, price, tx_date, notes)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE transactions SET quantity=%s, price=%s, total_value=%s, transaction_date=%s, notes=%s WHERE id=%s AND user_id=%s",
                    (q, px, round(q * px, 2), d, notes, tx_id, user_id))
        conn.commit()
    finally:
        conn.close()
    return {"id": tx_id, "ticker": rows[0]["ticker"], "message": f"{rows[0]['ticker']} transaction updated. (Holdings are not changed by editing a past transaction.)"}


def delete_transaction(user_id, tx_id):
    rows = _query("SELECT ticker FROM transactions WHERE id = %s AND user_id = %s", (tx_id, user_id))
    if not rows:
        raise PortfolioError("That transaction no longer exists.", 404)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions WHERE id = %s AND user_id = %s", (tx_id, user_id))
        conn.commit()
    finally:
        conn.close()
    return {"id": tx_id, "ticker": rows[0]["ticker"], "message": f"{rows[0]['ticker']} transaction removed. (Holdings are not changed.)"}


# ══════════════════════════════════════════════════════════
# PERFORMANCE, BENCHMARK AND RISK
# ══════════════════════════════════════════════════════════

BENCHMARK = "SPY"
BENCHMARK_NAME = "S&P 500 (SPY)"
MAX_HISTORY_YEARS = 5
MAX_POINTS = 260
_perf_cache = {}
PERF_TTL = 1800


def risk_metrics(daily_returns, benchmark_returns, weights):
    """Volatility, worst fall, beta and diversification. Each is None when there is too little data to mean anything."""
    import numpy as np
    r = np.asarray(daily_returns, dtype=float)
    out = {"volatility_pct": None, "max_drawdown_pct": None, "beta": None, "effective_holdings": None, "top3_weight_pct": None,
           "diversification": None, "observations": int(len(r))}
    if len(r) >= 30:
        out["volatility_pct"] = round(float(np.std(r, ddof=1) * np.sqrt(252) * 100), 1)
        curve = np.cumprod(1 + r)
        peaks = np.maximum.accumulate(curve)
        out["max_drawdown_pct"] = round(float(((curve - peaks) / peaks).min() * 100), 1)
    b = np.asarray(benchmark_returns, dtype=float) if benchmark_returns is not None else None
    if b is not None and len(b) == len(r) and len(r) >= 60 and np.var(b, ddof=1) > 0:
        out["beta"] = round(float(np.cov(r, b, ddof=1)[0, 1] / np.var(b, ddof=1)), 2)
    w = np.asarray([x for x in weights if x and x > 0], dtype=float)
    if len(w):
        w = w / w.sum()
        eff = float(1 / np.sum(w ** 2))   # "as if the money were spread evenly over this many stocks"
        out["effective_holdings"] = round(eff, 1)
        out["top3_weight_pct"] = round(float(np.sort(w)[::-1][:3].sum() * 100), 1)
        out["diversification"] = "Concentrated" if eff < 5 else "Moderate" if eff < 10 else "Diversified"
    return out


def performance(user_id):
    """
    How the portfolio compares with putting the same dollars into the S&P 500 on the same dates.

    Method and limits: each holding is assumed to have been owned from its buy date at today's quantity (Holdings keeps
    no sales history). The benchmark buys SPY with each holding's cost on that holding's buy date.
    """
    import numpy as np
    import pandas as pd
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    held = [h for h in list_holdings(user_id) if h.get("buy_date") and h.get("quantity") and h.get("avg_buy_price")]
    if not held:
        return {"empty": True}
    sig = tuple((h["ticker"], h["quantity"], h["avg_buy_price"], str(h["buy_date"])) for h in held)
    hit = _perf_cache.get(sig)
    if hit and hit["expires"] > __import__("time").time():
        return hit["value"]

    today = pd.Timestamp.today().normalize()
    floor = today - pd.DateOffset(years=MAX_HISTORY_YEARS)
    first_buy = min(pd.Timestamp(h["buy_date"]) for h in held)
    start = max(first_buy - pd.Timedelta(days=7), floor)
    tickers = sorted({h["ticker"] for h in held} | {BENCHMARK})

    def closes(t):
        try:
            c = yf.Ticker(t).history(start=start.strftime("%Y-%m-%d"), timeout=20)["Close"].dropna()
            c.index = pd.to_datetime([str(x)[:10] for x in c.index])
            return c[~c.index.duplicated(keep="last")]
        except Exception as e:
            print(f"Performance: price history failed for {t}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        series = dict(zip(tickers, pool.map(closes, tickers)))
    if series.get(BENCHMARK) is None or len(series[BENCHMARK]) < 20:
        raise PortfolioError("The S&P 500 price history isn't available right now, so there is nothing to compare against.", 503)
    prices = pd.DataFrame({t: s for t, s in series.items() if s is not None}).sort_index().ffill()
    prices = prices.loc[prices[BENCHMARK].notna()]
    dates = prices.index
    n = len(dates)

    value = np.zeros(n); invested = np.zeros(n); bench = np.zeros(n)
    active_value = {}            # ticker -> value series, used for the daily-return weights
    truncated = False
    skipped = []
    for h in held:
        t = h["ticker"]
        if t not in prices.columns or prices[t].isna().all():
            skipped.append(t)
            continue
        entry = int(dates.searchsorted(pd.Timestamp(h["buy_date"])))
        if pd.Timestamp(h["buy_date"]) < dates[0]:
            truncated = True
        if entry >= n:
            continue
        cost = h["quantity"] * h["avg_buy_price"]
        px = prices[t].to_numpy()
        px = np.where(np.isnan(px), np.nanmean(px[entry:]) if np.isfinite(np.nanmean(px[entry:])) else 0, px)
        pos = np.zeros(n); pos[entry:] = h["quantity"] * px[entry:]
        value += pos
        invested[entry:] += cost
        spy = prices[BENCHMARK].to_numpy()
        bench[entry:] += cost / spy[entry] * spy[entry:]
        active_value[t] = (pos, px, entry)

    if not active_value:
        raise PortfolioError("None of your holdings have price history to chart.", 503)

    # Daily return of the money that was already invested the day before (new purchases don't count as performance)
    daily, spy_daily = [], []
    spy = prices[BENCHMARK].to_numpy()
    for i in range(1, n):
        prev_total = ret_sum = 0.0
        for pos, px, entry in active_value.values():
            if entry <= i - 1 and px[i - 1] > 0:
                prev = pos[i - 1]
                prev_total += prev
                ret_sum += prev * (px[i] / px[i - 1] - 1)
        if prev_total > 0:
            daily.append(ret_sum / prev_total)
            spy_daily.append(spy[i] / spy[i - 1] - 1)

    now = list_holdings(user_id)
    weights = [h["current_value"] for h in now if h.get("current_value")]
    risk = risk_metrics(daily, spy_daily, weights)

    cost_now, v_now, b_now = float(invested[-1]), float(value[-1]), float(bench[-1])
    # thin the series for the chart, always keeping the last day
    step = max(1, n // MAX_POINTS)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    out = {
        "empty": False,
        "benchmark_name": BENCHMARK_NAME,
        "dates": [dates[i].strftime("%Y-%m-%d") for i in idx],
        "portfolio": [round(float(value[i]), 2) for i in idx],
        "invested": [round(float(invested[i]), 2) for i in idx],
        "benchmark": [round(float(bench[i]), 2) for i in idx],
        "summary": {
            "cost": round(cost_now, 2), "portfolio_value": round(v_now, 2), "benchmark_value": round(b_now, 2),
            "portfolio_return_pct": round((v_now / cost_now - 1) * 100, 1) if cost_now else None,
            "benchmark_return_pct": round((b_now / cost_now - 1) * 100, 1) if cost_now else None,
            "difference_pp": round((v_now - b_now) / cost_now * 100, 1) if cost_now else None,
            "difference_usd": round(v_now - b_now, 2),
        },
        "risk": risk,
        "notes": ["Each holding is assumed to have been owned from its buy date at today's quantity (sales aren't tracked in Holdings).",
                  "The S&P 500 line shows what the same dollars would be worth if put into SPY on the same dates."]
                 + (["Some buy dates are older than the five years of price history used, so those holdings start at the first available date."] if truncated else [])
                 + ([f"No price history for {', '.join(skipped)}, so they are left out of the chart."] if skipped else []),
    }
    _perf_cache[sig] = {"value": out, "expires": __import__("time").time() + PERF_TTL}
    return out
