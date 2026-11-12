"""A person deleting their own account: the confirmation with a password, the 30 days in which signing in offers to restore it,
the restore itself, and the removal for good afterwards. No database: accounts live in the in-memory store, and the clock is
moved by hand."""
import re
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import _path  # noqa: F401
from _accounts import AccountsTestCase, add_user, signed_in_client
import api
import auth
import tiers

PW = "correct horse battery"
PW_HASH = auth.hash_password(PW)
DAY = 86400
T0 = 1_800_000_000                    # the moment the account is deleted
FETCH = {"x-requested-with": "fetch"}


class WithAClock(AccountsTestCase):
    def setUp(self):
        super().setUp()
        self.clock = [T0]
        patcher = mock.patch.object(auth, "now", lambda: self.clock[0])
        patcher.start(); self.addCleanup(patcher.stop)

    def at(self, seconds_after_deleting):
        self.clock[0] = T0 + seconds_after_deleting

    def account(self, email="me@example.com", user_id=1, tier=2, admin=False, status="approved"):
        uid = add_user(email, status, admin, user_id, tier)
        auth.store.users[uid]["password_hash"] = PW_HASH
        return uid

    def delete(self, client, password=PW, headers=FETCH, **kw):
        return client.post("/api/profile/delete", json={"password": password}, headers=headers, **kw)

    def browser(self):
        return TestClient(api.app, follow_redirects=False)

    def sign_in(self, email="me@example.com", password=PW):
        return self.browser().post("/login", data={"email": email, "password": password})

    def token_from(self, page):
        return re.search(r'name="token" value="([^"]+)"', page.text).group(1)

    def delete_account(self, uid=1, **kw):
        """Account `uid` deleted by its owner at T0, through the real endpoint."""
        self.account(user_id=uid, **kw)
        c = signed_in_client(uid, create=False, raise_server_exceptions=False)
        self.assertEqual(self.delete(c).status_code, 200)


