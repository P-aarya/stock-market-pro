"""Helpers for tests that need accounts but no database: an in-memory user store and a signed-in test client."""
from fastapi.testclient import TestClient

import _path  # noqa: F401
import api
import auth


class FakeStore:
    """Stands in for auth.MySQLStore. Same methods, kept in a dict."""

    def __init__(self):
        self.users, self.next_id = {}, 1
        self.fail_with = None            # set to an exception to make every call raise it

    def _check(self):
        if self.fail_with:
            raise self.fail_with

    def create(self, email, password_hash):
        self._check()
        if any(u["email"] == email for u in self.users.values()):
            raise auth.AuthError("An account with that email already exists. Try signing in instead.", 409)
        user_id, self.next_id = self.next_id, self.next_id + 1
        self.users[user_id] = {"id": user_id, "email": email, "password_hash": password_hash}
        return user_id

    def find_by_email(self, email):
        self._check()
        return next((dict(u) for u in self.users.values() if u["email"] == email), None)

    def get(self, user_id):
        self._check()
        u = self.users.get(user_id)
        return {"id": u["id"], "email": u["email"]} if u else None

    def touch_login(self, user_id):
        self._check()


class AccountsTestCase:
    """Mixin: a fresh fake store and empty rate limits for every test, restored afterwards."""

    def setUp(self):
        self._saved = (auth.store, api.ALLOW_SIGNUP, api.SIGNUP_CODE)
        auth.store = FakeStore()
        api.ALLOW_SIGNUP, api.SIGNUP_CODE = True, ""
        for limiter in (api._login_by_client, api._login_by_email, api._signups):
            limiter.reset()
        api._agent_hits.clear()

    def tearDown(self):
        auth.store, api.ALLOW_SIGNUP, api.SIGNUP_CODE = self._saved
        for limiter in (api._login_by_client, api._login_by_email, api._signups):
            limiter.reset()
        api._agent_hits.clear()


def signed_in_client(user_id=1, **kwargs):
    """A test client carrying a valid session cookie for `user_id`."""
    client = TestClient(api.app, **kwargs)
    client.cookies.set(api.SESSION_COOKIE, auth.make_token(user_id))
    return client
