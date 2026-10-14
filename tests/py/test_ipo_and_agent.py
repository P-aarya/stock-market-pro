import json
import unittest
from datetime import date

import _path  # noqa: F401
import agent
import ipo_data as I
import screener as SC


class IpoParsing(unittest.TestCase):
    def test_price_ranges(self):
        self.assertEqual(I._price_range("14.00-16.00"), (14.0, 16.0)); self.assertEqual(I._price_range("10.00"), (10.0, 10.0))
        self.assertEqual(I._price_range(""), (None, None)); self.assertEqual(I._price_range("NA"), (None, None))

    def test_numbers_and_names(self):
        self.assertEqual(I._number("$153,333,344"), 153333344.0); self.assertIsNone(I._number(""))
        self.assertEqual(I._clean_name("AI OKTO CORP. Common Stock"), "AI OKTO CORP.")
        self.assertEqual(I._clean_name("Southern Cross Acquisition II Corp. Ordinary Shares"), "Southern Cross Acquisition II Corp.")

    def test_month_list_crosses_year_end(self):
        self.assertEqual(I._months_between(date(2026, 11, 20), date(2027, 1, 5)), ["2026-11", "2026-12", "2027-01"])

    def test_merge_prefers_nasdaq_terms_and_drops_duplicate_yahoo_rows(self):
        base = {"exchange": "", "status": "Expected", "price_low": None, "price_high": None, "shares": None, "deal_value": None}
        yahoo = [{**base, "ticker": "TRXB", "company": "TRex Bio, Inc.", "date": date(2026, 10, 9), "sources": ["Yahoo Finance"]},
                 {**base, "ticker": "TRXB", "company": "TRex Bio, Inc.", "date": date(2026, 10, 9), "sources": ["Yahoo Finance"]},
                 {**base, "ticker": "ONLYY", "company": "Only Yahoo Co", "date": date(2026, 10, 10), "sources": ["Yahoo Finance"]}]
        nasdaq = [{**base, "ticker": "TRXB", "company": "TRex Bio, Inc.", "exchange": "NASDAQ Global", "date": date(2026, 10, 9), "price_low": 14.0, "price_high": 16.0, "shares": 8333334, "sources": ["Nasdaq"]}]
        merged = I._merge(yahoo, nasdaq)
        self.assertEqual(len(merged), 2)
        trxb = next(r for r in merged if r["ticker"] == "TRXB")
        self.assertEqual((trxb["price_low"], trxb["shares"], trxb["exchange"]), (14.0, 8333334, "NASDAQ Global")); self.assertEqual(trxb["sources"], ["Nasdaq", "Yahoo Finance"])


class Filters(unittest.TestCase):
    def test_malformed_filters_are_repaired_or_dropped(self):
        cleaned = SC.clean_filters({"current_price": 50, "pe_ratio": {"max": "25", "min": "x"}, "sector": "Tech", "buy_signal": "BUY", "bogus": 1,
                                    "pays_dividend": True, "beta": {"max": None}, "sentiment": {"min": "0.2"}})
        self.assertEqual(cleaned, {"pe_ratio": {"max": 25.0}, "sector": "Tech", "buy_signal": "BUY", "pays_dividend": True, "sentiment": {"min": 0.2}})
        self.assertEqual(SC.clean_filters("nonsense"), {})

    def test_buy_and_sell_include_the_strong_variants(self):
        self.assertEqual(SC.SIGNAL_GROUPS["BUY"], ["Buy", "Strong Buy"]); self.assertEqual(SC.SIGNAL_GROUPS["SELL"], ["Sell", "Strong Sell"])


class RouterAndLookups(unittest.TestCase):
    def test_route_parsing_survives_fences_and_junk(self):
        good = '{"intent":"education","answer":"x"}'
        for raw in (good, "```json\n" + good + "\n```", "Sure! " + good + " hope that helps"):
            self.assertEqual(agent.parse_route(raw)["intent"], "education")
        for raw in ('{"intent":"nonsense"}', "not json", "", "[1,2]"):
            self.assertIsNone(agent.parse_route(raw))
        fixed = agent.parse_route('{"intent":"compare","filters":"x","compare_items":"y","answer":null}')
        self.assertEqual((fixed["filters"], fixed["compare_items"], fixed["answer"]), ({}, [], ""))

    def test_ipo_is_a_valid_intent(self):
        self.assertIn("ipo", agent.VALID_INTENTS); self.assertEqual(agent.parse_route('{"intent":"ipo"}')["intent"], "ipo")

    def test_company_name_matching_ignores_corporate_suffixes(self):
        self.assertEqual(agent._name_tokens("The Coca-Cola Company"), ["coca", "cola"]); self.assertEqual(agent._name_tokens("Snap Inc."), ["snap"])
        self.assertEqual(agent._name_tokens("Snap-on Incorporated"), ["snap", "on"])

    def test_commodities_are_whole_words_in_order_longest_first(self):
        names = lambda q: [c["name"] for c in agent.detect_commodities(q)]
        self.assertEqual(names("compare gold and silver"), ["Gold", "Silver"]); self.assertEqual(names("gold vs crude oil vs copper"), ["Gold", "Crude Oil (WTI)", "Copper"])
        self.assertEqual(names("brent crude oil versus wti"), ["Brent Crude", "Crude Oil (WTI)"]); self.assertEqual(names("apple vs microsoft"), [])
        self.assertEqual(names("together we rise in Las Vegas"), []); self.assertEqual(agent.detect_commodity("what is soil"), None)
        self.assertEqual(agent.detect_commodity("natural gas outlook")["name"], "Natural Gas")

    def test_history_block_trims_and_labels(self):
        text = agent.history_block([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x" * 900}], max_messages=2, max_chars=50)
        self.assertEqual(text.split("\n")[0], "USER: hi"); self.assertEqual(len(text.split("\n")[1]), len("ASSISTANT: ") + 50)

    def test_stock_summaries_carry_sentiment_and_conflict_notes(self):
        row = {"ticker": "X", "company_name": "X Inc", "current_price": 10.0, "price_change_pct": 1.0, "target_1w": 10.5, "news_sentiment": -0.4,
               "key_factors": json.dumps(["⚠️ Technical signal is bullish, but the model forecasts -3.6% over the next 30 trading days"])}
        text = agent.format_stock(row)
        self.assertIn("News Sentiment: Negative (-0.40)", text); self.assertIn("Note: Technical signal is bullish", text); self.assertIn("Backtest hit rate", text)


if __name__ == "__main__":
    unittest.main()
