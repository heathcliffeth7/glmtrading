"""
Signal Publisher - Push-based notification for new trading signals

Publishes signals to Redis channel for real-time WebSocket broadcast.
Works independently of API server - if no subscribers, messages are simply ignored.
"""
import json
from datetime import datetime
from typing import Dict, Any, Optional

from app.data_feeds.constants import SIGNAL_CHANNEL
from app.utils.redis import publish_safe
from app.utils.logging import get_logger

logger = get_logger(__name__)


def publish_signal(
    symbol: str,
    action: str,
    reasoning: str,
    amount: float = 0.0,
    leverage: float = 1.0,
    equity: float = 0.0,
    confidence: float = 0.0,
    current_price: float = 0.0,
    composite_bias: float = 0.0,
    interval: str = "",
    trace_id: str = "",
    # Penalty tracking fields
    original_confidence: float = 0.0,
    original_action: str = "",
    original_reasoning: str = "",
    penalty_breakdown: Optional[Dict[str, float]] = None,
    total_penalty: float = 0.0,
) -> bool:
    """
    Publish trading signal to Redis for real-time WebSocket broadcast.

    This is a fire-and-forget operation - if API server isn't running
    or Redis is down, the signal is still logged to InfluxDB normally.

    Args:
        symbol: Trading symbol (e.g., BTCUSDT)
        action: Trading action (BUY, SELL, HOLD, CLOSE)
        reasoning: GLM reasoning text
        amount: Position size
        leverage: Leverage used
        equity: Current equity
        confidence: GLM confidence score (0-100)
        current_price: Current market price
        composite_bias: Composite bias score
        interval: Timeframe interval
        trace_id: Trace ID for debugging
        original_confidence: Qwen's original confidence before penalties
        original_action: Qwen's original action before forcing HOLD
        original_reasoning: Qwen's original reasoning
        penalty_breakdown: Dict of penalty names and values
        total_penalty: Sum of all penalties applied

    Returns:
        True if published successfully, False otherwise
    """
    try:
        payload = {
            "type": "new_signal",
            "signal": {
                "symbol": symbol,
                "action": action,
                "reasoning": reasoning,
                "amount": amount,
                "leverage": leverage,
                "equity": equity,
                "confidence": confidence,
                "current_price": current_price,
                "composite_bias": composite_bias,
                "interval": interval,
                "trace_id": trace_id,
                # Penalty tracking for detailed reasoning display
                "original_confidence": original_confidence,
                "original_action": original_action,
                "original_reasoning": original_reasoning,
                "penalty_breakdown": penalty_breakdown or {},
                "total_penalty": total_penalty,
            },
            "timestamp": datetime.utcnow().isoformat(),
            "source": "orchestrator",
        }

        publish_safe(SIGNAL_CHANNEL, payload)
        logger.info("Signal published to Redis: %s %s", symbol, action)
        return True

    except Exception as e:
        # Non-fatal: Signal is still logged to InfluxDB
        logger.warning("Failed to publish signal to Redis: %s", e)
        return False
