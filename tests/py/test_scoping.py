"""
One person must never be able to read or change another person's holdings, transactions or watchlist.

The data layer is run against a recording fake database and every statement on a personal table is checked: it must
filter by the signed-in user's id, and a record that belongs to someone else must look like it doesn't exist.
"""
import ast
import inspect
import os
import re
import unittest
from unittest import mock

import _path
import portfolio
import watchlist
from _fakedb import FakeConn

ME, OTHER = 42, 7
PERSONAL = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+(portfolio|transactions|watchlist)\b", re.I)


def stock_rows(sql, params):
    """Market-data lookups every personal operation makes; they are shared and carry no owner."""
    if "FROM stocks_live" in sql and "company_name" in sql:
        return [{"company_name": "Apple Inc.", "sector": "Technology"}]
    return None


def run(fn, *args, respond=None, **kwargs):
    """Call `fn` with a fake connection. Returns (result, connection) so the SQL it sent can be inspected."""
    def answer(sql, params):
        return stock_rows(sql, params) or (respond(sql, params) if respond else []) or []
    conn = FakeConn(answer)
    with mock.patch.object(portfolio, "get_db_connection", return_value=conn), mock.patch.object(watchlist, "get_db_connection", return_value=conn):
        return fn(*args, **kwargs), conn


def assert_scoped(testcase, conn, user_id=ME):
    """Every statement that touches a personal table filters or writes by the user's id."""
    touched = [(sql, p) for sql, p in conn.log if PERSONAL.search(sql)]
    testcase.assertTrue(touched, "the operation never touched a personal table")
    for sql, params in touched:
        testcase.assertIn("user_id", sql, f"unscoped statement: {sql}")
        testcase.assertIn(user_id, params, f"{sql} was not given the user id {user_id}: {params}")


class Holdings(unittest.TestCase):
    def test_listing_only_asks_for_my_rows(self):
        _, conn = run(portfolio.list_holdings, ME)
        sql, params = conn.log[0]
        self.assertIn("WHERE p.user_id = %s", sql); self.assertEqual(params, (ME,))

    def test_adding_a_holding_records_a_buy_against_me_and_rebuilds_my_holding(self):
        # Holdings come from transactions: "add a holding" is a BUY, then MY holding is rebuilt from MY transactions.
        bought = lambda sql, p: ([{"id": 1, "transaction_type": "BUY", "quantity": 5, "price": 100.0, "transaction_date": "2025-01-02", "notes": ""}]
                                 if "FROM transactions" in sql else None)
        _, conn = run(portfolio.add_holding, ME, "aapl", 5, 100.0, "2025-01-02", respond=bought)
        assert_scoped(self, conn)
        self.assertEqual(conn.statements("INSERT INTO transactions")[0][1][:2], (ME, "AAPL"))
        self.assertEqual(conn.statements("INSERT INTO portfolio")[0][1][:2], (ME, "AAPL"))
        self.assertTrue(all(p == (ME, "AAPL") for _, p in conn.statements("SELECT id FROM portfolio")))

    def test_rebuilding_merges_my_lots_into_one_position(self):
        rows = lambda sql, p: ([{"id": 1, "transaction_type": "BUY", "quantity": 2, "price": 50.0, "transaction_date": "2025-01-02", "notes": ""},
                                {"id": 2, "transaction_type": "BUY", "quantity": 3, "price": 60.0, "transaction_date": "2025-02-02", "notes": ""}]
                               if "FROM transactions" in sql else [{"id": 11}, {"id": 12}] if "SELECT id FROM portfolio" in sql else None)
        pos, conn = run(portfolio.sync_holding, ME, "AAPL", respond=rows)
        assert_scoped(self, conn)
        self.assertEqual(pos["quantity"], 5); self.assertAlmostEqual(pos["avg_cost"], 56.0)
        self.assertEqual(conn.statements("UPDATE portfolio")[0][1][-2:], (11, ME))
        self.assertEqual(conn.statements("DELETE FROM portfolio")[0][1], (12, ME))

    def test_holdings_cannot_be_edited_or_deleted_directly(self):
        for call in (lambda: portfolio.update_holding(ME, 9, 5, 100.0, "2025-01-02"), lambda: portfolio.delete_holding(ME, 9)):
            conn = FakeConn(lambda sql, p: [])
            with mock.patch.object(portfolio, "get_db_connection", return_value=conn), self.assertRaises(portfolio.PortfolioError) as cm:
                call()
            self.assertEqual(cm.exception.status, 409)
            self.assertEqual(conn.log, [])                          # nothing was read or changed


