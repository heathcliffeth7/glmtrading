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
    
    def is_stale(self, max_age_seconds: int = 10) -> bool:
        """Check if price is older than acceptable threshold"""
        return self.age_seconds() > max_age_seconds
    
    def is_valid(self) -> bool:
        """Validate price is in reasonable range (for BTC)"""
        # Reasonable range for BTCUSDT: $1,000 - $1,000,000
        return 1000.0 < self.price < 1_000_000.0


class EnhancedPriceCache:
    """
    Thread-safe price cache with staleness detection and validation
    
    Replaces simple PriceCache with enhanced features:
    - Staleness detection (default 10s)
    - Price validation (range checks)
    - Source tracking (websocket, REST API, etc.)
    - Thread safety with locks
    """
    
    def __init__(self):
        self._snapshots: Dict[str, PriceSnapshot] = {}
        self._lock = threading.Lock()
    
    def set(self, symbol: str, price: float, source: str = "unknown") -> None:
        """
        Set price with source tracking
        
        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            price: Price value
            source: Data source (e.g., "websocket", "binance_rest")
        """
        if price <= 0:
            logger.debug("Ignoring invalid price: %.2f for %s", price, symbol)
            return
        
        snapshot = PriceSnapshot(price, datetime.now(timezone.utc), source)
        
        # Validate before storing
        if not snapshot.is_valid():
            logger.warning(
                "⚠️ Price validation failed for %s: $%.2f from %s",
                symbol, price, source
            )
            return
        
        with self._lock:
            self._snapshots[symbol.upper()] = snapshot
            logger.debug(
                "Price updated for %s: $%.2f from %s",
                symbol, price, source
            )
    
    def get(self, symbol: str, max_age_seconds: int = 10) -> Optional[float]:
        """
        Get price only if fresh enough
        
        Args:
            symbol: Trading symbol
            max_age_seconds: Maximum acceptable age (default 10s)
        
        Returns:
            Price if available and fresh, None otherwise
        """
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
            "https://fapi.binance.com/fapi/v1/ticker/price",
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
                    
                    # Extract price
                    kline_payload = payload.get("payload") or {}
                    kline = kline_payload.get("k") or {}
                    close_price = float(kline.get("c", 0.0))
                    
                    if close_price > 0:
                        # Update cache
                        price_cache.set(symbol_key, close_price, source="websocket")
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
