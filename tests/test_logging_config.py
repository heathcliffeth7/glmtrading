import logging

from app.utils.logging import TelegramLogHandler, configure_logging


def test_configure_logging_idempotent(monkeypatch) -> None:
    for handler in list(logging.getLogger("app").handlers):
        logging.getLogger("app").removeHandler(handler)

    configure_logging("INFO")
    first_handlers = list(logging.getLogger("app").handlers)

    configure_logging("DEBUG")
    second_handlers = list(logging.getLogger("app").handlers)

    assert first_handlers == second_handlers
    assert any(isinstance(handler, TelegramLogHandler) for handler in first_handlers)
