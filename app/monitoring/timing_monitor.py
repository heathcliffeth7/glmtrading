"""
Cycle Timing Monitor

Tracks timing breakdown of each trading cycle for performance monitoring
and debugging. Helps identify bottlenecks and timing issues.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.utils.influx import write_measurement
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TimingMetric:
    """Single timing measurement for a cycle stage"""

    stage: str
    start_time: datetime
    end_time: datetime

    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds"""
        return (self.end_time - self.start_time).total_seconds() * 1000


class CycleTimingTracker:
    """
    Tracks timing breakdown of trading cycle

    Usage:
        tracker = CycleTimingTracker()
        tracker.start_cycle()

        t1 = datetime.utcnow()
        # ... do data collection ...
        t2 = datetime.utcnow()
        tracker.add_stage("data_collection", t1, t2)

        # ... more stages ...

        breakdown = tracker.finish_cycle()
        # Returns dict with timing for each stage
    """

    def __init__(self, symbol: str = "BTCUSDT", interval: str = "3min"):
        self._symbol = symbol
        self._interval = interval
        self._current_cycle: List[TimingMetric] = []
        self._cycle_start: Optional[datetime] = None

    def start_cycle(self):
        """Start tracking a new cycle"""
        self._cycle_start = datetime.now(timezone.utc)
        self._current_cycle = []
        logger.debug("🕐 Cycle timing started")

    def add_stage(self, stage: str, start: datetime, end: datetime):
        """
        Add timing for a cycle stage

        Args:
            stage: Stage name (e.g., "data_collection", "glm_evaluation")
            start: Stage start timestamp
            end: Stage end timestamp
        """
        metric = TimingMetric(stage, start, end)
        self._current_cycle.append(metric)
        logger.debug("  ⏱️ %s: %.0fms", stage, metric.duration_ms)

    def finish_cycle(self) -> Dict:
        """
        Finish cycle and return timing breakdown

        Returns:
            Dictionary with timing breakdown and total duration
        """
        if not self._cycle_start:
            logger.warning("Cycle not started, cannot finish")
            return {}

        cycle_end = datetime.now(timezone.utc)
        total_duration = (cycle_end - self._cycle_start).total_seconds() * 1000

        # Build breakdown
        breakdown = {metric.stage: metric.duration_ms for metric in self._current_cycle}

        # Calculate untracked time
        tracked_time = sum(breakdown.values())
        untracked_time = total_duration - tracked_time

        breakdown["total"] = total_duration
        breakdown["untracked"] = untracked_time

        # Write to InfluxDB for historical analysis
        try:
            write_measurement(
                "cycle_timing",
                tags={"symbol": self._symbol, "interval": self._interval, "type": "breakdown"},
                fields=breakdown,
                timestamp=cycle_end,
            )
        except Exception as e:
            logger.debug("Failed to write timing to InfluxDB: %s", e)

        # Log summary
        logger.info(
            "📊 Cycle timing breakdown (total: %.0fms):\n%s",
            total_duration,
            json.dumps(breakdown, indent=2),
        )

        # Warn on slow cycles
        if total_duration > 150_000:  # 2.5 minutes
            logger.error(
                "❌ Cycle took %.1fs - TOO SLOW! Risk missing next cycle", total_duration / 1000
            )
        elif total_duration > 120_000:  # 2 minutes
            logger.warning("⚠️ Cycle took %.1fs - approaching cycle limit", total_duration / 1000)

        return breakdown

    def get_stage_breakdown_summary(self) -> str:
        """
        Get human-readable stage breakdown summary

        Returns:
            Formatted string with stage timings
        """
        if not self._current_cycle:
            return "No stages recorded"

        lines = []
        for metric in self._current_cycle:
            lines.append(f"  • {metric.stage}: {metric.duration_ms:.0f}ms")

        return "\n".join(lines)
