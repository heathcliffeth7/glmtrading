"""Trade Pydantic Schemas"""
from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel


class TradeResponse(BaseModel):
    """Single trade record"""
    id: int
    position_id: Optional[str] = None
    symbol: str
    side: str
    position_side: Optional[str] = None
    amount: float
    entry_price: float
    close_price: Optional[float] = None
    pnl: float
    pnl_pct: Optional[float] = None
    leverage: float
    fees: float
    timestamp: str
    close_time: Optional[str] = None
    exit_plan: Optional[Dict[str, Any]] = None
    exit_reasoning: Optional[str] = None
    is_partial_close: bool = False
    action_label: Optional[str] = None
    remaining_amount: Optional[float] = None


class TradeListResponse(BaseModel):
    """Paginated trade list"""
    trades: List[TradeResponse]
    total: int
    page: int
    per_page: int
    has_more: bool


class HistogramBin(BaseModel):
    """Single histogram bin"""
    range_min: float
    range_max: float
    count: int


class PnLStats(BaseModel):
    """PnL statistics"""
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_pnl: float
    avg_winner: float
    avg_loser: float
    max_profit: float
    max_loss: float
    profit_factor: float


class PnLHistogramResponse(BaseModel):
    """PnL histogram with statistics"""
    symbol: Optional[str] = None
    period_days: int
    histogram: List[HistogramBin]
    stats: PnLStats
