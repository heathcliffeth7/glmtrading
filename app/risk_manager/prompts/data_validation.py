"""
Data Validation Module

Security utilities for input sanitization and safe operations.
"""

import hashlib
import hmac
import json
import re
from typing import Any, Dict

from app.utils.logging import get_logger


logger = get_logger(__name__)


class DataValidator:
    """
    Validates and sanitizes data for prompt building.
    """

    def __init__(self, hmac_secret: str = None):
        """
        Initialize the data validator.

        Args:
            hmac_secret: Secret key for HMAC verification (optional)
        """
        self._hmac_secret = hmac_secret

    def sanitize_prompt_input(self, text: str, max_length: int = 200) -> str:
        """
        Sanitize user-provided text to prevent prompt injection attacks.

        Protections:
        1. Length limit to prevent context overflow
        2. Remove dangerous instruction patterns
        3. Strip newlines to prevent multi-line injection
        4. Keep only printable characters

        Args:
            text: Input text to sanitize
            max_length: Maximum allowed length

        Returns:
            Sanitized text
        """
        if not text:
            return "N/A"

        # 1. Length limit
        text = text[:max_length]

        # 2. Remove dangerous patterns (case-insensitive)
        dangerous_patterns = [
            r"ignore\s+(all\s+)?(previous\s+)?instructions?",
            r"system\s+override",
            r"you\s+must\s+output",
            r"forget\s+(everything|all)",
            r"new\s+instructions?:",
            r"```json",  # JSON block injection
            r"={10,}",  # Separator injection
            r"-{10,}",  # Separator injection
            r"CRITICAL\s+SYSTEM",
            r"OVERRIDE",
        ]

        for pattern in dangerous_patterns:
            text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)

        # 3. Remove newlines (prevent multi-line injection)
        text = text.replace("\n", " ").replace("\r", " ")

        # 4. Keep only printable characters
        text = "".join(c for c in text if c.isprintable())

        # 5. Collapse multiple spaces
        text = re.sub(r"\s+", " ", text).strip()

        return text if text else "N/A"

    def safe_divide(
        self, numerator: float, denominator: float, default: float = 0.0
    ) -> float:
        """
        Safe division that handles zero/near-zero denominators.

        Args:
            numerator: The number to divide
            denominator: The number to divide by
            default: Value to return if division is unsafe

        Returns:
            Result of division or default value
        """
        if abs(denominator) < 1e-10:
            return default
        return numerator / denominator

    def verify_hmac_signature(
        self, data: Dict[str, Any], signature: str
    ) -> bool:
        """
        Verify HMAC signature for Redis notification data.

        Args:
            data: Dictionary of notification data (without signature)
            signature: HMAC signature to verify

        Returns:
            True if signature is valid, False otherwise
        """
        if not self._hmac_secret:
            # No secret configured - skip HMAC validation (backward compatible)
            logger.debug("HMAC validation skipped - no secret configured")
            return True

        try:
            # Create canonical JSON string for signing
            payload = json.dumps(data, sort_keys=True).encode("utf-8")
            expected_sig = hmac.new(
                self._hmac_secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).hexdigest()

            return hmac.compare_digest(signature, expected_sig)
        except Exception as e:
            logger.error("HMAC verification error: %s", e)
            return False

    def validate_price(self, price: float, min_val: float = 0.0) -> bool:
        """
        Validate a price value.

        Args:
            price: Price to validate
            min_val: Minimum valid value

        Returns:
            True if valid
        """
        return price is not None and price > min_val

    def validate_percentage(
        self, value: float, min_val: float = 0.0, max_val: float = 100.0
    ) -> bool:
        """
        Validate a percentage value.

        Args:
            value: Value to validate
            min_val: Minimum valid value
            max_val: Maximum valid value

        Returns:
            True if valid
        """
        return value is not None and min_val <= value <= max_val

    def clamp(
        self, value: float, min_val: float, max_val: float
    ) -> float:
        """
        Clamp a value to a range.

        Args:
            value: Value to clamp
            min_val: Minimum value
            max_val: Maximum value

        Returns:
            Clamped value
        """
        return max(min_val, min(value, max_val))

    def validate_exit_plan(self, exit_plan: Dict) -> tuple[bool, str]:
        """
        Validate an exit plan dictionary.

        Args:
            exit_plan: Exit plan to validate

        Returns:
            (is_valid, error_message)
        """
        if not exit_plan:
            return False, "Exit plan is missing"

        stop_loss = exit_plan.get("stop_loss")
        profit_target = exit_plan.get("profit_target")
        invalidation = exit_plan.get("invalidation_condition")

        if not stop_loss or stop_loss <= 0:
            return False, "Stop loss is missing or invalid"

        if not profit_target or profit_target <= 0:
            return False, "Profit target is missing or invalid"

        if not invalidation or not isinstance(invalidation, str):
            return False, "Invalidation condition is missing"

        return True, ""

    def sanitize_dict_values(
        self, data: Dict, max_string_length: int = 200
    ) -> Dict:
        """
        Sanitize all string values in a dictionary.

        Args:
            data: Dictionary to sanitize
            max_string_length: Maximum string length

        Returns:
            Sanitized dictionary
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.sanitize_prompt_input(value, max_string_length)
            elif isinstance(value, dict):
                result[key] = self.sanitize_dict_values(value, max_string_length)
            else:
                result[key] = value
        return result
