from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd

from app.agents.base import Agent, AgentSignal
from app.utils.influx import query_latest


class LongTermAgent(Agent):
    def __init__(self, model_path: str | None = None) -> None:
        path = Path(model_path or "models/long_term.joblib")
        self._model = joblib.load(path) if path.exists() else None

    def generate_signal(self) -> AgentSignal:
        if not self._model:
            return AgentSignal(
                direction="HOLD",
                confidence=0.0,
                reasoning="Model yüklenemedi",
                timestamp=datetime.utcnow().isoformat(),
            )

        latest = query_latest("features_4h", symbol="BTCUSDT", interval="4h") or {}
        features = pd.DataFrame(
            [
                {
                    "ema_50": latest.get("ema_50", 0),
                    "ema_200": latest.get("ema_200", 0),
                    "atr_14": latest.get("atr_14", 0),
                }
            ]
        )
        proba = self._model.predict_proba(features)[0]
        direction = "BUY" if proba[1] > 0.6 else "SELL" if proba[0] > 0.6 else "HOLD"
        confidence = float(max(proba))
        return AgentSignal(
            direction=direction,
            confidence=confidence,
            reasoning="Uzun vadeli model tahmini",
            timestamp=datetime.utcnow().isoformat(),
        )
