"""
US IPO calendar.

Yahoo Finance supplies the dated list of upcoming and recent IPOs but no pricing.
Nasdaq's public IPO calendar (the feed behind nasdaq.com) adds price ranges, share
counts and deal sizes. Nasdaq is best-effort: if it is unreachable the calendar is
still returned, just without those fields.

The merged result is cached in memory, because IPO data changes slowly.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

ET = ZoneInfo("America/New_York")
CACHE_TTL = 1800          # seconds
DAYS_BACK = 14            # recently priced IPOs still shown
DAYS_AHEAD = 60
NASDAQ_URL = "https://api.nasdaq.com/api/ipo/calendar?date={month}"
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

_cache = {"at": 0.0, "data": None}
_build_lock = threading.Lock()


class IPOUnavailable(Exception):
    """No IPO data could be fetched and nothing is cached."""


# ── helpers ───────────────────────────────────────────────

_SHARE_SUFFIX = re.compile(
    r"\s+(Class [A-Z]\s+)?(Common Stock|Ordinary Shares?|Common Shares?|American Depositary Shares?|Units?)\s*$", re.I)
_NOT_COMMON_EQUITY = re.compile(r"\b(Warrants?|Rights?|Units?)\b", re.I)
# Yahoo's calendar also lists fund launches, bond listings and spin-off "when-issued" trading.
# Rows Nasdaq confirms as IPOs are exempt from this filter.
_NOT_AN_IPO = re.compile(
    r"\b(ETFs?|Funds?|Trust|Portfolio|Notes?|Debentures?|Senior|Investment Dimensions|Exchange[- ]Traded|When[- ]Issued)\b", re.I)
_SPAC = re.compile(r"\b(Acquisition|Merger)\b", re.I)


def _clean_name(name) -> str:
    name = str(name or "").strip()
    return _SHARE_SUFFIX.sub("", name).strip()


def _name_key(name: str) -> str:
    """Loose key to match the same company across the two sources."""
    n = _clean_name(name).lower()
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\b(inc|corp|corporation|co|ltd|limited|plc|holdings?|group|the|class [a-z])\b", " ", n)
    return " ".join(n.split())


def _number(text):
    """'$153,333,344' -> 153333344.0, '14.00' -> 14.0, ''/'NA' -> None"""
    cleaned = re.sub(r"[^0-9.]", "", str(text or ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _price_range(text):
    """'14.00-16.00' -> (14.0, 16.0); '10.00' -> (10.0, 10.0); '' -> (None, None)"""
    parts = [p for p in re.split(r"\s*-\s*", str(text or "").strip()) if p]
    values = [v for v in (_number(p) for p in parts) if v is not None]
    if not values:
        return None, None
    return min(values), max(values)


def _parse_us_date(text):
    try:
        return datetime.strptime(str(text).strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


# ── sources ───────────────────────────────────────────────

def _fetch_yahoo(start: date, end: date) -> list:
    cal = yf.Calendars(start=datetime.combine(start, datetime.min.time()),
                       end=datetime.combine(end, datetime.min.time()))
    rows, offset = [], 0
    for _ in range(5):  # at most 500 rows
        df = cal.get_ipo_info_calendar(limit=100, offset=offset)
        if df is None or df.empty:
            break
        for symbol, r in df.iterrows():
            ts = r.get("Date")
            if ts is None or str(ts) == "NaT":
                continue
            day = ts.tz_convert("UTC").date() if getattr(ts, "tzinfo", None) else ts.date()
            raw_name = str(r.get("Company") or "")
            if _NOT_COMMON_EQUITY.search(raw_name):
                continue  # warrants, rights and units are not the IPO itself
            low, high = r.get("Price From"), r.get("Price To")
            rows.append({
                "ticker": str(symbol).strip().upper(),
                "company": _clean_name(raw_name),
                "exchange": (str(r["Exchange"]).strip() if r.get("Exchange") == r.get("Exchange") and r.get("Exchange") else ""),
                "date": day,
                "status": "Expected",
                "price_low": float(low) if low == low and low is not None else None,
                "price_high": float(high) if high == high and high is not None else None,
                "shares": int(r["Shares"]) if r.get("Shares") == r.get("Shares") and r.get("Shares") else None,
                "deal_value": None,
                "sources": ["Yahoo Finance"],
            })
        if len(df) < 100:
            break
        offset += 100
    return rows


def _fetch_nasdaq_month(month: str) -> list:
    resp = requests.get(NASDAQ_URL.format(month=month), headers=NASDAQ_HEADERS, timeout=10)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    out = []
    sections = [
        ("Expected", ((data.get("upcoming") or {}).get("upcomingTable") or {}).get("rows"), "expectedPriceDate"),
        ("Priced", (data.get("priced") or {}).get("rows"), "pricedDate"),
    ]
    for status, rows, date_field in sections:
        for r in rows or []:
            day = _parse_us_date(r.get(date_field))
            if not day:
                continue
            low, high = _price_range(r.get("proposedSharePrice"))
            out.append({
                "ticker": str(r.get("proposedTickerSymbol") or "").strip().upper(),
                "company": _clean_name(r.get("companyName")),
                "exchange": str(r.get("proposedExchange") or "").strip(),
                "date": day,
                "status": status,
                "price_low": low,
                "price_high": high,
                "shares": int(_number(r.get("sharesOffered"))) if _number(r.get("sharesOffered")) else None,
                "deal_value": int(_number(r.get("dollarValueOfSharesOffered"))) if _number(r.get("dollarValueOfSharesOffered")) else None,
                "sources": ["Nasdaq"],
            })
    return out


def _months_between(start: date, end: date) -> list:
    months, d = [], date(start.year, start.month, 1)
    while d <= end:
        months.append(d.strftime("%Y-%m"))
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return months


# ── merge ─────────────────────────────────────────────────

def _merge(yahoo: list, nasdaq: list) -> list:
    merged, by_ticker, by_name = [], {}, {}

    def index(row):
        if row["ticker"]:
            by_ticker[row["ticker"]] = row
        key = _name_key(row["company"])
        if key:
            by_name[key] = row

    for row in yahoo:
        merged.append(row)
        index(row)

    for n in nasdaq:
        existing = by_ticker.get(n["ticker"]) or by_name.get(_name_key(n["company"]))
        if existing is None:
            merged.append(n)
            index(n)
            continue
        # Nasdaq is the more detailed source for terms and status
        for field in ("price_low", "price_high", "shares", "deal_value"):
            if n[field] is not None:
                existing[field] = n[field]
        existing["exchange"] = n["exchange"] or existing["exchange"]
        existing["status"] = n["status"]
        existing["date"] = n["date"]
        existing["sources"] = sorted(set(existing["sources"]) | set(n["sources"]))
    return merged


def _build() -> dict:
    today = datetime.now(ET).date()
    start, end = today - timedelta(days=DAYS_BACK), today + timedelta(days=DAYS_AHEAD)
    warnings = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        yahoo_future = pool.submit(_fetch_yahoo, start, end)
        nasdaq_futures = [pool.submit(_fetch_nasdaq_month, m) for m in _months_between(start, end)]

        try:
            yahoo = yahoo_future.result()
        except Exception as e:
            print(f"IPO: Yahoo Finance failed: {e}")
            yahoo = None
            warnings.append("Yahoo Finance data is unavailable right now.")

        nasdaq, nasdaq_ok = [], 0
        for f in nasdaq_futures:
            try:
                nasdaq += f.result()
                nasdaq_ok += 1
            except Exception as e:
                print(f"IPO: Nasdaq month failed: {e}")
        if nasdaq_ok < len(nasdaq_futures):
            warnings.append("Price ranges and share counts are unavailable or incomplete right now.")

    if yahoo is None and nasdaq_ok == 0:
        raise IPOUnavailable("Both IPO data sources failed")

    rows = []
    for r in _merge(yahoo or [], nasdaq):
        if not (start <= r["date"] <= end):
            continue
        if "Nasdaq" not in r["sources"] and (not r["company"] or r["company"].lower() == "nan" or _NOT_AN_IPO.search(r["company"])):
            continue
        r["is_spac"] = bool(_SPAC.search(r["company"]))
        r["days_until"] = (r["date"] - today).days
        r["date"] = r["date"].isoformat()
        rows.append(r)
    rows.sort(key=lambda r: (r["date"], r["company"].lower()))

    return {
        "ipos": rows,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sorted({s for r in rows for s in r["sources"]}),
        "warnings": warnings,
    }


def _tracked_tickers(tickers: list) -> set:
    """Which of these tickers are already in our stock database."""
    tickers = [t for t in tickers if t]
    if not tickers:
        return set()
    try:
        from screener import get_db_connection
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT ticker FROM stocks_live WHERE ticker IN (%s)" % ",".join(["%s"] * len(tickers)), tickers)
            return {r[0] for r in cursor.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        print(f"IPO: tracked-ticker lookup failed: {e}")
        return set()


def get_ipos(days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD) -> dict:
    """
    The calendar for the API and chatbot: windowed, with `in_database` set on each row.
    A Yahoo-only row for a stock we already track is an existing company (relisting,
    transfer), not an IPO, so it is dropped.
    """
    data = get_ipo_calendar()
    tracked = _tracked_tickers([r["ticker"] for r in data["ipos"]])
    rows = []
    for r in data["ipos"]:
        in_db = r["ticker"] in tracked
        if in_db and "Nasdaq" not in r["sources"]:
            continue
        if not (-days_back <= r["days_until"] <= days_ahead):
            continue
        rows.append({**r, "in_database": in_db, "confirmed": "Nasdaq" in r["sources"]})
    return {**data, "ipos": rows, "sources": sorted({s for r in rows for s in r["sources"]})}


def get_ipo_calendar(force: bool = False) -> dict:
    """Cached IPO calendar. Serves the last good copy if a refresh fails."""
    if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_TTL:
        return _cache["data"]
    with _build_lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < CACHE_TTL:
            return _cache["data"]  # another request refreshed it while we waited
        try:
            data = _build()
        except IPOUnavailable:
            if _cache["data"]:
                return {**_cache["data"], "warnings": _cache["data"]["warnings"] + ["Showing older data: refresh failed."]}
            raise
        _cache.update(at=time.time(), data=data)
        return data
