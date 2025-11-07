import json
import threading
import time
from collections import deque
from typing import Any, Dict

import redis

from app.config.settings import get_settings
from app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


_publisher = redis.Redis.from_url(str(settings.redis.url))

# Local message queue for Redis failure resilience
_local_queue: deque = deque(maxlen=1000)  # Keep last 1000 messages
_redis_connected = True
_queue_lock = threading.Lock()
_retry_thread_started = False


def _start_retry_thread() -> None:
    """Start background thread to retry publishing queued messages"""
    global _retry_thread_started

    if _retry_thread_started:
        return

    _retry_thread_started = True

    def _retry_worker():
        global _redis_connected

        while True:
            time.sleep(5)  # Check every 5 seconds

            with _queue_lock:
                if not _local_queue:
                    continue

                # Try to reconnect and flush queue
                try:
                    # Test connection
                    _publisher.ping()
                    _redis_connected = True

                    # Flush queued messages
                    flushed = 0
                    while _local_queue:
                        channel, payload = _local_queue.popleft()
                        try:
                            _publisher.publish(channel, json.dumps(payload, default=str))
                            flushed += 1
                        except Exception as e:
                            # Re-queue if publish fails
                            _local_queue.appendleft((channel, payload))
                            logger.warning("Failed to flush queued message: %s", e)
                            break

                    if flushed > 0:
                        logger.info("✅ Redis reconnected, flushed %d queued messages", flushed)

                except Exception:
                    _redis_connected = False

    thread = threading.Thread(target=_retry_worker, name="redis-retry", daemon=True)
    thread.start()
    logger.info("Started Redis retry thread")


def publish(channel: str, payload: Dict[str, Any]) -> None:
    """Original publish function - raises exception on failure"""
    _publisher.publish(channel, json.dumps(payload, default=str))


def publish_safe(channel: str, payload: Dict[str, Any]) -> None:
    """
    Safe publish with local queue fallback

    If Redis connection fails, critical data (e.g., 5m kline closures)
    are temporarily stored in a local queue until Redis reconnects.

    Args:
        channel: Redis channel name
        payload: Message payload
    """
    global _redis_connected

    try:
        _publisher.publish(channel, json.dumps(payload, default=str))
        _redis_connected = True
    except Exception as e:
        logger.warning("⚠️ Redis publish failed, queuing message locally: %s", e)
        _redis_connected = False

        # Queue message locally
        with _queue_lock:
            _local_queue.append((channel, payload))
            logger.info(
                "📦 Queued message locally (queue size: %d/%d)",
                len(_local_queue),
                _local_queue.maxlen,
            )

        # Start retry thread if not already running
        _start_retry_thread()


def get_queue_status() -> Dict[str, Any]:
    """Get current status of local queue and Redis connection"""
    with _queue_lock:
        return {
            "redis_connected": _redis_connected,
            "queue_size": len(_local_queue),
            "queue_max_size": _local_queue.maxlen,
            "queue_full": len(_local_queue) >= _local_queue.maxlen,
        }


def get_redis_client() -> redis.Redis:
    """
    Get Redis client for direct operations (e.g., set/get for notifications)

    Returns:
        Redis client instance
    """
    return _publisher
