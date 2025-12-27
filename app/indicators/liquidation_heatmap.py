"""
Liquidation Heatmap Analyzer - Estimates liquidation zones from market data.

Features:
- Tracks recent liquidations from Binance
- Estimates liquidation clusters based on OI and price
- Identifies high-risk zones for cascade liquidations
- Provides token-efficient summary for AI prompt

Note: This uses estimation since real heatmap data requires paid APIs (Coinglass).
We approximate using OI changes, funding rates, and price action.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)


@dataclass
class LiquidationZone:
    """A price zone with estimated liquidation volume."""
    price_low: float
    price_high: float
    side: str  # "LONG" or "SHORT"
    estimated_volume_usd: float
    intensity: str  # "LOW", "MEDIUM", "HIGH", "EXTREME"
    distance_pct: float  # Distance from current price


@dataclass
class LiquidationHeatmapResult:
    """Result of liquidation heatmap analysis."""
    symbol: str
    current_price: float
    long_zones: List[LiquidationZone] = field(default_factory=list)
    short_zones: List[LiquidationZone] = field(default_factory=list)
    nearest_long_zone: Optional[LiquidationZone] = None
    nearest_short_zone: Optional[LiquidationZone] = None
    cascade_risk: str = "LOW"  # LOW, MEDIUM, HIGH, EXTREME
    dominant_side: str = "BALANCED"  # LONG_HEAVY, SHORT_HEAVY, BALANCED
    feature_enabled: bool = True


class LiquidationHeatmapAnalyzer:
    """
    Analyzes and estimates liquidation zones.

    Uses a combination of:
    - Open Interest distribution
    - Recent price levels where positions were opened
    - Leverage assumptions (avg 10x-20x for retail)
    - Funding rate imbalance
    """

    # Common leverage levels for estimation
    LEVERAGE_LEVELS = [5, 10, 20, 25, 50, 100]

    # Price zone width as percentage
    ZONE_WIDTH_PCT = 0.5  # 0.5% per zone

    def __init__(
        self,
        lookback_hours: int = 24,
        num_zones: int = 10,
        feature_enabled: bool = True
    ):
        """
        Initialize LiquidationHeatmapAnalyzer.

        Args:
            lookback_hours: Hours to look back for position estimation
            num_zones: Number of zones above/below current price
            feature_enabled: Enable/disable the feature
        """
        self.lookback_hours = lookback_hours
        self.num_zones = num_zones
        self.feature_enabled = feature_enabled

        # Cache for OI snapshots
        self._oi_history: Dict[str, List[Tuple[datetime, float, float]]] = defaultdict(list)
        # (timestamp, price, oi_value)

        # Recent liquidations cache
        self._recent_liquidations: Dict[str, List[Dict]] = defaultdict(list)

        logger.info(
            "LiquidationHeatmapAnalyzer initialized | Lookback: %dh | Zones: %d | Enabled: %s",
            lookback_hours, num_zones, feature_enabled
        )

    def record_oi_snapshot(
        self,
        symbol: str,
        price: float,
        open_interest: float
    ) -> None:
        """
        Record an OI snapshot for tracking position changes.

        Args:
            symbol: Trading pair
            price: Current price
            open_interest: Current open interest value
        """
        if not self.feature_enabled:
            return

        now = datetime.now(timezone.utc)
        self._oi_history[symbol].append((now, price, open_interest))

        # Clean old data
        cutoff = now - timedelta(hours=self.lookback_hours)
        self._oi_history[symbol] = [
            (t, p, oi) for t, p, oi in self._oi_history[symbol]
            if t > cutoff
        ]

    def estimate_liquidation_zones(
        self,
        symbol: str,
        current_price: float,
        open_interest: float,
        funding_rate: float,
        long_short_ratio: float,
        recent_high: float,
        recent_low: float,
        avg_leverage: float = 15.0
    ) -> LiquidationHeatmapResult:
        """
        Estimate liquidation zones based on market data.

        Logic:
        - Positions opened at higher prices = Longs at risk below
        - Positions opened at lower prices = Shorts at risk above
        - Higher leverage = closer liquidation price
        - Funding imbalance indicates crowded side

        Args:
            symbol: Trading pair
            current_price: Current market price
            open_interest: Current OI in USD
            funding_rate: Current funding rate
            long_short_ratio: Long/Short account ratio
            recent_high: Recent swing high
            recent_low: Recent swing low
            avg_leverage: Assumed average leverage

        Returns:
            LiquidationHeatmapResult with estimated zones
        """
        if not self.feature_enabled:
            return LiquidationHeatmapResult(
                symbol=symbol,
                current_price=current_price,
                feature_enabled=False
            )

        long_zones = []
        short_zones = []

        # Estimate position distribution from L/S ratio
        total_oi = open_interest
        if long_short_ratio > 0:
            # L/S ratio > 1 means more longs
            long_pct = long_short_ratio / (1 + long_short_ratio)
        else:
            long_pct = 0.5

        short_pct = 1 - long_pct
        long_oi = total_oi * long_pct
        short_oi = total_oi * short_pct

        # Calculate liquidation levels for common leverages
        # Long liquidation: entry_price * (1 - 1/leverage)
        # Short liquidation: entry_price * (1 + 1/leverage)

        # Estimate entry prices from recent price range
        price_range = recent_high - recent_low
        mid_price = (recent_high + recent_low) / 2

        # Create zones below current price (Long liquidations)
        for i in range(1, self.num_zones + 1):
            zone_center = current_price * (1 - i * self.ZONE_WIDTH_PCT / 100)
            zone_low = zone_center * (1 - self.ZONE_WIDTH_PCT / 200)
            zone_high = zone_center * (1 + self.ZONE_WIDTH_PCT / 200)

            # Estimate how much OI would be liquidated at this level
            # More volume near recent swing low (where longs entered)
            distance_from_low = abs(zone_center - recent_low) / price_range if price_range > 0 else 1
            proximity_factor = max(0, 1 - distance_from_low)

            # Estimate liquidation volume
            # Assume positions entered at various levels with avg leverage
            liq_estimate = long_oi * proximity_factor * (1 / self.num_zones)

            # Intensity based on volume
            if liq_estimate > total_oi * 0.1:
                intensity = "EXTREME"
            elif liq_estimate > total_oi * 0.05:
                intensity = "HIGH"
            elif liq_estimate > total_oi * 0.02:
                intensity = "MEDIUM"
            else:
                intensity = "LOW"

            distance_pct = (current_price - zone_center) / current_price * 100

            if liq_estimate > total_oi * 0.01:  # Only include meaningful zones
                long_zones.append(LiquidationZone(
                    price_low=round(zone_low, 2),
                    price_high=round(zone_high, 2),
                    side="LONG",
                    estimated_volume_usd=round(liq_estimate, 0),
                    intensity=intensity,
                    distance_pct=round(distance_pct, 2)
                ))

        # Create zones above current price (Short liquidations)
        for i in range(1, self.num_zones + 1):
            zone_center = current_price * (1 + i * self.ZONE_WIDTH_PCT / 100)
            zone_low = zone_center * (1 - self.ZONE_WIDTH_PCT / 200)
            zone_high = zone_center * (1 + self.ZONE_WIDTH_PCT / 200)

            # More volume near recent swing high (where shorts entered)
            distance_from_high = abs(zone_center - recent_high) / price_range if price_range > 0 else 1
            proximity_factor = max(0, 1 - distance_from_high)

            liq_estimate = short_oi * proximity_factor * (1 / self.num_zones)

            if liq_estimate > total_oi * 0.1:
                intensity = "EXTREME"
            elif liq_estimate > total_oi * 0.05:
                intensity = "HIGH"
            elif liq_estimate > total_oi * 0.02:
                intensity = "MEDIUM"
            else:
                intensity = "LOW"

            distance_pct = (zone_center - current_price) / current_price * 100

            if liq_estimate > total_oi * 0.01:
                short_zones.append(LiquidationZone(
                    price_low=round(zone_low, 2),
                    price_high=round(zone_high, 2),
                    side="SHORT",
                    estimated_volume_usd=round(liq_estimate, 0),
                    intensity=intensity,
                    distance_pct=round(distance_pct, 2)
                ))

        # Find nearest zones
        nearest_long = min(long_zones, key=lambda z: z.distance_pct) if long_zones else None
        nearest_short = min(short_zones, key=lambda z: z.distance_pct) if short_zones else None

        # Determine cascade risk
        # High risk if: large OI imbalance + positions close to liquidation
        cascade_risk = self._calculate_cascade_risk(
            long_zones, short_zones, funding_rate, long_short_ratio
        )

        # Determine dominant side
        if long_short_ratio > 1.5:
            dominant_side = "LONG_HEAVY"
        elif long_short_ratio < 0.67:
            dominant_side = "SHORT_HEAVY"
        else:
            dominant_side = "BALANCED"

        return LiquidationHeatmapResult(
            symbol=symbol,
            current_price=current_price,
            long_zones=long_zones,
            short_zones=short_zones,
            nearest_long_zone=nearest_long,
            nearest_short_zone=nearest_short,
            cascade_risk=cascade_risk,
            dominant_side=dominant_side,
            feature_enabled=True
        )

    def _calculate_cascade_risk(
        self,
        long_zones: List[LiquidationZone],
        short_zones: List[LiquidationZone],
        funding_rate: float,
        long_short_ratio: float
    ) -> str:
        """Calculate cascade liquidation risk."""
        risk_score = 0

        # Check for high-intensity zones nearby
        for zone in long_zones[:3]:  # Top 3 nearest
            if zone.intensity == "EXTREME":
                risk_score += 3
            elif zone.intensity == "HIGH":
                risk_score += 2
            elif zone.intensity == "MEDIUM":
                risk_score += 1

        for zone in short_zones[:3]:
            if zone.intensity == "EXTREME":
                risk_score += 3
            elif zone.intensity == "HIGH":
                risk_score += 2
            elif zone.intensity == "MEDIUM":
                risk_score += 1

        # Funding rate imbalance adds risk
        if abs(funding_rate) > 0.0005:  # 0.05%
            risk_score += 2
        elif abs(funding_rate) > 0.0002:
            risk_score += 1

        # L/S ratio imbalance adds risk
        if long_short_ratio > 2.0 or long_short_ratio < 0.5:
            risk_score += 2
        elif long_short_ratio > 1.5 or long_short_ratio < 0.67:
            risk_score += 1

        if risk_score >= 8:
            return "EXTREME"
        elif risk_score >= 5:
            return "HIGH"
        elif risk_score >= 3:
            return "MEDIUM"
        return "LOW"

    def get_prompt_section(
        self,
        result: LiquidationHeatmapResult
    ) -> str:
        """
        Generate token-efficient prompt section.

        Target: ~50-80 tokens with critical information.

        Args:
            result: LiquidationHeatmapResult

        Returns:
            Formatted string for prompt
        """
        if not result.feature_enabled:
            return ""

        lines = ["LIQUIDATION HEATMAP:"]

        # Nearest Long liquidation zone
        if result.nearest_long_zone:
            z = result.nearest_long_zone
            vol_str = self._format_volume(z.estimated_volume_usd)
            emoji = "🔴" if z.intensity in ["HIGH", "EXTREME"] else "🟡"
            lines.append(
                f"  {emoji} LONG LIQ: ${z.price_low:,.0f}-${z.price_high:,.0f} "
                f"({z.intensity} ~{vol_str}) [{z.distance_pct:.1f}% below]"
            )

        # Nearest Short liquidation zone
        if result.nearest_short_zone:
            z = result.nearest_short_zone
            vol_str = self._format_volume(z.estimated_volume_usd)
            emoji = "🟢" if z.intensity in ["HIGH", "EXTREME"] else "🟡"
            lines.append(
                f"  {emoji} SHORT LIQ: ${z.price_low:,.0f}-${z.price_high:,.0f} "
                f"({z.intensity} ~{vol_str}) [{z.distance_pct:.1f}% above]"
            )

        # Cascade risk warning
        if result.cascade_risk in ["HIGH", "EXTREME"]:
            lines.append(f"  ⚠️ CASCADE RISK: {result.cascade_risk}")
            lines.append(f"  → Dominant: {result.dominant_side}")

        return "\n".join(lines)

    def _format_volume(self, volume: float) -> str:
        """Format volume in human-readable format."""
        if volume >= 1_000_000_000:
            return f"${volume/1_000_000_000:.1f}B"
        elif volume >= 1_000_000:
            return f"${volume/1_000_000:.1f}M"
        elif volume >= 1_000:
            return f"${volume/1_000:.0f}K"
        return f"${volume:.0f}"

    async def fetch_and_analyze(
        self,
        symbol: str,
        current_price: float,
        futures_data: Dict
    ) -> LiquidationHeatmapResult:
        """
        Convenience method to fetch data and analyze.

        Args:
            symbol: Trading pair
            current_price: Current market price
            futures_data: Futures data dict with OI, funding, L/S ratio

        Returns:
            LiquidationHeatmapResult
        """
        if not self.feature_enabled or not futures_data:
            return LiquidationHeatmapResult(
                symbol=symbol,
                current_price=current_price,
                feature_enabled=False
            )

        current = futures_data.get("current", {})
        oi = current.get("open_interest", 0)
        funding = current.get("funding_rate", 0)
        ls_ratio = current.get("long_short_ratio", 1.0)

        # Get recent high/low from historical data or estimate
        recent_high = current_price * 1.05  # Default 5% range
        recent_low = current_price * 0.95

        if "price_range" in futures_data:
            recent_high = futures_data["price_range"].get("high", recent_high)
            recent_low = futures_data["price_range"].get("low", recent_low)

        return self.estimate_liquidation_zones(
            symbol=symbol,
            current_price=current_price,
            open_interest=oi,
            funding_rate=funding,
            long_short_ratio=ls_ratio,
            recent_high=recent_high,
            recent_low=recent_low
        )


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    analyzer = LiquidationHeatmapAnalyzer(lookback_hours=24, num_zones=5)

    # Sample data
    result = analyzer.estimate_liquidation_zones(
        symbol="BTCUSDT",
        current_price=95000,
        open_interest=5_000_000_000,  # $5B OI
        funding_rate=0.0003,  # 0.03%
        long_short_ratio=1.8,  # More longs
        recent_high=98000,
        recent_low=92000
    )

    print(f"Symbol: {result.symbol}")
    print(f"Cascade Risk: {result.cascade_risk}")
    print(f"Dominant Side: {result.dominant_side}")
    print()

    print("Long Liquidation Zones:")
    for z in result.long_zones:
        print(f"  ${z.price_low:,.0f}-${z.price_high:,.0f}: {z.intensity} ({z.distance_pct:.1f}%)")

    print("\nShort Liquidation Zones:")
    for z in result.short_zones:
        print(f"  ${z.price_low:,.0f}-${z.price_high:,.0f}: {z.intensity} ({z.distance_pct:.1f}%)")

    print("\n" + analyzer.get_prompt_section(result))
