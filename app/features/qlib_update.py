"""QLib veri deposuna günlük batch ekleme betiği."""

from pathlib import Path
from typing import Iterable

import pandas as pd

from app.config.settings import get_settings
from app.utils.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


def append_parquet(symbol: str, records: Iterable[dict]) -> None:
    data_path = Path(settings.qlib.data_path) / f"{symbol.upper()}.parquet"
    df = pd.DataFrame(records)
    if data_path.exists():
        existing = pd.read_parquet(data_path)
        df = pd.concat([existing, df], ignore_index=True).drop_duplicates(subset=["datetime"])
    df.to_parquet(data_path, index=False)
    logger.info("Written %d records to %s", len(df), data_path)


if __name__ == "__main__":
    logger.info("QLib data update script invoked")
