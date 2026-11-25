"""
"Your finances" (profile menu): monthly money, optional giving and goals. Each answer is only ever read or changed for
its own account, answers are checked before the database, the routes sit on a plan, the SQL works on the live
database's ANSI mode, and the answers go with the account (download and deletion). The page itself is checked for its
place in the profile menu, its five sections, and the Suggestions / Clear and Edit / Remove buttons.
"""
import datetime as dt
import io
import os
import re
import unittest
from unittest import mock

import _path  # noqa: F401
import account_data
import api
import exports
import my_finance as mf
import tiers
from _accounts import signed_in_client
from _fakedb import FakeConn

ME = 7
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LATER = (dt.date.today() + dt.timedelta(days=400)).isoformat()


def run(fn, *args, respond=None):
    conn = FakeConn(respond or (lambda sql, p: []))
    mf._ready[0] = False
    with mock.patch.object(mf, "get_db_connection", return_value=conn):
        return fn(*args), conn


class Scoping(unittest.TestCase):
    def test_every_statement_is_limited_to_my_account(self):
        found = lambda sql, p: [{"id": 3}] if sql.startswith("SELECT id FROM") else [{"n": 0}] if "COUNT" in sql else 1
        calls = (lambda: mf.get_all(ME), lambda: mf.save_monthly(ME, {"income": 100}), lambda: mf.clear_monthly(ME),
                 lambda: mf.save_giving(ME, True, "pct", 2), lambda: mf.clear_giving(ME), lambda: mf.list_goals(ME),
                 lambda: mf.add_goal(ME, "Car", 100, LATER, 0), lambda: mf.update_goal(ME, 3, "Car", 100, LATER, 0),
                 lambda: mf.delete_goal(ME, 3))
        for call in calls:
            _, conn = run(call, respond=found)
            for sql, params in conn.log:
                if sql.startswith("CREATE TABLE"):
                    continue
                self.assertIn("user_id", sql, sql)
                self.assertIn(ME, params, sql)

    def test_someone_elses_goal_is_not_found(self):
        with self.assertRaises(mf.FinanceError) as cm:
            run(mf.update_goal, ME, 99, "Car", 10, LATER, 0, respond=lambda sql, p: [])
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(mf.FinanceError):
            run(mf.delete_goal, ME, 99, respond=lambda sql, p: 0)

    def test_tables_are_set_up_once(self):
        conn = FakeConn(lambda sql, p: [])
        mf._ready[0] = False
        with mock.patch.object(mf, "get_db_connection", return_value=conn):
            mf.list_goals(ME); mf.list_goals(ME)
        self.assertEqual(sum(1 for s, _ in conn.log if s.startswith("CREATE TABLE")), 2)

    def test_sql_works_in_ansi_mode(self):
        import inspect
        src = inspect.getsource(mf)
        self.assertNotIn("||", src)
        for sql in mf.TABLES:
            self.assertNotIn('"', sql)


class Checks(unittest.TestCase):
    def test_monthly_money_needs_income_and_sensible_numbers(self):
        for answers in ({}, {"income": None}, {"income": -1}, {"income": "abc"}, {"income": 10, "food": float("nan")}, {"income": 1e12}):
            with self.assertRaises(mf.FinanceError):
                run(mf.save_monthly, ME, answers)
        d, _ = run(mf.save_monthly, ME, {"income": "5000", "housing": "", "food": 0})
        self.assertEqual((d["monthly"]["income"], d["monthly"]["housing"], d["monthly"]["food"]), (5000.0, None, 0.0))

    def test_giving(self):
        for args in ((True, "pct", 0), (True, "pct", 101), (True, "bogus", 5), (True, "fixed", None)):
            with self.assertRaises(mf.FinanceError):
                run(mf.save_giving, ME, *args)
        d, _ = run(mf.save_giving, ME, False, "pct", 50, "x")
        self.assertEqual((d["giving"]["on"], d["giving"]["mode"], d["giving"]["value"]), (False, None, None))   # off keeps no amount

    def test_goals(self):
        for args in (("", 10, LATER, 0), ("Car", 0, LATER, 0), ("Car", 10, "2020-01-01", 0), ("Car", 10, "soon", 0),
                     ("Car", 10, "2200-01-01", 0), ("Car", 10, LATER, -5)):
            with self.assertRaises(mf.FinanceError):
                mf._goal_check(*args)
        self.assertEqual(mf._goal_check("  my   car ", "25000", LATER, None)[:2], ("my car", 25000.0))

    def test_too_many_goals(self):
        with self.assertRaises(mf.FinanceError):
            run(mf.add_goal, ME, "Car", 10, LATER, 0, respond=lambda sql, p: [{"n": mf.MAX_GOALS}])


