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


def has_column(done):
    """Responder for the information_schema checks. `done` says which columns already exist: a table name (all its
    columns), or a (table, column) pair. A ("table", name) key says an optional table exists at all."""
    def respond(sql, params):
        if "information_schema.columns" in sql:
            table, column = params
            return [{"n": 1 if done.get(table) or done.get((table, column)) else 0}]
        if "information_schema.tables" in sql:
            return [{"n": 1 if done.get(("table", params[0])) else 0}]
        return []
    return respond


ALL_DONE = {"portfolio": True, "transactions": True, "users": True, "user_profiles": True}
SETTLED_USERS = ("status", "is_admin", "reviewed_at", "reviewed_by", "tier", "deleted_at", "full_name", "sessions_after")   # every column of users that migrate looks for


class Migrate(unittest.TestCase):
    def test_first_run_sets_everything_up(self):
        conn = FakeConn(has_column({}))
        done = mu.migrate(conn)
        self.assertEqual(len(conn.statements("CREATE TABLE IF NOT EXISTS users")), 1)
        self.assertEqual(len(conn.statements("CREATE TABLE IF NOT EXISTS watchlist")), 1)
        alters = [sql for sql, _ in conn.statements("ALTER TABLE")]
        self.assertTrue(any(a.startswith("ALTER TABLE portfolio ADD COLUMN user_id INT NULL") for a in alters))
        self.assertTrue(any(a.startswith("ALTER TABLE transactions ADD COLUMN user_id INT NULL") for a in alters))
        for column in ("status", "is_admin", "reviewed_at", "reviewed_by"):
            self.assertTrue(any(a.startswith(f"ALTER TABLE users ADD COLUMN {column} ") for a in alters), column)
        self.assertEqual(conn.commits, 3)                                    # the accounts, the profile and reset tables, the Help Desk tables
        self.assertIn("portfolio: added user_id column", done); self.assertIn("transactions: added user_id column", done)
        self.assertTrue(any("users: added status, is_admin" in line and "existing accounts are approved" in line for line in done))

    def test_existing_accounts_are_approved_and_new_sign_ups_default_to_pending(self):
        conn = FakeConn(has_column({"portfolio": True, "transactions": True}))
        mu.migrate(conn)
        alters = [sql for sql, _ in conn.statements("ALTER TABLE users")]
        add_status = next(a for a in alters if "ADD COLUMN status" in a)
        self.assertIn("DEFAULT 'approved'", add_status)                      # the rows that exist now keep their access
        self.assertLess(alters.index(add_status), alters.index("ALTER TABLE users ALTER COLUMN status SET DEFAULT 'pending'"))
        self.assertIn("status VARCHAR(10) NOT NULL DEFAULT 'pending'", mu.USERS_DDL)   # a brand-new table starts the right way round

    def test_price_alerts_get_an_owner_column_when_that_table_exists(self):
        conn = FakeConn(has_column({("table", "price_alerts"): True}))
        done = mu.migrate(conn)
        self.assertTrue(any(sql.startswith("ALTER TABLE price_alerts ADD COLUMN user_id INT NULL") for sql, _ in conn.statements("ALTER TABLE")))
        self.assertIn("price_alerts: added user_id column", done)
        conn = FakeConn(has_column({**ALL_DONE, ("table", "price_alerts"): True, "price_alerts": True}))
        self.assertIn("price_alerts: user_id column already there", mu.migrate(conn))
        self.assertEqual(conn.statements("ALTER TABLE"), [])

    def test_a_price_alerts_table_that_does_not_exist_yet_is_left_for_the_app_to_create(self):
        conn = FakeConn(has_column({}))
        done = mu.migrate(conn)
        self.assertEqual(conn.statements("ALTER TABLE price_alerts"), [])
        self.assertTrue(any(line.startswith("price_alerts: not created yet") for line in done))

    def test_running_it_again_changes_nothing(self):
        conn = FakeConn(has_column(ALL_DONE))
        done = mu.migrate(conn)
        self.assertEqual(conn.statements("ALTER TABLE"), [])
        self.assertTrue(all(("already there" in line or "not created yet" in line or line.endswith("table ready")) for line in done[2:]), done)   # "table ready" = CREATE TABLE IF NOT EXISTS, harmless

    def test_a_half_finished_earlier_run_is_completed(self):
        conn = FakeConn(has_column({"portfolio": True, ("users", "status"): True, ("users", "is_admin"): True}))
        mu.migrate(conn)
        alters = [sql for sql, _ in conn.statements("ALTER TABLE")]
        self.assertEqual(sorted(a.split(" ADD COLUMN ")[0] + " " + a.split(" ADD COLUMN ")[1].split()[0] for a in alters),
                         ["ALTER TABLE transactions user_id", "ALTER TABLE user_profiles interests", "ALTER TABLE user_profiles invest_range", "ALTER TABLE user_profiles recent_stocks",
                          "ALTER TABLE users deleted_at", "ALTER TABLE users full_name", "ALTER TABLE users reviewed_at", "ALTER TABLE users reviewed_by",
                          "ALTER TABLE users sessions_after", "ALTER TABLE users tier"])

    def test_the_plan_column_is_added_and_existing_accounts_start_on_the_basic_plan(self):
        everything_else = {"portfolio": True, "transactions": True, "user_profiles": True, **{("users", c): True for c in SETTLED_USERS if c != "tier"}}
        conn = FakeConn(has_column(everything_else))
        done = mu.migrate(conn)
        alters = [sql for sql, _ in conn.statements("ALTER TABLE")]
        self.assertEqual(alters, ["ALTER TABLE users ADD COLUMN tier TINYINT NOT NULL DEFAULT 2"])                  # accounts that exist now stay on Basic
        self.assertTrue(any("users: added tier" in line and "existing accounts stay on the Basic plan" in line and "new sign-ups start on Free" in line for line in done), done)
        again = mu.migrate(FakeConn(has_column({**everything_else, ("users", "tier"): True})))
        self.assertIn("users: tier column already there", again)

    def test_the_deletion_column_is_added_and_the_migration_says_what_it_is_for(self):
        everything_else = {"portfolio": True, "transactions": True, "user_profiles": True, **{("users", c): True for c in SETTLED_USERS if c != "deleted_at"}}
        conn = FakeConn(has_column(everything_else))
        done = mu.migrate(conn)
        self.assertEqual([sql for sql, _ in conn.statements("ALTER TABLE")], ["ALTER TABLE users ADD COLUMN deleted_at TIMESTAMP NULL DEFAULT NULL"])
        self.assertTrue(any("users: added deleted_at" in line and "delete and restore their own account" in line for line in done), done)
        self.assertIn("deleted_at TIMESTAMP NULL DEFAULT NULL", mu.USERS_DDL)                      # a brand-new table has it too

    def test_a_brand_new_users_table_has_the_plan_column_defaulting_to_free(self):
        self.assertIn("tier TINYINT NOT NULL DEFAULT 1", mu.USERS_DDL)

    def test_it_only_ever_adds_never_removes_or_rewrites_data(self):
        conn = FakeConn(has_column({}))
        mu.migrate(conn)
        for sql, _ in conn.log:
            self.assertRegex(sql, r"^(CREATE TABLE IF NOT EXISTS|SELECT|ALTER TABLE \w+ ADD COLUMN|ALTER TABLE users ALTER COLUMN status SET DEFAULT)", sql)
            self.assertNotRegex(sql, r"\b(DROP|DELETE|TRUNCATE|UPDATE|RENAME|MODIFY|CHANGE)\b")

    def test_the_schema_matches_what_the_app_queries(self):
        users, watch = mu.USERS_DDL, mu.WATCHLIST_DDL
        for column in ("id INT NOT NULL AUTO_INCREMENT", "email VARCHAR(254) NOT NULL", "password_hash VARCHAR(255) NOT NULL", "last_login_at", "is_admin TINYINT(1)",
                       "reviewed_at", "reviewed_by", "UNIQUE KEY uq_users_email (email)"):
            self.assertIn(column, users)
        for column in ("user_id INT NOT NULL", "ticker VARCHAR(20) NOT NULL", "sort_order INT", "PRIMARY KEY (user_id, ticker)"):
            self.assertIn(column, watch)
        self.assertEqual(auth.MAX_EMAIL, 254)                             # the column is as wide as the longest allowed email
        self.assertLessEqual(len(auth.hash_password(GOOD)), 255)          # and a hash fits its column
        for status in auth.STATUSES:
            self.assertLessEqual(len(status), 10)                         # every status fits the column


