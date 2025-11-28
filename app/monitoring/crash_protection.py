"""
Crash Protection System for Swing Trading

3-Tier crash detection with HTF confirmation:
- Seviye 1 (WARNING): 1 dakika drop + HTF check → Alert veya %50 azalt
- Seviye 2 (DANGER): 5 dakika drop → %50 azalt (HTF'e bakmadan)
- Seviye 3 (CRITICAL): 10 dakika drop → TÜM pozisyonları kapat

Real-time WebSocket tick-level monitoring (<5 saniye tepki süresi)
"""

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional, Tuple

from app.config.settings import get_settings
from app.utils.logging import get_logger


logger = get_logger(__name__)
settings = get_settings()


class CrashLevel(Enum):
    """Crash severity levels"""
    NONE = "none"
    WARNING = "warning"      # 1 dakika drop (HTF ile doğrulama)
    DANGER = "danger"        # 5 dakika drop (%50 azalt)
    CRITICAL = "critical"    # 10 dakika drop (TÜM kapat)


class CrashAction(Enum):
    """Actions to take on crash detection"""
    NONE = "none"
    ALERT_ONLY = "alert_only"
    REDUCE_50 = "reduce_50"
    CLOSE_ALL = "close_all"


@dataclass
class CrashThresholds:
    """Per-symbol crash detection thresholds (percentage)"""
    warning_1min: float   # 1 dakika drop threshold
    danger_5min: float    # 5 dakika drop threshold
    critical_10min: float # 10 dakika drop threshold


@dataclass
class CrashEvent:
    """Represents a detected crash event"""
    symbol: str
    level: CrashLevel
    action: CrashAction
    drop_pct: float
    timeframe: str  # "1min", "5min", "10min"
    htf_bearish: bool
    current_price: float
    peak_price: float
    timestamp: datetime


# Callback type for position close
ClosePositionCallback = Callable[[str, float, str, CrashAction], None]