class Transactions(unittest.TestCase):
    def test_listing_and_summary_only_read_my_rows(self):
        for fn in (portfolio.list_transactions, portfolio.transaction_summary):
            _, conn = run(fn, ME)
            assert_scoped(self, conn); self.assertEqual(conn.log[0][1], (ME,))

    def test_recording_a_buy(self):
        _, conn = run(portfolio.add_transaction, ME, "AAPL", "BUY", 5, 100.0, "2025-01-02")
        assert_scoped(self, conn); self.assertEqual(conn.statements("INSERT INTO transactions")[0][1][0], ME)

    def test_a_sale_is_checked_against_my_records_not_everyones(self):
        mine = lambda sql, p: [{"id": 1, "transaction_type": "BUY", "quantity": 10, "price": 90.0, "transaction_date": "2024-01-01", "notes": ""}] if "FROM transactions" in sql else None
        _, conn = run(portfolio.add_transaction, ME, "AAPL", "SELL", 4, 100.0, "2025-01-02", respond=mine)
        assert_scoped(self, conn)
        self.assertTrue(all(p[:2] == (ME, "AAPL") for _, p in conn.statements("FROM transactions WHERE")))

    def test_a_sale_always_updates_my_holding(self):
        sale = lambda sql, p: ([{"id": 1, "transaction_type": "BUY", "quantity": 10, "price": 90.0, "transaction_date": "2024-01-01", "notes": ""},
                                {"id": 2, "transaction_type": "SELL", "quantity": 4, "price": 100.0, "transaction_date": "2025-01-02", "notes": ""}]
                               if "FROM transactions" in sql else [{"id": 5}] if "SELECT id FROM portfolio" in sql else None)
        _, conn = run(portfolio.add_transaction, ME, "AAPL", "SELL", 4, 100.0, "2025-01-02", respond=sale)
        assert_scoped(self, conn)
        self.assertEqual(conn.statements("UPDATE portfolio SET quantity")[0][1][0], 6.0)
        self.assertEqual(conn.statements("UPDATE portfolio SET quantity")[0][1][-2:], (5, ME))

    def test_selling_more_than_my_transactions_show_is_refused(self):
        mine = lambda sql, p: [{"id": 1, "transaction_type": "BUY", "quantity": 3, "price": 90.0, "transaction_date": "2024-01-01", "notes": ""}] if "FROM transactions" in sql else None
        conn = FakeConn(lambda sql, p: stock_rows(sql, p) or mine(sql, p) or [])
        with mock.patch.object(portfolio, "get_db_connection", return_value=conn), self.assertRaises(portfolio.PortfolioError):
            portfolio.add_transaction(ME, "AAPL", "SELL", 4, 100.0, "2025-01-02")
        self.assertEqual(conn.statements("INSERT INTO transactions"), [])

    def test_editing_and_deleting_my_transaction(self):
        found = lambda sql, p: [{"ticker": "AAPL", "transaction_type": "BUY"}] if sql.startswith("SELECT ticker") else None
        _, conn = run(portfolio.update_transaction, ME, 3, 5, 100.0, "2025-01-02", respond=found)
        assert_scoped(self, conn); self.assertEqual(conn.statements("UPDATE transactions")[0][1][-2:], (3, ME))
        _, conn = run(portfolio.delete_transaction, ME, 3, respond=found)
        assert_scoped(self, conn); self.assertEqual(conn.statements("DELETE FROM transactions")[0][1], (3, ME))

    def test_someone_elses_transaction_id_looks_like_it_does_not_exist(self):
        for call in (lambda: portfolio.update_transaction(ME, 3, 5, 100.0, "2025-01-02"), lambda: portfolio.delete_transaction(ME, 3)):
            conn = FakeConn()
            with mock.patch.object(portfolio, "get_db_connection", return_value=conn), self.assertRaises(portfolio.PortfolioError) as cm:
                call()
            self.assertEqual(cm.exception.status, 404)
            self.assertEqual(conn.statements("UPDATE transactions") + conn.statements("DELETE FROM transactions"), [])


