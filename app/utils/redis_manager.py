"""
Centralized Redis Connection Manager

Features:
- Connection pooling (sync: 10, async: 10 per loop)
- Circuit breaker pattern (5 failures → 60s wait)
- Graceful shutdown hooks (atexit + SIGTERM)
- Health metrics (latency, failed connections)
- Pub/Sub connection limit (max 10)
"""

import atexit
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

import redis
import redis.asyncio as aioredis

from app.config.settings import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PoolMetrics:
    """Connection pool health metrics"""
    pool_size: int = 10
    connections_in_use: int = 0
    failed_connections: int = 0
    last_health_check: Optional[datetime] = None
    latency_ms: float = 0.0
    circuit_breaker_state: str = "CLOSED"


class CircuitBreaker:
    """Circuit breaker for Redis connection failures"""

    def __init__(self, threshold: int = 5, timeout: int = 60):
        self._threshold = threshold
        self._timeout = timeout
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self._lock = threading.Lock()

    def record_failure(self) -> None:
        """Record a connection failure"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self._threshold:
                self._state = "OPEN"
                logger.warning(
                    "Circuit breaker OPENED after %d failures (will retry in %ds)",
                    self._failure_count, self._timeout
                )

    def record_success(self) -> None:
        """Record a successful operation"""
        with self._lock:
            self._failure_count = 0
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                logger.info("Circuit breaker CLOSED - connection recovered")

    def can_execute(self) -> bool:
        """Check if operations can proceed"""
        with self._lock:
            if self._state == "CLOSED":
                return True
            if self._state == "OPEN":
                if self._last_failure_time and (time.time() - self._last_failure_time >= self._timeout):
                    self._state = "HALF_OPEN"
                    logger.info("Circuit breaker HALF_OPEN - testing connection")
                    return True
                return False
            return True  # HALF_OPEN allows one test request

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count


class RedisConnectionManager:
    """
    Singleton manager for all Redis connections in a service process.

    Usage:
        from app.utils.redis_manager import get_redis_manager

        # Sync client
        client = get_redis_manager().get_sync_client()
        client.ping()

        # Async client
        client = await get_redis_manager().get_async_client()
        await client.ping()

        # Pub/Sub
        pubsub = get_redis_manager().get_pubsub("price_listener_BTCUSDT")
        # ... use pubsub ...
        get_redis_manager().release_pubsub("price_listener_BTCUSDT")
    """

    _instance: Optional["RedisConnectionManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "RedisConnectionManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self._settings = get_settings().redis
        self._shutdown_event = threading.Event()

        # Sync connection pool
        self._sync_pool: Optional[redis.ConnectionPool] = None
        self._sync_client: Optional[redis.Redis] = None

        # Async clients per event loop
        self._async_clients: Dict[int, aioredis.Redis] = {}
        self._async_lock = threading.Lock()

        # Pub/Sub connections (named, for tracking)
        self._pubsub_connections: Dict[str, redis.client.PubSub] = {}
        self._pubsub_clients: Dict[str, redis.Redis] = {}  # Keep client refs for cleanup
        self._pubsub_lock = threading.Lock()
        self._max_pubsub_connections = 10

        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(threshold=5, timeout=60)

        # Metrics
        self._metrics = PoolMetrics()

        # Initialize
        self._init_pools()
        self._register_shutdown_hooks()

        self._initialized = True
        logger.info("RedisConnectionManager initialized (pool_size=10)")

    def _init_pools(self) -> None:
        """Initialize connection pools"""
        try:
            # Use configurable timeout from settings (default: 30s)
            socket_timeout = float(self._settings.socket_timeout)
            socket_keepalive = self._settings.socket_keepalive

            self._sync_pool = redis.ConnectionPool.from_url(
                str(self._settings.url),
                max_connections=10,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_timeout,
                health_check_interval=30,
                socket_keepalive=socket_keepalive,
            )
            self._sync_client = redis.Redis(connection_pool=self._sync_pool)
            logger.info(
                "Redis sync pool config: socket_timeout=%.1fs, keepalive=%s",
                socket_timeout, socket_keepalive
            )

            # Test connection
            self._sync_client.ping()
            logger.info("Sync Redis pool initialized: max_connections=10")

        except Exception as e:
            logger.error("Failed to initialize Redis pool: %s", e)
            self._circuit_breaker.record_failure()

    def get_sync_client(self) -> redis.Redis:
        """
        Get pooled sync Redis client.

        Raises:
            RuntimeError: If circuit breaker is open
        """
        if not self._circuit_breaker.can_execute():
            raise RuntimeError(
                f"Redis circuit breaker is OPEN (failures: {self._circuit_breaker.failure_count})"
            )
        return self._sync_client

    async def get_async_client(self) -> aioredis.Redis:
        """
        Get pooled async Redis client for current event loop.
        Creates a new client if one doesn't exist for this loop.
        """
        import asyncio
        loop_id = id(asyncio.get_running_loop())

        with self._async_lock:
            if loop_id not in self._async_clients:
                self._async_clients[loop_id] = aioredis.from_url(
                    str(self._settings.url),
                    max_connections=10,
                    decode_responses=True,
                )
                logger.debug("Async Redis client initialized for loop %d", loop_id)

            return self._async_clients[loop_id]

    def get_pubsub(self, name: str) -> redis.client.PubSub:
        """
        Get or create a named pub/sub connection.

        Args:
            name: Unique name for this pub/sub connection (e.g., "price_listener_BTCUSDT")

        Returns:
            PubSub object for subscribing to channels

        Raises:
            RuntimeError: If pub/sub connection limit reached
        """
        with self._pubsub_lock:
            if name in self._pubsub_connections:
                return self._pubsub_connections[name]

            if len(self._pubsub_connections) >= self._max_pubsub_connections:
                raise RuntimeError(
                    f"Pub/Sub connection limit reached ({self._max_pubsub_connections}). "
                    f"Active: {list(self._pubsub_connections.keys())}"
                )

            # Create dedicated client for this pubsub (required for blocking listen())
            # Use longer timeout for pub/sub (default: 60s) since listen() is blocking
            pubsub_timeout = float(self._settings.pubsub_socket_timeout)
            socket_keepalive = self._settings.socket_keepalive

            client = redis.Redis.from_url(
                str(self._settings.url),
                socket_timeout=pubsub_timeout,
                socket_keepalive=socket_keepalive,
            )
            pubsub = client.pubsub()
            logger.debug(
                "PubSub client config: socket_timeout=%.1fs, keepalive=%s",
                pubsub_timeout, socket_keepalive
            )

            self._pubsub_clients[name] = client
            self._pubsub_connections[name] = pubsub

            logger.info(
                "Created pub/sub: %s (total: %d/%d)",
                name, len(self._pubsub_connections), self._max_pubsub_connections
            )

            return pubsub

    def release_pubsub(self, name: str) -> None:
        """
        Release a named pub/sub connection.

        Args:
            name: Name of the pub/sub connection to release
        """
        with self._pubsub_lock:
            if name not in self._pubsub_connections:
                return

            try:
                pubsub = self._pubsub_connections[name]
                pubsub.unsubscribe()
                pubsub.close()
            except Exception as e:
                logger.warning("Error closing pubsub %s: %s", name, e)

            try:
                client = self._pubsub_clients.get(name)
                if client:
                    client.close()
            except Exception as e:
                logger.warning("Error closing pubsub client %s: %s", name, e)

            self._pubsub_connections.pop(name, None)
            self._pubsub_clients.pop(name, None)

            logger.info(
                "Released pub/sub: %s (remaining: %d)",
                name, len(self._pubsub_connections)
            )

    def get_health_metrics(self) -> PoolMetrics:
        """Get pool health metrics"""
        try:
            start = time.time()
            self._sync_client.ping()
            self._metrics.latency_ms = (time.time() - start) * 1000
            self._metrics.last_health_check = datetime.utcnow()
            self._metrics.circuit_breaker_state = self._circuit_breaker.state
            self._circuit_breaker.record_success()
        except Exception as e:
            logger.warning("Health check failed: %s", e)
            self._circuit_breaker.record_failure()
            self._metrics.failed_connections += 1
            self._metrics.circuit_breaker_state = self._circuit_breaker.state

        return self._metrics

    def get_connection_count(self) -> Dict[str, int]:
        """Get current connection counts"""
        return {
            "sync_pool": 1,
            "async_clients": len(self._async_clients),
            "pubsub_connections": len(self._pubsub_connections),
            "total": 1 + len(self._async_clients) + len(self._pubsub_connections),
        }

    def _register_shutdown_hooks(self) -> None:
        """Register graceful shutdown hooks"""
        atexit.register(self.shutdown)

        # Handle SIGTERM for systemd services
        def _signal_handler(signum, frame):
            logger.info("Received signal %d, initiating shutdown", signum)
            self.shutdown()

        try:
            signal.signal(signal.SIGTERM, _signal_handler)
        except ValueError:
            # Signal handling may fail in non-main threads
            pass

    def shutdown(self) -> None:
        """Graceful shutdown of all connections"""
        if self._shutdown_event.is_set():
            return

        self._shutdown_event.set()
        logger.info("RedisConnectionManager shutting down...")

        # Close pub/sub connections
        with self._pubsub_lock:
            for name in list(self._pubsub_connections.keys()):
                try:
                    pubsub = self._pubsub_connections[name]
                    pubsub.unsubscribe()
                    pubsub.close()
                except Exception:
                    pass

                try:
                    client = self._pubsub_clients.get(name)
                    if client:
                        client.close()
                except Exception:
                    pass

            self._pubsub_connections.clear()
            self._pubsub_clients.clear()

        # Close async clients
        with self._async_lock:
            for loop_id, client in list(self._async_clients.items()):
                try:
                    # Can't await here, just mark for cleanup
                    pass
                except Exception:
                    pass
            self._async_clients.clear()

        # Disconnect sync pool
        if self._sync_pool:
            try:
                self._sync_pool.disconnect()
            except Exception:
                pass

        logger.info("RedisConnectionManager shutdown complete")


# Singleton accessor
_manager: Optional[RedisConnectionManager] = None
_manager_lock = threading.Lock()


def get_redis_manager() -> RedisConnectionManager:
    """Get the singleton RedisConnectionManager instance"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = RedisConnectionManager()
    return _manager
