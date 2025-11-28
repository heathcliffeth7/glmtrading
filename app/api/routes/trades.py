"""Trades API Routes"""
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.dependencies import get_db
from app.api.schemas.trade import (
    TradeResponse,
    TradeListResponse,
    PnLHistogramResponse,
    HistogramBin,
    PnLStats,
)
from app.executor.ledger import Trade

router = APIRouter()

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@router.get("", response_model=TradeListResponse)
async def get_trades(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    status: str = Query("all", regex="^(open|closed|all)$", description="Trade status filter"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db)
):
    """Get paginated trade list"""
    query = db.query(Trade)

    # Filter by symbol
    if symbol:
        query = query.filter(Trade.symbol == symbol.upper())

    # Filter by status
    if status == "open":
        query = query.filter(Trade.close_price.is_(None))
    elif status == "closed":
        query = query.filter(Trade.close_price.isnot(None))

    # Get total count
    total = query.count()

    # Paginate
    offset = (page - 1) * per_page
    trades = query.order_by(Trade.timestamp.desc()).offset(offset).limit(per_page).all()

    # Convert to response
    trade_responses = []
    for t in trades:
        pnl_pct = None
        if t.pnl and t.price and t.amount:
            position_value = t.price * t.amount
            if position_value > 0:
                pnl_pct = (t.pnl / position_value) * 100

        trade_responses.append(TradeResponse(
            id=t.id,
            position_id=t.position_id,
            symbol=t.symbol,
            side=t.side,
            position_side=t.position_side,
            amount=t.amount,
            entry_price=t.price,
            close_price=t.close_price,
            pnl=t.pnl or 0.0,
            pnl_pct=round(pnl_pct, 2) if pnl_pct else None,
            leverage=t.leverage or 1.0,
            fees=t.fees or 0.0,
            timestamp=t.timestamp.isoformat() if t.timestamp else "",
            close_time=t.close_time.isoformat() if t.close_time else None,
            exit_plan=t.exit_plan,
        ))

    return TradeListResponse(
        trades=trade_responses,
        total=total,
        page=page,
        per_page=per_page,
        has_more=(offset + per_page) < total,
    )


@router.get("/pnl-histogram", response_model=PnLHistogramResponse)
async def get_pnl_histogram(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    days: int = Query(30, ge=1, le=365, description="Days of history"),
    bins: int = Query(10, ge=5, le=50, description="Number of histogram bins"),
    db: Session = Depends(get_db)
):
    """Get PnL distribution histogram"""
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = db.query(Trade).filter(
        Trade.close_price.isnot(None),
        Trade.close_time >= cutoff
    )

    if symbol:
        query = query.filter(Trade.symbol == symbol.upper())

    trades = query.all()
    pnls = [t.pnl for t in trades if t.pnl is not None]

    if not pnls:
        return PnLHistogramResponse(
            symbol=symbol.upper() if symbol else None,
            period_days=days,
            histogram=[],
            stats=PnLStats(
                total_trades=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                avg_pnl=0.0,
                avg_winner=0.0,
                avg_loser=0.0,
                max_profit=0.0,
                max_loss=0.0,
                profit_factor=0.0,
            ),
        )

    # Calculate histogram
    counts, bin_edges = np.histogram(pnls, bins=bins)
    histogram = [
        HistogramBin(
            range_min=round(bin_edges[i], 2),
            range_max=round(bin_edges[i + 1], 2),
            count=int(counts[i]),
        )
        for i in range(len(counts))
    ]

    # Calculate statistics
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]

    win_rate = len(winners) / len(pnls) if pnls else 0.0
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
    avg_winner = sum(winners) / len(winners) if winners else 0.0
    avg_loser = sum(losers) / len(losers) if losers else 0.0
    profit_factor = abs(sum(winners) / sum(losers)) if losers and sum(losers) != 0 else 0.0

    return PnLHistogramResponse(
        symbol=symbol.upper() if symbol else None,
        period_days=days,
        histogram=histogram,
        stats=PnLStats(
            total_trades=len(pnls),
            win_count=len(winners),
            loss_count=len(losers),
            win_rate=round(win_rate, 4),
            avg_pnl=round(avg_pnl, 2),
            avg_winner=round(avg_winner, 2),
            avg_loser=round(avg_loser, 2),
            max_profit=round(max(pnls), 2),
            max_loss=round(min(pnls), 2),
            profit_factor=round(profit_factor, 2),
        ),
    )


@router.get("/{trade_id}")
async def get_trade_detail(
    trade_id: int,
    db: Session = Depends(get_db)
):
    """Get single trade details"""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trade not found")

    return {
        "id": trade.id,
        "position_id": trade.position_id,
        "symbol": trade.symbol,
        "side": trade.side,
        "position_side": trade.position_side,
        "amount": trade.amount,
        "entry_price": trade.price,
        "close_price": trade.close_price,
        "pnl": trade.pnl,
        "leverage": trade.leverage,
        "fees": trade.fees,
        "notional_value": trade.notional_value,
        "exit_plan": trade.exit_plan,
        "exit_plan_history": trade.exit_plan_history,
        "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
        "close_time": trade.close_time.isoformat() if trade.close_time else None,
    }
