"""
Recent daily closing prices for several symbols at once, from Yahoo's "spark" route (the one its mini-charts use).

It needs no Yahoo sign-in key ("crumb"), which Yahoo sometimes refuses to cloud servers for hours, and it answers for
many symbols in one request. Answers are kept in memory for a while, so the Watchlist and the Portfolio chart don't ask
Yahoo again on every page load.

    closes(["AAPL", "MSFT"], "3mo")  ->  {"AAPL": {"dates": ["2026-08-17", ...], "closes": [231.1, ...]}, ...}

A symbol Yahoo has nothing for is left out.
"""
import threading
import time
from datetime import datetime, timezone

SPARK_URL = "https://query1.finance.yahoo.com/v7/finance/spark"
BATCH = 20                       # symbols per request
TTL = {"1mo": 1800, "3mo": 1800, "6mo": 3600, "1y": 3600}
_cache = {}                      # (symbol, range) -> (expires, value or None)
_lock = threading.Lock()
_session = [None]


def _http():
    if _session[0] is None:
        try:
            from curl_cffi import requests as creq            # comes with yfinance; looks like a real browser to Yahoo
            _session[0] = creq.Session(impersonate="chrome")
        except Exception:
            import requests
            s = requests.Session()
            s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            _session[0] = s
    return _session[0]


def _series(stamps, closes):
    dates, values = [], []
    for ts, c in zip(stamps or [], closes or []):
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c != c or c <= 0:                                  # NaN or nonsense
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        if dates and dates[-1] == day:
            values[-1] = c
        else:
            dates.append(day)
            values.append(c)
    return {"dates": dates, "closes": values} if values else None


def parse(data):
    """Yahoo's spark answer -> {symbol: {"dates", "closes"}}. Yahoo uses two layouts; both are read."""
    out = {}
    if isinstance(data, dict) and isinstance(data.get("spark"), dict):      # {"spark": {"result": [{symbol, response: [...]}]}}
        for item in data["spark"].get("result") or []:
            resp = ((item or {}).get("response") or [{}])[0] or {}
            quote = ((resp.get("indicators") or {}).get("quote") or [{}])[0] or {}
            s = _series(resp.get("timestamp"), quote.get("close"))
            if item.get("symbol") and s:
                out[item["symbol"]] = s
    elif isinstance(data, dict):                                             # {"AAPL": {timestamp: [...], close: [...]}}
        for sym, item in data.items():
            if isinstance(item, dict):
                s = _series(item.get("timestamp"), item.get("close"))
                if s:
                    out[item.get("symbol") or sym] = s
    return out


def _fetch(symbols, rng):
    r = _http().get(SPARK_URL, params={"symbols": ",".join(symbols), "range": rng, "interval": "1d"}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Yahoo answered {r.status_code}")
    return parse(r.json())


def closes(symbols, rng="3mo"):
    symbols = list(dict.fromkeys(s for s in symbols if s))
    now = time.time()
    out, missing = {}, []
    with _lock:
        for s in symbols:
            hit = _cache.get((s, rng))
            if hit and hit[0] > now:
                if hit[1]:
                    out[s] = hit[1]
            else:
                missing.append(s)
    for i in range(0, len(missing), BATCH):
        batch = missing[i:i + BATCH]
        try:
            got = _fetch(batch, rng)
        except Exception as e:
            print(f"market_history: {rng} prices for {batch[0]}... couldn't be loaded: {e}")
            continue                                          # not cached: tried again next time
        with _lock:
            for s in batch:
                _cache[(s, rng)] = (now + TTL.get(rng, 1800) if s in got else now + 600, got.get(s))
        out.update({s: got[s] for s in batch if s in got})
    return out
