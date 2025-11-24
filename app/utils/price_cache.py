from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import redis

from app.config.settings import get_settings
from app.data_feeds.constants import KLINE_CHANNEL
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)


@dataclass
class PriceSnapshot:
    """Enhanced price snapshot with metadata and validation"""
    price: float
    timestamp: datetime
    source: str
    
    def age_seconds(self) -> float:
        """How old is this price data?"""
        return (datetime.now(timezone.utc) - self.timestamp).total_seconds()
    
    def is_stale(self, max_age_seconds: int = 30) -> bool:
        """Check if price is older than acceptable threshold (30s for crypto markets)"""
        return self.age_seconds() > max_age_seconds
    
    def is_valid(self, symbol: str = "", last_valid_price: Optional[float] = None) -> bool:
        """
        Validate price with multi-layer checks:
        1. Symbol-specific range validation
        2. Sudden spike detection (5% threshold)
        """
        # Layer 1: Symbol-specific reasonable ranges - SPOT market
        price_ranges = {
            "SOLUSDT": (70.0, 250.0),       # SOL SPOT: $70 - $250
            "ETHUSDT": (1500.0, 5000.0),    # ETH SPOT: $1500 - $5000
            "BTCUSDT": (70000.0, 120000.0), # BTC SPOT: $70k - $120k
        }
        
        # Get range for this symbol (default to wide range)
        min_price, max_price = price_ranges.get(symbol.upper(), (0.01, 1_000_000.0))
        
        if not (min_price <= self.price <= max_price):
            logger.debug(
                "❌ Price out of range for %s: %.2f not in [%.2f, %.2f]",
                symbol, self.price, min_price, max_price
            )
            return False
        
        # Layer 2: Enhanced spike detection with detailed logging
        if last_valid_price and last_valid_price > 0:
            change_pct = abs(self.price - last_valid_price) / last_valid_price
            max_change_pct = settings.binance.websocket.max_price_change_pct  # 1% from settings

            if change_pct > max_change_pct:
                # 🚨 Enhanced spike detection with detailed diagnostics
                logger.warning(
                    "❌ SPIKE DETECTED %s: %.2f%% change (%.2f → %.2f) > %.1f%% | source: %s | age: %.1fs | impact: %s",
                    symbol,
                    change_pct * 100,
                    last_valid_price,
                    self.price,
                    max_change_pct * 100,
                    self.source,
                    self.age_seconds(),
                    "EXTREME" if change_pct > 0.10 else "MODERATE"
                )

                # Categorize spike severity for better monitoring
                if change_pct > 0.10:  # 10% threshold - EXTREME
                    logger.error(
                        "🚨 EXTREME SPIKE ALERT %s: %.2f%% jump - SUSPICIOUS DATA SOURCE: %s | may indicate data corruption",
                        symbol, change_pct * 100, self.source
                    )
                elif change_pct > 0.05:  # 5% threshold - HIGH
                    logger.warning(
                        "⚠️ HIGH VOLATILITY %s: %.2f%% spike - requires monitoring | source: %s",
                        symbol, change_pct * 100, self.source
                    )
                else:  # 1-5% threshold - MODERATE
                    logger.info(
                        "📊 VOLATILE MOVEMENT %s: %.2f%% change - within acceptable range | source: %s",
                        symbol, change_pct * 100, self.source
                    )

                return False
        
        return True


