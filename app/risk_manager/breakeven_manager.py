"""
Breakeven Manager - Manage breakeven stop loss movements.

Features:
- Activate after reaching X R profit
- Move SL to entry + buffer (for fees)
- Never move SL back down
- Integration with trailing stop activation

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class BreakevenConfig:
    """Breakeven configuration"""
    activation_r: float  # R-multiple to activate (e.g., 1.0 = 1R profit)
    buffer_pct: float  # Buffer above entry for fees
    trailing_after: bool  # Start trailing after breakeven


class BreakevenManager:
    """
    Manage breakeven stop loss movements.

    Rules:
    1. Activate after reaching X R profit
    2. Move SL to entry + buffer (for fees)
    3. Never move SL back down

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Mode-specific default configurations
    MODE_CONFIGS = {
        "scalp": {
            "activation_r": 1.0,  # Activate at 1R profit
            "buffer_pct": 0.05,   # 0.05% buffer for fees
            "trailing_after": True,
        },
        "swing": {
            "activation_r": 1.5,  # Activate at 1.5R profit (more room)
            "buffer_pct": 0.03,   # 0.03% buffer
            "trailing_after": True,
        }
    }

    def __init__(
        self,
        mode: str = "scalp",
        activation_r: Optional[float] = None,
        buffer_pct: Optional[float] = None,
        feature_enabled: bool = True,
    ):
        """
        Initialize BreakevenManager.

        Args:
            mode: Trading mode - "scalp" or "swing"
            activation_r: Override activation R-multiple
            buffer_pct: Override buffer percentage
            feature_enabled: Enable/disable the feature (for backward compatibility)
        """
        self.mode = mode
        self.feature_enabled = feature_enabled

        # Get mode-specific defaults
        mode_config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])

        self.config = BreakevenConfig(
            activation_r=activation_r if activation_r is not None else mode_config["activation_r"],
            buffer_pct=buffer_pct if buffer_pct is not None else mode_config["buffer_pct"],
            trailing_after=mode_config["trailing_after"],
        )

        self.active_positions: Dict[str, Dict] = {}

        logger.info(
            "BreakevenManager initialized | Mode: %s | Activation R: %.1f | Buffer: %.2f%% | Enabled: %s",
            mode, self.config.activation_r, self.config.buffer_pct, feature_enabled
        )

    def register_position(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        side: str,  # "LONG" or "SHORT"
    ) -> Dict:
        """
        Register a new position for breakeven tracking.

        Args:
            symbol: Trading pair
            entry_price: Entry price
            stop_loss: Initial stop loss
            side: Position side ("LONG" or "SHORT")

        Returns:
            Position tracking dictionary
        """
        if not self.feature_enabled:
            return {"feature_enabled": False}

        # Calculate 1R distance
        if side == "LONG":
            r_distance = entry_price - stop_loss
        else:
            r_distance = stop_loss - entry_price

        if r_distance <= 0:
            logger.warning(
                "Invalid R distance for %s: entry=%.2f, sl=%.2f, side=%s",
                symbol, entry_price, stop_loss, side
            )
            return {"error": "Invalid R distance"}

        position_data = {
            "entry_price": entry_price,
            "original_stop": stop_loss,
            "current_stop": stop_loss,
            "side": side,
            "r_distance": r_distance,
            "is_breakeven": False,
            "highest_r": 0.0,
            "breakeven_activated_at": None,
            "created_at": datetime.utcnow(),
            "feature_enabled": True,
        }

        self.active_positions[symbol] = position_data

        logger.info(
            "Registered position for breakeven tracking: %s %s | Entry: %.2f | SL: %.2f | 1R: %.2f",
            symbol, side, entry_price, stop_loss, r_distance
        )

        return position_data

    def check_and_update(
        self,
        symbol: str,
        current_price: float,
    ) -> Optional[Dict]:
        """
        Check if breakeven should be activated.

        Args:
            symbol: Trading pair
            current_price: Current market price

        Returns:
            Dict with new stop loss if updated, None otherwise
        """
        if not self.feature_enabled:
            return None

        if symbol not in self.active_positions:
            return None

        pos = self.active_positions[symbol]

        # Skip if already at breakeven
        if pos["is_breakeven"]:
            return None

        # Calculate current R
        if pos["side"] == "LONG":
            current_r = (current_price - pos["entry_price"]) / pos["r_distance"]
        else:
            current_r = (pos["entry_price"] - current_price) / pos["r_distance"]

        # Track highest R
        if current_r > pos["highest_r"]:
            pos["highest_r"] = current_r

        # Check activation
        if current_r >= self.config.activation_r:
            # Move to breakeven
            if pos["side"] == "LONG":
                new_stop = pos["entry_price"] * (1 + self.config.buffer_pct / 100)
            else:
                new_stop = pos["entry_price"] * (1 - self.config.buffer_pct / 100)

            old_stop = pos["current_stop"]
            pos["current_stop"] = round(new_stop, 2)
            pos["is_breakeven"] = True
            pos["breakeven_activated_at"] = datetime.utcnow()

            logger.info(
                "Breakeven ACTIVATED for %s | Old SL: %.2f -> New SL: %.2f | Current R: %.2f",
                symbol, old_stop, pos["current_stop"], current_r
            )

            return {
                "action": "MOVE_TO_BREAKEVEN",
                "symbol": symbol,
                "old_stop": old_stop,
                "new_stop": pos["current_stop"],
                "current_r": current_r,
                "message": f"Breakeven activated at {current_r:.2f}R profit",
            }

        return None

    def should_move_to_breakeven(
        self,
        entry_price: float,
        current_price: float,
        stop_loss: float,
        side: str,
    ) -> Dict:
        """
        Standalone function to check breakeven eligibility.
        Can be used without registering a position.

        Args:
            entry_price: Entry price
            current_price: Current market price
            stop_loss: Current stop loss
            side: Position side ("LONG" or "SHORT")

        Returns:
            Dict with recommendation
        """
        if not self.feature_enabled:
            return {"eligible": False, "reason": "Feature disabled", "feature_enabled": False}

        # Calculate R
        if side == "LONG":
            risk = entry_price - stop_loss
            profit = current_price - entry_price
        else:
            risk = stop_loss - entry_price
            profit = entry_price - current_price

        if risk <= 0:
            return {"eligible": False, "reason": "Invalid risk calculation"}

        current_r = profit / risk

        if current_r >= self.config.activation_r:
            # Calculate new stop
            if side == "LONG":
                new_stop = entry_price * (1 + self.config.buffer_pct / 100)
            else:
                new_stop = entry_price * (1 - self.config.buffer_pct / 100)

            return {
                "eligible": True,
                "current_r": current_r,
                "new_stop": round(new_stop, 2),
                "reason": f"Position at {current_r:.2f}R profit - move SL to breakeven",
            }
        else:
            return {
                "eligible": False,
                "current_r": current_r,
                "needed_r": self.config.activation_r,
                "reason": f"Need {self.config.activation_r}R profit, currently at {current_r:.2f}R",
            }

    def get_status(self, symbol: str) -> Optional[Dict]:
        """Get current breakeven status for a position"""
        if not self.feature_enabled:
            return None

        if symbol not in self.active_positions:
            return None

        pos = self.active_positions[symbol]

        return {
            "symbol": symbol,
            "side": pos["side"],
            "entry_price": pos["entry_price"],
            "original_stop": pos["original_stop"],
            "current_stop": pos["current_stop"],
            "r_distance": pos["r_distance"],
            "is_breakeven": pos["is_breakeven"],
            "highest_r": pos["highest_r"],
            "activation_r": self.config.activation_r,
            "breakeven_activated_at": pos["breakeven_activated_at"],
            "feature_enabled": True,
        }

    def get_prompt_section(self, symbol: str) -> str:
        """Generate prompt section for position"""
        if not self.feature_enabled:
            return ""

        if symbol not in self.active_positions:
            return ""

        pos = self.active_positions[symbol]

        status = "BREAKEVEN ACTIVE" if pos["is_breakeven"] else f"Waiting for {self.config.activation_r}R profit"

        lines = [
            "",
            f"Breakeven Status ({symbol}): {status}",
            f"  Entry: ${pos['entry_price']:,.2f}",
            f"  Original SL: ${pos['original_stop']:,.2f}",
            f"  Current SL: ${pos['current_stop']:,.2f}",
            f"  Highest R: {pos['highest_r']:.2f}",
        ]

        if pos["is_breakeven"] and pos["breakeven_activated_at"]:
            lines.append(f"  Activated: {pos['breakeven_activated_at'].strftime('%H:%M UTC')}")

        return "\n".join(lines)

    def remove_position(self, symbol: str) -> bool:
        """Remove position from tracking (e.g., when closed)"""
        if symbol in self.active_positions:
            del self.active_positions[symbol]
            logger.info("Breakeven tracking removed for %s", symbol)
            return True
        return False

    def should_apply_to_trade(self, trade_created_at: datetime) -> bool:
        """
        Check if breakeven should apply to a specific trade.
        For backward compatibility with existing positions.

        Args:
            trade_created_at: Timestamp when the trade was created

        Returns:
            True if breakeven should apply
        """
        if not self.feature_enabled:
            return False

        # Feature release date - trades before this use old behavior
        feature_release_date = datetime(2024, 11, 26, 0, 0, 0)
        return trade_created_at >= feature_release_date

    def get_all_active(self) -> List[Dict]:
        """Get all positions being tracked for breakeven"""
        if not self.feature_enabled:
            return []

        return [
            {
                "symbol": symbol,
                "side": pos["side"],
                "is_breakeven": pos["is_breakeven"],
                "current_stop": pos["current_stop"],
                "highest_r": pos["highest_r"],
            }
            for symbol, pos in self.active_positions.items()
        ]

    def get_summary(self) -> Dict:
        """Get summary of breakeven manager state"""
        return {
            "mode": self.mode,
            "feature_enabled": self.feature_enabled,
            "activation_r": self.config.activation_r,
            "buffer_pct": self.config.buffer_pct,
            "active_positions": len(self.active_positions),
            "breakeven_count": sum(1 for p in self.active_positions.values() if p["is_breakeven"]),
            "pending_count": sum(1 for p in self.active_positions.values() if not p["is_breakeven"]),
        }


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    breakeven = BreakevenManager(mode="scalp")

    # Register a LONG position
    breakeven.register_position(
        symbol="BTCUSDT",
        entry_price=100000,
        stop_loss=98500,  # 1.5% SL
        side="LONG",
    )

    print("Initial status:", breakeven.get_status("BTCUSDT"))

    # Check at various prices
    print("\n--- Price at 100500 (not enough profit) ---")
    result = breakeven.check_and_update("BTCUSDT", 100500)
    print(f"Result: {result}")

    print("\n--- Price at 101500 (1R profit) ---")
    result = breakeven.check_and_update("BTCUSDT", 101500)
    print(f"Result: {result}")

    print("\nFinal status:", breakeven.get_status("BTCUSDT"))
    print(breakeven.get_prompt_section("BTCUSDT"))

    # Test standalone check
    print("\n--- Standalone check ---")
    check = breakeven.should_move_to_breakeven(
        entry_price=100000,
        current_price=102000,
        stop_loss=98500,
        side="LONG",
    )
    print(f"Check result: {check}")
