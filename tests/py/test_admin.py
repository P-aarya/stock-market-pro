"""Access management: new sign-ups wait for approval, administrators approve or block them, and a block cuts a person off fast."""
import threading
import time
import unittest

from fastapi.testclient import TestClient

import _path  # noqa: F401
import api
import auth
from _accounts import AccountsTestCase, add_user, signed_in_client

GOOD = "correct horse battery"
FETCH = {"x-requested-with": "fetch"}


class SignUpAndSignIn(AccountsTestCase, unittest.TestCase):
    def client(self):
        return TestClient(api.app, follow_redirects=False)

    def sign_up(self, email="new@example.com"):
        return self.client().post("/register", data={"email": email, "password": GOOD, "confirm": GOOD})

    def sign_in(self, email="new@example.com", password=GOOD):
        return self.client().post("/login", data={"email": email, "password": password})

    def test_a_new_account_waits_for_approval_and_gets_no_session(self):
        r = self.sign_up()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Waiting for approval", r.text); self.assertIn("new@example.com", r.text)
        self.assertNotIn("set-cookie", r.headers)
        row = auth.store.find_by_email("new@example.com")
        self.assertEqual((row["status"], row["is_admin"]), ("pending", False))

    def test_a_new_account_can_never_make_itself_an_administrator_or_approved(self):
        r = self.client().post("/register", data={"email": "sneaky@example.com", "password": GOOD, "confirm": GOOD, "status": "approved", "is_admin": "1"})
        row = auth.store.find_by_email("sneaky@example.com")
        self.assertEqual((r.status_code, row["status"], row["is_admin"]), (200, "pending", False))

    def test_a_pending_person_cannot_sign_in_even_with_the_right_password(self):
        self.sign_up()
        r = self.sign_in()
        self.assertEqual(r.status_code, 403); self.assertIn("Waiting for approval", r.text); self.assertNotIn("set-cookie", r.headers)

    def test_a_wrong_password_does_not_reveal_whether_an_account_is_pending(self):
        self.sign_up()
        r = self.sign_in(password="not it")
        self.assertEqual(r.status_code, 401); self.assertNotIn("approval", r.text.lower())

    def test_once_approved_they_can_sign_in(self):
        self.sign_up()
        auth.set_user_status(99, auth.store.find_by_email("new@example.com")["id"], "approved")
        r = self.sign_in()
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/")); self.assertIn("smp_session=", r.headers["set-cookie"])

    def test_a_blocked_person_gets_a_clear_refusal_and_no_session(self):
        add_user("blocked@example.com", "blocked")
        auth.store.users[1]["password_hash"] = auth.hash_password(GOOD)
        r = self.sign_in("blocked@example.com")
        self.assertEqual(r.status_code, 403); self.assertIn("No access", r.text); self.assertNotIn("set-cookie", r.headers)

    def test_approval_can_be_switched_off(self):
        api.REQUIRE_APPROVAL = False
        r = self.sign_up()
        self.assertEqual((r.status_code, auth.store.find_by_email("new@example.com")["status"]), (303, "approved")); self.assertIn("smp_session=", r.headers["set-cookie"])

    def test_signing_in_does_not_count_as_a_failed_attempt_for_a_pending_account(self):
        self.sign_up()
        for _ in range(8):
            self.assertEqual(self.sign_in().status_code, 403)       # never throttled: the password was right


