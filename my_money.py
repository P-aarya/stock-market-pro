"""
"Where your money is": the things a person owns outside Aymetrix (savings, fixed deposits, real estate, pension...),
which they add themselves, in US dollars. The Portfolio page shows them in one pie together with the stocks and
commodities that come from their transactions.

Every row belongs to one account (user_id) and every statement is limited to it. The table is created the first time
it is needed. Deleting an account removes these rows too (account_data.OPTIONAL_PERSONAL_TABLES), and they are part of
"download my data".
"""
import math

from screener import get_db_connection

KINDS = {                                   # stored key -> what people see; the order is the order in the Add box
    "savings": "Savings / cash",
    "fd": "Fixed deposits",
    "real_estate": "Real estate",
    "pension": "Pension",
    "bonds": "Bonds",
    "crypto": "Crypto",
    "gold": "Physical gold",
    "other": "Other",
}
MAX_ITEMS = 50
MAX_AMOUNT = 1_000_000_000_000
MAX_NAME = 80

TABLE = """CREATE TABLE IF NOT EXISTS user_assets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    kind VARCHAR(20) NOT NULL,
    name VARCHAR(80) NOT NULL DEFAULT '',
    amount DECIMAL(16,2) NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user_assets_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


class MoneyError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


_ready = [False]                            # the table is checked once per server start, not on every request


def _run(sql, params=(), fetch=False):
    conn = get_db_connection()
    try:
        cur = conn.cursor(dictionary=True)
        if not _ready[0]:
            cur.execute(TABLE)
            _ready[0] = True
        cur.execute(sql, params)
        if fetch:
            return cur.fetchall()
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount
    finally:
        conn.close()


def _check(kind, name, amount):
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        raise MoneyError("Choose what kind of money this is.")
    name = " ".join(str(name or "").split())[:MAX_NAME]
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise MoneyError("Enter the amount as a number, e.g. 2500.")
    if not math.isfinite(amount) or amount <= 0:
        raise MoneyError("The amount must be more than $0.")
    if amount > MAX_AMOUNT:
        raise MoneyError("That amount is too large.")
    return kind, name, round(amount, 2)


def _row(r):
    return {"id": r["id"], "kind": r["kind"], "kind_label": KINDS.get(r["kind"], "Other"), "name": r["name"] or "",
            "amount": float(r["amount"]), "updated_at": str(r["updated_at"])[:10] if r.get("updated_at") else None}


def list_items(user_id):
    rows = _run("SELECT id, kind, name, amount, updated_at FROM user_assets WHERE user_id = %s ORDER BY amount DESC, id",
                (user_id,), fetch=True)
    return [_row(r) for r in rows]


def add_item(user_id, kind, name, amount):
    kind, name, amount = _check(kind, name, amount)
    count = _run("SELECT COUNT(*) AS n FROM user_assets WHERE user_id = %s", (user_id,), fetch=True)[0]["n"]
    if count >= MAX_ITEMS:
        raise MoneyError(f"You can add up to {MAX_ITEMS} items. Remove or combine some first.")
    new_id = _run("INSERT INTO user_assets (user_id, kind, name, amount) VALUES (%s, %s, %s, %s)", (user_id, kind, name, amount))
    return {"id": new_id, "message": f"Added {name or KINDS[kind]}: ${amount:,.2f}."}


def update_item(user_id, item_id, kind, name, amount):
    kind, name, amount = _check(kind, name, amount)
    if not _run("SELECT id FROM user_assets WHERE id = %s AND user_id = %s", (item_id, user_id), fetch=True):
        raise MoneyError("That item no longer exists.", 404)
    _run("UPDATE user_assets SET kind = %s, name = %s, amount = %s WHERE id = %s AND user_id = %s",
         (kind, name, amount, item_id, user_id))
    return {"id": item_id, "message": f"Updated {name or KINDS[kind]}: ${amount:,.2f}."}


def delete_item(user_id, item_id):
    if not _run("DELETE FROM user_assets WHERE id = %s AND user_id = %s", (item_id, user_id)):
        raise MoneyError("That item no longer exists.", 404)
    return {"id": item_id, "message": "Removed."}
