"""Application configuration models and loader.

Supports two config-file schemas:
  1. Legacy nested format    (configs/config.yaml)
  2. Flat 30d format         (configs/config_30d.yaml)

The ``load_config`` entrypoint normalises both into a single ``AppConfig``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
#  SUB-MODELS
# ═══════════════════════════════════════════════════════════════

class TradingConfig(BaseModel):
    symbols: List[str] = ["XAUUSD"]
    timeframes: List[str] = ["1m", "5m", "1h", "4h"]


class RiskConfig(BaseModel):
    risk_per_trade_pct: float = 0.02
    sl_atr_multiplier: float = 1.5
    tp_rr_ratio: float = 1.5
    atr_multiplier: float = 1.5
    risk_pct: float = 0.02
    rr_ratio: float = 1.5
    partial_tp_enabled: bool = False
    partial_tp_fraction: float = 0.5
    partial_tp_rr_1: float = 1.0
    partial_tp_rr_2: float = 1.5
    breakeven_after_partial: bool = True
    breakeven_plus_ticks: float = 0.01
    daily_loss_limit_pct: float = -0.04
    atr_period: int = 14
    atr_volatility_threshold: float = 0.004
    high_vol_rr_multiplier: float = 2.0
    news_blackout_minutes: int = 30


class Session(BaseModel):
    start: str
    end: str


class SessionsConfig(BaseModel):
    asia: Session
    london: Session
    ny: Session


class ApiConfig(BaseModel):
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    news_api_url: str = ""


class DataConfig(BaseModel):
    file_path: str = "xauusd_1m.csv"
    warmup_days: int = 15
    trade_start: Optional[str] = None
    trade_end: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class ExecutionConfig(BaseModel):
    commission: float = 0.0002
    initial_cash: float = 100_000.0
    slippage_fixed: float = 0.05
    spread_buffer: float = 0.1


class LoggingConfig(BaseModel):
    directory: str = "logs"
    level: str = "INFO"


class StrategyConfig(BaseModel):
    """Flat-style strategy parameters (used by config_30d.yaml et al)."""
    ema_period: int = 200
    atr_period: int = 14
    atr_multiplier: float = 1.5
    risk_pct: float = 0.02
    rr_ratio: float = 1.5
    structure_lookback: int = 3
    sweep_expiry_bars: int = 12
    max_daily_trades: int = 8
    min_bars_between_trades: int = 5
    enable_killzone_filter: bool = True
    enable_volume_filter: bool = False
    entry_restrict_killzones: bool = True
    min_confirmations: int = 1


# ═══════════════════════════════════════════════════════════════
#  TOP-LEVEL CONFIG (unified)
# ═══════════════════════════════════════════════════════════════

class AppConfig(BaseModel):
    """
    Unified application config.

    Accepts both legacy nested blocks and modern flat YAML keys.
    Fields have sensible defaults so partial YAML files are valid.
    """
    trading: TradingConfig = Field(default_factory=TradingConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    sessions: Optional[SessionsConfig] = None
    api: ApiConfig = Field(default_factory=ApiConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)


# ═══════════════════════════════════════════════════════════════
#  LOADER — normalises both file formats
# ═══════════════════════════════════════════════════════════════

def _coerce_sessions(raw: Dict[str, Any]) -> SessionsConfig:
    """Accept flat session keys and nest them into a SessionsConfig."""
    # Already nested?
    if "asia" in raw and isinstance(raw.get("asia"), dict):
        return SessionsConfig(**raw)

    # Flat keys like asia_start, asia_end, london_start, etc.
    asia_start = str(raw.get("asia_start", "00:00"))
    asia_end = str(raw.get("asia_end", "07:00"))
    london_start = str(raw.get("london_start", "07:00"))
    london_end = str(raw.get("london_end", "12:00"))
    ny_start = str(raw.get("ny_start", "12:00"))
    ny_end = str(raw.get("ny_end", "21:00"))

    # Pad single-digit hours
    def _pad(t: str) -> str:
        parts = t.strip().split(":")
        return f"{int(parts[0]):02d}:{int(parts[1]) if len(parts) > 1 else 0:02d}"

    return SessionsConfig(
        asia=Session(start=_pad(asia_start), end=_pad(asia_end)),
        london=Session(start=_pad(london_start), end=_pad(london_end)),
        ny=Session(start=_pad(ny_start), end=_pad(ny_end)),
    )


def _coerce_risk(raw: Dict[str, Any]) -> RiskConfig:
    """Normalise flat risk keys into RiskConfig."""
    return RiskConfig(
        risk_per_trade_pct=float(raw.get("risk_per_trade_pct", raw.get("risk_pct", 0.02))),
        sl_atr_multiplier=float(raw.get("sl_atr_multiplier", raw.get("atr_multiplier", 1.5))),
        tp_rr_ratio=float(raw.get("tp_rr_ratio", raw.get("rr_ratio", 1.5))),
        atr_multiplier=float(raw.get("atr_multiplier", 1.5)),
        risk_pct=float(raw.get("risk_pct", 0.02)),
        rr_ratio=float(raw.get("rr_ratio", 1.5)),
        partial_tp_enabled=bool(raw.get("partial_tp_enabled", False)),
        partial_tp_fraction=float(raw.get("partial_tp_fraction", 0.5)),
        partial_tp_rr_1=float(raw.get("partial_tp_rr_1", 1.0)),
        partial_tp_rr_2=float(raw.get("partial_tp_rr_2", 1.5)),
        breakeven_after_partial=bool(raw.get("breakeven_after_partial", True)),
        breakeven_plus_ticks=float(raw.get("breakeven_plus_ticks", 0.01)),
        daily_loss_limit_pct=float(raw.get("daily_loss_limit_pct", -0.04)),
        atr_period=int(raw.get("atr_period", 14)),
    )


def load_config(path: str = "configs/config_30d.yaml") -> AppConfig:
    """
    Load a YAML config file and return a normalised ``AppConfig``.

    Works with both legacy nested format (configs/config.yaml) and
    the modern flat format (configs/config_30d.yaml).
    """
    with open(path, "r") as f:
        raw: Dict[str, Any] = yaml.safe_load(f) or {}

    # ── Data ──
    data_raw = raw.get("data", {})
    data_cfg = DataConfig(
        file_path=str(data_raw.get("file_path", "xauusd_1m.csv")),
        warmup_days=int(data_raw.get("warmup_days", 15)),
        trade_start=data_raw.get("trade_start"),
        trade_end=data_raw.get("trade_end"),
        start_date=data_raw.get("start_date"),
        end_date=data_raw.get("end_date"),
    )

    # ── Execution ──
    exec_raw = raw.get("execution", {})
    exec_cfg = ExecutionConfig(
        commission=float(exec_raw.get("commission", 0.0002)),
        initial_cash=float(exec_raw.get("initial_cash", 100_000.0)),
        slippage_fixed=float(exec_raw.get("slippage_fixed", 0.05)),
        spread_buffer=float(exec_raw.get("spread_buffer", 0.1)),
    )

    # ── Logging ──
    log_raw = raw.get("logging", {})
    log_cfg = LoggingConfig(
        directory=str(log_raw.get("directory", "logs")),
        level=str(log_raw.get("level", "INFO")),
    )

    # ── Sessions ──
    sessions_raw = raw.get("sessions", {})
    sessions_cfg = _coerce_sessions(sessions_raw)

    # ── Trading ──
    trading_raw = raw.get("trading", {})
    trading_cfg = TradingConfig(
        symbols=trading_raw.get("symbols", ["XAUUSD"]),
        timeframes=trading_raw.get("timeframes", ["1m", "5m", "1h", "4h"]),
    )

    # ── Risk ──
    risk_raw = raw.get("risk", {})
    risk_cfg = _coerce_risk(risk_raw)

    # ── API ──
    api_raw = raw.get("api", {})
    api_cfg = ApiConfig(
        mt5_login=int(api_raw.get("mt5_login", 0)),
        mt5_password=str(api_raw.get("mt5_password", "")),
        mt5_server=str(api_raw.get("mt5_server", "")),
        news_api_url=str(api_raw.get("news_api_url", "")),
    )

    # ── Strategy ──
    strat_raw = raw.get("strategy", {})
    strat_cfg = StrategyConfig(
        ema_period=int(strat_raw.get("ema_period", 200)),
        atr_period=int(strat_raw.get("atr_period", 14)),
        atr_multiplier=float(strat_raw.get("atr_multiplier", 1.5)),
        risk_pct=float(strat_raw.get("risk_pct", 0.02)),
        rr_ratio=float(strat_raw.get("rr_ratio", 1.0)),
        structure_lookback=int(strat_raw.get("structure_lookback", 3)),
        sweep_expiry_bars=int(strat_raw.get("sweep_expiry_bars", 12)),
        max_daily_trades=int(strat_raw.get("max_daily_trades", 8)),
        min_bars_between_trades=int(strat_raw.get("min_bars_between_trades", 5)),
        enable_killzone_filter=bool(strat_raw.get("enable_killzone_filter", True)),
        enable_volume_filter=bool(strat_raw.get("enable_volume_filter", False)),
        entry_restrict_killzones=bool(strat_raw.get("entry_restrict_killzones", True)),
        min_confirmations=int(strat_raw.get("min_confirmations", 2)),
    )

    return AppConfig(
        trading=trading_cfg,
        risk=risk_cfg,
        sessions=sessions_cfg,
        api=api_cfg,
        data=data_cfg,
        execution=exec_cfg,
        logging=log_cfg,
        strategy=strat_cfg,
    )
