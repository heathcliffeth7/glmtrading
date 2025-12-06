"""
Exit Validator - GLM Elite Swing Trader Framework

Validates CLOSE decisions to prevent premature exits:
- Minimum hold period enforcement (4 hours)
- Fee break-even check (0.15% minimum PnL)
- Exit validation field requirement (SL_HIT, TP_HIT, THESIS_INVALID)
- SL/TP hit verification

This module acts as a safety layer between GLM decisions and executor.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)


class ExitValidator:
    """
    Validates CLOSE decisions to enforce swing trading discipline.

    Rules:
    1. SL/TP must be hit OR thesis must be invalidated
    2. Minimum 4 hour hold period
    3. PnL must exceed fee break-even threshold
    4. exit_validation field must be valid
    """

    # Configuration constants
    MINIMUM_HOLD_HOURS = 4.0
    FEE_BREAKEVEN_PCT = 0.15  # Round-trip fee + margin

    # Valid exit validation values
    VALID_EXIT_VALIDATIONS = {"SL_HIT", "TP_HIT", "THESIS_INVALID"}

    def __init__(self, minimum_hold_hours: float = 4.0, fee_breakeven_pct: float = 0.15):
        """
        Initialize ExitValidator.

        Args:
            minimum_hold_hours: Minimum hours before CLOSE is allowed
            fee_breakeven_pct: Minimum PnL percentage to justify closing
        """
        self.minimum_hold_hours = minimum_hold_hours
        self.fee_breakeven_pct = fee_breakeven_pct

        logger.info(
            "ExitValidator initialized | min_hold=%sh | fee_breakeven=%.2f%%",
            minimum_hold_hours, fee_breakeven_pct
        )

    def validate_close_decision(
        self,
        signal: str,
        exit_validation: Optional[str],
        position: Dict[str, Any],
        current_price: float,
        allow_sl_override: bool = True
    ) -> Tuple[bool, str, str]:
        """
        Validate a CLOSE decision.

        Args:
            signal: The GLM signal (should be "CLOSE")
            exit_validation: The exit validation field from GLM (SL_HIT, TP_HIT, THESIS_INVALID, N/A)
            position: Position dictionary with entry_price, stop_loss, take_profit, open_time
            current_price: Current market price
            allow_sl_override: If True, SL hit bypasses hold period check

        Returns:
            Tuple of (is_valid, reason, override_action)
            - is_valid: True if CLOSE is valid
            - reason: Human-readable explanation
            - override_action: "CLOSE" if valid, "HOLD" if should override
        """
        # Not a CLOSE signal - no validation needed
        if signal != "CLOSE":
            return True, "Not a CLOSE decision", signal

        # No position to close
        if not position or position.get("quantity", 0) == 0:
            return False, "No position to close", "HOLD"

        entry_price = position.get("entry_price", 0)
        stop_loss = position.get("stop_loss", 0)
        take_profit = position.get("take_profit", 0)
        position_type = position.get("type", position.get("position_type", "LONG"))
        open_time = position.get("open_time")

        # Calculate PnL percentage
        if entry_price > 0:
            pnl_pct = abs(current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = 0

        # Check 1: Validate exit_validation field
        if exit_validation not in self.VALID_EXIT_VALIDATIONS:
            if exit_validation in (None, "N/A", ""):
                return False, f"exit_validation field is '{exit_validation}' - CLOSE requires valid reason (SL_HIT, TP_HIT, THESIS_INVALID)", "HOLD"

        # Check 2: Verify SL/TP hit claims
        sl_hit = self._check_sl_hit(current_price, stop_loss, position_type)
        tp_hit = self._check_tp_hit(current_price, take_profit, position_type)

        if exit_validation == "SL_HIT" and not sl_hit:
            # GLM claims SL hit but price isn't at SL
            if abs(current_price - stop_loss) / stop_loss * 100 > 0.5:  # More than 0.5% away
                logger.warning(
                    "GLM claimed SL_HIT but price (%.2f) is not at SL (%.2f)",
                    current_price, stop_loss
                )
                return False, f"SL_HIT claimed but price ({current_price:.2f}) not at SL ({stop_loss:.2f})", "HOLD"

        if exit_validation == "TP_HIT" and not tp_hit:
            # GLM claims TP hit but price isn't at TP
            if abs(current_price - take_profit) / take_profit * 100 > 0.5:  # More than 0.5% away
                logger.warning(
                    "GLM claimed TP_HIT but price (%.2f) is not at TP (%.2f)",
                    current_price, take_profit
                )
                return False, f"TP_HIT claimed but price ({current_price:.2f}) not at TP ({take_profit:.2f})", "HOLD"

        # Check 3: If actual SL hit, allow immediate close (bypass hold period)
        if sl_hit and allow_sl_override:
            logger.info("SL hit detected - allowing immediate CLOSE")
            return True, "Stop loss triggered - immediate close allowed", "CLOSE"

        # Check 3b: THESIS_INVALID allows bypass (RED TEAM critical risk)
        if exit_validation == "THESIS_INVALID":
            logger.info("THESIS_INVALID detected - allowing immediate CLOSE (RED TEAM override)")
            return True, "Thesis invalidated - immediate close allowed", "CLOSE"

        # Check 4: Minimum hold period
        if open_time:
            hold_hours = self._calculate_hold_hours(open_time)
            if hold_hours < self.minimum_hold_hours:
                remaining = self.minimum_hold_hours - hold_hours
                return False, f"Hold period {hold_hours:.1f}h < {self.minimum_hold_hours}h minimum ({remaining:.1f}h remaining)", "HOLD"
        else:
            logger.warning("Position open_time not available - skipping hold period check")

        # Check 5: Fee break-even (only for non-SL exits)
        if not sl_hit and pnl_pct < self.fee_breakeven_pct:
            return False, f"PnL ({pnl_pct:.2f}%) < fee break-even ({self.fee_breakeven_pct:.2f}%)", "HOLD"

        # All checks passed
        return True, f"CLOSE validated: {exit_validation}", "CLOSE"

    def _check_sl_hit(self, current_price: float, stop_loss: float, position_type: str) -> bool:
        """Check if stop loss has been hit."""
        if stop_loss <= 0:
            return False

        if position_type == "LONG":
            return current_price <= stop_loss
        else:  # SHORT
            return current_price >= stop_loss

    def _check_tp_hit(self, current_price: float, take_profit: float, position_type: str) -> bool:
        """Check if take profit has been hit."""
        if take_profit <= 0:
            return False

        if position_type == "LONG":
            return current_price >= take_profit
        else:  # SHORT
            return current_price <= take_profit

    def _calculate_hold_hours(self, open_time) -> float:
        """Calculate hours since position was opened."""
        if isinstance(open_time, str):
            try:
                open_time = datetime.fromisoformat(open_time.replace('Z', '+00:00'))
                open_time = open_time.replace(tzinfo=None)
            except (ValueError, TypeError):
                return float('inf')  # If we can't parse, allow close

        if isinstance(open_time, datetime):
            hold_duration = datetime.utcnow() - open_time
            return hold_duration.total_seconds() / 3600

        return float('inf')  # Unknown format - allow close

    def get_position_exit_status(
        self,
        position: Dict[str, Any],
        current_price: float
    ) -> Dict[str, Any]:
        """
        Get comprehensive exit status for a position.

        Args:
            position: Position dictionary
            current_price: Current market price

        Returns:
            Dictionary with exit status information
        """
        if not position or position.get("quantity", 0) == 0:
            return {"has_position": False}

        entry_price = position.get("entry_price", 0)
        stop_loss = position.get("stop_loss", 0)
        take_profit = position.get("take_profit", 0)
        position_type = position.get("type", position.get("position_type", "LONG"))
        open_time = position.get("open_time")

        # Calculate metrics
        pnl_pct = abs(current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
        sl_distance_pct = abs(current_price - stop_loss) / current_price * 100 if stop_loss > 0 else 0
        tp_distance_pct = abs(take_profit - current_price) / current_price * 100 if take_profit > 0 else 0

        hold_hours = self._calculate_hold_hours(open_time) if open_time else None

        return {
            "has_position": True,
            "position_type": position_type,
            "entry_price": entry_price,
            "current_price": current_price,
            "pnl_pct": pnl_pct,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "sl_distance_pct": sl_distance_pct,
            "tp_distance_pct": tp_distance_pct,
            "hold_hours": hold_hours,
            "sl_hit": self._check_sl_hit(current_price, stop_loss, position_type),
            "tp_hit": self._check_tp_hit(current_price, take_profit, position_type),
            "can_close_by_hold": hold_hours is not None and hold_hours >= self.minimum_hold_hours,
            "can_close_by_fee": pnl_pct >= self.fee_breakeven_pct,
            "close_allowed": (
                self._check_sl_hit(current_price, stop_loss, position_type) or
                self._check_tp_hit(current_price, take_profit, position_type) or
                (hold_hours is not None and hold_hours >= self.minimum_hold_hours and pnl_pct >= self.fee_breakeven_pct)
            )
        }

    def format_exit_status_for_prompt(
        self,
        position: Dict[str, Any],
        current_price: float
    ) -> str:
        """
        Format exit status for inclusion in GLM prompt.

        Args:
            position: Position dictionary
            current_price: Current market price

        Returns:
            Formatted string for prompt
        """
        status = self.get_position_exit_status(position, current_price)

        if not status.get("has_position"):
            return "MEVCUT POZİSYON: YOK (FLAT)"

        hold_str = f"{status['hold_hours']:.1f} saat" if status.get('hold_hours') is not None else "N/A"

        return f"""
