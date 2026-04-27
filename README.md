# XAUUSD Quant Session-Sweep Backtesting Strategy

> **Production Architecture v5.0** — A modular, event-driven backtesting engine for the XAUUSD (Gold) session-liquidity sweep day-trading strategy. Pure candlestick-based confirmation — no trend filter, no MSS, no FVG. Single-take-profit OCO brackets with 5M ATR-based stop placement.

---

## 🧠 Strategy Philosophy (The "Smart Money" Edge)

The strategy exploits **institutional liquidity sweeps** during key forex sessions. It operates on the principle that large players hunt stops above/below prior session ranges before reversing into their intended direction:

| Step | Rule | Description |
|------|------|-------------|
| 1 | **Session Liquidity** | London watches Asia's range. NY watches London's range (falls back to Asia if untouched). |
| 2 | **Level Identification** | Cache the prior session's high/low as key liquidity zones. |
| 3 | **Sweep Detection** | On 1M candles: price **breaks** a session high/low (wick) → then **closes back inside** the level on a subsequent candle. Confirmation can span multiple 1M bars. |
| 4 | **Confirmation** | Pure candlestick reversal triggers: Double Top / Double Bottom, Pin Bar (wick ≫ body), Engulfing candle. A single valid pattern confirms. |
| 5 | **Risk Execution** | Fixed 2% risk per trade, 5M ATR × 1.5 for stop placement, single take-profit at 1:1.5 reward-to-risk. |

### Core Edge Concepts

- **DT/DB (Double Top / Double Bottom):** Two swing highs or lows matching within ±0.5% tolerance, confirmed by volume > SMA20 filter. A classic reversal structure that exploits trapped traders.
- **Pin Bar (Wick > Body):** A candle where the wick dominates the body (wick ≥ 50% of total range, body ≤ 35%). Bullish pin bar = long lower wick (hammer); bearish pin bar = long upper wick (shooting star). Represents aggressive rejection of a price level.
- **Engulfing Candle:** A candle whose body fully engulfs the prior candle's body in the sweep direction. Bullish engulfing = green body covers prior red; bearish engulfing = red body covers prior green. Signals strong momentum shift.

---

## 📁 Project Structure

```
.
├── README.md                          # ← This file
├── backtest.py                        # Main Backtrader strategy (Production v5.0) — Cerebro pipeline entry point
├── backtest_run.txt                   # Console output from a historical backtest run
├── main.py                            # Live / paper-trading demo loop (simplified pipeline)
├── fetch_xauusd.py                    # Script to download XAUUSD 1M price data
├── merge_data.py                      # Merges multiple ASCII price files into one CSV
├── prepare_data.py                    # Pre-processing / resampling utilities
├── fix_config.py / fix_sessions_config.py  # Config repair helpers
├── mock_1m.csv                        # Mock 1M price data for unit tests
├── xauusd_1m.csv                      # Real XAUUSD 1M price data (Jan–Apr 2026)
├── requirements.txt
├── Quant.pdf                          # Original strategy playbook (detailed rules)
│
├── configs/
│   └── config_30d.yaml                # 30-day backtest config (risk, sessions, strategy params)
│
├── src/
│   ├── engine/
│   │   ├── session_liquidity.py       # Session range tracking + multi-bar sweep detection (bullish/bearish)
│   │   ├── pattern_engine.py          # Pattern detectors (DT/DB, Pin Bar, Engulfing) + ConfirmationValidator
│   │   ├── pattern_structure.py       # Additional pattern detection utilities
│   │   └── risk_execution.py          # ATR-based SL/TP calculation + position sizing
│   │
│   ├── strategy/
│   │   └── unified_strategy.py        # QuantSessionSweepStrategy (Backtrader Strategy) — the full pipeline
│   │
│   ├── validator/
│   │   └── checklist.py               # Pydantic pre-trade checklist (5-gate validation model)
│   │
│   ├── filters/
│   │   └── macro_news.py              # High-impact news blackout filter
│   │
│   ├── data/
│   │   ├── data_fetcher.py            # MT5/CSV multi-timeframe data interface
│   │   └── session_handler.py         # Session tagging (Asia/London/NY/Overlap), high/low caching
│   │
│   ├── backtest/
│   │   └── backtester.py              # Simplified balance-tracker / metrics calculator
│   │
│   └── utils/
│       ├── config.py                   # Pydantic config models + YAML loader
│       └── logger.py                   # Logging setup
│
├── tests/
│   ├── test_checklist.py             # Unit test: TradeChecklist validation
│   ├── test_risk.py                  # Unit test: position sizing formula
│   └── test_sweep.py                 # Unit test: sweep detection
│
└── Price1m/                          # Raw ASCII price data files (multi-month)
    ├── DAT_ASCII_XAUUSD_M1_202601.txt
    ├── DAT_ASCII_XAUUSD_M1_202602.txt
    ├── DAT_ASCII_XAUUSD_M1_202603.txt
    └── DAT_ASCII_XAUUSD_M1_202604.txt
```

