"""
Exit Plan Calculator

Calculates dynamic risk parameters based on volatility, performance, and market conditions.

Responsibilities:
- Volatility regime classification (low/medium/high/extreme)
- Stop loss distance bounds calculation
- Take profit distance bounds calculation
- Leverage cap calculation
- Risk/reward ratio requirements
- BTC correlation context fetching
- Complete exit plan generation (SL/TP/invalidation/leverage)

Design:
- Stateless: All state passed as parameters
- Asset-agnostic: Uses relative volatility metrics
- Performance-aware: Adjusts parameters based on trading performance

Example:
    calculator = ExitPlanCalculator(
        settings=get_settings(),
        primary_tf="4h",
        data_validator=validator
    )
    
    regime = calculator.calculate_volatility_regime(
        vol_ratio=1.2,
        atr_ratio=1.5,
        atr_pct=2.3
    )
    # Returns: "medium"
    
    min_sl, max_sl, regime = calculator.calculate_sl_bounds(
        atr_pct=2.3,
        vol=0.6,
        position_side="LONG",
        consecutive_losses=1,
        recent_win_rate=0.55
    )
    # Returns: (1.8, 2.5, "medium")
"""

import logging
from typing import Any, Dict, Optional, Tuple

from app.config.settings import get_settings
from app.risk_manager.prompts.data_validation import DataValidator
from app.risk_manager.prompts.models import VOLATILITY_PARAMS

logger = logging.getLogger(__name__)


