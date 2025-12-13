"""WebSocket API Routes for Real-time Updates"""
import asyncio
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.services.websocket_manager import manager
from app.api.dependencies import SessionLocal
from app.config.settings import get_settings
from app.data_feeds.constants import SIGNAL_CHANNEL, PRICE_CHANNEL
from app.executor.ledger import Trade
from app.utils.influx import query_historical_snapshots
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Redis key for cross-worker trade ID tracking
REDIS_LAST_TRADE_KEY = "dashboard:last_broadcast_trade_id"

# Connection timeout (5 minutes of inactivity)
CONNECTION_TIMEOUT = 300
# Heartbeat interval to keep Cloudflare/websocket proxies from closing idle connections early
HEARTBEAT_INTERVAL = 5

# Shared async Redis client for signal subscriptions (memory optimization)
_shared_async_redis: Optional[aioredis.Redis] = None


async def _get_shared_async_redis() -> aioredis.Redis:
    """Get or create shared async Redis client (memory optimization)"""
    global _shared_async_redis
    if _shared_async_redis is None:
        _shared_async_redis = aioredis.from_url(
            str(settings.redis.url),
            decode_responses=True,
            max_connections=40  # Connection pool (increased for multiple pubsub subscribers)
        )
    return _shared_async_redis


async def get_current_prices() -> Dict[str, float]:
    """Get current prices from Binance (async to avoid blocking event loop)"""
    prices = {}
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                timeout=5.0
            )
            data = response.json()
            for item in data:
                if item["symbol"] in SYMBOLS:
                    prices[item["symbol"]] = float(item["price"])
    except Exception:
        pass
    return prices


# Global price publisher task (publishes to Redis for cross-worker distribution)
_price_publisher_task: Optional[asyncio.Task] = None


PRICE_PUBLISHER_LOCK = "dashboard:price_publisher_lock"


async def global_price_publisher_task():
    """
    Global task to fetch prices from Binance and publish to Redis.

    Uses Redis distributed lock to ensure only ONE worker publishes prices.
    Other workers skip publishing if lock is held.
    """
    logger.info("Global price publisher task started")
    try:
        redis_client = await _get_shared_async_redis()

        while True:
            try:
                # Try to acquire lock (expires in 2s, we publish every 1s)
                lock_acquired = await redis_client.set(
                    PRICE_PUBLISHER_LOCK, "1", nx=True, ex=2
                )

                if lock_acquired:
                    prices = await get_current_prices()
                    for symbol in SYMBOLS:
                        if symbol in prices:
                            message = json.dumps({
                                "type": "price_update",
                                "symbol": symbol,
                                "price": prices[symbol],
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                            await redis_client.publish(PRICE_CHANNEL, message)
            except Exception as e:
                logger.warning("Price publish error: %s", e)

            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Global price publisher task cancelled")


def _ensure_price_publisher():
    """Ensure global price publisher task is running"""
    global _price_publisher_task
    if _price_publisher_task is None or _price_publisher_task.done():
        _price_publisher_task = asyncio.create_task(global_price_publisher_task())
        logger.info("Started global price publisher task")


def _format_ws_state(websocket: WebSocket) -> str:
    """Compact helper to log websocket state without raising"""
    return (
        f"app_state={getattr(websocket, 'application_state', None)} "
        f"client_state={getattr(websocket, 'client_state', None)} "
        f"subscribed={len(manager.connection_subscriptions.get(websocket, set()))}"
    )


async def server_heartbeat_task(websocket: WebSocket):
    """
    Server-side heartbeat to keep connection alive through Cloudflare.

    Cloudflare can drop idle connections early (observed <15s in some cases).
    First heartbeat is delayed to let client fully initialize.
    """
    try:
        # Brief wait before first heartbeat - client already received "connected"
        await asyncio.sleep(0.2)

        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.utcnow().isoformat()
                })
            except Exception as e:
                # Log details once; the caller will handle cleanup
                logger.info(
                    "Heartbeat send failed; closing connection | err=%s | app_state=%s | client_state=%s",
                    e,
                    getattr(websocket, "application_state", None),
                    getattr(websocket, "client_state", None),
                )
                raise
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def signal_subscriber_task(websocket: WebSocket):
    """
    Per-connection Redis subscriber for real-time AI signals.

    Uses shared Redis client pool for memory optimization.
    Uses get_message with timeout instead of listen() for proper cancellation support.
    Includes auto-reconnect on consecutive failures.
    """
    pubsub = None
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3

    try:
        # Use shared Redis client instead of creating new one per connection
        client = await _get_shared_async_redis()
        pubsub = client.pubsub()
        await pubsub.subscribe(SIGNAL_CHANNEL)

        logger.info("Signal subscriber started for WebSocket client (using shared pool)")

        # Use polling with timeout instead of blocking listen()
        # This allows proper task cancellation without connection leaks
        while True:
            try:
                # Poll for message with timeout - allows cancellation to interrupt
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=5.0
                )

                # Reset failure counter on successful poll
                consecutive_failures = 0

                if message is None:
                    continue

                payload = json.loads(message["data"])
                payload["server_timestamp"] = datetime.utcnow().isoformat()
                await websocket.send_json(payload)
                logger.debug("Signal sent to WebSocket client: %s", payload.get("signal", {}).get("symbol", "?"))

            except asyncio.TimeoutError:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        "Signal subscriber: %d consecutive timeouts, reconnecting pubsub",
                        consecutive_failures
                    )
                    # Reconnect pubsub
                    try:
                        await asyncio.wait_for(pubsub.unsubscribe(SIGNAL_CHANNEL), timeout=2.0)
                        await asyncio.wait_for(pubsub.close(), timeout=2.0)
                    except Exception:
                        pass
                    pubsub = client.pubsub()
                    await pubsub.subscribe(SIGNAL_CHANNEL)
                    consecutive_failures = 0
                    logger.info("Signal subscriber reconnected successfully")
                continue
            except Exception as e:
                logger.warning("Failed to send signal to WebSocket client: %s", e)
                break

    except asyncio.CancelledError:
        logger.debug("Signal subscriber task cancelled")
    except Exception as e:
        logger.warning("Signal subscriber error: %s", e)
    finally:
        # Cleanup pubsub with timeout to prevent hanging
        if pubsub:
            try:
                await asyncio.wait_for(pubsub.unsubscribe(SIGNAL_CHANNEL), timeout=2.0)
            except Exception:
                pass
            try:
                await asyncio.wait_for(pubsub.close(), timeout=2.0)
            except Exception:
                pass
        logger.debug("Signal subscriber cleanup complete")