class EnhancedPriceCache:
    """
    Thread-safe price cache with staleness detection and validation
    
    Replaces simple PriceCache with enhanced features:
    - Staleness detection (default 10s)
    - Price validation (range checks)
    - Source tracking (websocket, REST API, etc.)
    - Thread safety with locks
    - Spike detection (5% threshold)
    """
    
    def __init__(self):
        self._snapshots: Dict[str, PriceSnapshot] = {}
        self._lock = threading.Lock()
        self._last_valid_prices: Dict[str, float] = {}  # Track last valid price per symbol

        # 🎯 SOURCE PRIORITY SYSTEM - prevents race conditions
        self._source_priority = {
            "binance_websocket": 1,      # Highest priority - real-time
            "initial_rest": 2,           # Initial REST price
            "rest_api": 3,               # REST API fallback
            "rest_fallback": 4,          # REST fallback
            "influx_fallback": 5,        # InfluxDB fallback
            "unknown": 10                # Lowest priority
        }
    
    def set(self, symbol: str, price: float, source: str = "unknown") -> None:
        """
        Set price with enhanced validation (spike detection + range check)
        
        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            price: Price value
            source: Data source (e.g., "websocket", "binance_rest")
        """
        if price <= 0:
            logger.debug("Ignoring invalid price: %.2f for %s", price, symbol)
            return
        
        symbol_upper = symbol.upper()
        
        # Get last valid price for spike detection
        last_valid = self._last_valid_prices.get(symbol_upper)
        
        snapshot = PriceSnapshot(price, datetime.now(timezone.utc), source)
        
        # Enhanced validation: symbol-specific range + spike detection
        if not snapshot.is_valid(symbol=symbol_upper, last_valid_price=last_valid):
            logger.debug(
                "⚠️ Price validation failed for %s: $%.2f from %s (last_valid: $%.2f)",
                symbol_upper, price, source, last_valid or 0
            )
            return
        
        with self._lock:
            current_snapshot = self._snapshots.get(symbol_upper)
            current_priority = self._source_priority.get(current_snapshot.source, 10) if current_snapshot else 10
            new_priority = self._source_priority.get(source, 10)

            # 🎯 SOURCE PRIORITY CHECK - only update if new source has higher priority
            if current_snapshot and new_priority > current_priority:
                logger.debug(
                    "⏭️ Skipping lower priority update for %s: $%.2f from %s (priority %d) vs current $%.2f from %s (priority %d)",
                    symbol_upper, price, source, new_priority,
                    current_snapshot.price, current_snapshot.source, current_priority
                )
                return

            # Accept update (higher priority or no existing data)
            self._snapshots[symbol_upper] = snapshot
            self._last_valid_prices[symbol_upper] = price  # Update last valid

            # Log priority changes
            if current_snapshot and new_priority < current_priority:
                logger.info(
                    "🔄 Higher priority update for %s: $%.2f from %s (priority %d) replaced $%.2f from %s (priority %d)",
                    symbol_upper, price, source, new_priority,
                    current_snapshot.price, current_snapshot.source, current_priority
                )
            else:
                logger.debug(
                    "✅ Price updated for %s: $%.2f from %s (priority %d)",
                    symbol_upper, price, source, new_priority
                )
    
    def get(self, symbol: str, max_age_seconds: Optional[int] = None) -> Optional[float]:
        """
        Get price only if fresh enough (with symbol-aware threshold)
        
        Args:
            symbol: Trading symbol
            max_age_seconds: Maximum acceptable age (default: symbol-specific from settings)
        
        Returns:
            Price if available and fresh, None otherwise
        """
        # Use symbol-specific threshold from settings if not explicitly provided
        if max_age_seconds is None:
            from app.config.settings import get_settings
            settings = get_settings()
            symbol_upper = symbol.upper()
            max_age_seconds = settings.price_freshness_thresholds.get(
                symbol_upper, 
                settings.price_freshness_thresholds.get("default", 10)
            )
            logger.debug(
                "Using symbol-specific freshness threshold for %s: %ds",
                symbol_upper, max_age_seconds
            )
        
        with self._lock:
            snapshot = self._snapshots.get(symbol.upper())
        
        if not snapshot:
            logger.warning("⚠️ No price cached for %s", symbol)
            return None
        
        if snapshot.is_stale(max_age_seconds):
            logger.warning(
                "⚠️ Price for %s is stale: %.1fs old (max: %ds, source: %s)",
                symbol, snapshot.age_seconds(), max_age_seconds, snapshot.source
            )
            return None
        
        if not snapshot.is_valid():
            logger.error(
                "❌ Invalid price for %s: $%.2f (source: %s)",
                symbol, snapshot.price, snapshot.source
            )
            return None
        
        return snapshot.price
    
    def get_snapshot(self, symbol: str) -> Optional[PriceSnapshot]:
        """Get full snapshot with metadata"""
        with self._lock:
            return self._snapshots.get(symbol.upper())
    
    def get_age_seconds(self, symbol: str) -> float:
        """Get age of cached price in seconds"""
        snapshot = self.get_snapshot(symbol)
        if snapshot:
            return snapshot.age_seconds()
        return 999999.0

    def get_price_change_stats(self, symbol: str) -> Optional[dict]:
        """
        Get comprehensive price change statistics for monitoring

        Returns:
            dict with price_change_pct, age_seconds, source, last_valid_price, etc.
        """
        symbol_upper = symbol.upper()
        snapshot = self.get_snapshot(symbol_upper)
        last_valid = self._last_valid_prices.get(symbol_upper)

        if not snapshot:
            return None

        price_change_pct = 0.0
        if last_valid and last_valid > 0:
            price_change_pct = abs(snapshot.price - last_valid) / last_valid * 100

        return {
            "symbol": symbol_upper,
            "current_price": snapshot.price,
            "last_valid_price": last_valid or 0.0,
            "price_change_pct": price_change_pct,
            "age_seconds": snapshot.age_seconds(),
            "source": snapshot.source,
            "is_stale": snapshot.is_stale(),
            "source_priority": self._source_priority.get(snapshot.source, 10)
        }

    def force_reset(self, symbol: str, new_price: float, source: str = "reset") -> None:
        """
        Force reset cached price regardless of validation

        Args:
            symbol: Trading symbol
            new_price: New price to set
            source: Source of the reset
        """
        logger.warning(
            "🔄 FORCE RESET %s: Setting price to %.2f from %s",
            symbol, new_price, source
        )

        snapshot = PriceSnapshot(new_price, datetime.now(timezone.utc), source)

        with self._lock:
            self._snapshots[symbol.upper()] = snapshot

    def update_rest_price(self, symbol: str, rest_price: float) -> None:
        """
        Update with REST API price and trigger cache reset if needed

        Args:
            symbol: Trading symbol
            rest_price: Current REST API price
        """
        if rest_price <= 0:
            logger.debug("Invalid REST price for %s: %.2f", symbol, rest_price)
            return

        with self._lock:
            current_snapshot = self._snapshots.get(symbol.upper())

            if current_snapshot:
                current_price = current_snapshot.price
                diff_pct = abs(rest_price - current_price) / current_price

                # 🚨 FORCE RESET if significant difference - reduced sensitivity
                if diff_pct > 0.15:  # 15% threshold (reduced from 10%)
                    logger.warning(
                        "🔄 CACHE RESET TRIGGERED %s: REST=%.2f vs WebSocket=%.2f (%.1f%% diff)",
                        symbol, rest_price, current_price, diff_pct * 100
                    )
                    self.force_reset(symbol, rest_price, "rest_fallback")
                    return

                # 🔄 Regular update if moderate difference (use 1% from settings)
                elif diff_pct > settings.binance.websocket.max_price_change_pct:  # 1% threshold
                    logger.info(
                        "📊 REST price update %s: %.2f → %.2f (%.1f%%)",
                        symbol, current_price, rest_price, diff_pct * 100
                    )
                    self.set(symbol, rest_price, "rest_api")
                    return

        # Set REST price if no cache or small difference
        self.set(symbol, rest_price, "rest_api")


