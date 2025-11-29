"""WebSocket API Routes for Real-time Updates"""
import asyncio
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional

import redis
import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.services.websocket_manager import manager
from app.api.dependencies import SessionLocal
from app.config.settings import get_settings
from app.data_feeds.constants import SIGNAL_CHANNEL
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

# Sync Redis client for trade tracking
_redis_client: Optional[redis.Redis] = None

# Shared async Redis client for signal subscriptions (memory optimization)
_shared_async_redis: Optional[aioredis.Redis] = None


def _get_redis_client() -> redis.Redis:
    """Get or create sync Redis client for trade tracking"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(str(settings.redis.url))
    return _redis_client


async def _get_shared_async_redis() -> aioredis.Redis:
    """Get or create shared async Redis client (memory optimization)"""
    global _shared_async_redis
    if _shared_async_redis is None:
        _shared_async_redis = aioredis.from_url(
            str(settings.redis.url),
            decode_responses=True,
            max_connections=10  # Connection pool
        )
    return _shared_async_redis


def get_current_prices() -> Dict[str, float]:
    """Get current prices from Binance"""
    prices = {}
    try:
        import httpx
        response = httpx.get(
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


# Global price broadcast task (shared across all connections - memory optimization)
_price_task: Optional[asyncio.Task] = None


async def global_price_broadcast_task():
    """
    Global background task to broadcast prices to ALL connections.

    Memory optimization: Single task instead of one per connection.
    """
    logger.info("Global price broadcast task started")
    try:
        while True:
            # Only broadcast if there are active connections
            if manager.get_connection_count() > 0:
                prices = get_current_prices()
                for symbol in SYMBOLS:
                    if symbol in prices:
                        await manager.broadcast_to_symbol(symbol, {
                            "type": "price_update",
                            "symbol": symbol,
                            "price": prices[symbol],
                            "timestamp": datetime.utcnow().isoformat(),
                        })
            await asyncio.sleep(1)  # Update every second
    except asyncio.CancelledError:
        logger.info("Global price broadcast task cancelled")


def _ensure_price_task():
    """Ensure global price broadcast task is running"""
    global _price_task
    if _price_task is None or _price_task.done():
        _price_task = asyncio.create_task(global_price_broadcast_task())
        logger.info("Started global price broadcast task")


async def signal_subscriber_task(websocket: WebSocket):
    """
    Per-connection Redis subscriber for real-time AI signals.

    Uses shared Redis client pool for memory optimization.
    """
    pubsub = None

    try:
        # Use shared Redis client instead of creating new one per connection
        client = await _get_shared_async_redis()
        pubsub = client.pubsub()
        await pubsub.subscribe(SIGNAL_CHANNEL)

        logger.info("Signal subscriber started for WebSocket client (using shared pool)")

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            try:
                payload = json.loads(message["data"])
                payload["server_timestamp"] = datetime.utcnow().isoformat()
                await websocket.send_json(payload)
                logger.debug("Signal sent to WebSocket client: %s", payload.get("signal", {}).get("symbol", "?"))
            except Exception as e:
                logger.warning("Failed to send signal to WebSocket client: %s", e)
                break

    except asyncio.CancelledError:
        logger.debug("Signal subscriber task cancelled")
    except Exception as e:
        logger.warning("Signal subscriber error: %s", e)
    finally:
        # Only cleanup pubsub, not the shared client
        try:
            if pubsub:
                await pubsub.unsubscribe(SIGNAL_CHANNEL)
                await pubsub.close()
        except Exception:
            pass
        logger.debug("Signal subscriber cleanup complete")


async def trade_broadcast_task():
    """
    Background task to broadcast new trades in real-time.

    Uses Redis to track last broadcast trade ID across all workers,
    preventing duplicate broadcasts.
    """
    try:
        redis_client = _get_redis_client()

        while True:
            try:
                # Query latest trade from SQLite
                db = SessionLocal()
                try:
                    latest_trade = db.query(Trade).order_by(Trade.id.desc()).first()

                    if latest_trade:
                        # Get last broadcast trade ID from Redis (shared across workers)
                        last_id_raw = redis_client.get(REDIS_LAST_TRADE_KEY)
                        last_id = int(last_id_raw) if last_id_raw else 0

                        if latest_trade.id > last_id:
                            # Atomically set the new trade ID using SETNX pattern
                            # Use SET with XX=False to only set if value changes
                            pipe = redis_client.pipeline()
                            pipe.watch(REDIS_LAST_TRADE_KEY)

                            current_val = redis_client.get(REDIS_LAST_TRADE_KEY)
                            current_id = int(current_val) if current_val else 0

                            if latest_trade.id > current_id:
                                pipe.multi()
                                pipe.set(REDIS_LAST_TRADE_KEY, latest_trade.id, ex=86400)  # 24h TTL
                                pipe.execute()

                                # Broadcast new trade to all clients in THIS worker
                                await manager.broadcast_to_all({
                                    "type": "new_trade",
                                    "trade": {
                                        "id": latest_trade.id,
                                        "symbol": latest_trade.symbol,
                                        "side": latest_trade.side,
                                        "position_side": latest_trade.position_side,
                                        "amount": latest_trade.amount,
                                        "entry_price": latest_trade.price,
                                        "close_price": latest_trade.close_price,
                                        "pnl": latest_trade.pnl or 0.0,
                                        "leverage": latest_trade.leverage or 1.0,
                                        "timestamp": latest_trade.timestamp.isoformat() if latest_trade.timestamp else None,
                                        "close_time": latest_trade.close_time.isoformat() if latest_trade.close_time else None,
                                    },
                                    "timestamp": datetime.utcnow().isoformat(),
                                })
                                logger.info("Broadcast new trade: %s %s %s", latest_trade.symbol, latest_trade.side, latest_trade.id)
                            else:
                                pipe.unwatch()
                finally:
                    db.close()

            except redis.WatchError:
                # Another worker already broadcast this trade
                logger.debug("Trade already broadcast by another worker")
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

    # Start global price broadcast task if not running (shared across ALL connections)
    _ensure_price_task()

    # Start per-connection signal subscriber (uses shared Redis pool)
    signal_task = asyncio.create_task(signal_subscriber_task(websocket))

    # Start global trade broadcast task if not running
    if _trade_task is None or _trade_task.done():
        _trade_task = asyncio.create_task(trade_broadcast_task())
        logger.info("Started trade broadcast task")

    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to Trading Dashboard WebSocket",
            "subscribed_symbols": SYMBOLS,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Listen for client messages with timeout check
        while True:
            try:
                # Check for timeout (zombie connection cleanup)
                if time.time() - last_activity > CONNECTION_TIMEOUT:
                    logger.info(f"WebSocket timeout after {CONNECTION_TIMEOUT}s of inactivity")
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

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: {websocket.client.host}:{websocket.client.port}")
    finally:
        signal_task.cancel()
        await manager.disconnect(websocket)
        logger.info(f"WebSocket cleanup complete. Active connections: {manager.get_connection_count()}")


@router.get("/ws/status")
async def websocket_status():
    """Get WebSocket connection statistics"""
    return {
        "total_connections": manager.get_connection_count(),
        "subscriptions_by_symbol": manager.get_symbol_subscription_counts(),
        "timestamp": datetime.utcnow().isoformat(),
    }
