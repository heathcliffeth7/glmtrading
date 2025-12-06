"""
Risk Control Components for Trading System

This module provides safety controls and validation mechanisms for risk management:
- ConsistencyValidator: Validates thought process alignment with signals
- DynamicThreshold: Calculates confidence thresholds based on volatility
- SafetyLimits: Applies position size and leverage constraints
- ConfidenceGuardrails: Validates and adjusts decisions based on confidence
- Utility functions for leverage normalization and quantity allocation

Extracted from manager.py for better code organization and token optimization.
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any

from app.risk_manager.decision_models import RiskDecision, ThoughtProcess, clamp
from app.utils.logging import get_logger
from app.utils.telegram import telegram_client
from app.agents.base import AgentSignal


logger = get_logger(__name__)


__all__ = [
    "ConsistencyValidator",
    "DynamicThreshold",
    "SafetyLimits",
    "ConfidenceGuardrails",
    "normalize_leverage",
    "normalize_quantity_to_allocation",
]


class ConsistencyValidator:
    """
    Validates consistency between GLM's thought process and trading signals.
    
    Ensures that the synthesis conclusion aligns with the recommended action,
    preventing contradictory decisions.
    """
    
    @staticmethod
    def validate(thought_process: ThoughtProcess, signal: str) -> Tuple[bool, str]:
        """
        Validate that thesis/synthesis aligns with signal direction.

        Args:
            thought_process: Parsed ThoughtProcess object
            signal: Trading signal (BUY, SELL, HOLD, CLOSE)

        Returns:
            Tuple of (is_consistent, reason)
        """
        if not thought_process or not thought_process.synthesis_verdict:
            return True, "no_synthesis_to_validate"

        synthesis = thought_process.synthesis_verdict.lower()

        # Turkish and English keywords for direction detection
        bullish_keywords = ['bullish', 'yukselis', 'yükseliş', 'long', 'al', 'buy', 'yukari', 'yukarı', 'pozitif']
        bearish_keywords = ['bearish', 'dusus', 'düşüş', 'short', 'sat', 'sell', 'asagi', 'aşağı', 'negatif']

        is_bullish = any(kw in synthesis for kw in bullish_keywords)
        is_bearish = any(kw in synthesis for kw in bearish_keywords)

        # Check alignment
        if signal == "BUY" and is_bearish and not is_bullish:
            return False, "BUY_signal_but_bearish_synthesis"

        if signal == "SELL" and is_bullish and not is_bearish:
            return False, "SELL_signal_but_bullish_synthesis"

        return True, "aligned"
    
    @staticmethod
    def notify_mismatch(symbol: str, original_signal: str, reason: str, synthesis: str) -> None:
        """
        Send Telegram alert for consistency mismatch.
        
        Args:
            symbol: Trading symbol
            original_signal: Original signal from agent
            reason: Mismatch reason
            synthesis: Synthesis text (truncated to 200 chars)
        """
        try:
            message = f"""⚠️ *CONSISTENCY MISMATCH*

*Symbol:* {symbol}
*Original Signal:* {original_signal}
*Reason:* {reason}
*Action:* Forced to HOLD

*Synthesis:* {synthesis[:200]}...

_Thought process consistency validation triggered_"""

            telegram_client.send(message)
            logger.warning("Consistency mismatch notification sent for %s", symbol)
        except Exception as e:
            logger.error("Failed to send consistency mismatch notification: %s", e)


class DynamicThreshold:
    """
    Calculates dynamic confidence thresholds based on market volatility.
    
    Adjusts required confidence levels to match market conditions:
    - Low volatility: Lower threshold (safer to trade)
    - High volatility: Higher threshold (need more confirmation)
    """
    
    THRESHOLDS = {
        "low": 75.0,
        "medium": 80.0,
        "high": 85.0,
        "extreme": 90.0,
    }
    
    @classmethod
    def get_threshold(cls, volatility_regime: str) -> float:
        """
        Return confidence threshold based on volatility regime.

        Low volatility = 75% (safer to trade, need less confirmation)
        High volatility = 85% (riskier, need more confirmation)

        Args:
            volatility_regime: "low", "medium", "high", or "extreme"

        Returns:
            Confidence threshold (0-100)
        """
        return cls.THRESHOLDS.get(volatility_regime, 80.0)


class SafetyLimits:
    """
    Applies safety constraints to trading decisions.
    
    Enforces:
    - Maximum margin per trade: 3000 USD
    - Leverage range: 1x to 20x
    - Maximum position size: 60,000 USD (3000 × 20x)
    """
    
    @staticmethod
    def apply(decision: RiskDecision, portfolio_metrics: Optional[dict] = None) -> RiskDecision:
        """
        Apply only safety limits to GLM decision:
        - Max 3000 USD margin (teminat) per trade
        - Max 20x leverage (min 1x)
        - This allows up to 60,000 USD position size (3000 × 20x)
        
        GLM has complete freedom otherwise.
        
        Args:
            decision: Original risk decision
            portfolio_metrics: Portfolio state (price, equity, position)
        
        Returns:
            Modified decision with safety limits applied
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
                    exit_plan=decision.exit_plan,
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
                exit_plan=decision.exit_plan,
                decision_timestamp=decision.decision_timestamp,
                market_snapshot_timestamp=decision.market_snapshot_timestamp,
                close_side=decision.close_side,
                context_volatility=decision.context_volatility,
                context_atr_pct=decision.context_atr_pct,
                context_vol_ratio=decision.context_vol_ratio,
                context_atr_ratio=decision.context_atr_ratio,
            )
        
        return decision


