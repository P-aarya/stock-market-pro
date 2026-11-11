"""The pictures behind the sign-in and sign-up pages: they load before anyone is signed in, each page gets its own, they are safe to
serve, and the box in front of them stays readable."""
import os
import re
import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

import _path  # noqa: F401
from _accounts import AccountsTestCase
import api
import auth_pages

STATIC = os.path.join(_path.ROOT, "static")
PICTURES = {"/bg-login.svg": "bg-login.svg", "/bg-register.svg": "bg-register.svg"}


class ThePictures(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            return f.read()

    def test_both_files_exist_are_valid_svg_and_are_small(self):
        for name in PICTURES.values():
            text = self.read(name)
            root = ET.fromstring(text)                                                           # well-formed XML
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg", name)
            self.assertIn("viewBox", root.attrib); self.assertEqual(root.attrib.get("preserveAspectRatio"), "xMidYMid slice")
            self.assertLess(len(text.encode("utf-8")), 60_000, f"{name} should stay light: it is fetched by every visitor")

    def test_they_are_pictures_only_no_scripts_no_links_no_outside_files(self):
        for name in PICTURES.values():
            text = self.read(name)
            for forbidden in ("<script", "onload", "onclick", "<foreignObject", "<a ", "xlink:href", "href=", "http://www.w3.org/1999/xlink", "@import", "<image", "<style"):
                self.assertNotIn(forbidden, text, f"{name}: {forbidden}")
            self.assertEqual(re.findall(r"https?://[^\"' )]+", text), ["http://www.w3.org/2000/svg"], name)      # only the SVG namespace

    def test_the_two_are_different_pictures(self):
        self.assertNotEqual(self.read("bg-login.svg"), self.read("bg-register.svg"))


class ServedBeforeSigningIn(AccountsTestCase, unittest.TestCase):
    def test_each_picture_loads_without_a_session_as_a_cacheable_image(self):
        c = TestClient(api.app, follow_redirects=False)
        for path, name in PICTURES.items():
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers["content-type"], "image/svg+xml")
            self.assertIn("max-age=", r.headers["cache-control"]); self.assertIn("public", r.headers["cache-control"])
            with open(os.path.join(STATIC, name), "rb") as f:
                self.assertEqual(r.content, f.read())
            self.assertIn(path, api._OPEN_PATHS)

    def test_other_files_in_that_folder_are_not_exposed_by_this(self):
        c = TestClient(api.app, follow_redirects=False)
        for path in ("/static/bg-login.svg", "/make_icons.py", "/bg-login.svg/../make_icons.py", "/bg-other.svg"):
            self.assertNotEqual(c.get(path).status_code, 200, path)


class EachPageGetsItsOwnPicture(unittest.TestCase):
    def body_class(self, page):
        return re.search(r'<body class="([^"]+)"', page).group(1)

    def test_sign_in_uses_the_chart_and_sign_up_uses_the_growth_picture(self):
        self.assertEqual(self.body_class(auth_pages.login_page()), "bg-login")
        self.assertEqual(self.body_class(auth_pages.register_page()), "bg-register")
        self.assertEqual(self.body_class(auth_pages.register_page(error="x", email="a@b.co", needs_code=True)), "bg-register")

    def test_the_pages_around_them_follow_the_page_they_belong_to(self):
        self.assertEqual(self.body_class(auth_pages.register_page(closed=True)), "bg-register")
        self.assertEqual(self.body_class(auth_pages.pending_page("a@b.co")), "bg-register")          # right after signing up
        self.assertEqual(self.body_class(auth_pages.blocked_page()), "bg-login")
        self.assertEqual(self.body_class(auth_pages.restore_page("a@b.co", "tok", "Nov 1, 2026", "Dec 1, 2026", 20)), "bg-login")
        self.assertEqual(self.body_class(auth_pages.notice_page("Title", "Message")), "bg-login")
        self.assertEqual(self.body_class(auth_pages.notice_page("Title", "Message", backdrop="register")), "bg-register")

    def test_the_css_points_each_class_at_its_file(self):
        self.assertIn("body.bg-login::before{background-image:url(/bg-login.svg)}", auth_pages._CSS)
        self.assertIn("body.bg-register::before{background-image:url(/bg-register.svg)}", auth_pages._CSS)
        for path in PICTURES:
            self.assertIn(path, api._APP_ASSETS)

    def test_an_unknown_picture_is_a_mistake_not_a_silent_blank(self):
        with self.assertRaises(ValueError):
            auth_pages._page("T", "body", backdrop="nope")

    def test_the_served_pages_carry_the_class_too(self):
        c = TestClient(api.app, follow_redirects=False)
        self.assertEqual(self.body_class(c.get("/login").text), "bg-login")
        self.assertEqual(self.body_class(c.get("/register").text), "bg-register")


class TheBoxStaysReadable(unittest.TestCase):
    def alpha(self, selector):
        rule = re.search(re.escape(selector) + r"\{[^}]*background:rgba\(\d+,\d+,\d+,([0-9.]+)\)", auth_pages._CSS)
        self.assertIsNotNone(rule, selector)
        return float(rule.group(1))

    def test_the_form_box_and_the_notice_are_nearly_opaque(self):
        self.assertGreaterEqual(self.alpha(".card"), 0.85)
        self.assertGreaterEqual(self.alpha(".disclaimer"), 0.85)

    def test_the_picture_is_a_fixed_layer_behind_everything_that_fills_the_screen(self):
        css = re.search(r"body::before\{([^}]*)\}", auth_pages._CSS).group(1)
        for needed in ('content:""', "position:fixed", "inset:0", "z-index:-1", "center/cover no-repeat"):
            self.assertIn(needed, css)


if __name__ == "__main__":
    unittest.main()
