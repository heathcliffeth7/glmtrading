import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

import pandas as pd
from binance import AsyncClient, BinanceSocketManager

from app.config.settings import get_settings
from app.data_feeds.constants import HEALTH_CHANNEL, KLINE_CHANNEL
from app.utils.latency import get_latency_tracker
from app.utils.logging import get_logger
from app.utils.price_cache import price_cache
from app.utils.redis import publish, publish_safe, publish_async


settings = get_settings()
logger = get_logger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class WebSocketMessage:
    stream: str
    payload: dict


@dataclass
class ConnectionMetrics:
    state: ConnectionState
    last_connected_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    last_error_message: Optional[str] = None
    reconnect_attempts: int = 0
    total_messages: int = 0
    connection_uptime: float = 0.0


class BinanceWebSocketClient:
    def __init__(self, symbol: str, interval: str = "1m") -> None:
        self._symbol = symbol.upper()
        self._interval = interval
        self._client: Optional[AsyncClient] = None
        self._manager: Optional[BinanceSocketManager] = None
        self._socket = None
        self._last_valid_price: Optional[float] = None
        self._last_rest_price: Optional[float] = None  # Track REST API price for validation
        self._last_price_check_time = 0  # Track time for time-based validation
        self._consecutive_rejections = 0  # Ardışık retleri takip et

        # Dynamic price range validation (24h high/low from Binance)
        self._price_range_cache: Optional[tuple[float, float, float]] = None  # (min, max, timestamp)
        self._range_cache_ttl = 3600  # 1 hour cache TTL

        # Symbol-specific price ranges for hard validation - SPOT market
        self._symbol_ranges = {
            'BTCUSDT': (70000, 120000),  # 70k - 120k
            'ETHUSDT': (1500, 5000),     # 1.5k - 5k
            'SOLUSDT': (70, 250)         # 70 - 250
        }

        # Enhanced connection management - use settings
        ws_settings = settings.binance.websocket
        self._connection_state = ConnectionState.DISCONNECTED
        self._metrics = ConnectionMetrics(state=ConnectionState.DISCONNECTED)
        self._max_reconnect_attempts = ws_settings.max_reconnect_attempts
        self._base_retry_delay = ws_settings.base_retry_delay
        self._max_retry_delay = ws_settings.max_retry_delay
        self._circuit_breaker_threshold = ws_settings.circuit_breaker_threshold
        self._connection_cooldown = ws_settings.connection_cooldown
        self._last_ping_time = 0
        self._ping_interval = ws_settings.ping_interval
        self._heartbeat_check_interval = ws_settings.heartbeat_check_interval
        self._message_timeout_multiplier = ws_settings.message_timeout_multiplier
        self._connection_start_time = 0

        # Validation settings
        self._validate_ohlc = ws_settings.validate_ohlc_relationship
        self._max_price_change_pct = ws_settings.max_price_change_pct
        self._cache_reset_threshold = ws_settings.cache_reset_threshold

    @property
    def stream_name(self) -> str:
        return f"{self._symbol.lower()}@kline_{self._interval}"
    
    async def _fetch_24h_price_range(self) -> tuple[float, float]:
        """
        Fetch 24h high/low from Binance REST API for dynamic range validation
        
        Returns:
            (min_price, max_price) with 20% buffer applied
        """
        try:
            import httpx
            response = await asyncio.to_thread(
                httpx.get,
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": self._symbol},
                timeout=5.0
            )
            response.raise_for_status()
            data = response.json()
            
            high = float(data["highPrice"])
            low = float(data["lowPrice"])
            
            # Apply 20% buffer for volatility tolerance
            min_price = low * 0.80
            max_price = high * 1.20
            
            # Cache the result with timestamp
            self._price_range_cache = (min_price, max_price, time.time())
            
            logger.info(
                "📊 24h price range for %s: $%.2f - $%.2f (buffered from $%.2f - $%.2f)",
                self._symbol, min_price, max_price, low, high
            )
            
            return min_price, max_price
            
        except Exception as exc:
            logger.warning("Failed to fetch 24h price range for %s: %s", self._symbol, exc)
            # Return wide range as fallback
            return 0.01, 1_000_000.0

    def _update_connection_state(self, state: ConnectionState, error_message: Optional[str] = None) -> None:
        """Update connection state and metrics"""
        old_state = self._connection_state
        self._connection_state = state
        self._metrics.state = state

        current_time = datetime.utcnow()

        if state == ConnectionState.CONNECTED:
            self._metrics.last_connected_at = current_time
            self._metrics.reconnect_attempts = 0
            self._connection_start_time = time.time()
            if old_state in [ConnectionState.DISCONNECTED, ConnectionState.ERROR]:
                logger.info("🟢 WebSocket bağlandı %s", self._symbol)

        elif state == ConnectionState.ERROR:
            self._metrics.last_error_at = current_time
            self._metrics.last_error_message = error_message
            if self._connection_start_time > 0:
                self._metrics.connection_uptime += time.time() - self._connection_start_time

        elif state in [ConnectionState.CONNECTING, ConnectionState.RECONNECTING]:
            if state == ConnectionState.RECONNECTING:
                self._metrics.reconnect_attempts += 1

        # Publish state change
        publish(
            HEALTH_CHANNEL,
            {
                "source": "binance_ws",
                "symbol": self._symbol,
                "status": state.value,
                "detail": error_message,
                "reconnect_attempts": self._metrics.reconnect_attempts,
                "timestamp": current_time.isoformat(),
            },
        )

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter"""
        delay = min(self._base_retry_delay * (2 ** attempt), self._max_retry_delay)
        # Add jitter to prevent thundering herd
        jitter = random.uniform(0.1, 0.3) * delay
        return delay + jitter

    def _should_circuit_break(self) -> bool:
        """Check if circuit breaker should be triggered"""
        return self._metrics.reconnect_attempts >= self._circuit_breaker_threshold

    def _classify_error(self, error: Exception) -> str:
        """Classify WebSocket errors for appropriate handling"""
        error_str = str(error).lower()

        if "read loop has been closed" in error_str:
            return "read_loop_closed"
        elif "connection lost" in error_str:
            return "connection_lost"
        elif "timeout" in error_str:
            return "timeout"
        elif "rate limit" in error_str:
            return "rate_limit"
        elif "invalid api key" in error_str:
            return "auth_error"
        else:
            return "unknown"

    async def _heartbeat_monitor(self, stream) -> None:
        """Monitor connection health with periodic ping/pong checks"""
        consecutive_failures = 0
        max_consecutive_failures = 3
        
        while self._connection_state == ConnectionState.CONNECTED:
            try:
                current_time = time.time()
                time_since_last_message = current_time - self._last_ping_time
                
                # Check if it's time for a connection health check
                if time_since_last_message >= self._ping_interval:
                    # Check if we've exceeded the timeout threshold
                    if time_since_last_message > self._ping_interval * self._message_timeout_multiplier:
                        consecutive_failures += 1
                        logger.warning(
                            "⚠️ Mesaj akışı yok %s - %.1f saniyedir mesaj alınmıyor (başarısız deneme: %d/%d)",
                            self._symbol, time_since_last_message, consecutive_failures, max_consecutive_failures
                        )
                        
                        # If we've had multiple consecutive failures, trigger reconnection
                        if consecutive_failures >= max_consecutive_failures:
                            logger.error(
                                "🔌 Bağlantı kopması tespit edildi %s - %.1f saniyedir mesaj yok, yeniden bağlanılıyor",
                                self._symbol, time_since_last_message
                            )
                            self._update_connection_state(
                                ConnectionState.ERROR,
                                f"Connection lost: No messages for {time_since_last_message:.1f} seconds"
                            )
                            break
                    else:
                        # Reset failure counter on successful message receipt
                        consecutive_failures = 0
                        logger.debug(
                            "✅ Bağlantı sağlıklı %s - son mesajden %.1f saniye geçti",
                            self._symbol, time_since_last_message
                        )
                    
                    # Update ping time for next check
                    self._last_ping_time = current_time

                await asyncio.sleep(self._heartbeat_check_interval)

            except Exception as exc:
                logger.error("Heartbeat monitor hatası %s: %s", self._symbol, exc)
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "🔌 Heartbeat monitor başarısız %s - %d ardışık hata, yeniden bağlanılıyor",
                        self._symbol, consecutive_failures
                    )
                    self._update_connection_state(
                        ConnectionState.ERROR,
                        f"Heartbeat monitor failed: {exc}"
                    )
                    break
                await asyncio.sleep(self._heartbeat_check_interval)

    async def listen(self, handler: Callable[[WebSocketMessage], None]) -> None:
        self._client = await AsyncClient.create(
            api_key=settings.binance.api_key,
            api_secret=settings.binance.api_secret,
        )
        self._manager = BinanceSocketManager(self._client)
        
        # Initialize 24h price range before starting WebSocket
        logger.info("🔍 Fetching initial 24h price range for %s...", self._symbol)
        await self._fetch_24h_price_range()

        retry_count = 0
        self._last_ping_time = time.time()
        connection_id = f"{self._symbol}_{int(time.time())}"  # Unique ID for this connection attempt

        while True:
            try:
                # Circuit breaker check
                if self._should_circuit_break():
                    self._update_connection_state(
                        ConnectionState.ERROR,
                        f"Circuit breaker triggered after {retry_count} failed attempts"
                    )
                    logger.warning(
                        "🔌 Circuit breaker aktif %s - %d deneme sonrası %d saniye beklenecek",
                        self._symbol, retry_count, self._connection_cooldown
                    )
                    await asyncio.sleep(self._connection_cooldown)
                    retry_count = 0  # Reset after cooldown
                    continue

                # Update state to connecting
                state = ConnectionState.RECONNECTING if retry_count > 0 else ConnectionState.CONNECTING
                self._update_connection_state(state)
                
                logger.info(
                    "🔗 Binance WebSocket bağlanıyor %s (deneme: %d, bağlantı ID: %s)",
                    self._symbol, retry_count + 1, connection_id
                )

                socket = self._manager.kline_socket(symbol=self._symbol, interval=self._interval)
                async with socket as stream:
                    self._update_connection_state(ConnectionState.CONNECTED)
                    retry_count = 0  # Reset on successful connection
                    logger.info(
                        "✅ Binance WebSocket bağlandı %s (bağlantı ID: %s)",
                        self._symbol, connection_id
                    )

                    # Start heartbeat monitor
                    heartbeat_task = asyncio.create_task(self._heartbeat_monitor(stream))

                    while True:
                        try:
                            data = await stream.recv()
                            self._metrics.total_messages += 1
                            self._last_ping_time = time.time()  # Update last activity time

                            # LATENCY TRACKING: Stage 1d - Data Ingestion
                            # Extract market timestamp and start latency trace
                            kline = data.get("k", {})
                            market_timestamp_ms = kline.get("T")  # Kline close time
                            market_ts = None
                            trace_id = None

                            if market_timestamp_ms:
                                market_ts = pd.to_datetime(int(market_timestamp_ms), unit='ms')
                                trace_id = f"{self._symbol}_{self._interval}_{market_timestamp_ms}"

                                # Start latency trace
                                tracker = get_latency_tracker()
                                trace = tracker.start_trace(
                                    symbol=self._symbol,
                                    interval=self._interval,
                                    trace_id=trace_id,
                                    market_ts=market_ts,
                                )

                            # Data sanity check: Filter price spikes
                            if not self._validate_price_data(data):
                                kline = data.get("k", {})
                                current_price = self._extract_close_price(data)
                                last_valid = self._last_valid_price or 0

                                # Hesapla değişim yüzdesini göster
                                change_pct = 0
                                if last_valid > 0:
                                    change_pct = abs(current_price - last_valid) / last_valid * 100

                                logger.info(
                                    "⚠️ Fiyat filtrelendi %s: mevcut=%.2f → son=%.2f (%.1f%%) | ardışık_ret=%d | kapanmış=%s",
                                    self._symbol,
                                    current_price,
                                    last_valid,
                                    change_pct,
                                    self._consecutive_rejections,
                                    kline.get("x", False)
                                )
                                continue

                            handler(WebSocketMessage(stream=self.stream_name, payload=data))
                            self._cache_close_price(data)

                            # Başarılı veri işleme metrikleri
                            if hasattr(self, '_total_messages') and self._total_messages % 100 == 0:
                                logger.info(
                                    "✅ %s için %d mesaj işlendi | ardışık_ret=%d | son_fiyat=%.2f",
                                    self._symbol, self._total_messages, self._consecutive_rejections, self._last_valid_price or 0
                                )

                            # Fire-and-forget async publish (non-blocking)
                            asyncio.create_task(
                                publish_async(
                                    KLINE_CHANNEL,
                                    {
                                        "symbol": self._symbol,
                                        "interval": self._interval,
                                        "payload": data,
                                        "received_at": datetime.utcnow().isoformat(),
                                        "trace_id": trace_id,  # Pass trace_id for downstream tracking
                                    },
                                )
                            )

                        except Exception as inner_exc:
                            # Handle individual message errors without breaking connection
                            error_type = self._classify_error(inner_exc)

                            if error_type == "read_loop_closed":
                                logger.critical(
                                    "🔌 WebSocket read loop kapandı %s - bağlantı yeniden kuruluyor",
                                    self._symbol
                                )
                                self._update_connection_state(
                                    ConnectionState.ERROR,
                                    f"Read loop closed: {inner_exc}"
                                )
                                # Cancel heartbeat monitor
                                if not heartbeat_task.done():
                                    heartbeat_task.cancel()
                                break  # Break inner loop to reconnect
                            else:
                                logger.warning(
                                    "⚠️ WebSocket mesaj hatası %s: %s",
                                    self._symbol, inner_exc
                                )
                                continue  # Continue processing other messages

            except asyncio.CancelledError:
                logger.info("WebSocket iptal edildi %s", self._symbol)
                # Cancel any running heartbeat monitor
                if 'heartbeat_task' in locals() and not heartbeat_task.done():
                    heartbeat_task.cancel()
                raise
            except Exception as exc:  # noqa: BLE001
                error_type = self._classify_error(exc)
                retry_count += 1
                
                # Generate new connection ID for this attempt
                connection_id = f"{self._symbol}_{int(time.time())}"

                # Enhanced error handling based on error type
                if error_type == "read_loop_closed":
                    logger.critical(
                        "🔌 Read loop kapandı %s (deneme %d) - özel reconnection başlatılıyor",
                        self._symbol, retry_count
                    )
                    self._update_connection_state(
                        ConnectionState.ERROR,
                        f"Read loop has been closed: {exc}"
                    )
                elif error_type == "rate_limit":
                    logger.warning(
                        "🚦 Rate limit aşıldı %s - daha uzun bekleme",
                        self._symbol
                    )
                    await asyncio.sleep(60)  # Longer delay for rate limits
                    continue
                elif error_type == "auth_error":
                    logger.error(
                        "❌ Kimlik doğrulama hatası %s - reconnection deneme停止",
                        self._symbol
                    )
                    break  # Don't retry on auth errors
                else:
                    logger.warning(
                        "Binance WebSocket hatası %s (deneme %d): %s",
                        self._symbol, retry_count, exc
                    )
                    self._update_connection_state(
                        ConnectionState.ERROR,
                        f"WebSocket error: {exc}"
                    )

                # Calculate retry delay with exponential backoff
                retry_delay = self._calculate_retry_delay(retry_count - 1)

                logger.info(
                    "🔄 %d saniye sonra yeniden bağlanılacak %s (deneme %d, bağlantı ID: %s)",
                    retry_delay, self._symbol, retry_count, connection_id
                )

                await asyncio.sleep(retry_delay)

    def _extract_close_price(self, payload: dict) -> float:
        """Extract close price from kline payload"""
        try:
            kline = payload.get("k") or {}
            return float(kline.get("c", 0.0))
        except Exception:
            return 0.0
    
    def _validate_price_data(self, payload: dict) -> bool:
        """
        Basic Data Quality Control - REMOVED REDUNDANT SPIKE DETECTION

        Only validates:
        - Kline structure completeness
        - Price range (basic)
        - OHLC relationship
        - Positive price values

        NOTE: Spike detection is now handled ONLY in EnhancedPriceCache
        """
        try:
            kline = payload.get("k") or {}

            # Check if kline structure is complete
            if not kline or not all(k in kline for k in ["o", "h", "l", "c"]):
                logger.debug("Eksik kline yapısı, atlanıyor: %s", list(kline.keys()) if kline else "boş")
                return False

            close_price = self._extract_close_price(payload)

            # Only basic positive price check
            if close_price <= 0:
                logger.debug("Geçersiz close price: %.2f (sıfır veya negatif)", close_price)
                return False

            # Validate OHLC relationship (basic data integrity)
            try:
                open_price = float(kline.get("o", 0))
                high_price = float(kline.get("h", 0))
                low_price = float(kline.get("l", 0))

                if high_price > 0 and low_price > 0 and self._validate_ohlc:
                    if not (low_price <= close_price <= high_price and low_price <= open_price <= high_price):
                        logger.warning(
                            "OHLC ilişkisi ihlali %s: O=%.2f H=%.2f L=%.2f C=%.2f",
                            self._symbol, open_price, high_price, low_price, close_price
                        )
                        return False
            except (ValueError, TypeError) as e:
                logger.debug("OHLC validasyon hatası: %s", e)
                return False

            # ✅ PRICE DATA IS VALID - spike detection is handled by EnhancedPriceCache
            return True

        except Exception as e:
            logger.debug("Price validation error: %s", e)
            return False

    def update_rest_price(self, rest_price: float) -> None:
        """
        Update REST API price for multi-source validation - konservatif yaklaşım

        Args:
            rest_price: Current price from REST API
        """
        if rest_price > 0:
            old_rest_price = self._last_rest_price
            self._last_rest_price = rest_price

            if old_rest_price and abs(rest_price - old_rest_price) / old_rest_price > 0.01:
                logger.info(
                    "🔄 REST fiyat güncellendi %s: %.2f → %.2f (%.1f%%)",
                    self._symbol, old_rest_price, rest_price,
                    abs(rest_price - old_rest_price) / old_rest_price * 100
                )

            # Sadece çok büyük farklarda önbellek sıfırlama (1% threshold ile)
            if self._last_valid_price:
                diff_pct = abs(rest_price - self._last_valid_price) / self._last_valid_price
                if diff_pct > 0.01:  # 1% threshold
                    logger.warning(
                        "🚨 REST ve WebSocket fiyatı uyuşmuyor %s: REST=%.2f vs WebSocket=%.2f (%.1f%% fark) - Önbellek korunuyor",
                        self._symbol, rest_price, self._last_valid_price, diff_pct * 100
                    )
                    # Önbelleği sıfırlama, sadece uyarı göster
    
    def _cache_close_price(self, payload: dict) -> None:
        try:
            close_price = self._extract_close_price(payload)
            if close_price > 0:
                price_cache.set(self._symbol, close_price, source="binance_websocket")
        except Exception:
            logger.debug("Failed to cache websocket close price", exc_info=True)

    async def close(self) -> None:
        if self._manager:
            await self._manager.close()
        if self._client:
            await self._client.close_connection()


async def stream(symbol: str, handler: Callable[[WebSocketMessage], None], interval: str = "1m") -> None:
    client = BinanceWebSocketClient(symbol=symbol, interval=interval)
    try:
        await client.listen(handler)
    finally:
        await client.close()
