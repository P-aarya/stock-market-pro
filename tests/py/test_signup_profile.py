"""The investor questions asked when someone signs up: expected returns, risk appetite, markets, salary range and how they plan to
split their investments. They are checked on the server before any account is made, saved with the person's other interests, shown and
editable in Settings, and listed in the administrators' download. No database: accounts live in the in-memory store."""
import io
import json
import re
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import _path  # noqa: F401
from _accounts import AccountsTestCase, SIGNUP_ANSWERS, add_user, signed_in_client
import api
import auth
import auth_pages

try:
    import exports
except ImportError:                      # openpyxl is in requirements.txt, but not every machine has it installed
    exports = None

GOOD = "correct-horse-9"
FETCH = {"X-Requested-With": "fetch"}
VALID = dict(expected_return="5_10", risk="medium", markets=["us", "india"], salary="50k_100k", plan={"stocks": "60", "commodities": "30", "ipos": "10"})


def clean(**changes):
    return auth.clean_investor_profile(**{**VALID, **changes}, required=True)


def refused(test, **changes):
    with test.assertRaises(auth.AuthError) as caught:
        clean(**changes)
    return caught.exception.message


class TheRules(unittest.TestCase):
    def test_a_complete_set_of_answers_is_accepted_and_tidied(self):
        self.assertEqual(clean(), {"expected_return": "5_10", "risk": "medium", "markets": ["us", "india"], "salary": "50k_100k",
                                   "plan": {"stocks": 60, "commodities": 30, "ipos": 10}})
        self.assertEqual(clean(markets=["india", "us", "us"])["markets"], ["us", "india"])                 # standard order, no repeats

    def test_every_question_has_the_choices_that_were_asked_for(self):
        self.assertEqual(list(auth.RISK_LEVELS), ["low", "medium", "high"])
        self.assertEqual(list(auth.MARKETS.values()), ["US", "EU", "India"])
        self.assertEqual(list(auth.PLAN_BUCKETS.values()), ["Stocks", "Commodities", "IPOs"])
        self.assertIn("prefer_not", auth.SALARY_RANGES)                                                    # salary is sensitive: it can be declined
        self.assertIn("not_sure", auth.EXPECTED_RETURNS)

    def test_at_sign_up_every_question_must_be_answered_and_the_message_says_which(self):
        for field, blank, word in (("expected_return", "", "return you expect"), ("risk", "", "risk"), ("markets", [], "market"), ("salary", "", "salary range"),
                                   ("plan", {}, "split your investments")):
            self.assertIn(word, refused(self, **{field: blank}), field)
            self.assertIn(word, refused(self, **{field: None}), field)
        self.assertIn("split your investments", refused(self, plan={"stocks": "", "commodities": " ", "ipos": None}))

    def test_in_settings_an_empty_answer_just_means_not_given(self):
        none = auth.clean_investor_profile(required=False)
        self.assertEqual(none, {"expected_return": "", "risk": "", "markets": [], "salary": "", "plan": {}})
        self.assertEqual(auth.clean_investor_profile(risk="high", required=False)["risk"], "high")

    def test_only_our_own_choices_are_accepted(self):
        for field, bad in (("expected_return", "1000%"), ("expected_return", "5_10 "), ("risk", "extreme"), ("risk", "LOW"), ("salary", "a lot"),
                           ("expected_return", "<script>"), ("risk", "medium; DROP TABLE users")):
            if field == "expected_return" and bad == "5_10 ":
                continue                                                                                   # surrounding spaces are trimmed, not refused
            with self.assertRaises(auth.AuthError, msg=(field, bad)):
                auth.clean_investor_profile(**{**VALID, field: bad}, required=False)
        for bad in (["mars"], ["us", "<b>"], "us", {"us": True}, 5):
            with self.assertRaises(auth.AuthError, msg=bad):
                auth.clean_investor_profile(**{**VALID, "markets": bad}, required=False)
        self.assertEqual(clean(expected_return=" 5_10 ")["expected_return"], "5_10")

    def test_the_plan_must_add_up_to_exactly_100_and_the_message_says_what_it_added_up_to(self):
        for plan, total in (({"stocks": "50", "commodities": "30", "ipos": "10"}, 90), ({"stocks": "60", "commodities": "30", "ipos": "20"}, 110),
                            ({"stocks": "100", "commodities": "1", "ipos": ""}, 101), ({"stocks": "1", "commodities": "", "ipos": ""}, 1)):
            self.assertIn(f"adds up to {total}%", refused(self, plan=plan), plan)

    def test_plan_boxes_are_whole_numbers_from_0_to_100_and_a_blank_box_counts_as_zero(self):
        self.assertEqual(clean(plan={"stocks": "100", "commodities": "", "ipos": ""})["plan"], {"stocks": 100, "commodities": 0, "ipos": 0})
        self.assertEqual(clean(plan={"stocks": "40%", "commodities": " 40 ", "ipos": 20})["plan"], {"stocks": 40, "commodities": 40, "ipos": 20})
        self.assertEqual(clean(plan={"stocks": "0", "commodities": "0", "ipos": "100"})["plan"]["ipos"], 100)
        for bad in ("12.5", "abc", "-5", "1e2", "0x10", "٣٠", "²", "101", "1000", "10 0", "+30", "30,0", "NaN", "<script>"):
            self.assertIn("whole number", refused(self, plan={"stocks": bad, "commodities": "0", "ipos": "0"}), bad)

    def test_a_saved_set_of_answers_is_read_back_safely_whatever_is_in_the_database(self):
        junk = {"expected_return": "1000%", "risk": ["x"], "markets": "us", "salary": 5, "plan": {"stocks": "60", "commodities": 30, "ipos": 10}}
        self.assertEqual(auth.investor_from(junk), {"expected_return": "", "risk": "", "markets": [], "salary": "", "plan": {}})
        self.assertEqual(auth.investor_from({"plan": {"stocks": 50, "commodities": 30, "ipos": 10}})["plan"], {})          # doesn't add up
        self.assertEqual(auth.investor_from({"plan": {"stocks": True, "commodities": 99, "ipos": 0}})["plan"], {})          # True is not 1
        self.assertEqual(auth.investor_from({"markets": ["india", "mars", "us"]})["markets"], ["us", "india"])
        self.assertEqual(auth.investor_from(None), auth.investor_from({}))
        for raw in (None, "", "not json", "[1, 2]", "null", json.dumps({"purposes": ["learning"]})):                      # older saves have none of the new keys
            got = auth.interests_from(raw)
            for key in auth.INVESTOR_KEYS:
                self.assertIn(key, got, (raw, key))
        self.assertEqual(auth.interests_from(json.dumps({"purposes": ["learning"]}))["purposes"], ["learning"])


