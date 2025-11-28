"""
Partial Take Profit Manager - Kademeli kar alma sistemi.

Features:
- 3 kademeli TP: 1R=%40, 2R=%30, 3R=%30 (trailing)
- TP1 sonrası breakeven hareketi
- TP2 sonrası trailing aktivasyonu
- Risk azaltma ve kar kilitleme

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TPStatus(Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    CANCELLED = "cancelled"


@dataclass
class TPLevel:
    """Single take profit level"""
    level_id: int
    price: float
    percentage: float  # % of position to close
    status: TPStatus = TPStatus.PENDING
    triggered_at: Optional[datetime] = None
    actual_close_price: Optional[float] = None


@dataclass
class PartialTPPlan:
    """Complete partial take profit plan"""
    entry_price: float
    position_side: str  # "LONG" or "SHORT"
    initial_quantity: float
    stop_loss: float
    levels: List[TPLevel] = field(default_factory=list)
    remaining_quantity: float = 0.0
    is_breakeven: bool = False
    trailing_active: bool = False
    trailing_stop: Optional[float] = None
    created_at: Optional[datetime] = None

    def __post_init__(self):
        self.remaining_quantity = self.initial_quantity
        self.created_at = datetime.utcnow()


class PartialTakeProfitManager:
    """
    Manages partial take profit execution with automatic:
    - TP level triggering
    - Breakeven adjustment after TP1
    - Trailing stop activation after TP2

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Mode-specific default configurations
    MODE_CONFIGS = {
        "scalp": {
            "rr_levels": [1.0, 1.5, 2.5],  # R:R targets
            "percentages": [40, 30, 30],   # % to close at each level
            "atr_pct": 1.0,                # Trailing distance
        },
        "swing": {
            # GLM Elite Swing Trader config update
            "rr_levels": [1.5, 2.5, 3.5],  # TP1: 1.5R, TP2: 2.5R, TP3: 3.5R (changed from 4.0)
            "percentages": [33, 33, 34],   # Equal distribution (changed from 35/35/30)
            "atr_pct": 1.0,                # Trailing distance (changed from 1.5)
        }
    }

    def __init__(
        self,
        mode: str = "scalp",
        atr_pct: Optional[float] = None,
        feature_enabled: bool = True,
    ):
        """
        Initialize PartialTakeProfitManager.

        Args:
            mode: Trading mode - "scalp" or "swing"
            atr_pct: Override ATR percentage for trailing
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        self.config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])
        self.atr_pct = atr_pct if atr_pct is not None else self.config["atr_pct"]
        self.active_plans: Dict[str, PartialTPPlan] = {}

        logger.info(
            "PartialTakeProfitManager initialized | Mode: %s | R:R Levels: %s | Enabled: %s",
            mode, self.config["rr_levels"], feature_enabled
        )

    def create_tp_plan(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        position_side: str,
        quantity: float,
        custom_rr_levels: Optional[List[float]] = None,
        custom_percentages: Optional[List[float]] = None,
    ) -> Optional[PartialTPPlan]:
        """
        Create a partial take profit plan.

        Args:
            symbol: Trading pair
            entry_price: Entry price
            stop_loss: Stop loss price
            position_side: "LONG" or "SHORT"
            quantity: Total position size
            custom_rr_levels: Optional custom R:R levels
            custom_percentages: Optional custom percentages

        Returns:
            PartialTPPlan or None if feature disabled
        """
        if not self.feature_enabled:
            return None

        # Calculate risk (R)
        if position_side == "LONG":
            risk = entry_price - stop_loss
        else:
            risk = stop_loss - entry_price

        if risk <= 0:
            logger.error(
                "Invalid risk calculation: entry=%.2f, sl=%.2f, side=%s",
                entry_price, stop_loss, position_side
            )
            return None

        # Use custom or default levels
        rr_levels = custom_rr_levels or self.config["rr_levels"]
        percentages = custom_percentages or self.config["percentages"]

        # Ensure they match
        if len(rr_levels) != len(percentages):
            logger.error("R:R levels and percentages must have same length")
            return None

        # Create TP levels
        levels = []
        for i, (rr, pct) in enumerate(zip(rr_levels, percentages)):
            if position_side == "LONG":
                tp_price = entry_price + (risk * rr)
            else:
                tp_price = entry_price - (risk * rr)

            levels.append(TPLevel(
                level_id=i + 1,
                price=round(tp_price, 2),
                percentage=pct,
            ))

        plan = PartialTPPlan(
            entry_price=entry_price,
            position_side=position_side,
            initial_quantity=quantity,
            stop_loss=stop_loss,
            levels=levels,
        )

        self.active_plans[symbol] = plan

        logger.info(
            "Created Partial TP Plan for %s: Entry=%.2f, SL=%.2f, "
            "TP1=%.2f (%.0f%%), TP2=%.2f (%.0f%%), TP3=%.2f (%.0f%%)",
            symbol, entry_price, stop_loss,
            levels[0].price, levels[0].percentage,
            levels[1].price, levels[1].percentage,
            levels[2].price, levels[2].percentage
        )

        return plan

    def check_and_execute(
        self,
        symbol: str,
        current_price: float,
        current_high: float,
        current_low: float
    ) -> Optional[Dict]:
        """
        Check if any TP levels are triggered and return execution instructions.

        Args:
            symbol: Trading pair
            current_price: Current market price
            current_high: Current candle high
            current_low: Current candle low

        Returns:
            Dict with action details or None if no action needed
        """
        if not self.feature_enabled:
            return None

        if symbol not in self.active_plans:
            return None

        plan = self.active_plans[symbol]
        actions = []

        for level in plan.levels:
            if level.status != TPStatus.PENDING:
                continue

            # Check if price hit TP level
            triggered = False
            if plan.position_side == "LONG":
                if current_high >= level.price:
                    triggered = True
            else:  # SHORT
                if current_low <= level.price:
                    triggered = True

            if triggered:
                # Calculate quantity to close
                close_qty = plan.initial_quantity * (level.percentage / 100)

                level.status = TPStatus.TRIGGERED
                level.triggered_at = datetime.utcnow()
                level.actual_close_price = level.price
                plan.remaining_quantity -= close_qty

                action = {
                    "action": "PARTIAL_CLOSE",
                    "level": level.level_id,
                    "price": level.price,
                    "quantity": close_qty,
                    "percentage": level.percentage,
                    "remaining_qty": plan.remaining_quantity,
                    "remaining_pct": (plan.remaining_quantity / plan.initial_quantity) * 100,
                }
                actions.append(action)

                logger.info(
                    "TP%d triggered for %s at %.2f - Closing %.4f (%.0f%%), Remaining: %.4f",
                    level.level_id, symbol, level.price, close_qty,
                    level.percentage, plan.remaining_quantity
                )

                # Special actions after specific TPs
                if level.level_id == 1:
                    # After TP1: Move SL to breakeven
                    self._move_to_breakeven(plan)
                    action["sl_adjustment"] = {
                        "new_stop_loss": plan.stop_loss,
                        "reason": "Moved to breakeven after TP1"
                    }

                elif level.level_id == 2:
                    # After TP2: Activate trailing stop
                    self._activate_trailing(plan, current_price)
                    action["trailing_activated"] = True
                    action["trailing_stop"] = plan.trailing_stop

        # Check trailing stop if active
        if plan.trailing_active and plan.remaining_quantity > 0:
            trail_action = self._check_trailing_stop(plan, current_price, current_low, current_high)
            if trail_action:
                actions.append(trail_action)

        if actions:
            return {
                "symbol": symbol,
                "actions": actions,
                "plan_status": self._get_plan_status(plan)
            }

        return None

    def _move_to_breakeven(self, plan: PartialTPPlan):
        """Move stop loss to breakeven (entry price) after TP1"""
        # Add small buffer for fees
        buffer_pct = 0.05  # 0.05% buffer

        if plan.position_side == "LONG":
            new_sl = plan.entry_price * (1 + buffer_pct / 100)
        else:
            new_sl = plan.entry_price * (1 - buffer_pct / 100)

        old_sl = plan.stop_loss
        plan.stop_loss = round(new_sl, 2)
        plan.is_breakeven = True

        logger.info(
            "Moved SL to breakeven for %s: %.2f -> %.2f",
            plan.position_side, old_sl, plan.stop_loss
        )

    def _activate_trailing(self, plan: PartialTPPlan, current_price: float):
        """Activate trailing stop after TP2"""
        # Trail distance: ATR percentage (approximately)
        trail_distance_pct = max(self.atr_pct, 0.5)

        if plan.position_side == "LONG":
            plan.trailing_stop = current_price * (1 - trail_distance_pct / 100)
        else:
            plan.trailing_stop = current_price * (1 + trail_distance_pct / 100)

        plan.trailing_stop = round(plan.trailing_stop, 2)
        plan.trailing_active = True

        logger.info(
            "Trailing stop activated: %.2f (%.1f%% distance)",
            plan.trailing_stop, trail_distance_pct
        )

    def _check_trailing_stop(
        self,
        plan: PartialTPPlan,
        current_price: float,
        current_low: float,
        current_high: float
    ) -> Optional[Dict]:
        """Update trailing stop and check if hit"""
        if not plan.trailing_stop:
            return None

        trail_distance_pct = max(self.atr_pct, 0.5)

        # Update trailing stop
        if plan.position_side == "LONG":
            new_trail = current_price * (1 - trail_distance_pct / 100)
            if new_trail > plan.trailing_stop:
                old_trail = plan.trailing_stop
                plan.trailing_stop = round(new_trail, 2)
                logger.debug("Trailing stop updated: %.2f -> %.2f", old_trail, plan.trailing_stop)

            # Check if hit
            if current_low <= plan.trailing_stop:
                return {
                    "action": "TRAILING_STOP_HIT",
                    "price": plan.trailing_stop,
                    "quantity": plan.remaining_quantity,
                    "reason": "Trailing stop triggered"
                }
        else:  # SHORT
            new_trail = current_price * (1 + trail_distance_pct / 100)
            if new_trail < plan.trailing_stop:
                plan.trailing_stop = round(new_trail, 2)

            if current_high >= plan.trailing_stop:
                return {
                    "action": "TRAILING_STOP_HIT",
                    "price": plan.trailing_stop,
                    "quantity": plan.remaining_quantity,
                    "reason": "Trailing stop triggered"
                }

        return None

    def _get_plan_status(self, plan: PartialTPPlan) -> Dict:
        """Get current status of the plan"""
        triggered_count = sum(1 for l in plan.levels if l.status == TPStatus.TRIGGERED)
        pending_count = sum(1 for l in plan.levels if l.status == TPStatus.PENDING)

        return {
            "triggered_levels": triggered_count,
            "pending_levels": pending_count,
            "remaining_quantity": plan.remaining_quantity,
            "remaining_pct": (plan.remaining_quantity / plan.initial_quantity) * 100,
            "is_breakeven": plan.is_breakeven,
            "trailing_active": plan.trailing_active,
            "trailing_stop": plan.trailing_stop,
            "current_stop_loss": plan.stop_loss,
        }

    def get_plan_for_prompt(self, symbol: str) -> Optional[str]:
        """Get human-readable plan status for GLM prompt"""
        if not self.feature_enabled:
            return None

        if symbol not in self.active_plans:
            return None

        plan = self.active_plans[symbol]
        status = self._get_plan_status(plan)

        lines = [
            f"Partial TP Plan ({plan.position_side}):",
        ]

        for level in plan.levels:
            status_emoji = "Triggered" if level.status == TPStatus.TRIGGERED else "Pending"
            lines.append(f"  TP{level.level_id}: ${level.price:.2f} ({level.percentage}%) - {status_emoji}")

        lines.extend([
            f"  Current SL: ${plan.stop_loss:.2f} {'(Breakeven)' if plan.is_breakeven else ''}",
            f"  Remaining: {plan.remaining_quantity:.4f} ({status['remaining_pct']:.0f}%)",
        ])

        if plan.trailing_active:
            lines.append(f"  Trailing Stop: ${plan.trailing_stop:.2f}")

        return "\n".join(lines)

    def get_status(self, symbol: str) -> Optional[Dict]:
        """Get current status of a plan"""
        if not self.feature_enabled:
            return None

        if symbol not in self.active_plans:
            return None

        plan = self.active_plans[symbol]
        return {
            "symbol": symbol,
            "position_side": plan.position_side,
            "entry_price": plan.entry_price,
            "initial_quantity": plan.initial_quantity,
            "remaining_quantity": plan.remaining_quantity,
            "stop_loss": plan.stop_loss,
            "is_breakeven": plan.is_breakeven,
            "trailing_active": plan.trailing_active,
            "trailing_stop": plan.trailing_stop,
            "levels": [
                {
                    "level_id": l.level_id,
                    "price": l.price,
                    "percentage": l.percentage,
                    "status": l.status.value,
                    "triggered_at": l.triggered_at.isoformat() if l.triggered_at else None,
                }
                for l in plan.levels
            ],
            "feature_enabled": True,
        }

    def remove_plan(self, symbol: str) -> bool:
        """Remove plan for a symbol (e.g., when position is fully closed)"""
        if symbol in self.active_plans:
            del self.active_plans[symbol]
            logger.info("Partial TP plan removed for %s", symbol)
            return True
        return False

    def should_apply_to_trade(self, trade_created_at: datetime) -> bool:
        """
        Check if partial TP should apply to a specific trade.
        For backward compatibility with existing positions.
        """
        if not self.feature_enabled:
            return False

        feature_release_date = datetime(2024, 11, 26, 0, 0, 0)
        return trade_created_at >= feature_release_date

    def get_all_active(self) -> List[Dict]:
        """Get all active partial TP plans"""
        if not self.feature_enabled:
            return []

        return [
            {
                "symbol": symbol,
                "position_side": plan.position_side,
                "remaining_pct": (plan.remaining_quantity / plan.initial_quantity) * 100,
                "is_breakeven": plan.is_breakeven,
                "trailing_active": plan.trailing_active,
            }
            for symbol, plan in self.active_plans.items()
        ]


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    manager = PartialTakeProfitManager(mode="scalp")

    # Create plan for LONG position
    plan = manager.create_tp_plan(
        symbol="BTCUSDT",
        entry_price=100000,
        stop_loss=98500,  # 1.5% SL
        position_side="LONG",
        quantity=0.5,
    )

    print(f"Created plan:")
    print(f"TP1: ${plan.levels[0].price} ({plan.levels[0].percentage}%)")
    print(f"TP2: ${plan.levels[1].price} ({plan.levels[1].percentage}%)")
    print(f"TP3: ${plan.levels[2].price} ({plan.levels[2].percentage}%)")

    # Simulate price hitting TP1
    print("\n--- Price hits TP1 ---")
    result = manager.check_and_execute("BTCUSDT", 101500, 101600, 101400)
    print(f"Result: {result}")

    # Simulate price hitting TP2
    print("\n--- Price hits TP2 ---")
    result = manager.check_and_execute("BTCUSDT", 102250, 102300, 102200)
    print(f"Result: {result}")

    print("\n" + manager.get_plan_for_prompt("BTCUSDT"))
