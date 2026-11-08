"""Subscription tiers: what each plan includes (it must match the plan table exactly), that the server (not just the page)
enforces it, and what a visitor without an account can reach. No database: market data is stubbed, accounts live in the
in-memory store."""
import html as html_lib
import json
import re
import unittest
from unittest import mock

from fastapi.routing import APIRoute

import _path  # noqa: F401
from _accounts import AccountsTestCase, FULL_TIER, add_user, signed_in_client
import agent
import api
import auth
import ipo_data
import tiers

# the real plans, without the "everything" stand-in the other tests sign in with
REAL = {n: t for n, t in tiers.TIERS.items() if n != FULL_TIER}

ROWS = [{"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Technology", "current_price": 190.5, "price_change_pct": 1.25,
         "market_cap": 3e12, "_low": "apple inc.", "_plain": "appleinc"},
        {"ticker": "KO", "company_name": "Coca-Cola", "sector": "Consumer Staples", "current_price": 60.0, "price_change_pct": -0.4,
         "market_cap": 2.6e11, "_low": "coca-cola", "_plain": "cocacola"}]
FULL_ROW = {"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Technology", "industry": "Consumer Electronics", "current_price": 190.5,
            "price_change": 2.3, "price_change_pct": 1.25, "pe_ratio": 31.2, "eps": 6.1, "rsi_14": 55.0, "macd": 0.4, "beta": 1.2,
            "dividend_yield": 0.5, "target_price": 210.0, "analyst_rating": "buy", "upside_pct": 10.2, "institutional_ownership": 0.6}

# The plan table, column by column: what each plan can use in total (read off the table, not off tiers.py).
TABLE = {
    1: ("Free",            0.00,  {"initial_screen", "portfolio_view"}),
    2: ("Basic",           9.99,  {"initial_screen", "portfolio_view", "portfolio_mgt"}),
    3: ("AI Stock Finder", 19.99, {"initial_screen", "portfolio_view", "portfolio_mgt", "stock_prediction", "ai_stock_finder"}),
    4: ("AI Interaction",  29.99, {"initial_screen", "portfolio_view", "portfolio_mgt", "stock_prediction", "ai_stock_finder", "ai_interaction"}),
    5: ("Deluxe",          39.99, {"initial_screen", "portfolio_view", "portfolio_mgt", "stock_prediction", "ai_stock_finder", "ai_interaction", "commodities"}),
    6: ("Premier",         49.99, {"initial_screen", "portfolio_view", "portfolio_mgt", "stock_prediction", "ai_stock_finder", "ai_interaction", "commodities", "pre_ipo"}),
}

# one route per feature, used to check a plan reaches (or is refused) each
SAMPLE = {
    "initial_screen":   ("GET", "/api/market/summary"),
    "portfolio_view":   ("GET", "/api/portfolio"),
    "portfolio_mgt":    ("DELETE", "/api/portfolio/1"),
    "stock_prediction": ("GET", "/api/predictions/top"),
    "ai_stock_finder":  ("POST", "/agent/recommend"),
    "ai_interaction":   ("POST", "/agent/recommend/stream"),
    "commodities":      ("GET", "/api/market/commodities"),
    "pre_ipo":          ("GET", "/api/ipos"),
}


