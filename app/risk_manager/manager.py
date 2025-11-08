import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.agents.base import AgentSignal
from app.config.settings import get_settings
from app.risk_manager.glm_client import GLMClient
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
from app.risk_manager.text_parser import parse_state_payload
from app.utils.logging import get_logger
from app.utils.runtime_tracker import RuntimeTracker
from app.utils.signal_logger import get_signal_logger
from app.utils.telegram import telegram_client


logger = get_logger(__name__)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _json_serializer(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


@dataclass
class RiskDecision:
    action: str
    amount: float
    reasoning: str
    leverage: float = 5.0
    glm_confidence: float = 0.0  # GLM's own confidence score (0-100)
    reason_primary: str = ""  # Primary reason for decision
    reason_secondary: str = ""  # Secondary reason for decision
    glm_response_time_ms: float = 0.0  # GLM API response time in milliseconds
    exit_plan: Optional[dict] = None  # GLM's exit plan: {profit_target, stop_loss, invalidation_condition}
    close_side: Optional[str] = None  # YENİ: "LONG" veya "SHORT" (CLOSE action için hangi pozisyon kapatılacak)
    glm_response_json: Optional[dict] = None  # GLM response JSON for Telegram notification
    # Timing and staleness detection
    decision_timestamp: Optional[datetime] = None  # When GLM made this decision
    market_snapshot_timestamp: Optional[datetime] = None  # Timestamp of market data used
    
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
            return 999999.0
        return (datetime.now(timezone.utc) - self.decision_timestamp).total_seconds()


class RiskManager:
    def __init__(self) -> None:
        self._glm = GLMClient()
        self._signal_logger = get_signal_logger()
        self._settings = get_settings()
        self._runtime_tracker = RuntimeTracker.get_instance()
        self._nof1_prompt_builder = Nof1PromptBuilder()

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
        
        # Use nof1.ai style if enabled
        if self._settings.use_nof1_style:
            prompt_messages = self._build_nof1_prompt(signals, portfolio_metrics)
        else:
            prompt_messages = self._build_prompt(signals, portfolio_metrics)
        
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
        
        # === GLM COMPLETE FREEDOM MODE ===
        # No position restrictions, no confidence guardrails
        # Only safety limits: max 3000 USD per trade, max 20x leverage
        
        try:
            response = self._glm.request(prompt_messages, trace_id=trace_id)
            if self._settings.use_nof1_style:
                decision = self._parse_nof1_response(response, portfolio_metrics)
            else:
                decision = self._parse_response(response)
            
            # Add timing information for staleness detection
            decision.decision_timestamp = datetime.now(timezone.utc)
            decision.market_snapshot_timestamp = market_snapshot_timestamp
            
            # Capture GLM response latency from response metadata
            if '_glm_latency_ms' in response:
                decision.glm_response_time_ms = response['_glm_latency_ms']
                logger.info("✅ GLM latency captured: %.0fms", decision.glm_response_time_ms)
            
            # Log timing information
            evaluation_duration = (datetime.now(timezone.utc) - evaluation_start_time).total_seconds()
            if evaluation_duration > 90:
                logger.warning(
                    "⚠️ GLM evaluation took %.1fs - market may have moved significantly",
                    evaluation_duration
                )
            
            # === APPLY ONLY SAFETY LIMITS ===
            # Max 3000 USD per trade, Max 20x leverage
            decision = self._apply_safety_limits(decision, portfolio_metrics)
            
            # === CONFIDENCE THRESHOLD ENFORCEMENT ===
            # Minimum 80% confidence required for opening positions (BUY/SELL)
            if decision.action in ["BUY", "SELL"] and decision.glm_confidence < 80.0:
                logger.warning(
                    "⚠️ CONFIDENCE TOO LOW: GLM wanted %s with confidence %.1f%% < 80%% → Forcing HOLD",
                    decision.action,
                    decision.glm_confidence
                )
                
                # Force decision to HOLD
                decision = RiskDecision(
                    action="HOLD",
                    amount=0.0,
                    reasoning=f"GLM confidence ({decision.glm_confidence:.1f}%) below 80% threshold - forced to HOLD for risk management",
                    leverage=decision.leverage,
                    glm_confidence=decision.glm_confidence,
                    reason_primary=decision.reason_primary,
                    reason_secondary="Confidence threshold not met",
                    glm_response_time_ms=decision.glm_response_time_ms,
                    exit_plan=None,  # No position opened, no exit plan needed
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
            self._notify_glm_failure(signals, str(exc))
            
            fallback_decision = self._fallback_decision(signals, error_msg)
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
            "BTCUSDT MULTI-TIMEFRAME TRADING ANALYSIS",
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
                f"  Position: {position:+.6f} BTC ({position_type})",
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
                f"  - If position is LONG ({position:.6f} BTC) and market shows bearish signals → Use CLOSE action (NOT SELL!)",
                f"  - If position is SHORT ({position:.6f} BTC) and market shows bullish signals → Use CLOSE action (NOT BUY!)",
                "  - If position is FLAT (0 BTC) → You can open new LONG (BUY) or SHORT (SELL)",
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
            "    Example: 'BTC position holding steady. Current price 109967, entry 107343, unrealized PnL +314.94. '",
            "            'Exit plan: stop_loss 102026 (not hit), invalidation below 105000 (not triggered). '",
            "            'RSI oversold at 29.7 but invalidation condition not met, so holding per exit plan.'",
            "  - For CLOSE: Explain which exit plan condition was triggered",
            "    Example: 'BTC position closing. Stop-loss triggered at 102026, current price 101800.'",
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
            '  "miktar": 0.5,  // For BUY/SELL: 0.0-1.0 (equity allocation), For CLOSE: 0.0-1.0 (position close ratio, 1.0=100%)',
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
            '🎯 POSITION SIZING - BE CONSERVATIVE:',
            '  - miktar: 0.05-0.1 → Use this for MOST trades (5-10%)',
            '  - miktar: 0.15-0.2 → Only for VERY strong signals',
            '  - miktar: 0.3 → RARELY! Only when ALL indicators perfectly aligned',
            '  - miktar: 0.5 → NEVER use! Too risky, always start small',
            '  - Default approach: Start with 0.1 (10%) for safety',
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

    def _parse_response(self, response: dict) -> RiskDecision:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # noqa: PERF203
            logger.error("GLM response parse error: %s", exc)
            return RiskDecision(action="HOLD", amount=0.0, reasoning="LLM yanıtı okunamadı")

        parsed = self._parse_content(content)
        if not parsed:
            logger.warning("GLM output format invalid: %s", content)
            return RiskDecision(action="HOLD", amount=0.0, reasoning=content)
        return RiskDecision(**parsed)

    def _parse_content(self, content: str) -> Dict[str, str | float] | None:
        try:
            content = self._prepare_json_payload(content)

            payload = json.loads(content)
            action = payload.get("karar", "HOLD").upper()
            amount = float(payload.get("miktar", 0))
            leverage = float(payload.get("kaldıraç", 0))
            reasoning = payload.get("gerekçe", "")
            
            # === PARSE STRUCTURED OUTPUT ===
            glm_confidence = float(payload.get("ai_confidence", 0))  # 0-100
            reason_primary = payload.get("reason_primary", "")
            reason_secondary = payload.get("reason_secondary", "")
            
            if action not in {"BUY", "SELL", "HOLD", "CLOSE"}:
                return None
            # For CLOSE action, amount represents how much of the position to close (1.0 = 100%)
            amount = max(0.0, min(amount, 1.0))
            leverage = self._normalize_leverage(leverage)
            
            # Clamp confidence to 0-100
            glm_confidence = max(0.0, min(100.0, glm_confidence))
            
            return {
                "action": action,
                "amount": amount,
                "reasoning": reasoning,
                "leverage": leverage,
                "glm_confidence": glm_confidence,
                "reason_primary": reason_primary,
                "reason_secondary": reason_secondary,
            }
        except Exception:  # noqa: BLE001
            return None

    def _prepare_json_payload(self, raw: str) -> str:
        """Clean GLM response so that json.loads accepts multi-line reasoning."""
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        elif raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        # Handle truncated JSON responses
        # Find the last complete JSON object
        brace_count = 0
        last_complete_pos = -1
        
        for i, ch in enumerate(raw):
            if ch == '{':
                brace_count += 1
            elif ch == '}':
                brace_count -= 1
                if brace_count == 0:
                    last_complete_pos = i
        
        # If we found a complete JSON object, truncate to that point
        if last_complete_pos > 0 and last_complete_pos < len(raw) - 1:
            raw = raw[:last_complete_pos + 1]
            logger.info("🔧 Truncated JSON to complete object at position %d", last_complete_pos)

        # Escape bare newlines within quoted strings
        cleaned_chars: list[str] = []
        in_string = False
        escape_next = False
        for ch in raw:
            if in_string:
                if escape_next:
                    cleaned_chars.append(ch)
                    escape_next = False
                    continue
                if ch == "\\":
                    cleaned_chars.append(ch)
                    escape_next = True
                    continue
                if ch == '"':
                    cleaned_chars.append(ch)
                    in_string = False
                    continue
                if ch == "\n":
                    cleaned_chars.append("\\n")
                    continue
                if ch == "\r":
                    continue
                cleaned_chars.append(ch)
            else:
                cleaned_chars.append(ch)
                if ch == '"':
                    in_string = True
        
        result = "".join(cleaned_chars)
        
        # Additional safety: try to parse and fix common issues
        try:
            # Test if it's valid JSON
            json.loads(result)
            return result
        except json.JSONDecodeError as e:
            logger.warning("🔧 JSON still invalid after cleaning, attempting repair: %s", str(e))
            
            # Try some common fixes
            # 1. Remove trailing commas
            result = result.replace(',}', '}').replace(',]', ']')
            
            # 2. Fix unclosed strings (find last quote and close it)
            if result.count('"') % 2 != 0:
                # Odd number of quotes means unclosed string
                # Find the last escape sequence and add a quote after it
                last_quote = result.rfind('"')
                if last_quote > 0:
                    # Insert a closing quote before the next closing brace
                    next_brace = result.find('}', last_quote)
                    if next_brace > last_quote:
                        result = result[:next_brace] + '"' + result[next_brace:]
                        logger.info("🔧 Fixed unclosed string in JSON")
            
            return result

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
        content = content.replace(
            """OUTPUT FORMAT:

⚠️ CRITICAL: You MUST respond with valid JSON only - no explanations before or after!

Respond with a JSON object in this exact format:

```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "<BUY|SELL|HOLD|CLOSE>",
      "quantity": <float>,
      "profit_target": <float>,
      "stop_loss": <float>,
      "invalidation_condition": "<string>",
      "leverage": <int 1-20>,
      "confidence": <0.0-1.0>,
      "risk_usd": <float>
    },
    "justification": "<your reasoning here>"
  }
}
```

EXAMPLES:

Example HOLD response:
```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "HOLD",
      "quantity": 0.0,
      "profit_target": 0.0,
      "stop_loss": 0.0,
      "invalidation_condition": "N/A",
      "leverage": 1,
      "confidence": 0.8,
      "risk_usd": 0.0
    },
    "justification": "Market showing mixed signals, better to wait."
  }
}
```

Example BUY response:
```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "BUY",
      "quantity": 0.05,
      "profit_target": 115000.0,
      "stop_loss": 105000.0,
      "invalidation_condition": "If price closes below 105000 on 30m candle",
      "leverage": 10,
      "confidence": 0.75,
      "risk_usd": 500.0
    },
    "justification": "RSI oversold at 30, good risk/reward for long position."
  }
}
```

IMPORTANT:
- For HOLD: Use current position details, no justification needed
- For BUY/SELL: Provide full entry plan with justification
- For CLOSE: Explain why you're closing the position
- Confidence should reflect your conviction (0.5-1.0 range)
- Risk should be proportional to confidence and account size

Think step by step and make your decision based on:
1. Current market state across all timeframes
2. Technical indicators alignment
3. Your existing position (if any) and exit plan
4. Risk/reward ratio
5. Market structure and momentum""",
            
            """OUTPUT FORMAT:

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
        )
        
        return content

    def _notify_glm_failure(self, signals: List[AgentSignal], error: str) -> None:
        """Send Telegram notification when GLM fails"""
        try:
            from app.utils.telegram import telegram_client, format_markdown
            from datetime import datetime
            
            if not telegram_client.enabled():
                return
            
            # Summarize signals
            signal_summary = []
            for signal in signals:
                signal_summary.append(
                    f"  • {signal.direction} (güven: {signal.confidence:.2f})"
                )
            
            message = "\n".join([
                "🚨 *GLM API HATASI*",
                "",
                "*Hata Detayı:*",
                f"{format_markdown(error)}",
                "",
                "*Agent Sinyalleri:*",
                "\n".join(signal_summary) if signal_summary else "  Sinyal yok",
                "",
                "⚠️ *Karar: HOLD (Güvenli mod)*",
                "GLM çalışmadığı için işlem yapılmıyor.",
                "",
                f"🕒 {datetime.utcnow().isoformat()}",
                "",
                "💡 *Aksiyon:*",
                "1. GLM API key'i kontrol edin",
                "2. GLM servis durumunu kontrol edin",
                "3. Hata devam ederse log'ları inceleyin",
            ])
            
            telegram_client.send_message(message)
            logger.info("GLM failure notification sent to Telegram")
            
        except Exception as exc:
            logger.error("Failed to send GLM failure notification: %s", exc)
    
    def _apply_safety_limits(self, decision: RiskDecision, portfolio_metrics: dict = None) -> RiskDecision:
        """
        Apply only safety limits to GLM decision:
        - Max 3000 USD margin (teminat) per trade
        - Max 20x leverage (min 1x)
        - This allows up to 60,000 USD position size (3000 × 20x)
        
        GLM has complete freedom otherwise.
        """
        if decision.action not in ["BUY", "SELL"]:
            # HOLD and CLOSE don't need safety checks
            return decision
        
        # 1. Clamp leverage to 1-20x range
        original_leverage = decision.leverage
        clamped_leverage = max(1.0, min(decision.leverage, 20.0))
        
        if abs(clamped_leverage - original_leverage) > 0.01:
            logger.info(
                "🔒 Safety limit: Leverage clamped from %.2fx to %.2fx",
                original_leverage,
                clamped_leverage
            )
        
        # 2. Apply margin limit: max 3000 USD from equity can be used as margin
        if portfolio_metrics:
            current_price = portfolio_metrics.get("price", 0)
            equity = portfolio_metrics.get("equity", 10000)
            
            if current_price > 0 and equity > 0:
                # Maximum margin (teminat) that can be allocated
                MAX_MARGIN_USD = 3000.0
                
                # GLM's amount is equity percentage (0.0-1.0)
                # Convert to USD margin
                requested_margin_usd = decision.amount * equity
                
                # Clamp margin to maximum
                actual_margin_usd = min(requested_margin_usd, MAX_MARGIN_USD)
                
                # Calculate leveraged position size
                position_size_usd = actual_margin_usd * clamped_leverage
                position_size_btc = position_size_usd / current_price
                
                # Convert back to equity percentage for decision.amount
                clamped_amount = actual_margin_usd / equity
                
                original_amount = decision.amount
                
                if clamped_amount < original_amount - 0.001:
                    logger.info(
                        "🔒 Safety limit: Margin clamped from %.2f USD to %.2f USD (max margin)",
                        requested_margin_usd,
                        actual_margin_usd
                    )
                    logger.info(
                        "   → Position: %.2f USD (%.6f BTC) with %.1fx leverage",
                        position_size_usd,
                        position_size_btc,
                        clamped_leverage
                    )
                else:
                    # Log even when not clamped to show final position size
                    logger.info(
                        "✅ Position within limits: %.2f USD margin, %.2f USD position (%.6f BTC) @ %.1fx leverage",
                        actual_margin_usd,
                        position_size_usd,
                        position_size_btc,
                        clamped_leverage
                    )
                
                # Return modified decision
                return RiskDecision(
                    action=decision.action,
                    amount=clamped_amount,
                    reasoning=decision.reasoning + (
                        f" | Safety: {clamped_leverage:.1f}x leverage, {actual_margin_usd:.0f} USD margin → {position_size_usd:.0f} USD position"
                        if (abs(clamped_leverage - original_leverage) > 0.01 or clamped_amount < original_amount - 0.001)
                        else ""
                    ),
                    leverage=clamped_leverage,
                    glm_confidence=decision.glm_confidence,
                    reason_primary=decision.reason_primary,
                    reason_secondary=decision.reason_secondary,
                    glm_response_time_ms=decision.glm_response_time_ms,
                    exit_plan=decision.exit_plan,  # CRITICAL: Preserve GLM's exit plan
                    decision_timestamp=decision.decision_timestamp,
                    market_snapshot_timestamp=decision.market_snapshot_timestamp,
                    close_side=decision.close_side,
                )
        
        # Fallback: only apply leverage limit if no portfolio metrics
        if abs(clamped_leverage - original_leverage) > 0.01:
            return RiskDecision(
                action=decision.action,
                amount=decision.amount,
                reasoning=decision.reasoning + f" | Safety: leverage {clamped_leverage:.1f}x",
                leverage=clamped_leverage,
                glm_confidence=decision.glm_confidence,
                reason_primary=decision.reason_primary,
                reason_secondary=decision.reason_secondary,
                glm_response_time_ms=decision.glm_response_time_ms,
                exit_plan=decision.exit_plan,  # CRITICAL: Preserve GLM's exit plan
                decision_timestamp=decision.decision_timestamp,
                market_snapshot_timestamp=decision.market_snapshot_timestamp,
                close_side=decision.close_side,
            )
        
        return decision

    def _normalize_leverage(self, value: float) -> float:
        """Normalize leverage to 1-20x range (GLM freedom mode)"""
        if value <= 0:
            return 1.0  # Default to 1x if invalid
        return max(1.0, min(value, 20.0))

    # --- Fallback & Guardrail Helpers ---

    def _fallback_decision(self, signals: List[AgentSignal], reason: str) -> RiskDecision:
        """Produce a conservative decision when GLM API fails.
        
        When GLM API is unavailable, we prioritize risk management:
        - Default to HOLD to avoid making decisions without AI analysis
        - Only allow trading if there's very strong signal confidence
        - Use reduced position sizes for safety
        """
        # Check if we have any signals to base decision on
        if not signals:
            logger.warning("No signals available for fallback decision - defaulting to HOLD")
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"GLM API unavailable and no signals: {reason}",
                leverage=5.0,
            )

        # Get confidence band from available signals
        band = self._confidence_band(signals)
        
        # Extra conservative approach when GLM is down
        # Only allow trading if confidence is very high (>= 80%)
        if band["confidence"] < 80.0:
            logger.info(
                "GLM API down - signal confidence %.1f%% below 80%% threshold → HOLD for safety",
                band["confidence"]
            )
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"GLM API unavailable - signal confidence too low ({band['confidence']:.1f}% < 80%): {reason}",
                leverage=5.0,
            )

        # If confidence is high enough, allow trading but with reduced size
        reduced_amount = min(band["amount"] * 0.5, 0.1)  # Max 10% position, half of normal
        reasoning = (
            f"GLM API down - using reduced position (confidence={band['confidence']:.1f}%, "
            f"reduced_amount={reduced_amount:.3f}) | {reason}"
        )
        
        logger.warning(
            "GLM API down but using fallback trade: action=%s amount=%.3f confidence=%.1f%%",
            band["action"],
            reduced_amount,
            band["confidence"]
        )
        
        return RiskDecision(
            action=band["action"],
            amount=reduced_amount,
            reasoning=reasoning,
            leverage=5.0,
        )

    def _apply_confidence_guardrails(
        self,
        decision: RiskDecision,
        signals: List[AgentSignal],
    ) -> RiskDecision:
        """Clamp GLM decisions using signal confidence bands with dynamic market conditions.
        
        NEW STRATEGY: Prioritize GLM's own confidence assessment.
        - If GLM confidence >= 80: Trust GLM, bypass bias guardrails
        - If GLM confidence < 80: Use bias scores for validation
        """
        if not signals:
            return decision

        if decision.action not in {"BUY", "SELL"}:
            return decision

        # === NEW: GLM CONFIDENCE PRIORITY ===
        # If GLM has high confidence (>= 80), trust it directly
        if decision.glm_confidence >= 80:
            logger.info(
                "✅ GLM High Confidence Mode: GLM confidence %.1f%% >= 80%% → Bypassing bias guardrails",
                decision.glm_confidence,
            )
            # Still apply reasonable position sizing based on GLM confidence
            if decision.glm_confidence >= 85:
                max_amount = 0.25  # Very high confidence
            elif decision.glm_confidence >= 75:
                max_amount = 0.18  # High confidence
            else:  # 70-74
                max_amount = 0.12  # Moderate-high confidence
            
            # Clamp amount if GLM requested too much
            if decision.amount > max_amount:
                logger.info(
                    "Position size clamped: %.4f → %.4f (GLM confidence %.1f%%)",
                    decision.amount,
                    max_amount,
                    decision.glm_confidence,
                )
                return RiskDecision(
                    action=decision.action,
                    amount=max_amount,
                    reasoning=f"{decision.reasoning} | GLM high confidence mode (clamped to {max_amount:.2f})",
                    leverage=decision.leverage,
                    glm_confidence=decision.glm_confidence,
                    reason_primary=decision.reason_primary,
                    reason_secondary=decision.reason_secondary,
                    glm_response_time_ms=decision.glm_response_time_ms,
                    decision_timestamp=decision.decision_timestamp,
                    market_snapshot_timestamp=decision.market_snapshot_timestamp,
                    close_side=decision.close_side,
                )
            
            # GLM amount is reasonable, use it directly
            logger.info(
                "Using GLM decision directly: action=%s amount=%.4f confidence=%.1f%%",
                decision.action,
                decision.amount,
                decision.glm_confidence,
            )
            return decision
        
        # === FALLBACK: Use bias guardrails for low GLM confidence ===
        logger.info(
            "⚠️ GLM Low Confidence Mode: GLM confidence %.1f%% < 80%% → Using bias guardrails",
            decision.glm_confidence,
        )
        
        band = self._confidence_band(signals)
        market_condition = self._analyze_market_condition(signals)

        # Dynamic HOLD strategy based on market conditions
        if band["action"] == "HOLD":
            # In trending markets, allow smaller positions even with lower confidence
            if market_condition["trend_strength"] > 0.6 and market_condition["volatility"] < 0.7:
                # Strong trend, low volatility - allow small position
                if band["confidence"] > 0.15:  # Very low threshold for trending markets
                    adjusted_amount = min(0.05, band["amount"] or 0.05)  # Max 5% in trending
                    logger.info(
                        "Dynamic strategy: Trending market detected, allowing small position (%.4f) with confidence %.2f",
                        adjusted_amount,
                        band["confidence"],
                    )
                    return RiskDecision(
                        action=decision.action,
                        amount=adjusted_amount,
                        reasoning=f"{decision.reasoning} | Dynamic: Trending market (confidence {band['confidence']:.2f})",
                        leverage=decision.leverage,
                        glm_confidence=decision.glm_confidence,
                        reason_primary=decision.reason_primary,
                        reason_secondary=decision.reason_secondary,
                        glm_response_time_ms=decision.glm_response_time_ms,
                        decision_timestamp=decision.decision_timestamp,
                        market_snapshot_timestamp=decision.market_snapshot_timestamp,
                        close_side=decision.close_side,
                    )

            logger.info(
                "Confidence guardrail: insufficient confidence (bias=%.2f, GLM=%.1f%%) -> HOLD",
                band["confidence"],
                decision.glm_confidence,
            )
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"{decision.reasoning} | Confidence guardrail HOLD (bias={band['confidence']:.2f}, GLM={decision.glm_confidence:.1f}%)",
                leverage=decision.leverage,
                glm_confidence=decision.glm_confidence,
                reason_primary=decision.reason_primary,
                reason_secondary=decision.reason_secondary,
                glm_response_time_ms=decision.glm_response_time_ms,
                decision_timestamp=decision.decision_timestamp,
                market_snapshot_timestamp=decision.market_snapshot_timestamp,
                close_side=decision.close_side,
            )

        if band["action"] != decision.action:
            # In strong trending markets, allow direction flexibility
            if market_condition["trend_strength"] > 0.7:
                # Very strong trend - follow trend direction with minimal amount
                minimal_amount = 0.03  # 3% position
                logger.info(
                    "Dynamic strategy: Strong trend detected, allowing minimal position in trend direction (%.4f)",
                    minimal_amount,
                )
                return RiskDecision(
                    action=band["action"],  # Use signal direction, not GLM
                    amount=minimal_amount,
                    reasoning=f"{decision.reasoning} | Dynamic: Strong trend override (minimal position)",
                    leverage=decision.leverage,
                    glm_confidence=decision.glm_confidence,
                    reason_primary=decision.reason_primary,
                    reason_secondary=decision.reason_secondary,
                    glm_response_time_ms=decision.glm_response_time_ms,
                    decision_timestamp=decision.decision_timestamp,
                    market_snapshot_timestamp=decision.market_snapshot_timestamp,
                    close_side=decision.close_side,
                )

            logger.info(
                "Confidence guardrail: direction mismatch (GLM=%s %.1f%%, bias=%s) -> HOLD",
                decision.action,
                decision.glm_confidence,
                band["action"],
            )
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"{decision.reasoning} | Confidence guardrail direction mismatch (GLM={decision.action}, bias={band['action']})",
                leverage=decision.leverage,
                glm_confidence=decision.glm_confidence,
                reason_primary=decision.reason_primary,
                reason_secondary=decision.reason_secondary,
                glm_response_time_ms=decision.glm_response_time_ms,
                decision_timestamp=decision.decision_timestamp,
                market_snapshot_timestamp=decision.market_snapshot_timestamp,
                close_side=decision.close_side,
            )

        clamped_amount = min(decision.amount, band["amount"])

        # Adjust amount based on market conditions
        if market_condition["volatility"] > 0.8:
            # High volatility - reduce position size by 30%
            clamped_amount *= 0.7
            logger.info("Dynamic strategy: High volatility detected, reducing position by 30%%")
        elif market_condition["volatility"] < 0.3:
            # Low volatility - can increase position slightly
            clamped_amount = min(clamped_amount * 1.2, band["amount"])
            logger.info("Dynamic strategy: Low volatility detected, allowing slightly larger position")

        if clamped_amount <= 0:
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"{decision.reasoning} | Confidence guardrail zero amount",
                leverage=decision.leverage,
            )

        if clamped_amount < decision.amount - 1e-6:
            logger.info(
                "Confidence guardrail: amount clamped from %.4f to %.4f (bias_conf=%.2f, GLM_conf=%.1f%%)",
                decision.amount,
                clamped_amount,
                band["confidence"],
                decision.glm_confidence,
            )
            return RiskDecision(
                action=decision.action,
                amount=clamped_amount,
                reasoning=f"{decision.reasoning} | Confidence guardrail (max {band['amount']:.2f}, bias={band['confidence']:.2f})",
                leverage=decision.leverage,
                glm_confidence=decision.glm_confidence,
                reason_primary=decision.reason_primary,
                decision_timestamp=decision.decision_timestamp,
                market_snapshot_timestamp=decision.market_snapshot_timestamp,
                close_side=decision.close_side,
                reason_secondary=decision.reason_secondary,
                glm_response_time_ms=decision.glm_response_time_ms,
            )

        return decision

    def _analyze_market_condition(self, signals: List[AgentSignal]) -> Dict[str, float]:
        """Analyze current market condition for dynamic strategy adaptation."""
        if not signals:
            return {"trend_strength": 0.0, "volatility": 0.5}

        signal = signals[0]
        bias_snapshot: Dict[str, float] | None = None
        try:
            bias_snapshot = signal.metadata.get("bias_snapshot") if signal.metadata else None
        except AttributeError:
            bias_snapshot = None

        if not bias_snapshot:
            return {"trend_strength": 0.0, "volatility": 0.5}

        # Extract key metrics
        trend_bias = abs(float(bias_snapshot.get("trend_bias_score", 0.0) or 0.0))
        momentum_bias = abs(float(bias_snapshot.get("momentum_bias_score", 0.0) or 0.0))
        volatility_regime = float(bias_snapshot.get("volatility_regime_score", 0.5) or 0.5)
        composite = float(bias_snapshot.get("composite_bias_score", 0.0) or 0.0)

        # Calculate trend strength (0.0 to 1.0)
        # Strong trend = high absolute composite + aligned trend/momentum
        trend_strength = _clamp(
            (abs(composite) + trend_bias + momentum_bias) / 3.0,
            0.0,
            1.0
        )

        # Volatility is already normalized (0.0 to 1.0)
        volatility = _clamp(volatility_regime, 0.0, 1.0)

        return {
            "trend_strength": trend_strength,
            "volatility": volatility,
        }

    def _confidence_band(self, signals: List[AgentSignal]) -> Dict[str, float]:
        if not signals:
            return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

        primary = signals[0]
        bias_snapshot: Dict[str, float] | None = None
        try:
            bias_snapshot = primary.metadata.get("bias_snapshot") if primary.metadata else None
        except AttributeError:
            bias_snapshot = None

        if bias_snapshot:
            band = self._bias_confidence_band(bias_snapshot)
            if band["action"] != "HOLD" or band["confidence"] > 0:
                return band

        return self._legacy_confidence_band(signals)

    def _bias_confidence_band(self, bias: Dict[str, float]) -> Dict[str, float]:
        composite = float(bias.get("composite_bias_score", 0.0) or 0.0)
        trend = float(bias.get("trend_bias_score", 0.0) or 0.0)
        momentum = float(bias.get("momentum_bias_score", 0.0) or 0.0)
        intraday = float(bias.get("intraday_bias_score", 0.0) or 0.0)
        futures = float(bias.get("futures_bias_score", 0.0) or 0.0)
        bias_conf = _clamp(float(bias.get("bias_confidence_score", 0.0) or 0.0), 0.0, 1.0)
        volatility = _clamp(float(bias.get("volatility_regime_score", 0.5) or 0.5), 0.0, 1.0)

        direction = "HOLD"
        if composite > 0.08:
            direction = "BUY"
        elif composite < -0.08:
            direction = "SELL"

        consensus = sum(
            1
            for component in (trend, momentum, intraday, futures)
            if component * composite > 0.02
        )

        min_consensus = 2
        if volatility >= 0.9:
            min_consensus = 3

        if direction == "HOLD" or consensus < min_consensus:
            return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

        consensus_factor = consensus / 4.0
        volatility_factor = 1.0 - 0.5 * volatility
        strength = _clamp(abs(composite), 0.0, 1.0)

        effective_conf = _clamp(
            max(0.0, bias_conf * consensus_factor * volatility_factor),
            0.0,
            1.0,
        )
        effective_conf = max(effective_conf, strength * 0.6)

        momentum_magnitude = abs(momentum)
        if momentum_magnitude > 0.5:
            momentum_correction = 1.0 - (momentum_magnitude - 0.5) * 0.5
            effective_conf *= momentum_correction

        if effective_conf < 0.30:
            return {"action": "HOLD", "amount": 0.0, "confidence": effective_conf}

        if effective_conf < 0.6:
            amount = 0.10
        elif effective_conf < 0.75:
            amount = 0.18
        else:
            amount = 0.25

        amount *= _clamp(1.0 - 0.6 * volatility, 0.4, 1.0)
        amount = _clamp(amount, 0.05, 0.3)

        return {"action": direction, "amount": amount, "confidence": effective_conf}

    def _legacy_confidence_band(self, signals: List[AgentSignal]) -> Dict[str, float]:
        long_conf = sum(s.confidence for s in signals if s.direction == "BUY")
        short_conf = sum(s.confidence for s in signals if s.direction == "SELL")
        net_conf = long_conf - short_conf
        abs_conf = min(1.0, abs(net_conf))

        if abs_conf < 0.35:
            return {"action": "HOLD", "amount": 0.0, "confidence": abs_conf}

        if abs_conf < 0.7:
            amount = 0.1
        elif abs_conf < 0.85:
            amount = 0.2
        else:
            amount = 0.3

        action = "BUY" if net_conf > 0 else "SELL"
        return {"action": action, "amount": amount, "confidence": abs_conf}
    
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
                portfolio_metrics=portfolio_metrics,
                latency_metrics=latency_metrics,
            )
            
            self._signal_logger.log_signal(signal_log)
            
            # Return summary for Telegram
            return signal_log.get_summary()
            
        except Exception as e:
            logger.error("Failed to log signal: %s", e, exc_info=True)
            return ""
    
    def _get_bias_reliability_data(
        self,
        decision: RiskDecision,
        signals: List[AgentSignal],
        portfolio_metrics: dict | None,
    ) -> Dict[str, Any] | None:
        """
        DISABLED - Pure GLM system doesn't use bias scores
        
        Args:
            decision: RiskDecision with GLM confidence
            signals: List of agent signals with bias scores
            portfolio_metrics: Portfolio state
            
        Returns:
            Dictionary with bias reliability data, or None if not available
        """
        # Pure GLM system - no bias scores needed
        return None
        
        try:
            if not signals:
                return None
            
            signal = signals[0]
            bias_snapshot = signal.metadata.get("bias_snapshot", {}) if signal.metadata else {}
            
            if not bias_snapshot:
                return None
            
            # Extract bias scores
            composite_bias = float(bias_snapshot.get("composite_bias_score", 0.0) or 0.0)
            bias_confidence = float(bias_snapshot.get("bias_confidence_score", 0.0) or 0.0)
            trend_bias = float(bias_snapshot.get("trend_bias_score", 0.0) or 0.0)
            momentum_bias = float(bias_snapshot.get("momentum_bias_score", 0.0) or 0.0)
            futures_bias = float(bias_snapshot.get("futures_bias_score", 0.0) or 0.0)
            volatility_regime = float(bias_snapshot.get("volatility_regime_score", 0.0) or 0.0)
            
            # Convert GLM confidence (0-100) to 0-1 scale for comparison
            glm_confidence_normalized = decision.glm_confidence / 100.0
            
            # Calculate agreement between GLM and bias
            # Both should point in same direction and have similar confidence
            glm_direction = 1 if decision.action == "BUY" else -1 if decision.action == "SELL" else 0
            bias_direction = 1 if composite_bias > 0.1 else -1 if composite_bias < -0.1 else 0
            
            direction_agreement = (glm_direction == bias_direction)
            confidence_diff = abs(glm_confidence_normalized - bias_confidence)
            
            # Determine reliability scenario
            if direction_agreement and confidence_diff < 0.2:
                reliability_status = "HIGH_AGREEMENT"
                reliability_color = "🟢"
            elif direction_agreement and confidence_diff < 0.4:
                reliability_status = "MODERATE_AGREEMENT"
                reliability_color = "🟡"
            elif not direction_agreement:
                reliability_status = "DIRECTION_MISMATCH"
                reliability_color = "🔴"
            else:
                reliability_status = "CONFIDENCE_MISMATCH"
                reliability_color = "🟠"
            
            # Log bias reliability analysis (file logs only)
            logger.info(
                "%s Bias Reliability Test: %s | GLM_conf=%.1f%% Bias_conf=%.1f%% | GLM_dir=%s Bias_dir=%.2f | diff=%.2f",
                reliability_color,
                reliability_status,
                decision.glm_confidence,
                bias_confidence * 100,
                decision.action,
                composite_bias,
                confidence_diff
            )
            
            # Log to InfluxDB for analysis
            try:
                from app.utils.influx import write_point
                from datetime import datetime
                
                point = {
                    "measurement": "bias_reliability",
                    "tags": {
                        "symbol": "BTCUSDT",
                        "action": decision.action,
                        "reliability_status": reliability_status,
                        "direction_agreement": "true" if direction_agreement else "false",
                    },
                    "fields": {
                        "glm_confidence": decision.glm_confidence,
                        "bias_confidence": bias_confidence * 100,
                        "composite_bias": composite_bias,
                        "trend_bias": trend_bias,
                        "momentum_bias": momentum_bias,
                        "futures_bias": futures_bias,
                        "volatility_regime": volatility_regime,
                        "confidence_diff": confidence_diff,
                        "glm_direction": float(glm_direction),
                        "bias_direction": float(bias_direction),
                    },
                    "time": datetime.utcnow(),
                }
                
                write_point(point)
                
            except Exception as influx_err:
                logger.warning("Failed to write bias reliability to InfluxDB: %s", influx_err)
            
            # Detailed breakdown for high disagreement cases
            if reliability_status in ["DIRECTION_MISMATCH", "CONFIDENCE_MISMATCH"]:
                logger.warning(
                    "⚠️ Bias-GLM Disagreement Detected:\n"
                    "  GLM: action=%s confidence=%.1f%% reasons=[%s, %s]\n"
                    "  Bias: composite=%.2f trend=%.2f momentum=%.2f futures=%.2f volatility=%.2f",
                    decision.action,
                    decision.glm_confidence,
                    decision.reason_primary[:50] if decision.reason_primary else "N/A",
                    decision.reason_secondary[:50] if decision.reason_secondary else "N/A",
                    composite_bias,
                    trend_bias,
                    momentum_bias,
                    futures_bias,
                    volatility_regime
                )
            
            # Return data for Telegram summary
            return {
                "reliability_status": reliability_status,
                "reliability_color": reliability_color,
                "glm_confidence": decision.glm_confidence,
                "bias_confidence": bias_confidence * 100,
                "composite_bias": composite_bias,
                "confidence_diff": confidence_diff,
                "volatility_regime": volatility_regime,
            }
        
        except Exception as e:
            logger.error("Failed to get bias reliability data: %s", e, exc_info=True)
            return None
    
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
            
            # === PROMPT GÖNDERİM ÖNCESİ DOĞRULAMA ===
            # Prompt'un tam içeriğinin API'ye gönderilmeden önce doğru olduğunu kontrol et
            system_message = "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. KRİTİK KURAL: JSON yanıtındaki 'gerekçe' alanı MUTLAKA TÜRKÇE olmalıdır. Gerekçe detaylı ve kapsamlı olmalı (400-600 karakter önerilir). Her karar için: (1) TÜM timeframe'leri (1m, 30m, 4h) AYRI AYRI analiz et ve SAYISAL değerler ver, (2) Her timeframe için RSI, EMA20, EMA50, MACD değerlerini belirt, (3) Fiyat seviyelerini, destek/direnç noktalarını KESIN RAKAMLARLA açıkla, (4) Timeframe'ler arasındaki uyum/çelişkileri açıkla, (5) Neden ŞİMDİ bu kararı verdiğini piyasa bağlamında izah et. HOLD kararları da detaylı gerekçe gerektirir. Her cümle spesifik veri içermeli ve net olmalı. Fırsatları kaçırmaktan korkma ama her kararını KANITA DAYALI olarak açıkla!"
            system_len = len(system_message)
            user_content_len = len(content)
            total_prompt_len = system_len + user_content_len
            
            logger.info(
                "📤 GLM Prompt Validation: system=%d chars, user=%d chars, total=%d chars",
                system_len,
                user_content_len,
                total_prompt_len,
            )
            
            # Prompt içeriğinin son 200 karakterini logla (tam içeriğin gönderildiğini doğrulamak için)
            if len(content) > 200:
                logger.debug(
                    "📤 User prompt (last 200 chars): %s",
                    content[-200:],
                )
            else:
                logger.debug("📤 User prompt (full): %s", content)

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
            '{\n  "BTCUSDT": {\n    "trade_signal_args": {\n      "coin": "BTCUSDT",\n      "signal": "hold|close_position|buy|sell",\n      "quantity": 0.12,\n      "stop_loss": 102026.675,\n      "invalidation_condition": "If the price closes below 105000 on a 3-minute candle",\n      "leverage": 10,\n      "confidence": 0.75,\n      "risk_usd": 619.2345,\n      "justification": "..." // Only for entry/close\n    }\n  }\n}',
            "",
            "Signal types:",
            "- hold: Keep current position, monitor",
            "- close_position: Exit current position",
            "- buy: Open new LONG position",
            "- sell: Open new SHORT position",
            "",
            "Quantity and Leverage:",
            "- quantity: For BUY/SELL signals, this is the BTC amount to open (e.g., 0.12 = 0.12 BTC).",
            "  For CLOSE signals, this can be BTC amount or ratio (0-1.0) of position to close.",
            "  For HOLD signals, use current position quantity.",
            "- leverage: Always use 10x leverage (fixed standard).",
            "- risk_usd: USD amount at risk for this trade (used for position sizing verification).",
            "",
            "For 'hold' signals, provide the same exit plan details as the current position.",
            "For 'close_position', 'buy', or 'sell', provide justification.",
            "",
            "⚠️ CRITICAL: The 'justification' field MUST be written in TURKISH. This is mandatory. All explanations, reasoning, and analysis must be in Turkish language.",
            "",
            "📋 JUSTIFICATION REQUIREMENTS (IMPORTANT FOR QUALITY DECISIONS):",
            "- Should be DETAILED and COMPREHENSIVE (400-600 characters recommended)",
            "- Include specific numbers and indicator values from multiple timeframes",
            "- Must include SPECIFIC indicator values with EXACT NUMBERS from ALL timeframes",
            "- Must explain EACH timeframe separately: 1m, 15m, 30m, 4h with concrete data",
            "- Must mention EXACT RSI values for EACH timeframe (e.g., 1m RSI: 72.3, 15m RSI: 45.2)",
            "- Must describe EMA20/EMA50 relationships with EXACT prices for EACH timeframe",
            "- Must include MACD values and histogram direction for EACH timeframe",
            "- Must explain WHY this specific decision at THIS exact moment with market context",
            "- Must reference current price vs support/resistance levels with exact numbers",
            "- Must discuss volume, momentum, and trend strength with quantitative analysis",
            "- Must explain confluence or divergence between timeframes in detail",
            "- NO GENERIC PHRASES! Every sentence must contain specific data points",
            "- HOLD decisions need EQUALLY DETAILED justification as BUY/SELL",
            "",
            "Example MINIMUM acceptable justification (400+ chars):",
            "Mevcut fiyat 109,926.5 seviyesinde. 1 DAKİKALIK ANALİZ: RSI 72.34 ile aşırı alım bölgesinde, EMA20 (109,842.3) ve EMA50 (109,765.8) fiyatın altında kalmış, MACD histogram pozitif ancak momentum zayıflamaya başladı. 15 DAKİKALIK ANALİZ: RSI 58.12 ile nötr bölgede, EMA20 (110,123.4) fiyatın üzerinde direniş oluşturuyor, MACD -125.8 ile negatif, düşüş trendi devam ediyor. 30 DAKİKALIK ANALİZ: RSI 45.67 ile orta seviyede, EMA20 (110,456.2) güçlü direnç seviyesi, MACD -234.5 ile güçlü düşüş sinyali, hacim ortalamanın %15 altında. 4 SAATLİK ANALİZ: RSI 42.18 ile düşüş eğiliminde, EMA20 (111,234.5) EMA50 (111,890.3) üzerinde bearish crossover, MACD -456.7 ile güçlü negatif momentum. SONUÇ: Kısa vadede 1m timeframe aşırı alım gösterse de, 15m, 30m ve 4h timeframe lerde güçlü düşüş sinyalleri mevcut. Çoklu zaman dilimi uyumsuzluğu nedeniyle risk yüksek, pozisyon açmak için güvenli değil.",
            "",
        ])
        
        prompt_content = "\n".join(content_lines)
        # Token estimate: ~4 chars per token for Turkish text
        estimated_tokens = len(prompt_content) // 4
        logger.info("📤 GLM Prompt: %d chars, ~%d tokens (estimated)", len(prompt_content), estimated_tokens)
        
        # === PROMPT GÖNDERİM ÖNCESİ DOĞRULAMA ===
        system_message = "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. KRİTİK KURAL: JSON yanıtındaki 'justification' alanı MUTLAKA TÜRKÇE olmalıdır. Gerekçe detaylı ve kapsamlı olmalı (400-600 karakter önerilir). Her karar için: (1) TÜM timeframe'leri (1m, 15m, 30m, 4h) AYRI AYRI analiz et ve SAYISAL değerler ver, (2) Her timeframe için RSI, EMA20, EMA50, MACD değerlerini belirt, (3) Fiyat seviyelerini, destek/direnç noktalarını KESIN RAKAMLARLA açıkla, (4) Timeframe'ler arasındaki uyum/çelişkileri açıkla, (5) Neden ŞİMDİ bu kararı verdiğini piyasa bağlamında izah et. HOLD kararları da detaylı gerekçe gerektirir. Her cümle spesifik veri içermeli ve net olmalı. Fırsatları kaçırmaktan korkma ama her kararını KANITA DAYALI olarak açıkla!"
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
    
    def _parse_nof1_response(self, response: dict, portfolio_metrics: dict | None = None) -> RiskDecision:
        """Parse nof1.ai style JSON response."""
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # noqa: PERF203
            logger.error("GLM response parse error: %s", exc)
            return RiskDecision(action="HOLD", amount=0.0, reasoning="LLM yanıtı okunamadı")
        
        # Log the raw response for debugging
        response_tokens = len(content) // 4  # Rough estimate
        logger.info("📥 GLM Response: %d chars, ~%d tokens (estimated)", len(content), response_tokens)
        logger.info("🔍 Raw GLM response (first 500 chars): %s", content[:500])
        
        # Extract JSON from response
        content = self._prepare_json_payload(content)
        
        try:
            payload = json.loads(content)
            
            # NOF1.AI format: {"BTCUSDT": {"trade_signal_args": {..., "justification": "..."}}}
            # Try BTCUSDT first (our new format), then fallback to BTC
            btc_data = payload.get("BTCUSDT", payload.get("BTC", {}))
            trade_signal_args = btc_data.get("trade_signal_args", {})
            # Justification is inside trade_signal_args (per prompt), with fallbacks for backward compatibility
            justification = (
                trade_signal_args.get("justification", "") or  # Primary: as specified in prompt
                trade_signal_args.get("gerekçe", "") or        # Turkish version inside args
                btc_data.get("justification", "") or            # Fallback: outside args (English)
                btc_data.get("gerekçe", "")                     # Fallback: outside args (Turkish)
            )
            
            if not trade_signal_args:
                logger.warning("❌ No trade_signal_args found in NOF1.AI response.")
                logger.warning("🔍 Payload structure: %s", json.dumps(payload, indent=2)[:500])
                logger.warning("🔍 Available keys: %s", list(payload.keys()))
                if "BTCUSDT" in payload:
                    logger.warning("🔍 BTCUSDT keys: %s", list(payload["BTCUSDT"].keys()))
                return RiskDecision(action="HOLD", amount=0.0, reasoning="Invalid nof1.ai response format - missing trade_signal_args")
            
            signal = trade_signal_args.get("signal", "hold").lower()
            quantity = float(trade_signal_args.get("quantity") or 0)
            leverage = float(trade_signal_args.get("leverage") or 10)
            confidence = float(trade_signal_args.get("confidence") or 0.5) * 100  # Convert 0-1 to 0-100 percentage
            
            # Debug log to see what justification we got
            logger.info("🔍 GLM justification received: '%s'", justification)
            if not justification:
                logger.warning("⚠️ GLM provided empty justification - this is the main issue!")
            
            # Map signal to action
            signal_map = {
                "hold": "HOLD",
                "close_position": "CLOSE",
                "close": "CLOSE",  # GLM sends "CLOSE" not "close_position"
                "buy": "BUY",
                "sell": "SELL",
            }
            
            action = signal_map.get(signal, "HOLD")
            
            # NOF1.AI format: quantity is BTC amount directly
            # For HOLD: quantity is current position size (keep as is)
            # For CLOSE: quantity is amount to close (0-1.0 ratio or BTC amount)
            # For BUY/SELL: quantity is BTC amount to open
            
            # Get current price and equity for conversion
            current_price = 0.0
            equity = 10000.0
            if portfolio_metrics:
                current_price = portfolio_metrics.get("price", 0.0)
                equity = portfolio_metrics.get("equity", 10000.0)
            
            # NOTE: Position size limiting is handled by _apply_safety_limits()
            # No arbitrary % of equity limit here - GLM has freedom within safety limits
            # Safety limits: max 3000 USD margin, max 20x leverage = up to 60k USD position
            
            # Convert quantity to amount (equity allocation ratio)
            if action in ["HOLD", "CLOSE"]:
                # For HOLD/CLOSE: quantity is position ratio (0-1.0) or BTC amount
                if quantity <= 1.0:
                    # Already a ratio
                    amount = quantity
                else:
                    # BTC amount - convert to ratio based on current position
                    if portfolio_metrics:
                        current_position = abs(portfolio_metrics.get("position", 0.0))
                        if current_position > 0.0001:
                            amount = min(quantity / current_position, 1.0)
                        else:
                            amount = 1.0  # Close all if no position
                    else:
                        amount = 1.0  # Default: close all
            else:
                # For BUY/SELL: quantity is BTC amount
                # Convert to equity allocation ratio
                if current_price > 0 and equity > 0:
                    # amount = (quantity * price) / equity
                    notional_value = quantity * current_price
                    amount = min(notional_value / equity, 1.0)
                else:
                    # Fallback: assume quantity is already a ratio
                    amount = min(abs(quantity), 1.0)
            
            # nof1.ai standard: leverage is fixed at 10x
            # But we allow model to specify leverage, then normalize to 10x for nof1.ai style
            if self._settings.use_nof1_style:
                leverage = 10.0  # Fixed 10x leverage for nof1.ai style
            else:
                leverage = self._normalize_leverage(leverage)
            
            # Convert confidence from 0-1 to 0-100
            glm_confidence = confidence * 100.0
            
            # Extract exit plan info
            profit_target = trade_signal_args.get("profit_target")
            stop_loss = trade_signal_args.get("stop_loss")
            invalidation_condition = trade_signal_args.get("invalidation_condition", "")
            risk_usd = trade_signal_args.get("risk_usd", 0.0)
            
            # 🔍 DEBUG: GLM Exit Plan (profit_target removed - not required)
            logger.info("🔍 GLM Exit Plan Debug:")
            logger.info("  └─ stop_loss: %s (type: %s)", stop_loss, type(stop_loss).__name__)
            logger.info("  └─ invalidation_condition: '%s'", invalidation_condition)
            
            # Build exit_plan dict if GLM provided exit plan values (profit_target removed - not required)
            exit_plan = None
            if stop_loss is not None:
                exit_plan = {
                    "stop_loss": stop_loss,
                    "invalidation_condition": invalidation_condition,
                }
                logger.info("✅ GLM Exit Plan created: %s", exit_plan)
            else:
                logger.warning("⚠️ GLM Exit Plan MISSING in JSON - stop_loss is None")
                
                # FALLBACK: Try to extract exit plan from justification text
                # Sometimes GLM provides exit plan in text but not in JSON fields
                if justification and action in ["BUY", "SELL"]:
                    logger.info("🔍 Attempting to extract exit plan from justification text...")
                    import re
                    
                    # Turkish patterns: "Stop loss: 111200"
                    sl_patterns = [
                        r'[Ss]top\s+loss[:\s]+(\d+\.?\d*)',       # Stop loss: 111200
                        r'zarar\s+dur[:\s]+(\d+\.?\d*)',          # zarar dur: 111200
                    ]
                    
                    extracted_sl = None
                    
                    for pattern in sl_patterns:
                        match = re.search(pattern, justification)
                        if match:
                            extracted_sl = float(match.group(1))
                            logger.info("✅ Extracted stop_loss from text: %.2f", extracted_sl)
                            break
                    
                    if extracted_sl:
                        # Successfully extracted from text!
                        exit_plan = {
                            "stop_loss": extracted_sl,
                            "invalidation_condition": invalidation_condition or "Gerekçede belirtilen koşul",
                        }
                        logger.info("✅ GLM Exit Plan extracted from justification text: %s", exit_plan)
                        
                        # Update the trade_signal_args for consistency
                        stop_loss = extracted_sl
                    else:
                        logger.warning("⚠️ Could not extract exit plan from justification text")
                
                # If still no exit plan for BUY/SELL, log warning but let executor handle fallback
                if not exit_plan and action in ["BUY", "SELL"]:
                    logger.warning("⚠️ No valid exit plan for %s action - executor will use fallback", action)
                    # Don't force HOLD here - let executor create fallback exit plan
                    exit_plan = {
                        "stop_loss": None,      # Executor will calculate fallback
                        "invalidation_condition": invalidation_condition or "",
                    }
            
            # Build reasoning
            reasoning_parts = []
            if justification:
                reasoning_parts.append(justification)
            
            # Türkçe mesajlar ekle
            if action == "CLOSE":
                if not justification:  # Sadece justification yoksa ekle
                    reasoning_parts.append("Pozisyonu kapatıyorum")
            elif action in ["BUY", "SELL"]:
                if not justification:  # Sadece justification yoksa ekle
                    action_tr = "LONG" if action == "BUY" else "SHORT"
                    reasoning_parts.append(f"Yeni {action_tr} pozisyon açıyorum")
            elif action == "HOLD":
                if not justification:  # Sadece justification yoksa ekle
                    reasoning_parts.append("Mevcut pozisyonu koruyorum")
            
            if stop_loss:
                reasoning_parts.append(f"Stop loss: {stop_loss:.2f}")
            if invalidation_condition and invalidation_condition.strip() and invalidation_condition.strip().upper() not in ["N/A", "NA", "NONE", ""]:
                reasoning_parts.append(f"Geçersiz kılma koşulu: {invalidation_condition}")
            
            reasoning = " | ".join(reasoning_parts) if reasoning_parts else "Gerekçe belirtilmedi"
            
            # Prepare GLM response JSON for Telegram notification
            # This will be sent in _notify_cycle_complete to avoid duplicate messages
            glm_response_json_data = {
                "type": "GLM_TAM_YANITI",
                "karar": {
                    "action": action,
                    "miktar_btc": quantity,
                    "kaldirac": leverage,
                    "guven": confidence,
                    "yanit_suresi_ms": 0  # Will be updated with actual response time
                },
                "glm_yaniti": {
                    "tam_json": payload,
                    "gerekce_uzunluk": len(justification)
                },
                "portfoy": portfolio_metrics if portfolio_metrics else {},
                "timestamp": response.get("timestamp", ""),
                "token_kullanimi": response.get("usage", {})
            }
            
            # Create decision object
            decision = RiskDecision(
                action=action,
                amount=amount,
                reasoning=reasoning,
                leverage=leverage,  # Already normalized for nof1.ai style
                glm_confidence=max(0.0, min(100.0, glm_confidence)),
                reason_primary=justification[:100] if justification else "",
                reason_secondary="",
                exit_plan=exit_plan,  # GLM's exit plan
                glm_response_json=glm_response_json_data,  # Store JSON for Telegram notification
            )
            
            # Response time bilgisi decision objesine zaten ekleniyor (evaluate() içinde)
            # Telegram mesajı _notify_cycle_complete içinde gönderiliyor
            
            return decision
            
        except json.JSONDecodeError as exc:
            logger.warning("❌ Failed to parse nof1.ai JSON response: %s", exc)
            logger.warning("🔍 Response content (first 500 chars): %s", content[:500])
            logger.warning("🔍 This usually means GLM didn't return valid JSON format")
            
            # 🔧 FALLBACK: Try to extract signal data from malformed JSON using regex
            logger.info("🔧 Attempting fallback parsing from malformed JSON...")
            try:
                import re
                
                # Extract key values using regex patterns (profit_target removed - not required)
                signal_match = re.search(r'"signal":\s*"([^"]+)"', content)
                quantity_match = re.search(r'"quantity":\s*([0-9.]+)', content)
                stop_loss_match = re.search(r'"stop_loss":\s*([0-9.]+)', content)
                leverage_match = re.search(r'"leverage":\s*([0-9.]+)', content)
                confidence_match = re.search(r'"confidence":\s*([0-9.]+)', content)
                justification_match = re.search(r'"gerekçe":\s*"([^"]+)"', content)
                
                if signal_match and quantity_match:
                    # Extract values
                    signal = signal_match.group(1).lower()
                    quantity = float(quantity_match.group(1))
                    stop_loss = float(stop_loss_match.group(1)) if stop_loss_match else None
                    leverage = float(leverage_match.group(1)) if leverage_match else 10.0
                    confidence = float(confidence_match.group(1)) if confidence_match else 0.5
                    justification = justification_match.group(1) if justification_match else "Fallback parsing"
                    
                    # Map signal to action
                    signal_map = {
                        "hold": "HOLD",
                        "close": "CLOSE",
                        "buy": "BUY", 
                        "sell": "SELL",
                    }
                    action = signal_map.get(signal, "HOLD")
                    
                    logger.info("✅ Fallback parsing successful: action=%s, quantity=%.6f", action, quantity)
                    logger.info("  └─ SL: %s, Lev: %.1f, Conf: %.2f", 
                               stop_loss, leverage, confidence)
                    
                    # Build exit plan if we have the data (profit_target removed - not required)
                    exit_plan = None
                    if stop_loss and action in ["BUY", "SELL"]:
                        exit_plan = {
                            "stop_loss": stop_loss,
                            "invalidation_condition": "",
                        }
                        logger.info("✅ Exit plan created from fallback parsing")
                    
                    # Calculate amount (equity ratio) from quantity
                    amount = min(abs(quantity), 1.0)  # Simple fallback
                    
                    return RiskDecision(
                        action=action,
                        amount=amount,
                        reasoning=f"Fallback parsing: {justification} (JSON error: {str(exc)[:50]})",
                        leverage=leverage,
                        glm_confidence=confidence * 100.0,
                        reason_primary=justification[:100] if justification else "Fallback parsing",
                        reason_secondary=f"JSON error recovered",
                        exit_plan=exit_plan,
                    )
                else:
                    logger.error("❌ Fallback parsing failed - could not extract basic signal/quantity")
                    
            except Exception as fallback_exc:
                logger.error("❌ Fallback parsing exception: %s", fallback_exc)
            
            return RiskDecision(action="HOLD", amount=0.0, reasoning=f"JSON parse error: {str(exc)}")
        except Exception as exc:  # noqa: BLE001
            logger.error("❌ Error parsing nof1.ai response: %s", exc, exc_info=True)
            logger.error("🔍 Response content (first 500 chars): %s", content[:500])
            return RiskDecision(action="HOLD", amount=0.0, reasoning=f"Parse error: {str(exc)}")
