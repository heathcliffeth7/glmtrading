"""
Data Consistency Validation for Multi-Timeframe Trading System

Ensures that data from different timeframes and sources is synchronized
and prevents trading on stale or inconsistent market data.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List

from app.utils.logging import get_logger


logger = get_logger(__name__)


@dataclass
class TimestampedSnapshot:
    """
    Timestamped market data snapshot with staleness detection
    
    Tracks both market time (when data was generated) and ingestion time
    (when system received it) to detect delays and stale data.
    """
    market_timestamp: datetime  # Binance kline close time or market event time
    ingestion_timestamp: datetime  # System receive time
    data: Dict
    source: str
    
    @property
    def latency_ms(self) -> float:
        """Calculate latency between market time and ingestion time"""
        if not self.market_timestamp or not self.ingestion_timestamp:
            return 0.0
        delta = self.ingestion_timestamp - self.market_timestamp
        return abs(delta.total_seconds() * 1000)
    
    @property
    def age_seconds(self) -> float:
        """How old is this data (from now)?"""
        if not self.ingestion_timestamp:
            return 999999.0
        return (datetime.now(timezone.utc) - self.ingestion_timestamp).total_seconds()
    
    def is_stale(self, max_age_seconds: int = 30) -> bool:
        """Check if data is older than acceptable threshold"""
        return self.age_seconds > max_age_seconds
    
    def is_valid(self) -> bool:
        """Basic validation checks"""
        if not self.data:
            return False
        if not self.market_timestamp or not self.ingestion_timestamp:
            return False
        # Check if timestamps are reasonable (not from future, not too old)
        now = datetime.now(timezone.utc)
        if self.ingestion_timestamp > now + timedelta(seconds=60):
            logger.error("❌ Future timestamp detected: %s", self.ingestion_timestamp)
            return False
        if self.ingestion_timestamp < now - timedelta(days=7):
            logger.error("❌ Very old timestamp detected: %s", self.ingestion_timestamp)
            return False
        return True


class DataConsistencyValidator:
    """
    Validates consistency across multiple data sources
    
    Ensures that data from different timeframes is synchronized and
    prevents trading decisions based on misaligned data.
    """
    
    def __init__(self, max_drift_ms: int = 5000):
        """
        Initialize validator
        
        Args:
            max_drift_ms: Maximum acceptable time drift between data sources (milliseconds)
        """
        self._max_drift_ms = max_drift_ms
    
    def validate_multi_timeframe(
        self, 
        snapshots: Dict[str, TimestampedSnapshot]
    ) -> tuple[bool, str]:
        """
        Validate that all timeframe snapshots are synchronized
        
        Args:
            snapshots: Dictionary of {timeframe: TimestampedSnapshot}
        
        Returns:
            (is_valid: bool, reason: str)
        """
        if not snapshots:
            return False, "No snapshots provided"
        
        # Collect all snapshots
        valid_snapshots = []
        for timeframe, snapshot in snapshots.items():
            if not snapshot.is_valid():
                logger.warning("⚠️ Invalid snapshot for %s", timeframe)
                continue
            valid_snapshots.append((timeframe, snapshot))
        
        if not valid_snapshots:
            return False, "No valid snapshots available"
        
        # Check staleness
        stale_snapshots = []
        for timeframe, snapshot in valid_snapshots:
            if snapshot.is_stale(max_age_seconds=60):
                stale_snapshots.append(
                    f"{timeframe} (age: {snapshot.age_seconds:.1f}s)"
                )
        
        if stale_snapshots:
            return False, f"Stale data detected: {', '.join(stale_snapshots)}"
        
        # Check time drift between sources
        market_times = [s.market_timestamp for _, s in valid_snapshots]
        if len(market_times) > 1:
            time_range = max(market_times) - min(market_times)
            drift_ms = time_range.total_seconds() * 1000
            
            if drift_ms > self._max_drift_ms:
                return False, f"Time drift {drift_ms:.0f}ms > {self._max_drift_ms}ms"
        
        # All checks passed
        avg_latency = sum(s.latency_ms for _, s in valid_snapshots) / len(valid_snapshots)
        return True, f"All {len(valid_snapshots)} timeframes synchronized (avg latency: {avg_latency:.0f}ms)"
    
    def validate_price_consistency(
        self,
        snapshots: Dict[str, TimestampedSnapshot],
        max_price_deviation_pct: float = 1.0
    ) -> tuple[bool, str]:
        """
        Validate that prices across timeframes are consistent
        
        Args:
            snapshots: Dictionary of {timeframe: TimestampedSnapshot}
            max_price_deviation_pct: Maximum acceptable price deviation between timeframes
        
        Returns:
            (is_valid: bool, reason: str)
        """
        prices = []
        for timeframe, snapshot in snapshots.items():
            if not snapshot.is_valid():
                continue
            price = snapshot.data.get('close', 0)
            if price > 0:
                prices.append((timeframe, price))
        
        if len(prices) < 2:
            return True, "Not enough price data to validate consistency"
        
        # Calculate price deviation
        price_values = [p for _, p in prices]
        min_price = min(price_values)
        max_price = max(price_values)
        deviation_pct = ((max_price - min_price) / min_price) * 100
        
        if deviation_pct > max_price_deviation_pct:
            price_str = ', '.join(f"{tf}:${p:.2f}" for tf, p in prices)
            return False, f"Price deviation {deviation_pct:.2f}% > {max_price_deviation_pct}% ({price_str})"
        
        return True, f"Price consistency OK (deviation: {deviation_pct:.3f}%)"
    
    def get_diagnostic_info(
        self,
        snapshots: Dict[str, TimestampedSnapshot]
    ) -> Dict:
        """
        Get detailed diagnostic information about snapshots
        
        Returns:
            Dictionary with diagnostic metrics
        """
        diagnostics = {
            "total_snapshots": len(snapshots),
            "valid_snapshots": 0,
            "stale_snapshots": 0,
            "invalid_snapshots": 0,
            "avg_latency_ms": 0.0,
            "max_drift_ms": 0.0,
            "timeframes": {}
        }
        
        valid_snapshots = []
        for timeframe, snapshot in snapshots.items():
            info = {
                "valid": snapshot.is_valid(),
                "stale": snapshot.is_stale(),
                "age_seconds": snapshot.age_seconds,
                "latency_ms": snapshot.latency_ms,
                "source": snapshot.source
            }
            diagnostics["timeframes"][timeframe] = info
            
            if info["valid"]:
                diagnostics["valid_snapshots"] += 1
                valid_snapshots.append(snapshot)
            else:
                diagnostics["invalid_snapshots"] += 1
            
            if info["stale"]:
                diagnostics["stale_snapshots"] += 1
        
        if valid_snapshots:
            diagnostics["avg_latency_ms"] = sum(
                s.latency_ms for s in valid_snapshots
            ) / len(valid_snapshots)
            
            market_times = [s.market_timestamp for s in valid_snapshots]
            if len(market_times) > 1:
                time_range = max(market_times) - min(market_times)
                diagnostics["max_drift_ms"] = time_range.total_seconds() * 1000
        
        return diagnostics
