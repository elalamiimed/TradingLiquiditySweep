import pandas as pd
import numpy as np
from typing import Dict, Tuple
from src.utils.logger import logger
from src.utils.config import AppConfig

class RiskExecutionEngine:
    def __init__(self, config: AppConfig):
        self.risk_pct = config.risk.risk_per_trade_pct
        self.sl_multiplier = config.risk.sl_atr_multiplier
        self.tp_ratio = config.risk.tp_rr_ratio

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range."""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(period).mean().iloc[-1]
        return atr

    def calculate_position_size(self, balance: float, entry_price: float, sl_price: float, pip_value: float = 10.0) -> float:
        """
        Position Size = (Balance * Risk%) / (SL_distance_pips * pip_value)
        Assuming pip_value and standard lot sizing for forex/crypto.
        """
        risk_amount = balance * self.risk_pct
        sl_distance = abs(entry_price - sl_price)
        
        if sl_distance == 0:
            return 0.0
            
        # Simplified position sizing calculation
        position_size = risk_amount / sl_distance
        return round(position_size, 4)

    def calculate_sl_tp(self, df: pd.DataFrame, entry_price: float, direction: str) -> Tuple[float, float]:
        """Calculate Stop Loss and Take Profit."""
        atr = self.calculate_atr(df)
        sl_distance = atr * self.sl_multiplier
        
        if direction == "long":
            sl = entry_price - sl_distance
            tp = entry_price + (sl_distance * self.tp_ratio)
        else:
            sl = entry_price + sl_distance
            tp = entry_price - (sl_distance * self.tp_ratio)
            
        return sl, tp

    def execute_trade(self, symbol: str, direction: str, size: float, entry: float, sl: float, tp: float):
        """Mock execution logic."""
        logger.info(f"Executing {direction} on {symbol} | Size: {size} | Entry: {entry} | SL: {sl} | TP: {tp}")
        # In a real system, this would interface with ccxt or MT5 to place orders
        return {"status": "executed", "symbol": symbol, "direction": direction, "size": size}
