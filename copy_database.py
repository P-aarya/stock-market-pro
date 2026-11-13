"""
Copy the whole database from the OLD server (Aiven) into the NEW one (the MySQL private service on Render).

Run it ONCE, from the Render Shell of the website service (that service already has the new database's settings:
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME). You only give it the old server's address; it asks for the
old password, so the password isn't saved anywhere:

    python copy_database.py --from-host mysql-xxxx.aivencloud.com --from-port 25303 --from-user avnadmin --from-db defaultdb --check
        (--check only reads both sides and lists the tables and row counts; nothing is changed)
    python copy_database.py --from-host mysql-xxxx.aivencloud.com --from-port 25303 --from-user avnadmin --from-db defaultdb

What it does, table by table: reads the table's exact definition on the old server, creates it on the new one
(replacing a table of the same name that is already there, e.g. an empty one the website made when it started),
copies every row in batches, then counts the rows on both sides. Views are copied after the tables. Tables that only
exist on the new server are left alone. The old server is only read, never changed.

At the end it prints one line per table with both row counts and ✓ when they match, and stops with an error
(exit code 1) if any table doesn't match, so you know whether to run it again.
"""
import argparse
import getpass
import os
import re
import sys
import time

import mysql.connector

BATCH = 1000


def connect(host, port, user, password, database, ssl):
    cfg = {"host": host, "port": int(port), "user": user, "password": password, "database": database,
           "charset": "utf8mb4", "use_unicode": True, "connection_timeout": 30, "autocommit": False}
    if ssl:
        cfg["ssl_disabled"] = False
    return mysql.connector.connect(**cfg)


def new_server_settings():
    missing = [k for k in ("DB_HOST", "DB_USER", "DB_NAME") if not os.getenv(k)]
    if missing:
        sys.exit(f"The new database's settings are missing ({', '.join(missing)}). Run this from the Render Shell of the "
                 "website service, which has them.")
    return {"host": os.getenv("DB_HOST"), "port": os.getenv("DB_PORT", "3306"), "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD") or os.getenv("DB_PASS", ""), "database": os.getenv("DB_NAME"),
            "ssl": os.getenv("DB_SSL", "false").lower() == "true"}


def q(name):
    return "`" + str(name).replace("`", "``") + "`"


def tables_and_views(conn):
    cur = conn.cursor()
    cur.execute("SHOW FULL TABLES")
    rows = cur.fetchall()
    cur.close()
    tables = sorted(r[0] for r in rows if r[1] == "BASE TABLE")
    views = sorted(r[0] for r in rows if r[1] == "VIEW")
    return tables, views


def count(conn, table):
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {q(table)}")
    n = cur.fetchone()[0]
    cur.close()
    return int(n)


def create_statement(conn, table):
    cur = conn.cursor()
    cur.execute(f"SHOW CREATE TABLE {q(table)}")
    ddl = cur.fetchone()[1]
    cur.close()
    return ddl


def view_statement(conn, view):
    """CREATE VIEW without the old server's user (DEFINER), which doesn't exist on the new server."""
    cur = conn.cursor()
    cur.execute(f"SHOW CREATE VIEW {q(view)}")
    ddl = cur.fetchone()[1]
    cur.close()
    ddl = re.sub(r"\s+DEFINER=`[^`]*`@`[^`]*`", "", ddl)
    ddl = re.sub(r"\s+SQL SECURITY DEFINER", " SQL SECURITY INVOKER", ddl)
    return ddl


def copy_table(src, dst, table):
    """Recreate one table on the new server and copy its rows. Returns the number of rows copied."""
    ddl = create_statement(src, table)
    w = dst.cursor()
    w.execute(f"DROP TABLE IF EXISTS {q(table)}")
    w.execute(ddl)
    r = src.cursor()                                   # unbuffered: rows are read in batches, not all at once
    r.execute(f"SELECT * FROM {q(table)}")
    cols = [d[0] for d in r.description]
    insert = f"INSERT INTO {q(table)} ({', '.join(q(c) for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    copied = 0
    while True:
        rows = r.fetchmany(BATCH)
        if not rows:
            break
        w.executemany(insert, [tuple(row) for row in rows])
        dst.commit()
        copied += len(rows)
        if copied % (BATCH * 20) == 0:
            print(f"      … {copied:,} rows", flush=True)
    r.close()
    w.close()
    dst.commit()
    return copied