---

## 🏗️ Architecture Overview

### Two Execution Paths

| Path | File | Description |
|------|------|-------------|
| **Production Backtest** | `backtest.py` | Full Backtrader Cerebro engine with QuantSessionSweepStrategy — 7-gate checklist pipeline, OCO bracket orders, daily loss circuit breaker |
| **Demo / Paper Pipeline** | `main.py` | Simplified single-iteration loop using `src/` modules — great for understanding signal flow or paper trading via MT5/CCXT |

### Key Engine Modules

```
QuantSessionSweepStrategy (Backtrader Strategy)  ←─ src/strategy/unified_strategy.py
│
├── SessionLiquidityTracker      ←─ src/engine/session_liquidity.py
│   └── Tracks Asia/London session ranges, detects multi-bar 1M sweeps
│
├── ConfirmationValidator        ←─ src/engine/pattern_engine.py
│   ├── PatternDetectors (static: pin_bar, engulfing, dt_db)
│   └── Single valid pattern confirms sweep (no hierarchy)
│
├── TradeChecklist               ←─ src/validator/checklist.py
│   └── Pydantic model for 5-field validation
│
└── Risk / Bracket Execution     ←─ built into strategy (_build_and_submit_bracket)
    └── 5M ATR-based SL, single TP at 1:1.5 RR

---

## 🔧 Configuration (`configs/config_30d.yaml`)

```yaml
data:
  file_path: xauusd_1m.csv
  warmup_days: 15
  trade_start: "2026-03-11"
  trade_end: "2026-04-10"

execution:
  commission: 0.0002         # 0.02%
  initial_cash: 100000.0     # Starting equity
  slippage_fixed: 0.05       # Fixed slippage in pips
  spread_buffer: 0.1         # Spread allowance

risk:
  risk_pct: 0.02             # 2% risk per trade
  atr_multiplier: 1.5        # SL = 5M ATR × multiplier
  rr_ratio: 1.5              # TP = SL × RR ratio

sessions:
  asia_start: 0   asia_end: 7
  london_start: 7 london_end: 12
  ny_start: 12    ny_end: 21
  london_open_start: 7   london_open_end: 16
  ny_am_start: 12         ny_am_end: 21
  entry_restrict_killzones: true

strategy:
  atr_period: 14
  atr_multiplier: 1.5
  risk_pct: 0.02
  rr_ratio: 1.5
  structure_lookback: 3       # Bars for swing detection (DT/DB)
  sweep_expiry_bars: 12       # Sweep signal expires after N 5M bars
  max_daily_trades: 8
  min_bars_between_trades: 5
  enable_killzone_filter: true
  enable_volume_filter: false
