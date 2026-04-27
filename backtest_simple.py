#!/usr/bin/env python3
"""
Simplified Sweep Backtest — No complex gates.
Rules:
  1. Price wick breaks session high/low (Asia or London)
  2. Price stays beyond that level for 1–15 minutes
  3. Price reverses (close returns inside the level) → trade
"""
import argparse
import logging
import sys
from datetime import datetime, time
from pathlib import Path

import pandas as pd
import backtrader as bt

from src.engine.session_liquidity import SessionLiquidityTracker
from src.utils.config import load_config
from src.strategy.unified_strategy import load_xauusd_dataframe

logger = logging.getLogger("simple_backtest")


class SimpleSweepStrategy(bt.Strategy):
    params = dict(
        atr_period=14,
        atr_multiplier=1.5,
        risk_pct=0.02,
        rr_ratio=1.5,
        trade_start=None,
        trade_end=None,
        stale_bar_limit=15,
        sweep_cooldown=5,
    )

    def __init__(self):
        self.d1m = self.datas[0]
        self.d5m = self.datas[1]

        self.tracker = SessionLiquidityTracker(
            stale_bar_limit=self.p.stale_bar_limit,
            sweep_cooldown_bars=self.p.sweep_cooldown,
        )
        self._bar_index_5m = 0
        self._last_5m_len = 0
        self.atr = bt.indicators.ATR(self.d5m, period=self.p.atr_period)
        self.order_state = None
        self.bars_since_last_trade = 999

    @staticmethod
    def _classify_session(ts: datetime) -> str:
        t = ts.time()
        if time(0, 0) <= t < time(7, 0):
            return "Asia"
        if time(7, 0) <= t < time(12, 0):
            return "London"
        if time(12, 0) <= t < time(16, 0):
            return "London-NY Overlap"
        if time(12, 0) <= t < time(21, 0):
            return "NY"
        return "Off"

    def _ts(self) -> datetime:
        return bt.num2date(self.d1m.datetime[0]).replace(tzinfo=None)

    def log(self, msg: str) -> None:
        logger.info("[%s] %s", self._ts(), msg)

    def prenext(self):
        self._update_tracker()

    def nextstart(self):
        self._update_tracker()

    def next(self):
        self._update_tracker()
        self._process_bar()

    def _update_tracker(self):
        ts = self._ts()
        high = float(self.d1m.high[0])
        low = float(self.d1m.low[0])
        close = float(self.d1m.close[0])
        self.tracker.update(ts, price_high=high, price_low=low)
        session = self._classify_session(ts)
        self.tracker.mark_reference_touched(session, close)
        if len(self.d5m) > self._last_5m_len:
            self._bar_index_5m = len(self.d5m)
            self._last_5m_len = len(self.d5m)
            self.tracker.expire_if_stale(self._bar_index_5m)

    def _process_bar(self):
        if self.position or (self.order_state and self.order_state.get("alive", False)):
            return

        ts = self._ts()

        if self.p.trade_start and ts < self.p.trade_start:
            return
        if self.p.trade_end and ts > self.p.trade_end:
            return

        if len(self.d5m) < self.p.atr_period + 2:
            return

        session = self._classify_session(ts)
        ref = self.tracker.get_reference_level(session)
        if ref is None or not ref.is_valid():
            return

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

        if not signal.confirmed or signal.direction is None:
            return

        atr = self.atr[0]
        if atr <= 0:
            return

        self.bars_since_last_trade += 1
        if self.bars_since_last_trade < 3:
            return

        direction = signal.direction
        entry = bar_close
        sl_dist = atr * self.p.atr_multiplier
        tp_dist = sl_dist * self.p.rr_ratio
        stop_p = entry - sl_dist if direction == 1 else entry + sl_dist
        tp_p = entry + tp_dist if direction == 1 else entry - tp_dist

        # Position sizing: risk % of equity per trade, cap notional to 50%
        equity = self.broker.getvalue()
        risk_size = int(equity * self.p.risk_pct / sl_dist)
        max_notional = int(equity / entry * 0.5)
        size = max(1, min(risk_size, max_notional))

        self.log(
            f"SWEEP TRADE | "
            f"dir={'LONG' if direction == 1 else 'SHORT'} | "
            f"ref={signal.reference_session} | "
            f"level={signal.swept_level:.2f} | "
            f"entry={entry:.2f} | sl={stop_p:.2f} | tp={tp_p:.2f} | "
            f"size={size}"
        )

        if direction == 1:
            parent = self.buy(size=size, exectype=bt.Order.Market, transmit=False)
            self.sell(size=size, exectype=bt.Order.Stop, price=stop_p,
                      parent=parent, transmit=False)
            self.sell(size=size, exectype=bt.Order.Limit, price=tp_p,
                      parent=parent, transmit=True)
        else:
            parent = self.sell(size=size, exectype=bt.Order.Market, transmit=False)
            self.buy(size=size, exectype=bt.Order.Stop, price=stop_p,
                     parent=parent, transmit=False)
            self.buy(size=size, exectype=bt.Order.Limit, price=tp_p,
                     parent=parent, transmit=True)

        self.order_state = {"alive": True, "parent": parent}
        self.bars_since_last_trade = 0

    def notify_order(self, order):
        if order.status == order.Completed:
            self.log(
                f"Order filled | ref={order.ref} | {'BUY' if order.isbuy() else 'SELL'} | "
                f"px={order.executed.price:.2f} | sz={int(order.executed.size)}"
            )
        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            self.log(f"Order failed | ref={order.ref} status={order.status}")
        if self.order_state and order is self.order_state.get("parent"):
            if order.status in (order.Completed, order.Canceled, order.Margin, order.Rejected):
                self.order_state["alive"] = False

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        self.log(
            f"Trade closed | pnl={trade.pnlcomm:.2f} | "
            f"{'WIN' if trade.pnlcomm > 0 else 'LOSS'}"
        )
        self.order_state = None


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

