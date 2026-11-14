# ============================================================
# yahoo_batch.py - ask Yahoo about many stocks in ONE request.
#
# Used by the weekly jobs (fetch_earnings.py, fetch_fundamentals.py).
# Yahoo blocks cloud servers (like Render) that ask for thousands of
# stocks one by one ("Too Many Requests", "Invalid Crumb", "User is unable
# to access this feature"). Asking for 150 stocks per request means about
# 20 requests for the whole list, which Yahoo allows.
#
# DetailGate: the per-stock "details" call (stock.info) has numbers the
# batch answer doesn't (margins, growth, revenue ...). It is still tried.
# If Yahoo refuses it 8 times in a row, the job rests 3 minutes and tries
# again (Yahoo's refusals come in waves); after 3 rests it stops asking for
# the rest of the run and keeps the saved values instead of sending
# thousands of refused requests.
# ============================================================

import time

from yfinance.data import YfData
from yfinance.exceptions import YFRateLimitError

QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote?"
BATCH_SIZE = 150
PAUSE = 2                 # seconds between batch requests
RATE_LIMIT_WAIT = 60      # if Yahoo says "too many requests": wait this long, try that batch once more


def reset_yahoo_key():
    """Forget yfinance's saved Yahoo sign-in key ("crumb") so the next request gets a fresh one.
    yfinance has a quirk: if Yahoo says "Too Many Requests" while it fetches the key, it keeps that error text AS the
    key, and every request after that fails with "Invalid Crumb" until the program restarts."""
    try:
        d = YfData()
        d._crumb = None
        d._cookie = None
    except Exception:
        pass


def _one_batch(symbols):
    res = YfData().get_raw_json(QUOTE_URL, params={"symbols": ",".join(symbols), "formatted": "false"})
    return {q["symbol"]: q for q in ((res.get("quoteResponse") or {}).get("result") or []) if q.get("symbol")}


def fetch_quotes(tickers, label="stocks"):
    """Returns (quotes, asked): quotes = {ticker: Yahoo quote}; asked = the tickers whose batch Yahoo actually answered
    (so a ticker in `asked` but not in `quotes` really isn't on Yahoo; one outside `asked` was just refused)."""
    tickers = list(dict.fromkeys(t for t in tickers if t))
    out, asked = {}, set()
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    print("  Asking Yahoo for {} {} in {} requests...".format(len(tickers), label, len(batches)), flush=True)
    refused_in_row = 0
    for b, batch in enumerate(batches, 1):
        for attempt in (1, 2, 3):
            try:
                out.update(_one_batch(batch))
                asked.update(batch)
                refused_in_row = 0
                break
            except Exception as e:
                reset_yahoo_key()
                why = "rate limited" if isinstance(e, YFRateLimitError) else "{}: {}".format(type(e).__name__, str(e)[:80])
                if attempt < 3:
                    wait = RATE_LIMIT_WAIT * attempt
                    print("      batch {}: Yahoo refused ({}) - new key, waiting {}s, trying again...".format(b, why, wait), flush=True)
                    time.sleep(wait)
                else:
                    refused_in_row += 1
                    print("      batch {}: still refused - skipped (old data kept)".format(b), flush=True)
        if refused_in_row >= 2:
            print("  ⚠ Yahoo refused two batches in a row even after waiting - stopping here; old data kept."
                  " Run the job again later.", flush=True)
            break
        if b < len(batches):
            time.sleep(PAUSE)
    print("  Yahoo answered for {} of {}.".format(len(out), len(tickers)), flush=True)
    return out, asked


class DetailGate:
    """Tries stock.info. After `limit` refusals in a row it rests for `rest` seconds and tries again
    (Yahoo's refusals often come in waves); after `max_rests` rests it stops for the rest of the run."""

    def __init__(self, limit=8, pause=0.5, rest=180, max_rests=3):
        self.limit = limit
        self.pause = pause
        self.rest = rest
        self.max_rests = max_rests
        self.rests = 0
        self.fails_in_row = 0
        self.closed = False
        self.got = 0

    def info(self, stock):
        if self.closed:
            return {}
        info = {}
        try:
            info = stock.info or {}
        except YFRateLimitError:
            info = {}
        except Exception:
            info = {}
        time.sleep(self.pause)
        # a real answer has the company's basics; a refused one is empty or nearly empty
        if isinstance(info, dict) and len(info) >= 10 and (info.get("quoteType") or info.get("shortName") or info.get("longName")):
            self.fails_in_row = 0
            self.got += 1
            return info
        self.fails_in_row += 1
        reset_yahoo_key()                               # a refused answer may mean a spoiled key - get a fresh one
        if self.fails_in_row >= self.limit:
            self.fails_in_row = 0
            if self.rests < self.max_rests:
                self.rests += 1
                print("\n  ⏸  Yahoo refused the per-stock details {} times in a row - resting {} min, then trying again"
                      " ({}/{})\n".format(self.limit, self.rest // 60, self.rests, self.max_rests), flush=True)
                time.sleep(self.rest)
            else:
                self.closed = True
                print("\n  ⚠ Yahoo keeps refusing the per-stock details - not asking for the rest of this run."
                      "\n    Those stocks keep their saved values; everything from the batch answer is still updated.\n",
                      flush=True)
        return {}


def analyst_score(q):
    """'2.1 - Buy' -> 2.1"""
    v = q.get("averageAnalystRating")
    try:
        return float(str(v).split("-")[0].strip()) if v else None
    except ValueError:
        return None
