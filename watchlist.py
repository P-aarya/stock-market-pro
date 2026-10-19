"""A person's watchlist, kept in the database so it follows their account to every device."""
import re

from screener import get_db_connection

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,10}$")
MAX_WATCHLIST = 100


class WatchlistError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def clean(tickers):
    """Upper-cased, de-duplicated, in the order given. Raises WatchlistError for anything that isn't a ticker."""
    if not isinstance(tickers, (list, tuple)):
        raise WatchlistError("The watchlist must be a list of tickers.")
    out, seen = [], set()
    for item in tickers:
        t = str(item).strip().upper() if isinstance(item, str) else ""
        if not TICKER_RE.match(t):
            raise WatchlistError(f"'{str(item)[:20]}' doesn't look like a ticker symbol.")
        if t not in seen:
            seen.add(t)
            out.append(t)
    if len(out) > MAX_WATCHLIST:
        raise WatchlistError(f"A watchlist can hold up to {MAX_WATCHLIST} stocks.")
    return out


def get_watchlist(user_id):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM watchlist WHERE user_id = %s ORDER BY sort_order, ticker", (user_id,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def save_watchlist(user_id, tickers):
    """Replace the person's whole list (what the dashboard sends after every change). Returns the saved list."""
    tickers = clean(tickers)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM watchlist WHERE user_id = %s", (user_id,))
        if tickers:
            cur.executemany("INSERT INTO watchlist (user_id, ticker, sort_order) VALUES (%s, %s, %s)",
                            [(user_id, t, i) for i, t in enumerate(tickers)])
        conn.commit()
        return tickers
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
