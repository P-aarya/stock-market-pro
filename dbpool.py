"""
Reused database connections for accounts, Settings, the Help Desk and the Admin Center.

The database is on the internet (Aiven), so opening a connection costs about half a second (a secure handshake), while
a query on an open connection takes a few hundredths of a second. These connections are kept open and handed out again
instead of being opened fresh for every step.

    with dbpool.borrow() as conn:
        ...                      # everything here, including nested borrow() calls on this thread, uses one connection

Rules that keep this safe:
  * Each connection is used by one thread at a time.
  * When the outermost borrow() ends, any open transaction is ended (rolled back), so a reused connection never sees an
    old snapshot of the data. Code that writes commits its own changes, as it always has.
  * A connection that raised an error is closed, never reused.
  * One that sat unused for a while is checked with a quick ping first; the server may have dropped it.
  * At most MAX_IDLE are kept; connections older than MAX_AGE are replaced.

The rest of the dashboard (market data, predictions and so on) keeps opening its own connections as before.
"""
import threading
import time
from contextlib import contextmanager

MAX_IDLE = 4          # connections kept open between requests
MAX_AGE = 600         # seconds before a connection is replaced anyway
PING_AFTER = 20       # seconds unused before it is checked with a ping

_idle = []            # [(connection, opened_at, last_used)]
_lock = threading.Lock()
_local = threading.local()


def _open():
    from screener import get_db_connection
    return get_db_connection()


def _close(conn):
    try:
        conn.close()
    except Exception:                                          # noqa: BLE001
        pass


def _take():
    now = time.time()
    while True:
        with _lock:
            if not _idle:
                break
            conn, opened, used = _idle.pop()
        if now - opened > MAX_AGE:
            _close(conn)
            continue
        if now - used > PING_AFTER:
            try:
                conn.ping(reconnect=False)
            except Exception:                                  # noqa: BLE001 - dropped by the server: open another
                _close(conn)
                continue
        return conn, opened
    return _open(), now


def _give_back(conn, opened):
    try:
        conn.rollback()                                        # end any open transaction (see the rules above)
    except Exception:                                          # noqa: BLE001
        _close(conn)
        return
    with _lock:
        if len(_idle) < MAX_IDLE and time.time() - opened < MAX_AGE:
            _idle.append((conn, opened, time.time()))
            return
    _close(conn)


@contextmanager
def borrow():
    current = getattr(_local, "conn", None)
    if current is not None:                                    # already inside borrow() on this thread: share it
        yield current
        return
    conn, opened = _take()
    _local.conn = conn
    failed = False
    try:
        yield conn
    except BaseException as e:
        failed = not getattr(e, "keeps_connection", False)   # a message for the person (wrong password...) isn't a database problem
        raise
    finally:
        _local.conn = None
        if failed:
            try:
                conn.rollback()
            except Exception:                                  # noqa: BLE001
                pass
            _close(conn)
        else:
            _give_back(conn, opened)


def clear():
    """Close every kept connection (tests, and if the settings change)."""
    with _lock:
        conns = [c for c, _, _ in _idle]
        _idle.clear()
    for c in conns:
        _close(c)
