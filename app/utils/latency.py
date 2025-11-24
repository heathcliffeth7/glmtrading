"""
End-to-End Latency Monitoring System

Tracks data flow latency across critical pipeline stages:
1d: WebSocket data ingestion (Binance → System)
2d: TA calculation completion (Raw Data → Indicators)
3d: InfluxDB write completion (Indicators → Storage)
4d: Agent signal generation (Storage → Signal)
5d: GLM request submission (Signal → Decision)

Δt = Total latency from market data to trading decision
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from app.utils.logging import get_logger


logger = get_logger(__name__)


@dataclass
class LatencyTrace:
    """
    Tracks timestamps across the data pipeline for latency measurement
    
    Stages:
    - t1_ingestion: Data received from WebSocket
    - t2_ta_complete: Technical indicators calculated
    - t3_influx_write: Data written to InfluxDB
    - t4_signal_generated: Agent signal generated
    - t5_glm_submitted: Request sent to GLM
    """
    symbol: str
    interval: str
    trace_id: str  # Unique identifier for this data point
    
    # Timestamps (Unix epoch in seconds with microsecond precision)
    t1_ingestion: Optional[float] = None
    t2_ta_complete: Optional[float] = None
    t3_influx_write: Optional[float] = None
    t4_signal_generated: Optional[float] = None
    t5_glm_submitted: Optional[float] = None
    
    # Market timestamp (from exchange)
    market_timestamp: Optional[datetime] = None
    
    # Metadata
    metadata: Dict = field(default_factory=dict)
    
    def mark_ingestion(self, market_ts: Optional[datetime] = None) -> None:
        """Mark data ingestion point (1d)"""
        self.t1_ingestion = time.time()
        if market_ts:
            self.market_timestamp = market_ts
    
    def mark_ta_complete(self) -> None:
        """Mark TA calculation completion (2d)"""
        self.t2_ta_complete = time.time()
    
    def mark_influx_write(self) -> None:
        """Mark InfluxDB write completion (3d)"""
        self.t3_influx_write = time.time()
    
    def mark_signal_generated(self) -> None:
        """Mark agent signal generation (4d)"""
        self.t4_signal_generated = time.time()
    
    def mark_glm_submitted(self) -> None:
        """Mark GLM request submission (5d)"""
        self.t5_glm_submitted = time.time()
    
    def get_latencies(self) -> Dict[str, float]:
        """
        Calculate latencies between stages (in seconds)
        
        Returns:
            Dict with stage-to-stage latencies and total latency
        """
        latencies = {}
        
        if self.t1_ingestion and self.t2_ta_complete:
            latencies['ingestion_to_ta'] = self.t2_ta_complete - self.t1_ingestion
        
        if self.t2_ta_complete and self.t3_influx_write:
            latencies['ta_to_influx'] = self.t3_influx_write - self.t2_ta_complete
        
        if self.t3_influx_write and self.t4_signal_generated:
            latencies['influx_to_signal'] = self.t4_signal_generated - self.t3_influx_write
        
        if self.t4_signal_generated and self.t5_glm_submitted:
            latencies['signal_to_glm'] = self.t5_glm_submitted - self.t4_signal_generated
        
        # Total end-to-end latency
        if self.t1_ingestion and self.t5_glm_submitted:
            latencies['total_e2e'] = self.t5_glm_submitted - self.t1_ingestion
        
        # Market data age (if available)
        if self.market_timestamp and self.t1_ingestion:
            market_age = self.t1_ingestion - self.market_timestamp.timestamp()
            latencies['market_to_ingestion'] = market_age
        
        return latencies
    
    def get_summary(self) -> str:
        """Get human-readable latency summary"""
        latencies = self.get_latencies()
        
        if not latencies:
            return f"No latency data for {self.symbol}/{self.interval}"
        
        parts = [f"Latency trace for {self.symbol}/{self.interval}:"]
        
        for stage, latency_sec in latencies.items():
            latency_ms = latency_sec * 1000
            
            # Color coding based on latency
            if latency_sec > 1.0:
                emoji = "🔴"  # Critical
            elif latency_sec > 0.5:
                emoji = "🟡"  # Warning
            else:
                emoji = "🟢"  # Good
            
            parts.append(f"  {emoji} {stage}: {latency_ms:.2f}ms")
        
        return " | ".join(parts)
    
    def is_fresh(self, max_latency_sec: float = 1.0) -> bool:
        """
        Check if data is fresh (total latency under threshold)
        
        Args:
            max_latency_sec: Maximum acceptable latency in seconds
        
        Returns:
            True if data is fresh, False if stale
        """
        latencies = self.get_latencies()
        total = latencies.get('total_e2e')
        
        if total is None:
            return False
        
        return total <= max_latency_sec
    
    def to_influx_fields(self) -> Dict[str, float]:
        """
        Convert latencies to InfluxDB fields
        
        Returns:
            Dict suitable for InfluxDB write_measurement
        """
        latencies = self.get_latencies()
        fields = {}
        
        # Convert to milliseconds for better readability
        for key, value_sec in latencies.items():
            fields[f"latency_{key}_ms"] = value_sec * 1000
        
        # Add stage timestamps (as Unix epoch)
        if self.t1_ingestion:
            fields['t1_ingestion'] = self.t1_ingestion
        if self.t2_ta_complete:
            fields['t2_ta_complete'] = self.t2_ta_complete
        if self.t3_influx_write:
            fields['t3_influx_write'] = self.t3_influx_write
        if self.t4_signal_generated:
            fields['t4_signal_generated'] = self.t4_signal_generated
        if self.t5_glm_submitted:
            fields['t5_glm_submitted'] = self.t5_glm_submitted
        
        return fields


class LatencyTracker:
    """
    Global latency tracker for managing traces across the pipeline
    
    Usage:
        tracker = LatencyTracker()
        
        # Stage 1: WebSocket ingestion
        trace = tracker.start_trace(symbol="BTCUSDT", interval="1m", trace_id="kline_123")
        trace.mark_ingestion()
        
        # Stage 2: TA calculation
        trace.mark_ta_complete()
        
        # ... continue through pipeline ...
        
        # Final: Write to InfluxDB
        tracker.write_latency_metrics(trace)
    """
    
    def __init__(self, max_traces: int = 1000):
        """
        Initialize latency tracker
        
        Args:
            max_traces: Maximum number of active traces to keep in memory
        """
        self._traces: Dict[str, LatencyTrace] = {}
        self._max_traces = max_traces
        self._trace_count = 0
        self._last_latency_warning: float = 0  # Timestamp of last warning (throttling)
    
    def start_trace(
        self,
        symbol: str,
        interval: str,
        trace_id: Optional[str] = None,
        market_ts: Optional[datetime] = None,
    ) -> LatencyTrace:
        """
        Start a new latency trace
        
        Args:
            symbol: Trading symbol
            interval: Time interval
            trace_id: Unique identifier (auto-generated if None)
            market_ts: Market timestamp from exchange
        
        Returns:
            LatencyTrace object
        """
        if trace_id is None:
            self._trace_count += 1
            trace_id = f"{symbol}_{interval}_{self._trace_count}"
        
        trace = LatencyTrace(
            symbol=symbol,
            interval=interval,
            trace_id=trace_id,
        )
        trace.mark_ingestion(market_ts)
        
        # Store trace
        self._traces[trace_id] = trace
        
        # Cleanup old traces if exceeding limit
        if len(self._traces) > self._max_traces:
            # Remove oldest 10%
            to_remove = list(self._traces.keys())[:int(self._max_traces * 0.1)]
            for key in to_remove:
                del self._traces[key]
        
        return trace
    
    def get_trace(self, trace_id: str) -> Optional[LatencyTrace]:
        """Get existing trace by ID"""
        return self._traces.get(trace_id)
    
    def write_latency_metrics(self, trace: LatencyTrace) -> None:
        """
        Write latency metrics to InfluxDB
        
        Args:
            trace: Completed or partial latency trace
        """
        try:
            from app.utils.influx import write_measurement
            
            fields = trace.to_influx_fields()
            
            if not fields:
                logger.debug("No latency data to write for trace %s", trace.trace_id)
                return
            
            write_measurement(
                measurement="pipeline_latency",
                tags={
                    'symbol': trace.symbol,
                    'interval': trace.interval,
                    'trace_id': trace.trace_id,
                },
                fields=fields,
                timestamp=datetime.utcnow(),
            )
            
            # Log summary
            summary = trace.get_summary()
            logger.info("📊 %s", summary)
            
            # Alert if latency is high (throttled to once every 5 minutes)
            latencies = trace.get_latencies()
            total = latencies.get('total_e2e', 0)
            current_time = time.time()
            if total > 1.0:
                # Only warn if 5 minutes (300 seconds) have passed since last warning
                if current_time - self._last_latency_warning > 300:
                    logger.warning(
                        "⚠️ High latency detected: %.2fms (threshold: 1000ms) for %s/%s",
                        total * 1000,
                        trace.symbol,
                        trace.interval,
                    )
                    self._last_latency_warning = current_time
            
        except Exception as e:
            logger.error("Failed to write latency metrics: %s", e, exc_info=True)
    
    def get_stats(self) -> Dict[str, any]:
        """Get tracker statistics"""
        return {
            'active_traces': len(self._traces),
            'max_traces': self._max_traces,
            'total_traces_created': self._trace_count,
        }


# Global singleton instance
_global_tracker: Optional[LatencyTracker] = None


def get_latency_tracker() -> LatencyTracker:
    """Get or create global latency tracker instance"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = LatencyTracker()
    return _global_tracker
