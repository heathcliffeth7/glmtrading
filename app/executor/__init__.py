"""
Executor Module

Trade execution and portfolio management components.

Modular Components:
- ExecutionResult: Trade execution outcome dataclass
- TradeLogger: File-based trade logging
- PositionManager: Position tracking and retrieval
- GuardrailManager: Risk enforcement and trade validation
- PriceManager: Price resolution with fallback chain
- PortfolioCalculator: Portfolio metrics and PnL calculation
- TradeNotifier: Telegram notifications for trades
- ExitManager: Stop-loss monitoring and exit plan execution
- Executor: Main orchestrator (uses all components)
"""

from .execution_result import ExecutionResult
from .trade_logger import log_trade_to_file, TRADE_LOG_DIR, TRADE_LOG_FILE
from .position_manager import PositionManager
from .guardrails import GuardrailManager
from .price_manager import PriceManager
from .portfolio_calculator import PortfolioCalculator
from .trade_notifications import TradeNotifier
from .exit_manager import ExitManager
from .executor import Executor

__all__ = [
    # Main class
    'Executor',
    'ExecutionResult',
    # Modular components
    'PositionManager',
    'GuardrailManager',
    'PriceManager',
    'PortfolioCalculator',
    'TradeNotifier',
    'ExitManager',
    # Utilities
    'log_trade_to_file',
    'TRADE_LOG_DIR',
    'TRADE_LOG_FILE',
]
