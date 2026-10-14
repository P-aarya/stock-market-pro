import unittest
from datetime import date

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import _path  # noqa: F401
import api
import stock_period as SP


def series(start, values):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


class Statistics(unittest.TestCase):
    def test_change_high_low_and_trading_days(self):
        s = series("2024-01-01", [100, 110, 90, 120, 108])                       # Mon Jan 1 .. Fri Jan 5
        r = SP.compute_period(s, None, date(2024, 1, 1), date(2024, 1, 5))["stats"]
        self.assertEqual((r["start_price"], r["end_price"], r["change_pct"], r["change_usd"]), (100.0, 108.0, 8.0, 8.0))
        self.assertEqual((r["high"], r["high_date"], r["low"], r["low_date"]), (120.0, "2024-01-04", 90.0, "2024-01-03"))
        self.assertEqual(r["trading_days"], 5)

    def test_biggest_drop_is_measured_from_the_peak_before_it(self):
        s = series("2024-01-01", [100, 120, 90, 95, 130, 117])
        r = SP.compute_period(s, None, date(2024, 1, 1), date(2024, 1, 8))["stats"]
        self.assertEqual(r["max_drawdown_pct"], -25.0)                            # 120 -> 90
        self.assertEqual((r["max_drawdown_peak_date"], r["max_drawdown_low_date"]), ("2024-01-02", "2024-01-03"))

    def test_best_worst_day_and_up_days(self):
        s = series("2024-01-01", [100, 110, 99, 99, 108.9])
        r = SP.compute_period(s, None, date(2024, 1, 1), date(2024, 1, 5))["stats"]
        self.assertEqual((r["best_day_pct"], r["best_day"]), (10.0, "2024-01-02")); self.assertEqual((r["worst_day_pct"], r["worst_day"]), (-10.0, "2024-01-03"))
        self.assertEqual(r["up_days_pct"], 50.0)

    def test_only_days_inside_the_period_count(self):
        s = series("2024-01-01", list(range(100, 160)))                          # 60 business days
        r = SP.compute_period(s, None, date(2024, 1, 15), date(2024, 1, 19))["stats"]
        self.assertEqual((r["actual_start"], r["actual_end"], r["trading_days"]), ("2024-01-15", "2024-01-19", 5))

    def test_volatility_needs_enough_days(self):
        rng = np.random.default_rng(3); s = series("2024-01-01", 100 * np.cumprod(1 + rng.normal(0, 0.01, 120)))
        self.assertIsNone(SP.compute_period(s, None, date(2024, 1, 1), date(2024, 1, 12))["stats"]["volatility_pct"])
        self.assertTrue(10 < SP.compute_period(s, None, date(2024, 1, 1), date(2024, 6, 1))["stats"]["volatility_pct"] < 30)

    def test_benchmark_comparison_and_rebased_line(self):
        stock = series("2024-01-01", [100, 110, 120]); bench = series("2024-01-01", [400, 404, 440])
        r = SP.compute_period(stock, bench, date(2024, 1, 1), date(2024, 1, 3))
        self.assertEqual((r["stats"]["benchmark_change_pct"], r["stats"]["difference_pp"]), (10.0, 10.0))
        self.assertEqual(r["benchmark"], [100.0, 101.0, 110.0])                   # the index drawn from the stock's starting price
        self.assertEqual(r["benchmark_name"], SP.BENCHMARK_NAME)

    def test_missing_or_partial_benchmark_is_left_out_not_guessed(self):
        stock = series("2024-01-01", [100, 110, 120]); late_bench = series("2024-01-03", [400, 410])
        for bench in (None, late_bench):
            r = SP.compute_period(stock, bench, date(2024, 1, 1), date(2024, 1, 3))
            self.assertIsNone(r["stats"]["benchmark_change_pct"]); self.assertIsNone(r["benchmark"])

    def test_a_stock_that_started_trading_mid_period_is_flagged(self):
        s = series("2024-03-01", [50, 55, 60, 66])
        r = SP.compute_period(s, None, date(2024, 1, 1), date(2024, 3, 31))["stats"]
        self.assertTrue(r["starts_late"]); self.assertEqual(r["actual_start"], "2024-03-01")
        self.assertFalse(SP.compute_period(series("2024-01-01", [1, 2, 3]), None, date(2024, 1, 1), date(2024, 1, 3))["stats"]["starts_late"])

    def test_no_data_gives_a_clear_error(self):
        s = series("2024-03-01", [50, 55, 60])
        for start, end in ((date(2019, 1, 1), date(2019, 12, 31)), (date(2024, 3, 2), date(2024, 3, 3))):             # before listing; a weekend
            with self.assertRaises(SP.PeriodError) as cm:
                SP.compute_period(s, None, start, end)
            self.assertEqual(cm.exception.status, 404); self.assertIn("isn't enough price data", cm.exception.message)

    def test_long_periods_are_thinned_for_the_chart_but_keep_both_ends(self):
        s = series("2000-01-03", np.linspace(10, 100, 3000))
        r = SP.compute_period(s, None, date(2000, 1, 1), date(2011, 12, 31))
        self.assertLessEqual(len(r["dates"]), SP.MAX_POINTS + 1); self.assertEqual(r["dates"][0], "2000-01-03"); self.assertEqual(r["dates"][-1], r["stats"]["actual_end"])
        self.assertEqual(r["stats"]["trading_days"], 3000)                         # the statistics still use every day


