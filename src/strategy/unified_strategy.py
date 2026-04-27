#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  QUANT SESSION-SWEEP UNIFIED STRATEGY                                      ║
║  Production-ready Backtrader strategy integrating all modular engines.     ║
╚══════════════════════════════════════════════════════════════════════════════╝

Integrates:
  • SessionLiquidityTracker   (src/engine/session_liquidity.py)
  • ConfirmationValidator     (src/engine/pattern_engine.py)
  • PatternConfig             (src/engine/pattern_engine.py)
  • TradeChecklist            (src/validator/checklist.py)

Lifecycle (Backtrader event-driven):
  prenext()     → warmup data, initialize session ranges, skip entries
  nextstart()   → begin trading if indicators ready
  next()        → sequential checklist: CircuitBreaker → KillZone → News →
                  Cooldown → Sweep → Confirmation → Volume → Execute
  notify_order() → handle fill/cancel/reject; sync order_state; cancel siblings
  notify_trade() → track daily PnL, reset sweep/state, log closed trade

Pre-trade checklist pipeline (must pass in order):
  1. Daily loss circuit breaker check
  2. Kill-zone time window check
  3. Macro / news blackout window check
  4. Cooldown / daily trade limit check
  5. Active liquidity sweep detection (multi-bar 1M)
  6. Pattern confirmation (DT/DB, pin bar, engulfing — single signal sufficient)
  7. Volume filter pass
  8. Risk / position sizing / bracket-order execution (5M ATR-based, 1:1.5 RR)

Analyzers attached in main block: TradeMetrics, ClosedTradeList, DrawDown, Returns.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import backtrader as bt
import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════════════════════════════════
#  Import modular engines (the canonical, unit-tested implementations)
# ═══════════════════════════════════════════════════════════════════════════
from src.engine.session_liquidity import (
    SessionLiquidityTracker,
    SessionRange as TrackerSessionRange,
    SweepSignal as TrackerSweepSignal,
    SweepDirection,
)
from src.engine.pattern_engine import (
    ConfirmationValidator,
    PatternConfig,
    Signal,
)
from src.validator.checklist import TradeChecklist

logger = logging.getLogger("backtest")


# ═══════════════════════════════════════════════════════════════════════════
#  ORDER BOOK STATE DATA CLASS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OrderBookState:
    """Tracks the current bracket order set (parent + stop + target)."""

    parent: Optional[bt.Order] = None
    stop: Optional[bt.Order] = None
    target: Optional[bt.Order] = None
    direction: int = 0
    total_size: int = 0
    entry_estimate: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    exit_px: Optional[float] = None
    exit_reason: Optional[str] = None

    def clear(self) -> None:
        self.parent = None
        self.stop = None
        self.target = None
        self.direction = 0
        self.total_size = 0
        self.entry_estimate = 0.0
        self.stop_price = 0.0
        self.target_price = 0.0
        self.exit_px = None
        self.exit_reason = None

    def has_pending_orders(self) -> bool:
        for o in (self.parent, self.stop, self.target):
            if o is not None and o.alive():
                return True
        return False

    def remaining_target(self) -> Optional[bt.Order]:
        if self.target and self.target.alive():
            return self.target
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  HELPER: BUILD pandas DataFrame FROM BACKTRADER DATA LINE
# ═══════════════════════════════════════════════════════════════════════════

def _bt_data_to_df(data_line, num_bars: int = 40) -> pd.DataFrame:
    """
    Extract the most recent ``num_bars`` from a Backtrader data line
    into a pandas DataFrame with columns [open, high, low, close, volume].

    Bar index -1 is the *second* most recent closed bar; bar index 0 is
    the *current* (active) bar.  For pattern detection we typically want
    closed bars only, so we use indices [-num_bars .. -1].
    """
    n = min(len(data_line), num_bars)
    if n < 2:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    rows = []
    for i in range(n, 0, -1):
        rows.append({
            "open": float(data_line.open[-i]),
            "high": float(data_line.high[-i]),
            "low": float(data_line.low[-i]),
            "close": float(data_line.close[-i]),
            "volume": float(data_line.volume[-i]) if len(data_line.volume) else 0.0,
        })
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════
#  UNIFIED STRATEGY CLASS
# ═══════════════════════════════════════════════════════════════════════════