class DeletingYourAccount(WithAClock, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.uid = self.account()
        self.c = signed_in_client(self.uid, create=False, raise_server_exceptions=False)

    def test_the_right_password_locks_the_account_stamps_the_time_and_signs_them_out(self):
        r = self.delete(self.c)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["status"], body["deleted_at"], body["remove_at"], body["grace_days"]), ("success", T0, T0 + 30 * DAY, 30))
        self.assertEqual((auth.store.users[self.uid]["status"], auth.store.users[self.uid]["deleted_at"]), ("deleted", T0))
        cookie = r.headers["set-cookie"].lower()
        self.assertIn(f"{api.SESSION_COOKIE}=", cookie); self.assertIn("max-age=0", cookie)        # the cookie is cleared on this device

    def test_the_old_session_stops_working_at_once_on_every_device(self):
        other_device = signed_in_client(self.uid, create=False, raise_server_exceptions=False)
        self.assertEqual(other_device.get("/api/profile").status_code, 200)
        self.delete(self.c)
        self.assertEqual(other_device.get("/api/profile").status_code, 401)
        self.assertEqual(other_device.get("/api/stocks/AAPL").status_code, 401)
        self.assertNotIn('id="page-market"', other_device.get("/").text)                         # the public page, not the dashboard

    def test_nothing_is_erased_yet_so_it_can_be_restored(self):
        self.delete(self.c)
        row = auth.store.users[self.uid]
        self.assertEqual((row["email"], row["tier"], row["password_hash"]), ("me@example.com", 2, PW_HASH))
        self.assertEqual(auth.store.purged, [])

    def test_a_wrong_password_changes_nothing_and_is_refused(self):
        r = self.delete(self.c, password="not the password")
        self.assertEqual(r.status_code, 403); self.assertEqual(r.json()["message"], "That password isn't right.")
        self.assertEqual(auth.store.users[self.uid]["status"], "approved")
        self.assertEqual(self.c.get("/api/profile").status_code, 200)

    def test_guessing_the_password_here_is_limited_like_signing_in(self):
        codes = [self.delete(self.c, password=f"guess {i}").status_code for i in range(6)]
        self.assertEqual(codes, [403] * 5 + [429])
        self.assertEqual(self.delete(self.c).status_code, 429)                                   # even the right one waits
        self.assertEqual(auth.store.users[self.uid]["status"], "approved")

    def test_a_right_password_clears_the_failed_attempts(self):
        for i in range(3):
            self.delete(self.c, password=f"guess {i}")
        self.assertEqual(self.delete(self.c).status_code, 200)
        self.assertEqual(dict(api._login_by_client._hits), {}); self.assertEqual(dict(api._login_by_email._hits), {})

    def test_it_must_come_from_the_app_itself_and_needs_a_password(self):
        self.assertEqual(self.delete(self.c, headers={}).status_code, 403)                       # no header: another site can't do this
        self.assertEqual(self.delete(self.c, headers={"x-requested-with": "xhr"}).status_code, 403)
        for body in ({}, {"password": ""}, {"password": "x" * 300}, {"pw": PW}):
            self.assertEqual(self.c.post("/api/profile/delete", json=body, headers=FETCH).status_code, 422, body)
        self.assertEqual(auth.store.users[self.uid]["status"], "approved")

    def test_visitors_and_unapproved_accounts_cannot_delete_anything(self):
        self.assertEqual(self.delete(self.browser()).status_code, 401)
        self.account("wait@example.com", 5, status="pending")
        pending = signed_in_client(5, create=False, raise_server_exceptions=False)
        self.assertEqual(self.delete(pending).status_code, 401)
        self.assertEqual(auth.store.users[5]["status"], "pending")

    def test_it_only_ever_deletes_the_signed_in_persons_own_account(self):
        self.account("other@example.com", 2)
        self.delete(self.c)
        self.assertEqual((auth.store.users[1]["status"], auth.store.users[2]["status"]), ("deleted", "approved"))

    def test_deleting_twice_is_refused(self):
        self.delete(self.c)
        again = signed_in_client(self.uid, create=False, raise_server_exceptions=False)
        self.assertEqual(self.delete(again).status_code, 401)
        self.assertEqual(auth.store.users[self.uid]["deleted_at"], T0)

    def test_the_only_administrator_cannot_delete_themselves_but_one_of_two_can(self):
        boss = self.account("boss@example.com", 7, admin=True)
        c = signed_in_client(boss, create=False, raise_server_exceptions=False)
        r = self.delete(c)
        self.assertEqual(r.status_code, 409); self.assertIn("only administrator", r.json()["message"])
        self.assertEqual(auth.store.users[boss]["status"], "approved")
        self.account("blocked-admin@example.com", 8, admin=True, status="blocked")               # a blocked one is no use
        self.assertEqual(self.delete(c).status_code, 409)
        self.account("second@example.com", 9, admin=True)
        self.assertEqual(self.delete(c).status_code, 200)
        self.assertEqual(auth.store.users[boss]["is_admin"], True)                               # kept, so a restore brings it back

    def test_a_failure_is_a_plain_message_and_nothing_is_deleted(self):
        with mock.patch.object(auth, "delete_own_account", side_effect=RuntimeError("secret internals")):
            r = self.delete(self.c)
        self.assertEqual(r.status_code, 500); self.assertNotIn("secret", r.text); self.assertIn("was not deleted", r.json()["message"])
        self.assertEqual(auth.store.users[self.uid]["status"], "approved")
        with mock.patch.object(auth, "delete_own_account", side_effect=auth.AccountsNotSetUp("x")):
            self.assertEqual(self.delete(self.c).status_code, 503)

    def test_the_profile_tells_the_page_how_long_the_grace_period_is(self):
        self.assertEqual(self.c.get("/api/profile").json()["grace_days"], 30)


