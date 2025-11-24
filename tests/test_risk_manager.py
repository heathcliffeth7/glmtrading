import json

from app.agents.base import AgentSignal
from app.risk_manager.manager import RiskDecision, RiskManager
from app.utils.rate_limiter import SlidingWindowRateLimiter


def test_parse_json_response(monkeypatch):
    manager = RiskManager()

    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"karar": "BUY", "miktar": 0.7, "gerekçe": "Positive signal"}
                    )
                }
            }
        ]
    }

    signals = [
        AgentSignal(direction="BUY", confidence=0.8, reasoning="", timestamp=""),
        AgentSignal(direction="SELL", confidence=0.2, reasoning="", timestamp=""),
    ]

    monkeypatch.setattr(manager._glm, "request", lambda _: response)
    decision = manager.evaluate(signals)
    assert decision.action == "BUY"
    # Confidence guardrail should clamp GLM amount (0.7) down to 0.3
    assert decision.amount == 0.3


def test_fallback_logic():
    manager = RiskManager()
    signals = [
        AgentSignal(direction="BUY", confidence=0.9, reasoning="", timestamp=""),
        AgentSignal(direction="SELL", confidence=0.2, reasoning="", timestamp=""),
    ]

    decision = manager._fallback_decision(signals, "GLM yok")
    assert decision.action == "BUY"
    assert decision.amount > 0.0


def test_rate_limiter():
    limiter = SlidingWindowRateLimiter(window_seconds=10, max_requests=2)
    limiter.hit()
    limiter.hit()
    assert not limiter.hit()