class TheSignUpPage(unittest.TestCase):
    page = auth_pages.register_page()

    def test_the_five_questions_are_on_the_form_with_the_choices_from_one_place(self):
        form = self.page.split('<form method="post" action="/register">')[1].split("</form>")[0]
        for name in ("expected_return", "salary"):
            self.assertIn(f'<select id="{name}" name="{name}" required>', form)
        for key in list(auth.EXPECTED_RETURNS) + list(auth.SALARY_RANGES):
            self.assertIn(f'<option value="{key}"', form)
        self.assertEqual(re.findall(r'<input type="radio" name="risk" value="(\w+)"', form), ["low", "medium", "high"])
        self.assertEqual(re.findall(r'<input type="checkbox" name="markets" value="(\w+)"', form), ["us", "eu", "india"])
        self.assertEqual(re.findall(r'name="(plan_\w+)" type="number"', form), ["plan_stocks", "plan_commodities", "plan_ipos"])
        for label in ("Expected returns", "Risk appetite", "Markets you are interested in", "Salary range", "Investment plan", "IPOs %", "Commodities %", "Stocks %"):
            self.assertIn(label, form)
        self.assertIn("Aymetrix covers the US stock market today.", form)
        self.assertIn("add up to 100%", form)

    def test_nothing_is_picked_on_a_fresh_form(self):
        self.assertNotIn(" selected", self.page); self.assertNotIn(" checked", self.page)

    def test_after_a_mistake_what_was_filled_in_is_shown_again(self):
        answers = {"expected_return": "10_15", "risk": "high", "markets": ["eu", "india"], "salary": "over_200k", "plan_stocks": "70", "plan_commodities": "20", "plan_ipos": "5"}
        page = auth_pages.register_page("x", "a@b.co", answers=answers)
        self.assertIn('<option value="10_15" selected>', page); self.assertIn('<option value="over_200k" selected>', page)
        self.assertIn('value="high" checked', page); self.assertNotIn('value="low" checked', page)
        self.assertIn('value="eu" checked', page); self.assertIn('value="india" checked', page); self.assertNotIn('value="us" checked', page)
        for name, n in (("plan_stocks", "70"), ("plan_commodities", "20"), ("plan_ipos", "5")):
            self.assertIn(f'name="{name}" type="number" min="0" max="100" step="1" inputmode="numeric" placeholder="0" value="{n}"', page)

    def test_what_was_typed_is_shown_as_text_never_as_markup(self):
        evil = '"><script>alert(1)</script>'
        page = auth_pages.register_page("x", "a@b.co", answers={"expected_return": evil, "risk": evil, "markets": [evil], "salary": evil, "plan_stocks": evil, "plan_commodities": evil, "plan_ipos": evil})
        self.assertNotIn("<script>alert(1)", page)

    def test_the_questions_sit_inside_the_box_and_before_the_button(self):
        text = self.page
        self.assertLess(text.index('<form method="post" action="/register">'), text.index("About your investing"))
        self.assertLess(text.index("Investment plan"), text.index('<button type="submit">Create account</button>'))
        self.assertLess(text.index("Investment plan"), text.index("</main>"))

    def test_a_running_total_script_keeps_the_three_plan_boxes_honest(self):
        self.assertIn("plan-total", self.page); self.assertIn("plan_stocks", self.page)
        self.assertIn("Total: ", self.page)


