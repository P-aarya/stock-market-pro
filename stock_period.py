"""
How a stock performed over any period (a year, a quarter or custom dates), compared with the S&P 500 over the same days.

The full daily price history of a stock is fetched once from Yahoo Finance and cached, so picking different periods
afterwards is instant. Prices are adjusted for splits and dividends, so the change is a total return.
"""
import math
import threading
import time
from datetime import date, datetime

import numpy as np
import pandas as pd

BENCHMARK = "SPY"
BENCHMARK_NAME = "S&P 500 (SPY)"
HISTORY_TTL = 6 * 3600       # how long a downloaded history is reused
MAX_CACHED = 40              # histories kept in memory
MAX_POINTS = 400             # points sent to the chart (statistics use every day)
MIN_YEAR = 1970
MAX_SPAN_YEARS = 40
LATE_START_DAYS = 10         # a first price this many days after the requested start means the stock was not trading yet

_cache = {}                  # ticker -> {"series": pd.Series, "at": float}
_lock = threading.Lock()


class PeriodError(Exception):
    """A problem the user can act on. `status` is the HTTP status to return."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


# ── price history ─────────────────────────────────────────

def _download(ticker: str) -> pd.Series:
    import yfinance as yf
    closes = yf.Ticker(ticker).history(period="max", timeout=20)["Close"].dropna()
    closes.index = pd.to_datetime([str(x)[:10] for x in closes.index])
    return closes[~closes.index.duplicated(keep="last")].sort_index()


def full_history(ticker: str) -> pd.Series:
    """Every daily close for the ticker (cached). Raises PeriodError if Yahoo has no prices for it."""
    ticker = ticker.upper()
    with _lock:
        hit = _cache.get(ticker)
        if hit and time.time() - hit["at"] < HISTORY_TTL:
            return hit["series"]
    series = _download(ticker)
    if len(series) < 2:
        raise PeriodError(f"No price history is available for {ticker}.", 404)
    with _lock:
        _cache[ticker] = {"series": series, "at": time.time()}
        while len(_cache) > MAX_CACHED:               # drop the oldest download
            del _cache[min(_cache, key=lambda k: _cache[k]["at"])]
    return series


def price_range(ticker: str) -> dict:
    s = full_history(ticker)
    return {"first_date": s.index[0].strftime("%Y-%m-%d"), "last_date": s.index[-1].strftime("%Y-%m-%d")}


# ── statistics (pure, so they can be tested without a network) ──

def _pct(a, b):
    return (b / a - 1) * 100


def compute_period(closes: pd.Series, benchmark, start: date, end: date) -> dict:
    """
    Performance of `closes` between start and end (inclusive), measured from the first to the last trading day
    that falls inside the period. `benchmark` is an optional second series for the same days.
    """
    s = pd.Timestamp(start); e = pd.Timestamp(end)
    seg = closes[(closes.index >= s) & (closes.index <= e)].dropna()
    if len(seg) < 2:
        raise PeriodError("There isn't enough price data in that period. The stock may not have been trading yet, or the dates are too close together.", 404)

    first, last = float(seg.iloc[0]), float(seg.iloc[-1])
    daily = seg.pct_change().dropna()
    running_peak = seg.cummax()
    drawdown = (seg / running_peak - 1) * 100
    worst_dd_end = drawdown.idxmin()
    peak_before = seg[:worst_dd_end].idxmax()

    stats = {
        "start_price": round(first, 2), "end_price": round(last, 2),
        "actual_start": seg.index[0].strftime("%Y-%m-%d"), "actual_end": seg.index[-1].strftime("%Y-%m-%d"),
        "change_pct": round(_pct(first, last), 2), "change_usd": round(last - first, 2),
        "high": round(float(seg.max()), 2), "high_date": seg.idxmax().strftime("%Y-%m-%d"),
        "low": round(float(seg.min()), 2), "low_date": seg.idxmin().strftime("%Y-%m-%d"),
        "max_drawdown_pct": round(float(drawdown.min()), 2),
        "max_drawdown_peak_date": peak_before.strftime("%Y-%m-%d"), "max_drawdown_low_date": worst_dd_end.strftime("%Y-%m-%d"),
        "volatility_pct": round(float(daily.std(ddof=1) * math.sqrt(252) * 100), 1) if len(daily) >= 20 else None,
        "up_days_pct": round(float((daily > 0).mean() * 100), 1) if len(daily) else None,
        "best_day_pct": round(float(daily.max() * 100), 2), "best_day": daily.idxmax().strftime("%Y-%m-%d"),
        "worst_day_pct": round(float(daily.min() * 100), 2), "worst_day": daily.idxmin().strftime("%Y-%m-%d"),
        "trading_days": int(len(seg)),
        "starts_late": (seg.index[0] - s).days > LATE_START_DAYS,
        "ends_early": (e - seg.index[-1]).days > LATE_START_DAYS + 4,
    }

    bench_values = None
    if benchmark is not None:
        b = benchmark.reindex(seg.index, method="ffill").dropna()
        if len(b) >= 2 and len(b) == len(seg):
            stats["benchmark_change_pct"] = round(_pct(float(b.iloc[0]), float(b.iloc[-1])), 2)
            stats["difference_pp"] = round(stats["change_pct"] - stats["benchmark_change_pct"], 2)
            bench_values = (b / float(b.iloc[0]) * first)     # drawn from the stock's starting price, so the lines compare directly
    stats.setdefault("benchmark_change_pct", None); stats.setdefault("difference_pp", None)

    step = max(1, math.ceil(len(seg) / MAX_POINTS))
    idx = list(range(0, len(seg), step))
    if idx[-1] != len(seg) - 1:
        idx.append(len(seg) - 1)
    return {
        "stats": stats,
        "dates": [seg.index[i].strftime("%Y-%m-%d") for i in idx],
        "prices": [round(float(seg.iloc[i]), 2) for i in idx],
        "benchmark": [round(float(bench_values.iloc[i]), 2) for i in idx] if bench_values is not None else None,
        "benchmark_name": BENCHMARK_NAME if bench_values is not None else None,
    }


def validate_dates(start: date, end: date, today: date = None) -> tuple:
    """Returns (start, end) with `end` capped at today. Raises PeriodError for dates that make no sense."""
    today = today or date.today()
    if start >= end:
        raise PeriodError("The start date must be before the end date.")
    if start.year < MIN_YEAR:
        raise PeriodError(f"Dates before {MIN_YEAR} aren't supported.")
    if start > today:
        raise PeriodError("That period hasn't started yet.")
    if (min(end, today) - start).days > MAX_SPAN_YEARS * 366:
        raise PeriodError(f"Please choose a period of {MAX_SPAN_YEARS} years or less.")
    return start, min(end, today)


def period_performance(ticker: str, start: date, end: date) -> dict:
    start, end = validate_dates(start, end)
    stock = full_history(ticker)
    try:
        bench = full_history(BENCHMARK)
    except Exception:
        bench = None                       # the comparison is a bonus: show the stock without it
    result = compute_period(stock, bench, start, end)
    result.update({"ticker": ticker.upper(), "requested_start": start.isoformat(), "requested_end": end.isoformat(),
                   "adjusted": "Prices are adjusted for splits and dividends."})
    return result
