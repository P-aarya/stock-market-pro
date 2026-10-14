import unittest
from datetime import date, timedelta

import numpy as np

import _path  # noqa: F401
import portfolio as P


def tx(i, ticker, kind, qty, price, day, total=None):
    return {"id": i, "ticker": ticker, "transaction_type": kind, "quantity": qty, "price": price, "transaction_date": day, "total_value": total}


class SummaryMaths(unittest.TestCase):
    def setUp(self):
        self.rows = P.with_values([
            {"ticker": "AAA", "quantity": 10, "avg_buy_price": 100.0, "current_price": 150.0, "day_change_pct": 5.0, "sector": "Technology"},
            {"ticker": "BBB", "quantity": 5, "avg_buy_price": 200.0, "current_price": 150.0, "day_change_pct": -2.0, "sector": "Banking"},
            {"ticker": "NOPX", "quantity": 100, "avg_buy_price": 10.0, "current_price": None, "day_change_pct": None, "sector": "Unknown"}])

    def test_row_values(self):
        a, b, c = self.rows
        self.assertEqual((a["invested_value"], a["current_value"], a["unrealized_pnl"], a["pnl_pct"]), (1000, 1500, 500, 50.0))
        self.assertEqual((b["current_value"], b["unrealized_pnl"], b["pnl_pct"]), (750, -250, -25.0))
        self.assertIsNone(c["current_value"]); self.assertIsNone(c["pnl_pct"]); self.assertEqual(c["invested_value"], 1000)

    def test_sector_groups_are_standard(self):
        self.assertEqual([r["sector_group"] for r in self.rows], ["Technology", "Financials", "Other"])

    def test_unpriced_holdings_stay_out_of_the_return(self):
        s = P.summarize(self.rows)
        self.assertEqual((s["total_value"], s["total_invested"], s["priced_invested"]), (2250, 3000, 2000))
        self.assertEqual((s["total_pnl"], s["return_pct"]), (250, 12.5))          # 250 / 2000, not 250 / 3000
        self.assertEqual((s["unpriced_holdings"], s["unpriced_tickers"]), (1, ["NOPX"]))

    def test_todays_change_uses_yesterdays_value(self):
        exact = (1500 - 1500 / 1.05) + (750 - 750 / 0.98)
        self.assertAlmostEqual(P.summarize(self.rows)["day_change"], round(exact, 2), places=2)

    def test_empty_and_fully_unpriced(self):
        self.assertEqual(P.summarize([])["total_value"], 0); self.assertIsNone(P.summarize([])["return_pct"])
        self.assertIsNone(P.summarize(P.with_values([{"ticker": "X", "quantity": 1, "avg_buy_price": 5, "current_price": None}]))["return_pct"])


class Validation(unittest.TestCase):
    def bad(self, *args, message):
        with self.assertRaises(P.PortfolioError) as cm:
            P._check_position(*args)
        self.assertIn(message, cm.exception.message)

    def test_rejects_bad_input(self):
        today = date.today().isoformat()
        for args, msg in [((0, 10, today, ""), "Quantity"), ((-5, 10, today, ""), "Quantity"), ((float("nan"), 10, today, ""), "Quantity"), ((1e12, 10, today, ""), "Quantity"),
                          ((1, 0, today, ""), "price"), ((1, -3, today, ""), "price"), ((1, 10, "14/03/2025", ""), "Buy date must"), ((1, 10, "not a date", ""), "Buy date must"),
                          ((1, 10, (date.today() + timedelta(days=2)).isoformat(), ""), "future"), ((1, 10, "1901-01-01", ""), "far in the past")]:
            self.bad(*args, message=msg)

    def test_transaction_wording_is_generic(self):
        with self.assertRaises(P.PortfolioError) as cm:
            P._check_tx("SELL", 1, 1, "2999-01-01", "")
        self.assertEqual(cm.exception.message, "Date can't be in the future.")
        with self.assertRaises(P.PortfolioError):
            P._check_tx("SHORT", 1, 1, date.today().isoformat(), "")

    def test_accepts_good_input_and_trims_notes(self):
        self.assertEqual(P._check_position(2, 10.5, date.today().isoformat(), "  note  ")[3], "note")

    def test_merging_purchases_uses_the_weighted_average(self):
        self.assertEqual(P.merge_lots([{"quantity": 10, "avg_buy_price": 100.0}], 10, 200.0), (20, 150.0))
        q, avg = P.merge_lots([{"quantity": 10, "avg_buy_price": 100.0}, {"quantity": 30, "avg_buy_price": 120.0}], 10, 200.0)
        self.assertEqual(q, 50); self.assertAlmostEqual(avg, (1000 + 3600 + 2000) / 50)


