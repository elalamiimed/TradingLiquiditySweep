"""
Merge all HistData CSV files from Price1m/ into a single clean CSV.
Generates realistic tick volume based on price range + hour patterns.

Usage:
  python merge_data.py
  
Output: xauusd_1m.csv
"""
import pandas as pd
import numpy as np
import glob, os

DATA_FOLDER = r"Price1m"
OUTPUT_FILE = "xauusd_1m.csv"

def generate_volume(df):
    """Generate realistic tick volume if all zeros."""
    if df["Volume"].sum() > 0:
        return df
    
    print("  Generating synthetic volume from price action...")
    # Base volume from price range
    price_range = (df["High"] - df["Low"]).abs()
    df["Volume"] = np.maximum(1, (price_range * 800 + np.random.normal(200, 50, len(df))).astype(int))
    
    # Boost volume during known active hours (UTC 7-16 = London + NY)
    df["Hour"] = df["DateTime"].dt.hour
    active_mask = (df["Hour"] >= 7) & (df["Hour"] <= 16)
    df.loc[active_mask, "Volume"] = (df.loc[active_mask, "Volume"] * 2.5).astype(int)
    
    # Boost during opening hours
    open_mask = (df["Hour"] == 7) | (df["Hour"] == 12) | (df["Hour"] == 13)
    df.loc[open_mask, "Volume"] = (df.loc[open_mask, "Volume"] * 2).astype(int)
    
    df.drop(columns=["Hour"], inplace=True)
    return df


def main():
    print("=" * 60)
    print("  XAUUSD M1 DATA MERGER")
    print("=" * 60)
    
    # Find CSVs
    pattern = os.path.join(DATA_FOLDER, "DAT_ASCII_XAUUSD_M1_*.csv")
    files = sorted(glob.glob(pattern))
    
    if not files:
        pattern2 = os.path.join(DATA_FOLDER, "*.csv")
        files = sorted(glob.glob(pattern2))
    
    if not files:
        print(f"ERROR: No CSV files in {DATA_FOLDER}/")
        print("Download monthly XAUUSD M1 files from http://www.histdata.com")
        print("Place them in the Price1m/ folder, then re-run this script.")
        return
    
    print(f"Found {len(files)} files:")
    for f in files:
        print(f"  {os.path.basename(f)}")
    
    all_dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, sep=";", header=None,
                           names=["DateTime", "Open", "High", "Low", "Close", "Volume"])
            df["DateTime"] = pd.to_datetime(df["DateTime"], format="%Y%m%d %H%M%S")
            all_dfs.append(df)
        except:
            # Try comma-separated
            try:
                df = pd.read_csv(f)
                if "DateTime" in df.columns or "datetime" in df.columns:
                    dt_col = "DateTime" if "DateTime" in df.columns else "datetime"
                    df[dt_col] = pd.to_datetime(df[dt_col])
                    df = df.rename(columns={dt_col: "DateTime"})
                    for c in ["Open", "High", "Low", "Close", "Volume"]:
                        if c.lower() in df.columns:
                            df = df.rename(columns={c.lower(): c, c.upper(): c})
                    all_dfs.append(df)
                else:
                    print(f"  WARNING: Could not parse {os.path.basename(f)}")
            except Exception as e:
                print(f"  WARNING: {os.path.basename(f)} - {e}")
    
    if not all_dfs:
        print("No valid data loaded.")
        return
    
    merged = pd.concat(all_dfs, ignore_index=True)
    merged = merged.sort_values("DateTime").drop_duplicates("DateTime").reset_index(drop=True)
    
    # Generate volume if needed
    merged = generate_volume(merged)
    
    merged.to_csv(OUTPUT_FILE, index=False)
    
    print(f"\nSaved {len(merged):,} rows to {OUTPUT_FILE}")
    print(f"Date range: {merged.DateTime.min()} -> {merged.DateTime.max()}")
    
    merged["Month"] = merged["DateTime"].dt.to_period("M")
    for m, c in merged.groupby("Month").size().items():
        print(f"  {m}: {c:,} bars")
    
    print(f"\nReady: python backtest.py --config configs/config_30d.yaml --csv {OUTPUT_FILE} --log-level ERROR")


if __name__ == "__main__":
    main()
