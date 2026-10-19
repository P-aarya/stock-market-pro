"""
Account administration. Run it from the project folder with the virtual environment active.

    python manage_users.py migrate                  set up the accounts tables (safe to run again)
    python manage_users.py create-user EMAIL        create an account (asks for the password)
    python manage_users.py claim-legacy EMAIL       give the portfolio and transactions saved before accounts existed to EMAIL
    python manage_users.py set-password EMAIL       reset someone's password (there is no "forgot password" email)
    python manage_users.py list-users
    python manage_users.py delete-user EMAIL --yes  delete an account together with its holdings, transactions and watchlist

Back the database up before the first `migrate`: it adds a column to the portfolio and transactions tables.
"""
import argparse
import getpass
import sys
import auth

USERS_DDL = """CREATE TABLE IF NOT EXISTS users (
  id INT NOT NULL AUTO_INCREMENT,
  email VARCHAR(254) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TIMESTAMP NULL DEFAULT NULL,
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


def add_user_id_column(cur, table):
    """Add the owner column to `table` unless it is already there. Returns True when it was added."""
    cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s AND column_name = 'user_id'", (table,))
    if cur.fetchone()[0]:
        return False
    cur.execute(f"ALTER TABLE {table} ADD COLUMN user_id INT NULL, ADD KEY idx_{table}_user (user_id)")
    return True


def migrate(conn):
    """Create the accounts tables and the owner column on the personal tables. Safe to run any number of times."""
    cur = conn.cursor()
    cur.execute(USERS_DDL)
    cur.execute(WATCHLIST_DDL)
    done = ["users table ready", "watchlist table ready"]
    for table in PERSONAL_TABLES:
        done.append(f"{table}: added user_id column" if add_user_id_column(cur, table) else f"{table}: user_id column already there")
    conn.commit()
    return done


def claim_legacy(conn, user_id):
    """Give every row that has no owner yet to `user_id`. Returns {table: rows given}."""
    cur = conn.cursor()
    claimed = {}
    for table in PERSONAL_TABLES:
        cur.execute(f"UPDATE {table} SET user_id = %s WHERE user_id IS NULL", (user_id,))
        claimed[table] = cur.rowcount
    conn.commit()
    return claimed


def delete_account(conn, user_id):
    """Remove an account and everything it owns, all or nothing."""
    cur = conn.cursor()
    try:
        for table in (*PERSONAL_TABLES, "watchlist"):
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
    sub.add_parser("list-users")
    for name in ("create-user", "claim-legacy", "set-password", "delete-user"):
        p = sub.add_parser(name)
        p.add_argument("email")
        if name == "delete-user":
            p.add_argument("--yes", action="store_true", help="confirm the deletion")
    args = ap.parse_args(argv)

    conn = _connect()
    try:
        if args.cmd == "migrate":
            for line in migrate(conn):
                print("✓", line)
            print("\nNext: create your account (python manage_users.py create-user YOU@EXAMPLE.COM), then")
            print("      give it the existing portfolio (python manage_users.py claim-legacy YOU@EXAMPLE.COM).")
        elif args.cmd == "create-user":
            email = auth.normalize_email(args.email)
            auth.check_email(email)
            user = auth.register(email, _ask_password(email))
            print(f"✓ Created account #{user['id']} for {user['email']}")
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
            cur.execute("SELECT id, email, created_at, last_login_at FROM users ORDER BY id")
            for uid, email, created, last in cur.fetchall():
                print(f"#{uid:<4} {email:<40} created {created:%Y-%m-%d}   last sign-in {last:%Y-%m-%d} " if last else f"#{uid:<4} {email:<40} created {created:%Y-%m-%d}   never signed in")
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
