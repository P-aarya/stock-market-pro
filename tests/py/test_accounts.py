"""Accounts: passwords, sessions, sign-up and sign-in, and that every personal endpoint serves only the signed-in person."""
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import _path  # noqa: F401
import agent
import api
import auth
from _accounts import AccountsTestCase, FakeStore, signed_in_client

GOOD = "correct horse battery"


class Passwords(unittest.TestCase):
    def test_hashes_are_salted_and_never_the_password(self):
        a, b = auth.hash_password(GOOD), auth.hash_password(GOOD)
        self.assertNotEqual(a, b)
        self.assertNotIn(GOOD, a)
        self.assertTrue(a.startswith("scrypt$"))

    def test_verifying(self):
        stored = auth.hash_password(GOOD)
        self.assertTrue(auth.verify_password(GOOD, stored))
        self.assertFalse(auth.verify_password(GOOD + "x", stored))
        self.assertFalse(auth.verify_password("", stored))
        for garbage in ("", "plaintext", "scrypt$a$b", "md5$1$2$3$4$5", "scrypt$x$8$1$AAAA$AAAA", None):
            self.assertFalse(auth.verify_password(GOOD, garbage), repr(garbage))

    def test_unicode_passwords_work(self):
        self.assertTrue(auth.verify_password("pässwörd-密码-🔑", auth.hash_password("pässwörd-密码-🔑")))

    def test_password_rules(self):
        auth.check_password(GOOD, "abcdefgh@x.co")
        for bad, why in (("short", "at least"), ("x" * 129, "at most"), ("password", "easy to guess"), ("12345678", "easy to guess"), ("ABCDEFGH@X.CO", "easy to guess")):
            with self.assertRaises(auth.AuthError, msg=bad[:12]) as cm:
                auth.check_password(bad, "abcdefgh@x.co")
            self.assertIn(why, cm.exception.message)

    def test_email_rules(self):
        self.assertEqual(auth.normalize_email("  Abheek@Example.COM "), "abheek@example.com")
        auth.check_email("a@b.co")
        for bad in ("", "nope", "a@b", "a b@c.co", "@b.co", "a@@b.co", "a@b.co" + "x" * 260):
            with self.assertRaises(auth.AuthError, msg=bad[:20]):
                auth.check_email(bad)


