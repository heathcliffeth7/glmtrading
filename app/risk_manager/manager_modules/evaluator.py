from datetime import datetime, timezone
from typing import Any, List, Optional

from app.agents.base import AgentSignal
from app.risk_manager.decision_models import RiskDecision
from app.risk_manager.fallback_handler import FallbackHandler
from app.risk_manager.glm_communicator import GLMCommunicator
from app.risk_manager.manager_modules.prompt_builder import PromptBuilder
from app.risk_manager.response_parser import ResponseParser
from app.risk_manager.risk_controls import (
    ConsistencyValidator,
    DynamicThreshold,
    SafetyLimits,
)
from app.utils.logging import get_logger
from app.utils.runtime_tracker import RuntimeTracker

logger = get_logger(__name__)


class Evaluator:
    """
    Evaluator: GLM evaluation ve risk control modülü
    
    Sorumluluk:
    - GLM API çağrısı ve response handling
    - Pre-GLM SL/TP kontrolü (deterministic exit)
    - Risk controls uygulama (SafetyLimits, ConsistencyValidator, DynamicThreshold)
    - Confidence threshold enforcement
    - Timing ve staleness tracking
    
    Single Responsibility: GLM evaluation ve risk management
    """
    
    def __init__(
        self,
        communicator: GLMCommunicator,
        response_parser: ResponseParser,
        fallback_handler: FallbackHandler,
        prompt_builder: PromptBuilder,
        settings: Any,
        runtime_tracker: RuntimeTracker,
        symbol: str = "BTCUSDT",
    ):
        self._communicator = communicator
        self._response_parser = response_parser
        self._fallback_handler = fallback_handler
        self._prompt_builder = prompt_builder
        self._settings = settings
        self._runtime_tracker = runtime_tracker
        self._symbol = symbol
    
    def evaluate(
        self,
        signals: List[AgentSignal],
        portfolio_metrics: dict = None,
    ) -> RiskDecision:
        """
        Evaluate signals and return trading decision.
        
        Flow:
        1. Increment runtime tracker
        2. Build prompt (NOF1 or regular style)
        3. Extract volatility context
        4. Pre-GLM SL/TP check (deterministic exit)
        5. Call GLM API
        6. Parse response
        7. Apply risk controls (SafetyLimits, Consistency, Confidence)
        8. Attach context and timing metadata
        9. Return decision
        """
        self._runtime_tracker.increment()
        
        evaluation_start_time = datetime.now(timezone.utc)
        
        try:
            if self._settings.use_nof1_style:
                prompt_messages = self._prompt_builder.build_nof1_prompt(signals, portfolio_metrics)
            else:
                prompt_messages = self._prompt_builder.build_prompt(
                    signals, portfolio_metrics, use_nof1_style=False
                )
            
            volatility_context = self._prompt_builder.get_volatility_context()
            
        except Exception as exc:
            logger.error("Prompt build failed: %s", exc, exc_info=True)
            return self._fallback_handler.fallback_decision(
                signals,
                f"Prompt build failed: {exc}",
            )
        
        trace_id = None
        market_snapshot_timestamp = None
        if signals:
            trace_id = signals[0].metadata.get("trace_id")
            raw_market_data = signals[0].metadata.get("raw_market_data", {})
            current_snapshots = raw_market_data.get("current_snapshots", {})
            snapshot_30m = current_snapshots.get("30m", {})
            if "_time" in snapshot_30m:
                market_snapshot_timestamp = snapshot_30m["_time"]
        
        sl_tp_decision = self._check_sl_tp_before_glm(portfolio_metrics)
        if sl_tp_decision:
            return sl_tp_decision
        
        try:
            response = self._communicator.request(prompt_messages, symbol=self._symbol, trace_id=trace_id)
            
            if self._settings.use_nof1_style:
                decision = self._response_parser.parse_response(response, portfolio_metrics)
            else:
                decision = self._response_parser.parse_response(response, portfolio_metrics)
            
            if decision is None:
                logger.error("❌ Decision object is None after parsing - using emergency fallback")
                decision = RiskDecision(
                    action="HOLD",
                    amount=0.0,
                    reasoning="Critical: Decision parsing returned None (GLM failure)",
                    leverage=5.0,
                    glm_confidence=0.0,
                    reason_primary="System Error",
                    reason_secondary="Decision object was None"
                )
            
            decision.decision_timestamp = datetime.now(timezone.utc)
            decision.market_snapshot_timestamp = market_snapshot_timestamp
            
            if '_glm_latency_ms' in response:
                decision.glm_response_time_ms = response['_glm_latency_ms']
                logger.info("✅ GLM latency captured: %.0fms", decision.glm_response_time_ms)
            
            decision = self._attach_context(decision, volatility_context, market_snapshot_timestamp)
            
            logger.info(
                "📊 Context attached to decision (id=%s): atr_pct=%s, volatility=%s",
                id(decision), decision.context_atr_pct, decision.context_volatility
            )
            
            evaluation_duration = (datetime.now(timezone.utc) - evaluation_start_time).total_seconds()
            if evaluation_duration > 90:
                logger.warning(
                    "⚠️ GLM evaluation took %.1fs - market may have moved significantly",
                    evaluation_duration
                )
            
            decision = SafetyLimits.apply(decision, portfolio_metrics)
            
            original_action = decision.action
            decision = self._apply_consistency_validation(decision, original_action)
            decision = self._enforce_confidence_threshold(decision, volatility_context["volatility_regime"])
            
            logger.info(
                "GLM decision: action=%s amount=%.4f leverage=%.2f confidence=%.1f",
                decision.action,
                decision.amount,
                decision.leverage,
                decision.glm_confidence,
            )
            
            if decision.reason_primary:
                logger.info("  Primary reason: %s", decision.reason_primary)
            if decision.reason_secondary:
                logger.info("  Secondary reason: %s", decision.reason_secondary)
            
            return decision
            
        except Exception as exc:
            error_msg = f"GLM API hatası: {str(exc)}"
            logger.error("GLM request failed: %s", exc, exc_info=True)
            
            self._fallback_handler.notify_glm_failure(signals, str(exc))
            
            fallback_decision = self._fallback_handler.fallback_decision(signals, error_msg)
            logger.warning(
                "Using fallback decision: action=%s amount=%.4f leverage=%.2f",
                fallback_decision.action,
                fallback_decision.amount,
                fallback_decision.leverage,
            )
            
            return fallback_decision
    
    def _check_sl_tp_before_glm(
        self,
        portfolio_metrics: dict,
    ) -> Optional[RiskDecision]:
        """
        Check if SL or TP hit BEFORE calling GLM (saves tokens + deterministic).
        Returns decision if exit needed, None otherwise.
        """
        if not portfolio_metrics:
            return None
        
        has_position = portfolio_metrics.get("position", 0) != 0
        if not has_position:
            return None
        
        current_price = (
            portfolio_metrics.get("current_price", 0.0) or
            portfolio_metrics.get("mark_price", 0.0) or
            portfolio_metrics.get("price", 0.0)
        )
        exit_plan = portfolio_metrics.get("exit_plan", {}) or {}
        stop_loss = exit_plan.get("stop_loss", 0.0) or 0.0
        take_profit = (
            exit_plan.get("profit_target", 0.0) or
            exit_plan.get("take_profit", 0.0) or 0.0
        )
        position_qty = portfolio_metrics.get("position", 0.0)
        position_type = "LONG" if position_qty > 0 else "SHORT"

        sl_hit = False
        if stop_loss > 0 and current_price > 0:
            if position_type == "LONG" and current_price <= stop_loss:
                sl_hit = True
            elif position_type == "SHORT" and current_price >= stop_loss:
                sl_hit = True

        if sl_hit:
            logger.info(
                "🛑 SL_HIT detected BEFORE GLM call - skipping GLM (price=%.2f, SL=%.2f, type=%s)",
                current_price, stop_loss, position_type
            )
            return RiskDecision(
                action="CLOSE",
                amount=0.0,
                reasoning=f"Stop-loss hit: {position_type} pozisyon SL seviyesine ulaştı (SL={stop_loss:.2f}, Fiyat={current_price:.2f})",
                leverage=10.0,
                glm_confidence=100.0,
                reason_primary="SL_HIT",
                reason_secondary=f"Price {current_price:.2f} hit SL {stop_loss:.2f}",
                exit_validation="SL_HIT",
                decision_timestamp=datetime.now(timezone.utc),
            )

        tp_hit = False
        if take_profit > 0 and current_price > 0:
            if position_type == "LONG" and current_price >= take_profit:
                tp_hit = True
            elif position_type == "SHORT" and current_price <= take_profit:
                tp_hit = True

        if tp_hit:
            logger.info(
                "🎯 TP_HIT detected BEFORE GLM call - skipping GLM (price=%.2f, TP=%.2f, type=%s)",
                current_price, take_profit, position_type
            )
            return RiskDecision(
                action="CLOSE",
                amount=0.0,
                reasoning=f"Take-profit hit: {position_type} pozisyon TP hedefine ulaştı (TP={take_profit:.2f}, Fiyat={current_price:.2f})",
                leverage=10.0,
                glm_confidence=100.0,
                reason_primary="TP_HIT",
                reason_secondary=f"Price {current_price:.2f} hit TP {take_profit:.2f}",
                exit_validation="TP_HIT",
                decision_timestamp=datetime.now(timezone.utc),
            )
        
        return None
    
    def _apply_consistency_validation(
        self,
        decision: RiskDecision,
        original_action: str,
    ) -> RiskDecision:
        """Apply consistency validation if enabled."""
        if not self._settings.zai.enable_thought_process:
            return decision
        
        if not decision.thought_process:
            return decision
        
        if not self._settings.zai.enable_consistency_validation:
            return decision
        
        is_consistent, consistency_reason = ConsistencyValidator.validate(
            decision.thought_process, decision.action
        )
        decision.thought_process.is_consistent = is_consistent
        decision.thought_process.consistency_reason = consistency_reason

        if not is_consistent:
            logger.warning(
                "⚠️ CONSISTENCY MISMATCH: Signal %s conflicts with synthesis - %s",
                decision.action, consistency_reason
            )

            if self._settings.zai.enable_consistency_enforcement:
                ConsistencyValidator.notify_mismatch(
                    self._symbol,
                    original_action,
                    consistency_reason,
                    decision.thought_process.synthesis_verdict
                )
                
                return RiskDecision(
                    action="HOLD",
                    amount=0.0,
                    reasoning=f"Consistency mismatch: {consistency_reason} - forced to HOLD",
                    leverage=decision.leverage,
                    glm_confidence=decision.glm_confidence,
                    reason_primary=decision.reason_primary,
                    reason_secondary=f"Mismatch: {consistency_reason}",
                    glm_response_time_ms=decision.glm_response_time_ms,
                    exit_plan=None,
                    decision_timestamp=decision.decision_timestamp,
                    market_snapshot_timestamp=decision.market_snapshot_timestamp,
                    context_volatility=decision.context_volatility,
                    context_atr_pct=decision.context_atr_pct,
                    context_vol_ratio=decision.context_vol_ratio,
                    context_atr_ratio=decision.context_atr_ratio,
                    thought_process=decision.thought_process,
                    volatility_regime=decision.volatility_regime,
                )
        
        return decision
    
    def _enforce_confidence_threshold(
        self,
        decision: RiskDecision,
        volatility_regime: str,
    ) -> RiskDecision:
        """Enforce confidence threshold based on volatility regime."""
        if self._settings.zai.enable_dynamic_threshold:
            threshold = DynamicThreshold.get_threshold(volatility_regime)
            logger.info(
                "📊 Dynamic threshold: %.1f%% (regime=%s)",
                threshold, volatility_regime
            )
        else:
            threshold = 80.0

        if decision.action in ["BUY", "SELL"] and decision.glm_confidence < threshold:
            logger.warning(
                "⚠️ CONFIDENCE TOO LOW: GLM wanted %s with confidence %.1f%% < %.1f%% → Forcing HOLD",
                decision.action,
                decision.glm_confidence,
                threshold
            )

            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"GLM confidence ({decision.glm_confidence:.1f}%) below {threshold:.0f}% threshold - forced to HOLD for risk management",
                leverage=decision.leverage,
                glm_confidence=decision.glm_confidence,
                reason_primary=decision.reason_primary,
                reason_secondary="Confidence threshold not met",
                glm_response_time_ms=decision.glm_response_time_ms,
                exit_plan=None,
                decision_timestamp=decision.decision_timestamp,
                market_snapshot_timestamp=decision.market_snapshot_timestamp,
                context_volatility=decision.context_volatility,
                context_atr_pct=decision.context_atr_pct,
                context_vol_ratio=decision.context_vol_ratio,
                context_atr_ratio=decision.context_atr_ratio,
                thought_process=decision.thought_process,
                volatility_regime=decision.volatility_regime,
            )
        
        return decision
    
    def _attach_context(
        self,
        decision: RiskDecision,
        volatility_context: dict,
        market_snapshot_timestamp: Optional[str],
    ) -> RiskDecision:
        """Attach market context and timing metadata to decision."""
        decision.context_volatility = volatility_context.get("volatility")
        decision.context_atr_pct = volatility_context.get("atr_pct")
        decision.context_vol_ratio = volatility_context.get("vol_ratio")
        decision.context_atr_ratio = volatility_context.get("atr_ratio")
        decision.volatility_regime = volatility_context.get("volatility_regime", "medium")
        
        return decision
