import asyncio
from datetime import datetime

from app.config.settings import get_settings
from app.data_feeds.binance_futures import poll as futures_poll
from app.data_feeds.binance_ws import stream as ws_stream
from app.data_feeds.constants import HEALTH_CHANNEL
from app.data_feeds.twelve_data import poll as twelve_poll
from app.data_feeds.twelve_data import poll_time_series
from app.utils.logging import configure_logging, get_logger
from app.utils.redis import publish

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


async def start_binance_ws() -> None:
    await asyncio.gather(
        ws_stream(symbol="BTCUSDT", handler=lambda _: None, interval="1m"),
        ws_stream(symbol="BTCUSDT", handler=lambda _: None, interval="5m"),
    )


async def start_binance_futures() -> None:
    await futures_poll(symbol="BTCUSDT", interval=60, handler=lambda _: None)


async def start_twelve_data() -> None:
    await asyncio.gather(
        twelve_poll(symbol="BTC/USD", interval=300, handler=lambda _: None),
        poll_time_series(
            symbol="BTC/USD",
            interval="5min",
            handler=lambda _: None,
            publish_symbol="BTCUSDT",
            poll_interval=60,
            outputsize=200,
        ),
    )


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
        start_twelve_data(),
    )


def main() -> None:
    try:
        asyncio.run(orchestrator())
    except KeyboardInterrupt:
        logger.info("Orchestrator stopped by user")


if __name__ == "__main__":
    main()
