import unittest
from datetime import date

import numpy as np
import pandas as pd

import _path  # noqa: F401
import predict as P


def history(start, closes):
    return pd.DataFrame({"Close": closes}, index=pd.bdate_range(start, periods=len(closes), tz="America/New_York"))


class ScoringPastPredictions(unittest.TestCase):
    run_date = date(2026, 9, 1)

    def test_seventh_trading_day_after_the_run_date(self):
        h = history("2026-09-01", list(range(100, 112)))
        actual, when, correct = P.evaluate_outcome(h, self.run_date, 100.0, 103.0)
        self.assertEqual((actual, str(when), correct), (107.0, "2026-09-10", 1))

    def test_direction_hits_and_misses(self):
        down = history("2026-09-01", [100] + [95] * 10)
        self.assertEqual(P.evaluate_outcome(down, self.run_date, 100.0, 103.0)[2], 0)
        self.assertEqual(P.evaluate_outcome(down, self.run_date, 100.0, 97.0)[2], 1)

    def test_not_ready_or_missing_history(self):
        self.assertIsNone(P.evaluate_outcome(history("2026-09-01", [100] + [101] * 6), self.run_date, 100.0, 103.0))
        self.assertIsNone(P.evaluate_outcome(pd.DataFrame({"Close": []}), self.run_date, 100.0, 103.0)); self.assertIsNone(P.evaluate_outcome(None, self.run_date, 100.0, 103.0))

    def test_the_run_date_itself_is_not_day_one(self):
        self.assertEqual(P.evaluate_outcome(history("2026-09-01", [100] + [100] * 6 + [200]), self.run_date, 100.0, 150.0)[0], 200)


class Ensemble(unittest.TestCase):
    x = {7: {"target": 110.0, "confidence": 60, "prob_up": 60}}
    p = {7: {"target": 100.0, "prob_up": 30, "upper": 105, "lower": 95}}

    def setUp(self):
        self.saved = (P.PROPHET_WEIGHT, P.FORECAST_SHRINK); P.FORECAST_SHRINK = 1.0

    def tearDown(self):
        P.PROPHET_WEIGHT, P.FORECAST_SHRINK = self.saved

    def test_weights(self):
        P.PROPHET_WEIGHT = 0.4; self.assertEqual(P.ensemble(self.x, self.p, 100.0)[7]["target"], 106.0)
        P.PROPHET_WEIGHT = 0.0; self.assertEqual(P.ensemble(self.x, self.p, 100.0)[7]["target"], 110.0)
        P.PROPHET_WEIGHT = 1.0; self.assertEqual(P.ensemble(self.x, self.p, 100.0)[7]["target"], 100.0)

    def test_confidence_is_the_holdout_hit_rate_unadjusted(self):
        P.PROPHET_WEIGHT = 0.4; e = P.ensemble(self.x, self.p, 100.0)[7]
        self.assertEqual((e["confidence"], e["prob_up"], e["models_agree"]), (60, 60, False))          # no +/-10 tweak, no Prophet 70/30

    def test_forecasts_are_shrunk_toward_no_change_without_changing_direction(self):
        P.PROPHET_WEIGHT = 0.0; P.FORECAST_SHRINK = 0.25
        e = P.ensemble(self.x, self.p, 100.0)[7]
        self.assertEqual(e["target"], 102.5); self.assertEqual(e["return_pct"], 2.5); self.assertEqual(e["raw_return_pct"], 10.0)
        self.assertEqual(P.ensemble({7: {"target": 90.0, "confidence": 55, "prob_up": 45}}, {}, 100.0)[7]["target"], 97.5)