async def price_subscriber_task(websocket: WebSocket):
    """
    Per-connection Redis subscriber for real-time price updates.

    Subscribes to PRICE_CHANNEL and forwards prices to WebSocket.
    This enables multi-worker support - prices are distributed via Redis.
    Includes auto-reconnect on consecutive failures.
    """
    pubsub = None
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3

    try:
        client = await _get_shared_async_redis()
        pubsub = client.pubsub()
        await pubsub.subscribe(PRICE_CHANNEL)

        logger.debug("Price subscriber started for WebSocket client")

        while True:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=5.0
                )

                # Reset failure counter on successful poll
                consecutive_failures = 0

                if message is None:
                    continue

                # Forward price update to WebSocket
                payload = json.loads(message["data"])
                await websocket.send_json(payload)

            except asyncio.TimeoutError:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        "Price subscriber: %d consecutive timeouts, reconnecting pubsub",
                        consecutive_failures
                    )
                    # Reconnect pubsub
                    try:
                        await asyncio.wait_for(pubsub.unsubscribe(PRICE_CHANNEL), timeout=2.0)
                        await asyncio.wait_for(pubsub.close(), timeout=2.0)
                    except Exception:
                        pass
                    pubsub = client.pubsub()
                    await pubsub.subscribe(PRICE_CHANNEL)
                    consecutive_failures = 0
                    logger.info("Price subscriber reconnected successfully")
                continue
            except Exception as e:
                logger.warning("Failed to send price to WebSocket client: %s", e)
                break

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("Price subscriber error: %s", e)
    finally:
        if pubsub:
            try:
                await asyncio.wait_for(pubsub.unsubscribe(PRICE_CHANNEL), timeout=2.0)
            except Exception:
                pass
            try:
                await asyncio.wait_for(pubsub.close(), timeout=2.0)
            except Exception:
                pass


