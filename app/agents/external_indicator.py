from datetime import datetime

from app.agents.base import Agent, AgentSignal


class ExternalIndicatorAgent(Agent):
    def generate_signal(self) -> AgentSignal:
        return AgentSignal(
            direction="HOLD",
            confidence=0.0,
            reasoning="Harici gösterge verisi bekleniyor",
            timestamp=datetime.utcnow().isoformat(),
        )
