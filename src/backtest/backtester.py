import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime

from src.engine import SessionLiquidityTracker, SweepSignal, SweepDirection
from src.utils.logger import logger


class Backtester:
    """
    Production backtest engine with SessionLiquidityTracker integration.

    Accepts 1M OHLC bars, updates the session tracker, detects sweeps,
    and executes mock trades based on sweep signals + pattern + risk params.
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        commission: float = 0.0001,
        slippage: float = 0.0001,
        tracker: Optional[SessionLiquidityTracker] = None,
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.commission = commission
        self.slippage = slippage
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = [initial_balance]

        # Session liquidity engine – inject or auto-create
        self.tracker = tracker or SessionLiquidityTracker(stale_bar_limit=3)

        # Internal bar counter for staleness / 5M indexing
        self._bar_index_1m: int = 0
        self._bar_index_5m: int = 0

    # ──────────────────────────────────────────────────────────────
    def process_bar(
        self,
        timestamp: datetime,
        symbol: str,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> Optional[SweepSignal]:
        """
        Feed a **1-minute** OHLC bar into the backtest pipeline.

        Workflow (per bar):
        1. Update the SessionLiquidityTracker with bar high/low.
        2. Classify the session for this bar.
        3. Mark reference levels as touched (for NY fallback logic).
        4. Detect whether this bar confirms a liquidity sweep.
        5. Every 5th 1M bar, run staleness expiry.

        Returns
        -------
        SweepSignal or None
            A confirmed sweep if detected, otherwise None.
        """
        # Update session ranges
        self.tracker.update(timestamp, high, low)

        # Determine session (reuse tracker's built-in classification)
        session = self.tracker._classify_session(timestamp)

        # Mark reference touched (essential for fallback chain)
        self.tracker.mark_reference_touched(session, close)

        # Detect sweep (returns SweepSignal with direction or None)
        signal = self.tracker.detect_sweep(
            timestamp=timestamp,
            bar_low=low,
            bar_high=high,
            bar_close=close,
            current_session=session,
            bar_index_5m=self._bar_index_5m,
        )

        # Every 5th 1M bar, expire stale pending breaches
        self._bar_index_1m += 1
        if self._bar_index_1m % 5 == 0:
            self._bar_index_5m += 1
            self.tracker.expire_if_stale(self._bar_index_5m)

        # Return only confirmed sweeps
        if signal and signal.confirmed and signal.direction is not None:
            logger.info(
                "Sweep detected | direction=%+d level=%.5f ref=%s bar=%d",
                signal.direction,
                signal.swept_level,
                signal.reference_session,
                signal.bar_index,
            )
            return signal

        return None

    # ──────────────────────────────────────────────────────────────
    def execute_mock_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        sl: float,
        tp: float,
        size: float,
    ):
        """Simulate trade execution and outcome based on SL/TP (instantaneous)."""
        # Apply slippage
        if direction == "long":
            entry_price *= (1 + self.slippage)
        else:
            entry_price *= (1 - self.slippage)

        # Mock result: 50 % win rate for demonstration
        import random

        won = random.choice([True, False])

        if won:
            exit_price = tp
            profit = abs(tp - entry_price) * size
        else:
            exit_price = sl
            profit = -abs(entry_price - sl) * size

        # Apply commission
        commission_cost = (entry_price * size + exit_price * size) * self.commission
        net_profit = profit - commission_cost

        self.balance += net_profit
        self.equity_curve.append(self.balance)

        self.trades.append({
            "symbol": symbol,
            "direction": direction,
            "entry": entry_price,
            "exit": exit_price,
            "size": size,
            "net_profit": net_profit,
            "won": won,
        })

        logger.info(
            "Paper Trade Executed: %s on %s | Net Profit: %.2f | Balance: %.2f",
            direction, symbol, net_profit, self.balance,
        )

    # ──────────────────────────────────────────────────────────────
    def calculate_metrics(self) -> Dict:
        """Compute portfolio performance metrics."""
        if not self.trades:
            return {}

        wins = [t for t in self.trades if t['won']]
        losses = [t for t in self.trades if not t['won']]

        win_rate = len(wins) / len(self.trades) if self.trades else 0

        gross_profit = sum(t['net_profit'] for t in wins)
        gross_loss = abs(sum(t['net_profit'] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Max Drawdown
        equity_s = pd.Series(self.equity_curve)
        peak = equity_s.expanding(min_periods=1).max()
        drawdown = (equity_s - peak) / peak
        max_drawdown = drawdown.min()

        expectancy = (
            (win_rate * (gross_profit / len(wins) if wins else 0))
            - ((1 - win_rate) * (gross_loss / len(losses) if losses else 0))
        )

        metrics = {
            "Total Trades": len(self.trades),
            "Win Rate": win_rate,
            "Profit Factor": profit_factor,
            "Max Drawdown": max_drawdown,
            "Expectancy": expectancy,
            "Final Balance": self.balance,
        }

        logger.info(f"Backtest Metrics: {metrics}")
        return metrics

    # ──────────────────────────────────────────────────────────────
    def reset(self) -> None:
        """Reset backtester state for a fresh run."""
        self.balance = self.initial_balance
        self.trades.clear()
        self.equity_curve = [self.initial_balance]
        self._bar_index_1m = 0
        self._bar_index_5m = 0