class QuantSessionSweepStrategy(bt.Strategy):
    """
    XAUUSD session-sweep day-trading strategy (no trend filter).

    Parameters
    ----------
    atr_period : int
        ATR period for stop-loss / take-profit calculations (on 5M).
    atr_multiplier : float
        Multiplier on 5M ATR for stop distance (default 1.5).
    risk_pct : float
        Fraction of equity risked per trade (e.g. 0.02 = 2%).
    rr_ratio : float
        Reward-to-risk ratio (default 1.5).
    sweep_expiry_bars : int
        Number of 5M bars after which an unconfirmed sweep expires.
    structure_lookback : int
        Bars used for swing-point detection (DT/DB).
    enable_killzone_filter : bool
        If True, only trade during London-open and NY-AM kill zones.
    enable_volume_filter : bool
        If True, require bar volume > SMA(volume, 20) before entry.
    max_daily_trades : int
        Hard cap on trades per UTC day.
    min_bars_between_trades : int
        Minimum 1M bars between consecutive entries.
    trade_start : datetime or None
        Earliest allowed entry datetime (for constrained backtests).
    trade_end : datetime or None
        Latest allowed entry datetime.
    daily_loss_limit_pct : float
        Daily loss threshold as fraction (e.g. -0.04 = -4%).
    """

    params = (
        # ── Core strategy parameters ──
        ("atr_period", 14),
        ("atr_multiplier", 1.5),
        ("risk_pct", 0.02),
        ("rr_ratio", 1.5),
        ("sweep_expiry_bars", 12),
        ("structure_lookback", 5),
        # ── Filters ──
        ("enable_killzone_filter", True),
        ("enable_volume_filter", False),
        ("volume_sma_period", 20),
        ("max_daily_trades", 8),
        ("min_bars_between_trades", 5),
        ("enable_trend_filter", True),
        ("ema_period", 200),
        # ── Confirmation ──
        ("enable_pattern_confirm", True),
        ("min_confirmation_signals", 2),
        # ── Risk guards ──
        ("max_consecutive_losses", 3),
        # ── Backtest window ──
        ("trade_start", None),
        ("trade_end", None),
        # ── Risk / execution ──
        ("daily_loss_limit_pct", -0.04),
        ("news_blackout_minutes", 30),
        ("volume_threshold_multiplier", 1.0),
        # ── LONG Entry Refinements ──
        ("enable_h1_momentum_filter", True),
        ("enable_bb_entry_zone", True),
        ("enable_volume_spike_confirm", True),
        ("volume_spike_multiplier", 1.5),
        ("bb_width_lookback", 5),
        ("h1_rsi_period", 14),
        ("h1_rsi_threshold", 50),
    )

    def __init__(self) -> None:
        # ── Data feeds (expects 1M, 5M, 1H, 4H in that order) ──
        self.d1m = self.datas[0]
        self.d5m = self.datas[1]
        self.d1h = self.datas[2]
        self.d4h = self.datas[3]

        # ── Indicators (5M ATR-based only) ──
        self.atr_5m = bt.indicators.ATR(self.d5m, period=self.p.atr_period)
        self.vol_ema_1m = bt.indicators.EMA(self.d1m.volume, period=20)

        # ── Bollinger Bands on 5M for sweep quality confirmation ──
        self.bb_5m = bt.indicators.BollingerBands(self.d5m.close, period=20, devfactor=2.0)

        # ── EMA 13 on 1M for short-term momentum alignment ──
        self.ema_13_1m = bt.indicators.EMA(self.d1m.close, period=13)

        # ── Trend indicators (1H + 4H EMA cross) ──
        self.ema_50_1h = bt.indicators.EMA(self.d1h.close, period=50)
        self.ema_200_1h = bt.indicators.EMA(self.d1h.close, period=200)
        self.ema_50_4h = bt.indicators.EMA(self.d4h.close, period=50)

        # ── H1 Momentum indicators (LONG entry refinements) ──
        self.ema_20_1h = bt.indicators.EMA(self.d1h.close, period=20)
        self.rsi_1h = bt.indicators.RSI(self.d1h.close, period=self.p.h1_rsi_period)
        self.macd_1h = bt.indicators.MACD(self.d1h.close, period_me1=12, period_me2=26, period_signal=9)

        # ── BB Width tracking (stored as rolling list for expansion check) ──
        self._bb_width_history = []

        # ── Modular engines ──
        self.tracker = SessionLiquidityTracker(
            stale_bar_limit=max(1, self.p.sweep_expiry_bars),
        )
        self.confirmation_validator = ConfirmationValidator(
            PatternConfig(
                structure_lookback=self.p.structure_lookback,
                min_confirmation_signals=1,
                volume_filter_enabled=self.p.enable_volume_filter,
                volume_sma_period=self.p.volume_sma_period,
                fallback_enabled=True,
                fallback_atr_multiplier=1.5,
            )
        )

        # ── State ──
        self.active_sweep: Optional[TrackerSweepSignal] = None
        self.order_state = OrderBookState()
        self.rejection_log_hours: Dict[str, datetime] = {}
        self._bar_index_5m: int = 0
        self._last_5m_len: int = 0

        # ── Cooldown / daily state ──
        self.bars_since_last_trade: int = 999
        self.trades_today: int = 0
        self.last_trade_day: Optional[object] = None

        # ── Daily loss circuit breaker ──
        self._daily_start_equity: Optional[float] = None
        self._daily_pnl: float = 0.0
        self._daily_halted: bool = False
        self._last_equity: Optional[float] = None
        self._current_breaker_date: Optional[object] = None

        # ── Consecutive loss streak guard ──
        self._consecutive_losses: int = 0
        self._day_halted_by_streak: bool = False

        # ── Config storage ──
        self.news_events: List[Any] = []

        # ── Detailed trade metadata for CSV export ──
        self.trade_metadata: List[Dict[str, Any]] = []

        # ── Diagnostic counters ──
        self._diag_sweep_bullish = 0
        self._diag_sweep_bearish = 0
        self._diag_blocked_by_position = 0
        self._diag_heavy_vol_bullish_pass = 0
        self._diag_heavy_vol_bullish_fail = 0
        self._diag_heavy_vol_bearish_pass = 0
        self._diag_heavy_vol_bearish_fail = 0

    # ═══════════════════════════════════════════════════════════════════════
    #  LIFECYCLE: start / prenext / nextstart / next / stop
    # ═══════════════════════════════════════════════════════════════════════

    def start(self) -> None:
        self._daily_start_equity = self.broker.getvalue()
        logger.info(
            "Strategy start | Portfolio: %.2f | Daily Loss Limit: %.1f%%",
            self.broker.getvalue(),
            self.p.daily_loss_limit_pct * 100,
        )

    def prenext(self) -> None:
        """Warmup: update session ranges & 5M counter; do NOT trade."""
        self._update_session_and_counter()

    def nextstart(self) -> None:
        self._process_current_bar(allow_entries=True)

    def next(self) -> None:
        self._process_current_bar(allow_entries=True)

    def stop(self) -> None:
        logger.info("Strategy stop | Final Portfolio: %.2f", self.broker.getvalue())
        logger.warning(
            "DIAGNOSTICS: bullish_sweeps=%d bearish_sweeps=%d "
            "blocked_by_position=%d "
            "hv_bullish_pass=%d hv_bullish_fail=%d "
            "hv_bearish_pass=%d hv_bearish_fail=%d",
            self._diag_sweep_bullish, self._diag_sweep_bearish,
            self._diag_blocked_by_position,
            self._diag_heavy_vol_bullish_pass, self._diag_heavy_vol_bullish_fail,
            self._diag_heavy_vol_bearish_pass, self._diag_heavy_vol_bearish_fail,
        )

    # ═══════════════════════════════════════════════════════════════════════
    #  INTERNAL: frame-processing helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _data_datetime_1m(self) -> datetime:
        return bt.num2date(self.d1m.datetime[0]).replace(tzinfo=None)

    def _data_datetime_5m(self) -> Optional[datetime]:
        if len(self.d5m) == 0:
            return None
        return bt.num2date(self.d5m.datetime[0]).replace(tzinfo=None)

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECKS: Guards (backtest window, indicators, kill-zone, cooldown)
    # ═══════════════════════════════════════════════════════════════════════

    def _log(self, message: str) -> None:
        ts = self._data_datetime_1m() if len(self.d1m) else "STARTUP"
        logger.info("[%s] %s", ts, message)

    def _log_rejection_once_per_hour(self, key: str, message: str) -> None:
        """Log a rejection message at most once per UTC hour per key."""
        ts = self._data_datetime_1m()
        hour_bucket = ts.replace(minute=0, second=0, microsecond=0)
        if self.rejection_log_hours.get(key) == hour_bucket:
            return
        self.rejection_log_hours[key] = hour_bucket
        self._log(message)

    # ═══════════════════════════════════════════════════════════════════════
    #  INTERNAL: session / sweep / counter updates
    # ═══════════════════════════════════════════════════════════════════════

    def _update_session_and_counter(self) -> None:
        """Feed 1M bar to the session tracker and update 5M bar index."""
        ts = self._data_datetime_1m()
        high, low = float(self.d1m.high[0]), float(self.d1m.low[0])
        close = float(self.d1m.close[0])

        # Update session ranges
        self.tracker.update(ts, price_high=high, price_low=low)

        # Mark reference touched for fallback logic
        session = self._classify_session(ts)
        self.tracker.mark_reference_touched(session, close)

        # Advance 5M bar index when a new 5M bar closes
        current_5m_len = len(self.d5m)
        if current_5m_len > self._last_5m_len:
            self._bar_index_5m = current_5m_len
            self._last_5m_len = current_5m_len
            # Expire stale sweep breaches on each new 5M bar
            expired = self.tracker.expire_if_stale(self._bar_index_5m)
            for ex in expired:
                self._log(
                    f"Sweep breach expired stale | "
                    f"level={ex.swept_level:.2f} dir={ex.direction:+d} "
                    f"ref={ex.reference_session}"
                )

    @staticmethod
    def _classify_session(timestamp: datetime) -> str:
        """Map a UTC timestamp to a session bucket."""
        t = timestamp.time()
        if time(0, 0) <= t < time(7, 0):
            return "Asia"
        if time(7, 0) <= t < time(12, 0):
            return "London"
        if time(12, 0) <= t < time(16, 0):
            return "London-NY Overlap"
        if time(16, 0) <= t < time(21, 0):
            return "NY"
        return "Off"

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECKS: Guards (backtest window, indicators, kill-zone, cooldown)
    # ═══════════════════════════════════════════════════════════════════════

    def _is_backtest_time_allowed(self, ts: datetime) -> bool:
        if self.p.trade_start and ts < self.p.trade_start:
            return False
        if self.p.trade_end and ts > self.p.trade_end:
            return False
        return True

    def _indicators_ready(self) -> bool:
        return (
            len(self.d5m) >= max(self.p.atr_period, self.p.volume_sma_period) + 2
            and len(self.d1m) >= 5
        )

    def _is_killzone_time(self, ts: datetime) -> bool:
        if not self.p.enable_killzone_filter:
            return True
        h = ts.hour
        return 10 <= h < 16  # London mid-AM to NY (10-16)

    def _check_cooldown_daily(self, ts: datetime) -> bool:
        td = ts.date()
        if self.last_trade_day != td:
            self.trades_today = 0
            self.last_trade_day = td
            self._consecutive_losses = 0
            self._day_halted_by_streak = False
        if self.trades_today >= self.p.max_daily_trades:
            return False
        if self.bars_since_last_trade < self.p.min_bars_between_trades:
            return False
        return True

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK: Trend filter (1H EMA50 vs EMA200)
    # ═══════════════════════════════════════════════════════════════════════

    def _trend_filter_ok(self, sweep_dir: int, ts: datetime) -> bool:
        if not self.p.enable_trend_filter:
            return True
        if len(self.d4h) < 60:
            return True
        try:
            ema50_4h = float(self.ema_50_4h[0])
            close_4h = float(self.d4h.close[0])
        except (IndexError, TypeError):
            return True
        if not (ema50_4h > 0):
            return True
        if close_4h > ema50_4h:
            if sweep_dir == 1:
                return True
            self._log_rejection_once_per_hour(
                "trend", f"Trend filter: 4H uptrend (close={close_4h:.1f} > EMA50={ema50_4h:.1f}), rejected SHORT."
            )
            return False
        else:
            if sweep_dir == -1:
                return True
            self._log_rejection_once_per_hour(
                "trend", f"Trend filter: 4H downtrend (close={close_4h:.1f} < EMA50={ema50_4h:.1f}), rejected LONG."
            )
            return False

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK: Consecutive loss streak guard
    # ═══════════════════════════════════════════════════════════════════════

    def _consecutive_loss_guard_ok(self) -> bool:
        if self._day_halted_by_streak:
            return False
        if self._consecutive_losses >= self.p.max_consecutive_losses:
            self._day_halted_by_streak = True
            self._log(f"Day halted: {self._consecutive_losses} consecutive losses reached max.")
            return False
        return True

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK: BB + EMA13 Sweep Quality Confirmation
    # ═══════════════════════════════════════════════════════════════════════

    def _bb_ema13_confirm(self, sweep_dir: int) -> bool:
        """Confirm sweep quality: price near BB band + EMA13 aligned with direction."""
        if len(self.d5m) < 22 or len(self.d1m) < 14:
            return True
        try:
            bb_top = float(self.bb_5m.lines.top[0])
            bb_mid = float(self.bb_5m.lines.mid[0])
            bb_bot = float(self.bb_5m.lines.bot[0])
            d5m_close = float(self.d5m.close[0])
            ema13 = float(self.ema_13_1m[0])
            d1m_close = float(self.d1m.close[0])
        except (IndexError, TypeError):
            return True

        bb_width = (bb_top - bb_bot) / bb_mid if bb_mid > 0 else 0.02
        if bb_width < 0.003:
            return True  # too flat, skip BB filter

        if sweep_dir == 1:
            bb_score = (d5m_close - bb_bot) / (bb_top - bb_bot) if (bb_top - bb_bot) > 0 else 0.5
            if bb_score > 0.85:
                return False
        else:
            bb_score = (d5m_close - bb_bot) / (bb_top - bb_bot) if (bb_top - bb_bot) > 0 else 0.5
            if bb_score < 0.15:
                return False

        return True

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK: H1/H4 Momentum Filter (LONG only)
    # ═══════════════════════════════════════════════════════════════════════

    def _h1_momentum_filter_ok(self) -> bool:
        if not self.p.enable_h1_momentum_filter:
            return True
        if len(self.d1h) < 30:
            return True
        try:
            macd_line = float(self.macd_1h.lines.macd[0])
            rsi_val = float(self.rsi_1h[0])
            ema20_cur = float(self.ema_20_1h[0])
            ema20_prev = float(self.ema_20_1h[-1])
        except (IndexError, TypeError):
            return True

        if math.isnan(macd_line) or math.isnan(rsi_val) or math.isnan(ema20_cur) or math.isnan(ema20_prev):
            return True

        macd_bullish = macd_line > 0
        rsi_bullish = rsi_val > self.p.h1_rsi_threshold
        ema20_rising = ema20_cur > ema20_prev

        ok = macd_bullish or rsi_bullish or ema20_rising
        if not ok:
            self._log_rejection_once_per_hour(
                "h1mom",
                f"H1 momentum filter failed: MACD={macd_line:.4f} RSI={rsi_val:.1f} "
                f"EMA20_rising={ema20_rising}"
            )
        return ok

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK: Bollinger Band Entry Zone (LONG only)
    # ═══════════════════════════════════════════════════════════════════════

    def _bb_entry_zone_long_ok(self) -> bool:
        if not self.p.enable_bb_entry_zone:
            return True
        if len(self.d5m) < 22:
            return True
        try:
            bb_bot = float(self.bb_5m.lines.bot[0])
            bb_mid = float(self.bb_5m.lines.mid[0])
            bb_top = float(self.bb_5m.lines.top[0])
            d5m_close = float(self.d5m.close[0])
        except (IndexError, TypeError):
            return True

        if math.isnan(bb_bot) or math.isnan(bb_mid) or math.isnan(bb_top) or math.isnan(d5m_close):
            return True
        if bb_mid <= 0:
            return True

        current_width = (bb_top - bb_bot) / bb_mid

        self._bb_width_history.append(current_width)
        max_history = max(self.p.bb_width_lookback, 5)
        if len(self._bb_width_history) > max_history:
            self._bb_width_history = self._bb_width_history[-max_history:]

        if len(self._bb_width_history) < 2:
            return True

        width_expanding = current_width > self._bb_width_history[-2]
        if not width_expanding:
            self._log_rejection_once_per_hour(
                "bbwidth",
                f"BB width not expanding: {current_width:.4f} <= {self._bb_width_history[-2]:.4f}"
            )
            return False

        bb_range = bb_top - bb_bot
        if bb_range <= 0:
            return True
        bb_score = (d5m_close - bb_bot) / bb_range
        in_entry_zone = bb_score <= 0.25
        if not in_entry_zone:
            self._log_rejection_once_per_hour(
                "bbzone",
                f"Price not in lower BB entry zone: score={bb_score:.2f} (need <=0.25)"
            )
        return in_entry_zone

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK: Volume Spike Confirmation (LONG only)
    # ═══════════════════════════════════════════════════════════════════════

    def _volume_spike_confirm_ok(self) -> bool:
        if not self.p.enable_volume_spike_confirm:
            return True
        if len(self.d1m) < 7:
            return True
        try:
            current_vol = float(self.d1m.volume[0])
            prev_vols = [float(self.d1m.volume[-i]) for i in range(2, 7)]
        except (IndexError, TypeError, ValueError):
            return True

        if math.isnan(current_vol) or any(math.isnan(v) for v in prev_vols):
            return True
        if current_vol <= 0:
            return False

        avg_prev = sum(prev_vols) / len(prev_vols) if prev_vols else 0
        if avg_prev <= 0:
            return True

        spike_ok = current_vol >= avg_prev * self.p.volume_spike_multiplier
        if not spike_ok:
            self._log_rejection_once_per_hour(
                "volspike",
                f"Volume spike failed: cur={current_vol:.0f} < {self.p.volume_spike_multiplier}x avg_prev={avg_prev:.0f}"
            )
        return spike_ok

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK 1: Daily Loss Circuit Breaker
    # ═══════════════════════════════════════════════════════════════════════

    def _daily_circuit_breaker_ok(self, ts: datetime) -> bool:
        td = ts.date()
        eq = self.broker.getvalue()

        # Reset at new day
        if self._current_breaker_date != td:
            self._current_breaker_date = td
            self._daily_start_equity = eq
            self._daily_pnl = 0.0
            self._daily_halted = False
            self._last_equity = eq
            logger.info("Daily loss circuit breaker reset for %s | Equity: %.2f", td, eq)

        # Calculate daily PnL
        if self._last_equity is not None:
            self._daily_pnl = eq - (self._daily_start_equity or eq)
        self._last_equity = eq

        # Check limit
        if self._daily_start_equity and self._daily_start_equity > 0:
            pnl_pct = self._daily_pnl / self._daily_start_equity
            if pnl_pct < self.p.daily_loss_limit_pct:
                self._daily_halted = True
                self._log_rejection_once_per_hour(
                    "cb",
                    f"Daily loss limit hit: {pnl_pct*100:.2f}% (limit: {self.p.daily_loss_limit_pct*100:.1f}%). "
                    f"Halted for today.",
                )
                return False

        return not self._daily_halted

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK 2: Macro / News Blackout
    # ═══════════════════════════════════════════════════════════════════════

    def _news_blackout_active(self, ts: datetime) -> bool:
        """Check whether the current bar falls within a news blackout window."""
        if not self.news_events:
            return False
        bo_seconds = self.p.news_blackout_minutes * 60
        for event in self.news_events:
            try:
                et = event.timestamp if hasattr(event, "timestamp") else event.get("timestamp")
                delta = abs((ts - et).total_seconds()) if hasattr(et, "total_seconds") else 1e9
                if delta <= bo_seconds:
                    return True
            except Exception:
                continue
        return False

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK 3: Liquidity Sweep Detection (delegates to tracker)
    # ═══════════════════════════════════════════════════════════════════════

    def _detect_active_sweep(self, ts: datetime) -> Optional[TrackerSweepSignal]:
        """
        Call the modular tracker's detect_sweep() on this 1M bar.
        Returns a confirmed SweepSignal or None.

        Swept levels are auto-marked inside the tracker to prevent duplicates.
        """
        session = self._classify_session(ts)
        ref = self.tracker.get_reference_level(session)
        if ref is None or not ref.is_valid():
            return None

        bar_low = float(self.d1m.low[0])
        bar_high = float(self.d1m.high[0])
        bar_close = float(self.d1m.close[0])

        signal = self.tracker.detect_sweep(
            timestamp=ts,
            bar_low=bar_low,
            bar_high=bar_high,
            bar_close=bar_close,
            current_session=session,
            bar_index_5m=self._bar_index_5m,
        )

        if signal.confirmed and signal.direction is not None:
            if signal.direction == 1:
                self._diag_sweep_bullish += 1
            elif signal.direction == -1:
                self._diag_sweep_bearish += 1
            return signal

        # Also check for an already-active sweep that hasn't been erased yet
        if self.active_sweep is not None:
            # Verify it hasn't been swept already (duplicate guard)
            if self.active_sweep.confirmed:
                # Check if this active sweep has expired via staleness
                bars_elapsed = self._bar_index_5m - (self.active_sweep.bar_index or 0)
                if bars_elapsed > self.p.sweep_expiry_bars:
                    self._log(
                        f"Active sweep expired after {bars_elapsed} 5M bars "
                        f"(limit={self.p.sweep_expiry_bars})."
                    )
                    self.active_sweep = None
                    return None
                return self.active_sweep

        return None

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK 5: Pattern Confirmation (delegates to ConfirmationValidator)
    # ═══════════════════════════════════════════════════════════════════════

    def _confirm_pattern(self, sweep_direction: int, swept_level: float) -> Tuple[bool, List[str]]:
        """
        Run the modular ConfirmationValidator across 5M and 1M data.
        Converts sweep_direction (±1) → "bullish"/"bearish".
        """
        direction_str = "bullish" if sweep_direction == 1 else "bearish"

        # Build DataFrames from Backtrader data (closed bars only)
        df_5m = _bt_data_to_df(self.d5m, num_bars=40)
        df_1m = _bt_data_to_df(self.d1m, num_bars=60)

        if df_5m.empty or len(df_5m) < 3:
            self._log("Confirmation skipped: insufficient 5M data.")
            return False, []

        atr_val = float(self.atr_5m[0]) if len(self.d5m) >= self.p.atr_period and not math.isnan(float(self.atr_5m[0])) else 0.0

        # Try 5M first, then 1M if 5M fails
        is_confirmed, signals = self.confirmation_validator.evaluate(
            df=df_5m,
            sweep_direction=direction_str,
            sweep_level=swept_level,
            atr_value=atr_val,
        )
        if not is_confirmed and len(df_1m) >= 3:
            is_confirmed, signals = self.confirmation_validator.evaluate(
                df=df_1m,
                sweep_direction=direction_str,
                sweep_level=swept_level,
                atr_value=atr_val,
            )

        signal_names = [s.name if isinstance(s, Signal) else str(s) for s in signals]
        logger.info(
            "Confirmation result: confirmed=%s | signals=%s | sweep=%s level=%.2f",
            is_confirmed, signal_names, direction_str, swept_level,
        )

        return is_confirmed, signal_names

    # ═══════════════════════════════════════════════════════════════════════
    #  CHECK 6: Volume Filter
    # ═══════════════════════════════════════════════════════════════════════

    def _volume_filter_passes(self) -> bool:
        if not self.p.enable_volume_filter:
            return True
        if len(self.d5m) < self.p.volume_sma_period:
            return True
        try:
            cv = float(self.d5m.volume[0])
            sv = float(self.vol_sma_5m[0])
        except (IndexError, TypeError, ValueError):
            return True
        if math.isnan(sv) or sv <= 0:
            return True
        if cv == 0.0 and sv == 0.0:
            return True
        return cv > sv

    # ═══════════════════════════════════════════════════════════════════════
    #  POSITION SIZING
    # ═══════════════════════════════════════════════════════════════════════

    # ───────────────────────────────────────────────────────────────────────
    #  CHECK 6: Heavy-Volume Candle Confirmation (Pine Script logic)
    # ───────────────────────────────────────────────────────────────────────

    def _sweep_quality_ok(self, sweep_dir: int, swept_level: float) -> bool:
        close = float(self.d1m.close[0])
        atr = float(self.atr_5m[0]) if len(self.d5m) >= self.p.atr_period and not math.isnan(float(self.atr_5m[0])) else 0.0
        if atr <= 0:
            return True
        displacement = abs(close - swept_level)
        min_disp = atr * 0.2  # close must be at least 0.2× ATR away from swept level
        if displacement < min_disp:
            bar_vol = float(self.d1m.volume[0]) if len(self.d1m) > 0 else 0.0
            vol_ok = bar_vol > float(self.vol_ema_1m[0]) if not math.isnan(float(self.vol_ema_1m[0])) else True
            if not vol_ok:
                self._log_rejection_once_per_hour(
                    "sq", f"Sweep quality: displacement={displacement:.2f} < {min_disp:.2f}, vol rejected."
                )
                return False
        return True

    def _heavy_volume_candle_confirm(self, sweep_direction: int) -> bool:
        """
        Check if current 1M bar is a heavy-volume candle matching sweep direction.
    
        Translated from volumeindicator.pine:
          EMA(volume, 20) x 1.25 threshold
          Bullish sweep (+1) -> GREEN candle (close > open) with heavy volume
          Bearish sweep (-1) -> RED candle (open > close) with heavy volume
        """
        if len(self.d1m) < 20:
            return False
    
        try:
            vol = float(self.d1m.volume[0])
            ema_val = float(self.vol_ema_1m[0])
        except (IndexError, TypeError, ValueError):
            return False
    
        if math.isnan(ema_val) or ema_val <= 0:
            open_price = float(self.d1m.open[0])
            close_price = float(self.d1m.close[0])
            if sweep_direction == 1:
                return close_price > open_price
            elif sweep_direction == -1:
                return open_price > close_price
            return False
    
        is_heavy = vol > ema_val * self.p.volume_threshold_multiplier
        if not is_heavy:
            return False
    
        open_price = float(self.d1m.open[0])
        close_price = float(self.d1m.close[0])
    
        if sweep_direction == 1:
            return close_price > open_price
        elif sweep_direction == -1:
            return open_price > close_price
        return False

    def _calculate_position_size(self, entry: float, stop: float, direction: int = 0) -> int:
        distance = abs(entry - stop)
        if distance <= 0:
            return 0
        equity = self.broker.getvalue()
        risk_based = int(math.floor(equity * self.p.risk_pct / distance))
        # Cap notional for LONG trades: XAUUSD 1 unit ≈ $4,000+ at entry price
        if direction == 1:
            max_by_cash = equity / entry
            size = int(min(risk_based, max_by_cash))
            size = max(size, 1) if max_by_cash >= 0.5 else 0
        else:
            size = risk_based
        return max(size, 0)

    # ═══════════════════════════════════════════════════════════════════════
    #  EXECUTION: Bracket Order Construction
    # ═══════════════════════════════════════════════════════════════════════

    def _build_and_submit_bracket(self, direction: int) -> None:
        """
        Build and submit OCO bracket order:
          - Market entry (parent)
          - Stop-loss at 1.5 x 5M ATR (child of parent)
          - Single take-profit at 1:2 RR (OCO with stop)
        """
        atr = float(self.atr_5m[0])
        if math.isnan(atr) or atr <= 0:
            self._log("ATR invalid; cannot build bracket.")
            return

        est = float(self.d1m.close[0])
        sd = atr * self.p.atr_multiplier

        stop_p = est - sd if direction == 1 else est + sd
        tp = est + (sd * self.p.rr_ratio) if direction == 1 else est - (sd * self.p.rr_ratio)

        total_size = self._calculate_position_size(est, stop_p, direction)
        if total_size <= 0:
            self._log("Position size is zero; skipping execution.")
            return

        if direction == 1:
            parent = self.buy(size=total_size, exectype=bt.Order.Market, transmit=False)
            stop_order = self.sell(size=total_size, exectype=bt.Order.Stop,
                                   price=stop_p, parent=parent, transmit=False)
            target = self.sell(size=total_size, exectype=bt.Order.Limit,
                               price=tp, parent=parent, oco=stop_order, transmit=True)
        else:
            parent = self.sell(size=total_size, exectype=bt.Order.Market, transmit=False)
            stop_order = self.buy(size=total_size, exectype=bt.Order.Stop,
                                  price=stop_p, parent=parent, transmit=False)
            target = self.buy(size=total_size, exectype=bt.Order.Limit,
                              price=tp, parent=parent, oco=stop_order, transmit=True)

        self.order_state = OrderBookState(
            parent=parent,
            stop=stop_order,
            target=target,
            direction=direction,
            total_size=total_size,
            entry_estimate=est,
            stop_price=stop_p,
            target_price=tp,
        )

        self._record_trade_opened()

        sweep_ref = self.active_sweep.reference_session if self.active_sweep else "Unknown"
        swept_lvl = self.active_sweep.swept_level if self.active_sweep else 0.0

        self.trade_metadata.append({
            "direction": "LONG" if direction == 1 else "SHORT",
            "entry_est": est,
            "sl": stop_p,
            "tp": tp,
            "size": total_size,
            "atr": atr,
            "rr": self.p.rr_ratio,
            "reference_session": sweep_ref,
            "swept_level": swept_lvl,
            "open_datetime": self._data_datetime_1m(),
            "entry_px": None,
            "exit_px": None,
            "exit_reason": None,
            "pnl": None,
            "pnl_net": None,
            "barlen": None,
            "close_datetime": None,
        })

        self._log(
            f"Submitted {'LONG' if direction == 1 else 'SHORT'} bracket | "
            f"size={total_size} | est={est:.2f} | sl={stop_p:.2f} | "
            f"tp={tp:.2f} | atr={atr:.2f} | rr={self.p.rr_ratio}"
        )
    def _record_trade_opened(self) -> None:
        self.bars_since_last_trade = 0
        self.trades_today += 1

    # ═══════════════════════════════════════════════════════════════════════
    #  MAIN CHECKLIST PIPELINE (sequential, skip on first fail)
    # ═══════════════════════════════════════════════════════════════════════

    def _run_pre_trade_checklist(self, ts: datetime) -> None:
        """
        Sequential checklist gating:

          LONG:  CB → Kill-zone → Cooldown → LossGuard → Sweep →
                 Trend → BB/EMA13 → H1Mom → BBEntryZone → VolSpike →
                 Pattern → Execute

          SHORT: CB → Kill-zone → Cooldown → LossGuard → Sweep →
                 Trend → BB/EMA13 → Pattern → Execute
        """
        # ── Gate 1: Daily loss circuit breaker ──
        if not self._daily_circuit_breaker_ok(ts):
            return

        # ── Gate 2: Kill-zone time window ──
        if not self._is_killzone_time(ts):
            self._log_rejection_once_per_hour("kz", "Checklist: outside kill-zone window.")
            return

        # ── Gate 3: Cooldown / daily trade limit ──
        if not self._check_cooldown_daily(ts):
            return

        # ── Gate 4: Consecutive loss streak guard (skip rest of day) ──
        if not self._consecutive_loss_guard_ok():
            return

        # ── Gate 5: Sweep detection ──
        sweep = self._detect_active_sweep(ts)
        if sweep is None:
            self._log_rejection_once_per_hour("sweep", "Checklist: no active sweep detected.")
            return

        self.active_sweep = sweep
        sweep_dir = sweep.direction

        if sweep.reference_session != "Asia":
            self._log_rejection_once_per_hour(
                "ref", f"Checklist: {sweep.reference_session} sweep rejected (Asia-only)."
            )
            return

        # ── Gate 6: Trend filter (EMA50/200 alignment) ──
        if not self._trend_filter_ok(sweep_dir, ts):
            return

        # ── Gate 6b: BB + EMA13 sweep quality (rejects only extremes) ──
        if not self._bb_ema13_confirm(sweep_dir):
            return

        if sweep_dir == 1:
            # ── LONG Entry Refinements ──
            if not self._h1_momentum_filter_ok():
                self._log_rejection_once_per_hour(
                    "h1mom", "Checklist: LONG rejected - H1 momentum filter."
                )
                return

            if not self._bb_entry_zone_long_ok():
                self._log_rejection_once_per_hour(
                    "bbzone", "Checklist: LONG rejected - BB entry zone."
                )
                return

            if not self._volume_spike_confirm_ok():
                self._log_rejection_once_per_hour(
                    "volspike", "Checklist: LONG rejected - volume spike confirmation."
                )
                return

            confirmed, patterns = self._confirm_pattern(sweep_dir, sweep.swept_level)
            if not confirmed:
                self._log_rejection_once_per_hour(
                    "pattern", "Checklist: LONG rejected - no pattern confirmation."
                )
                return
            self._diag_heavy_vol_bullish_pass += 1
        elif sweep_dir == -1:
            confirmed, patterns = self._confirm_pattern(sweep_dir, sweep.swept_level)
            if not confirmed:
                self._log_rejection_once_per_hour(
                    "pattern", "Checklist: SHORT rejected - no pattern confirmation."
                )
                return
            self._diag_heavy_vol_bearish_pass += 1

        self._log(
            f"Checklist PASSED | dir={'LONG' if sweep_dir == 1 else 'SHORT'} | "
            f"level={sweep.swept_level:.2f} | ref={sweep.reference_session} | "
            f"signals=[trend+pattern]"
        )

        self._build_and_submit_bracket(sweep_dir)

    # ═══════════════════════════════════════════════════════════════════════
    #  MAIN BAR PROCESSING
    # ═══════════════════════════════════════════════════════════════════════

    def _process_current_bar(self, allow_entries: bool) -> None:
        ts = self._data_datetime_1m()

        self._update_session_and_counter()
        self.bars_since_last_trade += 1

        if not allow_entries:
            return

        # ── Pre-entry guards ──
        if not self._indicators_ready():
            return
        if not self._is_backtest_time_allowed(ts):
            return

        # Overtrading guard: skip if already in a position or have pending orders
        if self.position:
            self._diag_blocked_by_position += 1
            return
        if self.order_state.has_pending_orders():
            return

        # Run the checklist pipeline
        self._run_pre_trade_checklist(ts)

    # ═══════════════════════════════════════════════════════════════════════
    #  LIFECYCLE: notify_order — handle fill, cancel, reject, partial, BE
    # ═══════════════════════════════════════════════════════════════════════

    def notify_order(self, order: bt.Order) -> None:
        if order.status in (order.Submitted, order.Accepted):
            return

        status_map = {
            order.Completed: "COMPLETED",
            order.Canceled: "CANCELLED",
            order.Margin: "MARGIN",
            order.Rejected: "REJECTED",
        }
        status_name = status_map.get(order.status, f"STATUS_{order.status}")

        if order.status == order.Completed:
            self._log(
                f"Order {status_name} | ref={order.ref} | "
                f"{'BUY' if order.isbuy() else 'SELL'} | "
                f"px={order.executed.price:.2f} | sz={int(order.executed.size)}"
            )
        else:
            self._log(f"Order {status_name} | ref={order.ref}")

        # ── Capture entry/exit prices from bracket fills ──
        if order.status == order.Completed:
            if order is self.order_state.parent or order.exectype == bt.Order.Market:
                self.order_state.entry_estimate = order.executed.price
                self._log(f"Entry confirmed at {order.executed.price:.2f}")
                for meta in reversed(self.trade_metadata):
                    if meta.get("entry_px") is None and meta.get("pnl_net") is None:
                        meta["entry_px"] = float(order.executed.price)
                        break
            elif order is self.order_state.stop or order.exectype == bt.Order.Stop:
                self._log("Stop loss triggered.")
                for meta in reversed(self.trade_metadata):
                    if meta.get("exit_px") is None and meta.get("pnl_net") is None and meta.get("entry_px") is not None:
                        meta["exit_px"] = float(order.executed.price)
                        meta["exit_reason"] = "Stop Loss"
                        break
            elif order is self.order_state.target or (order.exectype == bt.Order.Limit and order is not self.order_state.parent):
                self._log("Take profit triggered.")
                for meta in reversed(self.trade_metadata):
                    if meta.get("exit_px") is None and meta.get("pnl_net") is None and meta.get("entry_px") is not None:
                        meta["exit_px"] = float(order.executed.price)
                        meta["exit_reason"] = "Take Profit"
                        break

        # ── Parent order failed (canceled / margin / rejected) → clear state ──
        if order is self.order_state.parent and order.status in (
            order.Canceled, order.Margin, order.Rejected
        ):
            self._log("Parent order failed; clearing order state & active sweep.")
            self.order_state.clear()
            self.active_sweep = None

        # ── All bracket orders resolved → cleanup ──
        if (
            self.order_state.parent is not None
            and not self.order_state.has_pending_orders()
        ):
            self._log("All bracket orders resolved; clearing order state.")
            parent_filled = (
                self.order_state.parent is not None
                and self.order_state.parent.status == self.order_state.parent.Completed
            )
            if not parent_filled:
                self.active_sweep = None
            self.order_state.clear()

    # ═══════════════════════════════════════════════════════════════════════
    #  LIFECYCLE: notify_trade — PnL tracking, daily state, sweep reset
    # ═══════════════════════════════════════════════════════════════════════

    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed:
            return

        pnl = trade.pnlcomm
        is_win = pnl > 0

        # Update the matching metadata entry (first unfilled one with an entry)
        for meta in self.trade_metadata:
            if meta.get("pnl_net") is None and meta.get("entry_px") is not None:
                meta["entry_px"] = float(trade.price)
                meta["pnl"] = round(float(trade.pnl), 2)
                meta["pnl_net"] = round(float(pnl), 2)
                meta["barlen"] = int(trade.barlen)
                meta["close_datetime"] = bt.num2date(trade.dtclose)
                meta["open_datetime"] = bt.num2date(trade.dtopen)
                meta["is_win"] = is_win
                break

        self._log(
            f"Trade closed | {'PROFIT' if pnl > 0 else 'LOSS'} | "
            f"pnl={pnl:.2f} | bars={trade.barlen} | "
            f"net={trade.pnl:.2f} | comm={trade.commission:.2f}"
        )

        # Track consecutive losses for streak guard
        if is_win:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1

        # Reset sweep & order state on trade close
        self.order_state.clear()
        self.active_sweep = None

        # Update daily PnL tracking
        if self._daily_start_equity is not None:
            self._daily_pnl = self.broker.getvalue() - self._daily_start_equity


