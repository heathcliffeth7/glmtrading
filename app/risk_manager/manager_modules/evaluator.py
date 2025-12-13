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
from app.risk_manager.tp_sl_calculator import TPSLCalculator
from app.risk_manager.hard_veto import check_market_conditions_veto
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
        
        # Initialize TP/SL calculator with regime config
        regime_config = {
            'low': {
                'sl_multiplier': settings.trading.tp_sl_low_vol_multiplier,
                'risk_reward': settings.trading.tp_sl_low_vol_rr,
            },
            'medium': {
                'sl_multiplier': settings.trading.tp_sl_medium_vol_multiplier,
                'risk_reward': settings.trading.tp_sl_medium_vol_rr,
            },
            'high': {
                'sl_multiplier': settings.trading.tp_sl_high_vol_multiplier,
                'risk_reward': settings.trading.tp_sl_high_vol_rr,
            },
            'extreme': {
                'sl_multiplier': settings.trading.tp_sl_extreme_vol_multiplier,
                'risk_reward': settings.trading.tp_sl_extreme_vol_rr,
            },
        }
        
        self._tp_sl_calc = TPSLCalculator(
            use_dynamic_regime=settings.trading.tp_sl_use_dynamic_regime,
            regime_config=regime_config,
            default_atr_multiplier=settings.trading.tp_sl_atr_multiplier,
            default_risk_reward=settings.trading.tp_sl_risk_reward_ratio,
        )
    
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

            # Prompt'u decision'a ekle (CSV export için)
            try:
                if isinstance(prompt_messages, list):
                    decision.prompt_sent = "\n".join(
                        f"[{m.get('role', 'unknown')}]\n{m.get('content', '')}"
                        for m in prompt_messages if isinstance(m, dict)
                    )
                    logger.info("📝 prompt_sent set: %d chars (list with %d items)",
                               len(decision.prompt_sent) if decision.prompt_sent else 0,
                               len(prompt_messages))
                elif isinstance(prompt_messages, str):
                    decision.prompt_sent = prompt_messages
                    logger.info("📝 prompt_sent (str) set: %d chars", len(decision.prompt_sent))
                else:
                    logger.warning("⚠️ prompt_messages type unexpected: %s", type(prompt_messages))
            except Exception as pe:
                logger.warning("Failed to attach prompt to decision: %s", pe, exc_info=True)

            if '_glm_latency_ms' in response:
                decision.glm_response_time_ms = response['_glm_latency_ms']
                logger.info("✅ LLM latency captured: %.0fms", decision.glm_response_time_ms)
            
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

            # Extract market data for veto/penalty checks
            market_data_for_veto = self._extract_market_data_for_veto(signals, volatility_context)

            # STEP 1: Hard veto check (ADX + Volume + Trend)
            decision = self._check_hard_veto(decision, market_data_for_veto)

            # STEP 2: Apply confidence penalties (ADX + Volume + Trend)
            if decision.action in ['BUY', 'SELL']:  # Only if not already vetoed
                decision = self._apply_confidence_penalties(decision, market_data_for_veto)

            original_action = decision.action
            decision = self._apply_consistency_validation(decision, original_action)
            decision = self._enforce_confidence_threshold(decision, volatility_context["volatility_regime"])
            
            min_confidence = self._settings.trading.min_confidence_for_trade
            
            if decision.glm_confidence < min_confidence and decision.action in ['BUY', 'SELL']:
                logger.warning(
                    "Confidence %.1f%% < %.1f%% threshold, forcing HOLD",
                    decision.glm_confidence, min_confidence
                )
                original_reasoning = decision.reasoning or f"{decision.action} ({decision.glm_confidence:.1f}% conf)"
                decision.action = 'HOLD'
                decision.amount = 0.0
                decision.reasoning = (
                    f"Original: {original_reasoning} - "
                    f"Forced HOLD (confidence below {min_confidence:.0f}% threshold)"
                )
            
            if decision.action in ['BUY', 'SELL'] and decision.glm_confidence >= min_confidence:
                entry_price = (
                    signals[0].metadata.get('price', 0) if signals
                    else portfolio_metrics.get('current_price', 0)
                )
                atr = volatility_context.get('atr', 0)
                volatility_regime = volatility_context.get('volatility_regime', 'medium')
                
                # Initialize exit_plan if not exists
                if decision.exit_plan is None:
                    decision.exit_plan = {}
                
                if entry_price > 0 and atr > 0:
                    # ATR-based TP/SL calculator with dynamic regime
                    tp_sl = self._tp_sl_calc.calculate(
                        signal=decision.action,
                        entry_price=entry_price,
                        atr=atr,
                        volatility_regime=volatility_regime,
                    )
                    
                    decision.exit_plan['stop_loss'] = tp_sl['stop_loss']
                    decision.exit_plan['take_profit'] = tp_sl['take_profit']
                    
                    logger.info(
                        "✅ TP/SL [%s regime] calculated: TP=$%.2f SL=$%.2f (risk=%.1f%%, reward=%.1f%%, mult=%.1fx, R:R=%.1f:1)",
                        tp_sl['regime'].upper(),
                        tp_sl['take_profit'], tp_sl['stop_loss'],
                        tp_sl['risk_pct'], tp_sl['reward_pct'],
                        tp_sl['atr_multiplier'], tp_sl['risk_reward']
                    )
                elif entry_price > 0:
                    # FALLBACK: Percentage-based TP/SL
                    logger.warning("Cannot calculate ATR-based TP/SL (entry=%.2f, atr=%.2f), using fallback", entry_price, atr)
                    
                    sl_pct = self._settings.trading.get('tp_sl_fallback_sl_pct', 2.0) / 100.0
                    tp_pct = self._settings.trading.get('tp_sl_fallback_tp_pct', 5.0) / 100.0
                    
                    if decision.action == 'BUY':
                        decision.exit_plan['stop_loss'] = entry_price * (1 - sl_pct)
                        decision.exit_plan['take_profit'] = entry_price * (1 + tp_pct)
                    else:  # SELL
                        decision.exit_plan['stop_loss'] = entry_price * (1 + sl_pct)
                        decision.exit_plan['take_profit'] = entry_price * (1 - tp_pct)
                    
                    logger.info(
                        "✅ Fallback TP/SL: TP=$%.2f SL=$%.2f (±%.1f%%/%.1f%%)",
                        decision.exit_plan['take_profit'],
                        decision.exit_plan['stop_loss'],
                        sl_pct * 100,
                        tp_pct * 100
                    )
                else:
                    # WORST CASE: No entry price
                    logger.error("❌ No entry price available, cannot set exit_plan - forcing HOLD")
                    decision.action = 'HOLD'
                    decision.amount = 0.0
                    decision.reasoning = f"Forced HOLD - no price data for exit plan (original: {decision.action})"
                
                # Add invalidation condition if exit_plan is set
                if decision.action in ['BUY', 'SELL'] and 'stop_loss' in decision.exit_plan:
                    decision.exit_plan['invalidation_condition'] = self._generate_invalidation_condition(
                        decision.action,
                        decision.exit_plan['stop_loss'],
                        signals
                    )

                # Fix: If amount is 0, set default based on confidence ($2000-3000 position @ 10x)
                if decision.amount < 0.01:  # Less than 1% is effectively zero
                    # Set default amount based on confidence (equity ratio for $10k account)
                    if decision.glm_confidence >= 90:
                        default_amount = 0.30  # $3000 position
                    elif decision.glm_confidence >= 85:
                        default_amount = 0.25  # $2500 position
                    else:  # 80-84
                        default_amount = 0.20  # $2000 position

                    logger.info(
                        "📊 Amount was %.4f (too small), setting default: %.4f (GLM confidence %.1f%%)",
                        decision.amount, default_amount, decision.glm_confidence
                    )
                    decision.amount = default_amount

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
            error_msg = f"LLM API hatası: {str(exc)}"
            logger.error("LLM request failed: %s", exc, exc_info=True)

            self._fallback_handler.notify_llm_failure(signals, str(exc))
            
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
    
    def _generate_invalidation_condition(
        self,
        action: str,
        stop_loss: float,
        signals: list,
    ) -> str:
        """
        Generate invalidation condition using support/resistance if available.
        
        Args:
            action: 'BUY' or 'SELL'
            stop_loss: Stop loss price level
            signals: Market signals with metadata
            
        Returns:
            Human-readable invalidation condition string
        """
        metadata = signals[0].metadata if signals else {}
        
        if action == 'BUY':
            # Check for support level below SL
            support_4h = metadata.get('support_4h')
            support_daily = metadata.get('support_daily')
            
            support = None
            support_tf = None
            
            if support_4h and support_4h < stop_loss:
                support = support_4h
                support_tf = "4h"
            elif support_daily and support_daily < stop_loss:
                support = support_daily
                support_tf = "daily"
            
            if support:
                return f"15m candle closes below ${support:.2f} ({support_tf} support level)"
            else:
                return f"15m candle closes below ${stop_loss:.2f} (stop loss)"
        
        else:  # SELL
            # Check for resistance level above SL
            resistance_4h = metadata.get('resistance_4h')
            resistance_daily = metadata.get('resistance_daily')
            
            resistance = None
            resistance_tf = None
            
            if resistance_4h and resistance_4h > stop_loss:
                resistance = resistance_4h
                resistance_tf = "4h"
            elif resistance_daily and resistance_daily > stop_loss:
                resistance = resistance_daily
                resistance_tf = "daily"
            
            if resistance:
                return f"15m candle closes above ${resistance:.2f} ({resistance_tf} resistance level)"
            else:
                return f"15m candle closes above ${stop_loss:.2f} (stop loss)"
    
    def _apply_confidence_penalties(
        self,
        decision: RiskDecision,
        market_data: dict,
    ) -> RiskDecision:
        """
        GLM confidence'ını market koşullarına göre düzelt.

        ADX, Volume ve Trend Strength bazlı cezalar uygular.

        Args:
            decision: GLM'den gelen karar
            market_data: Market verileri (adx, volume_ratio, trend_strength)

        Returns:
            Düzeltilmiş confidence ile karar
        """
        if decision.action not in ['BUY', 'SELL']:
            return decision

        original_confidence = decision.glm_confidence
        penalties = []

        adx = market_data.get('adx', 25.0)
        vol_ratio = market_data.get('volume_ratio', 1.0)
        trend_strength = market_data.get('trend_strength', 'MODERATE')

        # Normalize values
        adx = float(adx) if adx is not None else 25.0
        vol_ratio = float(vol_ratio) if vol_ratio is not None else 1.0

        # =====================================================================
        # ADX-based penalties
        # =====================================================================
        if adx < 15:
            penalties.append(("ADX < 15 (no trend)", -30))
        elif adx < 20:
            penalties.append(("ADX < 20 (ranging)", -25))
        elif adx < 25:
            penalties.append(("ADX < 25 (weak trend)", -15))

        # =====================================================================
        # Volume-based penalties (threshold raised from 0.3 to 0.4)
        # =====================================================================
        if vol_ratio < 0.4:
            penalties.append(("Volume < 0.4 (illiquid)", -25))
        elif vol_ratio < 0.5:
            penalties.append(("Volume < 0.5 (low)", -20))
        elif vol_ratio < 0.7:
            penalties.append(("Volume < 0.7 (below avg)", -10))

        # =====================================================================
        # R:R Ratio penalties
        # =====================================================================
        rr_ratio = market_data.get('rr_ratio', 2.0)
        rr_ratio = float(rr_ratio) if rr_ratio is not None else 2.0

        if rr_ratio < 1.0:
            penalties.append((f"R:R {rr_ratio:.2f} < 1:1 (bad)", -25))
        elif rr_ratio < 1.5:
            penalties.append((f"R:R {rr_ratio:.2f} < 1.5:1 (weak)", -10))

        # =====================================================================
        # Trend strength penalties
        # =====================================================================
        if trend_strength == 'WEAK':
            penalties.append(("WEAK trend", -20))
        elif trend_strength == 'MODERATE':
            penalties.append(("MODERATE trend", -10))
        # STRONG trend = no penalty

        # Apply penalties
        total_penalty = sum(p[1] for p in penalties)
        new_confidence = max(0.0, original_confidence + total_penalty)

        if penalties:
            penalty_str = ", ".join(f"{name}={val}" for name, val in penalties)
            logger.warning(
                "📉 Confidence penalty applied: %.1f → %.1f (total: %d) | %s",
                original_confidence, new_confidence, total_penalty, penalty_str
            )
            decision.glm_confidence = new_confidence
            decision.reasoning = (
                f"{decision.reasoning} | Confidence adjusted: "
                f"{original_confidence:.0f}% → {new_confidence:.0f}% (penalties: {total_penalty})"
            )

        return decision

    def _check_hard_veto(
        self,
        decision: RiskDecision,
        market_data: dict,
    ) -> RiskDecision:
        """
        Hard veto kontrolü - ADX/Volume/Trend bazlı.

        Belirli koşullarda GLM kararından bağımsız HOLD'a zorlar.

        Args:
            decision: GLM'den gelen karar
            market_data: Market verileri (adx, volume_ratio, trend_strength)

        Returns:
            Veto uygulanmışsa HOLD kararı, değilse orijinal karar
        """
        if decision.action not in ['BUY', 'SELL']:
            return decision

        should_veto, veto_reason = check_market_conditions_veto(market_data)

        if should_veto:
            logger.warning(
                "🚫 HARD VETO: %s → %s | Forcing HOLD",
                decision.action, veto_reason
            )

            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"{veto_reason} | Original: {decision.action} ({decision.glm_confidence:.0f}% conf)",
                leverage=decision.leverage,
                glm_confidence=0.0,
                reason_primary="HARD_VETO",
                reason_secondary=veto_reason,
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

    def _extract_market_data_for_veto(
        self,
        signals: List[AgentSignal],
        volatility_context: dict,
    ) -> dict:
        """
        Signal ve volatility context'ten veto kontrolü için market data çıkar.

        Args:
            signals: Agent sinyalleri
            volatility_context: Volatility context dict

        Returns:
            Market data dict (adx, volume_ratio, trend_strength)
        """
        market_data = {
            'adx': 25.0,
            'volume_ratio': 1.0,
            'trend_strength': 'MODERATE',
            'logic_gates_risk_count': 0,
            'rr_ratio': 2.0,  # Default good R:R
        }

        if not signals:
            return market_data

        # Extract from signal metadata
        metadata = signals[0].metadata or {}
        raw_market_data = metadata.get('raw_market_data', {})
        current_snapshots = raw_market_data.get('current_snapshots', {})
        snapshot_4h = current_snapshots.get('4h', {})

        # ADX - handle both list and single float values
        adx_values = snapshot_4h.get('adx_14', [])
        if isinstance(adx_values, (list, tuple)) and len(adx_values) > 0:
            market_data['adx'] = float(adx_values[-1])
        elif isinstance(adx_values, (int, float)):
            market_data['adx'] = float(adx_values)

        # Volume ratio from volatility context
        vol_ratio = volatility_context.get('vol_ratio', 1.0)
        if vol_ratio is not None:
            market_data['volume_ratio'] = float(vol_ratio)

        # Trend strength from metadata
        trend_strength = metadata.get('trend_strength', 'MODERATE')
        if trend_strength:
            market_data['trend_strength'] = str(trend_strength).upper()

        # Logic gates risk count
        logic_gates_risk = metadata.get('logic_gates_risk_count', 0)
        if logic_gates_risk is not None:
            market_data['logic_gates_risk_count'] = int(logic_gates_risk)

        # R:R Ratio from metadata
        rr_ratio = metadata.get('rr_ratio', 2.0)
        if rr_ratio is not None:
            try:
                market_data['rr_ratio'] = float(rr_ratio)
            except (ValueError, TypeError):
                market_data['rr_ratio'] = 2.0

        logger.debug(
            "📊 Market data for veto check: ADX=%.1f, Vol=%.2fx, Trend=%s, R:R=%.2f",
            market_data['adx'],
            market_data['volume_ratio'],
            market_data['trend_strength'],
            market_data['rr_ratio']
        )

        return market_data

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
