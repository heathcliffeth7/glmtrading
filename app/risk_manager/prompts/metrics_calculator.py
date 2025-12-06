"""
Statistical and Technical Metrics Calculation Utilities

This module provides standalone metric calculation functions and caching utilities
for volatility analysis, percentiles, slopes, and divergence detection.

Extracted from nof1_prompt_builder.py for token optimization.
"""

import time
from typing import Dict, List, Tuple


class VolatilityCache:
    """
    Manages volatility sample caching for performance optimization.
    
    Uses strategic sampling (3 points: 25%, 50%, 100% of data) instead of
    O(n) full calculation for better performance.
    """
    
    def __init__(self, ttl: int = 60, max_size: int = 100):
        """
        Initialize volatility cache.
        
        Args:
            ttl: Time-to-live in seconds (default: 60)
            max_size: Maximum cache entries before cleanup (default: 100)
        """
        self._cache: Dict[str, Tuple[float, List[float]]] = {}
        self._ttl = ttl
        self._max_size = max_size
    
    def get_cached_samples(
        self, 
        symbol: str, 
        closes: List[float],
        compute_func
    ) -> List[float]:
        """
        Get volatility samples with caching.
        
        Args:
            symbol: Trading symbol for cache key
            closes: List of close prices
            compute_func: Function to compute realized volatility
            
        Returns:
            List of realized volatility samples at strategic points
        """
        if len(closes) < 60:
            return []
        
        # Create cache key from symbol and data fingerprint
        data_fingerprint = f"{closes[0]:.2f}_{closes[-1]:.2f}_{len(closes)}"
        cache_key = f"{symbol}_{data_fingerprint}"
        
        current_time = time.time()
        
        # Check cache
        if cache_key in self._cache:
            cached_time, cached_samples = self._cache[cache_key]
            if current_time - cached_time < self._ttl:
                return cached_samples
        
        # Calculate using 3 strategic sample points instead of O(n)
        # Points: early (25%), middle (50%), recent (100%)
        samples = []
        data_len = len(closes)
        
        sample_points = [
            int(data_len * 0.25),  # Early: 25% of data
            int(data_len * 0.5),   # Middle: 50% of data
            data_len,              # Recent: all data
        ]
        
        for point in sample_points:
            if point >= 60:  # Need at least 60 points for valid calculation
                rv = compute_func(closes[:point], lookback=50)
                if rv > 0:
                    samples.append(rv)
        
        # Cache the result
        self._cache[cache_key] = (current_time, samples)
        
        # Cleanup old cache entries (keep cache small)
        if len(self._cache) > self._max_size:
            oldest_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k][0]
            )[:50]
            for k in oldest_keys:
                del self._cache[k]
        
        return samples


def compute_realized_vol_pct(closes: List[float], lookback: int = 50) -> float:
    """
    Calculate realized volatility from returns over lookback period.
    
    This is asset-agnostic and works across BTC/ETH/SOL by using returns
    instead of absolute price changes.
    
    Args:
        closes: List of closing prices
        lookback: Number of bars for calculation (default: 50)
        
    Returns:
        Realized volatility as percentage
    """
    # Need at least lookback+1 closes to compute lookback returns
    if not closes or len(closes) < lookback + 1:
        return 0.0
    
    window = closes[-(lookback + 1):]
    rets = []
    
    for i in range(1, len(window)):
        p0 = window[i - 1]
        p1 = window[i]
        if p0 > 0 and p1 > 0:
            rets.append((p1 / p0) - 1.0)
    
    if len(rets) < 2:
        return 0.0
    
    # Calculate variance and convert to percentage
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / len(rets)
    return (var ** 0.5) * 100


def rolling_median(values: List[float], lookback: int = 200) -> float:
    """
    Calculate rolling median over lookback period.
    
    Args:
        values: List of values
        lookback: Number of periods for rolling window (default: 200)
        
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


def calculate_percentile(current: float, history: List[float]) -> float:
    """
    Calculate where current value stands in historical context (0-100%).
    
    Useful for context-aware interpretation: "RSI at 65th percentile means
    it's higher than 65% of recent readings".
    
    Args:
        current: Current value
        history: Historical values list
        
    Returns:
        Percentile rank (0-100)
    """
    if not history or len(history) < 2:
        return 50.0
    
    sorted_hist = sorted(history)
    count_below = sum(1 for v in sorted_hist if v < current)
    return (count_below / len(sorted_hist)) * 100


def calculate_slope(series: List[float], lookback: int = 4) -> float:
    """
    Calculate slope (trend direction) of data series.
    
    Returns change per bar, useful for detecting momentum direction.
    
    Args:
        series: Values list
        lookback: Number of bars to look back (default: 4)
        
    Returns:
        Change per bar (slope)
    """
    if not series or len(series) < 2:
        return 0.0
    
    window = series[-lookback:] if len(series) >= lookback else series
    if len(window) < 2:
        return 0.0
    
    return (window[-1] - window[0]) / (len(window) - 1)


def detect_divergence(
    price_slope: float, 
    indicator_slope: float, 
    threshold: float = 0.0
) -> str:
    """
    Detect divergence between price and indicator.
    
    Divergence signals potential trend reversals:
    - Bullish divergence: Price falling but indicator rising (reversal up)
    - Bearish divergence: Price rising but indicator falling (reversal down)
    
    Args:
        price_slope: Price slope
        indicator_slope: Indicator slope (e.g., RSI, CVD)
        threshold: Minimum meaningful slope threshold (default: 0.0)
        
    Returns:
        "BULLISH_DIV", "BEARISH_DIV", or "NONE"
    """
    if price_slope < -threshold and indicator_slope > threshold:
        return "BULLISH_DIV"
    elif price_slope > threshold and indicator_slope < -threshold:
        return "BEARISH_DIV"
    return "NONE"


def summarize_series(
    values: List[float], 
    decimals: int = 2, 
    name: str = ""
) -> str:
    """
    Create compact summary of a data series (tail values).
    
    Args:
        values: Data series
        decimals: Number of decimal places (default: 2)
        name: Series name for labeling (optional)
        
    Returns:
        Formatted string summary
    """
    if not values:
        return f"{name}: []" if name else "[]"
    
    # Show last 6 values to avoid token explosion
    tail = values[-6:]
    formatted = [f"{v:.{decimals}f}" for v in tail]
    result = "[" + ", ".join(formatted) + "]"
    
    return f"{name}: {result}" if name else result


def format_array(values: List[float], decimals: int = 2) -> str:
    """
    Format array of floats for compact display.
    
    Args:
        values: List of float values
        decimals: Number of decimal places (default: 2)
        
    Returns:
        Formatted string
    """
    if not values:
        return "[]"
    return "[" + ", ".join(f"{v:.{decimals}f}" for v in values) + "]"


__all__ = [
    "VolatilityCache",
    "compute_realized_vol_pct",
    "rolling_median",
    "calculate_percentile",
    "calculate_slope",
    "detect_divergence",
    "summarize_series",
    "format_array",
]
