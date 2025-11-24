import sys
import asyncio
import time
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.agents.base import AgentSignal

# Provide a lightweight httpx stub before importing modules that depend on it


class _DummyHTTPXClient:
    def __init__(self, *args, **kwargs) -> None:
        pass


class _DummyHTTPXResponse:
    def __init__(self, payload=None) -> None:
        self._payload = payload or []

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _DummyAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def get(self, *args, **kwargs):
        # Return minimal payload matching futures client expectations
        default_payload = [
            {"longShortRatio": "0", "sumOpenInterestValue": "0", "fundingRate": "0"}
        ]
        return _DummyHTTPXResponse(default_payload)


class _DummyTimeout:
    def __init__(self, *args, **kwargs) -> None:
        pass


class _DummyRequestError(Exception):
    pass


httpx_stub = types.SimpleNamespace(
    Client=_DummyHTTPXClient,
    AsyncClient=_DummyAsyncClient,
    Timeout=_DummyTimeout,
    ConnectTimeout=_DummyRequestError,
    ReadTimeout=_DummyRequestError,
    RequestError=_DummyRequestError,
    HTTPError=_DummyRequestError,
    get=lambda *args, **kwargs: None,
    post=lambda *args, **kwargs: _DummyHTTPXResponse({}),
)
sys.modules.setdefault("httpx", httpx_stub)

class _DummyInfluxDBClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def write_api(self, *args, **kwargs):
        return self

    def query_api(self):
        return self

    def write(self, *args, **kwargs):
        return None

    def query(self, *args, **kwargs):
        return []


class _DummyPoint:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def tag(self, *args, **kwargs):
        return self

    def field(self, *args, **kwargs):
        return self

    def time(self, *args, **kwargs):
        return self


class _DummyWritePrecision:
    NS = "ns"


sys.modules.setdefault(
    "influxdb_client",
    types.SimpleNamespace(InfluxDBClient=_DummyInfluxDBClient, Point=_DummyPoint, WritePrecision=_DummyWritePrecision),
)
sys.modules.setdefault("influxdb_client.client.write_api", types.SimpleNamespace(SYNCHRONOUS=None))
sys.modules.setdefault("influxdb_client.rest", types.SimpleNamespace(ApiException=Exception))
sys.modules.setdefault("joblib", types.SimpleNamespace(load=lambda *args, **kwargs: None, dump=lambda *args, **kwargs: None))
sys.modules.setdefault("redis", types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: types.SimpleNamespace(ping=lambda: None, publish=lambda *a, **k: None))))
sys.modules.setdefault("app.data_feeds.service", types.SimpleNamespace(orchestrator=lambda *args, **kwargs: None))
sys.modules.setdefault("app.features.orchestrator", types.SimpleNamespace(start_feature_workers=lambda *args, **kwargs: None))
sys.modules.setdefault(
    "app.utils.price_cache",
    types.SimpleNamespace(
        ensure_price_cache_listener=lambda *args, **kwargs: None,
        price_cache=types.SimpleNamespace(get=lambda *a, **k: None),
    ),
)


class DummyAdvancedExitMonitor:
    """No-op monitor to avoid network/DB during tests."""

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.symbol = symbol
        self.active_positions = {}

    def start_monitoring(self) -> None:  # pragma: no cover - noop
        return

    def add_position(self, *args, **kwargs) -> None:  # pragma: no cover - noop
        return

    def remove_position(self, *args, **kwargs) -> None:  # pragma: no cover - noop
        return

    def _get_current_price(self) -> float:
        return 0.0


class _DummyFeedbackCollector:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def start(self):  # pragma: no cover - noop
        return None

    def stop(self) -> None:  # pragma: no cover - noop
        return None


class _DummyRetrainer:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def retrain(self, *args, **kwargs):
        return {"success": False, "improvement": 0.0}


collector_mod = types.ModuleType("app.agents.feedback.collector")
collector_mod.FeedbackCollector = _DummyFeedbackCollector
sys.modules.setdefault("app.agents.feedback.collector", collector_mod)