```

### Key Parameters You Can Tune

| Parameter | Effect | Suggested Range |
|-----------|--------|-----------------|
| `atr_multiplier` | Stop-loss width (on 5M ATR); higher = wider SL, fewer stop-outs | 1.0 – 2.5 |
| `rr_ratio` | Take-profit ratio; higher = larger wins but lower hit rate | 1.0 – 2.0 |
| `risk_pct` | % of equity risked per trade | 0.5% – 3% |
| `structure_lookback` | Bars used to detect swing points (DT/DB) | 2 – 5 |
| `sweep_expiry_bars` | Max 5M bars before a sweep breach becomes stale | 6 – 24 |
| `max_daily_trades` | Cap to prevent overtrading | 3 – 12 |
| `warmup_days` | Days of data used for indicator warmup only | 15 – 60 |
| `enable_killzone_filter` | Only trade during London open & NY AM killzones | true/false |

---

## 📊 Signal Pipeline (How a Trade Fires)

```
┌─────────────────────────────────────────────────┐
│  STEP 1: Session Tagging                         │
│  ─────────────────────────────────────────────── │
│  Current UTC hour → Asia / London / NY / Overlap │
│  Cache prior session's high/low as liquidity     │
└───────────────┬─────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────┐
│  STEP 2: Macro / News Gate                       │
│  ─────────────────────────────────────────────── │
│  - High-impact news blackout (±30min window)     │
│  - Killzone restriction filter (optional)         │
└───────────────┬─────────────────────────────────┘
                │ (if clear)
┌───────────────▼─────────────────────────────────┐
│  STEP 3: Multi-Bar Sweep Detection (on 1M)       │
│  ─────────────────────────────────────────────── │
│  Bearish Sweep: wick > session_high on bar N     │
│                 THEN close < session_high on     │
│                 bar N+1, N+2, ... (multi-bar)    │
│  Bullish Sweep: wick < session_low on bar N      │
│                 THEN close > session_low on      │
│                 bar N+1, N+2, ... (multi-bar)    │
└───────────────┬─────────────────────────────────┘
                │ (if sweep confirmed)
┌───────────────▼─────────────────────────────────┐
│  STEP 4: Candlestick Confirmation (on 5M or 1M)  │
│  ─────────────────────────────────────────────── │
│  Any single valid pattern confirms:              │
│    ✅ Double Top / Double Bottom                  │
│    ✅ Pin Bar (wick ≫ body, rejection candle)    │
│    ✅ Engulfing Candle                            │
│  (ATR displacement fallback available)            │
└───────────────┬─────────────────────────────────┘
                │ (if confirmed)
┌───────────────▼─────────────────────────────────┐
│  STEP 5: Pre-Trade Checklist                     │
│  ─────────────────────────────────────────────── │
│  ☑ session_valid     (London, NY, or Overlap)    │
│  ☑ news_clear         (no blackout active)       │
│  ☑ sweep_confirmed    (multi-bar sweep detected) │
│  ☑ pattern_confirmed  (≥ 1 candlestick pattern)  │
│  ☑ risk_valid         (SL/TP within limits)      │
└───────────────┬─────────────────────────────────┘
                │ (all 5 pass)