class PriceCache:
    """Simple in-memory store for latest websocket prices per symbol."""

    def __init__(self) -> None:
        self._prices: Dict[str, tuple[float, datetime]] = {}
        self._ttl = timedelta(minutes=5)

    def set(self, symbol: str, price: float) -> None:
        if price <= 0:
            return
        self._prices[symbol.upper()] = (price, datetime.now(timezone.utc))

    def get(self, symbol: str) -> Optional[float]:
        entry = self._prices.get(symbol.upper())
        if not entry:
            return None
        price, ts = entry
        if datetime.now(timezone.utc) - ts > self._ttl:
            self._prices.pop(symbol.upper(), None)
            return None
        return price


# Use enhanced cache as default
price_cache = EnhancedPriceCache()

# Listener state tracking
_listeners_started: Dict[str, bool] = {}
_listener_threads: Dict[str, threading.Thread] = {}
_listener_last_message: Dict[str, datetime] = {}
_listener_reconnect_count: Dict[str, int] = {}


def _initialize_price_from_rest(symbol: str) -> None:
    """Initialize cache with REST API price before WebSocket starts"""
    try:
        import httpx
        response = httpx.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=5.0
        )
        response.raise_for_status()
        data = response.json()
        price = float(data["price"])
        if price > 0:
            price_cache.set(symbol, price, source="initial_rest")
            logger.info("✅ Initialized cache for %s with REST price: %.2f", symbol, price)
            return
    except Exception as exc:
        logger.warning("⚠️ Could not initialize cache from REST: %s", exc)


def _health_check_monitor(symbol: str, max_silence_seconds: int = 30) -> None:
    """
    Monitor listener health and restart if necessary.
    Runs in separate daemon thread.
    """
    symbol_key = symbol.upper()
    logger.info("🏥 Health check monitor started for %s", symbol_key)
    
    while True:
        try:
            time.sleep(10)  # Check every 10 seconds
            
            # Check if listener is still marked as started
            if not _listeners_started.get(symbol_key):
                logger.warning("⚠️ Listener flag cleared for %s, monitor exiting", symbol_key)
                break
            
            # Check thread health
            thread = _listener_threads.get(symbol_key)
            if not thread or not thread.is_alive():
                logger.error("❌ Listener thread DEAD for %s, restarting...", symbol_key)
                _listeners_started.pop(symbol_key, None)
                _restart_listener(symbol)
                break
            
            # Check message freshness
            last_msg = _listener_last_message.get(symbol_key)
            if last_msg:
                silence_duration = (datetime.now(timezone.utc) - last_msg).total_seconds()
                if silence_duration > max_silence_seconds:
                    logger.error(
                        "❌ No messages for %.1fs (max: %ds) for %s, restarting listener...",
                        silence_duration, max_silence_seconds, symbol_key
                    )
                    _listeners_started.pop(symbol_key, None)
                    _restart_listener(symbol)
                    break
                else:
                    logger.debug(
                        "✅ Health check OK for %s (last message: %.1fs ago)",
                        symbol_key, silence_duration
                    )
            
        except Exception as exc:
            logger.error("❌ Health check error for %s: %s", symbol_key, exc, exc_info=True)


