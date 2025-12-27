"""
Playwright Qwen Client Package.

Stealth browser ile chat.qwen.ai'ye baglanir.
SSE network interception ile response alir.

Usage:
    # Async
    from app.services.playwright_qwen import PlaywrightQwenClient

    client = PlaywrightQwenClient()
    response = await client.request([{"role": "user", "content": "Hello"}])
    await client.close()

    # Sync (for orchestrator)
    from app.services.playwright_qwen import PlaywrightQwenClientSync

    client = PlaywrightQwenClientSync()
    response = client.request([{"role": "user", "content": "Hello"}])
    client.close()
"""

from .browser_manager import BrowserManager
from .client import PlaywrightQwenClient
from .response_extractor import ResponseExtractor
from .session_manager import SessionExpiredError, SessionManager
from .sync_wrapper import PlaywrightQwenClientSync

__all__ = [
    "PlaywrightQwenClient",
    "PlaywrightQwenClientSync",
    "BrowserManager",
    "SessionManager",
    "SessionExpiredError",
    "ResponseExtractor",
]