class OnePageAtATime(unittest.TestCase):
    """The sign-up form is six pages, shown one at a time with Next and Back. It stays one form that is sent once, at the end."""
    page = auth_pages.register_page()

    def pages(self, text=None):
        form = (text or self.page).split('<form method="post" action="/register">')[1].split("</form>")[0]
        parts = re.split(r'data-step="(\d)"', form)
        return {int(number): body for number, body in zip(parts[1::2], parts[2::2])}

    def test_the_account_details_come_first_then_one_question_on_each_page(self):
        names = {number: sorted(set(re.findall(r'name="(\w+)"', body))) for number, body in self.pages().items()}
        self.assertEqual(names, {1: ["confirm", "email", "password"], 2: ["expected_return"], 3: ["risk"], 4: ["markets"], 5: ["salary"],
                                 6: ["plan_commodities", "plan_ipos", "plan_stocks"]})
        self.assertEqual(re.findall(r"<h2 id=\"q-\w+\">([^<]+)</h2>", self.page), ["Expected returns", "Risk appetite", "Markets you are interested in", "Salary range", "Investment plan"])

    def test_next_goes_on_back_goes_back_and_only_the_last_page_sends_the_form(self):
        for number, body in self.pages().items():
            self.assertEqual(body.count('class="stepbtn next"'), 0 if number == 6 else 1, number)
            self.assertEqual(body.count('class="stepbtn back"'), 0 if number == 1 else 1, number)
            self.assertEqual(body.count('type="submit"'), 1 if number == 6 else 0, number)
        self.assertEqual(self.page.count("<form"), 1)                                   # one form, so a password is never passed from page to page
        for button in re.findall(r'<button [^>]*class="stepbtn[^"]*"', self.page):
            self.assertIn('type="button"', button)                                      # Next and Back never send anything themselves

    def test_an_invite_code_is_asked_on_the_first_page(self):
        self.assertIn('name="code"', self.pages(auth_pages.register_page(needs_code=True))[1])

    def test_without_javascript_every_page_shows_because_only_the_script_hides_any(self):
        css = self.page.split("<style>")[1].split("</style>")[0]
        self.assertEqual(css.count(".step{display:none"), 1)
        self.assertIn(".wizard .step{display:none}", css)                               # .wizard is added by the script, never in the HTML
        self.assertIn(".stepbtn{display:none}", css)                                    # and the Next and Back buttons are for the script too
        form = self.page.split('<form method="post" action="/register">')[1].split("</form>")[0]
        self.assertNotIn(" hidden", form)
        self.assertNotIn('class="wizard"', self.page)
        self.assertIn("form.noValidate=true", self.page)                                # else the browser would block sending: fields on unseen pages


