"""
The Learn tab: beginner videos (open on YouTube), investing podcasts (open on Apple Podcasts) and practical investing
tips. Administrators add, edit, reorder and remove items in the Admin Center.

The starter list was checked by hand: every video and podcast link opens a real page, and every tip is based on
guidance published by the US regulators' investor-education sites (the SEC's Investor.gov and sec.gov, and FINRA),
with a link to that page so anyone can check it.

The starter list is written once, when the table is first created. After that only the Admin Center changes it, so
deleting everything really leaves it empty. Databases created with the first version (which had famous-investor
quotes instead of tips) are upgraded once: the quotes still there are replaced by the tips and the new videos are
added. A small learn_meta table remembers that the upgrade was done.
"""
import re
import threading
import time
from urllib.parse import parse_qs, urlparse

KINDS = ("video", "podcast", "tip")
MAX_TITLE, MAX_SOURCE, MAX_URL, MAX_BODY = 200, 120, 500, 600
MAX_ITEMS = 200
CONTENT_VERSION = 2

TABLE = """CREATE TABLE learn_items (
  id INT NOT NULL AUTO_INCREMENT,
  kind VARCHAR(10) NOT NULL,
  title VARCHAR(300) NOT NULL,
  body TEXT NULL,
  url VARCHAR(500) NULL,
  source VARCHAR(120) NULL,
  position INT NOT NULL DEFAULT 0,
  created_at INT NOT NULL,
  updated_at INT NOT NULL,
  PRIMARY KEY (id),
  KEY idx_learn_kind (kind, position)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

META_TABLE = """CREATE TABLE IF NOT EXISTS learn_meta (
  k VARCHAR(40) NOT NULL,
  v VARCHAR(200) NOT NULL,
  PRIMARY KEY (k)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

SEC_TEN = "https://www.sec.gov/investor/pubs/tenthingstoconsider.htm"
GOV_TIPS_2025 = "https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/ten-investment-tips-2025-investor-bulletin"
FINRA_TURBULENT = "https://www.finra.org/investors/insights/tips-turbulent-market"

# (kind, title, url, source, body)
VIDEOS = [
    ("video", "What it means to buy a company's stock", "https://www.youtube.com/watch?v=98qfFzqDKR8", "Khan Academy", None),
    ("video", "Investing 101", "https://www.youtube.com/watch?v=1Ob-hAYCnJE", "Ben Felix", None),
    ("video", "How to invest in the stock market for beginners", "https://www.youtube.com/watch?v=oYJaLgJL0Ok", "Graham Stephan", None),
    ("video", "Bonds vs. stocks", "https://www.youtube.com/watch?v=rs1md3e4aYU", "Khan Academy", None),
    ("video", "Market capitalization", "https://www.youtube.com/watch?v=5lmHzAHbtzg", "Khan Academy", None),
    ("video", "Introduction to the price-to-earnings (P/E) ratio", "https://www.youtube.com/watch?v=cppxO67e6eo", "Khan Academy", None),
    ("video", "Candlesticks, trendlines and chart patterns explained", "https://www.youtube.com/watch?v=4c5WKOB3U5c", "Whiteboard Crypto", None),
    ("video", "The risk of individual stocks", "https://www.youtube.com/watch?v=RxCqxhRsHiY", "Ben Felix", None),
    ("video", "The case for index funds", "https://www.youtube.com/watch?v=Nv5CiRSCVxA", "Ben Felix", None),
    ("video", "Choosing an asset allocation (how much in stocks vs. bonds?)", "https://www.youtube.com/watch?v=p25PPBgMiEk", "Ben Felix", None),
    ("video", "Market crashes (2025 edition)", "https://www.youtube.com/watch?v=_9c-DkBFS3w", "Ben Felix", None),
    ("video", "The most important lessons in investing", "https://www.youtube.com/watch?v=MOjS2zuQMdo", "Ben Felix", None),
    ("video", "Do stocks return 10% on average?", "https://www.youtube.com/watch?v=Yl3NxTS_DgY", "Ben Felix", None),
    ("video", "Why betting on \"winning\" industries almost never works", "https://www.youtube.com/watch?v=3B9umhfv_ww", "Ben Felix", None),
    ("video", "Why you will (probably) lose money trading options", "https://www.youtube.com/watch?v=K-U6eoICrYQ", "Ben Felix", None),
    ("video", "What about Warren Buffett?", "https://www.youtube.com/watch?v=32SnTUtdsOI", "Ben Felix", None),
    ("video", "Risk and reward: an introduction", "https://www.youtube.com/watch?v=mv5zucjq60k", "Khan Academy", None),
    ("video", "Index fund investing for beginners", "https://www.youtube.com/watch?v=fwe-PjrX23o", "Graham Stephan", None),
    ("video", "How an IPO works", "https://www.youtube.com/watch?v=ywOr4OYqXOs", "Khan Academy", None),
    ("video", "Shorting a stock", "https://www.youtube.com/watch?v=-IDmLERenrU", "Khan Academy", None),
]
PODCASTS = [
    ("podcast", "We Study Billionaires", "https://podcasts.apple.com/us/podcast/we-study-billionaires-the-investors-podcast-network/id928933489",
     "The Investor's Podcast Network", None),
    ("podcast", "Motley Fool Money", "https://podcasts.apple.com/us/podcast/motley-fool-money/id306106212", "The Motley Fool", None),
    ("podcast", "Odd Lots", "https://podcasts.apple.com/us/podcast/odd-lots/id1056200096", "Bloomberg", None),
    ("podcast", "Planet Money", "https://podcasts.apple.com/us/podcast/planet-money/id290783428", "NPR", None),
    ("podcast", "Invest Like the Best", "https://podcasts.apple.com/us/podcast/id1154105909", "Patrick O'Shaughnessy", None),
    ("podcast", "Chat With Traders", "https://podcasts.apple.com/us/podcast/chat-with-traders/id957265404", "Chat With Traders", None),
]
TIPS = [
    ("tip", "Build an emergency fund before you invest", SEC_TEN, "SEC",
     "Keep savings for emergencies such as losing your job; the SEC notes some investors keep up to six months of income "
     "aside. Then you won't be forced to sell investments at a bad time to pay bills."),
    ("tip", "Pay off high-interest credit card debt first", SEC_TEN, "SEC",
     "Paying off a card that charges high interest is a guaranteed return. No investment reliably beats it with less "
     "risk, so clear that debt as quickly as you can before you start investing."),
    ("tip", "Don't put too much into one stock", SEC_TEN, "SEC",
     "If one company (including the one you work for) runs into trouble, a big holding in it can mean large losses. "
     "Spread your money so that no single stock can sink your plans."),
    ("tip", "Spread your money across different investments", GOV_TIPS_2025, "SEC – Investor.gov",
     "Owning many different investments lowers your overall risk and smooths out the ups and downs. Funds and ETFs are "
     "an easier way to do this than picking lots of individual stocks yourself."),
    ("tip", "Invest a fixed amount on a regular schedule", "https://www.finra.org/investors/insights/three-things-know-about-dollar-cost-averaging", "FINRA",
     "Investing the same amount every month (dollar-cost averaging) buys more shares when prices are low and fewer when "
     "they are high, and takes emotion out of the timing. Keep in mind that investing a lump sum at once has often "
     "earned more over long periods."),
    ("tip", "When the market drops, don't make snap decisions", FINRA_TURBULENT, "FINRA",
     "Big swings tempt people to sell in a panic or buy in a rush. Instead, go back to your goals and your long-term "
     "plan, and stick to it. You can't control the market, but you can control your decisions."),
    ("tip", "Small fees make a big difference over time",
     "https://www.investor.gov/news-alerts/investor-bulletins/investor-bulletin-how-fees-expenses-affect-your-investment-portfolio",
     "SEC – Investor.gov",
     "$100,000 growing 4% a year for 20 years ends at about $208,000 with a 0.25% yearly fee, but only about $179,000 "
     "with a 1% fee. Check what you pay to buy, hold and sell, and compare costs before you choose."),
    ("tip", "Read the annual report before you buy",
     "https://www.investor.gov/introduction-investing/getting-started/researching-investments/how-read-10-k", "SEC – Investor.gov",
     "A company's annual report (10-K) has three key parts: Risk Factors (its biggest risks), Management's Discussion "
     "and Analysis (the year's results in management's own words) and the audited financial statements. Find it in "
     "the Filings tab of any stock."),
    ("tip", "Be careful with \"hot\" stocks from social media",
     "https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/investor-alert-thinking-about-investing-latest-hot-stock-understand-significant-risks-short-term",
     "SEC – Investor.gov",
     "Buying a stock because it's trending can lead to large losses, and hype is sometimes used to manipulate prices. "
     "Treat social media as a starting point only, research the company, and never feel pressured to buy right away."),
    ("tip", "Learn the signs of a pump-and-dump", "https://www.investor.gov/protect-your-investments/fraud/types-fraud/pump-and-dump-schemes",
     "SEC – Investor.gov",
     "Messages urging you to buy a stock quickly, or claiming \"inside information\", are warning signs. Promoters push "
     "the price up, sell their own shares, and then the price usually falls, so people who bought late lose money."),
    ("tip", "Be wary of \"guaranteed\" returns", FINRA_TURBULENT, "FINRA",
     "Scammers promise risk-free or guaranteed earnings, especially when markets are shaky. Real investments carry "
     "risk. Be suspicious of anyone who guarantees a profit, and check that the person is registered (FINRA BrokerCheck)."),
    ("tip", "Understand margin before you borrow to invest",
     "https://www.investor.gov/news-alerts/investor-bulletins/investor-bulletin-understanding-margin-accounts", "SEC – Investor.gov",
     "Buying with borrowed money (margin) magnifies losses: you can lose more than you put in, and your broker can sell "
     "your shares without asking you, to pay back the loan."),
    ("tip", "Watch out for your own habits", GOV_TIPS_2025, "SEC – Investor.gov",
     "Trading too often, chasing last year's winners, holding losing stocks too long and selling winners too early all "
     "tend to hurt returns. Sticking to a plan helps you avoid them."),
    ("tip", "Rebalance once or twice a year", SEC_TEN, "SEC",
     "Over time some investments grow faster than others and your mix drifts. Bring it back to your target mix, for "
     "example every 6 or 12 months, or when one part moves a set amount away from its target. Doing it occasionally works best."),
]
STARTER = VIDEOS + PODCASTS + TIPS

# The quotes the first version started with; the upgrade removes those that are still there unchanged.
OLD_QUOTES = {
    "Be fearful when others are greedy, and greedy when others are fearful.",
    "Price is what you pay. Value is what you get.",
    "Rule No. 1: Never lose money. Rule No. 2: Never forget rule No. 1.",
    "Our favorite holding period is forever.",
    "In the short run, the market is a voting machine but in the long run it is a weighing machine.",
    "The investor's chief problem, and even his worst enemy, is likely to be himself.",
    "Know what you own, and know why you own it.",
    "Don't look for the needle in the haystack. Just buy the haystack!",
    "The big money is not in the buying and selling, but in the waiting.",
    "The stock market is filled with individuals who know the price of everything, but the value of nothing.",
}


class LearnError(Exception):
    """A problem the administrator can fix, worded for them."""
    keeps_connection = True

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


# ── links ──

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url):
    """The 11-character video id from any usual YouTube link, or None."""
    try:
        u = urlparse(str(url or "").strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    vid = None
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [""])[0]
        else:
            parts = [p for p in u.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                vid = parts[1]
    return vid if vid and _YT_ID.match(vid) else None


def thumbnail(url):
    vid = youtube_id(url)
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else None


def _clean(raw, most, what, required=True, keep_lines=False):
    if keep_lines:
        text = "\n".join(" ".join(line.split()) for line in str(raw or "").strip().splitlines()).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = " ".join(str(raw or "").split())
    if required and not text:
        raise LearnError(f"Please fill in {what}.")
    if len(text) > most:
        raise LearnError(f"{what[0].upper() + what[1:]} can be at most {most} characters.")
    return text


def clean_item(kind, title, url, source, body=None):
    """(kind, title, url or None, source or None, body or None), checked. YouTube links are stored in one standard form."""
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        raise LearnError("Please choose Video, Podcast or Tip.")
    if kind == "tip":
        title = _clean(title, MAX_TITLE, "the tip's title")
        body = _clean(body, MAX_BODY, "what to do (the explanation)", keep_lines=True)
        source = _clean(source, MAX_SOURCE, "the source", required=False) or None
    else:
        title = _clean(title, MAX_TITLE, "a title")
        source = _clean(source, MAX_SOURCE, "the channel or show", required=False) or None
        body = None
    url = str(url or "").strip()
    if kind == "tip" and not url:
        return kind, title, None, source, body
    if not url:
        raise LearnError("Please paste the link.")
    if len(url) > MAX_URL:
        raise LearnError(f"The link can be at most {MAX_URL} characters.")
    try:
        parsed = urlparse(url)
    except ValueError:
        parsed = None
    if not parsed or parsed.scheme != "https" or not parsed.hostname or " " in url:
        raise LearnError("Please paste a full link that starts with https://")
    if kind == "video":
        vid = youtube_id(url)
        if not vid:
            raise LearnError("Please paste a YouTube video link (for example https://www.youtube.com/watch?v=…).")
        url = f"https://www.youtube.com/watch?v={vid}"
    return kind, title, url, source, body


def view(row):
    return {"id": int(row["id"]), "kind": row["kind"], "title": row["title"], "body": row.get("body") or "",
            "url": row.get("url") or None, "source": row.get("source") or "", "position": int(row.get("position") or 0),
            "thumb": thumbnail(row.get("url")) if row["kind"] == "video" else None}


# ── storage ──

class MySQLLearnStore:
    def __init__(self):
        self._ready = False
        self._lock = threading.Lock()

    def _run(self, work):
        import dbpool
        with dbpool.borrow() as conn:
            if not self._ready:
                with self._lock:
                    if not self._ready:
                        self._ensure(conn)
                        self._ready = True
            return work(conn)

    @staticmethod
    def _insert(cur, rows, start, now):
        for n, (kind, title, url, source, body) in enumerate(rows):
            cur.execute("INSERT INTO learn_items (kind, title, body, url, source, position, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (kind, title, body, url, source, start + n, now, now))

    @classmethod
    def _ensure(cls, conn):
        cur = conn.cursor()
        now = int(time.time())
        cur.execute(META_TABLE)
        cur.execute("SHOW TABLES LIKE 'learn_items'")
        if not cur.fetchone():
            cur.execute(TABLE)
            cls._insert(cur, STARTER, 1, now)
        else:
            cls._upgrade(cur, now)
        cur.execute("REPLACE INTO learn_meta (k, v) VALUES ('content_version', %s)", (str(CONTENT_VERSION),))
        conn.commit()

    @classmethod
    def _upgrade(cls, cur, now):
        cur.execute("SELECT v FROM learn_meta WHERE k = 'content_version'")
        row = cur.fetchone()
        if row and int(row[0]) >= CONTENT_VERSION:
            return
        cur.execute("SHOW COLUMNS FROM learn_items LIKE 'body'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE learn_items ADD COLUMN body TEXT NULL AFTER title")
        # quotes from the first version → practical tips
        cur.execute("SELECT id, title FROM learn_items WHERE kind = 'tip'")
        old = [r[0] for r in cur.fetchall() if r[1] in OLD_QUOTES]
        for item_id in old:
            cur.execute("DELETE FROM learn_items WHERE id = %s", (item_id,))
        cur.execute("SELECT COALESCE(MAX(position), 0) FROM learn_items WHERE kind = 'tip'")
        cls._insert(cur, TIPS, int(cur.fetchone()[0]) + 1, now)
        # videos added in this version (any already there are skipped)
        cur.execute("SELECT url FROM learn_items WHERE kind = 'video'")
        have = {r[0] for r in cur.fetchall()}
        new = [v for v in VIDEOS if v[2] not in have]
        cur.execute("SELECT COALESCE(MAX(position), 0) FROM learn_items WHERE kind = 'video'")
        cls._insert(cur, new, int(cur.fetchone()[0]) + 1, now)

    def all(self):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, kind, title, body, url, source, position FROM learn_items ORDER BY kind, position, id")
            return cur.fetchall()
        return self._run(work)

    def get(self, item_id):
        def work(conn):
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id, kind, title, body, url, source, position FROM learn_items WHERE id = %s", (item_id,))
            return cur.fetchone()
        return self._run(work)

    def add(self, kind, title, url, source, now, body=None):
        def work(conn):
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(MAX(position), 0) FROM learn_items WHERE kind = %s", (kind,))
            pos = int(cur.fetchone()[0]) + 1
            cur.execute("INSERT INTO learn_items (kind, title, body, url, source, position, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (kind, title, body, url, source, pos, now, now))
            conn.commit()
            return cur.lastrowid
        return self._run(work)

    def update(self, item_id, title, url, source, now, body=None):
        def work(conn):
            cur = conn.cursor()
            cur.execute("UPDATE learn_items SET title = %s, body = %s, url = %s, source = %s, updated_at = %s WHERE id = %s",
                        (title, body, url, source, now, item_id))
            conn.commit()
        return self._run(work)

    def delete(self, item_id):
        def work(conn):
            cur = conn.cursor()
            cur.execute("DELETE FROM learn_items WHERE id = %s", (item_id,))
            conn.commit()
        return self._run(work)

    def set_positions(self, pairs, now):
        """[(id, position), ...] in one go."""
        def work(conn):
            cur = conn.cursor()
            for item_id, pos in pairs:
                cur.execute("UPDATE learn_items SET position = %s, updated_at = %s WHERE id = %s", (pos, now, item_id))
            conn.commit()
        return self._run(work)

    def count(self):
        def work(conn):
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM learn_items")
            return int(cur.fetchone()[0])
        return self._run(work)


store = MySQLLearnStore()


# ── what the API calls ──

def listing():
    """{"videos": [...], "podcasts": [...], "tips": [...]} in their set order."""
    out = {"videos": [], "podcasts": [], "tips": []}
    rows = sorted(store.all(), key=lambda r: (r["kind"], int(r.get("position") or 0), int(r["id"])))
    for r in rows:
        if r["kind"] in KINDS:
            out[r["kind"] + "s"].append(view(r))
    return out


def _existing(item_id):
    row = store.get(item_id)
    if not row:
        raise LearnError("That item no longer exists.", 404)
    return row


def add_item(kind, title, url, source, now=None, body=None):
    kind, title, url, source, body = clean_item(kind, title, url, source, body)
    if store.count() >= MAX_ITEMS:
        raise LearnError(f"The Learn tab can hold at most {MAX_ITEMS} items. Remove some first.", 409)
    item_id = store.add(kind, title, url, source, int(time.time() if now is None else now), body=body)
    return view(store.get(item_id))


def edit_item(item_id, title, url, source, now=None, body=None):
    row = _existing(item_id)
    _, title, url, source, body = clean_item(row["kind"], title, url, source, body)
    store.update(item_id, title, url, source, int(time.time() if now is None else now), body=body)
    return view(store.get(item_id))


def remove_item(item_id):
    row = _existing(item_id)
    store.delete(item_id)
    return row


def move_item(item_id, direction, now=None):
    """Move an item one place up or down within its section."""
    if direction not in ("up", "down"):
        raise LearnError("Please choose up or down.")
    row = _existing(item_id)
    same = sorted((r for r in store.all() if r["kind"] == row["kind"]), key=lambda r: (int(r.get("position") or 0), int(r["id"])))
    ids = [int(r["id"]) for r in same]
    i = ids.index(int(item_id))
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    store.set_positions([(rid, n + 1) for n, rid in enumerate(ids)], int(time.time() if now is None else now))
    return listing()