class Sessions(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(auth.read_token(auth.make_token(42)), 42)

    def test_the_cookie_cannot_be_edited_to_become_someone_else(self):
        _, uid, exp, sig = auth.make_token(1).split(".")
        for forged in (f"v1.2.{exp}.{sig}", f"v1.1.{int(exp) + 99999}.{sig}", f"v1.1.{exp}.{'0' * 64}", f"v2.1.{exp}.{sig}"):
            self.assertIsNone(auth.read_token(forged), forged)

    def test_expired_and_garbage_are_refused(self):
        now = time.time()
        self.assertIsNone(auth.read_token(auth.make_token(1, now=now - auth.SESSION_DAYS * 86400 - 5)))
        self.assertEqual(auth.read_token(auth.make_token(1, now=now - auth.SESSION_DAYS * 86400 + 60)), 1)
        for junk in ("", "abc", "a.b.c.d", "v1.x.y.z", None, 5, "v1.1.9999999999"):
            self.assertIsNone(auth.read_token(junk), repr(junk))

    def test_a_different_secret_invalidates_every_session(self):
        token = auth.make_token(7)
        saved = auth._secret
        try:
            auth._secret = b"\x01" * 32
            self.assertIsNone(auth.read_token(token))
        finally:
            auth._secret = saved
        self.assertEqual(auth.read_token(token), 7)

    def test_secret_comes_from_the_environment_else_a_private_file(self):
        with mock.patch.dict(os.environ, {"APP_SECRET": "from-env"}):
            self.assertEqual(auth._load_secret(), b"from-env")
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(auth, "SECRET_FILE", os.path.join(d, ".app_secret")):
            os.environ.pop("APP_SECRET", None)
            first = auth._load_secret()
            self.assertEqual(auth._load_secret(), first)                      # stable across restarts
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(stat.S_IMODE(os.stat(auth.SECRET_FILE).st_mode), 0o600)   # readable only by the owner

    def test_unwritable_disk_falls_back_to_a_random_key_instead_of_crashing(self):
        with mock.patch.object(auth, "SECRET_FILE", "/nonexistent-dir/.app_secret"), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APP_SECRET", None)
            self.assertEqual(len(auth._load_secret()), 32)


class Limiter(unittest.TestCase):
    def test_blocks_after_the_limit_and_forgets_after_the_window(self):
        rl = auth.RateLimiter(3, 60)
        for i in range(3):
            self.assertFalse(rl.blocked("k", now=100 + i)); rl.record("k", now=100 + i)
        self.assertTrue(rl.blocked("k", now=110))
        self.assertFalse(rl.blocked("other", now=110))
        self.assertFalse(rl.blocked("k", now=100 + 60 + 3))
        rl.record("k", now=200); rl.clear("k"); self.assertFalse(rl.blocked("k", now=200))


class Registration(AccountsTestCase, unittest.TestCase):
    def client(self):
        return TestClient(api.app, follow_redirects=False)

    def post(self, c, **fields):
        data = {"email": "new@example.com", "password": GOOD, "confirm": GOOD, **fields}
        return c.post("/register", data=data)

    def test_the_form_is_served(self):
        r = self.client().get("/register")
        self.assertEqual(r.status_code, 200)
        for needle in ('name="email"', 'name="password"', 'name="confirm"', 'autocomplete="new-password"'):
            self.assertIn(needle, r.text)
        self.assertNotIn('name="code"', r.text)

    def test_signing_up_creates_the_account_and_signs_in(self):
        c = self.client()
        r = self.post(c, email="  New@Example.com ")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie); self.assertIn("samesite=lax", cookie); self.assertNotIn("secure", cookie)
        row = auth.store.find_by_email("new@example.com")
        self.assertIsNotNone(row)
        self.assertTrue(auth.verify_password(GOOD, row["password_hash"])); self.assertNotIn(GOOD, row["password_hash"])
        self.assertEqual(c.get("/api/auth/status").json()["user"], {"id": row["id"], "email": "new@example.com"})

    def test_the_cookie_is_marked_secure_behind_https(self):
        r = self.client().post("/register", data={"email": "s@example.com", "password": GOOD, "confirm": GOOD}, headers={"x-forwarded-proto": "https"})
        self.assertIn("secure", r.headers["set-cookie"].lower())

    def test_each_mistake_is_explained_and_nothing_is_created(self):
        cases = [({"email": "nope"}, 400, "valid email"), ({"password": "short", "confirm": "short"}, 400, "at least 8"),
                 ({"password": "password", "confirm": "password"}, 400, "too easy"), ({"confirm": "different-one"}, 400, "don&#x27;t match")]
        for fields, status, text in cases:
            r = self.post(self.client(), **fields)
            self.assertEqual(r.status_code, status, fields); self.assertIn(text, r.text); self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(auth.store.users, {})

    def test_the_email_is_kept_in_the_form_after_a_mistake_but_never_the_password(self):
        r = self.post(self.client(), confirm="nope")
        self.assertIn('value="new@example.com"', r.text); self.assertNotIn(GOOD, r.text)

    def test_an_email_can_only_be_used_once_whatever_its_case(self):
        self.assertEqual(self.post(self.client()).status_code, 303)
        r = self.post(self.client(), email="NEW@example.com")
        self.assertEqual(r.status_code, 409); self.assertIn("already exists", r.text)
        self.assertEqual(len(auth.store.users), 1)

    def test_html_in_an_email_is_shown_as_text(self):
        r = self.post(self.client(), email='"><script>alert(1)</script>@x.co', confirm="nope")
        self.assertNotIn("<script>alert(1)", r.text)

    def test_an_invite_code_can_be_required(self):
        api.SIGNUP_CODE = "open sesame"
        self.assertIn('name="code"', self.client().get("/register").text)
        self.assertEqual(self.post(self.client()).status_code, 403)
        self.assertEqual(self.post(self.client(), code="wrong").status_code, 403)
        self.assertEqual(auth.store.users, {})
        self.assertEqual(self.post(self.client(), code=" open sesame ").status_code, 303)

    def test_sign_ups_can_be_closed(self):
        api.ALLOW_SIGNUP = False
        c = self.client()
        for r in (c.get("/register"), self.post(c)):
            self.assertEqual(r.status_code, 403); self.assertIn("closed", r.text)
        self.assertEqual(auth.store.users, {})
        self.assertNotIn("Create an account", c.get("/login").text)

    def test_sign_ups_are_throttled_per_network(self):
        codes = [self.post(self.client(), email=f"u{i}@example.com", confirm="x").status_code for i in range(7)]
        self.assertEqual(codes, [400] * 5 + [429] * 2)
        self.assertEqual(self.post(self.client(), email="late@example.com").status_code, 429)

    def test_a_missing_accounts_table_gives_a_clear_message(self):
        auth.store.fail_with = auth.AccountsNotSetUp("x")
        r = self.post(self.client())
        self.assertEqual(r.status_code, 503); self.assertIn("manage_users.py migrate", r.text)