================================================================================
MEVCUT POZİSYON DURUMU
================================================================================
Tip: {status['position_type']}
Entry: ${status['entry_price']:,.2f}
Current: ${status['current_price']:,.2f}
PnL: {status['pnl_pct']:+.2f}%

ÇIKIŞ SEVİYELERİ:
├── Stop Loss: ${status['stop_loss']:,.2f} (Uzaklık: {status['sl_distance_pct']:.2f}%) {'HIT!' if status['sl_hit'] else 'Güvende'}
└── Take Profit: ${status['take_profit']:,.2f} (Uzaklık: {status['tp_distance_pct']:.2f}%) {'HIT!' if status['tp_hit'] else 'Bekliyor'}

HOLD DURUMU:
├── Süre: {hold_str}
└── CLOSE izni: {'Var (>4h)' if status['can_close_by_hold'] else 'Yok (<4h)'}

FEE BREAK-EVEN:
└── {status['pnl_pct']:.2f}% {'>' if status['can_close_by_fee'] else '<'} {self.fee_breakeven_pct:.2f}% {'OK' if status['can_close_by_fee'] else 'YETERSIZ'}

CLOSE VERİLEBİLİR Mİ?
{'EVET - SL tetiklendi' if status['sl_hit'] else ''}{'EVET - TP ulaşıldı' if status['tp_hit'] else ''}{'EVET - Hold süresi ve fee OK' if status['close_allowed'] and not status['sl_hit'] and not status['tp_hit'] else ''}{'HAYIR - Koşullar karşılanmadı' if not status['close_allowed'] else ''}
================================================================================
"""


# Singleton instance for easy import
_exit_validator_instance: Optional[ExitValidator] = None


def get_exit_validator() -> ExitValidator:
    """Get or create the singleton ExitValidator instance."""
    global _exit_validator_instance
    if _exit_validator_instance is None:
        _exit_validator_instance = ExitValidator()
    return _exit_validator_instance
