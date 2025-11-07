import json
import time
from typing import Any, Dict, List

import httpx

from app.config.settings import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class TelegramClient:
    def __init__(self) -> None:
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_channel_id
        self._base_url = f"https://api.telegram.org/bot{self._token}" if self._token else None

    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send_message(self, text: str, auto_split: bool = True) -> None:
        """
        Send a message to Telegram with automatic splitting for long messages.

        Args:
            text: Message text to send
            auto_split: If True, automatically split messages longer than 4096 chars
        """
        if not self.enabled():
            logger.debug("Telegram disabled; message skipped")
            return

        # Telegram's hard limit is 4096 characters
        max_length = 4096

        if len(text) <= max_length:
            # Message fits in one part
            self._send_single_message(text)
        elif auto_split:
            # Split message into multiple parts
            chunks = self._split_message(text, max_length)
            total_chunks = len(chunks)

            logger.info("Splitting Telegram message into %d parts", total_chunks)

            for i, chunk in enumerate(chunks, 1):
                # Add part indicator
                if total_chunks > 1:
                    part_header = f"[{i}/{total_chunks}]\n\n"
                    chunk_with_header = part_header + chunk
                else:
                    chunk_with_header = chunk

                self._send_single_message(chunk_with_header)

                # Small delay between messages to avoid rate limiting
                if i < total_chunks:
                    time.sleep(0.3)
        else:
            # Truncate without splitting
            truncated = text[: max_length - 20] + "\n\n...(truncated)"
            self._send_single_message(truncated)

    def _send_single_message(self, text: str) -> None:
        """
        Send a single message to Telegram with format fallback.

        Args:
            text: Message text to send (must be <= 4096 chars)
        """
        url = f"{self._base_url}/sendMessage"

        # Try MarkdownV2 first, fallback to plain text
        for parse_mode in ["MarkdownV2", "Markdown", None]:
            payload: Dict[str, Any] = {
                "chat_id": self._chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }

            if parse_mode:
                payload["parse_mode"] = parse_mode

            try:
                response = httpx.post(url, json=payload, timeout=10)
                response.raise_for_status()
                return  # Success!
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400 and parse_mode:
                    # Bad format, try next mode
                    logger.debug("Format %s failed, trying fallback", parse_mode)
                    continue
                elif exc.response.status_code == 429:
                    # Rate limited - wait and retry once
                    retry_after = int(exc.response.headers.get("Retry-After", 1))
                    logger.warning("Telegram rate limited, waiting %ds", retry_after)
                    time.sleep(retry_after)
                    # Retry with plain text
                    payload["parse_mode"] = None
                    try:
                        response = httpx.post(url, json=payload, timeout=10)
                        response.raise_for_status()
                        return
                    except Exception as retry_exc:
                        logger.error("Failed to send telegram message after retry: %s", retry_exc)
                        return
                logger.error("Failed to send telegram message: %s", exc)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to send telegram message: %s", exc)
                return

    def _split_message(self, text: str, max_length: int) -> List[str]:
        """
        Split a long message into chunks at natural break points.

        Args:
            text: Text to split
            max_length: Maximum length per chunk

        Returns:
            List of text chunks
        """
        # Reserve space for part indicator [X/Y]\n\n (about 10 chars)
        chunk_size = max_length - 15

        if len(text) <= chunk_size:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= chunk_size:
                chunks.append(remaining)
                break

            # Try to find a good break point (prefer double newline, then single newline, then space)
            break_point = chunk_size

            # Look for double newline (section break)
            double_newline_pos = remaining.rfind("\n\n", 0, chunk_size)
            if double_newline_pos > chunk_size * 0.7:  # If it's in the last 30% of chunk
                break_point = double_newline_pos + 2
            else:
                # Look for single newline
                newline_pos = remaining.rfind("\n", 0, chunk_size)
                if newline_pos > chunk_size * 0.6:  # If it's in the last 40% of chunk
                    break_point = newline_pos + 1
                else:
                    # Look for space
                    space_pos = remaining.rfind(" ", 0, chunk_size)
                    if space_pos > chunk_size * 0.5:  # If it's in the last 50% of chunk
                        break_point = space_pos + 1
                    else:
                        # Hard break at chunk_size
                        break_point = chunk_size

            chunk = remaining[:break_point].rstrip()
            chunks.append(chunk)
            remaining = remaining[break_point:].lstrip()

        return chunks


telegram_client = TelegramClient()


def format_markdown(text: str) -> str:
    specials = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{ch}" if ch in specials else ch for ch in text)
