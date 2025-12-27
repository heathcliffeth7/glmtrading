"""
Fibonacci Analyzer - Fibonacci retracement and extension levels.

Features:
- Auto swing high/low detection
- Fibonacci retracement levels (0.236, 0.382, 0.5, 0.618, 0.786)
- Extension levels (1.0, 1.272, 1.618)
- Nearest level detection
- S/R zone identification

Used for identifying key price levels and potential reversal zones.
"""

from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# Standard Fibonacci levels
FIB_LEVELS = {
    "0.0": 0.0,
    "0.236": 0.236,
    "0.382": 0.382,
    "0.5": 0.5,
    "0.618": 0.618,
    "0.786": 0.786,
    "1.0": 1.0,
}

# Extension levels
FIB_EXTENSIONS = {
    "1.272": 1.272,
    "1.618": 1.618,
    "2.0": 2.0,
}


class FibonacciAnalyzer:
    """
    Fibonacci retracement and extension level analyzer.

    Auto-detects swing highs/lows and calculates key Fibonacci levels.
    """

    def __init__(
        self,
        swing_lookback: int = 20,
        feature_enabled: bool = True
    ):
        """
        Initialize FibonacciAnalyzer.

        Args:
            swing_lookback: Bars to look back for swing detection
            feature_enabled: Enable/disable the feature
        """
        self.swing_lookback = swing_lookback
        self.feature_enabled = feature_enabled

        logger.info(
            "FibonacciAnalyzer initialized | Lookback: %d | Enabled: %s",
            swing_lookback, feature_enabled
        )

    def find_swing_high(
        self,
        highs: List[float],
        lookback: Optional[int] = None
    ) -> Tuple[float, int]:
        """
        Find the highest high in the lookback period.

        Args:
            highs: List of high prices
            lookback: Override default lookback

        Returns:
            Tuple[swing_high_price, index_from_end]
        """
        use_lookback = lookback or self.swing_lookback

        if len(highs) < use_lookback:
            use_lookback = len(highs)

        recent = highs[-use_lookback:]
        swing_high = max(recent)
        idx = recent.index(swing_high)

        return swing_high, use_lookback - idx - 1

    def find_swing_low(
        self,
        lows: List[float],
        lookback: Optional[int] = None
    ) -> Tuple[float, int]:
        """
        Find the lowest low in the lookback period.

        Args:
            lows: List of low prices
            lookback: Override default lookback

        Returns:
            Tuple[swing_low_price, index_from_end]
        """
        use_lookback = lookback or self.swing_lookback

        if len(lows) < use_lookback:
            use_lookback = len(lows)

        recent = lows[-use_lookback:]
        swing_low = min(recent)
        idx = recent.index(swing_low)

        return swing_low, use_lookback - idx - 1

    def detect_trend(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float]
    ) -> str:
        """
        Detect current trend for Fibonacci direction.

        Args:
            highs: High prices
            lows: Low prices
            closes: Close prices

        Returns:
            "UP" or "DOWN"
        """
        if len(closes) < 10:
            return "UP"

        # Compare recent close to average
        recent_close = closes[-1]
        avg_close = sum(closes[-20:]) / min(20, len(closes))

        swing_high, high_idx = self.find_swing_high(highs)
        swing_low, low_idx = self.find_swing_low(lows)

        # If swing low is more recent than swing high -> uptrend
        # If swing high is more recent than swing low -> downtrend
        if low_idx < high_idx:
            return "UP"
        elif high_idx < low_idx:
            return "DOWN"
        else:
            # Equal distance, use close comparison
            return "UP" if recent_close > avg_close else "DOWN"

    def calculate_retracement_levels(
        self,
        swing_high: float,
        swing_low: float,
        trend: str = "UP"
    ) -> Dict[str, float]:
        """
        Calculate Fibonacci retracement levels.

        For UPTREND: measures retracement from high back to low
        For DOWNTREND: measures retracement from low back to high

        Args:
            swing_high: Swing high price
            swing_low: Swing low price
            trend: "UP" or "DOWN"

        Returns:
            Dict with Fibonacci levels
        """
        diff = swing_high - swing_low

        if diff <= 0:
            return {}

        levels = {}

        if trend == "UP":
            # Uptrend: 0.0 at high, 1.0 at low
            # Retracement from high going down
            for name, ratio in FIB_LEVELS.items():
                levels[name] = swing_high - (diff * ratio)
        else:
            # Downtrend: 0.0 at low, 1.0 at high
            # Retracement from low going up
            for name, ratio in FIB_LEVELS.items():
                levels[name] = swing_low + (diff * ratio)

        # Round to 2 decimal places
        return {k: round(v, 2) for k, v in levels.items()}

    def calculate_extension_levels(
        self,
        swing_high: float,
        swing_low: float,
        trend: str = "UP"
    ) -> Dict[str, float]:
        """
        Calculate Fibonacci extension levels.

        Args:
            swing_high: Swing high price
            swing_low: Swing low price
            trend: "UP" or "DOWN"

        Returns:
            Dict with extension levels
        """
        diff = swing_high - swing_low

        if diff <= 0:
            return {}

        levels = {}

        if trend == "UP":
            # Extensions above swing high
            for name, ratio in FIB_EXTENSIONS.items():
                levels[name] = swing_low + (diff * ratio)
        else:
            # Extensions below swing low
            for name, ratio in FIB_EXTENSIONS.items():
                levels[name] = swing_high - (diff * ratio)

        return {k: round(v, 2) for k, v in levels.items()}

    def find_nearest_level(
        self,
        current_price: float,
        levels: Dict[str, float]
    ) -> Tuple[str, float, float]:
        """
        Find the nearest Fibonacci level to current price.

        Args:
            current_price: Current market price
            levels: Dict of Fibonacci levels

        Returns:
            Tuple[level_name, level_price, distance_pct]
        """
        if not levels:
            return "N/A", 0.0, 0.0

        nearest_name = ""
        nearest_price = 0.0
        min_distance = float('inf')

        for name, price in levels.items():
            distance = abs(current_price - price)
            if distance < min_distance:
                min_distance = distance
                nearest_name = name
                nearest_price = price

        # Calculate percentage distance
        distance_pct = (
            (current_price - nearest_price) / nearest_price * 100
            if nearest_price > 0 else 0
        )

        return nearest_name, nearest_price, round(distance_pct, 2)

    def get_fibonacci_analysis(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        current_price: float
    ) -> Dict:
        """
        Full Fibonacci analysis.

        Args:
            highs: High prices
            lows: Low prices
            closes: Close prices
            current_price: Current market price

        Returns:
            Dict with all Fibonacci data
        """
        if not self.feature_enabled:
            return {
                "feature_enabled": False,
                "retracement_levels": {},
                "extension_levels": {},
                "nearest_level": "N/A",
                "nearest_price": 0.0,
                "distance_pct": 0.0,
            }

        if len(highs) < 10 or len(lows) < 10:
            return {
                "feature_enabled": True,
                "retracement_levels": {},
                "extension_levels": {},
                "nearest_level": "INSUFFICIENT_DATA",
                "nearest_price": 0.0,
                "distance_pct": 0.0,
            }

        # Find swings
        swing_high, _ = self.find_swing_high(highs)
        swing_low, _ = self.find_swing_low(lows)

        # Detect trend
        trend = self.detect_trend(highs, lows, closes)

        # Calculate levels
        retracement = self.calculate_retracement_levels(swing_high, swing_low, trend)
        extensions = self.calculate_extension_levels(swing_high, swing_low, trend)

        # All levels combined
        all_levels = {**retracement, **extensions}

        # Find nearest
        nearest_name, nearest_price, distance_pct = self.find_nearest_level(
            current_price, all_levels
        )

        return {
            "feature_enabled": True,
            "trend": trend,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "retracement_levels": retracement,
            "extension_levels": extensions,
            "nearest_level": nearest_name,
            "nearest_price": nearest_price,
            "distance_pct": distance_pct,
            "current_price": current_price,
        }

    def get_prompt_section(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        current_price: float
    ) -> str:
        """
        Generate Fibonacci section for prompt.

        Args:
            highs: High prices
            lows: Low prices
            closes: Close prices
            current_price: Current market price

        Returns:
            Formatted string for prompt
        """
        if not self.feature_enabled:
            return ""

        analysis = self.get_fibonacci_analysis(highs, lows, closes, current_price)

        if not analysis.get("retracement_levels"):
            return ""

        ret = analysis["retracement_levels"]
        trend = analysis.get("trend", "UP")

        lines = [
            f"FIBONACCI LEVELS ({self.swing_lookback}-bar swing, {trend}trend):",
            f"  Swing: ${analysis['swing_low']:,.0f} - ${analysis['swing_high']:,.0f}",
        ]

        # Key retracement levels
        key_levels = ["0.382", "0.5", "0.618", "0.786"]
        level_str = " | ".join([
            f"{k}: ${ret.get(k, 0):,.0f}" for k in key_levels if k in ret
        ])
        lines.append(f"  {level_str}")

        # Nearest level info
        nearest = analysis["nearest_level"]
        nearest_price = analysis["nearest_price"]
        dist = analysis["distance_pct"]

        if nearest != "N/A":
            direction = "above" if dist > 0 else "below"
            lines.append(
                f"  → Current ${current_price:,.0f} is {abs(dist):.1f}% {direction} "
                f"Fib {nearest} (${nearest_price:,.0f})"
            )

            # Add context
            if nearest == "0.618" and abs(dist) < 1:
                lines.append("  ⚠️ Near GOLDEN RATIO (0.618) - key reversal zone")
            elif nearest == "0.5" and abs(dist) < 1:
                lines.append("  ℹ️ Near 50% retracement - psychological level")

        return "\n".join(lines)


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    analyzer = FibonacciAnalyzer(swing_lookback=20)

    # Sample data: uptrend with retracement
    highs = [100 + i * 0.5 for i in range(30)]
    lows = [99 + i * 0.5 for i in range(30)]
    closes = [99.5 + i * 0.5 for i in range(30)]

    # Last few bars show retracement
    highs[-5:] = [115, 114, 113, 112, 111]
    lows[-5:] = [114, 113, 112, 111, 110]
    closes[-5:] = [114.5, 113.5, 112.5, 111.5, 110.5]

    current_price = 110.5

    # Get analysis
    result = analyzer.get_fibonacci_analysis(highs, lows, closes, current_price)
    print(f"Trend: {result['trend']}")
    print(f"Swing High: {result['swing_high']}")
    print(f"Swing Low: {result['swing_low']}")
    print(f"Nearest Level: {result['nearest_level']} @ {result['nearest_price']}")
    print(f"Distance: {result['distance_pct']}%")

    # Get prompt section
    print("\n" + analyzer.get_prompt_section(highs, lows, closes, current_price))