# ═══════════════════════════════════════════════════════════════════════════
#  ANALYZERS
# ═══════════════════════════════════════════════════════════════════════════

class TradeMetricsAnalyzer(bt.Analyzer):
    """Aggregated trade statistics."""

    def create_analysis(self) -> None:
        self.total = 0
        self.wins = 0
        self.losses = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self.net_profit = 0.0

    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed:
            return
        self.total += 1
        self.net_profit += trade.pnlcomm
        if trade.pnlcomm > 0:
            self.wins += 1
            self.gross_profit += trade.pnlcomm
        else:
            self.losses += 1
            self.gross_loss += abs(trade.pnlcomm)

    def get_analysis(self) -> Dict[str, Any]:
        if self.total == 0:
            return {
                "Total Trades": 0, "Wins": 0, "Losses": 0,
                "Win Rate (%)": 0.0, "Profit Factor": 0.0,
                "Expectancy ($)": 0.0, "Gross Profit": 0.0,
                "Gross Loss": 0.0, "Net Profit": 0.0,
            }
        wr = (self.wins / self.total) * 100.0
        pf = self.gross_profit / self.gross_loss if self.gross_loss > 0 else float("inf")
        return {
            "Total Trades": self.total,
            "Wins": self.wins,
            "Losses": self.losses,
            "Win Rate (%)": round(wr, 2),
            "Profit Factor": round(pf, 2) if math.isfinite(pf) else float("inf"),
            "Expectancy ($)": round(self.net_profit / self.total, 2),
            "Gross Profit": round(self.gross_profit, 2),
            "Gross Loss": round(self.gross_loss, 2),
            "Net Profit": round(self.net_profit, 2),
        }


