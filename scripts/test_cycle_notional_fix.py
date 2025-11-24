#!/usr/bin/env python3
"""
Smoke test for cycle notification Notional calculation fix.

This script avoids real DB connections by monkeypatching runtime.Session and
telegram client, and feeding dummy recent trades (open/closed) via a dummy executor.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

# Ensure project root on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import types  # noqa: E402

# --- Stub heavy dependencies before importing runtime ---

# app.config.settings stub
settings_mod = types.ModuleType("app.config.settings")
def _get_settings():  # noqa: D401
    class S:
        log_level = "INFO"
        telegram_bot_token = ""
        telegram_channel_id = ""
    return S()
settings_mod.get_settings = _get_settings  # type: ignore[attr-defined]
sys.modules["app.config.settings"] = settings_mod

# app.utils.logging stub
logging_mod = types.ModuleType("app.utils.logging")
class _Logger:
    def info(self, *_, **__):
        pass
    def warning(self, *_, **__):
        pass
    def error(self, *_, **__):
        pass
    def debug(self, *_, **__):
        pass
    def critical(self, *_, **__):
        pass
def _get_logger(_name=None):  # noqa: ANN001
    return _Logger()
def _configure_logging(_level):  # noqa: ANN001
    return None
logging_mod.get_logger = _get_logger
logging_mod.configure_logging = _configure_logging
sys.modules["app.utils.logging"] = logging_mod

# app.utils.telegram stub (avoid httpx)
tg_mod = types.ModuleType("app.utils.telegram")
class _DummyTG:
    def __init__(self) -> None:
        self._msgs: list[str] = []
    def enabled(self) -> bool:
        return True
    def send_message(self, text: str) -> None:
        self._msgs.append(text)
def _fmt_md(text: str) -> str:
    return text
tg_mod.telegram_client = _DummyTG()
tg_mod.format_markdown = _fmt_md
sys.modules["app.utils.telegram"] = tg_mod

# Other stubs
sys.modules["httpx"] = types.ModuleType("httpx")
base_mod = types.ModuleType("app.agents.base")
@dataclass
class AgentSignal:
    timestamp: str | None = None
    direction: str = "HOLD"
    confidence: float = 0.0
    reasoning: str = ""
    metadata: dict = None  # type: ignore[assignment]
class Agent:  # noqa: D401
    pass
base_mod.Agent = Agent
base_mod.AgentSignal = AgentSignal
sys.modules["app.agents.base"] = base_mod

deriv_mod = types.ModuleType("app.agents.derivatives")
class DerivativesAgent:  # noqa: D401
    def __init__(self, *_, **__):
        pass
deriv_mod.DerivativesAgent = DerivativesAgent
sys.modules["app.agents.derivatives"] = deriv_mod

collector_mod = types.ModuleType("app.agents.feedback.collector")
class FeedbackCollector:  # noqa: D401
    def __init__(self, *_, **__):
        pass
    def start(self):
        pass
    def stop(self):
        pass
collector_mod.FeedbackCollector = FeedbackCollector
sys.modules["app.agents.feedback.collector"] = collector_mod

retrainer_mod = types.ModuleType("app.agents.feedback.retrainer")
class ActiveLearningRetrainer:  # noqa: D401
    def __init__(self, *_, **__):
        pass
    def retrain(self, *_, **__):
        return {"success": True, "improvement": 0.0}
retrainer_mod.ActiveLearningRetrainer = ActiveLearningRetrainer
sys.modules["app.agents.feedback.retrainer"] = retrainer_mod
sys.modules["app.data_feeds.service"] = types.ModuleType("app.data_feeds.service")
sys.modules["app.features.orchestrator"] = types.ModuleType("app.features.orchestrator")
sys.modules["app.research.backtest"] = types.ModuleType("app.research.backtest")
sys.modules["sqlalchemy"] = types.ModuleType("sqlalchemy")
sqlalchemy_orm_mod = types.ModuleType("sqlalchemy.orm")
class _SA_Session:
    def __init__(self, *_, **__):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *_, **__):
        return None
sqlalchemy_orm_mod.Session = _SA_Session
sys.modules["sqlalchemy.orm"] = sqlalchemy_orm_mod

# app.executor.executor stub
exec_mod = types.ModuleType("app.executor.executor")
@dataclass
class ExecutionResult:
    status: str
    details: str
class Executor:  # placeholder (we pass our dummy later)
    pass
exec_mod.ExecutionResult = ExecutionResult
exec_mod.Executor = Executor
sys.modules["app.executor.executor"] = exec_mod

# app.executor.ledger stub to satisfy Session/engine/Trade imports
ledger_mod = types.ModuleType("app.executor.ledger")
class Trade:  # minimal
    symbol = "BTCUSDT"
class Session:
    def __init__(self, *_, **__):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *_, **__):
        return None
    def query(self, *_):
        return self
    def filter(self, *_):
        return self
    def count(self) -> int:
        return 5
    def first(self):
        return None
ledger_mod.Session = Session
ledger_mod.engine = object()
ledger_mod.Trade = Trade
sys.modules["app.executor.ledger"] = ledger_mod

# Fill stubs with minimal callables to avoid attribute errors
sys.modules["app.data_feeds.service"].orchestrator = lambda: None  # type: ignore[attr-defined]
sys.modules["app.features.orchestrator"].start_feature_workers = lambda *a, **k: None  # type: ignore[attr-defined]
sys.modules["app.research.backtest"].Backtester = object  # type: ignore[attr-defined]
sys.modules["app.research.backtest"].load_historical_from_influx = lambda *a, **k: SimpleNamespace(empty=True)  # type: ignore[attr-defined]

# Now import runtime safely with stubs
from app.orchestrator import runtime  # noqa: E402


class DummyTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def enabled(self) -> bool:  # match interface
        return True

    def send_message(self, text: str) -> None:
        self.messages.append(text)


class DummySession:
    """Minimal dummy to satisfy runtime DB queries without touching Postgres."""

    def __init__(self, *_, **__):
        pass

    def __enter__(self) -> "DummySession":
        return self

    def __exit__(self, *_) -> None:  # noqa: ANN001
        return None

    # SQLAlchemy-like chainable stubs
    def query(self, *_):  # noqa: ANN001
        return self

    def filter(self, *_):  # noqa: ANN001
        return self

    def count(self) -> int:
        return 5

    def first(self):  # For CLOSE position_id lookup path
        return None


@dataclass
class DummyExecutor:
    def portfolio_metrics(self) -> dict:
        return {
            "price": 113_700.0,
            "position": 0.0,
            "average_price": 0.0,
            "exposure": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 2189.54,
            "equity": 12_189.54,
            "margin_used": 0.0,
            "free_cash": 12_189.54,
        }

    def get_recent_trades_with_pnl(self, limit: int = 5) -> list[dict]:
        # latest trade first (closed)
        closed = {
            "side": "BUY",
            "amount": 1.4228,
            "open_price": 113_698.00,
            "close_price": 113_713.60,
            "pnl": 711.18,
            "pnl_pct": 0.0,
            "fee": 10.0,
            "is_closed": True,
            "timestamp": None,
            "position_id": "POS-20251026-001",
        }
        # an open trade example
        open_t = {
            "side": "BUY",
            "amount": 0.1779,
            "open_price": 112_452.30,
            "close_price": 113_713.60,  # current price
            "pnl": 214.33,
            "pnl_pct": 1.12,
            "fee": 1.0,
            "is_closed": False,
            "timestamp": None,
            "position_id": "POS-20251026-001",
        }
        return [closed, open_t][:limit]


def run_demo(action: str) -> list[str]:
    # Monkeypatch telegram client and Session in runtime
    dummy_tg = DummyTelegram()
    runtime.telegram_client = dummy_tg  # type: ignore[assignment]

    runner = runtime.AutomatedRunner(
        symbol="BTCUSDT",
        interval="30min",
        risk_manager=SimpleNamespace(evaluate=lambda *a, **k: None),
        executor=DummyExecutor(),
        agent=None,
        enable_feedback_collector=False,
        enable_daily_retraining=False,
    )

    decision = SimpleNamespace(action=action, amount=1.0, leverage=10.0, reasoning="Notional düzeltme testi")
    result = runtime.ExecutionResult(status="PAPER", details="Test")
    metrics = runner._executor.portfolio_metrics()  # type: ignore[attr-defined]
    runner._notify_cycle_complete(decision, result, metrics)

    return dummy_tg.messages


def main() -> None:
    # 1) CLOSED trade case (CLOSE action)
    closed_msgs = run_demo("CLOSE")
    print("=== CLOSED TRADE MESSAGE ===")
    print(closed_msgs[-1])
    print()

    # 2) OPEN trade case (SELL action) – switch order to make open trade latest
    # Reuse same function but alter DummyExecutor ordering on the fly if desired.
    # For simplicity, we just show the CLOSED case which was the reported issue.


if __name__ == "__main__":
    main()