class SigningIn(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        auth.register("me@example.com", GOOD)

    def client(self):
        return TestClient(api.app, follow_redirects=False)

    def login(self, c=None, email="me@example.com", password=GOOD, **headers):
        return (c or self.client()).post("/login", data={"email": email, "password": password}, headers=headers)

    def test_right_credentials_sign_in_whatever_the_email_case(self):
        c = self.client()
        r = self.login(c, email=" ME@Example.com ")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))
        self.assertIn("httponly", r.headers["set-cookie"].lower())
        self.assertTrue(c.get("/api/auth/status").json()["signed_in"])

    def test_wrong_password_and_unknown_email_look_the_same(self):
        a = self.login(password="not it"); b = self.login(email="nobody@example.com")
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertNotIn("set-cookie", a.headers); self.assertNotIn("set-cookie", b.headers)
        msg = "That email or password isn&#x27;t right."
        self.assertIn(msg, a.text); self.assertIn(msg, b.text)

    def test_repeated_wrong_passwords_are_throttled_per_network_even_for_the_right_one(self):
        c = self.client()
        codes = [self.login(c, password="x").status_code for _ in range(7)]
        self.assertEqual(codes, [401] * 5 + [429] * 2)
        self.assertEqual(self.login(c).status_code, 429)

    def test_guessing_one_account_from_many_networks_is_throttled_too(self):
        codes = [self.login(password="x", **{"x-forwarded-for": f"9.9.9.{i}"}).status_code for i in range(12)]
        self.assertEqual(codes, [401] * 10 + [429] * 2)
        self.assertEqual(self.login(**{"x-forwarded-for": "8.8.8.8"}).status_code, 429)
        self.assertEqual(self.login(email="someone-else@example.com", password="x", **{"x-forwarded-for": "8.8.8.8"}).status_code, 401)

    def test_a_good_sign_in_resets_the_count(self):
        c = self.client()
        for _ in range(4):
            self.login(c, password="x")
        self.assertEqual(self.login(c).status_code, 303)
        for _ in range(4):
            self.assertEqual(self.login(c, password="x").status_code, 401)

    def test_the_login_page_sends_signed_in_people_to_the_dashboard(self):
        auth.store.users[1] = {"id": 1, "email": "x@y.co", "password_hash": ""}
        r = signed_in_client(1, follow_redirects=False).get("/login")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))

    def test_a_session_for_a_deleted_account_is_cleared_instead_of_looping(self):
        c = signed_in_client(99, follow_redirects=False)           # no such user
        r = c.get("/login")
        self.assertEqual(r.status_code, 200); self.assertIn("smp_session=", r.headers.get("set-cookie", ""))
        self.assertFalse(c.get("/api/auth/status").json()["signed_in"])

    def test_a_database_hiccup_does_not_sign_anyone_out(self):
        c = signed_in_client(1, follow_redirects=False)
        auth.store.fail_with = RuntimeError("db down")
        self.assertEqual(c.get("/login").status_code, 303)
        self.assertEqual(c.get("/api/auth/status").json(), {"enabled": True, "signed_in": True, "user": None})

    def test_logging_out_clears_the_cookie(self):
        r = self.client().get("/logout")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login"))
        self.assertIn("smp_session=", r.headers["set-cookie"]); self.assertIn("Max-Age=0", r.headers["set-cookie"])

    def test_a_missing_accounts_table_gives_a_clear_message(self):
        auth.store.fail_with = auth.AccountsNotSetUp("x")
        r = self.login()
        self.assertEqual(r.status_code, 503); self.assertIn("manage_users.py migrate", r.text)


