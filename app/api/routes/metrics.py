"""Connection Pool Metrics API Routes"""
from typing import Any, Dict

from fastapi import APIRouter

from app.executor.ledger import engine
from app.executor.portfolio_sync import get_cache_stats
from app.utils.redis_manager import get_redis_manager

router = APIRouter()


@router.get("/metrics/connections")
async def connection_metrics() -> Dict[str, Any]:
    """
    Return connection pool usage statistics for monitoring.
    
    This endpoint helps track:
    - PostgreSQL connection pool utilization
    - Redis connection pool health
    - Portfolio sync cache performance
    """
    redis_mgr = get_redis_manager()
    
    # PostgreSQL pool metrics
    pg_pool = engine.pool
    pg_metrics = {
        "pool_size": pg_pool.size(),
        "checked_out": pg_pool.checkedout(),
        "overflow": pg_pool.overflow(),
        "total_connections": pg_pool.size() + pg_pool.overflow(),
        "max_connections": 150,  # pool_size (50) + max_overflow (100)
        "utilization_pct": round(
            ((pg_pool.checkedout() + pg_pool.overflow()) / 150) * 100, 2
        ),
    }
    
    # Redis pool metrics
    redis_health = redis_mgr.get_health_metrics()
    redis_conns = redis_mgr.get_connection_count()
    
    redis_metrics = {
        "sync_pool": redis_conns.get("sync_pool", 0),
        "async_clients": redis_conns.get("async_clients", 0),
        "pubsub_connections": redis_conns.get("pubsub_connections", 0),
        "total_connections": redis_conns.get("total", 0),
        "max_connections_per_pool": 50,
        "latency_ms": round(redis_health.latency_ms, 2),
        "circuit_breaker_state": redis_health.circuit_breaker_state,
        "failed_connections": redis_health.failed_connections,
    }
    
    # Portfolio sync cache metrics
    cache_stats = get_cache_stats()
    
    return {
        "postgres": pg_metrics,
        "redis": redis_metrics,
        "portfolio_cache": cache_stats,
        "status": "healthy" if pg_metrics["utilization_pct"] < 70 else "warning",
    }


@router.get("/metrics/portfolio-cache")
async def portfolio_cache_metrics() -> Dict[str, Any]:
    """Get detailed portfolio sync cache statistics"""
    return get_cache_stats()
