"""Endpoint behaviour that needs no database or network: request validation and error handling.
Sign-up, sign-in and who-can-see-what are in test_accounts.py."""
import re
import unittest
from unittest import mock

import _path  # noqa: F401
import agent
import api
from _accounts import signed_in_client


class Validation(unittest.TestCase):
    def setUp(self):
        self.c = signed_in_client(1)
        api._agent_hits.clear()

    def test_bad_requests_are_refused_before_any_work_is_done(self):
        cases = [("post", "/agent/recommend", {"json": {"query": ""}}), ("post", "/agent/recommend", {"json": {"query": "x" * 501}}),
                 ("post", "/agent/recommend", {"json": {"query": "hi", "chat_history": [{"role": "system", "content": "x"}]}}),
                 ("post", "/api/portfolio", {"json": {"ticker": "KO", "quantity": -5, "avg_buy_price": 10, "buy_date": "2025-01-01"}}),
                 ("post", "/api/portfolio", {"json": {"ticker": "K O", "quantity": 5, "avg_buy_price": 10, "buy_date": "2025-01-01"}}),
                 ("post", "/api/transactions", {"json": {"ticker": "KO", "transaction_type": "SHORT", "quantity": 1, "price": 1, "transaction_date": "2025-01-01"}}),
                 ("get", "/api/peers/bad%20t", {}), ("get", "/api/peers/NVDA?n=9", {}), ("get", "/api/ipos?days_back=-1", {}), ("get", "/api/ipos?days_ahead=500", {}),
                 ("get", "/api/compare?tickers=NVDA", {}), ("get", "/api/compare/prices?tickers=AAPL", {}), ("get", "/api/compare/verdict?tickers=AAPL;DROP,MSFT", {})]
        for method, path, kwargs in cases:
            r = getattr(self.c, method)(path, **kwargs)
            self.assertIn(r.status_code, (400, 422), f"{method} {path} -> {r.status_code}")

    def test_missing_ai_key_gives_a_clear_503_not_a_crash(self):
        saved = agent.client; agent.client = None
        try:
            r = self.c.post("/agent/recommend", json={"query": "what is inflation"})
            self.assertEqual((r.status_code, r.json()["message"]), (503, "The AI assistant isn't available right now."))
            events = [line for line in self.c.post("/agent/recommend/stream", json={"query": "hi"}).text.split("\n") if line.startswith("data: ")]
            self.assertEqual(len(events), 1); self.assertIn('"status": 503', events[0])
        finally:
            agent.client = saved

    def test_ai_endpoints_are_rate_limited_per_person(self):
        saved_limit = api.AGENT_RATE_LIMIT; api.AGENT_RATE_LIMIT = 3; api._agent_hits.clear()
        saved = agent.client; agent.client = None
        try:
            codes = [self.c.post("/agent/recommend", json={"query": "hi"}).status_code for _ in range(5)]
            self.assertEqual(codes, [503, 503, 503, 429, 429])
            other = signed_in_client(2).post("/agent/recommend", json={"query": "hi"}); self.assertEqual(other.status_code, 503)
        finally:
            api.AGENT_RATE_LIMIT = saved_limit; api._agent_hits.clear(); agent.client = saved

    def test_dashboard_page_has_the_features(self):
        html = self.c.get("/").text
        for needle in ('id="page-ipos"', 'id="fss-peers"', 'id="sent-card"', 'id="pf-perf-card"', 'id="tx-form-card"', "runPfReview('holdings')", "/agent/recommend/stream"):
            self.assertIn(needle, html)
        self.assertNotIn("pfRisk", html)


class CommodityQuery(unittest.TestCase):
    def test_every_table_alias_the_query_uses_is_joined_and_the_category_comes_from_fundamentals(self):
        # market_cap_category lives in stocks_fundamentals, not stocks_live: asking stocks_live for it made the whole query
        # fail ("Unknown column") and every commodity company list come back empty.
        seen = {}
        with mock.patch.object(api, "query", side_effect=lambda sql, params=None: seen.setdefault("sql", sql) and []):
            api.batch_commodity_companies({"tickers": ["AEM", "FCX"]})
        sql = " ".join(seen["sql"].split())
        defined = set(re.findall(r"(?:FROM|JOIN)\s+\w+\s+(\w+)", sql))
        used = set(re.findall(r"\b([a-z])\.\w+", sql))
        self.assertTrue(used <= defined, f"aliases used but never joined: {used - defined}")
        self.assertIn("f.market_cap_category", sql)
        self.assertNotIn("l.market_cap_category", sql)
        self.assertIn("LEFT JOIN stocks_fundamentals f ON l.ticker = f.ticker", sql)


if __name__ == "__main__":
    unittest.main()
