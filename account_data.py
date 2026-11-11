"""
The database side of removing an account and everything it owns. Used by manage_users.py (an administrator deleting someone) and
by auth.py (an account a person deleted themselves, once its 30 days are up).

Every function takes an open connection and either finishes all of its changes or none of them.
"""

PERSONAL_TABLES = ("portfolio", "transactions")      # tables whose rows belong to one account
OPTIONAL_PERSONAL_TABLES = ("price_alerts",)         # the same, but the app creates them on first use, so they may not exist yet


def _has_table(cur, table):
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def owned_tables(cur):
    """Every personal table that exists right now, required ones first."""
    return [*PERSONAL_TABLES, *(t for t in OPTIONAL_PERSONAL_TABLES if _has_table(cur, t))]


def _delete_everything_of(cur, user_id):
    for table in (*owned_tables(cur), "watchlist"):
        cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def delete_account(conn, user_id):
    """Remove an account and everything it owns, all or nothing."""
    cur = conn.cursor()
    try:
        _delete_everything_of(cur, user_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def purge_one(conn, user_id, cutoff):
    """Permanently remove `user_id` with everything it owns, but only if it is still a deleted account whose deletion happened at
    or before `cutoff` (seconds since 1970). The row is locked while that is checked, so an account restored a moment ago is never
    removed. Returns True when it was removed."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE id = %s AND status = 'deleted' AND deleted_at IS NOT NULL AND UNIX_TIMESTAMP(deleted_at) <= %s FOR UPDATE",
                    (user_id, cutoff))
        if not cur.fetchone():
            conn.rollback()
            return False
        _delete_everything_of(cur, user_id)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def expired_ids(conn, cutoff):
    """The ids of deleted accounts whose deletion happened at or before `cutoff`."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE status = 'deleted' AND deleted_at IS NOT NULL AND UNIX_TIMESTAMP(deleted_at) <= %s", (cutoff,))
    return [row[0] for row in cur.fetchall()]


def purge_expired(conn, cutoff):
    """Permanently remove every deleted account that is past its grace period. Returns how many were removed."""
    return sum(1 for user_id in expired_ids(conn, cutoff) if purge_one(conn, user_id, cutoff))
