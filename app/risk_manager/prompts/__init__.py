"""
Prompts Module

Modular components for NOF1 prompt building.
"""

from .data_validation import DataValidator
from .indicator_interpretation import IndicatorInterpreter
from .market_analysis import MarketAnalyzer
from .models import (
    LOSS_MANAGEMENT,
    VOLATILITY_PARAMS,
    Nof1Config,
    PerformanceState,
    PositionCloseNotification,
    VolatilityState,
)
from .risk_parameters import RiskParameterCalculator
from .volatility_analysis import VolatilityAnalyzer

# NEW: Token optimization modules
from .templates import (
    FEW_SHOT_TRAINING,
    HARD_RULES_BLOCK,
    GLOSSARY_TERMS,
    RSI_CONTEXT_RULES,
    build_position_active_instructions_template,
    build_no_position_instructions_template,
    build_dynamic_glossary,
)
from .metrics_calculator import (
    VolatilityCache,
    compute_realized_vol_pct,
    rolling_median,
    calculate_percentile,
    calculate_slope,
    detect_divergence,
    summarize_series,
    format_array,
)
from .feature_analyzers import (
    volume_depth_check,
    session_anomaly_check,
    oi_analysis_check,
    vwap_position_check,
    liquidation_magnet_check,
    data_sanity_check,
    adx_regime_check,
    micro_divergence_check,
    liquidity_sweep_check,
    LogicGatesBuilder,
)

__all__ = [
    # Models
    "PositionCloseNotification",
    "Nof1Config",
    "VolatilityState",
    "PerformanceState",
    "VOLATILITY_PARAMS",
    "LOSS_MANAGEMENT",
    # Analyzers
    "VolatilityAnalyzer",
    "IndicatorInterpreter",
    "DataValidator",
    "MarketAnalyzer",
    "RiskParameterCalculator",
    # Templates
    "FEW_SHOT_TRAINING",
    "HARD_RULES_BLOCK",
    "GLOSSARY_TERMS",
    "RSI_CONTEXT_RULES",
    "build_position_active_instructions_template",
    "build_no_position_instructions_template",
    "build_dynamic_glossary",
    # Metrics Calculator
    "VolatilityCache",
    "compute_realized_vol_pct",
    "rolling_median",
    "calculate_percentile",
    "calculate_slope",
    "detect_divergence",
    "summarize_series",
    "format_array",
    # Feature Analyzers
    "volume_depth_check",
    "session_anomaly_check",
    "oi_analysis_check",
    "vwap_position_check",
    "liquidation_magnet_check",
    "data_sanity_check",
    "adx_regime_check",
    "micro_divergence_check",
    "liquidity_sweep_check",
    "LogicGatesBuilder",
]
