"""
Candlestick Pattern Analyzer - Japanese candlestick pattern detection.

Features:
- Single candle patterns (Hammer, Doji, Shooting Star)
- Two candle patterns (Engulfing, Harami)
- Three candle patterns (Morning Star, Evening Star)
- Pattern strength/reliability scoring

Used for identifying potential reversals and continuations.
"""

from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class CandlestickPattern:
    """Detected candlestick pattern."""
    name: str
    signal: str  # BULLISH, BEARISH, NEUTRAL
    reliability: float  # 0.0 to 1.0
    description: str


class CandlestickAnalyzer:
    """
    Japanese candlestick pattern detector.

    Detects common reversal and continuation patterns.
    """

    def __init__(self, feature_enabled: bool = True):
        """
        Initialize CandlestickAnalyzer.

        Args:
            feature_enabled: Enable/disable the feature
        """
        self.feature_enabled = feature_enabled

        logger.info("CandlestickAnalyzer initialized | Enabled: %s", feature_enabled)

    def _get_candle_metrics(
        self,
        o: float,
        h: float,
        l: float,
        c: float
    ) -> Dict[str, float]:
        """
        Calculate candle metrics.

        Args:
            o: Open price
            h: High price
            l: Low price
            c: Close price

        Returns:
            Dict with body, upper_wick, lower_wick, range
        """
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        candle_range = h - l

        return {
            "body": body,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "range": candle_range,
            "is_bullish": c > o,
            "is_bearish": c < o,
            "body_pct": body / candle_range if candle_range > 0 else 0,
        }

    def detect_doji(
        self,
        o: float,
        h: float,
        l: float,
        c: float
    ) -> Optional[CandlestickPattern]:
        """
        Detect Doji pattern (indecision).

        Doji: Open and close are nearly equal.
        """
        metrics = self._get_candle_metrics(o, h, l, c)

        # Body is less than 10% of range
        if metrics["body_pct"] < 0.1 and metrics["range"] > 0:
            return CandlestickPattern(
                name="DOJI",
                signal="NEUTRAL",
                reliability=0.5,
                description="Indecision - buyers and sellers balanced"
            )
        return None

    def detect_hammer(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        prev_trend: str = "DOWN"
    ) -> Optional[CandlestickPattern]:
        """
        Detect Hammer pattern (bullish reversal).

        Hammer: Small body at top, long lower shadow, little/no upper shadow.
        Appears after downtrend.
        """
        metrics = self._get_candle_metrics(o, h, l, c)

        body = metrics["body"]
        lower_wick = metrics["lower_wick"]
        upper_wick = metrics["upper_wick"]

        # Lower wick at least 2x body, upper wick small
        if (lower_wick >= body * 2 and
            upper_wick <= body * 0.5 and
            metrics["range"] > 0):

            # More reliable after downtrend
            reliability = 0.75 if prev_trend == "DOWN" else 0.5

            return CandlestickPattern(
                name="HAMMER",
                signal="BULLISH",
                reliability=reliability,
                description="Potential bullish reversal - buyers rejected lower prices"
            )
        return None

    def detect_inverted_hammer(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        prev_trend: str = "DOWN"
    ) -> Optional[CandlestickPattern]:
        """
        Detect Inverted Hammer (bullish reversal).

        Inverted Hammer: Small body at bottom, long upper shadow.
        """
        metrics = self._get_candle_metrics(o, h, l, c)

        body = metrics["body"]
        lower_wick = metrics["lower_wick"]
        upper_wick = metrics["upper_wick"]

        if (upper_wick >= body * 2 and
            lower_wick <= body * 0.5 and
            metrics["range"] > 0):

            reliability = 0.65 if prev_trend == "DOWN" else 0.4

            return CandlestickPattern(
                name="INVERTED_HAMMER",
                signal="BULLISH",
                reliability=reliability,
                description="Potential bullish reversal - buying pressure appearing"
            )
        return None

    def detect_shooting_star(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        prev_trend: str = "UP"
    ) -> Optional[CandlestickPattern]:
        """
        Detect Shooting Star (bearish reversal).

        Shooting Star: Small body at bottom, long upper shadow.
        Appears after uptrend.
        """
        metrics = self._get_candle_metrics(o, h, l, c)

        body = metrics["body"]
        lower_wick = metrics["lower_wick"]
        upper_wick = metrics["upper_wick"]

        if (upper_wick >= body * 2 and
            lower_wick <= body * 0.5 and
            metrics["range"] > 0):

            reliability = 0.75 if prev_trend == "UP" else 0.5

            return CandlestickPattern(
                name="SHOOTING_STAR",
                signal="BEARISH",
                reliability=reliability,
                description="Potential bearish reversal - sellers rejected higher prices"
            )
        return None

    def detect_engulfing(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float]
    ) -> Optional[CandlestickPattern]:
        """
        Detect Engulfing pattern (2-candle reversal).

        Bullish Engulfing: Red candle followed by larger green candle.
        Bearish Engulfing: Green candle followed by larger red candle.
        """
        if len(opens) < 2:
            return None

        # Previous candle
        prev_o, prev_c = opens[-2], closes[-2]
        prev_body = abs(prev_c - prev_o)
        prev_bullish = prev_c > prev_o

        # Current candle
        curr_o, curr_c = opens[-1], closes[-1]
        curr_body = abs(curr_c - curr_o)
        curr_bullish = curr_c > curr_o

        # Engulfing: opposite direction, larger body
        if not prev_bullish and curr_bullish and curr_body > prev_body:
            # Bullish engulfing
            if curr_o <= prev_c and curr_c >= prev_o:
                return CandlestickPattern(
                    name="ENGULFING_BULLISH",
                    signal="BULLISH",
                    reliability=0.80,
                    description="Strong bullish reversal - buyers overwhelmed sellers"
                )

        elif prev_bullish and not curr_bullish and curr_body > prev_body:
            # Bearish engulfing
            if curr_o >= prev_c and curr_c <= prev_o:
                return CandlestickPattern(
                    name="ENGULFING_BEARISH",
                    signal="BEARISH",
                    reliability=0.80,
                    description="Strong bearish reversal - sellers overwhelmed buyers"
                )

        return None

    def detect_morning_star(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float]
    ) -> Optional[CandlestickPattern]:
        """
        Detect Morning Star (3-candle bullish reversal).

        1. Large red candle
        2. Small body (doji or spinning top) - gap down
        3. Large green candle closing above midpoint of first
        """
        if len(opens) < 3:
            return None

        # First candle: bearish with decent body
        c1_body = abs(closes[-3] - opens[-3])
        c1_bearish = closes[-3] < opens[-3]

        # Second candle: small body (star)
        c2_body = abs(closes[-2] - opens[-2])
        c2_range = highs[-2] - lows[-2]
        c2_is_star = c2_body < c2_range * 0.3 if c2_range > 0 else False

        # Third candle: bullish closing above first candle midpoint
        c3_bullish = closes[-1] > opens[-1]
        c1_midpoint = (opens[-3] + closes[-3]) / 2
        c3_above_mid = closes[-1] > c1_midpoint

        if c1_bearish and c2_is_star and c3_bullish and c3_above_mid:
            return CandlestickPattern(
                name="MORNING_STAR",
                signal="BULLISH",
                reliability=0.85,
                description="Strong bullish reversal pattern - trend change likely"
            )

        return None

    def detect_evening_star(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float]
    ) -> Optional[CandlestickPattern]:
        """
        Detect Evening Star (3-candle bearish reversal).

        1. Large green candle
        2. Small body (doji or spinning top) - gap up
        3. Large red candle closing below midpoint of first
        """
        if len(opens) < 3:
            return None

        # First candle: bullish with decent body
        c1_body = abs(closes[-3] - opens[-3])
        c1_bullish = closes[-3] > opens[-3]

        # Second candle: small body (star)
        c2_body = abs(closes[-2] - opens[-2])
        c2_range = highs[-2] - lows[-2]
        c2_is_star = c2_body < c2_range * 0.3 if c2_range > 0 else False

        # Third candle: bearish closing below first candle midpoint
        c3_bearish = closes[-1] < opens[-1]
        c1_midpoint = (opens[-3] + closes[-3]) / 2
        c3_below_mid = closes[-1] < c1_midpoint

        if c1_bullish and c2_is_star and c3_bearish and c3_below_mid:
            return CandlestickPattern(
                name="EVENING_STAR",
                signal="BEARISH",
                reliability=0.85,
                description="Strong bearish reversal pattern - trend change likely"
            )

        return None

    def detect_all_patterns(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        prev_trend: str = "NEUTRAL"
    ) -> List[CandlestickPattern]:
        """
        Detect all candlestick patterns.

        Args:
            opens: Open prices
            highs: High prices
            lows: Low prices
            closes: Close prices
            prev_trend: "UP", "DOWN", or "NEUTRAL"

        Returns:
            List of detected patterns
        """
        if not self.feature_enabled or len(opens) < 3:
            return []

        patterns = []

        # Current candle values
        o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]

        # Single candle patterns
        doji = self.detect_doji(o, h, l, c)
        if doji:
            patterns.append(doji)

        hammer = self.detect_hammer(o, h, l, c, prev_trend)
        if hammer:
            patterns.append(hammer)

        inv_hammer = self.detect_inverted_hammer(o, h, l, c, prev_trend)
        if inv_hammer:
            patterns.append(inv_hammer)

        shooting = self.detect_shooting_star(o, h, l, c, prev_trend)
        if shooting:
            patterns.append(shooting)

        # Two candle patterns
        engulfing = self.detect_engulfing(opens, highs, lows, closes)
        if engulfing:
            patterns.append(engulfing)

        # Three candle patterns
        morning = self.detect_morning_star(opens, highs, lows, closes)
        if morning:
            patterns.append(morning)

        evening = self.detect_evening_star(opens, highs, lows, closes)
        if evening:
            patterns.append(evening)

        # Sort by reliability (highest first)
        patterns.sort(key=lambda p: p.reliability, reverse=True)

        return patterns

    def get_prompt_section(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        timeframe: str = "1H"
    ) -> str:
        """
        Generate candlestick pattern section for prompt.

        Args:
            opens: Open prices
            highs: High prices
            lows: Low prices
            closes: Close prices
            timeframe: Timeframe label

        Returns:
            Formatted string for prompt
        """
        if not self.feature_enabled or len(opens) < 3:
            return ""

        # Detect trend from recent closes
        if len(closes) >= 10:
            recent_avg = sum(closes[-10:]) / 10
            older_avg = sum(closes[-20:-10]) / 10 if len(closes) >= 20 else recent_avg
            prev_trend = "UP" if recent_avg > older_avg else "DOWN"
        else:
            prev_trend = "NEUTRAL"

        patterns = self.detect_all_patterns(opens, highs, lows, closes, prev_trend)

        if not patterns:
            return ""

        lines = [f"CANDLESTICK PATTERNS ({timeframe}):"]

        for p in patterns[:3]:  # Top 3 patterns only
            reliability_pct = int(p.reliability * 100)
            signal_emoji = "🟢" if p.signal == "BULLISH" else "🔴" if p.signal == "BEARISH" else "⚪"
            lines.append(f"  {signal_emoji} {p.name}: {p.signal} ({reliability_pct}%)")
            lines.append(f"     {p.description}")

        return "\n".join(lines)


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    analyzer = CandlestickAnalyzer()

    # Sample data with hammer pattern
    opens = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91]
    highs = [101, 100, 99, 98, 97, 96, 95, 94, 93, 92]
    lows = [99, 98, 97, 96, 95, 94, 93, 92, 91, 88]  # Last has long lower wick
    closes = [99.5, 98.5, 97.5, 96.5, 95.5, 94.5, 93.5, 92.5, 91.5, 91]  # Close near high

    patterns = analyzer.detect_all_patterns(opens, highs, lows, closes, "DOWN")

    print("Detected Patterns:")
    for p in patterns:
        print(f"  - {p.name}: {p.signal} (reliability: {p.reliability})")
        print(f"    {p.description}")

    # Get prompt section
    print("\n" + analyzer.get_prompt_section(opens, highs, lows, closes, "1H"))
