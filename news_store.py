"""
The News tab's store: headlines collected every 30 minutes and kept for 30 days.

    collect(get_db)       one round: every category's feeds plus news for a share of the tracked stocks; then deletes
                          anything older than KEEP_DAYS. Run by the price updater (scheduler.py) in its own thread.
    search(get_db, ...)   what the News tab shows, with its filters; newest first, a page at a time.

Only the headline, source, time, link, a short snippet and the picture's address are stored; a click opens the
article on the publisher's site. The same story from two feeds is stored once (by its headline) and keeps every
category and ticker it was found under.
"""
import email.utils
import hashlib
import html
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import commodity_groups

KEEP_DAYS = 30
EVERY = 30 * 60                  # seconds between collection rounds
PER_FEED = 25                    # headlines taken from each feed per round
STOCKS_PER_ROUND = 40            # stock-news feeds per round (the rest wait for the next round)

CATEGORIES = [                   # key, label, on by default in the News tab
    ("world", "World", True), ("global", "Global", True), ("finance", "Finance", True), ("business", "Business", True),
    ("economic", "Economic", True), ("tax", "Tax", True), ("retail", "Retail", True), ("tech", "Tech", True),
    ("crypto", "Crypto", True), ("commodities", "Commodities", True), ("stocks", "Stock news", True),
    ("entertainment", "Entertainment", False), ("sport", "Sport", False),
]
CATEGORY_KEYS = [c[0] for c in CATEGORIES]


def _gn_topic(topic):
    return f"https://news.google.com/rss/headlines/section/topic/{topic}?hl=en-US&gl=US&ceid=US:en"


def _gn_search(q):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})


