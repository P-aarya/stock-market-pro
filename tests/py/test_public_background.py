"""The stock-themed picture behind the public price list, the page anyone sees at "/" before signing in: it is a safe, light image of its own,
it loads without a session, the page draws it as a fixed layer behind everything, and the panels and text in front of it stay readable."""
import os
import re
import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

import _path  # noqa: F401
from _accounts import AccountsTestCase, signed_in_client
import api

STATIC = os.path.join(_path.ROOT, "static")
NAME, ROUTE = "bg-public.svg", "/bg-public.svg"
OTHERS = ("bg-login.svg", "bg-register.svg", "bg-settings.svg")


def read(*parts, binary=False):
    with open(os.path.join(*parts), "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
        return f.read()


class ThePicture(unittest.TestCase):
    def test_it_is_a_valid_light_svg_that_fills_the_screen_whatever_its_shape(self):
        text = read(STATIC, NAME)
        root = ET.fromstring(text)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertIn("viewBox", root.attrib); self.assertEqual(root.attrib.get("preserveAspectRatio"), "xMidYMid slice")
        self.assertLess(len(text.encode("utf-8")), 60_000, "every signed-out visitor fetches it, so it should stay light")

    def test_it_is_a_picture_only_no_scripts_no_links_no_outside_files_no_text(self):
        text = read(STATIC, NAME)
        for forbidden in ("<script", "onload", "onclick", "<foreignObject", "<a ", "xlink:href", "href=", "http://www.w3.org/1999/xlink", "@import", "<image", "<style", "<text"):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertEqual(re.findall(r"https?://[^\"' )]+", text), ["http://www.w3.org/2000/svg"])             # only the SVG namespace

    def test_it_is_its_own_picture_not_a_copy_of_any_other_page_s(self):
        mine = read(STATIC, NAME)
        for other in OTHERS:
            self.assertNotEqual(mine, read(STATIC, other), other)

    def test_it_is_a_dark_picture_that_does_not_fight_the_text(self):
        text = read(STATIC, NAME)
        self.assertIn('fill="#050508"', text)                               # the app's own page colour underneath
        self.assertIn('id="vig"', text)                                     # a vignette darkens the edges

    def test_it_has_the_market_board_pieces_a_heat_map_of_up_and_down_tiles_sparklines_and_a_ticker_tape(self):
        text = read(STATIC, NAME)
        self.assertIn("#00e676", text); self.assertIn("#ff4444", text)       # up and down, in the app's own green and red
        self.assertGreaterEqual(text.count("<rect"), 40)                    # tiles, sparkline cards and tape bars


class HowItIsServed(AccountsTestCase, unittest.TestCase):
    def test_it_loads_before_anyone_signs_in_as_a_cacheable_image(self):
        c = TestClient(api.app, follow_redirects=False)
        r = c.get(ROUTE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/svg+xml")
        self.assertIn("max-age=", r.headers["cache-control"]); self.assertIn("public", r.headers["cache-control"])
        self.assertEqual(r.content, read(STATIC, NAME, binary=True))
        self.assertIn(ROUTE, api._APP_ASSETS); self.assertIn(ROUTE, api._OPEN_PATHS)

    def test_the_picture_route_does_not_open_up_the_rest_of_the_folder_or_the_account_pages(self):
        c = TestClient(api.app, follow_redirects=False)
        for path in ("/static/bg-public.svg", "/bg-public.svg/../make_icons.py", "/bg-other.svg", "/bg-public.svgx"):
            self.assertNotEqual(c.get(path).status_code, 200, path)
        self.assertEqual(c.get("/api/profile").status_code, 401)           # being able to fetch the picture opens nothing else
        self.assertEqual(c.get("/settings").status_code, 302)

    def test_the_public_page_carries_the_picture_and_still_fills_in_the_plan_text(self):
        r = TestClient(api.app, follow_redirects=False).get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("url(/bg-public.svg)", r.text)
        self.assertIn("/api/public/stocks", r.text); self.assertNotIn("<!--PLAN", r.text)
        self.assertIn("no-cache", r.headers["cache-control"])

    def test_the_dashboard_a_signed_in_person_gets_at_the_same_address_does_not_use_it(self):
        page = signed_in_client(1, tier=1).get("/")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("bg-public", page.text)


class ThePageStaysReadable(unittest.TestCase):
    css = read(_path.ROOT, "public.html").split("</style>")[0]

    def rule(self, selector):
        found = re.search(r"(?:^|\n)" + re.escape(selector) + r"\{([^}]*)\}", self.css)
        self.assertIsNotNone(found, selector)
        return found.group(1)

    def alphas(self, selector):
        values = [float(a) for a in re.findall(r"rgba\(\d+,\d+,\d+,([0-9.]+)\)", self.rule(selector))]
        self.assertTrue(values, selector)
        return values

    def test_the_price_table_the_sign_up_panel_and_the_figures_are_nearly_opaque(self):
        for selector in (".wrap", ".plan", ".stat"):
            self.assertGreaterEqual(min(self.alphas(selector)), 0.9, selector)

    def test_the_header_stays_nearly_solid(self):
        self.assertGreaterEqual(min(self.alphas(".bar")), 0.9)

    def test_the_long_price_table_has_no_blur_behind_it(self):
        # a blur over hundreds of rows makes scrolling heavy on a phone; at this opacity it would show nothing anyway
        self.assertNotIn("backdrop-filter", self.rule(".wrap"))

    def test_the_picture_is_a_fixed_layer_behind_everything_that_fills_the_screen(self):
        layer = self.rule("body::before")
        for needed in ('content:""', "position:fixed", "inset:0", "z-index:-1", "url(/bg-public.svg)", "center/cover no-repeat"):
            self.assertIn(needed, layer)

    def test_text_that_sits_straight_on_the_picture_has_a_dark_halo(self):
        # a chart line passing behind a letter would swallow it; measured in a browser, the halo is what keeps every letter above 4.5:1
        halo = self.rule("h1,.lead,.meta,.foot,.tabs button")
        self.assertGreaterEqual(halo.count("var(--bg)"), 2)
        self.assertIn("text-shadow", halo)

    def test_nothing_else_is_fixed_in_place(self):
        fixed = re.findall(r"([^{}\n]+)\{[^}]*position:fixed", self.css)
        self.assertEqual([s.strip() for s in fixed], ["body::before"])


if __name__ == "__main__":
    unittest.main()
