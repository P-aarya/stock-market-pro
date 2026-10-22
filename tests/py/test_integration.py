"""
Aarya's work (alerts, notifications, benchmark, dividends, live news, economic calendar, her prediction pipeline) and
Abheek's work (accounts, per-person portfolio, IPOs, peers, sentiment, period picker, AI chat) live in one codebase.
These tests keep the two from undoing each other: nothing is lost, personal data stays private, and nothing secret is committed.
"""
import ast
import os
import re
import unittest
from datetime import datetime
from unittest import mock

import _path
import api
import portfolio
from _accounts import AccountsTestCase, signed_in_client
from _fakedb import FakeConn

ONLY_ABHEEK = """
DELETE /api/portfolio/{holding_id}
DELETE /api/transactions/{tx_id}
GET /admin
GET /api/admin/summary
GET /api/admin/users
GET /api/compare
GET /api/compare/prices
GET /api/compare/verdict
GET /api/ipos
GET /api/peers/{ticker}
GET /api/portfolio/performance
GET /api/sentiment/{ticker}
GET /api/stocks/{ticker}/period
GET /api/stocks/{ticker}/price-range
GET /api/watchlist
GET /register
POST /agent/explain-stock
POST /agent/portfolio-review
POST /agent/recommend
POST /agent/recommend/stream
POST /api/admin/users/{user_id}/status
POST /api/portfolio
POST /api/transactions
POST /register
PUT /api/portfolio/{holding_id}
PUT /api/transactions/{tx_id}
PUT /api/watchlist
""".split("\n")
ONLY_AARYA = """
DELETE /api/alerts/{alert_id}
GET /api/alerts
GET /api/alerts/check
GET /api/economic-calendar
GET /api/news/world
GET /api/notifications/feed
GET /api/portfolio/benchmark
GET /api/stocks/{ticker}/dividends
GET /api/stocks/{ticker}/news-sentiment
GET /api/stocks/{ticker}/price-spikes
GET /api/stocks/{ticker}/period-spikes
POST /api/alerts/create
POST /api/portfolio/add
POST /api/transactions/add
""".split("\n")
SHARED_ROUTES = 50      # routes both of you had (market data, stocks, news, login ...)


def source(name):
    with open(os.path.join(_path.ROOT, name), encoding="utf-8") as f:
        return f.read()


class NothingIsLost(unittest.TestCase):
    def routes(self):
        return [f"{m.group(1).upper()} {m.group(2)}" for m in re.finditer(r'^@app\.(get|post|put|delete)\("([^"]*)"', source("api.py"), re.M)]

    def test_every_endpoint_either_of_you_built_is_there_exactly_once(self):
        actual = self.routes()
        self.assertEqual(len(actual), len(set(actual)), f"registered twice (the second would never run): {sorted({r for r in actual if actual.count(r) > 1})}")
        want = {r for r in ONLY_ABHEEK + ONLY_AARYA if r}
        self.assertFalse(want - set(actual), f"missing: {sorted(want - set(actual))}")
        self.assertEqual(len(set(actual)), SHARED_ROUTES + len(want), "the number of endpoints changed: something was added or lost")

    def test_both_prediction_pipelines_are_kept_side_by_side(self):
        for name in ("predict.py", "predict_aarya.py", "sentiment_engine.py", "sentiment.py", "ipo_data.py", "peers.py", "portfolio.py", "stock_period.py", "news_feed.py", "db.py"):
            self.assertTrue(os.path.exists(os.path.join(_path.ROOT, name)), name)
        self.assertIn("FORECAST_SHRINK", source("predict.py"))                  # Abheek's honest evaluation
        self.assertIn("sentiment_engine", source("predict_aarya.py"))           # Aarya's live sentiment and 6M / 1Y horizons
        self.assertIn("target_6m", source("predict_aarya.py"))
        self.assertIn("predict_aarya.py", source("fix_predict_tables.py"))      # her helper points at her pipeline

    def test_her_ticker_universe_is_the_one_in_use(self):
        import tickers
        self.assertGreater(len(tickers.ALL_TICKERS), 2000)
        self.assertTrue(hasattr(tickers, "TICKER_SECTOR_MAP"))


