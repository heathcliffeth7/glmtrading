"""Portfolio Pydantic Schemas"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SymbolPosition(BaseModel):
    """Single symbol position details"""
    symbol: str
    position: float
    position_side: str = Field(..., pattern="^(LONG|SHORT|FLAT)$")
    entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    realized_pnl: float = 0.0  # Bu sembolün realized PnL'i
    equity: float = 10000.0  # Bu sembolün equity'si (10K + PnL)
    margin_used: float
    leverage: float = 1.0


class PortfolioSummary(BaseModel):
    """Portfolio summary across all symbols"""
    total_equity: float
    total_unrealized_pnl: float
    total_realized_pnl: float
    symbols: List[SymbolPosition]
    updated_at: str


class EquityCurvePoint(BaseModel):
    """Single point on the equity curve"""
    timestamp: str
    equity: float
    pnl: float
    trade_count: Optional[int] = None


class EquityCurveResponse(BaseModel):
    """Equity curve time series"""
    symbol: Optional[str] = None
    interval: str
    data: List[EquityCurvePoint]
