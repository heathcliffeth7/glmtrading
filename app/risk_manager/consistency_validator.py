"""
Consistency Validator Module

Validates alignment between GLM thought process and trading signals:
- Thesis/Antithesis/Synthesis consistency
- Signal direction alignment
- Mismatch notifications
"""

from typing import Tuple

from app.risk_manager.decision_models import ThoughtProcess
from app.utils.logging import get_logger
from app.utils.telegram import telegram_client


logger = get_logger(__name__)


class ConsistencyValidator:
    """
    Validates consistency between GLM's thought process and trading signals.

    Ensures that the synthesis conclusion aligns with the proposed action.
    """

    def __init__(self):
        """Initialize the consistency validator with metrics."""
        self._metrics = {
            "consistency_checks_performed": 0,
            "consistency_mismatches": 0,
            "consistency_forced_holds": 0,
        }

    @property
    def metrics(self):
        """Get current validation metrics."""
        return self._metrics.copy()

    def validate_consistency(
        self,
        thought_process: ThoughtProcess,
        signal: str
    ) -> Tuple[bool, str]:
        """
        Validate that thesis/synthesis aligns with signal direction.

        Args:
            thought_process: Parsed ThoughtProcess object
            signal: Trading signal (BUY, SELL, HOLD, CLOSE)

        Returns:
            Tuple of (is_consistent, reason)
        """
        self._metrics["consistency_checks_performed"] += 1

        if not thought_process or not thought_process.synthesis_verdict:
            return True, "no_synthesis_to_validate"

        synthesis = thought_process.synthesis_verdict.lower()

        # Turkish and English keywords for direction detection
        bullish_keywords = [
            'bullish', 'yukselis', 'yükseliş', 'long', 'al', 'buy',
            'yukari', 'yukarı', 'pozitif'
        ]
        bearish_keywords = [
            'bearish', 'dusus', 'düşüş', 'short', 'sat', 'sell',
            'asagi', 'aşağı', 'negatif'
        ]

        is_bullish = any(kw in synthesis for kw in bullish_keywords)
        is_bearish = any(kw in synthesis for kw in bearish_keywords)

        # Check alignment
        if signal == "BUY" and is_bearish and not is_bullish:
            self._metrics["consistency_mismatches"] += 1
            return False, "BUY_signal_but_bearish_synthesis"

        if signal == "SELL" and is_bullish and not is_bearish:
            self._metrics["consistency_mismatches"] += 1
            return False, "SELL_signal_but_bullish_synthesis"

        return True, "aligned"

    def notify_consistency_mismatch(
        self,
        symbol: str,
        original_signal: str,
        reason: str,
        synthesis: str
    ) -> None:
        """Send Telegram alert for consistency mismatch."""
        try:
            message = f"""⚠️ *CONSISTENCY MISMATCH*

*Symbol:* {symbol}
*Original Signal:* {original_signal}
*Reason:* {reason}
*Action:* Forced to HOLD

*Synthesis:* {synthesis[:200]}...

_Thought process consistency validation triggered_"""

            telegram_client.send(message)
            logger.warning("Consistency mismatch notification sent for %s", symbol)
            self._metrics["consistency_forced_holds"] += 1
        except Exception as e:
            logger.error("Failed to send consistency mismatch notification: %s", e)
