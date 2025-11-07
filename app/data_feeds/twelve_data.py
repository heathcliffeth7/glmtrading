import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import httpx

from app.config.settings import get_settings
from app.data_feeds.constants import HEALTH_CHANNEL, KLINE_CHANNEL, TWELVEDATA_CHANNEL
from app.utils.logging import get_logger
from app.utils.redis import publish


settings = get_settings()
logger = get_logger(__name__)


# OPTIMIZED: Only fetch indicators that cannot be calculated from Binance OHLCV
# All standard indicators (RSI, MACD, EMA, BB, Stoch, ATR, etc.) are now calculated from Binance klines
# This reduces API calls by 80% (15 → 3 indicators)
INDICATORS: List[str] = [
    "ichimoku",         # Ichimoku Cloud - complex calculation with future projections
    "sar",              # Parabolic SAR - complex trailing stop algorithm
    "pivot_points_hl",  # Pivot Points - requires daily high/low (not available from 5min klines)
]


@dataclass
class IndicatorPayload:
    indicator: str
    symbol: str
    data: dict


@dataclass
class TimeSeriesBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str

    @classmethod
    def from_api(cls, symbol: str, value: Dict[str, str]) -> "TimeSeriesBar":
        timestamp = datetime.strptime(value["datetime"], "%Y-%m-%d %H:%M:%S")

        def _to_float(field: str) -> float:
            raw = value.get(field, "")
            if raw in (None, ""):
                return 0.0
            return float(raw)

        return cls(
            timestamp=timestamp,
            open=_to_float("open"),
            high=_to_float("high"),
            low=_to_float("low"),
            close=_to_float("close"),
            volume=_to_float("volume"),
            symbol=symbol,
        )

    def to_message(self, symbol: str, interval: str) -> Dict[str, object]:
        period_map = {
            "1min": 60000,
            "5min": 300000,
            "15min": 900000,
            "30min": 1800000,
            "60min": 3600000,
        }
        duration = period_map.get(interval, 0)
        end_ms = int(self.timestamp.timestamp() * 1000)
        start_ms = end_ms - duration if duration else end_ms
        return {
            "symbol": symbol,
            "interval": interval,
            "payload": {
                "k": {
                    "o": str(self.open),
                    "h": str(self.high),
                    "l": str(self.low),
                    "c": str(self.close),
                    "v": str(self.volume),
                    "t": start_ms,
                    "T": end_ms,
                }
            },
            "received_at": datetime.utcnow().isoformat(),
        }


