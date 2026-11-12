"""The SQL behind deleting an account for good, and the store's new queries, checked against a recording fake database."""
import unittest
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

import mysql.connector

import _path  # noqa: F401
import _accounts  # noqa: F401  (installs the "no real database" safety net)
import account_data
import auth
from _fakedb import FakeConn

CUTOFF = 1_790_000_000


def responder(found=True, tables=("price_alerts",), update_rows=1, rows=None):
    """Answers the queries account_data / MySQLStore make."""
    def respond(sql, params):
        if "information_schema.tables" in sql:
            return [{"n": 1 if params[0] in tables else 0}]
        if "FOR UPDATE" in sql:
            return [{"id": params[0]}] if found else []
        if sql.startswith(("DELETE", "UPDATE")):
            return update_rows
        if rows is not None:
            return rows(sql, params) if callable(rows) else rows
        return []
    return respond


class RemovingOneAccountForGood(unittest.TestCase):
    def test_it_locks_checks_and_then_removes_everything_the_account_owns_in_one_go(self):
        conn = FakeConn(responder())
        self.assertTrue(account_data.purge_one(conn, 5, CUTOFF))
        select = conn.statements("FOR UPDATE")
        self.assertEqual(len(select), 1)
        sql, params = select[0]
        self.assertEqual(params, (5, CUTOFF))
        for guard in ("status = 'deleted'", "deleted_at IS NOT NULL", "UNIX_TIMESTAMP(deleted_at) <= %s"):
            self.assertIn(guard, sql)                                                          # only a deleted, expired account
        deletes = [(s, p) for s, p in conn.log if s.startswith("DELETE")]
        self.assertEqual([s.split()[2] for s, _ in deletes], ["portfolio", "transactions", "price_alerts", "watchlist", "users"])
        self.assertTrue(all(p == (5,) for _, p in deletes))
        self.assertTrue(all("user_id = %s" in s for s, _ in deletes[:4])); self.assertIn("WHERE id = %s", deletes[4][0])
        self.assertEqual((conn.commits, conn.rollbacks), (1, 0))
        first_delete = conn.log.index(deletes[0])
        self.assertLess(conn.log.index(select[0]), first_delete)                                # checked before anything is removed

    def test_an_account_restored_a_moment_ago_is_left_alone(self):
        conn = FakeConn(responder(found=False))                                                  # no longer a deleted account (or not yet expired)
        self.assertFalse(account_data.purge_one(conn, 5, CUTOFF))
        self.assertEqual([s for s, _ in conn.log if s.startswith("DELETE")], [])
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))

    def test_an_optional_table_that_does_not_exist_is_skipped(self):
        conn = FakeConn(responder(tables=()))
        account_data.purge_one(conn, 5, CUTOFF)
        self.assertEqual([s.split()[2] for s, _ in conn.log if s.startswith("DELETE")], ["portfolio", "transactions", "watchlist", "users"])

    def test_a_failure_part_way_undoes_it_all(self):
        conn = FakeConn(responder(), fail_on="DELETE FROM watchlist")
        with self.assertRaises(RuntimeError):
            account_data.purge_one(conn, 5, CUTOFF)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))


class RemovingEveryExpiredAccount(unittest.TestCase):
    def test_it_asks_for_deleted_accounts_up_to_the_cutoff(self):
        conn = FakeConn(responder(rows=[{"id": 3}, {"id": 4}]))
        self.assertEqual(account_data.expired_ids(conn, CUTOFF), [3, 4])
        (sql, params), = conn.statements("SELECT id FROM users WHERE status = 'deleted'")
        self.assertEqual(params, (CUTOFF,)); self.assertIn("UNIX_TIMESTAMP(deleted_at) <= %s", sql)

    def test_it_counts_only_the_accounts_it_really_removed(self):
        seen = []

        def respond(sql, params):
            if "FOR UPDATE" in sql:
                seen.append(params[0])
                return [{"id": params[0]}] if params[0] != 4 else []                             # account 4 was restored in the meantime
            if "information_schema.tables" in sql:
                return [{"n": 0}]
            if sql.startswith("DELETE"):
                return 1
            return [{"id": 3}, {"id": 4}, {"id": 8}]
        conn = FakeConn(respond)
        self.assertEqual(account_data.purge_expired(conn, CUTOFF), [3, 8])
        self.assertEqual(seen, [3, 4, 8])
        removed = [p[0] for s, p in conn.log if s.startswith("DELETE FROM users")]
        self.assertEqual(removed, [3, 8])

    def test_with_nothing_to_remove_nothing_is_touched(self):
        conn = FakeConn(responder(rows=[]))
        self.assertEqual(account_data.purge_expired(conn, CUTOFF), [])
        self.assertEqual([s for s, _ in conn.log if s.startswith(("DELETE", "UPDATE"))], [])