class CrashProtectionHandler:
    """
    Real-time crash detection for a single symbol.
    Hooks into price_cache callbacks for tick-level monitoring.

    Detection is O(1) per tick using rolling price windows.
    """

    def __init__(
        self,
        symbol: str,
        close_position_callback: ClosePositionCallback,
        htf_data_getter: Optional[Callable[[str], Dict]] = None,
        thresholds: Optional[CrashThresholds] = None,
    ):
        self.symbol = symbol.upper()
        self.close_callback = close_position_callback
        self.htf_data_getter = htf_data_getter

        # Load thresholds from settings or use provided
        if thresholds:
            self.thresholds = thresholds
        else:
            self.thresholds = self._load_thresholds_from_settings()

        # Rolling price windows: (timestamp_ms, price)
        # 600 entries = 10 minutes at ~1 tick/second
        self.price_window: Deque[Tuple[int, float]] = deque(maxlen=600)

        # Peak price tracking for each window
        self._peak_1min: float = 0.0
        self._peak_5min: float = 0.0
        self._peak_10min: float = 0.0

        # Cooldown tracking
        self._last_critical_time: float = 0
        self._last_danger_time: Dict[str, float] = {}  # Per-symbol
        self._cooldown_critical_seconds = settings.crash_protection_cooldown_minutes * 60
        self._cooldown_danger_seconds = settings.crash_danger_cooldown_minutes * 60

        # Position state cache (updated by PositionMonitor)
        self._has_position: bool = False
        self._position_side: Optional[str] = None  # "LONG" or "SHORT"
        self._position_entry_price: float = 0.0
        self._position_quantity: float = 0.0

        # Statistics
        self._tick_count: int = 0
        self._last_crash_event: Optional[CrashEvent] = None

        logger.info(
            "CrashProtectionHandler initialized for %s | thresholds: 1min=%.1f%%, 5min=%.1f%%, 10min=%.1f%%",
            self.symbol,
            self.thresholds.warning_1min,
            self.thresholds.danger_5min,
            self.thresholds.critical_10min,
        )

    def _load_thresholds_from_settings(self) -> CrashThresholds:
        """Load symbol-specific thresholds from settings"""
        symbol_lower = self.symbol.lower().replace("usdt", "")

        warning = getattr(settings, f"crash_warning_1min_{symbol_lower}", 1.0)
        danger = getattr(settings, f"crash_danger_5min_{symbol_lower}", 3.0)
        critical = getattr(settings, f"crash_critical_10min_{symbol_lower}", 5.0)

        return CrashThresholds(
            warning_1min=warning,
            danger_5min=danger,
            critical_10min=critical,
        )

    def update_position_state(
        self,
        has_position: bool,
        position_side: Optional[str] = None,
        entry_price: float = 0.0,
        quantity: float = 0.0,
    ) -> None:
        """
        Update cached position state.
        Called by PositionMonitor to keep crash protection in sync.
        """
        self._has_position = has_position
        self._position_side = position_side
        self._position_entry_price = entry_price
        self._position_quantity = quantity

        logger.debug(
            "Position state updated for %s: has_position=%s, side=%s, entry=%.2f",
            self.symbol, has_position, position_side, entry_price
        )

    def on_price_update(self, symbol: str, price: float, timestamp: datetime) -> None:
        """
        Called on EVERY WebSocket tick via price_cache callback.
        MUST complete in <10ms for performance.
        """
        if symbol.upper() != self.symbol:
            return

        # Skip if no position to protect
        if not self._has_position:
            return

        # Skip if crash protection disabled
        if not settings.crash_protection_enabled:
            return

        self._tick_count += 1
        ts_ms = int(timestamp.timestamp() * 1000)

        # Add to price window
        self.price_window.append((ts_ms, price))

        # Update peak prices
        self._update_peaks(price)

        # Fast crash detection
        crash_event = self._detect_crash(price, ts_ms)

        if crash_event:
            self._handle_crash(crash_event)

    def _update_peaks(self, price: float) -> None:
        """Update peak prices for each window"""
        # Simple approach: always update if higher
        if price > self._peak_1min:
            self._peak_1min = price
        if price > self._peak_5min:
            self._peak_5min = price
        if price > self._peak_10min:
            self._peak_10min = price

    def _detect_crash(self, current_price: float, current_ts_ms: int) -> Optional[CrashEvent]:
        """
        Fast crash detection using peak prices.
        Returns CrashEvent if crash detected, None otherwise.
        """
        if len(self.price_window) < 10:  # Need minimum history
            return None

        # Calculate drops from peaks
        drop_1min = self._calculate_drop(current_price, "1min", current_ts_ms)
        drop_5min = self._calculate_drop(current_price, "5min", current_ts_ms)
        drop_10min = self._calculate_drop(current_price, "10min", current_ts_ms)

        # For LONG positions, we care about price drops
        # For SHORT positions, we care about price spikes
        is_long = self._position_side == "LONG"

        # Check CRITICAL first (highest priority)
        critical_threshold = self.thresholds.critical_10min
        if (is_long and drop_10min >= critical_threshold) or \
           (not is_long and drop_10min <= -critical_threshold):

            if self._is_in_cooldown(CrashLevel.CRITICAL):
                return None

            return CrashEvent(
                symbol=self.symbol,
                level=CrashLevel.CRITICAL,
                action=CrashAction.CLOSE_ALL,
                drop_pct=abs(drop_10min),
                timeframe="10min",
                htf_bearish=True,  # Always true for critical
                current_price=current_price,
                peak_price=self._peak_10min,
                timestamp=datetime.now(timezone.utc),
            )

        # Check DANGER
        danger_threshold = self.thresholds.danger_5min
        if (is_long and drop_5min >= danger_threshold) or \
           (not is_long and drop_5min <= -danger_threshold):

            if self._is_in_cooldown(CrashLevel.DANGER):
                return None

            return CrashEvent(
                symbol=self.symbol,
                level=CrashLevel.DANGER,
                action=CrashAction.REDUCE_50,
                drop_pct=abs(drop_5min),
                timeframe="5min",
                htf_bearish=True,  # Not checked for danger
                current_price=current_price,
                peak_price=self._peak_5min,
                timestamp=datetime.now(timezone.utc),
            )

        # Check WARNING (requires HTF confirmation)
        warning_threshold = self.thresholds.warning_1min
        if (is_long and drop_1min >= warning_threshold) or \
           (not is_long and drop_1min <= -warning_threshold):

            # Get HTF trend
            htf_bearish = self._check_htf_bearish()

            # Determine action based on HTF
            if htf_bearish:
                action = CrashAction.REDUCE_50  # HTF bearish → serious
            else:
                action = CrashAction.ALERT_ONLY  # HTF bullish → just alert

            return CrashEvent(
                symbol=self.symbol,
                level=CrashLevel.WARNING,
                action=action,
                drop_pct=abs(drop_1min),
                timeframe="1min",
                htf_bearish=htf_bearish,
                current_price=current_price,
                peak_price=self._peak_1min,
                timestamp=datetime.now(timezone.utc),
            )

        return None

    def _calculate_drop(self, current_price: float, timeframe: str, current_ts_ms: int) -> float:
        """
        Calculate drop percentage from peak in given timeframe.
        Positive = price dropped (for LONG)
        Negative = price spiked (for SHORT)
        """
        if timeframe == "1min":
            window_ms = 60_000
            peak = self._get_peak_in_window(current_ts_ms, window_ms)
            self._peak_1min = peak if peak > 0 else self._peak_1min
        elif timeframe == "5min":
            window_ms = 300_000
            peak = self._get_peak_in_window(current_ts_ms, window_ms)
            self._peak_5min = peak if peak > 0 else self._peak_5min
        else:  # 10min
            window_ms = 600_000
            peak = self._get_peak_in_window(current_ts_ms, window_ms)
            self._peak_10min = peak if peak > 0 else self._peak_10min

        if peak <= 0:
            return 0.0

        # Calculate drop (positive = price went down)
        drop_pct = ((peak - current_price) / peak) * 100
        return drop_pct

    def _get_peak_in_window(self, current_ts_ms: int, window_ms: int) -> float:
        """Get peak price within the time window"""
        cutoff_ts = current_ts_ms - window_ms

        peak = 0.0
        for ts, price in self.price_window:
            if ts >= cutoff_ts:
                if price > peak:
                    peak = price

        return peak

    def _check_htf_bearish(self) -> bool:
        """
        Check if HTF (30m) trend is bearish.
        EMA20 < EMA50 = Bearish
        """
        if not self.htf_data_getter:
            return False  # Can't check, assume bullish (safer)

        try:
            htf_data = self.htf_data_getter(self.symbol)
            if not htf_data:
                return False

            ema20 = htf_data.get("ema_20", 0)
            ema50 = htf_data.get("ema_50", 0)

            if ema20 > 0 and ema50 > 0:
                is_bearish = ema20 < ema50
                logger.debug(
                    "HTF check for %s: EMA20=%.2f, EMA50=%.2f, bearish=%s",
                    self.symbol, ema20, ema50, is_bearish
                )
                return is_bearish

        except Exception as e:
            logger.error("HTF check failed for %s: %s", self.symbol, e)

        return False

    def _is_in_cooldown(self, level: CrashLevel) -> bool:
        """Check if we're in cooldown for this crash level"""
        current_time = time.time()

        if level == CrashLevel.CRITICAL:
            if current_time - self._last_critical_time < self._cooldown_critical_seconds:
                logger.debug(
                    "CRITICAL cooldown active for %s (%.1fs remaining)",
                    self.symbol,
                    self._cooldown_critical_seconds - (current_time - self._last_critical_time)
                )
                return True

        elif level == CrashLevel.DANGER:
            last_danger = self._last_danger_time.get(self.symbol, 0)
            if current_time - last_danger < self._cooldown_danger_seconds:
                logger.debug(
                    "DANGER cooldown active for %s (%.1fs remaining)",
                    self.symbol,
                    self._cooldown_danger_seconds - (current_time - last_danger)
                )
                return True

        return False

    def _handle_crash(self, event: CrashEvent) -> None:
        """Handle detected crash event"""
        self._last_crash_event = event

        # Update cooldowns
        if event.level == CrashLevel.CRITICAL:
            self._last_critical_time = time.time()
        elif event.level == CrashLevel.DANGER:
            self._last_danger_time[self.symbol] = time.time()

        # Log the event
        level_emoji = {
            CrashLevel.WARNING: "⚠️",
            CrashLevel.DANGER: "🟠",
            CrashLevel.CRITICAL: "🔴",
        }

        logger.critical(
            "%s CRASH DETECTED %s: %.2f%% drop in %s | HTF bearish=%s | Action=%s | Price: $%.2f → $%.2f",
            level_emoji.get(event.level, "❓"),
            event.symbol,
            event.drop_pct,
            event.timeframe,
            event.htf_bearish,
            event.action.value,
            event.peak_price,
            event.current_price,
        )

        # Execute action via callback
        if event.action != CrashAction.NONE:
            reason = f"CRASH PROTECTION: {event.drop_pct:.1f}% drop in {event.timeframe}"
            if event.level == CrashLevel.WARNING:
                reason += f" (HTF bearish={event.htf_bearish})"

            try:
                self.close_callback(
                    event.symbol,
                    event.current_price,
                    reason,
                    event.action,
                )
            except Exception as e:
                logger.error("Failed to execute crash protection action: %s", e)

        # Reset peak prices after crash detection
        self._reset_peaks()

    def _reset_peaks(self) -> None:
        """Reset peak prices after crash event"""
        if self.price_window:
            latest_price = self.price_window[-1][1]
            self._peak_1min = latest_price
            self._peak_5min = latest_price
            self._peak_10min = latest_price

    def get_status(self) -> dict:
        """Get crash protection status for monitoring"""
        return {
            "symbol": self.symbol,
            "enabled": settings.crash_protection_enabled,
            "has_position": self._has_position,
            "position_side": self._position_side,
            "tick_count": self._tick_count,
            "price_window_size": len(self.price_window),
            "thresholds": {
                "warning_1min": self.thresholds.warning_1min,
                "danger_5min": self.thresholds.danger_5min,
                "critical_10min": self.thresholds.critical_10min,
            },
            "peaks": {
                "1min": self._peak_1min,
                "5min": self._peak_5min,
                "10min": self._peak_10min,
            },
            "last_crash_event": {
                "level": self._last_crash_event.level.value if self._last_crash_event else None,
                "drop_pct": self._last_crash_event.drop_pct if self._last_crash_event else None,
                "timestamp": self._last_crash_event.timestamp.isoformat() if self._last_crash_event else None,
            } if self._last_crash_event else None,
        }


