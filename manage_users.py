"""
Account administration. Run it from the project folder with the virtual environment active.

    python manage_users.py migrate                  set up the accounts tables (safe to run again)
    python manage_users.py create-user EMAIL        create an approved account (asks for the password); add --admin for an administrator
    python manage_users.py make-admin EMAIL         give an existing account administrator rights (it must be approved)
    python manage_users.py remove-admin EMAIL       take administrator rights away
    python manage_users.py approve EMAIL            let a pending or blocked account sign in
    python manage_users.py block EMAIL              stop an account signing in
    python manage_users.py claim-legacy EMAIL       give the portfolio, transactions and price alerts saved before accounts existed to EMAIL
    python manage_users.py set-password EMAIL       reset someone's password (there is no "forgot password" email)
    python manage_users.py list-users [--pending]
    python manage_users.py delete-user EMAIL --yes  delete an account together with its holdings, transactions, price alerts and watchlist

Back the database up before the first `migrate`: it adds a column to the portfolio, transactions and (if it exists) price_alerts tables.
"""
import argparse
import getpass
import sys

import auth

USERS_DDL = """CREATE TABLE IF NOT EXISTS users (
  id INT NOT NULL AUTO_INCREMENT,
  email VARCHAR(254) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  status VARCHAR(10) NOT NULL DEFAULT 'pending',
  is_admin TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TIMESTAMP NULL DEFAULT NULL,
  reviewed_at TIMESTAMP NULL DEFAULT NULL,
  reviewed_by INT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

WATCHLIST_DDL = """CREATE TABLE IF NOT EXISTS watchlist (
  user_id INT NOT NULL,
  ticker VARCHAR(20) NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, ticker)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

PERSONAL_TABLES = ("portfolio", "transactions")      # tables whose rows belong to one account
OPTIONAL_PERSONAL_TABLES = ("price_alerts",)         # the same, but the app creates them on first use, so they may not exist yet

