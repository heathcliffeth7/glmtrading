"""
Advanced Trailing Stop System.

Features:
- Multiple trailing stop strategies (Fixed %, ATR-based, Chandelier, Step)
- Activation threshold (only activates after X profit)
- Dynamic trail distance based on volatility
- Integration with position monitoring

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from enum import Enum
from typing import Optional, Dict, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TrailingStopType(Enum):
    FIXED_PERCENTAGE = "fixed_pct"
    ATR_BASED = "atr"
    CHANDELIER = "chandelier"
    STEP = "step"
    SWING_BASED = "swing"  # v2.0: Swing low/high takibi


class AdvancedTrailingStop:
    """
    Advanced trailing stop system with multiple strategies.

    Features:
    - ATR-based dynamic trailing
    - Activation threshold (only activates after X profit)
    - Step trailing (moves in discrete steps)
    - Chandelier exit

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Mode-specific default configurations
    MODE_CONFIGS = {
        "scalp": {
            "default_type": TrailingStopType.ATR_BASED,
            "atr_multiplier": 1.5,
            "fixed_pct": 1.0,
            "activation_profit_pct": 0.5,
            "step_size_pct": 0.3,
            "min_step_pct": 0.3,  # v2.0: Min hareket için güncelle
        },
        "swing": {
            # GLM Elite Swing Trader config update
            "default_type": TrailingStopType.SWING_BASED,  # v2.0: SWING_BASED default
            "atr_multiplier": 2.5,  # v2.0: ATR fallback için
            "fixed_pct": 2.0,
            "activation_profit_pct": 1.5,  # 1.5R kârda aktif
            "step_size_pct": 0.5,
            "min_step_pct": 0.5,  # v2.0: Min hareket için güncelle
        }
    }

    def __init__(self, mode: str = "scalp", feature_enabled: bool = True):
        """
        Initialize AdvancedTrailingStop.

        Args:
            mode: Trading mode - "scalp" or "swing"
            feature_enabled: Enable/disable the feature (for backward compatibility)
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        self.config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])
        self.active_trails: Dict[str, Dict] = {}

        logger.info(
            "AdvancedTrailingStop initialized | Mode: %s | Enabled: %s",
            mode, feature_enabled
        )

    def create_trailing_stop(
        self,
        symbol: str,
        entry_price: float,
        position_side: str,
        initial_stop: float,
        trail_type: Optional[TrailingStopType] = None,
        atr_value: float = 0.0,
        atr_multiplier: Optional[float] = None,
        fixed_pct: Optional[float] = None,
        activation_profit_pct: Optional[float] = None,
        step_size_pct: Optional[float] = None,
    ) -> Dict:
        """
        Create a new trailing stop configuration.

        Args:
            symbol: Trading pair
            entry_price: Entry price
            position_side: "LONG" or "SHORT"
            initial_stop: Initial stop loss price
            trail_type: Type of trailing stop (uses mode default if not specified)
            atr_value: ATR value (required for ATR_BASED and CHANDELIER)
            atr_multiplier: Multiplier for ATR (uses mode default if not specified)
            fixed_pct: Fixed percentage for FIXED_PERCENTAGE type
            activation_profit_pct: Minimum profit % to activate trailing
            step_size_pct: Step size for STEP type

        Returns:
            Configuration dictionary
        """
        if not self.feature_enabled:
            return {"feature_enabled": False}

        # Use mode defaults if not specified
        trail_type = trail_type or self.config["default_type"]
        atr_multiplier = atr_multiplier if atr_multiplier is not None else self.config["atr_multiplier"]
        fixed_pct = fixed_pct if fixed_pct is not None else self.config["fixed_pct"]
        activation_profit_pct = activation_profit_pct if activation_profit_pct is not None else self.config["activation_profit_pct"]
        step_size_pct = step_size_pct if step_size_pct is not None else self.config["step_size_pct"]

        # Calculate activation price
        if position_side == "LONG":
            activation_price = entry_price * (1 + activation_profit_pct / 100)
        else:
            activation_price = entry_price * (1 - activation_profit_pct / 100)

        config = {
            "symbol": symbol,
            "entry_price": entry_price,
            "position_side": position_side,
            "current_stop": initial_stop,
            "highest_price": entry_price if position_side == "LONG" else None,
            "lowest_price": entry_price if position_side == "SHORT" else None,
            "trail_type": trail_type,
            "atr_value": atr_value,
            "atr_multiplier": atr_multiplier,
            "fixed_pct": fixed_pct,
            "activation_price": activation_price,
            "activation_profit_pct": activation_profit_pct,
            "step_size_pct": step_size_pct,
            "min_step_pct": self.config.get("min_step_pct", 0.5),  # v2.0
            "is_activated": False,
            "last_step_price": None,  # For STEP type
            "last_swing_low": None,   # v2.0: For SWING_BASED
            "last_swing_high": None,  # v2.0: For SWING_BASED
            "updates_count": 0,
            "created_at": datetime.utcnow(),
            "last_updated_at": datetime.utcnow(),
            "feature_enabled": True,
        }

        self.active_trails[symbol] = config

        logger.info(
            "Created %s trailing stop for %s | Entry: %.2f | "
            "Initial Stop: %.2f | Activation at: %.2f (%.1f%% profit)",
            trail_type.value, symbol, entry_price, initial_stop,
            activation_price, activation_profit_pct
        )

        return config

    def update(
        self,
        symbol: str,
        current_price: float,
        current_high: float,
        current_low: float,
        current_atr: Optional[float] = None,
        swing_low: Optional[float] = None,   # v2.0: For SWING_BASED
        swing_high: Optional[float] = None,  # v2.0: For SWING_BASED
    ) -> Optional[Dict]:
        """
        Update trailing stop based on current price.

        Args:
            symbol: Trading pair
            current_price: Current market price
            current_high: Current candle high
            current_low: Current candle low
            current_atr: Current ATR value (optional, updates if provided)
            swing_low: Recent swing low (optional, for SWING_BASED)
            swing_high: Recent swing high (optional, for SWING_BASED)

        Returns:
            Dict with:
            - "updated": bool - whether stop was updated
            - "new_stop": float - new stop price
            - "triggered": bool - whether stop was hit
        """
        if not self.feature_enabled:
            return None

        if symbol not in self.active_trails:
            return None

        config = self.active_trails[symbol]
        result = {
            "updated": False,
            "new_stop": config["current_stop"],
            "triggered": False,
            "old_stop": config["current_stop"],
            "is_activated": config["is_activated"],
        }

        # Update ATR if provided
        if current_atr is not None:
            config["atr_value"] = current_atr

        # v2.0: Update swing levels if provided
        if swing_low is not None:
            config["last_swing_low"] = swing_low
        if swing_high is not None:
            config["last_swing_high"] = swing_high

        # Check if trailing should be activated
        if not config["is_activated"]:
            if self._check_activation(config, current_price):
                config["is_activated"] = True
                result["is_activated"] = True
                logger.info(
                    "Trailing stop ACTIVATED for %s at price %.2f",
                    symbol, current_price
                )

        # Update highest/lowest price tracking
        if config["position_side"] == "LONG":
            if current_high > (config["highest_price"] or 0):
                config["highest_price"] = current_high
        else:  # SHORT
            if config["lowest_price"] is None or current_low < config["lowest_price"]:
                config["lowest_price"] = current_low

        # Calculate new trailing stop if activated
        if config["is_activated"]:
            new_stop = self._calculate_trail(config, current_price, current_high, current_low)

            # Only move stop in favorable direction
            should_update = False

            # v2.0: Min step check to avoid micro-updates
            min_step_pct = config.get("min_step_pct", 0.5)
            step_threshold = config["current_stop"] * (min_step_pct / 100)

            if config["position_side"] == "LONG":
                if new_stop > config["current_stop"] + step_threshold:
                    should_update = True
            else:  # SHORT
                if new_stop < config["current_stop"] - step_threshold:
                    should_update = True

            if should_update:
                config["current_stop"] = new_stop
                config["updates_count"] += 1
                config["last_updated_at"] = datetime.utcnow()
                result["updated"] = True
                result["new_stop"] = new_stop

                logger.info(
                    "Trailing stop updated for %s: %.2f -> %.2f (update #%d)",
                    symbol, result["old_stop"], new_stop, config["updates_count"]
                )

        # Check if stop was triggered
        if self._check_triggered(config, current_low, current_high):
            result["triggered"] = True
            result["trigger_price"] = config["current_stop"]

            logger.info(
                "Trailing stop TRIGGERED for %s at %.2f",
                symbol, config["current_stop"]
            )

            # Remove from active trails
            del self.active_trails[symbol]

        return result

    def _check_activation(self, config: Dict, current_price: float) -> bool:
        """Check if trailing should be activated based on profit threshold"""
        if config["position_side"] == "LONG":
            return current_price >= config["activation_price"]
        else:
            return current_price <= config["activation_price"]

    def _calculate_trail(
        self,
        config: Dict,
        current_price: float,
        current_high: float,
        current_low: float
    ) -> float:
        """Calculate new trailing stop price based on strategy type"""
        trail_type = config["trail_type"]

        if trail_type == TrailingStopType.FIXED_PERCENTAGE:
            return self._calc_fixed_pct(config, current_price)

        elif trail_type == TrailingStopType.ATR_BASED:
            return self._calc_atr_based(config, current_price)

        elif trail_type == TrailingStopType.SWING_BASED:
            return self._calc_swing_based(config, current_price)

        elif trail_type == TrailingStopType.CHANDELIER:
            return self._calc_chandelier(config)

        elif trail_type == TrailingStopType.STEP:
            return self._calc_step(config, current_price)

        return config["current_stop"]

    def _calc_fixed_pct(self, config: Dict, current_price: float) -> float:
        """Fixed percentage trailing"""
        pct = config["fixed_pct"]

        if config["position_side"] == "LONG":
            # Trail below highest price
            reference = config["highest_price"]
            return reference * (1 - pct / 100)
        else:
            reference = config["lowest_price"]
            return reference * (1 + pct / 100)

    def _calc_atr_based(self, config: Dict, current_price: float) -> float:
        """ATR-based trailing (dynamic distance)"""
        atr = config["atr_value"]
        multiplier = config["atr_multiplier"]

        if atr <= 0:
            # Fallback to fixed percentage
            return self._calc_fixed_pct(config, current_price)

        trail_distance = atr * multiplier

        if config["position_side"] == "LONG":
            reference = config["highest_price"]
            return reference - trail_distance
        else:
            reference = config["lowest_price"]
            return reference + trail_distance

    def _calc_chandelier(self, config: Dict) -> float:
        """
        Chandelier Exit: Uses highest high (LONG) or lowest low (SHORT)
        minus ATR x multiplier
        """
        atr = config["atr_value"]
        multiplier = config["atr_multiplier"]

        if atr <= 0:
            return config["current_stop"]

        if config["position_side"] == "LONG":
            # Chandelier Exit Long = Highest High - ATR x multiplier
            return config["highest_price"] - (atr * multiplier)
        else:
            # Chandelier Exit Short = Lowest Low + ATR x multiplier
            return config["lowest_price"] + (atr * multiplier)

    def _calc_step(self, config: Dict, current_price: float) -> float:
        """
        Step trailing: Only moves in discrete steps.
        Good for reducing whipsaws in choppy markets.
        """
        step_pct = config["step_size_pct"]
        entry = config["entry_price"]
        last_step = config.get("last_step_price") or entry

        # Calculate how many steps we've moved
        if config["position_side"] == "LONG":
            profit_pct = (current_price - entry) / entry * 100
            steps = int(profit_pct / step_pct)

            if steps > 0:
                # New step level
                new_step_price = entry * (1 + (steps - 1) * step_pct / 100)
                if new_step_price > last_step:
                    config["last_step_price"] = new_step_price
                    # Stop is one step below current step
                    return new_step_price

            return config["current_stop"]

        else:  # SHORT
            profit_pct = (entry - current_price) / entry * 100
            steps = int(profit_pct / step_pct)

            if steps > 0:
                new_step_price = entry * (1 - (steps - 1) * step_pct / 100)
                if new_step_price < last_step:
                    config["last_step_price"] = new_step_price
                    return new_step_price

            return config["current_stop"]

    def _calc_swing_based(self, config: Dict, current_price: float) -> float:
        """
        v2.0: Swing-based trailing stop.
        Uses recent swing low (LONG) or swing high (SHORT) as trailing reference.
        Falls back to ATR-based if no swing levels available.
        """
        position_side = config["position_side"]
        swing_low = config.get("last_swing_low")
        swing_high = config.get("last_swing_high")

        if position_side == "LONG":
            if swing_low and swing_low > 0:
                # Trail just below the swing low (0.2% buffer)
                new_stop = swing_low * 0.998
                # Ensure we don't move stop backwards
                if new_stop > config["current_stop"]:
                    return new_stop
                return config["current_stop"]
            else:
                # Fallback to ATR-based if no swing low
                return self._calc_atr_based(config, current_price)

        else:  # SHORT
            if swing_high and swing_high > 0:
                # Trail just above the swing high (0.2% buffer)
                new_stop = swing_high * 1.002
                # Ensure we don't move stop backwards
                if new_stop < config["current_stop"]:
                    return new_stop
                return config["current_stop"]
            else:
                # Fallback to ATR-based if no swing high
                return self._calc_atr_based(config, current_price)

    def _check_triggered(
        self,
        config: Dict,
        current_low: float,
        current_high: float
    ) -> bool:
        """Check if trailing stop was hit"""
        stop = config["current_stop"]

        if config["position_side"] == "LONG":
            return current_low <= stop
        else:
            return current_high >= stop

    def get_status(self, symbol: str) -> Optional[Dict]:
        """Get current trailing stop status"""
        if not self.feature_enabled:
            return None

        if symbol not in self.active_trails:
            return None

        config = self.active_trails[symbol]

        return {
            "symbol": symbol,
            "position_side": config["position_side"],
            "entry_price": config["entry_price"],
            "current_stop": config["current_stop"],
            "is_activated": config["is_activated"],
            "trail_type": config["trail_type"].value,
            "highest_price": config.get("highest_price"),
            "lowest_price": config.get("lowest_price"),
            "updates_count": config["updates_count"],
            "activation_price": config["activation_price"],
            "atr_value": config["atr_value"],
            "feature_enabled": True,
        }

    def get_status_for_prompt(self, symbol: str) -> str:
        """Get human-readable status for GLM prompt"""
        status = self.get_status(symbol)
        if not status:
            return ""

        lines = [
            f"Trailing Stop ({status['trail_type']}):",
            f"  Current Stop: ${status['current_stop']:.2f}",
            f"  Activated: {'Yes' if status['is_activated'] else 'No (waiting for profit threshold)'}",
            f"  Updates: {status['updates_count']}",
        ]

        if status["position_side"] == "LONG" and status["highest_price"]:
            lines.append(f"  Highest Price: ${status['highest_price']:.2f}")
        elif status["lowest_price"]:
            lines.append(f"  Lowest Price: ${status['lowest_price']:.2f}")

        return "\n".join(lines)

    def remove_trailing(self, symbol: str) -> bool:
        """Remove trailing stop for a symbol (e.g., when position is closed)"""
        if symbol in self.active_trails:
            del self.active_trails[symbol]
            logger.info("Trailing stop removed for %s", symbol)
            return True
        return False

    def should_apply_to_trade(self, trade_created_at: datetime) -> bool:
        """
        Check if trailing stop should apply to a specific trade.
        For backward compatibility with existing positions.

        Args:
            trade_created_at: Timestamp when the trade was created

        Returns:
            True if trailing stop should apply
        """
        if not self.feature_enabled:
            return False

        # Feature release date - trades before this use old behavior
        feature_release_date = datetime(2024, 11, 26, 0, 0, 0)
        return trade_created_at >= feature_release_date

    def get_all_active(self) -> List[Dict]:
        """Get all active trailing stops"""
        if not self.feature_enabled:
            return []

        return [
            {
                "symbol": symbol,
                "position_side": config["position_side"],
                "current_stop": config["current_stop"],
                "is_activated": config["is_activated"],
                "trail_type": config["trail_type"].value,
            }
            for symbol, config in self.active_trails.items()
        ]


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    trailing = AdvancedTrailingStop(mode="scalp")

    # Create trailing for LONG position
    trailing.create_trailing_stop(
        symbol="BTCUSDT",
        entry_price=100000,
        position_side="LONG",
        initial_stop=98500,
        atr_value=1500,
    )

    print("Initial status:", trailing.get_status("BTCUSDT"))

    # Simulate price movements
    print("\n--- Price rises to 101000 ---")
    result = trailing.update("BTCUSDT", 101000, 101100, 100900, 1500)
    print(f"Update result: {result}")
    print(trailing.get_status_for_prompt("BTCUSDT"))

    print("\n--- Price rises to 102000 ---")
    result = trailing.update("BTCUSDT", 102000, 102200, 101800, 1500)
    print(f"Update result: {result}")
    print(trailing.get_status_for_prompt("BTCUSDT"))

    print("\n--- Price falls to 100500 ---")
    result = trailing.update("BTCUSDT", 100500, 100600, 100400, 1500)
    print(f"Update result: {result}")
