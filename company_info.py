"""
Two sections of the stock popup:

  * Insider deals: every share transaction by the company's own people (executives, directors, owners of more than
    10%): buys, sells, share awards, option exercises, gifts and others.
    From Yahoo, which takes them from the SEC's Form 4 filings (insiders must file within 2 business days of a trade).
  * Filings: annual reports, shareholder meeting notices and company announcements, from the SEC's free EDGAR site.

The SEC asks every program that reads EDGAR to say who it is in the User-Agent header and to make no more than 10
requests a second (https://www.sec.gov/os/accessing-edgar-data). Everything here is cached, so a popup normally makes
no SEC request at all. Set SEC_USER_AGENT in the environment to change how the app introduces itself.
"""
import datetime as dt
import logging
import math
import os
import re
import threading
import time

import requests

log = logging.getLogger("company_info")

INSIDER_TTL = 3600            # insider deals: kept for an hour per stock
FILINGS_TTL = 6 * 3600        # a company's filing list: kept for 6 hours
MAP_TTL = 24 * 3600           # the SEC's ticker → company number list: kept for a day
INSIDER_DAYS = 365            # insider deals from the last 12 months (the page can narrow it to 3 or 6)
ANNUAL_COUNT, MEETING_COUNT = 5, 5
ANNOUNCE_DAYS, ANNOUNCE_COUNT = 365, 15
TIMEOUT = 20

_lock = threading.Lock()
_insiders = {}                # ticker -> (time, result)
_filings = {}                 # cik -> (time, submissions json)
_map = {"at": 0.0, "data": {}}


def _yf():
    import yfinance as yf
    return yf


def _cache_get(cache, key, ttl, now):
    with _lock:
        hit = cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    return None


def _cache_put(cache, key, value, now, most=500):
    with _lock:
        if len(cache) > most:
            cache.clear()
        cache[key] = (now, value)


# ══ Insider deals ══

_PRICE_RE = re.compile(r"price\s+\$?([\d,]+(?:\.\d+)?)(?:\s*-\s*\$?([\d,]+(?:\.\d+)?))?", re.I)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


KIND_LABELS = {"buy": "Bought", "sell": "Sold", "award": "Award / grant", "option": "Option exercise", "gift": "Gift", "other": "Other"}


def _kind(text, transaction):
    """What kind of insider transaction this is, from Yahoo's description:
    buy, sell, award (shares given as pay), option (options exercised or converted), gift, or other."""
    words = f" {text or ''} {transaction or ''} ".lower()
    if "sale" in words or " sell" in words or " sold" in words:
        return "sell"
    if "purchase" in words or " buy" in words or " bought" in words:
        return "buy"
    if "award" in words or "grant" in words:
        return "award"
    if "exercise" in words or "conversion" in words or "option" in words:
        return "option"
    if "gift" in words:
        return "gift"
    return "other"


def _price_from_text(text):
    """'Sale at price 226.40 - 228.00 per share.' → 227.2 (the middle of a range)."""
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    lo = _num(m.group(1).replace(",", ""))
    hi = _num(m.group(2).replace(",", "")) if m.group(2) else None
    if lo is None:
        return None
    return (lo + hi) / 2 if hi is not None else lo


def _nice_name(name):
    """Yahoo gives 'COOK TIMOTHY D'; show 'Cook Timothy D'."""
    name = " ".join(str(name or "").split())
    if name.isupper():
        name = " ".join(w.capitalize() if len(w) > 1 else w for w in name.split())
    return name