┌───────────────▼─────────────────────────────────┐
│  STEP 6: Risk & Execution                        │
│  ─────────────────────────────────────────────── │
│  SL = 5M ATR(14) × 1.5                           │
│  Single TP @ 1:1.5 RR                            │
│  Position size = (Equity × 2%) / SL distance      │
│  OCO bracket: parent + stop + limit               │
└───────────────┬─────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────┐
│  STEP 7: Circuit Breaker                         │
│  ─────────────────────────────────────────────── │
│  If daily PnL < -4% of AM equity → halt entries  │
│  Reset at 00:00 UTC each day                     │
└─────────────────────────────────────────────────┘
```

---

## 🚀 Running the Backtest

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Key dependencies: `backtrader`, `pandas`, `numpy`, `pydantic`, `pyyaml`, `requests`

### 2. Prepare Data

Ensure `xauusd_1m.csv` exists with columns: `datetime, open, high, low, close, volume`

### 3. Run Production Backtest

```bash
python backtest.py --config configs/config_30d.yaml --log-level INFO
```

This uses the full Backtrader engine and prints metrics to console + `logs/backtest.log`.

### 4. Run Simplified Demo

```bash
python main.py
```

This runs a single iteration through the `src/` pipeline (for debugging / paper trading).

### 5. Run Unit Tests

```bash
python -m pytest tests/ -v
```

---

## 📈 Sample Backtest Results (from `backtest_run.txt`)

```
==================================================
XAUUSD SMART MONEY STRATEGY BACKTEST
==================================================
Data file:  xauusd_1m.csv
Range:      2026-01-01 → 2026-04-10
Price span: 4098.27 → 5596.81
--------------------------------------------------
Starting Portfolio Value: 100000.00
Final Portfolio Value:    99480.38
==================================================
Performance Metrics
==================================================
  Total Trades:       3
  Wins:               1
  Losses:             2
  Win Rate (%):       33.33
  Profit Factor:      0.38
  Expectancy ($):     -173.21
  Max Drawdown (%):   0.86

Closed Trades
==================================================
  #1 | 2026-02-20 10:11 | bars=4  | PnL= +318.44
  #2 | 2026-03-02 09:41 | bars=11 | PnL= -517.85
  #3 | 2026-03-31 07:31 | bars=8  | PnL= -320.22
```

> ⚠️ The above sample had only 3 trades over 3 months due to restrictive filter settings (v3.0 engine). The multi-bar sweep detection in v5.0, combined with simplified candlestick-only confirmation and removal of the trend filter, is expected to produce more frequent, higher-quality entries.

---

## 🧪 How to Edit & Tune the Strategy

### Quick Wins (Change These First)

| File | What to Change | Why |
|------|---------------|-----|
| `configs/config_30d.yaml` | `risk_pct: 0.015` | Reduce risk if drawdowns are too high |
| `configs/config_30d.yaml` | `atr_multiplier: 2.0` | Widen stops to reduce premature exits |
| `configs/config_30d.yaml` | `rr_ratio: 2.0` | Increase reward-to-risk for higher expectancy |
| `configs/config_30d.yaml` | `sweep_expiry_bars: 18` | Give sweep breaches more time to confirm |
| `src/strategy/unified_strategy.py` | `_calculate_position_size()` | Custom position sizing logic |
| `src/engine/pattern_engine.py` | `detect_dt_db()` | Tune DT/DB swing matching tolerance |

### Deeper Customizations

| Task | File | Change |
|------|------|--------|
| Add a new session (e.g., Tokyo) | `src/engine/session_liquidity.py` | Add time constants + update `_classify_session()` logic |
| Change confirmation requirements | `src/engine/pattern_engine.py` | Change `min_confirmation_signals` in `PatternConfig` |
| Add a new pattern detector | `src/engine/pattern_engine.py` | Add static method to `PatternDetectors` + register in `ConfirmationValidator.evaluate()` |
| Hook up real MT5/CCXT trading | `main.py` | Replace `risk_engine.execute_trade()` with real order placement |
| Integrate Optuna for hyperparameter optimization | `backtest.py` | Wrap `cerebro.run()` inside an Optuna objective function |
| Add walk-forward analysis | `backtest.py` | Loop over rolling date windows with in-sample/out-of-sample splits |

### Adding a New Pattern Detector Example

```python
# In src/engine/pattern_engine.py, add to PatternDetectors:
@staticmethod
def detect_my_pattern(df: pd.DataFrame, config: PatternConfig,
                      sweep_direction: str, bar_index: int,
                      volume_series: Optional[pd.Series] = None) -> Optional[Signal]:
    """Detect your custom pattern aligned with the sweep direction."""
    if len(df) < 3:
        return None
    # ... your logic ...
    return Signal(name=f"{sweep_direction}_my_pattern", category="candlestick", bar_index=bar_index)
