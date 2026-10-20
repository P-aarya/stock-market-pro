# ============================================================
# STOCK MARKET PRO - sentiment_engine.py
# Shared by predict.py (to adjust predictions) and api.py
# (the sentiment bar under the prediction chart), so both
# always agree.
#
# Two layers, each scored from -1 (bearish) to +1 (bullish):
#   A. Stock layer  - company news 40%, price momentum 25%,
#                     volume signal 15%, analyst rating 20%
#   B. Market layer - market fear (VIX), S&P 500 trend, sector
#                     trend, interest rates, oil, US dollar,
#                     gold, world news. Rates / oil / dollar /
#                     gold are weighted by how sensitive the
#                     stock's sector is to them.
# Overall = 60% stock layer + 40% market layer.
# ============================================================
import html
import email.utils
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import yfinance as yf

STOCK_WEIGHT = 0.60
MARKET_WEIGHT = 0.40
STOCK_FACTORS = [  # key, label, weight
    ("news", "Company News", 0.40),
    ("momentum", "Price Momentum", 0.25),
    ("volume", "Volume Signal", 0.15),
    ("analyst", "Analyst Rating", 0.20),
]
MARKET_FACTORS = [
    ("vix", "Market Fear (VIX)", 0.22),
    ("spy", "S&P 500 Trend", 0.22),
    ("sector", "Sector Trend", 0.18),
    ("rates", "Interest Rates", 0.10),
    ("oil", "Oil Price", 0.10),
    ("dollar", "US Dollar", 0.05),
    ("gold", "Gold / Safe Haven", 0.05),
    ("world", "World News", 0.08),
]

# Sector -> (sector ETF, sensitivity to rising rates, oil, dollar, gold)
# Sensitivity: +1 = helped when that thing rises, -1 = hurt, 0 = little effect
_SECTOR_RULES = [
    ("semiconductor", ("SMH",  -1.0, -0.1, -0.6, 0.0)),
    ("cloud",         ("IGV",  -1.0, -0.1, -0.5, 0.0)),
    ("software",      ("IGV",  -1.0, -0.1, -0.5, 0.0)),
    ("technology",    ("XLK",  -1.0, -0.1, -0.6, 0.0)),
    ("biotech",       ("XBI",  -1.0,  0.0, -0.3, 0.0)),
    ("health",        ("XLV",  -0.3,  0.0, -0.4, 0.0)),
    ("bank",          ("XLF",   0.6,  0.0,  0.0, 0.0)),
    ("insurance",     ("KIE",   0.5,  0.0,  0.0, 0.0)),
    ("crypto",        ("XLF",  -0.8,  0.0, -0.3, 0.0)),
    ("fintech",       ("XLF",  -0.6,  0.0, -0.3, 0.0)),
    ("financ",        ("XLF",   0.4,  0.0,  0.0, 0.0)),
    ("energy",        ("XLE",  -0.2,  1.0,  0.0, 0.0)),
    ("oil",           ("XLE",  -0.2,  1.0,  0.0, 0.0)),
    ("airline",       ("JETS", -0.4, -1.0,  0.0, 0.0)),
    ("transport",     ("IYT",  -0.4, -0.8, -0.2, 0.0)),
    ("automotive",    ("CARZ", -0.6, -0.6, -0.4, 0.0)),
    ("aerospace",     ("ITA",  -0.3, -0.4, -0.4, 0.0)),
    ("industrial",    ("XLI",  -0.4, -0.3, -0.5, 0.0)),
    ("material",      ("XLB",  -0.3,  0.2, -0.5, 0.6)),
    ("mining",        ("XME",  -0.3,  0.2, -0.5, 1.0)),
    ("real estate",   ("XLRE", -1.0,  0.0,  0.0, 0.0)),
    ("utilit",        ("XLU",  -0.8,  0.0,  0.0, 0.0)),
    ("staples",       ("XLP",  -0.3, -0.3, -0.5, 0.0)),
    ("retail",        ("XRT",  -0.6, -0.5, -0.2, 0.0)),
    ("discretionary", ("XLY",  -0.7, -0.5, -0.3, 0.0)),
    ("consumer",      ("XLY",  -0.6, -0.4, -0.3, 0.0)),
    ("media",         ("XLC",  -0.6, -0.1, -0.3, 0.0)),
    ("communication", ("XLC",  -0.6, -0.1, -0.3, 0.0)),
]
_DEFAULT_PROFILE = ("SPY", -0.4, -0.2, -0.3, 0.0)

