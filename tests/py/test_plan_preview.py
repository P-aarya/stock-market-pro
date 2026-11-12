"""An administrator looking at the app as a plan ("View plan"): the server then applies that plan's rules to the administrator's own
requests, so what they see is what an account on that plan sees. It is a signed cookie for that one administrator: it changes no
account and nothing in the database, can't be started by anyone else, and lapses on its own. No database: market data is stubbed,
accounts live in the in-memory store."""
import html as html_lib
import json
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import _path  # noqa: F401
from _accounts import AccountsTestCase, FULL_TIER, add_user, signed_in_client
import agent
import api
import auth
import company_info
import ipo_data
import learn
import peers
import support
import tiers
from test_tiers import F1, F2, REAL, ROWS, SAMPLE, TABLE

FETCH = {"x-requested-with": "fetch"}
GOOD = "correct horse battery"


def meta(page, name):
    """The content of one <meta name="..."> the server wrote into the dashboard, or None when there isn't one."""
    marker = f'<meta name="{name}" content="'
    return html_lib.unescape(page.split(marker)[1].split('">')[0]) if marker in page else None


class PreviewTestCase(AccountsTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(tiers.TIERS, {**REAL, FULL_TIER: tiers.TIERS[FULL_TIER]}, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)
        mock.patch.object(api, "query", return_value=[]).start()
        mock.patch.object(api, "_stock_directory", return_value=ROWS).start()
        mock.patch("urllib.request.urlopen", side_effect=OSError("offline")).start()
        mock.patch.object(api, "yf", mock.MagicMock()).start()
        mock.patch.object(agent, "client", None).start()
        mock.patch.object(api.portfolio, "delete_holding", return_value={}).start()
        mock.patch.object(ipo_data, "get_ipos", side_effect=ipo_data.IPOUnavailable("offline")).start()
        mock.patch.object(learn, "listing", return_value={"items": []}).start()
        mock.patch.object(support, "my_tickets", return_value=[]).start()
        mock.patch.object(peers, "suggest_peers", return_value={"target": "AAPL", "source": "ai", "peers": []}).start()
        mock.patch.object(company_info, "insider_deals", return_value={"deals": []}).start()
        mock.patch.object(company_info, "filings", return_value={"filings": []}).start()

    def admin(self, preview=None, user_id=1, own_plan=FULL_TIER):
        """A signed-in administrator, looking at the app as `preview` when given."""
        client = signed_in_client(user_id, is_admin=True, tier=own_plan, raise_server_exceptions=False)
        if preview is not None:
            client.cookies.set(api.PREVIEW_COOKIE, auth.make_preview_token(user_id, preview))
        return client

    def reached(self, client, feature):
        method, path = SAMPLE[feature]
        return client.request(method, path, json={"query": "hi"} if method == "POST" else None).status_code != 403


class WhatAPreviewShows(PreviewTestCase, unittest.TestCase):
    def test_an_administrator_previewing_a_plan_reaches_exactly_what_that_plan_does(self):
        for level in range(1, 7):
            client = self.admin(preview=level)
            for feature in SAMPLE:
                self.assertEqual(self.reached(client, feature), feature in TABLE[level][2], f"{TABLE[level][0]}: {feature}")

    def test_it_is_the_previewed_plan_that_counts_not_the_administrators_own(self):
        for own in (1, 6, FULL_TIER):                                       # the same view whatever plan the administrator's own account is on
            client = self.admin(preview=2, own_plan=own)
            self.assertEqual(self.reached(client, "market"), True)
            self.assertEqual(self.reached(client, "learn"), False)

    def test_without_a_preview_an_administrator_still_reaches_everything(self):
        client = self.admin()
        for feature in SAMPLE:
            self.assertTrue(self.reached(client, feature), feature)

    def test_the_refusal_names_the_plan_being_previewed_and_the_plan_that_has_the_feature(self):
        r = self.admin(preview=1).get("/api/learn")
        self.assertEqual(r.status_code, 403)
        body = r.json()
        self.assertEqual(body["code"], "upgrade_required")
        self.assertIn("isn't included in the Free plan", body["message"]); self.assertEqual(body["required_name"], "AI Interaction")

    def test_a_route_nobody_has_classified_is_refused_to_a_previewing_administrator_like_to_anyone_else(self):
        with mock.patch.object(tiers, "feature_for", return_value=tiers.UNCLASSIFIED):
            self.assertEqual(self.admin(preview=3).get("/api/market/summary").status_code, 403)
            self.assertNotEqual(self.admin().get("/api/market/summary").status_code, 403)

    def test_the_admin_pages_and_the_way_back_stay_open_whatever_plan_is_being_previewed(self):
        client = self.admin(preview=1)
        self.assertEqual(client.get("/admin").status_code, 200)
        self.assertEqual(client.get("/api/admin/preview").status_code, 200)
        self.assertEqual(client.post("/api/admin/preview", json={"plan": None}, headers=FETCH).status_code, 200)
        self.assertEqual(self.admin(preview=1).get("/settings").status_code, 200)               # and the pages every account has
        self.assertEqual(self.admin(preview=1).get("/api/profile").status_code, 200)

    def test_the_page_is_the_previewed_plans_own_with_no_admin_link(self):
        page = self.admin(preview=2).get("/").text
        self.assertEqual(meta(page, "smp-plan"), "Basic")
        self.assertEqual(set(meta(page, "smp-features").split(",")), F2)
        self.assertEqual(set(json.loads(meta(page, "smp-locks"))), set(tiers.FEATURES) - F2)    # exactly what a Basic account sees padlocked
        self.assertEqual(meta(page, "smp-user-admin"), "0")                                    # a Basic account has no Admin link
        self.assertEqual(meta(page, "smp-preview"), "Basic"); self.assertEqual(meta(page, "smp-preview-level"), "2")
        plans = json.loads(meta(page, "smp-plans"))
        self.assertEqual([p["name"] for p in plans if p["level"] in REAL], [t[0] for t in TABLE.values()])

    def test_the_free_plan_preview_padlocks_everything_but_the_home_tab_and_portfolio(self):
        page = self.admin(preview=1).get("/").text
        self.assertEqual(set(meta(page, "smp-features").split(",")), F1)
        self.assertEqual(set(json.loads(meta(page, "smp-locks"))), set(tiers.FEATURES) - F1)

    def test_with_no_preview_the_page_is_the_administrators_and_carries_no_preview_information(self):
        page = self.admin().get("/").text
        self.assertEqual(meta(page, "smp-plan"), "Administrator"); self.assertEqual(meta(page, "smp-user-admin"), "1")
        self.assertEqual(json.loads(meta(page, "smp-locks")), {})
        for name in ("smp-preview", "smp-preview-level", "smp-plans"):
            self.assertIsNone(meta(page, name), name)

    def test_the_status_endpoint_and_the_profile_page_show_the_previewed_plan(self):
        client = self.admin(preview=2)
        status = client.get("/api/auth/status").json()
        self.assertEqual((status["plan"], status["preview"], status["admin"]), ({"level": 2, "name": "Basic", "price": 9.99}, {"level": 2, "name": "Basic", "price": 9.99}, True))
        self.assertEqual(set(status["features"]), F2)
        profile = client.get("/api/profile").json()
        self.assertEqual((profile["plan"]["name"], profile["admin"], profile["preview"]["name"]), ("Basic", False, "Basic"))     # the page a Basic account gets
        self.assertEqual({f["key"] for f in profile["included"]}, F2)
        plain = self.admin().get("/api/profile").json()
        self.assertEqual((plain["plan"]["name"], plain["admin"], plain["preview"]), ("Administrator", True, None))
        self.assertIsNone(self.admin().get("/api/auth/status").json().get("preview"))

    def test_looking_at_a_plan_changes_nothing_in_the_account(self):
        client = self.admin(preview=1)
        before = dict(auth.store.users[1])
        client.get("/"); client.get("/api/profile"); client.post("/api/admin/preview", json={"plan": 3}, headers=FETCH)
        self.assertEqual(auth.store.users[1], before)
        self.assertEqual((before["tier"], before["is_admin"], before["status"]), (FULL_TIER, True, "approved"))


class WhoCanPreview(PreviewTestCase, unittest.TestCase):
    def member(self, user_id, level, cookie_level=None):
        client = signed_in_client(user_id, is_admin=False, tier=level, raise_server_exceptions=False)
        if cookie_level is not None:
            client.cookies.set(api.PREVIEW_COOKIE, auth.make_preview_token(user_id, cookie_level))
        return client

    def test_an_ordinary_account_cannot_start_a_preview(self):
        client = self.member(5, 1)
        for headers in ({}, FETCH):
            self.assertEqual(client.post("/api/admin/preview", json={"plan": 6}, headers=headers).status_code, 403)
        self.assertEqual(client.get("/api/admin/preview").status_code, 403)
        self.assertNotIn(api.PREVIEW_COOKIE, client.cookies)

    def test_a_preview_cookie_gives_an_ordinary_account_nothing(self):
        client = self.member(5, 1, cookie_level=6)                              # a perfectly signed cookie for the right account, but not an administrator
        self.assertEqual(client.get("/api/learn").status_code, 403)
        self.assertEqual(client.get("/api/auth/status").json()["plan"]["name"], "Free")
        self.assertIsNone(client.get("/api/auth/status").json().get("preview"))
        self.assertEqual(client.get("/api/profile").json()["admin"], False)
        self.assertIsNone(meta(client.get("/").text, "smp-preview"))

    def test_an_administrator_who_loses_admin_rights_loses_the_preview_with_them(self):
        client = self.admin(preview=6, own_plan=1)
        self.assertEqual(self.reached(client, "pre_ipo"), True)
        auth.store.users[1]["is_admin"] = False; auth.access_cache.clear()
        self.assertEqual(self.reached(client, "pre_ipo"), False)                 # back to being a Free account, with no preview upgrade
        self.assertIsNone(client.get("/api/auth/status").json().get("preview"))

    def test_a_visitor_with_no_account_cannot_use_one(self):
        client = TestClient(api.app, follow_redirects=False, raise_server_exceptions=False)
        client.cookies.set(api.PREVIEW_COOKIE, auth.make_preview_token(1, 6))
        self.assertEqual(client.post("/api/admin/preview", json={"plan": 6}, headers=FETCH).status_code, 401)
        self.assertEqual(client.get("/api/learn").status_code, 401)


class TheCookie(PreviewTestCase, unittest.TestCase):
    def ignored(self, token):
        """True when a preview cookie holding `token` leaves an administrator with full access (that is, it was not honoured)."""
        client = self.admin()
        client.cookies.set(api.PREVIEW_COOKIE, token)
        return self.reached(client, "pre_ipo") and self.reached(client, "learn")

    def test_a_cookie_made_for_another_account_is_ignored(self):
        add_user("other@example.com", "approved", True, 2)
        self.assertTrue(self.ignored(auth.make_preview_token(2, 1)))

    def test_an_expired_cookie_is_ignored_and_a_fresh_one_is_not(self):
        self.assertTrue(self.ignored(auth.make_preview_token(1, 1, now=time.time() - (auth.PREVIEW_HOURS * 3600 + 5))))
        self.assertFalse(self.ignored(auth.make_preview_token(1, 1, now=time.time() - (auth.PREVIEW_HOURS * 3600 - 60))))
        self.assertEqual(auth.PREVIEW_HOURS, 8)

    def test_a_tampered_or_garbled_cookie_is_ignored(self):
        good = auth.make_preview_token(1, 1)
        version, owner, level, expires, signature = good.split(".")
        for bad in (f"{version}.{owner}.6.{expires}.{signature}",               # a bigger plan, same signature
                    f"{version}.{owner}.{level}.{int(expires) + 99999}.{signature}",
                    f"{version}.{owner}.{level}.{expires}.{'0' * 64}", "", "garbage", "p1.1.1", good + ".extra", "p1.x.y.z.w"):
            self.assertTrue(self.ignored(bad), bad)

    def test_a_session_cookie_or_a_restore_token_is_not_a_preview_and_the_reverse(self):
        self.assertTrue(self.ignored(auth.make_token(1)))
        self.assertTrue(self.ignored(auth.make_restore_token(1)))
        signed_in_client(1, is_admin=True)                                      # make the account exist
        fresh = TestClient(api.app, follow_redirects=False, raise_server_exceptions=False)
        fresh.cookies.set(api.SESSION_COOKIE, auth.make_preview_token(1, 1))    # a preview cookie can't be used to sign in
        self.assertEqual(fresh.get("/api/learn").status_code, 401)

    def test_a_plan_that_does_not_exist_is_ignored(self):
        for level in (0, 7, -1, 99999):
            with mock.patch.dict(tiers.TIERS, {n: t for n, t in tiers.TIERS.items() if n != FULL_TIER}, clear=True):
                self.assertTrue(self.ignored(auth.make_preview_token(1, level)), level)

    def test_the_token_reads_back_only_for_its_own_account_while_it_lasts(self):
        token = auth.make_preview_token(7, 3, now=1000)
        self.assertEqual(auth.read_preview_token(token, 7, now=1000 + 60), 3)
        self.assertIsNone(auth.read_preview_token(token, 8, now=1000 + 60))
        self.assertIsNone(auth.read_preview_token(token, 7, now=1000 + auth.PREVIEW_HOURS * 3600))
        self.assertIsNone(auth.read_preview_token(None, 7)); self.assertIsNone(auth.read_preview_token(b"\xff", 7))


class StartingAndStopping(PreviewTestCase, unittest.TestCase):
    def test_the_change_must_come_from_the_admin_page_itself(self):
        client = self.admin()
        self.assertEqual(client.post("/api/admin/preview", json={"plan": 1}).status_code, 403)
        self.assertNotIn(api.PREVIEW_COOKIE, client.cookies)

    def test_choosing_a_plan_sets_a_private_cookie_that_lasts_only_as_long_as_the_browser_stays_open(self):
        client = self.admin()
        r = client.post("/api/admin/preview", json={"plan": 3}, headers=FETCH)
        self.assertEqual((r.status_code, r.json()), (200, {"status": "success", "current": {"level": 3, "name": "AI Stock Finder", "price": 19.99}}))
        cookie = r.headers["set-cookie"].lower()
        self.assertIn(f"{api.PREVIEW_COOKIE}=", cookie)
        self.assertIn("httponly", cookie); self.assertIn("samesite=lax", cookie)
        self.assertNotIn("max-age", cookie); self.assertNotIn("expires", cookie)            # a session cookie
        self.assertEqual(auth.read_preview_token(client.cookies.get(api.PREVIEW_COOKIE), 1), 3)
        self.assertEqual(client.get("/api/auth/status").json()["plan"]["name"], "AI Stock Finder")      # takes effect on the very next request

    def test_the_cookie_is_marked_secure_over_https(self):
        r = self.admin().post("/api/admin/preview", json={"plan": 3}, headers={**FETCH, "x-forwarded-proto": "https"})
        self.assertIn("secure", r.headers["set-cookie"].lower())

    def test_switching_from_one_plan_to_another_and_back_to_administrator(self):
        client = self.admin()
        for level in (1, 4, 6):
            client.post("/api/admin/preview", json={"plan": level}, headers=FETCH)
            self.assertEqual(client.get("/api/auth/status").json()["plan"]["level"], level)
        r = client.post("/api/admin/preview", json={"plan": None}, headers=FETCH)
        self.assertEqual(r.json(), {"status": "success", "current": None})
        self.assertIn(f"{api.PREVIEW_COOKIE}=", r.headers["set-cookie"])               # told to delete it
        self.assertNotIn(api.PREVIEW_COOKIE, client.cookies)
        again = client.get("/api/auth/status").json()
        self.assertEqual(set(again["features"]), set(tiers.FEATURES)); self.assertNotIn("preview", again)
        self.assertTrue(self.reached(client, "pre_ipo"))

    def test_a_plan_that_does_not_exist_is_refused_and_nothing_is_set(self):
        for body in ({"plan": 0}, {"plan": 7}, {"plan": -2}, {"plan": 500}):
            client = self.admin()
            r = client.post("/api/admin/preview", json=body, headers=FETCH)
            self.assertEqual(r.status_code, 400, body); self.assertNotIn(api.PREVIEW_COOKIE, client.cookies)
        for body in ({"plan": "premier"}, {"plan": [1]}, {"plan": {"a": 1}}):
            self.assertEqual(self.admin().post("/api/admin/preview", json=body, headers=FETCH).status_code, 422, body)

    def test_the_list_names_every_plan_with_what_it_includes_and_says_which_is_being_previewed(self):
        client = self.admin()
        first = client.get("/api/admin/preview")
        self.assertEqual(first.headers["cache-control"], "private, no-store")
        data = first.json()
        self.assertIsNone(data["current"])
        plans = [p for p in data["plans"] if p["level"] in REAL]
        self.assertEqual([(p["name"], p["price"]) for p in plans], [(name, price) for name, price, _ in TABLE.values()])
        for p in plans:
            self.assertEqual({f["key"] for f in p["included"]}, TABLE[p["level"]][2], p["name"])
            self.assertTrue(all(f["label"] for f in p["included"]))
        client.post("/api/admin/preview", json={"plan": 5}, headers=FETCH)
        self.assertEqual(client.get("/api/admin/preview").json()["current"], {"level": 5, "name": "Deluxe", "price": 39.99})

    def test_signing_out_ends_the_preview(self):
        client = TestClient(api.app, follow_redirects=False)
        client.cookies.set(api.PREVIEW_COOKIE, auth.make_preview_token(1, 2))
        r = client.get("/logout")
        cookies = r.headers.get_list("set-cookie")
        self.assertTrue(any(c.startswith(f"{api.PREVIEW_COOKIE}=") and "Max-Age=0" in c for c in cookies), cookies)
        self.assertTrue(any(c.startswith(f"{api.SESSION_COOKIE}=") for c in cookies))

    def test_signing_in_again_starts_as_the_administrator_not_as_a_plan_left_over(self):
        user = auth.register("boss@example.com", GOOD, status="approved")
        auth.store.users[user["id"]]["is_admin"] = True
        client = TestClient(api.app, follow_redirects=False)
        client.cookies.set(api.PREVIEW_COOKIE, auth.make_preview_token(user["id"], 1))
        r = client.post("/login", data={"email": "boss@example.com", "password": GOOD})
        cookies = r.headers.get_list("set-cookie")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(any(c.startswith(f"{api.SESSION_COOKIE}=") and not c.startswith(f"{api.SESSION_COOKIE}=;") for c in cookies), cookies)
        self.assertTrue(any(c.startswith(f"{api.PREVIEW_COOKIE}=") and "Max-Age=0" in c for c in cookies), cookies)


if __name__ == "__main__":
    unittest.main()
