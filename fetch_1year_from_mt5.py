"""Export XAUUSD M1 data from MT5 — run AFTER downloading history in MT5."""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone, timedelta

print("=" * 60)
print("  XAUUSD M1 EXPORT (post-MT5-history-download)")
print("=" * 60)

if not mt5.initialize():
    print(f"ERROR: MT5 init failed. Open MT5 terminal first.")
    exit(1)

mt5.symbol_select("XAUUSD", True)

# Fetch whatever is available using copy_rates_from (pulls from terminal DB)
end = datetime.now(timezone.utc)
start = end - timedelta(days=400)  # Just over 1 year

rates = mt5.copy_rates_range("XAUUSD", mt5.TIMEFRAME_M1, start, end)

if rates is None or len(rates) == 0:
    print("No data. You must download history in MT5 first:")
    print("  MT5 -> F2 -> XAUUSD -> M1 -> Download -> set 1 year range")
    mt5.shutdown()
    exit(1)

df = pd.DataFrame(rates)
df["DateTime"] = pd.to_datetime(df["time"], unit="s")
df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "tick_volume": "Volume"})
df = df[["DateTime", "Open", "High", "Low", "Close", "Volume"]]
df = df.sort_values("DateTime").drop_duplicates("DateTime").reset_index(drop=True)

print(f"Exported {len(df):,} bars")
print(f"Range: {df.DateTime.min()} to {df.DateTime.max()}")

# Monthly counts
df["Month"] = df["DateTime"].dt.to_period("M")
for m, c in df.groupby("Month").size().items():
    print(f"  {m}: {c:,} bars")

filename = "xauusd_1m_1year.csv"
df.drop(columns=["Month"]).to_csv(filename, index=False)
print(f"\nSaved to {filename}")
print(f"Run: python backtest.py --config configs/config_30d.yaml --csv {filename} --log-level ERROR")

mt5.shutdown()
