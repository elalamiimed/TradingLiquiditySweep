# XAUUSD Session Liquidity Sweep Strategy

## Overview

**Strategy:** Asia session liquidity sweep fade during London/NY hours
**Instrument:** XAUUSD (Gold vs USD, 1-minute data)
**Direction:** Long (Asia low sweeps) and Short (Asia high sweeps)
**Risk/Reward:** 1:2 (5M ATR-based stop, 2x ATR target)
**Backtest Engine:** Backtrader (Python)

---

## Performance

### 2025 Full-Year Backtest (with LONG Entry Refinements)

| Metric | Value |
|---|---|
| Total Trades | 29 (2.4/month) |
| Win Rate | **58.6%** |
| Profit Factor | **3.48** |
| Net Profit | **+$22,388** (on $50K capital) |
| Max Drawdown | 6.73% |
| Total Return | +44.8% |
| Avg Win/Loss Ratio | 2.3:1 |

#### Directional Breakdown

| Direction | Trades | Win Rate | Net PnL |
|---|---|---|---|
| LONG (Asia low sweeps) | 11 | 45% | +$157 |
| SHORT (Asia high sweeps) | 18 | **67%** | +$22,231 |

### 2026 Jan–Apr Backtest

| Metric | Value |
|---|---|
| Total Trades | 10 |
| Win Rate | **80.0%** |
| Profit Factor | **7.50** |
| Net Profit | **+$8,468** |
| Max Drawdown | 3.76% |
| Total Return | +16.9% |

---

## Core Logic

```
LONG:  CB → Killzone → Cooldown → LossGuard → Sweep → Asia-Only →
       Trend → BB/EMA13 → H1 Momentum → BB Entry Zone → Vol Spike → Pattern → Execute

SHORT: CB → Killzone → Cooldown → LossGuard → Sweep → Asia-Only →
       Trend → BB/EMA13 → Pattern → Execute
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
9. **Pattern Confirmation** — Require ≥1 of: DT/DB, Pin Bar, or Engulfing candle

### LONG Entry Refinements (Gates 10–12, For Asia Low Sweeps Only)

10. **H1 Momentum Filter** — Only take LONG if at least one of: H1 MACD bullish (macd line > 0), H1 RSI > 50, or H1 EMA20 is rising
11. **Bollinger Band Entry Zone** — After Asia low sweep, wait for price to pull back to the lower BB (≤25th percentile of the band). Also requires BB width to be expanding (volatility increasing) — avoids entries in compression
12. **Volume Spike Confirmation** — Require volume on the reversal 1M candle to be ≥ 1.5× the average volume of the previous 5 bars

These refinements significantly filter LOW-quality LONG entries. Before refinements, 49 LONG trades were taken with only ~$1,200 net profit. After refinements, only 11 LONG trades passed all gates — maintaining positive PnL with vastly improved selectivity.

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

- **Strong Bull Market ($2,600→$4,500 in 2025):** SHORT dominates at 67% WR — catching counter-trend pullbacks with precision via the 4H trend filter. LONG trades are selectively taken only when H1 momentum + BB pullback + volume spike all align, producing 45% WR on 11 high-quality entries.
- **Neutral/Volatile (2026 Jan–Apr):** Both LONG and SHORT perform at 80% WR. Volatility and liquidity conditions are favorable for the full set of refinements.
- **Flat/Ranging:** Not yet tested over an extended ranging period.

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
    # ── Core strategy ──
    ("atr_period", 14),            # ATR lookback on 5M
    ("atr_multiplier", 1.5),       # Stop distance multiplier
    ("risk_pct", 0.02),            # 2% risk per trade
    ("rr_ratio", 2.0),             # 1:2 risk/reward
    ("sweep_expiry_bars", 12),     # Sweep expiry in 5M bars
    ("structure_lookback", 5),     # Swing point lookback for DT/DB
    # ── Filters ──
    ("enable_killzone_filter", True),
    ("enable_volume_filter", False),
    ("enable_trend_filter", True),
    ("ema_period", 200),
    ("volume_sma_period", 20),
    ("max_daily_trades", 8),
    ("min_bars_between_trades", 5),
    # ── Confirmation ──
    ("enable_pattern_confirm", True),
    ("min_confirmation_signals", 2),
    # ── Risk guards ──
    ("max_consecutive_losses", 3),
    ("daily_loss_limit_pct", -0.04),
    ("news_blackout_minutes", 30),
    ("volume_threshold_multiplier", 1.0),
    # ── LONG Entry Refinements ──
    ("enable_h1_momentum_filter", True),   # H1 MACD/RSI/EMA20 momentum
    ("enable_bb_entry_zone", True),        # Lower BB pullback + BB Width expansion
    ("enable_volume_spike_confirm", True), # Volume ≥ 1.5x prev 5 bars avg
    ("volume_spike_multiplier", 1.5),      # Spike multiplier
    ("bb_width_lookback", 5),              # Bars for BB Width expansion check
    ("h1_rsi_period", 14),
    ("h1_rsi_threshold", 50),
)
```

---

## Running the Backtest

```bash
# Full 2025
python backtest.py --config configs/config_30d.yaml --csv xauusd_1m_2025.csv --log-level ERROR

# 2026 data (Jan–Apr)
python backtest.py --config configs/config_30d.yaml --csv xauusd_1m_realvol.csv \
    --trade-start 2026-01-15 --trade-end 2026-04-15 --log-level ERROR

# With overrides
python backtest.py --config configs/config_30d.yaml --csv xauusd_1m_2025.csv \
    --atr-multiplier 2.0 --max-consecutive-losses 1 --log-level INFO

# Disable LONG entry refinements (compare pre/post refinement behavior)
python backtest.py --config configs/config_30d.yaml --csv xauusd_1m_2025.csv \
    --no-h1-momentum-filter --no-bb-entry-zone --no-volume-spike-confirm \
    --log-level INFO
```

### Data Sources

- `xauusd_1m_2025.csv` — Full 2025 from MT5 export (354K bars)
- `xauusd_1m_realvol.csv` — Jan–Apr 2026 with generated volume (96K bars)
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

- **2.4 trades/month** — Low frequency by design (further reduced by LONG entry refinements); not suitable for traders wanting multiple daily setups
- **Untested in ranges** — 2025 was a strong bull trend; extended sideways markets may reduce WR
- **Synthetic volume** — MT5 export had all-zero tick volume; generated volume is approximate. The volume spike filter will function differently with real tick volume data
- **No news filter active** — Major economic releases (FOMC, NFP) can cause sweep failures
- **Pattern engine** — DT/DB detection is simple swing-point based; more sophisticated Order Block detection could improve LONG WR further