class AccessControl(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        auth.store.users[1] = {"id": 1, "email": "one@example.com", "password_hash": ""}
        auth.store.users[2] = {"id": 2, "email": "two@example.com", "password_hash": ""}

    def test_strangers_are_sent_to_the_login_page_or_refused(self):
        c = TestClient(api.app, follow_redirects=False)
        page = c.get("/"); self.assertEqual((page.status_code, page.headers["location"]), (302, "/login"))
        self.assertEqual(c.get("/dashboard.html").status_code, 302)
        for method, path in (("get", "/api/portfolio"), ("get", "/api/stocks"), ("get", "/api/watchlist"), ("put", "/api/watchlist"),
                             ("post", "/agent/recommend"), ("delete", "/api/portfolio/1"), ("post", "/api/transactions"), ("get", "/api/market/gainers")):
            r = getattr(c, method)(path); self.assertEqual(r.status_code, 401, path); self.assertEqual(r.json()["message"], "Please sign in.")

    def test_what_a_stranger_may_open(self):
        c = TestClient(api.app, follow_redirects=False)
        for path in ("/login", "/register", "/health", "/api/auth/status", "/manifest.webmanifest", "/icon-192.png", "/apple-touch-icon.png"):
            self.assertEqual(c.get(path).status_code, 200, path)
        self.assertEqual(c.get("/api/auth/status").json(), {"enabled": True, "signed_in": False, "user": None})

    def test_a_forged_or_edited_cookie_is_a_stranger(self):
        c = TestClient(api.app, follow_redirects=False)
        _, _, exp, sig = auth.make_token(1).split(".")
        for forged in (f"v1.2.{exp}.{sig}", "garbage", f"v1.1.{exp}.{'0' * 64}"):
            c.cookies.set(api.SESSION_COOKIE, forged)
            self.assertEqual(c.get("/api/portfolio").status_code, 401, forged)

    def test_the_dashboard_is_served_with_the_signed_in_persons_identity(self):
        r = signed_in_client(2).get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text.count('name="smp-user-id"'), 1)
        self.assertIn('<meta name="smp-user-id" content="2">', r.text); self.assertIn('content="two@example.com"', r.text)
        self.assertNotIn("<!--SMP_USER-->", r.text); self.assertNotIn("one@example.com", r.text)
        self.assertIn("private", r.headers["cache-control"]); self.assertIn("no-store", r.headers["cache-control"])
        self.assertIn('id="page-ipos"', signed_in_client(1).get("/dashboard.html").text)

    def test_an_email_cannot_break_out_of_the_page_head(self):
        auth.store.users[3] = {"id": 3, "email": '"><script>x@y.co', "password_hash": ""}
        text = signed_in_client(3).get("/").text
        self.assertNotIn('"><script>x@y.co', text); self.assertIn("&quot;&gt;&lt;script&gt;x@y.co", text)

    def test_the_status_endpoint(self):
        self.assertEqual(signed_in_client(1).get("/api/auth/status").json(), {"enabled": True, "signed_in": True, "user": {"id": 1, "email": "one@example.com"}})
        self.assertFalse(signed_in_client(77).get("/api/auth/status").json()["signed_in"])

    def test_ai_requests_are_limited_per_person_not_per_network(self):
        saved_limit, saved_client = api.AGENT_RATE_LIMIT, agent.client
        api.AGENT_RATE_LIMIT = 3; agent.client = None
        try:
            one, two = signed_in_client(1), signed_in_client(2)
            codes = [one.post("/agent/recommend", json={"query": "hi"}, headers={"x-forwarded-for": f"1.1.1.{i}"}).status_code for i in range(5)]
            self.assertEqual(codes, [503, 503, 503, 429, 429])                      # same person, new network each time: still limited
            self.assertEqual(two.post("/agent/recommend", json={"query": "hi"}, headers={"x-forwarded-for": "1.1.1.1"}).status_code, 503)
        finally:
            api.AGENT_RATE_LIMIT, agent.client = saved_limit, saved_client


