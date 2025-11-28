"""PnL API Routes"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.dependencies import get_db
from app.api.schemas.pnl import DailyPnLResponse, DailyPnLRecord
from app.executor.ledger import DailyPnL, Trade

router = APIRouter()


@router.get("/daily", response_model=DailyPnLResponse)
async def get_daily_pnl(
    days: int = Query(30, ge=1, le=365, description="Days of history"),
    db: Session = Depends(get_db)
):
    """Get daily PnL records"""
    cutoff = datetime.utcnow().date() - timedelta(days=days)

    records = db.query(DailyPnL).filter(
        DailyPnL.date >= cutoff
    ).order_by(DailyPnL.date.desc()).all()

    total_realized = sum(r.realized_pnl or 0.0 for r in records)
    total_fees = sum(r.total_fees or 0.0 for r in records)

    return DailyPnLResponse(
        records=[
            DailyPnLRecord(
                date=r.date.isoformat(),
                realized_pnl=r.realized_pnl or 0.0,
                unrealized_pnl=r.unrealized_pnl or 0.0,
                total_fees=r.total_fees or 0.0,
                net_pnl=(r.realized_pnl or 0.0) - (r.total_fees or 0.0),
            )
            for r in records
        ],
        total_realized=round(total_realized, 2),
        total_fees=round(total_fees, 2),
    )


@router.get("/summary")
async def get_pnl_summary(db: Session = Depends(get_db)):
    """Get overall PnL summary"""
    # Get total realized PnL from closed trades
    total_realized = db.query(func.sum(Trade.pnl)).filter(
        Trade.close_price.isnot(None)
    ).scalar() or 0.0

    # Get total fees
    total_fees = db.query(func.sum(Trade.fees)).scalar() or 0.0

    # Get trade counts
    total_trades = db.query(func.count(Trade.id)).filter(
        Trade.close_price.isnot(None)
    ).scalar() or 0

    winning_trades = db.query(func.count(Trade.id)).filter(
        Trade.close_price.isnot(None),
        Trade.pnl > 0
    ).scalar() or 0

    losing_trades = db.query(func.count(Trade.id)).filter(
        Trade.close_price.isnot(None),
        Trade.pnl < 0
    ).scalar() or 0

    # Get best/worst trades
    best_trade = db.query(Trade).filter(
        Trade.close_price.isnot(None)
    ).order_by(Trade.pnl.desc()).first()

    worst_trade = db.query(Trade).filter(
        Trade.close_price.isnot(None)
    ).order_by(Trade.pnl.asc()).first()

    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

    return {
        "total_realized_pnl": round(total_realized, 2),
        "total_fees": round(total_fees, 2),
        "net_pnl": round(total_realized - total_fees, 2),
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate * 100, 2),
        "best_trade": {
            "id": best_trade.id,
            "symbol": best_trade.symbol,
            "pnl": best_trade.pnl,
        } if best_trade else None,
        "worst_trade": {
            "id": worst_trade.id,
            "symbol": worst_trade.symbol,
            "pnl": worst_trade.pnl,
        } if worst_trade else None,
    }
