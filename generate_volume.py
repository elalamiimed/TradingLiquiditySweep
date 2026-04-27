"""
Generate realistic synthetic tick volumes for XAUUSD 1M data.
Based on statistical analysis of real MT5 tick volume data:
  - Mean: 118, Median: 66, Std: 143
  - Lognormal-ish: log1p ~ N(4.30, 0.96)
  - Correlation with bar range: 0.586 (log)
  - Strong hourly pattern (NY/London open spikes)
"""
import pandas as pd
import numpy as np
from datetime import datetime

# Hourly mean volumes from real MT5 data (UTC)
HOURLY_MEAN = {
    0: 28,  1: 41,  2: 54,  3: 93,  4: 75,  5: 63,
    6: 45,  7: 56,  8: 80,  9: 111, 10: 132, 11: 113,
    12: 107, 13: 105, 14: 125, 15: 263, 16: 283, 17: 279,
    18: 189, 19: 137, 20: 141, 21: 104, 22: 94, 23: 28
}

np.random.seed(42)

print("Loading xauusd_1m.csv...")
df = pd.read_csv("xauusd_1m.csv")
df["DateTime"] = pd.to_datetime(df["DateTime"])
print(f"  {len(df)} rows, {df['DateTime'].min()} to {df['DateTime'].max()}")

df["hour"] = df["DateTime"].dt.hour
df["range"] = df["High"] - df["Low"]
df["range_pct"] = df["range"] / df["Close"]

# Step 1: Base volume from hourly pattern
df["base_vol"] = df["hour"].map(HOURLY_MEAN).fillna(60)

# Step 2: Range adjustment (log scale, as real data shows log correlation)
# Normalize range to get a multiplier
median_range = df["range"].median()
df["range_factor"] = np.log1p(df["range"].clip(lower=0.01) / median_range)
df["range_factor"] = df["range_factor"].clip(-1.5, 2.5)
df["range_factor"] = (df["range_factor"] - df["range_factor"].min()) / (df["range_factor"].max() - df["range_factor"].min())
df["range_factor"] = 0.3 + df["range_factor"] * 1.4  # Scale to 0.3-1.7 range

# Step 3: Generate lognormal noise matching real distribution
noise = np.random.lognormal(mean=0.0, sigma=0.70, size=len(df))
noise = np.clip(noise, 0.2, 5.0)

# Step 4: Combine
df["Volume"] = (df["base_vol"] * df["range_factor"] * noise).astype(int)
df["Volume"] = df["Volume"].clip(lower=5, upper=3000)

print(f"\nGenerated volume stats:")
print(f"  Mean: {df['Volume'].mean():.0f}, Median: {df['Volume'].median():.0f}")
print(f"  Std: {df['Volume'].std():.0f}, Min: {df['Volume'].min()}, Max: {df['Volume'].max()}")
for p in [10, 25, 50, 75, 90, 95, 99]:
    print(f"  P{p}: {df['Volume'].quantile(p/100):.0f}")

# Save
out = df[["DateTime", "Open", "High", "Low", "Close", "Volume"]].copy()
out.to_csv("xauusd_1m_realvol.csv", index=False)
print(f"\nSaved {len(out)} rows to xauusd_1m_realvol.csv")
print(f"Sample volume values: {out['Volume'].head(10).tolist()}")