class ReviewAndPerformance(unittest.TestCase):
    def test_they_work_from_my_holdings(self):
        asked = []
        with mock.patch.object(portfolio, "list_holdings", side_effect=lambda uid: asked.append(uid) or []):
            self.assertEqual(portfolio.performance(ME), {"empty": True})
            with self.assertRaises(portfolio.PortfolioError):
                portfolio.review(ME, use_holdings=True)
        self.assertEqual(asked, [ME, ME])


class EveryStatementInTheModuleIsScoped(unittest.TestCase):
    """A guard for future edits: a new query on a personal table that forgets the user id fails here."""

    def test_no_statement_on_a_personal_table_lacks_a_user_filter(self):
        for filename, at_least in (("portfolio.py", 15), ("watchlist.py", 3)):
            with open(os.path.join(_path.ROOT, filename), encoding="utf-8") as source:
                tree = ast.parse(source.read())
            checked = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("execute", "executemany", "_query") and node.args:
                    sql = ast.unparse(node.args[0])
                    if PERSONAL.search(sql):
                        checked += 1
                        self.assertIn("user_id", sql, f"{filename} line {node.lineno}: {sql[:100]}")
            self.assertGreaterEqual(checked, at_least, f"{filename}: the scan found too few statements, so it may be missing some")

    def test_every_data_function_takes_the_user_first(self):
        for name in ("list_holdings", "add_holding", "update_holding", "delete_holding", "review", "list_transactions", "transaction_summary",
                     "add_transaction", "update_transaction", "delete_transaction", "performance", "sync_holding", "held_tickers", "_ticker_txs", "_remove_tx"):
            self.assertEqual(next(iter(inspect.signature(getattr(portfolio, name)).parameters)), "user_id", name)
        for name in ("get_watchlist", "save_watchlist"):
            self.assertEqual(next(iter(inspect.signature(getattr(watchlist, name)).parameters)), "user_id", name)


class Watchlist(unittest.TestCase):
    def test_clean_uppercases_dedupes_and_keeps_order(self):
        self.assertEqual(watchlist.clean(["msft", " aapl ", "MSFT", "brk-b"]), ["MSFT", "AAPL", "BRK-B"])
        self.assertEqual(watchlist.clean([]), [])

    def test_clean_refuses_anything_that_is_not_a_ticker(self):
        for bad in ("AAPL", [["AAPL"]], ["bad ticker"], ["x" * 11], [""], [5], ["A;DROP"], [None]):
            with self.assertRaises(watchlist.WatchlistError, msg=repr(bad)):
                watchlist.clean(bad)
        with self.assertRaises(watchlist.WatchlistError):
            watchlist.clean([f"T{i}" for i in range(watchlist.MAX_WATCHLIST + 1)])
        self.assertEqual(len(watchlist.clean([f"T{i}" for i in range(watchlist.MAX_WATCHLIST)])), watchlist.MAX_WATCHLIST)

    def test_saving_replaces_only_my_list_in_one_transaction(self):
        saved, conn = run(watchlist.save_watchlist, ME, ["aapl", "msft"])
        self.assertEqual(saved, ["AAPL", "MSFT"])
        assert_scoped(self, conn)
        self.assertEqual(conn.statements("DELETE FROM watchlist")[0][1], (ME,))
        self.assertEqual([p for _, p in conn.statements("INSERT INTO watchlist")], [(ME, "AAPL", 0), (ME, "MSFT", 1)])
        self.assertEqual((conn.commits, conn.rollbacks, conn.closed), (1, 0, True))

    def test_saving_an_empty_list_just_clears_mine(self):
        _, conn = run(watchlist.save_watchlist, ME, [])
        self.assertEqual(len(conn.log), 1); assert_scoped(self, conn)

    def test_a_failed_save_is_rolled_back_and_the_connection_closed(self):
        conn = FakeConn(fail_on="INSERT INTO watchlist")
        with mock.patch.object(watchlist, "get_db_connection", return_value=conn), self.assertRaises(RuntimeError):
            watchlist.save_watchlist(ME, ["AAPL"])
        self.assertEqual((conn.commits, conn.rollbacks, conn.closed), (0, 1, True))

    def test_reading_returns_my_tickers_in_my_order(self):
        result, conn = run(watchlist.get_watchlist, ME, respond=lambda sql, p: [{"t": "MSFT"}, {"t": "AAPL"}])
        assert_scoped(self, conn); self.assertIn("ORDER BY sort_order", conn.log[0][0])
        self.assertEqual(result, ["MSFT", "AAPL"])


if __name__ == "__main__":
    unittest.main()
