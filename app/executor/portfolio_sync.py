"""
Portfolio synchronization module for maintaining consistency between trades and portfolio tables.
This should be called before each trade execution to ensure accurate position tracking.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.executor.ledger import Portfolio, Trade

logger = logging.getLogger(__name__)

def calculate_actual_position_from_trades(session: Session, symbol: str) -> Dict[str, float]:
    """
    Gerçek long/short pozisyonları açık (close_price IS NULL) işlemler üzerinden hesapla.
    YENİ: Long ve short pozisyonları ayrı takip eder (hedge desteği).
    """

    # Yalnızca AÇIK işlemleri (close_price IS NULL) kronolojik sırada al
    open_trades = (
        session.query(Trade)
        .filter(Trade.symbol == symbol)
        .filter(Trade.close_price.is_(None))
        .order_by(Trade.timestamp.asc())
        .all()
    )

    if not open_trades:
        return {
            "position": 0.0,
            "average_price": 0.0,
            "total_cost": 0.0,
            "long_position": 0.0,
            "long_avg_price": None,
            "short_position": 0.0,
            "short_avg_price": None,
            "net_position": 0.0,
            "last_trade_price": None,
            "last_trade_timestamp": None,
        }

    # Long ve short trade'leri ayır
    long_trades = [t for t in open_trades if t.position_side == "LONG"]
    short_trades = [t for t in open_trades if t.position_side == "SHORT"]

    # Long pozisyon hesaplama
    long_position = 0.0
    long_total_cost = 0.0
    long_avg_price = None

    for trade in long_trades:
        amount = float(trade.amount)
        price = float(trade.price)

        # Güvenlik: anormal büyük miktarları yok say
        if amount > 2.0:
            logger.warning(
                "Ignoring abnormal LONG trade amount: amount=%.6f price=%.2f id=%s",
                amount, price, getattr(trade, "id", None)
            )
            continue

        if trade.side == "BUY":
            long_position += amount
            long_total_cost += amount * price

    if long_position > 0.0001:
        long_avg_price = long_total_cost / long_position

    # Short pozisyon hesaplama
    short_position = 0.0
    short_total_cost = 0.0
    short_avg_price = None

    for trade in short_trades:
        amount = float(trade.amount)
        price = float(trade.price)

        # Güvenlik: anormal büyük miktarları yok say
        if amount > 2.0:
            logger.warning(
                "Ignoring abnormal SHORT trade amount: amount=%.6f price=%.2f id=%s",
                amount, price, getattr(trade, "id", None)
            )
            continue

        if trade.side == "SELL":
            short_position -= amount
            short_total_cost += amount * price

    if abs(short_position) > 0.0001:
        short_avg_price = short_total_cost / abs(short_position)

    # Net pozisyon hesapla
    net_position = long_position + short_position

    # Backward compatibility için eski position değeri
    position = net_position
    average_price = long_avg_price if long_avg_price else (short_avg_price if short_avg_price else 0.0)
    total_cost = long_total_cost + short_total_cost

    # Son işlem bilgisi (TÜM trade'lerden, açık olanlar da dahil)
    all_trades = (
        session.query(Trade)
        .filter(Trade.symbol == symbol)
        .order_by(Trade.timestamp.desc())
        .first()
    )
    
    last_trade_price = float(all_trades.price) if all_trades else None
    last_trade_timestamp = all_trades.timestamp if all_trades else None

    return {
        "position": position,
        "average_price": average_price,
        "total_cost": total_cost,
        "long_position": long_position,
        "long_avg_price": long_avg_price,
        "short_position": short_position,
        "short_avg_price": short_avg_price,
        "net_position": net_position,
        "last_trade_price": last_trade_price,
        "last_trade_timestamp": last_trade_timestamp,
    }

def sync_portfolio_with_trades(session: Session, symbol: str) -> Portfolio:
    """
    Synchronize portfolio table with actual trades and return the correct portfolio state.
    YENİ: Long/short pozisyonları ve last_trade bilgilerini günceller.
    """
    
    # Get current portfolio state
    portfolio = session.query(Portfolio).filter(Portfolio.symbol == symbol).first()
    if not portfolio:
        # Create portfolio entry if it doesn't exist
        portfolio = Portfolio(
            symbol=symbol,
            position=0.0,
            average_price=0.0,
            long_position=0.0,
            long_avg_price=None,
            short_position=0.0,
            short_avg_price=None,
            net_position=0.0,
            last_trade_price=None,
            last_trade_timestamp=None,
        )
        session.add(portfolio)
    
    # Calculate actual state from trades
    actual_state = calculate_actual_position_from_trades(session, symbol)
    
    # Check if synchronization is needed (backward compatibility için position kontrolü)
    position_diff = abs(portfolio.position - actual_state['position'])
    long_diff = abs((portfolio.long_position or 0.0) - actual_state['long_position'])
    short_diff = abs((portfolio.short_position or 0.0) - actual_state['short_position'])
    
    sync_needed = (
        position_diff > 0.0001 or
        long_diff > 0.0001 or
        short_diff > 0.0001
    )
    
    if sync_needed:
        logger.info(
            "Portfolio sync needed for %s: "
            "position=%.6f→%.6f, long=%.6f→%.6f, short=%.6f→%.6f",
            symbol,
            portfolio.position,
            actual_state['position'],
            portfolio.long_position or 0.0,
            actual_state['long_position'],
            portfolio.short_position or 0.0,
            actual_state['short_position']
        )
        
        # Update portfolio with correct values
        portfolio.position = actual_state['position']
        portfolio.average_price = actual_state['average_price']
        portfolio.long_position = actual_state['long_position']
        portfolio.long_avg_price = actual_state['long_avg_price']
        portfolio.short_position = actual_state['short_position']
        portfolio.short_avg_price = actual_state['short_avg_price']
        portfolio.net_position = actual_state['net_position']
        portfolio.last_trade_price = actual_state['last_trade_price']
        portfolio.last_trade_timestamp = actual_state['last_trade_timestamp']
        portfolio.updated_at = datetime.utcnow()
        
        session.flush()
        
        logger.info(
            "Portfolio synchronized for %s: "
            "long=%.6f@%.2f, short=%.6f@%.2f, net=%.6f",
            symbol,
            portfolio.long_position,
            portfolio.long_avg_price or 0.0,
            portfolio.short_position,
            portfolio.short_avg_price or 0.0,
            portfolio.net_position
        )
    
    return portfolio

def get_synced_portfolio(session: Session, symbol: str) -> Portfolio:
    """
    Get portfolio with automatic synchronization.
    This is the main entry point that should be used instead of direct get_portfolio calls.
    """
    return sync_portfolio_with_trades(session, symbol)

def calculate_correct_margin_usage(portfolio: Portfolio, current_price: float, leverage: float) -> float:
    """
    Calculate correct margin usage based on current position and current price.
    Uses the synced portfolio position to ensure accuracy.
    """
    
    if leverage <= 0:
        leverage = 1.0
    
    position_value = abs(portfolio.position * current_price)
    used_margin = position_value / leverage if position_value > 0 else 0.0
    
    return used_margin

def validate_portfolio_consistency(session: Session, symbol: str) -> bool:
    """
    Validate that portfolio is consistent with trades.
    Returns True if consistent, False if issues detected.
    """
    
    portfolio = session.query(Portfolio).filter(Portfolio.symbol == symbol).first()
    if not portfolio:
        return True  # No portfolio is consistent with no trades
    
    actual_state = calculate_actual_position_from_trades(session, symbol)
    
    position_diff = abs(portfolio.position - actual_state['position'])
    return position_diff <= 0.0001
