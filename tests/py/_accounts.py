"""Helpers for tests that need accounts but no database: an in-memory user store and a signed-in test client."""
from unittest import mock

from fastapi.testclient import TestClient

import _path  # noqa: F401
import api
import auth
import watchlist


def _no_database(*args, **kwargs):
    raise AssertionError("A test tried to open a real database connection. Stub the data layer it calls instead.")


# Safety net: the tests run against fakes only. Anything that reaches for the real database fails loudly instead of
# quietly reading (or writing) your live data. It sits at the one place every connection comes from; tests that check
# SQL or connection settings install their own fake over it.
import mysql.connector

mysql.connector.connect = _no_database


class FakeStore:
    """Stands in for auth.MySQLStore. Same methods, kept in a dict."""

    def __init__(self):
        self.users, self.next_id = {}, 1
        self.fail_with = None            # set to an exception to make every call raise it
        self.gets = 0                    # how many times the database would have been asked for one account

    def _check(self):
        if self.fail_with:
            raise self.fail_with

    @staticmethod
    def _public(u):
        return {"id": u["id"], "email": u["email"], "status": u.get("status", "approved"), "is_admin": bool(u.get("is_admin", False))}

    def create(self, email, password_hash, status="pending", is_admin=False):
        self._check()
        if any(u["email"] == email for u in self.users.values()):
            raise auth.AuthError("An account with that email already exists. Try signing in instead.", 409)
        user_id, self.next_id = self.next_id, self.next_id + 1
        self.users[user_id] = {"id": user_id, "email": email, "password_hash": password_hash, "status": status, "is_admin": is_admin,
                               "created_at": 1_700_000_000 + user_id, "last_login_at": None, "reviewed_at": None}
        return user_id

    def find_by_email(self, email):
        self._check()
        u = next((u for u in self.users.values() if u["email"] == email), None)
        return {**self._public(u), "password_hash": u["password_hash"]} if u else None

    def get(self, user_id):
        self._check()
        self.gets += 1
        u = self.users.get(user_id)
        return self._public(u) if u else None

    def list_users(self):
        self._check()
        return [{**self._public(u), "created_at": u.get("created_at"), "last_login_at": u.get("last_login_at"), "reviewed_at": u.get("reviewed_at")}
                for u in sorted(self.users.values(), key=lambda u: -u["id"])]

    def set_status(self, user_id, status, reviewed_by):
        self._check()
        self.users[user_id]["status"] = status
        self.users[user_id]["reviewed_at"] = 1_800_000_000
        self.users[user_id]["reviewed_by"] = reviewed_by

    def touch_login(self, user_id):
        self._check()
        self.users[user_id]["last_login_at"] = 1_800_000_000


auth.store = FakeStore()


def add_user(email, status="approved", is_admin=False, user_id=None):
    """Put an account straight into the fake store (no password needed) and return its id."""
    store = auth.store
    uid = user_id or store.next_id
    store.next_id = max(store.next_id, uid + 1)
    store.users[uid] = {"id": uid, "email": email, "password_hash": "", "status": status, "is_admin": is_admin,
                        "created_at": 1_700_000_000 + uid, "last_login_at": None, "reviewed_at": None}
    auth.access_cache.invalidate(uid)
    return uid


class AccountsTestCase:
    """Mixin: a fresh fake store, empty caches and rate limits for every test, restored afterwards."""

    def setUp(self):
        self._saved = (auth.store, api.ALLOW_SIGNUP, api.SIGNUP_CODE, api.REQUIRE_APPROVAL)
        auth.store = FakeStore()
        auth.access_cache.clear()
        api.ALLOW_SIGNUP, api.SIGNUP_CODE, api.REQUIRE_APPROVAL = True, "", True
        for limiter in (api._login_by_client, api._login_by_email, api._signups):
            limiter.reset()
        api._agent_hits.clear()
        # personal data endpoints answer with empty data, so a test can see whether a request got through
        for target, name, value in ((api.portfolio, "list_holdings", []), (api.watchlist, "get_watchlist", [])):
            mock.patch.object(target, name, return_value=value).start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        auth.store, api.ALLOW_SIGNUP, api.SIGNUP_CODE, api.REQUIRE_APPROVAL = self._saved
        auth.access_cache.clear()
        for limiter in (api._login_by_client, api._login_by_email, api._signups):
            limiter.reset()
        api._agent_hits.clear()


def signed_in_client(user_id=1, is_admin=False, create=True, **kwargs):
    """A test client carrying a valid session cookie for `user_id`, who is made an approved account if it isn't one yet
    (pass create=False for a cookie whose account does not exist). Also makes sure no test can reach the real database
    through the accounts layer."""
    if not isinstance(auth.store, FakeStore):
        auth.store = FakeStore()
    if create and user_id not in auth.store.users:
        add_user(f"user{user_id}@example.com", "approved", is_admin, user_id)
    auth.access_cache.clear()
    client = TestClient(api.app, **kwargs)
    client.cookies.set(api.SESSION_COOKIE, auth.make_token(user_id))
    return client
