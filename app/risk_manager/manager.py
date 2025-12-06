import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.agents.base import AgentSignal
from app.config.settings import get_settings
from app.risk_manager.glm_client import GLMClient
from app.risk_manager.glm_communicator import GLMCommunicator
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
from app.risk_manager.text_parser import parse_state_payload
from app.utils.logging import get_logger
from app.utils.runtime_tracker import RuntimeTracker
from app.utils.signal_logger import get_signal_logger
from app.utils.telegram import telegram_client

# Modular components
from app.risk_manager.decision_models import (
    DataAnalysis,
    ThoughtProcess,
    RiskDecision,
    clamp,
    json_serializer,
)
from app.risk_manager.risk_controls import (
    ConsistencyValidator,
    DynamicThreshold,
    SafetyLimits,
    ConfidenceGuardrails,
    normalize_leverage,
    normalize_quantity_to_allocation,
)
from app.risk_manager.fallback_handler import FallbackHandler
from app.risk_manager.response_parser import ResponseParser


logger = get_logger(__name__)


class RiskManager:
    def __init__(self, glm_client: GLMClient | None = None, symbol: str = "BTCUSDT") -> None:
        """
        Initialize RiskManager.

        Args:
            glm_client: Optional GLMClient instance. If not provided, creates default.
                        Use this to inject custom GLM clients with different API keys
                        for parallel processing across symbols.
            symbol: Trading symbol (e.g., BTCUSDT, ETHUSDT, SOLUSDT)
        """
        self._symbol = symbol
        self._glm = glm_client if glm_client else GLMClient()
        self._communicator = GLMCommunicator(self._glm)  # Wrapper with logging
        self._signal_logger = get_signal_logger()
        self._settings = get_settings()
        self._runtime_tracker = RuntimeTracker.get_instance()
        self._nof1_prompt_builder = Nof1PromptBuilder()

        # Initialize modular components
        self._response_parser = ResponseParser(
            settings=self._settings,
            glm_client=self._glm,
            nof1_prompt_builder=self._nof1_prompt_builder
        )
        self._fallback_handler = FallbackHandler()

        # 📊 Monitoring metrics
        self._parsing_metrics = {
            "total_json_requests": 0,
            "successful_json_parsing": 0,
            "json_parsing_errors": 0,
            "fallback_parsing_successes": 0,
            "fallback_parsing_failures": 0,
            "signal_recoveries": 0,
            "complete_failures": 0,
            # Thought Process metrics
            "thought_process_present": 0,
            "thought_process_missing": 0,
            "thought_process_parse_errors": 0,
            # Consistency validation metrics
            "consistency_checks_performed": 0,
            "consistency_mismatches": 0,
            "consistency_forced_holds": 0,
            # Dynamic threshold metrics
            "dynamic_threshold_low": 0,
            "dynamic_threshold_medium": 0,
            "dynamic_threshold_high": 0,
            "dynamic_threshold_extreme": 0,
        }

    # =========================================================================
    # THOUGHT PROCESS HELPERS (Thesis/Antithesis/Synthesis)
    # =========================================================================

    def evaluate_text_payload(self, payload: str, portfolio_metrics: dict | None = None) -> RiskDecision:
        """Evaluate an external text payload (BTC-only) and return a RiskDecision.

        The payload is expected to include BTCUSDT intraday arrays and 4h context as
        plain text. We parse it into our AgentSignal metadata and reuse GLM evaluation.
        """
        try:
            meta = parse_state_payload(payload)
        except Exception as exc:  # noqa: BLE001
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
        # Increment invocation count for runtime tracking
        self._runtime_tracker.increment()
        
        # Track timing for staleness detection
        evaluation_start_time = datetime.now(timezone.utc)
        
        try:
            # Use nof1.ai style if enabled
            if self._settings.use_nof1_style:
                prompt_messages = self._build_nof1_prompt(signals, portfolio_metrics)
            else:
                prompt_messages = self._build_prompt(signals, portfolio_metrics)
            # Capture volatility/ATR context from prompt builder for downstream validation
            context_vol = getattr(self._nof1_prompt_builder, "_current_volatility", None)
            context_atr_pct = getattr(self._nof1_prompt_builder, "_current_atr_pct", None)
            context_vol_ratio = getattr(self._nof1_prompt_builder, "_vol_ratio", None)
            context_atr_ratio = getattr(self._nof1_prompt_builder, "_atr_ratio", None)
            # Capture volatility regime for dynamic threshold
            volatility_regime = "medium"
            if hasattr(self._nof1_prompt_builder, "_vol_regime_key"):
                try:
                    volatility_regime = self._nof1_prompt_builder._vol_regime_key()
                except Exception:
                    volatility_regime = "medium"
        except Exception as exc:  # noqa: BLE001
            # Prompt generation failure should not bubble up - return a safe fallback
            logger.error("Prompt build failed: %s", exc, exc_info=True)
            return self._fallback_handler.fallback_decision(
                signals,
                f"Prompt build failed: {exc}",
            )
        
        # Extract trace_id and market timestamp from signal metadata
        trace_id = None
        market_snapshot_timestamp = None
        if signals:
            trace_id = signals[0].metadata.get("trace_id")
            # Try to get market timestamp from signal metadata
            raw_market_data = signals[0].metadata.get("raw_market_data", {})
            current_snapshots = raw_market_data.get("current_snapshots", {})
            # Use 30min snapshot timestamp as representative
            snapshot_30m = current_snapshots.get("30m", {})
            if "_time" in snapshot_30m:
                market_snapshot_timestamp = snapshot_30m["_time"]
        
        # === PRE-GLM SL/TP CHECK ===
        # Check if SL or TP hit BEFORE calling GLM (saves tokens + deterministic)
        if portfolio_metrics:
            has_position = portfolio_metrics.get("position", 0) != 0
            if has_position:
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

                # SL kontrolü
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
                        glm_confidence=100.0,  # Deterministic
                        reason_primary="SL_HIT",
                        reason_secondary=f"Price {current_price:.2f} hit SL {stop_loss:.2f}",
                        exit_validation="SL_HIT",
                        decision_timestamp=datetime.now(timezone.utc),
                    )

                # TP kontrolü
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
                        glm_confidence=100.0,  # Deterministic
                        reason_primary="TP_HIT",
                        reason_secondary=f"Price {current_price:.2f} hit TP {take_profit:.2f}",
                        exit_validation="TP_HIT",
                        decision_timestamp=datetime.now(timezone.utc),
                    )

        # === GLM COMPLETE FREEDOM MODE ===
        # No position restrictions, no confidence guardrails
        # Only safety limits: max 3000 USD per trade, max 20x leverage

        try:
            response = self._communicator.request(prompt_messages, symbol=self._symbol, trace_id=trace_id)
            if self._settings.use_nof1_style:
                decision = self._response_parser.parse_response(response, portfolio_metrics)
            else:
                decision = self._response_parser.parse_response(response, portfolio_metrics)
            
            # Critical safety check: Ensure decision is not None
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
            
            # Add timing information for staleness detection
            decision.decision_timestamp = datetime.now(timezone.utc)
            decision.market_snapshot_timestamp = market_snapshot_timestamp
            
            # Capture GLM response latency from response metadata
            if '_glm_latency_ms' in response:
                decision.glm_response_time_ms = response['_glm_latency_ms']
                logger.info("✅ GLM latency captured: %.0fms", decision.glm_response_time_ms)
            
            # Attach market context used during prompt build for executor-side validation
            decision.context_volatility = context_vol
            decision.context_atr_pct = context_atr_pct
            decision.context_vol_ratio = context_vol_ratio
            decision.context_atr_ratio = context_atr_ratio
            decision.volatility_regime = volatility_regime

            # DEBUG: Log context values being attached to decision
            logger.info(
                "📊 Context attached to decision (id=%s): atr_pct=%s, volatility=%s",
                id(decision), context_atr_pct, context_vol
            )

            # Log timing information
            evaluation_duration = (datetime.now(timezone.utc) - evaluation_start_time).total_seconds()
            if evaluation_duration > 90:
                logger.warning(
                    "⚠️ GLM evaluation took %.1fs - market may have moved significantly",
                    evaluation_duration
                )
            
            # === APPLY ONLY SAFETY LIMITS ===
            # Max 3000 USD per trade, Max 20x leverage
            decision = SafetyLimits.apply(decision, portfolio_metrics)
            
            # === CONSISTENCY VALIDATION (Feature Flag) ===
            # Validate that thought_process synthesis aligns with signal
            original_action = decision.action
            if self._settings.zai.enable_thought_process and decision.thought_process:
                if self._settings.zai.enable_consistency_validation:
                    is_consistent, consistency_reason = ConsistencyValidator.validate(
                        decision.thought_process, decision.action
                    )
                    decision.thought_process.is_consistent = is_consistent
                    decision.thought_process.consistency_reason = consistency_reason
                    self._parsing_metrics["consistency_checks_performed"] += 1

                    if not is_consistent:
                        self._parsing_metrics["consistency_mismatches"] += 1
                        logger.warning(
                            "⚠️ CONSISTENCY MISMATCH: Signal %s conflicts with synthesis - %s",
                            decision.action, consistency_reason
                        )

                        # Force HOLD if enforcement is enabled
                        if self._settings.zai.enable_consistency_enforcement:
                            self._parsing_metrics["consistency_forced_holds"] += 1
                            # Send Telegram notification
                            ConsistencyValidator.notify_mismatch(
                                self._symbol,
                                original_action,
                                consistency_reason,
                                decision.thought_process.synthesis_verdict
                            )
                            # Force to HOLD
                            decision = RiskDecision(
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

            # === CONFIDENCE THRESHOLD ENFORCEMENT ===
            # Dynamic threshold based on volatility (Feature Flag)
            if self._settings.zai.enable_dynamic_threshold:
                threshold = DynamicThreshold.get_threshold(decision.volatility_regime)
                logger.info(
                    "📊 Dynamic threshold: %.1f%% (regime=%s)",
                    threshold, decision.volatility_regime
                )
            else:
                threshold = 80.0  # Default hardcoded threshold

            if decision.action in ["BUY", "SELL"] and decision.glm_confidence < threshold:
                logger.warning(
                    "⚠️ CONFIDENCE TOO LOW: GLM wanted %s with confidence %.1f%% < %.1f%% → Forcing HOLD",
                    decision.action,
                    decision.glm_confidence,
                    threshold
                )

                # Force decision to HOLD (preserve context attributes!)
                decision = RiskDecision(
                    action="HOLD",
                    amount=0.0,
                    reasoning=f"GLM confidence ({decision.glm_confidence:.1f}%) below {threshold:.0f}% threshold - forced to HOLD for risk management",
                    leverage=decision.leverage,
                    glm_confidence=decision.glm_confidence,
                    reason_primary=decision.reason_primary,
                    reason_secondary="Confidence threshold not met",
                    glm_response_time_ms=decision.glm_response_time_ms,
                    exit_plan=None,  # No position opened, no exit plan needed
                    decision_timestamp=decision.decision_timestamp,
                    market_snapshot_timestamp=decision.market_snapshot_timestamp,
                    context_volatility=decision.context_volatility,
                    context_atr_pct=decision.context_atr_pct,
                    context_vol_ratio=decision.context_vol_ratio,
                    context_atr_ratio=decision.context_atr_ratio,
                    thought_process=decision.thought_process,
                    volatility_regime=decision.volatility_regime,
                )
            
            # Log successful GLM decision
            logger.info(
                "GLM decision: action=%s amount=%.4f leverage=%.2f confidence=%.1f",
                decision.action,
                decision.amount,
                decision.leverage,
                decision.glm_confidence,
            )
            
            # Log GLM reasons
            if decision.reason_primary:
                logger.info("  Primary reason: %s", decision.reason_primary)
            if decision.reason_secondary:
                logger.info("  Secondary reason: %s", decision.reason_secondary)
            
            # LOG SIGNAL: Record decision with all risk scores
            signal_summary = self._log_signal(decision, signals[0] if signals else None, portfolio_metrics)
            
            return decision
            
        except Exception as exc:  # noqa: BLE001
            error_msg = f"GLM API hatası: {str(exc)}"
            logger.error("GLM request failed: %s", exc, exc_info=True)
            
            # Send Telegram notification about GLM failure
            self._fallback_handler.notify_glm_failure(signals, str(exc))
            
            fallback_decision = self._fallback_handler.fallback_decision(signals, error_msg)
            logger.warning(
                "Using fallback decision: action=%s amount=%.4f leverage=%.2f",
                fallback_decision.action,
                fallback_decision.amount,
                fallback_decision.leverage,
            )
            
            # LOG SIGNAL: Record fallback decision
            self._log_signal(fallback_decision, signals[0] if signals else None, portfolio_metrics)
            
            return fallback_decision

    def _build_prompt(self, signals: List[AgentSignal], portfolio_metrics: dict = None) -> List[dict[str, str]]:
        """Build regular prompt (NOT NOF1.AI format) - asks for natural language response."""
        
        if not signals:
            return []
        
        signal = signals[0]
        raw_market_data = signal.metadata.get("raw_market_data", {})
        htf_analysis = signal.metadata.get("htf_analysis")
        
        # Use regular prompt format (not NOF1.AI JSON)
        if raw_market_data:
            content = self._build_regular_prompt(
                raw_market_data=raw_market_data,
                portfolio_metrics=portfolio_metrics or {},
                htf_analysis=htf_analysis,
            )
            
            return [{"role": "user", "content": content}]
        
        # Fallback for old format (should not happen with PureDataCollector)
        logger.warning("No raw_market_data found, using fallback prompt")
        historical_data = signal.metadata.get("historical_data", {})
        feature_snapshot = signal.metadata.get("feature_snapshot", {})
        
        # Extract timeframe data
        intraday_1m = historical_data.get("intraday_1m", {})
        medium_15min = historical_data.get("medium_15min", {})
        main_30min = historical_data.get("main_30min", {})
        longterm_4h = historical_data.get("longterm_4h", {})
        
        # Get symbol from metadata or default to BTCUSDT (for backward compatibility)
        symbol = raw_market_data.get("symbol", "BTCUSDT")
        
        # Helper to get latest value from array
        def get_latest(data_dict, key, default=0):
            values = data_dict.get(key, [])
            return values[-1] if values else default
        
        # Helper to format arrays
        def format_array(values, decimals=2):
            if not values:
                return "[]"
            formatted = [f"{v:.{decimals}f}" if isinstance(v, float) else str(v) for v in values]
            return "[" + ", ".join(formatted) + "]"
        
        # Current market state (from 30min main timeframe)
        current_price = get_latest(main_30min, "close", 0)
        current_ema20 = get_latest(main_30min, "ema_20", 0)
        current_ema50 = get_latest(main_30min, "ema_50", 0)
        current_macd = get_latest(main_30min, "macd", 0)
        current_rsi = get_latest(main_30min, "rsi_14", 50)
        
        # 4h context
        ema20_4h = get_latest(longterm_4h, "ema_20", 0)
        ema50_4h = get_latest(longterm_4h, "ema_50", 0)
        volume_4h = get_latest(longterm_4h, "volume", 0)
        atr_4h = get_latest(longterm_4h, "atr_14", 0)
        
        # Build DeepSeek-style prompt
        content_lines = [
            f"{symbol} MULTI-TIMEFRAME TRADING ANALYSIS",
            "",
            "ALL DATA BELOW IS ORDERED: OLDEST → NEWEST",
            "",
            "=== CURRENT MARKET STATE ===",
            "",
            f"current_price = {current_price:.2f}",
            f"current_ema20 = {current_ema20:.2f}",
            f"current_ema50 = {current_ema50:.2f}",
            f"current_macd = {current_macd:.2f}",
            f"current_rsi (14-period) = {current_rsi:.2f}",
            "",
        ]
        # Portfolio context (if available)
        if portfolio_metrics:
            equity = portfolio_metrics.get("equity", 10000)
            position = portfolio_metrics.get("position", 0)
            pnl = portfolio_metrics.get("total_pnl", 0)
            pnl_pct = (pnl / 10000) * 100 if pnl else 0
            
            # Determine position type
            position_type = "FLAT (no position)"
            if position > 0.0001:
                position_type = "LONG (positive BTC)"
            elif position < -0.0001:
                position_type = "SHORT (negative BTC)"
            
            # Get exit plan info if available
            exit_plan = portfolio_metrics.get("exit_plan")
            entry_price = portfolio_metrics.get("entry_price")
            
            content_lines.extend([
                "=== CURRENT PORTFOLIO (CRITICAL!) ===",
                "",
                f"  Equity: ${equity:,.2f}",
                f"  Position: {position:+.6f} {symbol} ({position_type})",
                f"  Total PnL: ${pnl:,.2f} ({pnl_pct:+.2f}%)",
                "",
            ])
            
            # Add exit plan info if position exists (Nof1.ai style)
            if abs(position) > 0.0001 and exit_plan:
                profit_target = exit_plan.get("profit_target")
                stop_loss = exit_plan.get("stop_loss")
                invalidation_condition = exit_plan.get("invalidation_condition", "")
                current_price_val = get_latest(main_30min, "close", 0)
                
                content_lines.extend([
                    "=== EXIT PLAN (NOF1.AI STYLE) ===",
                    "",
                    f"  Entry Price: ${entry_price:,.2f}" if entry_price else "",
                    f"  Current Price: ${current_price_val:,.2f}",
                    f"  Unrealized PnL: ${pnl:,.2f} ({pnl_pct:+.2f}%)",
                    "",
                    f"  Stop Loss: ${stop_loss:,.2f}" if stop_loss else "  Stop Loss: N/A",
                    f"  Invalidation Condition: {invalidation_condition}",
                    "",
                    "⚠️ DECISION RULES:",
                    "  - Check if current price has hit stop_loss or invalidation_condition",
                    "  - If exit plan conditions NOT met → HOLD (even if indicators show concern)",
                    "  - If stop_loss or invalidation_condition met → CLOSE (automatic)",
                    "  - Technical indicators (RSI, MACD) are for CONTEXT only, not for overriding exit plan",
                    "",
                ])
            
            content_lines.extend([
                "⚠️ POSITION MANAGEMENT RULES:",
                f"  - If position is LONG ({position:.6f} {symbol}) and market shows bearish signals → Use CLOSE action (NOT SELL!)",
                f"  - If position is SHORT ({position:.6f} {symbol}) and market shows bullish signals → Use CLOSE action (NOT BUY!)",
                f"  - If position is FLAT (0 {symbol}) → You can open new LONG (BUY) or SHORT (SELL)",
                "",
                "📌 IMPORTANT: Use CLOSE to exit position directly, not BUY/SELL!",
                "",
                "🎯 NOF1.AI HOLDING STEADY STRATEGY:",
                "  - If position exists and has small negative PnL (< 5%) but stop-loss NOT triggered → HOLD, do NOT close!",
                "  - Do NOT open new position if current position exists! Wait for current position to close first.",
                "  - Trust your exit plan! Small fluctuations are normal, do not panic.",
                "",
                "⚠️ CLOSE ACTION KURALLARI (ÇOK KATI - ERKEN KAPATMAYI ÖNLEMEK İÇİN):",
                "",
                "❌ CLOSE YAPMA EĞER:",
                "  - Küçük negatif PnL (<%5) ve stop-loss tetiklenmemiş → MUTLAKA HOLD",
                "  - Kârlı pozisyon (>%2) ve sadece 1-2 timeframe'de reversal → HOLD (tüm timeframe'ler gerekli)",
                "  - Kârlı pozisyon ve confidence <%95 → HOLD (çok yüksek güven gerekli)",
                "  - Küçük fiyat dalgalanmaları (%3-5) → HOLD (normal dalgalanmalar)",
                "  - Tek bir indikatörün sinyali → HOLD (tüm timeframe'lerde reversal gerekli)",
                "",
                "✅ CLOSE YAP SADECE EĞER:",
                "  - Stop-loss tetiklendi (otomatik kapanır zaten - bu durumda CLOSE gerekmez)",
                "  - TÜM timeframe'lerde (1m, 30m, 4h) güçlü trend reversal + confidence ≥%95",
                "  - Pozisyon zararlı (<-%5) ve trend reversal tüm timeframe'lerde + confidence ≥%95",
                "  - Kârlı pozisyon (>%2) için: TÜM timeframe'lerde reversal + confidence ≥%98 (daha katı)",
                "",
                "🔍 CLOSE İÇİN MUTLAKA GEREKLİ KOŞULLAR:",
                "  1. Tüm timeframe'lerde (1m, 30m, 4h) trend reversal OLMALI",
                "  2. Minimum confidence: %95 (çok yüksek güven gerekli)",
                "  3. Pozisyon kârlıysa (>%2): Minimum confidence %98 (daha katı)",
                "  4. Küçük negatif PnL (<%5) ve stop-loss tetiklenmemişse → MUTLAKA HOLD, CLOSE YAPMA",
                "",
            ])
        
        # Futures data (from agent snapshot if available)
        lsr = feature_snapshot.get("long_short_ratio") if feature_snapshot else None
        oi = feature_snapshot.get("open_interest") if feature_snapshot else None
        fr = feature_snapshot.get("funding_rate") if feature_snapshot else None
        content_lines.append("Futures Market Data:")
        content_lines.append(f"  Long/Short Ratio: {lsr:.4f}" if lsr is not None else "  Long/Short Ratio: N/A")
        content_lines.append(f"  Open Interest: {oi:.2f}" if oi is not None else "  Open Interest: N/A")
        content_lines.append(f"  Funding Rate: {fr:.6f}" if fr is not None else "  Funding Rate: N/A")
        content_lines.append("")
        
        # INTRADAY 1min
        content_lines.extend([
            "=== INTRADAY (1-minute intervals, oldest → latest) ===",
            "",
            "Last 10 minutes:",
            "",
        ])
        
        if intraday_1m:
            content_lines.append(f"Prices:  {format_array(intraday_1m.get('close', []))}")
            content_lines.append(f"EMA(20): {format_array(intraday_1m.get('ema_20', []))}")
            content_lines.append(f"MACD:    {format_array(intraday_1m.get('macd', []))}")
            content_lines.append(f"RSI(14): {format_array(intraday_1m.get('rsi_14', []))}")
        else:
            content_lines.append("No 1m data available")
        
        content_lines.append("")
        
        # MEDIUM 15min
        content_lines.extend([
            "=== MEDIUM TIMEFRAME (15-minute intervals, oldest → latest) ===",
            "",
            "Last 2.5 hours:",
            "",
        ])
        
        if medium_15min:
            content_lines.append(f"Prices:     {format_array(medium_15min.get('close', []))}")
            content_lines.append(f"EMA(20):    {format_array(medium_15min.get('ema_20', []))}")
            content_lines.append(f"EMA(50):    {format_array(medium_15min.get('ema_50', []))}")
            content_lines.append(f"MACD:       {format_array(medium_15min.get('macd', []))}")
            content_lines.append(f"RSI(14):    {format_array(medium_15min.get('rsi_14', []))}")
            content_lines.append(f"Stoch(K):   {format_array(medium_15min.get('stoch_k', []))}")
            content_lines.append(f"ATR(14):    {format_array(medium_15min.get('atr_14', []))}")
            content_lines.append(f"MFI:        {format_array(medium_15min.get('mfi', []))}")
        else:
            content_lines.append("No 15m data available")
        
        content_lines.append("")
        
        # MAIN 30min
        content_lines.extend([
            "=== MAIN TIMEFRAME (30-minute intervals, oldest → latest) ===",
            "",
            "Last 5 hours:",
            "",
        ])
        
        if main_30min:
            content_lines.append(f"Prices:     {format_array(main_30min.get('close', []))}")
            content_lines.append(f"EMA(20):    {format_array(main_30min.get('ema_20', []))}")
            content_lines.append(f"EMA(50):    {format_array(main_30min.get('ema_50', []))}")
            content_lines.append(f"MACD:       {format_array(main_30min.get('macd', []))}")
            content_lines.append(f"RSI(14):    {format_array(main_30min.get('rsi_14', []))}")
            content_lines.append(f"Stoch(K):   {format_array(main_30min.get('stoch_k', []))}")
            content_lines.append(f"ATR(14):    {format_array(main_30min.get('atr_14', []))}")
            content_lines.append(f"MFI:        {format_array(main_30min.get('mfi', []))}")
        else:
            content_lines.append("No 30m data available")
        
        content_lines.append("")
        
        # LONG-TERM 4h
        content_lines.extend([
            "=== LONGER-TERM CONTEXT (4-hour timeframe) ===",
            "",
            f"20-Period EMA: {ema20_4h:.2f}",
            f"50-Period EMA: {ema50_4h:.2f}",
            f"Current Volume: {volume_4h:.2f}",
            f"14-Period ATR: {atr_4h:.2f}",
            "",
        ])
        
        if longterm_4h:
            content_lines.append(f"MACD (last 40 hours): {format_array(longterm_4h.get('macd', []))}")
            content_lines.append(f"RSI(14): {format_array(longterm_4h.get('rsi_14', []))}")
        
        content_lines.append("")
        
        # HTF SUPPORT/RESISTANCE ANALYSIS
        if htf_analysis:
            content_lines.extend([
                "=== HIGHER TIMEFRAME (HTF) SUPPORT/RESISTANCE ANALYSIS ===",
                "",
            ])
            
            current_price_htf = htf_analysis.get("current_price")
            in_support = htf_analysis.get("in_support_zone", False)
            in_resistance = htf_analysis.get("in_resistance_zone", False)
            nearest_support = htf_analysis.get("nearest_support")
            nearest_resistance = htf_analysis.get("nearest_resistance")
            htf_interval = htf_analysis.get("htf_interval", "1h")
            
            if current_price_htf:
                content_lines.append(f"Current Price: ${current_price_htf:,.2f}")
            
            content_lines.append(f"HTF Interval: {htf_interval}")
            content_lines.append("")
            
            if in_support:
                content_lines.append("⚠️ PRICE IS IN SUPPORT ZONE (≈2% from low)")
                content_lines.append("  → Consider LONG opportunities (price may bounce from support)")
                if nearest_support:
                    content_lines.append(f"  → Nearest Support Level: ${nearest_support:,.2f}")
            elif in_resistance:
                content_lines.append("⚠️ PRICE IS IN RESISTANCE ZONE (≈2% from high)")
                content_lines.append("  → Consider SHORT opportunities (price may reject from resistance)")
                if nearest_resistance:
                    content_lines.append(f"  → Nearest Resistance Level: ${nearest_resistance:,.2f}")
            else:
                content_lines.append("📍 PRICE IS IN NEUTRAL ZONE")
                content_lines.append("  → Price is between support and resistance levels")
                if nearest_support:
                    content_lines.append(f"  → Nearest Support: ${nearest_support:,.2f}")
                if nearest_resistance:
                    content_lines.append(f"  → Nearest Resistance: ${nearest_resistance:,.2f}")
            
            content_lines.append("")
            content_lines.append("HTF Context:")
            content_lines.append("  - Support zones indicate potential buying opportunities")
            content_lines.append("  - Resistance zones indicate potential selling opportunities")
            content_lines.append("  - Combine HTF context with multi-timeframe momentum for better entries")
            content_lines.append("")
        
        if feature_snapshot:
            content_lines.extend([
                "=== LATEST INDICATOR SNAPSHOT (30m timeframe) ===",
                "",
            ])
            for key, value in feature_snapshot.items():
                content_lines.append(f"{key}: {value}")
            content_lines.append("")
        
        # Analysis instructions
        content_lines.extend([
            "=== ANALYSIS REQUIRED ===",
            "",
            "YOU ARE THE *SOLE* DECISION MAKER. There is NO machine-learning pre-decision.",
            "",
            "🎯 NOF1.AI DEEPSEEK STYLE DECISION PROCESS:",
            "",
            "STEP 1: CHECK EXISTING POSITION AND EXIT PLAN",
            "  - First, check if you have an existing position in BTC",
            "  - If position exists, retrieve its exit plan: {stop_loss, invalidation_condition}",
            "  - Review current price vs entry price, calculate unrealized PnL",
            "  - Check if current price has hit stop_loss or invalidation_condition",
            "",
            "STEP 2: EVALUATE EXIT PLAN CONDITIONS",
            "  - For LONG position:",
            "    * Check if price <= stop_loss → CLOSE (stop loss - automatic)",
            "    * Check if invalidation condition met (e.g., 'price closes below X on 3-minute candle') → CLOSE (automatic)",
            "  - For SHORT position:",
            "    * Check if price >= stop_loss → CLOSE (stop loss - automatic)",
            "    * Check if invalidation condition met (e.g., 'price closes above X on 3-minute candle') → CLOSE (automatic)",
            "",
            "STEP 3: TECHNICAL INDICATORS (FOR CONTEXT ONLY)",
            "  - Review RSI, MACD, EMA indicators",
            "  - Check if oversold/overbought signals",
            "  - **IMPORTANT**: Technical indicators are for CONTEXT, NOT for overriding exit plan!",
            "  - Even if RSI shows oversold, if invalidation condition NOT triggered → HOLD",
            "  - Example: BTC RSI oversold (29.7) but invalidation is 105000, current price 109967 → HOLD",
            "",
            "STEP 4: DECISION LOGIC",
            "  - If exit plan conditions met → CLOSE",
            "  - If exit plan conditions NOT met → HOLD (even if indicators show concern)",
            "  - If no position exists → Can BUY/SELL based on opportunity",
            "  - **CRITICAL**: Do NOT close position just because indicators look bad!",
            "  - **TRUST YOUR EXIT PLAN**: It was set when opening position for a reason",
            "",
            "STEP 5: REASONING FORMAT",
            "  - For HOLD: Explain why exit plan conditions not met",
            f"    Example: '{symbol} position holding steady. Current price 109967, entry 107343, unrealized PnL +314.94. '",
            "            'Exit plan: stop_loss 102026 (not hit), invalidation below 105000 (not triggered). '",
            "            'RSI oversold at 29.7 but invalidation condition not met, so holding per exit plan.'",
            "  - For CLOSE: Explain which exit plan condition was triggered OR why market condition invalidates the trade",
            "    Example: '{symbol} position closing. Stop-loss triggered at 102026, current price 101800.'",
            "    Example: 'Closing LONG position because market flipped to bearish (EMA crossover + RSI breakdown). Protecting capital.'",
            "",
            "Using the data above, determine the optimal trade action:",
            "",
            "1. MOMENTUM ALIGNMENT:",
            "   - Are 1m, 15m, 30m, and 4h timeframes showing aligned momentum?",
            "   - Is momentum strengthening or weakening?",
            "   - Any divergence between timeframes?",
            "",
            "2. TREND IDENTIFICATION:",
            "   - 4h: EMA20 vs EMA50 → Overall trend direction",
            "   - 30m: MACD improving or deteriorating?",
            "   - 15m: Medium-term momentum confirmation",
            "   - 1m: Short-term momentum shifts?",
            "",
            "3. ENTRY TIMING (Critical!):",
            "   - 1m RSI: Overbought (>70) or Oversold (<30)?",
            "   - If 30m says BUY but 1m RSI >70 → Wait for pullback",
            "   - If 30m says BUY and 1m RSI <30 → Perfect entry",
            "",
            "4. RISK ASSESSMENT:",
            "   - Volatility level (check ATR)",
            "   - Stochastic position (overbought/oversold)",
            "   - Volume confirmation",
            "",
            "5. CONFLUENCE CHECK:",
            "   - All timeframes bullish → Strong BUY",
            "   - Mixed signals → HOLD or low confidence",
            "   - 4h bearish but 30m bullish → Risky, wait for 4h confirmation",
            "",
            "6. HTF CONTEXT (if available):",
            "   - If price is in support zone → Favor LONG entries",
            "   - If price is in resistance zone → Favor SHORT entries",
            "   - Combine HTF levels with multi-timeframe momentum for better entries",
            "",
            'DECISION OUTPUT (STRUCTURED JSON):',
            '{',
            '  "karar": "BUY|SELL|CLOSE|HOLD",',
            '  "miktar": 0.5,  // BUY/SELL: Equity allocation (no 1.0 cap). System enforces $3000 margin + 20x leverage (max ~$60k position). CLOSE: 0.0-1.0 close ratio, 1.0=100%.',
            '  "kaldıraç": 10,  // 5-20x leverage (ignored for CLOSE)',
            '  "ai_confidence": 85,  // 0-100: Your confidence in this decision (0=very uncertain, 100=very confident)',
            '  "reason_primary": "Most important factor (e.g., RSI shows clear divergence on 4h chart)",',
            '  "reason_secondary": "Second most important factor (e.g., Funding rate slightly negative - short squeeze risk)",',
            '  "gerekçe": "Detailed Turkish analysis: multi-timeframe confluence, entry timing, risk level"',
            '}',
            '',
            '⚠️ CONFIDENCE SCORE GUIDELINES (ai_confidence):',
            '  - 90-100: Perfect alignment, all indicators agree, clear trend',
            '  - 70-89: Strong signal, 2/3 timeframes aligned, most indicators agree',
            '  - 50-69: Moderate signal, some disagreement but reasonable opportunity',
            '  - 30-49: Weak signal, mixed indicators, high uncertainty',
            '  - 0-29: Very uncertain, conflicting signals, should probably HOLD',
            '',
            '⚠️ REASONS (reason_primary & reason_secondary):',
            '  - Must be specific and data-driven (not generic)',
            '  - Reference actual indicator values and timeframe alignment',
            '  - Example primary: "RSI shows clear divergence on 4h chart (RSI=28, price making lower lows)"',
            '  - Example secondary: "Funding rate slightly negative (-0.0002) indicates short squeeze risk"',
            '  - Keep each reason under 100 characters for clarity',
            '',
            'ACTION TYPES:',
            '  - BUY: Open new LONG position or add to existing LONG',
            '  - SELL: Open new SHORT position or add to existing SHORT',
            '  - CLOSE: Close existing position (LONG or SHORT) - NO new position opened!',
            '  - HOLD: Do nothing, wait for better opportunity',
            '',
            'WHEN TO USE CLOSE (ÇOK KATI KURALLAR):',
            '',
            '❌ CLOSE YAPMA EĞER:',
            '  - Küçük negatif PnL (<%5) ve stop-loss tetiklenmemiş → MUTLAKA HOLD',
            '  - Kârlı pozisyon (>%2) ve sadece 1-2 timeframe\'de reversal → HOLD',
            '  - Kârlı pozisyon ve confidence <%98 → HOLD',
            '  - Küçük fiyat dalgalanmaları (%3-5) → HOLD',
            '',
            '✅ CLOSE YAP SADECE EĞER:',
            '  - TÜM timeframe\'lerde (1m, 30m, 4h) güçlü trend reversal + confidence ≥%95',
            '  - Pozisyon zararlı (<-%5) ve trend reversal tüm timeframe\'lerde + confidence ≥%95',
            '  - Kârlı pozisyon (>%2) için: TÜM timeframe\'lerde reversal + confidence ≥%98',
            '',
            '⚠️ CLOSE İÇİN MUTLAKA GEREKLİ:',
            '  1. Tüm timeframe\'lerde (1m, 30m, 4h) trend reversal',
            '  2. Minimum confidence: %95 (kârlı pozisyonlar için %98)',
            '  3. Küçük negatif PnL (<%5) ve stop-loss tetiklenmemişse → HOLD, CLOSE YAPMA',
            '',
            '  - CLOSE directly exits position without opening opposite direction trade',
            '',
            "🎯 NOF1.AI AGGRESSIVE TRADING STYLE: COMPLETE FREEDOM",
            "  **PRINCIPLE**: Take opportunities when they arise, be decisive and confident!",
            "",
            "  ✅ TRADING FREEDOM:",
            "    1. OPEN positions freely when you see opportunities - don't hesitate!",
            "    2. CLOSE positions when trend changes - be decisive!",
            "    3. You can OPEN new positions immediately after closing - no waiting required!",
            "    4. Trade actively based on technical analysis and market conditions",
            "    5. Use your judgment - you are the expert trader!",
            "",
            '  - Don\'t chase the market immediately after exiting',
            '',
            '⚠️ HIGH QUALITY TRADES ONLY - BE SELECTIVE!',
            '',
            '✅ TRADE WHEN REASONABLE CONDITIONS ARE MET:',
            '  1. At least 2/3 timeframes (1m/30m/4h) ALIGNED in same direction',
            '  2. At least 3/5 agreeing indicators (allow some disagreement)',
            '  3. Clear trend direction preferred, but can work in early trend stages',
            '  4. Momentum indicators reasonable (RSI 30-70 range acceptable)',
            '  5. Volatility manageable (high volatility = smaller position)',
            '',
            '❌ USE HOLD WHEN:',
            '  - All 3 timeframes CONFLICTING (strong disagreement)',
            '  - Market is EXTREMELY CHOPPY/SIDEWAYS (no structure at all)',
            '  - Most indicators CONFLICTING (4+ disagreeing)',
            '  - RSI EXTREMELY NEUTRAL (45-55 with no movement for hours)',
            '  - Price whipsawing rapidly without structure',
            '  - Volatility at extreme panic levels (ATR > 2x normal)',
            '  - Major news/event causing total uncertainty',
            '',
            '📊 BALANCED PHILOSOPHY:',
            '  ⭐ Quality trades are preferred but reasonable opportunities count',
            '  ⭐ 2-3 good trades per day > One perfect trade',
            '  ⭐ If you\'re unsure → ANALYZE THE DATA and make best decision!',
            '  ⭐ Missing some opportunities is OK, but being too passive misses trends',
            '  ⭐ Small position with good probability > No position with perfect setup',
            '',
            '🎯 POSITION SIZING - BALANCED APPROACH:',
            '  - miktar: 0.20-0.25 → Use this for MOST trades (20-25%)',
            '  - miktar: 0.25-0.30 → For STRONG signals with good setup (25-30%)',
            '  - miktar: 0.15-0.20 → For lower confidence setups (15-20%)',
            '  - miktar: 0.35+ → RARELY! Only when ALL indicators perfectly aligned',
            '  - Default approach: Start with 0.25 (25%) for normal trades',
        ])
        
        prompt_content = "\n".join(content_lines)
        logger.debug("GLM prompt length: %d chars", len(prompt_content))
        
        return [
            {
                "role": "system",
                "content": "Sen profesyonel bir kripto türev piyasası risk yöneticisisin. **NOF1.AI DEEPSEEK STYLE: HOLDING STEADY STRATEGY** - Pozisyonları sabit tut, küçük dalgalanmalarda panik yapma! **MULTI-TIMEFRAME ANALİZİ ÖNCELİKLİ**: 3 farklı timeframe'i (1m intraday, 30m ana, 4h uzun vade) değerlendiriyorsun. Önce tüm timeframe'lerin uyumunu kontrol et - hepsi aynı yönde mi? Sonra 25 indikatörü (Futures + Momentum + Trend + Volatilite) analiz et. **ZAMAN SERİSİ ANALİZİ**: Her timeframe için son 10 verilik dizilere bakarak trend değişimlerini, dip/tepe formasyonlarını ve momentum dönüşlerini tespit et. **POZİSYON YÖNETİMİ KURALLARI** (ÇOK ÖNEMLİ!): (1) **HOLDING STEADY PRINCIPLE**: Mevcut pozisyon varsa ve küçük negatif PnL'de (< %5) ama stop-loss tetiklenmemişse → MUTLAKA **HOLD** kararı ver, CLOSE yapma! (2) **CLOSE KARARI**: Sadece exit plan'daki stop-loss veya invalidation_condition tetiklendiğinde (otomatik kapanır) veya güçlü trend değişimi tüm timeframe'lerde doğrulandığında CLOSE yap. Küçük dalgalanmalarda panik yapma! (3) Mevcut pozisyon LONG (+pozitif BTC miktarı) ve piyasa düşüş sinyali veriyorsa → Exit plan stop-loss tetiklenmedikçe **HOLD**, sadece ciddi trend değişimi varsa **CLOSE**. (4) Mevcut pozisyon SHORT (-negatif BTC miktarı) ve piyasa yükseliş sinyali veriyorsa → Exit plan stop-loss tetiklenmedikçe **HOLD**, sadece ciddi trend değişimi varsa **CLOSE**. (5) **CLOSE action**: Mevcut pozisyonu DİREK kapatır, karşı yönde yeni pozisyon AÇMAZ. Miktar: tam kapatma için 1.0, kısmi kapatma için 0.5-0.8 kullan. (6) **BUY/SELL action**: Sadece YENİ pozisyon açmak veya mevcut pozisyonu artırmak için kullan. Mevcut pozisyon varsa yeni pozisyon açma! (7) Pozisyon = 0 ise BUY/SELL ile yeni pozisyon açabilirsin. Gerekçende MUTLAKA belirt: (1) Timeframe uyumu (1m/30m/4h), (2) Her timeframe'deki pattern'ler, (3) Momentum göstergeleri (RSI, WillR, MFI, CCI, Stoch), (4) Trend göstergeleri (EMA, MACD, SAR), (5) Volatilite (ATR, BB), (6) Futures metrikleri (L/S, FR, OI), (7) **Mevcut pozisyon durumu ve holding steady kararı - Exit plan tetiklenmiş mi? Küçük negatif PnL'de mi?**. Timeframe'ler uyumluysa yüksek güven, uyumsuzsa HOLD veya düşük güven ver."
            },
            {"role": "user", "content": prompt_content},
        ]

    def _build_regular_prompt(
        self,
        raw_market_data: Dict[str, Any],
        portfolio_metrics: Dict[str, Any],
        htf_analysis: Dict[str, Any] = None,
    ) -> str:
        """Build regular prompt that asks for natural language response (not JSON)"""
        
        # Use the NOF1 prompt builder but modify the instructions to ask for natural language
        content = self._nof1_prompt_builder.build_prompt(
            raw_market_data=raw_market_data,
            portfolio_metrics=portfolio_metrics,
            htf_analysis=htf_analysis,
        )
        
        # Replace the JSON instructions with natural language instructions
        # We use a split/join approach because the JSON instructions might contain dynamic values now
        split_marker = "OUTPUT FORMAT (JSON ONLY)"
        if split_marker in content:
            base_content = content.split(split_marker)[0]
            
            new_instructions = """OUTPUT FORMAT:

Respond with a clear, concise decision in natural language. Start with your action:

ACTION: BUY | SELL | HOLD | CLOSE

Then provide your reasoning in 1-2 sentences.

EXAMPLES:
ACTION: HOLD
Market showing mixed signals with RSI at neutral levels, better to wait for clearer direction.

ACTION: BUY  
RSI oversold at 28 with bullish divergence on 4h timeframe, good risk/reward for long entry at current levels.

ACTION: CLOSE
Stop-loss triggered at 105000, current price 104800. Exiting position to limit further losses.

IMPORTANT:
- Keep responses concise and actionable
- Focus on the most important factors
- Mention key indicator levels if relevant
- Consider your current position and exit plan"""
            
            return base_content + new_instructions
            
        return content

    # --- Fallback & Guardrail Helpers ---
    # NOTE: Prompt/Response logging moved to GLMCommunicator module

    def _log_signal(
        self,
        decision: RiskDecision,
        signal: AgentSignal | None,
        portfolio_metrics: dict | None,
    ) -> str:
        """
        Log trading signal with all risk scores and decision factors
        
        Args:
            decision: RiskDecision object
            signal: AgentSignal object (if available)
            portfolio_metrics: Portfolio state dict
            
        Returns:
            Summary string for Telegram notification
        """
        try:
            if signal is None:
                logger.warning("No signal available for logging")
                return ""
            
            # Extract latency metrics from signal metadata
            latency_metrics = {}
            if signal.metadata:
                trace_id = signal.metadata.get('trace_id')
                if trace_id:
                    # Try to get latency from tracker
                    try:
                        from app.utils.latency import get_latency_tracker
                        tracker = get_latency_tracker()
                        trace = tracker.get_trace(trace_id)
                        if trace:
                            latency_metrics = trace.get_latencies()
                    except Exception:
                        pass
            
            # Create and log signal
            signal_log = self._signal_logger.create_signal_from_decision(
                decision=decision,
                signal=signal,
                symbol=self._symbol,  # Pass symbol explicitly
                portfolio_metrics=portfolio_metrics,
                latency_metrics=latency_metrics,
            )
            
            self._signal_logger.log_signal(signal_log)
            
            # Return summary for Telegram
            return signal_log.get_summary()
            
        except Exception as e:
            logger.error("Failed to log signal: %s", e, exc_info=True)
            return ""
    
    def _send_analysis_summary(
        self,
        decision: RiskDecision,
        signals: List[AgentSignal],
        portfolio_metrics: dict | None,
        bias_reliability_data: Dict[str, Any] | None,
        signal_summary: str,
    ) -> None:
        """
        Send consolidated analysis summary to Telegram
        
        Args:
            decision: RiskDecision object
            signals: List of agent signals
            portfolio_metrics: Portfolio state
            bias_reliability_data: Bias reliability test results
            signal_summary: Signal log summary
        """
        try:
            from app.utils.telegram import telegram_client
            
            if not telegram_client.enabled():
                return
            
            lines = ["🔄 *ANALİZ DÖNGÜSÜ TAMAMLANDI*", ""]
            
            # GLM Latency
            if decision.glm_response_time_ms > 0:
                lines.extend([
                    "⏱️ *GLM Latency*",
                    f"  Yanıt Süresi: {decision.glm_response_time_ms:.0f}ms",
                    ""
                ])
            
            # Bias Reliability Test
            if bias_reliability_data:
                status_emoji = bias_reliability_data["reliability_color"]
                status = bias_reliability_data["reliability_status"].replace("_", " ")
                lines.extend([
                    "📊 *Bias Güvenilirlik Testi*",
                    f"  Status: {status} {status_emoji}",
                    f"  GLM Güven: {bias_reliability_data['glm_confidence']:.1f}%",
                    f"  Bias Güven: {bias_reliability_data['bias_confidence']:.1f}%",
                    f"  Composite Bias: {bias_reliability_data['composite_bias']:+.2f}",
                    f"  Güven Farkı: {bias_reliability_data['confidence_diff']:.2f}",
                    f"  Volatilite: {bias_reliability_data['volatility_regime']:.2f}",
                    ""
                ])
            
            # GLM Decision
            action_emoji = "🟢" if decision.action == "BUY" else "🔴" if decision.action == "SELL" else "⚪"
            lines.extend([
                "🎯 *GLM Kararı*",
                f"  Action: {decision.action} {action_emoji}",
                f"  Amount: {decision.amount:.4f}",
                f"  Leverage: {decision.leverage:.2f}x",
                f"  Güven: {decision.glm_confidence:.1f}%",
                ""
            ])
            
            # GLM Reasons
            if decision.reason_primary or decision.reason_secondary:
                lines.append("📝 *Gerekçeler*")
                if decision.reason_primary:
                    lines.append(f"  ▸ Ana: {decision.reason_primary}")
                if decision.reason_secondary:
                    lines.append(f"  ▸ İkincil: {decision.reason_secondary}")
                lines.append("")
            
            # Signal Summary
            if signal_summary:
                lines.extend([
                    "💾 *Signal Kaydedildi*",
                    f"  {signal_summary}",
                    ""
                ])
            
            # Portfolio info (if available)
            if portfolio_metrics:
                equity = portfolio_metrics.get("equity", 0)
                position = portfolio_metrics.get("position", 0)
                total_pnl = portfolio_metrics.get("total_pnl", 0)
                pnl_pct = (total_pnl / 10000) * 100 if total_pnl else 0
                
                position_emoji = "🟢" if position > 0 else "🔴" if position < 0 else "⚪"
                pnl_emoji = "📈" if total_pnl > 0 else "📉" if total_pnl < 0 else "➖"
                
                lines.extend([
                    "💼 *Portfolio Durumu*",
                    f"  Equity: ${equity:,.2f}",
                    f"  Position: {position:+.6f} BTC {position_emoji}",
                    f"  PnL: ${total_pnl:,.2f} ({pnl_pct:+.2f}%) {pnl_emoji}",
                    ""
                ])
            
            # Combine all lines
            message = "\n".join(lines)
            
            # Send with auto-split
            telegram_client.send_message(message, auto_split=True)
            logger.info("Analysis summary sent to Telegram")
            
        except Exception as exc:
            logger.error("Failed to send analysis summary to Telegram: %s", exc, exc_info=True)
    
    def _calculate_sharpe_ratio(self, portfolio_metrics: dict | None) -> float:
        """Calculate Sharpe ratio from portfolio metrics."""
        if not portfolio_metrics:
            return 0.0
        
        # Try to get recent returns from executor
        try:
            from app.executor.executor import Executor
            executor = Executor(symbol="BTCUSDT")
            recent_trades = executor.get_recent_trades_with_pnl(limit=50)
            
            if not recent_trades:
                return 0.0
            
            returns = []
            for trade in recent_trades:
                if trade.get("is_closed", False):
                    pnl_pct = trade.get("pnl_pct", 0.0) or 0.0
                    if pnl_pct != 0:
                        returns.append(pnl_pct / 100.0)
            
            if len(returns) < 3:
                return 0.0
            
            mean_return = sum(returns) / len(returns)
            variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
            std_return = math.sqrt(max(variance, 1e-18))
            
            if std_return == 0:
                return 0.0
            
            # Annualize: assume daily returns
            sharpe = (mean_return / std_return) * math.sqrt(252)
            return sharpe
        except Exception:
            return 0.0
    
    def _build_nof1_prompt(self, signals: List[AgentSignal], portfolio_metrics: dict = None) -> List[dict[str, str]]:
        """Build NOF1.AI style prompt with runtime tracking and complete market data."""
        
        if not signals:
            return []
        
        signal = signals[0]
        raw_market_data = signal.metadata.get("raw_market_data", {})
        htf_analysis = signal.metadata.get("htf_analysis")
        
        # DEBUG: Log portfolio metrics content to verify multi-position support
        if portfolio_metrics:
            logger.info("🔍 DEBUG Portfolio Metrics Keys: %s", list(portfolio_metrics.keys()))
            logger.info("🔍 DEBUG long_position: %s", portfolio_metrics.get("long_position"))
            logger.info("🔍 DEBUG short_position: %s", portfolio_metrics.get("short_position"))
            logger.info("🔍 DEBUG long_exit_plan: %s", portfolio_metrics.get("long_exit_plan"))
            logger.info("🔍 DEBUG short_exit_plan: %s", portfolio_metrics.get("short_exit_plan"))
        else:
            logger.warning("⚠️ DEBUG: portfolio_metrics is None or empty!")
        
        # Use NOF1.AI prompt builder for professional format
        if raw_market_data:
            content = self._nof1_prompt_builder.build_prompt(
                raw_market_data=raw_market_data,
                portfolio_metrics=portfolio_metrics or {},
                htf_analysis=htf_analysis,
            )

            # Log prompt size
            estimated_tokens = len(content) // 4
            logger.info("📤 NOF1 GLM Prompt: %d chars, ~%d tokens", len(content), estimated_tokens)

            # GLM özgürlüğü: Minimal system message - hiçbir kural/kısıtlama yok
            system_message = "Sen bir kripto analisti. Verileri analiz et ve JSON formatında yanıt ver."

            # Prompt logging now handled automatically by GLMCommunicator

            return [
                {
                    "role": "system",
                    "content": system_message,
                },
                {"role": "user", "content": content}
            ]
        
        # Fallback for old format (should not happen with PureDataCollector)
        logger.warning("No raw_market_data in nof1 prompt, using fallback")
        historical_data = signal.metadata.get("historical_data", {})
        feature_snapshot = signal.metadata.get("feature_snapshot", {})
        
        # Get runtime info
        runtime_info = self._runtime_tracker.get_runtime_info()
        minutes_since_start = runtime_info["minutes_since_start"]
        invocation_count = runtime_info["invocation_count"]
        current_time = runtime_info["current_time"]
        
        # Extract timeframe data
        intraday_1m = historical_data.get("intraday_1m", {})
        main_30min = historical_data.get("main_30min", {})
        longterm_4h = historical_data.get("longterm_4h", {})
        
        # Helper to get latest value from array
        def get_latest(data_dict, key, default=0):
            values = data_dict.get(key, [])
            return values[-1] if values else default
        
        # Helper to format arrays (limit to 10 for nof1.ai format)
        def format_array(values, decimals=2, limit=10):
            if not values:
                return "[]"
            # Take last N values
            values = values[-limit:] if len(values) > limit else values
            formatted = [f"{v:.{decimals}f}" if isinstance(v, float) else str(v) for v in values]
            return "[" + ", ".join(formatted) + "]"
        
        # Current market state
        current_price = get_latest(main_30min, "close", 0)
        current_ema20 = get_latest(main_30min, "ema_20", 0)
        current_macd = get_latest(main_30min, "macd", 0)
        current_rsi7 = get_latest(intraday_1m, "rsi_7", 50) if intraday_1m.get("rsi_7") else get_latest(intraday_1m, "rsi_14", 50)
        current_rsi14 = get_latest(intraday_1m, "rsi_14", 50) if intraday_1m.get("rsi_14") else get_latest(main_30min, "rsi_14", 50)
        
        # Futures data
        oi = feature_snapshot.get("open_interest")
        oi_avg = feature_snapshot.get("open_interest_avg")
        fr = feature_snapshot.get("funding_rate")
        
        # 4h context
        ema20_4h = get_latest(longterm_4h, "ema_20", 0)
        ema50_4h = get_latest(longterm_4h, "ema_50", 0)
        atr_14_4h = get_latest(longterm_4h, "atr_14", 0)
        atr_3_4h = get_latest(longterm_4h, "atr_3", 0)  # ATR 3-period
        volume_4h = get_latest(longterm_4h, "volume", 0)
        
        # Calculate volume average from 4h volume array
        volume_4h_array = longterm_4h.get("volume", [])
        volume_4h_avg = sum(volume_4h_array) / len(volume_4h_array) if volume_4h_array else volume_4h
        
        # Calculate Open Interest average if not available
        if oi_avg is None and oi is not None:
            # Try to get historical OI from feature_snapshot or use current as fallback
            oi_avg = oi
        
        # Build nof1.ai style prompt
        content_lines = [
            f"It has been {minutes_since_start:.0f} minutes since you started trading. The current time is {current_time} and you've been invoked {invocation_count} times. Below, we are providing you with a variety of state data, price data, and predictive signals so you can discover alpha. Below that is your current account information, value, performance, positions, etc.",
            "",
            "",
            "ALL OF THE PRICE OR SIGNAL DATA BELOW IS ORDERED: OLDEST → NEWEST",
            "",
            "Timeframes note: Unless stated otherwise in a section title, intraday series are provided at 3‑minute intervals. If a coin uses a different interval, it is explicitly stated in that coin's section.",
            "",
            "CURRENT MARKET STATE FOR ALL COINS",
            "",
            "ALL BTC DATA",
            "",
            f"current_price = {current_price:.2f}, current_ema20 = {current_ema20:.2f}, current_macd = {current_macd:.2f}, current_rsi (7 period) = {current_rsi7:.3f}",
            "",
            "In addition, here is the latest BTC open interest and funding rate for perps (the instrument you are trading):",
            "",
        ]
        
        if oi is not None:
            oi_avg_val = oi_avg if oi_avg is not None else oi
            content_lines.append(f"Open Interest: Latest: {oi:.2f} Average: {oi_avg_val:.2f}")
        else:
            content_lines.append("Open Interest: Latest: N/A Average: N/A")
        
        if fr is not None:
            content_lines.append(f"Funding Rate: {fr:.6e}")
        else:
            content_lines.append("Funding Rate: N/A")
        
        content_lines.extend([
            "",
            "Intraday series (by minute, oldest → latest):",
            "",
        ])
        
        # Use 1m data and downsample to 3m (every 3rd value) for 10 bars
        mid_prices = intraday_1m.get("close", [])
        if mid_prices:
            # Downsample to 3m (take every 3rd)
            mid_prices_3m = mid_prices[::3][-10:] if len(mid_prices) >= 3 else mid_prices[-10:]
        else:
            mid_prices_3m = []
        
        ema20_values = intraday_1m.get("ema_20", [])
        if ema20_values:
            ema20_3m = ema20_values[::3][-10:] if len(ema20_values) >= 3 else ema20_values[-10:]
        else:
            ema20_3m = []
        
        macd_values = intraday_1m.get("macd", [])
        if macd_values:
            macd_3m = macd_values[::3][-10:] if len(macd_values) >= 3 else macd_values[-10:]
        else:
            macd_3m = []
        
        rsi7_values = intraday_1m.get("rsi_7", [])
        if not rsi7_values:
            rsi7_values = intraday_1m.get("rsi_14", [])
        if rsi7_values:
            rsi7_3m = rsi7_values[::3][-10:] if len(rsi7_values) >= 3 else rsi7_values[-10:]
        else:
            rsi7_3m = []
        
        rsi14_values = intraday_1m.get("rsi_14", [])
        if rsi14_values:
            rsi14_3m = rsi14_values[::3][-10:] if len(rsi14_values) >= 3 else rsi14_values[-10:]
        else:
            rsi14_3m = []
        
        content_lines.extend([
            f"Mid prices: {format_array(mid_prices_3m, decimals=1)}",
            f"EMA indicators (20‑period): {format_array(ema20_3m, decimals=3)}",
            f"MACD indicators: {format_array(macd_3m, decimals=2)}",
            f"RSI indicators (7‑Period): {format_array(rsi7_3m, decimals=3)}",
            f"RSI indicators (14‑Period): {format_array(rsi14_3m, decimals=3)}",
            "",
            "Longer‑term context (4‑hour timeframe):",
            "",
            f"20‑Period EMA: {ema20_4h:.3f} vs. 50‑Period EMA: {ema50_4h:.3f}",
            f"3‑Period ATR: {atr_3_4h:.2f} vs. 14‑Period ATR: {atr_14_4h:.2f}",
            f"Current Volume: {volume_4h:.3f} vs. Average Volume: {volume_4h_avg:.3f}",
            "",
        ])
        
        # 4h MACD and RSI arrays
        macd_4h = longterm_4h.get("macd", [])
        rsi_4h = longterm_4h.get("rsi_14", [])
        content_lines.extend([
            f"MACD indicators: {format_array(macd_4h, decimals=3, limit=10)}",
            f"RSI indicators (14‑Period): {format_array(rsi_4h, decimals=3, limit=10)}",
            "",
            "HERE IS YOUR ACCOUNT INFORMATION & PERFORMANCE",
            "",
        ])
        
        # Account information
        if portfolio_metrics:
            equity = portfolio_metrics.get("equity", 10000.0)
            starting_cash = portfolio_metrics.get("starting_cash", 10000.0)
            total_pnl = portfolio_metrics.get("total_pnl", 0.0)
            available_cash = portfolio_metrics.get("free_cash", equity)
            position = portfolio_metrics.get("position", 0.0)
            
            total_return_pct = ((equity - starting_cash) / starting_cash) * 100.0 if starting_cash > 0 else 0.0
            sharpe_ratio = self._calculate_sharpe_ratio(portfolio_metrics)
            
            content_lines.extend([
                f"Current Total Return (percent): {total_return_pct:.2f}%",
                f"Available Cash: {available_cash:.2f}",
                f"Current Account Value: {equity:.2f}",
                "",
            ])
            
            # Position details
            if abs(position) > 0.0001:
                entry_price = portfolio_metrics.get("entry_price", current_price)
                current_price_val = current_price
                unrealized_pnl = portfolio_metrics.get("unrealized_pnl", 0.0)
                leverage = portfolio_metrics.get("leverage", 10.0)
                exit_plan = portfolio_metrics.get("exit_plan", {})
                
                # Get order IDs and additional fields from portfolio_metrics (Nof1.ai style)
                sl_oid = portfolio_metrics.get("sl_oid", -1)
                tp_oid = portfolio_metrics.get("tp_oid", -1)
                entry_oid = portfolio_metrics.get("entry_oid", -1)
                notional_usd = portfolio_metrics.get("notional_usd", abs(position) * current_price)
                confidence = portfolio_metrics.get("confidence", 0.65)
                
                # Calculate liquidation price (simplified)
                liquidation_price = entry_price * (1 - 0.9 / leverage) if position > 0 else entry_price * (1 + 0.9 / leverage)
                
                # Risk USD (simplified)
                risk_usd = abs(position) * entry_price * 0.01  # 1% risk
                
                position_dict = {
                    "symbol": "BTC",
                    "quantity": abs(position),
                    "entry_price": entry_price,
                    "current_price": current_price_val,
                    "liquidation_price": liquidation_price,
                    "unrealized_pnl": unrealized_pnl,
                    "leverage": int(leverage) if leverage else 10,
                    "exit_plan": exit_plan,
                    "confidence": confidence if confidence is not None else 0.65,
                    "risk_usd": risk_usd,
                    "sl_oid": int(sl_oid) if sl_oid != -1 else -1,
                    "tp_oid": int(tp_oid) if tp_oid != -1 else -1,
                    "wait_for_fill": False,
                    "entry_oid": int(entry_oid) if entry_oid != -1 else -1,
                    "notional_usd": notional_usd,
                }
                
                import json as json_lib
                content_lines.append(f"Current live positions & performance: {json_lib.dumps(position_dict, indent=2)}")
            else:
                content_lines.append("Current live positions & performance: {}")
            
            content_lines.append(f"Sharpe Ratio: {sharpe_ratio:.3f}")
        
        # Instructions for GLM (simplified version of nof1.ai instructions)
        content_lines.extend([
            "",
            "",
            "Based on the above market state and account information, analyze the market and provide your trading decision.",
            "",
            "You must output your decision in JSON format:",
            "",
            '{\n  "BTCUSDT": {\n    "trade_signal_args": {\n      "coin": "BTCUSDT",\n      "signal": "hold|close_position|buy|sell",\n      "quantity": 0.12,\n      "stop_loss": 102026.675,\n      "profit_target": 115000.0,\n      "invalidation_condition": "If the price closes below 105000 on a 3-minute candle",\n      "leverage": 10,\n      "confidence": 0.75,\n      "risk_usd": 619.2345,\n      "justification": "..." // Only for entry/close\n    }\n  }\n}',
            "",
            "Signal types:",
            "- hold: Keep current position. Do NOT provide exit plan fields (use stop_loss=0, profit_target=0, invalidation_condition=\"\").",
            "- close_position: Exit current position. Do NOT provide exit plan fields (use stop_loss=0, profit_target=0, invalidation_condition=\"\").",
            "- buy/sell: Open new position. Quantity is BTC amount. MUST include stop_loss, profit_target, invalidation_condition (machine-parseable, not embedded only in text).",
            "",
            "⚠️ CRITICAL: The 'justification' field MUST be written in TURKISH.",
            "",
            "📋 JUSTIFICATION REQUIREMENTS:",
            "- Include specific numbers and indicator values from multiple timeframes (1m, 15m, 30m, 4h)",
            "- Explain WHY this specific decision at THIS exact moment",
            "- Reference current price vs support/resistance levels",
            "- Discuss volume, momentum, and trend strength",
            "- NO GENERIC PHRASES! Every sentence must contain specific data points",
            "- END justification with an exit summary line mirroring JSON values exactly: '| Stop loss: <price> | Geçersiz kılma koşulu: <invalidation> | Take profit: <price>'",
            "",
        ])
        
        prompt_content = "\n".join(content_lines)
        # Token estimate: ~4 chars per token for Turkish text
        estimated_tokens = len(prompt_content) // 4
        logger.info("📤 GLM Prompt: %d chars, ~%d tokens (estimated)", len(prompt_content), estimated_tokens)
        
        # === PROMPT GÖNDERİM ÖNCESİ DOĞRULAMA ===
        system_message = (
            "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. "
            "KRİTİK KURAL: JSON yanıtındaki 'justification' alanı MUTLAKA TÜRKÇE olmalıdır. "
            "Gerekçe detaylı ve kapsamlı olmalı (400-600 karakter). "
            "Her karar için: (1) TÜM timeframe'leri (1m, 15m, 30m, 4h) AYRI AYRI analiz et ve SAYISAL değerler ver, "
            "(2) Her timeframe için RSI, EMA20, EMA50, MACD değerlerini belirt, "
            "(3) Fiyat seviyelerini, destek/direnç noktalarını KESIN RAKAMLARLA açıkla, "
            "(4) Timeframe'ler arasındaki uyum/çelişkileri açıkla. "
            "HOLD kararları da detaylı gerekçe gerektirir. Fırsatları kaçırmaktan korkma ama her kararını KANITA DAYALI olarak açıkla!"
        )
        system_len = len(system_message)
        user_content_len = len(prompt_content)
        total_prompt_len = system_len + user_content_len
        
        logger.info(
            "📤 GLM Prompt Validation (fallback): system=%d chars, user=%d chars, total=%d chars",
            system_len,
            user_content_len,
            total_prompt_len,
        )
        
        # Prompt içeriğinin son 200 karakterini logla
        if len(prompt_content) > 200:
            logger.debug(
                "📤 User prompt (last 200 chars): %s",
                prompt_content[-200:],
            )
        
        return [
            {
                "role": "system",
                "content": system_message,
            },
            {"role": "user", "content": prompt_content},
        ]
    
    def get_parsing_metrics(self) -> dict:
        """Get current JSON parsing metrics for monitoring"""
        total = self._parsing_metrics["total_json_requests"]
        if total == 0:
            return {
                "status": "No requests processed yet",
                "metrics": self._parsing_metrics.copy()
            }

        success_rate = (self._parsing_metrics["successful_json_parsing"] / total) * 100
        error_rate = (self._parsing_metrics["json_parsing_errors"] / total) * 100
        recovery_rate = (self._parsing_metrics["signal_recoveries"] / max(1, self._parsing_metrics["json_parsing_errors"])) * 100

        return {
            "total_requests": total,
            "success_rate_percent": round(success_rate, 2),
            "error_rate_percent": round(error_rate, 2),
            "signal_recovery_rate_percent": round(recovery_rate, 2),
            "metrics": self._parsing_metrics.copy(),
            "alert_status": self._get_alert_status()
        }

    def _get_alert_status(self) -> dict:
        """Determine alert status based on metrics"""
        total = self._parsing_metrics["total_json_requests"]
        if total == 0:
            return {"level": "INFO", "message": "No data yet"}

        error_rate = (self._parsing_metrics["json_parsing_errors"] / total) * 100
        recovery_rate = (self._parsing_metrics["signal_recoveries"] / max(1, self._parsing_metrics["json_parsing_errors"])) * 100

        if error_rate > 20:
            return {
                "level": "CRITICAL",
                "message": f"High JSON error rate: {error_rate:.1f}% - GLM response format issues"
            }
        elif error_rate > 10:
            return {
                "level": "WARNING",
                "message": f"Elevated JSON error rate: {error_rate:.1f}% - Monitor closely"
            }
        elif recovery_rate < 50 and self._parsing_metrics["json_parsing_errors"] > 0:
            return {
                "level": "WARNING",
                "message": f"Low signal recovery rate: {recovery_rate:.1f}% - Fallback parsing needs improvement"
            }
        else:
            return {
                "level": "OK",
                "message": "JSON parsing performance is acceptable"
            }

    def log_parsing_metrics(self) -> None:
        """Log current parsing metrics with alert status"""
        metrics = self.get_parsing_metrics()
        alert = metrics["alert_status"]

        if alert["level"] == "CRITICAL":
            logger.critical("🚨 JSON Parsing Alert: %s", alert["message"])
        elif alert["level"] == "WARNING":
            logger.warning("⚠️ JSON Parsing Warning: %s", alert["message"])
        else:
            logger.info("✅ JSON Parsing Status: %s", alert["message"])

        logger.info("📊 Parsing Metrics: %d total, %.1f%% success, %.1f%% errors, %d signals recovered",
                   metrics["total_requests"], metrics["success_rate_percent"],
                   metrics["error_rate_percent"], metrics["metrics"]["signal_recoveries"])
