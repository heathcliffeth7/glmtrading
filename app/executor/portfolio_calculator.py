"""
Portfolio Calculator Module

Handles portfolio metrics calculation, PnL tracking, and position updates.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.executor.ledger import (
    DailyPnL,
    Portfolio,
    StopLossOrder,
    Trade,
    engine,
    get_daily_pnl,
    get_recent_trades,
)
from app.executor.portfolio_sync import get_synced_portfolio
from app.utils.logging import get_logger


logger = get_logger(__name__)


class PortfolioCalculator:
    """
    Calculates and manages portfolio metrics.
    """

    def __init__(
        self,
        symbol: str,
        starting_cash: float = 10000.0,
        taker_fee_rate: float = 0.0005,
    ):
        """
        Initialize the portfolio calculator.

        Args:
            symbol: Trading symbol
            starting_cash: Initial equity
            taker_fee_rate: Trading fee rate
        """
        self._symbol = symbol
        self._starting_cash = starting_cash
        self._taker_fee_rate = taker_fee_rate

    def portfolio_metrics(self, price: float) -> Dict[str, Any]:
        """
        Calculate comprehensive portfolio metrics.

        Args:
            price: Current market price

        Returns:
            Dictionary with portfolio metrics
        """
        with Session(engine) as session:
            portfolio = get_synced_portfolio(session, self._symbol)
            daily_pnl = get_daily_pnl(session)

            # Calculate margin from open trades
            open_trades = (
                session.query(Trade)
                .filter_by(symbol=self._symbol)
                .filter(Trade.close_price.is_(None))
                .all()
            )

            margin_used = 0.0
            total_notional = 0.0
            position_details = []

            # Separate LONG and SHORT trades
            long_trades = [t for t in open_trades if t.position_side == "LONG"]
            short_trades = [t for t in open_trades if t.position_side == "SHORT"]

            # Get latest open trade for backward compatibility
            latest_trade = None
            if open_trades:
                latest_trade = sorted(
                    open_trades, key=lambda t: t.timestamp, reverse=True
                )[0]

            # Calculate LONG position metrics
            long_position = sum(t.amount for t in long_trades)
            long_avg_price = 0.0
            long_exit_plan = None
            long_leverage = 1.0

            if long_trades:
                total_long_value = sum(t.amount * t.price for t in long_trades)
                long_avg_price = (
                    total_long_value / long_position if long_position > 0 else 0.0
                )

                long_trade = sorted(
                    long_trades, key=lambda t: t.timestamp, reverse=True
                )[0]
                long_exit_plan = long_trade.exit_plan
                long_leverage = (
                    long_trade.leverage
                    if long_trade.leverage and long_trade.leverage > 0
                    else 1.0
                )

            # Calculate SHORT position metrics
            short_position = sum(t.amount for t in short_trades)
            short_avg_price = 0.0
            short_exit_plan = None
            short_leverage = 1.0

            if short_trades:
                total_short_value = sum(abs(t.amount) * t.price for t in short_trades)
                total_short_amount = sum(abs(t.amount) for t in short_trades)
                short_avg_price = (
                    total_short_value / total_short_amount
                    if total_short_amount > 0
                    else 0.0
                )

                short_trade = sorted(
                    short_trades, key=lambda t: t.timestamp, reverse=True
                )[0]
                short_exit_plan = short_trade.exit_plan
                short_leverage = (
                    short_trade.leverage
                    if short_trade.leverage and short_trade.leverage > 0
                    else 1.0
                )

            # Calculate margin and notional for all positions
            for trade in open_trades:
                leverage = (
                    trade.leverage if trade.leverage and trade.leverage > 0 else 1.0
                )
                trade_notional = abs(trade.price * trade.amount)
                trade_margin = trade_notional / leverage
                margin_used += trade_margin
                total_notional += trade_notional

                position_details.append(
                    {
                        "amount": trade.amount,
                        "price": trade.price,
                        "leverage": leverage,
                        "notional": trade_notional,
                        "margin": trade_margin,
                        "position_side": trade.position_side,
                    }
                )

            # Get exit plan from latest trade
            exit_plan = None
            if latest_trade and latest_trade.exit_plan:
                exit_plan = latest_trade.exit_plan

            # Get order IDs for latest trade
            sl_oid = -1
            tp_oid = -1
            entry_oid = -1
            if latest_trade:
                entry_oid = latest_trade.id
                sl_orders = (
                    session.query(StopLossOrder)
                    .filter(
                        StopLossOrder.trade_id == latest_trade.id,
                        StopLossOrder.is_active == True,
                        StopLossOrder.triggered == False,
                    )
                    .order_by(StopLossOrder.created_at.desc())
                    .first()
                )
                if sl_orders:
                    sl_oid = sl_orders.id

            exposure = portfolio.position * price
            unrealized = (
                (price - portfolio.average_price) * portfolio.position
                if portfolio.position
                else 0.0
            )

            # Limit unrealized PnL to prevent astronomical values
            max_reasonable_pnl = self._starting_cash * 5
            unrealized = max(-max_reasonable_pnl, min(unrealized, max_reasonable_pnl))

            total_pnl = daily_pnl.realized_pnl + unrealized
            equity = self._starting_cash + total_pnl

            # Equity sanity check
            equity = max(0, min(equity, self._starting_cash * 10))

            # Calculate safe equity from closed trades only
            closed_trades_net_pnl = (
                session.query(func.coalesce(func.sum(Trade.pnl), 0.0))
                .filter(
                    Trade.symbol == self._symbol, Trade.close_price.isnot(None)
                )
                .scalar()
                or 0.0
            )

            safe_equity = self._starting_cash + closed_trades_net_pnl
            safe_equity = max(100, min(safe_equity, self._starting_cash * 10))
            free_cash = safe_equity - margin_used

            # Calculate notional_usd for latest trade
            notional_usd = 0.0
            if latest_trade:
                notional_usd = abs(latest_trade.amount * price)

            result = {
                "price": price,
                "position": portfolio.position,
                "average_price": portfolio.average_price,
                "exposure": exposure,
                "realized_pnl": daily_pnl.realized_pnl,
                "unrealized_pnl": unrealized,
                "total_pnl": total_pnl,
                "equity": equity,
                "margin_used": margin_used,
                "free_cash": free_cash,
                "total_notional": total_notional,
                "position_details": position_details,
                "starting_cash": self._starting_cash,
                "total_fees": daily_pnl.total_fees,
                "last_trade_price": portfolio.last_trade_price,
                "last_trade_timestamp": portfolio.last_trade_timestamp,
                "current_price": price,
                "long_position": long_position,
                "short_position": short_position,
                "net_position": portfolio.position,
                "long_avg_price": long_avg_price,
                "short_avg_price": short_avg_price,
                "long_exit_plan": long_exit_plan,
                "short_exit_plan": short_exit_plan,
                "long_leverage": long_leverage,
                "short_leverage": short_leverage,
                "recent_trades": self.get_recent_trades_with_pnl(price, limit=5),
            }

            # Add exit plan info
            if exit_plan:
                result["exit_plan"] = exit_plan
                if latest_trade:
                    result["entry_price"] = latest_trade.price
                    result["entry_oid"] = entry_oid
                    result["sl_oid"] = sl_oid
                    result["tp_oid"] = tp_oid
                    result["notional_usd"] = notional_usd
                    result["leverage"] = (
                        latest_trade.leverage
                        if latest_trade.leverage and latest_trade.leverage > 0
                        else 10.0
                    )
                    result["confidence"] = None

            return result

    def calculate_pnl(
        self, portfolio: Portfolio, action: str, amount: float, price: float
    ) -> float:
        """
        Calculate realized PnL for a trade.

        Args:
            portfolio: Current portfolio state
            action: Trade action ("BUY" or "SELL")
            amount: Trade amount
            price: Trade price

        Returns:
            Realized PnL (0 if opening/increasing position)
        """
        if portfolio.position == 0:
            return 0.0

        is_long = portfolio.position > 0
        is_closing = (is_long and action == "SELL") or (not is_long and action == "BUY")

        if not is_closing:
            return 0.0

        if is_long:
            return (price - portfolio.average_price) * amount
        else:
            return (portfolio.average_price - price) * amount

    def update_portfolio(
        self, portfolio: Portfolio, action: str, amount: float, price: float
    ) -> None:
        """
        Update portfolio after a trade.

        Args:
            portfolio: Portfolio to update
            action: Trade action ("BUY" or "SELL")
            amount: Trade amount
            price: Trade price
        """
        old_position = portfolio.position

        if action == "BUY":
            portfolio.position += amount
        else:
            portfolio.position -= amount

        # Average price update logic
        if old_position == 0:
            portfolio.average_price = price
        elif (old_position > 0 and action == "BUY") or (
            old_position < 0 and action == "SELL"
        ):
            total_cost = abs(old_position) * portfolio.average_price + amount * price
            portfolio.average_price = total_cost / abs(portfolio.position)
        elif portfolio.position == 0:
            portfolio.average_price = 0.0
        elif (old_position > 0 and portfolio.position < 0) or (
            old_position < 0 and portfolio.position > 0
        ):
            portfolio.average_price = price

        portfolio.updated_at = datetime.utcnow()

    def update_daily_pnl(
        self,
        daily_pnl: DailyPnL,
        pnl: float,
        portfolio: Portfolio,
        price: float,
        fee: float = 0.0,
    ) -> None:
        """
        Update daily PnL tracking.

        Args:
            daily_pnl: DailyPnL record to update
            pnl: Realized PnL from trade
            portfolio: Current portfolio state
            price: Current price
            fee: Trade fee
        """
        daily_pnl.realized_pnl += pnl
        daily_pnl.unrealized_pnl = (
            (price - portfolio.average_price) * portfolio.position
        )
        if fee > 0:
            daily_pnl.total_fees += fee

    def calculate_real_pnl(self, pnl_before_fee: float, fees: float) -> dict:
        """
        Calculate real PnL with fee breakdown.

        Args:
            pnl_before_fee: Gross PnL before fees
            fees: Total fees

        Returns:
            Dictionary with pnl_gross, fees, pnl_net, fee_percentage
        """
        pnl_gross = pnl_before_fee
        total_fees = fees
        pnl_net = pnl_gross - total_fees

        fee_percentage = 0.0
        if total_fees > 0:
            fee_percentage = total_fees * 200  # Estimate

        return {
            "pnl_gross": pnl_gross,
            "fees": total_fees,
            "pnl_net": pnl_net,
            "fee_percentage": fee_percentage,
        }

    def get_recent_trades_with_pnl(
        self, price: float, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get recent trades with calculated PnL.

        Args:
            price: Current market price
            limit: Maximum number of trades to return

        Returns:
            List of trade dictionaries with PnL info
        """
        with Session(engine) as session:
            trades = get_recent_trades(session, self._symbol, limit)

        result = []
        for trade in trades:
            entry_notional = abs((trade.price or 0.0) * (trade.amount or 0.0))
            notional_value = getattr(trade, "notional_value", None)
            if notional_value is None or notional_value <= 0:
                notional_value = entry_notional

            if trade.close_price is not None:
                # Closed position
                trade_pct_pnl = (
                    ((trade.pnl or 0.0) / entry_notional) * 100
                    if entry_notional > 0
                    else 0.0
                )

                fees = trade.fees if trade.fees else 0.0
                pnl_gross = (trade.pnl or 0.0) + fees
                real_pnl = self.calculate_real_pnl(pnl_gross, fees)

                exit_plan = (
                    trade.exit_plan
                    if hasattr(trade, "exit_plan") and trade.exit_plan
                    else {}
                )

                result.append(
                    {
                        "side": trade.side,
                        "amount": trade.amount,
                        "open_price": trade.price,
                        "close_price": trade.close_price,
                        "pnl": trade.pnl,
                        "pnl_pct": trade_pct_pnl,
                        "fee": fees,
                        "is_closed": True,
                        "timestamp": trade.timestamp,
                        "position_id": trade.position_id,
                        "notional": entry_notional,
                        "real_pnl": real_pnl,
                        "stop_loss": exit_plan.get("stop_loss"),
                        "invalidation_condition": exit_plan.get(
                            "invalidation_condition"
                        ),
                        "profit_target": exit_plan.get("profit_target"),
                    }
                )
            else:
                # Open position
                current_pnl = 0.0
                trade_pct_pnl = 0.0

                if trade.side == "BUY":
                    price_diff = price - trade.price
                    current_pnl = (price_diff * trade.amount) + trade.pnl
                else:
                    price_diff = trade.price - price
                    current_pnl = (price_diff * trade.amount) + trade.pnl

                trade_pct_pnl = (
                    (current_pnl / entry_notional) * 100 if entry_notional > 0 else 0.0
                )

                fees = trade.fees if trade.fees else 0.0
                pnl_gross = current_pnl + fees
                real_pnl = self.calculate_real_pnl(pnl_gross, fees)

                exit_plan = (
                    trade.exit_plan
                    if hasattr(trade, "exit_plan") and trade.exit_plan
                    else {}
                )

                result.append(
                    {
                        "side": trade.side,
                        "amount": trade.amount,
                        "open_price": trade.price,
                        "close_price": price,
                        "pnl": current_pnl,
                        "pnl_pct": trade_pct_pnl,
                        "fee": fees,
                        "is_closed": False,
                        "timestamp": trade.timestamp,
                        "position_id": trade.position_id,
                        "notional": entry_notional,
                        "real_pnl": real_pnl,
                        "stop_loss": exit_plan.get("stop_loss"),
                        "invalidation_condition": exit_plan.get(
                            "invalidation_condition"
                        ),
                        "profit_target": exit_plan.get("profit_target"),
                    }
                )

        return result
