"""
GLM Communication Module

Handles all GLM API communication:
- Request building
- API calls via GLMClient
- Prompt/Response logging to file
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.risk_manager.glm_client import GLMClient
from app.utils.logging import get_logger

logger = get_logger(__name__)

LOG_DIR = Path("/root/trading/logs")
LOG_FILE = LOG_DIR / "glm_prompts.log"


class GLMCommunicator:
    """Wrapper for GLM communication with automatic logging."""

    def __init__(self, glm_client: Optional[GLMClient] = None):
        """
        Initialize GLM Communicator.

        Args:
            glm_client: Optional GLMClient instance. If not provided, creates default.
        """
        self._glm = glm_client or GLMClient()
        LOG_DIR.mkdir(exist_ok=True)

    def request(
        self,
        messages: List[Dict[str, str]],
        symbol: str = "UNKNOWN",
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send request to GLM and log both prompt and response.

        Args:
            messages: List of message dicts for GLM API
            symbol: Trading symbol for logging context
            trace_id: Optional trace ID for latency tracking

        Returns:
            GLM API response dict
        """
        # Extract and log prompt
        system_msg = ""
        user_content = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg.get("content", "")
            elif msg.get("role") == "user":
                user_content = msg.get("content", "")

        estimated_tokens = len(user_content) // 4
        self._write_prompt(symbol, system_msg, user_content, estimated_tokens)

        # Call GLM
        response = self._glm.request(messages, trace_id=trace_id)

        # Log response
        raw_content = self._extract_content(response)
        if raw_content:
            resp_tokens = len(raw_content) // 4
            self._write_response(raw_content, resp_tokens)

        return response

    def _extract_content(self, response: Dict[str, Any]) -> str:
        """Extract content from GLM response."""
        try:
            choices = response.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
        except Exception:
            pass
        return ""

    def _write_prompt(self, symbol: str, system_msg: str, user_content: str, tokens: int) -> None:
        """Log prompt to file."""
        try:
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*100}\n")
                f.write(f"📤 GLM PROMPT | {ts} | {symbol}\n")
                f.write(f"📊 Stats: {len(user_content)} chars, ~{tokens} tokens\n")
                f.write(f"{'='*100}\n\n")
                f.write(f"🔷 SYSTEM MESSAGE:\n{'-'*50}\n{system_msg}\n\n")
                f.write(f"🔷 USER CONTENT:\n{'-'*50}\n{user_content}\n")
                f.write(f"{'='*100}\n\n")
            logger.info("📝 Prompt logged for %s", symbol)
        except Exception as e:
            logger.warning("Failed to write prompt: %s", e)

    def _write_response(self, content: str, tokens: int) -> None:
        """Log response to file."""
        try:
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n{'-'*100}\n")
                f.write(f"📥 GLM RESPONSE | {ts}\n")
                f.write(f"📊 Stats: {len(content)} chars, ~{tokens} tokens\n")
                f.write(f"{'-'*100}\n")
                f.write(f"{content}\n")
                f.write(f"{'-'*100}\n\n")
            logger.info("📥 Response logged")
        except Exception as e:
            logger.warning("Failed to write response: %s", e)
