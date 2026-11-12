"""
Subscription tiers: which plan can use what. This file is the one place that says so.

A *feature* is one row of the plan table ("Portfolio management", "Commodities", ...). A *tier* is a plan: a column of that
table. Each tier includes every feature of the tiers below it plus the ones it adds, so moving a feature to another tier is
a one-line edit in TIERS. Nothing is a feature unless the plan table lists it: the parts of the app the table does not name
(prices, stock pages, news, calendars, the watchlist, alerts ...) are part of the "initial screen".

Tier 1 (Free) can have an account: it opens the Home tab and a read-only Portfolio page. Someone who hasn't signed in at all
sees only the public price list (public.html), which is not a plan. New accounts start on Free; accounts that existed before
Free accounts did stay on Basic. Administrators are not on a tier; they can use everything.

The server enforces all of this: every route below is tied to a feature, and a route that nobody has classified yet is
refused for everyone but administrators (see tests/py/test_tiers.py, which fails until a new route is classified). The
dashboard only uses the same list to show padlocks instead of empty boxes.
"""
import re
from dataclasses import dataclass

# feature key -> its name in the plan table
FEATURES = {
    "initial_screen":   "Home tab",
    "portfolio_view":   "Portfolio visibility",
    "market":           "Market tab",
    "portfolio_mgt":    "Portfolio management",
    "raise_ticket":     "Raising tickets",
    "stock_prediction": "Stock prediction",
    "ai_stock_finder":  "AI stock finder",
    "peer_comparison":  "Peer comparison",
    "ai_interaction":   "AI interaction",
    "learn":            "Learn tab",
    "commodities":      "Commodities",
    "pre_ipo":          "IPO details",
    "insider_dealings": "Insider dealings",
    "filings":          "Company filings",
}


@dataclass(frozen=True)
class Tier:
    level: int
    name: str
    price: float          # US dollars per month
    adds: tuple           # the features this tier adds on top of the tier below
    account: bool = True  # whether someone on this plan can have an account (every plan can now; Free accounts see only the Home tab)


TIERS = {
    1: Tier(1, "Free",            0.00,  ("initial_screen", "portfolio_view")),
    2: Tier(2, "Basic",           9.99,  ("market", "portfolio_mgt", "raise_ticket")),
    3: Tier(3, "AI Stock Finder", 19.99, ("stock_prediction", "ai_stock_finder", "peer_comparison")),
    4: Tier(4, "AI Interaction",  29.99, ("ai_interaction", "learn")),
    5: Tier(5, "Deluxe",          39.99, ("commodities",)),
    6: Tier(6, "Premier",         49.99, ("pre_ipo", "insider_dealings", "filings")),
}
EXISTING_ACCOUNTS_TIER = 2     # accounts that existed before Free accounts did were all on Basic, so a database update keeps them there

ADMIN_FEATURES = frozenset(FEATURES)
UNCLASSIFIED = "?"        # a route nobody has assigned to a feature; only administrators may use it
NO_FEATURE = ""           # a route any signed-in account may use (sign-out, the page itself, ...)


def _features_up_to(level) -> frozenset:
    return frozenset(f for n, t in TIERS.items() if n <= level for f in t.adds)


def account_tier() -> int:
    """The tier a new account starts on: the lowest one that lets a person have an account."""
    return min(n for n, t in TIERS.items() if t.account)


def resolve(level):
    """The plan an *account* with this stored level is on. A level this version doesn't define counts as the highest plan
    below it, and anything unreadable or below the lowest account plan as the lowest account plan, so a stray value never
    locks a person out or opens too much."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        return TIERS[account_tier()]
    defined = [n for n in TIERS if n <= level]
    tier = TIERS[max(defined)] if defined else TIERS[account_tier()]
    return tier if tier.account else TIERS[account_tier()]


def features_for(level) -> frozenset:
    """Every feature the plan of an account on `level` includes (its own and those of the plans below)."""
    return _features_up_to(resolve(level).level)


def visitor_plan() -> Tier:
    """The plan of someone who is not signed in: the lowest one (they see less than a Free account: just the public price list)."""
    return TIERS[min(TIERS)]


def first_paid_plan() -> Tier:
    """The cheapest plan that costs money (what the public page invites people to upgrade to)."""
    return min((t for t in TIERS.values() if t.price > 0), key=lambda t: t.level)


def visitor_features() -> frozenset:
    return _features_up_to(visitor_plan().level)


def lowest_tier_with(feature):
    """The cheapest tier that includes `feature`, or None when no tier does."""
    for n in sorted(TIERS):
        if feature in _features_up_to(n):
            return TIERS[n]
    return None


def describe(level) -> dict:
    t = resolve(level)
    return {"level": t.level, "name": t.name, "price": t.price}


def describe_visitor() -> dict:
    t = visitor_plan()
    return {"level": t.level, "name": t.name, "price": t.price}


def upgrade_info(feature) -> dict:
    """What to tell someone whose plan doesn't include `feature`."""
    t = lowest_tier_with(feature)
    return {"feature": feature, "label": FEATURES.get(feature, "This feature"),
            "required_tier": t.level if t else None, "required_name": t.name if t else None,
            "required_price": t.price if t else None}