class SigningInToADeletedAccount(WithAClock, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.delete_account()

    def test_the_right_password_inside_30_days_offers_to_restore_instead_of_signing_in(self):
        self.at(1 * DAY)
        r = self.sign_in()
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("set-cookie", r.headers)                                                # no session yet
        self.assertEqual(r.headers["cache-control"], "no-store")
        for text in ("Restore your account?", "me@example.com", "You have 29 days left", "Restore my account", "permanently deleted"):
            self.assertIn(text, r.text)
        self.assertIn('action="/restore"', r.text); self.assertIn('href="/login"', r.text)
        self.assertEqual(auth.read_restore_token(self.token_from(r)), 1)

    def test_the_page_names_the_day_it_was_deleted_and_the_day_it_goes_for_good(self):
        r = self.sign_in()
        from datetime import datetime, timezone
        d0, d1 = datetime.fromtimestamp(T0, timezone.utc), datetime.fromtimestamp(T0 + 30 * DAY, timezone.utc)
        self.assertIn(f"deleted on {d0:%b} {d0.day}, {d0.year}", r.text); self.assertIn(f"until {d1:%b} {d1.day}, {d1.year}", r.text)
        self.assertIn("You have 30 days left", r.text)

    def test_the_last_day_counts_as_one_day_left_and_not_a_second_longer(self):
        self.at(30 * DAY - 1)
        r = self.sign_in()
        self.assertEqual(r.status_code, 200); self.assertIn("You have 1 day left", r.text)
        self.at(30 * DAY)
        self.assertEqual(self.sign_in().status_code, 401)

    def test_a_wrong_password_is_the_usual_message_and_hints_at_nothing(self):
        r = self.sign_in(password="not the password")
        self.assertEqual(r.status_code, 401); self.assertIn("isn&#x27;t right", r.text); self.assertNotIn("Restore", r.text)

    def test_after_30_days_it_is_as_if_there_were_no_account_and_it_is_removed_on_the_spot(self):
        self.at(31 * DAY)
        r = self.sign_in()
        self.assertEqual(r.status_code, 401); self.assertIn("isn&#x27;t right", r.text); self.assertNotIn("Restore", r.text)
        self.assertNotIn(1, auth.store.users); self.assertEqual(auth.store.purged, [1])

    def test_if_removing_it_fails_the_sign_in_still_just_says_no(self):
        self.at(31 * DAY)
        with mock.patch.object(auth.store, "purge_if_expired", side_effect=RuntimeError("db down")):
            r = self.sign_in()
        self.assertEqual(r.status_code, 401); self.assertIn(1, auth.store.users)                  # the sweep will get it later

    def test_the_restore_offer_is_not_a_way_around_the_attempt_limit(self):
        for i in range(5):
            self.sign_in(password=f"guess {i}")
        r = self.sign_in()
        self.assertEqual(r.status_code, 429)


class Restoring(WithAClock, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.delete_account(tier=4)
        self.at(2 * DAY)
        self.token = self.token_from(self.sign_in())

    def restore(self, token=None, client=None):
        return (client or self.browser()).post("/restore", data={"token": self.token if token is None else token})

    def test_restoring_brings_the_account_back_exactly_as_it_was_and_signs_them_in(self):
        r = self.restore()
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))
        row = auth.store.users[1]
        self.assertEqual((row["status"], row["deleted_at"], row["tier"], row["email"]), ("approved", None, 4, "me@example.com"))
        cookie = r.headers["set-cookie"]
        self.assertIn(f"{api.SESSION_COOKIE}=", cookie); self.assertIn("HttpOnly", cookie)
        c = signed_in_client(1, create=False, raise_server_exceptions=False)
        self.assertEqual(c.get("/api/profile").json()["plan"]["name"], tiers.TIERS[4].name)       # the plan came back with it

    def test_the_restored_account_signs_in_normally_afterwards(self):
        self.restore()
        self.assertEqual(self.sign_in().status_code, 303)

    def test_an_administrator_gets_their_rights_back(self):
        self.account("boss@example.com", 7, admin=True); self.account("second@example.com", 8, admin=True)
        boss = signed_in_client(7, create=False, raise_server_exceptions=False)
        self.assertEqual(self.delete(boss).status_code, 200)
        token = self.token_from(self.sign_in("boss@example.com"))
        self.assertEqual(self.restore(token).status_code, 303)
        self.assertEqual(signed_in_client(7, create=False, raise_server_exceptions=False).get("/api/admin/users").status_code, 200)

    def test_a_link_that_has_gone_stale_or_was_tampered_with_does_nothing(self):
        self.clock[0] += 16 * 60
        r = self.restore()
        self.assertEqual(r.status_code, 400); self.assertIn("expired", r.text); self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(auth.store.users[1]["status"], "deleted")
        self.clock[0] -= 16 * 60
        for bad in ("", "garbage", self.token[:-1] + ("0" if self.token[-1] != "0" else "1"), self.token.replace("r1.", "r1.9", 1),
                    auth.make_token(1)):                                                          # a session cookie is not a restore token
            r = self.restore(bad)
            self.assertEqual(r.status_code, 400, bad); self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(auth.store.users[1]["status"], "deleted")

    def test_a_restore_token_cannot_be_used_as_a_session_and_a_session_cannot_restore(self):
        self.assertIsNone(auth.read_token(self.token)); self.assertIsNone(auth.read_restore_token(auth.make_token(1)))
        c = self.browser(); c.cookies.set(api.SESSION_COOKIE, self.token)
        self.assertEqual(c.get("/api/profile").status_code, 401)

    def test_pressing_it_twice_does_not_sign_anyone_in_the_second_time(self):
        self.assertEqual(self.restore().status_code, 303)
        again = self.restore()
        self.assertEqual(again.status_code, 409); self.assertNotIn("set-cookie", again.headers); self.assertIn("Please sign in", again.text)

    def test_if_the_30_days_run_out_while_the_button_is_waiting_the_restore_is_refused(self):
        self.at(30 * DAY - 60)
        token = self.token_from(self.sign_in())
        self.at(30 * DAY + 60)
        r = self.restore(token)
        self.assertEqual(r.status_code, 410); self.assertIn("permanently deleted", r.text); self.assertNotIn("set-cookie", r.headers)
        self.assertNotIn(1, auth.store.users)

    def test_an_account_that_was_removed_cannot_be_restored(self):
        del auth.store.users[1]
        r = self.restore()
        self.assertEqual(r.status_code, 404); self.assertNotIn("set-cookie", r.headers)

    def test_an_account_an_operator_has_blocked_meanwhile_stays_blocked(self):
        auth.store.users[1]["status"] = "blocked"
        r = self.restore()
        self.assertEqual(r.status_code, 409); self.assertEqual(auth.store.users[1]["status"], "blocked")

    def test_the_page_needs_no_sign_in_and_only_accepts_a_post(self):
        self.assertEqual(self.browser().get("/restore").status_code, 405)
        self.assertEqual(self.restore().status_code, 303)

    def test_a_missing_accounts_table_gives_the_set_up_message(self):
        with mock.patch.object(auth, "restore_account", side_effect=auth.AccountsNotSetUp("x")):
            self.assertEqual(self.restore().status_code, 503)


