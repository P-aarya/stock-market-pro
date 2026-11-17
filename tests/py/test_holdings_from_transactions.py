"""
Holdings come from transactions: every BUY and SELL updates the portfolio, holdings can't be changed by hand, and the
Portfolio's 3-month chart and the Watchlist's extra columns are served correctly (the model's columns only on plans
that include stock prediction).
"""
import unittest
from unittest import mock

import _path  # noqa: F401
import api
import market_history
import portfolio
import tiers
from _accounts import signed_in_client


def tx(i, typ, q, px, day):
    return {"id": i, "transaction_type": typ, "quantity": q, "price": px, "transaction_date": day, "notes": ""}


class Replay(unittest.TestCase):
    def test_average_cost_and_sales(self):
        pos = portfolio.replay([tx(1, "BUY", 10, 100, "2025-01-01"), tx(2, "BUY", 10, 200, "2025-02-01"), tx(3, "SELL", 5, 300, "2025-03-01")])
        self.assertEqual(pos["quantity"], 15); self.assertAlmostEqual(pos["avg_cost"], 150)   # a sale keeps the average cost
        self.assertEqual(pos["since"], "2025-01-01"); self.assertIsNone(pos["short"])

    def test_selling_everything_starts_afresh(self):
        pos = portfolio.replay([tx(1, "BUY", 10, 100, "2025-01-01"), tx(2, "SELL", 10, 120, "2025-02-01"), tx(3, "BUY", 2, 50, "2025-03-01")])
        self.assertEqual(pos["quantity"], 2); self.assertAlmostEqual(pos["avg_cost"], 50); self.assertEqual(pos["since"], "2025-03-01")

    def test_dates_not_entry_order_and_buys_first_on_the_same_day(self):
        pos = portfolio.replay([tx(5, "SELL", 3, 10, "2025-01-01"), tx(9, "BUY", 3, 8, "2025-01-01")])
        self.assertEqual(pos["quantity"], 0); self.assertIsNone(pos["short"])

    def test_a_sale_bigger_than_the_position_is_reported(self):
        pos = portfolio.replay([tx(1, "BUY", 2, 100, "2025-01-01"), tx(2, "SELL", 5, 100, "2025-02-01")])
        self.assertEqual(pos["short"], {"date": "2025-02-01", "held": 2, "sold": 5})

    def test_dividends_change_nothing(self):
        pos = portfolio.replay([tx(1, "BUY", 2, 100, "2025-01-01"), tx(2, "DIVIDEND", 2, 0.5, "2025-02-01")])
        self.assertEqual(pos["quantity"], 2); self.assertAlmostEqual(pos["avg_cost"], 100)


class Signals(unittest.TestCase):
    def test_one_word(self):
        self.assertEqual([api.simple_signal(x) for x in ("Strong Buy", "Buy", "Hold", "Sell", "Strong Sell", None, "")],
                         ["BUY", "BUY", "HOLD", "SELL", "SELL", None, None])


class MarketHistory(unittest.TestCase):
    def test_both_answer_layouts_are_read(self):
        a = {"spark": {"result": [{"symbol": "AAPL", "response": [{"timestamp": [1755475200, 1755561600],
                                                                    "indicators": {"quote": [{"close": [200.0, None]}]}}]}]}}
        b = {"MSFT": {"symbol": "MSFT", "timestamp": [1755475200, 1755561600], "close": [400.0, 410.0]}}
        self.assertEqual(market_history.parse(a), {"AAPL": {"dates": ["2025-08-18"], "closes": [200.0]}})
        self.assertEqual(market_history.parse(b)["MSFT"]["closes"], [400.0, 410.0])


class Endpoints(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_routes_are_on_the_right_plans(self):
        self.assertEqual(tiers.feature_for("GET", "/api/watchlist/extras"), "initial_screen")
        self.assertEqual(tiers.feature_for("GET", "/api/portfolio/history"), "portfolio_view")

    def test_portfolio_history_only_charts_my_own_holdings_and_at_most_five(self):
        mine = {t: t for t in ("A", "B", "C", "D", "E", "F")}
        asked = []
        mock.patch.object(api.portfolio, "held_tickers", return_value=mine).start()
        mock.patch.object(api.market_history, "closes", side_effect=lambda syms, rng: asked.extend(syms) or
                          {s: {"dates": ["2026-08-17", "2026-11-16"], "closes": [100.0, 110.0]} for s in syms}).start()
        d = signed_in_client(5).get("/api/portfolio/history?tickers=A,B,ZZZ,C,D,E,F").json()
        self.assertEqual(asked, ["A", "B", "C", "D", "E"])                         # ZZZ isn't mine; F is the sixth
        self.assertEqual([x["ticker"] for x in d["series"]], ["A", "B", "C", "D", "E"])
        self.assertEqual(d["series"][0]["change_pct"], 10.0)

    def test_watchlist_extras_with_and_without_prediction(self):
        mock.patch.object(api.market_history, "closes", return_value={"AAPL": {"dates": ["2026-08-17"], "closes": [180.0]}}).start()
        mock.patch.object(api, "query", return_value=[{"ticker": "AAPL", "buy_signal": "Strong Buy", "prob_up_3m": 61.6}]).start()
        full = signed_in_client(5).get("/api/watchlist/extras?tickers=AAPL").json()
        self.assertEqual(full, {"extras": {"AAPL": {"price_3m_ago": 180.0, "signal": "BUY", "chance_up": 62}}, "predictions": True})
        basic = signed_in_client(6, tier=2).get("/api/watchlist/extras?tickers=AAPL").json()
        self.assertEqual(basic, {"extras": {"AAPL": {"price_3m_ago": 180.0}}, "predictions": False})

    def test_holdings_cannot_be_changed_directly(self):
        r = signed_in_client(5).put("/api/portfolio/3", json={"quantity": 1, "avg_buy_price": 1, "buy_date": "2025-01-02"})
        self.assertEqual(r.json()["status"], "error")
        self.assertIn("Transactions", r.json()["message"])


if __name__ == "__main__":
    unittest.main()
