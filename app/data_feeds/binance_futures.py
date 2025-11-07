import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional

import httpx

from app.config.settings import get_settings
from app.data_feeds.constants import FUTURES_CHANNEL, HEALTH_CHANNEL
from app.utils.logging import get_logger
from app.utils.redis import publish

settings = get_settings()
logger = get_logger(__name__)


@dataclass
class FuturesSnapshot:
    symbol: str
    long_short_ratio: float
    open_interest: float
    funding_rate: float


class BinanceFuturesClient:
    def __init__(self, session: Optional[httpx.AsyncClient] = None) -> None:
        self._base_url = str(settings.binance.futures_rest_endpoint)
        self._client = session or httpx.AsyncClient(base_url=self._base_url, timeout=10.0)

    async def fetch_metrics(self, symbol: str) -> FuturesSnapshot:
        # Use limit=1 to get only the most recent data (not historical)
        base_params = {"symbol": symbol.upper(), "period": "5m", "limit": 1}
        long_short_ratio = await self._fetch_indicator(
            "futures/data/globalLongShortAccountRatio", base_params
        )
        open_interest = await self._fetch_indicator("futures/data/openInterestHist", base_params)
        funding_rate = await self._fetch_indicator(
            "fapi/v1/fundingRate", {"symbol": symbol.upper(), "limit": 1}
        )
        return FuturesSnapshot(
            symbol=symbol,
            long_short_ratio=float(long_short_ratio[0]["longShortRatio"]),
            open_interest=float(open_interest[0]["sumOpenInterestValue"]),
            funding_rate=float(funding_rate[0]["fundingRate"]),
        )

    async def _fetch_indicator(self, path: str, params: Dict[str, str]) -> list[dict]:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected response: {data}")
        return data


async def poll(symbol: str, interval: int, handler: Callable[[FuturesSnapshot], None]) -> None:
    client = BinanceFuturesClient()
    while True:
        try:
            snapshot = await client.fetch_metrics(symbol)
            handler(snapshot)
            publish(
                FUTURES_CHANNEL,
                {
                    "symbol": symbol,
                    "payload": snapshot.__dict__,
                    "polled_at": datetime.utcnow().isoformat(),
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("Binance Futures polling error: %s", exc)
            publish(
                HEALTH_CHANNEL,
                {
                    "source": "binance_futures",
                    "status": "error",
                    "detail": str(exc),
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error while polling futures data: %s", exc)
            publish(
                HEALTH_CHANNEL,
                {
                    "source": "binance_futures",
                    "status": "exception",
                    "detail": str(exc),
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
        await asyncio.sleep(interval)
