"""Unit tests: SessionRange reset/update and SweepSignal expiry logic."""

import pytest
from datetime import datetime, timezone
from src.engine.session_liquidity import (
    SessionLiquidityTracker,
    SessionRange,
    SweepSignal,
    SweepDirection,
)


def _dt(hour, minute=0, day=15):
    return datetime(2024, 7, day, hour, minute, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════
#  SessionRange
# ═══════════════════════════════════════════════════════════════

class TestSessionRange:
    def test_invalid_when_none(self):
        r = SessionRange("Asia", "2024-07-15")
        assert not r.is_valid()

    def test_valid_when_both_set(self):
        r = SessionRange("Asia", "2024-07-15", session_high=1.105, session_low=1.095)
        assert r.is_valid()

    def test_partial_set_is_invalid(self):
        r = SessionRange("London", "2024-07-15", session_high=1.110)
        assert not r.is_valid()

    def test_swept_flags_default_false(self):
        r = SessionRange("Asia", "2024-07-15", session_high=1.105, session_low=1.095)
        assert r.high_swept is False
        assert r.low_swept is False
        assert r.touched is False


# ═══════════════════════════════════════════════════════════════
#  SessionLiquidityTracker.update()
# ═══════════════════════════════════════════════════════════════

class TestUpdate:
    def test_asia_range_built_from_multiple_bars(self, session_tracker):
        session_tracker.update(_dt(1, 0), 2055.0, 2048.0)
        session_tracker.update(_dt(3, 30), 2058.0, 2045.5)
        session_tracker.update(_dt(6, 0), 2056.0, 2047.0)

        r = session_tracker.asia_range
        assert r.session_high == 2058.0
        assert r.session_low == 2045.5
        assert r.session_name == "Asia"

    def test_london_range_ignores_asia_high(self, session_tracker):
        session_tracker.update(_dt(1, 0), 2055.0, 2048.0)   # Asia
        session_tracker.update(_dt(8, 0), 2062.0, 2050.0)   # London

        lr = session_tracker.london_range
        assert lr.session_high == 2062.0
        assert lr.session_low == 2050.0

    def test_ny_bars_do_not_build_ranges(self, session_tracker):
        session_tracker.update(_dt(14, 0), 2070.0, 2065.0)
        assert session_tracker.asia_range is None
        assert session_tracker.london_range is None

    def test_daily_reset_purges_old_ranges(self, session_tracker):
        session_tracker.update(_dt(2, 0), 2055.0, 2048.0)
        assert session_tracker.asia_range is not None

        # Next day bar triggers reset
        session_tracker.update(datetime(2024, 7, 16, 1, 0, tzinfo=timezone.utc), 2060.0, 2050.0)
        assert session_tracker.asia_range.date == "2024-07-16"
        assert session_tracker.asia_range.session_high == 2060.0

    def test_force_daily_reset(self, session_tracker):
        session_tracker.update(_dt(2, 0), 2055.0, 2048.0)
        session_tracker.force_daily_reset("2024-07-20")
        assert session_tracker.asia_range is None
        assert session_tracker.london_range is None
        assert session_tracker.pending_count == 0


# ═══════════════════════════════════════════════════════════════
#  Reference level resolution
# ═══════════════════════════════════════════════════════════════

class TestReferenceLevel:
    def test_london_ref_asia(self, tracker_with_asia):
        ref = tracker_with_asia.get_reference_level("London")
        assert ref.session_name == "Asia"

    def test_ny_ref_london_when_touched(self, session_tracker):
        session_tracker.update(_dt(2, 0), 2055.0, 2048.0)   # Asia
        session_tracker.update(_dt(9, 0), 2060.0, 2052.0)   # London
        session_tracker.mark_reference_touched("NY", 2060.0)
        ref = session_tracker.get_reference_level("NY")
        assert ref.session_name == "London"

    def test_ny_fallback_to_asia_when_london_untouched(self, session_tracker):
        session_tracker.update(_dt(2, 0), 2055.0, 2048.0)   # Asia
        session_tracker.update(_dt(9, 0), 2060.0, 2052.0)   # London
        # No touch → fallback
        ref = session_tracker.get_reference_level("NY")
        assert ref.session_name == "Asia"


# ═══════════════════════════════════════════════════════════════
#  SweepSignal expiry & staleness
# ═══════════════════════════════════════════════════════════════

class TestExpiry:
    def test_pending_built_on_breach_no_close(self, tracker_with_asia):
        # Breach Asia low without close rejection → pending
        sig = tracker_with_asia.detect_sweep(
            _dt(8, 0), bar_low=2046.0, bar_high=2051.0, bar_close=2047.0,
            current_session="London", bar_index_5m=10,
        )
        assert sig.direction is None
        assert tracker_with_asia.pending_count == 1

    def test_expire_after_stale_limit(self, session_tracker):
        session_tracker.update(_dt(2, 0), 2055.0, 2048.0)
        session_tracker.detect_sweep(
            _dt(8, 0), bar_low=2046.0, bar_high=2051.0, bar_close=2047.0,
            current_session="London", bar_index_5m=10,
        )
        # stale_bar_limit=3, bar 14 → elapsed=4 > 3
        expired = session_tracker.expire_if_stale(14)
        assert len(expired) == 1
        assert expired[0].confirmed is False
        assert expired[0].direction == +1
        assert session_tracker.pending_count == 0

    def test_not_expired_before_limit(self, session_tracker):
        session_tracker.update(_dt(2, 0), 2055.0, 2048.0)
        session_tracker.detect_sweep(
            _dt(8, 0), bar_low=2046.0, bar_high=2051.0, bar_close=2047.0,
            current_session="London", bar_index_5m=10,
        )
        expired = session_tracker.expire_if_stale(12)  # elapsed=2 ≤ 3
        assert len(expired) == 0
        assert session_tracker.pending_count == 1

    def test_expire_idempotent(self, session_tracker):
        session_tracker.update(_dt(2, 0), 2055.0, 2048.0)
        session_tracker.detect_sweep(
            _dt(8, 0), bar_low=2046.0, bar_high=2051.0, bar_close=2047.0,
            current_session="London", bar_index_5m=10,
        )
        e1 = session_tracker.expire_if_stale(14)
        e2 = session_tracker.expire_if_stale(14)
        assert len(e1) == 1
        assert len(e2) == 0