class SigningUpAgainWithTheSameEmail(WithAClock, unittest.TestCase):
    def setUp(self):
        super().setUp()
        api.REQUIRE_APPROVAL = False
        self.delete_account()

    def sign_up(self):
        return self.browser().post("/register", data={"email": "me@example.com", "password": PW, "confirm": PW})

    def test_while_it_can_still_be_restored_the_address_is_taken(self):
        self.at(10 * DAY)
        r = self.sign_up()
        self.assertEqual(r.status_code, 409); self.assertIn("already exists", r.text)

    def test_once_the_30_days_are_over_the_address_is_free_again_and_the_old_account_is_gone(self):
        self.at(30 * DAY)
        r = self.sign_up()
        self.assertEqual(r.status_code, 303)
        self.assertEqual(auth.store.purged, [1])
        new = auth.store.find_by_email("me@example.com")
        self.assertNotEqual(new["id"], 1); self.assertEqual(new["status"], "approved")


class RemovingThemForGood(WithAClock, unittest.TestCase):
    def test_only_deleted_accounts_past_their_30_days_are_removed(self):
        self.delete_account(uid=1)                                                               # deleted at T0
        self.at(10 * DAY); self.delete_account(uid=2, email="two@example.com")                   # deleted 10 days later
        self.account("keep@example.com", 3); self.account("wait@example.com", 4, status="pending"); self.account("nope@example.com", 5, status="blocked")
        self.at(30 * DAY)
        self.assertEqual(auth.purge_expired(), 1)
        self.assertEqual(sorted(auth.store.users), [2, 3, 4, 5]); self.assertEqual(auth.store.purged, [1])
        self.assertEqual(auth.purge_expired(), 0)                                                # nothing more to do
        self.at(40 * DAY)
        self.assertEqual(auth.purge_expired(), 1); self.assertEqual(sorted(auth.store.users), [3, 4, 5])

    def test_a_deleted_account_with_no_date_is_never_removed_and_never_restored(self):
        self.account("odd@example.com", 6, status="deleted")
        self.at(400 * DAY)
        self.assertEqual(auth.purge_expired(), 0); self.assertIn(6, auth.store.users)
        self.assertEqual(self.sign_in("odd@example.com").status_code, 401)

    def test_the_sweep_never_raises_and_says_what_it_did(self):
        self.delete_account(uid=1)
        self.at(31 * DAY)
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            self.assertEqual(api.run_account_cleanup(), 1)
        self.assertTrue(any("Permanently removed 1 deleted account" in m for m in logs.output))
        for failure in (RuntimeError("db down"), auth.AccountsNotSetUp("x")):
            with mock.patch.object(auth, "purge_expired", side_effect=failure), self.assertLogs("uvicorn.error", level="WARNING"):
                self.assertEqual(api.run_account_cleanup(), 0)

    def test_the_server_starts_the_sweep_in_the_background_unless_it_is_switched_off(self):
        with mock.patch.object(api.threading, "Thread") as thread:
            api.start_account_cleanup()
        self.assertEqual((thread.call_args.kwargs["target"], thread.call_args.kwargs["daemon"]), (api._account_cleanup_loop, True))
        thread.return_value.start.assert_called_once()
        with mock.patch.object(api, "ACCOUNT_CLEANUP", False), mock.patch.object(api.threading, "Thread") as thread:
            api.start_account_cleanup()
        thread.assert_not_called()

    def test_the_loop_sweeps_then_sleeps_for_hours(self):
        calls = []

        def fake_sleep(seconds):
            calls.append(seconds)
            raise KeyboardInterrupt                                                              # the loop never ends by itself: stop it after one round

        with mock.patch.object(api, "run_account_cleanup", side_effect=lambda: calls.append("sweep")), mock.patch.object(api.time, "sleep", fake_sleep):
            with self.assertRaises(KeyboardInterrupt):
                api._account_cleanup_loop()
        self.assertEqual(calls, ["sweep", 6 * 3600])