```

Then register it in `ConfirmationValidator.evaluate()` by adding it to the `detectors` list.

---

## 🧩 Pre-Trade Checklist Components

The `TradeChecklist` Pydantic model in `src/validator/checklist.py` validates 5 gates:

```python
checklist = TradeChecklist(
    session_valid     = True,   # Must be London, NY, or Overlap
    news_clear        = True,   # No high-impact news within ±30 min
    sweep_confirmed   = True,   # Multi-bar level sweep detected (bullish or bearish)
    pattern_confirmed = True,   # ≥ 1 candlestick pattern signal present
    risk_valid        = True,   # Position size > 0 and within limits
    direction         = "long"  # Optional: stores signal direction
)

if checklist.is_valid():
    # Execute trade
```

To **disable a check** (e.g., for testing), simply set its value to `True` in the calling code. To **add a new check**, add a field to the model and update `is_valid()`.

---

## 📚 Strategy Rules Reference

The strategy implements these core rules:

| Rule | Name | Implementation |
|------|------|----------------|
| 1 | **DT/DB** | `PatternDetectors.detect_dt_db()` in [`src/engine/pattern_engine.py`](file:///c:/Users/hp/OneDrive/Desktop/QuantTrading/src/engine/pattern_engine.py) — swing matching ±0.5% + volume filter |
| 2 | **Pin Bar** | `PatternDetectors.detect_pin_bar()` in [`src/engine/pattern_engine.py`](file:///c:/Users/hp/OneDrive/Desktop/QuantTrading/src/engine/pattern_engine.py) — wick ≥ 50% total range, body ≤ 35% |
| 3 | **Engulfing** | `PatternDetectors.detect_engulfing()` in [`src/engine/pattern_engine.py`](file:///c:/Users/hp/OneDrive/Desktop/QuantTrading/src/engine/pattern_engine.py) — body fully covers prior candle body |
| 4 | **Sweep Detection** | `SessionLiquidityTracker.detect_sweep()` in [`src/engine/session_liquidity.py`](file:///c:/Users/hp/OneDrive/Desktop/QuantTrading/src/engine/session_liquidity.py) — multi-bar wick breach + later close-back |
| 5 | **Daily Loss Limit** | `_daily_circuit_breaker_ok()` in [`src/strategy/unified_strategy.py`](file:///c:/Users/hp/OneDrive/Desktop/QuantTrading/src/strategy/unified_strategy.py) — halt if daily PnL < -4%, reset at 00:00 UTC |

### What Was Removed (v4.0 → v5.0)

| Removed | Reason |
|---------|--------|
| **MSS (Market Structure Shift)** | Too slow for day trading; replaced by simple candlestick patterns |
| **FVG (Fair Value Gap)** | Over-engineered for 1M entry; candlestick rejection signals suffice |
| **4H EMA200 Trend Filter** | Day-trading positions are too short for HTF trend alignment to matter |
| **1H Structure Analysis** | Removed along with the trend filter |
| **Confirmation Hierarchy (≥2 signals)** | A single valid candlestick pattern is sufficient to confirm |
| **Partial TP (50% at 1:1 RR)** | Simplified to single take-profit at 1:1.5 RR for cleaner execution |

---

## 🔍 Debugging Tips

- **No trades firing?** Check `sweep_expiry_bars`, `enable_killzone_filter`, and session times — the filters are conservative by design.
- **Too many trades?** Reduce `max_daily_trades`, increase `min_bars_between_trades`, or tighten confirmation criteria (set `min_confirmation_signals` to 2).
- **Poor win rate?** The strategy works best in trending markets with clear liquidity sweeps. Sideways/choppy ranges around news events hurt performance — enable the news filter.
- **Sweeps not detecting?** The multi-bar confirmation means the wick breach and the close-back can happen on different 1M candles. If sweeps still seem too rare, reduce `sweep_expiry_bars` to allow faster expiry and re-entry.

---

*Last updated: April 2026*