def main(argv=None):
    p = argparse.ArgumentParser(description="Copy the whole database from the old server (Aiven) to the new one (Render).")
    p.add_argument("--from-host", required=True, help="old server address, e.g. mysql-xxxx.aivencloud.com")
    p.add_argument("--from-port", default="3306", help="old server port, e.g. 25303")
    p.add_argument("--from-user", required=True, help="old server user, e.g. avnadmin")
    p.add_argument("--from-db", required=True, help="old database name, e.g. defaultdb")
    p.add_argument("--from-no-ssl", action="store_true", help="connect to the old server without SSL (Aiven needs SSL)")
    p.add_argument("--check", action="store_true", help="only list the tables and row counts on both sides; change nothing")
    p.add_argument("--only", nargs="*", default=None, help="copy only these tables (to redo one that didn't match)")
    a = p.parse_args(argv)

    new = new_server_settings()
    if new["host"] == a.from_host and str(new["port"]) == str(a.from_port):
        sys.exit("The old and the new server are the same. Check DB_HOST on this service and --from-host.")
    password = os.getenv("FROM_DB_PASSWORD") or getpass.getpass(f"Password for {a.from_user} on the OLD server: ")

    print(f"\nOld server: {a.from_host}:{a.from_port} / {a.from_db}")
    print(f"New server: {new['host']}:{new['port']} / {new['database']}\n")
    src = connect(a.from_host, a.from_port, a.from_user, password, a.from_db, not a.from_no_ssl)
    dst = connect(new["host"], new["port"], new["user"], new["password"], new["database"], new["ssl"])

    for conn in (src, dst):
        c = conn.cursor()
        c.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        c.close()
    s = src.cursor()
    s.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")   # every table is read as it was at one moment
    s.execute("SELECT @@GLOBAL.sql_mode")
    src_mode = s.fetchone()[0]
    s.close()
    d = dst.cursor()
    d.execute("SELECT @@GLOBAL.sql_mode")
    dst_mode = d.fetchone()[0]
    # Rows go in exactly as they are (an id of 0 stays 0) and table order doesn't matter
    d.execute("SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO'")
    d.execute("SET SESSION foreign_key_checks = 0")
    d.execute("SET SESSION unique_checks = 0")
    d.close()

    tables, views = tables_and_views(src)
    new_tables, new_views = tables_and_views(dst)
    if a.only:
        unknown = [t for t in a.only if t not in tables]
        if unknown:
            sys.exit(f"Not on the old server: {', '.join(unknown)}")
        tables, views = [t for t in tables if t in a.only], []

    if a.check:
        print(f"{'table':<32}{'old server':>14}{'new server':>14}")
        for t in tables:
            print(f"{t:<32}{count(src, t):>14,}{(count(dst, t) if t in new_tables else '—'):>14}")
        for v in views:
            print(f"{v + ' (view)':<32}")
        extra = [t for t in new_tables if t not in tables]
        if extra:
            print(f"\nOnly on the new server (left alone): {', '.join(extra)}")
        print(f"\nSQL mode  old: {src_mode}\n          new: {dst_mode}")
        print("\nNothing was changed (--check).")
        return 0

    print(f"Copying {len(tables)} tables" + (f" and {len(views)} views" if views else "") + "...\n")
    started = time.time()
    for i, t in enumerate(tables, 1):
        t0 = time.time()
        print(f"  [{i}/{len(tables)}] {t}", flush=True)
        n = copy_table(src, dst, t)
        print(f"      {n:,} rows in {time.time() - t0:.1f}s", flush=True)
    for v in views:
        d = dst.cursor()
        d.execute(f"DROP VIEW IF EXISTS {q(v)}")
        d.execute(view_statement(src, v))
        d.close()
        dst.commit()
        print(f"  view {v}")
    src.commit()                                        # ends the read snapshot

    print(f"\n{'table':<32}{'old server':>14}{'new server':>14}")
    bad = []
    for t in tables:
        o, n = count(src, t), count(dst, t)
        ok = o == n
        if not ok:
            bad.append(t)
        print(f"{t:<32}{o:>14,}{n:>14,}  {'✓' if ok else '✗ DIFFERENT'}")
    extra = [t for t in new_tables if t not in tables]
    if extra and not a.only:
        print(f"\nOnly on the new server (left alone): {', '.join(extra)}")
    if src_mode != dst_mode:
        print(f"\nNote: the servers' SQL modes differ.\n  old: {src_mode}\n  new: {dst_mode}")
    src.close()
    dst.close()
    print(f"\nDone in {time.time() - started:.0f}s.")
    if bad:
        print(f"✗ {len(bad)} table(s) don't match: {', '.join(bad)}. (A table the price updater is writing to can "
              f"differ by a few rows; otherwise run again with --only {' '.join(bad)})")
        return 1
    print(f"✓ All {len(tables)} tables copied and the row counts match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
