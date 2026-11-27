"""
"Your personal finance" (profile menu): monthly money, optional giving, goals and the wishlist. Each answer is only ever read or changed for
its own account, answers are checked before the database, the routes sit on a plan, the SQL works on the live
database's ANSI mode, and the answers go with the account (download and deletion). The page itself is checked for its
place in the profile menu, its six sections, and the Analysis / Clear and Edit / Remove buttons. A wishlist stock gets
"Looks good" / "Wait for now" / "Be careful" from the data, with the model's part only on plans with prediction.
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
        self.assertEqual(sum(1 for s, _ in conn.log if s.startswith("CREATE TABLE")), len(mf.TABLES))

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
            self.assertEqual(tiers.feature_for(m, p), "personal_finance", p)

    def test_only_deluxe_and_up_get_in_and_the_others_are_told_which_plan_has_it(self):
        mock.patch.object(api.my_finance, "get_all", return_value={"monthly": {}, "goals": []}).start()
        mock.patch.object(api.my_finance, "save_giving", side_effect=AssertionError("a plan without it wrote something")).start()
        mock.patch.dict(tiers.TIERS, {n: t for n, t in tiers.TIERS.items() if n <= 6}, clear=True).start()          # the real plans only
        for level in (1, 2, 3, 4):
            c = signed_in_client(10 + level, tier=level)
            r = c.get("/api/finance")
            self.assertEqual(r.status_code, 403, level)
            self.assertEqual((r.json()["code"], r.json()["required_name"]), ("upgrade_required", "Deluxe"), level)
            self.assertEqual(c.put("/api/finance/giving", json={"on": False}).status_code, 403, level)
            self.assertEqual(c.get("/api/finance/wishlist-stocks").status_code, 403, level)
        for level in (5, 6):
            self.assertEqual(signed_in_client(10 + level, tier=level).get("/api/finance").status_code, 200, level)
        self.assertEqual(signed_in_client(30, is_admin=True, tier=1).get("/api/finance").status_code, 200)          # administrators have everything

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
        values = [c.value for row in wb["Your personal finance"].iter_rows() for c in row if c.value is not None]
        for v in ("Comes in each month", 5000, "1.5% of income", "Education", "Car", 25000):
            self.assertIn(v, values)


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def test_in_the_profile_menu_after_profile_and_plan(self):
        self.assertIn("<span>Your personal finance</span>", self.html)
        self.assertIn("menu.appendChild(prof);menu.appendChild(fin);menu.appendChild(out);", self.html)
        self.assertIn("if(n==='finance')loadFinance();", self.html)

    def test_six_sections(self):
        tabs = re.findall(r'class="fin-tab[^"]*"[^>]*data-t="(\w+)"[^>]*>([^<]+)<', self.html)
        self.assertEqual(tabs, [("monthly", "Monthly money"), ("invest", "Investments"), ("goals", "Goals"), ("wish", "Wishlist"),
                                ("give", "Donations"), ("calc", "Calculators")])
        for t, _ in tabs:
            self.assertIn(f'id="fin-p-{t}"', self.html)

    def test_analysis_only_when_asked_and_clear_closes_it(self):
        for sec in ("monthly", "invest", "goals", "wstock", "witem", "give"):
            self.assertIn(f'data-s="{sec}" onclick="finSuggest(\'{sec}\')">Analysis</button>', self.html)
            self.assertIn(f'<div class="fin-tips" id="fin-tips-{sec}" hidden></div>', self.html)
        self.assertIn("onclick=\"finClearTips('${sec}')\">Clear</button>", self.html)

    def test_the_calculators(self):
        block = re.search(r"const FIN_CALCS=\{(.*?)\n\};", self.html, re.S).group(1)
        self.assertEqual(re.findall(r"^  (\w+):\{name:'([^']+)'", block, re.M),
                         [("loan", "Loan"), ("stock", "Stock return"), ("grow", "Savings growth"), ("sip", "Monthly investing"), ("goal", "Saving for a goal")])



class Wishlist(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_only_my_rows(self):
        found = lambda sql, p: [] if "AND kind = 'stock'" in sql else [{"id": 3}] if sql.startswith("SELECT id FROM") \
            else [{"ticker": "AAPL"}] if "stocks_live" in sql \
            else [{"n": 0}] if "COUNT" in sql else [] if sql.startswith("SELECT") else 1
        for call in (lambda: mf.list_wishlist(ME), lambda: mf.add_wish(ME, "stock", "AAPL"), lambda: mf.add_wish(ME, "item", "Car", "car", 100),
                     lambda: mf.update_wish(ME, 3, "item", "Car", "car", 100), lambda: mf.delete_wish(ME, 3)):
            _, conn = run(call, respond=found)
            for sql, params in conn.log:
                if sql.startswith("CREATE TABLE") or "stocks_live" in sql:
                    continue
                self.assertIn("user_id", sql, sql)
                self.assertIn(ME, params, sql)

    def test_checks(self):
        with self.assertRaises(mf.FinanceError):
            run(mf.add_wish, ME, "stock", "ZZZZ", respond=lambda sql, p: [])          # not a stock we have
        for args in (("stock", "not a ticker!"), ("item", ""), ("item", "Car", "car", 0), ("item", "Car", "car", 10, "2020-01-01"),
                     ("item", "Car", "car", 10, None, True, 0), ("item", "Car", "car", 10, None, True, 41), ("other", "x")):
            with self.assertRaises(mf.FinanceError, msg=str(args)):
                mf._wish_check(*(list(args) + [None] * (7 - len(args))))
        self.assertEqual(mf._wish_check("item", " my  car ", "spaceship", "25000", None, False, 5),
                         ("item", "my car", "other", 25000.0, None, 0, None))          # unknown kind -> other; no loan -> no length

    def test_routes_and_plan(self):
        for m, p in (("POST", "/api/finance/wishlist"), ("PUT", "/api/finance/wishlist/3"), ("DELETE", "/api/finance/wishlist/3"),
                     ("GET", "/api/finance/wishlist-stocks")):
            self.assertEqual(tiers.feature_for(m, p), "personal_finance", p)
        seen = []
        mock.patch.object(api.portfolio, "list_holdings", return_value=[]).start()
        mock.patch.object(api.my_finance, "wishlist_stocks", side_effect=lambda uid, pred, h: seen.append((uid, pred)) or {"stocks": []}).start()
        signed_in_client(5).get("/api/finance/wishlist-stocks")
        self.assertEqual(seen, [(5, True)])                         # the model's part goes with stock prediction, which every plan with this has


TODAY = dt.date(2026, 11, 26)
GOOD = {"ticker": "AAPL", "sector": "Technology", "current_price": 200, "week_52_high": 260, "week_52_low": 160, "ma_50": 195, "ma_200": 180,
        "rsi_14": 55, "pe_ratio": 30, "recommendation": "buy", "upside_pct": 12, "num_analysts": 40,
        "buy_signal": "Strong Buy", "prob_up_3m": 64, "target_3m": 215}


class StockRead(unittest.TestCase):
    def read(self, **change):
        return mf.stock_read(dict(GOOD, **change), sector_pe=40, predictions=True, today=TODAY)

    def test_looks_good(self):
        r = self.read()
        self.assertEqual((r["verdict"], r["label"]), ("good", "Looks good"))
        self.assertTrue(any("BUY" in p[1] for p in r["points"]))

    def test_results_soon_means_wait(self):
        r = self.read(next_earnings_date=TODAY + dt.timedelta(days=5))
        self.assertEqual((r["verdict"], r["label"]), ("wait", "Looks good, but wait"))
        self.assertTrue(any(p[1] == "Results in 5 days." for p in r["points"]))

    def test_near_the_high_and_overbought(self):
        r = self.read(current_price=258, rsi_14=78, buy_signal="Hold", prob_up_3m=50, recommendation="hold", upside_pct=2)
        self.assertEqual(r["label"], "Wait for now")

    def test_be_careful(self):
        r = self.read(current_price=165, ma_200=190, pe_ratio=-4, recommendation="sell", upside_pct=-8, buy_signal="Sell", prob_up_3m=30)
        self.assertEqual((r["verdict"], r["label"]), ("careful", "Be careful"))
        self.assertTrue(any(p[1] == "Not making a profit." for p in r["points"]))

    def test_no_model_without_prediction(self):
        r = mf.stock_read(dict(GOOD), sector_pe=40, predictions=False, today=TODAY)
        self.assertFalse(any("model" in p[1].lower() for p in r["points"]))

    def test_missing_data_is_left_out(self):
        r = mf.stock_read({"ticker": "NEW", "current_price": 10}, predictions=True, today=TODAY)
        self.assertEqual((r["points"], r["label"]), ([], "Wait for now"))

    def test_budget_and_sector(self):
        r = mf.stock_read(dict(GOOD), sector_pe=40, today=TODAY, sector_share=80, budget=1000)
        texts = " ".join(p[1] + p[2] for p in r["points"])
        self.assertIn("80% in Technology", texts)
        self.assertIn("buys 5 shares", texts)


class Calculators(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def test_stock_return_has_years_roi_and_return_per_year(self):
        block = re.search(r"  stock:\{name:'Stock return',fields:\[(.*?)\]\],", self.html, re.S).group(1)
        self.assertIn("'yrs','Years held'", block)
        self.assertIn("ROI (total return)", self.html)
        self.assertIn("Return per year (average)", self.html)

    def test_compounded_note_and_growth_explained(self):
        self.assertIn("This is the compounded amount", self.html)
        self.assertNotIn("0 for a bank account", self.html)
        self.assertIn("How much your saved money earns each year while you save", self.html)

    def test_loan_types_for_each_kind(self):
        block = re.search(r"const FIN_LOANS=\{(.*?)\n\};", self.html, re.S).group(1)
        self.assertEqual(re.findall(r"^  (\w+):", block, re.M), list(mf.ITEM_KINDS))


if __name__ == "__main__":
    unittest.main()
