"""Portfolio API Routes"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.dependencies import get_db
from app.api.schemas.portfolio import PortfolioSummary, SymbolPosition, EquityCurveResponse, EquityCurvePoint
from app.executor.ledger import Trade, Portfolio, DailyPnL
from app.executor.portfolio_sync import get_synced_portfolio

router = APIRouter()

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INITIAL_CAPITAL_PER_SYMBOL = 10000.0  # Her sembol için ayrı başlangıç bakiyesi


async def get_current_price(symbol: str) -> float:
    """Get current price from Binance (async)"""
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=5.0
            )
            return float(response.json()["price"])
    except Exception:
        return 0.0


@router.get("", response_model=PortfolioSummary)
async def get_portfolio_summary(db: Session = Depends(get_db)):
    """Get portfolio summary across all symbols"""
    symbol_data = []

    for symbol in SYMBOLS:
        portfolio = get_synced_portfolio(db, symbol)
        current_price = await get_current_price(symbol)

        # Bu sembol için realized PnL
        symbol_realized = db.query(func.sum(Trade.pnl)).filter(
            Trade.symbol == symbol,
            Trade.close_price.isnot(None)
        ).scalar() or 0.0

        if abs(portfolio.position) > 0.0001 and current_price > 0:
            unrealized_pnl = (current_price - portfolio.average_price) * portfolio.position
            unrealized_pnl_pct = (
                ((current_price - portfolio.average_price) / portfolio.average_price) * 100
                if portfolio.average_price > 0 else 0.0
            )
            margin_used = abs(portfolio.position * portfolio.average_price) / 10  # Assuming 10x default
            position_side = "LONG" if portfolio.position > 0 else "SHORT"
        else:
            unrealized_pnl = 0.0
            unrealized_pnl_pct = 0.0
            margin_used = 0.0
            position_side = "FLAT"

        # Bu sembolün equity'si = 10K + sembol PnL
        symbol_equity = INITIAL_CAPITAL_PER_SYMBOL + symbol_realized + unrealized_pnl

        symbol_data.append(SymbolPosition(
            symbol=symbol,
            position=portfolio.position,
            position_side=position_side,
            entry_price=portfolio.average_price,
            current_price=current_price,
            unrealized_pnl=round(unrealized_pnl, 2),
            unrealized_pnl_pct=round(unrealized_pnl_pct, 2),
            realized_pnl=round(symbol_realized, 2),
            equity=round(symbol_equity, 2),
            margin_used=round(margin_used, 2),
            leverage=10.0,
        ))

    # Toplam değerler = tüm sembollerin toplamı
    total_equity = sum(s.equity for s in symbol_data)
    total_realized = sum(s.realized_pnl for s in symbol_data)
    total_unrealized = sum(s.unrealized_pnl for s in symbol_data)

    return PortfolioSummary(
        total_equity=round(total_equity, 2),
        total_unrealized_pnl=round(total_unrealized, 2),
        total_realized_pnl=round(total_realized, 2),
        symbols=symbol_data,
        updated_at=datetime.utcnow().isoformat(),
    )


@router.get("/equity-curve", response_model=EquityCurveResponse)
async def get_equity_curve(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    days: int = Query(30, ge=1, le=365, description="Days of history"),
    db: Session = Depends(get_db)
):
    """Get equity curve time series from closed trades"""
    from datetime import timedelta, date as date_type

    cutoff = datetime.utcnow() - timedelta(days=days)

    # Build query for daily PnL aggregation
    query = db.query(
        func.date(Trade.close_time).label("date"),
        func.sum(Trade.pnl).label("daily_pnl"),
        func.count(Trade.id).label("trade_count")
    ).filter(
        Trade.close_price.isnot(None),
        Trade.close_time >= cutoff
    )

    if symbol:
        query = query.filter(Trade.symbol == symbol.upper())

    results = query.group_by(func.date(Trade.close_time)).order_by(func.date(Trade.close_time)).all()

    # Calculate cumulative equity
    # Toplam başlangıç = 3 sembol x 10K = 30K (veya symbol filtresi varsa 10K)
    initial_capital = INITIAL_CAPITAL_PER_SYMBOL if symbol else (INITIAL_CAPITAL_PER_SYMBOL * len(SYMBOLS))
    cumulative_pnl = 0.0
    data_points = []

    # Always add starting point first (one day before first trade or 7 days ago)
    if results:
        first_trade_date = results[0].date
        start_date = first_trade_date - timedelta(days=1)
    else:
        start_date = date_type.today() - timedelta(days=min(days, 7))

    data_points.append(EquityCurvePoint(
        timestamp=start_date.isoformat(),
        equity=initial_capital,
        pnl=0.0,
        trade_count=0,
    ))

    for row in results:
        cumulative_pnl += row.daily_pnl or 0.0
        data_points.append(EquityCurvePoint(
            timestamp=row.date.isoformat() if row.date else "",
            equity=round(initial_capital + cumulative_pnl, 2),
            pnl=round(row.daily_pnl or 0.0, 2),
            trade_count=row.trade_count,
        ))

    # If only starting point exists (no trades), add current point with unrealized PnL
    if len(data_points) == 1:
        total_unrealized = 0.0
        symbols_to_check = [symbol.upper()] if symbol else SYMBOLS
        for sym in symbols_to_check:
            portfolio = get_synced_portfolio(db, sym)
            if abs(portfolio.position) > 0.0001:
                current_price = await get_current_price(sym)
                if current_price > 0:
                    total_unrealized += (current_price - portfolio.average_price) * portfolio.position

        # Get total realized PnL (sembol filtresi varsa sadece o sembol)
        realized_query = db.query(func.sum(Trade.pnl)).filter(Trade.close_price.isnot(None))
        if symbol:
            realized_query = realized_query.filter(Trade.symbol == symbol.upper())
        total_realized = realized_query.scalar() or 0.0

        current_equity = initial_capital + total_realized + total_unrealized
        today = date_type.today()

        # Add current point (starting point already added above)
        data_points.append(EquityCurvePoint(
            timestamp=today.isoformat(),
            equity=round(current_equity, 2),
            pnl=round(total_realized + total_unrealized, 2),
            trade_count=0,
        ))

    return EquityCurveResponse(
        symbol=symbol.upper() if symbol else None,
        interval="1d",
        data=data_points,
    )


@router.get("/{symbol}")
async def get_portfolio_by_symbol(
    symbol: str,
    db: Session = Depends(get_db)
):
    """Get detailed portfolio for a specific symbol"""
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

    portfolio = get_synced_portfolio(db, symbol)
    current_price = await get_current_price(symbol)

    # Get open trades for this symbol
    open_trades = db.query(Trade).filter(
        Trade.symbol == symbol,
        Trade.close_price.is_(None)
    ).order_by(Trade.timestamp.desc()).all()

    # Get recent closed trades
    closed_trades = db.query(Trade).filter(
        Trade.symbol == symbol,
        Trade.close_price.isnot(None)
    ).order_by(Trade.close_time.desc()).limit(10).all()

    unrealized_pnl = 0.0
    if abs(portfolio.position) > 0.0001 and current_price > 0:
        unrealized_pnl = (current_price - portfolio.average_price) * portfolio.position

    return {
        "symbol": symbol,
        "position": portfolio.position,
        "long_position": portfolio.long_position,
        "short_position": portfolio.short_position,
        "average_price": portfolio.average_price,
        "current_price": current_price,
        "unrealized_pnl": round(unrealized_pnl, 2),
        "open_trades_count": len(open_trades),
        "recent_trades": [
            {
                "id": t.id,
                "position_id": t.position_id,
                "side": t.side,
                "amount": t.amount,
                "price": t.price,
                "close_price": t.close_price,
                "pnl": t.pnl,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "close_time": t.close_time.isoformat() if t.close_time else None,
            }
            for t in (closed_trades[:5] if closed_trades else [])
        ],
        "updated_at": portfolio.updated_at.isoformat() if portfolio.updated_at else None,
    }
