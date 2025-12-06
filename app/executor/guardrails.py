"""
Guardrails Module

Risk enforcement and trade validation guardrails.
"""

from typing import Optional

from app.executor.ledger import Portfolio
from app.utils.logging import get_logger
from app.utils.telegram import format_markdown, telegram_client


logger = get_logger(__name__)


class GuardrailManager:
    """
    Manages trade guardrails and risk limits enforcement.
    """

    def __init__(
        self,
        symbol: str,
        max_position: float = 1000.0,
        max_btc_per_trade: Optional[float] = None,
        starting_cash: float = 10000.0,
        min_leverage: float = 1.0,
        max_leverage: float = 20.0,
    ):
        """
        Initialize the guardrail manager.

        Args:
            symbol: Trading symbol
            max_position: Maximum position size in base asset
            max_btc_per_trade: Maximum units per trade (None = disabled)
            starting_cash: Starting equity
            min_leverage: Minimum allowed leverage
            max_leverage: Maximum allowed leverage
        """
        self._symbol = symbol
        self._max_position = max_position
        self._max_btc_per_trade = max_btc_per_trade
        self._starting_cash = starting_cash
        self._min_leverage = min_leverage
        self._max_leverage = max_leverage

    def check_guardrails(
        self, portfolio: Portfolio, btc_amount: float, equity: float
    ) -> bool:
        """
        Check all guardrail conditions.

        Returns:
            True if all guardrails pass, False if any fail
        """
        # 1. Per-trade unit cap (disabled when None)
        if self._max_btc_per_trade and btc_amount > self._max_btc_per_trade:
            logger.warning(
                "Single trade unit limit exceeded: requested=%.6f limit=%.6f",
                btc_amount,
                self._max_btc_per_trade,
                extra={"skip_telegram": True},
            )
            return False

        # 2. Total position check (in base asset terms)
        new_total_position = abs(portfolio.position) + btc_amount
        if new_total_position > self._max_position:
            logger.warning(
                "Total position limit exceeded: current=%.6f + new=%.6f = %.6f > limit=%.6f",
                abs(portfolio.position),
                btc_amount,
                new_total_position,
                self._max_position,
                extra={"skip_telegram": True},
            )
            return False

        # 3. Equity sanity check - prevent astronomical values
        if equity > self._starting_cash * 10:
            logger.warning(
                "Equity too high (%.2f), possible calculation error. Max allowed: %.2f",
                equity,
                self._starting_cash * 10,
                extra={"skip_telegram": True},
            )
            return False

        return True

    def notify_guardrail_block(
        self,
        decision,
        reason: str,
        current_position: float,
        requested_amount: float,
        leverage: float,
    ) -> None:
        """
        Send Telegram notification when a guardrail blocks a trade.

        Args:
            decision: The RiskDecision that was blocked
            reason: The formatted reasoning
            current_position: Current position size
            requested_amount: Requested trade amount
            leverage: Requested leverage
        """
        if not telegram_client.enabled():
            raise RuntimeError("Telegram notification cannot be disabled")

        base_asset = self._symbol.replace("USDT", "")
        new_position = abs(current_position + requested_amount)

        message = "\n".join(
            [
                "*Trade Blocked (Guardrail)*",
                f"Symbol: {format_markdown(self._symbol)}",
                f"{'Long' if decision.action == 'BUY' else 'Short'} Request: {format_markdown(decision.action)}",
                f"Risk Ratio: {decision.amount * 100:.1f}% equity",
                f"Leverage: {leverage:.1f}x",
                f"Leveraged Amount: {requested_amount:.4f} {base_asset}",
                "",
                "*Position Status*",
                f"Current Position: {current_position:+.4f} {base_asset}",
                f"Requested New Position: {new_position:.4f} {base_asset}",
                f"Limit: {self._max_position:.4f} {base_asset}",
                f"Excess: {(new_position - self._max_position):.4f} {base_asset}",
                "",
                f"Reason: {format_markdown(reason)}",
            ]
        )

        try:
            telegram_client.send_message(message)
        except Exception as exc:
            logger.error("Telegram guardrail notify failed: %s", exc)

    def normalize_leverage(self, value: float) -> float:
        """
        Normalize leverage value within allowed bounds.

        Args:
            value: Requested leverage

        Returns:
            Normalized leverage within [min_leverage, max_leverage]
        """
        if value <= 0:
            return self._min_leverage
        return max(self._min_leverage, min(value, self._max_leverage))

    def check_notional_cap(
        self,
        current_total_notional: float,
        new_notional: float,
        max_total_notional: float,
    ) -> tuple[bool, float]:
        """
        Check if new trade would exceed notional cap.

        Args:
            current_total_notional: Current total notional across positions
            new_notional: Proposed new trade notional
            max_total_notional: Maximum allowed total notional

        Returns:
            Tuple of (is_allowed, allowed_amount)
        """
        allowed_remaining = max_total_notional - current_total_notional

        if allowed_remaining <= 0:
            return False, 0.0

        if new_notional > allowed_remaining + 1e-8:
            return True, allowed_remaining

        return True, new_notional

    def check_margin_available(
        self, free_equity: float, min_margin: float
    ) -> bool:
        """
        Check if there's sufficient free equity for trade.

        Args:
            free_equity: Available equity
            min_margin: Minimum required margin

        Returns:
            True if sufficient margin available
        """
        return free_equity > min_margin