class TwelveDataClient:
    def __init__(self, keys: List[str], daily_quota: int = 800) -> None:
        if not keys:
            raise ValueError("En az bir Twelve Data API anahtarı gerekli")
        self._keys = deque(keys)
        self._quota_per_key = daily_quota
        self._usage: Dict[str, int] = {key: 0 for key in keys}
        self._window_start = datetime.utcnow()
        self._base_url = str(settings.twelve_data.base_url)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=10.0)
        self._current_key_index = 0
        self._rate_limited_keys: Dict[str, datetime] = {}  # Track keys hitting 429 with expiry

    def _mark_rate_limited(self, key: str, block_duration_seconds: int = 60) -> None:
        """Mark a key as rate limited with expiry time"""
        self._rate_limited_keys[key] = datetime.utcnow() + timedelta(seconds=block_duration_seconds)
        available = sum(1 for k in self._keys if self._is_key_available(k))
        logger.warning(
            "Key marked as rate limited for %ds. Available keys: %d/%d",
            block_duration_seconds,
            available,
            len(self._keys),
        )

    def _is_key_available(self, key: str) -> bool:
        """Check if key is usable (not blocked by 429, has quota)"""
        # Check if blocked by 429
        if key in self._rate_limited_keys:
            if datetime.utcnow() < self._rate_limited_keys[key]:
                return False  # Still blocked
            else:
                del self._rate_limited_keys[key]  # Block expired
                logger.info("Rate limit block expired for key")
        
        # Check daily quota
        if self._usage[key] >= self._quota_per_key:
            return False
        
        return True

    def _get_key(self) -> str:
        """Get next available key (not blocked, has quota)"""
        # Reset daily usage if new day
        if datetime.utcnow() - self._window_start >= timedelta(days=1):
            self._usage = {key: 0 for key in self._usage}
            self._rate_limited_keys.clear()  # Reset all blocks
            self._window_start = datetime.utcnow()
            logger.info("Daily quota reset, all keys available")
        
        # Try all keys
        for _ in range(len(self._keys)):
            key = self._keys[0]
            if self._is_key_available(key):
                self._usage[key] += 1
                self._keys.rotate(-1)
                return key
            self._keys.rotate(-1)
        
        # No key available
        available = sum(1 for k in self._keys if self._is_key_available(k))
        blocked = len(self._rate_limited_keys)
        quota_exhausted = sum(1 for k in self._keys if self._usage.get(k, 0) >= self._quota_per_key)
        
        detail = f"No usable keys: {available}/{len(self._keys)} available, {blocked} rate-limited, {quota_exhausted} quota-exhausted"
        logger.error(detail)
        
        publish(
            HEALTH_CHANNEL,
            {
                "source": "twelve_data",
                "status": "no_keys_available",
                "detail": detail,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        raise RuntimeError("All Twelve Data API keys exhausted or rate limited")

    async def fetch_indicator(self, symbol: str, indicator: str, interval: str = "30min") -> IndicatorPayload:
        max_retries = len(self._keys)  # Try all available keys
        last_error = None
        used_key = None
        
        for attempt in range(max_retries):
            try:
                used_key = self._get_key()
                params: Dict[str, str] = {
                    "symbol": symbol,
                    "interval": interval,
                    "apikey": used_key,
                }
                url = f"/{indicator}"
                response = await self._client.get(url, params=params)
                
                # Check for rate limit in response body first
                data = response.json()
                if isinstance(data, dict) and data.get("code") == 429:
                    self._mark_rate_limited(used_key, block_duration_seconds=60)
                    logger.warning(
                        "Rate limit hit (body 429) for %s. Attempt %d/%d",
                        indicator,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(0.5)  # Brief pause before retry
                    continue
                
                response.raise_for_status()
                
                if "status" in data and data["status"] != "ok":
                    raise ValueError(f"Twelve Data error: {data}")
                
                return IndicatorPayload(indicator=indicator, symbol=symbol, data=data)
                
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and used_key:
                    self._mark_rate_limited(used_key, block_duration_seconds=60)
                    logger.warning(
                        "HTTP 429 for %s. Attempt %d/%d",
                        indicator,
                        attempt + 1,
                        max_retries,
                    )
                    last_error = exc
                    await asyncio.sleep(0.5)
                    continue
                raise
            except RuntimeError as exc:
                # "All keys exhausted" from _get_key()
                logger.error("Cannot get usable key for %s: %s", indicator, exc)
                raise
            except Exception as exc:
                last_error = exc
                if attempt == max_retries - 1:
                    raise
        
        # If all retries failed
        if last_error:
            raise last_error
        raise RuntimeError(f"All API keys exhausted for {indicator}")

    async def fetch_time_series(
        self,
        symbol: str,
        interval: str = "30min",
        outputsize: int = 5,
    ) -> List[TimeSeriesBar]:
        max_retries = len(self._keys)  # Try all available keys
        last_error = None
        used_key = None
        
        for attempt in range(max_retries):
            try:
                used_key = self._get_key()
                params: Dict[str, str] = {
                    "symbol": symbol,
                    "interval": interval,
                    "outputsize": str(outputsize),
                    "apikey": used_key,
                }
                response = await self._client.get("/time_series", params=params)
                
                # Check for rate limit in response body first
                data = response.json()
                if isinstance(data, dict) and data.get("code") == 429:
                    self._mark_rate_limited(used_key, block_duration_seconds=60)
                    logger.warning(
                        "Rate limit hit (body 429) for time_series. Attempt %d/%d",
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(0.5)
                    continue
                
                response.raise_for_status()
                
                if data.get("status") != "ok":
                    raise ValueError(f"Twelve Data error: {data}")
                
                values = data.get("values", [])
                if not values:
                    return []
                return [TimeSeriesBar.from_api(symbol, item) for item in values]
                
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and used_key:
                    self._mark_rate_limited(used_key, block_duration_seconds=60)
                    logger.warning(
                        "HTTP 429 for time_series. Attempt %d/%d",
                        attempt + 1,
                        max_retries,
                    )
                    last_error = exc
                    await asyncio.sleep(0.5)
                    continue
                raise
            except RuntimeError as exc:
                # "All keys exhausted" from _get_key()
                logger.error("Cannot get usable key for time_series: %s", exc)
                raise
            except Exception as exc:
                last_error = exc
                if attempt == max_retries - 1:
                    raise
        
        if last_error:
            raise last_error
        raise RuntimeError("All API keys exhausted for time_series")


async def poll(symbol: str, interval: int, handler: Callable[[IndicatorPayload], None]) -> None:
    client = TwelveDataClient(settings.twelve_data.api_keys)
    per_request_sleep = max(interval // len(INDICATORS), 1)
    while True:
        for indicator in INDICATORS:
            try:
                payload = await client.fetch_indicator(symbol=symbol, indicator=indicator, interval="5min")
                handler(payload)
                publish(
                    TWELVEDATA_CHANNEL,
                    {
                        "symbol": symbol,
                        "indicator": indicator,
                        "payload": payload.data,
                        "polled_at": datetime.utcnow().isoformat(),
                    },
                )
            except httpx.HTTPError as exc:
                logger.warning("Twelve Data HTTP error: %s", exc)
                publish(
                    HEALTH_CHANNEL,
                    {
                        "source": "twelve_data",
                        "status": "error",
                        "detail": str(exc),
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected Twelve Data error: %s", exc)
                publish(
                    HEALTH_CHANNEL,
                    {
                        "source": "twelve_data",
                        "status": "exception",
                        "detail": str(exc),
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            await asyncio.sleep(per_request_sleep)


async def poll_time_series(
    symbol: str,
    interval: str,
    handler: Callable[[TimeSeriesBar], None],
    *,
    publish_symbol: Optional[str] = None,
    poll_interval: int = 60,
    outputsize: int = 5,
) -> None:
    client = TwelveDataClient(settings.twelve_data.api_keys)
    last_timestamp: Optional[datetime] = None
    publish_symbol = publish_symbol or symbol.replace("/", "")

    while True:
        try:
            bars = await client.fetch_time_series(symbol=symbol, interval=interval, outputsize=outputsize)
            new_bars = [bar for bar in bars if last_timestamp is None or bar.timestamp > last_timestamp]
            new_bars.sort(key=lambda bar: bar.timestamp)
            for bar in new_bars:
                handler(bar)
                publish(
                    KLINE_CHANNEL,
                    bar.to_message(symbol=publish_symbol, interval=interval),
                )
                last_timestamp = bar.timestamp
        except httpx.HTTPError as exc:
            logger.warning("Twelve Data time series HTTP error: %s", exc)
            publish(
                HEALTH_CHANNEL,
                {
                    "source": "twelve_data_time_series",
                    "status": "error",
                    "detail": str(exc),
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected Twelve Data time series error: %s", exc)
            publish(
                HEALTH_CHANNEL,
                {
                    "source": "twelve_data_time_series",
                    "status": "exception",
                    "detail": str(exc),
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
        await asyncio.sleep(poll_interval)
