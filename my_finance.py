"""
"Your personal finance" (profile menu): the answers people give about their own money, in US dollars.

    Monthly money   what comes in and what goes out each month             (user_finance, one row per account)
    Donations       optional: a share of income or a fixed amount to give  (the same row)
    Goals           things they're saving for, by a date                   (user_goals, one row per goal)
    Wishlist        stocks they'd like to buy, and things they'd like to buy (maybe with a loan)   (user_wishlist)

For a wishlist stock, stock_read() turns the data we already collect (price, 1-year range, trend, valuation, analysts,
earnings date and, on plans with prediction, the model) into "Looks good" / "Wait for now" / "Be careful" with the
reasons. It describes what the data shows; it is not a recommendation to buy.

The page works out the answers and suggestions itself from these numbers (and from the Portfolio and "Where your money
is" items, which already have their own routes); this module only keeps the answers, each row limited to its account.
The tables are created the first time they are needed, deleted with the account (account_data.OTHER_PERSONAL_TABLES)
and part of "download my data". General guidance only: nothing here gives investment advice.
"""
import datetime as dt
import math
import re

from screener import get_db_connection

MONTHLY = ("income", "housing", "bills", "food", "transport", "repayments", "other_spend")   # the Monthly money questions
GIVE_MODES = ("pct", "fixed")
MAX_AMOUNT = 1_000_000_000
MAX_GOALS = 20
MAX_WISH = 30
ITEM_KINDS = ("car", "home", "education", "other")      # things to buy: what kind of thing (decides the loan types shown)
MAX_TEXT = 80

