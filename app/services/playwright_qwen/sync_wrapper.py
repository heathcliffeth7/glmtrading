"""
Sync Wrapper - Thread-safe synchronous wrapper for PlaywrightQwenClient.

Orchestrator ile uyumlu sync interface.
Async client'i ayri thread'de calistirir.
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging import get_logger

from .client import PlaywrightQwenClient
from .session_manager import SessionExpiredError

logger = get_logger(__name__)

# Default settings
DEFAULT_MODEL = "qwen-max-latest"
DEFAULT_TIMEOUT = 180  # seconds


class PlaywrightQwenClientSync:
    """
    Thread-safe synchronous wrapper for PlaywrightQwenClient.

    Runs async client in dedicated background thread.
    Compatible with QwenClient/GLMClient interface.

    Usage:
        client = PlaywrightQwenClientSync()
        response = client.request([{"role": "user", "content": "Hello"}])
        client.close()
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout_sec: int = DEFAULT_TIMEOUT,
        headless: Optional[bool] = None,
        session_file: Optional[Path] = None,
        # These params are for interface compatibility with QwenClient
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        """
        Initialize sync wrapper.

        Args:
            model: Qwen model to use
            timeout_sec: Request timeout in seconds
            headless: Force headless mode (None = auto-detect)
            session_file: Path to session storage file
            auth_token: Ignored (for interface compatibility)
            cookie: Ignored (for interface compatibility)
            chat_id: Ignored (for interface compatibility)
        """
        self._model = model
        self._timeout_sec = timeout_sec
        self._headless = headless
        self._session_file = session_file

        self._client: Optional[PlaywrightQwenClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw_qwen")

        logger.info(
            "PlaywrightQwenClientSync init: model=%s, timeout=%ds",
            model,
            timeout_sec,
        )

    def _ensure_loop(self):
        """Ensure event loop exists and is running in background thread."""
        if self._loop is not None and self._loop.is_running():
            return

        def run_loop():
            """Run event loop in background thread."""
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(
            target=run_loop,
            daemon=True,
            name="pw_qwen_loop",
        )
        self._thread.start()

        # Wait for loop to start with timeout (no busy-wait)
        loop_timeout = 10  # 10 seconds max
        start_wait = time.time()
        while self._loop is None or not self._loop.is_running():
            if time.time() - start_wait > loop_timeout:
                raise RuntimeError("Event loop failed to start within timeout")
            time.sleep(0.1)  # Yield CPU instead of busy-wait

        logger.debug("Event loop started in background thread")

    def _run_async(self, coro):
        """
        Run async coroutine in background loop.

        Args:
            coro: Coroutine to run

        Returns:
            Result from coroutine
        """
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=self._timeout_sec + 30)  # Extra buffer

    def _get_client(self) -> PlaywrightQwenClient:
        """Get or create async client."""
        if self._client is None:
            self._client = PlaywrightQwenClient(
                model=self._model,
                timeout_sec=self._timeout_sec,
                headless=self._headless,
                session_file=self._session_file,
            )
        return self._client

    def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send request to Qwen (sync wrapper).

        Args:
            messages: List of message dicts [{"role": "user", "content": "..."}]
            trace_id: Optional trace ID for logging

        Returns:
            Response dict compatible with GLMClient format

        Raises:
            SessionExpiredError: If session has expired
            RuntimeError: If request fails
        """
        with self._lock:
            try:
                client = self._get_client()
                return self._run_async(client.request(messages, trace_id))

            except SessionExpiredError:
                # Re-raise session errors for caller to handle
                raise

            except Exception as e:
                logger.error("Sync request failed: %s", e)
                raise

    def create_new_chat(self):
        """Create a new chat session."""
        with self._lock:
            if self._client:
                self._run_async(self._client.create_new_chat())

    def save_session(self):
        """Save current session to file."""
        with self._lock:
            if self._client:
                self._run_async(self._client.save_session())

    def close(self):
        """Close client and cleanup."""
        with self._lock:
            if self._client:
                try:
                    self._run_async(self._client.close())
                except Exception as e:
                    logger.warning("Error closing client: %s", e)
                self._client = None

            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)

            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5)

            self._loop = None
            self._thread = None

            self._executor.shutdown(wait=False)

        logger.info("PlaywrightQwenClientSync closed")

    def __enter__(self) -> "PlaywrightQwenClientSync":
        """Context manager entry."""
        return self

    def __exit__(self, *args):
        """Context manager exit."""
        self.close()

    def __del__(self):
        """Destructor - ensure cleanup."""
        try:
            self.close()
        except Exception:
            pass

    @property
    def is_initialized(self) -> bool:
        """Check if client is initialized."""
        return self._client is not None and self._client.is_initialized

    @property
    def model(self) -> str:
        """Get current model."""
        return self._model
