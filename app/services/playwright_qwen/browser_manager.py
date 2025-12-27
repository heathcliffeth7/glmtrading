"""
Browser Manager - Playwright Stealth browser lifecycle management.

Stealth ayarlari ile browser baslatir, Baxia SDK'yi bloklar.
CAPTCHA cikmamasi icin max stealth uygulanir.
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)

# Debug screenshot directory
DEBUG_DIR = Path("/root/trading")


class BrowserManager:
    """
    Playwright browser lifecycle manager with stealth settings.

    Features:
    - playwright-stealth for anti-bot evasion
    - Baxia SDK route blocking
    - Auto-detect headed/headless based on DISPLAY
    - Persistent browser context for session reuse
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        user_data_dir: Optional[str] = None,
    ):
        """
        Initialize browser manager.

        Args:
            headless: Force headless mode. None = auto-detect from DISPLAY
            user_data_dir: Optional persistent user data directory
        """
        # Auto-detect headless if not specified
        if headless is None:
            self._headless = os.environ.get("DISPLAY") is None
        else:
            self._headless = headless

        self._user_data_dir = user_data_dir

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False

        logger.info(
            "BrowserManager init: headless=%s, DISPLAY=%s",
            self._headless,
            os.environ.get("DISPLAY", "not set"),
        )

    async def init_browser(self, storage_state: Optional[str] = None):
        """
        Initialize browser with stealth settings.

        Args:
            storage_state: Path to session storage file (cookies/localStorage)

        Returns:
            Page object ready for use
        """
        if self._initialized and self._page:
            return self._page

        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth
        except ImportError as e:
            logger.error("Playwright or stealth not installed: %s", e)
            raise RuntimeError(
                "Required: pip install playwright playwright-stealth && "
                "playwright install chromium"
            ) from e

        logger.info("Starting Playwright browser (headless=%s)...", self._headless)

        self._playwright = await async_playwright().start()

        # Launch browser with anti-detection args
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-infobars",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--metrics-recording-only",
            "--no-first-run",
            "--safebrowsing-disable-auto-update",
        ]

        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=launch_args,
        )

        # Context options - realistic Chrome profile
        context_options = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "color_scheme": "light",
            "device_scale_factor": 1,
            "has_touch": False,
            "is_mobile": False,
            "java_script_enabled": True,
        }

        # Load session if provided
        if storage_state and Path(storage_state).exists():
            logger.info("Loading session from: %s", storage_state)
            context_options["storage_state"] = storage_state

        self._context = await self._browser.new_context(**context_options)
        self._page = await self._context.new_page()

        # Apply stealth BEFORE any navigation
        stealth = Stealth(
            navigator_webdriver=True,  # Hide navigator.webdriver
            chrome_app=True,
            chrome_csi=True,
            chrome_load_times=True,
            chrome_runtime=True,
            iframe_content_window=True,
            media_codecs=True,
            navigator_hardware_concurrency=True,
            navigator_languages=True,
            navigator_permissions=True,
            navigator_platform=True,
            navigator_plugins=True,
            navigator_user_agent=True,
            navigator_vendor=True,
            webgl_vendor=True,
        )
        await stealth.apply_stealth_async(self._page)

        # Block anti-bot SDKs at route level
        await self._setup_route_blocking()

        # Additional stealth scripts
        await self._inject_stealth_scripts()

        self._initialized = True

        # Verify stealth
        webdriver = await self._page.evaluate("navigator.webdriver")
        logger.info(
            "Browser ready - navigator.webdriver=%s (should be false/undefined)",
            webdriver,
        )

        return self._page

    async def _setup_route_blocking(self):
        """Block known anti-bot SDK routes."""
        blocked_patterns = [
            "**/baxia**",
            "**/sd/baxia**",
            "**/baxiaCommon.js",
            "**/aliyun**captcha**",
            "**/captcha**",
            "**/aeis.alicdn.com/**",
            "**/g.alicdn.com/sd/**",
            "**/g.alicdn.com/AWSC/**",
        ]

        # Fix: Use proper async function instead of lambda (avoids closure bug)
        async def abort_route(route):
            await route.abort()

        for pattern in blocked_patterns:
            await self._page.route(pattern, abort_route)

        logger.debug("Blocked %d anti-bot route patterns", len(blocked_patterns))

    async def _inject_stealth_scripts(self):
        """Inject additional stealth scripts."""
        # Save original fetch before any SDK can intercept
        await self._page.add_init_script("""
            // Save original fetch
            if (!window.__originalFetch) {
                window.__originalFetch = window.fetch.bind(window);
                window.__fetchSaved = true;
            }

            // Override permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // WebGL vendor/renderer override
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) {
                    return 'Intel Inc.';
                }
                if (parameter === 37446) {
                    return 'Intel Iris OpenGL Engine';
                }
                return getParameter.apply(this, arguments);
            };

            // Canvas fingerprint protection
            const toBlob = HTMLCanvasElement.prototype.toBlob;
            HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {
                const shift = {r: Math.random() * 0.01, g: Math.random() * 0.01, b: Math.random() * 0.01};
                return toBlob.apply(this, arguments);
            };

            // Timezone consistent with locale
            Date.prototype.getTimezoneOffset = function() { return 300; }; // EST
        """)

    async def get_page(self):
        """Get current page, initializing if needed."""
        if not self._initialized or not self._page:
            await self.init_browser()
        return self._page

    async def is_healthy(self) -> bool:
        """Check if browser is still healthy."""
        if not self._initialized or not self._page:
            return False

        try:
            # Check if page is closed first
            if self._page.is_closed():
                logger.warning("Browser health check: page is closed")
                return False

            # Try simple evaluation with timeout
            await asyncio.wait_for(
                self._page.evaluate("1 + 1"),
                timeout=5.0,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning("Browser health check timed out")
            return False
        except Exception as e:
            logger.warning("Browser health check failed: %s", e)
            return False

    async def restart(self, storage_state: Optional[str] = None):
        """Restart browser with fresh context."""
        logger.info("Restarting browser...")
        await self.close()
        return await self.init_browser(storage_state)

    async def take_screenshot(self, name: str = "debug"):
        """Take debug screenshot."""
        if self._page:
            path = DEBUG_DIR / f"{name}_{int(asyncio.get_event_loop().time())}.png"
            await self._page.screenshot(path=str(path))
            logger.info("Screenshot saved: %s", path)
            return path
        return None

    async def save_session(self, path: str):
        """Save current session state to file."""
        if self._context:
            await self._context.storage_state(path=path)
            logger.info("Session saved to: %s", path)

    async def close(self):
        """Close browser and cleanup."""
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._initialized = False
        logger.info("Browser closed")

    @property
    def page(self):
        """Get current page (may be None if not initialized)."""
        return self._page

    @property
    def context(self):
        """Get current context (may be None if not initialized)."""
        return self._context

    @property
    def is_initialized(self) -> bool:
        """Check if browser is initialized."""
        return self._initialized
