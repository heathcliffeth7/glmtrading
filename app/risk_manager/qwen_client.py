"""
Qwen Chat Client - chat.qwen.ai web interface üzerinden API erişimi.

GLMClient yerine kullanılır. Cookie-based authentication ile çalışır.
qwen_chat_template.sh mantığıyla API çağrısı yapar.

Gereksinimler:
- QWEN_COOKIE: Browser'dan alınan cookie string
- Redis: WAF tokenları için (opsiyonel, qwen_token_service)
"""

import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from curl_cffi.requests import Session as CurlSession
from curl_cffi.requests import RequestsError as CurlError

from app.config.settings import get_settings
from app.utils.logging import get_logger
from app.utils.rate_limiter import SlidingWindowRateLimiter
from app.utils.redis import get_redis_client

settings = get_settings()
logger = get_logger(__name__)

# GLM prompts log file
GLM_PROMPTS_LOG = Path("/root/trading/logs/glm_prompts.log")

# Redis keys for WAF tokens and chat IDs
REDIS_KEYS = {
    "bx_ua": "qwen:bx_ua",
    "bx_umidtoken": "qwen:bx_umidtoken",
    "cookies": "qwen:cookies",
    "status": "qwen:token_status",
    "chat_id_prefix": "qwen:chat_id:",  # + symbol
    "metrics_success_prefix": "qwen:metrics:",  # + symbol + :success_count
    "metrics_failure_prefix": "qwen:metrics:",  # + symbol + :failure_count
    "auth_token": "qwen:auth_token",
    "bx_v": "qwen:bx_v",
    "version": "qwen:version",
}


