import unittest
from unittest import mock

import _path  # noqa: F401
import api
from _accounts import signed_in_client


def stock(t, name):
    return {"ticker": t, "company_name": name, "sector": "Tech", "current_price": 100.0, "price_change_pct": 1.0}


class StockSearch(unittest.TestCase):
    def setUp(self):
        self.client = signed_in_client()
        self.calls = []

    def run_search(self, q, answers, limit=10):
        answers = list(answers)

        def fake(sql, params=None):
            self.calls.append((sql, list(params or [])))
            return answers.pop(0) if answers else []
        with mock.patch.object(api, "query", side_effect=fake):
            r = self.client.get("/api/stocks/search", params={"q": q, "limit": limit})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_one_letter_is_enough_and_matches_tickers_first(self):
        out = self.run_search("g", [[stock("G", "Genpact"), stock("GOOGL", "Alphabet Inc.")]])
        self.assertEqual([x["ticker"] for x in out], ["G", "GOOGL"])
        sql, params = self.calls[0]
        self.assertIn("CASE WHEN ticker = %s THEN 0 WHEN ticker LIKE %s THEN 1", sql)
        self.assertEqual(params[:2], ["G%", "%g%"])

    def test_punctuation_is_ignored_in_names(self):
        self.run_search("McDonalds", [[stock("MCD", "McDonald's Corporation")]])
        sql, params = self.calls[0]
        self.assertIn("''''", sql)                                       # the apostrophe is removed before comparing
        self.assertEqual(params[2:4], ["%mcdonalds%", "mcdonalds"])

    def test_a_brand_name_finds_its_company(self):
        out = self.run_search("facebook", [[], [], [stock("META", "Meta Platforms, Inc.")]])
        self.assertEqual(out[0]["ticker"], "META")
        self.assertEqual(out[0]["match"], "brand")
        self.assertEqual(self.calls[-1][1], ["META"])

    def test_a_ticker_one_letter_too_long_still_finds_it(self):
        out = self.run_search("GOOGLE", [[], [stock("GOOGL", "Alphabet Inc.")], []])
        self.assertIn("GOOGL", [x["ticker"] for x in out])
        self.assertEqual(self.calls[1][1][0], "GOOGL%")
        self.assertEqual(len([x for x in out if x["ticker"] == "GOOGL"]), 1)     # not listed twice

    def test_wildcards_are_taken_literally_and_limits_are_kept(self):
        self.run_search("50%_", [[]])
        self.assertEqual(self.calls[0][1][0], "50\\%\\_%")
        self.calls.clear()
        self.run_search("a", [[stock(f"A{i}", "x") for i in range(30)]], limit=999)
        self.assertEqual(self.calls[0][1][-1], 20)
        self.assertEqual(self.run_search("   ", [[stock("X", "y")]]), [])

    def test_if_the_full_search_fails_the_simple_one_still_answers(self):
        calls = []

        def flaky(sql, params=None):
            calls.append(sql)
            if len(calls) == 1:
                raise RuntimeError("Illegal mix of collations")
            return [stock("NVDA", "NVIDIA Corporation")]
        with mock.patch.object(api, "query", side_effect=flaky):
            out = self.client.get("/api/stocks/search?q=nvidia").json()
        self.assertEqual([x["ticker"] for x in out], ["NVDA"])
        with mock.patch.object(api, "query", side_effect=RuntimeError("down")):
            self.assertEqual(self.client.get("/api/stocks/search?q=nv").json(), [])


class EarningsNaN(unittest.TestCase):
    def test_nan_and_infinity_from_yahoo_become_blanks(self):
        client = signed_in_client()
        fake = {"ticker": "SPCX", "next_earnings_date": None, "eps_estimate": float("nan"), "eps_actual": float("inf"),
                "history": [{"date": "2026-01-01", "eps_estimate": float("nan"), "eps_actual": 1.5}]}
        with mock.patch.object(api, "_stock_earnings", return_value=fake):
            r = client.get("/api/earnings/SPCX")
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual((j["eps_estimate"], j["eps_actual"], j["history"][0]["eps_estimate"], j["history"][0]["eps_actual"]), (None, None, None, 1.5))


if __name__ == "__main__":
    unittest.main()
