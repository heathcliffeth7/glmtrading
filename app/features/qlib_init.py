"""QLib veri deposunu başlatma betiği."""

from pathlib import Path

import qlib

from app.config.settings import get_settings
from app.utils.logging import configure_logging, get_logger


settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


def main() -> None:
    data_path = Path(settings.qlib.data_path)
    data_path.mkdir(parents=True, exist_ok=True)
    logger.info("Initializing QLib data at %s", data_path)
    qlib.init(provider_uri=str(data_path), region=settings.qlib.region, expression_cache=False)


if __name__ == "__main__":
    main()