def parse_insider_rows(df, now=None):
    """Turn Yahoo's insider table into transactions from the last INSIDER_DAYS days, newest first: buys, sells, share
    awards, option exercises, gifts and anything else, each with its kind."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) if now is None else now
    cutoff = now - dt.timedelta(days=INSIDER_DAYS)
    deals = []
    if df is None or getattr(df, "empty", True):
        return deals
    for _, row in df.iterrows():
        when = row.get("Start Date")
        try:
            when = when.to_pydatetime() if hasattr(when, "to_pydatetime") else dt.datetime.fromisoformat(str(when)[:19])
        except (TypeError, ValueError):
            continue
        if when.tzinfo is not None:
            when = when.replace(tzinfo=None)
        if when < cutoff or when > now + dt.timedelta(days=2):
            continue
        text = str(row.get("Text") or "")
        kind = _kind(text, row.get("Transaction"))
        shares = _num(row.get("Shares"))
        if not shares or shares <= 0:
            continue
        value = _num(row.get("Value"))
        price = value / shares if value and value > 0 else _price_from_text(text)
        if not price or price <= 0:                       # awards and gifts are "at price 0.00": no price, no value
            price = None
        if (not value or value <= 0) and price:
            value = price * shares
        deals.append({"date": when.strftime("%Y-%m-%d"), "name": _nice_name(row.get("Insider")),
                      "role": " ".join(str(row.get("Position") or "").split()) or "Insider",
                      "type": kind, "label": KIND_LABELS[kind], "shares": int(round(shares)),
                      "price": round(price, 2) if price else None, "value": round(value, 2) if value else None})
    deals.sort(key=lambda d: d["date"], reverse=True)
    return deals


def insider_deals(ticker, now=None):
    """{"ticker", "deals": [...], "days": 365} for the stock popup. Kept for an hour."""
    key = str(ticker or "").upper().strip()[:20]
    stamp = time.time() if now is None else now
    hit = _cache_get(_insiders, key, INSIDER_TTL, stamp)
    if hit is not None:
        return hit
    try:
        df = _yf().Ticker(key).insider_transactions
        deals = parse_insider_rows(df)
        result = {"ticker": key, "deals": deals, "days": INSIDER_DAYS}
    except Exception as e:                                         # noqa: BLE001 - shown as "couldn't load"
        log.warning("Insider deals for %s failed: %s", key, e)
        return {"ticker": key, "deals": [], "days": INSIDER_DAYS, "error": "Insider deals couldn't be loaded just now."}
    _cache_put(_insiders, key, result, stamp)
    return result


# ══ SEC filings ══

def sec_user_agent():
    custom = os.getenv("SEC_USER_AGENT", "").strip()
    if custom:
        return custom
    contact = os.getenv("SUPPORT_EMAIL", "").strip() or "ayeim.noreply@gmail.com"
    return f"Aymetrix {contact}"


def _sec_get(url, stream=False):
    return requests.get(url, headers={"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip, deflate"},
                        timeout=TIMEOUT, stream=stream)


def _ticker_map(now):
    """{"AAPL": (320193, "Apple Inc."), ...} from the SEC's own list."""
    with _lock:
        if _map["data"] and now - _map["at"] < MAP_TTL:
            return _map["data"]
    r = _sec_get("https://www.sec.gov/files/company_tickers.json")
    r.raise_for_status()
    data = {}
    for row in (r.json() or {}).values():
        t = str(row.get("ticker") or "").upper()
        if t and t not in data:
            data[t] = (int(row["cik_str"]), str(row.get("title") or ""))
    with _lock:
        _map.update(at=now, data=data)
    return data


def _lookup(ticker, now):
    data = _ticker_map(now)
    t = ticker.upper()
    return data.get(t) or data.get(t.replace(".", "-")) or data.get(t.replace("-", "."))


def _submissions(cik, now):
    hit = _cache_get(_filings, cik, FILINGS_TTL, now)
    if hit is not None:
        return hit
    r = _sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    r.raise_for_status()
    data = r.json()
    _cache_put(_filings, cik, data, now, most=300)
    return data


ITEM_LABELS = {
    "1.01": "Major new agreement or contract",
    "1.02": "Major agreement ended",
    "1.03": "Bankruptcy or receivership",
    "1.04": "Mine safety notice",
    "1.05": "Cybersecurity incident",
    "2.01": "Acquisition or sale completed",
    "2.02": "Results announced",
    "2.03": "New debt or borrowing",
    "2.04": "Debt payment brought forward",
    "2.05": "Restructuring or cost cuts",
    "2.06": "Write-down of assets",
    "3.01": "Stock exchange listing notice",
    "3.02": "New shares sold privately",
    "3.03": "Change to shareholders' rights",
    "4.01": "Change of auditor",
    "4.02": "Earlier results corrected",
    "5.01": "Change of control",
    "5.02": "Leadership change (directors or executives)",
    "5.03": "Company rules or year-end changed",
    "5.04": "Pension plan trading suspended",
    "5.05": "Code of ethics changed",
    "5.07": "Shareholder vote results",
    "5.08": "Shareholder nomination dates",
    "7.01": "Announcement or presentation",
    "8.01": "Other news or announcement",
    "9.01": "Financial statements and attachments",
}
ANNUAL_FORMS = {"10-K": "Annual report (10-K)", "20-F": "Annual report (20-F)", "40-F": "Annual report (40-F)"}
MEETING_FORMS = {"DEF 14A": "Shareholder meeting notice (proxy statement)"}
ANNOUNCE_FORMS = {"8-K", "6-K"}