class WhatAnAdministratorSees(WithAClock, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.boss = self.account("boss@example.com", 20, admin=True)
        self.delete_account(uid=1)
        self.admin = signed_in_client(self.boss, create=False, raise_server_exceptions=False)

    def test_deleted_accounts_are_listed_with_when_and_counted(self):
        d = self.admin.get("/api/admin/users").json()
        row = next(u for u in d["users"] if u["id"] == 1)
        self.assertEqual((row["status"], row["deleted_at"]), ("deleted", T0))
        self.assertEqual(d["counts"]["deleted"], 1)
        self.assertNotIn("password", self.admin.get("/api/admin/users").text)

    def test_an_administrator_cannot_approve_or_block_a_deleted_account(self):
        for status in ("approved", "blocked"):
            r = self.admin.post("/api/admin/users/1/status", json={"status": status}, headers=FETCH)
            self.assertEqual(r.status_code, 409); self.assertIn("deleted by its owner", r.json()["message"])
        self.assertEqual(auth.store.users[1]["status"], "deleted")

    def test_the_pending_count_ignores_deleted_accounts(self):
        self.account("wait@example.com", 30, status="pending")
        self.assertEqual(auth.pending_count(), 1)                                                # the deleted one is not "waiting"
        self.assertEqual(self.admin.get("/api/admin/users").json()["counts"]["pending"], 1)


class TheSignInPage(AccountsTestCase, unittest.TestCase):
    def test_after_deleting_it_says_so_and_how_to_change_your_mind(self):
        r = TestClient(api.app).get("/login?notice=deleted")
        self.assertIn('class="note"', r.text); self.assertIn("Sign in within 30 days to restore it", r.text)

    def test_a_link_cannot_put_its_own_words_on_the_page(self):
        for value in ("<script>alert(1)</script>", "Your account was hacked", "", "DELETED", "deleted%00"):
            r = TestClient(api.app).get("/login", params={"notice": value})
            self.assertNotIn('class="note"', r.text, value); self.assertNotIn("alert(1)", r.text)

    def test_the_notice_never_hides_an_error(self):
        from auth_pages import login_page
        page = login_page("That email or password isn't right.", "a@b.co", True, notice="deleted")
        self.assertIn("isn&#x27;t right", page); self.assertNotIn('<p class="note"', page)


if __name__ == "__main__":
    unittest.main()
