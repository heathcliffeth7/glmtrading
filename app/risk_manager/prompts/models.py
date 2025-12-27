"""
Prompt Builder Models Module

Data classes and Pydantic models for prompt building.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple

from pydantic import BaseModel, field_validator


class PositionCloseNotification(BaseModel):
    """
    Validated position close notification from Redis.

    Used for secure handling of position close events.
    """

    trigger_type: str
    reason: str
    timestamp: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    position_type: str
    quantity: float
    signature: str = ""  # HMAC signature (optional for backward compatibility)

    @field_validator("trigger_type")
    @classmethod
    def validate_trigger(cls, v: str) -> str:
        allowed = {
            "stop_loss",
            "invalidation",
            "take_profit",
            "manual",
            "trailing_stop",
            "breakeven",
        }
        if v not in allowed:
            raise ValueError(f"Invalid trigger_type: {v}. Allowed: {allowed}")
        return v

    @field_validator("position_type")
    @classmethod
    def validate_position(cls, v: str) -> str:
        if v not in {"LONG", "SHORT"}:
            raise ValueError(f"Invalid position_type: {v}. Must be LONG or SHORT")
        return v

    @field_validator("entry_price", "exit_price", "quantity")
    @classmethod
    def validate_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"Value must be non-negative: {v}")
        return v


@dataclass(frozen=True)
class Nof1Config:
    """
    Centralized configuration for NOF1 prompt builder.
    Replaces magic numbers scattered throughout the code.
    Frozen to ensure immutability.
    """

    # Move thresholds
    min_move_pct_base: float = 0.25
    atr_multiplier: float = 0.3
    fomo_atr_multiplier: float = 2.0

    # Stop loss bounds
    sl_floor_pct: float = 0.3
    sl_cap_swing_pct: float = 15.0
    sl_cap_scalp_pct: float = 8.0

    # Take profit bounds
    tp_floor_pct: float = 0.6

    # Invalidation
    invalidation_sl_ratio: float = 0.7

    # Confidence calculations
    confidence_base: float = 0.80
    confidence_loss_boost: float = 0.10

    # Performance-based leverage caps
    consecutive_loss_lev_cap: int = 5
    low_winrate_lev_cap: int = 6

    # Caching
    cache_ttl_seconds: int = 60
    notification_max_age_minutes: int = 5


@dataclass(frozen=True)
class VolatilityState:
    """
    Immutable state container for volatility metrics.
    Groups related volatility values together.
    """

    legacy_score: float = 0.5
    atr: float = 0.0
    atr_pct: float = 0.0
    realized_vol_pct: float = 0.0
    median_realized_vol_pct: float = 0.0
    vol_ratio: float = 1.0
    atr_ratio: float = 1.0
    regime: str = "medium"


@dataclass(frozen=True)
class PerformanceState:
    """
    Immutable state container for performance tracking.
    Groups related performance metrics together.
    """

    consecutive_losses: int = 0
    last_trade_side: Optional[str] = None
    recent_win_rate: float = 0.5
    performance_history: Tuple[int, ...] = field(default_factory=tuple)


# Volatility parameters for different regimes (Day Trade - tighter stops)
VOLATILITY_PARAMS = {
    "low": {"sl_mult": 0.6, "tp_rr": 2.0, "max_lev": 7},
    "medium": {"sl_mult": 0.8, "tp_rr": 1.8, "max_lev": 6},
    "high": {"sl_mult": 1.0, "tp_rr": 1.5, "max_lev": 5},
    "extreme": {"sl_mult": 1.2, "tp_rr": 1.3, "max_lev": 3},
}


# Loss management multipliers
LOSS_MANAGEMENT = {
    0: {"size_mult": 1.0, "extra_confluence": 0},
    1: {"size_mult": 1.0, "extra_confluence": 0},
    2: {"size_mult": 0.75, "extra_confluence": 5},
    3: {"size_mult": 0.50, "extra_confluence": 10, "require_grade": "A+"},
    4: {"size_mult": 0.25, "extra_confluence": 15, "require_grade": "A+"},
}