# What each feature means in plain words, for the plan comparison on the profile page.
DESCRIPTIONS = {
    "initial_screen":   "Your portfolio snapshot, market movers and charts, recently viewed and saved stocks, commodities and news, and the stock pages you open from it (charts, company details, financials, earnings, dividends)",
    "portfolio_view":   "View your portfolio and your transaction history",
    "market":           "The full stock table with search, filters and sorting, live currency rates and world finance news",
    "portfolio_mgt":    "Add and edit your own holdings and transactions",
    "raise_ticket":     "Raise a Help Desk ticket about a problem or question, and follow the replies",
    "stock_prediction": "AI price predictions: the top 5 predicted stocks, and a forecast on every stock page",
    "ai_stock_finder":  "Describe the stocks you want in plain English, or use manual filters, and let the AI find them",
    "peer_comparison":  "Compare a stock with its closest competitors side by side, with a price chart over the period you choose",
    "ai_interaction":   "Chat with Bounty, the AI assistant, and get an AI review of your portfolio",
    "learn":            "Beginner videos, investing podcasts and practical tips from US investor-education sources",
    "commodities":      "The Commodities page: commodity prices, macro indicators and the companies behind them",
    "pre_ipo":          "The IPOs tab: upcoming and recently priced US IPOs",
    "insider_dealings": "On every stock page: shares bought or sold by the company's own people in the last 12 months",
    "filings":          "On every stock page: annual reports, shareholder notices and announcements from the SEC, with downloads",
}


def feature_info(feature) -> dict:
    return {"key": feature, "label": FEATURES.get(feature, feature), "description": DESCRIPTIONS.get(feature, "")}


def feature_list(features) -> list:
    """The given features as {key, label, description}, in the order of the plan table."""
    return [feature_info(f) for f in FEATURES if f in features]


def catalog() -> list:
    """Every plan, cheapest first, as the profile page compares them: name, price and the features it adds to the plan below."""
    out, below = [], None
    for level, tier in sorted(TIERS.items()):
        out.append({"level": level, "name": tier.name, "price": tier.price, "account": tier.account,
                    "includes": below.name if below else None, "adds": [feature_info(f) for f in tier.adds]})
        below = tier
    return out


def public_stock_row(row) -> list:
    """One stock as the free tier sees it: [ticker, name, sector, price, change %]. Nothing else leaves the server."""
    return [row.get("ticker"), row.get("company_name"), row.get("sector"), row.get("current_price"), row.get("price_change_pct")]