class NothingSecretIsCommitted(unittest.TestCase):
    def python_files(self):
        for folder, dirs, files in os.walk(_path.ROOT):
            dirs[:] = [d for d in dirs if d not in ("venv", "node_modules", ".git", "__pycache__", "tests", "lintenv")]
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(folder, f)

    def test_no_password_or_key_is_written_into_the_code(self):
        found = []
        literal = re.compile(r"""(?ix)
            (?: \bpass(?:word)?\s*=\s*["'][^"']+["']                                   # password="hunter2"
              | getenv\(\s*["'][A-Z_]*(?:PASS|SECRET|TOKEN|KEY)[A-Z_]*["']\s*,\s*["'][^"']+["']\s*\)      # os.getenv("DB_PASS", "hunter2")
              | getenv\([^)]*(?:PASS|SECRET|TOKEN|KEY)[^)]*\)\s*or\s*["'][^"']{3,}["'] )                  # os.getenv("DB_PASS") or "hunter2"
        """)
        for path in self.python_files():
            for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                if literal.search(line) and "example" not in line.lower():
                    found.append(f"{os.path.relpath(path, _path.ROOT)}:{n}")
        self.assertEqual(found, [], "a credential seems to be written into the code (values are not shown): " + ", ".join(found))

    def test_every_database_connection_the_web_app_opens_goes_through_get_db(self):
        src = source("api.py")
        self.assertEqual(src.count("mysql.connector.connect("), 1)
        body = src[src.index("def get_db():"):src.index("def query(")]
        self.assertIn("mysql.connector.connect(", body); self.assertIn('config["use_pure"] = True', body)
        for helper in ("_pf_db", "_al_db"):
            self.assertRegex(src, rf"def {helper}\(\):\n\s+return get_db\(\)")


