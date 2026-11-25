"""
"Your finances" (profile menu): the answers people give about their own money, in US dollars.

    Monthly money   what comes in and what goes out each month             (user_finance, one row per account)
    Donations       optional: a share of income or a fixed amount to give  (the same row)
    Goals           things they're saving for, by a date                   (user_goals, one row per goal)

The page works out the answers and suggestions itself from these numbers (and from the Portfolio and "Where your money
is" items, which already have their own routes); this module only keeps the answers, each row limited to its account.
Both tables are created the first time they are needed, deleted with the account (account_data.OTHER_PERSONAL_TABLES)
and part of "download my data". General guidance only: nothing here gives investment advice.
"""
import datetime as dt
import math

from screener import get_db_connection

MONTHLY = ("income", "housing", "bills", "food", "transport", "repayments", "other_spend")   # the Monthly money questions
GIVE_MODES = ("pct", "fixed")
MAX_AMOUNT = 1_000_000_000
MAX_GOALS = 20
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
            "giving": giving, "goals": list_goals(user_id)}


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
