"""The stock-themed picture behind the Settings page: it is a safe, light image of its own, it is served as a cacheable image, the page
draws it as a fixed layer behind everything, and the cards and the section list in front of it stay nearly opaque so the text is readable."""
import os
import re
import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

import _path  # noqa: F401
from _accounts import AccountsTestCase, signed_in_client
import api

STATIC = os.path.join(_path.ROOT, "static")
NAME, ROUTE = "bg-settings.svg", "/bg-settings.svg"


def read(*parts, binary=False):
    with open(os.path.join(*parts), "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
        return f.read()


class ThePicture(unittest.TestCase):
    def test_it_is_a_valid_light_svg_that_fills_the_screen_whatever_its_shape(self):
        text = read(STATIC, NAME)
        root = ET.fromstring(text)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertIn("viewBox", root.attrib); self.assertEqual(root.attrib.get("preserveAspectRatio"), "xMidYMid slice")
        self.assertLess(len(text.encode("utf-8")), 60_000, "it is fetched by every visit to Settings, so it should stay light")

    def test_it_is_a_picture_only_no_scripts_no_links_no_outside_files_no_text(self):
        text = read(STATIC, NAME)
        for forbidden in ("<script", "onload", "onclick", "<foreignObject", "<a ", "xlink:href", "href=", "http://www.w3.org/1999/xlink", "@import", "<image", "<style", "<text"):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertEqual(re.findall(r"https?://[^\"' )]+", text), ["http://www.w3.org/2000/svg"])             # only the SVG namespace

    def test_it_is_its_own_picture_not_a_copy_of_the_sign_in_ones(self):
        mine = read(STATIC, NAME)
        for other in ("bg-login.svg", "bg-register.svg"):
            self.assertNotEqual(mine, read(STATIC, other), other)

    def test_it_is_a_dark_picture_that_does_not_fight_the_text(self):
        text = read(STATIC, NAME)
        self.assertIn('fill="#050508"', text)                               # the app's own page colour underneath
        self.assertIn('id="vig"', text)                                     # a vignette darkens the edges, where the text of a narrow screen sits


class HowItIsServed(AccountsTestCase, unittest.TestCase):
    def test_it_loads_as_a_cacheable_image(self):
        c = TestClient(api.app, follow_redirects=False)
        r = c.get(ROUTE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/svg+xml")
        self.assertIn("max-age=", r.headers["cache-control"]); self.assertIn("public", r.headers["cache-control"])
        self.assertEqual(r.content, read(STATIC, NAME, binary=True))
        self.assertIn(ROUTE, api._APP_ASSETS); self.assertIn(ROUTE, api._OPEN_PATHS)

    def test_the_picture_route_does_not_open_up_the_rest_of_the_folder(self):
        c = TestClient(api.app, follow_redirects=False)
        for path in ("/static/bg-settings.svg", "/bg-settings.svg/../make_icons.py", "/bg-other.svg", "/bg-settings.svgx"):
            self.assertNotEqual(c.get(path).status_code, 200, path)

    def test_the_settings_page_itself_still_needs_a_sign_in_and_carries_the_picture(self):
        self.assertEqual(TestClient(api.app, follow_redirects=False).get("/settings").status_code, 302)
        page = signed_in_client(1, tier=1).get("/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn("url(/bg-settings.svg)", page.text)


class ThePageStaysReadable(unittest.TestCase):
    css = read(_path.ROOT, "settings.html").split("</style>")[0]

    def alpha(self, selector):
        rule = re.search(r"(?:^|\n)" + re.escape(selector) + r"\{[^}]*background:rgba\(\d+,\d+,\d+,([0-9.]+)\)", self.css)
        self.assertIsNotNone(rule, selector)
        return float(rule.group(1))

    def test_the_cards_and_the_section_list_are_nearly_opaque(self):
        self.assertGreaterEqual(self.alpha(".card"), 0.85)
        self.assertGreaterEqual(self.alpha(".side"), 0.85)

    def test_on_a_phone_the_section_list_keeps_that_panel_instead_of_becoming_bare_again(self):
        phone = self.css[self.css.index("@media(max-width:760px){"):]
        side = re.search(r"\n  \.side\{([^}]*)\}", phone).group(1)
        self.assertNotIn("background", side)                                # it inherits the panel above; it must not switch to nothing
        self.assertNotIn("background:none", side)

    def test_the_picture_is_a_fixed_layer_behind_everything_that_fills_the_screen(self):
        layer = re.search(r"body::before\{([^}]*)\}", self.css).group(1)
        for needed in ('content:""', "position:fixed", "inset:0", "z-index:-1", "url(/bg-settings.svg)", "center/cover no-repeat"):
            self.assertIn(needed, layer)

    def test_the_header_stays_solid(self):
        self.assertIn(".top{background:var(--bg1)", self.css)

    def test_nothing_fixed_lives_inside_a_blurred_panel(self):
        # backdrop-filter makes a panel the reference for anything fixed inside it, which would drag it out of place: the toast is outside
        html = read(_path.ROOT, "settings.html")
        fixed = re.findall(r"([^{}\n]+)\{[^}]*position:fixed", self.css)
        self.assertEqual(sorted(s.strip() for s in fixed), [".toast", "body::before"])
        self.assertGreater(html.index('<div class="toast"'), html.index("</main>"))


if __name__ == "__main__":
    unittest.main()
