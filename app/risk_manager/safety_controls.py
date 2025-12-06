"""
Safety Controls Module

Handles risk limits and guardrails for trading decisions:
- Margin limits
- Leverage limits
- Position size constraints
- Dynamic thresholds based on volatility
"""

from typing import Dict, List, Optional, Any

from app.risk_manager.decision_models import RiskDecision
from app.utils.logging import get_logger


logger = get_logger(__name__)


# Constants
MAX_MARGIN_USD = 3000.0
MAX_LEVERAGE = 20.0
MIN_LEVERAGE = 1.0


class SafetyControls:
    """
    Enforces safety limits on trading decisions.

    Key limits:
    - Max 3000 USD margin per trade
    - Max 20x leverage (min 1x)
    - Position size = margin × leverage (up to 60,000 USD)
    """

    def __init__(self):
        """Initialize safety controls with default metrics."""
        self._metrics = {
            "dynamic_threshold_low": 0,
            "dynamic_threshold_medium": 0,
            "dynamic_threshold_high": 0,
            "dynamic_threshold_extreme": 0,
        }

    @property
    def metrics(self) -> Dict[str, int]:
        """Get current safety control metrics."""
        return self._metrics.copy()

    def apply_safety_limits(
        self,
        decision: RiskDecision,
        portfolio_metrics: Optional[Dict[str, Any]] = None
    ) -> RiskDecision:
        """
        Apply safety limits to GLM decision:
        - Max 3000 USD margin per trade
        - Max 20x leverage (min 1x)
        - This allows up to 60,000 USD position size (3000 × 20x)

        GLM has complete freedom otherwise.
        """
        if decision.action not in ["BUY", "SELL"]:
            return decision

        # 1. Clamp leverage
        original_leverage = decision.leverage
        clamped_leverage = self.normalize_leverage(decision.leverage)

        if abs(clamped_leverage - original_leverage) > 0.01:
            logger.info(
                "Safety limit: Leverage clamped from %.2fx to %.2fx",
                original_leverage,
                clamped_leverage
            )

        # 2. Apply margin limit
        if portfolio_metrics:
            current_price = portfolio_metrics.get("price", 0)
            equity = portfolio_metrics.get("equity", 10000)

            if current_price > 0 and equity > 0:
                # GLM's amount is equity percentage (0.0-1.0)
                requested_margin_usd = decision.amount * equity
                actual_margin_usd = min(requested_margin_usd, MAX_MARGIN_USD)

                position_size_usd = actual_margin_usd * clamped_leverage
                position_size_btc = position_size_usd / current_price

                clamped_amount = actual_margin_usd / equity
                original_amount = decision.amount

                if clamped_amount < original_amount - 0.001:
                    logger.info(
                        "Safety limit: Margin clamped from %.2f USD to %.2f USD (max margin)",
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
                    logger.info(
                        "Position within limits: %.2f USD margin, %.2f USD position @ %.1fx leverage",
                        actual_margin_usd,
                        position_size_usd,
                        clamped_leverage
                    )

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
                    thought_process=decision.thought_process,
                    data_analysis=decision.data_analysis,
                )

        # Fallback: only apply leverage limit
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
                thought_process=decision.thought_process,
                data_analysis=decision.data_analysis,
            )

        return decision

    def normalize_leverage(self, value: float) -> float:
        """Normalize leverage to 1-20x range."""
        if value <= 0:
            return MIN_LEVERAGE
        return max(MIN_LEVERAGE, min(value, MAX_LEVERAGE))

    def get_dynamic_threshold(self, volatility_regime: str) -> float:
        """
        Return confidence threshold based on volatility regime.

        Low volatility = 75% (safer to trade, need less confirmation)
        High volatility = 85% (riskier, need more confirmation)

        Args:
            volatility_regime: "low", "medium", "high", or "extreme"

        Returns:
            Confidence threshold (0-100)
        """
        thresholds = {
            "low": 75.0,
            "medium": 80.0,
            "high": 85.0,
            "extreme": 90.0,
        }
        threshold = thresholds.get(volatility_regime, 80.0)

        # Track usage
        metric_key = f"dynamic_threshold_{volatility_regime}"
        if metric_key in self._metrics:
            self._metrics[metric_key] += 1

        return threshold

    def apply_confidence_guardrails(
        self,
        decision: RiskDecision,
        confidence_band: Dict[str, Any],
        market_condition: Dict[str, Any],
    ) -> RiskDecision:
        """
        Clamp GLM decisions using signal confidence bands with dynamic market conditions.

        NEW STRATEGY: Prioritize GLM's own confidence assessment.
        - If GLM confidence >= 80: Trust GLM, bypass bias guardrails
        - If GLM confidence < 80: Use bias scores for validation
        """
        if decision.action not in {"BUY", "SELL"}:
            return decision

        # GLM High Confidence Mode
        if decision.glm_confidence >= 80:
            logger.info(
                "GLM High Confidence Mode: GLM confidence %.1f%% >= 80%% → Bypassing bias guardrails",
                decision.glm_confidence,
            )

            # Apply reasonable position sizing based on GLM confidence
            if decision.glm_confidence >= 90:
                max_amount = 0.25
            elif decision.glm_confidence >= 85:
                max_amount = 0.20
            else:
                max_amount = 0.15

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
                    thought_process=decision.thought_process,
                    data_analysis=decision.data_analysis,
                )

            logger.info(
                "Using GLM decision directly: action=%s amount=%.4f confidence=%.1f%%",
                decision.action,
                decision.amount,
                decision.glm_confidence,
            )
            return decision

        # Low GLM Confidence - Use bias guardrails
        logger.info(
            "GLM Low Confidence Mode: GLM confidence %.1f%% < 80%% → Using bias guardrails",
            decision.glm_confidence,
        )

        # Dynamic HOLD strategy based on market conditions
        if confidence_band.get("action") == "HOLD":
            trend_strength = market_condition.get("trend_strength", 0)
            volatility = market_condition.get("volatility", 0.5)
            confidence = confidence_band.get("confidence", 0)

            if trend_strength > 0.6 and volatility < 0.7:
                if confidence > 0.15:
                    adjusted_amount = min(0.05, confidence_band.get("amount", 0.05))
                    logger.info(
                        "Dynamic strategy: Trending market, allowing small position (%.4f) with confidence %.2f",
                        adjusted_amount,
                        confidence,
                    )
                    return RiskDecision(
                        action=decision.action,
                        amount=adjusted_amount,
                        reasoning=f"{decision.reasoning} | Dynamic: Trending market (confidence {confidence:.2f})",
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
                        thought_process=decision.thought_process,
                        data_analysis=decision.data_analysis,
                    )

        return decision
