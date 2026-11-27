"""
"Where your money is": the savings, deposits, property... that people add themselves. Each row is only ever read or
changed for its own account, amounts are checked, the routes sit on a plan, and the data goes with the account
(download and deletion).
"""
import io
import re
import unittest
from unittest import mock

import _path  # noqa: F401
import account_data
import api
import exports
import my_money
import tiers
from _accounts import signed_in_client
from _fakedb import FakeConn

ME = 7


def run(fn, *args, respond=None):
    conn = FakeConn(respond or (lambda sql, p: []))
    my_money._ready[0] = False
    with mock.patch.object(my_money, "get_db_connection", return_value=conn):
        return fn(*args), conn


class Scoping(unittest.TestCase):
    def assert_mine(self, conn):
        for sql, params in conn.log:
            if sql.startswith("CREATE TABLE"):
                continue
            self.assertIn("user_id", sql, sql)
            self.assertIn(ME, params, sql)

    def test_list_add_edit_delete_only_touch_my_rows(self):
        found = lambda sql, p: [{"id": 3}] if sql.startswith("SELECT id FROM") else [{"n": 0}] if "COUNT" in sql else 1
        for call in (lambda: my_money.list_items(ME), lambda: my_money.add_item(ME, "fd", "HSBC", 5000),
                     lambda: my_money.update_item(ME, 3, "savings", "", 100), lambda: my_money.delete_item(ME, 3)):
            _, conn = run(call, respond=found)
            self.assert_mine(conn)

    def test_the_table_is_set_up_once(self):
        conn = FakeConn(lambda sql, p: [])
        my_money._ready[0] = False
        with mock.patch.object(my_money, "get_db_connection", return_value=conn):
            my_money.list_items(ME); my_money.list_items(ME)
        self.assertEqual(sum(1 for s, _ in conn.log if s.startswith("CREATE TABLE")), 1)

    def test_someone_elses_item_is_not_found(self):
        with self.assertRaises(my_money.MoneyError) as cm:
            run(my_money.update_item, ME, 99, "fd", "", 10, respond=lambda sql, p: [])
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(my_money.MoneyError):
            run(my_money.delete_item, ME, 99, respond=lambda sql, p: 0)


class Checks(unittest.TestCase):
    def test_bad_input_is_refused_before_the_database(self):
        for kind, amount in (("nonsense", 10), ("fd", 0), ("fd", -5), ("fd", "abc"), ("fd", float("nan")), ("fd", 1e13)):
            with self.assertRaises(my_money.MoneyError):
                my_money._check(kind, "", amount)

    def test_cleaning(self):
        self.assertEqual(my_money._check(" FD ", "  my   bank  ", "2500.456"), ("fd", "my bank", 2500.46))

    def test_too_many_items(self):
        with self.assertRaises(my_money.MoneyError):
            run(my_money.add_item, ME, "fd", "", 10, respond=lambda sql, p: [{"n": my_money.MAX_ITEMS}])


class Routes(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_plan(self):
        for m, p in (("GET", "/api/my-money"), ("POST", "/api/my-money"), ("PUT", "/api/my-money/3"), ("DELETE", "/api/my-money/3")):
            self.assertEqual(tiers.feature_for(m, p), "portfolio_view")

    def test_list_and_errors(self):
        mock.patch.object(api.my_money, "list_items", return_value=[{"id": 1, "kind": "fd", "amount": 10.0}]).start()
        d = signed_in_client(5).get("/api/my-money").json()
        self.assertEqual(d["items"][0]["kind"], "fd")
        self.assertIn("real_estate", d["kinds"])
        r = signed_in_client(5).post("/api/my-money", json={"kind": "fd", "name": "", "amount": -3}).json()
        self.assertEqual(r["status"], "error")


class GoesWithTheAccount(unittest.TestCase):
    def test_deleted_with_the_account(self):
        self.assertIn("user_assets", account_data.OTHER_PERSONAL_TABLES)

    def test_in_the_download(self):
        import openpyxl
        data = exports.my_data_workbook("a@b.c", [], {}, [], {}, other_money=[
            {"kind_label": "Fixed deposits", "name": "HSBC", "amount": 5000.0, "updated_at": "2026-11-24"}])
        wb = openpyxl.load_workbook(io.BytesIO(data))
        self.assertIn("Other money", wb.sheetnames)
        values = [c.value for row in wb["Other money"].iter_rows() for c in row if c.value is not None]
        self.assertIn("HSBC", values)
        self.assertIn(5000, values)


if __name__ == "__main__":
    unittest.main()
