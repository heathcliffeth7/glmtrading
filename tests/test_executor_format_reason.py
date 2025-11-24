import json

from app.executor.executor import Executor
from app.risk_manager.manager import RiskDecision


class DummyTelegramClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def enabled(self) -> bool:
        return True

    def send_message(self, text: str) -> None:
        self.messages.append(text)


def test_format_reason_preserves_long_text() -> None:
    executor = Executor()
    long_reason = "Fallback BUY " + json.dumps({"gerekçe": "".join([str(i) for i in range(600)])})

    formatted = executor.format_reason(long_reason)

    assert "599" in formatted
    assert len(formatted) >= len("Fallback BUY")


def test_execute_position_status_and_guardrail(monkeypatch) -> None:
    dummy_client = DummyTelegramClient()
    monkeypatch.setattr("app.executor.executor.telegram_client", dummy_client)

    executor = Executor(symbol="TEST", max_position=1.0)

    result_hold = executor.execute(RiskDecision(action="HOLD", amount=0.0, reasoning="", leverage=5.0))
    assert result_hold.status == "SKIP"
    assert not dummy_client.messages

    result1 = executor.execute(RiskDecision(action="BUY", amount=0.6, reasoning="ilk", leverage=5.0))
    assert result1.status == "PAPER"
    assert dummy_client.messages
    trade_msg = dummy_client.messages[-1]
    assert "Pozisyon Durumu" in trade_msg
    assert "Yeni pozisyon" in trade_msg or "Pozisyon artırıldı" in trade_msg

    result2 = executor.execute(RiskDecision(action="BUY", amount=0.6, reasoning="ikinci", leverage=5.0))
    assert result2.status == "BLOCKED"
    guardrail_msg = dummy_client.messages[-1]
    assert guardrail_msg.startswith("*İşlem Engellendi*")
    assert "Limit" in guardrail_msg
