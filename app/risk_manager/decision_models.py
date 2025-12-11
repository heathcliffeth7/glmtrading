"""
Risk Manager Decision Models

This module contains the data structures used for risk decisions:
- DataAnalysis: GLM's mandatory analysis fields
- ThoughtProcess: Thesis/Antithesis/Synthesis reasoning
- RiskDecision: Final trading decision with all metadata
- Utility functions: clamp, json_serializer
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Any


__all__ = [
    "DataAnalysis",
    "ThoughtProcess",
    "RiskDecision",
    "clamp",
    "json_serializer",
]


@dataclass
class DataAnalysis:
    """GLM's mandatory data analysis fields (ADX, Volume, Funding)"""
    adx_interpretation: str = ""  # ADX value and trend strength interpretation
    volume_assessment: str = ""   # Volume Ratio assessment
    funding_view: str = ""        # Funding rate interpretation


@dataclass
class ThoughtProcess:
    """GLM's Thesis/Antithesis/Synthesis reasoning structure"""
    thesis_bullish: str = ""      # Bullish factors (legacy name, now signal-direction aware)
    antithesis_bearish: str = ""  # Bearish risks and counter-arguments (legacy name)
    synthesis_verdict: str = ""   # Final logical conclusion
    is_consistent: bool = True    # Is signal consistent with synthesis?
    consistency_reason: str = ""  # Inconsistency reason (if any)


@dataclass
class RiskDecision:
    """Final trading decision with all metadata"""
    action: str
    amount: float
    reasoning: str
    leverage: float = 5.0
    glm_confidence: float = 0.0  # GLM's own confidence score (0-100)
    reason_primary: str = ""     # Primary reason for decision
    reason_secondary: str = ""   # Secondary reason for decision
    glm_response_time_ms: float = 0.0  # GLM API response time in milliseconds
    exit_plan: Optional[dict] = None   # GLM's exit plan: {profit_target, stop_loss, invalidation_condition}
    close_side: Optional[str] = None   # "LONG" or "SHORT" (for CLOSE action)
    glm_response_json: Optional[dict] = None  # LLM response JSON for Telegram notification
    prompt_sent: Optional[str] = None  # LLM'e gönderilen prompt

    # Exit validation for CLOSE decisions
    exit_validation: Optional[str] = None  # SL_HIT, TP_HIT, THESIS_INVALID, or N/A

    # Timing and staleness detection
    decision_timestamp: Optional[datetime] = None       # When GLM made this decision
    market_snapshot_timestamp: Optional[datetime] = None  # Timestamp of market data used

    # Market context used during prompt build (to re-use in executor validation)
    context_volatility: Optional[float] = None
    context_atr_pct: Optional[float] = None
    context_vol_ratio: Optional[float] = None
    context_atr_ratio: Optional[float] = None

    # Thought Process (Thesis/Antithesis/Synthesis)
    thought_process: Optional[ThoughtProcess] = None

    # Data Analysis (ADX, Volume, Funding)
    data_analysis: Optional[DataAnalysis] = None

    # Volatility regime for dynamic threshold
    volatility_regime: str = "medium"

    def is_stale(self, max_age_seconds: int = 60) -> bool:
        """
        Check if decision is too old to execute safely

        Args:
            max_age_seconds: Maximum acceptable age (default 60s)

        Returns:
            True if decision is stale and should not be executed
        """
        if not self.decision_timestamp:
            return True  # No timestamp = stale
        age = (datetime.now(timezone.utc) - self.decision_timestamp).total_seconds()
        return age > max_age_seconds

    def age_seconds(self) -> float:
        """Get age of decision in seconds"""
        if not self.decision_timestamp:
            # Return STALE by default (for safety), but log timestamp error
            return 999999.0
        return (datetime.now(timezone.utc) - self.decision_timestamp).total_seconds()


# Utility functions for decision processing


def clamp(value: float, lower: float, upper: float) -> float:
    """
    Clamp a value between lower and upper bounds.
    
    Args:
        value: Value to clamp
        lower: Lower bound
        upper: Upper bound
    
    Returns:
        Clamped value
    """
    return max(lower, min(value, upper))


def json_serializer(obj: Any) -> Any:
    """
    JSON serializer for objects not serializable by default json code.
    
    Args:
        obj: Object to serialize
    
    Returns:
        Serializable representation
    
    Raises:
        TypeError: If object type is not serializable
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")