class RealPlans(unittest.TestCase):
    """The plans as defined in tiers.py today."""

    def setUp(self):
        patcher = mock.patch.dict(tiers.TIERS, REAL, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_there_are_six_plans_with_the_names_and_prices_of_the_table(self):
        self.assertEqual({n: (t.name, t.price) for n, t in tiers.TIERS.items()}, {n: (name, price) for n, (name, price, _) in TABLE.items()})

    def test_each_plan_can_use_exactly_what_its_column_of_the_table_lists(self):
        for level, (name, _, features) in TABLE.items():
            self.assertEqual(tiers.features_for(level) if level > 1 else tiers.visitor_features(), features, name)

    def test_the_features_are_exactly_the_eight_rows_of_the_table_and_nothing_more(self):
        self.assertEqual(tiers.FEATURES, {"initial_screen": "Initial screen", "portfolio_view": "Portfolio visibility", "portfolio_mgt": "Portfolio management",
                                          "stock_prediction": "Stock prediction", "ai_stock_finder": "AI stock finder", "ai_interaction": "AI interaction",
                                          "commodities": "Commodities", "pre_ipo": "Pre-IPO deals"})
        self.assertEqual(set().union(*(t.adds for t in tiers.TIERS.values())), set(tiers.FEATURES))

    def test_each_plan_adds_only_what_the_table_adds_over_the_plan_before(self):
        added = {n: set(t.adds) for n, t in tiers.TIERS.items()}
        self.assertEqual(added[2], {"portfolio_mgt"}); self.assertEqual(added[3], {"stock_prediction", "ai_stock_finder"})
        self.assertEqual(added[4], {"ai_interaction"}); self.assertEqual(added[5], {"commodities"}); self.assertEqual(added[6], {"pre_ipo"})
        for level in range(2, 7):
            self.assertLess(tiers.features_for(level - 1) if level > 2 else tiers.visitor_features(), tiers.features_for(level))

    def test_free_is_a_visitor_and_every_account_starts_on_basic(self):
        self.assertFalse(tiers.TIERS[1].account); self.assertTrue(all(tiers.TIERS[n].account for n in range(2, 7)))
        self.assertEqual(tiers.account_tier(), 2)
        self.assertEqual((tiers.visitor_plan().name, tiers.describe_visitor()), ("Free", {"level": 1, "name": "Free", "price": 0.0}))

    def test_a_stored_level_never_puts_an_account_below_basic_or_above_the_top_plan(self):
        self.assertEqual(tiers.resolve(7).level, 6)              # a plan this version doesn't know yet counts as the top one it does
        for odd in (None, "x", 0, -3, 1, "1", 1.5 * 0):
            self.assertEqual(tiers.resolve(odd).level, 2, odd)   # unreadable, or the visitor plan: the lowest account plan
        self.assertEqual([tiers.resolve(n).level for n in range(2, 7)], [2, 3, 4, 5, 6]); self.assertEqual(tiers.resolve("4").level, 4)
        self.assertNotIn("portfolio_mgt", tiers.visitor_features())

    def test_what_a_refused_person_is_told(self):
        for feature, plan, price in (("portfolio_mgt", "Basic", 9.99), ("stock_prediction", "AI Stock Finder", 19.99), ("ai_stock_finder", "AI Stock Finder", 19.99),
                                     ("ai_interaction", "AI Interaction", 29.99), ("commodities", "Deluxe", 39.99), ("pre_ipo", "Premier", 49.99), ("initial_screen", "Free", 0.0)):
            info = tiers.upgrade_info(feature)
            self.assertEqual((info["feature"], info["label"], info["required_name"], info["required_price"]), (feature, tiers.FEATURES[feature], plan, price), feature)
        self.assertEqual(tiers.upgrade_info("something-else")["required_tier"], None)

    def test_the_free_list_is_five_fields_per_stock(self):
        self.assertEqual(tiers.public_stock_row(ROWS[0]), ["AAPL", "Apple Inc.", "Technology", 190.5, 1.25])


class EveryRouteIsClassified(unittest.TestCase):
    FRAMEWORK = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

    def test_no_route_is_left_without_a_feature(self):
        """A new endpoint must be given a plan in tiers.py (or it is refused to everyone but administrators)."""
        missing = []
        for route in api.app.routes:
            if not isinstance(route, APIRoute) and not hasattr(route, "methods"):
                continue
            path = route.path
            if path in api._OPEN_PATHS or path in self.FRAMEWORK:
                continue
            sample = re.sub(r"\{[^}]+\}", "x1", path)
            for method in sorted(m for m in (route.methods or ()) if m not in ("HEAD", "OPTIONS")):
                if tiers.feature_for(method, sample) == tiers.UNCLASSIFIED:
                    missing.append(f"{method} {path}")
        self.assertEqual(missing, [], "add these routes to the rules in tiers.py")

    def test_every_feature_in_the_table_is_enforced_by_at_least_one_route(self):
        self.assertEqual({f for _, _, f in tiers._RULES} - {tiers.NO_FEATURE}, set(tiers.FEATURES))

    def test_specific_routes_are_not_swallowed_by_the_general_ones(self):
        cases = {("GET", "/api/stocks/directory"): "initial_screen", ("GET", "/api/stocks/search"): "initial_screen", ("GET", "/api/stocks/batch"): "initial_screen",
                 ("GET", "/api/stocks"): "initial_screen", ("GET", "/api/stocks/AAPL"): "initial_screen", ("GET", "/api/stocks/AAPL/historical"): "initial_screen",
                 ("GET", "/api/stocks/AAPL/price-spikes"): "initial_screen", ("GET", "/api/market/indices"): "initial_screen",
                 ("GET", "/api/market/rsi-distribution"): "initial_screen", ("GET", "/api/earnings/calendar"): "initial_screen",
                 ("GET", "/api/economic-calendar"): "initial_screen", ("GET", "/api/news/world"): "initial_screen", ("PUT", "/api/watchlist"): "initial_screen",
                 ("DELETE", "/api/alerts/3"): "initial_screen", ("GET", "/api/notifications/feed"): "initial_screen", ("GET", "/api/peers/AAPL"): "initial_screen",
                 ("GET", "/api/compare/verdict"): "initial_screen", ("GET", "/api/compare/prices"): "initial_screen", ("GET", "/api/public/stocks"): "initial_screen",
                 ("GET", "/api/ai-predictions"): "stock_prediction", ("GET", "/api/ai-predictions/AAPL"): "stock_prediction", ("GET", "/api/predictions/top"): "stock_prediction",
                 ("GET", "/api/predictions/accuracy"): "stock_prediction", ("GET", "/api/market/signals"): "stock_prediction",
                 ("POST", "/api/screener"): "ai_stock_finder", ("POST", "/agent/recommend"): "ai_stock_finder", ("POST", "/agent/explain-stock"): "ai_stock_finder",
                 ("POST", "/agent/recommend/stream"): "ai_interaction", ("POST", "/agent/portfolio-review"): "ai_interaction",
                 ("GET", "/api/market/commodities"): "commodities", ("GET", "/api/market/macro"): "commodities", ("POST", "/api/stocks/batch_commodity"): "commodities",
                 ("GET", "/api/ipos"): "pre_ipo",
                 ("GET", "/api/portfolio"): "portfolio_view", ("GET", "/api/portfolio/summary"): "portfolio_view", ("GET", "/api/portfolio/performance"): "portfolio_view",
                 ("GET", "/api/portfolio/benchmark"): "portfolio_view", ("GET", "/api/transactions"): "portfolio_view", ("GET", "/api/transactions/summary"): "portfolio_view",
                 ("POST", "/api/portfolio"): "portfolio_mgt", ("PUT", "/api/portfolio/3"): "portfolio_mgt", ("DELETE", "/api/portfolio/3"): "portfolio_mgt",
                 ("POST", "/api/portfolio/add"): "portfolio_mgt", ("POST", "/api/transactions"): "portfolio_mgt", ("PUT", "/api/transactions/3"): "portfolio_mgt",
                 ("DELETE", "/api/transactions/3"): "portfolio_mgt", ("POST", "/api/transactions/add"): "portfolio_mgt",
                 ("GET", "/"): tiers.NO_FEATURE, ("POST", "/api/admin/users/3/status"): tiers.NO_FEATURE}
        for (method, path), feature in cases.items():
            self.assertEqual(tiers.feature_for(method, path), feature, f"{method} {path}")

    def test_an_unknown_route_is_unclassified_and_a_trailing_slash_changes_nothing(self):
        for path in ("/api/brand-new", "/api/market/brand-new", "/api/stocks/AAPL/brand-new", "/api/portfolio/1/brand-new", "/api/ipos/extra"):
            self.assertEqual(tiers.feature_for("GET", path), tiers.UNCLASSIFIED, path)
        self.assertEqual(tiers.feature_for("GET", "/api/market/summary/"), "initial_screen")
        self.assertEqual(tiers.feature_for("get", "/api/market/summary"), "initial_screen")
        self.assertEqual(tiers.feature_for("PATCH", "/api/watchlist"), "initial_screen")          # "*" rules cover any method


class WhatEachPlanReaches(AccountsTestCase, unittest.TestCase):
    def client(self, user_id=1, tier=2, admin=False):
        return signed_in_client(user_id, is_admin=admin, tier=tier, raise_server_exceptions=False)

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(tiers.TIERS, {**REAL, FULL_TIER: tiers.TIERS[FULL_TIER]}, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)
        mock.patch.object(api, "query", return_value=[]).start()
        mock.patch.object(api, "_stock_directory", return_value=ROWS).start()
        # nothing here may reach the internet: a route that is allowed through runs for real
        mock.patch("urllib.request.urlopen", side_effect=OSError("offline")).start()
        mock.patch.object(api, "yf", mock.MagicMock()).start()
        mock.patch.object(agent, "client", None).start()
        mock.patch.object(api.portfolio, "delete_holding", return_value={}).start()
        mock.patch.object(ipo_data, "get_ipos", side_effect=ipo_data.IPOUnavailable("offline")).start()

    def reached(self, client, feature):
        method, path = SAMPLE[feature]
        return client.request(method, path, json={"query": "hi"} if method == "POST" else None).status_code != 403

    def test_every_plan_reaches_exactly_its_own_features(self):
        for level in range(2, 7):
            c = self.client(user_id=level, tier=level)
            for feature in SAMPLE:
                self.assertEqual(self.reached(c, feature), feature in TABLE[level][2], f"{TABLE[level][0]}: {feature}")

    def test_a_refusal_says_which_feature_and_which_plan_has_it(self):
        r = self.client(tier=2).get("/api/ipos")
        self.assertEqual(r.status_code, 403)
        body = r.json()
        self.assertEqual((body["status"], body["code"], body["feature"], body["required_name"]), ("error", "upgrade_required", "pre_ipo", "Premier"))
        self.assertIn("Pre-IPO deals isn't included in the Basic plan", body["message"]); self.assertIn("Premier", body["message"])
        r = self.client(5, 5).get("/api/ipos")                    # Deluxe has everything but the Premier feature
        self.assertEqual(r.status_code, 403); self.assertIn("isn't included in the Deluxe plan", r.json()["message"])
        r = self.client(6, 2).get("/api/market/commodities")
        self.assertIn("It comes with the Deluxe plan", r.json()["message"])

    def test_a_refused_ai_request_is_not_counted_against_the_rate_limit_or_sent_anywhere(self):
        c = self.client(tier=2)
        self.assertEqual([c.post("/agent/recommend", json={"query": "hi"}).status_code for _ in range(3)], [403, 403, 403])
        self.assertEqual([c.post("/agent/recommend/stream", json={"query": "hi"}).status_code for _ in range(3)], [403, 403, 403])
        self.assertEqual(len(api._agent_hits), 0)

    def test_the_ai_finder_asks_one_question_but_a_conversation_needs_ai_interaction(self):
        talk = {"query": "and the second one?", "chat_history": [{"role": "user", "content": "find me dividend stocks"}, {"role": "assistant", "content": "Here are some."}]}
        reply = {"query": "tell me more", "reply_to": "Here are some."}
        finder_only, with_chat = self.client(1, 3), self.client(2, 4)
        self.assertNotEqual(finder_only.post("/agent/recommend", json={"query": "find me dividend stocks", "portfolio_tickers": ["AAPL"]}).status_code, 403)
        for body in (talk, reply):
            r = finder_only.post("/agent/recommend", json=body)
            self.assertEqual(r.status_code, 403, body)
            self.assertEqual((r.json()["code"], r.json()["feature"], r.json()["required_name"]), ("upgrade_required", "ai_interaction", "AI Interaction"))
            self.assertNotEqual(with_chat.post("/agent/recommend", json=body).status_code, 403, body)
        self.assertEqual(finder_only.post("/agent/recommend/stream", json=talk).status_code, 403)
        self.assertEqual(self.client(3, 2).post("/agent/recommend", json=talk).status_code, 403)                # Basic: no finder at all
        self.assertNotEqual(self.client(4, 2, admin=True).post("/agent/recommend", json=talk).status_code, 403)

    def test_the_everything_plan_and_administrators_reach_every_feature(self):
        for client in (self.client(1, FULL_TIER), self.client(2, 2, admin=True)):
            for feature in SAMPLE:
                self.assertTrue(self.reached(client, feature), feature)

    def test_a_route_nobody_has_classified_is_closed_to_everyone_but_administrators(self):
        with mock.patch.object(tiers, "feature_for", return_value=tiers.UNCLASSIFIED):
            self.assertEqual(self.client(1, FULL_TIER).get("/api/market/summary").status_code, 403)
            self.assertNotEqual(self.client(2, 2, admin=True).get("/api/market/summary").status_code, 403)

    def test_a_plan_the_page_cannot_read_falls_back_to_basic(self):
        add_user("a@x.co", user_id=1, tier="garbage")
        c = signed_in_client(1, create=False, raise_server_exceptions=False)
        self.assertNotEqual(c.get("/api/market/summary").status_code, 403)
        self.assertNotEqual(c.delete("/api/portfolio/1").status_code, 403)
        self.assertEqual(c.get("/api/predictions/top").status_code, 403)

    def test_an_account_left_on_the_visitor_plan_is_treated_as_basic_not_locked_out_or_given_less(self):
        c = self.client(5, 1)
        self.assertNotEqual(c.get("/api/market/summary").status_code, 403)
        self.assertNotEqual(c.delete("/api/portfolio/1").status_code, 403)
        self.assertEqual(c.get("/api/auth/status").json()["plan"]["name"], "Basic")

    def test_stock_pages_are_part_of_the_initial_screen_so_every_account_gets_the_whole_row(self):
        with mock.patch.object(api, "query", return_value=[dict(FULL_ROW)]):
            self.assertEqual(set(self.client(tier=2).get("/api/stocks/AAPL").json()), set(FULL_ROW))
            self.assertEqual(set(self.client(2, 2).get("/api/stocks").json()[0]), set(FULL_ROW))

    def test_the_page_is_told_the_plan_and_the_features(self):
        html = self.client(tier=3).get("/").text
        self.assertIn('<meta name="smp-plan" content="AI Stock Finder">', html)
        features = html.split('<meta name="smp-features" content="')[1].split('"')[0].split(",")
        self.assertEqual(set(features), TABLE[3][2])
        locks = json.loads(html_lib.unescape(html.split('<meta name="smp-locks" content="')[1].split('">')[0]))
        self.assertEqual(set(locks), {"ai_interaction", "commodities", "pre_ipo"})              # exactly what is padlocked
        self.assertEqual(locks["pre_ipo"], {"label": "Pre-IPO deals", "plan": "Premier"})
        self.assertEqual(locks["commodities"], {"label": "Commodities", "plan": "Deluxe"})
        admin = self.client(2, 2, admin=True).get("/").text
        self.assertEqual(json.loads(html_lib.unescape(admin.split('<meta name="smp-locks" content="')[1].split('">')[0])), {})
        self.assertIn('<meta name="smp-plan" content="Administrator">', admin)
        self.assertEqual(set(admin.split('<meta name="smp-features" content="')[1].split('"')[0].split(",")), set(tiers.FEATURES))

    def test_the_status_endpoint_reports_the_plan(self):
        body = self.client(tier=2).get("/api/auth/status").json()
        self.assertEqual(body["plan"], {"level": 2, "name": "Basic", "price": 9.99})
        self.assertEqual(set(body["features"]), TABLE[2][2])
        self.assertEqual(self.client(2, 6).get("/api/auth/status").json()["plan"], {"level": 6, "name": "Premier", "price": 49.99})
        admin = self.client(3, 2, admin=True).get("/api/auth/status").json()
        self.assertEqual(set(admin["features"]), set(tiers.FEATURES))

    def test_new_sign_ups_start_on_the_basic_plan(self):
        user = auth.register("new@example.com", "a-long-enough-pass", status="approved")
        self.assertEqual(auth.get_user(user["id"])["tier"], 2)


class Visitors(AccountsTestCase, unittest.TestCase):
    """The Free plan: someone with no account."""

    def setUp(self):
        super().setUp()
        mock.patch.object(api, "_stock_directory", return_value=ROWS).start()
        mock.patch.object(api, "query", return_value=[]).start()
        from fastapi.testclient import TestClient
        self.c = TestClient(api.app, follow_redirects=False)

    def test_the_front_page_is_the_public_page_not_the_dashboard(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/api/public/stocks", r.text)
        self.assertIn('href="/login"', r.text); self.assertIn('href="/register"', r.text)
        for dashboard_only in ("id=\"page-market\"", "/api/portfolio", "/agent/recommend", "id=\"bounty-btn\""):
            self.assertNotIn(dashboard_only, r.text)
        self.assertIn("no-cache", r.headers["cache-control"])
        self.assertIn("Basic plan, $9.99 a month", r.text); self.assertNotIn("<!--PLAN", r.text)       # filled in from tiers.py

    def test_the_free_plan_has_a_read_only_portfolio_page_that_asks_the_server_for_nothing(self):
        text = self.c.get("/").text
        self.assertIn('id="tab-portfolio"', text); self.assertIn('id="view-portfolio"', text)
        portfolio = text.split('id="view-portfolio"')[1].split("</main>")[0]
        for word in ("Portfolio value", "Total invested", "Unrealized P&amp;L", "Shares", "Avg cost", "read-only"):
            self.assertIn(word, portfolio)
        for editing in ("<form", "<input", "<textarea", "fetch(", "Add holding"):
            self.assertNotIn(editing, portfolio, editing)               # visible, not editable: nothing to type into or save
        self.assertNotIn("<!--PLAN", portfolio)

    def test_the_price_list_has_only_ticker_name_sector_price_and_change(self):
        r = self.c.get("/api/public/stocks")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["fields"], ["ticker", "company_name", "sector", "current_price", "price_change_pct"])
        self.assertEqual(body["stocks"], [["AAPL", "Apple Inc.", "Technology", 190.5, 1.25], ["KO", "Coca-Cola", "Consumer Staples", 60.0, -0.4]])
        self.assertNotIn("market_cap", r.text); self.assertNotIn("appleinc", r.text)
        self.assertIn("max-age", r.headers["cache-control"])

    def test_everything_else_still_needs_a_sign_in(self):
        for path in ("/api/stocks", "/api/stocks/AAPL", "/api/stocks/directory", "/api/market/summary", "/api/portfolio", "/api/watchlist",
                     "/api/news/world", "/api/ipos", "/api/admin/users", "/api/alerts", "/api/market/commodities", "/api/predictions/top"):
            r = self.c.get(path)
            self.assertIn(r.status_code, (401, 404, 307, 302), path)
            self.assertNotEqual(r.status_code, 200, path)
        self.assertEqual(self.c.post("/agent/recommend", json={"query": "hi"}).status_code, 401)
        self.assertEqual(self.c.post("/api/portfolio", json={"ticker": "AAPL", "quantity": 1, "avg_buy_price": 1, "buy_date": "2025-01-02"}).status_code, 401)
        r = self.c.get("/dashboard.html")
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/login"))
        self.assertEqual(self.c.get("/admin").status_code, 302)

    def test_a_visitor_cannot_reach_the_price_list_through_look_alike_paths(self):
        for path in ("/api/public/stocks/", "/api/public/stocks/x", "/api/public/", "/api/public/portfolio"):
            self.assertNotEqual(self.c.get(path).status_code, 200, path)

    def test_one_visitor_cannot_hammer_the_price_list(self):
        api._public_hits = auth.RateLimiter(3, 60)
        try:
            codes = [self.c.get("/api/public/stocks").status_code for _ in range(5)]
        finally:
            api._public_hits = auth.RateLimiter(120, 60)
        self.assertEqual(codes, [200, 200, 200, 429, 429])
        self.assertEqual(self.c.get("/api/public/stocks").status_code, 200)       # a different window: allowed again

    def test_a_visitor_who_signs_in_gets_the_dashboard_at_the_same_address(self):
        add_user("me@example.com", user_id=9, tier=2)
        c = signed_in_client(9, tier=2)
        self.assertIn('id="page-market"', c.get("/").text)

    def test_a_blocked_or_pending_account_is_treated_as_a_visitor(self):
        add_user("wait@example.com", status="pending", user_id=11)
        from fastapi.testclient import TestClient
        c = TestClient(api.app, follow_redirects=False)
        c.cookies.set(api.SESSION_COOKIE, auth.make_token(11))
        self.assertNotIn('id="page-market"', c.get("/").text)
        self.assertEqual(c.get("/api/market/summary").status_code, 401)


if __name__ == "__main__":
    unittest.main()