FEEDS = {                        # category -> [(feed url, source name used when the feed doesn't say)]
    "world": [(_gn_topic("WORLD"), "Google News"), ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC")],
    "global": [("https://www.cnbc.com/id/100727362/device/rss/rss.html", "CNBC"), (_gn_search("global markets"), "Google News")],
    "finance": [("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC"),
                ("https://feeds.marketwatch.com/marketwatch/topstories", "MarketWatch"), (_gn_search("stock market"), "Google News")],
    "business": [(_gn_topic("BUSINESS"), "Google News"), ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC"),
                 ("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "NY Times")],
    "economic": [("https://www.cnbc.com/id/20910258/device/rss/rss.html", "CNBC"),
                 (_gn_search("economy inflation OR \"interest rates\" OR GDP OR jobs report"), "Google News")],
    "tax": [(_gn_search("tax policy OR IRS OR HMRC OR tax cuts OR tariffs"), "Google News")],
    "retail": [(_gn_search("retail sales OR retailers OR consumer spending"), "Google News")],
    "tech": [("https://techcrunch.com/feed/", "TechCrunch"), (_gn_topic("TECHNOLOGY"), "Google News")],
    "crypto": [("https://feeds.feedburner.com/CoinDesk", "CoinDesk"), ("https://cointelegraph.com/rss", "CoinTelegraph"),
               (_gn_search("bitcoin OR crypto OR ethereum"), "Google News")],
    "commodities": [("https://oilprice.com/rss/main", "OilPrice"), (_gn_search("\"oil prices\" OR \"gold price\" OR commodities"), "Google News")],
    "entertainment": [(_gn_topic("ENTERTAINMENT"), "Google News")],
    "sport": [(_gn_topic("SPORTS"), "Google News")],
}

TABLE = """CREATE TABLE IF NOT EXISTS news_articles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    title_key CHAR(40) NOT NULL,
    title VARCHAR(400) NOT NULL,
    url VARCHAR(1000) NOT NULL,
    source VARCHAR(120) NOT NULL DEFAULT '',
    summary VARCHAR(300) NOT NULL DEFAULT '',
    image VARCHAR(1000) NULL,
    published_at DATETIME NOT NULL,
    categories VARCHAR(255) NOT NULL DEFAULT '',
    tickers VARCHAR(255) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_news_title (title_key),
    KEY idx_news_published (published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/rss+xml, application/xml, text/xml, */*"}
_MEDIA = "{http://search.yahoo.com/mrss/}"
_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_ready = [False]


# ---------- reading feeds ----------
def _text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _when(s):
    """A feed's date -> naive UTC datetime, or None."""
    if not s:
        return None
    s = s.strip()
    for parse in (email.utils.parsedate_to_datetime, lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))):
        try:
            d = parse(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            continue
    return None


def _image(item):
    for tag in (_MEDIA + "content", _MEDIA + "thumbnail"):
        for el in item.iter(tag):
            url = el.get("url")
            if url and (el.get("medium") in (None, "image") or tag.endswith("thumbnail")):
                return url
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image") and enc.get("url"):
        return enc.get("url")
    for raw in (item.findtext("description"), item.findtext(_CONTENT + "encoded")):
        m = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", raw or "")
        if m:
            return html.unescape(m.group(1))
    return None


def parse_feed(data, source, limit=PER_FEED):
    """RSS or Atom bytes -> [{title, url, source, summary, image, published_at}]."""
    root = ET.fromstring(data)
    items = root.findall("./channel/item") or root.findall(".//item") or root.findall(_ATOM + "entry")
    out = []
    for item in items[:limit]:
        title = _text(item.findtext("title") or item.findtext(_ATOM + "title"))
        link = (item.findtext("link") or "").strip()
        if not link:
            a = item.find(_ATOM + "link")
            link = a.get("href", "") if a is not None else ""
        if not title or not link.startswith("http"):
            continue
        src_el = item.find("source")
        src = (src_el.text or "").strip() if src_el is not None and src_el.text else source
        if "news.google.com" in link:                  # Google News titles end with " - Publisher"
            m = re.match(r"^(.*\S)\s+-\s+([^-]{2,60})$", title)
            if m:
                title, src = m.group(1), (src if src != source else m.group(2).strip())
        out.append({"title": title[:400], "url": link[:1000], "source": (src or source)[:120],
                    "summary": _text(item.findtext("description") or item.findtext(_ATOM + "summary"))[:300],
                    "image": (_image(item) or None),
                    "published_at": _when(item.findtext("pubDate") or item.findtext(_ATOM + "published") or item.findtext(_ATOM + "updated"))})
    return out


def fetch(url, source, timeout=8):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_feed(r.read(), source)


def title_key(title):
    return hashlib.sha1(re.sub(r"[^a-z0-9]", "", title.lower())[:160].encode()).hexdigest()


# ---------- storing ----------
def ensure(cur):
    if not _ready[0]:
        cur.execute(TABLE)
        _ready[0] = True


SAVE = """INSERT INTO news_articles (title_key, title, url, source, summary, image, published_at, categories, tickers)
          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
          ON DUPLICATE KEY UPDATE
            categories = IF(VALUES(categories) = '' OR FIND_IN_SET(VALUES(categories), categories) > 0, categories,
                            IF(categories = '', VALUES(categories), LEFT(CONCAT(categories, ',', VALUES(categories)), 255))),
            tickers = IF(VALUES(tickers) = '' OR FIND_IN_SET(VALUES(tickers), tickers) > 0, tickers,
                         IF(tickers = '', VALUES(tickers), LEFT(CONCAT(tickers, ',', VALUES(tickers)), 255))),
            image = COALESCE(image, VALUES(image))"""


def save(conn, articles, category, ticker=""):
    """Store articles under `category` (and `ticker`). Returns how many were new."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cur = conn.cursor()
    ensure(cur)
    new = 0
    for a in articles:
        when = a.get("published_at") or now
        if when > now:
            when = now
        cur.execute(SAVE, (title_key(a["title"]), a["title"], a["url"], a.get("source") or "", a.get("summary") or "",
                           a.get("image"), when, category, ticker))
        if cur.rowcount == 1:                          # 1 = inserted, 2 = an existing row got another category or ticker
            new += 1
    conn.commit()
    cur.close()
    return new


def clean_up(conn, days=KEEP_DAYS):
    cur = conn.cursor()
    ensure(cur)
    cur.execute("DELETE FROM news_articles WHERE published_at < UTC_TIMESTAMP() - INTERVAL %s DAY", (int(days),))
    gone = cur.rowcount
    conn.commit()
    cur.close()
    return gone


# ---------- one collection round ----------
_stock_turn = [0]


def tracked_tickers(conn, top=40):
    """Stocks whose news is collected: everything in anyone's portfolio or watchlist, plus the biggest companies."""
    cur = conn.cursor()
    out = []
    for sql in ("SELECT DISTINCT ticker FROM portfolio WHERE quantity > 0",
                "SELECT DISTINCT ticker FROM watchlist",
                f"SELECT ticker FROM stocks_live WHERE current_price IS NOT NULL ORDER BY market_cap DESC LIMIT {int(top)}"):
        try:
            cur.execute(sql)
            out += [r[0] for r in cur.fetchall()]
        except Exception:
            pass
    cur.close()
    seen = []
    for t in out:
        t = str(t or "").strip().upper()
        if t and "=" not in t and not t.startswith("^") and re.match(r"^[A-Z0-9.\-]{1,10}$", t) and t not in seen \
                and not commodity_groups.direct(t):        # gold, oil... have their own category, not a stock feed
            seen.append(t)
    return seen


def stock_feed(ticker):
    return f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(ticker)}&region=US&lang=en-US"


def collect(get_db, log=print):
    started = time.time()
    conn = get_db()
    try:
        new = feeds_ok = feeds_failed = 0
        for cat, feeds in FEEDS.items():
            for url, source in feeds:
                try:
                    new += save(conn, fetch(url, source), cat)
                    feeds_ok += 1
                except Exception:
                    feeds_failed += 1
                time.sleep(0.5)
        tickers = tracked_tickers(conn)
        if tickers:
            start = _stock_turn[0] % len(tickers)
            turn = (tickers[start:] + tickers[:start])[:STOCKS_PER_ROUND]
            _stock_turn[0] = start + len(turn)
            for t in turn:
                try:
                    new += save(conn, fetch(stock_feed(t), "Yahoo Finance"), "stocks", t)
                    feeds_ok += 1
                except Exception:
                    feeds_failed += 1
                time.sleep(1)
        gone = clean_up(conn)
        log(f"  📰 News: {new} new headlines from {feeds_ok} feeds ({feeds_failed} failed), {gone} older than "
            f"{KEEP_DAYS} days removed, {time.time() - started:.0f}s")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_forever(get_db, log=print):
    """Collect now and then every 30 minutes; never lets an error stop it."""
    while True:
        try:
            collect(get_db, log)
        except Exception as e:
            log(f"  📰 News collection failed this round: {type(e).__name__}: {str(e)[:120]}")
        time.sleep(EVERY)


def start(get_db, log=print):
    threading.Thread(target=run_forever, args=(get_db, log), name="news", daemon=True).start()


# ---------- the News tab ----------
def _like(s):
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(get_db, categories=None, tickers=None, days=30, q="", offset=0, limit=20):
    """Newest first. categories: keys to include (None = all). tickers: only news tagged with one of them (None = any).
    Returns (items, more)."""
    where, params = ["published_at >= UTC_TIMESTAMP() - INTERVAL %s DAY"], [max(1, min(int(days), KEEP_DAYS))]
    if categories is not None:
        cats = [c for c in categories if c in CATEGORY_KEYS]
        if not cats:
            return [], False
        where.append("(" + " OR ".join(["FIND_IN_SET(%s, categories) > 0"] * len(cats)) + ")")
        params += cats
    if tickers is not None:
        ts = [t for t in tickers if re.match(r"^[A-Z0-9.\-]{1,10}$", t)][:200]
        if not ts:
            return [], False
        where.append("(" + " OR ".join(["FIND_IN_SET(%s, tickers) > 0"] * len(ts)) + ")")
        params += ts
    q = " ".join(str(q or "").split())[:80]
    if q:
        where.append("(title LIKE %s OR summary LIKE %s)")
        params += [_like(q), _like(q)]
    limit = max(1, min(int(limit), 50))
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        ensure(cur)
        cur.execute("SELECT id, title, url, source, summary, image, published_at, categories, tickers FROM news_articles WHERE "
                    + " AND ".join(where) + " ORDER BY published_at DESC, id DESC LIMIT %s OFFSET %s",
                    params + [limit + 1, max(0, int(offset))])
        rows = cur.fetchall()
    finally:
        conn.close()
    more = len(rows) > limit
    items = [{"id": r["id"], "title": r["title"], "url": r["url"], "source": r["source"], "summary": r["summary"],
              "image": r["image"], "published": int(r["published_at"].replace(tzinfo=timezone.utc).timestamp()),
              "categories": [c for c in (r["categories"] or "").split(",") if c],
              "tickers": [t for t in (r["tickers"] or "").split(",") if t]} for r in rows[:limit]]
    return items, more


def fetch_stock_now(get_db, ticker):
    """A stock nobody tracks yet: fetch its news once so the filter isn't empty. Returns how many were new."""
    conn = get_db()
    try:
        return save(conn, fetch(stock_feed(ticker), "Yahoo Finance"), "stocks", ticker)
    finally:
        conn.close()
