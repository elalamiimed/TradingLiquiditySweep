import pandas as pd
import MetaTrader5 as mt5
import time
from typing import Dict, List
from src.utils.logger import logger
from src.utils.config import AppConfig

class DataFetcher:
    def __init__(self, config: AppConfig):
        self.config = config
        
        # Initialize MT5
        if not mt5.initialize():
            logger.error(f"MT5 initialize() failed, error code = {mt5.last_error()}")
            raise Exception("MT5 initialization failed")
            
        # Login
        if config.api.mt5_login and config.api.mt5_password and config.api.mt5_server:
            authorized = mt5.login(
                config.api.mt5_login, 
                password=config.api.mt5_password, 
                server=config.api.mt5_server
            )
            if not authorized:
                logger.error(f"MT5 login failed, error code = {mt5.last_error()}")
                raise Exception("MT5 login failed")
            else:
                logger.info("Connected to MT5 successfully with provided credentials")
        else:
            logger.info("Connected to MT5 (using default terminal login)")

        # Map string timeframes to MT5 timeframe constants
        self.tf_map = {
            "4h": mt5.TIMEFRAME_H4,
            "1h": mt5.TIMEFRAME_H1,
            "5m": mt5.TIMEFRAME_M5,
            "1m": mt5.TIMEFRAME_M1
        }
        
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
        """Fetch OHLCV data from MT5."""
        mt5_tf = self.tf_map.get(timeframe.lower())
        if mt5_tf is None:
            logger.error(f"Unsupported timeframe: {timeframe}")
            return pd.DataFrame()
            
        try:
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, limit)
            if rates is None or len(rates) == 0:
                logger.error(f"Failed to fetch data for {symbol} on {timeframe}, error code = {mt5.last_error()}")
                return pd.DataFrame()
                
            df = pd.DataFrame(rates)
            df['timestamp'] = pd.to_datetime(df['time'], unit='s')
            df.rename(columns={'tick_volume': 'volume'}, inplace=True)
            df.set_index('timestamp', inplace=True)
            
            # Return standard columns
            return df[['open', 'high', 'low', 'close', 'volume']]
            
        except Exception as e:
            logger.error(f"Exception fetching data for {symbol} on {timeframe}: {e}")
            return pd.DataFrame()

    def fetch_multi_timeframes(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """Fetch data for all required timeframes."""
        data = {}
        for tf in self.config.trading.timeframes:
            data[tf] = self.fetch_ohlcv(symbol, tf)
            # Add a small delay if needed, though MT5 is usually very fast locally
            time.sleep(0.1) 
        return data

    def shutdown(self):
        """Shutdown MT5 connection"""
        mt5.shutdown()
        logger.info("MT5 connection closed")
