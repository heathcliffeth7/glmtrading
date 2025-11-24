import asyncio
from datetime import datetime

from app.config.settings import get_settings
from app.data_feeds.binance_futures import poll as futures_poll
from app.data_feeds.binance_ws import stream as ws_stream
from app.data_feeds.constants import HEALTH_CHANNEL
from app.utils.logging import configure_logging, get_logger
from app.utils.redis import publish


settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


async def start_binance_ws() -> None:
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    tasks = []
    for symbol in symbols:
        tasks.append(ws_stream(symbol=symbol, handler=lambda _: None, interval="1m"))
        tasks.append(ws_stream(symbol=symbol, handler=lambda _: None, interval="5m"))
        tasks.append(ws_stream(symbol=symbol, handler=lambda _: None, interval="15m"))
    
    await asyncio.gather(*tasks)


async def start_binance_futures() -> None:
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    tasks = []
    for symbol in symbols:
        tasks.append(futures_poll(symbol=symbol, interval=60, handler=lambda _: None))
    
    await asyncio.gather(*tasks)


async def orchestrator() -> None:
    publish(
        HEALTH_CHANNEL,
        {
            "source": "orchestrator",
            "status": "starting",
            "detail": "Launching data feeds",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )

    await asyncio.gather(
        start_binance_ws(),
        start_binance_futures(),
    )


def main() -> None:
    try:
        asyncio.run(orchestrator())
    except KeyboardInterrupt:
        logger.info("Orchestrator stopped by user")


if __name__ == "__main__":
    main()