class ClaimLegacy(unittest.TestCase):
    def test_only_rows_with_no_owner_are_given_away(self):
        conn = FakeConn(lambda sql, params: 4 if "portfolio" in sql else 9)
        claimed = mu.claim_legacy(conn, 5)
        self.assertEqual(claimed, {"portfolio": 4, "transactions": 9})
        for sql, params in conn.statements("UPDATE"):
            self.assertIn("WHERE user_id IS NULL", sql); self.assertEqual(params, (5,))
        self.assertEqual(len(conn.statements("UPDATE")), 2); self.assertEqual(conn.commits, 1)


class ClaimLegacyAlerts(unittest.TestCase):
    def test_price_alerts_are_handed_over_too_when_the_table_exists(self):
        def respond(sql, params):
            if "information_schema.tables" in sql:
                return [{"n": 1}]
            return {"portfolio": 4, "transactions": 9, "price_alerts": 2}[next(t for t in ("portfolio", "transactions", "price_alerts") if t in sql)]
        conn = FakeConn(respond)
        self.assertEqual(mu.claim_legacy(conn, 5), {"portfolio": 4, "transactions": 9, "price_alerts": 2})
        for sql, params in conn.statements("UPDATE"):
            self.assertIn("WHERE user_id IS NULL", sql); self.assertEqual(params, (5,))


