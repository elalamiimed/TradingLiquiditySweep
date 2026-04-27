"""
Candlestick Reversal Confirmation Engine
========================================
Pure candlestick-based pattern detection — no MSS, no FVG.

Public API:
  PatternConfig         – dataclass of tunable thresholds
  PatternDetectors      – static/class methods for each pattern
  ConfirmationValidator – aggregates signals (single signal sufficient)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PatternConfig:
    """
    All pattern‑detection & confirmation thresholds.
    Set values here to make the engine fully config‑driven.
    """
    # ── Pin Bar ──────────────────────────────────────────
    pin_bar_wick_ratio: float = 0.50      # lower wick / total range ≥ this (bullish)
    pin_bar_body_ratio: float = 0.35      # body / total range ≤ this

    # ── Engulfing ────────────────────────────────────────
    engulfing_body_cover: bool = True     # engulfing body must fully cover prev body

    # ── MSS (Market Structure Shift) ─────────────────────
    structure_lookback: int = 3           # bars for swing detection (zigzag depth)
    mss_break_confirm: bool = True        # requirement of close beyond swing low/high

    # ── FVG (Fair Value Gap) ─────────────────────────────
    fvg_tolerance: float = 0.001          # ±0.1% tolerance for strict gap check
    fvg_strict_mid_check: bool = True     # apply mid‑bar non‑overlap rules

    # ── DT/DB (Double Top / Double Bottom) ───────────────
    dt_db_match_tolerance: float = 0.005  # ±0.5% match of swing levels
    dt_db_volume_sma_period: int = 20
    dt_db_volume_multiplier: float = 1.0  # volume > SMA20 * multiplier (≥1.0)

    # ── Volume Filter (global) ───────────────────────────
    volume_filter_enabled: bool = False
    volume_sma_period: int = 20
    volume_min_multiplier: float = 1.1

    # ── Confirmation Aggregator ─────────────────────────
    min_confirmation_signals: int = 2     # how many aligned patterns required

    # ── Fallback ────────────────────────────────────────
    fallback_atr_multiplier: float = 1.5  # displacement from sweep level
    fallback_enabled: bool = True


# ═══════════════════════════════════════════════════════════════════
#  SIGNAL RESULT DATA CLASS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Signal:
    """
    A single detected pattern aligned with the sweep direction.
    """
    name: str               # e.g. "bullish_pin_bar", "bearish_fvg"
    category: str           # "candlestick", "structure", "continuation" etc.
    bar_index: int          # reference bar index in the dataframe
    confidence: float = 1.0 # optional weighting (0.0–1.0)
    meta: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
#  PATTERN DETECTORS (static/class methods)
# ═══════════════════════════════════════════════════════════════════

class PatternDetectors:
    """
    Collection of static pattern detection functions.
    Each returns an Optional[Signal] aligned with the requested sweep direction,
    or None if no pattern detected.
    All thresholds are taken from the supplied PatternConfig instance.
    """

    # ─────────────────────────────────────────────────────────────────
    # Pin Bar
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def detect_pin_bar(
        df: pd.DataFrame,
        config: PatternConfig,
        sweep_direction: str,        # "bullish" or "bearish"
        bar_index: int,
        volume_series: Optional[pd.Series] = None,
    ) -> Optional[Signal]:
        """
        Pin Bar: wick ≥ config.pin_bar_wick_ratio of total range,
                 body ≤ config.pin_bar_body_ratio of total range.
        Bullish pin bar (hammer) expects a lower wick dominant;
        bearish pin bar (shooting star) expects an upper wick dominant.
        Aligns with sweep_direction.
        """
        if len(df) < 1:
            return None
        candle = df.iloc[bar_index]
        open_, high, low, close, vol = candle['open'], candle['high'], candle['low'], candle['close'], candle.get('volume', None)

        total_range = high - low
        if total_range <= 0:
            return None

        body = abs(close - open_)
        body_ratio = body / total_range

        # Determine wick direction
        if sweep_direction == "bullish":
            lower_wick = close - low if close > open_ else open_ - low
            wick_ratio = lower_wick / total_range
            if wick_ratio >= config.pin_bar_wick_ratio and body_ratio <= config.pin_bar_body_ratio:
                pass  # qualifies
            else:
                return None
        elif sweep_direction == "bearish":
            upper_wick = high - close if close > open_ else high - open_
            wick_ratio = upper_wick / total_range
            if wick_ratio >= config.pin_bar_wick_ratio and body_ratio <= config.pin_bar_body_ratio:
                pass
            else:
                return None
        else:
            return None

        # Volume filter (if enabled)
        if config.volume_filter_enabled and volume_series is not None:
            if not _volume_ok(vol, volume_series, bar_index, config):
                return None

        return Signal(
            name=f"{sweep_direction}_pin_bar",
            category="candlestick",
            bar_index=bar_index,
        )

    # ─────────────────────────────────────────────────────────────────
    # Engulfing
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def detect_engulfing(
        df: pd.DataFrame,
        config: PatternConfig,
        sweep_direction: str,
        bar_index: int,
        volume_series: Optional[pd.Series] = None,
    ) -> Optional[Signal]:
        """Bullish engulfing (green body engulfs red) / Bearish engulfing (red engulfs green)."""
        if bar_index < 1:
            return None
        prev = df.iloc[bar_index - 1]
        curr = df.iloc[bar_index]
        vol = curr.get('volume', None)

        if sweep_direction == "bullish":
            # Previous: red body
            if prev['close'] < prev['open'] and curr['close'] > curr['open']:
                if curr['close'] > prev['open'] and curr['open'] < prev['close']:
                    pass
                else:
                    return None
            else:
                return None
        elif sweep_direction == "bearish":
            if prev['close'] > prev['open'] and curr['close'] < curr['open']:
                if curr['close'] < prev['open'] and curr['open'] > prev['close']:
                    pass
                else:
                    return None
            else:
                return None
        else:
            return None

        if config.volume_filter_enabled and volume_series is not None:
            if not _volume_ok(vol, volume_series, bar_index, config):
                return None

        return Signal(
            name=f"{sweep_direction}_engulfing",
            category="candlestick",
            bar_index=bar_index,
        )

    # ─────────────────────────────────────────────────────────────────
    # MSS (Market Structure Shift) – swing failure break
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def detect_mss(
        df: pd.DataFrame,
        config: PatternConfig,
        sweep_direction: str,
        bar_index: int,
        volume_series: Optional[pd.Series] = None,
    ) -> Optional[Signal]:
        """
        Detect a Market Structure Shift using a simple swing detection.
        Bullish MSS: price forms a lower low, then breaks above a previous
                     lower high (structure turns bullish).
        Bearish MSS: price forms a higher high, then breaks below a previous
                     higher low.
        The lookback (config.structure_lookback) controls how many bars to
        consider for pivot detection.
        """
        lookback = max(2, config.structure_lookback)
        if bar_index < lookback * 2 + 1:
            return None

        # Simple rolling pivot detection using local minima/maxima
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        vol = df['volume'].values if 'volume' in df.columns else None

        if sweep_direction == "bullish":
            # Look for recent lower low then break of recent lower high
            # Find the lowest low in the recent window (excluding last bar)
            recent_low_idx = np.argmin(lows[bar_index - lookback:bar_index]) + (bar_index - lookback)
            # That must be a lower low (swing low) – compare to previous lows
            # Then the price must close above the most recent lower high (swing high)
            # We'll find the most recent swing high between that low and current bar
            swing_high_idx = recent_low_idx + np.argmax(highs[recent_low_idx + 1:bar_index + 1]) + 1
            if swing_high_idx < bar_index and closes[bar_index] > highs[swing_high_idx]:
                # additional check: that swing high was a lower high (downtrend)
                prev_high_idx = recent_low_idx - 1 - np.argmax(highs[:recent_low_idx][::-1]) if recent_low_idx > 0 else 0
                if highs[swing_high_idx] < highs[prev_high_idx]:
                    if config.volume_filter_enabled and vol is not None:
                        if not _volume_ok(vol[bar_index], pd.Series(vol), bar_index, config, period=config.volume_sma_period):
                            return None
                    return Signal(
                        name="bullish_mss",
                        category="structure",
                        bar_index=bar_index,
                    )
        elif sweep_direction == "bearish":
            recent_high_idx = np.argmax(highs[bar_index - lookback:bar_index]) + (bar_index - lookback)
            swing_low_idx = recent_high_idx + np.argmin(lows[recent_high_idx + 1:bar_index + 1]) + 1
            if swing_low_idx < bar_index and closes[bar_index] < lows[swing_low_idx]:
                prev_low_idx = recent_high_idx - 1 - np.argmin(lows[:recent_high_idx][::-1]) if recent_high_idx > 0 else 0
                if lows[swing_low_idx] > lows[prev_low_idx]:  # higher low
                    if config.volume_filter_enabled and vol is not None:
                        if not _volume_ok(vol[bar_index], pd.Series(vol), bar_index, config, period=config.volume_sma_period):
                            return None
                    return Signal(
                        name="bearish_mss",
                        category="structure",
                        bar_index=bar_index,
                    )
        return None

    # ─────────────────────────────────────────────────────────────────
    # FVG (Fair Value Gap) – strict 3‑bar non‑overlap gap ±0.1% tolerance
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def detect_fvg(
        df: pd.DataFrame,
        config: PatternConfig,
        sweep_direction: str,
        bar_index: int,
        volume_series: Optional[pd.Series] = None,
    ) -> Optional[Signal]:
        """
        Strict FVG per Quant.pdf:
          Bullish: low[0] > high[-2]  AND low[-1] < high[-2]
                   with 0.1% tolerance on gap, and middle bar’s high
                   must not overlap the gap (optional)
          Bearish: high[0] < low[-2] AND high[-1] > low[-2]
        The volume filter is checked on the third bar (bar_index).
        """
        if bar_index < 2:
            return None
        idx0 = bar_index
        idx1 = bar_index - 1
        idx2 = bar_index - 2

        low0, high0 = df.iloc[idx0]['low'], df.iloc[idx0]['high']
        low1, high1 = df.iloc[idx1]['low'], df.iloc[idx1]['high']
        low2, high2 = df.iloc[idx2]['low'], df.iloc[idx2]['high']

        vol = df.iloc[idx0].get('volume', None)
        tol = config.fvg_tolerance

        if sweep_direction == "bullish":
            # Strict condition: low[0] > high[-2]   (gap size > -tol)
            gap_size = low0 - high2
            if gap_size <= -tol * abs(high2):
                return None
            # Second condition: low[-1] < high[-2]
            if not (low1 < high2 + tol * abs(high2)):
                return None
            # Optional: mid‑bar non‑overlap (high[1] < low[0])
            if config.fvg_strict_mid_check and high1 > low0 + tol * abs(low0):
                return None
        elif sweep_direction == "bearish":
            gap_size = low2 - high0   # high0 must be below low2
            if gap_size <= -tol * abs(low2):
                return None
            if not (high1 > low2 - tol * abs(low2)):
                return None
            if config.fvg_strict_mid_check and low1 < high0 - tol * abs(high0):
                return None
        else:
            return None

        if config.volume_filter_enabled and volume_series is not None:
            if not _volume_ok(vol, volume_series, bar_index, config):
                return None

        return Signal(
            name=f"{sweep_direction}_fvg",
            category="continuation",
            bar_index=bar_index,
        )

    # ─────────────────────────────────────────────────────────────────
    # Double Top / Double Bottom
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def detect_dt_db(
        df: pd.DataFrame,
        config: PatternConfig,
        sweep_direction: str,
        bar_index: int,
        volume_series: Optional[pd.Series] = None,
    ) -> Optional[Signal]:
        """
        Double Top (bearish) / Double Bottom (bullish) based on swing
        matching within ±config.dt_db_match_tolerance.
        Confirmation requires volume on the second swing > SMA20 of volume.
        """
        lookback = max(3, config.structure_lookback)
        if bar_index < lookback * 2 + 1:
            return None

        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        volumes = df['volume'].values if 'volume' in df.columns else None

        tol = config.dt_db_match_tolerance

        if sweep_direction == "bullish":
            # Double Bottom: two swing lows within tolerance
            # Find recent swing low around bar_index
            recent_swing_low_idx = (bar_index - lookback) + np.argmin(lows[bar_index - lookback:bar_index + 1])
            # Find prior swing low earlier
            search_end = recent_swing_low_idx - 2
            search_start = max(0, search_end - lookback)
            if search_start >= search_end:
                return None
            prior_swing_low_idx = search_start + np.argmin(lows[search_start:search_end])
            level1 = lows[prior_swing_low_idx]
            level2 = lows[recent_swing_low_idx]
            if abs(level2 - level1) <= tol * abs(level1):
                # Volume check: volume at second low must exceed SMA20
                if volumes is not None and config.dt_db_volume_multiplier > 0:
                    sma_period = config.dt_db_volume_sma_period
                    sma = pd.Series(volumes).rolling(sma_period).mean().iloc[recent_swing_low_idx]
                    if volumes[recent_swing_low_idx] <= sma * config.dt_db_volume_multiplier:
                        return None
                # Additionally need some confirmation that price is rising from second low
                if closes[bar_index] > level2:
                    if config.volume_filter_enabled and volumes is not None:
                        if not _volume_ok(volumes[bar_index], pd.Series(volumes), bar_index, config):
                            return None
                    return Signal(
                        name="bullish_double_bottom",
                        category="reversal",
                        bar_index=bar_index,
                    )
        elif sweep_direction == "bearish":
            # Double Top
            recent_swing_high_idx = (bar_index - lookback) + np.argmax(highs[bar_index - lookback:bar_index + 1])
            search_end = recent_swing_high_idx - 2
            search_start = max(0, search_end - lookback)
            if search_start >= search_end:
                return None
            prior_swing_high_idx = search_start + np.argmax(highs[search_start:search_end])
            level1 = highs[prior_swing_high_idx]
            level2 = highs[recent_swing_high_idx]
            if abs(level2 - level1) <= tol * abs(level1):
                if volumes is not None and config.dt_db_volume_multiplier > 0:
                    sma = pd.Series(volumes).rolling(config.dt_db_volume_sma_period).mean().iloc[recent_swing_high_idx]
                    if volumes[recent_swing_high_idx] <= sma * config.dt_db_volume_multiplier:
                        return None
                if closes[bar_index] < level2:
                    if config.volume_filter_enabled and volumes is not None:
                        if not _volume_ok(volumes[bar_index], pd.Series(volumes), bar_index, config):
                            return None
                    return Signal(
                        name="bearish_double_top",
                        category="reversal",
                        bar_index=bar_index,
                    )
        return None


# ═══════════════════════════════════════════════════════════════════
#  CONFIRMATION VALIDATOR (Aggregator)
# ═══════════════════════════════════════════════════════════════════

class ConfirmationValidator:
    """
    Aggregates pattern detectors and validates signal alignment.
    A single valid pattern (DT/DB, pin bar, engulfing) is sufficient
    to confirm a sweep.  ATR displacement fallback is also available.
    """

    def __init__(self, config: PatternConfig) -> None:
        self.config = config

    def evaluate(
        self,
        df: pd.DataFrame,
        sweep_direction: str,
        sweep_level: float,
        atr_value: float,
        volume_series: Optional[pd.Series] = None,
    ) -> tuple[bool, List[Signal]]:
        """
        Primary entry point.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data for the relevant timeframe (5M or 1M).
            The most recent bar is assumed to be at index -1.
        sweep_direction : str
            "bullish" or "bearish" (the direction of the potential trade).
        sweep_level : float
            The session level that was swept (used for fallback).
        atr_value : float
            Current ATR value (computed externally) for fallback.
        volume_series : pd.Series, optional
            Volume column for the same dataframe (if not inside df).

        Returns
        -------
        (is_confirmed, signals) : tuple[bool, List[Signal]]
            is_confirmed is True if ≥1 aligned pattern OR (fallback enabled
            and displacement > atr * multiplier).
        """
        if volume_series is None and 'volume' in df.columns:
            volume_series = df['volume']

        bar_index = len(df) - 1

        detectors = [
            PatternDetectors.detect_pin_bar,
            PatternDetectors.detect_engulfing,
            PatternDetectors.detect_dt_db,
        ]

        confirmed_signals: List[Signal] = []
        for detector in detectors:
            signal = detector(
                df=df,
                config=self.config,
                sweep_direction=sweep_direction,
                bar_index=bar_index,
                volume_series=volume_series,
            )
            if signal is not None:
                confirmed_signals.append(signal)

        logger.info(
            "Confirmation scan for %s sweep: %d patterns found → %s",
            sweep_direction,
            len(confirmed_signals),
            [s.name for s in confirmed_signals],
        )

        # If we have enough signals, confirm
        if len(confirmed_signals) >= self.config.min_confirmation_signals:
            return True, confirmed_signals

        # Fallback: displacement from sweep level
        if self.config.fallback_enabled and sweep_level is not None and atr_value > 0:
            current_price = df.iloc[-1]['close']
            displacement = abs(current_price - sweep_level)
            threshold = self.config.fallback_atr_multiplier * atr_value
            if displacement > threshold:
                logger.info(
                    "No pattern confirmation but displacement %.5f > %.5f fallback threshold → entry allowed",
                    displacement, threshold,
                )
                return True, []   # fallback success, empty signals list

        return False, confirmed_signals  # not enough signals


# ═══════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════

def _volume_ok(
    vol: float,
    volume_series: pd.Series,
    bar_index: int,
    config: PatternConfig,
    period: Optional[int] = None,
) -> bool:
    """
    Check if the bar's volume exceeds sma * multiplier.
    Returns True if volume filter passes (or if volume is NaN).
    """
    if period is None:
        period = config.volume_sma_period
    if vol is None or np.isnan(vol):
        return True  # missing volume → pass

    if len(volume_series) < period:
        return True

    # compute SMA of last `period` values up to bar_index - 1? For real‑time it’s better to use
    # values up to bar_index to include current bar. We use inclusive window.
    start_idx = max(0, bar_index - period + 1)
    sub = volume_series.iloc[start_idx:bar_index + 1]
    if len(sub) < 2:
        return True
    sma = sub.mean()
    return vol > sma * config.volume_min_multiplier