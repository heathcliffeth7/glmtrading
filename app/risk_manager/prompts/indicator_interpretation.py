"""
Indicator Interpretation Module

Context-aware interpretation of technical indicators.
"""

from typing import List, Optional, Tuple

from app.utils.logging import get_logger


logger = get_logger(__name__)


class IndicatorInterpreter:
    """
    Interprets technical indicators with market context awareness.
    """

    def __init__(self):
        """Initialize the interpreter."""
        pass

    def _calculate_slope(self, series: List[float], lookback: int = 4) -> float:
        """Calculate slope of a series."""
        if not series or len(series) < 2:
            return 0.0
        window = series[-lookback:] if len(series) >= lookback else series
        if len(window) < 2:
            return 0.0
        return (window[-1] - window[0]) / (len(window) - 1)

    def interpret_cvd_context(
        self,
        cvd_values: List[float],
        closes: List[float],
        lookback: int = 8,
        current_position: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Interpret CVD (Cumulative Volume Delta) in price context.
        Provides warnings for positions with adverse signals.

        Args:
            cvd_values: CVD values list
            closes: Close prices list
            lookback: Bars to analyze (default: 8 = ~32 hours @ 4H)
            current_position: Current position type ("LONG", "SHORT", None)

        Returns:
            (label, description) tuple

        Labels:
            PASSIVE_ABSORPTION: Price stable + CVD falling (hidden buying)
            PASSIVE_DISTRIBUTION: Price stable + CVD rising (hidden selling)
            TREND_CONFIRMED: CVD and price moving together
            BEARISH_DIVERGENCE: Price rising but CVD falling
            BULLISH_DIVERGENCE: Price falling but CVD rising
            NEUTRAL: No clear pattern
        """
        if (
            not cvd_values
            or not closes
            or len(cvd_values) < 2
            or len(closes) < 2
        ):
            return "NEUTRAL", "Insufficient data"

        cvd_slope = self._calculate_slope(cvd_values, lookback)
        price_slope = self._calculate_slope(closes, lookback)

        # Normalize to percentage
        price_pct = (price_slope / closes[-1]) * 100 if closes[-1] > 0 else 0

        cvd_window = cvd_values[-lookback:] if len(cvd_values) >= lookback else cvd_values
        cvd_range = max(cvd_window) - min(cvd_window) if cvd_window else abs(cvd_values[-1])
        cvd_pct = (cvd_slope / cvd_range) * 100 if cvd_range > 0 else 0

        flat_threshold = 0.1  # 0.1% is flat

        # PASSIVE_ABSORPTION: Price stable/rising + CVD falling
        if price_pct >= -flat_threshold and cvd_pct < -5.0:
            if current_position == "SHORT":
                return (
                    "PASSIVE_ABSORPTION",
                    "Hidden buying detected - SHORT position jump risk",
                )
            return (
                "PASSIVE_ABSORPTION",
                "Price stable, CVD falling - hidden buying (Bullish)",
            )

        # PASSIVE_DISTRIBUTION: Price stable/falling + CVD rising
        if price_pct <= flat_threshold and cvd_pct > 5.0:
            if current_position == "LONG":
                return (
                    "PASSIVE_DISTRIBUTION",
                    "Hidden selling detected - LONG position drop risk",
                )
            return (
                "PASSIVE_DISTRIBUTION",
                "Price stable, CVD rising - hidden selling (Bearish)",
            )

        # TREND_CONFIRMED: Same direction
        if (price_pct > flat_threshold and cvd_pct > 3.0) or (
            price_pct < -flat_threshold and cvd_pct < -3.0
        ):
            return "TREND_CONFIRMED", "CVD confirms price direction"

        # BEARISH_DIVERGENCE: Price rising but CVD falling
        if price_pct > flat_threshold and cvd_pct < -3.0:
            if current_position == "LONG":
                return (
                    "BEARISH_DIVERGENCE",
                    "Price rising but CVD falling - LONG position weakening risk",
                )
            return (
                "BEARISH_DIVERGENCE",
                "Price rising but CVD falling - weakening trend",
            )

        # BULLISH_DIVERGENCE: Price falling but CVD rising
        if price_pct < -flat_threshold and cvd_pct > 3.0:
            if current_position == "SHORT":
                return (
                    "BULLISH_DIVERGENCE",
                    "Price falling but CVD rising - SHORT position reversal risk",
                )
            return (
                "BULLISH_DIVERGENCE",
                "Price falling but CVD rising - potential reversal",
            )

        return "NEUTRAL", "No clear CVD pattern"

    def interpret_rsi_context(
        self, rsi: float, adx: float, price_slope: float = 0.0
    ) -> Tuple[str, str]:
        """
        Context-aware RSI interpretation using ADX.

        ADX > 30: Strong trend, RSI extremes = momentum continuation
        ADX < 20: Range market, RSI extremes = mean reversion

        Args:
            rsi: Current RSI value
            adx: Current ADX value
            price_slope: Price trend slope

        Returns:
            (label, description) tuple
        """
        # Strong trend (ADX > 30)
        if adx > 30:
            if rsi > 70:
                return (
                    "STRONG_MOMENTUM",
                    f"RSI {rsi:.0f} in strong trend (ADX {adx:.0f}) - momentum likely to continue",
                )
            elif rsi < 30:
                return (
                    "STRONG_MOMENTUM",
                    f"RSI {rsi:.0f} oversold in strong trend - aggressive reversal or continuation",
                )
            elif rsi > 50:
                return (
                    "BULLISH_MOMENTUM",
                    f"RSI {rsi:.0f} + ADX {adx:.0f} - solid uptrend",
                )
            else:
                return (
                    "BEARISH_MOMENTUM",
                    f"RSI {rsi:.0f} + ADX {adx:.0f} - solid downtrend",
                )

        # Ranging market (ADX < 20)
        elif adx < 20:
            if rsi > 70:
                return (
                    "OVERBOUGHT_SELL_ZONE",
                    f"RSI {rsi:.0f} overbought in range (ADX {adx:.0f}) - sell zone",
                )
            elif rsi < 30:
                return (
                    "OVERSOLD_BUY_ZONE",
                    f"RSI {rsi:.0f} oversold in range (ADX {adx:.0f}) - buy zone",
                )
            else:
                return (
                    "RANGE_NEUTRAL",
                    f"RSI {rsi:.0f} in range market - wait for extremes",
                )

        # Medium trend (ADX 20-30)
        else:
            if rsi > 65:
                return (
                    "CAUTION_OVERBOUGHT",
                    f"RSI {rsi:.0f} high, medium ADX {adx:.0f} - diminishing upside",
                )
            elif rsi < 35:
                return (
                    "CAUTION_OVERSOLD",
                    f"RSI {rsi:.0f} low, medium ADX {adx:.0f} - diminishing downside",
                )
            else:
                return "NEUTRAL", f"RSI {rsi:.0f} in neutral zone"

    def interpret_funding_oi_context(
        self,
        funding_rate: float,
        oi_current: float,
        oi_avg: float,
        price_slope: float,
    ) -> Tuple[str, str]:
        """
        Interpret Funding Rate + Open Interest combination.

        Args:
            funding_rate: Current funding rate
            oi_current: Current open interest
            oi_avg: Average open interest
            price_slope: Price trend slope

        Returns:
            (label, description) tuple

        Labels:
            AGGRESSIVE_SHORTING: OI rising + price falling
            AGGRESSIVE_LONGING: OI rising + price rising
            LONG_LIQUIDATION: OI falling + price falling
            SHORT_LIQUIDATION: OI falling + price rising
            CROWDED_LONG: High funding + rising OI
            CROWDED_SHORT: Negative funding + rising OI
            NEUTRAL: Normal conditions
        """
        if oi_avg <= 0:
            return "NEUTRAL", "No OI baseline"

        oi_change_pct = ((oi_current - oi_avg) / oi_avg) * 100
        oi_rising = oi_change_pct > 2.0
        oi_falling = oi_change_pct < -2.0

        price_up = price_slope > 0.001
        price_down = price_slope < -0.001

        funding_high_long = funding_rate > 0.0005  # 0.05%+
        funding_high_short = funding_rate < -0.0005

        # OI + Price combinations
        if oi_rising and price_down:
            return (
                "AGGRESSIVE_SHORTING",
                f"OI +{oi_change_pct:.1f}% + price falling - new shorts entering",
            )

        if oi_rising and price_up:
            return (
                "AGGRESSIVE_LONGING",
                f"OI +{oi_change_pct:.1f}% + price rising - new longs entering",
            )

        if oi_falling and price_down:
            return (
                "LONG_LIQUIDATION",
                f"OI {oi_change_pct:.1f}% + price falling - long liquidation",
            )

        if oi_falling and price_up:
            return (
                "SHORT_LIQUIDATION",
                f"OI {oi_change_pct:.1f}% + price rising - short squeeze",
            )

        # Crowded trade
        if funding_high_long and oi_rising:
            return (
                "CROWDED_LONG",
                f"High funding ({funding_rate*100:.3f}%) + rising OI - crowded long",
            )

        if funding_high_short and oi_rising:
            return (
                "CROWDED_SHORT",
                f"Negative funding ({funding_rate*100:.3f}%) + rising OI - crowded short",
            )

        return (
            "NEUTRAL",
            f"OI: {oi_change_pct:+.1f}%, Funding: {funding_rate*100:.4f}%",
        )

    def interpret_adx(self, adx: float) -> Tuple[str, str]:
        """
        Interpret ADX for trend strength.

        Args:
            adx: ADX value

        Returns:
            (label, description) tuple
        """
        if adx > 40:
            return "VERY_STRONG_TREND", f"ADX {adx:.0f} - very strong trend"
        elif adx > 30:
            return "STRONG_TREND", f"ADX {adx:.0f} - strong trend"
        elif adx > 25:
            return "TRENDING", f"ADX {adx:.0f} - trending market"
        elif adx > 20:
            return "WEAK_TREND", f"ADX {adx:.0f} - weak trend"
        else:
            return "RANGING", f"ADX {adx:.0f} - ranging market"
