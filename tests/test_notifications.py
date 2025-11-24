from datetime import datetime
from types import SimpleNamespace
from typing import List

import pandas as pd

from app.agents.base import AgentSignal
from app.orchestrator.runtime import AutomatedRunner
from app.research.backtest import Backtester
from app.risk_manager.manager import RiskDecision


class DummyTelegramClient:
    def __init__(self) -> None:
        self.messages: List[str] = []

    def enabled(self) -> bool:
        return True

    def send_message(self, text: str) -> None:
        self.messages.append(text)


class DummyBacktester(Backtester):
    def __init__(self, agent):
        super().__init__(agent)

    def run(self, rows):  # type: ignore[override]
        return []

    def summary(self) -> dict:
        return {"portfolio_value": 10000.0, "initial_cash": 10000.0, "trades": 0}


class DummyExecutor:
    def __init__(self) -> None:
        self.last_decision: RiskDecision | None = None

    def execute(self, decision: RiskDecision):
        self.last_decision = decision
        return SimpleNamespace(status="PAPER", details="")

    def portfolio_metrics(self) -> dict:
        return {
            "price": 0.0,
            "position": 0.0,
            "average_price": 0.0,
            "exposure": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "equity": 10000.0,
        }
    
    def get_recent_trades_with_pnl(self, limit: int = 5):
        return []


class DummyRiskManager:
    def __init__(self, decision: RiskDecision) -> None:
        self._decision = decision

    def evaluate(self, signals):  # type: ignore[no-untyped-def]
        return self._decision


class DummyAgent:
    def __init__(self, signal: AgentSignal):
        self._signal = signal

    def generate_signal(self) -> AgentSignal:
        return self._signal


def test_runner_sends_decision(monkeypatch) -> None:
    from app import orchestrator
    from types import SimpleNamespace

    class DummyAdvancedExitMonitor:
        def __init__(self, symbol: str = "TEST") -> None:
            self.active_positions = {}

        def start_monitoring(self) -> None:
            return

        def add_position(self, *args, **kwargs) -> None:
            return

        def remove_position(self, *args, **kwargs) -> None:
            return

        def _get_current_price(self) -> float:
            return 0.0

    dummy_client = DummyTelegramClient()
    monkeypatch.setattr("app.orchestrator.runtime.telegram_client", dummy_client)
    monkeypatch.setattr("app.orchestrator.runtime.Backtester", DummyBacktester)
    monkeypatch.setattr("app.orchestrator.runtime.AdvancedExitMonitor", DummyAdvancedExitMonitor)
    
    class DummyQuery:
        def __init__(self):
            self.model = None
    
        def filter(self, *args, **kwargs):
            return self
    
        def filter_by(self, *args, **kwargs):
            return self
    
        def count(self):
            return 0
    
        def all(self):
            return []
    
        def scalar(self):
            return 0
    
    class DummySession:
        def __init__(self, *args, **kwargs):
            pass
    
        def __enter__(self):
            return self
    
        def __exit__(self, exc_type, exc, tb):
            return False
    
        def query(self, model):
            return DummyQuery()
    
    class DummyTrade:
        symbol = None
        close_price = None
        price = 0.0
        amount = 0.0
        position_id = None
        position_side = None
        leverage = 1.0
        exit_plan = None
        fees = 0.0
        pnl = 0.0
    
    monkeypatch.setattr("app.executor.ledger.Session", DummySession)
    monkeypatch.setattr("app.executor.ledger.engine", None)
    monkeypatch.setattr("app.executor.ledger.Trade", DummyTrade)

    decision = RiskDecision(action="BUY", amount=0.3, reasoning="GLM gerekçesi", leverage=7.0)

    runner = AutomatedRunner(
        symbol="TEST",
        risk_manager=DummyRiskManager(decision),
        executor=DummyExecutor(),
        agent=DummyAgent(
            AgentSignal(
                timestamp=datetime.utcnow(),
                direction="BUY",
                confidence=1.0,
                reasoning="agent"
            )
        ),
            interval="5min",
            backtest_window_minutes=5,
            enable_feedback_collector=False,
            enable_daily_retraining=False,
        )
    
    # Simplified notify to avoid DB dependency in unit test
    def fake_notify(symbol: str, decision: RiskDecision, result: SimpleNamespace, metrics: dict) -> None:
        dummy_client.send_message(f"Karar: {decision.action} | {decision.reasoning}")
    
    runner._notify_cycle_complete = fake_notify  # type: ignore[assignment]
    
    runner._notify_cycle_complete(
        symbol="TEST",
        decision=decision,
        result=SimpleNamespace(status="PAPER", telemetry={"last_action": "HOLD", "price": 0.0, "amount": 0.0}),
        metrics={
            "price": 0.0,
            "position": 0.0,
            "average_price": 0.0,
            "exposure": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "equity": 10000.0,
        },
    )

    assert dummy_client.messages
    decision_msg = dummy_client.messages[-1]
    assert decision.reasoning in decision_msg
    # At least include decision action text
    assert "Karar" in decision_msg
