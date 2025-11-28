"""WebSocket API Routes for Real-time Updates"""
import asyncio
import json
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.services.websocket_manager import manager
from app.api.dependencies import SessionLocal
from app.executor.ledger import Trade
from app.utils.influx import query_historical_snapshots
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Track last broadcast signal timestamp to avoid duplicates
_last_signal_timestamp: Optional[str] = None
# Track last broadcast trade ID to avoid duplicates
_last_trade_id: Optional[int] = None


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


async def price_broadcast_task(websocket: WebSocket, symbols: list):
    """Background task to broadcast prices periodically"""
    try:
        while True:
            prices = get_current_prices()
            for symbol in symbols:
                if symbol in prices:
                    await manager.broadcast_to_symbol(symbol, {
                        "type": "price_update",
                        "symbol": symbol,
                        "price": prices[symbol],
                        "timestamp": datetime.utcnow().isoformat(),
                    })
            await asyncio.sleep(1)  # Update every second
    except asyncio.CancelledError:
        pass


async def signal_broadcast_task():
    """Background task to broadcast new GLM signals in real-time"""
    global _last_signal_timestamp

    try:
        while True:
            try:
                # Check for new signals across all symbols
                for symbol in SYMBOLS:
                    result = query_historical_snapshots(
                        measurement="trading_signals",
                        symbol=symbol,
                        interval="30min",
                        limit=1
                    )

                    if result and len(result) > 0:
                        latest_signal = result[0]
                        signal_ts = latest_signal.get("timestamp", "")

                        # Check if this is a new signal
                        if signal_ts and signal_ts != _last_signal_timestamp:
                            _last_signal_timestamp = signal_ts

                            # Broadcast new signal to all clients
                            await manager.broadcast_to_all({
                                "type": "new_signal",
                                "signal": {
                                    "symbol": symbol,
                                    "action": latest_signal.get("action", "HOLD"),
                                    "reasoning": latest_signal.get("reasoning", ""),
                                    "timestamp": signal_ts,
                                    "equity": latest_signal.get("equity", 0),
                                    "amount": latest_signal.get("amount", 0),
                                    "leverage": latest_signal.get("leverage", 0),
                                },
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                            logger.info("Broadcast new signal: %s %s", symbol, latest_signal.get("action"))
                            break  # Only broadcast one signal per cycle

            except Exception as e:
                logger.warning("Signal broadcast error: %s", e)

            await asyncio.sleep(5)  # Check every 5 seconds
    except asyncio.CancelledError:
        pass


async def trade_broadcast_task():
    """Background task to broadcast new trades in real-time"""
    global _last_trade_id

    try:
        while True:
            try:
                # Query latest trade from SQLite
                db = SessionLocal()
                try:
                    latest_trade = db.query(Trade).order_by(Trade.id.desc()).first()

                    if latest_trade and latest_trade.id != _last_trade_id:
                        _last_trade_id = latest_trade.id

                        # Broadcast new trade to all clients
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
                finally:
                    db.close()

            except Exception as e:
                logger.warning("Trade broadcast error: %s", e)

            await asyncio.sleep(3)  # Check every 3 seconds
    except asyncio.CancelledError:
        pass


# Global signal broadcast task (only one instance)
_signal_task: Optional[asyncio.Task] = None
# Global trade broadcast task (only one instance)
_trade_task: Optional[asyncio.Task] = None


@router.websocket("/portfolio")
async def websocket_portfolio(websocket: WebSocket):
    """
    WebSocket endpoint for real-time portfolio updates.

    Client messages:
    - {"action": "subscribe", "symbols": ["BTCUSDT", "ETHUSDT"]}
    - {"action": "unsubscribe", "symbols": ["BTCUSDT"]}

    Server messages:
    - {"type": "price_update", "symbol": "BTCUSDT", "price": 96500.0, "timestamp": "..."}
    - {"type": "position_update", "symbol": "BTCUSDT", "unrealized_pnl": 75.0, ...}
    - {"type": "new_trade", "trade": {"symbol": "BTCUSDT", "side": "BUY", "pnl": 50.0, ...}, "timestamp": "..."}
    - {"type": "new_signal", "signal": {...}, "timestamp": "..."}
    """
    global _signal_task, _trade_task

    # Accept connection with default symbols
    await manager.connect(websocket, symbols=SYMBOLS)

    # Start price broadcast task
    broadcast_task = asyncio.create_task(price_broadcast_task(websocket, SYMBOLS))

    # Start global signal broadcast task if not running
    if _signal_task is None or _signal_task.done():
        _signal_task = asyncio.create_task(signal_broadcast_task())
        logger.info("Started signal broadcast task")

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

        # Listen for client messages
        while True:
            try:
                data = await websocket.receive_text()
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

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON",
                })

    except WebSocketDisconnect:
        pass
    finally:
        broadcast_task.cancel()
        await manager.disconnect(websocket)


@router.get("/ws/status")
async def websocket_status():
    """Get WebSocket connection statistics"""
    return {
        "total_connections": manager.get_connection_count(),
        "subscriptions_by_symbol": manager.get_symbol_subscription_counts(),
        "timestamp": datetime.utcnow().isoformat(),
    }
