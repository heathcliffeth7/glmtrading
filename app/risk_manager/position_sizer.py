"""
Dynamic Position Sizer - Akıllı pozisyon boyutlandırma sistemi.

Features:
- Kelly Criterion (half-Kelly for safety)
- Volatility-adjusted sizing
- Confidence-based scaling
- Consecutive loss reduction
- Drawdown multiplier integration

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from typing import Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
import math
import logging

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeResult:
    """Position sizing calculation result"""
    quantity: float
    margin_required: float
    leverage: int
    risk_amount: float
    risk_pct: float
    position_value: float
    method_used: str
    adjustments: List[str]
    feature_enabled: bool = True


class DynamicPositionSizer:
    """
    Dynamic position sizing with multiple methods.

    Factors considered:
    - Account equity
    - Volatility (ATR)
    - Signal confidence
    - Recent performance
    - Drawdown status
    - Correlation exposure

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Mode-specific default configurations
    MODE_CONFIGS = {
        "scalp": {
            "max_risk_per_trade_pct": 2.0,
            "max_margin_per_position": 3000,
            "max_leverage": 15,
            "kelly_fraction": 0.5,
            "default_method": "volatility_adjusted",
        },
        "swing": {
            "max_risk_per_trade_pct": 3.0,
            "max_margin_per_position": 5000,
            "max_leverage": 7,
            "kelly_fraction": 0.4,
            "default_method": "kelly",
        }
    }

    def __init__(
        self,
        mode: str = "scalp",
        max_risk_per_trade_pct: Optional[float] = None,
        max_margin_per_position: Optional[float] = None,
        max_leverage: Optional[int] = None,
        kelly_fraction: Optional[float] = None,
        feature_enabled: bool = True,
    ):
        """
        Initialize DynamicPositionSizer.

        Args:
            mode: Trading mode - "scalp" or "swing"
            max_risk_per_trade_pct: Override max risk per trade
            max_margin_per_position: Override max margin
            max_leverage: Override max leverage
            kelly_fraction: Override Kelly fraction (0.5 = half-Kelly)
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])

        self.max_risk_per_trade_pct = max_risk_per_trade_pct or config["max_risk_per_trade_pct"]
        self.max_margin_per_position = max_margin_per_position or config["max_margin_per_position"]
        self.max_leverage = max_leverage or config["max_leverage"]
        self.kelly_fraction = kelly_fraction or config["kelly_fraction"]
        self.default_method = config["default_method"]

        logger.info(
            "DynamicPositionSizer initialized | Mode: %s | Max Risk: %.1f%% | Max Margin: $%.0f | Max Lev: %dx | Enabled: %s",
            mode, self.max_risk_per_trade_pct, self.max_margin_per_position, self.max_leverage, feature_enabled
        )

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_pct: float,
        confidence: float,  # 0-100
        atr_pct: float,
        win_rate: float = 0.5,  # Historical win rate
        avg_win_loss_ratio: float = 2.0,  # Average win / average loss
        consecutive_losses: int = 0,
        drawdown_multiplier: float = 1.0,  # From DrawdownManager
        method: Optional[str] = None,  # "fixed", "kelly", "volatility_adjusted"
    ) -> PositionSizeResult:
        """
        Calculate optimal position size.

        Args:
            equity: Current account equity
            entry_price: Planned entry price
            stop_loss_pct: Stop loss distance as percentage
            confidence: Signal confidence (0-100)
            atr_pct: ATR as percentage of price
            win_rate: Historical win rate (0-1)
            avg_win_loss_ratio: Average win / average loss
            consecutive_losses: Current consecutive loss count
            drawdown_multiplier: Multiplier from drawdown manager (0-1)
            method: Sizing method to use (uses default if not specified)

        Returns:
            PositionSizeResult with all calculations
        """
        if not self.feature_enabled:
            # Return minimum safe defaults
            return PositionSizeResult(
                quantity=0.001,
                margin_required=100,
                leverage=5,
                risk_amount=50,
                risk_pct=0.5,
                position_value=500,
                method_used="disabled",
                adjustments=["Feature disabled - using defaults"],
                feature_enabled=False,
            )

        adjustments = []
        method = method or self.default_method

        # Base calculation
        if method == "kelly":
            base_risk_pct = self._kelly_criterion(win_rate, avg_win_loss_ratio)
            adjustments.append(f"Kelly base: {base_risk_pct:.2f}%")
        elif method == "volatility_adjusted":
            base_risk_pct = self._volatility_adjusted_risk(atr_pct)
            adjustments.append(f"Vol-adjusted base: {base_risk_pct:.2f}%")
        else:  # fixed
            base_risk_pct = self.max_risk_per_trade_pct
            adjustments.append(f"Fixed base: {base_risk_pct:.2f}%")

        # Cap at max risk
        base_risk_pct = min(base_risk_pct, self.max_risk_per_trade_pct)

        # Adjustment 1: Confidence scaling
        confidence_mult = self._confidence_multiplier(confidence)
        risk_pct = base_risk_pct * confidence_mult
        adjustments.append(f"Confidence ({confidence:.0f}%): x{confidence_mult:.2f}")

        # Adjustment 2: Consecutive losses
        loss_mult = self._loss_streak_multiplier(consecutive_losses)
        risk_pct *= loss_mult
        if loss_mult < 1:
            adjustments.append(f"Loss streak ({consecutive_losses}): x{loss_mult:.2f}")

        # Adjustment 3: Drawdown
        risk_pct *= drawdown_multiplier
        if drawdown_multiplier < 1:
            adjustments.append(f"Drawdown limit: x{drawdown_multiplier:.2f}")

        # Adjustment 4: Volatility scaling (inverse)
        vol_mult = self._volatility_multiplier(atr_pct)
        risk_pct *= vol_mult
        adjustments.append(f"Volatility ({atr_pct:.2f}%): x{vol_mult:.2f}")

        # Calculate risk amount
        risk_amount = equity * (risk_pct / 100)

        # Calculate position size from risk and stop loss
        if stop_loss_pct <= 0:
            stop_loss_pct = max(atr_pct * 1.5, 0.5)  # Default SL

        position_value = risk_amount / (stop_loss_pct / 100)

        # Calculate quantity
        quantity = position_value / entry_price

        # Calculate margin and leverage
        margin_required = position_value / self.max_leverage

        # Check margin cap
        if margin_required > self.max_margin_per_position:
            old_margin = margin_required
            margin_required = self.max_margin_per_position
            position_value = margin_required * self.max_leverage
            quantity = position_value / entry_price
            risk_amount = position_value * (stop_loss_pct / 100)
            risk_pct = (risk_amount / equity) * 100
            adjustments.append(f"Margin capped: ${old_margin:.0f} -> ${margin_required:.0f}")

        # Calculate actual leverage
        leverage = int(position_value / margin_required) if margin_required > 0 else 1
        leverage = min(leverage, self.max_leverage)

        return PositionSizeResult(
            quantity=round(quantity, 6),
            margin_required=round(margin_required, 2),
            leverage=leverage,
            risk_amount=round(risk_amount, 2),
            risk_pct=round(risk_pct, 2),
            position_value=round(position_value, 2),
            method_used=method,
            adjustments=adjustments,
            feature_enabled=True,
        )

    def _kelly_criterion(self, win_rate: float, win_loss_ratio: float) -> float:
        """
        Kelly Criterion: f* = (p x b - q) / b
        where:
        - p = probability of win
        - q = probability of loss (1 - p)
        - b = win/loss ratio
        """
        if win_rate <= 0 or win_rate >= 1:
            return self.max_risk_per_trade_pct * 0.5

        p = win_rate
        q = 1 - p
        b = win_loss_ratio

        kelly = (p * b - q) / b

        # Apply fraction for safety
        kelly *= self.kelly_fraction

        # Convert to percentage and cap
        kelly_pct = kelly * 100

        return max(0.5, min(kelly_pct, self.max_risk_per_trade_pct))

    def _volatility_adjusted_risk(self, atr_pct: float) -> float:
        """
        Adjust base risk based on volatility.
        Higher volatility = lower base risk.
        """
        # Baseline: 1% ATR = 2% risk
        # If ATR is higher, reduce risk proportionally
        baseline_atr = 1.0
        baseline_risk = 2.0

        if atr_pct <= 0:
            atr_pct = baseline_atr

        # Inverse relationship
        adjusted_risk = baseline_risk * (baseline_atr / atr_pct)

        # Bounds
        return max(0.5, min(adjusted_risk, self.max_risk_per_trade_pct))

    def _confidence_multiplier(self, confidence: float) -> float:
        """
        Scale position based on confidence.

        80%+ confidence: 1.0x
        70-80%: 0.8x
        60-70%: 0.6x
        <60%: 0.4x (should probably not trade)
        """
        if confidence >= 80:
            return 1.0
        elif confidence >= 70:
            return 0.8
        elif confidence >= 60:
            return 0.6
        else:
            return 0.4

    def _loss_streak_multiplier(self, consecutive_losses: int) -> float:
        """
        Reduce size after consecutive losses.

        0-1 losses: 1.0x
        2 losses: 0.75x
        3 losses: 0.5x
        4+ losses: 0.25x
        """
        if consecutive_losses <= 1:
            return 1.0
        elif consecutive_losses == 2:
            return 0.75
        elif consecutive_losses == 3:
            return 0.5
        else:
            return 0.25

    def _volatility_multiplier(self, atr_pct: float) -> float:
        """
        Additional volatility scaling.

        ATR < 1%: 1.2x (low vol, can size up slightly)
        ATR 1-2%: 1.0x (normal)
        ATR 2-3%: 0.8x
        ATR > 3%: 0.6x (high vol, reduce)
        """
        if atr_pct < 1.0:
            return 1.2
        elif atr_pct < 2.0:
            return 1.0
        elif atr_pct < 3.0:
            return 0.8
        else:
            return 0.6

    def get_prompt_section(self, result: PositionSizeResult) -> str:
        """Generate prompt section showing sizing calculation"""
        if not result.feature_enabled:
            return ""

        lines = [
            "",
            "=" * 60,
            "POSITION SIZING CALCULATION",
            "=" * 60,
            "",
            f"Method: {result.method_used}",
            f"Position Value: ${result.position_value:,.2f}",
            f"Quantity: {result.quantity:.6f}",
            f"Margin Required: ${result.margin_required:,.2f}",
            f"Leverage: {result.leverage}x",
            "",
            f"Risk Amount: ${result.risk_amount:.2f} ({result.risk_pct:.2f}% of equity)",
            "",
            "Adjustments Applied:",
        ]

        for adj in result.adjustments:
            lines.append(f"  - {adj}")

        return "\n".join(lines)

    def calculate_quick_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        side: str,
        confidence: float = 75,
    ) -> Dict:
        """
        Quick position size calculation with minimal inputs.

        Args:
            equity: Account equity
            entry_price: Entry price
            stop_loss_price: Stop loss price
            side: "LONG" or "SHORT"
            confidence: Signal confidence

        Returns:
            Dict with quantity, margin, leverage
        """
        if not self.feature_enabled:
            return {
                "quantity": 0.001,
                "margin": 100,
                "leverage": 5,
                "feature_enabled": False,
            }

        # Calculate SL percentage
        if side == "LONG":
            sl_pct = abs((entry_price - stop_loss_price) / entry_price * 100)
        else:
            sl_pct = abs((stop_loss_price - entry_price) / entry_price * 100)

        # Default ATR estimate (use SL as proxy)
        atr_pct = sl_pct * 0.7  # Rough estimate

        result = self.calculate_position_size(
            equity=equity,
            entry_price=entry_price,
            stop_loss_pct=sl_pct,
            confidence=confidence,
            atr_pct=atr_pct,
        )

        return {
            "quantity": result.quantity,
            "margin": result.margin_required,
            "leverage": result.leverage,
            "position_value": result.position_value,
            "risk_pct": result.risk_pct,
            "feature_enabled": True,
        }

    def get_summary(self) -> Dict:
        """Get summary of position sizer configuration"""
        return {
            "mode": self.mode,
            "feature_enabled": self.feature_enabled,
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "max_margin_per_position": self.max_margin_per_position,
            "max_leverage": self.max_leverage,
            "kelly_fraction": self.kelly_fraction,
            "default_method": self.default_method,
        }


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    sizer = DynamicPositionSizer(mode="scalp")

    # Calculate position size
    result = sizer.calculate_position_size(
        equity=10000,
        entry_price=100000,
        stop_loss_pct=1.5,
        confidence=80,
        atr_pct=1.2,
        win_rate=0.55,
        avg_win_loss_ratio=2.0,
        consecutive_losses=1,
        drawdown_multiplier=1.0,
    )

    print("Position Size Calculation:")
    print(f"  Quantity: {result.quantity}")
    print(f"  Margin: ${result.margin_required}")
    print(f"  Leverage: {result.leverage}x")
    print(f"  Position Value: ${result.position_value}")
    print(f"  Risk: ${result.risk_amount} ({result.risk_pct}%)")
    print(f"\nMethod: {result.method_used}")
    print("\nAdjustments:")
    for adj in result.adjustments:
        print(f"  - {adj}")

    print("\n" + sizer.get_prompt_section(result))

    # Test quick calculation
    print("\n--- Quick Calculation ---")
    quick = sizer.calculate_quick_size(
        equity=10000,
        entry_price=100000,
        stop_loss_price=98500,
        side="LONG",
        confidence=75,
    )
    print(f"Quick result: {quick}")
