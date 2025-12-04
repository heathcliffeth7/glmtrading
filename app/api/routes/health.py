"""Health Check API Routes"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.api.dependencies import get_db

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "trading-dashboard-api",
    }


@router.get("/health/databases")
async def database_health_check(db: Session = Depends(get_db)):
    """Check database connectivity"""
    checks = {}

    # PostgreSQL check
    try:
        db.execute(text("SELECT 1"))
        checks["postgresql"] = {"status": "connected"}
    except Exception as e:
        checks["postgresql"] = {"status": "error", "message": str(e)}

    # InfluxDB check
    try:
        from app.utils.influx import check_health
        influx_result = check_health()
        checks["influxdb"] = influx_result
    except Exception as e:
        checks["influxdb"] = {"status": "error", "message": str(e)}

    # Redis check (using connection pool from manager)
    try:
        from app.utils.redis_manager import get_redis_manager
        manager = get_redis_manager()
        r = manager.get_sync_client()
        r.ping()

        # Include pool metrics
        metrics = manager.get_health_metrics()
        checks["redis"] = {
            "status": "connected",
            "latency_ms": round(metrics.latency_ms, 2),
            "circuit_breaker": metrics.circuit_breaker_state,
            "connections": manager.get_connection_count(),
        }
    except Exception as e:
        checks["redis"] = {"status": "error", "message": str(e)}

    # Overall status
    all_healthy = all(
        c.get("status") in ["connected", "healthy"]
        for c in checks.values()
    )

    return {
        "status": "healthy" if all_healthy else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": checks,
    }


@router.get("/health/services")
async def service_status():
    """Get status of trading systemd services"""
    import subprocess

    services = [
        "trading-orchestrator",
        "trading-enriched-feed-1m",
        "trading-enriched-feed-15min",
        "trading-enriched-feed-4h",
    ]

    result = {}
    for svc in services:
        try:
            output = subprocess.run(
                ["systemctl", "is-active", f"{svc}.service"],
                capture_output=True,
                text=True,
                timeout=5
            )
            result[svc] = output.stdout.strip()
        except Exception as e:
            result[svc] = f"error: {e}"

    return {"services": result}
