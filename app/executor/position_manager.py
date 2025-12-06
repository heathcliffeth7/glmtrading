"""
Position Manager Module

Handles position tracking and retrieval from the database.
"""

from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.executor.ledger import Trade, get_open_position_details
from app.utils.logging import get_logger


logger = get_logger(__name__)


class PositionManager:
    """
    Manages position tracking and retrieval.
    """

    def __init__(self, symbol: str):
        """
        Initialize the position manager.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
        """
        self._symbol = symbol

    def get_current_position_id(self, session: Session) -> Optional[str]:
        """
        DEPRECATED: Assumes single position. Use get_position_id_by_side instead.

        Get the position ID for the most recent open trade.

        Args:
            session: Database session

        Returns:
            Position ID if found, None otherwise
        """
        open_trade = (
            session.query(Trade)
            .filter_by(symbol=self._symbol)
            .filter(Trade.close_price.is_(None))
            .filter(Trade.position_id.isnot(None))
            .order_by(Trade.timestamp.desc())
            .first()
        )
        return open_trade.position_id if open_trade else None

    def get_position_id_by_side(
        self, session: Session, position_side: str
    ) -> Optional[str]:
        """
        Get the position ID for a specific side (LONG/SHORT).

        Args:
            session: Database session
            position_side: "LONG" or "SHORT"

        Returns:
            Position ID if found, None otherwise
        """
        open_trade = (
            session.query(Trade)
            .filter_by(symbol=self._symbol, position_side=position_side)
            .filter(Trade.close_price.is_(None))
            .filter(Trade.position_id.isnot(None))
            .order_by(Trade.timestamp.desc())
            .first()
        )
        return open_trade.position_id if open_trade else None

    def get_all_open_position_ids(self, session: Session) -> Dict[str, str]:
        """
        Get all open position IDs mapped by position side.

        Args:
            session: Database session

        Returns:
            Dictionary mapping position_side to position_id
        """
        open_trades = (
            session.query(Trade)
            .filter_by(symbol=self._symbol)
            .filter(Trade.close_price.is_(None))
            .filter(Trade.position_id.isnot(None))
            .all()
        )

        result = {}
        for trade in open_trades:
            if trade.position_side and trade.position_id:
                result[trade.position_side] = trade.position_id
        return result

    def get_position_details(self, session: Session, price: float) -> dict:
        """
        Get detailed position information.

        Args:
            session: Database session
            price: Current market price

        Returns:
            Dictionary with position details
        """
        return get_open_position_details(session, self._symbol, price)

    def has_open_position(self, session: Session, position_side: str) -> bool:
        """
        Check if there's an open position for a specific side.

        Args:
            session: Database session
            position_side: "LONG" or "SHORT"

        Returns:
            True if position exists, False otherwise
        """
        count = (
            session.query(Trade)
            .filter_by(symbol=self._symbol, position_side=position_side)
            .filter(Trade.close_price.is_(None))
            .count()
        )
        return count > 0

    def get_open_trades(self, session: Session, position_side: str = None) -> list:
        """
        Get all open trades, optionally filtered by position side.

        Args:
            session: Database session
            position_side: Optional filter for "LONG" or "SHORT"

        Returns:
            List of open Trade objects
        """
        query = (
            session.query(Trade)
            .filter_by(symbol=self._symbol)
            .filter(Trade.close_price.is_(None))
        )

        if position_side:
            query = query.filter_by(position_side=position_side)

        return query.order_by(Trade.timestamp.desc()).all()