class Signals(unittest.TestCase):
    def frame(self, **o):
        row = {"ma50": 100.0, "ma200": 90.0, "rsi": 55.0, "macd": 1.0, "mom_30d": 5.0, "volatility": 20.0}; row.update(o)
        return pd.DataFrame([row])

    def score(self, **o):
        return P.get_signals(self.frame(**o), {}, 105.0, {"beta": 1.0})[1]

    def test_rsi_helps_in_the_middle_but_overbought_and_oversold_count_the_other_way(self):
        self.assertGreater(self.score(rsi=65), self.score(rsi=55))      # momentum zone
        self.assertLess(self.score(rsi=90), self.score(rsi=70))         # overbought: caution, as the explanation says
        self.assertGreater(self.score(rsi=10), self.score(rsi=30))      # oversold: potential bounce

    def test_risk_uses_beta_and_volatility_and_treats_missing_beta_as_unknown(self):
        self.assertEqual(P.get_signals(self.frame(volatility=70), {}, 105, {"beta": None})[2], "High")
        self.assertEqual(P.get_signals(self.frame(volatility=20), {}, 105, {"beta": 2.2})[2], "High")
        self.assertEqual(P.get_signals(self.frame(volatility=20), {}, 105, {"beta": None})[2], "Low")

    def test_news_sentiment_moves_the_score_a_little_and_is_explained(self):
        base = P.get_signals(self.frame(), {}, 105.0, {}, None)
        up = P.get_signals(self.frame(), {}, 105.0, {}, 0.8); down = P.get_signals(self.frame(), {}, 105.0, {}, -0.8)
        self.assertGreater(up[1], base[1]); self.assertLess(down[1], base[1]); self.assertLessEqual(up[1] - base[1], P.SENTIMENT_WEIGHT)
        self.assertTrue(any("News sentiment positive" in f for f in up[3]))

    def test_conflict_between_the_signal_and_the_forecast_is_called_out(self):
        bullish_signal_falling_forecast = {30: {"return_pct": -3.6}}
        factors = P.get_signals(self.frame(rsi=60), bullish_signal_falling_forecast, 105.0, {})[3]
        self.assertTrue(factors[0].startswith("⚠️ Technical signal is bullish"), factors)
        agreeing = P.get_signals(self.frame(rsi=60), {30: {"return_pct": 3.0}}, 105.0, {})[3]
        self.assertFalse(any(f.startswith("⚠️") for f in agreeing))


class BlankRowFromYahoo(unittest.TestCase):
    """Yahoo returns today's row with every price missing until the market data settles. That used to make every forecast NaN."""

    def frame(self):
        df = history("2026-09-01", [100.0, 101.0, 102.0, 103.0]); df["High"] = df["Close"] + 1; df["Low"] = df["Close"] - 1; df["Volume"] = 1000
        blank = df.iloc[[-1]].copy(); blank.index = blank.index + pd.offsets.BDay(1); blank[["Close", "High", "Low"]] = np.nan
        return pd.concat([df, blank])

    def test_the_blank_row_is_dropped(self):
        raw = self.frame(); clean = P.clean_history(raw)
        self.assertTrue(np.isnan(raw["Close"].iloc[-1])); self.assertEqual(len(clean), len(raw) - 1); self.assertEqual(float(clean["Close"].iloc[-1]), 103.0)
        self.assertIsNone(P.clean_history(None))

    def test_a_forecast_with_a_nan_is_caught_before_the_database(self):
        self.assertTrue(P.all_finite({7: {"target": 100.0, "confidence": 60, "models_agree": True, "xgb_target": None}}))
        self.assertFalse(P.all_finite({7: {"target": float("nan")}})); self.assertFalse(P.all_finite({30: {"target": 1.0}, 7: {"return_pct": np.float64("inf")}}))


class ModelTraining(unittest.TestCase):
    def test_forecast_comes_with_a_holdout_check_against_always_guessing_up(self):
        rng = np.random.default_rng(7); n = 700
        close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.012, n))
        df = pd.DataFrame({"Close": close, "High": close * 1.01, "Low": close * 0.99, "Volume": rng.integers(1_000_000, 2_000_000, n)}, index=pd.bdate_range("2023-01-02", periods=n))
        out = P.xgb_predict(P.calc_features(df.copy()), {}, float(close[-1]))
        self.assertTrue({7, 30, 90} <= set(out))
        for horizon in out.values():
            self.assertTrue(0 <= horizon["dir_acc"] <= 100 and 0 <= horizon["up_rate"] <= 100 and 50 <= horizon["confidence"] <= 90)
            self.assertLessEqual(abs(horizon["return_pct"]), 50)


if __name__ == "__main__":
    unittest.main()