class DeleteAccount(unittest.TestCase):
    def test_price_alerts_go_with_the_account_when_the_table_exists(self):
        conn = FakeConn(lambda sql, params: [{"n": 1 if params[0] == "price_alerts" else 0}] if "information_schema.tables" in sql else [])
        mu.delete_account(conn, 5)
        self.assertEqual([re.search(r"FROM (\w+)", sql).group(1) for sql, _ in conn.statements("DELETE")], ["portfolio", "transactions", "price_alerts", "watchlist", "users"])

    def test_the_help_desk_notifications_reset_links_and_profile_go_too_when_those_tables_exist(self):
        conn = FakeConn(lambda sql, params: [{"n": 1}] if "information_schema.tables" in sql else [])             # every table exists
        mu.delete_account(conn, 5)
        deleted = [re.search(r"FROM (\w+)", sql).group(1) for sql, _ in conn.statements("DELETE")]
        self.assertEqual(deleted, ["portfolio", "transactions", "price_alerts", "watchlist", "support_messages", "support_tickets",
                                   "user_notifications", "password_resets", "email_changes", "restore_links", "user_profiles", "users"])
        self.assertTrue(all(p == (5,) for _, p in conn.statements("DELETE")))
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))

    def test_removes_the_account_and_everything_it_owns_in_one_transaction(self):
        conn = FakeConn()
        mu.delete_account(conn, 5)
        deletes = conn.statements("DELETE")
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
        self.assertIn("users table ready", out); self.assertIn("portfolio: added user_id column", out); self.assertIn("make-admin", out)
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
        self.assertTrue(auth.verify_password(GOOD, params[0])); self.assertEqual(params[2], 5)
        self.assertIn("sessions_after = %s", sql); self.assertGreater(params[1], 1_700_000_000)               # other devices are signed out

    def test_list_users(self):
        rows = [{"a": 1, "b": "a@x.co", "s": "approved", "m": 1, "t": 2, "c": datetime(2026, 1, 2), "d": datetime(2026, 2, 3), "x": None},
                {"a": 2, "b": "b@x.co", "s": "pending", "m": 0, "t": 2, "c": datetime(2026, 1, 5), "d": None, "x": None}]
        out, _ = self.run_cli("list-users", conn=FakeConn(lambda sql, p: rows if "FROM users" in sql else []))
        self.assertIn("a@x.co", out); self.assertIn("admin", out); self.assertIn("last sign-in 2026-02-03", out)
        self.assertIn("pending", out); self.assertIn("never signed in", out)
        self.assertIn("Basic", out)                                  # each account's plan is shown

    def test_list_users_shows_the_longer_plan_names_in_full(self):
        rows = [{"a": 1, "b": "a@x.co", "s": "approved", "m": 0, "t": 3, "c": datetime(2026, 1, 2), "d": None, "x": None},
                {"a": 2, "b": "b@x.co", "s": "approved", "m": 0, "t": 4, "c": datetime(2026, 1, 5), "d": None, "x": None}]
        out, _ = self.run_cli("list-users", conn=FakeConn(lambda sql, p: rows if "FROM users" in sql else []))
        self.assertIn("AI Stock Finder", out); self.assertIn("AI Interaction", out)

    def test_set_plan_moves_one_account_to_the_plan_named_or_numbered(self):
        for typed, level, name in ((["3"], 3, "AI Stock Finder"), (["Deluxe"], 5, "Deluxe"), (["ai", "stock", "finder"], 3, "AI Stock Finder"), (["premier"], 6, "Premier"),
                                   (["1"], 1, "Free"), (["free"], 1, "Free")):
            out, conn = self.run_cli("set-plan", "Me@Example.com", *typed)
            (sql, params), = conn.statements("UPDATE users SET tier")
            self.assertEqual(params, (level, 5), typed); self.assertIn(name, out); self.assertEqual(conn.commits, 1)

    def test_set_plan_refuses_anything_unknown_and_changes_nothing(self):
        for typed in ("0", "7", "gold", "AI"):
            conn = FakeConn(lambda sql, p: [{"id": 5}] if "FROM users WHERE email" in sql else [])
            with mock.patch.object(mu, "_connect", return_value=conn), self.assertRaises(SystemExit) as cm:
                mu.main(["set-plan", "me@example.com", typed])
            self.assertIn("Choose one of", str(cm.exception), typed); self.assertIn("1 = Free ($0.00)", str(cm.exception)); self.assertIn("6 = Premier ($49.99)", str(cm.exception))
            self.assertEqual(conn.statements("UPDATE"), [], typed)

    def test_set_plan_for_an_unknown_account_changes_nothing(self):
        conn = FakeConn(lambda sql, p: [])
        with mock.patch.object(mu, "_connect", return_value=conn), self.assertRaises(SystemExit) as cm:
            mu.main(["set-plan", "ghost@example.com", "Basic"])
        self.assertIn("No account found", str(cm.exception)); self.assertEqual(conn.statements("UPDATE"), [])

    def test_list_users_says_when_a_deleted_account_goes_for_good(self):
        rows = [{"a": 3, "b": "gone@x.co", "s": "deleted", "m": 0, "t": 2, "c": datetime(2026, 1, 2), "d": None, "x": datetime(2026, 11, 10)}]
        out, _ = self.run_cli("list-users", conn=FakeConn(lambda sql, p: rows if "FROM users" in sql else []))
        self.assertIn("deleted", out); self.assertIn("deleted 2026-11-10, removed for good after 30 days", out)

    def test_purge_deleted_removes_only_what_the_database_says_is_past_its_30_days(self):
        def respond(sql, params):
            if "SELECT id FROM users WHERE status = 'deleted'" in sql:
                return [{"id": 3}, {"id": 4}]
            if "FOR UPDATE" in sql:
                return [{"id": params[0]}] if params[0] == 3 else []                           # 4 was restored a moment ago
            if "information_schema.tables" in sql:
                return [{"n": 0}]
            return 1 if sql.startswith("DELETE") else []
        with mock.patch.object(auth, "now", return_value=1_800_000_000.7):
            out, conn = self.run_cli("purge-deleted", conn=FakeConn(respond))
        self.assertIn("Permanently removed 1 account deleted more than 30 days ago", out)
        self.assertEqual(conn.statements("SELECT id FROM users WHERE status = 'deleted'")[0][1], (1_800_000_000 - 30 * 86400,))
        self.assertEqual([p for s, p in conn.log if s.startswith("DELETE FROM users")], [(3,)])
        out, _ = self.run_cli("purge-deleted", conn=FakeConn(lambda sql, p: []))
        self.assertIn("Permanently removed 0 accounts", out)

    def test_approving_an_account_also_clears_a_deletion_so_an_operator_can_restore_one(self):
        conn = FakeConn(lambda sql, p: [{"id": 5}] if "SELECT id FROM users" in sql else [])
        _, conn = self.run_cli("approve", "me@example.com", conn=conn)
        (sql, params), = conn.statements("UPDATE users SET status")
        self.assertIn("deleted_at = NULL", sql); self.assertEqual(params, ("approved", 5))

    def test_list_users_can_show_only_those_waiting(self):
        _, conn = self.run_cli("list-users", "--pending", conn=FakeConn(lambda sql, p: []))
        self.assertIn("WHERE status = 'pending'", conn.log[0][0])

    def test_approve_and_block_change_one_account(self):
        for cmd, status in (("approve", "approved"), ("block", "blocked")):
            conn = FakeConn(lambda sql, p: [{"id": 5}] if "SELECT id FROM users" in sql else [{"is_admin": 0}] if "SELECT is_admin" in sql else [])
            out, conn = self.run_cli(cmd, "me@example.com", conn=conn)
            (sql, params), = conn.statements("UPDATE users SET status")
            self.assertEqual(params, (status, 5)); self.assertIn(status, out)

    def test_an_administrator_cannot_be_blocked_from_the_command_line_either(self):
        conn = FakeConn(lambda sql, p: [{"id": 5}] if "SELECT id FROM users" in sql else [{"is_admin": 1}] if "SELECT is_admin" in sql else [])
        with mock.patch.object(mu, "_connect", return_value=conn), self.assertRaises(SystemExit) as cm:
            mu.main(["block", "me@example.com"])
        self.assertIn("remove-admin", str(cm.exception)); self.assertEqual(conn.statements("UPDATE"), [])

    def test_make_admin_needs_an_approved_account_and_remove_admin_keeps_one_administrator(self):
        def respond(admins=1, status="approved"):
            return lambda sql, p: ([{"id": 5}] if "SELECT id FROM users" in sql else [{"status": status}] if "SELECT status" in sql
                                   else [{"n": admins}] if "COUNT(*)" in sql else [])
        out, conn = self.run_cli("make-admin", "me@example.com", conn=FakeConn(respond()))
        self.assertEqual(conn.statements("UPDATE users SET is_admin")[0][1], (1, 5)); self.assertIn("now an administrator", out)
        for status in ("pending", "blocked"):
            conn = FakeConn(respond(status=status))
            with mock.patch.object(mu, "_connect", return_value=conn), self.assertRaises(SystemExit) as cm:
                mu.main(["make-admin", "me@example.com"])
            self.assertIn("approved account", str(cm.exception)); self.assertEqual(conn.statements("UPDATE"), [])
        out, conn = self.run_cli("remove-admin", "me@example.com", conn=FakeConn(respond(admins=1)))      # another admin exists
        self.assertEqual(conn.statements("UPDATE users SET is_admin")[0][1], (0, 5))
        conn = FakeConn(respond(admins=0))                                                              # the only one
        with mock.patch.object(mu, "_connect", return_value=conn), self.assertRaises(SystemExit) as cm:
            mu.main(["remove-admin", "me@example.com"])
        self.assertIn("only administrator", str(cm.exception)); self.assertEqual(conn.statements("UPDATE"), [])

    def test_create_user_can_make_an_administrator(self):
        out, _ = self.run_cli("create-user", "boss@example.com", "--admin", passwords=[GOOD, GOOD])
        row = auth.store.users[1]
        self.assertEqual((row["status"], row["is_admin"]), ("approved", True)); self.assertIn("administrator", out)
        self.run_cli("create-user", "plain@example.com", passwords=[GOOD, GOOD])
        self.assertEqual((auth.store.users[2]["status"], auth.store.users[2]["is_admin"]), ("approved", False))


if __name__ == "__main__":
    unittest.main()