class ClosedTradeListAnalyzer(bt.Analyzer):
    """List of all closed trades with open/close datetimes and PnL."""

    def create_analysis(self) -> None:
        self.trades: List[Dict[str, Any]] = []

    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed:
            return
        self.trades.append({
            "open_datetime": bt.num2date(trade.dtopen),
            "close_datetime": bt.num2date(trade.dtclose),
            "pnl": round(trade.pnlcomm, 2),
            "barlen": trade.barlen,
        })

    def get_analysis(self) -> List[Dict[str, Any]]:
        return self.trades


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN EXECUTION BLOCK
# ═══════════════════════════════════════════════════════════════════════════

def _detect_delimiter_and_header(filepath: str) -> Tuple[str, bool]:
    """Detect CSV delimiter and whether the file contains a header row."""
    with open(filepath, "r") as f:
        first_line = f.readline().strip()
    n_semi = first_line.count(";")
    n_comma = first_line.count(",")
    delimiter = ";" if n_semi > n_comma else ","
    lower = first_line.lower()
    header_keywords = {"datetime", "date", "time", "open", "high", "low", "close", "volume"}
    tokens = [t.strip().strip('"\'') for t in lower.split(delimiter)]
    has_header = any(kw in t for t in tokens for kw in header_keywords)
    return delimiter, has_header


