"""
Hold Decision Engine - Akıllı HOLD vs CLOSE karar motoru.

Pozisyon açıkken piyasa koşullarını değerlendirerek
HOLD, CLOSE veya PARTIAL_CLOSE kararı verir.

Features:
- Trend uyumu kontrolü
- RSI extreme değer tespiti
- Volume trend analizi
- Profit giveback koruması
- Minimum hold süresi kontrolü
- Stagnation tespiti

Supports both Scalp and Swing trading modes.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HoldEvaluation:
    """Hold değerlendirme sonucu"""
    decision: str  # "HOLD", "CLOSE", "PARTIAL_CLOSE"
    confidence: int  # 0-100
    reason: str
    warnings: List[str]
    suggested_action: str
    factors_to_hold: int
    factors_to_close: int
    hold_score: float
    position_age_hours: float


class HoldDecisionEngine:
    """
    Pozisyon açıkken HOLD vs CLOSE kararı için akıllı motor.
    Sadece SL/TP değil, piyasa koşullarını da değerlendirir.
    """

    # Mode-specific parameters
    MODE_PARAMS = {
        "swing": {
            "min_hold_hours": 4.0,
            "stagnation_hours": 24,
            "stagnation_threshold_pct": 0.5,
            "profit_giveback_threshold": 0.5,  # Max kârın %50'si geri verilirse
        },
        "scalp": {
            "min_hold_hours": 0.5,
            "stagnation_hours": 6,
            "stagnation_threshold_pct": 0.3,
            "profit_giveback_threshold": 0.4,
        }
    }

    def __init__(self, mode: str = "swing"):
        """
        Initialize HoldDecisionEngine.

        Args:
            mode: Trading mode - "scalp" or "swing"
        """
        self.mode = mode
        self.params = self.MODE_PARAMS.get(mode, self.MODE_PARAMS["swing"])
        self._position_states: Dict[str, Dict] = {}

        logger.info(
            "HoldDecisionEngine initialized | Mode: %s | Min Hold: %.1fh",
            mode, self.params["min_hold_hours"]
        )

    def initialize_position(
        self,
        symbol: str,
        entry_price: float,
        entry_time: str,
        position_side: str,
        stop_loss: float,
    ) -> None:
        """
        Yeni pozisyon için state başlat.

        Args:
            symbol: Trading pair
            entry_price: Giriş fiyatı
            entry_time: Giriş zamanı (ISO format)
            position_side: "LONG" veya "SHORT"
            stop_loss: Stop loss fiyatı
        """
        self._position_states[symbol] = {
            "entry_price": entry_price,
            "entry_time": entry_time,
            "position_side": position_side,
            "stop_loss": stop_loss,
            "highest_pnl_pct": 0.0,
            "lowest_pnl_pct": 0.0,
            "last_evaluation_time": None,
        }

        logger.info(
            "Position state initialized for %s: %s @ %.2f, SL: %.2f",
            symbol, position_side, entry_price, stop_loss
        )

    def update_pnl_tracking(
        self,
        symbol: str,
        current_pnl_pct: float,
    ) -> None:
        """
        PnL tracking güncelle (highest/lowest).

        Args:
            symbol: Trading pair
            current_pnl_pct: Mevcut unrealized PnL yüzdesi
        """
        if symbol not in self._position_states:
            return

        state = self._position_states[symbol]

        if current_pnl_pct > state["highest_pnl_pct"]:
            state["highest_pnl_pct"] = current_pnl_pct
        if current_pnl_pct < state["lowest_pnl_pct"]:
            state["lowest_pnl_pct"] = current_pnl_pct

    def evaluate_hold(
        self,
        symbol: str,
        current_price: float,
        current_time: str,
        # Market data
        current_trend: str,
        trend_strength: str,
        rsi: float,
        volume_trend: str,  # "INCREASING", "DECREASING", "STABLE"
        mtf_confluence: float,
        # Position data
        unrealized_pnl_pct: float,
    ) -> HoldEvaluation:
        """
        HOLD kararını değerlendir.

        Args:
            symbol: Trading pair
            current_price: Mevcut fiyat
            current_time: Mevcut zaman (ISO format)
            current_trend: Mevcut trend ("BULLISH", "BEARISH", "NEUTRAL")
            trend_strength: Trend gücü ("STRONG", "MODERATE", "WEAK")
            rsi: RSI değeri (0-100)
            volume_trend: Volume trendi
            mtf_confluence: MTF uyum skoru (0-100)
            unrealized_pnl_pct: Unrealized PnL yüzdesi

        Returns:
            HoldEvaluation with decision, confidence, and details
        """
        # Get position state
        state = self._position_states.get(symbol)
        if not state:
            return HoldEvaluation(
                decision="HOLD",
                confidence=50,
                reason="No position state found - defaulting to HOLD",
                warnings=["Position state not initialized"],
                suggested_action="MONITOR",
                factors_to_hold=0,
                factors_to_close=0,
                hold_score=50.0,
                position_age_hours=0.0,
            )

        entry_price = state["entry_price"]
        entry_time = state["entry_time"]
        position_side = state["position_side"]
        stop_loss = state["stop_loss"]
        highest_pnl_pct = state["highest_pnl_pct"]

        # Update PnL tracking
        self.update_pnl_tracking(symbol, unrealized_pnl_pct)

        warnings: List[str] = []
        factors_to_hold = 0
        factors_to_close = 0

        # 1. Calculate position age
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            current_dt = datetime.fromisoformat(current_time.replace('Z', '+00:00'))
            hours_held = (current_dt - entry_dt).total_seconds() / 3600
        except Exception:
            hours_held = 0

        # 2. Minimum hold time check
        min_hold = self.params["min_hold_hours"]
        if hours_held < min_hold:
            # Too early - only close on SL hit
            if self._is_sl_hit(current_price, stop_loss, position_side):
                return HoldEvaluation(
                    decision="CLOSE",
                    confidence=95,
                    reason="Stop loss hit (early exit allowed)",
                    warnings=[],
                    suggested_action="CLOSE_FULL",
                    factors_to_hold=0,
                    factors_to_close=10,
                    hold_score=0.0,
                    position_age_hours=hours_held,
                )
            else:
                return HoldEvaluation(
                    decision="HOLD",
                    confidence=90,
                    reason=f"Minimum hold time not reached ({hours_held:.1f}h / {min_hold}h)",
                    warnings=["Early in trade - patience required"],
                    suggested_action="WAIT",
                    factors_to_hold=5,
                    factors_to_close=0,
                    hold_score=100.0,
                    position_age_hours=hours_held,
                )

        # 3. Trend alignment check
        trend_aligned = (
            (position_side == "LONG" and "BULLISH" in current_trend.upper()) or
            (position_side == "SHORT" and "BEARISH" in current_trend.upper())
        )

        if trend_aligned:
            factors_to_hold += 2
            if trend_strength.upper() == "STRONG":
                factors_to_hold += 1
        else:
            factors_to_close += 2
            warnings.append(f"Trend reversed: {current_trend}")

        # 4. MTF Confluence check
        if mtf_confluence >= 60:
            factors_to_hold += 1
        elif mtf_confluence <= 40:
            factors_to_close += 1
            warnings.append(f"MTF confluence weak: {mtf_confluence:.0f}%")

        # 5. RSI extreme check
        if position_side == "LONG":
            if rsi > 80:
                factors_to_close += 2
                warnings.append(f"RSI overbought: {rsi:.0f}")
            elif rsi > 70:
                factors_to_close += 1
                warnings.append(f"RSI elevated: {rsi:.0f}")
            elif 40 <= rsi <= 65:
                factors_to_hold += 1
        else:  # SHORT
            if rsi < 20:
                factors_to_close += 2
                warnings.append(f"RSI oversold: {rsi:.0f}")
            elif rsi < 30:
                factors_to_close += 1
                warnings.append(f"RSI low: {rsi:.0f}")
            elif 35 <= rsi <= 60:
                factors_to_hold += 1

        # 6. Volume trend check
        if volume_trend.upper() == "INCREASING" and trend_aligned:
            factors_to_hold += 1
        elif volume_trend.upper() == "DECREASING" and unrealized_pnl_pct > 1.0:
            factors_to_close += 1
            warnings.append("Volume declining while in profit - consider taking profits")

        # 7. Profit giveback check
        giveback_threshold = self.params["profit_giveback_threshold"]
        if highest_pnl_pct > 2.0 and unrealized_pnl_pct < highest_pnl_pct * giveback_threshold:
            factors_to_close += 2
            warnings.append(
                f"Significant profit giveback: {highest_pnl_pct:.1f}% -> {unrealized_pnl_pct:.1f}%"
            )

        # 8. Stagnation check
        stag_hours = self.params["stagnation_hours"]
        stag_threshold = self.params["stagnation_threshold_pct"]
        if hours_held > stag_hours and abs(unrealized_pnl_pct) < stag_threshold:
            factors_to_close += 1
            warnings.append(f"Position stagnant for {hours_held:.0f}+ hours")

        # Calculate final decision
        total_factors = factors_to_hold + factors_to_close
        if total_factors == 0:
            hold_score = 50.0
        else:
            hold_score = (factors_to_hold / total_factors) * 100

        # Decision logic
        if hold_score >= 60:
            decision = "HOLD"
            confidence = int(hold_score)
            suggested = "CONTINUE_HOLDING"
        elif hold_score >= 40:
            decision = "HOLD"
            confidence = int(hold_score)
            suggested = "TIGHTEN_STOP" if unrealized_pnl_pct > 0 else "MONITOR_CLOSELY"
        else:
            # Are we in profit?
            if unrealized_pnl_pct > 1.0:
                decision = "PARTIAL_CLOSE"
                confidence = int(100 - hold_score)
                suggested = "CLOSE_50%_TRAIL_REST"
            elif unrealized_pnl_pct < -0.5:
                decision = "HOLD"  # Don't exit losing positions early
                confidence = 60
                suggested = "WAIT_FOR_SL_OR_RECOVERY"
                warnings.append("In loss but no clear exit signal - wait for SL or recovery")
            else:
                decision = "HOLD"
                confidence = 55
                suggested = "MONITOR_CLOSELY"

        reason = self._build_reason(factors_to_hold, factors_to_close, warnings)

        # Update state
        state["last_evaluation_time"] = current_time

        logger.debug(
            "Hold evaluation for %s: decision=%s, confidence=%d, hold_score=%.1f, "
            "factors: %d hold / %d close",
            symbol, decision, confidence, hold_score, factors_to_hold, factors_to_close
        )

        return HoldEvaluation(
            decision=decision,
            confidence=confidence,
            reason=reason,
            warnings=warnings,
            suggested_action=suggested,
            factors_to_hold=factors_to_hold,
            factors_to_close=factors_to_close,
            hold_score=hold_score,
            position_age_hours=hours_held,
        )

    def _is_sl_hit(
        self,
        current_price: float,
        stop_loss: float,
        position_side: str,
    ) -> bool:
        """Check if stop loss is hit"""
        if position_side == "LONG":
            return current_price <= stop_loss
        else:
            return current_price >= stop_loss

    def _build_reason(
        self,
        to_hold: int,
        to_close: int,
        warnings: List[str],
    ) -> str:
        """Build human-readable reason string"""
        if to_hold > to_close:
            return f"Holding factors ({to_hold}) outweigh closing factors ({to_close})"
        elif to_close > to_hold:
            warning_str = ', '.join(warnings[:2]) if warnings else "Multiple factors"
            return f"Closing factors ({to_close}) outweigh holding factors ({to_hold}): {warning_str}"
        else:
            return "Neutral - no strong signal either way"

    def get_position_state(self, symbol: str) -> Optional[Dict]:
        """Get current position state for a symbol"""
        return self._position_states.get(symbol)

    def remove_position(self, symbol: str) -> bool:
        """Remove position state when position is closed"""
        if symbol in self._position_states:
            del self._position_states[symbol]
            logger.info("Position state removed for %s", symbol)
            return True
        return False

    def get_summary_for_prompt(self, symbol: str, evaluation: HoldEvaluation) -> str:
        """
        Get compact summary for GLM prompt (~50 tokens).

        Args:
            symbol: Trading pair
            evaluation: HoldEvaluation result

        Returns:
            Compact string for prompt
        """
        state = self._position_states.get(symbol)
        if not state:
            return ""

        # Compact format: HOLD: 78% | 12.5h | Trend OK | 3 warnings
        warning_count = len(evaluation.warnings)

        return (
            f"HOLD: {evaluation.decision} {evaluation.confidence}% | "
            f"{evaluation.position_age_hours:.1f}h | "
            f"Score: {evaluation.hold_score:.0f} | "
            f"{warning_count} warning{'s' if warning_count != 1 else ''}"
        )


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = HoldDecisionEngine(mode="swing")

    # Initialize a position
    engine.initialize_position(
        symbol="BTCUSDT",
        entry_price=100000,
        entry_time="2024-11-26T10:00:00Z",
        position_side="LONG",
        stop_loss=98500,
    )

    # Simulate evaluation after 6 hours
    result = engine.evaluate_hold(
        symbol="BTCUSDT",
        current_price=101500,
        current_time="2024-11-26T16:00:00Z",
        current_trend="BULLISH",
        trend_strength="MODERATE",
        rsi=55,
        volume_trend="STABLE",
        mtf_confluence=65,
        unrealized_pnl_pct=1.5,
    )

    print(f"Decision: {result.decision}")
    print(f"Confidence: {result.confidence}%")
    print(f"Reason: {result.reason}")
    print(f"Suggested Action: {result.suggested_action}")
    print(f"Warnings: {result.warnings}")
    print(f"Position Age: {result.position_age_hours:.1f}h")