class ExitPlanCalculator:
    """Calculate dynamic risk parameters and exit plans"""

    def __init__(
        self,
        settings,
        primary_tf: str,
        data_validator: DataValidator
    ):
        """
        Initialize exit plan calculator.

        Args:
            settings: Application settings
            primary_tf: Primary timeframe (e.g., "4h")
            data_validator: Data validator for safe operations
        """
        self._settings = settings
        self._primary_tf = primary_tf
        self._data_validator = data_validator

    def calculate_volatility_regime(
        self,
        vol_ratio: float,
        atr_ratio: float,
        atr_pct: float
    ) -> str:
        """
        Calculate volatility regime (asset-agnostic).

        Args:
            vol_ratio: Current/median realized volatility ratio
            atr_ratio: Current/median ATR ratio
            atr_pct: ATR as percentage of price

        Returns:
            str: One of "low", "medium", "high", "extreme"
        """
        rel = max(vol_ratio, atr_ratio)

        # v3.3: Thresholds increased 20% - "Extreme" warning made harder
        if rel < 0.9 and atr_pct < 1.2:
            return "low"
        if rel < 1.5 and atr_pct < 2.5:  # Was 1.25 and 1.8
            return "medium"
        if rel < 2.5 and atr_pct < 4.0:  # Was 1.9 and 3.0
            return "high"
        return "extreme"

    def calculate_sl_bounds(
        self,
        atr_pct: float,
        vol: float,
        position_side: Optional[str],
        consecutive_losses: int,
        recent_win_rate: float
    ) -> Tuple[float, float, str]:
        """
        Calculate stop loss distance bounds.

        Args:
            atr_pct: ATR as percentage of price
            vol: Volatility score (0.0-1.0)
            position_side: "LONG" or "SHORT"
            consecutive_losses: Number of consecutive losses
            recent_win_rate: Recent win rate (0.0-1.0)

        Returns:
            Tuple[float, float, str]: (min_sl_pct, max_sl_pct, regime)
        """
        if vol is None:
            vol = 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0  # Default fallback

        regime = self.calculate_volatility_regime(
            vol_ratio=1.0,  # Will be recalculated by caller
            atr_ratio=1.0,
            atr_pct=atr_pct
        )
        params = VOLATILITY_PARAMS[regime]

        side_mult = 1.0  # Equal treatment: same SL multiplier for SHORT and LONG

        # SL: ATR × sl_mult × side
        min_distance_pct = atr_pct * params["sl_mult"] * side_mult
        min_distance_pct = max(min_distance_pct, 0.3)  # Floor: 0.3%

        # Max SL: ATR × 1.5 (cap based on regime) - tighter risk control
        max_cap = 3.0 if regime in ["high", "extreme"] else 2.5
        max_distance_pct = atr_pct * 1.5
        max_distance_pct = min(max_distance_pct, max_cap)

        # Ensure min <= max
        if min_distance_pct > max_distance_pct:
            min_distance_pct = max_distance_pct * 0.6

        return min_distance_pct, max_distance_pct, regime

    def calculate_tp_bounds(
        self,
        atr_pct: float,
        vol: float,
        sl_min: Optional[float],
        regime: str
    ) -> Tuple[float, float]:
        """
        Calculate take profit distance bounds.

        Args:
            atr_pct: ATR as percentage of price
            vol: Volatility score (0.0-1.0)
            sl_min: Minimum SL distance (for R:R calculation)
            regime: Volatility regime ("low", "medium", "high", "extreme")

        Returns:
            Tuple[float, float]: (min_tp_pct, max_tp_pct)
        """
        if vol is None:
            vol = 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0  # Default fallback

        params = VOLATILITY_PARAMS[regime]

        # Min TP: SL × R:R ratio (ensures consistent risk/reward)
        if sl_min and sl_min > 0:
            min_tp_pct = sl_min * params["tp_rr"]
        else:
            # Fallback: ATR-based
            min_tp_pct = atr_pct * params["sl_mult"] * params["tp_rr"]
        min_tp_pct = max(min_tp_pct, 0.6)  # Floor: 0.6%

        # Max TP: ATR × 2 (cap based on regime) - more realistic targets
        max_cap = 5.0 if regime in ["high", "extreme"] else 4.0
        max_tp_pct = atr_pct * 2.0
        max_tp_pct = min(max_tp_pct, max_cap)

        # Ensure min <= max
        if min_tp_pct > max_tp_pct:
            min_tp_pct = max_tp_pct * 0.6

        return min_tp_pct, max_tp_pct

    def calculate_leverage_cap(
        self,
        regime: str,
        position_side: Optional[str],
        consecutive_losses: int,
        recent_win_rate: float,
        performance_history_len: int
    ) -> int:
        """
        Calculate maximum leverage based on regime and performance.

        Args:
            regime: Volatility regime
            position_side: "LONG" or "SHORT"
            consecutive_losses: Number of consecutive losses
            recent_win_rate: Recent win rate (0.0-1.0)
            performance_history_len: Length of performance history

        Returns:
            int: Maximum leverage (1-20)
        """
        params = VOLATILITY_PARAMS[regime]
        cap = params["max_lev"]

        # Equal treatment: No leverage penalty for SHORT
        # if position_side == "SHORT":
        #     cap = int(cap * 0.9)

        # Performance-based reduction
        if consecutive_losses >= 2:
            cap = min(cap, 5)
        if recent_win_rate <= 0.4 and performance_history_len >= 5:
            cap = min(cap, 6)

        cap = max(1, min(cap, 20))
        return cap

    def calculate_min_rr_ratio(
        self,
        regime: str,
        position_side: str,
        consecutive_losses: int,
        recent_win_rate: float,
        performance_history_len: int
    ) -> float:
        """
        Calculate minimum risk/reward ratio.

        Args:
            regime: Volatility regime
            position_side: "LONG" or "SHORT"
            consecutive_losses: Number of consecutive losses
            recent_win_rate: Recent win rate (0.0-1.0)
            performance_history_len: Length of performance history

        Returns:
            float: Minimum R:R ratio
        """
        base_rr = {
            "low": 2.0,
            "medium": 2.5,
            "high": 3.0,
            "extreme": 3.5,
        }

        rr = base_rr.get(regime, 2.5)

        if consecutive_losses >= 2:
            rr += 0.2  # Reduced from 0.5 - less aggressive penalty
        if recent_win_rate <= 0.4 and performance_history_len >= 5:
            rr += 0.15  # Reduced from 0.3
        # Equal treatment: No R:R penalty for SHORT
        # if position_side == "SHORT":
        #     rr += 0.2

        return rr

    def determine_tf_trend(self, features: Dict[str, Any]) -> str:
        """
        Determine trend direction from features dict.

        Args:
            features: Feature dict with close, ema_20, ema_50

        Returns:
            str: "BULL", "BEAR", or "NEUTRAL"
        """
        close = features.get("close", 0)
        ema20 = features.get("ema_20", 0)
        ema50 = features.get("ema_50", 0)

        if close <= 0 or ema20 <= 0 or ema50 <= 0:
            return "NEUTRAL"

        if close > ema20 > ema50:
            return "BULL"
        elif close < ema20 < ema50:
            return "BEAR"
        return "NEUTRAL"

    def fetch_btc_correlation(self) -> Optional[Dict[str, Any]]:
        """
        Fetch BTC multi-timeframe trend context.

        Uses InfluxDB enriched data. Queries multiple timeframes for 
        consensus/conflict detection.

        Returns:
            Optional[Dict]: BTC correlation data with:
                - price: BTC price
                - trend: Primary TF trend
                - rsi: BTC RSI
                - tf_trends: Dict of timeframe -> trend direction
                - consensus_trend: Consensus trend ("BULL"/"BEAR"/"NEUTRAL"/"MIXED")
                - has_conflict: True if TFs show conflicting signals
                - agreement_pct: Percentage of TFs agreeing with consensus
        """
        try:
            from app.utils.influx import query_latest_snapshot

            # Query multiple timeframes for BTC
            btc_timeframes = ["4h", "1h", "30m", "15m"]
            tf_trends: Dict[str, str] = {}
            tf_data: Dict[str, Dict[str, Any]] = {}

            for tf in btc_timeframes:
                btc_features = query_latest_snapshot("enriched_features", "BTCUSDT", tf)
                if btc_features:
                    trend = self.determine_tf_trend(btc_features)
                    tf_trends[tf] = trend
                    tf_data[tf] = {
                        "close": btc_features.get("close", 0),
                        "ema20": btc_features.get("ema_20", 0),
                        "ema50": btc_features.get("ema_50", 0),
                        "rsi": btc_features.get("rsi_14", 50),
                        "trend": trend,
                    }

            if not tf_trends:
                logger.debug("BTC correlation: No data available from any timeframe")
                return None

            # Get primary timeframe data (fallback to first available)
            primary_tf = self._primary_tf
            if primary_tf not in tf_data:
                primary_tf = list(tf_data.keys())[0]

            primary_data = tf_data[primary_tf]

            # Calculate consensus
            unique_trends = set(t for t in tf_trends.values() if t != "NEUTRAL")

            if len(unique_trends) == 0:
                # All neutral
                consensus_trend = "NEUTRAL"
                has_conflict = False
            elif len(unique_trends) == 1:
                # All agree on direction (ignoring neutrals)
                consensus_trend = unique_trends.pop()
                has_conflict = False
            else:
                # Mixed signals (BULL and BEAR present)
                consensus_trend = "MIXED"
                has_conflict = True

            # Calculate agreement score (how many TFs agree with consensus)
            if consensus_trend in ("BULL", "BEAR"):
                agreement_count = sum(1 for t in tf_trends.values() if t == consensus_trend)
                agreement_pct = agreement_count / len(tf_trends) * 100
            else:
                agreement_pct = 0.0

            logger.debug(
                "BTC multi-TF correlation: primary=%s(%s), consensus=%s, conflict=%s, trends=%s",
                primary_tf, primary_data["trend"], consensus_trend, has_conflict, tf_trends
            )

            return {
                "price": primary_data["close"],
                "trend": primary_data["trend"],  # Primary TF trend for backward compatibility
                "rsi": primary_data["rsi"],
                "ema20": primary_data["ema20"],
                "ema50": primary_data["ema50"],
                # Multi-TF data
                "tf_trends": tf_trends,
                "consensus_trend": consensus_trend,
                "has_conflict": has_conflict,
                "agreement_pct": agreement_pct,
                "primary_tf": primary_tf,
            }

        except ImportError:
            logger.warning("BTC correlation: influx module not available")
            return None
        except Exception as e:
            logger.warning("BTC correlation fetch failed: %s", e)
            return None

    def calculate_exit_plan(
        self,
        signal: str,
        entry_price: float,
        historical_arrays: Dict,
        atr_pct: float,
        volatility: float,
        consecutive_losses: int,
        recent_win_rate: float,
        performance_history_len: int,
        sr_distances: Optional[Dict] = None
    ) -> Dict:
        """
        Calculate complete exit plan with SL/TP/leverage.

        Args:
            signal: "BUY" or "SELL"
            entry_price: Entry price
            historical_arrays: Historical data for S/R calculation
            atr_pct: ATR as percentage of price
            volatility: Volatility score (0.0-1.0)
            consecutive_losses: Number of consecutive losses
            recent_win_rate: Recent win rate (0.0-1.0)
            performance_history_len: Length of performance history
            sr_distances: Pre-calculated S/R distances (optional)

        Returns:
            Dict: Exit plan with SL, TP, TP1, leverage, R:R, etc.
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

        # Get S/R levels (use provided or default)
        if sr_distances is None:
            sr = {
                "nearest_support": 0,
                "nearest_resistance": 0,
                "support_dist_pct": 0,
                "resistance_dist_pct": 0,
                "rr_ratio": 0,
                "levels": {},
            }
        else:
            sr = sr_distances

        # Get bounds
        position_side = "LONG" if signal == "BUY" else "SHORT"
        
        # Calculate regime first
        vol_ratio = 1.0  # Placeholder, actual value passed from orchestrator
        atr_ratio = 1.0  # Placeholder
        regime = self.calculate_volatility_regime(vol_ratio, atr_ratio, atr_pct)
        
        min_sl, max_sl, regime = self.calculate_sl_bounds(
            atr_pct, volatility, position_side, consecutive_losses, recent_win_rate
        )
        min_tp, max_tp = self.calculate_tp_bounds(atr_pct, volatility, min_sl, regime)
        min_rr = self.calculate_min_rr_ratio(
            regime, position_side, consecutive_losses, recent_win_rate, performance_history_len
        )

        if signal == "BUY":
            # SL below entry, use nearest support or ATR-based
            sl_from_support = sr["nearest_support"]
            if sl_from_support > 0 and sl_from_support < entry_price:
                sl_dist_from_support = (entry_price - sl_from_support) / entry_price * 100
            else:
                sl_dist_from_support = min_sl  # fallback

            # Use support if within bounds, else clamp to bounds
            if min_sl <= sl_dist_from_support <= max_sl:
                sl_dist = sl_dist_from_support
            else:
                sl_dist = max(min_sl, min(sl_dist_from_support, max_sl))

            # TP must satisfy R:R requirement
            min_tp_for_rr = sl_dist * min_rr
            tp_dist = max(min_tp, min(min_tp_for_rr, max_tp))

            # If R:R still not satisfied due to TP cap, tighten SL
            if self._data_validator.safe_divide(tp_dist, sl_dist, default=min_rr) < min_rr and tp_dist >= min_tp:
                sl_dist = self._data_validator.safe_divide(tp_dist, min_rr, default=min_sl)
                sl_dist = max(min_sl, sl_dist)  # ensure min_sl

            # SECURITY: Ensure SL/TP distances are within safe bounds
            sl_dist = max(0.3, min(sl_dist, 99.0))  # 0.3% to 99%
            tp_dist = max(0.6, min(tp_dist, 99.0))  # 0.6% to 99%

            stop_loss = entry_price * (1 - sl_dist / 100)
            profit_target = entry_price * (1 + tp_dist / 100)

            # Invalidation between entry and SL (70% of SL distance)
            inv_price = entry_price * (1 - sl_dist * 0.7 / 100)
            invalidation = f"If price closes below {inv_price:.2f} on 15m candle"

        else:  # SELL
            # SL above entry, use nearest resistance or ATR-based
            sl_from_resistance = sr["nearest_resistance"]
            if sl_from_resistance > 0 and sl_from_resistance > entry_price:
                sl_dist_from_resistance = (sl_from_resistance - entry_price) / entry_price * 100
            else:
                sl_dist_from_resistance = min_sl  # fallback

            if min_sl <= sl_dist_from_resistance <= max_sl:
                sl_dist = sl_dist_from_resistance
            else:
                sl_dist = max(min_sl, min(sl_dist_from_resistance, max_sl))

            min_tp_for_rr = sl_dist * min_rr
            tp_dist = max(min_tp, min(min_tp_for_rr, max_tp))

            # If R:R still not satisfied, tighten SL
            if self._data_validator.safe_divide(tp_dist, sl_dist, default=min_rr) < min_rr and tp_dist >= min_tp:
                sl_dist = self._data_validator.safe_divide(tp_dist, min_rr, default=min_sl)
                sl_dist = max(min_sl, sl_dist)

            # SECURITY: Ensure SL/TP distances are within safe bounds
            sl_dist = max(0.3, min(sl_dist, 99.0))  # 0.3% to 99%
            tp_dist = max(0.6, min(tp_dist, 99.0))  # 0.6% to 99%

            stop_loss = entry_price * (1 + sl_dist / 100)
            profit_target = entry_price * (1 - tp_dist / 100)

            inv_price = entry_price * (1 + sl_dist * 0.7 / 100)
            invalidation = f"If price closes above {inv_price:.2f} on 15m candle"

        # Leverage based on regime
        leverage = self.calculate_leverage_cap(
            regime, position_side, consecutive_losses, recent_win_rate, performance_history_len
        )

        # Final R:R calculation (using safe divide)
        final_rr = self._data_validator.safe_divide(tp_dist, sl_dist, default=0.0)

        # SECURITY: Ensure final prices are positive
        stop_loss = max(0.01, stop_loss)
        profit_target = max(0.01, profit_target)

        # v3.0: TP1 (Micro-Harvesting) = 0.5R, halfway to breakeven
        if signal == "BUY":
            tp1_price = entry_price * (1 + sl_dist * 0.5 / 100)
        else:
            tp1_price = entry_price * (1 - sl_dist * 0.5 / 100)
        tp1_price = max(0.01, tp1_price)

        logger.info(
            "📐 Exit Plan: %s entry=%.2f SL=%.2f TP1=%.2f(0.5R) TP2=%.2f R:R=%.2f lev=%dx",
            signal, entry_price, stop_loss, tp1_price, profit_target, final_rr, leverage
        )

        return {
            "stop_loss": round(stop_loss, 2),
            "profit_target": round(profit_target, 2),
            "tp1_price": round(tp1_price, 2),  # v3.0: Micro-Harvesting
            "tp1_action": "TP1'de %50 kapat, SL'yi Entry'ye çek",
            "invalidation_condition": invalidation,
            "invalidation_timeframe": "15m",  # Invalidation check timeframe
            "leverage": leverage,
            "sl_distance_pct": round(sl_dist, 2),
            "tp_distance_pct": round(tp_dist, 2),
            "rr_ratio": round(final_rr, 2),
        }


# Static helper function (will be moved to market_state_builder temporarily)
def _calculate_sr_distances_static(historical_arrays: Dict, current_price: float) -> Dict:
    """Temporary stub - will delegate to EntryAnalyzer"""
    return {
        "nearest_support": 0,
        "nearest_resistance": 0,
        "support_dist_pct": 0,
        "resistance_dist_pct": 0,
        "rr_ratio": 0,
        "levels": {},
    }
