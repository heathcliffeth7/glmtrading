from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, Summary, generate_latest

from app.utils.logging import configure_logging


app = FastAPI()
configure_logging()

registry = CollectorRegistry()
ws_reconnects = Counter("binance_ws_reconnects_total", "WS reconnect count", registry=registry)
fallback_count = Counter("risk_fallback_total", "Fallback decisions", registry=registry)
queue_lag = Gauge("redis_queue_lag_seconds", "Redis stream processing lag", registry=registry)
decision_latency = Summary("risk_decision_latency_seconds", "Risk decision latency", registry=registry)


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    return generate_latest(registry).decode("utf-8")
