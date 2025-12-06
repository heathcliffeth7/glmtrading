"""
Trade Notifications Module

Handles Telegram notifications for trade events.
"""

import json
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Optional

from app.utils.logging import get_logger
from app.utils.telegram import format_markdown, telegram_client


logger = get_logger(__name__)


class TradeNotifier:
    """
    Manages trade notifications via Telegram.
    """

    def __init__(
        self,
        symbol: str,
        starting_cash: float = 10000.0,
        taker_fee_rate: float = 0.0005,
    ):
        """
        Initialize the trade notifier.

        Args:
            symbol: Trading symbol
            starting_cash: Initial equity
            taker_fee_rate: Trading fee rate
        """
        self._symbol = symbol
        self._starting_cash = starting_cash
        self._taker_fee_rate = taker_fee_rate

    def notify_telegram(
        self,
        trade: SimpleNamespace,
        portfolio: SimpleNamespace,
        daily_pnl: SimpleNamespace,
        leverage: float,
        reason: str,
        position_status: str,
        metrics: Dict[str, Any],
    ) -> None:
        """
        Send trade notification via Telegram.

        Args:
            trade: Trade details (amount, price, pnl, side, timestamp, etc.)
            portfolio: Portfolio state (position, average_price)
            daily_pnl: Daily PnL (realized_pnl, unrealized_pnl)
            leverage: Trade leverage
            reason: Trade reasoning
            position_status: Position change description
            metrics: Portfolio metrics dictionary
        """
        if not telegram_client.enabled():
            raise RuntimeError("Telegram notification cannot be disabled")

        equity = metrics.get("equity", self._starting_cash)
        equity_pct = ((equity - self._starting_cash) / self._starting_cash) * 100
        position_value = abs(
            metrics.get("position", 0.0) * metrics.get("price", trade.price)
        )
        margin_used = metrics.get("margin_used", 0.0)
        free_cash = metrics.get("free_cash", max(0.0, equity - margin_used))
        free_cash_pct = (free_cash / equity * 100) if equity > 0 else 0.0

        fee = getattr(trade, "fee", 0)
        notional = getattr(trade, "notional", trade.amount * trade.price)

        close_price = getattr(trade, "close_price", None)
        is_close_trade = close_price is not None
        position_side = getattr(trade, "position_side", "") or ""

        # Emoji and action text
        if is_close_trade:
            direction_emoji = "CLOSE"
            side_label = (
                position_side
                if position_side
                else ("LONG" if trade.side == "SELL" else "SHORT")
            )
            action_text = f"{side_label} POSITION CLOSED"
        elif trade.side == "BUY":
            direction_emoji = "LONG"
            action_text = "LONG OPENED"
        else:
            direction_emoji = "SHORT"
            action_text = "SHORT OPENED"

        base_asset = self._symbol.replace("USDT", "")
        timestamp_header = (
            f"{base_asset}_ANALYZER, [{trade.timestamp.strftime('%d.%m.%Y %H:%M')}]"
        )

        message_lines = [
            f"{timestamp_header}",
            f"*Trade Executed - {action_text}*",
            f"Symbol: {format_markdown(self._symbol)}",
            f"Direction: {format_markdown(trade.side)}",
            f"Amount: {trade.amount:.4f} {base_asset}",
            f"{'Close' if is_close_trade else 'Price'}: ${trade.price:.2f}",
            f"Leverage: {leverage:.1f}x",
            f"Notional: ${notional:.2f}",
            f"Fee: ${fee:.2f} ({self._taker_fee_rate*100:.2f}%)",
            f"{'Profit' if trade.pnl >= 0 else 'Loss'} Net PnL: ${trade.pnl:.2f}",
            "",
        ]

        if is_close_trade:
            message_lines.extend(
                [
                    "No new position opened (CLOSE)",
                    f"Closed: {trade.amount:.4f} {base_asset} | Remaining: {portfolio.position:.4f} {base_asset}",
                    f"Remaining unrealized: ${daily_pnl.unrealized_pnl:.2f}",
                    "",
                ]
            )

        position_details = metrics.get("position_details", [])
        starting_cash = metrics.get("starting_cash", self._starting_cash)

        message_lines.extend(
            [
                f"Position Status: {format_markdown(position_status)}",
                f"Portfolio Position: {portfolio.position:.4f} {base_asset} @ ${portfolio.average_price:.2f}",
                f"Position Value: ${position_value:.2f}",
                "",
                "*Portfolio Summary*",
                f"Free Equity: ${free_cash:.2f} ({free_cash_pct:.1f}%)",
                f"Margin Used: ${margin_used:.2f}",
            ]
        )

        if position_details:
            for pos in position_details:
                side = pos.get("position_side", "")
                lev = pos.get("leverage", 1.0)
                margin = pos.get("margin", 0.0)
                pos_notional = pos.get("notional", 0.0)
                amount = pos.get("amount", 0.0)
                message_lines.append(
                    f"  - {side}: {amount:.4f} {base_asset} @ {lev:.1f}x -> ${pos_notional:,.2f} (Margin: ${margin:,.2f})"
                )

        message_lines.extend(
            [
                f"Total Equity: ${equity:.2f}",
                f"Starting Equity: ${starting_cash:.2f}",
                f"{'Profit' if equity_pct >= 0 else 'Loss'} Total Change: {equity_pct:+.2f}%",
                f"Realized PnL: ${daily_pnl.realized_pnl:.2f}",
                f"Unrealized PnL: ${daily_pnl.unrealized_pnl:.2f}",
                "",
                f"Time: {trade.timestamp.isoformat()}",
            ]
        )

        # Take Profit display for open positions
        if not is_close_trade and metrics.get("tp_oid") != -1:
            latest_tp = metrics.get("exit_plan", {}).get("take_profit")
            if latest_tp:
                message_lines.append(f"Take Profit: ${latest_tp:.2f}")

        message = "\n".join(message_lines)

        # Escape reason for Telegram
        reason_escaped = (
            reason.replace("_", "\\_")
            .replace("*", "\\*")
            .replace("[", "\\[")
            .replace("`", "\\`")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )

        max_reason_length = 3000
        if len(reason_escaped) > max_reason_length:
            reason_escaped = reason_escaped[:max_reason_length] + "... (truncated)"

        message_with_reason = message + f"\n\n*Reasoning:*\n{reason_escaped}"

        logger.info(
            "Telegram trade notify: side=%s amount=%.4f price=%.2f position=%.4f",
            trade.side,
            trade.amount,
            trade.price,
            portfolio.position,
        )

        try:
            telegram_client.send_message(message_with_reason)
            logger.info("Telegram trade notification sent successfully")
        except Exception as exc:
            logger.error("Telegram trade notify failed: %s", exc, exc_info=True)
            try:
                telegram_client.send_message(
                    message + "\n\nReasoning: (too long, see logs)"
                )
                logger.warning("Sent telegram notification without detailed reasoning")
            except Exception as exc2:
                logger.error("Failed to send simplified telegram notification: %s", exc2)

    def describe_position_change(self, before: float, after: float) -> str:
        """
        Generate human-readable position change description.

        Args:
            before: Position before trade
            after: Position after trade

        Returns:
            Description string
        """
        if before == 0 and after == 0:
            return "No position"
        if after == 0:
            return f"Position closed ({before:+.4f} -> {after:+.4f})"
        if before == 0:
            return f"New position opened ({after:+.4f})"
        if before * after < 0:
            return f"Position direction changed ({before:+.4f} -> {after:+.4f})"
        if abs(after) > abs(before):
            return f"Position increased ({before:+.4f} -> {after:+.4f})"
        if abs(after) < abs(before):
            return f"Position decreased ({before:+.4f} -> {after:+.4f})"
        return "Position unchanged"

    def send_partial_close_notification(
        self,
        tp_level: int,
        position_side: str,
        close_qty: float,
        close_price: float,
        pnl: float,
        remaining_qty: float,
        is_breakeven: bool,
        trailing_activated: bool,
    ) -> None:
        """
        Send partial close notification for TP triggers.

        Args:
            tp_level: Take profit level (1, 2, 3, 4)
            position_side: "LONG" or "SHORT"
            close_qty: Quantity closed
            close_price: Close price
            pnl: Realized PnL
            remaining_qty: Remaining position quantity
            is_breakeven: Whether breakeven is now active
            trailing_activated: Whether trailing stop is now active
        """
        try:
            emoji = "Profit" if pnl >= 0 else "Loss"
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

            status_parts = []
            if is_breakeven:
                status_parts.append("BE active")
            if trailing_activated:
                status_parts.append("Trailing active")
            status_str = " | ".join(status_parts) if status_parts else ""

            total_qty = close_qty + remaining_qty
            close_pct = int((close_qty / total_qty) * 100) if total_qty > 0 else 100

            message = f"""
*TP{tp_level} Triggered* | {self._symbol}

{emoji} *{position_side}* position {close_pct}% closed

*Details:*
- Closed: {close_qty:.6f} @ ${close_price:,.2f}
- PnL: {pnl_str}
- Remaining: {remaining_qty:.6f}

{status_str}

*{datetime.utcnow().strftime('%H:%M:%S')} UTC*
"""
            telegram_client.send_message(message.strip())
            logger.info("Partial close notification sent for TP%d", tp_level)

        except Exception as e:
            logger.error("Failed to send partial close notification: %s", e)

    def send_sl_tp_notification(
        self,
        trigger_type: str,
        position_type: str,
        amount: float,
        entry_price: float,
        current_price: float,
        trigger_price: float,
    ) -> None:
        """
        Send stop-loss/take-profit trigger notification.

        Args:
            trigger_type: "STOP-LOSS" or "TAKE-PROFIT"
            position_type: "LONG" or "SHORT"
            amount: Position amount
            entry_price: Entry price
            current_price: Current market price
            trigger_price: Trigger price level
        """
        try:
            # Calculate PnL
            if position_type == "SHORT":
                pnl_pct = (entry_price - current_price) / entry_price * 100
                pnl_amount = (entry_price - current_price) * abs(amount)
            else:
                pnl_pct = (current_price - entry_price) / entry_price * 100
                pnl_amount = (current_price - entry_price) * amount

            base_asset = self._symbol.replace("USDT", "")
            current_time = datetime.utcnow()

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
            telegram_client.send_message(message)
            logger.warning(
                "%s notification sent: %s %.6f at %.2f",
                trigger_type,
                position_type,
                abs(amount),
                current_price,
            )

        except Exception as e:
            logger.error("Error sending SL/TP notification: %s", e)

    def format_reason(self, text: str) -> str:
        """
        Format reasoning text for display.

        Args:
            text: Raw reasoning text

        Returns:
            Cleaned and formatted reasoning
        """
        cleaned = text.replace("```", "")
        cleaned = cleaned.replace("\\n", "\n")
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        summary = cleaned.strip()

        if match:
            json_text = match.group(0)
            prefix = cleaned[: match.start()].strip(" ()\n")
            try:
                payload = json.loads(json_text)
                parts = []
                if prefix:
                    parts.append(prefix)
                karar = payload.get("karar")
                miktar = payload.get("miktar")
                kaldirac = payload.get("kaldrac") or payload.get("kaldirac")
                gerekce = payload.get("gerekce") or payload.get("gerekce")
                if karar is not None:
                    parts.append(f"karar={karar}")
                if miktar is not None:
                    parts.append(f"miktar={miktar}")
                if kaldirac is not None:
                    parts.append(f"kaldrac={kaldirac}")
                if gerekce:
                    parts.append(f"acklama={gerekce}")
                summary = " | ".join(parts) if parts else prefix
            except json.JSONDecodeError:
                summary = cleaned

        summary = re.sub(r"`+", "", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        return summary
