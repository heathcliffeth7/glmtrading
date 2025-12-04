"""
Funding Rate Analyzer - Analyze funding rates for trading signals.

Features:
- Extreme funding detection
- Contrarian signal generation
- Squeeze risk alerts
- Historical percentile calculation

Key thresholds (per 8h):
- Extreme positive: > 0.1% (>100% APR)
- High positive: > 0.05%
- Neutral: -0.01% to 0.03%
- High negative: < -0.03%
- Extreme negative: < -0.05%
"""

from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class FundingSignal(Enum):
    NEUTRAL = "neutral"
    CONTRARIAN_LONG = "contrarian_long"  # Extreme negative funding
    CONTRARIAN_SHORT = "contrarian_short"  # Extreme positive funding
    SQUEEZE_RISK_LONG = "squeeze_risk_long"  # Shorts may get squeezed
    SQUEEZE_RISK_SHORT = "squeeze_risk_short"  # Longs may get liquidated


@dataclass
class FundingAnalysis:
    """Funding rate analysis result"""
    current_rate: float
    rate_8h_avg: float
    rate_24h_avg: float
    percentile: float  # 0-100, where 50 is median
    signal: FundingSignal
    confidence_adjustment: int
    warning: Optional[str]
    opportunity: Optional[str]


class FundingRateAnalyzer:
    """
    Analyze funding rates for trading signals.

    Key thresholds (per 8h):
    - Extreme positive: > 0.1% (>100% APR)
    - High positive: > 0.05%
    - Neutral: -0.01% to 0.03%
    - High negative: < -0.03%
    - Extreme negative: < -0.05%
    """

    def __init__(self, mode: str = "scalp", feature_enabled: bool = True):
        """
        Initialize FundingRateAnalyzer.

        Args:
            mode: Trading mode - "scalp" or "swing"
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled

        # Historical funding rates for percentile calculation
        self.funding_history: List[float] = []
        self.history_max_size = 1000  # ~1 month of 8h rates

        # Thresholds (as decimals, e.g., 0.001 = 0.1%)
        self.extreme_positive = 0.001   # 0.1% per 8h
        self.high_positive = 0.0005     # 0.05%
        self.neutral_low = -0.0001      # -0.01%
        self.neutral_high = 0.0003      # 0.03%
        self.high_negative = -0.0003    # -0.03%
        self.extreme_negative = -0.0005 # -0.05%

        logger.info("FundingRateAnalyzer initialized | Mode: %s | Enabled: %s", mode, feature_enabled)

    def add_funding_rate(self, rate: float):
        """Add a funding rate to history"""
        self.funding_history.append(rate)
        if len(self.funding_history) > self.history_max_size:
            self.funding_history.pop(0)

    def analyze(
        self,
        current_rate: float,
        rate_8h_avg: Optional[float] = None,
        rate_24h_avg: Optional[float] = None,
        price_trend: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
    ) -> FundingAnalysis:
        """
        Analyze funding rate for trading signals.

        Args:
            current_rate: Current funding rate (e.g., 0.0001 = 0.01%)
            rate_8h_avg: 8-hour average rate
            rate_24h_avg: 24-hour average rate
            price_trend: Current price trend direction

        Returns:
            FundingAnalysis with signal and adjustments
        """
        if not self.feature_enabled:
            return FundingAnalysis(
                current_rate=current_rate,
                rate_8h_avg=rate_8h_avg or current_rate,
                rate_24h_avg=rate_24h_avg or current_rate,
                percentile=50.0,
                signal=FundingSignal.NEUTRAL,
                confidence_adjustment=0,
                warning=None,
                opportunity=None,
            )

        # Calculate percentile if we have history
        if self.funding_history:
            sorted_rates = sorted(self.funding_history)
            percentile = sum(1 for r in sorted_rates if r <= current_rate) / len(sorted_rates) * 100
        else:
            percentile = 50.0

        # Determine signal
        signal = FundingSignal.NEUTRAL
        confidence_adj = 0
        warning = None
        opportunity = None

        # Extreme positive funding
        if current_rate >= self.extreme_positive:
            signal = FundingSignal.CONTRARIAN_SHORT
            confidence_adj = -15 if price_trend == "BULLISH" else 10
            warning = f"Extreme positive funding ({current_rate*100:.3f}%) - Longs overcrowded"
            opportunity = "Potential SHORT opportunity on pullback"

        # High positive funding
        elif current_rate >= self.high_positive:
            if price_trend == "BULLISH":
                signal = FundingSignal.SQUEEZE_RISK_SHORT
                confidence_adj = -10
                warning = "High funding + bullish trend - be cautious with new LONGs"
            else:
                signal = FundingSignal.CONTRARIAN_SHORT
                confidence_adj = 5
                opportunity = "Funding suggests bullish sentiment but price bearish - divergence"

        # Extreme negative funding
        elif current_rate <= self.extreme_negative:
            signal = FundingSignal.CONTRARIAN_LONG
            confidence_adj = -15 if price_trend == "BEARISH" else 10
            warning = f"Extreme negative funding ({current_rate*100:.3f}%) - Shorts overcrowded"
            opportunity = "Potential LONG opportunity on bounce"

        # High negative funding
        elif current_rate <= self.high_negative:
            if price_trend == "BEARISH":
                signal = FundingSignal.SQUEEZE_RISK_LONG
                confidence_adj = -10
                warning = "Negative funding + bearish trend - short squeeze possible"
            else:
                signal = FundingSignal.CONTRARIAN_LONG
                confidence_adj = 5
                opportunity = "Funding suggests bearish sentiment but price bullish - accumulation?"

        # Neutral range
        else:
            signal = FundingSignal.NEUTRAL
            # No significant adjustment

        # Additional checks based on averages
        if rate_8h_avg and rate_24h_avg:
            # Funding trend
            if current_rate > rate_8h_avg > rate_24h_avg:
                # Rising funding
                if current_rate > 0:
                    warning = (warning or "") + " | Funding rising rapidly"
                    confidence_adj -= 5
            elif current_rate < rate_8h_avg < rate_24h_avg:
                # Falling funding
                if current_rate < 0:
                    opportunity = (opportunity or "") + " | Funding falling - shorts increasing"

        return FundingAnalysis(
            current_rate=current_rate,
            rate_8h_avg=rate_8h_avg or current_rate,
            rate_24h_avg=rate_24h_avg or current_rate,
            percentile=percentile,
            signal=signal,
            confidence_adjustment=confidence_adj,
            warning=warning,
            opportunity=opportunity,
        )

    def get_trade_filter(
        self,
        funding_analysis: FundingAnalysis,
        proposed_signal: str
    ) -> Dict:
        """
        Filter trade signals based on funding analysis.

        Args:
            funding_analysis: Result from analyze()
            proposed_signal: "BUY" or "SELL"

        Returns:
            Dict with approval and adjustments
        """
        if not self.feature_enabled:
            return {
                "approved": True,
                "confidence_adjustment": 0,
                "reasons": [],
                "warning": None,
                "opportunity": None,
                "feature_enabled": False,
            }

        approved = True
        reasons = []

        # Block trades against extreme funding
        if proposed_signal == "BUY":
            if funding_analysis.signal == FundingSignal.CONTRARIAN_SHORT:
                approved = False
                reasons.append("Extreme positive funding - avoid new LONGs")
            elif funding_analysis.signal == FundingSignal.SQUEEZE_RISK_SHORT:
                reasons.append("High funding - reduce position size")

        elif proposed_signal == "SELL":
            if funding_analysis.signal == FundingSignal.CONTRARIAN_LONG:
                approved = False
                reasons.append("Extreme negative funding - avoid new SHORTs")
            elif funding_analysis.signal == FundingSignal.SQUEEZE_RISK_LONG:
                reasons.append("Low funding - short squeeze risk")

        return {
            "approved": approved,
            "confidence_adjustment": funding_analysis.confidence_adjustment,
            "reasons": reasons,
            "warning": funding_analysis.warning,
            "opportunity": funding_analysis.opportunity,
            "feature_enabled": True,
        }

    def get_prompt_section(self, analysis: FundingAnalysis) -> str:
        """Generate prompt section for GLM"""
        if not self.feature_enabled:
            return ""

        lines = [
            "",
            "=" * 60,
            "FUNDING RATE ANALYSIS",
            "=" * 60,
            "",
            f"Current Rate: {analysis.current_rate*100:.4f}% (8h)",
            f"8H Average: {analysis.rate_8h_avg*100:.4f}%",
            f"24H Average: {analysis.rate_24h_avg*100:.4f}%",
            f"Percentile (historical): {analysis.percentile:.0f}%",
            "",
            f"Signal: {analysis.signal.value.upper()}",
            f"Confidence Impact: {analysis.confidence_adjustment:+d}%",
            "",
        ]

        if analysis.warning:
            lines.append(f"WARNING: {analysis.warning}")
        if analysis.opportunity:
            lines.append(f"OPPORTUNITY: {analysis.opportunity}")

        return "\n".join(lines)

    def get_summary(self) -> Dict:
        """Get summary of analyzer state"""
        return {
            "feature_enabled": self.feature_enabled,
            "history_size": len(self.funding_history),
            "thresholds": {
                "extreme_positive": self.extreme_positive,
                "high_positive": self.high_positive,
                "high_negative": self.high_negative,
                "extreme_negative": self.extreme_negative,
            }
        }


# ADX Calculation Functions (standalone)
def calculate_adx(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Dict[str, List[float]]:
    """
    Calculate ADX (Average Directional Index) and DI+/DI-.

    Args:
        highs: High prices
        lows: Low prices
        closes: Close prices
        period: ADX period (default 14)

    Returns:
        Dict with:
        - adx: List[float] - ADX values
        - plus_di: List[float] - +DI values
        - minus_di: List[float] - -DI values
    """
    if len(highs) < period + 1:
        return {"adx": [], "plus_di": [], "minus_di": []}

    n = len(highs)

    # True Range
    tr = []
    for i in range(1, n):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        tr.append(max(high_low, high_close, low_close))

    # Directional Movement
    plus_dm = []
    minus_dm = []

    for i in range(1, n):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)

    # Wilder's smoothing
    def wilder_smooth(values: List[float], period: int) -> List[float]:
        result = []
        if len(values) < period:
            return result

        # First value is SMA (Simple Moving Average), not sum
        first_sum = sum(values[:period])
        result.append(first_sum / period)  # FIX: Divide by period for SMA

        # Subsequent values use Wilder's formula
        # Correct formula: smoothed = (prev * (period-1) + current) / period
        for i in range(period, len(values)):
            smoothed = (result[-1] * (period - 1) + values[i]) / period
            result.append(smoothed)

        return result

    atr_smooth = wilder_smooth(tr, period)
    plus_dm_smooth = wilder_smooth(plus_dm, period)
    minus_dm_smooth = wilder_smooth(minus_dm, period)

    # Calculate +DI and -DI
    plus_di = []
    minus_di = []
    dx = []

    for i in range(len(atr_smooth)):
        if atr_smooth[i] > 0:
            pdi = (plus_dm_smooth[i] / atr_smooth[i]) * 100
            mdi = (minus_dm_smooth[i] / atr_smooth[i]) * 100
        else:
            pdi = 0
            mdi = 0

        plus_di.append(pdi)
        minus_di.append(mdi)

        # DX
        di_sum = pdi + mdi
        if di_sum > 0:
            dx.append(abs(pdi - mdi) / di_sum * 100)
        else:
            dx.append(0)

    # ADX (smoothed DX)
    adx_values = wilder_smooth(dx, period)

    # ADX değerleri 0-100 aralığında olmalı, normalizasyon yapılmamalı
    # Eski hatalı kod: adx_normalized = [v / period for v in adx_values]

    return {
        "adx": adx_values,
        "plus_di": plus_di,
        "minus_di": minus_di,
    }


def interpret_adx(adx_value: float, plus_di: float, minus_di: float) -> Dict:
    """
    Interpret ADX values for trading decisions.

    Args:
        adx_value: Current ADX value
        plus_di: Current +DI value
        minus_di: Current -DI value

    Returns:
        Dict with interpretation
    """
    # Trend strength
    if adx_value < 20:
        strength = "WEAK"
        trend_type = "RANGE"
        recommendation = "Range trading or wait"
    elif adx_value < 25:
        strength = "DEVELOPING"
        trend_type = "EMERGING"
        recommendation = "Watch for breakout confirmation"
    elif adx_value < 50:
        strength = "STRONG"
        trend_type = "TRENDING"
        recommendation = "Trade in trend direction"
    else:
        strength = "VERY_STRONG"
        trend_type = "EXTENDED"
        recommendation = "Caution - trend may be exhausting"

    # Direction
    if plus_di > minus_di:
        direction = "BULLISH"
    elif minus_di > plus_di:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # DI crossover signals
    di_diff = plus_di - minus_di
    di_signal = None

    if abs(di_diff) > 10:  # Significant difference
        if plus_di > minus_di:
            di_signal = "Strong bullish momentum"
        else:
            di_signal = "Strong bearish momentum"

    return {
        "adx": adx_value,
        "strength": strength,
        "trend_type": trend_type,
        "direction": direction,
        "recommendation": recommendation,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "di_signal": di_signal,
    }


def get_adx_trade_filter(
    adx_value: float,
    plus_di: float,
    minus_di: float,
    proposed_signal: str  # "BUY" or "SELL"
) -> Dict:
    """
    Use ADX as a trade filter.

    Args:
        adx_value: Current ADX value
        plus_di: Current +DI value
        minus_di: Current -DI value
        proposed_signal: "BUY" or "SELL"

    Returns:
        Dict with:
        - approved: bool
        - confidence_adjustment: float (-20 to +20)
        - reasons: List[str]
    """
    interpretation = interpret_adx(adx_value, plus_di, minus_di)

    # Base approval
    approved = True
    confidence_adj = 0
    reasons = []

    # 1. Check trend strength
    if interpretation["strength"] == "WEAK":
        approved = False
        confidence_adj = -20
        reasons.append(f"ADX too weak ({adx_value:.1f}) - no clear trend")

    elif interpretation["strength"] == "VERY_STRONG":
        confidence_adj = -10
        reasons.append(f"ADX very high ({adx_value:.1f}) - trend may exhaust")

    elif interpretation["strength"] == "STRONG":
        confidence_adj = +10
        reasons.append(f"ADX strong ({adx_value:.1f}) - good trend conditions")

    # 2. Check direction alignment
    if proposed_signal == "BUY":
        if interpretation["direction"] == "BEARISH":
            approved = False
            confidence_adj -= 15
            reasons.append(f"-DI > +DI ({minus_di:.1f} > {plus_di:.1f}) - bearish momentum, avoid LONG")
        elif interpretation["direction"] == "BULLISH":
            confidence_adj += 5
            reasons.append(f"+DI > -DI ({plus_di:.1f} > {minus_di:.1f}) - confirms bullish")

    elif proposed_signal == "SELL":
        if interpretation["direction"] == "BULLISH":
            approved = False
            confidence_adj -= 15
            reasons.append(f"+DI > -DI ({plus_di:.1f} > {minus_di:.1f}) - bullish momentum, avoid SHORT")
        elif interpretation["direction"] == "BEARISH":
            confidence_adj += 5
            reasons.append(f"-DI > +DI ({minus_di:.1f} > {plus_di:.1f}) - confirms bearish")

    return {
        "approved": approved,
        "confidence_adjustment": confidence_adj,
        "reasons": reasons,
        "adx_interpretation": interpretation,
    }


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test Funding Rate Analyzer
    analyzer = FundingRateAnalyzer()

    # Add some history
    for _ in range(100):
        import random
        analyzer.add_funding_rate(random.uniform(-0.0005, 0.0005))

    # Analyze current rate
    analysis = analyzer.analyze(
        current_rate=0.0008,  # 0.08% - high positive
        rate_8h_avg=0.0006,
        rate_24h_avg=0.0004,
        price_trend="BULLISH"
    )

    print("Funding Analysis:")
    print(f"  Signal: {analysis.signal.value}")
    print(f"  Confidence Adjustment: {analysis.confidence_adjustment}")
    print(f"  Warning: {analysis.warning}")
    print(f"  Opportunity: {analysis.opportunity}")

    print("\n" + analyzer.get_prompt_section(analysis))

    # Test trade filter
    filter_result = analyzer.get_trade_filter(analysis, "BUY")
    print(f"\nTrade Filter for BUY: {filter_result}")

    # Test ADX
    print("\n--- ADX Test ---")
    highs = [100 + i*0.5 + random.uniform(0, 1) for i in range(50)]
    lows = [100 + i*0.5 - random.uniform(0, 1) for i in range(50)]
    closes = [100 + i*0.5 for i in range(50)]

    adx_data = calculate_adx(highs, lows, closes)
    if adx_data["adx"]:
        current_adx = adx_data["adx"][-1]
        current_plus_di = adx_data["plus_di"][-1]
        current_minus_di = adx_data["minus_di"][-1]

        print(f"ADX: {current_adx:.2f}")
        print(f"+DI: {current_plus_di:.2f}")
        print(f"-DI: {current_minus_di:.2f}")

        interp = interpret_adx(current_adx, current_plus_di, current_minus_di)
        print(f"Interpretation: {interp}")