class SigningUp(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        api.REQUIRE_APPROVAL = False

    def client(self):
        return TestClient(api.app, follow_redirects=False)

    def post(self, c=None, **fields):
        api._signups.reset()                                                                          # sign-ups are throttled per network: these tests make many attempts
        data = {"email": "new@example.com", "password": GOOD, "confirm": GOOD, **SIGNUP_ANSWERS, **fields}
        return (c or self.client()).post("/register", data=data)

    def test_a_sign_up_with_every_answer_creates_the_account_and_saves_the_answers(self):
        r = self.post(markets=["us", "india"], risk="high", salary="100k_200k", expected_return="15_25", plan_stocks="50", plan_commodities="25", plan_ipos="25")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))
        uid = auth.store.find_by_email("new@example.com")["id"]
        got = auth.get_details(uid)
        self.assertEqual({k: got[k] for k in auth.INVESTOR_KEYS}, {"expected_return": "15_25", "risk": "high", "markets": ["us", "india"], "salary": "100k_200k",
                                                                    "plan": {"stocks": 50, "commodities": 25, "ipos": 25}})

    def test_each_missing_question_stops_the_sign_up_before_any_account_exists(self):
        for field, word in (("expected_return", "return you expect"), ("risk", "risk"), ("markets", "market"), ("salary", "salary range"),
                            ("plan_stocks", "100%")):
            data = {k: v for k, v in SIGNUP_ANSWERS.items() if k != field}
            if field == "plan_stocks":
                data = {k: v for k, v in data.items() if not k.startswith("plan_")}
            api._signups.reset()
            r = self.client().post("/register", data={"email": "new@example.com", "password": GOOD, "confirm": GOOD, **data})
            self.assertEqual(r.status_code, 400, field); self.assertIn(word, r.text, field); self.assertNotIn("set-cookie", r.headers)
            self.assertEqual(auth.store.users, {}, field)
        api._signups.reset()
        self.assertEqual(self.client().post("/register", data={"email": "new@example.com", "password": GOOD, "confirm": GOOD}).status_code, 400)

    def test_bad_answers_are_refused_with_nothing_created(self):
        cases = [dict(risk="extreme"), dict(expected_return="500%"), dict(salary="lots"), dict(markets=["mars"]), dict(markets=["us", "<b>"]),
                 dict(plan_stocks="50"), dict(plan_stocks="abc"), dict(plan_stocks="-10", plan_commodities="60", plan_ipos="50"), dict(plan_stocks="1e2", plan_commodities="0", plan_ipos="0")]
        for fields in cases:
            r = self.post(**fields)
            self.assertEqual(r.status_code, 400, fields); self.assertEqual(auth.store.users, {}, fields)

    def test_after_a_mistake_the_form_comes_back_with_the_answers_kept_and_no_password(self):
        r = self.post(markets=["eu", "india"], risk="low", salary="under_25k", expected_return="under_5", plan_stocks="10", plan_commodities="10", plan_ipos="10")
        self.assertEqual(r.status_code, 400); self.assertIn("adds up to 30%", r.text)
        self.assertIn('<option value="under_5" selected>', r.text); self.assertIn('<option value="under_25k" selected>', r.text)
        self.assertIn('value="low" checked', r.text); self.assertIn('value="eu" checked', r.text); self.assertIn('value="india" checked', r.text)
        self.assertIn('value="new@example.com"', r.text); self.assertNotIn(GOOD, r.text)
        self.assertEqual(auth.store.users, {})

    def test_the_answers_are_checked_after_the_passwords_but_before_the_account_is_made(self):
        r = self.post(confirm="different-one", risk="extreme")
        self.assertIn("don&#x27;t match", r.text)                                                       # the password mistake is reported first
        with mock.patch.object(auth, "register", side_effect=AssertionError("an account was made")):
            self.assertEqual(self.post(risk="extreme").status_code, 400)

    def test_a_failure_to_save_the_answers_never_undoes_or_blocks_the_sign_up(self):
        with mock.patch.object(auth, "save_investor_profile", side_effect=RuntimeError("database hiccup")), self.assertLogs(level="ERROR") as logs:
            r = self.post()
        self.assertIn("Could not save the investor answers", logs.output[0])                             # the failure is logged for the owner, not shown to the person
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))
        self.assertIsNotNone(auth.store.find_by_email("new@example.com"))

    def test_a_sign_up_that_waits_for_approval_still_keeps_the_answers(self):
        api.REQUIRE_APPROVAL = True
        r = self.post(risk="low")
        self.assertEqual(r.status_code, 200); self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(auth.get_details(auth.store.find_by_email("new@example.com")["id"])["risk"], "low")

    def test_signing_up_again_with_a_taken_address_changes_nothing_about_the_first_account(self):
        self.assertEqual(self.post(risk="low").status_code, 303)
        r = self.post(self.client(), risk="high")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(auth.get_details(auth.store.find_by_email("new@example.com")["id"])["risk"], "low")

    def test_extra_fields_still_cannot_make_an_account_an_administrator_or_change_its_plan(self):
        self.assertEqual(self.post(is_admin="1", status="approved", tier="6").status_code, 303)
        row = auth.store.find_by_email("new@example.com")
        self.assertFalse(row["is_admin"]); self.assertNotEqual(row.get("tier"), 6)