class CrashProtectionManager:
    """
    Manages crash protection handlers for all symbols.
    Provides centralized control and status reporting.
    """

    def __init__(
        self,
        symbols: List[str],
        close_position_callback: ClosePositionCallback,
        htf_data_getter: Optional[Callable[[str], Dict]] = None,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.close_callback = close_position_callback
        self.htf_data_getter = htf_data_getter

        # Create handlers for each symbol
        self.handlers: Dict[str, CrashProtectionHandler] = {}
        for symbol in self.symbols:
            self.handlers[symbol] = CrashProtectionHandler(
                symbol=symbol,
                close_position_callback=close_position_callback,
                htf_data_getter=htf_data_getter,
            )

        # Global cooldown state
        self._global_cooldown_until: float = 0

        logger.info(
            "CrashProtectionManager initialized for %d symbols: %s",
            len(self.symbols),
            ", ".join(self.symbols)
        )

    def get_handler(self, symbol: str) -> Optional[CrashProtectionHandler]:
        """Get handler for a specific symbol"""
        return self.handlers.get(symbol.upper())

    def update_position_state(
        self,
        symbol: str,
        has_position: bool,
        position_side: Optional[str] = None,
        entry_price: float = 0.0,
        quantity: float = 0.0,
    ) -> None:
        """Update position state for a symbol"""
        handler = self.get_handler(symbol)
        if handler:
            handler.update_position_state(
                has_position=has_position,
                position_side=position_side,
                entry_price=entry_price,
                quantity=quantity,
            )

    def is_in_global_cooldown(self) -> bool:
        """Check if system is in global cooldown (after CRITICAL event)"""
        return time.time() < self._global_cooldown_until

    def set_global_cooldown(self) -> None:
        """Set global cooldown (called after CRITICAL event closes all positions)"""
        cooldown_seconds = settings.crash_protection_cooldown_minutes * 60
        self._global_cooldown_until = time.time() + cooldown_seconds

        logger.warning(
            "🚫 GLOBAL COOLDOWN ACTIVATED: No new positions for %d minutes",
            settings.crash_protection_cooldown_minutes
        )

    def get_all_status(self) -> Dict[str, dict]:
        """Get status of all handlers"""
        return {
            symbol: handler.get_status()
            for symbol, handler in self.handlers.items()
        }

    def register_with_price_cache(self) -> None:
        """Register all handlers with price_cache for real-time updates"""
        from app.utils.price_cache import price_cache

        for symbol, handler in self.handlers.items():
            price_cache.register_price_callback(symbol, handler.on_price_update)
            logger.info("✅ Registered crash protection callback for %s", symbol)
