import pytest
import pandas as pd
from src.engine.risk_execution import RiskExecutionEngine
from src.utils.config import AppConfig, TradingConfig, RiskConfig, SessionsConfig, Session, ApiConfig

def test_position_sizing():
    config = AppConfig(
        trading=TradingConfig(symbols=["EUR/USD"], timeframes=["1m"]),
        risk=RiskConfig(risk_per_trade_pct=0.02, sl_atr_multiplier=1.5, tp_rr_ratio=1.0),
        sessions=SessionsConfig(asia=Session(start="00:00", end="08:00"), london=Session(start="07:00", end="16:00"), ny=Session(start="13:00", end="22:00")),
        api=ApiConfig(mt5_login=0, mt5_password="", mt5_server="", news_api_url="")
    )
    
    engine = RiskExecutionEngine(config)
    balance = 10000.0
    entry_price = 1.1000
    sl_price = 1.0950
    
    # SL distance = 0.0050. Risk = 10000 * 0.02 = 200.
    # Pos Size = 200 / 0.0050 = 40000
    size = engine.calculate_position_size(balance, entry_price, sl_price)
    
    assert size == 40000.0
