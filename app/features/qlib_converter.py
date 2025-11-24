from pathlib import Path
from typing import Any, Dict, Iterable

import pandas as pd

from app.config.settings import get_settings
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)


class QLibConverter:
    def __init__(self, base_path: str | None = None) -> None:
        self._base_path = Path(base_path or settings.qlib.data_path)
        self._base_path.mkdir(parents=True, exist_ok=True)

    def append_records(self, symbol: str, records: Iterable[Dict[str, Any]]) -> None:
        file_path = self._base_path / f"{symbol.upper()}.parquet"
        df = pd.DataFrame(list(records))
        if df.empty:
            return
        if file_path.exists():
            df_existing = pd.read_parquet(file_path)
            df = (
                pd.concat([df_existing, df], ignore_index=True)
                .drop_duplicates(subset=["datetime"])
                .sort_values("datetime")
            )
        df.to_parquet(file_path, index=False)
        logger.debug("Appended %d rows to %s", len(df), file_path)
