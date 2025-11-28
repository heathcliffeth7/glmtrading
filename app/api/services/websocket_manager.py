"""WebSocket Connection Manager for Real-time Updates"""
import asyncio
import json
from datetime import datetime
from typing import Dict, Set, List, Any

from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections per symbol subscription"""

    def __init__(self):
        # symbol -> set of websocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # websocket -> set of subscribed symbols
        self.connection_subscriptions: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, symbols: List[str] = None):
        """Accept connection and optionally subscribe to symbols"""
        await websocket.accept()

        async with self._lock:
            self.connection_subscriptions[websocket] = set()

            if symbols:
                for symbol in symbols:
                    await self._subscribe_to_symbol(websocket, symbol)

    async def disconnect(self, websocket: WebSocket):
        """Remove connection from all symbol subscriptions"""
        async with self._lock:
            # Get all symbols this connection was subscribed to
            subscribed_symbols = self.connection_subscriptions.pop(websocket, set())

            # Remove from each symbol's connection set
            for symbol in subscribed_symbols:
                if symbol in self.active_connections:
                    self.active_connections[symbol].discard(websocket)
                    if not self.active_connections[symbol]:
                        del self.active_connections[symbol]

    async def subscribe(self, websocket: WebSocket, symbols: List[str]):
        """Subscribe a connection to additional symbols"""
        async with self._lock:
            for symbol in symbols:
                await self._subscribe_to_symbol(websocket, symbol)

    async def unsubscribe(self, websocket: WebSocket, symbols: List[str]):
        """Unsubscribe a connection from symbols"""
        async with self._lock:
            for symbol in symbols:
                if symbol in self.active_connections:
                    self.active_connections[symbol].discard(websocket)
                if websocket in self.connection_subscriptions:
                    self.connection_subscriptions[websocket].discard(symbol)

    async def _subscribe_to_symbol(self, websocket: WebSocket, symbol: str):
        """Internal: subscribe to a symbol (must hold lock)"""
        symbol = symbol.upper()
        if symbol not in self.active_connections:
            self.active_connections[symbol] = set()
        self.active_connections[symbol].add(websocket)
        self.connection_subscriptions[websocket].add(symbol)

    async def broadcast_to_symbol(self, symbol: str, message: dict):
        """Broadcast message to all connections subscribed to symbol"""
        if symbol not in self.active_connections:
            return

        disconnected = set()
        message_json = json.dumps(message)

        for connection in self.active_connections[symbol]:
            try:
                await connection.send_text(message_json)
            except Exception:
                disconnected.add(connection)

        # Cleanup disconnected
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    await self.disconnect(ws)

    async def broadcast_to_all(self, message: dict):
        """Broadcast to all connected clients"""
        all_connections = set()
        for connections in self.active_connections.values():
            all_connections.update(connections)

        message_json = json.dumps(message)
        disconnected = set()

        for connection in all_connections:
            try:
                await connection.send_text(message_json)
            except Exception:
                disconnected.add(connection)

        # Cleanup disconnected
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    await self.disconnect(ws)

    def get_connection_count(self) -> int:
        """Get total number of active connections"""
        return len(self.connection_subscriptions)

    def get_symbol_subscription_counts(self) -> Dict[str, int]:
        """Get connection count per symbol"""
        return {
            symbol: len(connections)
            for symbol, connections in self.active_connections.items()
        }


# Global connection manager instance
manager = ConnectionManager()
