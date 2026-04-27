"""
Fetch 1 year of XAUUSD M1 data
===============================
MT5 broker (FXCM) only has ~4 months of data.
Use one of these methods to get 1 year:

METHOD A: Manual HistData download (10 min)
  1. Go to http://www.histdata.com
  2. Sign up (free account) or log in
  3. Navigate: Download Free Forex Data -> XAUUSD -> 1-minute
  4. Download each month you need as CSV
  5. Place ALL CSVs in the Price1m/ folder
  6. Run: python merge_data.py
  7. Output: xauusd_1m.csv (1 year, merged)

METHOD B: MT5 History Center (if broker supports it)
  1. Open MT5 terminal
  2. Press F2 (History Center)
  3. Double-click XAUUSD -> M1
  4. Click "Download" button
  5. Set date range to 1 year back
  6. Wait for download
  7. Run: python fetch_1year_from_mt5.py

METHOD C: Use Python wrapper (after MT5 download)
  Run: python fetch_1year_from_mt5.py
  (This exports whatever MT5 has after you download)
"""

if __name__ == "__main__":
    print(__doc__)