class ACookieDoesNotOutliveApproval(AccountsTestCase, unittest.TestCase):
    """A session proves who someone is. Whether they may still come in is checked against the account's status."""

    def test_pending_and_blocked_accounts_are_locked_out_whatever_cookie_they_hold(self):
        for status in ("pending", "blocked"):
            uid = add_user(f"{status}@example.com", status)
            c = signed_in_client(uid, follow_redirects=False)
            self.assertEqual(c.get("/api/portfolio").status_code, 401, status)
            page = c.get("/"); self.assertEqual((page.status_code, page.headers["location"]), (302, "/login"), status)
            self.assertEqual(c.get("/admin").status_code, 302, status)
            st = c.get("/api/auth/status").json(); self.assertEqual((st["signed_in"], st["user"], st["admin"]), (False, None, False), status)

    def test_the_login_page_does_not_loop_for_them_and_clears_the_cookie(self):
        uid = add_user("blocked@example.com", "blocked")
        r = signed_in_client(uid, follow_redirects=False).get("/login")
        self.assertEqual(r.status_code, 200); self.assertIn("smp_session=", r.headers["set-cookie"]); self.assertIn("Max-Age=0", r.headers["set-cookie"])

    def test_blocking_ends_a_session_at_once_on_this_server(self):
        admin = add_user("admin@example.com", is_admin=True)
        uid = add_user("member@example.com")
        member = signed_in_client(uid)
        self.assertEqual(member.get("/api/portfolio").status_code, 200)
        auth.set_user_status(admin, uid, "blocked")
        self.assertEqual(member.get("/api/portfolio").status_code, 401)
        auth.set_user_status(admin, uid, "approved")
        self.assertEqual(member.get("/api/portfolio").status_code, 200)       # and unblocking restores it

    def test_a_change_made_elsewhere_takes_effect_when_the_remembered_status_expires(self):
        clock = [1000.0]
        saved = auth.access_cache.clock
        auth.access_cache.clock = lambda: clock[0]
        try:
            uid = add_user("member@example.com")
            member = signed_in_client(uid)
            self.assertEqual(member.get("/api/portfolio").status_code, 200)
            auth.store.users[uid]["status"] = "blocked"                        # another server, or manage_users.py, blocked them
            clock[0] += auth.ACCESS_TTL - 1
            self.assertEqual(member.get("/api/portfolio").status_code, 200)    # still remembered
            clock[0] += 2
            self.assertEqual(member.get("/api/portfolio").status_code, 401)    # asked again: refused
        finally:
            auth.access_cache.clock = saved

    def test_deleting_an_account_locks_its_sessions_out_too(self):
        uid = add_user("gone@example.com"); c = signed_in_client(uid)
        self.assertEqual(c.get("/api/portfolio").status_code, 200)
        del auth.store.users[uid]; auth.access_cache.invalidate(uid)
        self.assertEqual(c.get("/api/portfolio").status_code, 401)


