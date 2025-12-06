"""
Volatility Analysis Module

Asset-agnostic volatility calculations and regime detection.
"""

import time
from typing import Dict, List, Optional, Tuple

from app.utils.logging import get_logger

from .models import VOLATILITY_PARAMS


logger = get_logger(__name__)


class VolatilityAnalyzer:
    """
    Analyzes volatility using relative (asset-agnostic) methods.
    """

    def __init__(self, cache_ttl: int = 60):
        """
        Initialize the volatility analyzer.

        Args:
            cache_ttl: Cache time-to-live in seconds
        """
        self._vol_cache: Dict[str, Tuple[float, List[float]]] = {}
        self._cache_ttl = cache_ttl

    def compute_realized_vol_pct(
        self, closes: List[float], lookback: int = 50
    ) -> float:
        """
        Compute realized volatility from returns over lookback bars, as percent.
        Asset-agnostic (works across BTC/ETH/SOL).

        Args:
            closes: List of close prices
            lookback: Number of bars to look back

        Returns:
            Realized volatility as percentage
        """
        if not closes or len(closes) < lookback + 1:
            return 0.0

        window = closes[-(lookback + 1) :]
        rets = []

        for i in range(1, len(window)):
            p0 = window[i - 1]
            p1 = window[i]
            if p0 > 0 and p1 > 0:
                rets.append((p1 / p0) - 1.0)

        if len(rets) < 2:
            return 0.0

        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        return (var**0.5) * 100

    def rolling_median(self, values: List[float], lookback: int = 200) -> float:
        """
        Calculate rolling median of values.

        Args:
            values: List of values
            lookback: Number of values to consider

        Returns:
            Median value
        """
        if not values:
            return 0.0

        w = values[-lookback:] if len(values) >= lookback else values[:]
        w_sorted = sorted(w)
        n = len(w_sorted)
        mid = n // 2

        if n % 2 == 1:
            return float(w_sorted[mid])
        return float((w_sorted[mid - 1] + w_sorted[mid]) / 2)

    def get_cached_volatility_samples(
        self, symbol: str, closes: List[float]
    ) -> List[float]:
        """
        Get volatility samples with caching for performance.
        Uses 3 strategic sample points instead of O(n) sampling.

        Args:
            symbol: Trading symbol for cache key
            closes: List of close prices

        Returns:
            List of realized volatility samples
        """
        if len(closes) < 60:
            return []

        # Create cache key from symbol and data fingerprint
        data_fingerprint = f"{closes[0]:.2f}_{closes[-1]:.2f}_{len(closes)}"
        cache_key = f"{symbol}_{data_fingerprint}"

        current_time = time.time()

        # Check cache
        if cache_key in self._vol_cache:
            cached_time, cached_samples = self._vol_cache[cache_key]
            if current_time - cached_time < self._cache_ttl:
                return cached_samples

        # Calculate using 3 strategic sample points
        samples = []
        data_len = len(closes)

        sample_points = [
            int(data_len * 0.25),  # Early: 25% of data
            int(data_len * 0.5),  # Middle: 50% of data
            data_len,  # Recent: all data
        ]

        for point in sample_points:
            if point >= 60:
                rv = self.compute_realized_vol_pct(closes[:point], lookback=50)
                if rv > 0:
                    samples.append(rv)

        # Cache the result
        self._vol_cache[cache_key] = (current_time, samples)

        # Cleanup old cache entries
        if len(self._vol_cache) > 100:
            oldest_keys = sorted(
                self._vol_cache.keys(), key=lambda k: self._vol_cache[k][0]
            )[:50]
            for k in oldest_keys:
                del self._vol_cache[k]

        return samples

    def calculate_percentile(self, current: float, history: List[float]) -> float:
        """
        Calculate where current value stands in historical distribution.

        Args:
            current: Current value
            history: Historical values

        Returns:
            Percentile (0-100)
        """
        if not history or len(history) < 2:
            return 50.0

        sorted_hist = sorted(history)
        count_below = sum(1 for v in sorted_hist if v < current)
        return (count_below / len(sorted_hist)) * 100

    def vol_regime_key(
        self,
        atr_pct: Optional[float] = None,
        vol_ratio: Optional[float] = None,
        atr_ratio: Optional[float] = None,
    ) -> str:
        """
        Determine volatility regime based on relative metrics.

        Args:
            atr_pct: ATR as percentage of price
            vol_ratio: Volatility ratio vs historical
            atr_ratio: ATR ratio vs historical

        Returns:
            Regime key: "low", "medium", "high", or "extreme"
        """
        atr_pct = atr_pct or 0.0
        vol_ratio = vol_ratio or 1.0
        atr_ratio = atr_ratio or 1.0

        rel = max(vol_ratio, atr_ratio)

        if rel < 0.9 and atr_pct < 1.2:
            return "low"
        if rel < 1.5 and atr_pct < 2.5:
            return "medium"
        if rel < 2.5 and atr_pct < 4.0:
            return "high"
        return "extreme"

    def calculate_slope(self, series: List[float], lookback: int = 4) -> float:
        """
        Calculate slope (trend direction) of a data series.

        Args:
            series: List of values
            lookback: Number of bars to analyze

        Returns:
            Change per bar
        """
        if not series or len(series) < 2:
            return 0.0

        window = series[-lookback:] if len(series) >= lookback else series
        if len(window) < 2:
            return 0.0

        return (window[-1] - window[0]) / (len(window) - 1)

    def detect_divergence(
        self, price_slope: float, indicator_slope: float, threshold: float = 0.0
    ) -> str:
        """
        Detect divergence between price and indicator.

        Args:
            price_slope: Price trend slope
            indicator_slope: Indicator trend slope
            threshold: Minimum significant slope threshold

        Returns:
            "BULLISH_DIV", "BEARISH_DIV", or "NONE"
        """
        if price_slope < -threshold and indicator_slope > threshold:
            return "BULLISH_DIV"
        elif price_slope > threshold and indicator_slope < -threshold:
            return "BEARISH_DIV"
        return "NONE"

    def get_regime_params(self, regime: str) -> Dict:
        """
        Get volatility parameters for a regime.

        Args:
            regime: Volatility regime key

        Returns:
            Dictionary with sl_mult, tp_rr, max_lev
        """
        return VOLATILITY_PARAMS.get(regime, VOLATILITY_PARAMS["medium"])
