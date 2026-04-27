"""Unit tests: FVG non-overlap, PinBar ratios, and ATR sizing."""

import pytest
import pandas as pd
import numpy as np
from src.engine.pattern_engine import (
    PatternConfig, PatternDetectors, ConfirmationValidator, Signal,
)


class TestFVGNonOverlap:
    """Strict FVG gap detection per Quant.pdf – 3-bar non-overlap."""

    @pytest.fixture
    def fvg_bullish_df(self):
        df = pd.DataFrame([
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.8, "volume": 100},
            {"open": 102.0, "high": 103.0, "low": 101.8, "close": 102.9, "volume": 120},
            {"open": 103.5, "high": 104.0, "low": 103.2, "close": 103.8, "volume": 90},
        ])
        return df

    @pytest.fixture
    def fvg_bearish_df(self):
        df = pd.DataFrame([
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.8, "volume": 100},
            {"open": 98.0, "high": 98.5, "low": 97.2, "close": 97.3, "volume": 130},
            {"open": 96.0, "high": 96.3, "low": 95.8, "close": 96.1, "volume": 110},
        ])
        return df

    def test_bullish_fvg_detected(self, fvg_bullish_df, strategy_params):
        sig = PatternDetectors.detect_fvg(
            fvg_bullish_df, strategy_params, sweep_direction="bullish", bar_index=2
        )
        assert sig is not None
        assert sig.name == "bullish_fvg"

    def test_bearish_fvg_detected(self, fvg_bearish_df, strategy_params):
        sig = PatternDetectors.detect_fvg(
            fvg_bearish_df, strategy_params, sweep_direction="bearish", bar_index=2
        )
        assert sig is not None
        assert sig.name == "bearish_fvg"

    def test_fvg_requires_3_bars(self, strategy_params):
        df2 = pd.DataFrame([
            {"open": 100, "high": 101, "low": 99.5, "close": 100.8, "volume": 100},
            {"open": 102, "high": 103, "low": 101.8, "close": 102.9, "volume": 120},
        ])
        sig = PatternDetectors.detect_fvg(df2, strategy_params, "bullish", bar_index=1)
        assert sig is None

    def test_bullish_fvg_fails_overlap(self, strategy_params):
        df = pd.DataFrame([
            {"open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 100},
            {"open": 102.5, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 120},
            {"open": 102.0, "high": 103.5, "low": 101.5, "close": 102.8, "volume": 90},
        ])
        sig = PatternDetectors.detect_fvg(df, strategy_params, "bullish", bar_index=2)
        assert sig is None

    def test_fvg_fails_mid_bar_overlap(self, strategy_params):
        df = pd.DataFrame([
            {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.8, "volume": 100},
            {"open": 101.5, "high": 104.0, "low": 101.3, "close": 103.5, "volume": 120},
            {"open": 103.5, "high": 104.5, "low": 103.2, "close": 104.0, "volume": 90},
        ])
        sig = PatternDetectors.detect_fvg(df, strategy_params, "bullish", bar_index=2)
        assert sig is None

    def test_fvg_wrong_direction(self, fvg_bullish_df, strategy_params):
        sig = PatternDetectors.detect_fvg(fvg_bullish_df, strategy_params, "bearish", bar_index=2)
        assert sig is None


class TestPinBarRatios:
    """Pin bar: wick_ratio ≥ threshold AND body_ratio ≤ threshold."""

    def _bar(self, o, h, l, c, v=100.0):
        return pd.DataFrame([{"open": o, "high": h, "low": l, "close": c, "volume": v}])

    def test_bullish_pin_bar(self, strategy_params):
        df = self._bar(1.1000, 1.1010, 1.0900, 1.1005)
        sig = PatternDetectors.detect_pin_bar(df, strategy_params, "bullish", 0)
        assert sig is not None and sig.name == "bullish_pin_bar"

    def test_bearish_pin_bar(self, strategy_params):
        df = self._bar(1.1000, 1.1120, 1.0995, 1.1005)
        sig = PatternDetectors.detect_pin_bar(df, strategy_params, "bearish", 0)
        assert sig is not None and sig.name == "bearish_pin_bar"

    def test_fails_low_wick(self, strategy_params):
        df = self._bar(1.1000, 1.1010, 1.0900, 1.0980)
        sig = PatternDetectors.detect_pin_bar(df, strategy_params, "bullish", 0)
        assert sig is None

    def test_fails_large_body(self, strategy_params):
        df = self._bar(1.1000, 1.1020, 1.0900, 1.0990)
        sig = PatternDetectors.detect_pin_bar(df, strategy_params, "bullish", 0)
        assert sig is None

    def test_zero_range(self, strategy_params):
        df = self._bar(1.1000, 1.1000, 1.1000, 1.1000)
        sig = PatternDetectors.detect_pin_bar(df, strategy_params, "bullish", 0)
        assert sig is None

    def test_opposite_direction_ignored(self, strategy_params):
        df = self._bar(1.1000, 1.1010, 1.0900, 1.1005)
        sig = PatternDetectors.detect_pin_bar(df, strategy_params, "bearish", 0)
        assert sig is None

    def test_strict_config_blocks_marginal(self, strict_config):
        df = self._bar(1.1000, 1.1005, 1.0900, 1.1002)
        sig = PatternDetectors.detect_pin_bar(df, strict_config, "bullish", 0)
        assert sig is None


class TestATRSizing:
    """RiskExecutionEngine ATR calculation and position sizing."""

    @pytest.fixture
    def atr_df(self):
        np.random.seed(1)
        n = 24
        data = []
        c = 2000.0
        for _ in range(n):
            h = c + np.random.uniform(1, 5)
            l = c - np.random.uniform(1, 5)
            o = c + np.random.uniform(-2, 2)
            c = o + np.random.uniform(-1, 1)
            data.append({"open": round(o, 2), "high": round(h, 2), "low": round(l, 2), "close": round(c, 2)})
        return pd.DataFrame(data)

    def test_atr_positive(self, atr_df, app_config):
        from src.engine.risk_execution import RiskExecutionEngine
        engine = RiskExecutionEngine(app_config)
        atr = engine.calculate_atr(atr_df, period=14)
        assert atr > 0 and not np.isnan(atr) and not np.isinf(atr)

    def test_position_size(self, app_config):
        from src.engine.risk_execution import RiskExecutionEngine
        engine = RiskExecutionEngine(app_config)
        size = engine.calculate_position_size(10000.0, 1.1000, 1.0950)
        assert size == 40000.0

    def test_position_size_zero_sl(self, app_config):
        from src.engine.risk_execution import RiskExecutionEngine
        engine = RiskExecutionEngine(app_config)
        size = engine.calculate_position_size(10000.0, 1.1000, 1.1000)
        assert size == 0.0

    def test_sl_tp_long(self, atr_df, app_config):
        from src.engine.risk_execution import RiskExecutionEngine
        engine = RiskExecutionEngine(app_config)
        sl, tp = engine.calculate_sl_tp(atr_df, 2000.0, "long")
        assert sl < 2000.0 < tp

    def test_sl_tp_short(self, atr_df, app_config):
        from src.engine.risk_execution import RiskExecutionEngine
        engine = RiskExecutionEngine(app_config)
        sl, tp = engine.calculate_sl_tp(atr_df, 2000.0, "short")
        assert sl > 2000.0 > tp

    def test_execute_trade(self, app_config):
        from src.engine.risk_execution import RiskExecutionEngine
        engine = RiskExecutionEngine(app_config)
        result = engine.execute_trade("XAUUSD", "long", 0.1, 2000.0, 1995.0, 2010.0)
        assert result["status"] == "executed"
        assert result["symbol"] == "XAUUSD"