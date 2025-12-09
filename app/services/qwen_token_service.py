"""
Qwen WAF Token Refresh Service

Background service that uses Playwright to:
1. Open chat.qwen.ai in headless browser
2. Intercept network requests to capture WAF headers (bx-ua, bx-umidtoken)
3. Store tokens in Redis with TTL

Run as: python -m app.services.qwen_token_service
Or via systemd: trading-qwen-token.service
"""

import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils.logging import get_logger
from app.utils.redis import get_redis_client
from app.services.captcha_solver import get_captcha_solver

logger = get_logger(__name__)


class QwenTokenService:
    """Background service for refreshing Qwen WAF tokens."""

    REFRESH_INTERVAL = 600      # 10 minutes
    TOKEN_TTL = 1500            # 25 minutes (tokens valid ~20-30min)
    STATUS_TTL = 1800           # 30 minutes
    MAX_RETRIES = 3

    REDIS_KEYS = {
        "bx_ua": "qwen:bx_ua",
        "bx_umidtoken": "qwen:bx_umidtoken",
        "cookies": "qwen:cookies",
        "last_refresh": "qwen:last_refresh",
        "status": "qwen:token_status",
        "error": "qwen:error_message",
    }

    def __init__(self):
        self._running = False
        self._captured_tokens: Dict[str, str] = {}

    async def start(self):
        """Main entry point - starts refresh loop."""
        logger.info("=" * 60)
        logger.info("Qwen Token Service Starting")
        logger.info("=" * 60)
        logger.info("Refresh interval: %ds, Token TTL: %ds", self.REFRESH_INTERVAL, self.TOKEN_TTL)

        self._running = True

        # Initial refresh
        await self._refresh_with_retry()

        # Continuous refresh loop
        while self._running:
            logger.info("Sleeping %ds until next refresh...", self.REFRESH_INTERVAL)
            await asyncio.sleep(self.REFRESH_INTERVAL)
            await self._refresh_with_retry()

    async def stop(self):
        """Stop the service gracefully."""
        logger.info("Stopping Qwen Token Service...")
        self._running = False

    async def _refresh_with_retry(self):
        """Refresh tokens with retry logic."""
        self._set_status("REFRESHING")

        for attempt in range(self.MAX_RETRIES):
            try:
                logger.info("Token refresh attempt %d/%d", attempt + 1, self.MAX_RETRIES)
                await self._refresh_tokens()
                self._set_status("VALID")
                logger.info("Token refresh successful!")
                return

            except Exception as e:
                logger.error("Token refresh failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    delay = 5 * (attempt + 1)
                    logger.info("Retrying in %ds...", delay)
                    await asyncio.sleep(delay)

        # All retries failed
        self._set_status("ERROR")
        self._set_error("All refresh attempts failed")
        logger.critical("Token refresh failed after %d attempts", self.MAX_RETRIES)

    async def _detect_captcha(self, page) -> bool:
        """Check if CAPTCHA is visible on page."""
        selectors = [
            ".nc_scale .btn_slide",
            ".nc_iconfont.btn_slide",
            ".geetest_slider_button",
            ".slider",
            ".handler",
            ".drag",
            '[class*="captcha"] .slider',
            '[class*="verify"] .slider',
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible(timeout=1000):
                    logger.info("CAPTCHA detected with selector: %s", sel)
                    return True
            except Exception:
                pass

        # Text-based detection
        captcha_texts = ["滑动验证", "Drag", "slide", "verify", "验证"]
        for text in captcha_texts:
            try:
                if await page.get_by_text(text, exact=False).first.is_visible(timeout=500):
                    logger.info("CAPTCHA detected by text: %s", text)
                    return True
            except Exception:
                pass

        return False

    async def _human_drag(self, page, distance: int):
        """Perform human-like slider drag."""
        slider_selectors = [
            ".nc_scale .btn_slide",
            ".nc_iconfont.btn_slide",
            ".geetest_slider_button",
            ".slider",
            ".handler",
            ".drag",
        ]

        slider = None
        for sel in slider_selectors:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible(timeout=500):
                    slider = locator
                    break
            except Exception:
                pass

        if not slider:
            raise Exception("Slider element not found")

        box = await slider.bounding_box()
        if not box:
            raise Exception("Slider bounding box not available")

        start_x = box['x'] + box['width'] / 2
        start_y = box['y'] + box['height'] / 2

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()

        # Human-like movement with jitter
        steps = max(15, min(35, distance // 3))
        moved = 0.0

        for i in range(steps):
            remaining = distance - moved
            step = remaining / (steps - i) * (0.85 + random.random() * 0.3)
            moved += step
            jitter_y = (random.random() - 0.5) * 2
            await page.mouse.move(start_x + moved, start_y + jitter_y)
            await page.wait_for_timeout(8 + random.randint(0, 12))

        await page.mouse.up()
        logger.info("Drag completed: %d px in %d steps", distance, steps)

    async def _solve_captcha(self, page, max_attempts: int = 3) -> bool:
        """Detect and solve CAPTCHA if present."""
        if not await self._detect_captcha(page):
            return True  # No CAPTCHA

        logger.warning("CAPTCHA detected, attempting to solve...")

        for attempt in range(max_attempts):
            try:
                # Try to find CAPTCHA container
                container_selectors = [
                    '[class*="captcha"]',
                    '.nc-container',
                    '.nc_wrapper',
                    '.geetest_holder',
                    '.geetest_panel',
                    '.slider-verify',
                ]

                screenshot = None
                for sel in container_selectors:
                    try:
                        container = page.locator(sel).first
                        if await container.is_visible(timeout=500):
                            screenshot = await container.screenshot()
                            logger.info("CAPTCHA screenshot from: %s", sel)
                            break
                    except Exception:
                        pass

                if not screenshot:
                    logger.warning("No container found, using full page screenshot")
                    screenshot = await page.screenshot()

                # Solve with Sider
                solver = get_captcha_solver()
                distance = solver.solve(screenshot)

                if not distance:
                    logger.error("CAPTCHA solve returned None (attempt %d)", attempt + 1)
                    await page.wait_for_timeout(1000)
                    continue

                # Perform drag
                await self._human_drag(page, distance)

                # Wait and verify
                await page.wait_for_timeout(2000)

                if not await self._detect_captcha(page):
                    logger.info("CAPTCHA solved successfully!")
                    return True

                logger.warning("CAPTCHA still present after solve attempt %d", attempt + 1)

            except Exception as e:
                logger.error("CAPTCHA solve error (attempt %d): %s", attempt + 1, e)

            await page.wait_for_timeout(1000)

        logger.error("Failed to solve CAPTCHA after %d attempts", max_attempts)
        return False

    async def _refresh_tokens(self):
        """Core refresh logic using Playwright."""
        from playwright.async_api import async_playwright
        from app.config.settings import get_settings

        settings = get_settings()
        self._captured_tokens = {}

        async with async_playwright() as p:
            logger.info("Launching Chromium browser...")

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                ]
            )

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/142.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )

            # Load existing cookies from settings
            if settings.qwen.cookie and settings.qwen.cookie != "changeme":
                logger.info("Loading cookies from settings...")
                cookies_to_add = []
                for part in settings.qwen.cookie.split("; "):
                    if "=" in part:
                        name, value = part.split("=", 1)
                        cookies_to_add.append({
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": ".qwen.ai",
                            "path": "/",
                        })
                if cookies_to_add:
                    await context.add_cookies(cookies_to_add)
                    logger.info("Loaded %d cookies", len(cookies_to_add))

            page = await context.new_page()

            # Intercept requests to capture WAF headers
            async def capture_request(request):
                headers = request.headers
                url = request.url

                # Capture bx-ua and bx-umidtoken from any API request
                if "chat.qwen.ai/api" in url or "qwen.ai" in url:
                    if "bx-ua" in headers:
                        self._captured_tokens["bx_ua"] = headers["bx-ua"]
                        logger.info("Captured bx-ua: %s...", headers["bx-ua"][:50])

                    if "bx-umidtoken" in headers:
                        self._captured_tokens["bx_umidtoken"] = headers["bx-umidtoken"]
                        logger.info("Captured bx-umidtoken: %s...", headers["bx-umidtoken"][:30])

            page.on("request", capture_request)

            try:
                # Navigate to Qwen chat
                logger.info("Navigating to chat.qwen.ai...")
                await page.goto("https://chat.qwen.ai/", wait_until="networkidle", timeout=60000)

                # Wait for JS to execute and generate tokens
                logger.info("Waiting for page to stabilize...")
                await page.wait_for_timeout(5000)

                # Check and solve CAPTCHA if present
                if not await self._solve_captcha(page):
                    raise Exception("CAPTCHA could not be solved")

                # Trigger a lightweight API call to capture WAF tokens
                # We'll fetch the chat list instead of sending a message
                try:
                    logger.info("Triggering lightweight API call...")

                    # Method 1: Execute fetch to trigger WAF token generation
                    await page.evaluate("""
                        fetch('/api/v2/chats?page=1&size=10', {
                            method: 'GET',
                            headers: {'Accept': 'application/json'}
                        }).catch(e => console.log('fetch error:', e));
                    """)
                    await page.wait_for_timeout(2000)

                    # Check if we captured tokens
                    if not self._captured_tokens.get("bx_ua"):
                        logger.info("No tokens yet, trying new chat endpoint...")
                        # Method 2: Create a new chat (doesn't send message)
                        await page.evaluate("""
                            fetch('/api/v2/chats/new', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                    'Accept': 'application/json'
                                },
                                body: '{}'
                            }).catch(e => console.log('fetch error:', e));
                        """)
                        await page.wait_for_timeout(2000)

                    # Check tokens captured
                    if self._captured_tokens.get("bx_ua"):
                        logger.info("WAF tokens captured successfully!")
                    else:
                        logger.warning("Could not capture WAF tokens from API calls")

                except Exception as e:
                    logger.warning("API trigger failed: %s", e)

                # Extract cookies
                cookies = await context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                logger.info("Captured %d cookies", len(cookie_dict))

                # Save tokens to Redis
                self._save_tokens(cookie_dict)

            finally:
                await browser.close()
                logger.info("Browser closed")

    def _save_tokens(self, cookies: Dict[str, str]):
        """Save tokens to Redis with TTL."""
        redis = get_redis_client()

        # Save bx-ua
        if self._captured_tokens.get("bx_ua"):
            redis.setex(
                self.REDIS_KEYS["bx_ua"],
                self.TOKEN_TTL,
                self._captured_tokens["bx_ua"]
            )
            logger.info("Saved bx_ua to Redis (TTL=%ds)", self.TOKEN_TTL)

        # Save bx-umidtoken
        if self._captured_tokens.get("bx_umidtoken"):
            redis.setex(
                self.REDIS_KEYS["bx_umidtoken"],
                self.TOKEN_TTL,
                self._captured_tokens["bx_umidtoken"]
            )
            logger.info("Saved bx_umidtoken to Redis (TTL=%ds)", self.TOKEN_TTL)

        # Save cookies as JSON string
        if cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            redis.setex(self.REDIS_KEYS["cookies"], self.TOKEN_TTL, cookie_str)
            logger.info("Saved cookies to Redis (TTL=%ds)", self.TOKEN_TTL)

        # Save last refresh timestamp
        redis.setex(
            self.REDIS_KEYS["last_refresh"],
            self.STATUS_TTL,
            datetime.now(timezone.utc).isoformat()
        )

    def _set_status(self, status: str):
        """Set token status in Redis."""
        try:
            redis = get_redis_client()
            redis.setex(self.REDIS_KEYS["status"], self.STATUS_TTL, status)
        except Exception as e:
            logger.warning("Failed to set status: %s", e)

    def _set_error(self, message: str):
        """Set error message in Redis."""
        try:
            redis = get_redis_client()
            redis.setex(self.REDIS_KEYS["error"], self.STATUS_TTL, message)
        except Exception as e:
            logger.warning("Failed to set error: %s", e)


async def main():
    """Entry point for the service."""
    service = QwenTokenService()

    # Handle graceful shutdown
    import signal

    def handle_signal(signum, frame):
        logger.info("Received signal %d, initiating shutdown...", signum)
        asyncio.create_task(service.stop())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    await service.start()


if __name__ == "__main__":
    asyncio.run(main())
