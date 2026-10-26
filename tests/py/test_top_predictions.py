import unittest
from unittest import mock

import _path  # noqa: F401
import api
from _accounts import signed_in_client


def row(ticker, price, target, prob, signal="Buy"):
    return {"ticker": ticker, "buy_signal": signal, "target_3m": target, "prob_up_3m": prob, "run_date": "2026-10-20",
            "company_name": ticker + " Inc", "current_price": price, "market_cap": 5e9, "avg_volume": 1e6, "volume": 1e6}


class TopPredictions(unittest.TestCase):
    def setUp(self):
        self.client = signed_in_client()

    def get(self, rows, limit=5):
        with mock.patch.object(api, "query", return_value=rows) as q:
            r = self.client.get(f"/api/predictions/top?limit={limit}")
        self.sql = q.call_args[0][0]
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_ranked_by_expected_gain_times_chance_of_going_up(self):
        out = self.get([
            row("LONGSHOT", 100, 140, 51),      # +40% x 0.51 = 20.4
            row("STEADY", 100, 130, 75),        # +30% x 0.75 = 22.5
            row("SMALL", 100, 105, 90),         # +5%  x 0.90 = 4.5
        ])
        self.assertEqual([x["ticker"] for x in out], ["STEADY", "LONGSHOT", "SMALL"])
        self.assertEqual((out[0]["upside_pct"], out[0]["target_3m"], out[0]["prob_up_3m"], out[0]["signal"]), (30.0, 130.0, 75.0, "Buy"))
        self.assertNotIn("_score", out[0])

    def test_drops_falling_targets_unlikely_rises_and_bad_rows(self):
        out = self.get([
            row("DOWN", 100, 90, 80),            # target below today's price
            row("COINFLIP", 100, 150, 40),       # less than an even chance of going up
            row("BROKEN", None, 150, 80),        # no price
            row("NOPROB", 100, 120, None),       # no probability: kept, ranked as an even chance
        ])
        self.assertEqual([x["ticker"] for x in out], ["NOPROB"])
        self.assertIsNone(out[0]["prob_up_3m"])

    def test_limit_and_the_sql_filters(self):
        out = self.get([row(f"T{i}", 100, 110 + i, 60) for i in range(9)], limit=5)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["ticker"], "T8")
        for part in ("UPPER(p.buy_signal) IN ('BUY', 'STRONG BUY')", "l.market_cap >= 2000000000", "MAX(run_date)", ">= 300000"):
            self.assertIn(part, self.sql)
        self.assertEqual(len(self.get([row(f"T{i}", 100, 120, 60) for i in range(30)], limit=999)), 20)   # capped

    def test_needs_a_signed_in_user(self):
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(api.app).get("/api/predictions/top").status_code, 401)


if __name__ == "__main__":
    unittest.main()
