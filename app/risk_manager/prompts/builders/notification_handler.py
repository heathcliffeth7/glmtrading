"""
Notification Handler

Redis-based position close notification handler with security validation.

Responsibilities:
- Check Redis for position close notifications
- Validate notifications with HMAC signature
- Format notification as prompt text

Security Features:
- JSON parsing with error handling
- HMAC signature verification
- Pydantic schema validation
- Timestamp freshness check
- Prompt injection sanitization

Example:
    handler = NotificationHandler(
        settings=get_settings(),
        data_validator=validator
    )
    
    notification = handler.check_notification("BTCUSDT")
    if notification:
        lines = handler.format_notification(notification, "BTCUSDT")
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.risk_manager.prompts.data_validation import DataValidator
from app.risk_manager.prompts.models import PositionCloseNotification

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Redis-based position close notification handler"""

    def __init__(
        self,
        settings,
        data_validator: DataValidator
    ):
        """
        Initialize notification handler.

        Args:
            settings: Application settings (for Redis config and security)
            data_validator: Data validator for HMAC verification and sanitization
        """
        self._settings = settings
        self._data_validator = data_validator
        self._redis_client = None  # Lazy initialization

    def check_notification(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Check for position close notification in Redis with security validation.

        Security measures:
        1. JSON parsing with error handling
        2. HMAC signature verification (if secret configured)
        3. Pydantic schema validation
        4. Timestamp freshness check (max 5 minutes)

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")

        Returns:
            Notification dict if valid, None otherwise
        """
        try:
            from app.utils.redis import get_redis_client

            redis_client = get_redis_client()
            redis_key = f"position_closed:{symbol}"
            notification_json = redis_client.get(redis_key)

            if not notification_json:
                return None

            # 1. Parse JSON safely
            try:
                raw_data = json.loads(notification_json)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in Redis notification: %s", e)
                redis_client.delete(redis_key)
                return None

            # 2. Extract and verify HMAC signature
            signature = raw_data.pop('signature', '')
            if not self._data_validator.verify_hmac_signature(raw_data, signature):
                logger.warning("⚠️ HMAC verification failed for Redis notification - possible injection attempt!")
                redis_client.delete(redis_key)
                return None

            # 3. Validate with Pydantic model
            try:
                notification = PositionCloseNotification(**raw_data, signature=signature)
            except Exception as e:
                logger.error("Schema validation failed for notification: %s", e)
                redis_client.delete(redis_key)
                return None

            # 4. Check timestamp freshness
            try:
                max_age = self._settings.security.notification_max_age_minutes
                notif_time = datetime.fromisoformat(notification.timestamp.replace('Z', '+00:00'))
                age = datetime.utcnow() - notif_time.replace(tzinfo=None)
                if age > timedelta(minutes=max_age):
                    logger.warning(
                        "Stale notification ignored (age: %s, max: %d min)",
                        age, max_age
                    )
                    redis_client.delete(redis_key)
                    return None
            except (ValueError, TypeError) as e:
                logger.warning("Could not parse notification timestamp: %s", e)
                # Continue anyway - timestamp format may vary

            # Success - delete from Redis and return
            redis_client.delete(redis_key)
            logger.info(
                "✅ GLM read validated position close notification | trigger=%s, pnl=%.2f",
                notification.trigger_type, notification.pnl
            )
            return notification.model_dump()

        except Exception as exc:
            logger.error("Failed to read position close notification from Redis: %s", exc)
            return None

    def format_notification(
        self, 
        notification: Dict[str, Any], 
        symbol: str
    ) -> List[str]:
        """
        Format notification as prompt lines.

        Args:
            notification: Notification dict from check_notification()
            symbol: Trading symbol (e.g., "BTCUSDT")

        Returns:
            List of formatted text lines
        """
        base_asset = symbol.replace("USDT", "")
        trigger_type = notification.get("trigger_type", "unknown")
        
        # SECURITY: Sanitize reason field to prevent prompt injection
        reason = self._data_validator.sanitize_prompt_input(notification.get("reason", "N/A"))
        
        timestamp = notification.get("timestamp", "N/A")
        entry_price = notification.get("entry_price", 0)
        exit_price = notification.get("exit_price", 0)
        pnl = notification.get("pnl", 0)
        pnl_pct = notification.get("pnl_pct", 0)
        position_type = notification.get("position_type", "UNKNOWN")
        quantity = notification.get("quantity", 0)

        emoji_map = {"stop_loss": "🛑", "invalidation": "⚠️"}
        emoji = emoji_map.get(trigger_type, "🔔")

        return [
            "=" * 80,
            f"{emoji} RECENT POSITION CLOSE NOTIFICATION (AUTOMATIC)",
            "=" * 80,
            "",
            f"⏰ Time: {timestamp}",
            f"🎯 Trigger: {trigger_type.upper().replace('_', ' ')}",
            f"📊 Position Type: {position_type}",
            f"📦 Quantity: {quantity:.6f} {base_asset}",
            "",
            f"💵 Entry Price: ${entry_price:,.2f}",
            f"💵 Exit Price: ${exit_price:,.2f}",
            f"💰 PnL: ${pnl:,.2f} ({pnl_pct:+.2f}%)",
            "",
            f"💬 Reason: {reason}",
            "",
            "✅ Position was AUTOMATICALLY CLOSED by exit plan monitor",
            "Focus on NEW opportunities; do not issue CLOSE unless a new position is open.",
            "",
        ]