class TheStoresQueries(unittest.TestCase):
    def store(self, conn, schema_ready=True):
        @contextmanager
        def borrow():                                   # the store takes its connections from dbpool.py
            yield conn
        patcher = mock.patch("dbpool.borrow", borrow)
        patcher.start(); self.addCleanup(patcher.stop)
        store = auth.MySQLStore()
        store._schema_ok = schema_ready                 # True: skip the one-time "add any missing columns" step, so the test sees only its own queries
        return store

    def test_deleting_marks_an_approved_account_with_the_time_and_says_whether_it_changed(self):
        conn = FakeConn(responder(update_rows=1)); store = self.store(conn)
        self.assertTrue(store.mark_deleted(5, 1_800_000_000))
        (sql, params), = conn.statements("UPDATE users SET status = 'deleted'")
        self.assertEqual(params, (1_800_000_000, 5)); self.assertIn("FROM_UNIXTIME(%s)", sql); self.assertIn("AND status = 'approved'", sql)
        self.assertEqual(conn.commits, 1)
        self.assertFalse(self.store(FakeConn(responder(update_rows=0))).mark_deleted(5, 1))      # it was not approved: nothing changed

    def test_restoring_only_touches_a_deleted_account_and_clears_the_date(self):
        conn = FakeConn(responder(update_rows=1)); store = self.store(conn)
        self.assertTrue(store.restore(5))
        (sql, params), = conn.statements("UPDATE users SET status = 'approved'")
        self.assertEqual(params, (5,)); self.assertIn("deleted_at = NULL", sql); self.assertIn("AND status = 'deleted'", sql)
        self.assertFalse(self.store(FakeConn(responder(update_rows=0))).restore(5))

    def test_other_administrators_are_counted_without_the_one_asking(self):
        conn = FakeConn(responder(rows=[{"n": 2}])); store = self.store(conn)
        self.assertEqual(store.other_admins(5), 2)
        (sql, params), = conn.statements("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        self.assertEqual(params, (5,)); self.assertIn("status = 'approved'", sql); self.assertIn("id <> %s", sql)

    def test_accounts_come_back_with_their_deletion_time_as_whole_seconds(self):
        row = {"id": 5, "email": "a@b.co", "status": "deleted", "is_admin": 0, "tier": 2, "created_at": Decimal("1700000000"), "deleted_at": Decimal("1800000000"),
               "last_login_at": None, "reviewed_at": None}
        for call in (lambda s: s.get(5), lambda s: s.list_users()[0]):
            conn = FakeConn(responder(rows=[dict(row)])); store = self.store(conn)
            user = call(store)
            self.assertEqual((user["deleted_at"], user["created_at"], user["status"]), (1_800_000_000, 1_700_000_000, "deleted"))
            self.assertIsInstance(user["deleted_at"], int)
            self.assertIn("UNIX_TIMESTAMP(deleted_at) AS deleted_at", conn.log[0][0])
        conn = FakeConn(responder(rows=[{**row, "deleted_at": None, "password_hash": "x"}]))
        found = self.store(conn).find_by_email("a@b.co")
        self.assertIsNone(found["deleted_at"]); self.assertIn("UNIX_TIMESTAMP(deleted_at) AS deleted_at", conn.log[0][0])

    def test_removing_for_good_goes_through_the_guarded_statement(self):
        conn = FakeConn(responder()); store = self.store(conn)
        self.assertTrue(store.purge_if_expired(5, CUTOFF))
        self.assertEqual(conn.statements("FOR UPDATE")[0][1], (5, CUTOFF))
        conn = FakeConn(responder(rows=[{"id": 9}])); store = self.store(conn)
        self.assertEqual(store.purge_expired(CUTOFF), [9])

    def test_a_new_account_is_created_on_the_free_plan(self):
        conn = FakeConn(responder(update_rows=1)); store = self.store(conn)
        store.create("new@example.com", "hash", "approved", False)
        (sql, params), = conn.statements("INSERT INTO users")
        self.assertIn("tier", sql); self.assertEqual(params, ("new@example.com", "hash", "approved", 0, 1))          # the last value is the plan: 1 = Free

    def test_a_fresh_database_gets_the_plan_and_deletion_columns_added_the_first_time_they_are_needed(self):
        def respond(sql, params):
            if "information_schema.columns" in sql:
                return [{"n": 0}]                        # no extra column exists yet
            return 1 if sql.startswith(("ALTER", "CREATE")) else [{"id": 5, "email": "a@b.co", "password_hash": "x", "status": "approved", "is_admin": 0, "deleted_at": None}]
        conn = FakeConn(respond); store = self.store(conn, schema_ready=False)
        self.assertEqual(store.find_by_email("a@b.co")["id"], 5)
        alters = [s for s, _ in conn.log if s.startswith("ALTER TABLE users ADD COLUMN")]
        self.assertIn("ALTER TABLE users ADD COLUMN tier TINYINT NOT NULL DEFAULT 2", alters)
        self.assertIn("ALTER TABLE users ADD COLUMN deleted_at TIMESTAMP NULL DEFAULT NULL", alters)
        self.assertEqual([i for i, (s, _) in enumerate(conn.log) if s.startswith("ALTER TABLE users ADD COLUMN deleted_at")][0] < [i for i, (s, _) in enumerate(conn.log) if "FROM users WHERE email" in s][0], True)   # added before the sign-in query that reads it
        before = len(conn.log); store.find_by_email("a@b.co")
        self.assertEqual(len(conn.log) - before, 1)                                   # and only once: the next lookup is just the query

    def test_a_database_that_has_not_been_migrated_gives_the_set_up_message(self):
        def respond(sql, params):
            raise mysql.connector.errors.ProgrammingError(msg="Unknown column 'deleted_at'", errno=1054)
        with self.assertRaises(auth.AccountsNotSetUp):
            self.store(FakeConn(respond)).find_by_email("a@b.co")


if __name__ == "__main__":
    unittest.main()
