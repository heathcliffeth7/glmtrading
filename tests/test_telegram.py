from types import SimpleNamespace

import httpx

from app.utils import telegram


def test_format_markdown_escape() -> None:
    text = "*_[]()~`>#+-=|{}.!"
    escaped = telegram.format_markdown(text)
    assert escaped == "\\*\\_\\[\\]\\(\\)\\~\\`\\>\\#\\+\\-\\=\\|\\{\\}\\.\\!"


def test_telegram_client_send(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, *, json: dict, timeout: int) -> httpx.Response:  # type: ignore[override]
        calls.append((url, json))
        response = httpx.Response(200)
        return response

    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    client = telegram.TelegramClient()
    client._token = "token"  # type: ignore[attr-defined]
    client._chat_id = "chat"  # type: ignore[attr-defined]
    client._base_url = "https://api.telegram.org/bottoken"  # type: ignore[attr-defined]

    client.send_message("hello")

    assert calls, "httpx.post should be called when telegram enabled"
    url, payload = calls[0]
    assert url == "https://api.telegram.org/bottoken/sendMessage"
    assert payload == {
        "chat_id": "chat",
        "text": "hello",
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }


def test_telegram_client_disabled(monkeypatch) -> None:
    called = False

    def fake_post(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(telegram.httpx, "post", fake_post)
    client = telegram.TelegramClient()
    client._token = ""  # type: ignore[attr-defined]
    client._chat_id = ""  # type: ignore[attr-defined]
    client._base_url = None  # type: ignore[attr-defined]

    client.send_message("hello")

    assert called is False