class QwenClient:
    """
    Qwen Chat Client - GLMClient drop-in replacement.

    chat.qwen.ai üzerinden Qwen modellerine erişim sağlar.
    GLMClient ile aynı interface'i kullanır.

    Kullanım:
        client = QwenClient()
        result = client.request([{"role": "user", "content": "..."}])
    """

    BASE_URL = "https://chat.qwen.ai"

    # Desteklenen modeller
    MODELS = {
        "qwen3-max-2025-10-30": "qwen3-max-2025-10-30",  # default
        "qwen-max-latest": "qwen-max-latest",
        "qwen-plus-latest": "qwen-plus-latest",
        "qwq-32b": "qwq-32b",
        "qwen-turbo-latest": "qwen-turbo-latest",
        "qwen3-235b-a22b": "qwen3-235b-a22b",  # thinking model
        "qwen2.5-coder-32b-instruct": "qwen2.5-coder-32b-instruct",
    }

    def __init__(
        self,
        auth_token: Optional[str] = None,
        cookie: Optional[str] = None,
        model: str = "qwen3-max-2025-10-30",
        chat_id: Optional[str] = None,
        trigger_path: Optional[str] = None,
        trigger_timeout_sec: Optional[int] = None,
        use_auth_header: Optional[bool] = None,
    ) -> None:
        """
        Initialize Qwen client.

        Args:
            auth_token: Bearer token from browser (Authorization header).
                        Falls back to QWEN_AUTH_TOKEN env var.
            cookie: Cookie string from browser.
                    Falls back to QWEN_COOKIE env var.
            model: Model to use (default: qwen-max-latest)
            chat_id: Optional fixed chat_id to reuse (default: create per request)
            trigger_path: Optional GET path to warm WAF before POST
            trigger_timeout_sec: Timeout for trigger GET
            use_auth_header: Send Authorization header (can disable to match curl)
        """
        self._auth_token = auth_token or settings.qwen.auth_token
        self._cookie = cookie or settings.qwen.cookie
        self._use_redis_cookies = settings.qwen.use_redis_cookies
        self._use_auth_header = (
            use_auth_header
            if use_auth_header is not None
            else settings.qwen.use_auth_header
        )
        self._model = model
        self._chat_id: Optional[str] = chat_id or settings.qwen.chat_id
        self._last_cookie_source: str = "none"  # debug amaçlı
        self._trigger_path: Optional[str] = trigger_path or settings.qwen.trigger_path
        self._trigger_timeout_sec: int = (
            trigger_timeout_sec or settings.qwen.trigger_timeout_sec
        )

        # Rate limiting (conservative for web interface)
        self._limiter = SlidingWindowRateLimiter(
            window_seconds=60,
            max_requests=10,  # 10 requests per minute
        )

        # Build timeout configuration from settings
        timeout_config = httpx.Timeout(
            timeout=settings.qwen.pool_timeout_sec,
            connect=settings.qwen.connect_timeout_sec,
            read=settings.qwen.read_timeout_sec,
            write=settings.qwen.write_timeout_sec,
        )

        # Determine HTTP protocol version
        use_http2 = not settings.qwen.force_http1
        self._use_http2 = use_http2  # Track protocol for logging

        # Primary: curl_cffi with Chrome fingerprint (WAF bypass)
        # Fallback: httpx (kept for compatibility)
        self._curl_client: Optional[CurlSession] = None
        self._use_curl_cffi = True  # Use curl_cffi by default for WAF bypass

        try:
            # curl_cffi with Chrome TLS fingerprint
            self._curl_client = CurlSession(
                impersonate="chrome124",
                timeout=settings.qwen.read_timeout_sec,
            )
            logger.info(
                "✅ Qwen client: curl_cffi (Chrome fingerprint), timeout=%ds",
                settings.qwen.read_timeout_sec,
            )
        except Exception as e:
            logger.warning("⚠️ curl_cffi init failed: %s, falling back to httpx", e)
            self._use_curl_cffi = False

        # Fallback httpx client
        try:
            self._client = httpx.Client(
                timeout=timeout_config,
                http2=use_http2,
                follow_redirects=True,
            )
            if not self._use_curl_cffi:
                protocol_version = "HTTP/2" if use_http2 else "HTTP/1.1"
                logger.info(
                    "Qwen client fallback: httpx %s, timeouts: connect=%ds, read=%ds",
                    protocol_version,
                    settings.qwen.connect_timeout_sec,
                    settings.qwen.read_timeout_sec,
                )
        except ImportError:
            logger.warning("⚠️ h2 paketi yok, HTTP/1.1 kullanılıyor")
            self._client = httpx.Client(
                timeout=timeout_config,
                http2=False,
                follow_redirects=True,
            )

        # Redis client for WAF tokens
        self._redis = get_redis_client()

        # Validate credentials (only warn if Redis cookies disabled)
        if not self._auth_token or self._auth_token == "changeme":
            logger.debug("QWEN_AUTH_TOKEN not in env (may use Redis)")
        if (not self._cookie or self._cookie == "changeme") and not self._use_redis_cookies:
            logger.warning("⚠️ QWEN_COOKIE not configured and Redis disabled!")

        # Check WAF token status
        waf_status = self._get_waf_status()
        if waf_status != "VALID":
            logger.warning("⚠️ WAF tokens not valid (status: %s). Run qwen_token_service!", waf_status)

        # Service availability flag
        self._available = False
        self._last_error: Optional[str] = None

        # Auto-refresh settings
        self._auto_refresh_enabled = settings.qwen.auto_refresh_enabled
        self._refresh_interval_seconds = settings.qwen.refresh_interval_seconds
        self._retry_after_refresh = settings.qwen.retry_after_refresh
        self._stop_background_refresh = False
        self._background_thread: Optional[threading.Thread] = None
        self._refresh_attempted = False

        cookie_preview = self._cookie[:30] if self._cookie else "N/A"
        logger.info(
            "QwenClient initialized - cookie: %s..., model: %s, auto_refresh: %s, interval: %ds",
            cookie_preview,
            self._model,
            self._auto_refresh_enabled,
            self._refresh_interval_seconds,
        )

        # Start background refresh thread if enabled
        if self._auto_refresh_enabled and self._refresh_interval_seconds > 0:
            self._background_thread = threading.Thread(
                target=self._background_refresh_loop,
                daemon=True,
                name="qwen-cookie-refresh"
            )
            self._background_thread.start()
            logger.info("✅ Background cookie refresh thread started (interval=%ds)", self._refresh_interval_seconds)

    def is_available(self) -> bool:
        """Check if Qwen service is available."""
        return self._available and self._get_waf_status() == "VALID"

    def get_last_error(self) -> Optional[str]:
        """Get last error message."""
        return self._last_error

    def get_last_cookie_source(self) -> str:
        """Return last chosen cookie source for diagnostics (user/redis/none)."""
        return self._last_cookie_source

    def check_availability(self, timeout: float = 10.0) -> bool:
        """
        Quick availability check.

        Returns True if service appears to be working.
        """
        try:
            headers = self._build_headers(include_waf=True)
            response = self._client.get(
                f"{self.BASE_URL}/api/v2/models",
                headers=headers,
                timeout=timeout,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self._available = True
                    return True

            self._available = False
            self._last_error = f"Models endpoint returned: {response.status_code}"
            return False

        except Exception as e:
            self._available = False
            self._last_error = str(e)
            logger.warning("Qwen availability check failed: %s", e)
            return False

    def _get_waf_status(self) -> str:
        """Get WAF token status from Redis."""
        try:
            status = self._redis.get(REDIS_KEYS["status"])
            return status.decode() if status else "NOT_SET"
        except Exception:
            return "ERROR"

    def _get_waf_tokens(self) -> Dict[str, Optional[str]]:
        """Get WAF tokens (bx-ua, bx-umidtoken, auth_token, bx-v, version) from Redis."""
        tokens = {
            "bx_ua": None, 
            "bx_umidtoken": None, 
            "cookies": None, 
            "auth_token": None,
            "bx_v": None,
            "version": None
        }
        try:
            for key_name, redis_key in REDIS_KEYS.items():
                if key_name in tokens:
                    val = self._redis.get(redis_key)
                    tokens[key_name] = val.decode() if val else None
        except Exception as e:
            logger.warning("Failed to get WAF tokens from Redis: %s", e)
        return tokens

    def _parse_cookie_header(self, cookie_str: Optional[str]) -> Dict[str, str]:
        """Parse cookie header string into a dict."""
        if not cookie_str:
            return {}

        cookies: Dict[str, str] = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
        return cookies

    def _build_cookie_header(self, cookies: Dict[str, str]) -> str:
        """Serialize cookie dict back to header string."""
        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    def _build_headers(self, include_waf: bool = True) -> Dict[str, str]:
        """Build request headers for Qwen API."""
        from datetime import datetime

        # Generate timezone string like browser does
        tz_str = datetime.now().strftime("%a %b %d %Y %H:%M:%S GMT+0000")

        # Get WAF tokens and cookies from Redis (required for API access)
        waf_tokens = self._get_waf_tokens()

        headers = {
            "accept": "application/json",
            # curl_cffi handles brotli/zstd automatically (like real Chrome)
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": self.BASE_URL,
            "referer": f"{self.BASE_URL}/",
            "source": "web",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            # Security headers (required by browser)
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            # Qwen specific headers (from working request)
            "bx-v": waf_tokens.get("bx_v") or "2.5.31",
            "version": waf_tokens.get("version") or "0.1.22",
            "timezone": tz_str,
        }

        cookie_header = None
        cookie_source = "none"

        # 1) Kullanıcı cookie'si her zaman öncelikli
        if self._cookie and self._cookie != "changeme":
            cookie_header = self._cookie
            cookie_source = "user"
        # 2) Redis cookie'si yalnızca açık ise VE kullanıcı cookie'si yoksa
        elif self._use_redis_cookies and waf_tokens.get("cookies"):
            cookie_header = waf_tokens["cookies"]
            cookie_source = "redis"

        # WAF tokenları Redis'ten al (cookie parse etmeden)
        # Shell script gibi cookie'yi direkt gönder, parse/rebuild yapma
        bx_ua_token = waf_tokens.get("bx_ua")
        bx_umid_token = waf_tokens.get("bx_umidtoken")

        # Cookie'yi shell script gibi direkt gönder (parse etme!)
        # qwen_chat_template.sh: -H "cookie: ${COOKIE_HEADER}"
        if cookie_header:
            headers["cookie"] = cookie_header

        # Auth token - Shell script'te YOK, WAF'ı tetikleyebilir
        # NOT: qwen_chat_template.sh Authorization header göndermez
        # Bu yüzden default olarak kapalı tutuyoruz
        # if self._use_auth_header:
        #     if waf_tokens.get("auth_token"):
        #         headers["Authorization"] = f"Bearer {waf_tokens['auth_token']}"
        #     elif self._auth_token and self._auth_token != "changeme":
        #         headers["Authorization"] = f"Bearer {self._auth_token}"

        # Debug: hangi cookie kaynağının kullanıldığını sakla
        self._last_cookie_source = cookie_source

        # WAF bypass requires bx-ua and bx-umidtoken headers (from Redis)
        if include_waf:
            if bx_ua_token:
                headers["bx-ua"] = bx_ua_token
            if bx_umid_token:
                headers["bx-umidtoken"] = bx_umid_token

        return headers

    def _create_new_chat(self) -> str:
        """Create a new chat session and return chat_id."""
        try:
            # Use headers with WAF tokens
            headers = self._build_headers(include_waf=True)
            response = self._client.post(
                f"{self.BASE_URL}/api/v2/chats/new",
                json={},
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()
            logger.info("New chat response: %s", result)
            # Response format: {"success": true, "data": {"id": "..."}}
            data = result.get("data", {})
            chat_id = data.get("id") or result.get("id") or result.get("chat_id")
            if not chat_id:
                logger.warning("No chat_id in response: %s", result)
                chat_id = str(uuid.uuid4())
            logger.info("Created new Qwen chat: %s", chat_id)

            # Small delay after chat creation to ensure server is ready
            time.sleep(0.5)

            return chat_id
        except Exception as exc:
            logger.warning("Failed to create chat, using random ID: %s", exc)
            return str(uuid.uuid4())

    def _extract_symbol_from_prompt(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """
        Extract symbol from prompt content.
        
        Searches for pattern: "CURRENT MARKET STATE FOR {SYMBOL}"
        
        Args:
            messages: List of message dicts
            
        Returns:
            Symbol (e.g., "BTCUSDT") or None if not found
        """
        for msg in messages:
            content = msg.get("content", "")
            match = re.search(r"CURRENT MARKET STATE FOR ([A-Z]+USDT)", content)
            if match:
                symbol = match.group(1)
                logger.debug("Extracted symbol from prompt: %s", symbol)
                return symbol
        
        logger.debug("No symbol found in prompt")
        return None

    def _get_or_create_chat_id(self, symbol: str) -> str:
        """
        Get existing chat ID from Redis or create new one.
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            
        Returns:
            chat_id (UUID string)
        """
        if not settings.qwen.enable_chat_id_management:
            logger.debug("Chat ID management disabled, creating new chat")
            return self._create_new_chat()
        
        redis_key = f"{REDIS_KEYS['chat_id_prefix']}{symbol}"
        
        try:
            # Try to get existing chat_id from Redis
            chat_id = self._redis.get(redis_key)
            
            if chat_id:
                chat_id = chat_id.decode('utf-8') if isinstance(chat_id, bytes) else chat_id
                ttl = self._redis.ttl(redis_key)
                logger.info("♻️ Reusing chat_id for %s: %s (TTL: %ds)", symbol, chat_id, ttl)
                return chat_id
            
            # No existing chat_id, create new one
            logger.info("🆕 No cached chat_id for %s, creating new chat", symbol)
            chat_id = self._create_new_chat()
            
            # Store in Redis with TTL
            ttl_seconds = settings.qwen.chat_id_ttl_seconds
            self._redis.setex(redis_key, ttl_seconds, chat_id)
            logger.info("💾 Stored chat_id for %s: %s (TTL: %ds)", symbol, chat_id, ttl_seconds)
            
            return chat_id
            
        except Exception as exc:
            logger.error("Failed to get/create chat_id for %s: %s", symbol, exc)
            # Fallback to creating new chat without Redis
            return self._create_new_chat()

    def _invalidate_chat_id(self, symbol: str) -> None:
        """
        Invalidate (delete) cached chat ID for a symbol.
        
        Called when chat_id becomes invalid (unauthorized, chat not exist errors).
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
        """
        if not settings.qwen.enable_chat_id_management:
            return
        
        redis_key = f"{REDIS_KEYS['chat_id_prefix']}{symbol}"
        
        try:
            deleted = self._redis.delete(redis_key)
            if deleted:
                logger.warning("🗑️ Invalidated chat_id for %s", symbol)
            else:
                logger.debug("No cached chat_id to invalidate for %s", symbol)
        except Exception as exc:
            logger.error("Failed to invalidate chat_id for %s: %s", symbol, exc)

    def _record_request_metrics(self, symbol: str, success: bool) -> None:
        """
        Record request success/failure metrics in Redis.
        
        Args:
            symbol: Trading symbol
            success: True if request succeeded
        """
        if not symbol:
            return
        
        try:
            metric_key = f"{REDIS_KEYS['metrics_success_prefix']}{symbol}:{'success' if success else 'failure'}_count"
            self._redis.incr(metric_key)
        except Exception as exc:
            logger.debug("Failed to record metrics for %s: %s", symbol, exc)

    def _trigger_waf(self, headers: Dict[str, str]) -> None:
        """Optional lightweight GET to warm WAF like the bash script."""
        if not self._trigger_path:
            return

        path = self._trigger_path
        if not path.startswith("/"):
            path = f"/{path}"

        url = f"{self.BASE_URL}{path}"
        trigger_headers = headers.copy()
        trigger_headers["accept"] = "*/*"
        trigger_headers.pop("content-type", None)

        try:
            resp = self._client.get(
                url,
                headers=trigger_headers,
                timeout=self._trigger_timeout_sec,
            )
            snippet = resp.text[:200].replace("\n", " ")
            logger.info(
                "WAF trigger GET %s -> %s (len=%d)",
                path,
                resp.status_code,
                len(resp.text),
            )
            if resp.status_code >= 400:
                logger.warning("WAF trigger response %s: %s", resp.status_code, snippet)
        except Exception as exc:
            logger.warning("WAF trigger GET failed: %s", exc)

    def _log_prompt_response(
        self,
        messages: List[Dict[str, str]],
        response_content: str,
        latency_ms: float,
    ) -> None:
        """Log prompt and response to glm_prompts.log for debugging."""
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            separator = "=" * 80

            with GLM_PROMPTS_LOG.open("a", encoding="utf-8") as f:
                f.write(f"\n{separator}\n")
                f.write(f"[{timestamp}] QWEN REQUEST/RESPONSE (latency: {latency_ms:.1f}ms)\n")
                f.write(f"{separator}\n\n")

                # Log each message in the prompt
                f.write(">>> PROMPT:\n")
                for i, msg in enumerate(messages):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    f.write(f"\n--- Message {i+1} ({role}) ---\n")
                    f.write(f"{content}\n")

                f.write(f"\n>>> RESPONSE:\n")
                f.write(f"{response_content}\n")
                f.write(f"\n{separator}\n")
        except Exception as exc:
            logger.debug("Failed to write to glm_prompts.log: %s", exc)

    def _record_token_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: float,
    ) -> None:
        """Record token usage for analysis."""
        try:
            root_dir = Path(__file__).resolve().parents[2]
            log_file = root_dir / "runtime_metrics_qwen_usage.jsonl"
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": "qwen",
                "model": self._model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "latency_ms": latency_ms,
            }
            with log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("Failed to write Qwen token usage metrics: %s", exc)

    def _parse_sse_response(self, raw_content: str) -> Dict[str, Any]:
        """
        Parse Server-Sent Events (SSE) streaming response.
        
        Raises:
            ValueError: If response contains error codes (unauthorized, chat_not_exist)
        """
        full_content = ""
        last_data = {}
        done_received = False
        line_count = 0
        error_detected = None

        def _extract_segments(line: str) -> List[str]:
            """
            Split a raw SSE line into individual data segments.
            Handles `data:` with/without space and multiple data blocks per line.
            """
            if not line:
                return []

            if "data:" in line:
                parts = re.split(r"data:\s*", line)
                return [part.strip() for part in parts if part.strip()]

            return [line.strip()]

        def _accumulate_from_data(data: Dict[str, Any]) -> str:
            """Collect text content from an SSE delta/message."""
            chunk = ""
            choices = data.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {}) or choices[0].get("message", {})
                content = delta.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            chunk += part.get("text", "")
                elif isinstance(content, str):
                    chunk += content
            return chunk

        def _extract_last_json(raw_text: str) -> Optional[Dict[str, Any]]:
            """Brace-scan the raw SSE body and return the last balanced JSON object."""
            start_idx = None
            depth = 0
            in_string = False
            escape_next = False
            candidate = None

            for i, ch in enumerate(raw_text):
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    if depth == 0:
                        start_idx = i
                    depth += 1
                elif ch == "}" and depth > 0:
                    depth -= 1
                    if depth == 0 and start_idx is not None:
                        candidate = raw_text[start_idx : i + 1]
                        start_idx = None

            if candidate:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
            return None

        # Parse raw content (already read from response)
        try:
            lines = raw_content.split("\n")
        except Exception as e:
            logger.error("Failed to parse response body: %s", e)
            lines = []

        for line in lines:
            line_count += 1
            segments = _extract_segments(line)
            if not segments:
                continue

            for data_str in segments:
                if data_str.strip() == "[DONE]":
                    done_received = True
                    logger.debug("SSE [DONE] received after %d lines", line_count)
                    break

                try:
                    data = json.loads(data_str)
                    last_data = data
                    
                    # Check for error responses
                    if not data.get("success", True):
                        error_code = None
                        error_details = ""
                        
                        if isinstance(data.get("data"), dict):
                            error_code = data["data"].get("code", "").lower()
                            error_details = data["data"].get("details", "")
                        
                        # Detect specific errors that need chat ID invalidation
                        if error_code == "unauthorized" or "session has expired" in error_details.lower():
                            error_detected = "unauthorized"
                        elif (error_code == "bad_request" and "chat" in error_details.lower() and "not exist" in error_details.lower()) or error_code == "not found":
                            error_detected = "chat_not_exist"
                        elif error_code:
                            error_detected = f"error:{error_code}"
                        
                        logger.warning("Qwen error response: code=%s, details=%s", error_code, error_details)
                    
                    chunk = _accumulate_from_data(data)
                    if chunk:
                        full_content += chunk
                except json.JSONDecodeError:
                    continue

        if not done_received:
            # Partial/interrupted stream - but we may have useful content
            if not full_content:
                snippet = raw_content[:400].replace("\n", "\\n")
                logger.warning(
                    "⚠️ SSE stream incomplete: no [DONE] signal, no content | lines=%d, bytes=%d | snippet=%s",
                    line_count,
                    len(raw_content),
                    snippet,
                )
            else:
                # Content received despite missing [DONE] - this is normal for Qwen API
                # Qwen often doesn't send [DONE] terminator, but response is complete
                logger.debug(
                    "SSE stream ended without [DONE] signal, %d chars recovered | lines=%d",
                    len(full_content),
                    line_count,
                )

        # Recover content if SSE aggregation failed
        if not full_content:
            if last_data:
                fallback_msg = last_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(fallback_msg, str):
                    full_content = fallback_msg

            if not full_content:
                recovered = _extract_last_json(raw_content)
                if recovered:
                    chunk = _accumulate_from_data(recovered)
                    if not chunk:
                        msg_content = recovered.get("choices", [{}])[0].get("message", {}).get("content", "")
                        if isinstance(msg_content, str):
                            chunk = msg_content
                    if chunk:
                        full_content = chunk
                        logger.info("Recovered content from raw SSE body via balanced JSON scan")

        # Wait after response completes to avoid interrupting other sessions
        time.sleep(2.0)
        logger.debug("Response complete, waited 2s before next request")
        
        # Raise exception if error was detected
        if error_detected:
            error_msg = f"Qwen API error: {error_detected}"
            logger.error(error_msg)
            raise ValueError(error_detected)  # Will be caught in request() for retry

        if not full_content and last_data:
            # Debug help: log last delta if no content accumulated
            try:
                logger.warning("Qwen response had no content; last_data keys: %s", list(last_data.keys()))
            except Exception:
                logger.warning("Qwen response had no content and last_data could not be inspected")

        if not full_content and raw_content:
            snippet = raw_content[:400].replace("\n", "\\n")
            logger.warning(
                "Qwen SSE returned empty content; raw snippet: %s",
                snippet,
            )

        # Build response in GLMClient-compatible format
        response_dict = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": full_content,
                },
                "finish_reason": last_data.get("choices", [{}])[0].get("finish_reason", "stop"),
            }],
            "usage": last_data.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": len(full_content) // 4,  # Rough estimate
                "total_tokens": len(full_content) // 4,
            }),
            "model": self._model,
            "_partial_response": not done_received,  # Flag for monitoring
        }

        return response_dict

    def request(
        self,
        messages: List[Dict[str, str]],
        trace_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send request to Qwen API.

        This method has the same signature as GLMClient.request() for compatibility.

        Args:
            messages: List of message dicts (role, content)
            trace_id: Optional trace ID for latency tracking
            chat_id: Optional chat_id override (else uses fixed one or creates new)

        Returns:
            Response dict compatible with GLMClient format
        """
        if not self._limiter.hit():
            raise RuntimeError("Qwen API rate limit exceeded")

        # Extract symbol from prompt for chat ID management
        symbol = self._extract_symbol_from_prompt(messages)
        
        # Determine chat_id: override > symbol-based > create new
        if chat_id or self._chat_id:
            chat_id_to_use = chat_id or self._chat_id
            logger.debug("Using provided/fixed chat_id: %s", chat_id_to_use)
        elif symbol and settings.qwen.enable_chat_id_management:
            chat_id_to_use = self._get_or_create_chat_id(symbol)
        else:
            chat_id_to_use = self._create_new_chat()
            if symbol:
                logger.debug("Created new chat for %s (management disabled)", symbol)

        # Build Qwen-specific payload (qwen_chat_template.sh format)
        timestamp = int(time.time())
        qwen_messages = []

        for i, msg in enumerate(messages):
            role = msg.get("role", "user")
            content = msg.get("content", "")

            qwen_messages.append({
                "fid": f"00000000-0000-0000-0000-{i+1:012d}",
                "parentId": None,
                "childrenIds": [],
                "role": role,
                "content": content,  # String format (not array)
                "user_action": "chat",
                "files": [],
                "timestamp": timestamp,
                "models": [self._model],
                "chat_type": "t2t",
                "feature_config": {
                    "thinking_enabled": False,
                    "output_schema": "phase",
                    "research_mode": "normal"
                },
                "extra": {"meta": {"subChatType": "t2t"}},
                "sub_chat_type": "t2t",
                "parent_id": None
            })

        payload = {
            "model": self._model,
            "stream": True,
            "incremental_output": True,
            "chat_id": chat_id_to_use,
            "chat_mode": "normal",
            "parent_id": None,
            "messages": qwen_messages,
            "timestamp": timestamp,
        }

        # Log payload stats
        total_content = sum(len(m.get("content", "")) for m in messages)
        symbol_info = f" [{symbol}]" if symbol else ""
        
        # Enhanced logging with protocol and timeout info
        protocol_info = "HTTP/1.1" if settings.qwen.force_http1 else "HTTP/2"
        logger.info(
            "📤 Qwen Request%s: %d messages, %d chars, model=%s, protocol=%s, read_timeout=%ds",
            symbol_info,
            len(messages),
            total_content,
            self._model,
            protocol_info,
            settings.qwen.read_timeout_sec,
        )
        logger.info(
            "Using chat_id=%s%s",
            chat_id_to_use,
            symbol_info,
        )

        # Debug: Log full payload
        payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
        logger.info("📋 Full Qwen payload:\n%s", payload_json[:2000])

        # Make request with retry logic
        max_retries = settings.qwen.max_retry_attempts
        base_delay = settings.qwen.retry_delay_seconds
        http1_fallback_used = False
        http1_client: Optional[httpx.Client] = None

        # Track protocol errors separately for extended retry
        protocol_error_count = 0
        max_protocol_errors = settings.qwen.max_protocol_error_retries

        endpoint = f"{self.BASE_URL}/api/v2/chat/completions?chat_id={chat_id_to_use}"

        try:
            for attempt in range(max_retries):
                try:
                    # Reset refresh flag for each attempt
                    self._refresh_attempted = False

                    start_time = time.time()
                    logger.info(
                        "🚀 Qwen Request Attempt %d/%d (http2=%s)",
                        attempt + 1,
                        max_retries,
                        not http1_fallback_used,
                    )

                    # Get fresh headers with WAF tokens for each request
                    request_headers = self._build_headers(include_waf=True)
                    waf_status = self._get_waf_status()
                    has_waf = "bx-ua" in request_headers
                    cookie_len = len(request_headers.get("cookie", ""))
                    logger.info("WAF status: %s | bx-ua: %s | cookie: %d chars", waf_status, has_waf, cookie_len)

                    # Optional WAF trigger (lightweight GET) before POST
                    self._trigger_waf(request_headers)

                    # Use curl_cffi for WAF bypass (Chrome TLS fingerprint)
                    raw_bytes = bytearray()
                    chunks_received = 0
                    bytes_received = 0
                    response = None

                    if self._use_curl_cffi and self._curl_client:
                        logger.debug("Using curl_cffi (Chrome fingerprint)")
                        try:
                            # Proxy configuration
                            proxies = {
                                "http": "http://xxlzmhjx:1t5v00u6ihru@151.245.206.16:8011",
                                "https": "http://xxlzmhjx:1t5v00u6ihru@151.245.206.16:8011"
                            }
                            
                            response = self._curl_client.post(
                                endpoint,
                                json=payload,
                                headers=request_headers,
                                stream=True,
                                proxies=proxies,
                            )
                            logger.debug("Qwen response status: %s", response.status_code)

                            # Check status
                            if response.status_code != 200:
                                try:
                                    error_snippet = response.text[:500]
                                except Exception:
                                    error_snippet = "<unreadable>"
                                logger.warning("Qwen non-200: %s - %s", response.status_code, error_snippet)
                                response.raise_for_status()

                            # Stream read
                            for chunk in response.iter_content():
                                if chunk:
                                    raw_bytes.extend(chunk)
                                    chunks_received += 1
                                    bytes_received += len(chunk)

                            logger.debug("Stream complete: %d chunks, %d bytes", chunks_received, bytes_received)

                            try:
                                raw_content = raw_bytes.decode('utf-8')
                            except UnicodeDecodeError:
                                raw_content = raw_bytes.decode('utf-8', errors='replace')

                        except CurlError as e:
                            bytes_received = len(raw_bytes)
                            logger.warning("⚠️ curl_cffi error after %d bytes: %s", bytes_received, str(e)[:100])
                            if bytes_received > 0:
                                raw_content = raw_bytes.decode('utf-8', errors='replace')
                                logger.info("💾 Recovered %d bytes", bytes_received)
                            else:
                                raise RuntimeError(f"curl_cffi request failed: {e}") from e

                    else:
                        # Fallback: httpx client
                        logger.debug("Using httpx fallback")
                        client = self._client
                        if http1_fallback_used:
                            if http1_client is None:
                                http1_client = httpx.Client(
                                    timeout=self._client.timeout,
                                    http2=False,
                                    follow_redirects=True,
                                )
                            client = http1_client

                        with client.stream(
                            "POST",
                            endpoint,
                            json=payload,
                            headers=request_headers,
                        ) as response:
                            logger.debug("Qwen response status: %s", response.status_code)

                            if response.status_code != 200:
                                try:
                                    error_snippet = response.read().decode('utf-8', errors='replace')[:500]
                                except Exception:
                                    error_snippet = "<unreadable>"
                                logger.warning("Qwen non-200: %s - %s", response.status_code, error_snippet)
                            response.raise_for_status()

                            try:
                                for chunk in response.iter_bytes(chunk_size=8192):
                                    raw_bytes.extend(chunk)
                                    chunks_received += 1
                                    bytes_received += len(chunk)

                                logger.debug("Stream complete: %d chunks, %d bytes", chunks_received, bytes_received)

                                try:
                                    raw_content = raw_bytes.decode('utf-8')
                                except UnicodeDecodeError:
                                    raw_content = raw_bytes.decode('utf-8', errors='replace')

                            except (httpx.ReadTimeout, httpx.RemoteProtocolError) as stream_error:
                                bytes_received = len(raw_bytes)
                                logger.warning("⚠️ Stream interrupted after %d bytes: %s", bytes_received, str(stream_error)[:100])
                                if bytes_received > 0:
                                    raw_content = raw_bytes.decode('utf-8', errors='replace')
                                    logger.info("💾 Recovered %d bytes", bytes_received)
                                else:
                                    raise RuntimeError(f"Failed to read Qwen response: {stream_error}") from stream_error

                            except Exception as e:
                                logger.error("Failed to read response: %s", e)
                                raise RuntimeError(f"Failed to read Qwen response: {e}") from e

                    # Common code for both curl_cffi and httpx
                    content_type = response.headers.get("content-type", "") if response else ""

                    # Check for WAF HTML block - prioritize actual content over header
                    raw_stripped = raw_content.strip().lower()
                    is_html_content_type = "text/html" in content_type

                    # Aliyun WAF specific markers
                    is_aliyun_waf = "aliyun_waf" in raw_content or "aliyun_waf_aa" in raw_content

                    content_looks_like_html = (
                        raw_stripped.startswith("<!doctype") or
                        raw_stripped.startswith("<html") or
                        "<head>" in raw_content[:500] or
                        "<meta" in raw_content[:200]
                    )
                    content_looks_like_json = (
                        raw_stripped.startswith("data:") or
                        raw_stripped.startswith("{") or
                        '"choices"' in raw_content
                    )

                    # WAF block: Either Aliyun WAF marker OR (HTML content without JSON markers)
                    is_waf_block = is_aliyun_waf or (
                        (is_html_content_type or content_looks_like_html) and
                        not content_looks_like_json
                    )

                    if is_waf_block:
                        snippet = raw_content[:500]
                        logger.warning("🚨 WAF block detected! aliyun_waf=%s, html_type=%s, html_content=%s",
                                      is_aliyun_waf, is_html_content_type, content_looks_like_html)

                        # Try auto-refresh on WAF block
                        if self._auto_refresh_enabled and self._retry_after_refresh and not self._refresh_attempted:
                            logger.warning("⚠️ WAF HTML block detected - otomatik cookie yenileme deneniyor...")
                            self._refresh_attempted = True

                            if self._refresh_cookies_from_service():
                                logger.info("🔄 Cookie yenilendi, request tekrar deneniyor...")
                                time.sleep(3)
                                continue

                        # Try stealth browser client as last resort for WAF bypass
                        if not getattr(self, '_browser_fallback_attempted', False):
                            self._browser_fallback_attempted = True
                            logger.warning("🌐 WAF block - trying stealth browser fallback (chat_id=%s)...", chat_id_to_use)
                            try:
                                from app.services.stealth_qwen_client import QwenStealthClientSync
                                # Stealth client uses session file, not headers
                                stealth_client = QwenStealthClientSync(
                                    model=self._model,
                                    timeout_sec=settings.qwen.read_timeout_sec,
                                    headless=False,  # WAF requires headed browser
                                )
                                try:
                                    result = stealth_client.request(messages)
                                finally:
                                    stealth_client.close()
                                logger.info("✅ Stealth browser fallback succeeded!")
                                return result
                            except Exception as browser_err:
                                logger.error("❌ Stealth browser fallback failed: %s", browser_err)
                                # Reset for next request
                                self._browser_fallback_attempted = False

                        self._last_error = f"WAF HTML response: {snippet}"
                        logger.error("Qwen returned HTML (likely WAF). Snippet: %s", snippet)
                        raise RuntimeError(
                            "Qwen WAF engellemesi: HTML döndü. Çerez/WAF tokenlarını yenileyin. "
                            f"Snippet: {snippet[:200]}"
                        )

                    # Parse response OUTSIDE context manager (safe now, we have raw content)
                    result = self._parse_sse_response(raw_content)

                    latency_ms = (time.time() - start_time) * 1000
                    result["_qwen_latency_ms"] = latency_ms

                    # Log response
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    symbol_info = f" [{symbol}]" if symbol else ""
                    
                    # Check if response was partial
                    partial_flag = result.get("_partial_response", False)
                    partial_marker = " [PARTIAL]" if partial_flag else ""

                    protocol_used = "HTTP/1.1" if http1_fallback_used else ("HTTP/1.1" if settings.qwen.force_http1 else "HTTP/2")

                    logger.info(
                        "📥 Qwen Response%s%s: %d chars in %.1fms (%s)",
                        symbol_info,
                        partial_marker,
                        len(content),
                        latency_ms,
                        protocol_used,
                    )

                    # Record usage
                    usage = result.get("usage", {})
                    self._record_token_usage(
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        usage.get("total_tokens", 0),
                        latency_ms,
                    )

                    # Log prompt and response to file
                    self._log_prompt_response(messages, content, latency_ms)
                    
                    # Record success metrics
                    if symbol:
                        self._record_request_metrics(symbol, success=True)

                    return result
                
                except ValueError as exc:
                    # Qwen API errors (unauthorized, chat_not_exist, etc.)
                    error_str = str(exc)
                    self._last_error = f"Qwen API error: {error_str}"
                    
                    if error_str in ["unauthorized", "chat_not_exist"]:
                        logger.warning("⚠️ Chat ID invalid (%s), invalidating and retrying...", error_str)
                        
                        if symbol:
                            self._invalidate_chat_id(symbol)
                            self._record_request_metrics(symbol, success=False)
                        
                        if attempt < max_retries - 1:
                            # Get new chat ID on next iteration
                            delay = base_delay
                            logger.info("🔄 Retrying with new chat ID in %.1fs...", delay)
                            time.sleep(delay)
                            
                            # ALWAYS create new chat on chat_not_exist error
                            if symbol and settings.qwen.enable_chat_id_management:
                                chat_id_to_use = self._get_or_create_chat_id(symbol)
                            else:
                                # Force create new chat even if management disabled
                                chat_id_to_use = self._create_new_chat()
                                logger.info("Created new chat (forced): %s", chat_id_to_use[:16])
                            
                            endpoint = f"{self.BASE_URL}/api/v2/chat/completions?chat_id={chat_id_to_use}"
                            payload["chat_id"] = chat_id_to_use
                            
                            continue
                    
                    # Other Qwen errors
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("⚠️ Qwen error %s, retrying in %.1fs...", error_str, delay)
                        time.sleep(delay)
                        continue
                    
                    logger.error("❌ Qwen error after %d attempts: %s", max_retries, error_str)
                    raise RuntimeError(f"Qwen API error: {error_str}") from exc

                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    # Try to get body, but response might already be consumed
                    try:
                        if hasattr(exc.response, '_content') and exc.response._content:
                            body_snippet = exc.response._content.decode('utf-8', errors='replace')[:300]
                        else:
                            body_snippet = f"Status {status} - body not available"
                    except Exception:
                        body_snippet = f"Status {status}"
                    self._last_error = f"HTTP {status}: {body_snippet}"
                    
                    # Record failure metrics
                    if symbol:
                        self._record_request_metrics(symbol, success=False)

                    # Auth errors - try auto-refresh once
                    if status in [401, 403]:
                        # Invalidate chat ID on auth errors
                        if symbol:
                            self._invalidate_chat_id(symbol)
                        
                        if self._auto_refresh_enabled and self._retry_after_refresh and not self._refresh_attempted:
                            logger.warning("⚠️ Auth error %d - otomatik cookie yenileme deneniyor...", status)
                            self._refresh_attempted = True
                            
                            if self._refresh_cookies_from_service():
                                logger.info("🔄 Cookie yenilendi, request tekrar deneniyor...")
                                time.sleep(3)
                                
                                # ALWAYS create new chat after refresh
                                if symbol and settings.qwen.enable_chat_id_management:
                                    chat_id_to_use = self._get_or_create_chat_id(symbol)
                                else:
                                    chat_id_to_use = self._create_new_chat()
                                    logger.info("Created new chat (forced): %s", chat_id_to_use[:16])
                                
                                endpoint = f"{self.BASE_URL}/api/v2/chat/completions?chat_id={chat_id_to_use}"
                                payload["chat_id"] = chat_id_to_use
                                
                                continue
                            else:
                                logger.error("❌ Cookie yenileme başarısız")
                        
                        logger.error("❌ Qwen auth error: %d - Token/cookie expired?", status)
                        raise RuntimeError(f"Qwen auth error: {status}. Token yenilenmeli!") from exc

                    # Rate limit - wait longer
                    if status == 429:
                        delay = base_delay * (3 ** attempt)
                        logger.warning("⚠️ Qwen rate limit, waiting %.1fs...", delay)
                        time.sleep(delay)
                        continue

                    # Server errors - retry
                    if status in [500, 502, 503, 504] and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "⚠️ Qwen server error %d, retrying in %.1fs...",
                            status,
                            delay,
                        )
                        time.sleep(delay)
                        continue

                    logger.error("❌ Qwen HTTP error: %d", status)
                    raise RuntimeError(f"Qwen API error: {status}") from exc

                except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                    self._last_error = "timeout"
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("⚠️ Qwen timeout, retrying in %.1fs...", delay)
                        time.sleep(delay)
                        continue

                    logger.error("❌ Qwen timeout after %d attempts", max_retries)
                    raise RuntimeError("Qwen API timeout") from exc

                except httpx.RemoteProtocolError as exc:
                    protocol_error_count += 1
                    # Connection closed unexpectedly during streaming
                    self._last_error = f"protocol_error: {str(exc)[:200]}"
                    
                    # Record failure metrics
                    if symbol:
                        self._record_request_metrics(symbol, success=False)

                    # Automatic HTTP/1.1 fallback on first protocol error
                    if not http1_fallback_used and settings.qwen.enable_http2_fallback:
                        http1_fallback_used = True
                        logger.warning(
                            "⚠️ RemoteProtocolError #%d (attempt %d/%d) - switching to HTTP/1.1: %s",
                            protocol_error_count,
                            attempt + 1,
                            max_retries,
                            str(exc)[:120],
                        )
                        # Immediate retry with HTTP/1.1, no delay
                        continue

                    # Already using HTTP/1.1 - retry with backoff
                    # Allow extra retries for protocol errors (up to max_protocol_error_retries)
                    total_attempts_allowed = max_retries + max_protocol_errors
                    
                    if attempt < total_attempts_allowed - 1:
                        delay = settings.qwen.protocol_error_retry_delay * (1.5 ** (protocol_error_count - 1))
                        delay = min(delay, 30)  # Cap at 30 seconds
                        
                        protocol_info = "HTTP/1.1" if http1_fallback_used else "HTTP/2"
                        logger.warning(
                            "⚠️ RemoteProtocolError #%d (%s, attempt %d/%d), retry in %.1fs: %s",
                            protocol_error_count,
                            protocol_info,
                            attempt + 1,
                            total_attempts_allowed,
                            delay,
                            str(exc)[:100],
                        )
                        time.sleep(delay)
                        continue

                    # All retries exhausted
                    protocol_info = "HTTP/1.1" if http1_fallback_used else "HTTP/2"
                    logger.error(
                        "❌ RemoteProtocolError after %d attempts (%s, %d protocol errors): %s",
                        attempt + 1,
                        protocol_info,
                        protocol_error_count,
                        exc
                    )
                    raise RuntimeError(
                        f"Qwen connection failed after {attempt + 1} attempts "
                        f"({protocol_error_count} protocol errors, {protocol_info}): {exc}"
                    ) from exc

            raise RuntimeError("Qwen API failed after all retries")
        finally:
            if http1_client is not None:
                try:
                    http1_client.close()
                except Exception:
                    pass

    def _refresh_cookies_from_service(self) -> bool:
        """
        Trigger force refresh in background token service and wait for new tokens.
        
        Sends force refresh signal to qwen_token_service via Redis,
        then waits for new tokens to be generated.
        
        Returns:
            True if refresh successful or optimistically assumed successful
        """
        try:
            logger.info("🔥 Triggering FORCE REFRESH in token service...")
            
            # Set force refresh trigger in Redis
            redis_client = self._redis
            redis_client.setex("qwen:force_refresh", 30, "1")  # 30 second TTL
            logger.info("Force refresh trigger set in Redis")
            
            # Check current WAF status
            old_status = self._get_waf_status()
            logger.info("Current WAF status: %s", old_status)
            
            # Wait for token service to pick up trigger and refresh
            # Service checks every 5 seconds, refresh takes ~10-15 seconds
            logger.info("Waiting 20 seconds for token service to complete refresh...")
            time.sleep(20)
            
            # Check new status
            new_status = self._get_waf_status()
            logger.info("New WAF status after force refresh: %s", new_status)
            
            # Check if last_refresh timestamp changed
            try:
                last_refresh = redis_client.get("qwen:last_refresh")
                logger.info("Last refresh timestamp: %s", last_refresh)
            except Exception:
                pass
            
            if new_status == "VALID":
                logger.info("✅ Force refresh successful - WAF tokens updated")
                return True
            else:
                logger.warning("⚠️ WAF status: %s after force refresh. Continuing optimistically.", new_status)
                return True
                
        except Exception as e:
            logger.error("❌ Force refresh trigger error: %s", e)
            return True  # Optimistic - don't block on errors

    def _background_refresh_loop(self) -> None:
        """
        Background thread that monitors WAF token status.
        
        Note: Actual token refresh is done by systemd service.
        This thread just monitors and logs status periodically.
        """
        logger.info("🔄 Token status monitor thread başlatıldı (interval=%ds)", self._refresh_interval_seconds)
        
        while not self._stop_background_refresh:
            try:
                time.sleep(self._refresh_interval_seconds)
                
                if self._stop_background_refresh:
                    break
                    
                # Just check and log status (systemd service does actual refresh)
                status = self._get_waf_status()
                logger.info("⏰ Periyodik WAF status check: %s", status)
                
            except Exception as e:
                logger.error("Background monitor error: %s", e)

    def close(self) -> None:
        """Close HTTP client and stop background refresh thread."""
        self._stop_background_refresh = True
        if self._background_thread and self._background_thread.is_alive():
            logger.debug("Waiting for background refresh thread to stop...")
            self._background_thread.join(timeout=5)
        self._client.close()

    def __enter__(self) -> "QwenClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