def _sync_get_latest_trade():
    """Sync helper to get latest trade from database"""
    db = SessionLocal()
    try:
        trade = db.query(Trade).order_by(Trade.id.desc()).first()
        if trade:
            is_closed = trade.close_price is not None
            has_open_same_position = False
            remaining_amount = None
            if is_closed and trade.position_id:
                has_open_same_position = db.query(Trade.id).filter(
                    Trade.position_id == trade.position_id,
                    Trade.close_price.is_(None)
                ).first() is not None
                if has_open_same_position:
                    open_trade = db.query(Trade).filter(
                        Trade.position_id == trade.position_id,
                        Trade.close_price.is_(None)
                    ).order_by(Trade.timestamp.desc()).first()
                    if open_trade:
                        remaining_amount = open_trade.amount
            is_partial_close = bool(is_closed and has_open_same_position)
            action_label = "PARTIAL CLOSE" if is_partial_close else ("CLOSE" if is_closed else trade.side)

            return {
                "id": trade.id,
                "symbol": trade.symbol,
                "side": trade.side,
                "position_side": trade.position_side,
                "amount": trade.amount,
                "entry_price": trade.price,
                "close_price": trade.close_price,
                "pnl": trade.pnl or 0.0,
                "leverage": trade.leverage or 1.0,
                "fees": trade.fees or 0.0,
                "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
                "close_time": trade.close_time.isoformat() if trade.close_time else None,
                "entry_reasoning": trade.entry_reasoning,
                "exit_reasoning": trade.exit_reasoning,
                "entry_prompt": trade.entry_prompt,
                "exit_prompt": trade.exit_prompt,
                "is_partial_close": is_partial_close,
                "action_label": action_label,
                "remaining_amount": remaining_amount,
            }
        return None
    finally:
        db.close()


