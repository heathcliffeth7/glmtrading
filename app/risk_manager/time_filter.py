"""
Time-Based Filter - Session detection and trading time management.

Features:
- Trading session detection (Asia/Europe/US)
- News event avoidance
- Low liquidity period warnings
- Weekend/holiday detection
- Optimal trading hours identification

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, time, timedelta
import logging

logger = logging.getLogger(__name__)


class TradingSession(Enum):
    ASIA = "asia"
    EUROPE = "europe"
    US = "us"
    OVERLAP_ASIA_EUROPE = "asia_europe_overlap"
    OVERLAP_EUROPE_US = "europe_us_overlap"
    OFF_HOURS = "off_hours"


class SessionQuality(Enum):
    OPTIMAL = "optimal"
    GOOD = "good"
    AVERAGE = "average"
    POOR = "poor"
    AVOID = "avoid"


@dataclass
class TimeFilterResult:
    """Result from time filter analysis"""
    can_trade: bool
    session: TradingSession
    quality: SessionQuality
    warnings: List[str]
    recommendations: List[str]
    hours_until_optimal: Optional[float] = None
    feature_enabled: bool = True


class TimeBasedFilter:
    """
    Time-based trading filter with session awareness.

    Sessions (UTC):
    - Asia: 00:00 - 09:00
    - Europe: 07:00 - 16:00
    - US: 13:00 - 22:00
    - Asia/Europe Overlap: 07:00 - 09:00
    - Europe/US Overlap: 13:00 - 16:00

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Session times in UTC
    SESSIONS = {
        TradingSession.ASIA: (time(0, 0), time(9, 0)),
        TradingSession.EUROPE: (time(7, 0), time(16, 0)),
        TradingSession.US: (time(13, 0), time(22, 0)),
    }

    # Overlap sessions have higher liquidity
    OVERLAPS = {
        TradingSession.OVERLAP_ASIA_EUROPE: (time(7, 0), time(9, 0)),
        TradingSession.OVERLAP_EUROPE_US: (time(13, 0), time(16, 0)),
    }

    # Mode-specific configurations
    MODE_CONFIGS = {
        "scalp": {
            "avoid_low_liquidity": True,
            "require_session_overlap": False,
            "optimal_sessions": [
                TradingSession.OVERLAP_EUROPE_US,
                TradingSession.OVERLAP_ASIA_EUROPE,
                TradingSession.US,
                TradingSession.EUROPE,
            ],
            "avoid_sessions": [],  # Scalp can trade in any session
            "avoid_hours": [(22, 0), (23, 0)],  # Avoid late hours
            "news_buffer_minutes": 30,
        },
        "swing": {
            "avoid_low_liquidity": False,  # Swing doesn't care as much
            "require_session_overlap": False,
            "optimal_sessions": [
                TradingSession.US,
                TradingSession.EUROPE,
                TradingSession.OVERLAP_EUROPE_US,
            ],
            "avoid_sessions": [],
            "avoid_hours": [],
            "news_buffer_minutes": 60,
        }
    }

    # Major economic news events (day of week: hour in UTC)
    # 0 = Monday, 4 = Friday
    MAJOR_NEWS_TIMES = [
        # US NFP - First Friday of month at 13:30 UTC
        {"day": 4, "hour": 13, "minute": 30, "name": "US NFP", "first_week": True},
        # FOMC - Various Wednesdays at 19:00 UTC
        {"day": 2, "hour": 19, "minute": 0, "name": "FOMC", "fomc_week": True},
        # CPI - Usually middle of month
        {"day": None, "hour": 13, "minute": 30, "name": "US CPI"},
    ]

    # Low liquidity periods (UTC hours)
    LOW_LIQUIDITY_HOURS = [22, 23, 0, 1, 2, 3, 4, 5]

    def __init__(
        self,
        mode: str = "scalp",
        custom_avoid_hours: Optional[List[Tuple[int, int]]] = None,
        feature_enabled: bool = True,
    ):
        """
        Initialize TimeBasedFilter.

        Args:
            mode: Trading mode - "scalp" or "swing"
            custom_avoid_hours: Optional custom hours to avoid [(hour, minute), ...]
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        self.config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])
        self.custom_avoid_hours = custom_avoid_hours or []

        logger.info(
            "TimeBasedFilter initialized | Mode: %s | Avoid Low Liquidity: %s | Enabled: %s",
            mode, self.config["avoid_low_liquidity"], feature_enabled
        )

    def analyze(self, timestamp: Optional[datetime] = None) -> TimeFilterResult:
        """
        Analyze if current time is suitable for trading.

        Args:
            timestamp: Optional timestamp to analyze (defaults to now)

        Returns:
            TimeFilterResult with analysis
        """
        if not self.feature_enabled:
            return TimeFilterResult(
                can_trade=True,
                session=TradingSession.OFF_HOURS,
                quality=SessionQuality.AVERAGE,
                warnings=["Time filter disabled"],
                recommendations=[],
                feature_enabled=False,
            )

        if timestamp is None:
            timestamp = datetime.utcnow()

        warnings = []
        recommendations = []

        # Detect current session
        current_session = self._detect_session(timestamp)

        # Check for overlaps (higher priority)
        overlap_session = self._detect_overlap(timestamp)
        if overlap_session:
            current_session = overlap_session

        # Determine quality
        quality = self._assess_quality(timestamp, current_session)

        # Check weekend
        if timestamp.weekday() >= 5:  # Saturday or Sunday
            quality = SessionQuality.POOR
            warnings.append("Weekend - reduced crypto liquidity")
            recommendations.append("Consider reducing position sizes")

        # Check low liquidity hours
        if timestamp.hour in self.LOW_LIQUIDITY_HOURS:
            if self.config["avoid_low_liquidity"]:
                quality = SessionQuality.POOR
                warnings.append(f"Low liquidity period ({timestamp.hour}:00 UTC)")

        # Check avoid hours
        for avoid_hour, avoid_minute in self.config["avoid_hours"]:
            if timestamp.hour == avoid_hour:
                quality = SessionQuality.AVOID
                warnings.append(f"Configured avoid hour: {avoid_hour}:00 UTC")

        # Check custom avoid hours
        for avoid_hour, avoid_minute in self.custom_avoid_hours:
            if timestamp.hour == avoid_hour and timestamp.minute >= avoid_minute:
                quality = SessionQuality.AVOID
                warnings.append(f"Custom avoid hour: {avoid_hour}:{avoid_minute:02d} UTC")

        # Check for major news events
        news_warning = self._check_news_events(timestamp)
        if news_warning:
            warnings.append(news_warning)
            quality = SessionQuality.AVOID

        # Generate recommendations
        if quality in [SessionQuality.POOR, SessionQuality.AVOID]:
            hours_until = self._hours_until_optimal(timestamp)
            if hours_until:
                recommendations.append(f"Optimal trading in {hours_until:.1f} hours")

        if current_session in [TradingSession.OVERLAP_EUROPE_US, TradingSession.OVERLAP_ASIA_EUROPE]:
            recommendations.append("Session overlap - high liquidity period")

        # Final decision
        can_trade = quality not in [SessionQuality.AVOID]

        return TimeFilterResult(
            can_trade=can_trade,
            session=current_session,
            quality=quality,
            warnings=warnings,
            recommendations=recommendations,
            hours_until_optimal=self._hours_until_optimal(timestamp) if not can_trade else None,
            feature_enabled=True,
        )

    def _detect_session(self, timestamp: datetime) -> TradingSession:
        """Detect which trading session is active"""
        current_time = timestamp.time()

        for session, (start, end) in self.SESSIONS.items():
            if start <= current_time < end:
                return session

        # Check for wrap-around (Asia session crosses midnight)
        if current_time >= time(22, 0) or current_time < time(9, 0):
            return TradingSession.ASIA

        return TradingSession.OFF_HOURS

    def _detect_overlap(self, timestamp: datetime) -> Optional[TradingSession]:
        """Detect if we're in an overlap session"""
        current_time = timestamp.time()

        for overlap, (start, end) in self.OVERLAPS.items():
            if start <= current_time < end:
                return overlap

        return None

    def _assess_quality(self, timestamp: datetime, session: TradingSession) -> SessionQuality:
        """Assess trading quality for current session"""
        # Overlaps are optimal
        if session in [TradingSession.OVERLAP_EUROPE_US, TradingSession.OVERLAP_ASIA_EUROPE]:
            return SessionQuality.OPTIMAL

        # Check if session is in optimal list
        if session in self.config["optimal_sessions"]:
            return SessionQuality.GOOD

        # Check if session is in avoid list
        if session in self.config["avoid_sessions"]:
            return SessionQuality.AVOID

        # Off hours
        if session == TradingSession.OFF_HOURS:
            return SessionQuality.POOR

        return SessionQuality.AVERAGE

    def _check_news_events(self, timestamp: datetime) -> Optional[str]:
        """Check if near major news event"""
        buffer_minutes = self.config["news_buffer_minutes"]

        # Check for monthly events (simplified check)
        day_of_week = timestamp.weekday()
        hour = timestamp.hour
        minute = timestamp.minute

        for news in self.MAJOR_NEWS_TIMES:
            # Skip if day doesn't match
            if news.get("day") is not None and news["day"] != day_of_week:
                continue

            # Check time proximity
            news_time = hour * 60 + minute
            event_time = news["hour"] * 60 + news["minute"]

            if abs(news_time - event_time) <= buffer_minutes:
                return f"Near {news['name']} release - high volatility expected"

        return None

    def _hours_until_optimal(self, timestamp: datetime) -> Optional[float]:
        """Calculate hours until next optimal trading period"""
        current_time = timestamp.time()

        # Europe/US overlap starts at 13:00 UTC
        optimal_start = time(13, 0)

        if current_time < optimal_start:
            # Today
            diff = datetime.combine(timestamp.date(), optimal_start) - timestamp
            return diff.total_seconds() / 3600
        elif current_time >= time(22, 0):
            # Tomorrow
            tomorrow = timestamp.date() + timedelta(days=1)
            diff = datetime.combine(tomorrow, optimal_start) - timestamp
            return diff.total_seconds() / 3600
        else:
            return None

    def get_session_info(self, timestamp: Optional[datetime] = None) -> Dict:
        """Get detailed session information"""
        if timestamp is None:
            timestamp = datetime.utcnow()

        if not self.feature_enabled:
            return {"feature_enabled": False}

        current_session = self._detect_session(timestamp)
        overlap = self._detect_overlap(timestamp)

        return {
            "timestamp_utc": timestamp.isoformat(),
            "day_of_week": timestamp.strftime("%A"),
            "current_session": current_session.value,
            "is_overlap": overlap.value if overlap else None,
            "is_weekend": timestamp.weekday() >= 5,
            "is_low_liquidity": timestamp.hour in self.LOW_LIQUIDITY_HOURS,
            "active_sessions": self._get_active_sessions(timestamp),
            "feature_enabled": True,
        }

    def _get_active_sessions(self, timestamp: datetime) -> List[str]:
        """Get all active sessions at current time"""
        active = []
        current_time = timestamp.time()

        for session, (start, end) in self.SESSIONS.items():
            if start <= current_time < end:
                active.append(session.value)

        return active

    def get_prompt_section(self, timestamp: Optional[datetime] = None) -> str:
        """Generate prompt section for GLM"""
        if not self.feature_enabled:
            return ""

        result = self.analyze(timestamp)

        if not result.feature_enabled:
            return ""

        lines = [
            "",
            "=" * 60,
            "TIME-BASED ANALYSIS",
            "=" * 60,
            "",
            f"Current Session: {result.session.value.upper()}",
            f"Trading Quality: {result.quality.value.upper()}",
            f"Can Trade: {'YES' if result.can_trade else 'NO'}",
        ]

        if result.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in result.warnings:
                lines.append(f"  - {w}")

        if result.recommendations:
            lines.append("")
            lines.append("Recommendations:")
            for r in result.recommendations:
                lines.append(f"  - {r}")

        if result.hours_until_optimal:
            lines.append("")
            lines.append(f"Hours until optimal: {result.hours_until_optimal:.1f}")

        return "\n".join(lines)

    def should_reduce_size(self, timestamp: Optional[datetime] = None) -> Tuple[bool, float]:
        """
        Check if position size should be reduced.

        Returns:
            Tuple of (should_reduce, multiplier)
        """
        if not self.feature_enabled:
            return False, 1.0

        result = self.analyze(timestamp)

        if result.quality == SessionQuality.OPTIMAL:
            return False, 1.0
        elif result.quality == SessionQuality.GOOD:
            return False, 1.0
        elif result.quality == SessionQuality.AVERAGE:
            return True, 0.8
        elif result.quality == SessionQuality.POOR:
            return True, 0.5
        else:  # AVOID
            return True, 0.25

    def get_trade_filter(self, timestamp: Optional[datetime] = None) -> Dict:
        """
        Get trade filter decision.

        Returns:
            Dict with filter decision and reasoning
        """
        if not self.feature_enabled:
            return {
                "allow_trade": True,
                "reason": "Time filter disabled",
                "size_multiplier": 1.0,
                "feature_enabled": False,
            }

        result = self.analyze(timestamp)
        should_reduce, multiplier = self.should_reduce_size(timestamp)

        return {
            "allow_trade": result.can_trade,
            "session": result.session.value,
            "quality": result.quality.value,
            "size_multiplier": multiplier,
            "warnings": result.warnings,
            "recommendations": result.recommendations,
            "feature_enabled": True,
        }

    def get_best_trading_times(self) -> List[Dict]:
        """Get recommended trading times for current mode"""
        if not self.feature_enabled:
            return []

        times = []

        if self.mode == "scalp":
            times = [
                {
                    "session": "Europe/US Overlap",
                    "utc_hours": "13:00 - 16:00",
                    "quality": "Optimal",
                    "reason": "Highest liquidity, best spreads",
                },
                {
                    "session": "US Session",
                    "utc_hours": "16:00 - 22:00",
                    "quality": "Good",
                    "reason": "High volume, good trends",
                },
                {
                    "session": "Asia/Europe Overlap",
                    "utc_hours": "07:00 - 09:00",
                    "quality": "Good",
                    "reason": "Fresh momentum from Asia",
                },
            ]
        else:  # swing
            times = [
                {
                    "session": "US Session Open",
                    "utc_hours": "13:00 - 16:00",
                    "quality": "Optimal",
                    "reason": "Major moves, clear direction",
                },
                {
                    "session": "Europe Session",
                    "utc_hours": "07:00 - 13:00",
                    "quality": "Good",
                    "reason": "Good for swing entries",
                },
            ]

        return times

    def get_summary(self) -> Dict:
        """Get summary of time filter configuration"""
        return {
            "mode": self.mode,
            "feature_enabled": self.feature_enabled,
            "avoid_low_liquidity": self.config["avoid_low_liquidity"],
            "optimal_sessions": [s.value for s in self.config["optimal_sessions"]],
            "news_buffer_minutes": self.config["news_buffer_minutes"],
            "custom_avoid_hours": self.custom_avoid_hours,
        }


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    time_filter = TimeBasedFilter(mode="scalp")

    # Analyze current time
    result = time_filter.analyze()

    print("Time Analysis:")
    print(f"  Session: {result.session.value}")
    print(f"  Quality: {result.quality.value}")
    print(f"  Can Trade: {result.can_trade}")

    if result.warnings:
        print("\nWarnings:")
        for w in result.warnings:
            print(f"  - {w}")

    if result.recommendations:
        print("\nRecommendations:")
        for r in result.recommendations:
            print(f"  - {r}")

    # Test session info
    print("\n--- Session Info ---")
    info = time_filter.get_session_info()
    print(f"  Current Session: {info['current_session']}")
    print(f"  Day: {info['day_of_week']}")
    print(f"  Weekend: {info['is_weekend']}")
    print(f"  Low Liquidity: {info['is_low_liquidity']}")

    # Test specific time
    print("\n--- Testing US/Europe Overlap (14:00 UTC) ---")
    test_time = datetime.utcnow().replace(hour=14, minute=0)
    result = time_filter.analyze(test_time)
    print(f"  Quality: {result.quality.value}")
    print(f"  Session: {result.session.value}")

    print("\n--- Prompt Section ---")
    print(time_filter.get_prompt_section())

    # Best trading times
    print("\n--- Best Trading Times ---")
    for t in time_filter.get_best_trading_times():
        print(f"  {t['session']}: {t['utc_hours']} - {t['quality']}")
