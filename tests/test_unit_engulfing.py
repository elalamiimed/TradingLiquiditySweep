"""Unit tests: Engulfing pattern detection + Volume filter helpers."""

import pytest
import pandas as pd
import numpy as np
from src.engine.pattern_engine import PatternConfig, PatternDetectors


class TestEngulfing:
    """Bullish/Bearish engulfing detection per Quant.pdf spec."""

    @pytest.fixture
    def engulfing_bullish_df(self):
        """Bar 0: red body. Bar 1: green body that engulfs bar 0."""
        df = pd.DataFrame([
            {"open": 105.0, "high": 106.0, "low": 103.5, "close": 103.8, "volume": 100},
            {"open": 103.0, "high": 107.0, "low": 102.5, "close": 106.5, "volume": 200},
        ])
        return df

    @pytest.fixture
    def engulfing_bearish_df(self):
        """Bar 0: green body. Bar 1: red body that engulfs bar 0."""
        df = pd.DataFrame([
            {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.5, "volume": 100},
            {"open": 102.0, "high": 103.0, "low": 97.0, "close": 98.0, "volume": 200},
        ])
        return df

    def test_bullish_engulfing(self, engulfing_bullish_df, strategy_params):
        sig = PatternDetectors.detect_engulfing(
            engulfing_bullish_df, strategy_params,
            sweep_direction="bullish", bar_index=1,
        )
        assert sig is not None
        assert sig.name == "bullish_engulfing"

    def test_bearish_engulfing(self, engulfing_bearish_df, strategy_params):
        sig = PatternDetectors.detect_engulfing(
            engulfing_bearish_df, strategy_params,
            sweep_direction="bearish", bar_index=1,
        )
        assert sig is not None
        assert sig.name == "bearish_engulfing"

    def test_engulfing_not_enough_bars(self, strategy_params):
        df = pd.DataFrame([
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100},
        ])
        sig = PatternDetectors.detect_engulfing(
            df, strategy_params, sweep_direction="bullish", bar_index=0,
        )
        assert sig is None

    def test_engulfing_wrong_prev_body_color(self, strategy_params):
        """Bullish engulfing needs red prev bar; if prev is green, no signal."""
        df = pd.DataFrame([
            {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.5, "volume": 100},
            {"open": 101.0, "high": 105.0, "low": 100.5, "close": 104.5, "volume": 200},
        ])
        sig = PatternDetectors.detect_engulfing(
            df, strategy_params, sweep_direction="bullish", bar_index=1,
        )
        assert sig is None

    def test_engulfing_opposite_direction(self, engulfing_bullish_df, strategy_params):
        """Bullish engulfing on bearish sweep request returns None."""
        sig = PatternDetectors.detect_engulfing(
            engulfing_bullish_df, strategy_params,
            sweep_direction="bearish", bar_index=1,
        )
        assert sig is None


class TestVolumeFilter:
    """Volume filter: vol > SMA * multiplier (when enabled)."""

    def _make_df(self, volumes, close=100.0):
        data = []
        for v in volumes:
            data.append({
                "open": close - 0.1, "high": close + 0.2,
                "low": close - 0.2, "close": close, "volume": v,
            })
        return pd.DataFrame(data)

    def test_volume_enabled_blocks_low_vol(self):
        """With volume filter on, a bar below SMA fails pattern detection."""
        cfg = PatternConfig(
            volume_filter_enabled=True, volume_sma_period=3,
            volume_min_multiplier=1.2,
            pin_bar_wick_ratio=0.30,  # relaxed for test
            pin_bar_body_ratio=0.80,  # relaxed
        )
        # Previous 3 bars have avg vol ~200; current bar has only 50
        df = pd.DataFrame([
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 200},
            {"open": 100.5, "high": 101.5, "low": 99.5, "close": 101.0, "volume": 220},
            {"open": 101.0, "high": 102.0, "low": 100.0, "close": 100.2, "volume": 180},
            # Pin bar shape but low volume:
            {"open": 100.2, "high": 100.8, "low": 97.0, "close": 100.5, "volume": 50},
        ])
        sig = PatternDetectors.detect_pin_bar(
            df, cfg, sweep_direction="bullish", bar_index=3,
        )
        # Should fail because 50 < 1.2 * avg(200,220,180)≈240 → fails vol filter
        assert sig is None

    def test_volume_disabled_passes_any_vol(self):
        """With volume filter off, low volume bar still passes."""
        cfg = PatternConfig(
            volume_filter_enabled=False,
            pin_bar_wick_ratio=0.30,
            pin_bar_body_ratio=0.80,
        )
        df = pd.DataFrame([
            {"open": 100.0, "high": 100.8, "low": 97.0, "close": 100.5, "volume": 5},
        ])
        sig = PatternDetectors.detect_pin_bar(
            df, cfg, sweep_direction="bullish", bar_index=0,
        )
        # Bullish pin bar (lower wick ~3.5, body ~0.5, range ~3.8) → wick ~92%
        assert sig is not None
        assert sig.name == "bullish_pin_bar"

    def test_volume_nan_passes(self):
        """NaN volume is treated as pass (no data → no filter)."""
        cfg = PatternConfig(
            volume_filter_enabled=True, volume_sma_period=3,
            volume_min_multiplier=1.2,
            pin_bar_wick_ratio=0.30,
            pin_bar_body_ratio=0.80,
        )
        df = pd.DataFrame([
            {"open": 100.0, "high": 100.8, "low": 97.0, "close": 100.5},  # no volume column
        ])
        sig = PatternDetectors.detect_pin_bar(
            df, cfg, sweep_direction="bullish", bar_index=0,
        )
        # Should still detect the pin bar
        assert sig is not None

    def test_volume_short_history_passes(self):
        """When not enough bars for SMA, volume check passes."""
        cfg = PatternConfig(
            volume_filter_enabled=True, volume_sma_period=20,  # large period
            volume_min_multiplier=1.5,
            pin_bar_wick_ratio=0.30,
            pin_bar_body_ratio=0.80,
        )
        df = pd.DataFrame([
            {"open": 100.0, "high": 100.8, "low": 97.0, "close": 100.5, "volume": 100},
        ])
        sig = PatternDetectors.detect_pin_bar(
            df, cfg, sweep_direction="bullish", bar_index=0,
        )
        assert sig is not None