REQUIRED_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]


def load_xauusd_dataframe(csv_path: str) -> pd.DataFrame:
    """Load XAUUSD OHLCV CSV into a UTC-indexed, cleaned DataFrame."""
    import os as _os
    delimiter, has_header = _detect_delimiter_and_header(csv_path)
    logger.info("Reading %s (delimiter=%r, header=%r)", csv_path, delimiter, has_header)

    if has_header:
        df = pd.read_csv(csv_path, sep=delimiter)
    else:
        df = pd.read_csv(csv_path, sep=delimiter, header=None, names=REQUIRED_COLUMNS)

    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}. Found: {list(df.columns)}")

    df = df[REQUIRED_COLUMNS].copy()

    # Parse datetime
    sample = str(df["datetime"].dropna().iloc[0])
    fmt = "%Y-%m-%d %H:%M:%S" if "-" in sample else "%Y%m%d %H%M%S"
    df["datetime"] = pd.to_datetime(df["datetime"], format=fmt, errors="coerce")
    df = df.dropna(subset=["datetime"])

    # Convert numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])

    # Sort and deduplicate
    df = df.sort_values("datetime").drop_duplicates("datetime", keep="last")

    if df.empty:
        raise ValueError("No valid OHLCV rows after cleaning.")

    # XAUUSD plausibility check
    med_close = float(df["close"].median())
    if med_close <= 1000:
        raise ValueError(f"Data does not look like XAUUSD: median close = {med_close:.2f}")

    # Fix high/low swaps
    swapped = 0
    mask = df["high"] < df["low"]
    if mask.any():
        swapped = mask.sum()
        df.loc[mask, ["high", "low"]] = df.loc[mask, ["low", "high"]].values
    if swapped:
        logger.warning("Swapped high/low values in %d rows.", swapped)

    # Clip negative volume
    df["volume"] = df["volume"].clip(lower=0)

    # Drop rows where open/close are outside [low, high]
    before = len(df)
    df = df[
        df["open"].between(df["low"], df["high"])
        & df["close"].between(df["low"], df["high"])
    ]
    if len(df) < before:
        logger.warning("Dropped %d rows with open/close outside [low, high].", before - len(df))

    df = df.set_index("datetime").tz_localize("utc")

    logger.info("Loaded %d XAUUSD 1m bars (%s to %s)", len(df),
                df.index.min().strftime("%Y-%m-%d"),
                df.index.max().strftime("%Y-%m-%d"))
    return df


