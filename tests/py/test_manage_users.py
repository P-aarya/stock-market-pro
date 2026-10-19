"""The account administration script, run against a recording fake database. It must only ever add things, and be safe to repeat."""
import io
import re
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
import auth
import manage_users as mu
from _accounts import FakeStore
from _fakedb import FakeConn

GOOD = "correct horse battery"


def has_column(table_flags):
    """Responder for the information_schema check: {'portfolio': True, ...} says which tables already have user_id."""
    def respond(sql, params):
        if "information_schema.columns" in sql:
            return [{"n": 1 if table_flags.get(params[0]) else 0}]
        return []
    return respond


class Migrate(unittest.TestCase):
    def test_first_run_sets_everything_up(self):
        conn = FakeConn(has_column({}))
        done = mu.migrate(conn)
        self.assertEqual(len(conn.statements("CREATE TABLE IF NOT EXISTS users")), 1)
        self.assertEqual(len(conn.statements("CREATE TABLE IF NOT EXISTS watchlist")), 1)
        alters = [sql for sql, _ in conn.statements("ALTER TABLE")]
        self.assertEqual(len(alters), 2)
        self.assertTrue(any(a.startswith("ALTER TABLE portfolio ADD COLUMN user_id INT NULL") for a in alters))
        self.assertTrue(any(a.startswith("ALTER TABLE transactions ADD COLUMN user_id INT NULL") for a in alters))
        self.assertEqual(conn.commits, 1)
        self.assertIn("portfolio: added user_id column", done); self.assertIn("transactions: added user_id column", done)

    def test_running_it_again_changes_nothing(self):
        conn = FakeConn(has_column({"portfolio": True, "transactions": True}))
        done = mu.migrate(conn)
        self.assertEqual(conn.statements("ALTER TABLE"), [])
        self.assertTrue(all("already there" in line for line in done[2:]))

    def test_a_half_finished_earlier_run_is_completed(self):
        conn = FakeConn(has_column({"portfolio": True}))
        mu.migrate(conn)
        alters = [sql for sql, _ in conn.statements("ALTER TABLE")]
        self.assertEqual(len(alters), 1); self.assertTrue(alters[0].startswith("ALTER TABLE transactions"))

    def test_it_only_ever_adds_never_removes_or_rewrites_data(self):
        conn = FakeConn(has_column({}))
        mu.migrate(conn)
        for sql, _ in conn.log:
            self.assertRegex(sql, r"^(CREATE TABLE IF NOT EXISTS|SELECT|ALTER TABLE \w+ ADD COLUMN)", sql)
            self.assertNotRegex(sql, r"\b(DROP|DELETE|TRUNCATE|UPDATE|RENAME|MODIFY|CHANGE)\b")

    def test_the_schema_matches_what_the_app_queries(self):
        users, watch = mu.USERS_DDL, mu.WATCHLIST_DDL
        for column in ("id INT NOT NULL AUTO_INCREMENT", "email VARCHAR(254) NOT NULL", "password_hash VARCHAR(255) NOT NULL", "last_login_at", "UNIQUE KEY uq_users_email (email)"):
            self.assertIn(column, users)
        for column in ("user_id INT NOT NULL", "ticker VARCHAR(20) NOT NULL", "sort_order INT", "PRIMARY KEY (user_id, ticker)"):
            self.assertIn(column, watch)
        self.assertEqual(auth.MAX_EMAIL, 254)                  # the column is as wide as the longest allowed email
        self.assertLessEqual(len(auth.hash_password(GOOD)), 255)          # and a hash fits its column


class ClaimLegacy(unittest.TestCase):
    def test_only_rows_with_no_owner_are_given_away(self):
        conn = FakeConn(lambda sql, params: 4 if "portfolio" in sql else 9)
        claimed = mu.claim_legacy(conn, 5)
        self.assertEqual(claimed, {"portfolio": 4, "transactions": 9})
        for sql, params in conn.statements("UPDATE"):
            self.assertIn("WHERE user_id IS NULL", sql); self.assertEqual(params, (5,))
        self.assertEqual(len(conn.statements("UPDATE")), 2); self.assertEqual(conn.commits, 1)


