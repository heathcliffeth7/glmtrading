from pathlib import Path

import pandas as pd

from app.config.settings import get_settings
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)


def run_workflow(config_path: str) -> None:
    logger.info("Running QLib workflow with config: %s", config_path)
    # Placeholder: QLib workflow entegrasyonu burada çalıştırılacak
    if not Path(config_path).exists():
        raise FileNotFoundError(config_path)
    _ = pd.DataFrame()
