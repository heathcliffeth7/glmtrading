"""PnL Pydantic Schemas"""
from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class DailyPnLRecord(BaseModel):
    """Single day PnL record"""
    date: str
    realized_pnl: float
    unrealized_pnl: float
    total_fees: float
    net_pnl: float


class DailyPnLResponse(BaseModel):
    """Daily PnL list"""
    records: List[DailyPnLRecord]
    total_realized: float
    total_fees: float
