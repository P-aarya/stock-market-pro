"""The peer comparison's price chart over a period the person chooses. Prices are fake (a straight line per stock) and the stock
list is stubbed, so nothing touches Yahoo or the database."""
import unittest
from datetime import date, timedelta
from unittest import mock

import pandas as pd

import _path  # noqa: F401
from _accounts import AccountsTestCase, signed_in_client
import api
import peers
import stock_period

TODAY = date(2026, 10, 20)                       # a Tuesday


def history(first, last="2026-10-20", price=100.0, step=0.1):
    """One close per weekday, rising in a straight line, so the right answer is easy to work out by hand."""
    index = pd.bdate_range(first, last)
    return pd.Series([price + step * i for i in range(len(index))], index=index)


HISTORIES = {"AAA": history("2015-01-02", price=50.0, step=0.05), "BBB": history("2015-01-02", price=200.0, step=0.4),
             "NEW": history("2026-03-02", price=20.0, step=0.2),                  # began trading this year
             "OLD": history("2010-01-04", last="2020-06-30", price=10.0, step=0.01)}   # stopped trading in 2020
TRACKED = set(HISTORIES) | {"GONE"}


def fake_history(ticker):
    if ticker == "GONE":
        raise stock_period.PeriodError("No price history is available for GONE.", 404)
    return HISTORIES[ticker]


def change(ticker, first, last):
    s = HISTORIES[ticker]
    return round((float(s[pd.Timestamp(last)]) / float(s[pd.Timestamp(first)]) - 1) * 100, 2)


class Stubbed(unittest.TestCase):
    def setUp(self):
        peers._price_cache.clear()
        self.history = mock.patch.object(stock_period, "full_history", side_effect=fake_history).start()
        mock.patch.object(peers, "_query", side_effect=lambda sql, params=(): [{"ticker": t} for t in params if t in TRACKED]).start()
        self.addCleanup(mock.patch.stopall)

    def prices(self, tickers=("AAA", "BBB"), **kw):
        return peers.rebased_prices(list(tickers), today=TODAY, **kw)


