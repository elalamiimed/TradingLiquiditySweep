"""Backtest smoke test: Run 7-day subset → assert >0 trades, valid metrics, no NaN/inf."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from src.engine.session_liquidity import SessionLiquidityTracker
from src.backtest.backtester import Backtester


def _dt(hour, minute=0, day=10):
    return datetime(2024, 7, day, hour, minute, tzinfo=timezone.utc)


class TestBacktestSmoke:
    """
    7-day backtest with a small generated dataset (24 bars).
    Asserts trades were taken, metrics are finite, no NaN/Inf.
    """

    @pytest.fixture
    def backtest_df(self):
        """24 rows spanning 7 days, with sweep opportunities built in."""
        np.random.seed(77)
        n = 24
        base_time = datetime(2024, 7, 10, 0, 0, tzinfo=timezone.utc)
        times = [base_time + timedelta(hours=i * 7) for i in range(n)]

        # Build data with clear swing to create at least one sweep
        data = []
        price = 2300.0
        for i, ts in enumerate(times):
            # Create a dip on bar 3, then rally on bar 6, then new high on 10, etc
            if i == 3:
                price = 2285.0
            elif i == 6:
                price = 2310.0
            elif i == 10:
                price = 2325.0
            elif i == 15:
                price = 2295.0
            elif i == 20:
                price = 2330.0
            else:
                price += np.random.uniform(-4, 5)

            wick = np.random.uniform(1, 6)
            body = np.random.uniform(-2, 3)
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

    def test_backtest_produces_trades(self, backtest_df):
        """Running the backtester must generate at least 1 trade."""
        tracker = SessionLiquidityTracker(stale_bar_limit=3)
        bt = Backtester(
            initial_balance=10000.0,
            commission=0.0001,
            slippage=0.0001,
            tracker=tracker,
        )

        for _, row in backtest_df.iterrows():
            signal = bt.process_bar(
                timestamp=row["timestamp"],
                symbol="XAUUSD",
                open_=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
            )
            if signal is not None and signal.confirmed:
                # Determine direction
                if signal.direction == 1:
                    direction = "long"
                elif signal.direction == -1:
                    direction = "short"
                else:
                    continue

                # Simple SL/TP based on ATR approximation
                entry = row["close"]
                atr = abs(row["high"] - row["low"]) * 1.5
                if direction == "long":
                    sl = entry - atr
                    tp = entry + atr * 2
                else:
                    sl = entry + atr
                    tp = entry - atr * 2

                size = 0.01  # mini contract
                bt.execute_mock_trade(
                    symbol="XAUUSD", direction=direction,
                    entry_price=entry, sl=sl, tp=tp, size=size,
                )

        # Assert at least 1 trade executed
        assert len(bt.trades) > 0, f"Expected >0 trades, got {len(bt.trades)}"

    def test_metrics_are_valid(self, backtest_df):
        """Metrics must be finite – no NaN or Inf."""
        tracker = SessionLiquidityTracker(stale_bar_limit=3)
        bt = Backtester(
            initial_balance=10000.0,
            commission=0.0001,
            slippage=0.0001,
            tracker=tracker,
        )

        for _, row in backtest_df.iterrows():
            signal = bt.process_bar(
                timestamp=row["timestamp"],
                symbol="XAUUSD",
                open_=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
            )
            if signal is not None and signal.confirmed:
                direction = "long" if signal.direction == 1 else "short"
                entry = row["close"]
                atr = abs(row["high"] - row["low"]) * 1.5
                sl = entry - atr if direction == "long" else entry + atr
                tp = entry + atr * 2 if direction == "long" else entry - atr * 2
                bt.execute_mock_trade(
                    symbol="XAUUSD", direction=direction,
                    entry_price=entry, sl=sl, tp=tp, size=0.01,
                )

        metrics = bt.calculate_metrics()

        if metrics:
            for key, val in metrics.items():
                if isinstance(val, float):
                    assert not np.isnan(val), f"Metric {key} is NaN"
                    assert not np.isinf(val), f"Metric {key} is Inf"

            assert "Total Trades" in metrics
            assert "Win Rate" in metrics
            assert "Profit Factor" in metrics
            assert "Final Balance" in metrics

    def test_equity_curve_monotonic_shape(self, backtest_df):
        """Equity curve starts at initial_balance and has correct length."""
        tracker = SessionLiquidityTracker(stale_bar_limit=3)
        bt = Backtester(initial_balance=10000.0, tracker=tracker)

        for _, row in backtest_df.iterrows():
            signal = bt.process_bar(
                timestamp=row["timestamp"],
                symbol="XAUUSD",
                open_=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
            )
            if signal and signal.confirmed:
                direction = "long" if signal.direction == 1 else "short"
                entry = row["close"]
                atr = abs(row["high"] - row["low"]) * 1.5
                sl = entry - atr if direction == "long" else entry + atr
                tp = entry + atr * 2 if direction == "long" else entry - atr * 2
                bt.execute_mock_trade(
                    symbol="XAUUSD", direction=direction,
                    entry_price=entry, sl=sl, tp=tp, size=0.01,
                )

        eq = bt.equity_curve
        assert eq[0] == 10000.0
        # Equity curve length = 1 (initial) + number of trades
        assert len(eq) == 1 + len(bt.trades)

    def test_backtest_reset_clears_state(self, backtest_df):
        """Reset() must clear trades and restore balance."""
        tracker = SessionLiquidityTracker(stale_bar_limit=3)
        bt = Backtester(initial_balance=5000.0, tracker=tracker)

        # Run a few bars
        for i in range(min(5, len(backtest_df))):
            row = backtest_df.iloc[i]
            bt.process_bar(
                timestamp=row["timestamp"],
                symbol="XAUUSD",
                open_=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
            )

        bt.reset()
        assert bt.balance == 5000.0
        assert len(bt.trades) == 0
        assert bt.equity_curve == [5000.0]

    def test_no_nan_in_metrics_when_no_trades(self):
        """Empty backtest returns empty metrics dict, no crash."""
        tracker = SessionLiquidityTracker()
        bt = Backtester(tracker=tracker)
        metrics = bt.calculate_metrics()
        assert metrics == {}