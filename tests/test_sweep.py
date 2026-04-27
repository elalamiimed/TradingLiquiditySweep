"""Unit tests for SessionLiquidityTracker – Quant.pdf sweep rules."""

import pytest
from datetime import datetime, timezone
from src.engine.session_liquidity import (
    SessionLiquidityTracker,
    SessionRange,
    SweepSignal,
    SweepDirection,
)


# ── helpers ─────────────────────────────────────────────────
def _dt(hour: int, minute: int = 0) -> datetime:
    """Return a UTC datetime on a fixed date for deterministic testing."""
    return datetime(2024, 7, 15, hour, minute, tzinfo=timezone.utc)


# ═════════════════════════════════════════════════════════════
#  SessionRange dataclass
# ═════════════════════════════════════════════════════════════

def test_session_range_is_valid():
    r = SessionRange("Asia", "2024-07-15")
    assert not r.is_valid()
    r.session_high = 1.1050
    r.session_low = 1.0950
    assert r.is_valid()


# ═════════════════════════════════════════════════════════════
#  update() – session range building
# ═════════════════════════════════════════════════════════════

def test_update_builds_asia_range():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0940)   # inside Asia window
    tracker.update(_dt(4, 0), 1.1070, 1.0920)
    r = tracker.asia_range
    assert r is not None
    assert r.session_high == 1.1070
    assert r.session_low == 1.0920


def test_update_builds_london_range():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(9, 0), 1.1100, 1.1060)
    tracker.update(_dt(11, 0), 1.1120, 1.1040)
    r = tracker.london_range
    assert r is not None
    assert r.session_high == 1.1120
    assert r.session_low == 1.1040


def test_update_ignores_ny_for_range_building():
    """update() only builds Asia / London ranges; NY bars are ignored."""
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(14, 0), 1.1200, 1.1150)
    assert tracker.asia_range is None
    assert tracker.london_range is None


def test_daily_reset_clears_all():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.10, 1.09)
    assert tracker.asia_range is not None
    # Next day (Asia window) → auto-reset
    tracker.update(datetime(2024, 7, 16, 1, 0, tzinfo=timezone.utc), 1.11, 1.10)
    # Old Asia range should be gone, new one started
    assert tracker.asia_range.date == "2024-07-16"


# ═════════════════════════════════════════════════════════════
#  get_reference_level()
# ═════════════════════════════════════════════════════════════

def test_reference_london_uses_asia():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)      # Asia
    ref = tracker.get_reference_level("London")
    assert ref is not None
    assert ref.session_name == "Asia"


def test_reference_ny_uses_london_when_touched():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)      # Asia
    tracker.update(_dt(9, 0), 1.1100, 1.1000)      # London
    # Simulate price touching London high during NY
    tracker.mark_reference_touched("NY", 1.1100)
    ref = tracker.get_reference_level("NY")
    assert ref is not None
    assert ref.session_name == "London"


def test_reference_ny_falls_back_to_asia_when_london_untouched():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)      # Asia
    tracker.update(_dt(9, 0), 1.1100, 1.1000)      # London
    # London never touched → NY should fall back to Asia
    ref = tracker.get_reference_level("NY")
    assert ref is not None
    assert ref.session_name == "Asia"


# ═════════════════════════════════════════════════════════════
#  detect_sweep() – returns SweepSignal with ±1 direction
# ═════════════════════════════════════════════════════════════

def test_detect_bullish_sweep():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)   # Asia high/low

    # 1M bar at London open: wick below Asia low → close above
    sig = tracker.detect_sweep(
        timestamp=_dt(8, 1),
        bar_low=1.0940,      # below Asia low of 1.0950
        bar_high=1.0975,
        bar_close=1.0965,    # closed back ABOVE 1.0950 → confirmed
        current_session="London",
        bar_index_5m=10,
    )
    assert sig.direction == +1                   # BULLISH sweep
    assert sig.confirmed is True
    assert sig.swept_level == 1.0950
    assert sig.reference_session == "Asia"


