from typing import List

from app.agents.base import AgentSignal
from app.config.settings import get_settings
from app.risk_manager.decision_models import RiskDecision
from app.risk_manager.fallback_handler import FallbackHandler
from app.risk_manager.qwen_client import QwenClient
from app.risk_manager.glm_communicator import GLMCommunicator
from app.risk_manager.manager_modules.evaluator import Evaluator
from app.risk_manager.manager_modules.metrics_calculator import MetricsCalculator
from app.risk_manager.manager_modules.prompt_builder import PromptBuilder
from app.risk_manager.manager_modules.signal_processor import SignalProcessor
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
from app.risk_manager.response_parser import ResponseParser
from app.risk_manager.text_parser import parse_state_payload
from app.utils.logging import get_logger
from app.utils.runtime_tracker import RuntimeTracker

logger = get_logger(__name__)


class RiskManager:
    """
    RiskManager: Orchestrator for trading decision evaluation
    
    Architecture: Orchestrator Pattern
    - Delegates prompt building to PromptBuilder
    - Delegates GLM evaluation to Evaluator
    - Delegates signal logging to SignalProcessor
    - Delegates metrics to MetricsCalculator
    
    Responsibilities:
    - Coordinate builder modules
    - Maintain backward compatibility
    - Provide public API
    
    Refactored from 1,557 lines → ~150 lines (90% reduction)
    """
    
    def __init__(self, glm_client: QwenClient | None = None, symbol: str = "BTCUSDT") -> None:
        """
        Initialize RiskManager.

        Args:
            glm_client: Optional QwenClient instance. If not provided, creates default.
                        Use this to inject custom clients with different configs
                        for parallel processing across symbols.
            symbol: Trading symbol (e.g., BTCUSDT, ETHUSDT, SOLUSDT)
        """
        self._symbol = symbol
        self._glm = glm_client if glm_client else QwenClient()
        self._communicator = GLMCommunicator(self._glm)
        self._settings = get_settings()
        self._runtime_tracker = RuntimeTracker.get_instance()
        self._nof1_prompt_builder = Nof1PromptBuilder()

        self._fallback_handler = FallbackHandler()
        
        self._metrics_calculator = MetricsCalculator()
        
        self._response_parser = ResponseParser(
            settings=self._settings,
            glm_client=self._glm,
            nof1_prompt_builder=self._nof1_prompt_builder,
            metrics_calculator=self._metrics_calculator,
        )
        
        self._signal_processor = SignalProcessor(symbol=symbol)
        
        self._prompt_builder = PromptBuilder(
            nof1_prompt_builder=self._nof1_prompt_builder,
            runtime_tracker=self._runtime_tracker,
        )
        
        self._evaluator = Evaluator(
            communicator=self._communicator,
            response_parser=self._response_parser,
            fallback_handler=self._fallback_handler,
            prompt_builder=self._prompt_builder,
            settings=self._settings,
            runtime_tracker=self._runtime_tracker,
            symbol=symbol,
        )

    def evaluate_text_payload(self, payload: str, portfolio_metrics: dict | None = None) -> RiskDecision:
        """Evaluate an external text payload (BTC-only) and return a RiskDecision.

        The payload is expected to include BTCUSDT intraday arrays and 4h context as
        plain text. We parse it into our AgentSignal metadata and reuse GLM evaluation.
        """
        try:
            meta = parse_state_payload(payload)
        except Exception as exc:
            logger.error("Text payload parse failed: %s", exc, exc_info=True)
            return RiskDecision(action="HOLD", amount=0.0, reasoning="Payload parse failed")

        signal = AgentSignal(
            direction="GLM_ONLY",
            confidence=0.0,
            reasoning="external-text",
            timestamp="",
            metadata=meta,
        )
        return self.evaluate([signal], portfolio_metrics)

    def evaluate(self, signals: List[AgentSignal], portfolio_metrics: dict = None) -> RiskDecision:
        """
        Evaluate signals and return trading decision.
        
        Orchestration flow:
        1. Delegate to Evaluator for GLM evaluation
        2. Log signal via SignalProcessor
        3. Return decision
        
        Args:
            signals: List of agent signals
            portfolio_metrics: Portfolio state
            
        Returns:
            RiskDecision with action, amount, leverage, confidence, etc.
        """
        decision = self._evaluator.evaluate(signals, portfolio_metrics)
        
        self._signal_processor.log_signal(
            decision, signals[0] if signals else None, portfolio_metrics
        )
        
        return decision

    def get_parsing_metrics(self) -> dict:
        """Get current JSON parsing metrics for monitoring (delegated to MetricsCalculator)"""
        return self._metrics_calculator.get_parsing_metrics()

    def log_parsing_metrics(self) -> None:
        """Log current parsing metrics with alert status (delegated to MetricsCalculator)"""
        self._metrics_calculator.log_parsing_metrics()
