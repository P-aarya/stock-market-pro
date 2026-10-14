"""Endpoint behaviour that needs no database or network: login, request validation and error handling."""
import time
import unittest

from fastapi.testclient import TestClient

import _path  # noqa: F401
import agent
import api

FORM = {"content-type": "application/x-www-form-urlencoded"}


class Authentication(unittest.TestCase):
    def setUp(self):
        self.saved = api.APP_PASSWORD
        api._login_failures.clear()

    def tearDown(self):
        api.APP_PASSWORD = self.saved
        api._login_failures.clear()

    def client(self):
        return TestClient(api.app, follow_redirects=False)

    def test_off_by_default_so_local_use_is_unchanged(self):
        api.APP_PASSWORD = ""
        self.assertEqual(self.client().get("/api/auth/status").json(), {"enabled": False, "signed_in": True})

    def test_protected_when_a_password_is_set(self):
        api.APP_PASSWORD = "correct horse"
        c = self.client()
        page = c.get("/"); self.assertEqual((page.status_code, page.headers["location"]), (302, "/login"))
        for method, path in (("get", "/api/portfolio"), ("post", "/api/portfolio"), ("post", "/agent/recommend"), ("delete", "/api/portfolio/1"), ("post", "/api/transactions")):
            r = getattr(c, method)(path); self.assertEqual(r.status_code, 401, path); self.assertEqual(r.json()["message"], "Please sign in.")
        self.assertEqual(c.get("/health").status_code, 200); self.assertEqual(c.get("/login").status_code, 200)
        self.assertEqual(c.get("/api/auth/status").json(), {"enabled": True, "signed_in": False})

    def test_login_flow(self):
        api.APP_PASSWORD = "correct horse"
        c = self.client()
        bad = c.post("/login", content="password=nope", headers=FORM); self.assertEqual(bad.status_code, 401); self.assertNotIn("smp_session", bad.headers.get("set-cookie", ""))
        good = c.post("/login", content="password=correct+horse", headers=FORM)
        self.assertEqual((good.status_code, good.headers["location"]), (303, "/"))
        cookie = good.headers["set-cookie"].lower(); self.assertIn("httponly", cookie); self.assertIn("samesite=lax", cookie)
        self.assertEqual(c.get("/api/auth/status").json(), {"enabled": True, "signed_in": True})
        self.assertEqual(c.get("/logout").status_code, 303); self.assertEqual(c.get("/api/auth/status").json()["signed_in"], False)

    def test_forged_expired_and_garbage_cookies_are_refused(self):
        api.APP_PASSWORD = "correct horse"
        token = api.make_session_token(); expires, signature = token.split(".")
        past = int(time.time()) - 5
        for bad in (f"{expires}.{'0' * 64}", f"{int(expires) + 99999}.{signature}", f"{past}.{api._sign(past)}", "abc", ""):
            c = self.client(); c.cookies.set("smp_session", bad)
            self.assertEqual(c.get("/api/portfolio").status_code, 401, bad)
        self.assertTrue(api.valid_session_token(token))

    def test_a_changed_password_invalidates_old_sessions(self):
        api.APP_PASSWORD = "first"; token = api.make_session_token()
        api.APP_PASSWORD = "second"; self.assertFalse(api.valid_session_token(token))

    def test_repeated_wrong_passwords_are_throttled(self):
        api.APP_PASSWORD = "correct horse"
        c = self.client()
        codes = [c.post("/login", content="password=x", headers=FORM).status_code for _ in range(7)]
        self.assertEqual(codes, [401] * 5 + [429] * 2)
        self.assertEqual(c.post("/login", content="password=correct+horse", headers=FORM).status_code, 429)   # even the right one waits


class Validation(unittest.TestCase):
    def setUp(self):
        api.APP_PASSWORD = ""
        self.c = TestClient(api.app)

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

    def test_ai_endpoints_are_rate_limited_per_client(self):
        saved_limit = api.AGENT_RATE_LIMIT; api.AGENT_RATE_LIMIT = 3; api._agent_hits.clear()
        saved = agent.client; agent.client = None
        try:
            codes = [self.c.post("/agent/recommend", json={"query": "hi"}).status_code for _ in range(5)]
            self.assertEqual(codes, [503, 503, 503, 429, 429])
            other = self.c.post("/agent/recommend", json={"query": "hi"}, headers={"x-forwarded-for": "9.9.9.9"}); self.assertEqual(other.status_code, 503)
        finally:
            api.AGENT_RATE_LIMIT = saved_limit; api._agent_hits.clear(); agent.client = saved

    def test_dashboard_page_has_the_features(self):
        html = self.c.get("/").text
        for needle in ('id="page-ipos"', 'id="fss-peers"', 'id="sent-card"', 'id="pf-perf-card"', 'id="tx-form-card"', "runPfReview('holdings')", "/agent/recommend/stream"):
            self.assertIn(needle, html)
        self.assertNotIn("pfRisk", html)


if __name__ == "__main__":
    unittest.main()
