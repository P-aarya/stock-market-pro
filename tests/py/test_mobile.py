"""The files a phone needs to install the app on its home screen: a manifest and icons, served without a login."""
import json
import os
import struct
import unittest

from fastapi.testclient import TestClient

import _path  # noqa: F401
import api

STATIC = os.path.join(_path.ROOT, "static")


def png_size(path):
    """(width, height) read from a PNG's header, so the test needs no image library."""
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", head[16:24])


class Manifest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(STATIC, "manifest.webmanifest"), encoding="utf-8") as f:
            self.m = json.load(f)

    def test_describes_an_installable_app(self):
        for key in ("name", "short_name", "start_url", "display", "icons", "theme_color", "background_color"):
            self.assertIn(key, self.m)
        self.assertEqual(self.m["display"], "standalone")
        self.assertEqual(self.m["start_url"], "/")
        self.assertLessEqual(len(self.m["short_name"]), 12, "longer names get cut off under the home-screen icon")

    def test_icons_exist_and_are_the_size_they_claim(self):
        sizes = set()
        for icon in self.m["icons"]:
            path = os.path.join(STATIC, os.path.basename(icon["src"]))
            self.assertTrue(os.path.exists(path), icon["src"])
            w, h = png_size(path)
            self.assertEqual(f"{w}x{h}", icon["sizes"], icon["src"])
            sizes.add(w)
        self.assertTrue({192, 512} <= sizes, "Android wants at least a 192 and a 512 icon")

    def test_ios_touch_icon_is_180_square(self):
        self.assertEqual(png_size(os.path.join(STATIC, "apple-touch-icon.png")), (180, 180))

    def test_every_icon_path_the_manifest_and_page_use_is_served(self):
        for icon in self.m["icons"]:
            self.assertIn(icon["src"], api._APP_ASSETS)
        self.assertIn("/apple-touch-icon.png", api._APP_ASSETS)


class Serving(unittest.TestCase):
    def test_served_with_the_right_content_types(self):
        c = TestClient(api.app)
        for path, (_, media_type) in api._APP_ASSETS.items():
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertTrue(r.headers["content-type"].startswith(media_type), path)
            self.assertTrue(r.content, path)
        self.assertEqual(c.get("/manifest.webmanifest").json()["short_name"], "Stock Pro")
        self.assertEqual(c.get("/apple-touch-icon.png").content[:4], b"\x89PNG")

    def test_reachable_without_signing_in(self):
        # A phone fetches the icon and manifest without the login cookie; they hold nothing private.
        c = TestClient(api.app, follow_redirects=False)
        for path in api._APP_ASSETS:
            self.assertEqual(c.get(path).status_code, 200, path)
        self.assertEqual(c.get("/api/portfolio").status_code, 401)   # the data stays locked
        self.assertEqual(c.get("/").status_code, 302)

    def test_the_dashboard_links_to_them(self):
        with open(os.path.join(_path.ROOT, "dashboard.html"), encoding="utf-8") as f:
            page = f.read()
        for path in ("/manifest.webmanifest", "/apple-touch-icon.png"):
            self.assertIn(f'href="{path}"', page)


if __name__ == "__main__":
    unittest.main()
