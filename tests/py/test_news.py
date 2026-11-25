"""
The News tab: headlines read from RSS/Atom feeds, stored once per story with every category and stock it was found
under, kept 30 days, and searched with the tab's filters. The route sits on the starting plan, "My stocks" only ever
uses the signed-in person's own portfolio and watchlist, and the page's category list matches the server's.
"""
import os
import re
import unittest
from datetime import datetime
from unittest import mock

import _path  # noqa: F401
import api
import news_store as ns
import tiers
from _accounts import signed_in_client
from _fakedb import FakeConn

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RSS = b"""<?xml version="1.0"?><rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>Fed holds rates steady - Reuters</title><link>https://news.google.com/a1</link>
  <pubDate>Mon, 24 Nov 2026 10:00:00 GMT</pubDate><source url="https://reuters.com">Reuters</source>
  <description>&lt;b&gt;The Fed&lt;/b&gt; waits &amp; watches</description><media:content url="https://img.example/1.jpg" medium="image"/></item>
<item><title>No link here</title></item>
<item><title>Bad link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Chips rally</title><link href="https://y.example/1"/>
<updated>2026-11-23T08:00:00Z</updated><summary>Up 5%</summary></entry></feed>"""


class Feeds(unittest.TestCase):
    def test_rss(self):
        a = ns.parse_feed(RSS, "Google News")
        self.assertEqual(len(a), 1)                                   # no link / not http: skipped
        self.assertEqual(a[0]["title"], "Fed holds rates steady")     # Google's " - Publisher" is taken off
        self.assertEqual(a[0]["source"], "Reuters")
        self.assertEqual(a[0]["summary"], "The Fed waits & watches")
        self.assertEqual(a[0]["image"], "https://img.example/1.jpg")
        self.assertEqual(a[0]["published_at"], datetime(2026, 11, 24, 10, 0))

    def test_atom(self):
        a = ns.parse_feed(ATOM, "X")
        self.assertEqual((a[0]["url"], a[0]["source"], a[0]["published_at"]), ("https://y.example/1", "X", datetime(2026, 11, 23, 8)))

    def test_the_same_story_has_one_key(self):
        self.assertEqual(ns.title_key("Fed holds rates, steady!"), ns.title_key("fed holds rates steady"))
        self.assertNotEqual(ns.title_key("Fed holds"), ns.title_key("Fed cuts"))

    def test_every_category_has_feeds(self):
        self.assertEqual(set(ns.FEEDS) | {"stocks"}, set(ns.CATEGORY_KEYS))
        self.assertEqual([k for k, _, on in ns.CATEGORIES if not on], ["entertainment", "sport"])


class Storing(unittest.TestCase):
    def test_sql_works_in_ansi_mode(self):
        # the live database runs with ANSI_QUOTES and PIPES_AS_CONCAT: no || and no double-quoted strings
        for sql in (ns.SAVE, ns.TABLE):
            self.assertNotIn("||", sql)
            self.assertNotIn('"', sql)

    def test_save_and_clean_up(self):
        conn = FakeConn(lambda sql, p: 1)
        ns._ready[0] = False
        new = ns.save(conn, ns.parse_feed(RSS, "G"), "economic", "AAPL")
        self.assertEqual(new, 1)
        sql, params = [x for x in conn.log if x[0].startswith("INSERT")][0]
        self.assertEqual(params[-2:], ("economic", "AAPL"))
        ns.clean_up(conn)
        self.assertTrue(any("DELETE FROM news_articles" in s and p == (30,) for s, p in conn.log))

    def test_a_date_in_the_future_is_stored_as_now(self):
        conn = FakeConn(lambda sql, p: 1)
        ns.save(conn, [{"title": "t", "url": "https://x", "published_at": datetime(2099, 1, 1)}], "world")
        when = [p for s, p in conn.log if s.startswith("INSERT")][0][6]
        self.assertLess(when.year, 2099)


class Searching(unittest.TestCase):
    def run_search(self, **kw):
        conn = FakeConn(lambda sql, p: [])
        ns.search(lambda: conn, **kw)
        return [(s, p) for s, p in conn.log if s.startswith("SELECT")][0]

    def test_filters(self):
        sql, p = self.run_search(categories=["tech", "bogus", "crypto"], days=7, q="50%_off", offset=20, limit=20)
        self.assertEqual(sql.count("FIND_IN_SET"), 2)
        self.assertEqual(p[0], 7)
        self.assertIn("tech", p); self.assertNotIn("bogus", p)
        self.assertIn("%50\\%\\_off%", p)                                  # % and _ are searched for literally
        self.assertEqual(tuple(p[-2:]), (21, 20))                                # one extra row says whether there's more

    def test_limits(self):
        _, p = self.run_search(days=999, limit=500)
        self.assertEqual((p[0], p[-2]), (30, 51))

    def test_nothing_chosen_returns_nothing_without_asking_the_database(self):
        boom = lambda: self.fail("should not connect")
        self.assertEqual(ns.search(boom, categories=[]), ([], False))
        self.assertEqual(ns.search(boom, tickers=[]), ([], False))


class Route(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_plan(self):
        self.assertEqual(tiers.feature_for("GET", "/api/news"), tiers.feature_for("GET", "/api/news/world"))

    def test_my_stocks_are_my_own(self):
        seen = {}
        mock.patch.object(api.portfolio, "held_tickers", side_effect=lambda uid: {"AAPL": "AAPL"} if uid == 5 else {"ZZZ": "ZZZ"}).start()
        mock.patch.object(api.watchlist, "get_watchlist", side_effect=lambda uid: ["NVDA"] if uid == 5 else ["YYY"]).start()
        mock.patch.object(api.news_store, "search", side_effect=lambda db, c, t, *a: seen.update(cats=c, tickers=t) or ([], False)).start()
        d = signed_in_client(5).get("/api/news?mine=true&cats=world").json()
        self.assertEqual(seen, {"cats": ["world"], "tickers": ["AAPL", "NVDA"]})
        self.assertEqual(d["mine_count"], 2)
        self.assertEqual([c["key"] for c in d["categories"]], ns.CATEGORY_KEYS)

    def test_one_stock_is_fetched_when_little_is_stored(self):
        calls = []
        mock.patch.object(api.news_store, "search", side_effect=lambda *a: calls.append(a[2]) or ([], False)).start()
        fetched = mock.patch.object(api.news_store, "fetch_stock_now", return_value=3).start()
        signed_in_client(5).get("/api/news?ticker=msft")
        fetched.assert_called_once_with(api.get_db, "MSFT")
        self.assertEqual(calls, [["MSFT"], ["MSFT"]])

    def test_an_error_is_explained(self):
        mock.patch.object(api.news_store, "search", side_effect=RuntimeError("db down")).start()
        d = signed_in_client(5).get("/api/news").json()
        self.assertEqual(d["status"], "error")
        self.assertEqual(d["items"], [])


class Page(unittest.TestCase):
    def test_the_page_lists_the_same_categories(self):
        with open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8") as f:
            html = f.read()
        block = re.search(r"const NW_CATS=\[(.*?)\];", html, re.S).group(1)
        page = [(k, l, bool(int(on))) for k, l, on in re.findall(r"\['(\w+)','([^']+)',(\d)\]", block)]
        self.assertEqual(page, ns.CATEGORIES)

    def test_the_price_updater_starts_the_collector(self):
        with open(os.path.join(ROOT, "scheduler.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("news_store.start(get_db)", src)


if __name__ == "__main__":
    unittest.main()
