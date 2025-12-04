"""
Time-Based Exit Manager - Zamana dayalı çıkış kuralları.

Features:
- Maximum hold süresi kontrolü
- Stagnation tespiti (fiyat hareket etmiyor)
- Weekend risk yönetimi (Cuma 20:00 UTC)
- Session-based çıkış önerileri

Supports both Scalp and Swing trading modes.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ExitUrgency(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExitType(Enum):
    TIME_LIMIT = "TIME_LIMIT"
    STAGNATION = "STAGNATION"
    WEEKEND_RISK = "WEEKEND_RISK"
    APPROACHING_LIMIT = "APPROACHING_LIMIT"
    NONE = "NONE"


@dataclass
class TimeExitResult:
    """Time-based exit check result"""
    should_exit: bool
    exit_type: ExitType
    urgency: ExitUrgency
    reason: str
    hours_held: float
    hours_remaining: float  # Until max hold time


class TimeBasedExitManager:
    """
    Zamana dayalı çıkış kuralları yöneticisi.

    Features:
    - Maximum hold süresi
    - Stagnation kontrolü
    - Weekend risk (Cuma 20:00 UTC sonrası)
    """

    # Mode-specific parameters
    MODE_PARAMS = {
        "swing": {
            "max_hold_hours": 168,        # 7 gün max
            "stagnation_hours": 48,       # 48 saat hareket yoksa
            "stagnation_threshold": 0.5,  # %0.5'ten az hareket
            "weekend_close_profit": 1.5,  # Cuma %1.5+ kârda kapat
            "weekend_close_loss": -1.0,   # Cuma %1+ zararda kapat
            "warning_threshold": 0.8,     # Max sürenin %80'inde uyar
        },
        "scalp": {
            "max_hold_hours": 24,
            "stagnation_hours": 6,
            "stagnation_threshold": 0.3,
            "weekend_close_profit": 0.5,
            "weekend_close_loss": -0.5,
            "warning_threshold": 0.8,
        }
    }

    def __init__(self, mode: str = "swing", weekend_rule_enabled: bool = True):
        """
        Initialize TimeBasedExitManager.

        Args:
            mode: Trading mode - "scalp" or "swing"
            weekend_rule_enabled: Enable weekend risk rule (default True)
        """
        self.mode = mode
        self.params = self.MODE_PARAMS.get(mode, self.MODE_PARAMS["swing"])
        self.weekend_rule_enabled = weekend_rule_enabled

        logger.info(
            "TimeBasedExitManager initialized | Mode: %s | Max Hold: %dh | Weekend Rule: %s",
            mode, self.params["max_hold_hours"], weekend_rule_enabled
        )

    def check_time_based_exit(
        self,
        entry_time: str,
        current_time: str,
        unrealized_pnl_pct: float,
        price_change_since_entry_pct: float,
    ) -> TimeExitResult:
        """
        Zamana dayalı çıkış kontrolü.

        Args:
            entry_time: Pozisyon giriş zamanı (ISO format)
            current_time: Mevcut zaman (ISO format)
            unrealized_pnl_pct: Unrealized PnL yüzdesi
            price_change_since_entry_pct: Entry'den bu yana fiyat değişimi %

        Returns:
            TimeExitResult with exit decision and details
        """
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            current_dt = datetime.fromisoformat(current_time.replace('Z', '+00:00'))
        except Exception as e:
            logger.warning("Failed to parse timestamps: %s", e)
            return TimeExitResult(
                should_exit=False,
                exit_type=ExitType.NONE,
                urgency=ExitUrgency.LOW,
                reason="Timestamp parse error",
                hours_held=0,
                hours_remaining=0,
            )

        hours_held = (current_dt - entry_dt).total_seconds() / 3600
        max_hours = self.params["max_hold_hours"]
        hours_remaining = max(0, max_hours - hours_held)

        # 1. Maximum hold time check
        if hours_held >= max_hours:
            logger.warning(
                "TIME_LIMIT: Position held for %.1fh >= %dh max",
                hours_held, max_hours
            )
            return TimeExitResult(
                should_exit=True,
                exit_type=ExitType.TIME_LIMIT,
                urgency=ExitUrgency.CRITICAL,
                reason=f"Maximum hold time reached ({max_hours}h)",
                hours_held=hours_held,
                hours_remaining=0,
            )

        # 2. Stagnation check
        stag_hours = self.params["stagnation_hours"]
        stag_threshold = self.params["stagnation_threshold"]

        if hours_held >= stag_hours:
            if abs(price_change_since_entry_pct) < stag_threshold:
                if unrealized_pnl_pct < 0.5:  # Not in meaningful profit
                    logger.warning(
                        "STAGNATION: Position held %.1fh with only %.2f%% price change",
                        hours_held, price_change_since_entry_pct
                    )
                    return TimeExitResult(
                        should_exit=True,
                        exit_type=ExitType.STAGNATION,
                        urgency=ExitUrgency.MEDIUM,
                        reason=f"Position stagnant for {hours_held:.0f}h with minimal movement",
                        hours_held=hours_held,
                        hours_remaining=hours_remaining,
                    )

        # 3. Weekend risk check (Friday 20:00 UTC onwards)
        if self.weekend_rule_enabled:
            weekend_result = self._check_weekend_risk(
                current_dt, unrealized_pnl_pct, hours_held, hours_remaining
            )
            if weekend_result.should_exit:
                return weekend_result

        # 4. Approaching max time warning
        warning_threshold = self.params["warning_threshold"]
        if hours_held >= max_hours * warning_threshold:
            return TimeExitResult(
                should_exit=False,
                exit_type=ExitType.APPROACHING_LIMIT,
                urgency=ExitUrgency.MEDIUM,
                reason=f"Approaching max hold time ({hours_held:.0f}h / {max_hours}h)",
                hours_held=hours_held,
                hours_remaining=hours_remaining,
            )

        # No exit needed
        return TimeExitResult(
            should_exit=False,
            exit_type=ExitType.NONE,
            urgency=ExitUrgency.LOW,
            reason="",
            hours_held=hours_held,
            hours_remaining=hours_remaining,
        )

    def _check_weekend_risk(
        self,
        current_dt: datetime,
        unrealized_pnl_pct: float,
        hours_held: float,
        hours_remaining: float,
    ) -> TimeExitResult:
        """
        Weekend risk kontrolü.
        Cuma 20:00 UTC sonrası:
        - %1.5+ kârda → CLOSE
        - %1+ zararda → CLOSE
        """
        # Check if Friday after 20:00 UTC
        if current_dt.weekday() == 4 and current_dt.hour >= 20:
            profit_threshold = self.params["weekend_close_profit"]
            loss_threshold = self.params["weekend_close_loss"]

            if unrealized_pnl_pct >= profit_threshold:
                logger.info(
                    "WEEKEND_RISK: Friday close - securing %.1f%% profit before weekend",
                    unrealized_pnl_pct
                )
                return TimeExitResult(
                    should_exit=True,
                    exit_type=ExitType.WEEKEND_RISK,
                    urgency=ExitUrgency.MEDIUM,
                    reason=f"Friday close - securing {unrealized_pnl_pct:.1f}% profit before weekend",
                    hours_held=hours_held,
                    hours_remaining=hours_remaining,
                )

            if unrealized_pnl_pct <= loss_threshold:
                logger.warning(
                    "WEEKEND_RISK: Friday close - cutting %.1f%% loss before weekend gap risk",
                    unrealized_pnl_pct
                )
                return TimeExitResult(
                    should_exit=True,
                    exit_type=ExitType.WEEKEND_RISK,
                    urgency=ExitUrgency.HIGH,
                    reason="Friday close - cutting loss before weekend gap risk",
                    hours_held=hours_held,
                    hours_remaining=hours_remaining,
                )

        # Saturday/Sunday warning (crypto markets open but traditional finance closed)
        if current_dt.weekday() in [5, 6]:  # Saturday or Sunday
            return TimeExitResult(
                should_exit=False,
                exit_type=ExitType.WEEKEND_RISK,
                urgency=ExitUrgency.LOW,
                reason="Weekend - reduced liquidity possible",
                hours_held=hours_held,
                hours_remaining=hours_remaining,
            )

        return TimeExitResult(
            should_exit=False,
            exit_type=ExitType.NONE,
            urgency=ExitUrgency.LOW,
            reason="",
            hours_held=hours_held,
            hours_remaining=hours_remaining,
        )

    def is_approaching_weekend(self, current_time: str) -> bool:
        """
        Check if we're approaching weekend (Thursday after 18:00 UTC).
        Useful for new position decisions.
        """
        try:
            current_dt = datetime.fromisoformat(current_time.replace('Z', '+00:00'))
            # Thursday after 18:00 or Friday
            if current_dt.weekday() == 3 and current_dt.hour >= 18:
                return True
            if current_dt.weekday() == 4:  # Friday
                return True
            return False
        except Exception:
            return False

    def get_summary_for_prompt(self, result: TimeExitResult) -> str:
        """
        Get compact summary for GLM prompt (~30 tokens).

        Args:
            result: TimeExitResult

        Returns:
            Compact string for prompt
        """
        if result.exit_type == ExitType.NONE and result.urgency == ExitUrgency.LOW:
            return ""

        urgency_emoji = {
            ExitUrgency.LOW: "",
            ExitUrgency.MEDIUM: "W",
            ExitUrgency.HIGH: "H",
            ExitUrgency.CRITICAL: "C",
        }

        urg = urgency_emoji.get(result.urgency, "")

        return (
            f"TIME: {result.hours_held:.0f}h/{self.params['max_hold_hours']}h | "
            f"{result.exit_type.value}{f' [{urg}]' if urg else ''}"
        )

    def should_block_new_position(self, current_time: str) -> tuple[bool, str]:
        """
        Check if new positions should be blocked due to time factors.

        Args:
            current_time: Current time (ISO format)

        Returns:
            Tuple of (should_block, reason)
        """
        try:
            current_dt = datetime.fromisoformat(current_time.replace('Z', '+00:00'))

            # Block new positions on Friday after 18:00 UTC
            if self.weekend_rule_enabled:
                if current_dt.weekday() == 4 and current_dt.hour >= 18:
                    return True, "Friday after 18:00 UTC - avoid new positions before weekend"

            return False, ""

        except Exception:
            return False, ""


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    manager = TimeBasedExitManager(mode="swing", weekend_rule_enabled=True)

    # Test 1: Normal position
    result = manager.check_time_based_exit(
        entry_time="2024-11-26T10:00:00Z",
        current_time="2024-11-26T20:00:00Z",
        unrealized_pnl_pct=1.0,
        price_change_since_entry_pct=1.2,
    )
    print(f"Test 1 - Normal: {result}")

    # Test 2: Max time exceeded
    result = manager.check_time_based_exit(
        entry_time="2024-11-20T10:00:00Z",
        current_time="2024-11-28T10:00:00Z",  # 8 days later
        unrealized_pnl_pct=0.5,
        price_change_since_entry_pct=0.8,
    )
    print(f"Test 2 - Max time: {result}")

    # Test 3: Friday evening with profit
    result = manager.check_time_based_exit(
        entry_time="2024-11-25T10:00:00Z",
        current_time="2024-11-29T21:00:00Z",  # Friday 21:00 UTC
        unrealized_pnl_pct=2.0,
        price_change_since_entry_pct=2.5,
    )
    print(f"Test 3 - Friday profit: {result}")

    # Test 4: Stagnation
    result = manager.check_time_based_exit(
        entry_time="2024-11-24T10:00:00Z",
        current_time="2024-11-26T20:00:00Z",  # 58 hours later
        unrealized_pnl_pct=0.1,
        price_change_since_entry_pct=0.2,  # Only 0.2% move
    )
    print(f"Test 4 - Stagnation: {result}")
