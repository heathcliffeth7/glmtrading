import asyncio
import json
import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Tuple

import redis
import redis.asyncio as aioredis

from app.config.settings import get_settings
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)


def _get_publisher() -> redis.Redis:
    """Get sync Redis client from connection manager (with pooling)"""
    from app.utils.redis_manager import get_redis_manager
    return get_redis_manager().get_sync_client()

# Local message queue for Redis failure resilience
_local_queue: deque = deque(maxlen=50000)  # Keep last 50000 messages (increased for burst handling)
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
                    # Get client from manager (with circuit breaker)
                    publisher = _get_publisher()
                    publisher.ping()
                    _redis_connected = True

                    # Flush queued messages
                    flushed = 0
                    while _local_queue:
                        channel, payload = _local_queue.popleft()
                        try:
                            publisher.publish(channel, json.dumps(payload, default=str))
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
    _get_publisher().publish(channel, json.dumps(payload, default=str))


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
        _get_publisher().publish(channel, json.dumps(payload, default=str))
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
        Redis client instance (from connection pool)
    """
    return _get_publisher()


# =============================================================================
# ASYNC REDIS PUBLISHING - Non-blocking, high-performance
# =============================================================================

# Per-loop clients to avoid "attached to different loop" errors
_async_clients: Dict[int, aioredis.Redis] = {}
_async_queues: Dict[int, asyncio.Queue] = {}


def _get_loop_id() -> int:
    """Get current event loop's ID."""
    try:
        loop = asyncio.get_running_loop()
        return id(loop)
    except RuntimeError:
        return 0


async def _ensure_async_client() -> aioredis.Redis:
    """Get or create async Redis client for current event loop (from manager)."""
    from app.utils.redis_manager import get_redis_manager
    return await get_redis_manager().get_async_client()


def _get_async_queue() -> asyncio.Queue:
    """Get or create async queue for current event loop."""
    loop_id = _get_loop_id()
    if loop_id not in _async_queues or _async_queues[loop_id] is None:
        _async_queues[loop_id] = asyncio.Queue(maxsize=50000)  # Increased for burst handling
    return _async_queues[loop_id]


async def publish_async(channel: str, payload: Dict[str, Any]) -> None:
    """
    Non-blocking async publish to Redis.
    Falls back to local queue if Redis connection fails.

    Args:
        channel: Redis channel name
        payload: Message payload
    """
    try:
        client = await _ensure_async_client()
        await client.publish(channel, json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("⚠️ Async Redis publish failed, queuing: %s", e)
        try:
            queue = _get_async_queue()
            queue.put_nowait((channel, payload))
        except asyncio.QueueFull:
            # Drop oldest message, add new one
            try:
                queue.get_nowait()
                queue.put_nowait((channel, payload))
                logger.warning("Async queue full, dropped oldest message")
            except asyncio.QueueEmpty:
                pass
        except Exception:
            # Fallback to sync publish_safe if async fails completely
            publish_safe(channel, payload)


_async_queue_flusher_started: Dict[int, bool] = {}


async def start_async_queue_flusher() -> None:
    """
    Background task to flush queued messages when Redis reconnects.
    Should be started once per event loop.
    """
    loop_id = _get_loop_id()

    if _async_queue_flusher_started.get(loop_id, False):
        return

    _async_queue_flusher_started[loop_id] = True
    logger.info("Starting async Redis queue flusher for loop %d", loop_id)

    while True:
        await asyncio.sleep(1)  # Check every second

        queue = _get_async_queue()
        if queue.empty():
            continue

        try:
            client = await _ensure_async_client()
            flushed = 0

            while not queue.empty():
                try:
                    channel, payload = queue.get_nowait()
                    await client.publish(channel, json.dumps(payload, default=str))
                    flushed += 1
                except asyncio.QueueEmpty:
                    break
                except Exception as e:
                    logger.warning("Failed to flush async queued message: %s", e)
                    # Re-queue
                    try:
                        queue.put_nowait((channel, payload))
                    except asyncio.QueueFull:
                        pass
                    break

            if flushed > 0:
                logger.info("✅ Async Redis flusher: sent %d queued messages", flushed)

        except Exception as e:
            logger.warning("Async Redis flusher error: %s", e)


async def close_async_client() -> None:
    """Close async Redis client for current event loop (for graceful shutdown)."""
    loop_id = _get_loop_id()
    client = _async_clients.get(loop_id)
    if client:
        try:
            await client.close()
            logger.info("Async Redis client closed for loop %d", loop_id)
        except Exception as e:
            logger.error("Error closing async Redis client: %s", e)
        finally:
            _async_clients.pop(loop_id, None)
            _async_queues.pop(loop_id, None)
            _async_queue_flusher_started.pop(loop_id, None)
