import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import pandas as pd
from binance import AsyncClient, BinanceSocketManager

from app.config.settings import get_settings
from app.data_feeds.constants import HEALTH_CHANNEL, KLINE_CHANNEL
from app.utils.latency import get_latency_tracker
from app.utils.logging import get_logger
from app.utils.price_cache import price_cache
from app.utils.redis import publish, publish_safe

settings = get_settings()
logger = get_logger(__name__)


@dataclass
class WebSocketMessage:
    stream: str
    payload: dict


class BinanceWebSocketClient:
    def __init__(self, symbol: str, interval: str = "1m") -> None:
        self._symbol = symbol.upper()
        self._interval = interval
        self._client: Optional[AsyncClient] = None
        self._manager: Optional[BinanceSocketManager] = None
        self._socket = None
        self._last_valid_price: Optional[float] = None
        self._max_price_change_pct = 0.05  # 5% max change threshold

    @property
    def stream_name(self) -> str:
        return f"{self._symbol.lower()}@kline_{self._interval}"

    async def listen(self, handler: Callable[[WebSocketMessage], None]) -> None:
        self._client = await AsyncClient.create(
            api_key=settings.binance.api_key,
            api_secret=settings.binance.api_secret,
        )
        self._manager = BinanceSocketManager(self._client)
        while True:
            try:
                socket = self._manager.kline_socket(symbol=self._symbol, interval=self._interval)
                async with socket as stream:
                    while True:
                        data = await stream.recv()

                        # LATENCY TRACKING: Stage 1d - Data Ingestion
                        # Extract market timestamp and start latency trace
                        kline = data.get("k", {})
                        market_timestamp_ms = kline.get("T")  # Kline close time
                        market_ts = None
                        trace_id = None

                        if market_timestamp_ms:
                            market_ts = pd.to_datetime(int(market_timestamp_ms), unit="ms")
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
                            logger.warning(
                                "⚠️ Bozuk fiyat filtrelendi: %s | mevcut=%.2f | son_geçerli=%.2f | kapanmış=%s | zaman=%s",
                                self._symbol,
                                self._extract_close_price(data),
                                self._last_valid_price or 0,
                                kline.get("x", False),  # Kline kapanmış mı?
                                kline.get("T"),  # Kline kapanış zamanı (ms)
                            )
                            continue

                        handler(WebSocketMessage(stream=self.stream_name, payload=data))
                        self._cache_close_price(data)

                        # Use safe publish with local queue fallback
                        publish_safe(
                            KLINE_CHANNEL,
                            {
                                "symbol": self._symbol,
                                "interval": self._interval,
                                "payload": data,
                                "received_at": datetime.utcnow().isoformat(),
                                "trace_id": trace_id,  # Pass trace_id for downstream tracking
                            },
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Binance WebSocket error: %s", exc)
                publish(
                    HEALTH_CHANNEL,
                    {
                        "source": "binance_ws",
                        "status": "error",
                        "detail": str(exc),
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                await asyncio.sleep(5)

    def _extract_close_price(self, payload: dict) -> float:
        """Extract close price from kline payload"""
        try:
            kline = payload.get("k") or {}
            return float(kline.get("c", 0.0))
        except Exception:
            return 0.0

    def _validate_price_data(self, payload: dict) -> bool:
        """
        Data Quality Control (Sanity Check)
        Validates that incoming kline close price is within reasonable range
        compared to previous close price. Filters out abnormal price spikes.

        Args:
            payload: WebSocket kline payload

        Returns:
            True if price is valid, False if spike detected
        """
        try:
            kline = payload.get("k") or {}

            # Check if kline structure is complete
            if not kline or not all(k in kline for k in ["o", "h", "l", "c"]):
                logger.debug(
                    "Eksik kline yapısı, atlanıyor: %s", list(kline.keys()) if kline else "boş"
                )
                return False

            close_price = self._extract_close_price(payload)

            if close_price <= 0:
                logger.debug("Geçersiz close price: %.2f (sıfır veya negatif)", close_price)
                return False

            # Validate OHLC relationship
            try:
                open_price = float(kline.get("o", 0))
                high_price = float(kline.get("h", 0))
                low_price = float(kline.get("l", 0))

                if high_price > 0 and low_price > 0:  # Only validate if we have valid OHLC
                    if not (
                        low_price <= close_price <= high_price
                        and low_price <= open_price <= high_price
                    ):
                        logger.warning(
                            "OHLC ilişkisi ihlali %s: O=%.2f H=%.2f L=%.2f C=%.2f",
                            self._symbol,
                            open_price,
                            high_price,
                            low_price,
                            close_price,
                        )
                        return False
            except (ValueError, TypeError) as e:
                logger.debug("OHLC validasyon hatası: %s", e)
                return False

            # First price - always valid
            if self._last_valid_price is None:
                self._last_valid_price = close_price
                return True

            # Calculate price change percentage
            price_change_pct = abs(close_price - self._last_valid_price) / self._last_valid_price

            # Check if within acceptable range
            if price_change_pct > self._max_price_change_pct:
                # Spike detected - reject this data point
                logger.debug(
                    "Price spike %s: %.2f%% değişim (limit: %.2f%%)",
                    self._symbol,
                    price_change_pct * 100,
                    self._max_price_change_pct * 100,
                )
                return False

            # Valid price - update last valid price
            self._last_valid_price = close_price
            return True

        except Exception as e:
            logger.debug("Price validation error: %s", e)
            return False

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


async def stream(
    symbol: str, handler: Callable[[WebSocketMessage], None], interval: str = "1m"
) -> None:
    client = BinanceWebSocketClient(symbol=symbol, interval=interval)
    try:
        await client.listen(handler)
    finally:
        await client.close()
