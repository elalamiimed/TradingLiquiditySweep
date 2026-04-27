"""Fetch XAUUSD M1 data from MT5 with real tick volumes - v2."""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

if not mt5.initialize():
    print("MT5 init failed:", mt5.last_error())
    exit(1)

print("MT5 connected!")

# Check terminal info
ti = mt5.terminal_info()
print(f"Connected: {ti.connected}, Trade allowed: {ti.trade_allowed}")
print(f"Build: {ti.build}, Path: {ti.path}")

# Check if XAUUSD is selected
sel = mt5.symbol_select("XAUUSD", True)
print(f"XAUUSD selected: {sel}")

# Check symbol info
info = mt5.symbol_info("XAUUSD")
if info:
    print(f"XAUUSD - Spread: {info.spread}, Digits: {info.digits}, Trade mode: {info.trade_mode}")

# Try different methods to get data
print("\n--- Method 1: copy_rates_from with count ---")
try:
    utc_now = datetime.now(timezone.utc)
    utc_from = utc_now.replace(year=2026, month=1, day=1)
    rates = mt5.copy_rates_from("XAUUSD", mt5.TIMEFRAME_M1, utc_from, 100000)
    if rates is not None and len(rates) > 0:
        print(f"Got {len(rates)} bars")
    else:
        print(f"None: {mt5.last_error()}")
except Exception as e:
    print(f"Error: {e}")

print("\n--- Method 2: copy_rates_from_pos ---")
try:
    rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M1, 0, 50000)
    if rates is not None and len(rates) > 0:
        print(f"Got {len(rates)} bars")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        print(f"Date range: {df['time'].min()} to {df['time'].max()}")
        print(f"tick_volume: min={df['tick_volume'].min()}, max={df['tick_volume'].max()}, mean={df['tick_volume'].mean():.0f}")
        non_zero = (df["tick_volume"] > 0).sum()
        print(f"Non-zero tick_volume bars: {non_zero}/{len(df)} ({100*non_zero/len(df):.1f}%)")
        
        # Save
        out_df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        out_df.columns = ["DateTime", "Open", "High", "Low", "Close", "Volume"]
        out_df.to_csv("xauusd_1m_mt5.csv", index=False)
        print(f"\nSaved {len(out_df)} rows to xauusd_1m_mt5.csv")
    else:
        print(f"None: {mt5.last_error()}")
except Exception as e:
    print(f"Error: {e}")

print("\n--- Method 3: copy_rates_range with datetime objects ---")
try:
    from datetime import datetime as dt
    start = dt(2026, 1, 1, tzinfo=timezone.utc)
    end = dt(2026, 4, 25, tzinfo=timezone.utc)
    rates = mt5.copy_rates_range("XAUUSD", mt5.TIMEFRAME_M1, start, end)
    if rates is not None and len(rates) > 0:
        print(f"Got {len(rates)} bars")
    else:
        print(f"None: {mt5.last_error()}")
except Exception as e:
    print(f"Error: {e}")

mt5.shutdown()