class Routes(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_plan(self):
        for m, p in (("GET", "/api/finance"), ("PUT", "/api/finance/monthly"), ("DELETE", "/api/finance/monthly"),
                     ("PUT", "/api/finance/giving"), ("DELETE", "/api/finance/giving"), ("POST", "/api/finance/goals"),
                     ("PUT", "/api/finance/goals/3"), ("DELETE", "/api/finance/goals/3")):
            self.assertEqual(tiers.feature_for(m, p), "portfolio_view", p)

    def test_answers_are_read_for_the_signed_in_account(self):
        seen = []
        mock.patch.object(api.my_finance, "get_all", side_effect=lambda uid: seen.append(uid) or {"monthly": {}, "goals": []}).start()
        d = signed_in_client(5).get("/api/finance").json()
        self.assertEqual((d["status"], seen), ("success", [5]))

    def test_errors_are_explained(self):
        r = signed_in_client(5).put("/api/finance/giving", json={"on": True, "mode": "pct", "value": 500}).json()
        self.assertEqual(r["status"], "error")
        self.assertIn("100%", r["message"])

    def test_a_stranger_gets_nothing(self):
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(api.app).get("/api/finance").status_code, 401)


class GoesWithTheAccount(unittest.TestCase):
    def test_deleted_with_the_account(self):
        self.assertIn("user_finance", account_data.OTHER_PERSONAL_TABLES)
        self.assertIn("user_goals", account_data.OTHER_PERSONAL_TABLES)

    def test_in_the_download(self):
        import openpyxl
        finance = {"monthly": {"income": 5000.0, "housing": 1600.0}, "giving": {"on": True, "mode": "pct", "value": 1.5, "causes": "Education"},
                   "goals": [{"name": "Car", "target": 25000.0, "target_date": "2028-06-30", "saved": 6000.0}]}
        wb = openpyxl.load_workbook(io.BytesIO(exports.my_data_workbook("a@b.c", [], {}, [], {}, finance=finance)))
        values = [c.value for row in wb["Your finances"].iter_rows() for c in row if c.value is not None]
        for v in ("Comes in each month", 5000, "1.5% of income", "Education", "Car", 25000):
            self.assertIn(v, values)


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def test_in_the_profile_menu_after_profile_and_plan(self):
        self.assertIn("<span>Your finances</span>", self.html)
        self.assertIn("menu.appendChild(prof);menu.appendChild(fin);menu.appendChild(out);", self.html)
        self.assertIn("if(n==='finance')loadFinance();", self.html)

    def test_five_sections(self):
        tabs = re.findall(r'class="fin-tab[^"]*"[^>]*data-t="(\w+)"[^>]*>([^<]+)<', self.html)
        self.assertEqual(tabs, [("monthly", "Monthly money"), ("invest", "Investments"), ("goals", "Goals"), ("give", "Donations"), ("calc", "Calculators")])
        for t, _ in tabs:
            self.assertIn(f'id="fin-p-{t}"', self.html)

    def test_suggestions_only_when_asked_and_clear_closes_them(self):
        for sec in ("monthly", "invest", "goals", "give"):
            self.assertIn(f'data-s="{sec}" onclick="finSuggest(\'{sec}\')">Suggestions</button>', self.html)
            self.assertIn(f'<div class="fin-tips" id="fin-tips-{sec}" hidden></div>', self.html)
        self.assertIn("onclick=\"finClearTips('${sec}')\">Clear</button>", self.html)

    def test_the_calculators(self):
        block = re.search(r"const FIN_CALCS=\{(.*?)\n\};", self.html, re.S).group(1)
        self.assertEqual(re.findall(r"^  (\w+):\{name:'([^']+)'", block, re.M),
                         [("loan", "Loan"), ("stock", "Stock return"), ("grow", "Savings growth"), ("sip", "Monthly investing"), ("goal", "Saving for a goal")])


if __name__ == "__main__":
    unittest.main()
