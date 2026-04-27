"""
Session Liquidity Tracker & Sweep Engine
=========================================
Production-ready engine implementing Quant.pdf session liquidity rules.

Rules implemented:
  • Asia session:  00:00–07:00 UTC
  • London session: 07:00–12:00 UTC
  • Daily reset at 00:00 UTC (automatic)
  • London references Asia H/L
  • NY references London H/L (fallback to Asia if London untouched)
  • Sweep = 1M bar wick breaks session level + close returns inside range
  • Swept levels are marked immediately to prevent duplicate signals
  • Unconfirmed breaches expire after N 5M bars (configurable, default=3)
  • Thread-safe state isolation – no globals, all state behind a Lock
  • Pure session/sweep engine: no trend, pattern, or execution logic mixed in

Unit-testable public methods:
  update()
  get_reference_level()
  detect_sweep()
  expire_if_stale()

Lifecycle (call from backtest or live engine):
  1M bar  → update()          – expand session ranges
  1M bar  → detect_sweep()    – check for confirmed sweeps (+1/-1)
  1M bar  → mark_reference_touched() – enables NY → Asia fallback
  5M bar  → expire_if_stale() – purge unconfirmed breaches older than N bars
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import IntEnum
from threading import Lock
from typing import List, Optional

import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  SUPPORTING DATACLASSES & ENUMS
# ═══════════════════════════════════════════════════════════════

class SweepDirection(IntEnum):
    """Directional outcome of a liquidity sweep (non-binary per Quant.pdf)."""
    BULLISH = +1   # Swept below session low → closed back above → buy signal
    BEARISH = -1   # Swept above session high → closed back below → sell signal


@dataclass
class SessionRange:
    """
    Daily session high/low with sweep-prevention flags.

    Attributes
    ----------
    session_name : str
        "Asia" or "London"
    date : str
        ISO date YYYY-MM-DD
    session_high : float or None
        Highest traded price during the session window
    session_low : float or None
        Lowest traded price during the session window
    high_swept : bool
        True if session_high has already produced a confirmed bearish sweep
    low_swept : bool
        True if session_low has already produced a confirmed bullish sweep
    touched : bool
        True once any subsequent session's price has reached either extreme;
        drives NY-fallback logic (if London never touched → NY uses Asia)
    """
    session_name: str
    date: str
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    high_swept: bool = False
    low_swept: bool = False
    touched: bool = False

    def is_valid(self) -> bool:
        """A range is usable only when both extremes are known."""
        return self.session_high is not None and self.session_low is not None


@dataclass
class SweepSignal:
    """
    Result of a sweep detection – always inspect ``direction`` first.

    Attributes
    ----------
    direction : int or None
        +1 = bullish sweep (low swept), -1 = bearish sweep (high swept);
        None = no sweep on this bar.
    swept_level : float or None
        The exact reference level that was swept.
    reference_session : str or None
        Which session's level was swept ("Asia" or "London").
    bar_index : int or None
        5M bar index at time of detection (for staleness / backtest alignment).
    confirmed : bool
        True when close returned inside the range (always True for a returned signal;
        False for expired signals emitted by expire_if_stale).
    timestamp : datetime or None
        UTC timestamp of the confirming bar.
    """
    direction: Optional[int] = None
    swept_level: Optional[float] = None
    reference_session: Optional[str] = None
    bar_index: Optional[int] = None
    confirmed: bool = False
    timestamp: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════
#  INTERNAL PENDING-BREACH TRACKER
# ═══════════════════════════════════════════════════════════════

@dataclass
class _PendingBreach:
    """A level that has been breached by a wick but not yet confirmed by a close."""
    level: float
    direction: int                     # +1 or -1 SweepDirection
    reference_session: str
    first_breach_bar: int              # 5M bar index of first breach
    timestamp: datetime


# ═══════════════════════════════════════════════════════════════
#  MAIN ENGINE
# ═══════════════════════════════════════════════════════════════

class SessionLiquidityTracker:
    """
    Track session highs/lows and detect 1M liquidity sweeps per Quant.pdf.

    Parameters
    ----------
    stale_bar_limit : int
        Number of 5M bars after which an unconfirmed breach is invalidated.
        Default 3 (= 15 minutes).
    """

    # ── Session boundary constants (UTC) ─────────────────────
    ASIA_START   = time(0, 0)
    ASIA_END     = time(7, 0)
    LONDON_START = time(7, 0)
    LONDON_END   = time(12, 0)

    # ────────────────────────────────────────────────────────
    def __init__(self, stale_bar_limit: int = 3, sweep_cooldown_bars: int = 5) -> None:
        self._stale_bar_limit = max(1, stale_bar_limit)
        self._sweep_cooldown_bars = max(1, sweep_cooldown_bars)

        # ── per-day state ──
        self._asia_range:   Optional[SessionRange] = None
        self._london_range: Optional[SessionRange] = None
        self._current_date: Optional[str] = None

        # ── pending-breach tracking ──
        self._pending: List[_PendingBreach] = []
        self._last_5m_bar: Optional[int] = None

        # ── sweep cooldown tracking ──
        self._sweep_cooldowns = []  # (level, direction, unlock_bar)

        # ── thread safety ──
        self._lock = Lock()

    # ═════════════════════════════════════════════════════════
    #  PUBLIC API  (unit-testable)
    # ═════════════════════════════════════════════════════════

    # ── update() ────────────────────────────────────────────
    def update(self,
               timestamp: datetime,
               price_high: float,
               price_low: float) -> None:
        """
        Expand the Asia or London session range with incoming bar data.

        Call this on **every** bar (1M or 5M).  The method will silently
        ignore bars that fall outside Asia / London windows or that belong
        to a non-tracked session (e.g. late NY).  Daily reset is handled
        automatically when ``timestamp`` crosses midnight UTC.

        Parameters
        ----------
        timestamp : datetime
            UTC datetime of the bar.
        price_high : float
            Highest price of the bar.
        price_low : float
            Lowest price of the bar.
        """
        with self._lock:
            self._check_daily_reset(timestamp)
            session = self._classify_session(timestamp)

            if session == "Asia":
                self._expand("asia", price_high, price_low)
            elif session == "London":
                self._expand("london", price_high, price_low)

    # ── get_reference_level() ───────────────────────────────
    def get_reference_level(self, current_session: str) -> Optional[SessionRange]:
        """
        Return the session range that acts as reference liquidity for
        *current_session*, following Quant.pdf hierarchical rules.

        +--------------+-----------------------------------------+
        | Session      | Reference                               |
        +==============+=========================================+
        | London       | Asia high/low                           |
        +--------------+-----------------------------------------+
        | NY / overlap | London high/low                         |
        |              | → fallback to Asia if London untouched  |
        +--------------+-----------------------------------------+

        Parameters
        ----------
        current_session : str
            One of "Asia", "London", "NY", "London-NY Overlap".

        Returns
        -------
        SessionRange or None
            The reference range, or None if not available yet.
        """
        with self._lock:
            return self._resolve_reference(current_session)

    # ── detect_sweep() ──────────────────────────────────────
    def detect_sweep(self,
                     timestamp: datetime,
                     bar_low: float,
                     bar_high: float,
                     bar_close: float,
                     current_session: str,
                     bar_index_5m: int) -> SweepSignal:
        """
        Detect a 1M liquidity sweep with multi-bar confirmation.

        A sweep is **confirmed** when:
          1. A wick (low or high) breaks through a reference session level.
          2. On any subsequent bar, the close returns back inside the level.

        This means the breach (step 1) and the close-back (step 2) can
        happen on different 1M bars — reflecting real market behavior
        where stop runs often take several candles to fully reject.

        Swept levels are immediately marked and will not trigger again.
        Breaches that fail to confirm within ``stale_bar_limit`` 5M bars
        are silently expired by :meth:`expire_if_stale`.

        Parameters
        ----------
        timestamp : datetime
            UTC datetime of the 1M bar.
        bar_low : float
            Low of the bar.
        bar_high : float
            High of the bar.
        bar_close : float
            Close of the bar.
        current_session : str
            Active session string.
        bar_index_5m : int
            Current 5M bar index (for staleness bookkeeping).

        Returns
        -------
        SweepSignal
            direction = +1 for bullish sweep, -1 for bearish sweep,
            or None if no sweep was confirmed on this bar.
        """
        with self._lock:
            ref = self._resolve_reference(current_session)
            if ref is None or not ref.is_valid():
                return SweepSignal()

            # ── Refresh sweep cooldowns: unlock levels whose cooldown has expired ──
            new_cooldowns = []
            for cd_level, cd_dir, cd_unlock_bar in self._sweep_cooldowns:
                if bar_index_5m >= cd_unlock_bar:
                    self._unlock_level_by_dir(ref, cd_dir, cd_level)
                else:
                    new_cooldowns.append((cd_level, cd_dir, cd_unlock_bar))
            self._sweep_cooldowns = new_cooldowns

            # --- Phase 2: Check pending breaches for close-back confirmation ---
            # A pending bullish breach: wick previously broke below session_low.
            # Confirmation: close has now returned ABOVE that level.
            for pb in list(self._pending):
                if pb.direction == int(SweepDirection.BULLISH):
                    if not ref.low_swept and bar_close > pb.level:
                        ref.low_swept = True
                        self._remove_pending(pb.level, pb.direction)
                        self._sweep_cooldowns.append(
                            (pb.level, int(SweepDirection.BULLISH), bar_index_5m + self._sweep_cooldown_bars))
                        logger.info(
                            "Bullish sweep confirmed (multi-bar) | %s low %.5f | close %.5f | breach_bar=%d confirm_bar=%d",
                            ref.session_name, pb.level, bar_close, pb.first_breach_bar, bar_index_5m)
                        return SweepSignal(
                            direction=int(SweepDirection.BULLISH),
                            swept_level=pb.level,
                            reference_session=ref.session_name,
                            bar_index=bar_index_5m,
                            confirmed=True,
                            timestamp=timestamp)

                if pb.direction == int(SweepDirection.BEARISH):
                    if not ref.high_swept and bar_close < pb.level:
                        ref.high_swept = True
                        self._remove_pending(pb.level, pb.direction)
                        self._sweep_cooldowns.append(
                            (pb.level, int(SweepDirection.BEARISH), bar_index_5m + self._sweep_cooldown_bars))
                        logger.info(
                            "Bearish sweep confirmed (multi-bar) | %s high %.5f | close %.5f | breach_bar=%d confirm_bar=%d",
                            ref.session_name, pb.level, bar_close, pb.first_breach_bar, bar_index_5m)
                        return SweepSignal(
                            direction=int(SweepDirection.BEARISH),
                            swept_level=pb.level,
                            reference_session=ref.session_name,
                            bar_index=bar_index_5m,
                            confirmed=True,
                            timestamp=timestamp)

            # --- Phase 1: Register new wick breaches as pending ---
            if not ref.low_swept and bar_low < ref.session_low:
                self._add_pending(
                    level=ref.session_low,
                    direction=int(SweepDirection.BULLISH),
                    ref_session=ref.session_name,
                    bar_index=bar_index_5m,
                    timestamp=timestamp)
            if not ref.high_swept and bar_high > ref.session_high:
                self._add_pending(
                    level=ref.session_high,
                    direction=int(SweepDirection.BEARISH),
                    ref_session=ref.session_name,
                    bar_index=bar_index_5m,
                    timestamp=timestamp)

            return SweepSignal()

    # ── expire_if_stale() ────────────────────────────────────
    def expire_if_stale(self, current_bar_5m: int) -> List[SweepSignal]:
        """
        Invalidate unconfirmed sweep breaches that have not received a
        confirming close within ``stale_bar_limit`` 5M bars.

        Call this **once per 5M bar**.  Repeated calls with the same
        ``current_bar_5m`` are no-ops.

        Parameters
        ----------
        current_bar_5m : int
            The current 5M bar index.

        Returns
        -------
        list[SweepSignal]
            Signals for expired breaches (direction set, confirmed=False).
            May be empty.
        """
        with self._lock:
            if self._last_5m_bar == current_bar_5m:
                return []
            self._last_5m_bar = current_bar_5m

            expired: List[SweepSignal] = []
            still_pending: List[_PendingBreach] = []

            for pb in self._pending:
                bars_elapsed = current_bar_5m - pb.first_breach_bar
                if bars_elapsed > self._stale_bar_limit:
                    expired.append(SweepSignal(
                        direction=pb.direction,
                        swept_level=pb.level,
                        reference_session=pb.reference_session,
                        bar_index=pb.first_breach_bar,
                        confirmed=False,
                        timestamp=pb.timestamp,
                    ))
                    logger.debug(
                        "Sweep breach expired stale | level=%.5f dir=%+d bars=%d",
                        pb.level, pb.direction, bars_elapsed,
                    )
                else:
                    still_pending.append(pb)

            self._pending = still_pending
            return expired

    # ── mark_reference_touched() ─────────────────────────────
    def mark_reference_touched(self,
                               current_session: str,
                               current_price: float) -> None:
        """
        Call on every bar to record that price has reached the reference
        session's level.  This is essential for the NY → Asia fallback:
        if London was **never** touched, NY reverts to Asia levels.

        Parameters
        ----------
        current_session : str
            Active session.
        current_price : float
            Current price (typically bar close or mid-price).
        """
        with self._lock:
            if current_session in ("NY", "London-NY Overlap"):
                # During NY, attempt to mark the London range as touched so
                # _resolve_reference prefers it over the Asia fallback.
                lr = self._london_range
                if lr is not None and not lr.touched and lr.is_valid():
                    if current_price <= lr.session_low or current_price >= lr.session_high:
                        lr.touched = True
                        logger.debug("London range touched by NY price %.5f", current_price)
            elif current_session == "London":
                ar = self._asia_range
                if ar is not None and not ar.touched and ar.is_valid():
                    if current_price <= ar.session_low or current_price >= ar.session_high:
                        ar.touched = True
                        logger.debug("Asia range touched by London price %.5f", current_price)

    # ── force_daily_reset() ──────────────────────────────────
    def force_daily_reset(self, date_str: str) -> None:
        """
        Force a daily reset to a specific date.  Useful for testing or
        backtest initialisation.  Not normally needed in production
        (automatic reset handles this).

        Parameters
        ----------
        date_str : str
            ISO date string, e.g. ``"2024-01-15"``.
        """
        with self._lock:
            self._asia_range = None
            self._london_range = None
            self._current_date = date_str
            self._pending.clear()
            self._sweep_cooldowns.clear()
            self._last_5m_bar = None
            logger.info("Forced daily reset → %s", date_str)

    # ═════════════════════════════════════════════════════════
    #  READ-ONLY PROPERTIES
    # ═════════════════════════════════════════════════════════

    @property
    def asia_range(self) -> Optional[SessionRange]:
        """Snapshot of the current Asia session range (thread-safe copy)."""
        with self._lock:
            return self._asia_range

    @property
    def london_range(self) -> Optional[SessionRange]:
        """Snapshot of the current London session range (thread-safe copy)."""
        with self._lock:
            return self._london_range

    @property
    def pending_count(self) -> int:
        """Number of unconfirmed pending breaches (diagnostics)."""
        with self._lock:
            return len(self._pending)

    # ═════════════════════════════════════════════════════════
    #  INTERNAL / PRIVATE
    # ═════════════════════════════════════════════════════════

    def _check_daily_reset(self, timestamp: datetime) -> None:
        """Auto-reset when UTC date changes (requires lock held by caller)."""
        date_str = timestamp.strftime("%Y-%m-%d")
        if self._current_date != date_str:
            self._asia_range = None
            self._london_range = None
            self._current_date = date_str
            self._pending.clear()
            self._sweep_cooldowns.clear()
            self._last_5m_bar = None
            logger.info("Daily session reset | new date: %s", date_str)

    @staticmethod
    def _classify_session(timestamp: datetime) -> str:
        """Map a UTC timestamp to a session bucket for range-building."""
        t = timestamp.time()
        if SessionLiquidityTracker.ASIA_START <= t < SessionLiquidityTracker.ASIA_END:
            return "Asia"
        elif SessionLiquidityTracker.LONDON_START <= t < SessionLiquidityTracker.LONDON_END:
            return "London"
        # Everything >= 12:00 UTC is NY for reference purposes.
        # (Off-session hours after NY close will still report "NY" but
        #  get_reference_level handles that correctly.)
        return "NY"

    def _resolve_reference(self, current_session: str) -> Optional[SessionRange]:
        """
        Core rule engine for reference-level selection.
        Requires lock held by caller.
        """
        if current_session == "London":
            return self._asia_range

        if current_session in ("NY", "London-NY Overlap"):
            # Preferred: London if it was touched
            if self._london_range is not None and self._london_range.touched:
                return self._london_range
            # Fallback: Asia
            return self._asia_range

        return None

    def _expand(self, which: str, high: float, low: float) -> None:
        """Expand a session range with new bar extremes (lock held by caller)."""
        if which == "asia":
            if self._asia_range is None:
                self._asia_range = SessionRange(
                    session_name="Asia", date=self._current_date,
                )
            r = self._asia_range
        else:
            if self._london_range is None:
                self._london_range = SessionRange(
                    session_name="London", date=self._current_date,
                )
            r = self._london_range

        if r.session_high is None or high > r.session_high:
            r.session_high = high
        if r.session_low is None or low < r.session_low:
            r.session_low = low

    def _add_pending(self,
                     level: float,
                     direction: int,
                     ref_session: str,
                     bar_index: int,
                     timestamp: datetime) -> None:
        """Add a level breach that hasn't yet confirmed (deduplicated)."""
        for pb in self._pending:
            if pb.level == level and pb.direction == direction:
                return  # already tracked
        self._pending.append(_PendingBreach(
            level=level,
            direction=direction,
            reference_session=ref_session,
            first_breach_bar=bar_index,
            timestamp=timestamp,
        ))

    def _unlock_level_by_dir(self, ref, direction: int, level: float) -> None:
        """Reset the swept flag on a level after cooldown expires."""
        if direction == int(SweepDirection.BULLISH) and ref.low_swept:
            ref.low_swept = False
            logger.debug("Level %.5f (bullish) unlocked for re-sweep after cooldown", level)
        elif direction == int(SweepDirection.BEARISH) and ref.high_swept:
            ref.high_swept = False
            logger.debug("Level %.5f (bearish) unlocked for re-sweep after cooldown", level)

    def _remove_pending(self, level: float, direction: int) -> None:
        """Remove a pending breach once a confirmed sweep closes it out."""
        self._pending = [
            pb for pb in self._pending
            if not (pb.level == level and pb.direction == direction)
        ]