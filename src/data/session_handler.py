import pandas as pd
from datetime import datetime, time
from typing import Dict, Tuple
from src.utils.config import AppConfig

class SessionHandler:
    def __init__(self, config: AppConfig):
        self.sessions = config.sessions

    def parse_time(self, time_str: str) -> time:
        hour, minute = map(int, time_str.split(':'))
        return time(hour, minute)

    def get_current_session(self, current_time: datetime) -> str:
        """Tag trading sessions: Asia, London, NY, London-NY overlap."""
        t = current_time.time()
        
        asia_start = self.parse_time(self.sessions.asia.start)
        asia_end = self.parse_time(self.sessions.asia.end)
        london_start = self.parse_time(self.sessions.london.start)
        london_end = self.parse_time(self.sessions.london.end)
        ny_start = self.parse_time(self.sessions.ny.start)
        ny_end = self.parse_time(self.sessions.ny.end)

        in_asia = asia_start <= t <= asia_end or (asia_start > asia_end and (t >= asia_start or t <= asia_end))
        in_london = london_start <= t <= london_end or (london_start > london_end and (t >= london_start or t <= london_end))
        in_ny = ny_start <= t <= ny_end or (ny_start > ny_end and (t >= ny_start or t <= ny_end))

        if in_london and in_ny:
            return "London-NY Overlap"
        elif in_ny:
            return "NY"
        elif in_london:
            return "London"
        elif in_asia:
            return "Asia"
        return "Off-Session"

    def cache_session_high_low(self, df: pd.DataFrame, session_name: str) -> Tuple[float, float]:
        """Cache prior session highs/lows per day."""
        # Note: df should be 1H or 5M data
        # This is a simplified logic. In a real scenario, you'd group by day and filter by session time
        if df.empty:
            return 0.0, 0.0
            
        high = df['high'].max()
        low = df['low'].min()
        return high, low
