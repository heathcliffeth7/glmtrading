import logging
from typing import Optional


_base_configured = False


class TelegramLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "skip_telegram", False):
            return

        if record.name.startswith("app.utils.telegram"):
            return

        try:
            from app.utils.telegram import format_markdown, telegram_client
        except Exception:
            return

        if not record.name.startswith("app."):
            return

        if not telegram_client.enabled():
            return

        try:
            message = self.format(record)
            telegram_client.send_message(format_markdown(message))
        except Exception:
            pass


def configure_logging(level: str = "INFO") -> None:
    global _base_configured

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    if not _base_configured:
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
        _base_configured = True
    else:
        logging.getLogger().setLevel(numeric_level)

    app_logger = logging.getLogger("app")
    app_logger.setLevel(numeric_level)

    handler_exists = any(isinstance(h, TelegramLogHandler) for h in app_logger.handlers)
    if not handler_exists:
        handler = TelegramLogHandler(level=logging.WARNING)  # Only send WARNING and ERROR to Telegram
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        app_logger.addHandler(handler)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or __name__)
