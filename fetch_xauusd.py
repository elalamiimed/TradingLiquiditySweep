import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

if not mt5.initialize():
    print("❌ MT5 init failed. Open MT5 terminal & log in first.")
    quit()

# ⚠️ CHECK MT5 "MARKET WATCH" FOR EXACT SYMBOL
# Common: "XAUUSD", "XAUUSDc", "XAUUSDm", "GOLD"
SYMBOL = "XAUUSD" 

print(f"⏳ Fetching 30 days of {SYMBOL} 1M data...")
rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, 
                             datetime(2024, 10, 1), datetime.now())

if rates is None or len(rates) == 0:
    print(f"❌ No data for {SYMBOL}. Verify symbol name & download history in MT5 (Ctrl+U).")
    mt5.shutdown()
    quit()

df = pd.DataFrame(rates)
df["DateTime"] = pd.to_datetime(df["time"], unit="s")
df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","tick_volume":"Volume"})
df[["DateTime","Open","High","Low","Close","Volume"]].to_csv("xauusd_1m.csv", index=False)

mt5.shutdown()
print(f"✅ Saved 'xauusd_1m.csv' ({len(df)} candles).")