def run_backtest(
    csv_path: str = "xauusd_1m.csv",
    initial_cash: float = 5_000.0,
    commission: float = 0.0002,
    slippage_fixed: float = 0.05,
    trade_start: Optional[str] = None,
    trade_end: Optional[str] = None,
    ema_period: int = 200,
    atr_period: int = 14,
    atr_multiplier: float = 1.5,
    risk_pct: float = 0.02,
    rr_ratio: float = 1.0,
    sweep_expiry_bars: int = 12,
    structure_lookback: int = 3,
    enable_killzone_filter: bool = True,
    enable_volume_filter: bool = False,
    enable_trend_filter: bool = True,
    max_daily_trades: int = 8,
    min_bars_between_trades: int = 5,
    max_consecutive_losses: int = 2,
    partial_tp_enabled: bool = True,
    daily_loss_limit_pct: float = -0.04,
    enable_h1_momentum_filter: bool = True,
    enable_bb_entry_zone: bool = True,
    enable_volume_spike_confirm: bool = True,
    volume_spike_multiplier: float = 1.5,
    log_level: str = "INFO",
) -> Dict[str, Any]:
    """
    Run a full Backtrader cerebro backtest with the unified strategy.

    Returns a dict with:
      - metrics: aggregated trade statistics
      - closed_trades: list of individual closed trades
      - drawdown: max drawdown percentage
      - returns: total return percentage
      - final_value: final portfolio value
    """
    # ── Logging setup ──
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Load data ──
    df_1m = load_xauusd_dataframe(csv_path)
    logger.info("Data loaded: %d bars from %s to %s",
                len(df_1m), df_1m.index.min(), df_1m.index.max())

    # ── Resample to higher timeframes ──
    ohlc = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    df_5m = df_1m.resample("5min").apply(ohlc).dropna()
    df_1h = df_1m.resample("1h").apply(ohlc).dropna()
    df_4h = df_1m.resample("4h").apply(ohlc).dropna()

    logger.info("Resampled: 5M=%d bars, 1H=%d bars, 4H=%d bars",
                len(df_5m), len(df_1h), len(df_4h))

    # ── Convert to Backtrader feeds ──
    def _to_feed(dff: pd.DataFrame) -> bt.feeds.PandasData:
        return bt.feeds.PandasData(dataname=dff, datetime=None)

    data_1m = _to_feed(df_1m)
    data_5m = _to_feed(df_5m)
    data_1h = _to_feed(df_1h)
    data_4h = _to_feed(df_4h)

    # ── Parse trade window ──
    ts_start = pd.Timestamp(trade_start).to_pydatetime() if trade_start else None
    ts_end = pd.Timestamp(trade_end).to_pydatetime() if trade_end else None

    # ── Cerebro ──
    cerebro = bt.Cerebro()
    cerebro.adddata(data_1m)
    cerebro.adddata(data_5m)
    cerebro.adddata(data_1h)
    cerebro.adddata(data_4h)

    cerebro.addstrategy(
        QuantSessionSweepStrategy,
        ema_period=ema_period,
        atr_period=atr_period,
        atr_multiplier=atr_multiplier,
        risk_pct=risk_pct,
        rr_ratio=rr_ratio,
        sweep_expiry_bars=sweep_expiry_bars,
        structure_lookback=structure_lookback,
        enable_killzone_filter=enable_killzone_filter,
        enable_volume_filter=enable_volume_filter,
        enable_trend_filter=enable_trend_filter,
        max_daily_trades=max_daily_trades,
        min_bars_between_trades=min_bars_between_trades,
        max_consecutive_losses=max_consecutive_losses,
        partial_tp_enabled=partial_tp_enabled,
        daily_loss_limit_pct=daily_loss_limit_pct,
        enable_h1_momentum_filter=enable_h1_momentum_filter,
        enable_bb_entry_zone=enable_bb_entry_zone,
        enable_volume_spike_confirm=enable_volume_spike_confirm,
        volume_spike_multiplier=volume_spike_multiplier,
        trade_start=ts_start,
        trade_end=ts_end,
    )

    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.broker.set_slippage_fixed(slippage_fixed)

    # ── Analyzers ──
    cerebro.addanalyzer(TradeMetricsAnalyzer, _name="metrics")
    cerebro.addanalyzer(ClosedTradeListAnalyzer, _name="closed_trades")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    # ── Run ──
    logger.info("Starting cerebro run | Initial cash: %.2f", initial_cash)
    results = cerebro.run()

    strat = results[0]
    metrics_raw = strat.analyzers.metrics.get_analysis()
    closed_trades = strat.analyzers.closed_trades.get_analysis()
    drawdown = strat.analyzers.drawdown.get_analysis()
    returns = strat.analyzers.returns.get_analysis()

    final_value = cerebro.broker.getvalue()

    # ── Print summary ──
    print("\n" + "=" * 80)
    print("  PERFORMANCE METRICS")
    print("=" * 80)
    for k, v in metrics_raw.items():
        print(f"  {k}: {v}")
    dd_pct = drawdown.get("max", {}).get("drawdown", 0.0) if isinstance(drawdown, dict) else drawdown.max.drawdown
    rtn_pct = returns.get("rnorm100", 0.0) if isinstance(returns, dict) else returns.rnorm100
    print(f"  Max Drawdown (%): {dd_pct:.2f}%")
    print(f"  Total Return (%): {rtn_pct:.2f}%")
    print(f"  Final Portfolio: {final_value:.2f}")
    print("=" * 80)
    if closed_trades:
        print("  CLOSED TRADES")
        print("-" * 80)
        for i, t in enumerate(closed_trades, 1):
            print(f"  #{i} | open={t['open_datetime']} | close={t['close_datetime']} | "
                  f"pnl={t['pnl']:.2f} | bars={t['barlen']}")
    print("=" * 80)
    print("Backtest completed.\n")

    return {
        "metrics": metrics_raw,
        "closed_trades": closed_trades,
        "drawdown": drawdown,
        "returns": returns,
        "final_value": final_value,
        "trade_metadata": strat.trade_metadata,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="XAUUSD Quant Session-Sweep Unified Backtest")
    parser.add_argument("--csv", default="xauusd_1m.csv", help="Path to 1M OHLCV CSV")
    parser.add_argument("--config", default="configs/config_30d.yaml", help="YAML config file")
    parser.add_argument("--cash", type=float, default=5_000.0, help="Initial cash")
    parser.add_argument("--commission", type=float, default=0.0002)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--trade-start", default=None, help="Trade window start (YYYY-MM-DD)")
    parser.add_argument("--trade-end", default=None, help="Trade window end (YYYY-MM-DD)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--ema-period", type=int, default=200)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--atr-multiplier", type=float, default=1.5)
    parser.add_argument("--risk-pct", type=float, default=0.02)
    parser.add_argument("--rr-ratio", type=float, default=1.0)
    parser.add_argument("--sweep-expiry-bars", type=int, default=12)
    parser.add_argument("--structure-lookback", type=int, default=3)
    parser.add_argument("--no-killzone", action="store_true", help="Disable kill-zone filter")
    parser.add_argument("--enable-volume-filter", action="store_true")
    parser.add_argument("--max-daily-trades", type=int, default=8)
    parser.add_argument("--min-bars-between", type=int, default=5)
    parser.add_argument("--no-partial-tp", action="store_true", help="Disable partial take-profit")
    parser.add_argument("--daily-loss-limit-pct", type=float, default=-0.04)

    args = parser.parse_args()

    # If --config is provided, load it and use its values as defaults
    if args.config and Path(args.config).exists():
        import yaml
        with open(args.config, "r") as f:
            cfg = yaml.safe_load(f) or {}

        strat_cfg = cfg.get("strategy", {})
        risk_cfg = cfg.get("risk", {})
        exec_cfg = cfg.get("execution", {})
        data_cfg = cfg.get("data", {})

        # Use config values unless overridden by explicit CLI args
        run_backtest(
            csv_path=args.csv or data_cfg.get("file_path", "xauusd_1m.csv"),
            initial_cash=args.cash if args.cash != 5_000.0 else exec_cfg.get("initial_cash", 5_000.0),
            commission=args.commission if args.commission != 0.0002 else exec_cfg.get("commission", 0.0002),
            slippage_fixed=args.slippage if args.slippage != 0.05 else exec_cfg.get("slippage_fixed", 0.05),
            trade_start=args.trade_start or data_cfg.get("trade_start"),
            trade_end=args.trade_end or data_cfg.get("trade_end"),
            ema_period=args.ema_period if args.ema_period != 200 else strat_cfg.get("ema_period", 200),
            atr_period=args.atr_period if args.atr_period != 14 else strat_cfg.get("atr_period", 14),
            atr_multiplier=args.atr_multiplier if args.atr_multiplier != 1.5 else strat_cfg.get("atr_multiplier", 1.5),
            risk_pct=args.risk_pct if args.risk_pct != 0.02 else strat_cfg.get("risk_pct", 0.02),
            rr_ratio=args.rr_ratio if args.rr_ratio != 1.0 else strat_cfg.get("rr_ratio", 1.0),
            sweep_expiry_bars=args.sweep_expiry_bars if args.sweep_expiry_bars != 12 else strat_cfg.get("sweep_expiry_bars", 12),
            structure_lookback=args.structure_lookback if args.structure_lookback != 3 else strat_cfg.get("structure_lookback", 3),
            enable_killzone_filter=not args.no_killzone if args.no_killzone else strat_cfg.get("enable_killzone_filter", True),
            enable_volume_filter=args.enable_volume_filter or strat_cfg.get("enable_volume_filter", False),
            max_daily_trades=args.max_daily_trades if args.max_daily_trades != 8 else strat_cfg.get("max_daily_trades", 8),
            min_bars_between_trades=args.min_bars_between if args.min_bars_between != 5 else strat_cfg.get("min_bars_between_trades", 5),
            partial_tp_enabled=not args.no_partial_tp,
            daily_loss_limit_pct=args.daily_loss_limit_pct if args.daily_loss_limit_pct != -0.04 else risk_cfg.get("daily_loss_limit_pct", -0.04),
            log_level=args.log_level or cfg.get("logging", {}).get("level", "INFO"),
        )
    else:
        run_backtest(
            csv_path=args.csv,
            initial_cash=args.cash,
            commission=args.commission,
            slippage_fixed=args.slippage,
            trade_start=args.trade_start,
            trade_end=args.trade_end,
            ema_period=args.ema_period,
            atr_period=args.atr_period,
            atr_multiplier=args.atr_multiplier,
            risk_pct=args.risk_pct,
            rr_ratio=args.rr_ratio,
            sweep_expiry_bars=args.sweep_expiry_bars,
            structure_lookback=args.structure_lookback,
            enable_killzone_filter=not args.no_killzone,
            enable_volume_filter=args.enable_volume_filter,
            max_daily_trades=args.max_daily_trades,
            min_bars_between_trades=args.min_bars_between,
            partial_tp_enabled=not args.no_partial_tp,
            daily_loss_limit_pct=args.daily_loss_limit_pct,
            log_level=args.log_level,
        )