# Columns added to a users table made before approval existed. Accounts that already exist were let in when they
# registered, so they are filled in as approved; the default for NEW rows is then switched to pending.
USERS_COLUMNS = (
    ("status", "VARCHAR(10) NOT NULL DEFAULT 'approved'"),
    ("is_admin", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("reviewed_at", "TIMESTAMP NULL DEFAULT NULL"),
    ("reviewed_by", "INT NULL"),
)


def _has_column(cur, table, column):
    cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s", (table, column))
    return bool(cur.fetchone()[0])


def _has_table(cur, table):
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def owned_tables(cur):
    """Every personal table that exists right now, required ones first."""
    return [*PERSONAL_TABLES, *(t for t in OPTIONAL_PERSONAL_TABLES if _has_table(cur, t))]


def add_user_id_column(cur, table):
    """Add the owner column to `table` unless it is already there. Returns True when it was added."""
    if _has_column(cur, table, "user_id"):
        return False
    cur.execute(f"ALTER TABLE {table} ADD COLUMN user_id INT NULL, ADD KEY idx_{table}_user (user_id)")
    return True


def add_users_columns(cur):
    """Add the approval columns to an existing users table. Returns the names of the columns it added."""
    added = []
    for column, ddl in USERS_COLUMNS:
        if not _has_column(cur, "users", column):
            cur.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
            added.append(column)
    if "status" in added:
        cur.execute("ALTER TABLE users ALTER COLUMN status SET DEFAULT 'pending'")     # new sign-ups wait for approval
    return added


def migrate(conn):
    """Create the accounts tables and the owner column on the personal tables. Safe to run any number of times."""
    cur = conn.cursor()
    cur.execute(USERS_DDL)
    cur.execute(WATCHLIST_DDL)
    done = ["users table ready", "watchlist table ready"]
    added = add_users_columns(cur)
    done.append("users: added " + ", ".join(added) + " (existing accounts are approved)" if added else "users: approval columns already there")
    for table in PERSONAL_TABLES:
        done.append(f"{table}: added user_id column" if add_user_id_column(cur, table) else f"{table}: user_id column already there")
    for table in OPTIONAL_PERSONAL_TABLES:
        if _has_table(cur, table):
            done.append(f"{table}: added user_id column" if add_user_id_column(cur, table) else f"{table}: user_id column already there")
        else:
            done.append(f"{table}: not created yet (the app adds it, with its owner column, the first time it is used)")
    conn.commit()
    return done


def claim_legacy(conn, user_id):
    """Give every row that has no owner yet to `user_id`. Returns {table: rows given}."""
    cur = conn.cursor()
    claimed = {}
    for table in owned_tables(cur):
        cur.execute(f"UPDATE {table} SET user_id = %s WHERE user_id IS NULL", (user_id,))
        claimed[table] = cur.rowcount
    conn.commit()
    return claimed


def delete_account(conn, user_id):
    """Remove an account and everything it owns, all or nothing."""
    cur = conn.cursor()
    try:
        for table in (*owned_tables(cur), "watchlist"):
            cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _connect():
    from screener import get_db_connection
    return get_db_connection()


def _user_id(conn, email):
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email = %s", (auth.normalize_email(email),))
    row = cur.fetchone()
    if not row:
        sys.exit(f"No account found for {email}.")
    return row[0]


def set_status(conn, user_id, status):
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = %s, reviewed_at = NOW() WHERE id = %s", (status, user_id))
    conn.commit()


def set_admin(conn, user_id, is_admin):
    """Grant or take away administrator rights. Only approved accounts can be administrators."""
    cur = conn.cursor()
    if is_admin:
        cur.execute("SELECT status FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row or row[0] != "approved":
            raise auth.AuthError("Only an approved account can be an administrator. Approve it first: python manage_users.py approve EMAIL")
    else:
        cur.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND status = 'approved' AND id <> %s", (user_id,))
        if cur.fetchone()[0] == 0:
            raise auth.AuthError("That is the only administrator. Make someone else an administrator first, or nobody could manage access.")
    cur.execute("UPDATE users SET is_admin = %s WHERE id = %s", (1 if is_admin else 0, user_id))
    conn.commit()


def _ask_password(email):
    while True:
        first = getpass.getpass("Password: ")
        try:
            auth.check_password(first, email)
        except auth.AuthError as e:
            print(e.message)
            continue
        if first == getpass.getpass("Repeat the password: "):
            return first
        print("The two passwords didn't match. Try again.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stock Market Pro account administration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    lu = sub.add_parser("list-users")
    lu.add_argument("--pending", action="store_true", help="only accounts waiting for approval")
    for name in ("create-user", "claim-legacy", "set-password", "delete-user", "approve", "block", "make-admin", "remove-admin"):
        p = sub.add_parser(name)
        p.add_argument("email")
        if name == "delete-user":
            p.add_argument("--yes", action="store_true", help="confirm the deletion")
        if name == "create-user":
            p.add_argument("--admin", action="store_true", help="make the new account an administrator")
    args = ap.parse_args(argv)

    conn = _connect()
    try:
        if args.cmd == "migrate":
            for line in migrate(conn):
                print("✓", line)
            print("\nNext: make yourself an administrator so you can approve new sign-ups:")
            print("      python manage_users.py make-admin YOU@EXAMPLE.COM")
            print("(No account yet? python manage_users.py create-user YOU@EXAMPLE.COM --admin, then claim-legacy.)")
        elif args.cmd == "create-user":
            email = auth.normalize_email(args.email)
            auth.check_email(email)
            user = auth.register(email, _ask_password(email), status="approved", is_admin=args.admin)
            print(f"✓ Created {'administrator ' if args.admin else ''}account #{user['id']} for {user['email']} (approved)")
        elif args.cmd == "claim-legacy":
            claimed = claim_legacy(conn, _user_id(conn, args.email))
            print("✓ Gave " + " and ".join(f"{n} {t} row{'s' if n != 1 else ''}" for t, n in claimed.items()) + f" to {auth.normalize_email(args.email)}")
        elif args.cmd == "set-password":
            user_id = _user_id(conn, args.email)
            cur = conn.cursor()
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (auth.hash_password(_ask_password(auth.normalize_email(args.email))), user_id))
            conn.commit()
            print("✓ Password changed. Any browser that is signed in stays signed in until its session expires.")
        elif args.cmd == "list-users":
            cur = conn.cursor()
            cur.execute("SELECT id, email, status, is_admin, created_at, last_login_at FROM users" + (" WHERE status = 'pending'" if args.pending else "") + " ORDER BY id")
            for uid, email, status, admin, created, last in cur.fetchall():
                role = "admin" if admin else ""
                seen = f"last sign-in {last:%Y-%m-%d}" if last else "never signed in"
                print(f"#{uid:<4} {email:<38} {status:<9} {role:<6} created {created:%Y-%m-%d}   {seen}")
        elif args.cmd in ("approve", "block"):
            user_id = _user_id(conn, args.email)
            if args.cmd == "block":
                cur = conn.cursor(); cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
                if cur.fetchone()[0]:
                    sys.exit("That account is an administrator. Remove its admin rights first: python manage_users.py remove-admin EMAIL")
            set_status(conn, user_id, "approved" if args.cmd == "approve" else "blocked")
            print(f"✓ {auth.normalize_email(args.email)} is now {'approved' if args.cmd == 'approve' else 'blocked'}")
        elif args.cmd in ("make-admin", "remove-admin"):
            set_admin(conn, _user_id(conn, args.email), args.cmd == "make-admin")
            print(f"✓ {auth.normalize_email(args.email)} is {'now an administrator' if args.cmd == 'make-admin' else 'no longer an administrator'}")
        elif args.cmd == "delete-user":
            user_id = _user_id(conn, args.email)
            if not args.yes:
                sys.exit(f"This deletes {args.email} and all of their holdings, transactions and watchlist. Run again with --yes to confirm.")
            delete_account(conn, user_id)
            print(f"✓ Deleted {auth.normalize_email(args.email)}")
    except auth.AuthError as e:
        sys.exit(e.message)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
