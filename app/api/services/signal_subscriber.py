"""
Signal Subscriber - Listens for signals from Redis and broadcasts to WebSocket clients

This runs as a background task in the API server, subscribing to the signal channel
and immediately broadcasting to all connected WebSocket clients.
"""
import asyncio
import json
import threading
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import redis

from app.config.settings import get_settings
from app.data_feeds.constants import SIGNAL_CHANNEL
from app.utils.logging import get_logger

if TYPE_CHECKING:
    from app.api.services.websocket_manager import ConnectionManager

logger = get_logger(__name__)
settings = get_settings()

# Reference to WebSocket manager (set during startup)
_websocket_manager: Optional["ConnectionManager"] = None
_running = False
_event_loop: Optional[asyncio.AbstractEventLoop] = None
_subscriber_thread: Optional[threading.Thread] = None


def set_websocket_manager(manager: "ConnectionManager") -> None:
    """
    Set the WebSocket manager reference for broadcasting.
    Called during API server startup.

    Args:
        manager: ConnectionManager instance from websocket_manager.py
    """
    global _websocket_manager
    _websocket_manager = manager
    logger.info("WebSocket manager registered with signal subscriber")


async def _broadcast_signal(signal_data: dict) -> None:
    """
    Broadcast signal to all WebSocket clients.

    Args:
        signal_data: Signal payload from Redis
    """
    if not _websocket_manager:
        logger.warning("WebSocket manager not set, cannot broadcast signal")
        return

    try:
        # Add server timestamp
        signal_data["server_timestamp"] = datetime.utcnow().isoformat()

        # Broadcast to all connected clients
        await _websocket_manager.broadcast_to_all(signal_data)

        symbol = signal_data.get("signal", {}).get("symbol", "UNKNOWN")
        action = signal_data.get("signal", {}).get("action", "UNKNOWN")
        logger.info("Broadcast signal to WebSocket clients: %s %s", symbol, action)

    except Exception as e:
        logger.error("Failed to broadcast signal: %s", e)


def _redis_listener() -> None:
    """
    Redis subscriber thread - listens for signals and queues for async broadcast.
    Runs in a separate thread to avoid blocking the event loop.
    """
    global _running

    client = None
    pubsub = None

    try:
        client = redis.Redis.from_url(str(settings.redis.url))
        pubsub = client.pubsub()
        pubsub.subscribe(SIGNAL_CHANNEL)

        logger.info("Signal subscriber connected to Redis channel: %s", SIGNAL_CHANNEL)

        for message in pubsub.listen():
            if not _running:
                break

            if message.get("type") != "message":
                continue

            try:
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                payload = json.loads(raw)

                # Schedule async broadcast on the main event loop
                if _websocket_manager and _event_loop:
                    asyncio.run_coroutine_threadsafe(
                        _broadcast_signal(payload),
                        _event_loop
                    )

            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in signal message: %s", e)
            except Exception as e:
                logger.error("Error processing signal message: %s", e)

    except redis.ConnectionError as e:
        logger.error("Redis connection error in signal subscriber: %s", e)
    except Exception as e:
        logger.error("Signal subscriber error: %s", e)
    finally:
        if pubsub:
            try:
                pubsub.unsubscribe(SIGNAL_CHANNEL)
                pubsub.close()
            except Exception:
                pass
        if client:
            try:
                client.close()
            except Exception:
                pass
        logger.info("Signal subscriber disconnected")


async def start_signal_subscriber() -> None:
    """
    Start the signal subscriber background task.
    Called during API server startup.
    """
    global _running, _event_loop, _subscriber_thread

    if _running:
        logger.warning("Signal subscriber already running")
        return

    _running = True
    _event_loop = asyncio.get_event_loop()

    # Start Redis listener in a separate thread
    _subscriber_thread = threading.Thread(
        target=_redis_listener,
        name="signal-subscriber",
        daemon=True
    )
    _subscriber_thread.start()

    logger.info("Signal subscriber started")


async def stop_signal_subscriber() -> None:
    """
    Stop the signal subscriber.
    Called during API server shutdown.
    """
    global _running
    _running = False
    logger.info("Signal subscriber stopping")
