"""
Indicator Interpretation Module

Context-aware interpretation of technical indicators.
"""

from typing import List, Optional, Tuple, Dict

from app.utils.logging import get_logger


logger = get_logger(__name__)


def calculate_cvd_context(
    cvd_values: List[float],
    *,
    price_start: float,
    price_end: float,
) -> Dict[str, str]:
    """
    Calculate high-level CVD/price context based on absolute values AND direction.

    Returns a dict with:
      - context: Pressure-based terminology (BUYING_PRESSURE_INCREASING, etc.)
      - cvd_direction: UP / DOWN / FLAT
      - price_direction: UP / DOWN / FLAT
      - cvd_bias: BUYING_PRESSURE / SELLING_PRESSURE / NEUTRAL
    """
    if not cvd_values or len(cvd_values) < 2:
        return {"context": "NEUTRAL", "cvd_direction": "FLAT", "price_direction": "FLAT", "cvd_bias": "NEUTRAL"}

    cvd_first = cvd_values[0]
    cvd_last = cvd_values[-1]
    cvd_improving = cvd_last > cvd_first  # CVD yukseliyor mu?

    cvd_direction = "FLAT"
    if cvd_improving:
        cvd_direction = "UP"
    elif cvd_last < cvd_first:
        cvd_direction = "DOWN"

    price_direction = "FLAT"
    if price_end > price_start:
        price_direction = "UP"
    elif price_end < price_start:
        price_direction = "DOWN"

    # Yeni terminoloji: Absolute CVD degerine gore baski yonu
    # Negatif CVD = satis baskisi hakim, Pozitif CVD = alis baskisi hakim
    if cvd_last < -100:  # Negatif CVD = satis baskisi
        cvd_bias = "SELLING_PRESSURE"
        if cvd_improving:
            context = "SELLING_PRESSURE_EASING"      # Satis azaliyor
        else:
            context = "SELLING_PRESSURE_INCREASING"  # Satis artiyor
    elif cvd_last > 100:  # Pozitif CVD = alis baskisi
        cvd_bias = "BUYING_PRESSURE"
        if cvd_improving:
            context = "BUYING_PRESSURE_INCREASING"   # Alis artiyor
        else:
            context = "BUYING_PRESSURE_EASING"       # Alis azaliyor
    else:  # CVD ~0 = notr
        cvd_bias = "NEUTRAL"
        context = "NEUTRAL"

    return {"context": context, "cvd_direction": cvd_direction, "price_direction": price_direction, "cvd_bias": cvd_bias}


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

        window = min(lookback, len(cvd_values), len(closes))
        cvd_window = cvd_values[-window:]
        close_window = closes[-window:]

        res = calculate_cvd_context(
            cvd_window,
            price_start=close_window[0],
            price_end=close_window[-1],
        )
        label = res["context"]
        cvd_bias = res.get("cvd_bias", "NEUTRAL")

        # Yeni pressure-based terminoloji
        if label == "BUYING_PRESSURE_INCREASING":
            return label, "Alis baskisi artiyor - bullish momentum"
        if label == "BUYING_PRESSURE_EASING":
            if current_position == "LONG":
                return label, "Alis baskisi azaliyor - LONG zayifliyor"
            return label, "Alis baskisi azaliyor - momentum kaybediyor"
        if label == "SELLING_PRESSURE_INCREASING":
            return label, "Satis baskisi artiyor - bearish momentum"
        if label == "SELLING_PRESSURE_EASING":
            if current_position == "SHORT":
                return label, "Satis baskisi azaliyor - SHORT zayifliyor"
            return label, "Satis baskisi azaliyor - dip olusabilir"

        return "NEUTRAL", "CVD notr bolge"

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
