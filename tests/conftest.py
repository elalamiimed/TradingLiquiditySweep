"""
Shared fixtures for the Quant QA test suite.
All fixtures generate <50 rows of synthetic data; zero network calls.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta


# ═══════════════════════════════════════════════════════════════════
#  SAMPLE 1M OHLCV DATAFRAME (40 rows, ~1 day)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_1m_df() -> pd.DataFrame:
    """40-row 1‑minute OHLCV DataFrame spanning Asia→London→NY."""
    np.random.seed(42)
    n = 40
    base_time = datetime(2024, 7, 15, 1, 0, tzinfo=timezone.utc)
    times = [base_time + timedelta(minutes=i * 30) for i in range(n)]

    # Price builds a gentle uptrend with volatility
    close = 2050.0
    data = []
    for i, ts in enumerate(times):
        wick = np.random.uniform(0.5, 3.0)
        body = np.random.uniform(-1.5, 1.5)
        open_ = close
        close = open_ + body
        high = max(open_, close) + wick * 0.6
        low = min(open_, close) - wick * 0.6
        volume = np.random.uniform(50, 300)
        data.append({
            "timestamp": ts,
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": round(volume, 1),
        })
    return pd.DataFrame(data)


# ═══════════════════════════════════════════════════════════════════
#  5M OHLCV DATAFRAME (30 rows)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_5m_df() -> pd.DataFrame:
    """30-row 5‑minute OHLCV DataFrame suitable for pattern detection."""
    np.random.seed(99)
    n = 30
    base_time = datetime(2024, 7, 15, 7, 0, tzinfo=timezone.utc)
    times = [base_time + timedelta(minutes=i * 5) for i in range(n)]

    close = 2100.0
    data = []
    for i, ts in enumerate(times):
        wick = np.random.uniform(1.0, 4.0)
        body = np.random.uniform(-2.0, 2.5)
        open_ = close
        close = open_ + body
        high = max(open_, close) + wick * 0.8
        low = min(open_, close) - wick * 0.8
        volume = np.random.uniform(80, 500)
        data.append({
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": round(volume, 1),
        })
    return pd.DataFrame(data)


# ═══════════════════════════════════════════════════════════════════
#  SESSION LIQUIDITY TRACKER (fresh instance)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def session_tracker():
    """Clean SessionLiquidityTracker with default stale_bar_limit=3."""
    from src.engine.session_liquidity import SessionLiquidityTracker
    return SessionLiquidityTracker(stale_bar_limit=3)


# ═══════════════════════════════════════════════════════════════════
#  SEEDED SESSION TRACKER (Asia range pre-built)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def tracker_with_asia(session_tracker):
    """Tracker pre-loaded with an Asia session range (high=2055, low=2048)."""
    session_tracker.update(
        datetime(2024, 7, 15, 2, 0, tzinfo=timezone.utc), 2055.0, 2048.0
    )
    session_tracker.update(
        datetime(2024, 7, 15, 5, 0, tzinfo=timezone.utc), 2056.5, 2047.2
    )
    return session_tracker


# ═══════════════════════════════════════════════════════════════════
#  PATTERN CONFIGURATION (default, overridable)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def strategy_params():
    """Default PatternConfig used by the strategy."""
    from src.engine.pattern_engine import PatternConfig
    return PatternConfig()


@pytest.fixture
def strict_config():
    """PatternConfig with stricter thresholds for edge-case testing."""
    from src.engine.pattern_engine import PatternConfig
    return PatternConfig(
        pin_bar_wick_ratio=0.65,
        pin_bar_body_ratio=0.25,
        fvg_tolerance=0.0005,
        fvg_strict_mid_check=True,
        min_confirmation_signals=3,
        fallback_atr_multiplier=2.0,
    )


# ═══════════════════════════════════════════════════════════════════
#  APP CONFIG (in-memory, no YAML file dependency)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def app_config():
    """An in-memory AppConfig suitable for RiskExecutionEngine tests."""
    from src.utils.config import (
        AppConfig, TradingConfig, RiskConfig, SessionsConfig, Session, ApiConfig,
    )
    return AppConfig(
        trading=TradingConfig(symbols=["XAUUSD"], timeframes=["1m", "5m", "1h", "4h"]),
        risk=RiskConfig(risk_per_trade_pct=0.02, sl_atr_multiplier=1.5, tp_rr_ratio=2.0),
        sessions=SessionsConfig(
            asia=Session(start="00:00", end="07:00"),
            london=Session(start="07:00", end="12:00"),
            ny=Session(start="12:00", end="21:00"),
        ),
        api=ApiConfig(mt5_login=0, mt5_password="", mt5_server="", news_api_url="http://localhost:9999"),
    )


# ═══════════════════════════════════════════════════════════════════
#  BACKTEST OHLCV (7-day subset for smoke test, 24 rows)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def backtest_7day_df() -> pd.DataFrame:
    """24 rows of 1M OHLCV covering 7 days for backtest smoke."""
    np.random.seed(7)
    n = 24
    base_time = datetime(2024, 7, 10, 0, 0, tzinfo=timezone.utc)
    times = [base_time + timedelta(hours=i * 7) for i in range(n)]

    price = 2400.0
    data = []
    for i, ts in enumerate(times):
        price += np.random.uniform(-5, 8)
        wick = np.random.uniform(1, 6)
        body = np.random.uniform(-3, 4)
        open_ = price
        close = open_ + body
        high = max(open_, close) + wick
        low = min(open_, close) - wick
        data.append({
            "timestamp": ts,
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": np.random.uniform(100, 400),
        })
    return pd.DataFrame(data)


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def utc_dt():
    """Factory for UTC datetimes on a fixed test date."""
    def _make(hour: int, minute: int = 0, day: int = 15):
        return datetime(2024, 7, day, hour, minute, tzinfo=timezone.utc)
    return _make