def test_detect_bearish_sweep():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)

    sig = tracker.detect_sweep(
        timestamp=_dt(9, 0),
        bar_low=1.1045,
        bar_high=1.1070,     # above Asia high of 1.1050
        bar_close=1.1040,    # closed back BELOW 1.1050 → confirmed
        current_session="London",
        bar_index_5m=20,
    )
    assert sig.direction == -1                   # BEARISH sweep
    assert sig.confirmed is True
    assert sig.swept_level == 1.1050


def test_detect_no_sweep_when_inside_range():
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)

    sig = tracker.detect_sweep(
        timestamp=_dt(9, 0),
        bar_low=1.0970,
        bar_high=1.1020,
        bar_close=1.1000,
        current_session="London",
        bar_index_5m=5,
    )
    assert sig.direction is None
    assert sig.confirmed is False


def test_detect_no_sweep_when_breach_without_close():
    """Wick breaks low but close stays below → NOT a sweep (no rejection)."""
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)

    sig = tracker.detect_sweep(
        timestamp=_dt(9, 0),
        bar_low=1.0930,      # breaks below 1.0950
        bar_high=1.0960,
        bar_close=1.0940,    # closes BELOW → no rejection
        current_session="London",
        bar_index_5m=5,
    )
    assert sig.direction is None
    # But a pending breach should be registered
    assert tracker.pending_count == 1


def test_swept_level_blocked_from_duplicate():
    """Once swept, the same level cannot produce another signal."""
    tracker = SessionLiquidityTracker()
    tracker.update(_dt(2, 0), 1.1050, 1.0950)

    # First sweep
    sig1 = tracker.detect_sweep(
        timestamp=_dt(9, 0),
        bar_low=1.0940, bar_high=1.0975, bar_close=1.0965,
        current_session="London", bar_index_5m=10,
    )
    assert sig1.direction == +1

    # Second attempt on same level
    sig2 = tracker.detect_sweep(
        timestamp=_dt(9, 2),
        bar_low=1.0930, bar_high=1.0970, bar_close=1.0965,
        current_session="London", bar_index_5m=11,
    )
    assert sig2.direction is None  # blocked


# ═════════════════════════════════════════════════════════════
#  expire_if_stale()
# ═════════════════════════════════════════════════════════════

def test_expire_stale_breaches():
    tracker = SessionLiquidityTracker(stale_bar_limit=3)
    tracker.update(_dt(2, 0), 1.1050, 1.0950)

    # Non-confirming breach at bar 10
    tracker.detect_sweep(
        timestamp=_dt(9, 0),
        bar_low=1.0930, bar_high=1.0975, bar_close=1.0940,
        current_session="London", bar_index_5m=10,
    )
    assert tracker.pending_count == 1

    # Expire after bar 14 (elapsed = 4 > 3)
    expired = tracker.expire_if_stale(14)
    assert len(expired) == 1
    assert expired[0].direction == +1
    assert expired[0].confirmed is False   # flagged as unconfirmed
    assert tracker.pending_count == 0


def test_expire_respects_stale_limit():
    tracker = SessionLiquidityTracker(stale_bar_limit=3)

    tracker.update(_dt(2, 0), 1.1050, 1.0950)
    tracker.detect_sweep(
        timestamp=_dt(9, 0),
        bar_low=1.0930, bar_high=1.0975, bar_close=1.0940,
        current_session="London", bar_index_5m=10,
    )

    # Bar 13 → elapsed = 3, NOT > 3 → still pending
    expired = tracker.expire_if_stale(13)
    assert len(expired) == 0
    assert tracker.pending_count == 1


def test_expire_idempotent_on_same_bar():
    tracker = SessionLiquidityTracker(stale_bar_limit=1)

    tracker.update(_dt(2, 0), 1.1050, 1.0950)
    tracker.detect_sweep(
        timestamp=_dt(9, 0),
        bar_low=1.0930, bar_high=1.0975, bar_close=1.0940,
        current_session="London", bar_index_5m=10,
    )

    # First call at bar 12 → expires
    expired1 = tracker.expire_if_stale(12)
    assert len(expired1) == 1

    # Second call at same bar → no-op
    expired2 = tracker.expire_if_stale(12)
    assert len(expired2) == 0