def _restart_listener(symbol: str, max_attempts: int = 5) -> None:
    """Restart listener with exponential backoff"""
    symbol_key = symbol.upper()
    reconnect_count = _listener_reconnect_count.get(symbol_key, 0)
    
    if reconnect_count >= max_attempts:
        logger.critical(
            "❌ FAILED to restart listener for %s after %d attempts",
            symbol_key, max_attempts
        )
        return
    
    reconnect_count += 1
    _listener_reconnect_count[symbol_key] = reconnect_count
    
    # Exponential backoff: 1s, 2s, 4s, 8s, 16s (max 30s)
    backoff = min(2 ** (reconnect_count - 1), 30)
    logger.warning(
        "🔄 Restarting listener for %s (attempt %d/%d) in %.1fs...",
        symbol_key, reconnect_count, max_attempts, backoff
    )
    time.sleep(backoff)
    
    # Clear old state
    _listener_threads.pop(symbol_key, None)
    _listener_last_message.pop(symbol_key, None)
    
    # Restart
    ensure_price_cache_listener(symbol)


def _normalize_kline_payload(
    payload: dict,
    symbol: str,
    interval: Optional[str] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Accept both full Binance payloads and simplified internal ones.

    Returns:
        (kline_dict, None) if valid/normalized; (None, reason) otherwise.
    """
    if not isinstance(payload, dict):
        return None, "payload_not_dict"

    # Unwrap common containers: combined stream ({data: {...}}) or double-wrapped
    raw_payload = payload
    for key in ("data", "payload"):
        inner = raw_payload.get(key)
        if isinstance(inner, dict):
            raw_payload = inner

    kline = raw_payload.get("k") or raw_payload.get("kline")
    if not isinstance(kline, dict) and all(
        key in raw_payload for key in ("o", "h", "l", "c")
    ):
        # Some internal publishers may send bare kline dict without wrapper
        kline = raw_payload
    if not isinstance(kline, dict):
        return None, "missing_kline_section"
    if not kline:
        return None, "empty_kline_section"

    expected_symbol = symbol.upper()
    symbol_in_msg = (raw_payload.get("s") or kline.get("s") or expected_symbol).upper()
    if not symbol_in_msg:
        return None, "missing_symbol"
    if expected_symbol and symbol_in_msg != expected_symbol:
        return None, f"symbol_mismatch:{symbol_in_msg}"
    kline.setdefault("s", symbol_in_msg)

    if interval:
        kline.setdefault("i", interval)

    # Require basic OHLC fields but allow missing Binance metadata
    required_price_fields = ["o", "h", "l", "c"]
    missing = [field for field in required_price_fields if field not in kline]
    if missing:
        return None, f"missing_fields:{','.join(sorted(missing))}"

    try:
        close_price = float(kline.get("c", 0.0))
        open_price = float(kline.get("o", 0.0))
        high_price = float(kline.get("h", 0.0))
        low_price = float(kline.get("l", 0.0))
    except (TypeError, ValueError):
        return None, "non_numeric_price_fields"

    if any(price <= 0 for price in (close_price, open_price, high_price, low_price)):
        return None, "non_positive_prices"

    event_type = raw_payload.get("e")
    if event_type and event_type != "kline":
        return None, f"unexpected_event:{event_type}"

    return kline, None


def ensure_price_cache_listener(symbol: str, channel: str = KLINE_CHANNEL) -> None:
    """
    Start WebSocket listener with health check and auto-reconnect.
    
    Features:
    - Initial price from REST API (never start with empty cache)
    - Health check monitoring (restart if silent for 30s)
    - Auto-reconnect on crash (exponential backoff, max 5 attempts)
    - Thread liveness monitoring
    """
    symbol_key = symbol.upper()
    
    if _listeners_started.get(symbol_key):
        logger.debug("Listener already running for %s", symbol_key)
        return

    logger.info("🎧 Starting price cache listener for %s on channel %s", symbol_key, channel)
    
    # STEP 1: Initialize cache with REST price
    _initialize_price_from_rest(symbol_key)
    
    # STEP 2: Start WebSocket listener
    def _run() -> None:
        logger.info("🔌 WebSocket listener thread STARTING for %s", symbol_key)
        client = None
        pubsub = None
        message_count = 0
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        try:
            # Create Redis connection
            client = redis.Redis.from_url(str(settings.redis.url))
            pubsub = client.pubsub()
            pubsub.subscribe(channel)
            
            logger.info("✅ WebSocket listener CONNECTED for %s", symbol_key)
            
            for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                
                try:
                    # Parse message
                    raw = message.get("data")
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    payload = json.loads(raw)
                    
                    # Filter by symbol
                    if payload.get("symbol", "").upper() != symbol_key:
                        continue

                    # 🔍 CRITICAL: Validate data source format
                    kline_payload = payload.get("payload") or {}

                    normalized_kline, invalid_reason = _normalize_kline_payload(
                        kline_payload,
                        symbol_key,
                        payload.get("interval"),
                    )
                    if not normalized_kline:
                        # Include top-level keys in log for debugging without dumping full payload
                        key_info = list(kline_payload.keys()) if isinstance(kline_payload, dict) else type(kline_payload).__name__
                        logger.warning(
                            "🚨 INVALID DATA FORMAT for %s - rejecting suspicious price data (%s) | keys=%s",
                            symbol_key,
                            invalid_reason or "unknown_reason",
                            key_info,
                        )
                        continue

                    # Extract price from validated format
                    close_price = float(normalized_kline.get("c", 0.0))
                    
                    if close_price > 0:
                        # Update cache with explicit Binance websocket source
                        price_cache.set(symbol_key, close_price, source="binance_websocket")
                        _listener_last_message[symbol_key] = datetime.now(timezone.utc)
                        message_count += 1
                        consecutive_errors = 0  # Reset error counter
                        
                        # Log every 50 messages
                        if message_count % 50 == 0:
                            logger.info(
                                "📊 WebSocket: %d messages processed for %s",
                                message_count, symbol_key
                            )
                    else:
                        logger.warning("⚠️ Invalid price (%.2f) from WebSocket", close_price)
                        consecutive_errors += 1
                
                except json.JSONDecodeError as exc:
                    logger.error("❌ JSON decode error: %s", exc)
                    consecutive_errors += 1
                except Exception as exc:
                    logger.error("❌ Message processing error: %s", exc, exc_info=True)
                    consecutive_errors += 1
                
                # Too many consecutive errors? Reconnect
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "❌ Too many consecutive errors (%d), forcing reconnect",
                        consecutive_errors
                    )
                    break
            
        except redis.ConnectionError as exc:
            logger.error("❌ Redis connection error for %s: %s", symbol_key, exc)
        except Exception as exc:
            logger.error("❌ Listener crashed for %s: %s", symbol_key, exc, exc_info=True)
        finally:
            # Cleanup
            try:
                if pubsub:
                    pubsub.unsubscribe(channel)
                    pubsub.close()
                if client:
                    client.close()
            except Exception:
                pass
            
            # Clear flags to allow restart
            _listeners_started.pop(symbol_key, None)
            logger.warning("🔌 WebSocket listener DISCONNECTED for %s", symbol_key)
            
            # Auto-restart
            logger.info("🔄 Attempting auto-restart for %s...", symbol_key)
            _restart_listener(symbol)
    
    try:
        # Start listener thread
        thread = threading.Thread(
            target=_run,
            name=f"price-cache-{symbol_key.lower()}",
            daemon=True
        )
        thread.start()
        
        # Wait for thread to start
        time.sleep(0.2)
        
        if thread.is_alive():
            _listeners_started[symbol_key] = True
            _listener_threads[symbol_key] = thread
            _listener_last_message[symbol_key] = datetime.now(timezone.utc)
            logger.info("✅ Listener thread CONFIRMED RUNNING for %s", symbol_key)
            
            # Start health check monitor
            monitor_thread = threading.Thread(
                target=_health_check_monitor,
                args=(symbol,),
                name=f"health-check-{symbol_key.lower()}",
                daemon=True
            )
            monitor_thread.start()
            logger.info("🏥 Health check monitor STARTED for %s", symbol_key)
        else:
            logger.error("❌ Listener thread FAILED to start for %s", symbol_key)
            _restart_listener(symbol)

    except Exception as exc:
        logger.error("❌ Failed to start listener for %s: %s", symbol_key, exc, exc_info=True)
