"""
Trading Dashboard FastAPI Application

Main entry point for the dashboard API server.
Integrates with existing trading system infrastructure.
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from app.api.config import get_dashboard_settings
from app.api.middleware.cors import setup_cors
from app.api.routes import portfolio, trades, pnl, health, websocket, signals

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_dashboard_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown"""
    logger.info(f"Dashboard API starting on port {settings.api_port}")

    # Verify database connectivity
    try:
        from app.executor.ledger import engine
        with engine.connect() as conn:
            conn.execute("SELECT 1")
        logger.info("Database connection verified")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")

    yield  # Application runs

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
