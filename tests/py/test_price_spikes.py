import time
import unittest
from unittest import mock

import numpy as np
import pandas as pd
import _path  # noqa: F401
import api
from _accounts import signed_in_client


def closes_with_spikes(n_spikes=15, days=250):
    """A quiet price series with `n_spikes` one-day moves of 5% (alternating up and down)."""
    rets = np.full(days, 0.001)
    for k in range(n_spikes):
        rets[10 + k * 15] = 0.05 if k % 2 == 0 else -0.05
    idx = pd.bdate_range("2025-11-03", periods=days)
    return pd.DataFrame({"Close": 100 * np.cumprod(1 + rets)}, index=idx)


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period="1y"):
        if self.symbol == "SPY":
            return pd.DataFrame({"Close": np.linspace(500, 520, 250)}, index=pd.bdate_range("2025-11-03", periods=250))
        return closes_with_spikes()


class ForecastSpikeNews(unittest.TestCase):
    def setUp(self):
        api._NEWS_CACHE.clear()
        self.client = signed_in_client()
        self.urls = []
        mock.patch.object(api.yf, "Ticker", side_effect=FakeTicker).start()
        mock.patch.object(api, "_fetch_news", return_value=[]).start()               # Yahoo has nothing this old
        mock.patch.object(api, "query", return_value=[{"company_name": "Alphabet Inc."}]).start()
        self.addCleanup(mock.patch.stopall)

    def feed(self, url, source, category, limit=8):
        self.urls.append(url)
        if "after%3A" in url:
            return [{"title": "Alphabet shares move on cloud news - Reuters", "url": "https://example.com/n",
                     "source": "Reuters", "published": time.time()}]
        return []

    def test_every_spike_gets_a_dated_search_by_ticker_or_company_name(self):
        with mock.patch.object(api, "_nu_fetch_feed", side_effect=self.feed):
            out = self.client.get("/api/stocks/GOOGL/price-spikes").json()
        spikes = out["spikes"]
        self.assertEqual(len(spikes), 15)                                          # more than the old limit of 12
        self.assertTrue(all(sp["reason"] == "Alphabet shares move on cloud news" for sp in spikes))
        self.assertTrue(all(sp["reason_source"] == "Reuters" for sp in spikes))
        self.assertTrue(all("Alphabet" in u and "GOOGL" in u for u in self.urls))  # (GOOGL OR "Alphabet")
        self.assertEqual(len(self.urls), 15)
        with mock.patch.object(api, "_nu_fetch_feed", side_effect=self.feed):
            self.client.get("/api/stocks/GOOGL/price-spikes")
        self.assertEqual(len(self.urls), 15)                                       # saved for an hour

    def test_a_wider_window_is_tried_when_the_first_finds_nothing(self):
        calls = []

        def only_wide(url, source, category, limit=8):
            calls.append(url)
            if len(calls) % 2 == 1:
                return []                                                         # nothing within 2 days
            return [{"title": "Late story - AP", "url": "u", "source": "AP", "published": time.time()}]
        with mock.patch.object(api, "_nu_fetch_feed", side_effect=only_wide):
            res = api._spike_headline("GOOGL", "Alphabet", "2026-03-10")
        self.assertEqual(res["reason"], "Late story")
        self.assertIn("after%3A2026-03-08", calls[0])
        self.assertIn("after%3A2026-03-06", calls[1])

    def test_no_headline_anywhere_leaves_the_spike_without_one(self):
        with mock.patch.object(api, "_nu_fetch_feed", return_value=[]):
            out = self.client.get("/api/stocks/GOOGL/price-spikes").json()
        self.assertTrue(out["spikes"])
        self.assertTrue(all(sp["reason"] is None for sp in out["spikes"]))
        self.assertTrue(all(sp.get("driver") is not None for sp in out["spikes"]))


if __name__ == "__main__":
    unittest.main()
