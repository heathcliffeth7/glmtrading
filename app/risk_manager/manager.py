import json
import math
import re
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
    # Exit validation for CLOSE decisions (GLM Elite Swing Trader)
    exit_validation: Optional[str] = None  # SL_HIT, TP_HIT, THESIS_INVALID, or N/A
    # Timing and staleness detection
    decision_timestamp: Optional[datetime] = None  # When GLM made this decision
    market_snapshot_timestamp: Optional[datetime] = None  # Timestamp of market data used
    # Market context used during prompt build (to re-use in executor validation)
    context_volatility: Optional[float] = None
    context_atr_pct: Optional[float] = None
    context_vol_ratio: Optional[float] = None
    context_atr_ratio: Optional[float] = None
    
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
            # Varsayılan olarak STALE döndür (güvenlik için), ancak loglarda timestamp hatası görünür olsun
            return 999999.0
        return (datetime.now(timezone.utc) - self.decision_timestamp).total_seconds()


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
        self._signal_logger = get_signal_logger()
        self._settings = get_settings()
        self._runtime_tracker = RuntimeTracker.get_instance()
        self._nof1_prompt_builder = Nof1PromptBuilder()

        # 📊 Monitoring metrics
        self._parsing_metrics = {
            "total_json_requests": 0,
            "successful_json_parsing": 0,
            "json_parsing_errors": 0,
            "fallback_parsing_successes": 0,
            "fallback_parsing_failures": 0,
            "signal_recoveries": 0,
            "complete_failures": 0
        }

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
        except Exception as exc:  # noqa: BLE001
            # Prompt generation failure should not bubble up - return a safe fallback
            logger.error("Prompt build failed: %s", exc, exc_info=True)
            return self._fallback_decision(
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
        
        # === GLM COMPLETE FREEDOM MODE ===
        # No position restrictions, no confidence guardrails
        # Only safety limits: max 3000 USD per trade, max 20x leverage
        
        try:
            response = self._glm.request(prompt_messages, trace_id=trace_id)
            if self._settings.use_nof1_style:
                decision = self._parse_nof1_response(response, portfolio_metrics)
            else:
                decision = self._parse_response(response, portfolio_metrics)
            
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
            decision = self._apply_safety_limits(decision, portfolio_metrics)
            
            # === CONFIDENCE THRESHOLD ENFORCEMENT ===
            # Minimum 80% confidence required for opening positions (BUY/SELL)
            if decision.action in ["BUY", "SELL"] and decision.glm_confidence < 80.0:
                logger.warning(
                    "⚠️ CONFIDENCE TOO LOW: GLM wanted %s with confidence %.1f%% < 80%% → Forcing HOLD",
                    decision.action,
                    decision.glm_confidence
                )
                
                # Force decision to HOLD (preserve context attributes!)
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
                    decision_timestamp=decision.decision_timestamp,
                    market_snapshot_timestamp=decision.market_snapshot_timestamp,
                    context_volatility=decision.context_volatility,
                    context_atr_pct=decision.context_atr_pct,
                    context_vol_ratio=decision.context_vol_ratio,
                    context_atr_ratio=decision.context_atr_ratio,
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

    def _parse_response(self, response: dict, portfolio_metrics: dict | None = None) -> RiskDecision:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # noqa: PERF203
            logger.error("GLM response parse error: %s", exc)
            return RiskDecision(
                action="HOLD", 
                amount=0.0, 
                reasoning="LLM yanıtı okunamadı",
                decision_timestamp=datetime.now(timezone.utc)
            )

        parsed = self._parse_content(content, portfolio_metrics)
        if not parsed:
            logger.warning("GLM output format invalid: %s", content)
            return RiskDecision(
                action="HOLD", 
                amount=0.0, 
                reasoning=content,
                decision_timestamp=datetime.now(timezone.utc)
            )
        return RiskDecision(**parsed)

    def _parse_content(self, content: str, portfolio_metrics: dict | None = None) -> Dict[str, str | float] | None:
        try:
            content = self._prepare_json_payload(content)

            payload = json.loads(content)
            
            # Extract trade details from nested structure if present
            if isinstance(payload, dict) and len(payload) == 1:
                first_key = next(iter(payload))
                if isinstance(payload[first_key], dict) and "trade_signal_args" in payload[first_key]:
                    logger.info("Detected nested NOF1 style response format")
                    signal_args = payload[first_key]["trade_signal_args"]
                    action = signal_args.get("signal", "HOLD").upper()
                    # quantity is usually absolute amount, but we need equity percentage or close ratio
                    # For now, we'll map it directly and let executor handle it
                    # Or better: extract 'quantity' but also look for top-level 'miktar'
                    quantity = float(signal_args.get("quantity", 0))
                    amount = quantity  # Will require adjustment if not equity %
                    
                    leverage = float(signal_args.get("leverage", 5))
                    glm_confidence = float(signal_args.get("confidence", 0)) * 100  # Convert 0-1 to 0-100
                    
                    # Extract exit plan
                    stop_loss = float(signal_args.get("stop_loss", 0))
                    take_profit = float(signal_args.get("take_profit", 0))
                    invalidation = signal_args.get("invalidation_condition", "")
                    
                    exit_plan = {
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "invalidation_condition": invalidation
                    }
                    
                    # Extract reasoning
                    reasoning = payload[first_key].get("gerekçe", "")
                    
                    # Additional fields
                    reason_primary = signal_args.get("reason_primary", "")
                    reason_secondary = signal_args.get("reason_secondary", "")
                    
                else:
                    # Standard flat format
                    action = payload.get("karar", "HOLD").upper()
                    amount = float(payload.get("miktar", 0))
                    leverage = float(payload.get("kaldıraç", 0))
                    reasoning = payload.get("gerekçe", "")
                    glm_confidence = float(payload.get("ai_confidence", 0))
                    reason_primary = payload.get("reason_primary", "")
                    reason_secondary = payload.get("reason_secondary", "")
                    
                    # Try to extract exit plan from flat format if available
                    if "stop_loss" in payload:
                        exit_plan = {
                            "stop_loss": float(payload.get("stop_loss", 0)),
                            "take_profit": float(payload.get("take_profit", 0)),
                            "invalidation_condition": payload.get("invalidation_condition", "")
                        }
                    else:
                        exit_plan = None
            else:
                # Standard flat format fallback
                action = payload.get("karar", "HOLD").upper()
                amount = float(payload.get("miktar", 0))
                leverage = float(payload.get("kaldıraç", 0))
                reasoning = payload.get("gerekçe", "")
                glm_confidence = float(payload.get("ai_confidence", 0))
                reason_primary = payload.get("reason_primary", "")
                reason_secondary = payload.get("reason_secondary", "")
                
                # Try to extract exit plan from flat format if available
                if "stop_loss" in payload:
                    exit_plan = {
                        "stop_loss": float(payload.get("stop_loss", 0)),
                        "take_profit": float(payload.get("take_profit", 0)),
                        "invalidation_condition": payload.get("invalidation_condition", "")
                    }
                else:
                    exit_plan = None
            
            if action not in {"BUY", "SELL", "HOLD", "CLOSE"}:
                return None
                
            # For CLOSE action, amount represents how much of the position to close (1.0 = 100%)
            # For BUY/SELL, ensure it's reasonable
            amount = self._normalize_quantity_to_allocation(action, amount, portfolio_metrics)
            amount = max(0.0, amount)  # Removed upper limit; guardrails handled later
            leverage = self._normalize_leverage(leverage)
            
            # Clamp confidence to 0-100
            glm_confidence = max(0.0, min(100.0, glm_confidence))
            
            # FORCE CLEANUP: For CLOSE and HOLD, exit_plan should be empty or minimal
            if action == "CLOSE":
                # For CLOSE, we don't need stop_loss/take_profit/invalidation
                exit_plan = None
            elif action == "HOLD":
                # For HOLD, we definitely don't want a NEW invalidation condition
                # But we might want to keep existing one? No, this parses the GLM's *new* output.
                # GLM shouldn't output invalidation for HOLD.
                exit_plan = None

            return {
                "action": action,
                "amount": amount,
                "reasoning": reasoning,
                "leverage": leverage,
                "glm_confidence": glm_confidence,
                "reason_primary": reason_primary,
                "reason_secondary": reason_secondary,
                "exit_plan": exit_plan,
            }
        except Exception as e:  # noqa: BLE001
            logger.error("Error parsing GLM content: %s", e)
            return None

    def _prepare_json_payload(self, raw: str) -> str:
        """Clean GLM response so that json.loads accepts multi-line reasoning."""
        raw = raw.strip()
        logger.debug("🔧 Original JSON payload length: %d", len(raw))

        # Extract JSON from markdown code block ANYWHERE in the text
        # GLM often returns: "DÜŞÜNCE: ...text... ```json {...} ```"
        json_block_match = re.search(r'```json\s*([\s\S]*?)```', raw)
        if json_block_match:
            raw = json_block_match.group(1).strip()
            logger.info("📦 Extracted JSON from markdown code block")
        else:
            # Fallback: try to find any code block
            code_block_match = re.search(r'```\s*([\s\S]*?)```', raw)
            if code_block_match:
                raw = code_block_match.group(1).strip()
                logger.info("📦 Extracted content from generic code block")
            else:
                # Legacy fallback: Remove markdown code blocks with old patterns
                if raw.startswith("```json"):
                    raw = raw[7:]
                elif raw.startswith("```"):
                    raw = raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

        # Handle truncated JSON responses - find the last complete JSON object
        brace_count = 0
        last_complete_pos = -1
        in_string = False
        escape_next = False
        last_quote_pos = -1

        for i, ch in enumerate(raw):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                if in_string:
                    last_quote_pos = i
                continue
            if not in_string:
                if ch == '{':
                    brace_count += 1
                elif ch == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        last_complete_pos = i

        # If we found a complete JSON object, handle string truncation
        if last_complete_pos > 0 and last_complete_pos < len(raw) - 1:
            # Check if we're truncating inside a string
            if in_string and last_quote_pos > 0:
                # We're inside a string - close it properly before truncating
                raw = raw[:last_complete_pos + 1]
                # Find the last quote before truncation and close the string there
                truncation_point = min(last_complete_pos, len(raw))
                # Add closing quote if we're truncating mid-string
                if truncation_point > last_quote_pos:
                    raw = raw[:last_quote_pos] + '"' + raw[last_quote_pos + 1:last_complete_pos + 1]
                logger.info("🔧 Fixed truncated string at position %d, closed quote at %d", last_complete_pos, last_quote_pos)
            else:
                raw = raw[:last_complete_pos + 1]
                logger.info("🔧 Truncated JSON to complete object at position %d", last_complete_pos)

        # Enhanced string cleaning for newlines and special characters
        cleaned_chars: list[str] = []
        in_string = False
        escape_next = False
        
        # Pre-process to fix unescaped quotes in "gerekçe" or "reasoning" fields
        # This is a heuristic: if we see "key": "value with "quotes" inside", we try to escape them
        # We look for patterns like: : "..."..."..." and try to fix the inner quotes
        
        for i, ch in enumerate(raw):
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
                    # Check if this is a closing quote
                    # A closing quote should be followed by: whitespace, comma, }, or ]
                    # Or it's the end of the string
                    is_closing = False
                    next_char_idx = i + 1
                    while next_char_idx < len(raw) and raw[next_char_idx].isspace():
                        next_char_idx += 1
                    
                    if next_char_idx < len(raw):
                        next_char = raw[next_char_idx]
                        if next_char in [',', '}', ']']:
                            is_closing = True
                        elif next_char == ':': # Dictionary key closing quote
                            is_closing = True
                    else:
                        is_closing = True # End of string
                    
                    if is_closing:
                        cleaned_chars.append(ch)
                        in_string = False
                    else:
                        # Likely an unescaped quote inside the string
                        cleaned_chars.append('\\"')
                    continue
                
                if ch == "\n":
                    cleaned_chars.append("\\n")
                    continue
                if ch == "\r":
                    continue
                if ch == "\t":
                    cleaned_chars.append("\\t")
                    continue
                if ord(ch) < 32:
                    cleaned_chars.append(f"\\u{ord(ch):04x}")
                    continue
                cleaned_chars.append(ch)
            else:
                cleaned_chars.append(ch)
                if ch == '"':
                    in_string = True

        result = "".join(cleaned_chars)

        # Enhanced JSON repair with multiple attempts
        repair_attempts = [
            self._fix_trailing_commas,
            self._fix_unclosed_strings,
            self._fix_missing_commas,
            self._fix_malformed_numbers,
            self._fix_boolean_null_values,
        ]

        for attempt_name, repair_func in [("trailing commas", self._fix_trailing_commas),
                                         ("unclosed strings", self._fix_unclosed_strings),
                                         ("missing commas", self._fix_missing_commas),
                                         ("malformed numbers", self._fix_malformed_numbers),
                                         ("boolean/null values", self._fix_boolean_null_values)]:
            try:
                # Test if it's valid JSON
                json.loads(result)
                logger.debug("✅ JSON is valid after %s repair", attempt_name)
                return result
            except json.JSONDecodeError as e:
                logger.debug("🔧 Attempting to fix %s: %s", attempt_name, str(e))
                result = repair_func(result)

        # Final validation attempt
        try:
            json.loads(result)
            logger.info("✅ JSON successfully repaired after all attempts")
            return result
        except json.JSONDecodeError as e:
            logger.warning("❌ JSON repair failed after all attempts: %s", str(e))
            logger.debug("🔍 Final JSON content: %s", result[:1000])
            return result

    def _fix_trailing_commas(self, json_str: str) -> str:
        """Remove trailing commas in objects and arrays"""
        # Remove trailing commas before closing braces/brackets
        import re
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        return json_str

    def _fix_unclosed_strings(self, json_str: str) -> str:
        """Fix unclosed string literals"""
        if json_str.count('"') % 2 != 0:
            # Odd number of quotes means unclosed string
            last_quote = json_str.rfind('"')
            if last_quote > 0:
                # Find the best place to close the string
                # Look for next closing brace or bracket
                next_brace = json_str.find('}', last_quote)
                next_bracket = json_str.find(']', last_quote)

                close_pos = min(pos for pos in [next_brace, next_bracket] if pos > last_quote)
                if close_pos > last_quote:
                    json_str = json_str[:close_pos] + '"' + json_str[close_pos:]
                    logger.debug("🔧 Fixed unclosed string in JSON")
        return json_str

    def _fix_missing_commas(self, json_str: str) -> str:
        """Attempt to fix missing commas between JSON elements"""
        import re
        # Add missing commas between object properties (standard case)
        json_str = re.sub(r'"\s*\n\s*"', '",\n    "', json_str)
        
        # Add missing commas between number/bool/null and key
        json_str = re.sub(r'(\d+|true|false|null)\s*\n\s*"', r'\1,\n    "', json_str)
        
        # Add missing commas after object/array closing and key
        json_str = re.sub(r'([}\]])\s*\n\s*"', r'\1,\n    "', json_str)
        
        # Add missing commas between array elements (basic pattern)
        json_str = re.sub(r'([0-9.]+)\s*\n\s*([0-9.]+)', r'\1,\n\2', json_str)
        
        # Add missing commas for inline cases (minified JSON)
        json_str = re.sub(r'(\d+|true|false|null)\s*"', r'\1, "', json_str)
        json_str = re.sub(r'([}\]])\s*"', r'\1, "', json_str)
        
        return json_str

    def _fix_malformed_numbers(self, json_str: str) -> str:
        """Fix malformed numbers (like extra decimals)"""
        import re
        # Fix numbers with multiple decimal points
        json_str = re.sub(r'(\d+\.\d+)\.\d+', r'\1', json_str)
        # Fix numbers that end with decimal point
        json_str = re.sub(r'(\d+)\.([^\d])', r'\1.0\2', json_str)
        return json_str

    def _fix_boolean_null_values(self, json_str: str) -> str:
        """Fix common boolean and null value formatting issues"""
        import re
        # Fix quoted boolean/null values
        json_str = re.sub(r'"true"', 'true', json_str)
        json_str = re.sub(r'"false"', 'false', json_str)
        json_str = re.sub(r'"null"', 'null', json_str)
        # Fix TRUE/FALSE in upper case
        json_str = re.sub(r'\bTRUE\b', 'true', json_str)
        json_str = re.sub(r'\bFALSE\b', 'false', json_str)
        json_str = re.sub(r'\bNULL\b', 'null', json_str)
        return json_str

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
                    context_volatility=decision.context_volatility,
                    context_atr_pct=decision.context_atr_pct,
                    context_vol_ratio=decision.context_vol_ratio,
                    context_atr_ratio=decision.context_atr_ratio,
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
                context_volatility=decision.context_volatility,
                context_atr_pct=decision.context_atr_pct,
                context_vol_ratio=decision.context_vol_ratio,
                context_atr_ratio=decision.context_atr_ratio,
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
                decision_timestamp=datetime.now(timezone.utc),
            )

        # Get confidence band from available signals
        band = self._confidence_band(signals)
        
        # Extra conservative approach when GLM is down
        # Only allow trading if confidence is very high (>= 70%)
        if band["confidence"] < 70.0:
            logger.info(
                "GLM API down - signal confidence %.1f%% below 70%% threshold → HOLD for safety",
                band["confidence"]
            )
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"GLM API unavailable - signal confidence too low ({band['confidence']:.1f}% < 70%): {reason}",
                leverage=5.0,
                decision_timestamp=datetime.now(timezone.utc),
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
            decision_timestamp=datetime.now(timezone.utc),
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
            if decision.glm_confidence >= 90:
                max_amount = 0.25  # Very high confidence
            elif decision.glm_confidence >= 85:
                max_amount = 0.20  # High confidence
            else:  # 80-84
                max_amount = 0.15  # Moderate-high confidence
            
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
                    exit_plan=decision.exit_plan,
                    glm_response_json=decision.glm_response_json,
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
                decision_timestamp=decision.decision_timestamp,
                market_snapshot_timestamp=decision.market_snapshot_timestamp,
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
            system_message = """Sen SERMAYEYİ KORUMA ÖNCELİKLİ bir profesyonel kripto türev traderısın.

TEMEL PRENSİP: "Capital Preservation First, Alpha Second"
- Önce sermayeni koru, sonra kar fırsatlarını değerlendir
- Şüphe durumunda HOLD - agresif olmak yerine sabırlı ol
- Stop-loss'ları sıkı tut, R:R en az 2:1 olmalı
- Tek bir işlemde portföyün %2'sinden fazla risk alma
- Fırsatları kaçırmaktan korkma - kötü bir trade açmak, fırsat kaçırmaktan daha kötü

KRİTİK KURALLAR:
1. JSON yanıtındaki 'reasoning' alanı HER ZAMAN TÜRKÇE yazılmalıdır - İngilizce yazmak YASAKTIR
2. Her karar için TÜM timeframe'leri (1m, 30m, 4h) analiz et
3. Fiyat seviyelerini, destek/direnç noktalarını KESIN RAKAMLARLA açıkla
4. HOLD kararları da detaylı gerekçe gerektirir
5. Emin değilsen → HOLD"""
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
    
    def _normalize_quantity_to_allocation(
        self,
        action: str,
        quantity: float,
        portfolio_metrics: dict | None = None,
    ) -> float:
        """
        Interpret GLM 'quantity' as equity allocation (0-1) and gracefully handle
        backwards-compatible coin-amount outputs.
        """
        price = 0.0
        equity = 10000.0
        current_position = 0.0

        if portfolio_metrics:
            price = portfolio_metrics.get("price", 0.0) or 0.0
            equity = portfolio_metrics.get("equity", 10000.0) or 10000.0
            current_position = abs(portfolio_metrics.get("position", 0.0) or 0.0)

        base_qty = abs(quantity)

        if action in ["HOLD", "CLOSE"]:
            if base_qty <= 1.0:
                return max(0.0, min(base_qty, 1.0))

            if current_position > 0.0:
                ratio = min(base_qty / current_position, 1.0)
                logger.info("🧮 Converted coin close amount %.6f to close ratio %.4f", base_qty, ratio)
                return ratio

            # No position info → default to closing everything
            return 1.0

        # BUY / SELL
        if base_qty <= 1.0:
            return max(0.0, min(base_qty, 1.0))

        if price > 0.0 and equity > 0.0:
            ratio = (base_qty * price) / equity
            logger.info(
                "🧮 Converted coin amount %.6f to equity ratio %.4f (price=%.2f, equity=%.2f, notional=%.2f)",
                base_qty,
                ratio,
                price,
                equity,
                base_qty * price,
            )
            return ratio

        # Fallback: preserve raw value if we cannot price it
        return base_qty
    
    def _parse_nof1_response(self, response: dict, portfolio_metrics: dict | None = None) -> RiskDecision:
        """Parse nof1.ai style JSON response."""
        # 📊 Track total requests
        self._parsing_metrics["total_json_requests"] += 1

        try:
            raw_content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # noqa: PERF203
            logger.error("GLM response parse error: %s", exc)
            self._parsing_metrics["complete_failures"] += 1
            return RiskDecision(
                action="HOLD", 
                amount=0.0, 
                reasoning="LLM yanıtı okunamadı",
                decision_timestamp=datetime.now(timezone.utc)
            )
        
        # Log the raw response for debugging
        response_tokens = len(raw_content) // 4  # Rough estimate
        logger.info("📥 GLM Response: %d chars, ~%d tokens (estimated)", len(raw_content), response_tokens)
        logger.info("🔍 Raw GLM response (first 500 chars): %s", raw_content[:500])

        # Empty-content guard: attempt alternative fields, else hard HOLD to avoid JSON parse spam
        if not raw_content or not raw_content.strip():
            choices = response.get("choices", []) if isinstance(response, dict) else []
            alt_content = ""
            if choices:
                choice0 = choices[0] or {}
                alt_content = (
                    choice0.get("text")
                    or choice0.get("delta", {}).get("content")
                    or choice0.get("message", {}).get("delta", {}).get("content", "")
                )
            if alt_content and alt_content.strip():
                logger.warning("📌 GLM content empty, using alternative field (%d chars)", len(alt_content))
                raw_content = alt_content
            else:
                logger.error("❌ GLM returned empty content in all known fields - forcing HOLD fallback")
                self._parsing_metrics["complete_failures"] += 1
                return RiskDecision(
                    action="HOLD",
                    amount=0.0,
                    reasoning="GLM boş yanıt verdi",
                    decision_timestamp=datetime.now(timezone.utc)
                )
        
        # Extract JSON from response
        content = self._prepare_json_payload(raw_content)
        
        try:
            payload = json.loads(content)
            # 📊 Track successful JSON parsing
            self._parsing_metrics["successful_json_parsing"] += 1
            logger.debug("📊 JSON parsing successful - metrics: %s", self._parsing_metrics)

            # Dynamic key handling for multi-symbol support
            symbol_data = {}
            detected_symbol = None

            # First try standard keys
            if "BTCUSDT" in payload:
                symbol_data = payload["BTCUSDT"]
                detected_symbol = "BTCUSDT"
            elif "ETHUSDT" in payload:
                symbol_data = payload["ETHUSDT"]
                detected_symbol = "ETHUSDT"
            elif "SOLUSDT" in payload:
                symbol_data = payload["SOLUSDT"]
                detected_symbol = "SOLUSDT"
            elif "BTC" in payload:
                symbol_data = payload["BTC"]
                detected_symbol = "BTCUSDT"
            else:
                # Try to find ANY key that looks like a symbol
                for key, value in payload.items():
                    if isinstance(value, dict):
                        symbol_data = value
                        detected_symbol = key
                        logger.info("🔍 Found dynamic symbol key: %s", key)
                        break

            # ============================================================
            # NEW SIMPLIFIED FORMAT: {"SYMBOL": {"signal": "BUY", "confidence": 85, "reasoning": "..."}}
            # GLM only decides signal + confidence, Python calculates exit plan
            # ============================================================
            if "signal" in symbol_data and "trade_signal_args" not in symbol_data:
                logger.info("🆕 Detected SIMPLIFIED GLM format - Python will calculate exit plan")

                signal = symbol_data.get("signal", "HOLD").upper()
                confidence = float(symbol_data.get("confidence", 0))
                reasoning = symbol_data.get("reasoning", "") or symbol_data.get("gerekçe", "")
                # Extract exit_validation field for CLOSE decisions (GLM Elite Swing Trader)
                exit_validation = symbol_data.get("exit_validation", "N/A")

                # Map signal to action
                signal_map = {
                    "HOLD": "HOLD",
                    "CLOSE": "CLOSE",
                    "CLOSE_POSITION": "CLOSE",
                    "BUY": "BUY",
                    "SELL": "SELL",
                    "LONG": "BUY",
                    "SHORT": "SELL",
                }
                action = signal_map.get(signal.upper(), "HOLD")

                logger.info("🔍 Simplified GLM response: signal=%s, confidence=%.1f, action=%s, exit_validation=%s",
                           signal, confidence, action, exit_validation)

                # For HOLD/CLOSE - no exit plan needed
                if action in ["HOLD", "CLOSE"]:
                    # Log exit_validation for CLOSE decisions
                    if action == "CLOSE":
                        valid_exit_validations = {"SL_HIT", "TP_HIT", "THESIS_INVALID"}
                        if exit_validation not in valid_exit_validations:
                            logger.warning(
                                "⚠️ CLOSE decision without valid exit_validation: '%s' (valid: %s)",
                                exit_validation, valid_exit_validations
                            )

                    return RiskDecision(
                        action=action,
                        amount=0.0,
                        reasoning=reasoning or f"GLM karar: {action}",
                        leverage=10.0,
                        glm_confidence=max(0.0, min(100.0, confidence)),
                        reason_primary=reasoning[:100] if reasoning else action,
                        reason_secondary="",
                        exit_plan=None,
                        exit_validation=exit_validation,  # GLM Elite Swing Trader field
                        decision_timestamp=datetime.now(timezone.utc),
                    )

                # For BUY/SELL - Python calculates exit plan
                if action in ["BUY", "SELL"]:
                    # Get current price from portfolio_metrics or use a fallback
                    current_price = 0.0
                    if portfolio_metrics:
                        current_price = portfolio_metrics.get("current_price", 0.0) or portfolio_metrics.get("mark_price", 0.0)

                    if current_price <= 0:
                        logger.warning("⚠️ Cannot calculate exit plan - current_price not available")
                        return RiskDecision(
                            action="HOLD",
                            amount=0.0,
                            reasoning="Exit plan hesaplanamadı - fiyat bilgisi yok",
                            decision_timestamp=datetime.now(timezone.utc),
                        )

                    # Get historical arrays from cached data (stored during prompt building)
                    historical_arrays = getattr(self._nof1_prompt_builder, '_cached_historical_arrays', {})

                    # Calculate exit plan using Python
                    exit_plan_data = self._nof1_prompt_builder.calculate_exit_plan(
                        signal=action,
                        entry_price=current_price,
                        historical_arrays=historical_arrays,
                    )

                    logger.info("🧮 Python calculated exit plan: SL=%.2f, TP=%.2f, R:R=%.2f, leverage=%d",
                               exit_plan_data["stop_loss"], exit_plan_data["profit_target"],
                               exit_plan_data["rr_ratio"], exit_plan_data["leverage"])

                    # Calculate position size based on risk percentage
                    equity = portfolio_metrics.get("equity", 10000.0) if portfolio_metrics else 10000.0
                    risk_pct = 0.02  # 2% risk per trade
                    sl_distance_pct = exit_plan_data["sl_distance_pct"]
                    leverage = exit_plan_data["leverage"]

                    # Position size = (equity * risk_pct) / (sl_distance_pct / 100) / leverage
                    # This gives us the position size in USD
                    if sl_distance_pct > 0:
                        position_usd = (equity * risk_pct) / (sl_distance_pct / 100)
                        quantity_usd = min(position_usd, 3000.0)  # Cap at 3000 USD margin
                        quantity_coin = quantity_usd / current_price
                    else:
                        quantity_coin = 0.0

                    logger.info("🧮 Python calculated quantity: %.6f (equity=%.2f, risk=%.1f%%, sl_dist=%.2f%%)",
                               quantity_coin, equity, risk_pct * 100, sl_distance_pct)

                    exit_plan = {
                        "stop_loss": exit_plan_data["stop_loss"],
                        "profit_target": exit_plan_data["profit_target"],
                        "invalidation_condition": exit_plan_data["invalidation_condition"],
                    }

                    # Build reasoning
                    reasoning_full = f"{reasoning} | SL: {exit_plan_data['stop_loss']:.2f} | TP: {exit_plan_data['profit_target']:.2f} | R:R: {exit_plan_data['rr_ratio']:.2f}"

                    return RiskDecision(
                        action=action,
                        amount=quantity_coin,
                        reasoning=reasoning_full,
                        leverage=leverage,
                        glm_confidence=max(0.0, min(100.0, confidence)),
                        reason_primary=reasoning[:100] if reasoning else f"{action} signal",
                        reason_secondary=f"R:R {exit_plan_data['rr_ratio']:.2f}",
                        exit_plan=exit_plan,
                        exit_validation=exit_validation,  # GLM Elite Swing Trader field (N/A for new positions)
                        decision_timestamp=datetime.now(timezone.utc),
                    )

            # ============================================================
            # OLD FORMAT: {"SYMBOL": {"trade_signal_args": {...}, "justification": "..."}}
            # Backward compatibility for old GLM responses
            # ============================================================
            trade_signal_args = symbol_data.get("trade_signal_args", {})
            # Justification is inside trade_signal_args (per prompt), with fallbacks for backward compatibility
            justification = (
                trade_signal_args.get("justification", "") or  # Primary: as specified in prompt
                trade_signal_args.get("gerekçe", "") or        # Turkish version inside args
                symbol_data.get("justification", "") or            # Fallback: outside args (English)
                symbol_data.get("gerekçe", "")                     # Fallback: outside args (Turkish)
            )

            # Sanitization: Check for Python error messages in justification
            if justification and ("name '" in justification and "' is not defined" in justification):
                logger.error("🚨 Detected Python error message in GLM justification: %s", justification)
                justification = "Gerekçe oluşturulurken teknik bir hata oluştu (AI response contained error pattern)."

            if not trade_signal_args:
                logger.warning("❌ No trade_signal_args found in response.")
                logger.warning("🔍 Payload structure: %s", json.dumps(payload, indent=2)[:500])
                logger.warning("🔍 Available keys: %s", list(payload.keys()))
                return RiskDecision(
                    action="HOLD",
                    amount=0.0,
                    reasoning="Invalid response format - missing trade_signal_args or simplified format",
                    decision_timestamp=datetime.now(timezone.utc)
                )

            signal = trade_signal_args.get("signal", "hold").lower()
            quantity = float(trade_signal_args.get("quantity") or 0)
            leverage = float(trade_signal_args.get("leverage") or 10)
            confidence = float(trade_signal_args.get("confidence") or 0.5) * 100  # Convert 0-1 to 0-100 percentage

            # Debug log to see what justification we got
            logger.info("🔍 GLM justification received (old format): '%s'", justification)
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
            
            # NOF1.AI format (per prompt): quantity is EQUITY ALLOCATION (0.0-1.0)
            # Backward compatibility: if GLM returns a value > 1.0 we treat it as
            # coin amount and convert to equity ratio using current price/equity.
            
            # NOTE: Position size limiting is handled by _apply_safety_limits()
            # No arbitrary % of equity limit here - GLM has freedom within safety limits
            # Safety limits: max 3000 USD margin, max 20x leverage = up to 60k USD position
            
            amount = self._normalize_quantity_to_allocation(action, quantity, portfolio_metrics)
            
            # nof1.ai standard: leverage is fixed at 10x
            # But we allow model to specify leverage, then normalize to 10x for nof1.ai style
            if self._settings.use_nof1_style:
                leverage = 10.0  # Fixed 10x leverage for nof1.ai style
            else:
                leverage = self._normalize_leverage(leverage)
            
            # Confidence is already 0-100 from previous conversion
            glm_confidence = confidence
            
            # Extract exit plan info
            profit_target = trade_signal_args.get("profit_target")
            if profit_target is None:
                # Backward compatibility: bazı GLM yanıtları take_profit anahtarını kullanıyor
                profit_target = trade_signal_args.get("take_profit")
                if profit_target is not None:
                    trade_signal_args["profit_target"] = profit_target
            stop_loss = trade_signal_args.get("stop_loss")
            invalidation_condition = trade_signal_args.get("invalidation_condition", "")
            
            # FORCE EXIT PLAN EMPTY IF HOLD OR CLOSE
            if action in ["HOLD", "CLOSE"]:
                profit_target = None
                stop_loss = None
                invalidation_condition = ""
            risk_usd = trade_signal_args.get("risk_usd", 0.0)
            
            # 🔍 DEBUG: GLM Exit Plan (profit_target removed - not required)
            logger.info("🔍 GLM Exit Plan Debug:")
            logger.info("  └─ profit_target: %s", profit_target)
            logger.info("  └─ stop_loss: %s (type: %s)", stop_loss, type(stop_loss).__name__)
            logger.info("  └─ invalidation_condition: '%s'", invalidation_condition)
            
            # For BUY/SELL: require numeric stop_loss and profit_target and non-empty invalidation_condition
            def _safe_float(val):
                try:
                    if isinstance(val, str):
                        val = val.replace(",", "")
                    return float(val)
                except Exception:
                    return None

            def _extract_exit_plan_from_text(text: str):
                """Extract SL/TP/invalid from justification exit summary line."""
                if not text:
                    return None, None, None
                import re
                price_pattern = r"\$?([0-9][0-9,]*(?:\.[0-9]+)?)"
                sl = None
                tp = None
                inv = None
                sl_patterns = [
                    rf"[Ss]top\\s+loss[:\\s]+{price_pattern}",
                    rf"stop\\s*loss[^0-9]{{0,80}}?{price_pattern}",
                    rf"zarar\\s*durdur[^0-9]{{0,80}}?{price_pattern}",
                    rf"SL[:\\s]+{price_pattern}",
                ]
                tp_patterns = [
                    rf"(Take\\s+profit|TP|Kar\\s+al)[:\\s]+{price_pattern}",
                    rf"(k[aâ]r|kar)\\s+hedefi.*?([0-9][0-9,]{{2,}}(?:\\.[0-9]+)?)",
                    rf"profit\\s+target.*?([0-9][0-9,]{{2,}}(?:\\.[0-9]+)?)",
                ]
                inv_patterns = [
                    r"Geçersiz\\s+kılma(?:\\s+koşulu)?[:\\s]+([^|\\n]+)",
                    r"Gecersiz\\s+kilma(?:\\s+kosulu)?[:\\s]+([^|\\n]+)",
                    r"Invalidation\\s+condition[:\\s]+([^|\\n]+)",
                    r"(If price closes[^|\\n]+)",
                ]
                flags = re.IGNORECASE | re.DOTALL
                for pattern in sl_patterns:
                    match = re.search(pattern, text, flags)
                    if match:
                        sl = _safe_float(match.group(1))
                        break
                for pattern in tp_patterns:
                    match = re.search(pattern, text, flags)
                    if match:
                        # TP patterns may capture the number in group 2 when prefix exists, otherwise group 1
                        tp_group = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)
                        tp = _safe_float(tp_group)
                        break
                for pattern in inv_patterns:
                    match = re.search(pattern, text, flags)
                    if match:
                        inv = match.group(1).strip()
                        break
                return sl, tp, inv

            if action in ["BUY", "SELL"]:
                stop_loss_val = _safe_float(stop_loss)
                profit_target_val = _safe_float(profit_target)
                inv_text = (invalidation_condition or "").strip()

                parsed_sl, parsed_tp, parsed_inv = _extract_exit_plan_from_text(justification)

                # Prefer justification-derived values to keep exit plan aligned with Gerekçe
                if parsed_sl is not None:
                    if stop_loss_val is not None and abs(stop_loss_val - parsed_sl) > 1e-9:
                        logger.warning("⚠️ Exit plan mismatch (SL) JSON=%s parsed=%s - using justification value", stop_loss, parsed_sl)
                    stop_loss_val = parsed_sl
                if parsed_tp is not None:
                    if profit_target_val is not None and abs(profit_target_val - parsed_tp) > 1e-9:
                        logger.warning("⚠️ Exit plan mismatch (TP) JSON=%s parsed=%s - using justification value", profit_target, parsed_tp)
                    profit_target_val = parsed_tp
                if parsed_inv:
                    if inv_text and parsed_inv != inv_text:
                        logger.warning("⚠️ Exit plan mismatch (invalid) JSON='%s' parsed='%s' - using justification value", inv_text, parsed_inv)
                    inv_text = parsed_inv

                if stop_loss_val is None or profit_target_val is None or not inv_text:
                    logger.warning(
                        "❌ GLM exit plan missing/invalid fields for %s: stop_loss=%s, profit_target=%s, invalidation='%s' (parsed sl=%s tp=%s inv=%s)",
                        action, stop_loss, profit_target, invalidation_condition, parsed_sl, parsed_tp, parsed_inv
                    )
                    return RiskDecision(
                        action="HOLD",
                        amount=0.0,
                        reasoning="GLM exit plan eksik/hatalı (stop_loss, profit_target ve invalidation_condition zorunlu)",
                        reason_primary="EXIT_PLAN_REQUIRED",
                        reason_secondary="GLM exit plan eksik/hatalı",
                        decision_timestamp=datetime.now(timezone.utc),
                    )

                stop_loss = stop_loss_val
                profit_target = profit_target_val
                invalidation_condition = inv_text

            # Build exit_plan dict if GLM provided exit plan values (BUY/SELL only)
            exit_plan = None
            if stop_loss is not None and profit_target is not None and action in ["BUY", "SELL"]:
                exit_plan = {
                    "stop_loss": stop_loss,
                    "profit_target": profit_target,
                    "invalidation_condition": invalidation_condition,
                }
                logger.info("✅ GLM Exit Plan created: %s", exit_plan)
            else:
                # No exit plan needed/allowed for HOLD/CLOSE
                exit_plan = None
            
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
            
            if profit_target:
                reasoning_parts.append(f"Take profit: {profit_target:.2f}")
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
            # 📊 Track JSON parsing errors
            self._parsing_metrics["json_parsing_errors"] += 1
            logger.error("❌ JSON parsing failed in nof1.ai response: %s", exc)
            logger.error("🔍 Error details: %s", str(exc))
            logger.error("📍 Error location: Line %d, Column %d, Character %d",
                        exc.lineno, exc.colno, exc.pos)
            logger.error("🔍 Raw response content (first 1000 chars): %s", raw_content[:1000])
            logger.error("🔍 Raw response content (last 500 chars): %s", raw_content[-500:])
            logger.error("🔍 Full response length: %d characters", len(raw_content))

            # Show context around error location
            if exc.pos and len(raw_content) > exc.pos:
                start_pos = max(0, exc.pos - 100)
                end_pos = min(len(raw_content), exc.pos + 100)
                error_context = raw_content[start_pos:end_pos]
                logger.error("🔍 Context around error (pos %d): %s", exc.pos, error_context)

            logger.warning("🔧 This usually means GLM didn't return valid JSON format - attempting fallback parsing")
            
            # 🔧 FALLBACK: Enhanced signal extraction with multiple regex patterns
            logger.info("🔧 Attempting enhanced fallback parsing from malformed JSON...")
            extracted_data = self._extract_trading_signals_enhanced(raw_content)

        if extracted_data:
            # 📊 Track fallback parsing success (even if some fields are defaulted)
            self._parsing_metrics["fallback_parsing_successes"] += 1
            self._parsing_metrics["signal_recoveries"] += 1
            logger.info("📊 Fallback parsing succeeded - metrics: %s", self._parsing_metrics)

            raw_signal = extracted_data.get("signal", "HOLD")
            signal = raw_signal.lower()
            quantity = extracted_data.get("quantity", 0.0)
            stop_loss = extracted_data.get("stop_loss")
            profit_target = extracted_data.get("profit_target") or extracted_data.get("take_profit")
            leverage = extracted_data.get("leverage", 10.0)
            confidence = extracted_data.get("confidence", 0.0)
            justification = extracted_data.get("justification", "Enhanced fallback parsing")
            
            # Sanitization for fallback parsing too
            if justification and ("name '" in justification and "' is not defined" in justification):
                logger.error("🚨 Detected Python error message in fallback justification: %s", justification)
                justification = "Gerekçe oluşturulurken teknik bir hata oluştu (Fallback parsed error pattern)."
            
            invalidation_condition = extracted_data.get("invalidation_condition", "")

            # Map signal to action
            signal_map = {
                "hold": "HOLD",
                "close": "CLOSE",
                "buy": "BUY",
                "sell": "SELL",
                "long": "BUY",  # Support long/short aliases
                "short": "SELL",
                "al": "BUY",   # Turkish aliases
                "sat": "SELL",
                "bekle": "HOLD",
                "kapat": "CLOSE"
            }
            action = signal_map.get(signal, "HOLD")

            if quantity == 0.0 and action in ["BUY", "SELL"]:
                logger.warning("⚠️ Fallback parsing missing quantity for %s - defaulting to 0 (skip execution)", action)
            logger.info("✅ Enhanced fallback parsing normalized: action=%s, quantity=%.6f, leverage=%.1f, conf=%.2f", action, quantity, leverage, confidence)

            # Build exit plan if we have the data
            exit_plan = None
            if stop_loss and action in ["BUY", "SELL"]:
                exit_plan = {
                    "stop_loss": stop_loss,
                    "profit_target": profit_target,
                    "invalidation_condition": invalidation_condition,
                }
                logger.info("✅ Exit plan created from enhanced fallback parsing")

            # Calculate amount (equity ratio) from quantity (safety limits will cap execution)
            amount = abs(quantity)

            return RiskDecision(
                action=action,
                amount=amount,
                reasoning=f"Enhanced fallback parsing: {justification}",
                leverage=leverage,
                glm_confidence=max(0.0, min(100.0, confidence * 100.0)),
                reason_primary=justification[:100] if justification else "Enhanced fallback parsing",
                reason_secondary="JSON parse error recovered - signal extracted",
                exit_plan=exit_plan,
                decision_timestamp=datetime.now(timezone.utc),
            )

        # 📊 Track fallback parsing failure
        self._parsing_metrics["fallback_parsing_failures"] += 1
        logger.error("❌ Enhanced fallback parsing failed - could not extract essential signal data")
        logger.error("📊 Fallback parsing failed - metrics: %s", self._parsing_metrics)
        logger.debug("🔍 Extracted data: %s", extracted_data)
        
        return RiskDecision(
            action="HOLD",
            amount=0.0,
            reasoning="Critical failure: JSON parsing and enhanced fallback both failed to extract signal",
            leverage=5.0,
            glm_confidence=0.0,
            reason_primary="Parsing Error",
            reason_secondary="Could not extract signal from GLM response",
            decision_timestamp=datetime.now(timezone.utc)
        )

    def _extract_trading_signals_enhanced(self, content: str) -> dict:
        """Enhanced signal extraction with multiple pattern strategies"""
        import re

        # 📊 Track extraction metrics
        extraction_metrics = {
            "patterns_tried": 0,
            "successful_extractions": 0,
            "failed_fields": [],
            "used_patterns": {}
        }
        
        logger.info("🔍 Starting enhanced fallback parsing - content length: %d chars", len(content))
        logger.debug("🔍 Raw content preview: %s", content[:200])

        # Result dictionary
        data = {}

        # Multiple pattern strategies for each field - ENHANCED VERSION
        strategies = {
            "signal": [
                # Standard JSON patterns
                r'"signal":\s*"([^"]+)"',
                r'"signal":\s*([\'"])(.*?)\1',
                r'"karar":\s*"([^"]+)"',  # Turkish decision
                
                # Flexible assignment patterns
                r'signal["\']?\s*[:=]\s*["\']?(\w+)',
                r'decision["\']?\s*[:=]\s*["\']?(\w+)',
                r'karar["\']?\s*[:=]\s*["\']?(\w+)',
                
                # Natural language patterns - English
                r'(?:recommend|suggest|advise)\s+(?:opening|to\s+open)\s+(a\s+)?(BUY|SELL|LONG|SHORT)\s+position',
                r'(?:strong|aggressive|moderate|weak)\s+(BUY|SELL|LONG|SHORT)',
                r'(?:open|enter|take)\s+(a\s+)?(BUY|SELL|LONG|SHORT)\s+(?:position|trade)',
                r'(?:go|be)\s+(LONG|SHORT)',
                
                # Natural language patterns - Turkish
                r'(?:alış|satış|long|short)\s+(?:sinyali|yap|aç)',
                r'(?:güçlü|agresif|ılımlı|zayıf)\s+(?:alış|satış|LONG|SHORT)',
                r'(?:yeni|aç)\s+(?:bir\s+)?(LONG|SHORT|alış|satış)\s+(?:pozisyon|pozisyonu)',
                
                # Standalone action patterns
                r'(BUY|SELL|HOLD|CLOSE|LONG|SHORT|AL|SAT|BEKLE|KAPAT)(?=\s|,|}|$)',
                r'\b(BUY|SELL|HOLD|CLOSE|LONG|SHORT|AL|SAT|BEKLE|KAPAT)\b',
                
                # Context patterns
                r'action["\']?\s*[:=]\s*["\']?(\w+)',
                r'recommendation["\']?\s*[:=]\s*["\']?(\w+)',
                r'trade["\']?\s*[:=]\s*["\']?(\w+)',
            ],
            "quantity": [
                # Standard JSON patterns
                r'"quantity":\s*([0-9.]+)',
                r'"miktar":\s*([0-9.]+)',  # Turkish quantity
                r'"amount":\s*([0-9.]+)',
                r'"size":\s*([0-9.]+)',
                r'"position_size":\s*([0-9.]+)',
                
                # Flexible assignment patterns
                r'quantity["\']?\s*[:=]\s*([0-9.]+)',
                r'miktar["\']?\s*[:=]\s*([0-9.]+)',
                r'amount["\']?\s*[:=]\s*([0-9.]+)',
                r'size["\']?\s*[:=]\s*([0-9.]+)',
                
                # Context patterns - after coin/symbol
                r'"coin":\s*"[^"]*".*?([0-9.]+)(?=\s|,|})',
                r'BTCUSDT.*?([0-9.]+)(?=\s|,|})',
                r'bitcoin.*?([0-9.]+)\s*(?:BTC|units)',
                
                # Natural language patterns
                r'([0-9.]+)\s*(?:BTC|bitcoin|units)',
                r'([0-9.]+)\s*(?:units?|contracts?)',
                r'(?:buy|sell|open)\s+([0-9.]+)\s*(?:BTC|bitcoin)',
                
                # Turkish patterns
                r'([0-9.]+)\s*(?:BTC|bitcoin|birim)',
                r'(?:al|sat)\s+([0-9.]+)\s*(?:BTC|bitcoin)',
            ],
            "stop_loss": [
                # Standard JSON patterns
                r'"stop_loss":\s*([0-9.]+)',
                r'"stop-loss":\s*([0-9.]+)',
                r'"sl":\s*([0-9.]+)',
                r'"stoploss":\s*([0-9.]+)',
                
                # Flexible assignment patterns
                r'stop[_\s]?loss["\']?\s*[:=]\s*([0-9.]+)',
                r'sl["\']?\s*[:=]\s*([0-9.]+)',
                r'stoploss["\']?\s*[:=]\s*([0-9.]+)',
                
                # Natural language patterns - English
                r'stop(?:\s+loss)?\s+(?:at|@)\s*\$?([0-9.]+)',
                r'set(?:\s+stop(?:\s+loss)?)?\s+(?:at|@)\s*\$?([0-9.]+)',
                r'cut\s+loss\s+(?:at|@)\s*\$?([0-9.]+)',
                
                # Natural language patterns - Turkish
                r'zarar(?:\s+dur)?\s+(?:at|@|\$)?\s*([0-9.]+)',
                r'stop(?:\s+loss)?\s+(?:at|@|\$)?\s*([0-9.]+)',
                r'kes(?:\s+zarar)?\s+(?:at|@|\$)?\s*([0-9.]+)',
                
                # Context patterns
                r'exit\s+(?:if|when|at)\s+\$?([0-9.]+)',
                r'close\s+(?:if|when|at)\s+\$?([0-9.]+)',
            ],
            "profit_target": [
                # Standard JSON patterns
                r'"profit_target":\s*([0-9.]+)',
                r'"profit-target":\s*([0-9.]+)',
                r'"target":\s*([0-9.]+)',
                r'"take_profit":\s*([0-9.]+)',
                r'"tp":\s*([0-9.]+)',
                
                # Flexible assignment patterns
                r'profit[_\s]?target["\']?\s*[:=]\s*([0-9.]+)',
                r'take[_\s]?profit["\']?\s*[:=]\s*([0-9.]+)',
                r'tp["\']?\s*[:=]\s*([0-9.]+)',
                r'target["\']?\s*[:=]\s*([0-9.]+)',
                
                # Natural language patterns - English
                r'target(?:\s+price)?\s+(?:at|@)\s*\$?([0-9.]+)',
                r'take\s+profit\s+(?:at|@)\s*\$?([0-9.]+)',
                r'tp\s+(?:at|@)\s*\$?([0-9.]+)',
                
                # Natural language patterns - Turkish
                r'kar(?:\s+al)?\s+(?:at|@|\$)?\s*([0-9.]+)',
                r'hedef(?:\s+fiyat)?\s+(?:at|@|\$)?\s*([0-9.]+)',
                r'tp(?:\s+seviyesi)?\s+(?:at|@|\$)?\s*([0-9.]+)',
                
                # Context patterns
                r'target\s+(?:is)?\s+\$?([0-9.]+)',
            ],
            "leverage": [
                # Standard JSON patterns
                r'"leverage":\s*([0-9.]+)',
                r'"kaldirac":\s*([0-9.]+)',  # Turkish leverage
                r'"leverage_ratio":\s*([0-9.]+)',
                
                # Flexible assignment patterns
                r'leverage["\']?\s*[:=]\s*([0-9.]+)',
                r'kaldirac["\']?\s*[:=]\s*([0-9.]+)',
                r'leverage_ratio["\']?\s*[:=]\s*([0-9.]+)',
                
                # Multiplier patterns
                r'(\d+)x(?:\s*(?:leverage|kaldirac))?',
                r'(\d+)\s*(?:x|times)\s*(?:leverage|kaldirac)?',
                
                # Natural language patterns
                r'use\s+(\d+)x\s*(?:leverage|kaldirac)?',
                r'(\d+)\s*(?:times|kat)\s*(?:leverage|kaldirac)?',
                
                # Turkish patterns
                r'(\d+)\s*kat\s*(?:kaldıraç|kaldirac)?',
                r'kaldıraç["\']?\s*[:=]\s*([0-9.]+)',
            ],
            "confidence": [
                # Standard JSON patterns
                r'"confidence":\s*([0-9.]+)',
                r'"guven":\s*([0-9.]+)',  # Turkish confidence
                r'"confidence_level":\s*([0-9.]+)',
                r'"certainty":\s*([0-9.]+)',
                
                # Flexible assignment patterns
                r'confidence["\']?\s*[:=]\s*([0-9.]+)',
                r'guven["\']?\s*[:=]\s*([0-9.]+)',
                r'confidence_level["\']?\s*[:=]\s*([0-9.]+)',
                r'certainty["\']?\s*[:=]\s*([0-9.]+)',
                
                # Percentage patterns
                r'(\d+)%?\s*(?:confidence|guven|certainty)',
                r'(\d+)%?\s*(?:guven|seviye|level)',
                r'(?:confidence|guven|certainty)\s+(?:of|at)?\s*(\d+)%?',
                
                # Natural language patterns
                r'(?:high|very\s+high|strong)\s+confidence.*?(\d+)%?',
                r'(?:low|very\s+low|weak)\s+confidence.*?(\d+)%?',
                
                # Turkish patterns
                r'(?:yüksek|çok\s+yüksek|güçlü)\s+guven.*?(\d+)%?',
                r'(?:düşük|çok\s+düşük|zayıf)\s+guven.*?(\d+)%?',
            ],
            "justification": [
                # Standard JSON patterns
                r'"gerekçe":\s*"([^"]+)"',
                r'"gerekce":\s*"([^"]+)"',
                r'"reasoning":\s*"([^"]+)"',
                r'"justification":\s*"([^"]+)"',
                r'"explanation":\s*"([^"]+)"',
                r'"analysis":\s*"([^"]+)"',
                
                # Flexible assignment patterns
                r'gerekçe["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'gerekce["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'reasoning["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'justification["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'explanation["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'analysis["\']?\s*[:=]\s*["\']([^"\']+)"',
                
                # Natural language patterns - English
                r'(?:reason|because|due to|as)\s*[:\-]\s*([^.]+)',
                r'(?:analysis|rationale|justification)\s*[:\-]\s*([^.]+)',
                
                # Natural language patterns - Turkish
                r'(?:neden|çünkü|sebep|gerekçe)\s*[:\-]\s*([^.]+)',
                r'(?:analiz|gerekçe|açıklama)\s*[:\-]\s*([^.]+)',
            ],
            "invalidation_condition": [
                # Standard JSON patterns
                r'"invalidation_condition":\s*"([^"]+)"',
                r'"invalidation":\s*"([^"]+)"',
                r'"exit_condition":\s*"([^"]+)"',
                r'"close_condition":\s*"([^"]+)"',
                
                # Flexible assignment patterns
                r'invalidation[_\s]?condition["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'invalidation["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'exit[_\s]?condition["\']?\s*[:=]\s*["\']([^"\']+)"',
                r'close[_\s]?condition["\']?\s*[:=]\s*["\']([^"\']+)"',
                
                # Natural language patterns - English
                r'(?:exit|close|invalidate)\s+(?:if|when)\s+([^.]+)',
                r'(?:stop|cut)\s+(?:loss|position)\s+(?:if|when)\s+([^.]+)',
                
                # Natural language patterns - Turkish
                r'(?:çık|kapat|geçersiz)\s+(?:olursa|zaman)\s+([^.]+)',
                r'(?:zarar|kes)\s+(?:dur|olursa)\s+([^.]+)',
            ]
        }

        # First, extract signal to determine which fields are needed
        signal_type = None
        for pattern in strategies.get("signal", []):
            match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if match:
                signal_type = (match.group(1) if match.groups() else match.group(0)).upper()
                if signal_type in ["HOLD", "BEKLE"]:
                    signal_type = "HOLD"
                elif signal_type in ["BUY", "LONG", "AL"]:
                    signal_type = "BUY"
                elif signal_type in ["SELL", "SHORT", "SAT"]:
                    signal_type = "SELL"
                elif signal_type in ["CLOSE", "KAPAT"]:
                    signal_type = "CLOSE"
                break

        # Fields that are only needed for BUY/SELL signals (not HOLD/CLOSE)
        position_only_fields = {"stop_loss", "profit_target", "invalidation_condition", "quantity", "leverage"}

        # Try each strategy for each field with enhanced logging
        for field, patterns in strategies.items():
            # Skip position-specific fields for HOLD/CLOSE signals
            if signal_type in ["HOLD", "CLOSE"] and field in position_only_fields:
                logger.debug("⏭️ Skipping %s extraction for %s signal", field, signal_type)
                continue

            field_success = False
            patterns_tried = 0

            for i, pattern in enumerate(patterns):
                patterns_tried += 1
                extraction_metrics["patterns_tried"] += 1

                logger.debug("🔍 Trying pattern %d/%d for %s: %s", i+1, len(patterns), field, pattern[:50])
                
                match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE | re.DOTALL)
                if match:
                    value = match.group(1) if match.groups() else match.group(0)
                    
                    # Track successful pattern
                    extraction_metrics["used_patterns"][field] = pattern[:50] + "..."

                    # Clean and convert the value
                    if field in ["quantity", "stop_loss", "profit_target", "leverage", "confidence"]:
                        try:
                            # Extract numeric value, handle percentages
                            numeric_match = re.search(r'([0-9]+(?:\.[0-9]*)?)', str(value))
                            if numeric_match:
                                data[field] = float(numeric_match.group(1))
                                extraction_metrics["successful_extractions"] += 1
                                logger.info("✅ Extracted %s: %s (pattern %d)", field, data[field], i+1)
                                field_success = True
                                break
                        except ValueError:
                            logger.debug("❌ Pattern %d for %s failed value conversion", i+1, field)
                            continue
                    else:
                        # String values
                        data[field] = str(value).strip().strip('\"\'')
                        extraction_metrics["successful_extractions"] += 1
                        logger.info("✅ Extracted %s: %s (pattern %d)", field, data[field][:50] + "..." if len(data[field]) > 50 else data[field], i+1)
                        field_success = True
                        break
                else:
                    logger.debug("❌ Pattern %d for %s no match", i+1, field)
            
            if not field_success:
                extraction_metrics["failed_fields"].append(field)
                logger.warning("⚠️ Failed to extract %s after trying %d patterns", field, patterns_tried)

        # Enhanced post-processing and validation with detailed logging
        logger.info("📊 Extraction Summary:")
        logger.info("  └─ Patterns tried: %d", extraction_metrics["patterns_tried"])
        logger.info("  └─ Successful extractions: %d", extraction_metrics["successful_extractions"])
        logger.info("  └─ Failed fields: %s", extraction_metrics["failed_fields"] if extraction_metrics["failed_fields"] else "None")
        
        if extraction_metrics["used_patterns"]:
            logger.info("  └─ Used patterns:")
            for field, pattern in extraction_metrics["used_patterns"].items():
                logger.info("    └─ %s: %s", field, pattern)
        
        if "signal" in data:
            # Normalize signal values with logging
            original_signal = data["signal"]
            signal = data["signal"].upper()
            if signal in ["BUY", "LONG", "AL"]:
                data["signal"] = "BUY"
                logger.info("🔄 Normalized signal: '%s' → 'BUY'", original_signal)
            elif signal in ["SELL", "SHORT", "SAT"]:
                data["signal"] = "SELL"
                logger.info("🔄 Normalized signal: '%s' → 'SELL'", original_signal)
            elif signal in ["HOLD", "BEKLE"]:
                data["signal"] = "HOLD"
                logger.info("🔄 Normalized signal: '%s' → 'HOLD'", original_signal)
            elif signal in ["CLOSE", "KAPAT"]:
                data["signal"] = "CLOSE"
                logger.info("🔄 Normalized signal: '%s' → 'CLOSE'", original_signal)
            else:
                logger.warning("⚠️ Unknown signal '%s' - keeping as-is", original_signal)
        else:
            logger.warning("⚠️ No signal extracted - defaulting to HOLD for safety")
            data["signal"] = "HOLD"
        
        # Final validation and quality check
        required_fields = ["signal"]

        # For HOLD/CLOSE, position fields are not required
        if signal_type in ["HOLD", "CLOSE"]:
            optional_fields = ["confidence", "justification"]
            logger.info("📊 %s signal - position fields not required", signal_type)
        else:
            optional_fields = ["quantity", "stop_loss", "profit_target", "leverage", "confidence", "justification"]

        missing_required = [f for f in required_fields if f not in data]
        missing_optional = [f for f in optional_fields if f not in data]

        if missing_required:
            logger.error("❌ Missing required fields: %s", missing_required)

        if missing_optional and signal_type not in ["HOLD", "CLOSE"]:
            logger.info("ℹ️ Missing optional fields (will default safely): %s", missing_optional)

        # Adjust success rate calculation based on signal type
        expected_fields = len(required_fields) + len(optional_fields)
        success_rate = (extraction_metrics["successful_extractions"] / max(1, expected_fields)) * 100
        logger.info("📈 Extraction success rate: %.1f%% (expected %d fields for %s)", success_rate, expected_fields, signal_type or "UNKNOWN")

        if success_rate >= 80 or signal_type in ["HOLD", "CLOSE"]:
            logger.info("✅ Extraction quality OK for %s signal", signal_type or "UNKNOWN")
        elif success_rate >= 50:
            logger.warning("⚠️ Moderate extraction quality - some data missing")
        else:
            logger.warning("⚠️ Low extraction quality - using safe defaults")

        if "confidence" in data:
            # Normalize confidence to 0-1 range
            if data["confidence"] > 1.0:
                data["confidence"] = data["confidence"] / 100.0

        # Extract BTC-specific quantity if no direct quantity found
        if "quantity" not in data:
            btc_match = re.search(r'([0-9.]+)\s*BTC', content, re.IGNORECASE)
            if btc_match:
                try:
                    data["quantity"] = float(btc_match.group(1))
                    logger.debug("✅ Extracted BTC quantity: %s", data["quantity"])
                except ValueError:
                    pass

        # Fill remaining optional defaults to keep downstream logic safe
        data.setdefault("quantity", 0.0)
        data.setdefault("leverage", 10.0)
        data.setdefault("confidence", 0.0)
        data.setdefault("justification", "Enhanced fallback defaulted missing fields")

        return data

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