class DeleteAccount(unittest.TestCase):
    def test_removes_the_account_and_everything_it_owns_in_one_transaction(self):
        conn = FakeConn()
        mu.delete_account(conn, 5)
        deletes = [(sql, p) for sql, p in conn.log]
        self.assertEqual([re.search(r"FROM (\w+)", sql).group(1) for sql, _ in deletes], ["portfolio", "transactions", "watchlist", "users"])
        self.assertTrue(all(p == (5,) for _, p in deletes))
        self.assertTrue(all("user_id = %s" in sql for sql, _ in deletes[:3])); self.assertIn("WHERE id = %s", deletes[3][0])
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))

    def test_a_failure_part_way_undoes_it_all(self):
        conn = FakeConn(fail_on="FROM watchlist")
        with self.assertRaises(RuntimeError):
            mu.delete_account(conn, 5)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.saved_store = auth.store
        auth.store = FakeStore()

    def tearDown(self):
        auth.store = self.saved_store

    def run_cli(self, *argv, conn=None, passwords=()):
        conn = conn or FakeConn(lambda sql, p: [{"id": 5}] if "FROM users WHERE email" in sql else [])
        out = io.StringIO()
        with mock.patch.object(mu, "_connect", return_value=conn), mock.patch("getpass.getpass", side_effect=list(passwords)), redirect_stdout(out):
            mu.main(list(argv))
        return out.getvalue(), conn

    def test_migrate_reports_what_it_did(self):
        out, conn = self.run_cli("migrate", conn=FakeConn(has_column({})))
        self.assertIn("users table ready", out); self.assertIn("portfolio: added user_id column", out); self.assertIn("claim-legacy", out)
        self.assertTrue(conn.closed)

    def test_create_user_stores_only_a_hash(self):
        out, _ = self.run_cli("create-user", " Me@Example.com ", passwords=[GOOD, GOOD])
        row = auth.store.find_by_email("me@example.com")
        self.assertIn("Created account", out); self.assertTrue(auth.verify_password(GOOD, row["password_hash"])); self.assertNotIn(GOOD, row["password_hash"])

    def test_create_user_asks_again_after_a_weak_or_mismatched_password(self):
        out, _ = self.run_cli("create-user", "me@example.com", passwords=["short", GOOD, "different-one", GOOD, GOOD])
        self.assertIn("at least 8", out); self.assertIn("didn't match", out); self.assertIsNotNone(auth.store.find_by_email("me@example.com"))

    def test_claim_legacy_names_the_account_it_gave_the_rows_to(self):
        out, conn = self.run_cli("claim-legacy", "me@example.com", conn=FakeConn(lambda sql, p: [{"id": 5}] if "FROM users" in sql else 3))
        self.assertIn("me@example.com", out); self.assertEqual([p for _, p in conn.statements("UPDATE")], [(5,), (5,)])

    def test_claiming_for_an_unknown_account_changes_nothing(self):
        conn = FakeConn(lambda sql, p: [])
        with mock.patch.object(mu, "_connect", return_value=conn), self.assertRaises(SystemExit) as cm:
            mu.main(["claim-legacy", "ghost@example.com"])
        self.assertIn("No account found", str(cm.exception)); self.assertEqual(conn.statements("UPDATE"), [])

    def test_delete_user_needs_an_explicit_yes(self):
        conn = FakeConn(lambda sql, p: [{"id": 5}] if "FROM users WHERE email" in sql else [])
        with mock.patch.object(mu, "_connect", return_value=conn), self.assertRaises(SystemExit) as cm:
            mu.main(["delete-user", "me@example.com"])
        self.assertIn("--yes", str(cm.exception)); self.assertEqual(conn.statements("DELETE"), [])
        out, conn = self.run_cli("delete-user", "me@example.com", "--yes")
        self.assertIn("Deleted", out); self.assertEqual(len(conn.statements("DELETE")), 4)

    def test_set_password_stores_a_new_hash(self):
        _, conn = self.run_cli("set-password", "me@example.com", passwords=[GOOD, GOOD])
        (sql, params), = conn.statements("UPDATE users SET password_hash")
        self.assertTrue(auth.verify_password(GOOD, params[0])); self.assertEqual(params[1], 5)

    def test_list_users(self):
        rows = [{"a": 1, "b": "a@x.co", "c": datetime(2026, 1, 2), "d": datetime(2026, 2, 3)}, {"a": 2, "b": "b@x.co", "c": datetime(2026, 1, 5), "d": None}]
        out, _ = self.run_cli("list-users", conn=FakeConn(lambda sql, p: rows if "FROM users ORDER" in sql else []))
        self.assertIn("a@x.co", out); self.assertIn("last sign-in 2026-02-03", out); self.assertIn("never signed in", out)


if __name__ == "__main__":
    unittest.main()
