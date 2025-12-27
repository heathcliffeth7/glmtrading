"""
Response Extractor - SSE network interception for Qwen responses.

Primary: Network response interception (fast, reliable)
Fallback: DOM polling (for edge cases)
"""

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

from playwright.async_api import Page, Response

from app.utils.logging import get_logger

logger = get_logger(__name__)


class ResponseExtractor:
    """
    Extract AI responses from Qwen via network interception.

    Strategy:
    1. Intercept network responses to /api/v2/chat/completions
    2. Parse SSE stream chunks
    3. Accumulate content until [DONE]
    4. Fall back to DOM extraction if network fails
    """

    # API endpoints to intercept
    COMPLETION_ENDPOINTS = [
        "/api/v2/chat/completions",
        "/api/v2/chats/",  # matches /api/v2/chats/{id}/completions
    ]

    def __init__(self):
        """Initialize response extractor."""
        self._response_future: Optional[asyncio.Future] = None
        self._accumulated_content = ""
        self._is_complete = False
        self._response_handler: Optional[Callable] = None
        self._error: Optional[Exception] = None

    async def setup_interception(self, page: Page):
        """
        Setup network response interception.

        Args:
            page: Page to intercept responses on
        """
        self._accumulated_content = ""
        self._is_complete = False
        self._error = None

        async def on_response(response: Response):
            """Handle network response."""
            url = response.url

            # Check if this is a completion response
            is_completion = any(ep in url for ep in self.COMPLETION_ENDPOINTS)
            if not is_completion:
                return

            content_type = response.headers.get("content-type", "")
            logger.debug(
                "Intercepted completion response: %s (content-type: %s)",
                url[:80],
                content_type,
            )

            try:
                if "text/event-stream" in content_type:
                    # SSE streaming response
                    body = await response.body()
                    content = self._parse_sse(body)

                    if content:
                        self._accumulated_content = content
                        self._is_complete = True

                        if self._response_future and not self._response_future.done():
                            self._response_future.set_result(content)

                elif "application/json" in content_type:
                    # JSON response (non-streaming)
                    body = await response.body()
                    content = self._parse_json_response(body)

                    if content:
                        self._accumulated_content = content
                        self._is_complete = True

                        if self._response_future and not self._response_future.done():
                            self._response_future.set_result(content)

            except Exception as e:
                logger.error("Error processing response: %s", e)
                self._error = e
                if self._response_future and not self._response_future.done():
                    self._response_future.set_exception(e)

        # Properly cleanup old handler before adding new one
        if self._response_handler:
            try:
                page.remove_listener("response", self._response_handler)
                logger.debug("Removed old response handler")
            except Exception as e:
                logger.debug("Could not remove old handler: %s", e)

        self._response_handler = on_response
        page.on("response", on_response)
        logger.debug("Response interception setup complete")

    def _parse_sse(self, raw_bytes: bytes) -> str:
        """
        Parse SSE stream into content string.

        SSE format:
            data: {"choices": [{"delta": {"content": "Hello"}}]}
            data: {"choices": [{"delta": {"content": " World"}}]}
            data: [DONE]

        Args:
            raw_bytes: Raw SSE response bytes

        Returns:
            Accumulated content string
        """
        content = ""

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("utf-8", errors="ignore")

        for line in text.split("\n"):
            line = line.strip()

            if not line:
                continue

            if line.startswith("data:"):
                data_str = line[5:].strip()

                # Check for stream end
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)

                    # Standard OpenAI-style streaming format
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        chunk = delta.get("content", "")
                        if chunk:
                            content += chunk

                    # Alternative format (some Qwen responses)
                    if not content and "content" in data:
                        content = data["content"]

                except json.JSONDecodeError:
                    # Not JSON, might be partial data
                    continue

        return content

    def _parse_json_response(self, raw_bytes: bytes) -> str:
        """
        Parse JSON (non-streaming) response.

        Args:
            raw_bytes: Raw JSON response bytes

        Returns:
            Content string
        """
        try:
            text = raw_bytes.decode("utf-8")
            data = json.loads(text)

            # Standard format
            choices = data.get("choices", [])
            if choices:
                message = choices[0].get("message", {})
                return message.get("content", "")

            # Alternative format
            if "content" in data:
                return data["content"]

            # Data wrapper format
            if "data" in data:
                inner = data["data"]
                if isinstance(inner, dict):
                    choices = inner.get("choices", [])
                    if choices:
                        message = choices[0].get("message", {})
                        return message.get("content", "")

            return ""

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug("Failed to parse JSON response: %s", e)
            return ""

    async def wait_for_response(self, timeout: float = 120.0) -> str:
        """
        Wait for SSE response to complete.

        Args:
            timeout: Maximum wait time in seconds

        Returns:
            Accumulated content string

        Raises:
            asyncio.TimeoutError: If timeout reached
            Exception: If error occurred during interception
        """
        self._response_future = asyncio.get_event_loop().create_future()

        try:
            content = await asyncio.wait_for(self._response_future, timeout)
            return content

        except asyncio.TimeoutError:
            # Return accumulated content if any
            if self._accumulated_content:
                logger.warning(
                    "Response timeout but got partial content (%d chars)",
                    len(self._accumulated_content),
                )
                return self._accumulated_content
            raise

        finally:
            self._response_future = None

    async def extract_from_dom(
        self,
        page: Page,
        timeout: int = 60,
        poll_interval: float = 0.5,
    ) -> str:
        """
        Extract response from DOM (fallback method).

        Polls DOM for response content until stable.

        Args:
            page: Page to extract from
            timeout: Maximum wait time in seconds
            poll_interval: Time between polls in seconds

        Returns:
            Response content string
        """
        # Multiple selector strategies
        selectors = [
            ".markdown-body:last-of-type",
            "[class*='message-content']:last-of-type",
            "[class*='assistant']:last-of-type",
            "[class*='response']:last-of-type",
            "[class*='answer']:last-of-type",
        ]

        last_content = ""
        stable_count = 0
        required_stable = 3  # Need 3 consecutive unchanged polls

        start_time = asyncio.get_event_loop().time()
        end_time = start_time + timeout

        while asyncio.get_event_loop().time() < end_time:
            current_content = ""

            # Try each selector
            for selector in selectors:
                try:
                    element = page.locator(selector).last
                    if await element.is_visible(timeout=500):
                        current_content = await element.inner_text(timeout=1000)
                        if current_content:
                            break
                except Exception:
                    continue

            # Check stability
            if current_content and current_content == last_content:
                stable_count += 1
                if stable_count >= required_stable:
                    logger.debug(
                        "DOM response stable after %d polls (%d chars)",
                        stable_count,
                        len(current_content),
                    )
                    return current_content.strip()
            else:
                stable_count = 0
                last_content = current_content

            await asyncio.sleep(poll_interval)

        # Return whatever we have
        if last_content:
            logger.warning(
                "DOM extraction timeout but got content (%d chars)",
                len(last_content),
            )
            return last_content.strip()

        return ""

    async def extract_response(
        self,
        page: Page,
        timeout: float = 120.0,
        use_dom_fallback: bool = True,
    ) -> str:
        """
        Extract response using network interception with DOM fallback.

        Args:
            page: Page to extract response from
            timeout: Maximum wait time
            use_dom_fallback: Whether to try DOM extraction on failure

        Returns:
            Response content string

        Raises:
            RuntimeError: If no response could be extracted
        """
        try:
            # Try network interception first
            content = await self.wait_for_response(timeout)
            if content:
                return content

        except asyncio.TimeoutError:
            if self._accumulated_content:
                return self._accumulated_content

            if use_dom_fallback:
                logger.warning("Network timeout, trying DOM extraction...")

        except Exception as e:
            logger.warning("Network extraction failed: %s", e)
            if use_dom_fallback:
                logger.info("Trying DOM extraction fallback...")

        # DOM fallback
        if use_dom_fallback:
            content = await self.extract_from_dom(page, timeout=30)
            if content:
                return content

        raise RuntimeError("Failed to extract response from Qwen")

    def reset(self):
        """Reset extractor state for new request."""
        self._accumulated_content = ""
        self._is_complete = False
        self._error = None
        if self._response_future and not self._response_future.done():
            self._response_future.cancel()
        self._response_future = None

    @property
    def accumulated_content(self) -> str:
        """Get currently accumulated content."""
        return self._accumulated_content

    @property
    def is_complete(self) -> bool:
        """Check if response is complete."""
        return self._is_complete
