"""The profile page's data: who the person is, when they joined, their plan, what it includes, and every plan with its perks.
No database: accounts live in the in-memory store."""
import unittest
from unittest import mock

import _path  # noqa: F401
from _accounts import AccountsTestCase, FULL_TIER, add_user, signed_in_client
import api
import auth
import tiers

REAL = {n: t for n, t in tiers.TIERS.items() if n != FULL_TIER}
ORDER = list(tiers.FEATURES)                       # the order of the plan table
FEATURES_OF = {
    1: {"initial_screen", "portfolio_view"},
    2: {"initial_screen", "portfolio_view", "portfolio_mgt"},
    3: {"initial_screen", "portfolio_view", "portfolio_mgt", "stock_prediction", "ai_stock_finder"},
    4: {"initial_screen", "portfolio_view", "portfolio_mgt", "stock_prediction", "ai_stock_finder", "ai_interaction"},
    5: {"initial_screen", "portfolio_view", "portfolio_mgt", "stock_prediction", "ai_stock_finder", "ai_interaction", "commodities"},
    6: set(tiers.FEATURES),
}


class Catalog(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(tiers.TIERS, REAL, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.plans = tiers.catalog()

    def test_every_plan_is_listed_cheapest_first_with_its_name_and_price(self):
        self.assertEqual([(p["level"], p["name"], p["price"]) for p in self.plans],
                         [(1, "Free", 0.0), (2, "Basic", 9.99), (3, "AI Stock Finder", 19.99), (4, "AI Interaction", 29.99), (5, "Deluxe", 39.99), (6, "Premier", 49.99)])
        self.assertEqual([p["account"] for p in self.plans], [False, True, True, True, True, True])

    def test_each_plan_says_which_plan_it_builds_on(self):
        self.assertEqual([p["includes"] for p in self.plans], [None, "Free", "Basic", "AI Stock Finder", "AI Interaction", "Deluxe"])

    def test_each_plan_lists_what_it_adds_and_the_total_matches_the_plan_table(self):
        adds = {p["level"]: [f["key"] for f in p["adds"]] for p in self.plans}
        self.assertEqual(adds[1], ["initial_screen", "portfolio_view"])
        self.assertEqual(adds[2], ["initial_screen", "portfolio_mgt"])          # an account widens the initial screen to the whole dashboard
        self.assertEqual(adds[3], ["stock_prediction", "ai_stock_finder"]); self.assertEqual(adds[4], ["ai_interaction"])
        self.assertEqual(adds[5], ["commodities"]); self.assertEqual(adds[6], ["pre_ipo"])
        for level, features in FEATURES_OF.items():                             # what each plan can use in all
            self.assertEqual(set().union(*(set(adds[n]) for n in range(1, level + 1))), features, level)

    def test_every_feature_has_a_label_and_a_description_and_free_has_its_own_wording(self):
        self.assertEqual(set(tiers.DESCRIPTIONS), set(tiers.FEATURES))
        for plan in self.plans:
            for f in plan["adds"]:
                self.assertEqual(f["label"], tiers.FEATURES[f["key"]]); self.assertTrue(f["description"], f)
        free = {f["key"]: f["description"] for f in self.plans[0]["adds"]}
        basic = {f["key"]: f["description"] for f in self.plans[1]["adds"]}
        self.assertIn("No account needed", free["initial_screen"]); self.assertIn("read-only", free["portfolio_view"])
        self.assertIn("full dashboard", basic["initial_screen"]); self.assertNotEqual(free["initial_screen"], basic["initial_screen"])

    def test_feature_list_follows_the_plan_table_order(self):
        self.assertEqual([f["key"] for f in tiers.feature_list({"pre_ipo", "initial_screen", "commodities"})], ["initial_screen", "commodities", "pre_ipo"])
        self.assertEqual(tiers.feature_list(set()), [])


class ProfileEndpoint(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(tiers.TIERS, REAL, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)

    def profile(self, user_id=1, tier=2, admin=False):
        c = signed_in_client(user_id, is_admin=admin, tier=tier, raise_server_exceptions=False)
        return c, c.get("/api/profile")

    def test_it_says_who_they_are_when_they_joined_and_which_plan_they_are_on(self):
        _, r = self.profile(1, 2)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual((d["status"], d["email"], d["admin"]), ("success", "user1@example.com", False))
        self.assertEqual(d["joined_at"], 1_700_000_001)                          # the store's created_at for this account
        self.assertEqual(d["plan"], {"level": 2, "name": "Basic", "price": 9.99})

    def test_every_plan_sees_its_own_name_price_and_features(self):
        for level, features in FEATURES_OF.items():
            if level == 1:
                continue
            d = self.profile(10 + level, level)[1].json()
            self.assertEqual(d["plan"]["level"], level, level)
            self.assertEqual([f["key"] for f in d["included"]], [k for k in ORDER if k in features], level)
            self.assertTrue(all(f["label"] and f["description"] for f in d["included"]))

    def test_an_administrator_is_shown_as_one_with_everything(self):
        d = self.profile(2, 2, admin=True)[1].json()
        self.assertEqual((d["admin"], d["plan"]), (True, {"level": None, "name": "Administrator", "price": None}))
        self.assertEqual([f["key"] for f in d["included"]], ORDER)

    def test_every_plan_is_included_for_the_comparison(self):
        d = self.profile()[1].json()
        self.assertEqual(d["plans"], tiers.catalog()); self.assertEqual(len(d["plans"]), 6)
        self.assertEqual([p["name"] for p in d["plans"]][-1], "Premier")

    def test_the_upgrade_address_is_only_sent_when_one_is_set_up(self):
        self.assertIsNone(self.profile()[1].json()["upgrade_contact"])
        with mock.patch.object(api, "UPGRADE_CONTACT", "help@example.com"):
            self.assertEqual(self.profile()[1].json()["upgrade_contact"], "help@example.com")

    def test_only_a_plain_email_address_is_accepted_as_the_upgrade_address(self):
        for good in ("help@example.com", "a.b+c@sub.example.co"):
            self.assertEqual(api._plain_email(good), good)
        for bad in ("", "nobody", "a@b", "a@b.co?bcc=x@y.zz", "a@b.co&x=1", "a b@c.de", "a@b.co\nBcc: x@y.zz", "<a@b.co>", "mailto:a@b.co"):
            self.assertEqual(api._plain_email(bad), "", repr(bad))

    def test_nothing_secret_or_about_anyone_else_is_in_the_answer(self):
        add_user("other@example.com", user_id=50)
        text = self.profile(1, 2)[1].text
        for leaked in ("password", "hash", "other@example.com", "is_admin", "reviewed", "last_login"):
            self.assertNotIn(leaked, text, leaked)

    def test_it_is_never_stored_by_a_browser_or_a_shared_cache(self):
        self.assertEqual(self.profile()[1].headers["cache-control"], "private, no-store")

    def test_an_account_without_a_recorded_join_date_still_gets_its_profile(self):
        add_user("old@example.com", user_id=7, tier=2)
        auth.store.users[7]["created_at"] = None
        c = signed_in_client(7, create=False)
        r = c.get("/api/profile")
        self.assertEqual((r.status_code, r.json()["joined_at"]), (200, None))

    def test_the_account_is_read_fresh_so_a_plan_change_shows_at_once(self):
        c, r = self.profile(1, 2)
        self.assertEqual(r.json()["plan"]["name"], "Basic")
        auth.store.users[1]["tier"] = 4
        self.assertEqual(c.get("/api/profile").json()["plan"]["name"], "AI Interaction")

    def test_visitors_and_unapproved_accounts_get_nothing(self):
        from fastapi.testclient import TestClient
        self.assertEqual(TestClient(api.app, follow_redirects=False).get("/api/profile").status_code, 401)
        add_user("wait@example.com", status="pending", user_id=11)
        c = TestClient(api.app, follow_redirects=False)
        c.cookies.set(api.SESSION_COOKIE, auth.make_token(11))
        self.assertEqual(c.get("/api/profile").status_code, 401)

    def test_a_database_failure_is_a_plain_503_without_details(self):
        c, _ = self.profile()
        with mock.patch.object(auth, "get_user", side_effect=RuntimeError("secret internals")):
            r = c.get("/api/profile")
        self.assertEqual(r.status_code, 503); self.assertNotIn("secret", r.text); self.assertIn("try again", r.json()["message"])

    def test_an_account_removed_a_moment_ago_is_asked_to_sign_in_again(self):
        c, _ = self.profile()
        with mock.patch.object(auth, "get_user", return_value=None):
            self.assertEqual(c.get("/api/profile").status_code, 401)

    def test_the_profile_is_open_to_every_plan_not_a_plan_feature(self):
        self.assertEqual(tiers.feature_for("GET", "/api/profile"), tiers.NO_FEATURE)
        self.assertEqual(tiers.feature_for("POST", "/api/profile"), tiers.UNCLASSIFIED)          # reading only

    def test_the_account_record_carries_the_join_date_as_whole_seconds(self):
        self.profile(1, 2)
        self.assertEqual(auth.get_user(1)["created_at"], 1_700_000_001)
        self.assertEqual(auth.MySQLStore._user({"id": 1, "email": "a@b.co", "created_at": 1_700_000_001.0})["created_at"], 1_700_000_001)
        self.assertIsNone(auth.MySQLStore._user({"id": 1, "email": "a@b.co", "created_at": None})["created_at"])


if __name__ == "__main__":
    unittest.main()
