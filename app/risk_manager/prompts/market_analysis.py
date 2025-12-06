"""
Market Analysis Module

Market structure analysis, MTF alignment, and regime detection.
"""

from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging import get_logger


logger = get_logger(__name__)


class MarketAnalyzer:
    """
    Analyzes market structure and conditions.
    """

    def __init__(self, mtf_weights: Dict[str, float] = None):
        """
        Initialize the market analyzer.

        Args:
            mtf_weights: Timeframe weights for MTF alignment
        """
        self._mtf_weights = mtf_weights or {
            "1d": 4.0,
            "4h": 3.0,
            "1h": 1.5,
            "30m": 0.0,
            "15m": 0.0,
            "5m": 0.0,
            "1m": 0.0,
        }
        self._series_tail = 6  # For summarization

    def summarize_series(
        self, values: List[float], decimals: int = 2, name: str = ""
    ) -> str:
        """
        Create a token-efficient summary of a data series.

        Args:
            values: List of values
            decimals: Decimal places for formatting
            name: Series name for labeling

        Returns:
            Formatted summary string
        """
        if not values:
            return f"{name}: N/A"

        tail = values[-self._series_tail :]
        last = values[-1]
        vmin = min(values[-50:]) if len(values) >= 50 else min(values)
        vmax = max(values[-50:]) if len(values) >= 50 else max(values)
        mean = sum(values[-50:]) / (50 if len(values) >= 50 else len(values))

        if len(tail) >= 2:
            slope = (tail[-1] - tail[0]) / max(1, (len(tail) - 1))
        else:
            slope = 0.0

        if len(tail) >= 2:
            m = sum(tail) / len(tail)
            var = sum((x - m) ** 2 for x in tail) / len(tail)
            std = var**0.5
        else:
            std = 0.0

        return (
            f"{name}: last={last:.{decimals}f}, "
            f"min={vmin:.{decimals}f}, max={vmax:.{decimals}f}, "
            f"mean={mean:.{decimals}f}, slope={slope:.{decimals}f}/bar, std_tail={std:.{decimals}f}"
        )

    def detect_pivots(
        self,
        highs: List[float],
        lows: List[float],
        window: int = 2,
        min_move_pct: float = 0.25,
    ) -> List[Tuple[int, str, float]]:
        """
        Detect swing highs and lows.

        Args:
            highs: High prices list
            lows: Low prices list
            window: Lookback/lookforward window
            min_move_pct: Minimum move percentage

        Returns:
            List of (index, type, price) tuples
        """
        pivots = []

        if not highs or not lows or len(highs) < window * 2 + 1:
            return pivots

        for i in range(window, len(highs) - window):
            # Check for swing high
            is_swing_high = all(
                highs[i] > highs[i - j] and highs[i] > highs[i + j]
                for j in range(1, window + 1)
            )

            # Check for swing low
            is_swing_low = all(
                lows[i] < lows[i - j] and lows[i] < lows[i + j]
                for j in range(1, window + 1)
            )

            if is_swing_high:
                pivots.append((i, "HIGH", highs[i]))
            if is_swing_low:
                pivots.append((i, "LOW", lows[i]))

        return sorted(pivots, key=lambda x: x[0])

    def analyze_market_structure(
        self, hist_data: Dict[str, List[float]], atr_pct: float = 0.0
    ) -> str:
        """
        Analyze market structure from historical data.

        Args:
            hist_data: Historical data with 'high' and 'low' arrays
            atr_pct: ATR percentage for minimum move calculation

        Returns:
            Market structure description
        """
        highs = hist_data.get("high", [])
        lows = hist_data.get("low", [])

        if not highs or not lows:
            return "UNKNOWN"

        min_move = max(0.25, atr_pct * 0.3) if atr_pct > 0 else 0.25
        pivots = self.detect_pivots(highs, lows, window=2, min_move_pct=min_move)

        if len(pivots) < 2:
            return "INSUFFICIENT_DATA"

        # Analyze last 4 pivots
        recent = pivots[-4:] if len(pivots) >= 4 else pivots

        # Look for HH/HL (bullish) or LH/LL (bearish) patterns
        highs_list = [(i, p) for i, t, p in recent if t == "HIGH"]
        lows_list = [(i, p) for i, t, p in recent if t == "LOW"]

        if len(highs_list) >= 2 and len(lows_list) >= 2:
            hh = highs_list[-1][1] > highs_list[-2][1]
            hl = lows_list[-1][1] > lows_list[-2][1]
            lh = highs_list[-1][1] < highs_list[-2][1]
            ll = lows_list[-1][1] < lows_list[-2][1]

            if hh and hl:
                return "BULLISH (HH + HL)"
            elif lh and ll:
                return "BEARISH (LH + LL)"
            elif hh and ll:
                return "EXPANDING (HH + LL)"
            elif lh and hl:
                return "CONTRACTING (LH + HL)"

        return "UNCLEAR"

    def detect_data_conflicts(
        self,
        trend_direction: str,
        market_structure: str,
        rsi: float,
        ema_distance: float,
    ) -> List[str]:
        """
        Detect conflicting data points.

        Args:
            trend_direction: "BULLISH", "BEARISH", or "NEUTRAL"
            market_structure: Pivot-based structure
            rsi: Current RSI value
            ema_distance: Price distance from EMA20 (%)

        Returns:
            List of conflict warning strings
        """
        conflicts = []

        # 1. Trend vs Structure conflict
        if "BULLISH" in market_structure and trend_direction == "BEARISH":
            conflicts.append(
                "CONFLICT: Market Structure BULLISH (pivot) but EMA trend BEARISH"
            )
        elif "BEARISH" in market_structure and trend_direction == "BULLISH":
            conflicts.append(
                "CONFLICT: Market Structure BEARISH (pivot) but EMA trend BULLISH"
            )

        # 2. RSI vs Trend conflict
        if trend_direction == "BULLISH" and rsi < 35:
            conflicts.append(
                f"CONFLICT: Trend BULLISH but RSI {rsi:.1f} oversold - momentum weak"
            )
        elif trend_direction == "BEARISH" and rsi > 65:
            conflicts.append(
                f"CONFLICT: Trend BEARISH but RSI {rsi:.1f} overbought - momentum weak"
            )

        # 3. EMA distance vs RSI conflict (overextension)
        if ema_distance > 3.0 and rsi > 70:
            conflicts.append(
                f"CAUTION: Price +{ema_distance:.1f}% from EMA20 and RSI {rsi:.1f} - overextended"
            )
        elif ema_distance < -3.0 and rsi < 30:
            conflicts.append(
                f"CAUTION: Price {ema_distance:.1f}% from EMA20 and RSI {rsi:.1f} - overextended"
            )

        return conflicts

    def calculate_mtf_alignment(
        self, current_snapshots: Dict
    ) -> Dict[str, Any]:
        """
        Calculate trend for each timeframe and weighted alignment score.

        Args:
            current_snapshots: Dictionary of timeframe snapshots

        Returns:
            Dictionary with trends, alignment, counts, and weights
        """
        tf_trends = {}

        for tf in ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]:
            data = current_snapshots.get(tf, {})
            close = data.get("close", 0)
            ema20 = data.get("ema_20", 0)
            ema50 = data.get("ema_50", 0)

            if close > 0 and ema20 > 0 and ema50 > 0:
                if close > ema20 > ema50:
                    trend = "BULLISH"
                elif close < ema20 < ema50:
                    trend = "BEARISH"
                else:
                    trend = "NEUTRAL"
            else:
                trend = "N/A"

            tf_trends[tf] = trend

        # Weighted alignment score
        bullish_weight = 0.0
        bearish_weight = 0.0
        total_weight = 0.0
        bullish_count = 0
        bearish_count = 0
        total_valid = 0
        ignored_tfs = []

        for tf, trend in tf_trends.items():
            weight = self._mtf_weights.get(tf, 1.0)

            if weight <= 0:
                ignored_tfs.append(tf)
                continue

            if trend == "N/A":
                continue

            total_weight += weight
            total_valid += 1

            if trend == "BULLISH":
                bullish_weight += weight
                bullish_count += 1
            elif trend == "BEARISH":
                bearish_weight += weight
                bearish_count += 1

        # Determine alignment
        if total_weight == 0:
            alignment = "UNKNOWN"
        else:
            bullish_pct = bullish_weight / total_weight
            bearish_pct = bearish_weight / total_weight

            if bullish_pct >= 0.7:
                alignment = "STRONG_BULLISH"
            elif bullish_pct >= 0.5:
                alignment = "WEAK_BULLISH"
            elif bearish_pct >= 0.7:
                alignment = "STRONG_BEARISH"
            elif bearish_pct >= 0.5:
                alignment = "WEAK_BEARISH"
            else:
                alignment = "MIXED"

        return {
            "trends": tf_trends,
            "alignment": alignment,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "total_valid": total_valid,
            "bullish_weight": round(bullish_weight, 2),
            "bearish_weight": round(bearish_weight, 2),
            "total_weight": round(total_weight, 2),
            "ignored_tfs": ignored_tfs,
        }

    def detect_market_regime_type(
        self,
        historical_arrays: Dict,
        current_snapshots: Dict,
        atr_ratio: float = 1.0,
    ) -> str:
        """
        Detect TRENDING vs RANGING market for dynamic prompt instructions.

        Args:
            historical_arrays: Historical data arrays
            current_snapshots: Current timeframe snapshots
            atr_ratio: ATR ratio for volatility context

        Returns:
            "TREND_STRONG", "TREND_WEAK", "RANGE", or "CHOPPY"
        """
        mtf = self.calculate_mtf_alignment(current_snapshots)
        alignment = mtf.get("alignment", "MIXED")

        if alignment in ["STRONG_BULLISH", "STRONG_BEARISH"]:
            return "TREND_STRONG" if atr_ratio > 0.8 else "TREND_WEAK"
        elif alignment in ["WEAK_BULLISH", "WEAK_BEARISH"]:
            return "TREND_WEAK" if atr_ratio >= 1.0 else "RANGE"
        else:
            return "RANGE" if atr_ratio < 0.7 else "CHOPPY"

    def get_regime_instructions(self, regime_type: str) -> str:
        """
        Get compact trading instructions specific to market regime.

        Args:
            regime_type: Market regime type

        Returns:
            Regime hint string (~50 tokens)
        """
        instructions = {
            "TREND_STRONG": "REGIME: STRONG TREND - Trade with trend, enter on pullbacks. DO NOT TRADE AGAINST!",
            "TREND_WEAK": "REGIME: WEAK TREND - Be careful, reduce position size, use tight stops.",
            "RANGE": "REGIME: SIDEWAYS MARKET - Trade at support/resistance, HOLD in middle, wait for breakout.",
            "CHOPPY": "REGIME: CHOPPY - AVOID trading, wait for clear trend. Only enter with very high confidence.",
        }
        return instructions.get(
            regime_type,
            "REGIME: UNKNOWN - Proceed with caution.",
        )

    def check_liquidity_sweep(
        self, close: float, low: float, high: float,
        prev_swing_low: float, prev_swing_high: float,
        alignment: str,
    ) -> bool:
        """
        Check for liquidity sweep pattern.

        Args:
            close: Current close price
            low: Current low
            high: Current high
            prev_swing_low: Previous swing low
            prev_swing_high: Previous swing high
            alignment: MTF alignment

        Returns:
            True if liquidity sweep detected
        """
        # Bullish sweep: Price dips below swing low then closes above
        if low < prev_swing_low and close > prev_swing_low:
            if "BULLISH" in alignment:
                return True

        # Bearish sweep: Price spikes above swing high then closes below
        if high > prev_swing_high and close < prev_swing_high:
            if "BEARISH" in alignment:
                return True

        return False

    def determine_tf_trend(
        self, close: float, ema20: float, ema50: float
    ) -> str:
        """
        Determine trend for a single timeframe.

        Args:
            close: Close price
            ema20: EMA 20 value
            ema50: EMA 50 value

        Returns:
            "BULLISH", "BEARISH", or "NEUTRAL"
        """
        if close > ema20 > ema50:
            return "BULLISH"
        elif close < ema20 < ema50:
            return "BEARISH"
        return "NEUTRAL"
