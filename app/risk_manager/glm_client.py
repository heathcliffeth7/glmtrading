import json
import json
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import get_settings
from app.utils.latency import get_latency_tracker
from app.utils.logging import get_logger
from app.utils.rate_limiter import SlidingWindowRateLimiter


settings = get_settings()
logger = get_logger(__name__)


class GLMClient:
    def __init__(self) -> None:
        self._base_url = str(settings.zai.base_url)
        self._api_key = settings.zai.api_key
        self._model = settings.zai.model
        self._limiter = SlidingWindowRateLimiter(
            window_seconds=settings.zai.window_seconds,
            max_requests=settings.zai.max_requests,
        )
        # Optimized timeout: 300s total, 270s read - 5 minute timeout for very slow GLM API responses
        self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=5.0, read=270.0))

    def request(self, messages: List[Dict[str, str]], trace_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Send request to GLM API with latency tracking
        
        Args:
            messages: List of message dicts for GLM
            trace_id: Optional trace ID for latency tracking
        
        Returns:
            GLM API response
        """
        if not self._limiter.hit():
            raise RuntimeError("GLM API kotası aşıldı")
        
        # LATENCY TRACKING: Stage 5d - GLM Request Submitted
        if trace_id:
            tracker = get_latency_tracker()
            trace = tracker.get_trace(trace_id)
            if trace:
                trace.mark_glm_submitted()
                # Write final latency metrics
                tracker.write_latency_metrics(trace)
                logger.info("📊 End-to-end latency trace completed: %s", trace.get_summary())
        
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "max_tokens": 32768,  # 32K output tokens - tam yanıt için yeterli
        }
        
        # === PAYLOAD LOGGING VE DOĞRULAMA ===
        # Her message'ın content uzunluğunu kontrol et
        total_content_length = 0
        for idx, msg in enumerate(messages):
            content = msg.get("content", "")
            content_len = len(content)
            total_content_length += content_len
            logger.info(
                "📤 GLM Message[%d] (%s): %d chars",
                idx,
                msg.get("role", "unknown"),
                content_len,
            )
        
        # JSON serialization sonrası body size kontrolü
        try:
            json_body = json.dumps(payload, ensure_ascii=False)
            body_size_bytes = len(json_body.encode('utf-8'))
            body_size_kb = body_size_bytes / 1024
            logger.info(
                "📤 GLM Payload Stats: %d messages, %d total content chars, "
                "%.2f KB JSON body size",
                len(messages),
                total_content_length,
                body_size_kb,
            )
            
            # Body size kontrolü - eğer çok büyükse uyarı ver
            if body_size_kb > 100:  # 100 KB üzeri
                logger.warning(
                    "⚠️ GLM Payload çok büyük: %.2f KB. "
                    "API limit aşımı riski var!",
                    body_size_kb,
                )
            elif body_size_kb > 50:  # 50 KB üzeri
                logger.warning(
                    "⚠️ GLM Payload büyük: %.2f KB. "
                    "İzlenmesi gerekiyor.",
                    body_size_kb,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Payload logging hatası: %s", exc)
        
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        logger.debug("Calling GLM API with %d messages", len(messages))
        try:
            glm_start = time.time()
            response = self._client.post(self._base_url, json=payload, headers=headers)
            glm_latency_ms = (time.time() - glm_start) * 1000
            response.raise_for_status()
            
            result = response.json()
            result['_glm_latency_ms'] = glm_latency_ms
            
            # Response token kullanımını logla
            try:
                usage = result.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
                
                if prompt_tokens > 0 or completion_tokens > 0:
                    logger.info(
                        "📥 GLM Token Usage: prompt=%d, completion=%d, total=%d",
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                    )
                    
                    # Response içeriğini logla (ilk 500 karakter)
                    choices = result.get("choices", [])
                    if choices:
                        choice = choices[0]
                        message = choice.get("message", {})
                        content = message.get("content", "")
                        
                        if content:
                            logger.info(
                                "📥 GLM Response: %d chars (first 500: %s)",
                                len(content),
                                content[:500],
                            )
                        else:
                            # EMPTY CONTENT BUG - log full response structure
                            logger.error(
                                "🚨 GLM API BUG: %d completion tokens but empty content!",
                                completion_tokens
                            )
                            logger.error(
                                "🔍 Full response structure: %s",
                                json.dumps(result, indent=2, ensure_ascii=False)[:3000]
                            )
                            
                            # Try alternative fields
                            content_alt = choice.get("text") or choice.get("delta", {}).get("content") or ""
                            if content_alt:
                                logger.warning(
                                    "📍 Found content in alternative field (%d chars): %s",
                                    len(content_alt),
                                    content_alt[:200]
                                )
                                # Patch it back into standard field
                                message["content"] = content_alt
                                logger.info("✅ Patched content from alternative field")
                            else:
                                logger.error("❌ No content found in any field - this is a GLM API issue")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Response logging hatası: %s", exc)
            
            logger.debug("GLM API response received in %.0fms", glm_latency_ms)
            return result
        except httpx.ReadTimeout as exc:
            logger.error("GLM API read timeout (waited 270s)")
            raise RuntimeError("GLM API read timeout - response too slow") from exc
        except httpx.ConnectTimeout as exc:
            logger.error("GLM API connection timeout")
            raise RuntimeError("GLM API connection failed") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("GLM API HTTP error: %s", exc.response.status_code)
            raise RuntimeError(f"GLM API error: {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            logger.error("GLM API request failed: %s", exc)
            raise RuntimeError("GLM API network error") from exc