WORLD_FEEDS = [
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://oilprice.com/rss/main",
    "https://economictimes.indiatimes.com/markets/rss.cms",
]
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

_cache = {}
_lock = threading.Lock()
_vader_obj = None


def _cached(key, ttl, builder):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = builder()
    if val is not None:
        with _lock:
            _cache[key] = (now, val)
    return val


def _clip(x, lo=-1.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def _vader():
    global _vader_obj
    if _vader_obj is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "vaderSentiment", "-q"])
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader_obj = SentimentIntensityAnalyzer()
    return _vader_obj


def score_text(text):
    try:
        return _vader().polarity_scores(text or "")["compound"]
    except Exception:
        return 0.0


def sector_profile(sector):
    s = (sector or "").lower()
    for key, prof in _SECTOR_RULES:
        if key in s:
            return prof
    return _DEFAULT_PROFILE


# ---------------------------------------------------------------- trading session
def _ny_offset_hours(utc_dt):
    """US Eastern offset from UTC (-4 in daylight time, -5 otherwise). No tz database needed."""
    y = utc_dt.year
    mar = datetime(y, 3, 1)
    second_sun_mar = mar + timedelta(days=(6 - mar.weekday()) % 7 + 7)
    nov = datetime(y, 11, 1)
    first_sun_nov = nov + timedelta(days=(6 - nov.weekday()) % 7)
    start = (second_sun_mar + timedelta(hours=2 + 5)).replace(tzinfo=timezone.utc)   # 2am EST
    end = (first_sun_nov + timedelta(hours=2 + 4)).replace(tzinfo=timezone.utc)      # 2am EDT
    return -4 if start <= utc_dt < end else -5


