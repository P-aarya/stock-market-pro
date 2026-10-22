import time
import unittest
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd
import _path  # noqa: F401
import api
from _accounts import signed_in_client
import stock_period as SP


def series(start, values):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def calm_with_jumps(n=60, jumps=None):
    """A stock that drifts 0.5% a day, with a few big days added."""
    rets = np.array([0.005 if i % 2 else -0.004 for i in range(n)])
    for i, r in (jumps or {}).items():
        rets[i] = r
    return series("2024-01-01", 100 * np.cumprod(1 + rets))


class FindSpikes(unittest.TestCase):
    def test_big_days_are_found_oldest_first_with_the_market_move(self):
        s = calm_with_jumps(jumps={10: 0.09, 30: -0.07})
        spy = series("2024-01-01", 100 * np.cumprod(1 + np.array([0.001] * 30 + [-0.04] + [0.001] * 29)))
        out = SP.find_spikes(s, spy, date(2024, 1, 1), date(2024, 3, 31))
        self.assertEqual([o["date"] for o in out], [s.index[10].strftime("%Y-%m-%d"), s.index[30].strftime("%Y-%m-%d")])
        self.assertEqual((out[0]["change_pct"], out[0]["direction"]), (9.0, "up"))
        self.assertEqual((out[1]["change_pct"], out[1]["market_change_pct"]), (-7.0, -4.0))
        self.assertIn("stock-specific", out[0]["driver"].lower())          # S&P 500 only +0.1% that day
        self.assertIn("Market-wide", out[1]["driver"])

    def test_only_the_biggest_ones_and_only_inside_the_period(self):
        s = calm_with_jumps(jumps={i: 0.05 + i / 1000 for i in range(5, 55, 3)})
        out = SP.find_spikes(s, None, date(2024, 1, 1), date(2024, 3, 31), limit=4)
        self.assertEqual(len(out), 4)
        self.assertEqual([o["date"] for o in out], sorted(o["date"] for o in out))   # oldest first
        self.assertTrue(all(o["change_pct"] >= 5.4 for o in out))          # the four largest
        self.assertTrue(all(o["market_change_pct"] is None and o["driver"] == "" for o in out))
        self.assertEqual(SP.find_spikes(s, None, date(2030, 1, 1), date(2030, 2, 1)), [])

    def test_a_quiet_period_has_no_spikes(self):
        self.assertEqual(SP.find_spikes(calm_with_jumps(), None, date(2024, 1, 1), date(2024, 3, 31)), [])


class Endpoint(unittest.TestCase):
    def setUp(self):
        api._NEWS_CACHE.clear()
        self.client = signed_in_client()
        s = calm_with_jumps(jumps={10: 0.09})
        self.day = s.index[10].strftime("%Y-%m-%d")
        patches = [
            mock.patch.object(api, "_tracked_stock", return_value=True),
            mock.patch.object(SP, "full_history", return_value=s),
            mock.patch.object(api, "query", return_value=[{"company_name": "NVIDIA Corporation"}]),
            mock.patch.object(api, "_spike_earnings_days", return_value={self.day}),
            mock.patch.object(api, "_fetch_news", return_value=[]),
        ]
        for p in patches:
            p.start()
        self.addCleanup(mock.patch.stopall)
        self.urls = []

        def feed(url, source, category, limit=8):
            self.urls.append(url)
            return [{"title": "Nvidia surges on data-centre demand - Reuters", "url": "https://example.com/a",
                     "source": "Reuters", "published": time.time()}]
        mock.patch.object(api, "_nu_fetch_feed", side_effect=feed).start()

    def test_returns_the_moves_with_earnings_and_a_dated_headline(self):
        r = self.client.get("/api/stocks/NVDA/period-spikes?start=2024-01-01&end=2024-03-31").json()
        self.assertEqual(r["status"], "success")
        sp = r["spikes"][0]
        self.assertEqual((sp["date"], sp["earnings"], sp["reason"], sp["reason_source"]),
                         (self.day, True, "Nvidia surges on data-centre demand", "Reuters"))
        self.assertIn("after%3A", self.urls[0])                               # a dated search
        self.assertIn("NVIDIA", self.urls[0])                                  # by ticker or company name
        self.client.get("/api/stocks/NVDA/period-spikes?start=2024-01-01&end=2024-03-31")
        self.assertEqual(len(self.urls), 1)                                    # the second call is cached

    def test_bad_dates_and_unknown_stocks_are_refused(self):
        r = self.client.get("/api/stocks/NVDA/period-spikes?start=2024-05-01&end=2024-01-01")
        self.assertEqual(r.status_code, 400)
        with mock.patch.object(api, "_tracked_stock", return_value=False):
            self.assertEqual(self.client.get("/api/stocks/ZZZZ/period-spikes?start=2024-01-01&end=2024-03-31").status_code, 404)

    def test_needs_a_signed_in_user(self):
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(api.app).get("/api/stocks/NVDA/period-spikes?start=2024-01-01&end=2024-03-31").status_code, 401)


if __name__ == "__main__":
    unittest.main()
