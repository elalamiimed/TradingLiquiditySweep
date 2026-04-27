import pytest
from src.validator.checklist import TradeChecklist

def test_valid_checklist():
    checklist = TradeChecklist(
        session_valid=True,
        news_clear=True,
        sweep_confirmed=True,
        pattern_confirmed=True,
        risk_valid=True,
        direction="long"
    )
    assert checklist.is_valid() is True

def test_invalid_checklist():
    checklist = TradeChecklist(
        session_valid=False, # Failed
        news_clear=True,
        sweep_confirmed=True,
        pattern_confirmed=True,
        risk_valid=True,
        direction="short"
    )
    assert checklist.is_valid() is False
