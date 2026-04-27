import requests
from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict
from src.utils.logger import logger
from src.utils.config import AppConfig

class MacroNewsFilter:
    def __init__(self, config: AppConfig):
        self.news_url = config.api.news_api_url
        self.high_impact_events = []

    def fetch_news(self):
        """Fetch high impact news events."""
        try:
            # Using ForexFactory JSON structure as reference
            response = requests.get(self.news_url)
            if response.status_code == 200:
                events = response.json()
                self.high_impact_events = [
                    e for e in events 
                    if e.get('impact') == 'High'
                ]
            else:
                logger.warning(f"Failed to fetch news: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching news: {e}")

    def is_blackout_window(self, current_time: datetime, currency: str = "USD") -> bool:
        """Block signals if current_time in [event_time-30m, event_time+30m]"""
        for event in self.high_impact_events:
            # Check if event affects the currency pair
            if event.get('country') != currency:
                continue
                
            try:
                # Format: "2023-10-25T14:00:00Z"
                event_time = pd.to_datetime(event['date']).replace(tzinfo=None)
                window_start = event_time - timedelta(minutes=30)
                window_end = event_time + timedelta(minutes=30)
                
                if window_start <= current_time <= window_end:
                    logger.info(f"News blackout window active for {currency}: {event.get('title')}")
                    return True
            except Exception as e:
                logger.error(f"Error parsing event time: {e}")
                
        return False

    def get_4h_trend_bias(self, df_4h: pd.DataFrame) -> str:
        """Skip counter-trend setups vs 4H trend bias. Returns 'Bullish' or 'Bearish'"""
        if len(df_4h) < 20:
            return "Neutral"
        
        # Simple SMA-based trend or Higher Highs / Lower Lows
        sma20 = df_4h['close'].rolling(window=20).mean()
        current_close = df_4h['close'].iloc[-1]
        current_sma = sma20.iloc[-1]
        
        if current_close > current_sma:
            return "Bullish"
        elif current_close < current_sma:
            return "Bearish"
        return "Neutral"
