"""Signals API Routes - GLM Trading Decisions"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Query

from app.utils.influx import query_historical_snapshots
from app.utils.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@router.get("/recent")
async def get_recent_signals(
    symbol: Optional[str] = Query(None, description="Filter by symbol (e.g., BTCUSDT)"),
    limit: int = Query(10, ge=1, le=50, description="Number of signals to return")
) -> Dict[str, Any]:
    """
    Get recent GLM trading signals with reasoning.

    Returns the latest trading decisions made by the GLM model,
    including the reasoning/rationale for each decision.
    """
    signals: List[Dict[str, Any]] = []

    # Determine which symbols to query
    symbols_to_query = [symbol.upper()] if symbol else SYMBOLS

    for sym in symbols_to_query:
        try:
            # Query InfluxDB for recent signals
            result = query_historical_snapshots(
                measurement="trading_signals",
                symbol=sym,
                interval="30min",
                limit=limit
            )

            if result:
                # Add symbol to each record (in case it's not in the data)
                for record in result:
                    record["symbol"] = sym
                signals.extend(result)

        except Exception as e:
            logger.warning("Failed to fetch signals for %s: %s", sym, e)
            continue

    # Sort by timestamp descending (most recent first)
    signals.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    # Limit total results
    signals = signals[:limit]

    return {
        "signals": signals,
        "count": len(signals),
        "timestamp": datetime.utcnow().isoformat()
    }