# ── which route needs which feature ──
# First match wins, so specific routes sit above the general ones. "*" means any method. Routes are listed one by one on
# purpose: a new endpoint matches nothing here until it is added, and until then only administrators can use it.
_P = r"[^/]+"
_INITIAL = "initial_screen"
_RULES = [
    # the page and sign-in plumbing: any signed-in account
    ("GET",  r"/", NO_FEATURE), ("GET", r"/dashboard\.html", NO_FEATURE), ("GET", r"/logout", NO_FEATURE),
    ("GET",  r"/api/auth/status", NO_FEATURE), ("GET", r"/health", NO_FEATURE), ("*", r"/admin", NO_FEATURE),
    ("GET",  r"/api/profile", NO_FEATURE),                     # a person's own profile and the plan comparison: every account has one
    ("POST", r"/api/profile/delete", NO_FEATURE),              # deleting your own account (asks for the password again)
    # Settings and downloading your data: managing your own account, which every account can do
    ("GET",  r"/settings", NO_FEATURE),
    ("GET",  r"/api/account(/(export|notifications))?", NO_FEATURE),
    ("PUT",  r"/api/account/(name|profile|notifications)", NO_FEATURE),
    ("POST", r"/api/account/(email|password)", NO_FEATURE),
    ("*",    r"/api/admin/.*", NO_FEATURE),                    # these also demand an administrator, checked separately
    ("GET",  r"/api/public/stocks", _INITIAL),
    # the parts the plan table puts on a plan of their own
    ("GET",  r"/api/support/(tickets(/[^/]+)?|updates)", "raise_ticket"),                   # the Help Desk
    ("POST", r"/api/support/(tickets(/[^/]+/messages)?|updates/seen)", "raise_ticket"),
    ("GET",  r"/api/learn", "learn"),
    ("GET",  rf"/api/peers/{_P}", "peer_comparison"),
    ("GET",  r"/api/compare(/(prices|verdict))?", "peer_comparison"),
    ("GET",  rf"/api/stocks/{_P}/insiders", "insider_dealings"),
    ("GET",  rf"/api/stocks/{_P}/filings(/download)?", "filings"),
    # The Market tab. Most of what it shows (the stock list, movers, charts, news) is also what the Home tab is built from, so the
    # server can't refuse it to a Free account without emptying Home; the tab itself is padlocked in the page. Its currency-rates
    # card is the one thing nothing else uses, so that is refused here.
    ("GET",  r"/api/market/currencies", "market"),
    ("GET",  r"/api/market/(commodities|macro)", "commodities"),
    ("POST", r"/api/stocks/batch_commodity", "commodities"),
    ("GET",  r"/api/(ai-predictions|predictions)(/[^/]+)?", "stock_prediction"),
    ("GET",  r"/api/market/signals", "stock_prediction"),
    ("GET",  r"/api/ipos", "pre_ipo"),
    ("POST", r"/api/screener", "ai_stock_finder"),
    ("POST", r"/agent/(recommend|explain-stock)", "ai_stock_finder"),     # /agent/recommend with a conversation also needs ai_interaction (api.py)
    ("POST", r"/agent/(recommend/stream|portfolio-review)", "ai_interaction"),
    ("GET",  r"/api/(portfolio|transactions)(/[^/]+)?", "portfolio_view"),
    ("*",    r"/api/(portfolio|transactions)(/[^/]+)?", "portfolio_mgt"),
    # everything else the dashboard shows is the Home tab and what you reach from it
    ("*",    r"/api/watchlist", _INITIAL),
    ("GET",  r"/api/stocks(/(directory|search|batch))?", _INITIAL),                  # "/api/stocks" is the full list that Home's cards are drawn from
    ("GET",  rf"/api/stocks/{_P}", _INITIAL),
    ("GET",  rf"/api/stocks/{_P}/(price-history|intraday|price-range|period|period-spikes|price-spikes|news-sentiment|historical|quarterly|company|dividends|buy-sell)", _INITIAL),
    ("GET",  r"/api/market/(summary|gainers|losers|most-active|sectors|indices|sentiment|pe-vs-growth|rsi-distribution|volume-by-sector|"
             r"most-volatile|dividend-leaders|52week-proximity|breadth|volume-spikes)", _INITIAL),
    ("GET",  r"/api/analyst/(consensus|sectors)", _INITIAL),
    ("GET",  rf"/api/sentiment/{_P}", _INITIAL),
    ("GET",  rf"/api/earnings/{_P}", _INITIAL),                # a ticker, or "calendar"
    ("GET",  rf"/api/news/{_P}", _INITIAL),                    # "market", "world" or a ticker
    ("GET",  r"/api/economic-calendar", _INITIAL),
    ("*",    r"/api/account/recent(/[^/]+)?", _INITIAL),       # the recently viewed stocks on the Market page
    ("*",    r"/api/alerts(/[^/]+)?", _INITIAL),
    ("GET",  r"/api/notifications/feed", _INITIAL),
]
_COMPILED = [(None if m == "*" else m, re.compile(p)) + (f,) for m, p, f in _RULES]


def feature_for(method, path) -> str:
    """The feature a request needs, NO_FEATURE for any signed-in account, or UNCLASSIFIED when no rule covers it."""
    method, path = method.upper(), (path.rstrip("/") or "/")
    for rule_method, pattern, feature in _COMPILED:
        if (rule_method is None or rule_method == method) and pattern.fullmatch(path):
            return feature
    return UNCLASSIFIED
