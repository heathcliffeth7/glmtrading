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

        # HTTP client - use http2 like bash script, fallback to http1.1 if h2 not available
        try:
            self._client = httpx.Client(
                timeout=httpx.Timeout(120.0, connect=15.0, read=90.0),
                http2=True,  # Enable HTTP/2 like bash script
                follow_redirects=True,
            )
            logger.debug("HTTP/2 client initialized")
        except ImportError:
            logger.warning("⚠️ h2 paketi yok, HTTP/1.1 kullanılıyor")
            self._client = httpx.Client(
                timeout=httpx.Timeout(120.0, connect=15.0, read=90.0),
                http2=False,
                follow_redirects=True,
            )

        # Redis client for WAF tokens
        self._redis = get_redis_client()

        # Validate credentials
        if not self._auth_token or self._auth_token == "changeme":
            logger.warning("⚠️ QWEN_AUTH_TOKEN not configured!")
        if not self._cookie or self._cookie == "changeme":
            logger.warning("⚠️ QWEN_COOKIE not configured!")

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
        """Get WAF tokens (bx-ua, bx-umidtoken) from Redis."""
        tokens = {"bx_ua": None, "bx_umidtoken": None, "cookies": None}
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
        headers = {
            "accept": "text/event-stream",
            # Brotli/zstd decoding is flaky in httpx without extra deps and was producing
            # binary SSE bodies; force gzip/deflate only to keep streamed text parseable.
            "accept-encoding": "gzip, deflate",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": self.BASE_URL,
            "referer": f"{self.BASE_URL}/",
            "source": "web",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
            # Security headers (required by browser)
            "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            # Qwen specific headers
            "bx-v": "2.5.31",
            "version": "0.1.15",
        }

        # Get WAF tokens and cookies from Redis (required for API access)
        waf_tokens = self._get_waf_tokens()
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

        cookie_map = self._parse_cookie_header(cookie_header)

        # WAF tokenları cookie'den ya da Redis'ten yakala
        bx_ua_token = (
            waf_tokens.get("bx_ua")
            or cookie_map.get("bx-ua")
            or cookie_map.get("bx_ua")
        )
        bx_umid_token = (
            waf_tokens.get("bx_umidtoken")
            or cookie_map.get("bx-umidtoken")
            or cookie_map.get("bx_umidtoken")
        )

        # Cookie'de yoksa ekle (Redis'ten geldiyse header ve cookie'ye sok)
        if bx_ua_token and "bx-ua" not in cookie_map and "bx_ua" not in cookie_map:
            cookie_map["bx-ua"] = bx_ua_token
        if (
            bx_umid_token
            and "bx-umidtoken" not in cookie_map
            and "bx_umidtoken" not in cookie_map
        ):
            cookie_map["bx-umidtoken"] = bx_umid_token

        if cookie_map:
            headers["cookie"] = self._build_cookie_header(cookie_map)
        elif cookie_header:
            headers["cookie"] = cookie_header

        # Auth token opsiyonel (curl davranışına yaklaşmak için kapatılabilir)
        if (
            self._use_auth_header
            and self._auth_token
            and self._auth_token != "changeme"
        ):
            headers["Authorization"] = f"Bearer {self._auth_token}"

        # Debug: hangi cookie kaynağının kullanıldığını sakla
        self._last_cookie_source = cookie_source

        # Add WAF tokens (critical for bypassing Alibaba WAF)
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
                        elif error_code == "bad_request" and "chat" in error_details.lower() and "not exist" in error_details.lower():
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
            # Only warn if no content received, otherwise it's just Qwen API behavior
            if not full_content:
                snippet = raw_content[:400].replace("\n", "\\n")
                logger.warning(
                    "SSE stream ended without [DONE] and no content after %d lines; raw_len=%d; snippet=%s",
                    line_count,
                    len(raw_content),
                    snippet,
                )
            else:
                logger.debug("SSE stream ended without [DONE] signal after %d lines (content received)", line_count)

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
        return {
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
        }

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

            # Generate unique fid for each message
            fid = f"{i:08d}-0000-0000-0000-000000000001"

            qwen_messages.append({
                "fid": fid,
                "parentId": None,
                "childrenIds": [],
                "role": role,
                "content": content,  # Simple string format like bash script
                "user_action": "chat",
                "files": [],
                "timestamp": timestamp,
                "models": [self._model],
                "chat_type": "t2t",
                "feature_config": {
                    "thinking_enabled": False,
                    "output_schema": "phase",
                    "research_mode": "normal",
                },
                "extra": {
                    "meta": {
                        "subChatType": "t2t",
                    },
                },
                "sub_chat_type": "t2t",
                "parent_id": None,
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
        logger.info(
            "📤 Qwen Request%s: %d messages, %d total chars, model=%s",
            symbol_info,
            len(messages),
            total_content,
            self._model,
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

                    # Choose client (http2 default, optional http1 fallback)
                    client = self._client
                    if http1_fallback_used:
                        if http1_client is None:
                            http1_client = httpx.Client(
                                timeout=self._client.timeout,
                                http2=False,
                                follow_redirects=True,
                            )
                        client = http1_client

                    # Stream POST - read content inside context manager
                    with client.stream(
                        "POST",
                        endpoint,
                        json=payload,
                        headers=request_headers,
                    ) as response:
                        logger.debug("Qwen response status: %s", response.status_code)
                        
                        # CRITICAL: Read entire response content INSIDE context manager
                        # For streaming responses, must call .read() first, then decode
                        try:
                            raw_bytes = response.read()
                            raw_content = raw_bytes.decode('utf-8')
                        except UnicodeDecodeError:
                            # Fallback for encoding issues
                            logger.warning("UTF-8 decode error, using replace mode")
                            raw_content = raw_bytes.decode('utf-8', errors='replace')
                        except Exception as e:
                            logger.error("Failed to read response content: %s", e)
                            raise RuntimeError(f"Failed to read Qwen response: {e}") from e
                        
                        # Now check status and content type (safe, we already read)
                        if response.status_code != 200:
                            logger.warning("Qwen non-200 response: %s - %s", response.status_code, raw_content[:500])
                        response.raise_for_status()

                        content_type = response.headers.get("content-type", "")
                        if "text/html" in content_type:
                            snippet = raw_content[:500]
                            
                            # Try auto-refresh on WAF block
                            if self._auto_refresh_enabled and self._retry_after_refresh and not self._refresh_attempted:
                                logger.warning("⚠️ WAF HTML block - otomatik cookie yenileme deneniyor...")
                                self._refresh_attempted = True
                                
                                if self._refresh_cookies_from_service():
                                    logger.info("🔄 Cookie yenilendi, request tekrar deneniyor...")
                                    time.sleep(3)
                                    continue
                            
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
                    logger.info(
                        "📥 Qwen Response%s: %d chars in %.1fms",
                        symbol_info,
                        len(content),
                        latency_ms,
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
                            
                            # Update chat_id_to_use for next attempt
                            if symbol and settings.qwen.enable_chat_id_management:
                                chat_id_to_use = self._get_or_create_chat_id(symbol)
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
                        if symbol and settings.qwen.enable_chat_id_management:
                            self._invalidate_chat_id(symbol)
                        
                        if self._auto_refresh_enabled and self._retry_after_refresh and not self._refresh_attempted:
                            logger.warning("⚠️ Auth error %d - otomatik cookie yenileme deneniyor...", status)
                            self._refresh_attempted = True
                            
                            if self._refresh_cookies_from_service():
                                logger.info("🔄 Cookie yenilendi, request tekrar deneniyor...")
                                time.sleep(3)
                                
                                # Get new chat ID if symbol exists
                                if symbol and settings.qwen.enable_chat_id_management:
                                    chat_id_to_use = self._get_or_create_chat_id(symbol)
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
                    # Connection closed unexpectedly during streaming
                    self._last_error = f"protocol_error: {str(exc)[:200]}"

                    # Try a one-off HTTP/1.1 fallback once
                    if not http1_fallback_used:
                        http1_fallback_used = True
                        logger.warning(
                            "⚠️ Qwen protocol error (attempt %d/%d); retrying once with HTTP/1.1: %s",
                            attempt + 1,
                            max_retries,
                            str(exc)[:120],
                        )
                        continue

                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "⚠️ Qwen connection closed unexpectedly, retrying in %.1fs: %s",
                            delay,
                            str(exc)[:100],
                        )
                        time.sleep(delay)
                        continue

                    logger.error("❌ Qwen connection error after %d attempts: %s", max_retries, exc)
                    raise RuntimeError(f"Qwen API connection error: {exc}") from exc

            raise RuntimeError("Qwen API failed after all retries")
        finally:
            if http1_client is not None:
                try:
                    http1_client.close()
                except Exception:
                    pass

    def _refresh_cookies_from_service(self) -> bool:
        """
        Wait for background token service to refresh cookies.
        
        Note: qwen_token_service runs as a systemd service and 
        automatically refreshes cookies every 10 minutes. This method
        simply waits for the next refresh cycle.
        
        Returns:
            True (optimistic - assumes service is working)
        """
        try:
            logger.info("🔄 Waiting for background token service to refresh cookies...")
            
            # Check current WAF status
            old_status = self._get_waf_status()
            logger.info("Current WAF status: %s", old_status)
            
            # Wait for token service refresh (it runs every 10 minutes)
            # Give it 15 seconds for the current cycle to complete
            logger.info("Waiting 15 seconds for token refresh...")
            time.sleep(15)
            
            # Check new status
            new_status = self._get_waf_status()
            logger.info("New WAF status: %s", new_status)
            
            if new_status == "VALID":
                logger.info("✅ Cookies refreshed by background service")
                return True
            else:
                logger.warning("⚠️ WAF status still %s after waiting. Background service may be running slow.", new_status)
                # Return True anyway - optimistic approach
                return True
                
        except Exception as e:
            logger.error("❌ Cookie refresh wait error: %s", e)
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