class PriceSeries(Stubbed):
    def test_with_no_period_the_last_year_is_charted_as_before(self):
        d = self.prices()
        self.assertEqual((d["requested_start"], d["requested_end"]), ("2025-10-20", "2026-10-20"))
        self.assertEqual((d["start"], d["end"]), ("2025-10-20", "2026-10-20"))
        self.assertEqual([s[0] for s in d["series"].values()], [100.0, 100.0])
        self.assertEqual(d["missing"], []); self.assertFalse(d["starts_late"]); self.assertEqual(d["limited_by"], [])

    def test_a_chosen_period_is_cut_out_of_the_history_and_rebased_to_100(self):
        d = self.prices(start=date(2024, 1, 2), end=date(2024, 3, 29))
        self.assertEqual((d["start"], d["end"]), ("2024-01-02", "2024-03-29"))
        self.assertTrue(all("2024-01-02" <= x <= "2024-03-29" for x in d["dates"]))
        self.assertEqual({len(v) for v in d["series"].values()}, {len(d["dates"])})
        s = HISTORIES["BBB"]
        mid = d["dates"][10]
        self.assertAlmostEqual(d["series"]["BBB"][10], round(float(s[pd.Timestamp(mid)]) / float(s[pd.Timestamp("2024-01-02")]) * 100, 2), places=2)
        self.assertEqual(d["series"]["BBB"][0], 100.0)

    def test_the_change_over_the_period_is_reported_per_stock(self):
        d = self.prices(start=date(2024, 1, 2), end=date(2024, 3, 29))
        self.assertEqual(d["changes"], {"AAA": change("AAA", "2024-01-02", "2024-03-29"), "BBB": change("BBB", "2024-01-02", "2024-03-29")})
        self.assertAlmostEqual(d["series"]["AAA"][-1] - 100, d["changes"]["AAA"], places=1)

    def test_an_end_date_in_the_future_stops_at_today(self):
        d = self.prices(start=date(2026, 1, 5), end=date(2027, 1, 1))
        self.assertEqual((d["requested_end"], d["end"]), ("2026-10-20", "2026-10-20"))

    def test_max_charts_everything_since_the_first_day_they_all_have_prices(self):
        d = self.prices(to_max=True)
        self.assertEqual((d["requested_start"], d["start"], d["end"]), (None, "2015-01-02", "2026-10-20"))
        self.assertFalse(d["starts_late"])

    def test_a_stock_that_began_trading_inside_the_period_decides_where_the_chart_starts(self):
        d = self.prices(("AAA", "NEW"))
        self.assertEqual(d["start"], "2026-03-02"); self.assertTrue(d["starts_late"]); self.assertEqual(d["limited_by"], ["NEW"])
        self.assertEqual(d["requested_start"], "2025-10-20")
        self.assertEqual(d["changes"]["NEW"], change("NEW", "2026-03-02", "2026-10-20"))
        m = self.prices(("AAA", "NEW"), to_max=True)
        self.assertEqual((m["start"], m["limited_by"]), ("2026-03-02", ["NEW"]))

    def test_a_few_days_of_slack_at_the_start_is_not_a_late_start(self):
        d = self.prices(start=date(2026, 1, 3), end=date(2026, 2, 27))              # a Saturday: the first price is the Monday
        self.assertEqual(d["start"], "2026-01-05"); self.assertFalse(d["starts_late"])

    def test_a_stock_with_no_prices_in_the_period_is_left_out_and_named(self):
        d = self.prices(("AAA", "OLD"))
        self.assertEqual(d["missing"], ["OLD"]); self.assertEqual(list(d["series"]), ["AAA"])
        d = self.prices(("AAA", "OLD"), start=date(2018, 1, 2), end=date(2019, 1, 2))
        self.assertEqual(d["missing"], []); self.assertEqual(set(d["series"]), {"AAA", "OLD"})

    def test_a_stock_we_do_not_track_or_cannot_get_prices_for_is_named_and_never_downloaded_if_unknown(self):
        d = self.prices(("AAA", "ZZZ", "GONE"))
        self.assertEqual(sorted(d["missing"]), ["GONE", "ZZZ"]); self.assertEqual(list(d["series"]), ["AAA"])
        asked = [c.args[0] for c in self.history.call_args_list]
        self.assertNotIn("ZZZ", asked)

    def test_when_nothing_can_be_charted_the_answer_is_empty_but_still_says_what_was_asked_for(self):
        d = self.prices(("OLD",))
        self.assertEqual((d["dates"], d["series"], d["changes"]), ([], {}, {})); self.assertEqual(d["missing"], ["OLD"])
        weekend = self.prices(start=date(2026, 10, 17), end=date(2026, 10, 18))
        self.assertEqual(weekend["dates"], []); self.assertEqual(weekend["requested_start"], "2026-10-17")

    def test_two_trading_days_are_enough_for_a_line(self):
        d = self.prices(start=date(2026, 10, 19), end=date(2026, 10, 20))
        self.assertEqual(d["dates"], ["2026-10-19", "2026-10-20"]); self.assertEqual(d["series"]["AAA"][0], 100.0)

    def test_a_long_period_is_thinned_for_the_chart_but_the_change_uses_every_day(self):
        d = self.prices(to_max=True)
        self.assertLessEqual(len(d["dates"]), peers.MAX_CHART_POINTS + 1)
        self.assertEqual((d["dates"][0], d["dates"][-1]), ("2015-01-02", "2026-10-20"))
        self.assertEqual(d["series"]["AAA"][0], 100.0)
        self.assertEqual(d["changes"]["AAA"], change("AAA", "2015-01-02", "2026-10-20"))
        self.assertEqual({len(v) for v in d["series"].values()}, {len(d["dates"])})

    def test_dates_that_make_no_sense_are_refused_in_words(self):
        for kwargs, words in (({"start": date(2024, 5, 1), "end": date(2024, 1, 1)}, "before the end"), ({"start": date(2024, 1, 1), "end": date(2024, 1, 1)}, "before the end"),
                              ({"start": date(2999, 1, 1), "end": date(2999, 2, 1)}, "hasn't started"), ({"start": date(1960, 1, 1), "end": date(1970, 1, 1)}, "1970"),
                              ({"start": date(1971, 1, 1), "end": date(2026, 1, 1)}, "40 years"), ({"start": date(2024, 1, 1)}, "both a start"), ({"end": date(2024, 1, 1)}, "both a start")):
            with self.assertRaises(stock_period.PeriodError) as caught:
                self.prices(**kwargs)
            self.assertIn(words, caught.exception.message, kwargs)
            self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.history.call_count, 0)                # refused before any price was fetched

    def test_the_same_question_is_answered_from_memory_and_a_different_period_is_not(self):
        self.prices(("AAA", "BBB")); first = self.history.call_count
        self.prices(("AAA", "BBB")); self.assertEqual(self.history.call_count, first)
        self.prices(("AAA", "BBB"), start=date(2024, 1, 2), end=date(2024, 3, 29)); self.assertGreater(self.history.call_count, first)

    def test_the_memory_does_not_grow_without_end(self):
        for i in range(peers.MAX_CACHED_SERIES + 25):                 # a different period every time
            self.prices(("AAA", "BBB"), start=date(2020, 1, 2) + timedelta(days=i), end=date(2020, 12, 31))
        self.assertEqual(len(peers._price_cache), peers.MAX_CACHED_SERIES)