class EveryPersonalEndpointServesOnlyTheSignedInPerson(AccountsTestCase, unittest.TestCase):
    """The id the endpoints hand to the data layer is the one in the cookie, never one the request supplies."""
    HOLDING = {"ticker": "AAPL", "quantity": 1, "avg_buy_price": 10, "buy_date": "2025-01-01"}
    TX = {"ticker": "AAPL", "transaction_type": "BUY", "quantity": 1, "price": 10, "transaction_date": "2025-01-01"}
    CASES = [("get", "/api/portfolio", None, "portfolio.list_holdings"), ("get", "/api/portfolio/summary", None, "portfolio.list_holdings"),
             ("post", "/api/portfolio", HOLDING, "portfolio.add_holding"), ("put", "/api/portfolio/3", HOLDING, "portfolio.update_holding"),
             ("delete", "/api/portfolio/3", None, "portfolio.delete_holding"), ("get", "/api/transactions", None, "portfolio.list_transactions"),
             ("get", "/api/transactions/summary", None, "portfolio.transaction_summary"), ("post", "/api/transactions", TX, "portfolio.add_transaction"),
             ("put", "/api/transactions/3", {"quantity": 1, "price": 10, "transaction_date": "2025-01-01"}, "portfolio.update_transaction"),
             ("delete", "/api/transactions/3", None, "portfolio.delete_transaction"), ("get", "/api/portfolio/performance", None, "portfolio.performance"),
             ("post", "/agent/portfolio-review", {"use_holdings": True}, "portfolio.review"),
             ("get", "/api/watchlist", None, "watchlist.get_watchlist"), ("put", "/api/watchlist", {"tickers": ["AAPL"]}, "watchlist.save_watchlist")]

    def test_each_endpoint_passes_the_cookies_user_id_first(self):
        for method, path, body, target in self.CASES:
            for uid in (5, 6):
                seen = []
                module, name = target.split(".")
                original = getattr(getattr(api, module), name)
                empty = [] if name.startswith("list") or name == "get_watchlist" else {}
                setattr(getattr(api, module), name, lambda *a, _seen=seen, _empty=empty, **k: (_seen.append(a[0]), _empty)[1])
                try:
                    kwargs = {"json": body} if body is not None else {}
                    r = getattr(signed_in_client(uid), method)(path, **kwargs)
                finally:
                    setattr(getattr(api, module), name, original)
                self.assertEqual(r.status_code, 200, f"{method} {path} -> {r.status_code} {r.text[:120]}")
                self.assertEqual(seen[:1], [uid], f"{method} {path} used {seen} instead of {uid}")

    def test_a_user_id_in_the_request_is_ignored(self):
        seen = []
        original = api.portfolio.list_holdings
        api.portfolio.list_holdings = lambda uid: (seen.append(uid), [])[1]
        try:
            signed_in_client(5).get("/api/portfolio?user_id=1", headers={"x-user-id": "1"})
        finally:
            api.portfolio.list_holdings = original
        self.assertEqual(seen, [5])

    def test_a_stranger_reaches_none_of_them(self):
        c = TestClient(api.app)
        for method, path, body, _ in self.CASES:
            kwargs = {"json": body} if body is not None else {}
            self.assertEqual(getattr(c, method)(path, **kwargs).status_code, 401, f"{method} {path}")


class WatchlistEndpoints(AccountsTestCase, unittest.TestCase):
    def test_round_trip_through_the_api(self):
        saved = {}
        orig = (api.watchlist.get_watchlist, api.watchlist.save_watchlist)
        api.watchlist.get_watchlist = lambda uid: list(saved.get(uid, []))
        api.watchlist.save_watchlist = lambda uid, t: saved.__setitem__(uid, api.watchlist.clean(t)) or saved[uid]
        try:
            c1, c2 = signed_in_client(1), signed_in_client(2)
            self.assertEqual(c1.put("/api/watchlist", json={"tickers": ["aapl", "MSFT", "AAPL"]}).json(), {"status": "success", "tickers": ["AAPL", "MSFT"]})
            self.assertEqual(c1.get("/api/watchlist").json()["tickers"], ["AAPL", "MSFT"])
            self.assertEqual(c2.get("/api/watchlist").json()["tickers"], [])         # another person sees nothing of it
            bad = c1.put("/api/watchlist", json={"tickers": ["AAPL", "no spaces allowed"]})
            self.assertEqual(bad.status_code, 400); self.assertIn("ticker", bad.json()["message"])
            self.assertEqual(c1.put("/api/watchlist", json={"tickers": "AAPL"}).status_code, 422)
            self.assertEqual(c1.get("/api/watchlist").json()["tickers"], ["AAPL", "MSFT"])   # a refused save changed nothing
        finally:
            api.watchlist.get_watchlist, api.watchlist.save_watchlist = orig


if __name__ == "__main__":
    unittest.main()
