"""
Qwen Browser Client - Playwright üzerinden chat.qwen.ai API erişimi.

WAF bypass için tüm API çağrılarını gerçek browser context'inde yapar.
Bu yöntem daha yavaş fakat fingerprint/token eşleşmesi gerektiren durumlarda çalışır.

Not: Playwright paketi ve Chromium'un kurulu olması gerekir:
  pip install playwright
  python -m playwright install chromium
"""

import asyncio
import concurrent.futures
import json
import uuid
import random
import time
import math
import sys
import os
from typing import Any, Dict, List, Optional
from pathlib import Path

from app.config.settings import get_settings
from app.utils.logging import get_logger
from app.services.captcha_solver import get_captcha_solver

logger = get_logger(__name__)
settings = get_settings()

# Add GeekedTest to path
sys.path.insert(0, "/root/geeked_test")
try:
    from geeked import Geeked
    GEEKED_AVAILABLE = True
except ImportError:
    GEEKED_AVAILABLE = False
    logger.warning("GeekedTest not available - falling back to OpenCV solver")


class QwenBrowserClient:
    """Playwright tabanlı Qwen client (WAF-safe)."""

    BASE_URL = "https://chat.qwen.ai/"
    DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        model: str = "qwen-max-latest",
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        chat_id: Optional[str] = None,
        headless: Optional[bool] = None,
        fetch_timeout_ms: int = 60000,
        max_waf_retries: int = 2,
    ):
        self._model = model
        self._auth_token = auth_token or settings.qwen.auth_token
        self._cookie = cookie or settings.qwen.cookie
        self._extra_headers = extra_headers or {}
        self._preset_chat_id = chat_id
        self._headless = headless
        self._fetch_timeout_ms = fetch_timeout_ms
        self._max_waf_retries = max_waf_retries

        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False

    def _cookie_dicts(self) -> List[Dict[str, str]]:
        """Convert cookie header string to Playwright cookie dicts."""
        cookies: List[Dict[str, str]] = []
        cookie_source = self._extra_headers.get("cookie") or self._cookie
        if not cookie_source or cookie_source == "changeme":
            return cookies

        for part in cookie_source.split("; "):
            if "=" in part:
                name, value = part.split("=", 1)
                cookies.append(
                    {
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": ".qwen.ai",
                        "path": "/",
                    }
                )
        return cookies

    def _build_headers(self) -> Dict[str, str]:
        """Headers passed to fetch inside the browser context."""
        headers: Dict[str, str] = {}
        # Start with explicit headers if provided (captures bx-ua, bx-umidtoken, version, etc.)
        headers.update(self._extra_headers)

        # Ensure required defaults
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")

        if self._auth_token and self._auth_token != "changeme":
            headers["Authorization"] = f"Bearer {self._auth_token}"

        # Browser fetch() forbids setting some headers (Cookie/User-Agent/Origin/Referer/sec-*)
        for key in list(headers.keys()):
            lower_key = key.lower()
            if lower_key in {"cookie", "user-agent", "host", "origin", "referer", "content-length"}:
                headers.pop(key, None)
                continue
            if lower_key.startswith("sec-"):
                headers.pop(key, None)
                continue

        return headers

    async def _ensure_browser(self):
        """Browser'ı başlat ve sayfa yükle."""
        if self._initialized and self._page:
            return

        from playwright.async_api import async_playwright

        logger.info("Starting Playwright browser...")
        headless = self._headless
        if headless is None:
            headless = not bool(os.environ.get("DISPLAY"))
            if headless:
                logger.warning("DISPLAY yok; Playwright headless başlatılıyor (WAF engelleyebilir).")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=headless,  # DISPLAY varsa headed; yoksa headless'a düş
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            user_agent=self.DEFAULT_UA,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        cookies = self._cookie_dicts()
        if cookies:
            await self._context.add_cookies(cookies)
            logger.info("Loaded %d cookies into browser context", len(cookies))

        self._page = await self._context.new_page()
        self._page.on("console", lambda msg: logger.info(f"BROWSER CONSOLE: {msg.text}"))
        await self._page.expose_function("pythonLog", lambda msg: print(f"JS LOG: {msg}"))

        logger.info("Loading chat.qwen.ai...")
        print("Browser: Loading chat.qwen.ai...")
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
        print("Browser: Page loaded, waiting 4s...")
        await self._page.wait_for_timeout(4000)

        # Debug screenshot to see what loaded
        await self._page.screenshot(path="/root/qwen_browser_loaded.png")
        logger.info("Debug screenshot saved to /root/qwen_browser_loaded.png")

        # Check if WAF page loaded instead of chat
        page_html = await self._page.content()
        if "aliyun_waf" in page_html or "Access Verification" in page_html:
            logger.warning("WAF/CAPTCHA page detected after load! Taking screenshot...")
            await self._page.screenshot(path="/root/qwen_waf_page.png")
            # Try to solve CAPTCHA before continuing
            if await self._detect_captcha():
                logger.info("CAPTCHA detected, attempting to solve...")
                await self._solve_captcha()
                await self._page.wait_for_timeout(3000)

        self._initialized = True
        logger.info("Browser ready (page + cookies loaded)")

    async def _detect_captcha(self) -> bool:
        """Check if CAPTCHA is visible on page or in any iframe."""
        if not self._page:
            return False
        
        # Only specific CAPTCHA slider selectors - avoid generic ".slider" which may match normal UI
        selectors = [
            ".nc_scale .btn_slide",
            ".nc_iconfont.btn_slide",
            ".geetest_slider_button",
            ".geetest_btn",
            "#nc_1_n1z",  # Alibaba slider ID
            ".nc-lang-cnt",  # Alibaba container
            '[class*="captcha-slider"]',
            '[class*="verify-slider"]',
            ".aliyun-captcha-slider",
        ]

        # Check main page
        for sel in selectors:
            try:
                locator = self._page.locator(sel).first
                if await locator.is_visible(timeout=500):
                    logger.info("CAPTCHA detected in main page with selector: %s", sel)
                    return True
            except Exception:
                pass

        # Check all frames
        for frame in self._page.frames:
            for sel in selectors:
                try:
                    locator = frame.locator(sel).first
                    if await locator.is_visible(timeout=200):
                        logger.info("CAPTCHA detected in frame %s with selector: %s", frame.name or frame.url[:50], sel)
                        return True
                except Exception:
                    pass

        # Only detect specific CAPTCHA-related text - avoid false positives from normal UI
        # Must be very specific to avoid detecting normal page elements
        captcha_texts = ["滑动验证", "Access Verification", "Please complete the verification"]
        for text in captcha_texts:
            try:
                if await self._page.get_by_text(text, exact=False).first.is_visible(timeout=300):
                    logger.info("CAPTCHA detected by text: %s", text)
                    return True
            except Exception:
                pass

        # Check for Aliyun WAF page specifically
        try:
            page_html = await self._page.content()
            if "aliyun_waf" in page_html:
                logger.info("CAPTCHA detected: Aliyun WAF page marker")
                return True
        except Exception:
            pass

        return False

    async def _human_drag(self, distance: int):
        """Perform human-like slider drag, with robust detection including Shadow DOM."""
        logger.info("Searching for slider element (including Shadow DOM)...")

        # JS function to find the slider element anywhere in the DOM or Shadow DOM
        find_slider_js = """
        () => {
            const findSlider = (root) => {
                const selectors = [
                    '.btn_slide', '#nc_1_n1z', '.nc_iconfont.btn_slide', 
                    '.geetest_slider_button', '.slider', '.handler', '.drag',
                    '#nc_1__btn_1', '.nc_1_n1z', '.slidetounlock .drag',
                    '#aliyunCaptcha-start-icon', '[class*="btn_slide"]',
                    '[class*="slider-button"]', '.nc_scale span'
                ];
                
                // 1. Check direct selectors
                for (const sel of selectors) {
                    try {
                        const el = root.querySelector(sel);
                        if (el && el.offsetWidth > 0) return el;
                    } catch(e) {}
                }

                // 2. Check all children for shadow roots
                const children = root.querySelectorAll('*');
                for (const child of children) {
                    if (child.shadowRoot) {
                        const found = findSlider(child.shadowRoot);
                        if (found) return found;
                    }
                }
                return null;
            };

            const el = findSlider(document.body);
            if (!el) return null;

            const rect = el.getBoundingClientRect();
            return {
                x: rect.left + window.scrollX,
                y: rect.top + window.scrollY,
                width: rect.width,
                height: rect.height,
                id: el.id,
                class: el.className
            };
        }
        """

        logger.info("Searching for slider element in %d frames...", len(self._page.frames))

        box = await self._page.evaluate(find_slider_js)
        
        if not box:
            # Try in all frames too
            for i, frame in enumerate(self._page.frames):
                try:
                    logger.info("Checking frame %d: %s", i, frame.url[:50])
                    box = await frame.evaluate(find_slider_js)
                    if box:
                        logger.info("Found slider in frame %d: %s", i, frame.url[:50])
                        break
                except Exception as e:
                    logger.debug("Failed to evaluate in frame %d: %s", i, e)
                    continue

        if not box:
            logger.error("Slider element NOT found. Dumping visible elements for debug.")
            try:
                # Get all elements with "slide" or "btn" in class/id
                debug_info = await self._page.evaluate("""
                    () => {
                        const results = [];
                        const walk = (root) => {
                            const els = root.querySelectorAll('*');
                            for (const e of els) {
                                if (e.offsetWidth > 0 && (
                                    (e.className && typeof e.className === 'string' && e.className.toLowerCase().includes('slide')) ||
                                    (e.id && e.id.toLowerCase().includes('slide')) ||
                                    (e.className && typeof e.className === 'string' && e.className.toLowerCase().includes('nc_'))
                                )) {
                                    const r = e.getBoundingClientRect();
                                    results.push({ tag: e.tagName, id: e.id, class: e.className, x: r.left, y: r.top });
                                }
                                if (e.shadowRoot) walk(e.shadowRoot);
                            }
                        };
                        walk(document.body);
                        return results;
                    }
                """)
                logger.info("Potential slider elements: %s", debug_info)
            except: pass
            raise Exception("Slider element not found in any frame or shadow DOM")

        logger.info("Found slider: %s", box)
        start_x = box['x'] + box['width'] / 2
        start_y = box['y'] + box['height'] / 2

        logger.info("Starting drag from (%f, %f) for distance %d", start_x, start_y, distance)
        
        await self._page.mouse.move(start_x, start_y)
        await self._page.mouse.down()

        # Human-like movement
        steps = max(30, min(60, distance // 2))
        moved = 0.0

        for i in range(steps):
            remaining = distance - moved
            progress = i / steps
            # S-curve speed
            speed_factor = 0.8 + 0.7 * math.sin(progress * math.pi) 
            
            step = (remaining / (steps - i)) * speed_factor * (0.8 + random.random() * 0.4)
            moved += step
            
            jitter_y = (random.random() - 0.5) * 4
            await self._page.mouse.move(start_x + moved, start_y + jitter_y)
            await self._page.wait_for_timeout(random.randint(5, 12))

        await self._page.wait_for_timeout(random.randint(200, 500))
        await self._page.mouse.up()
        logger.info("Drag completed: %d px", distance)

    async def _solve_captcha_geeked(self) -> bool:
        """Solve CAPTCHA using GeekedTest library (API-based, no browser drag)."""
        if not GEEKED_AVAILABLE:
            logger.warning("GeekedTest not available")
            return False

        try:
            # Extract captcha_id from page via JavaScript
            # Using raw string to avoid escape sequence warnings
            captcha_info = await self._page.evaluate(r"""
                () => {
                    // Method 1: Look for captcha_id in window/global variables
                    if (window.captcha_id) return { captcha_id: window.captcha_id, risk_type: 'slide' };
                    if (window.gt4) return { captcha_id: window.gt4.captcha_id, risk_type: window.gt4.risk_type || 'slide' };

                    // Method 2: Look in iframe src
                    const iframes = document.querySelectorAll('iframe');
                    for (const iframe of iframes) {
                        const src = iframe.src || '';
                        const match = src.match(/captcha_id=([a-f0-9]+)/);
                        if (match) return { captcha_id: match[1], risk_type: 'slide' };
                    }

                    // Method 3: Look in script tags
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        const text = script.textContent || '';
                        const match = text.match(/captcha_id['":\s]+['"]?([a-f0-9]{32})['"]?/);
                        if (match) return { captcha_id: match[1], risk_type: 'slide' };
                    }

                    // Method 4: Look in any element's data attributes
                    const allElements = document.querySelectorAll('[data-captcha-id], [captcha-id]');
                    for (const el of allElements) {
                        const id = el.getAttribute('data-captcha-id') || el.getAttribute('captcha-id');
                        if (id) return { captcha_id: id, risk_type: 'slide' };
                    }

                    // Method 5: Search in page HTML for geetest pattern
                    const html = document.documentElement.innerHTML;
                    const geeMatch = html.match(/captcha_id['"=:\s]+['"]?([a-f0-9]{32})['"]?/i);
                    if (geeMatch) return { captcha_id: geeMatch[1], risk_type: 'slide' };

                    return null;
                }
            """)

            if not captcha_info or not captcha_info.get('captcha_id'):
                logger.warning("Could not extract captcha_id from page, trying network interception...")
                # Try to get from intercepted network requests
                captcha_info = await self._get_captcha_id_from_network()

            if not captcha_info or not captcha_info.get('captcha_id'):
                logger.error("Failed to extract captcha_id")
                return False

            captcha_id = captcha_info['captcha_id']
            risk_type = captcha_info.get('risk_type', 'slide')

            logger.info("Extracted captcha_id: %s, risk_type: %s", captcha_id, risk_type)

            # Solve using GeekedTest
            geeked = Geeked(captcha_id, risk_type)
            seccode = geeked.solve()

            logger.info("GeekedTest solved: lot_number=%s", seccode.get('lot_number'))

            # Submit the solution back to the page
            submit_result = await self._page.evaluate("""
                (seccode) => {
                    // Try to find and call the captcha callback
                    if (window.captchaCallback) {
                        window.captchaCallback(seccode);
                        return { success: true, method: 'callback' };
                    }

                    // Try gt4 object
                    if (window.gt4 && window.gt4.verify) {
                        window.gt4.verify(seccode);
                        return { success: true, method: 'gt4' };
                    }

                    // Try to dispatch custom event
                    const event = new CustomEvent('geetest_verified', { detail: seccode });
                    document.dispatchEvent(event);

                    // Store seccode for potential form submission
                    window.__geetest_seccode = seccode;

                    return { success: true, method: 'event', seccode: seccode };
                }
            """, seccode)

            logger.info("Solution submitted: %s", submit_result)
            await self._page.wait_for_timeout(2000)

            # Check if CAPTCHA is gone
            if not await self._detect_captcha():
                logger.info("CAPTCHA solved successfully with GeekedTest!")
                return True

            # If CAPTCHA still present, try to fill hidden fields
            await self._page.evaluate("""
                (seccode) => {
                    // Fill any hidden input fields
                    const fields = ['captcha_output', 'pass_token', 'lot_number', 'gen_time'];
                    for (const field of fields) {
                        const inputs = document.querySelectorAll(`input[name*="${field}"], input[id*="${field}"]`);
                        for (const input of inputs) {
                            input.value = seccode[field] || '';
                        }
                    }

                    // Try clicking verify/submit button
                    const btns = document.querySelectorAll('button[type="submit"], .verify-btn, .submit-btn');
                    if (btns.length > 0) btns[0].click();
                }
            """, seccode)

            await self._page.wait_for_timeout(2000)
            return not await self._detect_captcha()

        except Exception as e:
            logger.error("GeekedTest solve failed: %s", e)
            return False

    async def _get_captcha_id_from_network(self) -> Optional[Dict]:
        """Try to get captcha_id from recent network requests."""
        try:
            # This requires network interception to be set up
            # For now, return None and let caller handle
            return None
        except Exception:
            return None

    async def _solve_captcha(self, max_attempts: int = 3) -> bool:
        """Detect and solve CAPTCHA if present."""
        if not await self._detect_captcha():
            return True

        logger.warning("CAPTCHA detected in browser client, attempting to solve...")

        # First try GeekedTest (API-based, more reliable)
        if GEEKED_AVAILABLE:
            logger.info("Trying GeekedTest solver first...")
            if await self._solve_captcha_geeked():
                return True
            logger.warning("GeekedTest failed, falling back to slider drag...")

        # Fallback to OpenCV-based slider drag
        for attempt in range(max_attempts):
            try:
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
                        container = self._page.locator(sel).first
                        if await container.is_visible(timeout=500):
                            screenshot = await container.screenshot()
                            logger.info("CAPTCHA screenshot from: %s", sel)
                            break
                    except Exception:
                        pass

                if not screenshot:
                    screenshot = await self._page.screenshot()

                solver = get_captcha_solver()
                distance = solver.solve(screenshot)

                if not distance:
                    logger.error("CAPTCHA solve returned None (attempt %d)", attempt + 1)
                    await self._page.wait_for_timeout(1000)
                    continue

                await self._human_drag(distance)
                await self._page.wait_for_timeout(2000)

                if not await self._detect_captcha():
                    logger.info("CAPTCHA solved successfully with slider drag!")
                    return True

            except Exception as e:
                logger.error("CAPTCHA solve error (attempt %d): %s", attempt + 1, e)

            await self._page.wait_for_timeout(1000)

        return False

    async def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
        waf_retry: int = 0,
    ) -> Dict[str, Any]:
        """Send request via browser context to bypass WAF fingerprinting."""
        try:
            await self._ensure_browser()
            return await self._request_via_dom(messages)
        except Exception as e:
            logger.error("Browser request failed: %s", e)
            if self._page:
                try:
                    await self._page.screenshot(path="/root/qwen_error.png")
                except:
                    pass
            raise

    async def _request_via_dom(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Send message via DOM interaction (typing and clicking)."""
        if not self._page:
            raise RuntimeError("Browser page not initialized")

        # Extract last user message
        user_message = next((m["content"] for m in reversed(messages) if m["role"] == "user"), None)
        if not user_message:
            raise ValueError("No user message found")

        logger.info("Sending message via DOM: %s...", user_message[:50])

        try:
            # 1. Find textarea and type message
            textarea = self._page.locator("textarea.chat-input")
            await textarea.wait_for(state="visible", timeout=10000)
            await textarea.fill(user_message)
            await self._page.wait_for_timeout(500)

            # 2. Find and click send button
            # Based on analysis, send button has class 'send-button'
            send_btn = self._page.locator(".send-button")
            if await send_btn.count() > 0:
                await send_btn.click()
            else:
                # Fallback logic
                buttons = self._page.locator("button")
                count = await buttons.count()
                clicked = False
                for i in range(count):
                    btn = buttons.nth(i)
                    cls = await btn.get_attribute("class") or ""
                    if "send-button" in cls:
                        await btn.click()
                        clicked = True
                        break
                if not clicked:
                    # Last resort: Press Enter
                    await self._page.keyboard.press("Enter")

            logger.info("Message sent, waiting for response...")

            # 3. Wait for response
            await self._page.wait_for_timeout(2000)

            last_content = ""
            unchanged_count = 0
            max_unchanged = 6  # 3 seconds of no change
            
            # Get initial page text length to detect changes
            initial_text_len = await self._page.evaluate("document.body.innerText.length")
            
            for i in range(120): # Max 60 seconds
                if i % 10 == 0:
                    logger.info(f"Waiting for response... tick {i}")
                
                # Execute JS to find the last message bubble
                # Try multiple selectors
                content = await self._page.evaluate("""() => {
                    // Strategy 1: .markdown-body (standard)
                    const bubbles = document.querySelectorAll('.markdown-body');
                    if (bubbles.length > 0) {
                        return bubbles[bubbles.length - 1].innerText;
                    }
                    
                    // Strategy 2: Look for the last div that contains text and is not the user message
                    // This is harder, so let's try to find elements with specific classes seen in logs
                    const msgs = document.querySelectorAll('div[class*="message"], div[class*="content"], div[class*="bubble"]');
                    if (msgs.length > 0) {
                         return msgs[msgs.length - 1].innerText;
                    }
                    
                    return '';
                }""")
                
                # If specific selectors fail, check if page text grew significantly
                if not content:
                    current_text_len = await self._page.evaluate("document.body.innerText.length")
                    if current_text_len > initial_text_len + 10:
                        # Something changed, try to grab the new text
                        # This is a fallback and might be messy
                        pass

                if content and content.strip() != user_message.strip() and len(content) > 0:
                    if content == last_content:
                        unchanged_count += 1
                    else:
                        unchanged_count = 0
                        last_content = content
                        
                    if unchanged_count >= max_unchanged:
                        # Done generating
                        break
                
                await self._page.wait_for_timeout(500)

            if not last_content:
                # Debug: Dump page text to see what's happening
                page_text = await self._page.evaluate("document.body.innerText")
                logger.warning("No response found. Page text dump (last 500 chars): %s", page_text[-500:])
                
                # Check if we are still on the same page
                url = self._page.url
                if "login" in url:
                     raise RuntimeError("Redirected to login page")
                     
                raise RuntimeError("No response received from DOM")

            return {
                "choices": [{"message": {"role": "assistant", "content": last_content}}],
                "usage": {},
                "model": self._model,
            }

        except Exception as e:
            logger.error("DOM interaction failed: %s", e)
            raise

    async def close(self):
        """Close browser."""
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_playwright") and self._playwright:
            await self._playwright.stop()
        self._initialized = False


class QwenBrowserClientSync:
    """Sync wrapper for QwenBrowserClient using ThreadPoolExecutor.

    Avoids asyncio conflicts by running the async client in a separate thread.
    """

    def __init__(
        self,
        model: str = "qwen-max-latest",
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        chat_id: Optional[str] = None,
        timeout_sec: int = 180,
        headless: Optional[bool] = None,
    ):
        self._model = model
        self._auth_token = auth_token
        self._cookie = cookie
        self._extra_headers = extra_headers
        self._chat_id = chat_id
        self._timeout_sec = timeout_sec
        self._headless = headless
        self._async_client = None

    def _run_async(self, coro, timeout_sec: Optional[int] = None):
        """Run async coroutine in a new thread with its own event loop."""
        effective_timeout = timeout_sec if timeout_sec is not None else self._timeout_sec

        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(asyncio.wait_for(coro, timeout=effective_timeout))
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_thread)
            return future.result()

    def _ensure_client(self):
        """Create async client if not exists."""
        if self._async_client is None:
            self._async_client = QwenBrowserClient(
                model=self._model,
                auth_token=self._auth_token,
                cookie=self._cookie,
                extra_headers=self._extra_headers,
                chat_id=self._chat_id,
                headless=self._headless,
            )

    def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sync request using ThreadPoolExecutor to avoid asyncio conflicts."""
        self._ensure_client()
        try:
            return self._run_async(self._async_client.request(messages, trace_id))
        except asyncio.TimeoutError:
            logger.error("Browser request timed out after %ds", self._timeout_sec)
            raise RuntimeError("Browser request timed out")
        except Exception as e:
            logger.error("Browser request failed: %s", e)
            raise

    def close(self):
        """Close async client."""
        if self._async_client:
            try:
                self._run_async(self._async_client.close(), timeout_sec=min(self._timeout_sec, 30))
            except asyncio.TimeoutError:
                logger.warning("Browser client close timed out after %ds", min(self._timeout_sec, 30))
            except Exception as e:
                logger.warning("Error closing browser client: %s", e)
            self._async_client = None
