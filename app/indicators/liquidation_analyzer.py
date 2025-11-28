"""
Liquidation Analyzer - Track and analyze liquidation levels.

Features:
- Estimate liquidation levels from Open Interest data
- Track liquidation clusters
- Provide warnings for nearby liquidation zones
- Calculate liquidation impact risk

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum
import logging
import math

logger = logging.getLogger(__name__)


class LiquidationRisk(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class LiquidationLevel:
    """A single liquidation level"""
    price: float
    estimated_volume_usd: float
    leverage_range: Tuple[int, int]  # (min_lev, max_lev)
    side: str  # "LONG" or "SHORT"
    distance_pct: float
    risk_score: float


@dataclass
class LiquidationAnalysisResult:
    """Result from liquidation analysis"""
    current_price: float
    nearest_long_liq: Optional[LiquidationLevel]
    nearest_short_liq: Optional[LiquidationLevel]
    risk_level: LiquidationRisk
    long_liquidation_zones: List[LiquidationLevel]
    short_liquidation_zones: List[LiquidationLevel]
    warnings: List[str]
    recommendations: List[str]
    feature_enabled: bool = True


class LiquidationAnalyzer:
    """
    Analyze liquidation levels for trading decisions.

    Estimates liquidation prices based on:
    - Common leverage levels (5x, 10x, 20x, 50x, 100x)
    - Open Interest distribution
    - Recent price action

    Liquidation formula: Liq_Price = Entry * (1 - 1/leverage) for longs
                         Liq_Price = Entry * (1 + 1/leverage) for shorts

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Common leverage levels on Binance Futures
    LEVERAGE_LEVELS = [5, 10, 20, 25, 50, 75, 100, 125]

    # Mode-specific configurations
    MODE_CONFIGS = {
        "scalp": {
            "warning_distance_pct": 3.0,  # Warn if liq within 3%
            "danger_distance_pct": 1.5,   # Danger if within 1.5%
            "critical_distance_pct": 0.5,  # Critical if within 0.5%
            "track_range_pct": 10.0,      # Track liquidations within 10%
        },
        "swing": {
            "warning_distance_pct": 5.0,
            "danger_distance_pct": 3.0,
            "critical_distance_pct": 1.0,
            "track_range_pct": 15.0,
        }
    }

    def __init__(
        self,
        mode: str = "scalp",
        feature_enabled: bool = True,
    ):
        """
        Initialize LiquidationAnalyzer.

        Args:
            mode: Trading mode - "scalp" or "swing"
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        self.config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])

        # Track estimated open interest at price levels
        self.oi_distribution: Dict[str, Dict[float, float]] = defaultdict(dict)

        # Recent price high/low for level estimation
        self.price_extremes: Dict[str, Dict] = {}

        logger.info(
            "LiquidationAnalyzer initialized | Mode: %s | Warning: %.1f%% | Enabled: %s",
            mode, self.config["warning_distance_pct"], feature_enabled
        )

    def estimate_liquidation_levels(
        self,
        symbol: str,
        current_price: float,
        recent_high: float,
        recent_low: float,
        open_interest_usd: float,
    ) -> List[LiquidationLevel]:
        """
        Estimate liquidation levels based on price action and OI.

        Args:
            symbol: Trading pair
            current_price: Current market price
            recent_high: Recent high price
            recent_low: Recent low price
            open_interest_usd: Current open interest in USD

        Returns:
            List of estimated liquidation levels
        """
        if not self.feature_enabled:
            return []

        levels = []

        # Store price extremes
        self.price_extremes[symbol] = {
            "high": recent_high,
            "low": recent_low,
            "timestamp": datetime.utcnow(),
        }

        # Estimate OI distribution (simplified model)
        # Assume OI is distributed around recent price action
        oi_per_level = open_interest_usd / (len(self.LEVERAGE_LEVELS) * 2)

        # Calculate liquidation prices for LONG positions
        # (Shorts got liquidated when price went up to recent_high)
        for lev in self.LEVERAGE_LEVELS:
            # Longs that entered at recent high
            liq_price = recent_high * (1 - 1/lev - 0.004)  # 0.4% fee buffer

            distance_pct = abs(liq_price - current_price) / current_price * 100

            if distance_pct <= self.config["track_range_pct"]:
                risk_score = self._calculate_risk_score(distance_pct)

                levels.append(LiquidationLevel(
                    price=round(liq_price, 2),
                    estimated_volume_usd=oi_per_level * self._leverage_weight(lev),
                    leverage_range=(lev, lev),
                    side="LONG",
                    distance_pct=round(distance_pct, 2),
                    risk_score=risk_score,
                ))

        # Calculate liquidation prices for SHORT positions
        # (Longs got liquidated when price went down to recent_low)
        for lev in self.LEVERAGE_LEVELS:
            # Shorts that entered at recent low
            liq_price = recent_low * (1 + 1/lev + 0.004)  # 0.4% fee buffer

            distance_pct = abs(liq_price - current_price) / current_price * 100

            if distance_pct <= self.config["track_range_pct"]:
                risk_score = self._calculate_risk_score(distance_pct)

                levels.append(LiquidationLevel(
                    price=round(liq_price, 2),
                    estimated_volume_usd=oi_per_level * self._leverage_weight(lev),
                    leverage_range=(lev, lev),
                    side="SHORT",
                    distance_pct=round(distance_pct, 2),
                    risk_score=risk_score,
                ))

        # Sort by distance
        levels.sort(key=lambda x: x.distance_pct)

        return levels

    def _leverage_weight(self, leverage: int) -> float:
        """
        Weight OI estimate by leverage popularity.
        Higher leverage = more aggressive traders, often retail.
        """
        weights = {
            5: 0.5,
            10: 1.0,
            20: 1.5,
            25: 1.2,
            50: 0.8,
            75: 0.5,
            100: 0.3,
            125: 0.2,
        }
        return weights.get(leverage, 0.5)

    def _calculate_risk_score(self, distance_pct: float) -> float:
        """Calculate risk score based on distance"""
        if distance_pct <= self.config["critical_distance_pct"]:
            return 1.0  # Maximum risk
        elif distance_pct <= self.config["danger_distance_pct"]:
            return 0.8
        elif distance_pct <= self.config["warning_distance_pct"]:
            return 0.5
        else:
            return 0.2

    def analyze(
        self,
        symbol: str,
        current_price: float,
        recent_high: float,
        recent_low: float,
        open_interest_usd: float,
        position_side: Optional[str] = None,
    ) -> LiquidationAnalysisResult:
        """
        Analyze liquidation levels and provide risk assessment.

        Args:
            symbol: Trading pair
            current_price: Current price
            recent_high: Recent high price
            recent_low: Recent low price
            open_interest_usd: Open interest in USD
            position_side: Optional position side to focus analysis

        Returns:
            LiquidationAnalysisResult with full analysis
        """
        if not self.feature_enabled:
            return LiquidationAnalysisResult(
                current_price=current_price,
                nearest_long_liq=None,
                nearest_short_liq=None,
                risk_level=LiquidationRisk.LOW,
                long_liquidation_zones=[],
                short_liquidation_zones=[],
                warnings=["Liquidation analyzer disabled"],
                recommendations=[],
                feature_enabled=False,
            )

        levels = self.estimate_liquidation_levels(
            symbol, current_price, recent_high, recent_low, open_interest_usd
        )

        # Separate by side
        long_levels = [l for l in levels if l.side == "LONG"]
        short_levels = [l for l in levels if l.side == "SHORT"]

        # Find nearest levels
        nearest_long = long_levels[0] if long_levels else None
        nearest_short = short_levels[0] if short_levels else None

        # Determine risk level
        risk_level = self._assess_risk_level(nearest_long, nearest_short, position_side)

        # Generate warnings and recommendations
        warnings, recommendations = self._generate_insights(
            current_price, nearest_long, nearest_short, position_side
        )

        return LiquidationAnalysisResult(
            current_price=current_price,
            nearest_long_liq=nearest_long,
            nearest_short_liq=nearest_short,
            risk_level=risk_level,
            long_liquidation_zones=long_levels[:5],  # Top 5
            short_liquidation_zones=short_levels[:5],
            warnings=warnings,
            recommendations=recommendations,
            feature_enabled=True,
        )

    def _assess_risk_level(
        self,
        nearest_long: Optional[LiquidationLevel],
        nearest_short: Optional[LiquidationLevel],
        position_side: Optional[str],
    ) -> LiquidationRisk:
        """Assess overall liquidation risk"""
        # If we have a position, focus on relevant side
        if position_side == "LONG" and nearest_long:
            if nearest_long.distance_pct <= self.config["critical_distance_pct"]:
                return LiquidationRisk.CRITICAL
            elif nearest_long.distance_pct <= self.config["danger_distance_pct"]:
                return LiquidationRisk.HIGH
            elif nearest_long.distance_pct <= self.config["warning_distance_pct"]:
                return LiquidationRisk.MEDIUM

        if position_side == "SHORT" and nearest_short:
            if nearest_short.distance_pct <= self.config["critical_distance_pct"]:
                return LiquidationRisk.CRITICAL
            elif nearest_short.distance_pct <= self.config["danger_distance_pct"]:
                return LiquidationRisk.HIGH
            elif nearest_short.distance_pct <= self.config["warning_distance_pct"]:
                return LiquidationRisk.MEDIUM

        # Check both sides for general risk
        min_distance = float('inf')

        if nearest_long:
            min_distance = min(min_distance, nearest_long.distance_pct)
        if nearest_short:
            min_distance = min(min_distance, nearest_short.distance_pct)

        if min_distance <= self.config["critical_distance_pct"]:
            return LiquidationRisk.HIGH  # High (not critical) if no position
        elif min_distance <= self.config["danger_distance_pct"]:
            return LiquidationRisk.MEDIUM
        else:
            return LiquidationRisk.LOW

    def _generate_insights(
        self,
        current_price: float,
        nearest_long: Optional[LiquidationLevel],
        nearest_short: Optional[LiquidationLevel],
        position_side: Optional[str],
    ) -> Tuple[List[str], List[str]]:
        """Generate warnings and recommendations"""
        warnings = []
        recommendations = []

        # Long liquidation warnings
        if nearest_long and nearest_long.distance_pct <= self.config["warning_distance_pct"]:
            warnings.append(
                f"Long liquidations at ${nearest_long.price:.0f} ({nearest_long.distance_pct:.1f}% away)"
            )

            if nearest_long.distance_pct <= self.config["danger_distance_pct"]:
                warnings.append("Long liquidation cascade risk - expect volatility on drop")

                if position_side == "SHORT":
                    recommendations.append("Consider taking profits before liquidation zone")
                elif position_side == "LONG":
                    recommendations.append("DANGER: Your position near long liquidation zone")

        # Short liquidation warnings
        if nearest_short and nearest_short.distance_pct <= self.config["warning_distance_pct"]:
            warnings.append(
                f"Short liquidations at ${nearest_short.price:.0f} ({nearest_short.distance_pct:.1f}% away)"
            )

            if nearest_short.distance_pct <= self.config["danger_distance_pct"]:
                warnings.append("Short squeeze risk - expect volatility on pump")

                if position_side == "LONG":
                    recommendations.append("Short squeeze may push price higher")
                elif position_side == "SHORT":
                    recommendations.append("DANGER: Your position near short liquidation zone")

        # General recommendations
        if not warnings:
            recommendations.append("No nearby liquidation clusters - normal trading conditions")

        if nearest_long and nearest_short:
            gap = abs(nearest_long.price - nearest_short.price)
            gap_pct = gap / current_price * 100

            if gap_pct < 5:
                warnings.append(f"Liquidations clustered on both sides ({gap_pct:.1f}% gap)")
                recommendations.append("High volatility expected - consider reducing position size")

        return warnings, recommendations

    def get_magnet_effect(
        self,
        current_price: float,
        nearest_long: Optional[LiquidationLevel],
        nearest_short: Optional[LiquidationLevel],
    ) -> Dict:
        """
        Calculate the "magnet effect" of liquidation levels.

        Price tends to gravitate toward large liquidation clusters
        as market makers and whales hunt for liquidity.

        Returns:
            Dict with magnet effect analysis
        """
        if not self.feature_enabled:
            return {"feature_enabled": False}

        if not nearest_long and not nearest_short:
            return {
                "magnet_direction": "neutral",
                "magnet_strength": 0,
                "target_price": None,
                "feature_enabled": True,
            }

        # Calculate which side has stronger magnet effect
        long_pull = 0
        short_pull = 0

        if nearest_long:
            # Closer + larger volume = stronger pull
            long_pull = (nearest_long.estimated_volume_usd / 1e6) / (nearest_long.distance_pct + 1)

        if nearest_short:
            short_pull = (nearest_short.estimated_volume_usd / 1e6) / (nearest_short.distance_pct + 1)

        if long_pull > short_pull:
            return {
                "magnet_direction": "down",
                "magnet_strength": long_pull / (long_pull + short_pull) if (long_pull + short_pull) > 0 else 0,
                "target_price": nearest_long.price if nearest_long else None,
                "reason": "Long liquidation zone pulling price down",
                "feature_enabled": True,
            }
        elif short_pull > long_pull:
            return {
                "magnet_direction": "up",
                "magnet_strength": short_pull / (long_pull + short_pull) if (long_pull + short_pull) > 0 else 0,
                "target_price": nearest_short.price if nearest_short else None,
                "reason": "Short liquidation zone pulling price up",
                "feature_enabled": True,
            }
        else:
            return {
                "magnet_direction": "neutral",
                "magnet_strength": 0.5,
                "target_price": None,
                "reason": "Balanced liquidation zones",
                "feature_enabled": True,
            }

    def get_prompt_section(
        self,
        symbol: str,
        current_price: float,
        recent_high: float,
        recent_low: float,
        open_interest_usd: float,
        position_side: Optional[str] = None,
    ) -> str:
        """Generate prompt section for GLM"""
        if not self.feature_enabled:
            return ""

        result = self.analyze(
            symbol, current_price, recent_high, recent_low,
            open_interest_usd, position_side
        )

        if not result.feature_enabled:
            return ""

        lines = [
            "",
            "=" * 60,
            "LIQUIDATION ANALYSIS",
            "=" * 60,
            "",
            f"Current Price: ${current_price:,.0f}",
            f"Risk Level: {result.risk_level.value.upper()}",
        ]

        if result.nearest_long_liq:
            liq = result.nearest_long_liq
            lines.append(f"Nearest Long Liq: ${liq.price:,.0f} ({liq.distance_pct:.1f}% below)")

        if result.nearest_short_liq:
            liq = result.nearest_short_liq
            lines.append(f"Nearest Short Liq: ${liq.price:,.0f} ({liq.distance_pct:.1f}% above)")

        # Magnet effect
        magnet = self.get_magnet_effect(
            current_price,
            result.nearest_long_liq,
            result.nearest_short_liq
        )
        if magnet.get("magnet_direction") != "neutral":
            lines.append("")
            lines.append(f"Magnet Effect: {magnet['magnet_direction'].upper()} (strength: {magnet['magnet_strength']:.2f})")
            if magnet.get("target_price"):
                lines.append(f"Target Zone: ${magnet['target_price']:,.0f}")

        if result.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in result.warnings:
                lines.append(f"  - {w}")

        if result.recommendations:
            lines.append("")
            lines.append("Recommendations:")
            for r in result.recommendations:
                lines.append(f"  - {r}")

        return "\n".join(lines)

    def get_trade_filter(
        self,
        symbol: str,
        side: str,
        current_price: float,
        recent_high: float,
        recent_low: float,
        open_interest_usd: float,
    ) -> Dict:
        """
        Get trade filter decision based on liquidation analysis.

        Args:
            symbol: Trading pair
            side: Proposed trade side
            current_price: Current price
            recent_high: Recent high
            recent_low: Recent low
            open_interest_usd: Open interest

        Returns:
            Dict with filter decision
        """
        if not self.feature_enabled:
            return {
                "allow_trade": True,
                "risk_level": "low",
                "reason": "Liquidation analyzer disabled",
                "feature_enabled": False,
            }

        result = self.analyze(
            symbol, current_price, recent_high, recent_low,
            open_interest_usd, side
        )

        # Don't trade in critical liquidation risk
        allow_trade = result.risk_level != LiquidationRisk.CRITICAL

        # Size adjustment based on risk
        size_multiplier = 1.0
        if result.risk_level == LiquidationRisk.HIGH:
            size_multiplier = 0.5
        elif result.risk_level == LiquidationRisk.MEDIUM:
            size_multiplier = 0.75

        return {
            "allow_trade": allow_trade,
            "risk_level": result.risk_level.value,
            "size_multiplier": size_multiplier,
            "warnings": result.warnings,
            "recommendations": result.recommendations,
            "feature_enabled": True,
        }

    def get_summary(self) -> Dict:
        """Get summary of liquidation analyzer state"""
        return {
            "mode": self.mode,
            "feature_enabled": self.feature_enabled,
            "warning_distance_pct": self.config["warning_distance_pct"],
            "danger_distance_pct": self.config["danger_distance_pct"],
            "critical_distance_pct": self.config["critical_distance_pct"],
            "track_range_pct": self.config["track_range_pct"],
        }


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    analyzer = LiquidationAnalyzer(mode="scalp")

    # Analyze BTC liquidations
    result = analyzer.analyze(
        symbol="BTCUSDT",
        current_price=95000,
        recent_high=100000,
        recent_low=90000,
        open_interest_usd=5_000_000_000,  # $5B OI
        position_side="LONG",
    )

    print("Liquidation Analysis:")
    print(f"  Risk Level: {result.risk_level.value}")
    print(f"  Current Price: ${result.current_price:,.0f}")

    if result.nearest_long_liq:
        print(f"  Nearest Long Liq: ${result.nearest_long_liq.price:,.0f} ({result.nearest_long_liq.distance_pct:.1f}%)")

    if result.nearest_short_liq:
        print(f"  Nearest Short Liq: ${result.nearest_short_liq.price:,.0f} ({result.nearest_short_liq.distance_pct:.1f}%)")

    print("\nWarnings:")
    for w in result.warnings:
        print(f"  - {w}")

    print("\nRecommendations:")
    for r in result.recommendations:
        print(f"  - {r}")

    # Magnet effect
    print("\n--- Magnet Effect ---")
    magnet = analyzer.get_magnet_effect(
        result.current_price,
        result.nearest_long_liq,
        result.nearest_short_liq
    )
    print(f"  Direction: {magnet['magnet_direction']}")
    print(f"  Strength: {magnet['magnet_strength']:.2f}")

    print("\n--- Prompt Section ---")
    print(analyzer.get_prompt_section(
        "BTCUSDT", 95000, 100000, 90000, 5_000_000_000, "LONG"
    ))