TABLES = (
    """CREATE TABLE IF NOT EXISTS user_finance (
    user_id INT PRIMARY KEY,
    income DECIMAL(14,2) NULL, housing DECIMAL(14,2) NULL, bills DECIMAL(14,2) NULL, food DECIMAL(14,2) NULL,
    transport DECIMAL(14,2) NULL, repayments DECIMAL(14,2) NULL, other_spend DECIMAL(14,2) NULL,
    give_on TINYINT NOT NULL DEFAULT 0,
    give_mode VARCHAR(10) NULL,
    give_value DECIMAL(14,2) NULL,
    give_causes VARCHAR(80) NOT NULL DEFAULT '',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS user_goals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(80) NOT NULL,
    target DECIMAL(14,2) NOT NULL,
    target_date DATE NOT NULL,
    saved DECIMAL(14,2) NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user_goals_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS user_wishlist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    kind VARCHAR(10) NOT NULL,
    name VARCHAR(80) NOT NULL,
    category VARCHAR(20) NULL,
    amount DECIMAL(14,2) NULL,
    want_by DATE NULL,
    loan TINYINT NOT NULL DEFAULT 0,
    loan_years INT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user_wishlist_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


class FinanceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


_ready = [False]                            # the tables are checked once per server start, not on every request


def _run(sql, params=(), fetch=False):
    conn = get_db_connection()
    try:
        cur = conn.cursor(dictionary=True)
        if not _ready[0]:
            for t in TABLES:
                cur.execute(t)
            _ready[0] = True
        cur.execute(sql, params)
        if fetch:
            return cur.fetchall()
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount
    finally:
        conn.close()


# ---------- checking answers ----------
def _money(value, label, allow_empty=True, allow_zero=True):
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_empty:
            return None
        raise FinanceError(f"Enter {label}.")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise FinanceError(f"Enter {label} as a number, e.g. 2500.")
    if not math.isfinite(v) or v < 0 or (v == 0 and not allow_zero):
        raise FinanceError(f"{label[0].upper() + label[1:]} must be {'more than' if not allow_zero else 'at least'} $0.")
    if v > MAX_AMOUNT:
        raise FinanceError(f"{label[0].upper() + label[1:]} is too large.")
    return round(v, 2)


def _text(value):
    return " ".join(str(value or "").split())[:MAX_TEXT]


def _f(v):
    return None if v is None else float(v)


# ---------- reading everything ----------
def get_all(user_id):
    rows = _run("SELECT * FROM user_finance WHERE user_id = %s", (user_id,), fetch=True)
    r = rows[0] if rows else {}
    monthly = {k: _f(r.get(k)) for k in MONTHLY}
    giving = {"on": bool(r.get("give_on")), "mode": r.get("give_mode"), "value": _f(r.get("give_value")),
              "causes": r.get("give_causes") or ""}
    return {"monthly": monthly, "has_monthly": any(v is not None for v in monthly.values()),
            "giving": giving, "goals": list_goals(user_id), "wishlist": list_wishlist(user_id)}


# ---------- Monthly money ----------
def save_monthly(user_id, answers):
    vals = {k: _money((answers or {}).get(k), _LABELS[k]) for k in MONTHLY}
    if vals["income"] is None:
        raise FinanceError("Enter how much comes in each month.")
    cols = ", ".join(MONTHLY)
    _run(f"INSERT INTO user_finance (user_id, {cols}) VALUES (%s, {', '.join(['%s'] * len(MONTHLY))}) "
         f"ON DUPLICATE KEY UPDATE {', '.join(f'{k} = VALUES({k})' for k in MONTHLY)}",
         (user_id, *[vals[k] for k in MONTHLY]))
    return {"monthly": vals, "message": "Saved your monthly money."}


def clear_monthly(user_id):
    _run(f"UPDATE user_finance SET {', '.join(f'{k} = NULL' for k in MONTHLY)} WHERE user_id = %s", (user_id,))
    return {"message": "Removed your monthly money answers."}


_LABELS = {"income": "what comes in each month", "housing": "rent or home loan", "bills": "bills", "food": "food and shopping",
           "transport": "transport", "repayments": "loan or card repayments", "other_spend": "everything else"}


# ---------- Donations (optional) ----------
def save_giving(user_id, on, mode, value, causes=""):
    on = bool(on)
    if on:
        if mode not in GIVE_MODES:
            raise FinanceError("Choose a share of income or a fixed amount.")
        value = _money(value, "how much to give", allow_empty=False, allow_zero=False)
        if mode == "pct" and value > 100:
            raise FinanceError("A share of income can't be more than 100%.")
    else:
        mode, value = None, None
    causes = _text(causes)
    _run("INSERT INTO user_finance (user_id, give_on, give_mode, give_value, give_causes) VALUES (%s, %s, %s, %s, %s) "
         "ON DUPLICATE KEY UPDATE give_on = VALUES(give_on), give_mode = VALUES(give_mode), give_value = VALUES(give_value), "
         "give_causes = VALUES(give_causes)", (user_id, 1 if on else 0, mode, value, causes))
    return {"giving": {"on": on, "mode": mode, "value": value, "causes": causes},
            "message": "Saved your giving plan." if on else "Giving is switched off."}


def clear_giving(user_id):
    _run("UPDATE user_finance SET give_on = 0, give_mode = NULL, give_value = NULL, give_causes = '' WHERE user_id = %s", (user_id,))
    return {"message": "Removed your giving plan."}


# ---------- Goals ----------
def _goal_check(name, target, target_date, saved):
    name = _text(name)
    if not name:
        raise FinanceError("Say what the goal is for, e.g. Car.")
    target = _money(target, "how much it costs", allow_empty=False, allow_zero=False)
    saved = _money(saved, "what you've already saved") or 0.0
    try:
        when = dt.date.fromisoformat(str(target_date or "")[:10])
    except ValueError:
        raise FinanceError("Choose when you want it by.")
    today = dt.date.today()
    if when <= today:
        raise FinanceError("Choose a date in the future.")
    if when > today.replace(year=today.year + 60):
        raise FinanceError("Choose a date within the next 60 years.")
    return name, target, when, saved


def _goal_row(r):
    return {"id": r["id"], "name": r["name"], "target": float(r["target"]), "saved": float(r["saved"]),
            "target_date": str(r["target_date"])[:10]}


def list_goals(user_id):
    rows = _run("SELECT id, name, target, target_date, saved FROM user_goals WHERE user_id = %s ORDER BY target_date, id",
                (user_id,), fetch=True)
    return [_goal_row(r) for r in rows]


def add_goal(user_id, name, target, target_date, saved=0):
    name, target, when, saved = _goal_check(name, target, target_date, saved)
    count = _run("SELECT COUNT(*) AS n FROM user_goals WHERE user_id = %s", (user_id,), fetch=True)[0]["n"]
    if count >= MAX_GOALS:
        raise FinanceError(f"You can have up to {MAX_GOALS} goals. Remove one first.")
    new_id = _run("INSERT INTO user_goals (user_id, name, target, target_date, saved) VALUES (%s, %s, %s, %s, %s)",
                  (user_id, name, target, when, saved))
    return {"id": new_id, "message": f"Added your goal: {name}."}


def update_goal(user_id, goal_id, name, target, target_date, saved=0):
    name, target, when, saved = _goal_check(name, target, target_date, saved)
    if not _run("SELECT id FROM user_goals WHERE id = %s AND user_id = %s", (goal_id, user_id), fetch=True):
        raise FinanceError("That goal no longer exists.", 404)
    _run("UPDATE user_goals SET name = %s, target = %s, target_date = %s, saved = %s WHERE id = %s AND user_id = %s",
         (name, target, when, saved, goal_id, user_id))
    return {"id": goal_id, "message": f"Updated your goal: {name}."}


def delete_goal(user_id, goal_id):
    if not _run("DELETE FROM user_goals WHERE id = %s AND user_id = %s", (goal_id, user_id)):
        raise FinanceError("That goal no longer exists.", 404)
    return {"id": goal_id, "message": "Removed."}


# ---------- Wishlist: stocks and things to buy ----------
_TICKER = re.compile(r"^[A-Z0-9.=^-]{1,15}$")


def _wish_check(kind, name, category, amount, want_by, loan, loan_years):
    if kind == "stock":
        t = str(name or "").strip().upper()
        if not _TICKER.match(t):
            raise FinanceError("Type the stock's symbol, e.g. AAPL.")
        if not _run("SELECT ticker FROM stocks_live WHERE ticker = %s", (t,), fetch=True):
            raise FinanceError(f"We don't have {t}. Pick a stock from the list as you type.")
        budget = _money(amount, "your budget", allow_zero=False)
        return "stock", t, None, budget, None, 0, None
    if kind != "item":
        raise FinanceError("Choose a stock or a thing to buy.")
    name = _text(name)
    if not name:
        raise FinanceError("Say what you'd like to buy, e.g. Car.")
    category = category if category in ITEM_KINDS else "other"
    cost = _money(amount, "how much it costs", allow_empty=False, allow_zero=False)
    when = None
    if want_by:
        try:
            when = dt.date.fromisoformat(str(want_by)[:10])
        except ValueError:
            raise FinanceError("Choose when you'd like it, or leave it empty.")
        if when <= dt.date.today():
            raise FinanceError("Choose a date in the future, or leave it empty.")
    years = None
    if loan:
        try:
            years = int(loan_years) if loan_years not in (None, "") else None
        except (TypeError, ValueError):
            raise FinanceError("Enter the loan length in whole years.")
        if years is not None and not 1 <= years <= 40:
            raise FinanceError("A loan length is between 1 and 40 years.")
    return "item", name, category, cost, when, 1 if loan else 0, years


def _wish_row(r):
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "category": r.get("category"),
            "amount": _f(r.get("amount")), "want_by": str(r["want_by"])[:10] if r.get("want_by") else None,
            "loan": bool(r.get("loan")), "loan_years": r.get("loan_years")}


def list_wishlist(user_id):
    rows = _run("SELECT id, kind, name, category, amount, want_by, loan, loan_years FROM user_wishlist WHERE user_id = %s "
                "ORDER BY kind DESC, id", (user_id,), fetch=True)
    return [_wish_row(r) for r in rows]


def add_wish(user_id, kind, name, category=None, amount=None, want_by=None, loan=False, loan_years=None):
    vals = _wish_check(kind, name, category, amount, want_by, loan, loan_years)
    count = _run("SELECT COUNT(*) AS n FROM user_wishlist WHERE user_id = %s", (user_id,), fetch=True)[0]["n"]
    if count >= MAX_WISH:
        raise FinanceError(f"Your wishlist can hold up to {MAX_WISH} things. Remove one first.")
    if vals[0] == "stock" and _run("SELECT id FROM user_wishlist WHERE user_id = %s AND kind = 'stock' AND name = %s",
                                   (user_id, vals[1]), fetch=True):
        raise FinanceError(f"{vals[1]} is already on your wishlist.")
    new_id = _run("INSERT INTO user_wishlist (user_id, kind, name, category, amount, want_by, loan, loan_years) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (user_id, *vals))
    return {"id": new_id, "message": f"Added {vals[1]} to your wishlist."}


def update_wish(user_id, wish_id, kind, name, category=None, amount=None, want_by=None, loan=False, loan_years=None):
    vals = _wish_check(kind, name, category, amount, want_by, loan, loan_years)
    if not _run("SELECT id FROM user_wishlist WHERE id = %s AND user_id = %s", (wish_id, user_id), fetch=True):
        raise FinanceError("That wishlist item no longer exists.", 404)
    _run("UPDATE user_wishlist SET kind = %s, name = %s, category = %s, amount = %s, want_by = %s, loan = %s, loan_years = %s "
         "WHERE id = %s AND user_id = %s", (*vals, wish_id, user_id))
    return {"id": wish_id, "message": f"Updated {vals[1]}."}


def delete_wish(user_id, wish_id):
    if not _run("DELETE FROM user_wishlist WHERE id = %s AND user_id = %s", (wish_id, user_id)):
        raise FinanceError("That wishlist item no longer exists.", 404)
    return {"id": wish_id, "message": "Removed."}


# ---------- what the data says about a wishlist stock ----------
STOCK_DATA = """SELECT l.ticker, l.company_name, l.sector, l.current_price, l.week_52_high, l.week_52_low,
       f.pe_ratio, a.recommendation, a.analyst_rating, a.upside_pct, a.num_analysts,
       t.ma_50, t.ma_200, t.rsi_14, e.next_earnings_date,
       p.buy_signal, p.prob_up_3m, p.target_3m
FROM stocks_live l
LEFT JOIN stocks_fundamentals f ON f.ticker = l.ticker
LEFT JOIN stocks_analyst a ON a.ticker = l.ticker
LEFT JOIN stocks_technicals t ON t.ticker = l.ticker
LEFT JOIN stocks_earnings e ON e.ticker = l.ticker
LEFT JOIN stocks_predictions p ON p.ticker = l.ticker
WHERE l.ticker IN ({})"""
SECTOR_PE = """SELECT l.sector, AVG(f.pe_ratio) AS pe FROM stocks_live l JOIN stocks_fundamentals f ON f.ticker = l.ticker
WHERE f.pe_ratio > 0 AND f.pe_ratio < 200 AND l.sector IN ({}) GROUP BY l.sector"""


def _num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def stock_read(r, sector_pe=None, predictions=False, today=None, sector_share=None, budget=None):
    """One stock's data -> {"verdict": good|wait|careful, "label", "points": [[tone, title, text]], ...}.
    tone: good / warn / bad / info. A point only appears when its data is there."""
    today = today or dt.date.today()
    price = _num(r.get("current_price"))
    pts, score, wait = [], 0, False
    if predictions:
        sig = str(r.get("buy_signal") or "").lower()
        prob = _num(r.get("prob_up_3m"))
        tgt = _num(r.get("target_3m"))
        if sig:
            word = "BUY" if "buy" in sig else "SELL" if "sell" in sig else "HOLD"
            score += 2 if word == "BUY" else -2 if word == "SELL" else 0
            extra = f", with a {prob:.0f}% chance of being higher in 3 months" if prob is not None else ""
            extra += f" (3-month target ${tgt:,.2f})" if tgt and price else ""
            pts.append(["good" if word == "BUY" else "bad" if word == "SELL" else "info", f"Our model says {word}.",
                        f"Its 3-month signal is {word}{extra}."])
            if prob is not None:
                score += 1 if prob >= 60 else -1 if prob <= 40 else 0
    hi, lo = _num(r.get("week_52_high")), _num(r.get("week_52_low"))
    if price and hi and lo and hi > lo:
        pos = (price - lo) / (hi - lo)
        if pos >= 0.9:
            score -= 1; wait = True
            pts.append(["warn", "Near its 1-year high.", f"${price:,.2f} is close to the highest price of the past year (${hi:,.2f}). "
                        "Buying after a big rise can mean paying more; some people wait for a dip."])
        elif pos <= 0.15:
            pts.append(["info", "Near its 1-year low.", f"${price:,.2f} is close to the lowest price of the past year (${lo:,.2f}). "
                        "That can be good value, or a sign of trouble: check why it fell."])
        else:
            pts.append(["info", "In the middle of its 1-year range.", f"Between ${lo:,.2f} and ${hi:,.2f} over the past year."])
    m50, m200 = _num(r.get("ma_50")), _num(r.get("ma_200"))
    if price and m200:
        if price < m200:
            score -= 1
            pts.append(["warn", "In a downtrend.", "The price is below its 200-day average, so it has been falling for a while."])
        elif m50 and price > m50 > m200:
            score += 1
            pts.append(["good", "In an uptrend.", "The price is above its 50-day and 200-day averages."])
    rsi = _num(r.get("rsi_14"))
    if rsi is not None:
        if rsi >= 70:
            score -= 1; wait = True
            pts.append(["warn", "Rose quickly lately.", f"Its RSI is {rsi:.0f} (above 70), which often comes before a pause or a small drop."])
        elif rsi <= 30:
            pts.append(["info", "Fell quickly lately.", f"Its RSI is {rsi:.0f} (below 30): sold heavily, sometimes followed by a bounce."])
    pe = _num(r.get("pe_ratio"))
    if pe is not None and pe <= 0:
        score -= 1
        pts.append(["warn", "Not making a profit.", "The company lost money over the past year, which makes it riskier."])
    elif pe and sector_pe:
        if pe > sector_pe * 1.5:
            score -= 1
            pts.append(["warn", "Expensive for its sector.", f"Its P/E is {pe:.0f} against about {sector_pe:.0f} for its sector: "
                        "the price expects a lot of growth."])
        elif pe < sector_pe * 0.7:
            score += 1
            pts.append(["good", "Cheaper than its sector.", f"Its P/E is {pe:.0f} against about {sector_pe:.0f} for its sector."])
    rec = str(r.get("recommendation") or r.get("analyst_rating") or "").lower().replace("_", " ")
    up, n = _num(r.get("upside_pct")), r.get("num_analysts")
    if rec:
        who = f"{int(n)} analysts" if n else "Analysts"
        if "buy" in rec:
            score += 1
            pts.append(["good", "Analysts are positive.", f"{who} rate it {rec.title()}" + (f", with an average target {up:+.0f}% from today." if up is not None else ".")])
        elif "sell" in rec or "underperform" in rec:
            score -= 1
            pts.append(["bad", "Analysts are negative.", f"{who} rate it {rec.title()}."])
        elif up is not None:
            pts.append(["info", "Analysts are neutral.", f"{who} rate it {rec.title()}, average target {up:+.0f}% from today."])
    if up is not None and up < 0:
        score -= 1
    nxt = r.get("next_earnings_date")
    if nxt:
        try:
            d = nxt if isinstance(nxt, dt.date) else dt.date.fromisoformat(str(nxt)[:10])
            days = (d - today).days
            if 0 <= days <= 14:
                wait = True
                pts.append(["warn", f"Results in {days} day{'s' if days != 1 else ''}.", "Prices often jump or drop on results day. "
                            "Waiting until after them avoids that surprise."])
        except ValueError:
            pass
    if sector_share is not None and r.get("sector") and sector_share >= 40:
        pts.append(["warn", f"You already hold {sector_share:.0f}% in {r['sector']}.", "Adding more of the same sector puts more "
                    "of your money on one part of the market."])
    if budget and price:
        k = int(budget // price)
        pts.append(["info", "Your budget.", f"${budget:,.0f} buys {k} share{'s' if k != 1 else ''} at today's price." if k
                    else f"One share costs ${price:,.2f}, more than your ${budget:,.0f} budget."])
    if score <= -2:
        verdict, label = "careful", "Be careful"
    elif score >= 2:
        verdict, label = ("wait", "Looks good, but wait") if wait else ("good", "Looks good")
    else:
        verdict, label = "wait", "Wait for now"
    return {"ticker": r.get("ticker"), "name": r.get("company_name") or r.get("ticker"), "sector": r.get("sector"),
            "price": price, "verdict": verdict, "label": label, "score": score, "points": pts}


def wishlist_stocks(user_id, predictions=False, holdings=None):
    """The read-out for every stock on this person's wishlist. holdings: their portfolio rows (for the sector check)."""
    wishes = [w for w in list_wishlist(user_id) if w["kind"] == "stock"]
    if not wishes:
        return {"stocks": []}
    tickers = [w["name"] for w in wishes]
    rows = {r["ticker"]: r for r in _run(STOCK_DATA.format(", ".join(["%s"] * len(tickers))), tuple(tickers), fetch=True)}
    sectors = sorted({r["sector"] for r in rows.values() if r.get("sector")})
    pes = {}
    if sectors:
        pes = {x["sector"]: _num(x["pe"]) for x in _run(SECTOR_PE.format(", ".join(["%s"] * len(sectors))), tuple(sectors), fetch=True)}
    by_sector, total = {}, 0.0
    for h in holdings or []:
        v = _num(h.get("current_value"))
        if v and str(h.get("asset_type") or "stock") != "commodity":
            total += v
            by_sector[h.get("sector") or "Other"] = by_sector.get(h.get("sector") or "Other", 0) + v
    out = []
    for w in wishes:
        r = rows.get(w["name"])
        if not r:
            out.append({"id": w["id"], "ticker": w["name"], "verdict": None, "label": "No data", "points": [
                ["info", "No data yet.", "We have no recent data for this stock."]]})
            continue
        share = by_sector.get(r.get("sector"), 0) / total * 100 if total else None
        out.append({"id": w["id"], "budget": w["amount"],
                    **stock_read(r, pes.get(r.get("sector")), predictions, sector_share=share, budget=w["amount"])})
    return {"stocks": out, "predictions": predictions}
