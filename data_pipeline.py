"""
XAUUSD price data loading & validation pipeline.

Auto-detects CSV format (HistData semicolon-delimited or standard comma-separated),
validates required columns, rejects non-XAUUSD data, and returns a clean UTC-indexed
DataFrame ready for Backtrader's PandasData feed.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Required OHLCV columns (lowercase)
REQUIRED_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]

# XAUUSD plausibility threshold: median close below this value indicates wrong instrument.
XAUUSD_CLOSE_THRESHOLD = 1000


def _detect_delimiter_and_header(filepath: Path) -> tuple[str, bool]:
    """
    Detect CSV delimiter and whether the file contains a header row.

    Returns (delimiter, has_header).
    """
    with open(filepath, "r") as f:
        first_line = f.readline().strip()

    # Try to detect delimiter: if semicolons present and more than commas, use semicolon.
    n_semi = first_line.count(";")
    n_comma = first_line.count(",")
    delimiter = ";" if n_semi > n_comma else ","

    # Check if the first line looks like a header (contains typical column names)
    lower = first_line.lower()
    header_keywords = {"datetime", "date", "time", "open", "high", "low", "close", "volume"}
    tokens = [t.strip().strip('"\'') for t in first_line.split(delimiter)]
    has_header = any(kw in t.lower() for t in tokens for kw in header_keywords)

    return delimiter, has_header


def _infer_datetime_format(datetime_series: pd.Series) -> str:
    """Infer datetime format from the first non-null sample."""
    sample = str(datetime_series.dropna().iloc[0])
    return "%Y-%m-%d %H:%M:%S" if "-" in sample else "%Y%m%d %H%M%S"


def load_xauusd_dataframe(
    csv_path: Path,
    *,
    convert_utc: bool = True,
    low_memory: bool = False,
) -> pd.DataFrame:
    """
    Load XAUUSD OHLCV data from a CSV file and return a clean, UTC-indexed DataFrame.

    Handles:
      - HistData format: semicolon-delimited, no header, columns:
        YYYYMMDD HHMMSS;Open;High;Low;Close;Volume
      - Standard CSV: comma-delimited, with header row containing
        datetime, open, high, low, close, volume (case-insensitive).

    Returns a DataFrame with columns [open, high, low, close, volume] indexed by
    UTC-aware datetime, sorted chronologically, duplicates removed, missing rows dropped.

    Raises:
        FileNotFoundError: if *csv_path* does not exist.
        ValueError: if required columns are missing, no valid rows remain,
                    or the data does not resemble XAUUSD.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Price data file not found: {csv_path}")

    delimiter, has_header = _detect_delimiter_and_header(csv_path)
    logger.info("Reading %s (delimiter=%r, header=%r)", csv_path.name, delimiter, has_header)

    # Read raw CSV
    if has_header:
        df = pd.read_csv(csv_path, sep=delimiter, low_memory=low_memory)
    else:
        df = pd.read_csv(
            csv_path,
            sep=delimiter,
            header=None,
            names=REQUIRED_COLUMNS,
            low_memory=low_memory,
        )

    # Normalize column names
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Validate required columns
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {missing}. "
            f"Columns found: {list(df.columns)}"
        )

    # Keep only required columns
    df = df[REQUIRED_COLUMNS].copy()

    # Parse datetime
    fmt = _infer_datetime_format(df["datetime"])
    df["datetime"] = pd.to_datetime(df["datetime"], format=fmt, errors="coerce")

    # Drop rows with unparseable dates
    df = df.dropna(subset=["datetime"])

    # Convert numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with missing price/volume
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])

    # Sort and drop duplicate timestamps (keep latest)
    df = df.sort_values("datetime").drop_duplicates("datetime", keep="last")

    if df.empty:
        raise ValueError("No valid OHLCV rows after cleaning.")

    # Validate price characteristics (XAUUSD check, high-low sanity, ...)
    df = validate_price_data(df)

    # Set UTC datetime index
    df = df.set_index("datetime")
    if convert_utc:
        if df.index.tz is None:
            df.index = df.index.tz_localize("utc")
        else:
            df.index = df.index.tz_convert("utc")

    logger.info(
        "Loaded %d XAUUSD 1m bars (%s to %s)",
        len(df),
        df.index.min().strftime("%Y-%m-%d"),
        df.index.max().strftime("%Y-%m-%d"),
    )
    return df


def validate_price_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate a raw OHLCV DataFrame and return the cleaned version.

    Checks performed:
      - median close > XAUUSD_CLOSE_THRESHOLD (1000), otherwise raises ValueError.
      - high >= low (swaps if necessary).
      - volume >= 0 (clips negative values).
      - open / close are within [low, high] (drops offending rows).

    Returns the (possibly modified) DataFrame.
    """
    # 1. XAUUSD plausibility
    med_close = float(df["close"].median())
    if med_close <= XAUUSD_CLOSE_THRESHOLD:
        raise ValueError(
            f"Data does not look like XAUUSD: median close = {med_close:.2f} "
            f"(threshold = {XAUUSD_CLOSE_THRESHOLD}). Are you using the correct file?"
        )

    # 2. High >= Low
    swapped = 0
    mask = df["high"] < df["low"]
    if mask.any():
        swapped = mask.sum()
        df.loc[mask, ["high", "low"]] = df.loc[mask, ["low", "high"]].values
        logger.warning("Swapped high/low for %d rows where high < low.", swapped)

    # 3. Non-negative volume
    neg_vol = (df["volume"] < 0).sum()
    if neg_vol:
        df["volume"] = df["volume"].clip(lower=0)
        logger.warning("Clipped %d negative volume values to 0.", neg_vol)

    # 4. OHLC consistency
    tolerance = 1e-6
    o_bad = df["open"] < df["low"] - tolerance
    c_bad = df["close"] < df["low"] - tolerance
    o_bad |= df["open"] > df["high"] + tolerance
    c_bad |= df["close"] > df["high"] + tolerance
    bad = o_bad | c_bad
    if bad.any():
        logger.warning("Dropping %d rows with O/C outside [L, H].", bad.sum())
        df = df[~bad]

    return df


def apply_date_filters(
    price_data: pd.DataFrame,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    trade_start: Optional[str] = None,
    warmup_days: int = 5,
) -> pd.DataFrame:
    """
    Filter price data between optional start/end dates.

    If enabled via env var ``ENABLE_DATE_FILTER=false``, this function is a no‑op.
    Preserves original logic: if ``trade_start`` is provided and ``start_date`` is
    missing, the start is computed as ``trade_start - warmup_days``.

    Raises ValueError if filtering removes all rows.
    """
    if os.environ.get("ENABLE_DATE_FILTER", "true").lower() in ("false", "0", "no"):
        logger.info("Date filtering disabled via ENABLE_DATE_FILTER env var.")
        return price_data

    sd = start_date
    if not sd and trade_start:
        sd = (
            pd.Timestamp(trade_start) - pd.Timedelta(days=warmup_days)
        ).strftime("%Y-%m-%d")

    if sd:
        price_data = price_data.loc[sd:]
    if end_date:
        price_data = price_data.loc[:end_date]

    if price_data.empty:
        raise ValueError("Date filters removed all rows; check your start/end configuration.")

    return price_data