from dataclasses import dataclass, field
from typing import Any, Dict, Protocol


@dataclass
class AgentSignal:
    direction: str
    confidence: float
    reasoning: str
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    def generate_signal(self) -> AgentSignal: ...