class Endpoint(AccountsTestCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        peers._price_cache.clear()
        self.rebased = mock.patch.object(peers, "rebased_prices", return_value={"dates": ["2025-01-02"], "series": {"AAPL": [100.0]}, "missing": []}).start()
        self.c = signed_in_client(1)

    def get(self, query):
        return self.c.get("/api/compare/prices?tickers=AAPL,MSFT" + query)

    def test_no_period_means_the_last_year(self):
        self.assertEqual(self.get("").status_code, 200)
        args, kwargs = self.rebased.call_args
        self.assertEqual((args[1], args[2], kwargs["to_max"]), (None, None, False))

    def test_start_and_end_are_passed_on_as_dates(self):
        self.assertEqual(self.get("&start=2024-01-02&end=2024-03-29").status_code, 200)
        args, kwargs = self.rebased.call_args
        self.assertEqual((args[1], args[2], kwargs["to_max"]), (date(2024, 1, 2), date(2024, 3, 29), False))

    def test_max_is_passed_on(self):
        self.assertEqual(self.get("&period=max").status_code, 200)
        self.assertTrue(self.rebased.call_args.kwargs["to_max"])

    def test_a_period_and_dates_together_are_refused(self):
        r = self.get("&period=max&start=2024-01-02&end=2024-03-29")
        self.assertEqual(r.status_code, 400); self.assertIn("not both", r.json()["message"])
        self.assertFalse(self.rebased.called)

    def test_nonsense_is_refused_before_any_work(self):
        for query in ("&period=1y", "&period=MAX", "&start=abc&end=2024-01-01", "&start=2024-13-45&end=2024-12-01"):
            self.assertEqual(self.get(query).status_code, 422, query)
        self.assertFalse(self.rebased.called)

    def test_the_reason_a_period_was_refused_reaches_the_person(self):
        self.rebased.side_effect = stock_period.PeriodError("The start date must be before the end date.")
        r = self.get("&start=2024-05-01&end=2024-01-01")
        self.assertEqual(r.status_code, 400); self.assertEqual(r.json()["message"], "The start date must be before the end date.")

    def test_a_real_failure_is_a_plain_500_with_no_details(self):
        self.rebased.side_effect = RuntimeError("secret internals")
        r = self.get("")
        self.assertEqual(r.status_code, 500); self.assertNotIn("secret", r.text)

    def test_the_comparison_still_needs_two_to_six_stocks(self):
        self.assertEqual(self.c.get("/api/compare/prices?tickers=AAPL&start=2024-01-02&end=2024-03-29").status_code, 400)
        self.assertFalse(self.rebased.called)


if __name__ == "__main__":
    unittest.main()
