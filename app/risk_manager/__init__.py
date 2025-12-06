"""
Risk Manager Module

Enhanced Features (v2.0):
- Drawdown Manager (Daily/Weekly/Max limits)
- Trailing Stop (ATR, Fixed, Chandelier, Step)
- Breakeven Manager
- Partial Take Profit Manager
- Dynamic Position Sizer (Kelly Criterion)
- Time-Based Filter
- Correlation Guard

Modular Components (v3.0):
- Decision Models (DataAnalysis, ThoughtProcess, RiskDecision)
- Response Parser (JSON parsing and repair)
- Safety Controls (Margin/leverage limits)
- Consistency Validator (Thought process validation)
- Risk Market Analysis (Confidence bands, market conditions)
- Risk Monitoring (Logging, metrics)
- Fallback Handler (Error recovery)
"""

from .drawdown_manager import DrawdownManager
from .trailing_stop import AdvancedTrailingStop, TrailingStopType
from .breakeven_manager import BreakevenManager
from .partial_tp_manager import PartialTakeProfitManager
from .position_sizer import DynamicPositionSizer
from .time_filter import TimeBasedFilter
from .correlation_guard import CorrelationGuard
from .manager import RiskManager
from .nof1_prompt_builder import Nof1PromptBuilder

# New modular components
from .decision_models import DataAnalysis, ThoughtProcess, RiskDecision
from .response_parser import ResponseParser
from .safety_controls import SafetyControls
from .consistency_validator import ConsistencyValidator
from .risk_market_analysis import RiskMarketAnalyzer
from .risk_monitoring import RiskMonitor
from .fallback_handler import FallbackHandler
from .utilities import clamp, json_serializer

__all__ = [
    # Original exports
    'DrawdownManager',
    'AdvancedTrailingStop',
    'TrailingStopType',
    'BreakevenManager',
    'PartialTakeProfitManager',
    'DynamicPositionSizer',
    'TimeBasedFilter',
    'CorrelationGuard',
    'RiskManager',
    'Nof1PromptBuilder',
    # New modular exports
    'DataAnalysis',
    'ThoughtProcess',
    'RiskDecision',
    'ResponseParser',
    'SafetyControls',
    'ConsistencyValidator',
    'RiskMarketAnalyzer',
    'RiskMonitor',
    'FallbackHandler',
    'clamp',
    'json_serializer',
]
