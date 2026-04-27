# XAUUSD Session Liquidity Sweep Strategy

## Overview

**Strategy:** Asia session liquidity sweep fade during London/NY hours  
**Instrument:** XAUUSD (Gold vs USD, 1-minute data)  
**Direction:** Long (Asia low sweeps) and Short (Asia high sweeps)  
**Risk/Reward:** 1:2 (5M ATR-based stop, 2x ATR target)  
**Backtest Engine:** Backtrader (Python)

---

## Performance (2025 Full-Year Backtest)

| Metric | Value |
|---|---|
| Total Trades | 67 (5.6/month) |
| Win Rate | **50.75%** |
| Profit Factor | **3.06** |
| Net Profit | **+$23,804** (on $50K capital) |
| Max Drawdown | 6.72% |
| Total Return | +48% |
| Avg Win/Loss Ratio | 2.3:1 |

### Directional Breakdown

| Direction | Trades | Win Rate | Net PnL |
|---|---|---|---|
| LONG (Asia low sweeps) | 49 | 45% | +$1,224 |
| SHORT (Asia high sweeps) | 18 | **67%** | +$22,580 |

### Quarterly Consistency

| Quarter | Trades | Win Rate | PnL |
|---|---|---|---|
| Q1 (Jan–Mar) | 13 | 54% | +$574 |
| Q2 (Apr–Jun) | 18 | 50% | +$3,144 |
| Q3 (Jul–Sep) | 16 | 50% | +$6,006 |
| Q4 (Oct–Dec) | 20 | 50% | +$14,080 |

---

## Core Logic

```
Pipeline: CB → Killzone → Cooldown → LossGuard → Sweep → Asia-Only → Trend → BB/EMA13 → Pattern → Execute
```

### Checklist Gates (Sequential — first fail aborts)

1. **Daily Circuit Breaker** — Halt if daily PnL < -4% of starting equity
2. **Kill Zone (10:00–16:00 UTC)** — Only trade during London mid-AM through NY overlap
3. **Cooldown / Daily Limit** — Max 8 trades/day, min 5 bars between entries
4. **Loss Streak Guard** — Halt for rest of day after 3 consecutive losses
5. **Sweep Detection** — SessionLiquidityTracker detects active Asia high/low sweeps
6. **Asia-Only Reference** — Reject London-referenced sweeps (only Asia session levels)
7. **4H Trend Filter** — 4H close vs EMA50: uptrend = LONG only, downtrend = SHORT only
8. **BB + EMA13 (soft)** — Reject only extreme BB positions (top 15% for LONG, bottom 15% for SHORT)
9. **Pattern Confirmation** — Require ≥1 of: DT/DB, Pin Bar, or Engulfing candle on BOTH sides

### Entry & Exit

- **Entry:** Market order on sweep confirmation bar
- **Stop-Loss:** 1.5× 5M ATR from entry
- **Take-Profit:** 2.0× stop distance (OCO bracket with stop)
- **Position Size:** 2% risk per trade

---

## How It Works

### The Asia Sweep Pattern

1. During Asia session (00:00–07:00 UTC), XAUUSD forms a range with clear high and low
2. In London mid-morning (10:00–12:00 UTC) or NY overlap (12:00–16:00 UTC):
   - Price pushes beyond the Asia high (sweeping sell stops above)
   - OR price pushes below the Asia low (sweeping buy stops below)
3. After the sweep, price reverses back inside the Asia range — this is the fade entry
4. The reversal runs to a 1:2 RR target based on 5M ATR

### Why 10:00 UTC (Not 09:00)

The first hour of London (09:00–10:00 UTC) produces false breakouts — XAUUSD is still finding direction. By 10:00, the real institutional direction is established and sweeps have higher follow-through. 09:00-only trades achieved only ~20% WR across 1 year of testing.

### Why Asia Sweeps (Not London Sweeps)

London-referenced sweeps during NY overlap perform poorly (~14% WR) because NY momentum overwhelms London levels. Asia sweeps represent overnight liquidity that London institutions deliberately target and fade with discipline.

