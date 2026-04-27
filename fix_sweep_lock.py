#!/usr/bin/env python3
"""Fix permanent sweep lock: add cooldown so levels can re-sweep."""

with open('src/engine/session_liquidity.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1) Add sweep_cooldown_bars param to __init__
content = content.replace(
    'def __init__(self, stale_bar_limit: int = 3) -> None:',
    'def __init__(self, stale_bar_limit: int = 3, sweep_cooldown_bars: int = 5) -> None:'
)

content = content.replace(
    'self._stale_bar_limit = max(1, stale_bar_limit)',
    'self._stale_bar_limit = max(1, stale_bar_limit)\n        self._sweep_cooldown_bars = max(1, sweep_cooldown_bars)'
)

# 2) Add sweep_cooldowns tracking list after _last_5m_bar
content = content.replace(
    'self._last_5m_bar: Optional[int] = None\n\n        # ── thread safety ──',
    'self._last_5m_bar: Optional[int] = None\n\n        # ── sweep cooldown tracking ──\n        self._sweep_cooldowns = []  # (level, direction, unlock_bar)\n\n        # ── thread safety ──'
)

# 3) Clear cooldowns on daily reset
content = content.replace(
    'self._pending.clear()\n            self._last_5m_bar = None\n            logger.info("Daily session reset', 
    'self._pending.clear()\n            self._sweep_cooldowns.clear()\n            self._last_5m_bar = None\n            logger.info("Daily session reset'
)

# 4) Clear cooldowns on force_daily_reset
content = content.replace(
    'self._pending.clear()\n            self._last_5m_bar = None\n            logger.info("Forced daily reset',
    'self._pending.clear()\n            self._sweep_cooldowns.clear()\n            self._last_5m_bar = None\n            logger.info("Forced daily reset'
)

# 5) Add cooldown refresh at top of detect_sweep before bullish check
content = content.replace(
    '''signal = SweepSignal(
                timestamp=timestamp,
                bar_index=bar_index_5m,
                reference_session=ref.session_name,
            )

            # --- Bullish sweep: wick below low, close back above ---''',
    '''signal = SweepSignal(
                timestamp=timestamp,
                bar_index=bar_index_5m,
                reference_session=ref.session_name,
            )

            # ── Refresh sweep cooldowns: unlock levels whose cooldown has expired ──
            new_cooldowns = []
            for cd_level, cd_dir, cd_unlock_bar in self._sweep_cooldowns:
                if bar_index_5m >= cd_unlock_bar:
                    self._unlock_level_by_dir(ref, cd_dir, cd_level)
                else:
                    new_cooldowns.append((cd_level, cd_dir, cd_unlock_bar))
            self._sweep_cooldowns = new_cooldowns

            # --- Bullish sweep: wick below low, close back above ---'''
)

# 6) Schedule cooldown after bullish sweep confirmation
content = content.replace(
    '''signal.confirmed = True
                self._remove_pending(ref.session_low, int(SweepDirection.BEARISH))
                logger.info(
                    "Bullish sweep confirmed''',
    '''signal.confirmed = True
                self._remove_pending(ref.session_low, int(SweepDirection.BEARISH))
                self._sweep_cooldowns.append((ref.session_low, int(SweepDirection.BULLISH), bar_index_5m + self._sweep_cooldown_bars))
                logger.info(
                    "Bullish sweep confirmed'''
)

# 7) Schedule cooldown after bearish sweep confirmation
content = content.replace(
    '''signal.confirmed = True
                self._remove_pending(ref.session_high, int(SweepDirection.BULLISH))
                logger.info(
                    "Bearish sweep confirmed''',
    '''signal.confirmed = True
                self._remove_pending(ref.session_high, int(SweepDirection.BULLISH))
                self._sweep_cooldowns.append((ref.session_high, int(SweepDirection.BEARISH), bar_index_5m + self._sweep_cooldown_bars))
                logger.info(
                    "Bearish sweep confirmed'''
)

# 8) Add _unlock_level_by_dir method before _remove_pending
content = content.replace(
    '''    def _remove_pending(self, level: float, direction: int) -> None:
        """Remove a pending breach once a confirmed sweep closes it out."""
        self._pending = [
            pb for pb in self._pending
            if not (pb.level == level and pb.direction == direction)
        ]''',
    '''    def _unlock_level_by_dir(self, ref, direction: int, level: float) -> None:
        """Reset the swept flag on a level after cooldown expires."""
        if direction == int(SweepDirection.BULLISH) and ref.low_swept:
            ref.low_swept = False
            logger.debug("Level %.5f (bullish) unlocked for re-sweep after cooldown", level)
        elif direction == int(SweepDirection.BEARISH) and ref.high_swept:
            ref.high_swept = False
            logger.debug("Level %.5f (bearish) unlocked for re-sweep after cooldown", level)

    def _remove_pending(self, level: float, direction: int) -> None:
        """Remove a pending breach once a confirmed sweep closes it out."""
        self._pending = [
            pb for pb in self._pending
            if not (pb.level == level and pb.direction == direction)
        ]'''
)


with open('src/engine/session_liquidity.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched session_liquidity.py successfully")