class InSettings(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.c = signed_in_client(1, tier=1)

    def account(self):
        return self.c.get("/api/account").json()

    def save(self, **fields):
        body = {"name": "Sam", **fields}
        return self.c.put("/api/account/profile", json=body, headers=FETCH)

    def test_the_account_carries_the_five_answers_and_their_choices(self):
        body = self.account()
        for key in auth.INVESTOR_KEYS:
            self.assertIn(key, body["account"])
        self.assertEqual(body["account"]["markets"], []); self.assertEqual(body["account"]["plan"], {})
        for key in ("expected_returns", "risk_levels", "risk_hints", "markets", "salary_ranges", "plan_buckets"):
            self.assertTrue(body["choices"][key], key)
        self.assertEqual(body["choices"]["markets"], {"us": "US", "eu": "EU", "india": "India"})

    def test_changing_the_answers_saves_them(self):
        r = self.save(expected_return="10_15", risk="high", markets=["eu"], salary="over_200k", plan_stocks="20", plan_commodities="30", plan_ipos="50")
        self.assertEqual(r.status_code, 200, r.text)
        a = self.account()["account"]
        self.assertEqual((a["expected_return"], a["risk"], a["markets"], a["salary"], a["plan"]), ("10_15", "high", ["eu"], "over_200k", {"stocks": 20, "commodities": 30, "ipos": 50}))

    def test_saving_the_page_without_those_fields_keeps_what_was_saved(self):
        self.save(risk="low", markets=["us"], expected_return="5_10", salary="prefer_not", plan_stocks="100", plan_commodities="", plan_ipos="")
        self.assertEqual(self.save(city="Pune", follows="stocks").status_code, 200)                  # an older copy of the page that knows nothing of them
        a = self.account()["account"]
        self.assertEqual((a["risk"], a["markets"], a["plan"], a["city"]), ("low", ["us"], {"stocks": 100, "commodities": 0, "ipos": 0}, "Pune"))

    def test_the_other_answers_are_kept_when_the_investor_ones_change(self):
        self.save(follows="both", purposes=["learning"], sectors=["Energy"], commodities=["Gold"], invest_range="1k_10k")
        self.save(risk="medium", follows="both", purposes=["learning"], sectors=["Energy"], commodities=["Gold"], invest_range="1k_10k")
        a = self.account()["account"]
        self.assertEqual((a["risk"], a["follows"], a["purposes"], a["sectors"], a["commodities"], a["invest_range"]), ("medium", "both", ["learning"], ["Energy"], ["Gold"], "1k_10k"))

    def test_emptying_the_answers_clears_them_and_none_are_needed_in_settings(self):
        self.save(risk="low", markets=["us"], salary="under_25k", expected_return="under_5", plan_stocks="100")
        self.assertEqual(self.save(risk="", markets=[], salary="", expected_return="", plan_stocks="", plan_commodities="", plan_ipos="").status_code, 200)
        a = self.account()["account"]
        self.assertEqual((a["risk"], a["markets"], a["salary"], a["expected_return"], a["plan"]), ("", [], "", "", {}))

    def test_a_wrong_answer_is_explained_and_nothing_is_saved(self):
        self.save(risk="low")
        for fields, word in ((dict(risk="extreme"), "risk"), (dict(markets=["mars"]), "market"), (dict(plan_stocks="50", plan_commodities="10", plan_ipos="10"), "adds up to 70%"),
                             (dict(salary="lots"), "salary"), (dict(expected_return="500%"), "return")):
            r = self.save(**fields)
            self.assertGreaterEqual(r.status_code, 400, fields); self.assertIn(word, r.text, fields)
        self.assertEqual(self.account()["account"]["risk"], "low")

    def test_the_page_is_not_changed_without_the_fetch_header(self):
        self.assertGreaterEqual(self.c.put("/api/account/profile", json={"name": "x", "risk": "low"}).status_code, 400)


@unittest.skipIf(exports is None, "openpyxl is not installed here, so the Excel download cannot be built")
class InTheAdministratorsDownload(unittest.TestCase):
    def workbook(self, details):
        from openpyxl import load_workbook
        users = [{"id": 1, "email": "a@example.com", "full_name": "A", "status": "approved", "is_admin": False, "created_at": 1_700_000_000, "last_login_at": None},
                 {"id": 2, "email": "b@example.com", "full_name": "", "status": "approved", "is_admin": False, "created_at": 1_700_000_001, "last_login_at": None}]
        raw = exports.users_workbook(users, details, auth.GENDERS, auth.age_on)
        return load_workbook(io.BytesIO(raw))

    def interests(self, **investor):
        return {"interests": json.dumps({"purposes": [], "follows": "", "sectors": [], "commodities": [], **investor})}

    def test_the_users_sheet_has_a_column_for_each_answer(self):
        wb = self.workbook({1: self.interests(expected_return="10_15", risk="high", markets=["us", "india"], salary="prefer_not", plan={"stocks": 60, "commodities": 30, "ipos": 10})})
        ws = wb["Users"]
        headers = [c.value for c in ws[4]]                                                          # row 4 holds the column headings
        for name in ("Expected returns", "Risk appetite", "Markets", "Salary range (US $)", "Investment plan"):
            self.assertIn(name, headers)
        rows = {r[0].value: [c.value for c in r] for r in ws.iter_rows(min_row=5)}
        mine = dict(zip(headers, rows["a@example.com"]))
        self.assertEqual((mine["Expected returns"], mine["Risk appetite"], mine["Markets"], mine["Salary range (US $)"], mine["Investment plan"]),
                         ("10% – 15% a year", "High", "US, India", "Prefer not to say", "Stocks 60% · Commodities 30% · IPOs 10%"))
        blank = dict(zip(headers, rows["b@example.com"]))
        self.assertIn(blank["Risk appetite"], ("", None)); self.assertIn(blank["Investment plan"], ("", None))

    def test_the_summary_counts_each_answer(self):
        wb = self.workbook({1: self.interests(risk="high", markets=["us", "india"], expected_return="5_10", salary="under_25k"),
                            2: self.interests(risk="high", markets=["us"], expected_return="5_10", salary="under_25k")})
        cells = [[c.value for c in row] for row in wb["Summary"].iter_rows()]
        flat = {tuple(r[:2]) for r in cells if r and r[0] is not None}
        for expected in (("High", 2), ("US", 2), ("India", 1), ("EU", 0), ("5% – 10% a year", 2), ("Under $25,000", 2), ("Low", 0)):
            self.assertIn(expected, flat, expected)
        titles = [r[0] for r in cells if r and r[0]]
        for title in ("Expected returns", "Risk appetite", "Salary range (US $)"):
            self.assertIn(title, titles)
        self.assertTrue(any(str(t).startswith("Markets of interest") for t in titles))


if __name__ == "__main__":
    unittest.main()