class ConfidenceGuardrails:
    """
    Applies confidence-based guardrails to trading decisions.
    
    Strategy:
    - GLM confidence >= 80%: Trust GLM, bypass bias guardrails
    - GLM confidence < 80%: Use bias scores for validation
    - Dynamic adjustments based on market conditions (trend, volatility)
    """
    
    @staticmethod
    def apply(
        decision: RiskDecision,
        signals: List[AgentSignal],
    ) -> RiskDecision:
        """
        Clamp GLM decisions using signal confidence bands with dynamic market conditions.
        
        NEW STRATEGY: Prioritize GLM's own confidence assessment.
        - If GLM confidence >= 80: Trust GLM, bypass bias guardrails
        - If GLM confidence < 80: Use bias scores for validation
        
        Args:
            decision: Original risk decision
            signals: List of agent signals with bias data
        
        Returns:
            Modified or validated decision
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
        
        band = ConfidenceGuardrails._confidence_band(signals)
        market_condition = ConfidenceGuardrails._analyze_market_condition(signals)

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
    
    @staticmethod
    def _analyze_market_condition(signals: List[AgentSignal]) -> Dict[str, float]:
        """
        Analyze current market condition for dynamic strategy adaptation.
        
        Args:
            signals: List of agent signals with bias data
        
        Returns:
            Dictionary with trend_strength and volatility (0.0 to 1.0)
        """
        if not signals:
            return {"trend_strength": 0.0, "volatility": 0.5}

        signal = signals[0]
        bias_snapshot: Optional[Dict[str, float]] = None
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
        trend_strength = clamp(
            (abs(composite) + trend_bias + momentum_bias) / 3.0,
            0.0,
            1.0
        )

        # Volatility is already normalized (0.0 to 1.0)
        volatility = clamp(volatility_regime, 0.0, 1.0)

        return {
            "trend_strength": trend_strength,
            "volatility": volatility,
        }
    
    @staticmethod
    def _confidence_band(signals: List[AgentSignal]) -> Dict[str, Any]:
        """
        Calculate confidence band from signal bias data.
        
        Args:
            signals: List of agent signals
        
        Returns:
            Dictionary with action, amount, and confidence
        """
        if not signals:
            return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

        primary = signals[0]
        
        # Extract bias snapshot if available
        try:
            bias_snapshot = primary.metadata.get("bias_snapshot") if primary.metadata else None
        except AttributeError:
            bias_snapshot = None
        
        if not bias_snapshot:
            # Fallback to signal confidence
            conf = primary.confidence if hasattr(primary, 'confidence') else 0.0
            direction = primary.direction if hasattr(primary, 'direction') else "HOLD"
            
            # Map direction to action
            action_map = {"BUY": "BUY", "SELL": "SELL", "LONG": "BUY", "SHORT": "SELL"}
            action = action_map.get(direction, "HOLD")
            
            # Simple confidence-based amount
            if conf > 0.7:
                amount = 0.15
            elif conf > 0.5:
                amount = 0.10
            elif conf > 0.3:
                amount = 0.05
            else:
                amount = 0.0
                action = "HOLD"
            
            return {"action": action, "amount": amount, "confidence": conf}
        
        # Use bias composite score
        composite = float(bias_snapshot.get("composite_bias_score", 0.0) or 0.0)
        abs_composite = abs(composite)
        
        # Determine action from composite
        if abs_composite < 0.2:
            action = "HOLD"
            amount = 0.0
        elif composite > 0:
            action = "BUY"
            if abs_composite > 0.7:
                amount = 0.15
            elif abs_composite > 0.5:
                amount = 0.10
            else:
                amount = 0.05
        else:
            action = "SELL"
            if abs_composite > 0.7:
                amount = 0.15
            elif abs_composite > 0.5:
                amount = 0.10
            else:
                amount = 0.05
        
        return {"action": action, "amount": amount, "confidence": abs_composite}


# Standalone utility functions


def normalize_leverage(value: float) -> float:
    """
    Normalize leverage to 1-20x range (GLM freedom mode).
    
    Args:
        value: Raw leverage value
    
    Returns:
        Normalized leverage (1.0 to 20.0)
    """
    if value <= 0:
        return 1.0  # Default to 1x if invalid
    return max(1.0, min(value, 20.0))


def normalize_quantity_to_allocation(
    action: str,
    quantity: float,
    portfolio_metrics: Optional[dict] = None,
) -> float:
    """
    Interpret GLM 'quantity' as equity allocation (0-1) and gracefully handle
    backwards-compatible coin-amount outputs.
    
    Args:
        action: Trading action (BUY, SELL, HOLD, CLOSE)
        quantity: Raw quantity value from GLM
        portfolio_metrics: Portfolio state (price, equity, position)
    
    Returns:
        Normalized allocation (0.0 to 1.0)
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