class RealizedPnl(unittest.TestCase):
    def test_average_cost_dividends_and_unmatched_sales(self):
        r = P.realized_pnl([tx(1, "AAA", "BUY", 10, 100, "2024-01-01"), tx(2, "AAA", "BUY", 10, 200, "2024-02-01"), tx(3, "AAA", "SELL", 5, 300, "2024-03-01"),
                            tx(4, "BBB", "BUY", 10, 50, "2024-01-01"), tx(5, "BBB", "SELL", 4, 40, "2024-02-01"), tx(6, "CCC", "SELL", 3, 10, "2024-02-01"),
                            tx(7, "AAA", "DIVIDEND", 15, 1.5, "2024-04-01", 22.5)])
        self.assertEqual(r["realized_by_ticker"], {"AAA": 750.0, "BBB": -40.0}); self.assertEqual(r["realized_total"], 710.0)
        self.assertEqual(r["dividends"], 22.5); self.assertEqual(r["unmatched_sells"], {"CCC": 3.0})

    def test_order_is_by_date_not_insertion(self):
        self.assertEqual(P.realized_pnl([tx(2, "AAA", "SELL", 5, 300, "2024-03-01"), tx(1, "AAA", "BUY", 10, 100, "2024-01-01")])["realized_total"], 1000.0)

    def test_selling_more_than_owned_matches_only_what_is_owned(self):
        r = P.realized_pnl([tx(1, "AAA", "BUY", 2, 100, "2024-01-01"), tx(2, "AAA", "SELL", 5, 150, "2024-02-01")])
        self.assertEqual(r["realized_total"], 100.0); self.assertEqual(r["unmatched_sells"], {"AAA": 3.0})


class RiskAndFacts(unittest.TestCase):
    def test_beta_volatility_drawdown_and_diversification(self):
        rng = np.random.default_rng(1); mkt = rng.normal(0.0004, 0.01, 300); port = 1.3 * mkt + rng.normal(0, 0.004, 300)
        k = P.risk_metrics(port, mkt, [50, 30, 20])
        self.assertTrue(1.15 < k["beta"] < 1.45); self.assertEqual(k["effective_holdings"], 2.6); self.assertEqual(k["top3_weight_pct"], 100.0)
        self.assertEqual(k["diversification"], "Concentrated"); self.assertLess(k["max_drawdown_pct"], 0)

    def test_too_little_data_gives_none_not_nonsense(self):
        k = P.risk_metrics(np.zeros(20), np.zeros(20), [1] * 12)
        self.assertIsNone(k["volatility_pct"]); self.assertIsNone(k["beta"]); self.assertEqual(k["diversification"], "Diversified")

    def test_concentration_flags(self):
        pos = [{"ticker": "BIG", "current_value": 6000.0, "invested_value": 3000.0, "sector": "Technology", "risk_level": "High", "buy_signal": "Strong Sell"},
               {"ticker": "B", "current_value": 2000.0, "invested_value": 2500.0, "sector": "Cloud & SaaS", "risk_level": "Low", "buy_signal": "Buy"},
               {"ticker": "C", "current_value": 2000.0, "invested_value": 2000.0, "sector": "Healthcare", "risk_level": "Low", "buy_signal": "Hold"}]
        f = P.portfolio_facts(pos)
        self.assertEqual(f["largest"][0], "BIG"); self.assertEqual(f["return_pct"], 33.3)
        self.assertEqual(dict(f["sectors"])["Technology"], 80.0)          # Technology + Cloud & SaaS are one group
        text = " | ".join(f["flags"])
        for expected in ("BIG is 60%", "80% of the portfolio is in Technology", "High risk", "sell signals on: BIG", "Only 3 holdings"):
            self.assertIn(expected, text)
        self.assertIn("**Portfolio snapshot**", P.snapshot_markdown(f, True))


if __name__ == "__main__":
    unittest.main()
