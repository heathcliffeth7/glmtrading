import asyncio

from app.data_feeds.constants import KLINE_CHANNEL
from app.features.feature_worker import FeatureWorker
from app.features.listeners import start_stream_listener
from app.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def start_feature_workers(symbols: list[str], intervals: list[str]) -> None:
    workers = [
        FeatureWorker(symbol=symbol, interval=interval)
        for symbol in symbols
        for interval in intervals
    ]
    await asyncio.gather(*(worker.run() for worker in workers))