class AccessCache(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.cache = auth.AccessCache(ttl=10, stale_ok=100, clock=lambda: self.t[0])
        self.calls = 0

    def loader(self, value="v"):
        def load():
            self.calls += 1
            return value
        return load

    def test_remembers_for_the_ttl_then_asks_again(self):
        self.assertEqual(self.cache.get(1, self.loader("a")), "a")
        self.t[0] = 9; self.cache.get(1, self.loader("b")); self.assertEqual(self.calls, 1)
        self.t[0] = 11; self.assertEqual(self.cache.get(1, self.loader("b")), "b"); self.assertEqual(self.calls, 2)

    def test_each_account_has_its_own_entry_and_a_missing_account_is_remembered_too(self):
        self.cache.get(1, self.loader("a")); self.cache.get(2, self.loader("b")); self.assertEqual(self.calls, 2)
        self.cache.get(3, self.loader(None)); self.cache.get(3, self.loader("late")); self.assertEqual(self.calls, 3)
        self.assertIsNone(self.cache.get(3, self.loader("late")))

    def test_invalidate_and_clear(self):
        self.cache.get(1, self.loader("a")); self.cache.get(2, self.loader("a"))
        self.cache.invalidate(1); self.cache.get(1, self.loader("b")); self.cache.get(2, self.loader("b")); self.assertEqual(self.calls, 3)
        self.cache.clear(); self.cache.get(2, self.loader("c")); self.assertEqual(self.calls, 4)

    def test_a_page_load_worth_of_simultaneous_requests_asks_the_database_once(self):
        calls, results = [], []

        def slow():
            calls.append(1); time.sleep(0.05); return "v"
        threads = [threading.Thread(target=lambda: results.append(self.cache.get(7, slow))) for _ in range(30)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual((len(calls), results), (1, ["v"] * 30))

    def test_if_the_database_fails_a_recently_seen_status_is_trusted_but_not_forever(self):
        self.cache.get(1, self.loader("ok"))
        def broken(): raise RuntimeError("db down")
        self.t[0] = 50                                    # expired, but seen within the stale window
        self.assertEqual(self.cache.get(1, broken), "ok")
        self.t[0] = 500                                   # too old to trust
        with self.assertRaises(RuntimeError):
            self.cache.get(1, broken)
        with self.assertRaises(RuntimeError):
            self.cache.get(2, broken)                     # never seen: nothing to fall back on

    def test_a_failed_lookup_is_never_remembered(self):
        def broken(): raise RuntimeError("db down")
        with self.assertRaises(RuntimeError):
            self.cache.get(1, broken)
        self.assertEqual(self.cache.get(1, self.loader("ok")), "ok")


class AdminApi(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.admin = add_user("admin@example.com", is_admin=True, user_id=1)
        self.pending = add_user("waiting@example.com", "pending", user_id=2)
        self.member = add_user("member@example.com", user_id=3)
        self.blocked = add_user("blocked@example.com", "blocked", user_id=4)
        self.a = signed_in_client(self.admin)

    def status_of(self, uid):
        return auth.store.users[uid]["status"]

    def test_only_administrators_get_in(self):
        stranger = TestClient(api.app, follow_redirects=False)
        for method, path in (("get", "/api/admin/users"), ("get", "/api/admin/summary"), ("post", "/api/admin/users/2/status"), ("get", "/admin")):
            self.assertIn(getattr(stranger, method)(path).status_code, (401, 302), path)
            r = getattr(signed_in_client(self.member), method)(path, **({"json": {"status": "approved"}, "headers": FETCH} if method == "post" else {}))
            self.assertEqual(r.status_code, 403, path)
        self.assertEqual(self.status_of(self.pending), "pending")

    def test_the_list_has_everyone_newest_first_with_counts_and_no_secrets(self):
        d = self.a.get("/api/admin/users").json()
        self.assertEqual([u["id"] for u in d["users"]], [4, 3, 2, 1])
        self.assertEqual(d["counts"], {"pending": 1, "approved": 2, "blocked": 1, "total": 4}); self.assertEqual(d["you"], self.admin)
        for u in d["users"]:
            self.assertEqual(set(u), {"id", "email", "status", "is_admin", "created_at", "last_login_at", "reviewed_at"})
        self.assertNotIn("password", self.a.get("/api/admin/users").text.lower())

    def test_the_summary_counts_those_waiting(self):
        self.assertEqual(self.a.get("/api/admin/summary").json(), {"status": "success", "pending": 1})
        add_user("another@example.com", "pending")
        self.assertEqual(self.a.get("/api/admin/summary").json()["pending"], 2)

    def test_approving_lets_that_person_in_straight_away(self):
        waiting = signed_in_client(self.pending)
        self.assertEqual(waiting.get("/api/portfolio").status_code, 401)
        r = self.a.post(f"/api/admin/users/{self.pending}/status", json={"status": "approved"}, headers=FETCH)
        self.assertEqual((r.status_code, r.json()["user"]), (200, {"id": 2, "email": "waiting@example.com", "status": "approved"}))
        self.assertEqual(waiting.get("/api/portfolio").status_code, 200)
        self.assertEqual((auth.store.users[self.pending]["reviewed_by"], auth.store.users[self.pending]["reviewed_at"] is not None), (self.admin, True))

    def test_declining_blocking_and_unblocking(self):
        post = lambda uid, status: self.a.post(f"/api/admin/users/{uid}/status", json={"status": status}, headers=FETCH)
        self.assertEqual(post(self.pending, "blocked").status_code, 200); self.assertEqual(self.status_of(self.pending), "blocked")
        member = signed_in_client(self.member); self.assertEqual(member.get("/api/portfolio").status_code, 200)
        self.assertEqual(post(self.member, "blocked").status_code, 200); self.assertEqual(member.get("/api/portfolio").status_code, 401)
        self.assertEqual(post(self.blocked, "approved").status_code, 200); self.assertEqual(self.status_of(self.blocked), "approved")

    def test_administrators_cannot_be_blocked_not_even_by_themselves(self):
        other = add_user("admin2@example.com", is_admin=True)
        for target in (self.admin, other):
            r = self.a.post(f"/api/admin/users/{target}/status", json={"status": "blocked"}, headers=FETCH)
            self.assertEqual(r.status_code, 409); self.assertIn("manage_users.py", r.json()["message"]); self.assertEqual(self.status_of(target), "approved")

    def test_bad_requests_are_refused_and_change_nothing(self):
        url = f"/api/admin/users/{self.pending}/status"
        self.assertEqual(self.a.post("/api/admin/users/999/status", json={"status": "approved"}, headers=FETCH).status_code, 404)
        for body in ({"status": "pending"}, {"status": "admin"}, {"status": ""}, {}, {"is_admin": True}):
            self.assertEqual(self.a.post(url, json=body, headers=FETCH).status_code, 422, body)
        self.assertEqual(self.a.post("/api/admin/users/abc/status", json={"status": "approved"}, headers=FETCH).status_code, 422)
        self.assertEqual(self.status_of(self.pending), "pending")
        self.assertFalse(auth.store.users[self.pending]["is_admin"])

    def test_a_change_must_come_from_the_admin_page_itself(self):
        url = f"/api/admin/users/{self.pending}/status"
        for headers in ({}, {"x-requested-with": "XMLHttpRequest"}, {"x-requested-with": ""}):
            r = self.a.post(url, json={"status": "approved"}, headers=headers)
            self.assertEqual(r.status_code, 403, headers)
        self.assertEqual(self.status_of(self.pending), "pending")
        # a form posted from another site (no custom header, form encoding) gets nowhere either
        self.assertIn(self.a.post(url, data={"status": "approved"}).status_code, (403, 422))
        self.assertEqual(self.status_of(self.pending), "pending")

    def test_there_is_no_way_to_grant_admin_rights_over_http(self):
        paths = {getattr(r, "path", "") for r in api.app.routes}
        self.assertFalse([p for p in paths if "admin" in p and ("promote" in p or "role" in p or "make" in p)])
        self.a.post(f"/api/admin/users/{self.member}/status", json={"status": "approved", "is_admin": True}, headers=FETCH)
        self.assertFalse(auth.store.users[self.member]["is_admin"])

    def test_losing_admin_rights_or_being_blocked_applies_on_the_very_next_call(self):
        auth.store.users[self.admin]["is_admin"] = False                     # not through the cache: the check is fresh each time
        self.assertEqual(self.a.get("/api/admin/users").status_code, 403)
        auth.store.users[self.admin]["is_admin"] = True
        self.assertEqual(self.a.get("/api/admin/users").status_code, 200)
        auth.store.users[self.admin]["status"] = "blocked"
        self.assertEqual(self.a.get("/api/admin/users").status_code, 403)

    def test_the_admin_page(self):
        r = self.a.get("/admin")
        self.assertEqual(r.status_code, 200)
        for needle in ("User access", "/api/admin/users", "X-Requested-With", "data-act", "escapeHtml"):
            self.assertIn(needle, r.text)
        self.assertIn("no-store", r.headers["cache-control"])
        denied = signed_in_client(self.member).get("/admin")
        self.assertEqual(denied.status_code, 403); self.assertIn("Administrators only", denied.text); self.assertNotIn("/api/admin/users", denied.text)

    def test_the_dashboard_tells_the_page_whether_to_show_the_admin_link(self):
        self.assertIn('<meta name="smp-user-admin" content="1">', self.a.get("/").text)
        self.assertIn('<meta name="smp-user-admin" content="0">', signed_in_client(self.member).get("/").text)

    def test_the_login_status_reports_admin_rights(self):
        self.assertTrue(self.a.get("/api/auth/status").json()["admin"])
        self.assertFalse(signed_in_client(self.member).get("/api/auth/status").json()["admin"])


class WhenTheDatabaseIsUnavailable(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        add_user("member@example.com", user_id=1)

    def test_accounts_not_set_up_says_so_instead_of_pretending_to_sign_you_out(self):
        c = signed_in_client(1, follow_redirects=False)
        auth.access_cache.clear(); auth.store.fail_with = auth.AccountsNotSetUp("x")
        page = c.get("/"); self.assertEqual(page.status_code, 503); self.assertIn("manage_users.py migrate", page.text)
        api_call = c.get("/api/portfolio"); self.assertEqual(api_call.status_code, 503); self.assertIn("manage_users.py migrate", api_call.json()["message"])

    def test_any_other_failure_is_a_503_not_a_sign_out(self):
        c = signed_in_client(1, follow_redirects=False)
        auth.access_cache.clear(); auth.store.fail_with = RuntimeError("db down")
        self.assertEqual(c.get("/api/portfolio").status_code, 503); self.assertEqual(c.get("/").status_code, 503)

    def test_a_brief_outage_does_not_interrupt_someone_who_was_just_using_the_app(self):
        c = signed_in_client(1)
        clock = [time.time()]
        saved = auth.access_cache.clock; auth.access_cache.clock = lambda: clock[0]
        try:
            auth.access_cache.clear()
            self.assertEqual(c.get("/").status_code, 200)                      # their status is now remembered
            auth.store.fail_with = RuntimeError("db down"); clock[0] += auth.ACCESS_TTL + 5     # expired, and the database is down
            self.assertEqual(c.get("/").status_code, 200)                      # the recent status is still trusted
            clock[0] += auth.ACCESS_STALE_OK + 5
            self.assertEqual(c.get("/").status_code, 503)                      # but not for ever
        finally:
            auth.access_cache.clock = saved

    def test_other_failures_in_an_admin_action_are_reported_not_leaked(self):
        admin = add_user("admin@example.com", is_admin=True)
        a = signed_in_client(admin)
        original = auth.store.set_status
        auth.store.set_status = lambda *args: (_ for _ in ()).throw(RuntimeError("secret detail"))
        try:
            r = a.post("/api/admin/users/1/status", json={"status": "blocked"}, headers=FETCH)
        finally:
            auth.store.set_status = original
        self.assertEqual(r.status_code, 500); self.assertNotIn("secret detail", r.text)


if __name__ == "__main__":
    unittest.main()