def to_new_york(dt=None):
    """Any datetime (naive = this computer's local time) -> naive New York time."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.astimezone()  # treat naive as local time of this machine
    u = dt.astimezone(timezone.utc)
    return (u + timedelta(hours=_ny_offset_hours(u))).replace(tzinfo=None)


def session_fraction(ny_dt):
    """Share of the regular session (9:30-16:00 New York) elapsed at ny_dt, or None outside it."""
    if ny_dt.weekday() >= 5:
        return None
    open_ = ny_dt.replace(hour=9, minute=30, second=0, microsecond=0)
    close = ny_dt.replace(hour=16, minute=0, second=0, microsecond=0)
    if not (open_ <= ny_dt < close):
        return None
    return (ny_dt - open_).total_seconds() / (close - open_).total_seconds()


def adjust_volume_ratio(raw_ratio, as_of=None, bar_date=None):
    """
    A day's volume vs its normal full-day average. During market hours only part of the
    day has traded, so the raw ratio looks too low. Scale it by the share of a normal day's
    volume that has usually traded by that time (more trades early, so share = f ** 0.85).
      as_of    - when the volume figure was taken (naive = local time). Default: now.
      bar_date - for daily price bars: the bar's date; only today's bar is partial.
    Returns (ratio, note).
    """
    if raw_ratio is None:
        return None, ""
    ny = to_new_york(as_of)
    if bar_date is not None and str(bar_date)[:10] != ny.strftime("%Y-%m-%d"):
        return raw_ratio, "full trading day"
    f = session_fraction(ny)
    if f is None:
        return raw_ratio, "full trading day"
    if f < 0.08:  # first ~30 minutes: too little trading to judge
        return None, "market just opened - too early to judge today's volume"
    share = f ** 0.85
    return raw_ratio / share, f"so far today, adjusted for time of day - {f*100:.0f}% of the session done"


# ---------------------------------------------------------------- data helpers
def _closes(symbol, period="3mo"):
    def build():
        try:
            h = yf.Ticker(symbol).history(period=period)
            if h is None or h.empty:
                return None
            return [float(x) for x in h["Close"].dropna().tolist()]
        except Exception:
            return None
    return _cached(f"px:{symbol}:{period}", 1800, build)


def _chg(closes, n=20):
    if not closes or len(closes) <= n:
        return None
    a, b = closes[-1], closes[-1 - n]
    return (a - b) / b * 100 if b else None


def _parse_ts(s):
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s.strip()).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _rss_titles(url, limit=25):
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=6) as r:
            root = ET.fromstring(r.read())
        out = []
        for it in root.iter("item"):
            t = html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("title") or "")).strip()
            if t:
                out.append({"title": t, "url": (it.findtext("link") or "").strip(),
                            "published": _parse_ts(it.findtext("pubDate")),
                            "source": (it.findtext("source") or "").strip()})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def company_headlines(ticker, limit=15):
    """Recent company headlines from Yahoo Finance (yfinance + RSS)."""
    def build():
        items = []
        try:
            for it in (yf.Ticker(ticker).news or []):
                c = it.get("content") or {}
                title = (c.get("title") if c else it.get("title")) or ""
                if not title.strip():
                    continue
                ts = _parse_ts(c.get("pubDate")) if c else it.get("providerPublishTime")
                url = ((c.get("canonicalUrl") or {}).get("url") if c else it.get("link")) or ""
                src = ((c.get("provider") or {}).get("displayName") if c else it.get("publisher")) or "Yahoo Finance"
                items.append({"title": title.strip(), "url": url, "published": ts, "source": src})
        except Exception:
            pass
        items += _rss_titles(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(ticker)}&region=US&lang=en-US", limit)
        seen, out = set(), []
        for a in items:
            k = re.sub(r"[^a-z0-9]", "", a["title"].lower())[:80]
            if k and k not in seen:
                seen.add(k)
                out.append(a)
        out.sort(key=lambda a: a.get("published") or 0, reverse=True)
        return out[:limit]
    return _cached(f"news:{ticker}", 900, build) or []


# ---------------------------------------------------------------- layer A
def stock_layer(ticker, headlines=None, closes=None, volume_ratio=None, day_change_pct=None,
                analyst_rating=None, upside_pct=None, volume_note=""):
    """Stock-specific sentiment. Any input left as None is fetched or treated as neutral."""
    now = datetime.now(timezone.utc).timestamp()

    # News (VADER, recent headlines weighted more)
    if headlines is None:
        headlines = company_headlines(ticker)
    scored = []
    for h in headlines or []:
        title = h.get("title") or ""
        if not title:
            continue
        pub = h.get("published")
        age = round((now - float(pub)) / 86400, 1) if pub else 7.0
        if age > 14:
            continue
        w = 1.0 if age <= 1 else 0.7 if age <= 3 else 0.4 if age <= 7 else 0.2
        scored.append({"title": title, "score": round(score_text(title), 3), "weight": w,
                       "age_days": age, "source": h.get("source", ""), "url": h.get("url", "")})
    tw = sum(s["weight"] for s in scored)
    news = round(sum(s["score"] * s["weight"] for s in scored) / tw, 3) if tw else 0.0
    scored.sort(key=lambda s: abs(s["score"]) * s["weight"], reverse=True)

    # Momentum (14 trading days)
    if closes is None:
        closes = _closes(ticker, "2mo")
    mom14 = None
    momentum = 0.0
    if closes and len(closes) >= 5:
        n = min(14, len(closes) - 1)
        mom14 = (closes[-1] - closes[-1 - n]) / closes[-1 - n] * 100
        momentum = _clip(mom14 / 10)

    # Volume (today vs average, signed by price direction)
    volume = 0.0
    if volume_ratio is not None:
        direction = 1 if (day_change_pct or 0) >= 0 else -1
        volume = _clip(direction * (float(volume_ratio) - 1.0))

    # Analyst
    rating_map = {"strong_buy": 1.0, "buy": 0.6, "hold": 0.0, "sell": -0.6, "strong_sell": -1.0}
    analyst = 0.0
    if analyst_rating or upside_pct is not None:
        base = rating_map.get(str(analyst_rating or "").lower().replace(" ", "_"), 0.0)
        analyst = _clip(base + _clip((upside_pct or 0) / 50, -0.4, 0.4))

    factors = {"news": news, "momentum": round(momentum, 3), "volume": round(volume, 3), "analyst": round(analyst, 3)}
    score = round(sum(factors[k] * w for k, _, w in STOCK_FACTORS), 3)
    details = {
        "news": f"{len(scored)} recent headlines, average tone {news:+.2f}" if scored else "No recent company headlines",
        "momentum": f"Price {mom14:+.1f}% over ~3 weeks" if mom14 is not None else "Not enough price history",
        "volume": (f"Volume {float(volume_ratio):.1f}x normal on a {'rising' if (day_change_pct or 0) >= 0 else 'falling'} day"
                   + (f" ({volume_note})" if volume_note else "")) if volume_ratio is not None else (volume_note or "No volume data"),
        "analyst": f"Analysts: {str(analyst_rating or 'n/a').replace('_', ' ')}" + (f", target {upside_pct:+.0f}% away" if upside_pct is not None else ""),
    }
    return {"score": score, "factors": factors, "details": details, "headlines": scored[:3], "count": len(scored)}


# ---------------------------------------------------------------- layer B
def market_context():
    """Market-wide readings, shared by every stock (cached 30 min)."""
    def build():
        syms = ["^VIX", "SPY", "^TNX", "DX-Y.NYB", "CL=F", "GC=F"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            data = dict(zip(syms, pool.map(lambda s: _closes(s, "3mo"), syms)))
        vix = data["^VIX"][-1] if data["^VIX"] else None
        spy = data["SPY"]
        tnx = data["^TNX"]
        ctx = {
            "vix": vix,
            "vix_chg": _chg(data["^VIX"], 20),
            "spy_chg": _chg(spy, 20),
            "spy_above_50": (spy[-1] > sum(spy[-50:]) / 50) if spy and len(spy) >= 50 else None,
            "tnx": tnx[-1] if tnx else None,
            "tnx_chg_pts": (tnx[-1] - tnx[-21]) if tnx and len(tnx) > 21 else None,
            "dxy_chg": _chg(data["DX-Y.NYB"], 20),
            "oil_chg": _chg(data["CL=F"], 20),
            "gold_chg": _chg(data["GC=F"], 20),
        }
        # World news tone
        with ThreadPoolExecutor(max_workers=7) as pool:
            lists = list(pool.map(lambda u: _rss_titles(u, 20), WORLD_FEEDS))
        titles = [a["title"] for lst in lists for a in lst]
        ctx["world_n"] = len(titles)
        ctx["world_tone"] = round(sum(score_text(t) for t in titles) / len(titles), 3) if titles else None
        return ctx
    return _cached("market_ctx", 1800, build) or {}


def market_layer(sector=None):
    ctx = market_context()
    etf, s_rates, s_oil, s_dollar, s_gold = sector_profile(sector)
    f, d = {}, {}

    vix = ctx.get("vix")
    if vix is not None:
        f["vix"] = _clip((20 - vix) / 10 - _clip((ctx.get("vix_chg") or 0) / 50, -0.3, 0.3))
        mood = "low - investors calm" if vix < 16 else "normal" if vix < 22 else "elevated - investors nervous" if vix < 30 else "very high - fear in the market"
        d["vix"] = f"VIX {vix:.1f} ({mood})"
    spy = ctx.get("spy_chg")
    if spy is not None:
        f["spy"] = _clip(spy / 5 + (0.2 if ctx.get("spy_above_50") else -0.2))
        d["spy"] = f"S&P 500 {spy:+.1f}% over the last month"
    sec_closes = _closes(etf, "3mo")
    sec = _chg(sec_closes, 20)
    if sec is not None:
        f["sector"] = _clip(sec / 6)
        d["sector"] = f"Sector ({etf}) {sec:+.1f}% over the last month"
    tnx_chg = ctx.get("tnx_chg_pts")
    if tnx_chg is not None:
        f["rates"] = _clip((tnx_chg / 0.30) * s_rates)
        effect = "a headwind" if f["rates"] < -0.05 else "a tailwind" if f["rates"] > 0.05 else "little effect"
        d["rates"] = f"10-yr yield {ctx.get('tnx', 0):.2f}% ({tnx_chg:+.2f} pts in a month) - {effect} for this sector"
    oil = ctx.get("oil_chg")
    if oil is not None:
        f["oil"] = _clip((oil / 10) * s_oil)
        effect = "helps" if f["oil"] > 0.05 else "hurts" if f["oil"] < -0.05 else "barely affects"
        d["oil"] = f"Oil {oil:+.1f}% in a month - {effect} this sector"
    dxy = ctx.get("dxy_chg")
    if dxy is not None:
        f["dollar"] = _clip((dxy / 2) * s_dollar)
        d["dollar"] = f"US dollar {dxy:+.1f}% in a month" + (" - a stronger dollar cuts overseas earnings" if dxy > 0.5 and s_dollar < 0 else "")
    gold = ctx.get("gold_chg")
    if gold is not None:
        f["gold"] = _clip(gold / 5 * s_gold) if s_gold > 0 else _clip(-gold / 5 * 0.5)
        d["gold"] = f"Gold {gold:+.1f}% in a month" + (" - helps miners" if s_gold > 0 and gold > 0 else " - investors seeking safety" if gold > 3 else "")
    tone = ctx.get("world_tone")
    if tone is not None:
        f["world"] = _clip(tone / 0.25)
        d["world"] = f"{ctx.get('world_n', 0)} world headlines, average tone {tone:+.2f}"

    # Weighted average over the factors we could measure
    wsum = sum(w for k, _, w in MARKET_FACTORS if k in f)
    score = round(sum(f[k] * w for k, _, w in MARKET_FACTORS if k in f) / wsum, 3) if wsum else 0.0
    return {"score": score, "factors": {k: round(v, 3) for k, v in f.items()}, "details": d, "sector_etf": etf}


# ---------------------------------------------------------------- combined
def label_for(score):
    return "Bullish" if score >= 0.15 else "Bearish" if score <= -0.15 else "Neutral"


def _drivers(stock, market):
    items = []
    for k, label, w in STOCK_FACTORS:
        v = stock["factors"].get(k, 0)
        items.append((abs(v) * w * STOCK_WEIGHT, v, label, stock["details"].get(k, "")))
    for k, label, w in MARKET_FACTORS:
        if k in market["factors"]:
            v = market["factors"][k]
            items.append((abs(v) * w * MARKET_WEIGHT, v, label, market["details"].get(k, "")))
    items.sort(reverse=True)
    out = []
    for impact, v, label, detail in items[:4]:
        if impact < 0.01:
            continue
        tag = "supports upside" if v > 0 else "weighs on the price"
        out.append(f"{label}: {detail} - {tag}")
    return out


def full_sentiment(ticker, sector=None, **stock_kwargs):
    stock = stock_layer(ticker, **stock_kwargs)
    market = market_layer(sector)
    overall = round(STOCK_WEIGHT * stock["score"] + MARKET_WEIGHT * market["score"], 3)
    return {
        "ticker": ticker,
        "score": overall,
        "label": label_for(overall),
        "stock_score": stock["score"],
        "stock_label": label_for(stock["score"]),
        "market_score": market["score"],
        "market_label": label_for(market["score"]),
        "factors": {k: {"score": stock["factors"][k], "weight": w, "detail": stock["details"][k]}
                    for k, _, w in STOCK_FACTORS},
        "market_factors": [{"key": k, "label": lbl, "weight": w, "score": market["factors"][k],
                            "detail": market["details"].get(k, "")}
                           for k, lbl, w in MARKET_FACTORS if k in market["factors"]],
        "sector_etf": market["sector_etf"],
        "drivers": _drivers(stock, market),
        "headlines": stock["headlines"],
        "count": stock["count"],
        "weights": {"stock": STOCK_WEIGHT, "market": MARKET_WEIGHT},
    }


# How much sentiment may move each prediction (max % added to / taken from the return)
SENTIMENT_MAX_ADJ_PCT = {7: 1.0, 30: 2.0, 90: 3.0, 180: 4.0, 365: 5.0}
