"""
Backward‑compatible wrapper for the full PatternEngine.
This module now delegates to pattern_engine.PatternDetectors and
pattern_engine.ConfirmationValidator for the complete multi‑signal
hierarchy. Old callers (main.py, backtest.py) continue to work unchanged.
"""

import pandas as pd
from typing import Optional

from src.engine.pattern_engine import (
    PatternConfig,
    PatternDetectors,
)
from src.utils.logger import logger

_default_config = PatternConfig()


class PatternStructure:
    """
    Thin compatibility wrapper; real logic lives in PatternDetectors.
    Maintains original static-method API for main.py / backtest.py.
    """

    @staticmethod
    def detect_mss(df: pd.DataFrame) -> Optional[str]:
        result = PatternDetectors.detect_mss(
            df,
            _default_config,
            sweep_direction="bullish",
            bar_index=len(df) - 1,
        )
        if result:
            return "bullish_mss"
        result = PatternDetectors.detect_mss(
            df,
            _default_config,
            sweep_direction="bearish",
            bar_index=len(df) - 1,
        )
        return "bearish_mss" if result else None

    @staticmethod
    def detect_fvg(df: pd.DataFrame) -> Optional[str]:
        result = PatternDetectors.detect_fvg(
            df,
            _default_config,
            sweep_direction="bullish",
            bar_index=len(df) - 1,
        )
        if result:
            return "bullish_fvg"
        result = PatternDetectors.detect_fvg(
            df,
            _default_config,
            sweep_direction="bearish",
            bar_index=len(df) - 1,
        )
        return "bearish_fvg" if result else None

    @staticmethod
    def detect_candlestick_trigger(df: pd.DataFrame) -> Optional[str]:
        idx = len(df) - 1
        for direction in ("bullish", "bearish"):
            pb = PatternDetectors.detect_pin_bar(
                df, _default_config, direction, idx
            )
            if pb:
                return f"{direction}_trigger"
            eng = PatternDetectors.detect_engulfing(
                df, _default_config, direction, idx
            )
            if eng:
                return f"{direction}_trigger"
        return None