async def trade_broadcast_task():
    """
    Background task to broadcast new trades in real-time.

    Uses async Redis to track last broadcast trade ID.
    """
    try:
        redis_client = await _get_shared_async_redis()
        last_broadcast_id = 0

        while True:
            try:
                # Run sync DB query in thread pool to avoid blocking
                trade_data = await asyncio.to_thread(_sync_get_latest_trade)

                if trade_data and trade_data["id"] > last_broadcast_id:
                    # Use async Redis
                    current_id_raw = await redis_client.get(REDIS_LAST_TRADE_KEY)
                    current_id = int(current_id_raw) if current_id_raw else 0

                    if trade_data["id"] > current_id:
                        # Set new trade ID in Redis
                        await redis_client.set(REDIS_LAST_TRADE_KEY, trade_data["id"], ex=86400)
                        last_broadcast_id = trade_data["id"]

                        # Broadcast new trade to all clients
                        await manager.broadcast_to_all({
                            "type": "new_trade",
                            "trade": trade_data,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                        logger.info("Broadcast new trade: %s %s %s", trade_data["symbol"], trade_data["side"], trade_data["id"])

            except Exception as e:
                logger.warning("Trade broadcast error: %s", e)

            await asyncio.sleep(3)  # Check every 3 seconds
    except asyncio.CancelledError:
        pass


# Global trade broadcast task (only one instance)
_trade_task: Optional[asyncio.Task] = None


@router.websocket("/portfolio")
async def websocket_portfolio(websocket: WebSocket):
    """
    WebSocket endpoint for real-time portfolio updates.

    Client messages:
    - {"action": "subscribe", "symbols": ["BTCUSDT", "ETHUSDT"]}
    - {"action": "unsubscribe", "symbols": ["BTCUSDT"]}
    - {"action": "ping"}

    Server messages:
    - {"type": "price_update", "symbol": "BTCUSDT", "price": 96500.0, "timestamp": "..."}
    - {"type": "position_update", "symbol": "BTCUSDT", "unrealized_pnl": 75.0, ...}
    - {"type": "new_trade", "trade": {"symbol": "BTCUSDT", "side": "BUY", "pnl": 50.0, ...}, "timestamp": "..."}
    - {"type": "new_signal", "signal": {...}, "timestamp": "..."}
    """
    global _trade_task

    # Accept connection with default symbols
    await manager.connect(websocket, symbols=SYMBOLS)
    logger.info(f"WebSocket connection established from {websocket.client.host}:{websocket.client.port}")
    logger.info(f"Active connections: {manager.get_connection_count()}")

    # Track last activity for timeout (memory optimization - cleanup zombie connections)
    last_activity = time.time()

    # Start global price publisher task if not running (publishes to Redis)
    _ensure_price_publisher()

    try:
        # Send initial connection confirmation FIRST (before any background tasks)
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to Trading Dashboard WebSocket",
            "subscribed_symbols": SYMBOLS,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Brief delay to let client process "connected" message
        await asyncio.sleep(0.05)

        # Start per-connection tasks
        signal_task = asyncio.create_task(signal_subscriber_task(websocket))
        price_task = asyncio.create_task(price_subscriber_task(websocket))
        heartbeat_task = asyncio.create_task(server_heartbeat_task(websocket))

        # Start global trade broadcast task if not running
        if _trade_task is None or _trade_task.done():
            _trade_task = asyncio.create_task(trade_broadcast_task())
            logger.info("Started trade broadcast task")

        # Listen for client messages with timeout check
        while True:
            try:
                # Check if signal task ended unexpectedly
                if signal_task.done():
                    exc = signal_task.exception() if not signal_task.cancelled() else None
                    logger.warning(f"Signal task ended unexpectedly: {exc}")
                    break

                # Check if heartbeat task ended (likely disconnect)
                if heartbeat_task.done():
                    exc = heartbeat_task.exception() if not heartbeat_task.cancelled() else None
                    logger.info(
                        "Heartbeat task ended; closing websocket | err=%s | %s",
                        exc,
                        _format_ws_state(websocket),
                    )
                    break

                # Check for timeout (zombie connection cleanup)
                if time.time() - last_activity > CONNECTION_TIMEOUT:
                    logger.info(
                        "WebSocket timeout after %ss of inactivity | %s | client=%s:%s",
                        CONNECTION_TIMEOUT,
                        _format_ws_state(websocket),
                        websocket.client.host,
                        websocket.client.port,
                    )
                    await websocket.close(code=1000, reason="Timeout - no activity")
                    break

                # Wait for message with timeout
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0  # Check every 30 seconds
                )
                last_activity = time.time()  # Reset activity timer

                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    symbols = message.get("symbols", [])
                    await manager.subscribe(websocket, symbols)
                    await websocket.send_json({
                        "type": "subscribed",
                        "symbols": symbols,
                        "timestamp": datetime.utcnow().isoformat(),
                    })

                elif action == "unsubscribe":
                    symbols = message.get("symbols", [])
                    await manager.unsubscribe(websocket, symbols)
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "symbols": symbols,
                        "timestamp": datetime.utcnow().isoformat(),
                    })

                elif action == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.utcnow().isoformat(),
                    })

            except asyncio.TimeoutError:
                # No message received, continue loop to check timeout
                continue
            except json.JSONDecodeError:
                last_activity = time.time()  # Still activity even if invalid
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON",
                })

    except WebSocketDisconnect as e:
        logger.info(
            "WebSocketDisconnect received | client=%s:%s | code=%s | state=%s",
            websocket.client.host,
            websocket.client.port,
            getattr(e, "code", None),
            _format_ws_state(websocket),
        )
    except Exception as e:
        logger.warning(
            "WebSocket error | client=%s:%s | state=%s | err=%s",
            websocket.client.host,
            websocket.client.port,
            _format_ws_state(websocket),
            e,
        )
    finally:
        # Cancel background tasks
        signal_task.cancel()
        price_task.cancel()
        heartbeat_task.cancel()

        # Wait for tasks to complete with timeout to prevent connection leaks
        try:
            await asyncio.wait_for(
                asyncio.gather(signal_task, price_task, heartbeat_task, return_exceptions=True),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logger.warning("Task cleanup timeout - forcing disconnect")

        # Disconnect from manager
        await manager.disconnect(websocket)

        # Explicitly close WebSocket to prevent CLOSE_WAIT state
        try:
            await websocket.close()
        except Exception:
            pass

        logger.info(
            "WebSocket cleanup complete | active_connections=%s | %s | client=%s:%s",
            manager.get_connection_count(),
            _format_ws_state(websocket),
            websocket.client.host,
            websocket.client.port,
        )


@router.get("/status")
async def websocket_status():
    """Get WebSocket connection statistics"""
    return {
        "total_connections": manager.get_connection_count(),
        "subscriptions_by_symbol": manager.get_symbol_subscription_counts(),
        "timestamp": datetime.utcnow().isoformat(),
    }
