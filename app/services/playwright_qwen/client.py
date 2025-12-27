"""
Playwright Qwen Client - Main async client for Qwen API via browser.

Stealth browser ile chat.qwen.ai'ye baglanir.
SSE network interception ile response alir.
"""

import asyncio
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging import get_logger

from .browser_manager import BrowserManager
from .response_extractor import ResponseExtractor
from .session_manager import SessionExpiredError, SessionManager

logger = get_logger(__name__)

# Constants
QWEN_BASE_URL = "https://chat.qwen.ai"
DEFAULT_MODEL = "qwen-max-latest"
DEFAULT_TIMEOUT = 180  # seconds


class PlaywrightQwenClient:
    """
    Async Playwright-based Qwen client.

    Uses stealth browser to interact with chat.qwen.ai.
    Extracts responses via network interception.

    Usage:
        client = PlaywrightQwenClient()
        response = await client.request([{"role": "user", "content": "Hello"}])
        await client.close()
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout_sec: int = DEFAULT_TIMEOUT,
        headless: Optional[bool] = None,
        session_file: Optional[Path] = None,
    ):
        """
        Initialize Playwright Qwen client.

        Args:
            model: Qwen model to use
            timeout_sec: Request timeout in seconds
            headless: Force headless mode (None = auto-detect)
            session_file: Path to session storage file
        """
        self._model = model
        self._timeout_sec = timeout_sec

        self._browser_manager = BrowserManager(headless=headless)
        self._session_manager = SessionManager(session_file=session_file)
        self._response_extractor = ResponseExtractor()

        self._initialized = False
        self._page = None

        logger.info(
            "PlaywrightQwenClient init: model=%s, timeout=%ds",
            model,
            timeout_sec,
        )

    async def _ensure_initialized(self):
        """Initialize browser if not already done."""
        if self._initialized and self._page:
            # Verify browser is healthy
            if await self._browser_manager.is_healthy():
                return
            logger.warning("Browser unhealthy, reinitializing...")

        # Get storage state path
        storage_state = self._session_manager.get_storage_state_path()

        if not storage_state:
            raise SessionExpiredError(
                f"No session file found. Please login manually and save session to: "
                f"{self._session_manager.session_file}"
            )

        # Initialize browser with session
        self._page = await self._browser_manager.init_browser(storage_state)
        self._initialized = True

    async def _navigate_to_chat(self):
        """Navigate to Qwen chat page - always start fresh."""
        # Always navigate to base URL to ensure clean state
        # This prevents issues with leftover Monaco editor overlays
        logger.info("Navigating to Qwen chat (fresh state)...")
        await self._page.goto(
            QWEN_BASE_URL,
            wait_until="domcontentloaded",
            timeout=20000,  # Reduced from 30s
        )

        # Wait for page to be interactive (reduced timeout)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            # networkidle can timeout, continue anyway
            pass

    async def _validate_session(self):
        """Validate user is logged in."""
        await self._session_manager.validate_session(self._page)

    async def _find_textarea(self):
        """Find and return the chat input textarea."""
        # Wait for page to be interactive first (reduced timeout)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass  # Continue anyway
        await asyncio.sleep(0.5)  # Reduced extra wait for JS rendering

        selectors = [
            "textarea",
            "[placeholder*='help']",  # "How can I help you today?"
            "[class*='chat-input']",
            "[class*='message-input']",
            "[contenteditable='true']",
        ]

        for selector in selectors:
            try:
                element = self._page.locator(selector).first
                if await element.is_visible(timeout=5000):
                    logger.debug("Found textarea with selector: %s", selector)
                    return element
            except Exception:
                continue

        # Last resort: wait for any textarea
        try:
            await self._page.wait_for_selector("textarea", timeout=10000)
            return self._page.locator("textarea").first
        except Exception:
            pass

        raise RuntimeError("Could not find chat input textarea")

    async def _find_send_button(self):
        """Find and return the send button."""
        selectors = [
            "[class*='send']",
            "button[type='submit']",
            "[aria-label*='send']",
            "[aria-label*='Send']",
        ]

        for selector in selectors:
            try:
                element = self._page.locator(selector).first
                if await element.is_visible(timeout=1000):
                    return element
            except Exception:
                continue

        return None  # Will use Enter key instead

    async def _type_message(self, textarea, message: str):
        """
        Type message with human-like behavior.

        Args:
            textarea: Textarea element to type into
            message: Message to type
        """
        # Use JavaScript to focus instead of click (avoids overlay issues)
        try:
            await textarea.evaluate("el => el.focus()")
        except Exception:
            # Fallback to click with force
            await textarea.click(force=True, timeout=5000)

        await asyncio.sleep(random.uniform(0.1, 0.3))

        # Clear any existing content using JavaScript
        try:
            await textarea.evaluate("el => { el.value = ''; el.dispatchEvent(new Event('input', {bubbles: true})); }")
        except Exception:
            await textarea.fill("")

        await asyncio.sleep(random.uniform(0.05, 0.15))

        # Use fill for all messages (faster and more reliable)
        await textarea.fill(message)

        await asyncio.sleep(random.uniform(0.1, 0.2))

    async def _send_message(self, textarea):
        """
        Send the message (click send button or press Enter).

        Args:
            textarea: Textarea element
        """
        send_button = await self._find_send_button()

        if send_button:
            await send_button.click()
        else:
            # Use Enter key
            await textarea.press("Enter")

        logger.debug("Message sent")

    def _is_browser_crash(self, error: Exception) -> bool:
        """Check if exception indicates browser crash."""
        error_str = str(error).lower()
        crash_indicators = [
            "target crashed",
            "page crashed",
            "browser has been closed",
            "target closed",
            "connection closed",
            "execution context was destroyed",
        ]
        return any(indicator in error_str for indicator in crash_indicators)

    async def _do_request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal request implementation."""
        start_time = time.time()

        # Extract user message
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        if not user_message:
            raise RuntimeError("No user message found in messages")

        # Initialize browser
        await self._ensure_initialized()

        # Navigate to chat
        await self._navigate_to_chat()

        # Validate session
        await self._validate_session()

        # Setup response interception
        self._response_extractor.reset()
        await self._response_extractor.setup_interception(self._page)

        # Find and fill textarea
        textarea = await self._find_textarea()
        await self._type_message(textarea, user_message)

        # Send message
        await self._send_message(textarea)

        # Wait for response (reduced timeout)
        content = await self._response_extractor.extract_response(
            self._page,
            timeout=min(self._timeout_sec, 90),  # Cap at 90s
            use_dom_fallback=True,
        )

        if not content:
            raise RuntimeError("Empty response from Qwen")

        latency_ms = (time.time() - start_time) * 1000

        logger.info(
            "Playwright response: %d chars, %.1fms",
            len(content),
            latency_ms,
        )

        # Build GLMClient-compatible response
        prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 4
        completion_tokens = len(content) // 4

        # Save session after successful request to keep it fresh
        try:
            await self.save_session()
            logger.debug("Session saved after successful request")
        except Exception as e:
            logger.warning("Failed to save session: %s", e)

        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "model": self._model,
            "_playwright_latency_ms": latency_ms,
            "_method": "playwright_stealth",
        }

    async def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send request to Qwen via browser with crash recovery.

        Args:
            messages: List of message dicts [{"role": "user", "content": "..."}]
            trace_id: Optional trace ID for logging

        Returns:
            Response dict compatible with GLMClient format:
            {
                "choices": [{"message": {"role": "assistant", "content": "..."}}],
                "usage": {"prompt_tokens": N, "completion_tokens": M},
                "model": "qwen-max-latest",
                "_playwright_latency_ms": 1234.5,
            }

        Raises:
            SessionExpiredError: If session has expired
            RuntimeError: If request fails
        """
        max_retries = 2
        last_error = None

        logger.info(
            "Sending Playwright request: %d messages, %d chars, trace=%s",
            len(messages),
            sum(len(m.get("content", "")) for m in messages),
            trace_id or "none",
        )

        for attempt in range(max_retries):
            try:
                return await self._do_request(messages, trace_id)

            except SessionExpiredError:
                # Take screenshot and re-raise - no retry for session errors
                await self._browser_manager.take_screenshot("session_expired")
                raise

            except Exception as e:
                last_error = e

                # Check if browser crashed
                if self._is_browser_crash(e):
                    logger.warning(
                        "Browser crash detected (attempt %d/%d): %s",
                        attempt + 1,
                        max_retries,
                        e,
                    )

                    # Restart browser and retry
                    if attempt < max_retries - 1:
                        try:
                            storage_state = self._session_manager.get_storage_state_path()
                            self._page = await self._browser_manager.restart(storage_state)
                            self._initialized = True
                            logger.info("Browser restarted, retrying...")
                            continue
                        except Exception as restart_error:
                            logger.error("Browser restart failed: %s", restart_error)

                # Non-crash error or final attempt
                logger.error("Playwright request failed: %s", e)
                await self._browser_manager.take_screenshot("request_error")
                break

        raise RuntimeError(f"Playwright request failed after {max_retries} attempts: {last_error}") from last_error

    async def create_new_chat(self):
        """
        Create a new chat session (clear conversation).

        Navigates to base URL to start fresh chat.
        """
        await self._ensure_initialized()
        await self._page.goto(
            QWEN_BASE_URL,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        logger.info("Created new chat session")

    async def save_session(self):
        """Save current session to file."""
        if self._browser_manager.context:
            await self._session_manager.save_session(self._browser_manager.context)

    async def close(self):
        """Close browser and cleanup."""
        # Save session before closing
        try:
            await self.save_session()
        except Exception as e:
            logger.warning("Failed to save session on close: %s", e)

        await self._browser_manager.close()
        self._initialized = False
        self._page = None
        logger.info("PlaywrightQwenClient closed")

    @property
    def is_initialized(self) -> bool:
        """Check if client is initialized."""
        return self._initialized

    @property
    def model(self) -> str:
        """Get current model."""
        return self._model
