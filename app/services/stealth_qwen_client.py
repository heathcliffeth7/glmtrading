"""
Stealth Qwen Browser Client - Playwright + Stealth Plugin ile WAF Bypass.

QwenBrowserClientSync yerine kullanılır.
fetch() API ile hızlı request, DOM interaction yok.

Kullanım:
    client = QwenStealthClientSync()
    result = client.request([{"role": "user", "content": "Merhaba"}])
    client.close()
"""

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)

# Session dosyası
SESSION_FILE = Path("/root/trading/qwen_session.json")

# Default model
DEFAULT_MODEL = "qwen-max-latest"


class QwenStealthClient:
    """
    Async Playwright + Stealth client for Qwen API.

    Browser context içinde fetch() API kullanarak WAF'ı bypass eder.
    DOM interaction yerine direkt API çağrısı yapar (çok daha hızlı).
    """

    BASE_URL = "https://chat.qwen.ai"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout_sec: int = 180,
        headless: bool = False,  # Aliyun WAF headless tespit ediyor
        session_file: Optional[Path] = None,
    ):
        """
        Initialize stealth client.

        Args:
            model: Qwen model to use
            timeout_sec: Request timeout in seconds
            headless: Run browser in headless mode (NOT recommended for WAF)
            session_file: Path to session storage file
        """
        self._model = model
        self._timeout_sec = timeout_sec
        self._headless = headless
        self._session_file = session_file or SESSION_FILE

        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._initialized = False

    async def _init_browser(self) -> None:
        """Initialize browser with stealth settings."""
        if self._initialized:
            return

        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth
        except ImportError as e:
            logger.error("Playwright or stealth not installed: %s", e)
            raise RuntimeError("pip install playwright playwright-stealth") from e

        logger.info("Initializing stealth browser (headless=%s)...", self._headless)

        self._playwright = await async_playwright().start()

        # Launch browser
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        # Context options
        context_options = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
        }

        # Load session if exists
        if self._session_file.exists():
            logger.info("Loading session from %s", self._session_file)
            context_options["storage_state"] = str(self._session_file)
        else:
            logger.warning("Session file not found: %s", self._session_file)

        self._context = await self._browser.new_context(**context_options)
        self._page = await self._context.new_page()

        # Apply stealth
        stealth = Stealth()
        await stealth.apply_stealth_async(self._page)

        # Block Baxia anti-bot SDK (causes "Failed to fetch" on completions)
        await self._page.route("**/baxia**", lambda route: route.abort())
        await self._page.route("**/sd/baxia**", lambda route: route.abort())
        await self._page.route("**/baxiaCommon.js", lambda route: route.abort())

        # Save original fetch BEFORE any SDK can intercept it
        await self._page.add_init_script("""
            window.__originalFetch = window.fetch.bind(window);
            window.__fetchSaved = true;
        """)

        logger.info("Stealth applied, navigating to Qwen...")

        # Navigate to Qwen
        await self._page.goto(
            self.BASE_URL,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Check login status
        login_visible = await self._page.locator("text=Log in").is_visible()
        if login_visible:
            logger.error("NOT LOGGED IN - Session expired or invalid")
            raise RuntimeError("Qwen session expired. Please login manually.")

        # Verify stealth
        webdriver = await self._page.evaluate("navigator.webdriver")
        logger.info("Browser ready - navigator.webdriver=%s", webdriver)

        self._initialized = True

    async def _create_chat(self) -> str:
        """Create new chat session."""
        result = await self._page.evaluate("""
            async () => {
                try {
                    const r = await fetch('/api/v2/chats/new', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: '{}'
                    });
                    return await r.json();
                } catch(e) {
                    return {error: e.message};
                }
            }
        """)

        if result.get("error"):
            raise RuntimeError(f"Failed to create chat: {result['error']}")

        chat_id = result.get("data", {}).get("id") or result.get("id")
        if not chat_id:
            raise RuntimeError(f"No chat_id in response: {result}")

        logger.info("Created chat: %s", chat_id[:16])
        return chat_id

    async def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send request to Qwen API via browser fetch().

        Args:
            messages: List of message dicts (role, content)
            trace_id: Optional trace ID for logging

        Returns:
            Response dict compatible with GLMClient format
        """
        await self._init_browser()

        start_time = time.time()

        logger.info(
            "Sending request via DOM: %d messages, %d chars, model=%s",
            len(messages),
            sum(len(m.get("content", "")) for m in messages),
            self._model,
        )

        # Get the last message from the prompt
        user_message = messages[-1].get("content", "") if messages else ""
        if not user_message:
            raise RuntimeError("No user message provided")

        # Navigate to new chat (cleaner state)
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=15000)

        # Wait for textarea
        await self._page.wait_for_selector("textarea", timeout=10000)

        # Fill textarea with the prompt
        await self._page.fill("textarea", user_message)
        logger.info("Prompt filled in textarea (%d chars)", len(user_message))

        # Press Enter to send
        await self._page.press("textarea", "Enter")
        logger.info("Message sent, waiting for response...")

        # Wait for AI response - poll for response appearance
        max_wait_sec = self._timeout_sec
        poll_interval = 1.0
        content = ""

        for i in range(int(max_wait_sec / poll_interval)):
            await asyncio.sleep(poll_interval)

            # Check if response has appeared and is complete
            result = await self._page.evaluate("""
            () => {
                // Find all message elements - look for assistant response
                const body = document.body.innerText;

                // Check if still loading/typing
                const isTyping = document.querySelector('[class*="typing"]') ||
                                document.querySelector('[class*="loading"]') ||
                                document.querySelector('[class*="thinking"]');

                // Try to find the response content after the prompt
                // The page typically shows: user message, then assistant response
                return {
                    isTyping: !!isTyping,
                    bodyLength: body.length,
                    body: body
                };
            }
            """)

            is_typing = result.get("isTyping", False)

            # After first few seconds, if not typing anymore, response is ready
            if not is_typing and i >= 3:
                # Extract just the AI response from the page
                body_text = result.get("body", "")

                # Find the response - it comes after the user message
                if user_message[:50] in body_text:
                    # Response is everything after user message (simplified extraction)
                    idx = body_text.find(user_message[:50])
                    if idx >= 0:
                        after_prompt = body_text[idx + len(user_message):]
                        # Clean up - remove UI elements
                        lines = after_prompt.split("\n")
                        response_lines = []
                        for line in lines:
                            line = line.strip()
                            # Skip UI elements
                            if line and not any(skip in line.lower() for skip in [
                                "new chat", "coder", "projects", "all chats", "today",
                                "yesterday", "copy", "regenerate", "edit", "qwen"
                            ]):
                                response_lines.append(line)
                            # Stop at clear UI boundary
                            if len(response_lines) > 0 and any(stop in line.lower() for stop in [
                                "copy", "regenerate"
                            ]):
                                break
                        content = "\n".join(response_lines[:20])  # Limit lines

                if content:
                    break

            logger.debug("Waiting for response... %ds (typing=%s)", i + 1, is_typing)

        latency_ms = (time.time() - start_time) * 1000

        if not content:
            logger.error("No response received after %ds", max_wait_sec)
            raise RuntimeError(f"Timeout waiting for AI response after {max_wait_sec}s")

        logger.info(
            "Response received: %d chars, %.1fms",
            len(content),
            latency_ms,
        )

        # Build GLMClient-compatible response
        prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 4
        completion_tokens = len(content) // 4
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
            "_stealth_latency_ms": latency_ms,
            "_method": "dom_interaction",
        }

    async def close(self) -> None:
        """Close browser and cleanup."""
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        self._initialized = False
        logger.info("Stealth browser closed")


class QwenStealthClientSync:
    """
    Sync wrapper for QwenStealthClient.

    Uses ThreadPoolExecutor to run async code in sync context.
    Compatible with QwenBrowserClientSync interface.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        auth_token: Optional[str] = None,  # Ignored, for interface compatibility
        cookie: Optional[str] = None,  # Ignored, uses session file
        extra_headers: Optional[Dict[str, str]] = None,  # Ignored
        chat_id: Optional[str] = None,  # Ignored, creates new each time
        timeout_sec: int = 180,
        headless: Optional[bool] = None,
    ):
        """
        Initialize sync stealth client.

        Args match QwenBrowserClientSync for drop-in replacement.
        Some args are ignored as stealth client uses session file.
        """
        self._model = model
        self._timeout_sec = timeout_sec
        self._headless = headless if headless is not None else False

        self._client: Optional[QwenStealthClient] = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop for async execution."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run_async(self, coro):
        """Run async coroutine in executor."""
        loop = self._get_loop()
        return loop.run_until_complete(coro)

    def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send request to Qwen API (sync wrapper).

        Args:
            messages: List of message dicts (role, content)
            trace_id: Optional trace ID

        Returns:
            Response dict compatible with GLMClient format
        """
        if self._client is None:
            self._client = QwenStealthClient(
                model=self._model,
                timeout_sec=self._timeout_sec,
                headless=self._headless,
            )

        return self._run_async(self._client.request(messages, trace_id))

    def close(self) -> None:
        """Close browser and cleanup."""
        if self._client is not None:
            self._run_async(self._client.close())
            self._client = None

        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

        self._executor.shutdown(wait=False)
        logger.info("Stealth client sync wrapper closed")

    def __enter__(self) -> "QwenStealthClientSync":
        return self

    def __exit__(self, *args) -> None:
        self.close()