class DateRules(unittest.TestCase):
    today = date(2026, 10, 13)

    def test_end_is_capped_at_today(self):
        self.assertEqual(SP.validate_dates(date(2026, 1, 1), date(2099, 12, 31), self.today), (date(2026, 1, 1), self.today))

    def test_sensible_errors(self):
        for start, end, text in ((date(2024, 6, 30), date(2024, 4, 1), "before the end"), (date(2024, 6, 28), date(2024, 6, 28), "before the end"),
                                 (date(2030, 1, 1), date(2030, 12, 31), "hasn't started"), (date(1960, 1, 1), date(1961, 1, 1), "1970"),
                                 (date(1971, 1, 1), date(2026, 10, 1), "40 years")):
            with self.assertRaises(SP.PeriodError) as cm:
                SP.validate_dates(start, end, self.today)
            self.assertIn(text, cm.exception.message)


class Endpoints(unittest.TestCase):
    """The endpoints with the price download replaced by synthetic prices."""

    def setUp(self):
        api.APP_PASSWORD = ""
        self.saved = (SP.full_history, api._tracked_stock)
        stock = series("2020-01-01", np.linspace(100, 300, 1800)); bench = series("2020-01-01", np.linspace(300, 500, 1800))
        SP.full_history = lambda t: bench if t.upper() == "SPY" else (stock if t.upper() == "TEST" else (_ for _ in ()).throw(SP.PeriodError("No price history is available for X.", 404)))
        api._tracked_stock = lambda t: t.upper() in ("TEST", "NOPRICE")
        self.c = TestClient(api.app)

    def tearDown(self):
        SP.full_history, api._tracked_stock = self.saved

    def test_price_range(self):
        r = self.c.get("/api/stocks/TEST/price-range").json()
        self.assertEqual((r["status"], r["first_date"]), ("success", "2020-01-01")); self.assertGreater(r["last_date"], "2026-01-01")

    def test_a_period_returns_stats_and_a_chart(self):
        r = self.c.get("/api/stocks/TEST/period?start=2021-01-01&end=2021-12-31").json()
        self.assertEqual(r["status"], "success"); self.assertEqual(r["ticker"], "TEST")
        self.assertEqual(len(r["dates"]), len(r["prices"])); self.assertEqual(len(r["dates"]), len(r["benchmark"]))
        self.assertGreater(r["stats"]["change_pct"], 0); self.assertIsNotNone(r["stats"]["difference_pp"]); self.assertIn("adjusted", r["adjusted"])

    def test_errors_are_plain_and_use_the_right_status(self):
        for url, status, text in (("/api/stocks/TEST/period?start=2030-01-01&end=2030-12-31", 400, "hasn't started"),
                                  ("/api/stocks/TEST/period?start=2021-06-30&end=2021-04-01", 400, "before the end"),
                                  ("/api/stocks/TEST/period?start=2010-01-01&end=2010-12-31", 404, "enough price data"),
                                  ("/api/stocks/UNKNOWN/period?start=2021-01-01&end=2021-12-31", 404, "isn't in our database"),
                                  ("/api/stocks/NOPRICE/period?start=2021-01-01&end=2021-12-31", 404, "No price history")):
            r = self.c.get(url); self.assertEqual(r.status_code, status, url); self.assertIn(text, r.json()["message"])
        for url in ("/api/stocks/TEST/period?start=2024-13-01&end=2024-06-30", "/api/stocks/TEST/period", "/api/stocks/T%20X/period?start=2021-01-01&end=2021-12-31"):
            self.assertEqual(self.c.get(url).status_code, 422, url)

    def test_the_stock_still_works_when_the_benchmark_cannot_be_loaded(self):
        original = SP.full_history
        SP.full_history = lambda t: (_ for _ in ()).throw(RuntimeError("down")) if t.upper() == "SPY" else original(t)
        r = self.c.get("/api/stocks/TEST/period?start=2021-01-01&end=2021-12-31").json()
        self.assertEqual(r["status"], "success"); self.assertIsNone(r["benchmark"]); self.assertIsNone(r["stats"]["benchmark_change_pct"])

    def test_history_is_cached_between_requests(self):
        SP.full_history, api._tracked_stock = self.saved[0], self.saved[1]
        calls = []
        original_download = SP._download
        SP._download = lambda t: (calls.append(t), series("2020-01-01", np.linspace(1, 2, 300)))[1]
        SP._cache.clear()
        try:
            SP.full_history("CACHETEST"); SP.full_history("cachetest"); SP.full_history("CACHETEST")
            self.assertEqual(calls, ["CACHETEST"])
        finally:
            SP._download = original_download; SP._cache.clear()


if __name__ == "__main__":
    unittest.main()