def item_labels(items, form="8-K"):
    """'2.02,9.01' → ['Results announced'] (the attachments item is left out when there's something else)."""
    if form == "6-K":
        return ["Report from a foreign company"]
    codes = [c.strip() for c in str(items or "").split(",") if c.strip()]
    main = [c for c in codes if c != "9.01"] or codes
    labels = []
    for c in main:
        label = ITEM_LABELS.get(c)
        if label and label not in labels:
            labels.append(label)
    return labels or ["Announcement"]


def _doc_url(cik, accession, document):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"


def parse_filings(sub, cik, now=None):
    """The three lists the popup shows, from the SEC's 'recent filings' arrays."""
    today = (dt.datetime.now(dt.timezone.utc) if now is None else now).date()
    rec = ((sub or {}).get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    out = {"annual": [], "meetings": [], "announcements": []}
    for i, form in enumerate(forms):
        try:
            acc = rec["accessionNumber"][i]
            doc = rec["primaryDocument"][i]
            date = rec["filingDate"][i]
        except (KeyError, IndexError):
            continue
        if not (acc and doc and date):
            continue
        entry = {"form": form, "date": date, "report_date": (rec.get("reportDate") or [""] * len(forms))[i] or "",
                 "accession": acc, "url": _doc_url(cik, acc, doc)}
        if form in ANNUAL_FORMS and len(out["annual"]) < ANNUAL_COUNT:
            year = (entry["report_date"] or date)[:4]
            out["annual"].append({**entry, "title": f"{ANNUAL_FORMS[form]} · year {year}"})
        elif form in MEETING_FORMS and len(out["meetings"]) < MEETING_COUNT:
            out["meetings"].append({**entry, "title": MEETING_FORMS[form]})
        elif form in ANNOUNCE_FORMS and len(out["announcements"]) < ANNOUNCE_COUNT:
            try:
                age = (today - dt.date.fromisoformat(date)).days
            except ValueError:
                continue
            if age > ANNOUNCE_DAYS:
                continue
            items = (rec.get("items") or [""] * len(forms))[i]
            out["announcements"].append({**entry, "labels": item_labels(items, form)})
    for k in out:
        out[k].sort(key=lambda e: e["date"], reverse=True)
    return out


def filings(ticker, now=None):
    """{"found", "company", "cik", "annual", "meetings", "announcements", "sec_url"} for the popup."""
    key = str(ticker or "").upper().strip()[:20]
    stamp = time.time() if now is None else now
    try:
        found = _lookup(key, stamp)
        if not found:
            return {"ticker": key, "found": False}
        cik, company = found
        lists = parse_filings(_submissions(cik, stamp), cik)
    except Exception as e:                                         # noqa: BLE001
        log.warning("SEC filings for %s failed: %s", key, e)
        return {"ticker": key, "found": None, "error": "Filings couldn't be loaded from the SEC just now. Please try again later."}
    return {"ticker": key, "found": True, "company": company, "cik": cik, **lists,
            "sec_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=&dateb=&owner=include&count=40"}


def find_document(ticker, accession, now=None):
    """The filing with this accession number among the ones we show for this stock, or None. Downloads only go through
    for these, so the server can't be used to fetch anything else."""
    data = filings(ticker, now)
    if not data.get("found"):
        return None
    for group in ("annual", "meetings", "announcements"):
        for e in data[group]:
            if e["accession"] == accession:
                return e
    return None


def open_document(url):
    """A streaming response for a filing document from the SEC (raises on failure)."""
    r = _sec_get(url, stream=True)
    r.raise_for_status()
    return r