def main(argv=None):
    parser = argparse.ArgumentParser(description="Simple Sweep Backtest")
    parser.add_argument("--csv", default="xauusd_1m.csv")
    parser.add_argument("--config", default="configs/config_30d.yaml")
    parser.add_argument("--cash", type=float, default=100000.0)
    parser.add_argument("--risk-pct", type=float, default=0.02)
    parser.add_argument("--rr", type=float, default=1.5)
    parser.add_argument("--atr-mult", type=float, default=1.5)
    parser.add_argument("--stale-limit", type=int, default=15)
    parser.add_argument("--sweep-cooldown", type=int, default=5)
    parser.add_argument("--trade-start", default=None)
    parser.add_argument("--trade-end", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        cfg = load_config(args.config)
    except Exception:
        cfg = None

    trade_start = args.trade_start
    trade_end = args.trade_end
    if cfg and not trade_start:
        trade_start = getattr(cfg.data, "trade_start", None)
    if cfg and not trade_end:
        trade_end = getattr(cfg.data, "trade_end", None)

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = Path(__file__).resolve().parent / csv_path
    df_1m = load_xauusd_dataframe(str(csv_path))

    ohlc = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    df_5m = df_1m.resample("5min").apply(ohlc).dropna()

    logger.info("1M bars: %d | 5M bars: %d", len(df_1m), len(df_5m))

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(bt.feeds.PandasData(dataname=df_1m, datetime=None))
    cerebro.adddata(bt.feeds.PandasData(dataname=df_5m, datetime=None))
    cerebro.broker.setcash(args.cash)
    cerebro.broker.setcommission(commission=0.0002)

    ts_start = pd.Timestamp(trade_start).to_pydatetime() if trade_start else None
    ts_end = pd.Timestamp(trade_end).to_pydatetime() if trade_end else None

    cerebro.addstrategy(
        SimpleSweepStrategy,
        atr_multiplier=args.atr_mult,
        risk_pct=args.risk_pct,
        rr_ratio=args.rr,
        stale_bar_limit=args.stale_limit,
        sweep_cooldown=args.sweep_cooldown,
        trade_start=ts_start,
        trade_end=ts_end,
    )

    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")

    logger.info("Starting simple sweep backtest...")
    starting = cerebro.broker.getvalue()
    results = cerebro.run()
    strat = results[0]

    final_val = cerebro.broker.getvalue()
    ta = strat.analyzers.trades.get_analysis()
    dd = strat.analyzers.drawdown.get_analysis()
    ret = strat.analyzers.returns.get_analysis()

    print("\n" + "=" * 60)
    print("  SIMPLE SWEEP BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Total Trades:     {ta.get('total', {}).get('total', 0)}")
    print(f"  Wins:             {ta.get('won', {}).get('total', 0)}")
    print(f"  Losses:           {ta.get('lost', {}).get('total', 0)}")
    wr = 0
    if ta.get('total', {}).get('total', 0) > 0:
        wr = ta['won']['total'] / ta['total']['total'] * 100
    print(f"  Win Rate (%):     {wr:.1f}")
    gross_p = ta.get('won', {}).get('pnl', {}).get('total', 0.0)
    gross_l = abs(ta.get('lost', {}).get('pnl', {}).get('total', 0.0))
    pf = gross_p / gross_l if gross_l > 0 else float('inf')
    print(f"  Profit Factor:    {pf:.2f}")
    net_p = ta.get('pnl', {}).get('net', {}).get('total', 0.0)
    print(f"  Net Profit:       {net_p:.2f}")
    print(f"  Max Drawdown:     {dd.get('max', {}).get('drawdown', 0):.2f}%")
    rtn = ret.get('rnorm100', 0)
    print(f"  Total Return:     {rtn:.2f}%")
    print(f"  Start Value:      {starting:.2f}")
    print(f"  Final Value:      {final_val:.2f}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())