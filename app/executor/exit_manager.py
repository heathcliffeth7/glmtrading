"""
Exit Manager Module

Handles position closing, stop-loss monitoring, and exit plan execution.
"""

import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.executor.execution_result import ExecutionResult
from app.executor.ledger import (
    DailyPnL,
    Portfolio,
    Trade,
    StopLossNotification,
    close_open_trades,
    create_stop_loss_notification,
    engine,
    get_active_stop_loss_orders,
    get_daily_pnl,
    trigger_stop_loss_order,
)
from app.executor.portfolio_sync import get_synced_portfolio
from app.executor.trade_logger import log_trade_to_file
from app.utils.logging import get_logger
from app.utils.price_cache import price_cache
from app.utils.telegram import telegram_client


logger = get_logger(__name__)


class ExitManager:
    """
    Manages position exits, stop-loss monitoring, and partial closes.
    """

    def __init__(
        self,
        symbol: str,
        taker_fee_rate: float = 0.0005,
        close_cooldown_seconds: int = 60,
    ):
        """
        Initialize the exit manager.

        Args:
            symbol: Trading symbol
            taker_fee_rate: Trading fee rate
            close_cooldown_seconds: Cooldown period after close
        """
        self._symbol = symbol
        self._taker_fee_rate = taker_fee_rate
        self._close_cooldown_seconds = close_cooldown_seconds

        # State tracking
        self._last_close_time: Optional[datetime] = None
        self._symbol_close_timestamps: Dict[str, datetime] = {}
        self._closing_in_progress: Dict[str, bool] = {}
        self._close_lock = threading.Lock()

        # Stop-loss monitoring
        self._stop_loss_monitoring_active = False
        self._last_sl_tp_notification: Optional[Dict] = None

        # Partial close tracking
        self._partial_close_timestamps: Dict[str, datetime] = {}

    def execute_closing_trade(
        self,
        session: Session,
        portfolio: Portfolio,
        daily_pnl: DailyPnL,
        price: float,
        decision,
        reason: str,
        pre_position: float,
        btc_amount: float,
        leverage: float,
        position_side: str,
        portfolio_calculator,
    ) -> ExecutionResult:
        """
        Execute a position closing trade.

        Args:
            session: Database session
            portfolio: Current portfolio
            daily_pnl: Daily PnL record
            price: Closing price
            decision: RiskDecision
            reason: Close reason
            pre_position: Position before close
            btc_amount: Amount to close
            leverage: Position leverage
            position_side: "LONG" or "SHORT"
            portfolio_calculator: PortfolioCalculator instance

        Returns:
            ExecutionResult
        """
        # Only use WebSocket price
        eff_price = price if price and price > 0 else None

        if not eff_price:
            eff_price = price_cache.get(self._symbol, max_age_seconds=3)

        if not eff_price or eff_price <= 0:
            logger.error("WebSocket price not available for closing %s", self._symbol)
            return ExecutionResult(
                status="FAILED", details="WebSocket price unavailable - cannot close"
            )

        position_size_usd = btc_amount * eff_price

        logger.info(
            "CLOSING %s position: %.6f BTC @ $%.2f via %s",
            position_side,
            btc_amount,
            eff_price,
            decision.action,
        )

        # Update existing trades (no new trade created for CLOSE)
        exit_prompt_value = getattr(decision, 'prompt_sent', None)
        logger.info("📝 CLOSE (exit_manager): exit_prompt length=%d", len(exit_prompt_value) if exit_prompt_value else 0)
        updated_count, realized_delta, closing_fee_total = close_open_trades(
            session=session,
            symbol=self._symbol,
            close_side=decision.action,
            close_amount=btc_amount,
            close_price=eff_price,
            taker_fee_rate=self._taker_fee_rate,
            exit_reasoning=decision.reasoning,
            exit_prompt=exit_prompt_value,
        )

        if updated_count == 0:
            logger.warning("No trades updated - position may already be closed")
            return ExecutionResult(status="SKIP", details="Position already closed")

        logger.info(
            "Closed %s position: Updated %d trade(s) | PnL: $%.2f | Fee: $%.2f",
            position_side,
            updated_count,
            realized_delta,
            closing_fee_total,
        )

        # Log to file
        entry_reasoning = None
        entry_sl = None
        entry_tp = None
        entry_inv = None
        entry_amount = None
        entry_leverage = None

        try:
            closed_trade = (
                session.query(Trade)
                .filter_by(symbol=self._symbol)
                .filter(Trade.close_price.isnot(None))
                .order_by(Trade.close_time.desc())
                .first()
            )
            if closed_trade:
                entry_reasoning = closed_trade.entry_reasoning
                entry_amount = closed_trade.amount
                entry_leverage = closed_trade.leverage
                if closed_trade.exit_plan:
                    entry_sl = closed_trade.exit_plan.get("stop_loss")
                    entry_tp = closed_trade.exit_plan.get("profit_target")
                    entry_inv = closed_trade.exit_plan.get("invalidation_condition")
        except Exception as e:
            logger.debug("Could not fetch entry details: %s", e)

        entry_price = (
            portfolio.long_avg_price
            if position_side == "LONG"
            else portfolio.short_avg_price
        )
        log_trade_to_file(
            symbol=self._symbol,
            action=f"CLOSE_{position_side}",
            entry_price=entry_price,
            close_price=eff_price,
            pnl=realized_delta,
            reasoning=decision.reasoning,
            entry_reasoning=entry_reasoning,
            amount=entry_amount,
            leverage=entry_leverage,
            stop_loss=entry_sl,
            take_profit=entry_tp,
            invalidation_condition=entry_inv,
        )

        # Update portfolio and PnL
        portfolio_calculator.update_portfolio(
            portfolio, decision.action, btc_amount, eff_price
        )
        portfolio_calculator.update_daily_pnl(
            daily_pnl, realized_delta, portfolio, eff_price, closing_fee_total
        )

        session.flush()
        session.commit()

        telemetry = {
            "last_action": "CLOSE",
            "amount": btc_amount,
            "price": eff_price,
            "position_side": position_side,
            "fee": closing_fee_total,
            "pnl": realized_delta,
        }

        return ExecutionResult(
            status="PAPER",
            details="Position closed (trade updated)",
            telemetry=telemetry,
        )

    def close_position_by_exit_plan(
        self,
        reason: str,
        trigger_type: str,
        exit_price: Optional[float],
        price_manager,
        portfolio_calculator,
    ) -> ExecutionResult:
        """
        Close position triggered by exit plan (automatic closing).

        Args:
            reason: Close reason
            trigger_type: "stop_loss" or "invalidation"
            exit_price: Price at which to close
            price_manager: PriceManager instance
            portfolio_calculator: PortfolioCalculator instance

        Returns:
            ExecutionResult
        """
        logger.info(
            "Closing position by exit plan | trigger=%s | reason=%s | exit_price=%s",
            trigger_type,
            reason,
            exit_price,
        )

        # Race condition prevention
        with self._close_lock:
            current_time = datetime.utcnow()

            last_close = self._symbol_close_timestamps.get(self._symbol)
            if last_close:
                elapsed = (current_time - last_close).total_seconds()
                if elapsed < self._close_cooldown_seconds:
                    logger.warning(
                        "Close cooldown active for %s: %.1f/%.0f seconds remaining",
                        self._symbol,
                        elapsed,
                        self._close_cooldown_seconds,
                    )
                    return ExecutionResult(
                        status="SKIP",
                        details=f"Cooldown: {self._close_cooldown_seconds - elapsed:.1f}s remaining",
                    )

            if self._closing_in_progress.get(self._symbol):
                logger.warning(
                    "Close already in progress for %s, skipping", self._symbol
                )
                return ExecutionResult(
                    status="SKIP", details="Close already in progress"
                )

            self._closing_in_progress[self._symbol] = True
            self._symbol_close_timestamps[self._symbol] = current_time

        try:
            with Session(engine) as session:
                portfolio = get_synced_portfolio(session, self._symbol, force_sync=False)

                if abs(portfolio.position) < 0.0001:
                    logger.warning("No open position to close")
                    return ExecutionResult(
                        status="SKIP", details="No position to close"
                    )

                daily_pnl = get_daily_pnl(session)

                # Use exit_price from position monitor if available
                if exit_price and exit_price > 0:
                    price = exit_price
                    logger.info("Using exit_price from position monitor: %.2f", price)
                else:
                    price = price_manager.resolve_price()
                    logger.warning(
                        "No exit_price provided, using resolved price: %.2f", price
                    )

                if price <= 0:
                    logger.error("Invalid price: %.2f, cannot close position", price)
                    return ExecutionResult(status="ERROR", details="Invalid price")

                pre_position = portfolio.position
                self._last_close_time = datetime.utcnow()

                max_closeable = abs(portfolio.position)
                btc_amount = max_closeable
                eff_price = price
                position_size_usd = btc_amount * eff_price

                is_long = portfolio.position > 0
                position_side = "LONG" if is_long else "SHORT"

                logger.info(
                    "CLOSE %s position (exit plan): current=%.6f btc_to_close=%.6f notional=%.2f trigger=%s",
                    position_side,
                    portfolio.position,
                    btc_amount,
                    position_size_usd,
                    trigger_type,
                )

                trade_side = "SELL" if is_long else "BUY"
                updated_count, realized_delta, closing_fee_total = close_open_trades(
                    session=session,
                    symbol=self._symbol,
                    close_side=trade_side,
                    close_amount=btc_amount,
                    close_price=eff_price,
                    taker_fee_rate=self._taker_fee_rate,
                    exit_reasoning=f"Exit plan: {trigger_type} - {reason}",
                )

                if updated_count == 0:
                    logger.warning(
                        "No trades updated - position may already be closed"
                    )
                    return ExecutionResult(
                        status="SKIP", details="Position already closed"
                    )

                logger.info(
                    "Closed %s position: %d trade(s) updated with close_price=%.2f",
                    position_side,
                    updated_count,
                    eff_price,
                )

                portfolio.position = 0.0
                portfolio.average_price = 0.0
                portfolio.updated_at = datetime.utcnow()

                portfolio_calculator.update_daily_pnl(
                    daily_pnl, realized_delta, portfolio, eff_price, closing_fee_total
                )

                session.flush()
                session.commit()

                logger.info(
                    "Position closed by exit plan | pnl=%.2f fee=%.2f",
                    realized_delta,
                    closing_fee_total,
                )

                telemetry = {
                    "last_action": "CLOSE",
                    "amount": btc_amount,
                    "price": eff_price,
                    "position_side": position_side,
                    "fee": closing_fee_total,
                    "pnl": realized_delta,
                    "trigger_type": trigger_type,
                }

                return ExecutionResult(
                    status="PAPER",
                    details=f"{position_side} position closed by {trigger_type}",
                    telemetry=telemetry,
                )

        finally:
            with self._close_lock:
                self._closing_in_progress[self._symbol] = False

    def execute_partial_close(
        self,
        position_side: str,
        close_quantity: float,
        close_price: float,
        tp_level: int,
        reason: str,
        remaining_quantity: float,
        is_breakeven: bool,
        trailing_activated: bool,
        portfolio_calculator,
        trade_notifier,
    ) -> ExecutionResult:
        """
        Execute partial position close for TP triggers.

        Args:
            position_side: "LONG" or "SHORT"
            close_quantity: Quantity to close
            close_price: Close price
            tp_level: TP level (1, 2, 3, 4)
            reason: Close reason
            remaining_quantity: Remaining position after close
            is_breakeven: Whether breakeven is now active
            trailing_activated: Whether trailing stop is now active
            portfolio_calculator: PortfolioCalculator instance
            trade_notifier: TradeNotifier instance

        Returns:
            ExecutionResult
        """
        logger.info(
            "Partial close triggered | TP%d | %s | qty=%.6f @ $%.2f",
            tp_level,
            position_side,
            close_quantity,
            close_price,
        )

        # Short cooldown for partial close
        current_time = datetime.utcnow()
        partial_cooldown_key = f"{self._symbol}_partial_tp{tp_level}"
        last_partial = self._partial_close_timestamps.get(partial_cooldown_key)

        if last_partial:
            elapsed = (current_time - last_partial).total_seconds()
            if elapsed < 5:
                logger.warning(
                    "Partial close cooldown active: %.1fs remaining", 5 - elapsed
                )
                return ExecutionResult(
                    status="SKIP", details=f"Partial cooldown: {5 - elapsed:.1f}s"
                )

        self._partial_close_timestamps[partial_cooldown_key] = current_time

        try:
            with Session(engine) as session:
                portfolio = get_synced_portfolio(session, self._symbol, force_sync=False)

                if position_side == "LONG":
                    current_qty = portfolio.long_position
                else:
                    current_qty = abs(portfolio.short_position)

                if current_qty < 0.0001:
                    logger.warning("No %s position to partially close", position_side)
                    return ExecutionResult(
                        status="SKIP", details=f"No {position_side} position"
                    )

                actual_close_qty = min(close_quantity, current_qty)
                if actual_close_qty < 0.0001:
                    logger.warning("Close quantity too small: %.8f", actual_close_qty)
                    return ExecutionResult(
                        status="SKIP", details="Close quantity too small"
                    )

                daily_pnl = get_daily_pnl(session)
                pre_position = portfolio.position

                trade_side = "SELL" if position_side == "LONG" else "BUY"

                updated_count, realized_delta, closing_fee_total = close_open_trades(
                    session=session,
                    symbol=self._symbol,
                    close_side=trade_side,
                    close_amount=actual_close_qty,
                    close_price=close_price,
                    taker_fee_rate=self._taker_fee_rate,
                    exit_reasoning=f"Partial close TP{tp_level}: {reason}",
                )

                if updated_count == 0:
                    logger.warning("No trades updated for partial close")
                    return ExecutionResult(
                        status="SKIP", details="No trades to close"
                    )

                logger.info(
                    "Partial close TP%d: %d trade(s) | qty=%.6f | pnl=$%.2f | fee=$%.2f",
                    tp_level,
                    updated_count,
                    actual_close_qty,
                    realized_delta,
                    closing_fee_total,
                )

                portfolio_calculator.update_portfolio(
                    portfolio, trade_side, actual_close_qty, close_price
                )
                portfolio_calculator.update_daily_pnl(
                    daily_pnl, realized_delta, portfolio, close_price, closing_fee_total
                )

                entry_price = (
                    portfolio.long_avg_price
                    if position_side == "LONG"
                    else portfolio.short_avg_price
                )
                log_trade_to_file(
                    symbol=self._symbol,
                    action=f"PARTIAL_CLOSE_TP{tp_level}_{position_side}",
                    entry_price=entry_price,
                    close_price=close_price,
                    pnl=realized_delta,
                    reasoning=reason,
                    amount=actual_close_qty,
                )

                session.flush()
                session.commit()

                # Send notification
                trade_notifier.send_partial_close_notification(
                    tp_level=tp_level,
                    position_side=position_side,
                    close_qty=actual_close_qty,
                    close_price=close_price,
                    pnl=realized_delta,
                    remaining_qty=remaining_quantity,
                    is_breakeven=is_breakeven,
                    trailing_activated=trailing_activated,
                )

                telemetry = {
                    "last_action": f"PARTIAL_CLOSE_TP{tp_level}",
                    "amount": actual_close_qty,
                    "price": close_price,
                    "position_side": position_side,
                    "fee": closing_fee_total,
                    "pnl": realized_delta,
                    "tp_level": tp_level,
                    "remaining_quantity": remaining_quantity,
                    "is_breakeven": is_breakeven,
                    "trailing_activated": trailing_activated,
                }

                return ExecutionResult(
                    status="PAPER",
                    details=f"TP{tp_level} partial close: {actual_close_qty:.6f} @ ${close_price:.2f}",
                    telemetry=telemetry,
                )

        except Exception as e:
            logger.error("Partial close error: %s", e, exc_info=True)
            return ExecutionResult(status="ERROR", details=str(e))

    def start_stop_loss_monitoring(self) -> None:
        """Start WebSocket-based stop-loss monitoring."""
        if self._stop_loss_monitoring_active:
            return

        self._stop_loss_monitoring_active = True
        logger.info("Stop-loss/Take-profit monitoring started for %s", self._symbol)

        monitor_thread = threading.Thread(
            target=self._monitor_stop_loss_websocket,
            name=f"sl-tp-monitor-{self._symbol}",
            daemon=True,
        )
        monitor_thread.start()

    def _monitor_stop_loss_websocket(self) -> None:
        """WebSocket price monitoring for stop-loss."""

        async def monitor():
            try:
                from app.data_feeds.binance_ws import BinanceWebSocketClient

                def price_handler(message):
                    try:
                        if hasattr(message, "payload"):
                            payload = message.payload
                        elif isinstance(message, dict):
                            payload = message.get("payload", {})
                        else:
                            logger.warning("Unknown message format: %s", type(message))
                            return

                        kline = payload.get("k", {})
                        close_price = float(kline.get("c", 0.0))

                        if close_price > 0:
                            self._check_and_notify_sl_tp(close_price)
                    except Exception as e:
                        logger.error("Error in price handler: %s", e)

                ws_client = BinanceWebSocketClient(self._symbol, "1m")
                await ws_client.listen(price_handler)

            except Exception as e:
                logger.error("WebSocket monitoring error: %s", e)

        try:
            asyncio.run(monitor())
        except Exception as e:
            logger.error("Failed to start WebSocket monitoring: %s", e)

    def _check_and_notify_sl_tp(self, current_price: float) -> None:
        """Check stop-loss level and close position if triggered."""
        try:
            with Session(engine) as session:
                portfolio = get_synced_portfolio(session, self._symbol, force_sync=False)

                if abs(portfolio.position) < 0.0001:
                    return

                latest_trade = (
                    session.query(Trade)
                    .filter_by(symbol=self._symbol)
                    .filter(Trade.close_price.is_(None))
                    .order_by(Trade.timestamp.desc())
                    .first()
                )

                if not latest_trade:
                    return

                entry_price = latest_trade.price
                is_long = portfolio.position > 0

                stop_loss_price = None

                if latest_trade.exit_plan:
                    exit_plan = latest_trade.exit_plan
                    stop_loss_raw = exit_plan.get("stop_loss")

                    if stop_loss_raw is not None and stop_loss_raw != 0.0:
                        stop_loss_price = stop_loss_raw

                # Fallback SL logic
                if not stop_loss_price:
                    logger.debug(
                        "GLM exit plan not found or values invalid, using fallback SL logic"
                    )
                    if is_long:
                        stop_loss_price = entry_price * 0.995
                    else:
                        stop_loss_price = entry_price * 1.005

                trigger_type = None
                trigger_price = None

                if is_long:
                    if stop_loss_price and current_price <= stop_loss_price:
                        trigger_type = "STOP-LOSS"
                        trigger_price = stop_loss_price
                else:
                    if stop_loss_price and current_price >= stop_loss_price:
                        trigger_type = "STOP-LOSS"
                        trigger_price = stop_loss_price

                if trigger_type:
                    logger.warning(
                        "%s triggered: %s position %.6f BTC @ %.2f, current %.2f, trigger %.2f",
                        trigger_type,
                        "LONG" if is_long else "SHORT",
                        abs(portfolio.position),
                        entry_price,
                        current_price,
                        trigger_price,
                    )

                    self._close_position_on_trigger(
                        session, trigger_type, current_price, trigger_price
                    )
                else:
                    logger.debug(
                        "SL check: %s @ %.2f, current %.2f, SL=%.2f",
                        "LONG" if is_long else "SHORT",
                        entry_price,
                        current_price,
                        stop_loss_price or 0.0,
                    )

        except Exception as e:
            logger.error("Error checking SL: %s", e, exc_info=True)

    def _close_position_on_trigger(
        self,
        session: Session,
        trigger_type: str,
        current_price: float,
        trigger_price: float,
    ) -> None:
        """Close position when stop-loss is triggered."""
        with self._close_lock:
            current_time = datetime.utcnow()

            last_close = self._symbol_close_timestamps.get(self._symbol)
            if last_close:
                elapsed = (current_time - last_close).total_seconds()
                if elapsed < self._close_cooldown_seconds:
                    logger.warning(
                        "Close cooldown active for %s (WebSocket): %.1f/%.0f seconds remaining",
                        self._symbol,
                        elapsed,
                        self._close_cooldown_seconds,
                    )
                    return

            if self._closing_in_progress.get(self._symbol):
                logger.warning(
                    "Close already in progress for %s (WebSocket), skipping",
                    self._symbol,
                )
                return

            self._closing_in_progress[self._symbol] = True
            self._symbol_close_timestamps[self._symbol] = current_time

        try:
            from app.risk_manager.decision_models import RiskDecision

            portfolio = get_synced_portfolio(session, self._symbol, force_sync=False)

            if abs(portfolio.position) < 0.0001:
                logger.warning("Position already closed or no position")
                return

            position_amount = abs(portfolio.position)
            entry_price = portfolio.average_price
            position_type = "LONG" if portfolio.position > 0 else "SHORT"

            latest_trade = (
                session.query(Trade)
                .filter_by(symbol=self._symbol)
                .filter(Trade.close_price.is_(None))
                .order_by(Trade.timestamp.desc())
                .first()
            )
            leverage = latest_trade.leverage if latest_trade else 1.0

            logger.info(
                "Closing position due to %s trigger: %.6f BTC %s @ %.2f, current %.2f",
                trigger_type,
                position_amount,
                position_type,
                entry_price,
                current_price,
            )

            close_decision = RiskDecision(
                action="CLOSE",
                amount=1.0,
                reasoning=f"{trigger_type} triggered at {trigger_price:.2f}",
                leverage=leverage,
                glm_confidence=100.0,
                reason_primary=f"Risk Management: {trigger_type}",
                reason_secondary=f"Triggered at ${trigger_price:,.2f}",
            )

            daily_pnl = get_daily_pnl(session)

            trade_side = "SELL" if position_type == "LONG" else "BUY"
            updated_count, realized_delta, closing_fee_total = close_open_trades(
                session=session,
                symbol=self._symbol,
                close_side=trade_side,
                close_amount=position_amount,
                close_price=current_price,
                taker_fee_rate=self._taker_fee_rate,
                exit_reasoning=f"Trigger: {trigger_type}",
            )

            if updated_count > 0:
                logger.info("Position closed successfully due to %s", trigger_type)

                self._last_close_time = datetime.utcnow()

                self._send_sl_tp_notification_internal(
                    trigger_type=trigger_type,
                    position_type=position_type,
                    amount=position_amount,
                    entry_price=entry_price,
                    current_price=current_price,
                    trigger_price=trigger_price,
                )

                session.commit()
            else:
                logger.error("Failed to close position")

        except Exception as e:
            logger.error("Error closing position on trigger: %s", e, exc_info=True)
        finally:
            with self._close_lock:
                self._closing_in_progress[self._symbol] = False

    def _send_sl_tp_notification_internal(
        self,
        trigger_type: str,
        position_type: str,
        amount: float,
        entry_price: float,
        current_price: float,
        trigger_price: float,
    ) -> None:
        """Internal method to send SL/TP notification."""
        try:
            notification_key = f"{trigger_type}_{position_type}_{current_price:.2f}"
            current_time = datetime.utcnow()

            if (
                self._last_sl_tp_notification
                and self._last_sl_tp_notification.get("key") == notification_key
                and (
                    current_time - self._last_sl_tp_notification["time"]
                ).total_seconds()
                < 60
            ):
                return

            if position_type == "SHORT":
                pnl_pct = (entry_price - current_price) / entry_price * 100
                pnl_amount = (entry_price - current_price) * abs(amount)
            else:
                pnl_pct = (current_price - entry_price) / entry_price * 100
                pnl_amount = (current_price - entry_price) * amount

            # Save to database
            try:
                with Session(engine) as db_session:
                    if trigger_type == "STOP-LOSS":
                        orders = get_active_stop_loss_orders(db_session, self._symbol)
                    else:
                        orders = []

                    order_id = None
                    if orders:
                        for order in orders:
                            if abs(order.entry_price - entry_price) < 1.0:
                                order_id = order.id
                                break

                    if order_id:
                        create_stop_loss_notification(
                            session=db_session,
                            order_id=order_id,
                            symbol=self._symbol,
                            notification_type=trigger_type,
                            position_type=position_type,
                            position_amount=abs(amount),
                            entry_price=entry_price,
                            trigger_price=trigger_price,
                            current_price=current_price,
                            pnl_amount=pnl_amount,
                            pnl_percentage=pnl_pct,
                            telegram_sent=False,
                        )

                        if trigger_type == "STOP-LOSS":
                            trigger_stop_loss_order(
                                db_session, order_id, current_price, pnl_amount, pnl_pct
                            )

                        db_session.commit()

            except Exception as db_e:
                logger.error("Database notification failed: %s", db_e)

            # Telegram notification
            base_asset = self._symbol.replace("USDT", "")

            message = f"""
*{trigger_type} TRIGGERED*

*Position Info:*
- Type: {position_type}
- Amount: {abs(amount):.6f} {base_asset}
- Entry Price: ${entry_price:,.2f}
- Current Price: ${current_price:,.2f}
- Trigger Price: ${trigger_price:,.2f}

*Profit/Loss:*
- Percent: {pnl_pct:+.2f}%
- Amount: ${pnl_amount:+,.2f}

*{current_time.strftime('%H:%M:%S')}*
"""
            try:
                telegram_client.send_message(message)
            except Exception as telegram_e:
                logger.error("Telegram notification failed: %s", telegram_e)

            self._last_sl_tp_notification = {
                "key": notification_key,
                "time": current_time,
                "type": trigger_type,
                "price": current_price,
            }

            logger.warning(
                "%s notification sent: %s %.6f at %.2f",
                trigger_type,
                position_type,
                abs(amount),
                current_price,
            )

        except Exception as e:
            logger.error("Error sending SL/TP notification: %s", e)

    def validate_exit_plan(
        self, exit_plan: dict, position_side: str, entry_price: float
    ) -> Tuple[bool, str]:
        """
        Validate exit plan.

        Python-calculated exit plans are always valid (bounds enforced during calculation).

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not exit_plan:
            return False, "Exit plan missing"

        return True, ""

    @property
    def last_close_time(self) -> Optional[datetime]:
        """Get the last close time."""
        return self._last_close_time

    @last_close_time.setter
    def last_close_time(self, value: datetime) -> None:
        """Set the last close time."""
        self._last_close_time = value

    @property
    def stop_loss_monitoring_active(self) -> bool:
        """Check if stop-loss monitoring is active."""
        return self._stop_loss_monitoring_active
