"""
Risk Parameters Module

Stop-loss, take-profit, leverage calculations based on volatility.
"""

from typing import Dict, List, Optional, Tuple

from app.utils.logging import get_logger

from .models import VOLATILITY_PARAMS, PerformanceState, VolatilityState


logger = get_logger(__name__)


class RiskParameterCalculator:
    """
    Calculates risk parameters (SL/TP/leverage) based on volatility and performance.
    """

    def __init__(
        self,
        volatility_state: VolatilityState = None,
        performance_state: PerformanceState = None,
    ):
        """
        Initialize the calculator.

        Args:
            volatility_state: Current volatility metrics
            performance_state: Current performance metrics
        """
        self._vol_state = volatility_state or VolatilityState()
        self._perf_state = performance_state or PerformanceState()

    def update_states(
        self,
        volatility_state: VolatilityState = None,
        performance_state: PerformanceState = None,
    ) -> None:
        """Update volatility and performance states."""
        if volatility_state:
            self._vol_state = volatility_state
        if performance_state:
            self._perf_state = performance_state

    def _safe_divide(
        self, numerator: float, denominator: float, default: float = 0.0
    ) -> float:
        """Safe division with zero handling."""
        if abs(denominator) < 1e-10:
            return default
        return numerator / denominator

    def sl_distance_bounds(
        self,
        atr_pct: float,
        vol: float = None,
        position_side: Optional[str] = None,
    ) -> Tuple[float, float, str]:
        """
        Calculate volatility-based dynamic SL bounds.

        Args:
            atr_pct: ATR as percentage of price
            vol: Volatility score (optional, uses state if not provided)
            position_side: "LONG" or "SHORT" (currently not used, equal treatment)

        Returns:
            (min_sl_pct, max_sl_pct, regime)
        """
        if vol is None:
            vol = self._vol_state.legacy_score or 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0

        regime = self._vol_state.regime
        params = VOLATILITY_PARAMS.get(regime, VOLATILITY_PARAMS["medium"])

        # Equal treatment: same SL multiplier for LONG and SHORT
        side_mult = 1.0

        # SL: ATR × sl_mult × side
        min_distance_pct = atr_pct * params["sl_mult"] * side_mult
        min_distance_pct = max(min_distance_pct, 0.3)  # Floor: 0.3%

        # Max SL: ATR × 1.5 (cap based on regime)
        max_cap = 3.0 if regime in ["high", "extreme"] else 2.5
        max_distance_pct = atr_pct * 1.5
        max_distance_pct = min(max_distance_pct, max_cap)

        # Ensure min <= max
        if min_distance_pct > max_distance_pct:
            min_distance_pct = max_distance_pct * 0.6

        return min_distance_pct, max_distance_pct, regime

    def tp_distance_bounds(
        self,
        atr_pct: float,
        vol: float = None,
        sl_min: Optional[float] = None,
    ) -> Tuple[float, float, str]:
        """
        Calculate volatility-based dynamic TP bounds with R:R ratio.

        Args:
            atr_pct: ATR as percentage of price
            vol: Volatility score (optional)
            sl_min: Minimum SL percentage (for R:R calculation)

        Returns:
            (min_tp_pct, max_tp_pct, regime)
        """
        if vol is None:
            vol = self._vol_state.legacy_score or 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0

        regime = self._vol_state.regime
        params = VOLATILITY_PARAMS.get(regime, VOLATILITY_PARAMS["medium"])

        # Min TP: SL × R:R ratio
        if sl_min and sl_min > 0:
            min_tp_pct = sl_min * params["tp_rr"]
        else:
            # Fallback: ATR-based
            min_tp_pct = atr_pct * params["sl_mult"] * params["tp_rr"]
        min_tp_pct = max(min_tp_pct, 0.6)  # Floor: 0.6%

        # Max TP: ATR × 2 (cap based on regime)
        max_cap = 5.0 if regime in ["high", "extreme"] else 4.0
        max_tp_pct = atr_pct * 2.0
        max_tp_pct = min(max_tp_pct, max_cap)

        # Ensure min <= max
        if min_tp_pct > max_tp_pct:
            min_tp_pct = max_tp_pct * 0.6

        return min_tp_pct, max_tp_pct, regime

    def recommended_leverage_cap(
        self,
        vol: float = None,
        position_side: Optional[str] = None,
    ) -> Tuple[int, str]:
        """
        Calculate volatility-based dynamic leverage cap.

        Args:
            vol: Volatility score (optional)
            position_side: "LONG" or "SHORT" (currently not used)

        Returns:
            (max_leverage, regime)
        """
        regime = self._vol_state.regime
        params = VOLATILITY_PARAMS.get(regime, VOLATILITY_PARAMS["medium"])
        cap = params["max_lev"]

        # Performance-based reduction
        if self._perf_state.consecutive_losses >= 2:
            cap = min(cap, 5)
        if (
            self._perf_state.recent_win_rate <= 0.4
            and len(self._perf_state.performance_history) >= 5
        ):
            cap = min(cap, 6)

        cap = max(1, min(cap, 20))
        return cap, regime

    def get_min_rr_ratio(
        self, vol: float = None, position_side: str = "LONG"
    ) -> float:
        """
        Get minimum required R:R ratio based on regime and performance.

        Args:
            vol: Volatility score (optional)
            position_side: "LONG" or "SHORT"

        Returns:
            Minimum R:R ratio
        """
        regime = self._vol_state.regime
        base_rr = {
            "low": 2.0,
            "medium": 2.5,
            "high": 3.0,
            "extreme": 3.5,
        }

        rr = base_rr.get(regime, 2.5)

        # Performance-based adjustment
        if self._perf_state.consecutive_losses >= 2:
            rr += 0.2
        if (
            self._perf_state.recent_win_rate <= 0.4
            and len(self._perf_state.performance_history) >= 5
        ):
            rr += 0.15

        return rr

    def calculate_sr_distances(
        self, historical_arrays: Dict, current_price: float
    ) -> Dict:
        """
        Calculate support/resistance distances from historical data.

        Args:
            historical_arrays: Historical price data with high/low arrays
            current_price: Current price

        Returns:
            Dictionary with nearest_support, nearest_resistance, distances
        """
        if current_price <= 0:
            return {
                "nearest_support": 0,
                "nearest_resistance": 0,
                "support_dist_pct": 0,
                "resistance_dist_pct": 0,
                "rr_ratio": 0,
                "levels": {},
            }

        # Try to get 4h or 1h data
        hist_4h = historical_arrays.get("4h", {})
        hist_1h = historical_arrays.get("1h", {})
        hist = hist_4h if hist_4h else hist_1h

        lows = hist.get("low", [])
        highs = hist.get("high", [])

        if not lows or not highs:
            return {
                "nearest_support": current_price * 0.98,
                "nearest_resistance": current_price * 1.02,
                "support_dist_pct": 2.0,
                "resistance_dist_pct": 2.0,
                "rr_ratio": 1.0,
                "levels": {},
            }

        # Find recent lows and highs (last 50 bars)
        recent_lows = lows[-50:] if len(lows) >= 50 else lows
        recent_highs = highs[-50:] if len(highs) >= 50 else highs

        # Support: highest low below current price
        supports = [l for l in recent_lows if l < current_price]
        nearest_support = max(supports) if supports else current_price * 0.98

        # Resistance: lowest high above current price
        resistances = [h for h in recent_highs if h > current_price]
        nearest_resistance = min(resistances) if resistances else current_price * 1.02

        support_dist = ((current_price - nearest_support) / current_price) * 100
        resistance_dist = ((nearest_resistance - current_price) / current_price) * 100

        rr_ratio = self._safe_divide(resistance_dist, support_dist, default=1.0)

        return {
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "support_dist_pct": round(support_dist, 2),
            "resistance_dist_pct": round(resistance_dist, 2),
            "rr_ratio": round(rr_ratio, 2),
            "levels": {
                "support_levels": sorted(set(supports))[-3:] if supports else [],
                "resistance_levels": sorted(set(resistances))[:3] if resistances else [],
            },
        }

    def calculate_exit_plan(
        self,
        signal: str,
        entry_price: float,
        historical_arrays: Dict,
        atr_pct: float = None,
    ) -> Dict:
        """
        Calculate valid exit plan based on volatility and S/R levels.

        Args:
            signal: "BUY" or "SELL"
            entry_price: Entry price
            historical_arrays: Historical data for S/R calculation
            atr_pct: ATR as percentage (optional)

        Returns:
            Exit plan dictionary with SL, TP, leverage, etc.
        """
        if entry_price <= 0:
            return {
                "stop_loss": 0,
                "profit_target": 0,
                "invalidation_condition": "",
                "leverage": 1,
                "sl_distance_pct": 0,
                "tp_distance_pct": 0,
                "rr_ratio": 0,
            }

        atr_pct = atr_pct or self._vol_state.atr_pct or 1.0
        vol = self._vol_state.legacy_score or 0.5

        # Get S/R levels
        sr = self.calculate_sr_distances(historical_arrays, entry_price)

        # Get bounds
        position_side = "LONG" if signal == "BUY" else "SHORT"
        min_sl, max_sl, regime = self.sl_distance_bounds(atr_pct, vol, position_side)
        min_tp, max_tp, _ = self.tp_distance_bounds(atr_pct, vol)
        min_rr = self.get_min_rr_ratio(vol, position_side)

        if signal == "BUY":
            # SL below entry, use nearest support or ATR-based
            sl_from_support = sr["nearest_support"]
            if sl_from_support > 0 and sl_from_support < entry_price:
                sl_dist_from_support = (
                    (entry_price - sl_from_support) / entry_price * 100
                )
            else:
                sl_dist_from_support = min_sl

            # Use support if within bounds, else clamp
            if min_sl <= sl_dist_from_support <= max_sl:
                sl_dist = sl_dist_from_support
            else:
                sl_dist = max(min_sl, min(sl_dist_from_support, max_sl))

            # TP must satisfy R:R requirement
            min_tp_for_rr = sl_dist * min_rr
            tp_dist = max(min_tp, min(min_tp_for_rr, max_tp))

            # If R:R still not satisfied, tighten SL
            if (
                self._safe_divide(tp_dist, sl_dist, default=min_rr) < min_rr
                and tp_dist >= min_tp
            ):
                sl_dist = self._safe_divide(tp_dist, min_rr, default=min_sl)
                sl_dist = max(min_sl, sl_dist)

            # Ensure bounds
            sl_dist = max(0.3, min(sl_dist, 99.0))
            tp_dist = max(0.6, min(tp_dist, 99.0))

            stop_loss = entry_price * (1 - sl_dist / 100)
            profit_target = entry_price * (1 + tp_dist / 100)

            # Invalidation between entry and SL (70%)
            inv_price = entry_price * (1 - sl_dist * 0.7 / 100)
            invalidation = f"If price closes below {inv_price:.2f} on 15m candle"

            # TP1 (Micro-Harvesting) = 0.5R
            tp1_price = entry_price * (1 + sl_dist * 0.5 / 100)

        else:  # SELL
            # SL above entry, use nearest resistance or ATR-based
            sl_from_resistance = sr["nearest_resistance"]
            if sl_from_resistance > 0 and sl_from_resistance > entry_price:
                sl_dist_from_resistance = (
                    (sl_from_resistance - entry_price) / entry_price * 100
                )
            else:
                sl_dist_from_resistance = min_sl

            if min_sl <= sl_dist_from_resistance <= max_sl:
                sl_dist = sl_dist_from_resistance
            else:
                sl_dist = max(min_sl, min(sl_dist_from_resistance, max_sl))

            min_tp_for_rr = sl_dist * min_rr
            tp_dist = max(min_tp, min(min_tp_for_rr, max_tp))

            if (
                self._safe_divide(tp_dist, sl_dist, default=min_rr) < min_rr
                and tp_dist >= min_tp
            ):
                sl_dist = self._safe_divide(tp_dist, min_rr, default=min_sl)
                sl_dist = max(min_sl, sl_dist)

            sl_dist = max(0.3, min(sl_dist, 99.0))
            tp_dist = max(0.6, min(tp_dist, 99.0))

            stop_loss = entry_price * (1 + sl_dist / 100)
            profit_target = entry_price * (1 - tp_dist / 100)

            inv_price = entry_price * (1 + sl_dist * 0.7 / 100)
            invalidation = f"If price closes above {inv_price:.2f} on 15m candle"

            tp1_price = entry_price * (1 - sl_dist * 0.5 / 100)

        # Leverage based on regime
        leverage, _ = self.recommended_leverage_cap(vol, position_side)

        # Final R:R
        final_rr = self._safe_divide(tp_dist, sl_dist, default=0.0)

        # Ensure positive prices
        stop_loss = max(0.01, stop_loss)
        profit_target = max(0.01, profit_target)
        tp1_price = max(0.01, tp1_price)

        logger.info(
            "Exit Plan: %s entry=%.2f SL=%.2f TP1=%.2f TP2=%.2f R:R=%.2f lev=%dx",
            signal,
            entry_price,
            stop_loss,
            tp1_price,
            profit_target,
            final_rr,
            leverage,
        )

        return {
            "stop_loss": round(stop_loss, 2),
            "profit_target": round(profit_target, 2),
            "tp1_price": round(tp1_price, 2),
            "tp1_action": "TP1'de %50 kapat, SL'yi Entry'ye cek",
            "invalidation_condition": invalidation,
            "invalidation_timeframe": "15m",
            "leverage": leverage,
            "sl_distance_pct": round(sl_dist, 2),
            "tp_distance_pct": round(tp_dist, 2),
            "rr_ratio": round(final_rr, 2),
        }

    def get_volatility_params(self, regime: str = None) -> Dict:
        """
        Get volatility parameters for a regime.

        Args:
            regime: Volatility regime (uses current state if not provided)

        Returns:
            Dictionary with sl_mult, tp_rr, max_lev
        """
        regime = regime or self._vol_state.regime
        return VOLATILITY_PARAMS.get(regime, VOLATILITY_PARAMS["medium"])
