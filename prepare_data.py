import pandas as pd
import glob
import os

def prepare_xauusd_data():
    """Merge and reformat HistData CSV files for backtest.py"""
    
    # Find all CSV files
    csv_files = glob.glob("DAT_ASCII_XAUUSD_M1_*.csv")
    print(f"Found {len(csv_files)} files: {csv_files}")
    
    all_data = []
    
    for file in csv_files:
        print(f"Processing {file}...")
        # HistData format: YYYYMMDD HHMMSS;Open;High;Low;Close;Volume
        df = pd.read_csv(file, sep=';', header=None,
                        names=['DateTime', 'Open', 'High', 'Low', 'Close', 'Volume'])
        
        # Convert DateTime from "20260115 185900" to "2026-01-15 18:59:00"
        df['DateTime'] = pd.to_datetime(df['DateTime'], format='%Y%m%d %H%M%S')
        df['DateTime'] = df['DateTime'].dt.strftime('%Y-%m-%d %H:%M:%S')
        
        all_data.append(df)
    
    # Merge all files
    merged_df = pd.concat(all_data, ignore_index=True)
    
    # Sort by DateTime
    merged_df = merged_df.sort_values('DateTime').reset_index(drop=True)
    
    # Save to final CSV
    output_file = "xauusd_1m.csv"
    merged_df.to_csv(output_file, index=False)
    
    print(f"\n✅ Successfully created {output_file}")
    print(f"Total candles: {len(merged_df)}")
    print(f"Date range: {merged_df['DateTime'].iloc[0]} to {merged_df['DateTime'].iloc[-1]}")
    print(f"Price range: {merged_df['Close'].min():.2f} - {merged_df['Close'].max():.2f}")
    
    return output_file

if __name__ == "__main__":
    prepare_xauusd_data()