class PersonalDataStaysPrivate(AccountsTestCase, unittest.TestCase):
    """Aarya's alerts, benchmark and add-forms now work per account, with her URLs and reply shapes unchanged."""

    def setUp(self):
        super().setUp()
        self.alerts = [{"id": 1, "user_id": 5, "ticker": "AAPL", "condition_type": "above", "target_price": 200.0, "triggered": 0, "company_name": "Apple Inc."},
                       {"id": 2, "user_id": 6, "ticker": "TSLA", "condition_type": "below", "target_price": 100.0, "triggered": 0, "company_name": "Tesla Inc."}]
        self.conn = FakeConn(self.respond)
        patches = [mock.patch.object(api, "_al_setup", lambda: None), mock.patch.object(api, "_al_db", lambda: self.conn), mock.patch.object(api, "get_db", lambda: self.conn),
                   mock.patch.object(api, "_al_live_prices", lambda tickers: {t: (150.0, datetime.now(), t + " Inc.") for t in tickers}),
                   mock.patch.dict(api._AL_STATE, {"ready": True, "cols": {"user_id"}})]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def respond(self, sql, params):
        if sql.startswith("SELECT * FROM price_alerts") and "user_id = %s" in sql:
            return [dict(a) for a in self.alerts if a["user_id"] == params[0] and ("triggered = 0" not in sql or not a["triggered"])]
        if sql.startswith("DELETE FROM price_alerts"):
            return sum(1 for a in self.alerts if a["id"] == params[0] and a["user_id"] == params[1])
        if sql.startswith("UPDATE price_alerts"):
            return 1
        return []

    def test_each_person_sees_only_their_own_alerts(self):
        self.assertEqual([a["ticker"] for a in signed_in_client(5).get("/api/alerts").json()["alerts"]], ["AAPL"])
        self.assertEqual([a["ticker"] for a in signed_in_client(6).get("/api/alerts").json()["alerts"]], ["TSLA"])
        self.assertEqual(signed_in_client(7).get("/api/alerts").json()["alerts"], [])

    def test_a_new_alert_belongs_to_the_person_who_made_it(self):
        r = signed_in_client(5).post("/api/alerts/create", json={"ticker": "msft", "target_price": 500, "condition_type": "above"})
        self.assertTrue(r.json()["success"], r.text)
        (sql, params), = self.conn.statements("INSERT INTO price_alerts")
        columns = re.search(r"\((.*?)\) VALUES", sql).group(1).split(", ")
        self.assertEqual(params[columns.index("user_id")], 5)

    def test_you_cannot_delete_someone_elses_alert(self):
        r = signed_in_client(6).delete("/api/alerts/1").json()                       # alert 1 is user 5's
        self.assertEqual(r["deleted"], 0)
        self.assertEqual(self.conn.statements("DELETE FROM price_alerts")[0][1], (1, 6))
        self.assertEqual(signed_in_client(5).delete("/api/alerts/1").json()["deleted"], 1)

    def test_checking_alerts_only_looks_at_and_fires_your_own(self):
        r = signed_in_client(5).get("/api/alerts/check").json()
        selects = self.conn.statements("SELECT * FROM price_alerts")
        self.assertTrue(selects and all(p == (5,) for _, p in selects))
        self.alerts[0]["target_price"] = 100.0                                         # price 150 is now above the target: it fires
        fired = signed_in_client(5).get("/api/alerts/check").json()["triggered"]
        self.assertEqual([a["ticker"] for a in fired], ["AAPL"])
        for sql, params in self.conn.statements("UPDATE price_alerts"):
            self.assertIn("user_id=%s", sql); self.assertEqual(params[-1], 5)
        other = signed_in_client(6).get("/api/alerts/check").json()
        self.assertNotIn("AAPL", [a["ticker"] for a in other.get("triggered", [])])    # user 6 never sees user 5's alert

    def test_the_benchmark_only_reads_your_holdings(self):
        r = signed_in_client(5).get("/api/portfolio/benchmark")
        self.assertEqual(r.status_code, 200)
        (sql, params), = self.conn.statements("FROM portfolio")
        self.assertIn("WHERE user_id = %s", sql); self.assertEqual(params, (5,))

    def test_her_add_forms_save_through_the_per_account_code_and_keep_her_reply_shape(self):
        seen = []
        with mock.patch.object(portfolio, "add_holding", lambda *a, **k: (seen.append(a), {"message": "Added 5 shares of AAPL."})[1]):
            r = signed_in_client(5).post("/api/portfolio/add", params={"ticker": "AAPL", "quantity": 5, "avg_buy_price": 100, "buy_date": "2025-01-02"})
        self.assertEqual(r.json(), {"success": True, "message": "Added 5 shares of AAPL."}); self.assertEqual(seen[0][:2], (5, "AAPL"))
        with mock.patch.object(portfolio, "add_holding", side_effect=portfolio.PortfolioError("That doesn't look like a ticker symbol.")):
            bad = signed_in_client(5).post("/api/portfolio/add", params={"ticker": "??", "quantity": 5, "avg_buy_price": 100, "buy_date": "2025-01-02"}).json()
        self.assertEqual(bad, {"success": False, "message": "That doesn't look like a ticker symbol."})

    def test_her_transaction_form_too(self):
        seen = []
        params = {"ticker": "AAPL", "transaction_type": "buy", "quantity": 5, "price": 100, "transaction_date": "2025-01-02"}
        with mock.patch.object(portfolio, "add_transaction", lambda *a, **k: (seen.append(a), {"message": "Recorded BUY 5 AAPL."})[1]):
            ok = signed_in_client(5).post("/api/transactions/add", params=params).json()
            refused = signed_in_client(5).post("/api/transactions/add", params={**params, "transaction_type": "DIVIDEND"}).json()
        self.assertEqual(ok, {"success": True, "message": "Recorded BUY 5 AAPL."}); self.assertEqual(seen[0][:3], (5, "AAPL", "BUY"))
        self.assertEqual(refused, {"success": False, "message": "Type must be BUY or SELL"}); self.assertEqual(len(seen), 1)

    def test_a_stranger_reaches_none_of_these(self):
        from fastapi.testclient import TestClient
        c = TestClient(api.app)
        for method, path in (("get", "/api/alerts"), ("post", "/api/alerts/create"), ("delete", "/api/alerts/1"), ("get", "/api/alerts/check"), ("get", "/api/portfolio/benchmark"),
                             ("post", "/api/portfolio/add"), ("post", "/api/transactions/add"), ("get", "/api/notifications/feed")):
            self.assertEqual(getattr(c, method)(path).status_code, 401, path)

    def test_every_statement_on_a_personal_table_names_the_owner(self):
        personal = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)\s+(portfolio|transactions|price_alerts|watchlist)\b", re.I)
        tree = ast.parse(source("api.py"))
        checked, problems = 0, []
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if fn.name == "_al_setup":                                           # creates the table and adds its columns: not a person's data
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("execute", "executemany", "_pf_rows", "query") and node.args:
                    sql = ast.unparse(node.args[0])
                    if personal.search(sql):
                        checked += 1
                        # an INSERT whose column list is built in code names user_id in that list, not in the SQL text
                        named = "user_id" in sql or ("join(" in sql and re.search(r"""['"]user_id['"]""", ast.unparse(fn)))
                        if not named:
                            problems.append(f"api.py line {node.lineno}: {sql[:70]}")
        self.assertGreaterEqual(checked, 5, "the scan found too few statements, so it may be missing some")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
