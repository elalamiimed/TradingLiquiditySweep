from pydantic import BaseModel, Field, field_validator
from typing import Optional
from src.utils.logger import logger

class TradeChecklist(BaseModel):
    """
    Sequential checks (all must pass):
    ✅ Session filter (prefer London-NY overlap)
    ✅ Macro/news blackout window clear
    ✅ Level identified & sweep confirmed
    ✅ Rejection/pattern/MSS/FVG confirmed on 5M/1M
    ✅ Risk/SL/TP calculated within limits
    """
    session_valid: bool
    news_clear: bool
    sweep_confirmed: bool
    pattern_confirmed: bool
    risk_valid: bool
    
    # Store the actual signal direction
    direction: Optional[str] = None
    
    @field_validator('*')
    def validate_all_true(cls, v, info):
        if info.field_name != 'direction' and not v:
            logger.info(f"Checklist failed at: {info.field_name}")
        return v

    def is_valid(self) -> bool:
        """Returns True only if all checklist criteria are met."""
        valid = (
            self.session_valid and
            self.news_clear and
            self.sweep_confirmed and
            self.pattern_confirmed and
            self.risk_valid
        )
        if valid:
            logger.info("All pre-trade checklist criteria passed. Trade is VALID.")
        else:
            logger.info("Pre-trade checklist failed.")
        return valid
