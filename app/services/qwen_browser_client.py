"""
Qwen Browser Client - Playwright üzerinden chat.qwen.ai API erişimi.

WAF bypass için tüm API çağrılarını gerçek browser context'inde yapar.
Bu yöntem daha yavaş fakat fingerprint/token eşleşmesi gerektiren durumlarda çalışır.

Not: Playwright paketi ve Chromium'un kurulu olması gerekir:
  pip install playwright
  python -m playwright install chromium
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


class QwenBrowserClient:
    """Playwright tabanlı Qwen client (WAF-safe)."""

    BASE_URL = "https://chat.qwen.ai/"
    DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        model: str = "qwen-max-latest",
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        chat_id: Optional[str] = None,
    ):
        self._model = model
        self._auth_token = auth_token or settings.qwen.auth_token
        self._cookie = cookie or settings.qwen.cookie
        self._extra_headers = extra_headers or {}
        self._preset_chat_id = chat_id

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
        headers = {}
        # Start with explicit headers if provided (captures bx-ua, bx-umidtoken, version, etc.)
        headers.update(self._extra_headers)

        # Ensure required defaults
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")

        if self._auth_token and self._auth_token != "changeme":
            headers["Authorization"] = f"Bearer {self._auth_token}"
        # Merge user-supplied headers (e.g., bx-ua, bx-umidtoken, bx-v)
        return headers

    async def _ensure_browser(self):
        """Browser'ı başlat ve sayfa yükle."""
        if self._initialized and self._page:
            return

        from playwright.async_api import async_playwright

        logger.info("Starting Playwright browser...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
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

        logger.info("Loading chat.qwen.ai...")
        await self._page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=30000)
        await self._page.wait_for_timeout(4000)

        self._initialized = True
        logger.info("Browser ready (page + cookies loaded)")

    async def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send request via browser context to bypass WAF fingerprinting."""
        await self._ensure_browser()

        # Normalize messages to the structure used by the web client
        normalized_messages = [
            {
                "role": msg.get("role", "user"),
                "content": [{"type": "text", "text": msg.get("content", "")}],
            }
            for msg in messages
        ]

        payload = {
            "model": self._model,
            "messages": normalized_messages,
            "stream": True,  # Use streaming path like the web client
            "id": str(uuid.uuid4()),
        }
        if self._preset_chat_id:
            payload["chat_id"] = self._preset_chat_id

        result = await self._page.evaluate(
            """
            async ({ payload, headers }) => {
                const makeHeaders = () => ({ ...headers });

                // 1) Resolve chatId: use preset if provided, otherwise create new
                let chatId = payload.chat_id;
                if (!chatId) {
                    const chatResp = await fetch('/api/v2/chats/new', {
                        method: 'POST',
                        headers: makeHeaders(),
                        body: '{}'
                    });

                    const chatText = await chatResp.text();
                    let chatJson = null;
                    try { chatJson = JSON.parse(chatText); } catch (_) {}

                    chatId = chatJson?.data?.id;
                    if (!chatResp.ok || !chatId) {
                        return {
                            error: 'chat_create_failed',
                            status: chatResp.status,
                            rawBody: chatText,
                        };
                    }
                }

                // 2) Send message in the SAME browser context
                payload.chat_id = chatId;
                const msgResp = await fetch(`/api/v2/chat/completions?chat_id=${chatId}`, {
                    method: 'POST',
                    headers: makeHeaders(),
                    body: JSON.stringify(payload),
                });

                const contentType = msgResp.headers.get('content-type') || '';
                const msgText = await msgResp.text();

                let msgJson = null;
                try { msgJson = JSON.parse(msgText); } catch (_) {}

                if (contentType.includes('text/html')) {
                    return {
                        error: 'waf_html',
                        status: msgResp.status,
                        rawBody: msgText.slice(0, 800),
                        chatId,
                    };
                }

                if (!msgResp.ok || !msgJson?.success) {
                    return {
                        error: 'completions_failed',
                        status: msgResp.status,
                        rawBody: msgText.slice(0, 800),
                        chatId,
                    };
                }

                const content = msgJson?.data?.choices?.[0]?.message?.content || '';

                return {
                    success: true,
                    content,
                    usage: msgJson?.data?.usage || {},
                    chatId,
                    status: msgResp.status,
                };
            }
            """,
            {"payload": payload, "headers": self._build_headers()},
        )

        if result.get("error"):
            # Provide a compact but informative error for logs
            raise RuntimeError(
                f"Qwen browser call failed ({result.get('error')} - status={result.get('status')}): "
                f"{result.get('rawBody', '')}"
            )

        content = result.get("content", "")
        logger.info("📥 Qwen browser response: %d chars (chat_id=%s)", len(content), result.get("chatId"))

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
            "usage": result.get("usage", {}),
            "model": self._model,
            "_qwen_browser_chat_id": result.get("chatId"),
            "_qwen_browser_status": result.get("status"),
        }

    async def close(self):
        """Close browser."""
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_playwright") and self._playwright:
            await self._playwright.stop()
        self._initialized = False


class QwenBrowserClientSync:
    """Sync wrapper for QwenBrowserClient."""

    def __init__(
        self,
        model: str = "qwen-max-latest",
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        chat_id: Optional[str] = None,
    ):
        self._async_client = QwenBrowserClient(
            model=model,
            auth_token=auth_token,
            cookie=cookie,
            extra_headers=extra_headers,
            chat_id=chat_id,
        )
        self._loop = None

    def _get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sync request wrapper."""
        loop = self._get_loop()
        return loop.run_until_complete(self._async_client.request(messages, trace_id))

    def close(self):
        """Close client."""
        if self._loop and not self._loop.is_closed():
            self._loop.run_until_complete(self._async_client.close())
            self._loop.close()
