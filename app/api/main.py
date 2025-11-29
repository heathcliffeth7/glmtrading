"""
Trading Dashboard FastAPI Application

Main entry point for the dashboard API server.
Integrates with existing trading system infrastructure.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI

from app.api.config import get_dashboard_settings
from app.api.middleware.cors import setup_cors
from app.api.routes import portfolio, trades, pnl, health, websocket, signals
from app.utils.price_cache import price_cache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_dashboard_settings()

# Symbols to track
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Background task for price refresh
_price_refresh_task: Optional[asyncio.Task] = None


async def _init_price_cache():
    """Fetch initial prices from Binance REST API and populate price cache"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        for symbol in SYMBOLS:
            try:
                resp = await client.get(
                    f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    price_val = float(data["price"])
                    price_cache.set(symbol, price_val, source="binance_rest_init")
                    logger.info(f"Price cache initialized: {symbol} = ${price_val:.2f}")
            except Exception as e:
                logger.warning(f"Failed to init price for {symbol}: {e}")


async def _price_refresh_loop():
    """Background task to refresh prices periodically"""
    while True:
        try:
            await asyncio.sleep(30)  # Refresh every 30 seconds
            await _init_price_cache()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Price refresh error: {e}")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown"""
    global _price_refresh_task

    logger.info(f"Dashboard API starting on port {settings.api_port}")

    # Verify database connectivity
    try:
        from app.executor.ledger import engine
        with engine.connect() as conn:
            conn.execute("SELECT 1")
        logger.info("Database connection verified")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")

    # Initialize price cache with current prices
    await _init_price_cache()
    logger.info("Price cache initialized for PnL calculations")

    # Start background price refresh task
    _price_refresh_task = asyncio.create_task(_price_refresh_loop())
    logger.info("Price refresh task started (30s interval)")

    # Note: Signal subscriber no longer needed here - each WebSocket connection
    # has its own per-connection Redis subscriber (see websocket.py)

    yield  # Application runs

    # Cleanup
    if _price_refresh_task:
        _price_refresh_task.cancel()
        try:
            await _price_refresh_task
        except asyncio.CancelledError:
            pass
    logger.info("Dashboard API shutting down")


app = FastAPI(
    title="Trading Dashboard API",
    description="Portfolio monitoring dashboard for AI trading system",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Setup CORS
setup_cors(app)

# Include routers
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(trades.router, prefix="/api/trades", tags=["Trades"])
app.include_router(pnl.router, prefix="/api/pnl", tags=["PnL"])
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(websocket.router, prefix="/ws", tags=["WebSocket"])
app.include_router(signals.router, prefix="/api/signals", tags=["Signals"])


@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "name": "Trading Dashboard API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/symbols")
async def get_symbols():
    """Get list of supported trading symbols"""
    return {
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