retrainer_mod = types.ModuleType("app.agents.feedback.retrainer")
retrainer_mod.ActiveLearningRetrainer = _DummyRetrainer
sys.modules.setdefault("app.agents.feedback.retrainer", retrainer_mod)

redis_mod = types.ModuleType("app.utils.redis")
redis_mod.publish = lambda *args, **kwargs: None
redis_mod.publish_safe = lambda *args, **kwargs: None
redis_mod.get_redis_client = lambda *args, **kwargs: types.SimpleNamespace(ping=lambda: None, publish=lambda *a, **k: None)
sys.modules.setdefault("app.utils.redis", redis_mod)

adv_monitor_mod = types.ModuleType("app.monitoring.advanced_monitor")
adv_monitor_mod.AdvancedExitMonitor = DummyAdvancedExitMonitor
sys.modules.setdefault("app.monitoring.advanced_monitor", adv_monitor_mod)

from app.risk_manager.manager import RiskDecision  # noqa: E402
from app.orchestrator import runtime  # noqa: E402  (import after stubbing httpx)


class DummyAgent:
    def generate_signal(self) -> AgentSignal:
        return AgentSignal(
            timestamp=datetime.now(timezone.utc),
            direction="BUY",
            confidence=1.0,
            reasoning="dummy-signal",
            metadata={"raw_market_data": {"current_snapshots": {"30m": {"_time": datetime.now(timezone.utc)}}}},
        )


class DummyRiskManager:
    def evaluate(self, signals, portfolio_metrics=None):  # type: ignore[no-untyped-def]
        return RiskDecision(action="HOLD", amount=0.0, reasoning="dummy-decision")


class DummyExecutor:
    def __init__(self, symbol: str, call_log: list[dict], delay: float = 0.3) -> None:
        self.symbol = symbol
        self._call_log = call_log
        self._delay = delay

    def execute(self, decision: RiskDecision):
        start = time.perf_counter()
        time.sleep(self._delay)
        end = time.perf_counter()
        self._call_log.append({"symbol": self.symbol, "start": start, "end": end})
        return SimpleNamespace(status="EXECUTED", details="", telemetry={"last_action": "HOLD"})

    def portfolio_metrics(self) -> dict:
        return {
            "price": 100.0,
            "position": 0.0,
            "average_price": 0.0,
            "exposure": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "equity": 10000.0,
        }

    def get_recent_trades_with_pnl(self, limit: int = 5):  # pragma: no cover - unused in test
        return []


class DummyTelegramClient:
    def enabled(self) -> bool:
        return False

    def send_message(self, text: str) -> None:  # pragma: no cover - noop
        return


def test_parallel_execution_runs_concurrently() -> None:
    """
    Validate that execution tasks respect the parallel guard and run concurrently.
    """
    call_log: list[dict] = []
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    # Lightweight runner that reuses the guard but stubs execution
    runner = object.__new__(runtime.AutomatedRunner)
    runner._symbols = symbols
    runner._execution_concurrency = 3
    runner._execution_semaphore = asyncio.Semaphore(runner._execution_concurrency)

    async def stub_execute(self, symbol: str, _: dict) -> None:
        start = time.perf_counter()
        await asyncio.sleep(0.3)
        end = time.perf_counter()
        call_log.append({"symbol": symbol, "start": start, "end": end})

    runner._execute_symbol = stub_execute.__get__(runner, runtime.AutomatedRunner)

    async def run_all():
        await asyncio.gather(*(runner._execute_with_guard(sym, {}) for sym in symbols))

    start = time.perf_counter()
    asyncio.run(asyncio.wait_for(run_all(), timeout=5))
    total_duration = time.perf_counter() - start

    assert len(call_log) == len(symbols)

    longest_single = max(entry["end"] - entry["start"] for entry in call_log)
    # Parallel execution should finish notably faster than strict serialization
    assert total_duration < longest_single * len(runner._symbols) * 0.8

    # Ensure at least two execution windows overlap
    overlap = any(
        (a["start"] < b["end"] and b["start"] < a["end"])
        for i, a in enumerate(call_log)
        for b in call_log[i + 1 :]
    )
    assert overlap