---

## Market Regime Behavior

- **Strong Bull Market ($2,600→$4,500 in 2025):** SHORT dominates at 67% WR — catching counter-trend pullbacks with precision via the 4H trend filter
- **LONG in bull market:** 45% WR — pattern-confirmed Asia low sweeps work but don't always hit the 1:2 target in a one-directional rally
- **Flat/Ranging:** Not yet tested over an extended ranging period

---

## Code Files

| File | Purpose |
|---|---|
| `src/strategy/unified_strategy.py` | Full strategy logic (QuantSessionSweepStrategy) |
| `src/engine/session_liquidity.py` | SessionLiquidityTracker — session range building + sweep detection |
| `src/engine/pattern_engine.py` | PatternDetectors + ConfirmationValidator (DT/DB, Pin Bar, Engulfing) |
| `backtest.py` | CLI runner — loads CSV data, builds Backtrader cerebro, runs backtest |
| `configs/config_30d.yaml` | Strategy parameters (kill zone, ATR, risk, etc.) |

### Key Parameters in `unified_strategy.py`

```python
params = (
    ("atr_period", 14),            # ATR lookback on 5M
    ("atr_multiplier", 1.5),       # Stop distance multiplier
    ("risk_pct", 0.02),            # 2% risk per trade
    ("rr_ratio", 2.0),             # 1:2 risk/reward
    ("sweep_expiry_bars", 12),     # Sweep expiry in 5M bars
    ("structure_lookback", 3),     # Swing point lookback for DT/DB
    ("enable_killzone_filter", True),
    ("enable_volume_filter", False),
    ("enable_trend_filter", True),
    ("ema_period", 200),
    ("enable_pattern_confirm", True),
    ("min_confirmation_signals", 2),
    ("max_consecutive_losses", 3),
    ("max_daily_trades", 8),
    ("min_bars_between_trades", 5),
    ("daily_loss_limit_pct", -0.04),
)
```

---

## Running the Backtest

```bash
# 3.3 months (2026 data)
python backtest.py --config configs/config_30d.yaml --csv xauusd_1m_realvol.csv --log-level ERROR

# Full 2025
python backtest.py --config configs/config_30d.yaml --csv xauusd_1m_2025.csv --log-level ERROR

# With overrides
python backtest.py --config configs/config_30d.yaml --csv xauusd_1m_2025.csv \
    --atr-multiplier 2.0 --max-consecutive-losses 1 --log-level INFO
```

### Data Sources

- `xauusd_1m_realvol.csv` — Jan–Apr 2026 with generated volume (96K bars)
- `xauusd_1m_2025.csv` — Full 2025 from MT5 export (354K bars)
- `DAT_MT_XAUUSD_M1_2025.csv` — Raw MT5 format (converted to `xauusd_1m_2025.csv`)

To add more data:
1. Download monthly M1 CSVs from [histdata.com](http://www.histdata.com) → XAUUSD → 1-minute
2. Place in `Price1m/` folder
3. Run `python merge_data.py`
4. Run the backtest command above

---

## Live Trading Preparations

For forward-testing before live capital:

1. **TradingView Bar Replay** — Manually replay historical data bar-by-bar, mark Asia H/L, and paper trade sweeps
2. **Soft4FX for MT5** — Installs in MT5, replays tick data at any speed for manual execution
3. **FXReplay** — Web-based bar replay with trade logging and stats

---

## Known Limitations

- **5.6 trades/month** — Low frequency by design; not suitable for traders wanting multiple daily setups
- **Untested in ranges** — 2025 was a strong bull trend; extended sideways markets may reduce WR
- **Synthetic volume** — MT5 export had all-zero tick volume; generated volume is approximate
- **No news filter active** — Major economic releases (FOMC, NFP) can cause sweep failures
- **Pattern engine** — DT/DB detection is simple swing-point based; more sophisticated Order Block detection could improve LONG WR
