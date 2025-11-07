"""Prediction logging for active learning"""

from typing import Optional

from sqlalchemy.orm import Session

from app.executor.ledger import engine, record_prediction
from app.utils.logging import get_logger

logger = get_logger(__name__)


def log_prediction(
    symbol: str,
    features: dict,
    model_score: float,
    predicted_direction: str,
    confidence: float,
    trade_id: Optional[int] = None,
) -> int:
    """
    Active Learning: Agent prediction'ını kaydet

    Args:
        symbol: Trading symbol (e.g., "BTCUSDT")
        features: Feature dictionary with keys:
            - long_short_ratio
            - open_interest
            - funding_rate
            - ema_20, ema_50, rsi_14
            - close
        model_score: Model probability score (0-1)
        predicted_direction: BUY/SELL/HOLD
        confidence: Confidence level (0-1)
        trade_id: Optional trade ID if trade was executed

    Returns:
        prediction_id: Created prediction log ID
    """
    try:
        with Session(engine) as session:
            prediction = record_prediction(
                session,
                symbol=symbol,
                features=features,
                model_score=model_score,
                predicted_direction=predicted_direction,
                confidence=confidence,
                trade_id=trade_id,
            )
            session.commit()

            logger.info(
                "Prediction logged: id=%d symbol=%s direction=%s score=%.4f confidence=%.4f",
                prediction.id,
                symbol,
                predicted_direction,
                model_score,
                confidence,
            )

            return prediction.id

    except Exception as exc:
        logger.error("Failed to log prediction: %s", exc, exc_info=True)
        return -1
