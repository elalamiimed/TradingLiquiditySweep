#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XAUUSD Quant Session-Sweep Backtest -- Production v5.0
======================================================
Pure candlestick-based session-sweep strategy (no trend filter, no MSS/FVG).
Single-take-profit OCO brackets with 5M ATR-based stop placement.

CLI:
    python backtest.py --config configs/config_30d.yaml --log-level INFO
    python backtest.py --dry-run                               # validate only
    python backtest.py --config configs/config.yaml --csv xauusd_1m.csv

Exit codes:
    0 -- success
    1 -- data error   (CSV not found / empty / invalid OHLC)
    2 -- config error (YAML missing / schema mismatch)
    3 -- backtest failure (cerebro run exception)
"""

from __future__ import annotations

# -- stdlib ------------------------------------------------------------
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# -- pandas ------------------------------------------------------------
import pandas as pd

# -- backtrader --------------------------------------------------------
import backtrader as bt

# -- local modules -----------------------------------------------------
from src.utils.config import load_config, AppConfig
from src.utils.logger import setup_logger

from src.engine.session_liquidity import (
    SessionLiquidityTracker,
    SweepSignal,
    SweepDirection,
)

from src.engine.pattern_engine import (
    PatternConfig,
    PatternDetectors,
    ConfirmationValidator,
)

from src.strategy.unified_strategy import (
    QuantSessionSweepStrategy,
    TradeMetricsAnalyzer,
    ClosedTradeListAnalyzer,
    load_xauusd_dataframe,
)


# ======================================================================
#  LOGGING
# ======================================================================

LOG_LEVEL_MAP: Dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

log = logging.getLogger(__name__)


def _configure_logging(level_name: str, log_dir: str = "logs") -> None:
    """Stream + rotating file handler."""
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL_MAP.get(level_name.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    Path(log_dir).mkdir(exist_ok=True)
    fh = logging.FileHandler(Path(log_dir) / "backtest.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


# ======================================================================
#  BANNER & SUMMARY
# ======================================================================

_BANNER = r"""
+============================================================+
|  XAUUSD QUANT SESSION-SWEEP BACKTEST  v5.0                |
|  Rules: DT/DB | Pin Bar | Engulfing | Multi-Bar Sweep    |
|  Risk:  OCO Bracket | 5M ATR SL | 1:1.5 RR | Daily Limit |
+============================================================+
"""


def _print_banner() -> None:
    print(_BANNER)


def _print_data_summary(
    df: pd.DataFrame,
    cfg: AppConfig,
    symbol: str = "XAUUSD",
) -> None:
    """Print a concise data summary."""
    print("-" * 60)
    print(f"  SYMBOL:            {symbol}")
    print(f"  DATA FILE:         {cfg.data.file_path}")
    print(f"  BARS:              {len(df):,}")
    print(f"  DATE RANGE:        {df.index.min()}  ->  {df.index.max()}")
    print(f"  PRICE RANGE:       {df['low'].min():.2f}  ->  {df['high'].max():.2f}")
    print(f"  SESSIONS:          Asia {cfg.sessions.asia.start}-{cfg.sessions.asia.end}  "
          f"London {cfg.sessions.london.start}-{cfg.sessions.london.end}  "
          f"NY {cfg.sessions.ny.start}-{cfg.sessions.ny.end}")
    print(f"  RISK:              {cfg.risk.risk_per_trade_pct*100:.1f}% per trade  "
          f"5M ATRx{cfg.risk.sl_atr_multiplier}  RR {cfg.risk.tp_rr_ratio}:1")
    print("-" * 60)


def _print_config_overrides(args: argparse.Namespace, cfg: AppConfig) -> None:
    """Print which CLI args override YAML values."""
    overrides: List[str] = []
    if args.log_level != "INFO":
        overrides.append(f"log_level={args.log_level}")
    if args.csv:
        overrides.append(f"csv={args.csv}")
    if args.cash != 5_000.0:
        overrides.append(f"cash={args.cash}")
    if args.commission != 0.00005:
        overrides.append(f"commission={args.commission}")
    if args.trade_start:
        overrides.append(f"trade_start={args.trade_start}")
    if args.trade_end:
        overrides.append(f"trade_end={args.trade_end}")
    if args.no_killzone:
        overrides.append("killzone_filter=OFF")
    if args.daily_loss_limit_pct != -0.04:
        overrides.append(f"daily_loss_limit={args.daily_loss_limit_pct}")
    if args.rr_ratio != 2.0:
        overrides.append(f"rr_ratio={args.rr_ratio}")
    if args.dry_run:
        overrides.append("mode=DRY_RUN")

    if overrides:
        print(f"  CONFIG OVERRIDES:  {', '.join(overrides)}")


# ======================================================================
#  DRY-RUN (validate data + config only, no cerebro)
# ======================================================================

def _dry_run(df: pd.DataFrame, cfg: AppConfig) -> int:
    """
    Validate the data and config without running any backtest.

    Checks performed:
      1. DataFrame non-empty with required OHLC columns
      2. Price coherence (high>=low, open/close within [low,high])
      3. No NaN in price columns
      4. Sessions config parseable
      5. At least 1 day of data for warmup

    Returns exit code 0 on success, 1 on failure.
    """
    print("\n  DRY-RUN VALIDATION")
    print("  " + "-" * 50)
    errors: List[str] = []

    # Check columns
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        errors.append(f"Missing columns: {missing}")

    if df.empty:
        errors.append("DataFrame is empty.")
    else:
        # Price coherence
        bad_high = (df["high"] < df["low"]).sum()
        if bad_high:
            errors.append(f"high < low in {bad_high} rows.")

        bad_open = (~df["open"].between(df["low"], df["high"])).sum()
        if bad_open:
            errors.append(f"open outside [low, high] in {bad_open} rows.")

        bad_close = (~df["close"].between(df["low"], df["high"])).sum()
        if bad_close:
            errors.append(f"close outside [low, high] in {bad_close} rows.")

        # NaN check
        nan_counts = df[["open", "high", "low", "close"]].isna().sum()
        for col, cnt in nan_counts.items():
            if cnt:
                errors.append(f"NaN in '{col}': {cnt} rows.")

        # Warmup check
        if len(df) < 60 * 24:  # less than 1 day of 1M bars
            errors.append(f"Only {len(df)} bars (<1 day); warmup insufficient.")

    # Config check: session times parseable
    try:
        from datetime import time
        time.fromisoformat(cfg.sessions.asia.start)
        time.fromisoformat(cfg.sessions.asia.end)
        time.fromisoformat(cfg.sessions.london.start)
        time.fromisoformat(cfg.sessions.london.end)
        time.fromisoformat(cfg.sessions.ny.start)
        time.fromisoformat(cfg.sessions.ny.end)
    except ValueError as e:
        errors.append(f"Session time parse error: {e}")

    if errors:
        print("  FAILED:")
        for e in errors:
            print(f"    [X] {e}")
        return 1
    else:
        print("  [OK] All validations passed (columns, prices, NaNs, sessions, warmup).")
        print("  [OK] Config is well-formed.")
        print(f"  [OK] {len(df):,} XAUUSD 1M bars ready for backtest.")
        return 0


# ======================================================================
#  CEREBRO BUILDER
# ======================================================================

def _resample_to_feeds(df_1m: pd.DataFrame) -> Dict[str, bt.feeds.PandasData]:
    """
    Resample a 1M OHLCV DataFrame into 5M / 1H / 4H and return
    Backtrader PandasData feeds for all four timeframes.
    """
    ohlc = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

    df_5m = df_1m.resample("5min").apply(ohlc).dropna()
    df_1h = df_1m.resample("1h").apply(ohlc).dropna()
    df_4h = df_1m.resample("4h").apply(ohlc).dropna()

    log.info("Resampled: 5M=%d bars, 1H=%d bars, 4H=%d bars",
             len(df_5m), len(df_1h), len(df_4h))

    return {
        "1m": bt.feeds.PandasData(dataname=df_1m, datetime=None),
        "5m": bt.feeds.PandasData(dataname=df_5m, datetime=None),
        "1h": bt.feeds.PandasData(dataname=df_1h, datetime=None),
        "4h": bt.feeds.PandasData(dataname=df_4h, datetime=None),
    }


def _build_cerebro(
    feeds: Dict[str, bt.feeds.PandasData],
    cfg: AppConfig,
    args: argparse.Namespace,
) -> bt.Cerebro:
    """
    Construct the Cerebro engine with strategy, broker settings, and analyzers.
    """
    cerebro = bt.Cerebro(stdstats=False)

    # Add data feeds
    for name, feed in feeds.items():
        cerebro.adddata(feed, name=name)

    # Parse trade window
    trade_start = None
    trade_end = None
    if args.trade_start:
        trade_start = pd.Timestamp(args.trade_start).to_pydatetime()
    elif cfg.data.trade_start:
        trade_start = pd.Timestamp(cfg.data.trade_start).to_pydatetime()

    if args.trade_end:
        trade_end = pd.Timestamp(args.trade_end).to_pydatetime()
    elif cfg.data.trade_end:
        trade_end = pd.Timestamp(cfg.data.trade_end).to_pydatetime()

    cerebro.addstrategy(
        QuantSessionSweepStrategy,
        # Strategy params with CLI overrides
        atr_period=args.atr_period if args.atr_period != 14 else cfg.strategy.atr_period,
        atr_multiplier=(args.atr_multiplier if args.atr_multiplier != 1.5
                        else cfg.strategy.atr_multiplier),
        risk_pct=args.risk_pct if args.risk_pct != 0.02 else cfg.strategy.risk_pct,
        rr_ratio=args.rr_ratio if args.rr_ratio != 2.0 else cfg.strategy.rr_ratio,
        sweep_expiry_bars=(args.sweep_expiry_bars if args.sweep_expiry_bars != 12
                           else cfg.strategy.sweep_expiry_bars),
        structure_lookback=(args.structure_lookback if args.structure_lookback != 3
                            else cfg.strategy.structure_lookback),
        enable_killzone_filter=(not args.no_killzone if args.no_killzone
                                else cfg.strategy.enable_killzone_filter),
        enable_volume_filter=(args.enable_volume_filter
                              or cfg.strategy.enable_volume_filter),
        enable_trend_filter=(not args.no_trend_filter if args.no_trend_filter
                             else getattr(cfg.strategy, 'enable_trend_filter', True)),
        max_daily_trades=(args.max_daily_trades if args.max_daily_trades != 8
                          else cfg.strategy.max_daily_trades),
        min_bars_between_trades=(args.min_bars_between if args.min_bars_between != 5
                                  else cfg.strategy.min_bars_between_trades),
        max_consecutive_losses=(args.max_consecutive_losses if args.max_consecutive_losses != 1
                                else getattr(cfg.strategy, 'max_consecutive_losses', 1)),
        daily_loss_limit_pct=(args.daily_loss_limit_pct if args.daily_loss_limit_pct != -0.04
                              else cfg.risk.daily_loss_limit_pct),
        trade_start=trade_start,
        trade_end=trade_end,
    )

    # Broker settings — set_checksubmit(False) allows leveraged XAUUSD trading
    # Must be set BEFORE cash to prevent spurious MARGIN rejections on LONG trades
    cerebro.broker.set_checksubmit(False)
    cash = (args.cash if args.cash != 5_000.0 else cfg.execution.initial_cash)
    commission = (args.commission if args.commission != 0.00005
                  else cfg.execution.commission)
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.broker.set_slippage_fixed(
        cfg.execution.slippage_fixed,
        slip_open=True, slip_limit=True, slip_match=True, slip_out=True,
    )

    # Analyzers
    cerebro.addanalyzer(TradeMetricsAnalyzer, _name="metrics")
    cerebro.addanalyzer(ClosedTradeListAnalyzer, _name="closed_trades")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    return cerebro


# ======================================================================
#  RESULTS PRINTER
# ======================================================================

def _print_results(strat: QuantSessionSweepStrategy, cerebro: bt.Cerebro, export_csv: bool = True) -> None:
    """Print final metrics, closed-trade list, drawdown, and returns."""
    metrics = strat.analyzers.metrics.get_analysis()
    closed = strat.analyzers.closed_trades.get_analysis()
    dd = strat.analyzers.drawdown.get_analysis()
    returns = strat.analyzers.returns.get_analysis()

    print("\n" + "=" * 72)
    print("  PERFORMANCE METRICS")
    print("=" * 72)
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    dd_pct = dd.get("max", {}).get("drawdown", 0.0) if isinstance(dd, dict) else dd.max.drawdown
    rtn_pct = (returns.get("rnorm100", 0.0) if isinstance(returns, dict)
               else getattr(returns, "rnorm100", 0.0))
    print(f"  Max Drawdown (%):       {dd_pct:.2f}%")
    print(f"  Total Return (%):       {rtn_pct:.2f}%")
    print(f"  Final Portfolio Value:  {cerebro.broker.getvalue():.2f}")

    if closed:
        print("-" * 72)
        print(f"  CLOSED TRADES ({len(closed)} total)")
        print("-" * 72)
        for i, t in enumerate(closed, 1):
            print(f"  #{i:>2} | {t['open_datetime']} -> {t['close_datetime']} "
                  f"| pnl={t['pnl']:.2f} | bars={t['barlen']}")
    else:
        print("  No closed trades in this run.")

    print("=" * 72)

    if export_csv and strat.trade_metadata:
        _export_trade_details_csv(strat.trade_metadata)


def _export_trade_details_csv(trade_metadata: list) -> None:
    import csv
    from datetime import datetime
    from pathlib import Path

    # Only export completed trades
    completed = [m for m in trade_metadata if m.get("pnl_net") is not None]

    if not completed:
        print("\n  No completed trades to export.\n")
        return

    output_path = Path("trade_details.csv")
    fieldnames = [
        "Trade #", "Open DateTime", "Close DateTime", "Direction",
        "Entry Price", "Exit Price", "SL", "TP", "Size", "ATR", "RR",
        "Reference Session", "Swept Level", "Exit Reason",
        "PnL (Gross)", "PnL (Net)", "Bars", "Result",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for i, meta in enumerate(completed, 1):
            open_dt = meta.get("open_datetime")
            close_dt = meta.get("close_datetime")
            w.writerow({
                "Trade #": i,
                "Open DateTime": open_dt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(open_dt, datetime) else str(open_dt),
                "Close DateTime": close_dt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(close_dt, datetime) else str(close_dt or "N/A"),
                "Direction": meta.get("direction", ""),
                "Entry Price": meta.get("entry_px"),
                "Exit Price": meta.get("exit_px"),
                "SL": meta.get("sl"),
                "TP": meta.get("tp"),
                "Size": meta.get("size"),
                "ATR": meta.get("atr"),
                "RR": meta.get("rr"),
                "Reference Session": meta.get("reference_session", ""),
                "Swept Level": meta.get("swept_level"),
                "Exit Reason": meta.get("exit_reason", ""),
                "PnL (Gross)": meta.get("pnl"),
                "PnL (Net)": meta.get("pnl_net"),
                "Bars": meta.get("barlen"),
                "Result": "WIN" if meta.get("is_win") else "LOSS" if meta.get("is_win") is False else "N/A",
            })
    print(f"\n  Detailed trade data exported to: {output_path.resolve()}")
    print(f"  Total completed trades exported: {len(completed)}")
    print(f"  You can open this CSV in Excel or any third-party platform to review each trade.\n")


# ======================================================================
#  MAIN
# ======================================================================

def main(argv: Optional[List[str]] = None) -> int:
    """
    Entry point.

    Workflow:
      1. Parse CLI args.
      2. Load YAML config (exit code 2 on failure).
      3. Configure logging.
      4. Load XAUUSD 1M CSV (exit code 1 on failure).
      5. Print banner, summary, overrides, run command.
      6. If --dry-run -> validate & exit (0 or 1).
      7. Build Cerebro + run backtest.
      8. Print results.
      9. Return 0 on success, 3 on Cerebro failure.
    """
    # -- 1. CLI ------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="XAUUSD Quant Session-Sweep Backtest v4.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="configs/config_30d.yaml",
                        help="YAML config file (default: configs/config_30d.yaml)")
    parser.add_argument("--csv", default=None,
                        help="Path to 1M OHLCV CSV (overrides config data.file_path)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate data + config only; do not run cerebro.")

    # Broker / exec overrides
    parser.add_argument("--cash", type=float, default=5_000.0)
    parser.add_argument("--commission", type=float, default=0.00005)
    parser.add_argument("--trade-start", default=None)
    parser.add_argument("--trade-end", default=None)

    # Strategy param overrides
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--atr-multiplier", type=float, default=1.5)
    parser.add_argument("--risk-pct", type=float, default=0.02)
    parser.add_argument("--rr-ratio", type=float, default=2.0)
    parser.add_argument("--sweep-expiry-bars", type=int, default=12)
    parser.add_argument("--structure-lookback", type=int, default=3)
    parser.add_argument("--no-killzone", action="store_true")
    parser.add_argument("--enable-volume-filter", action="store_true")
    parser.add_argument("--max-daily-trades", type=int, default=8)
    parser.add_argument("--min-bars-between", type=int, default=5)
    parser.add_argument("--no-trend-filter", action="store_true")
    parser.add_argument("--max-consecutive-losses", type=int, default=1)
    parser.add_argument("--daily-loss-limit-pct", type=float, default=-0.04)

    args = parser.parse_args(argv)

    # -- 2. Config --------------------------------------------------
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print(f"FATAL: Config file not found: {args.config}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"FATAL: Config parse error: {e}", file=sys.stderr)
        return 2

    # -- 3. Logging -------------------------------------------------
    log_level = args.log_level or cfg.logging.level
    _configure_logging(log_level, cfg.logging.directory)

    _print_banner()

    # -- 4. Data ----------------------------------------------------
    csv_path = args.csv or cfg.data.file_path
    dp = Path(csv_path)
    if not dp.is_absolute():
        dp = Path(__file__).resolve().parent / dp

    try:
        df_1m = load_xauusd_dataframe(dp)
    except FileNotFoundError:
        log.error("Data file not found: %s", dp)
        return 1
    except Exception as e:
        log.error("Data load failed: %s", e)
        return 1

    if df_1m is None or df_1m.empty:
        log.error("Data is empty after load.")
        return 1

    # -- 5. Summary -------------------------------------------------
    _print_data_summary(df_1m, cfg)
    _print_config_overrides(args, cfg)
    print(f"  RUN COMMAND:  python backtest.py --config {args.config} "
          f"--log-level {log_level}" + (" --dry-run" if args.dry_run else ""))
    print()

    # -- 6. Dry-run -------------------------------------------------
    if args.dry_run:
        return _dry_run(df_1m, cfg)

    # -- 7. Cerebro -------------------------------------------------
    log.info("Building Cerebro engine ...")
    try:
        feeds = _resample_to_feeds(df_1m)
        cerebro = _build_cerebro(feeds, cfg, args)
    except Exception as e:
        log.error("Cerebro build failed: %s", e, exc_info=True)
        return 3

    starting_value = cerebro.broker.getvalue()
    log.info("Starting Portfolio: %.2f", starting_value)

    try:
        results = cerebro.run()
    except Exception as e:
        log.error("Backtest run failed: %s", e, exc_info=True)
        return 3

    if not results:
        log.warning("Cerebro returned no results (empty strategy list).")
        return 3

    # -- 8. Results -------------------------------------------------
    strat = results[0]
    _print_results(strat, cerebro)

    # -- 9. Cleanup -------------------------------------------------
    log.info("Backtest complete. Final value: %.2f", cerebro.broker.getvalue())
    return 0


# ======================================================================
#  ENTRY POINT
# ======================================================================

if __name__ == "__main__":
    sys.exit(main())
