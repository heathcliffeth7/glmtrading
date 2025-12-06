"""
NOF1.AI Style Prompt Builder for GLM
Builds prompts in the exact format used by professional trading systems

Supports both SCALP and SWING trading modes via settings.swing_trade_mode

Enhanced Features (v2.0):
- Volume Analysis (CVD, OBV, VWAP)
- Funding Rate Analysis with ADX
- Time-Based Filters
- Correlation Guard
- Liquidation Analysis
- Drawdown Management
- Dynamic Position Sizing
- Partial Take Profit
- Trailing Stop & Breakeven

Security Features (v2.1):
- Redis notification HMAC validation
- Prompt injection sanitization
- Division by zero protection
"""

import hmac
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional

from dataclasses import dataclass, field
from pydantic import BaseModel, field_validator

from app.risk_manager.dynamic_risk_manager import DynamicRiskManager
from app.risk_manager.advanced_parser import AdvancedInvalidationParser
from app.config.settings import get_settings

# FAZA 3: Modüler prompt bileşenleri
from app.risk_manager.prompts import (
    PositionCloseNotification,
    Nof1Config,
    VolatilityState,
    PerformanceState,
    VOLATILITY_PARAMS,
    LOSS_MANAGEMENT,
    VolatilityAnalyzer,
    IndicatorInterpreter,
    DataValidator,
    MarketAnalyzer,
    RiskParameterCalculator,
    # NEW: Token optimization modules
    FEW_SHOT_TRAINING,
    HARD_RULES_BLOCK,
    GLOSSARY_TERMS,
    RSI_CONTEXT_RULES,
    build_position_active_instructions_template,
    build_no_position_instructions_template,
    build_dynamic_glossary,
    VolatilityCache,
    compute_realized_vol_pct,
    rolling_median,
    calculate_percentile,
    calculate_slope,
    detect_divergence,
    summarize_series,
    format_array,
    LogicGatesBuilder,
)


# =============================================================================
# SECURITY: Models imported from prompts.models
# =============================================================================

# Models imported from prompts.models (PositionCloseNotification, Nof1Config, VolatilityState, PerformanceState)

# =============================================================================
# Enhanced Feature Imports with Error Tracking
# =============================================================================
ENHANCED_FEATURES_AVAILABLE = False
ENHANCED_FEATURES_ERRORS: List[str] = []

try:
    from app.indicators.volume_analyzer import VolumeAnalyzer
    from app.indicators.funding_analyzer import FundingRateAnalyzer, calculate_adx, interpret_adx
    from app.indicators.liquidation_analyzer import LiquidationAnalyzer
    from app.indicators.entry_analyzer import EntryAnalyzer, EntryConfirmation, CandlePattern
    from app.risk_manager.time_filter import TimeBasedFilter
    from app.risk_manager.correlation_guard import CorrelationGuard
    from app.risk_manager.drawdown_manager import DrawdownManager
    from app.risk_manager.position_sizer import DynamicPositionSizer
    from app.risk_manager.partial_tp_manager import PartialTakeProfitManager
    from app.risk_manager.trailing_stop import AdvancedTrailingStop
    from app.risk_manager.breakeven_manager import BreakevenManager
    # v2.0: Hold Decision & Time Exit Managers
    from app.risk_manager.hold_decision_engine import HoldDecisionEngine
    from app.risk_manager.time_exit_manager import TimeBasedExitManager
    ENHANCED_FEATURES_AVAILABLE = True
except ImportError as e:
    ENHANCED_FEATURES_ERRORS.append(str(e))
    logging.getLogger(__name__).warning("Enhanced feature import failed: %s", e)

logger = logging.getLogger(__name__)

# VOLATILITY_PARAMS and dataclasses imported from prompts module


class Nof1PromptBuilder:
    """Builds NOF1.AI style prompts with full market context"""

    # =============================================================================
    # FEW-SHOT TRAINING & HARD RULES - Imported from prompts.templates
    # =============================================================================
    # FEW_SHOT_TRAINING and HARD_RULES_BLOCK now imported from prompts.templates

    # LOSS_MANAGEMENT imported from prompts.models

    # Minimum hold period for swing trades (hours)
    MINIMUM_HOLD_HOURS = 4.0

    # Fee break-even threshold (round-trip fee + margin)
    FEE_BREAKEVEN_PCT = 0.15

    def __init__(self):
        self._start_time = datetime.utcnow()
        self._invocation_count = 0
        self._advanced_parser = AdvancedInvalidationParser()
        self._risk_manager = DynamicRiskManager()

        # Configuration dataclass (replaces magic numbers)
        self._config = Nof1Config()

        self._consecutive_losses = 0
        self._last_trade_side = None
        self._performance_history: List[int] = []  # 1=win, 0=loss
        self._recent_win_rate: float = 0.5

        self._current_volatility: Optional[float] = None  # 0.0-1.0 legacy
        self._current_atr: Optional[float] = None
        self._current_atr_pct: Optional[float] = None

        # Relative volatility context (asset-agnostic)
        self._current_realized_vol_pct: Optional[float] = None
        self._median_realized_vol_pct: Optional[float] = None
        self._median_atr_pct: Optional[float] = None
        self._vol_ratio: Optional[float] = None
        self._atr_ratio: Optional[float] = None

        # Volatility cache for performance optimization (using new VolatilityCache class)
        self._vol_cache = VolatilityCache(ttl=60, max_size=100)

        # How much raw series to show if needed
        self._series_tail = 6  # keep tiny to avoid token blowups

        # Active glossary contexts for dynamic glossary building
        # Populated during prompt building, reset each invocation
        self._active_glossary_contexts: List[str] = []

        # Settings reference
        self._settings = get_settings()

        # Primary timeframe - 4H for stability (no swing/scalp modes anymore)
        self._primary_tf = "4h"

        # MTF alignment weights - HTF dominant for direction
        self._mtf_weights = {
            "1d": 4.0,    # Dominant - big picture trend
            "4h": 3.0,    # Primary TF
            "1h": 1.5,    # Secondary confirmation
            "30m": 0.0,   # ZERO - noise, ignore
            "15m": 0.0,   # ZERO - noise, ignore
            "5m": 0.0,    # ZERO - ignore
            "1m": 0.0,    # ZERO - ignore
        }

        # FAZA 3: Modüler helper sınıflar
        self._market_analyzer = MarketAnalyzer(mtf_weights=self._mtf_weights)
        self._data_validator = DataValidator(
            hmac_secret=self._settings.security.redis_hmac_secret
        )
        self._indicator_interpreter = IndicatorInterpreter()
        
        # Logic Gates Builder (for Red Team Mode)
        self._logic_gates_builder = None  # Will be initialized with volume_analyzer if available

        # Log trading mode (volatility-based, no swing/scalp)
        logger.info("📊 Nof1PromptBuilder: VOLATILITY-BASED MODE (primary TF: %s)", self._primary_tf)

        # Initialize Enhanced Features
        self._enhanced_features_enabled = ENHANCED_FEATURES_AVAILABLE

        # SECURITY: Check if enhanced features are required but unavailable
        if self._settings.security.require_enhanced_features and not ENHANCED_FEATURES_AVAILABLE:
            error_msg = (
                f"Required enhanced features are unavailable. "
                f"Import errors: {ENHANCED_FEATURES_ERRORS}. "
                f"Set REQUIRE_ENHANCED_FEATURES=false to run in degraded mode."
            )
            logger.critical(error_msg)
            raise RuntimeError(error_msg)

        if not ENHANCED_FEATURES_AVAILABLE and ENHANCED_FEATURES_ERRORS:
            logger.warning(
                "⚠️ DEGRADED MODE: Enhanced features disabled due to import errors: %s",
                ENHANCED_FEATURES_ERRORS
            )

        if self._enhanced_features_enabled:
            try:
                # Volatility-based mode - no swing/scalp distinction
                self._volume_analyzer = VolumeAnalyzer()
                self._funding_analyzer = FundingRateAnalyzer()
                self._liquidation_analyzer = LiquidationAnalyzer()
                self._entry_analyzer = EntryAnalyzer()
                self._time_filter = TimeBasedFilter()
                self._correlation_guard = CorrelationGuard()
                self._drawdown_manager = DrawdownManager(initial_equity=10000.0)
                self._position_sizer = DynamicPositionSizer()
                self._partial_tp_manager = PartialTakeProfitManager()
                self._trailing_stop_manager = AdvancedTrailingStop()
                self._breakeven_manager = BreakevenManager()
                # v2.0: Hold Decision & Time Exit Managers
                self._hold_engine = HoldDecisionEngine()
                self._time_exit_manager = TimeBasedExitManager(weekend_rule_enabled=True)
                # Initialize Logic Gates Builder with volume analyzer
                self._logic_gates_builder = LogicGatesBuilder(
                    primary_tf=self._primary_tf,
                    volume_analyzer=self._volume_analyzer
                )
                logger.info("✅ Enhanced features initialized (volatility-based)")
            except Exception as e:
                logger.error("Failed to initialize enhanced features: %s", e)
                self._enhanced_features_enabled = False
        else:
            logger.warning("⚠️ Enhanced features not available - running in basic mode")

    # ---------------------------------------------------------------------
    # SECURITY: Utility methods for input sanitization and safe operations
    # ---------------------------------------------------------------------
    def _sanitize_prompt_input(self, text: str, max_length: int = 200) -> str:
        """Delegate to DataValidator."""
        return self._data_validator.sanitize_prompt_input(text, max_length)

    def _safe_divide(self, numerator: float, denominator: float, default: float = 0.0) -> float:
        """Delegate to DataValidator."""
        return self._data_validator.safe_divide(numerator, denominator, default)

    def _verify_hmac_signature(self, data: Dict[str, Any], signature: str) -> bool:
        """Delegate to DataValidator."""
        return self._data_validator.verify_hmac_signature(data, signature)

    # ---------------------------------------------------------------------
    # Relative volatility helpers (asset-agnostic) - Now using modular utilities
    # ---------------------------------------------------------------------

    def _interpret_cvd_context(
        self,
        cvd_values: List[float],
        closes: List[float],
        lookback: int = 8,
        current_position: str = None
    ) -> Tuple[str, str]:
        """Delegate to IndicatorInterpreter."""
        return self._indicator_interpreter.interpret_cvd_context(
            cvd_values, closes, lookback, current_position
        )

    def _interpret_rsi_context(
        self, rsi: float, adx: float, price_slope: float = 0.0
    ) -> Tuple[str, str]:
        """Delegate to IndicatorInterpreter."""
        return self._indicator_interpreter.interpret_rsi_context(rsi, adx, price_slope)

    def _interpret_funding_oi_context(
        self, funding_rate: float, oi_current: float, oi_avg: float, price_slope: float
    ) -> Tuple[str, str]:
        """Delegate to IndicatorInterpreter."""
        return self._indicator_interpreter.interpret_funding_oi_context(
            funding_rate, oi_current, oi_avg, price_slope
        )

    # ---------------------------------------------------------------------
    # Regime mapping (RELATIVE)
    # ---------------------------------------------------------------------
    def _vol_regime_key(self) -> str:
        """
        Relative (asset-agnostic) volatility regime.
        Uses instance state (self._vol_ratio, self._atr_ratio, self._current_atr_pct)
        instead of a parameter to avoid confusion.

        Returns:
            str: One of "low", "medium", "high", "extreme"
        """
        atr_pct = self._current_atr_pct or 0.0
        vol_ratio = self._vol_ratio or 1.0
        atr_ratio = self._atr_ratio or 1.0

        rel = max(vol_ratio, atr_ratio)

        # v3.3: Eşikler %20 artırıldı - "Extreme" uyarısı zorlaştırıldı
        if rel < 0.9 and atr_pct < 1.2:
            return "low"
        if rel < 1.5 and atr_pct < 2.5:  # Eskiden 1.25 ve 1.8'di
            return "medium"
        if rel < 2.5 and atr_pct < 4.0:  # Eskiden 1.9 ve 3.0'dı
            return "high"
        return "extreme"

    def _sl_distance_bounds(self, atr_pct: float, vol: float, position_side: Optional[str] = None) -> Tuple[float, float, str]:
        """Volatility-based dynamic SL calculation (no swing/scalp modes)"""
        if vol is None:
            vol = 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0  # Default fallback

        regime = self._vol_regime_key()
        params = VOLATILITY_PARAMS[regime]

        side_mult = 1.0  # Eşit muamele: SHORT ve LONG için aynı SL multiplier

        # SL: ATR × sl_mult × side
        min_distance_pct = atr_pct * params["sl_mult"] * side_mult
        min_distance_pct = max(min_distance_pct, 0.3)  # Floor: 0.3%

        # Max SL: ATR × 1.5 (cap based on regime) - daha sıkı risk kontrolü
        max_cap = 3.0 if regime in ["high", "extreme"] else 2.5
        max_distance_pct = atr_pct * 1.5
        max_distance_pct = min(max_distance_pct, max_cap)

        # Ensure min <= max
        if min_distance_pct > max_distance_pct:
            min_distance_pct = max_distance_pct * 0.6

        return min_distance_pct, max_distance_pct, regime

    def _tp_distance_bounds(self, atr_pct: float, vol: float, sl_min: Optional[float] = None) -> Tuple[float, float, str]:
        """Volatility-based dynamic TP calculation with R:R ratio (no swing/scalp modes)"""
        if vol is None:
            vol = 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0  # Default fallback

        regime = self._vol_regime_key()
        params = VOLATILITY_PARAMS[regime]

        # Min TP: SL × R:R ratio (ensures consistent risk/reward)
        if sl_min and sl_min > 0:
            min_tp_pct = sl_min * params["tp_rr"]
        else:
            # Fallback: ATR-based
            min_tp_pct = atr_pct * params["sl_mult"] * params["tp_rr"]
        min_tp_pct = max(min_tp_pct, 0.6)  # Floor: 0.6%

        # Max TP: ATR × 2 (cap based on regime) - daha gerçekçi hedefler
        max_cap = 5.0 if regime in ["high", "extreme"] else 4.0
        max_tp_pct = atr_pct * 2.0
        max_tp_pct = min(max_tp_pct, max_cap)

        # Ensure min <= max
        if min_tp_pct > max_tp_pct:
            min_tp_pct = max_tp_pct * 0.6

        return min_tp_pct, max_tp_pct, regime

    def _recommended_leverage_cap(self, vol: float, position_side: Optional[str] = None) -> Tuple[int, str]:
        """Volatility-based dynamic leverage cap (no swing/scalp modes)"""
        regime = self._vol_regime_key()
        params = VOLATILITY_PARAMS[regime]
        cap = params["max_lev"]

        # Eşit muamele: SHORT için leverage cezası kaldırıldı
        # if position_side == "SHORT":
        #     cap = int(cap * 0.9)

        # Performance-based reduction
        if self._consecutive_losses >= 2:
            cap = min(cap, 5)
        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            cap = min(cap, 6)

        cap = max(1, min(cap, 20))
        return cap, regime

    def _get_min_rr_ratio(self, vol: float, position_side: str) -> float:
        regime = self._vol_regime_key()
        base_rr = {
            "low": 2.0,
            "medium": 2.5,
            "high": 3.0,
            "extreme": 3.5,
        }

        rr = base_rr.get(regime, 2.5)

        if self._consecutive_losses >= 2:
            rr += 0.2  # Reduced from 0.5 - less aggressive penalty
        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            rr += 0.15  # Reduced from 0.3
        # Eşit muamele: SHORT için R:R cezası kaldırıldı
        # if position_side == "SHORT":
        #     rr += 0.2

        return rr

    # ---------------------------------------------------------------------
    # Series summarization (token saver)
    # ---------------------------------------------------------------------


    # ---------------------------------------------------------------------
    # Market structure via pivots - DELEGATED TO EntryAnalyzer (v4.0)
    # ---------------------------------------------------------------------
    def _detect_pivots(
        self,
        highs: List[float],
        lows: List[float],
        window: int = 2,
        min_move_pct: float = 0.25
    ) -> List[Tuple[int, str, float]]:
        """Delegate to EntryAnalyzer"""
        if self._enhanced_features_enabled and hasattr(self, '_entry_analyzer'):
            return self._entry_analyzer.detect_pivots(highs, lows, window, min_move_pct)
        return []

    def _analyze_market_structure(self, hist_data: Dict[str, List[float]]) -> str:
        """Delegate to EntryAnalyzer"""
        if self._enhanced_features_enabled and hasattr(self, '_entry_analyzer'):
            atr_pct = self._current_atr_pct or 0.0
            vol = self._current_volatility or 0.5
            return self._entry_analyzer.analyze_market_structure(hist_data, atr_pct, vol)
        return "UNKNOWN"

    def _detect_data_conflicts(
        self,
        trend_direction: str,
        market_structure: str,
        rsi: float,
        ema_distance: float
    ) -> List[str]:
        """Delegate to MarketAnalyzer."""
        return self._market_analyzer.detect_data_conflicts(
            trend_direction, market_structure, rsi, ema_distance
        )

    # ---------------------------------------------------------------------
    # PRE-CALCULATED ANALYSIS (Token Optimization)
    # Delegated to MarketAnalyzer
    # ---------------------------------------------------------------------
    def _calculate_mtf_alignment(self, current_snapshots: Dict) -> Dict:
        """Delegate to MarketAnalyzer."""
        return self._market_analyzer.calculate_mtf_alignment(current_snapshots)

    def _check_liquidity_sweep(self, data_primary: Dict, mtf_alignment: Dict) -> bool:
        """Delegate to EntryAnalyzer (v4.0)"""
        if self._enhanced_features_enabled and hasattr(self, '_entry_analyzer'):
            return self._entry_analyzer.check_liquidity_sweep(
                close=data_primary.get("close", 0),
                low=data_primary.get("low", 0),
                high=data_primary.get("high", 0),
                prev_swing_low=data_primary.get("prev_swing_low", data_primary.get("low", 0)),
                prev_swing_high=data_primary.get("prev_swing_high", data_primary.get("high", 0)),
                alignment=mtf_alignment.get("alignment", "")
            )
        return False

    def _generate_narrative(self, data_primary: Dict) -> str:
        """
        v4.0: Contextual Data Points (No Judgments)
        Sadece ham veri, yorum GLM'e birakilir.
        """
        rsi = data_primary.get("rsi_14", 50)
        adx = data_primary.get("adx_14", 0)
        close = data_primary.get("close", 0)
        ema20 = data_primary.get("ema_20", 0)

        # EMA mesafesi hesapla (yorum yok)
        ema_dist_pct = ((close - ema20) / ema20 * 100) if ema20 > 0 else 0

        return f"""
CONTEXTUAL METRICS:
• RSI Level: {rsi:.1f} (Reference: <30 oversold, >70 overbought)
• ADX Strength: {adx:.1f} (Reference: >25 trending, <20 ranging)
• Price vs EMA20: {ema_dist_pct:+.2f}%
"""

    def _calculate_sr_distances(self, historical_arrays: Dict, current_price: float) -> Dict:
        """Delegate to EntryAnalyzer (v4.0)"""
        if self._enhanced_features_enabled and hasattr(self, '_entry_analyzer'):
            return self._entry_analyzer.calculate_sr_distances(historical_arrays, current_price)
        return {
            "nearest_support": 0, "nearest_resistance": 0,
            "support_dist_pct": 0, "resistance_dist_pct": 0,
            "rr_ratio": 0, "levels": {},
        }

    def _build_pre_calculated_section(
        self,
        current_snapshots: Dict,
        historical_arrays: Dict,
        current_price: float,
    ) -> str:
        """
        v4.0: RAW DATA BLOCKS (No Scoring)
        GLM'in kendi analizini yapabilmesi icin ham verileri ve matematiksel mesafeleri sunar.
        Puanlama yok - GLM ozgurce karar verecek.
        """
        lines = [
            "",
            "=" * 80,
            "MARKET DATA BLOCKS (RAW INTELLIGENCE)",
            "=" * 80,
            "",
        ]

        # A. MTF Structure (Sadece veri, yorum yok)
        data_4h = current_snapshots.get("4h", {})
        data_1d = current_snapshots.get("1d", {})
        data_1h = current_snapshots.get("1h", {})

        lines.append("A. MULTI-TIMEFRAME STRUCTURE:")
        d1_close = data_1d.get("close", 0)
        d1_ema20 = data_1d.get("ema_20", 0)
        d1_ema50 = data_1d.get("ema_50", 0)
        d1_rsi = data_1d.get("rsi_14", 50)
        lines.append(f"  [1D] Price: {d1_close:.2f} | EMA20: {d1_ema20:.2f} | EMA50: {d1_ema50:.2f} | RSI: {d1_rsi:.1f}")

        h4_close = data_4h.get("close", 0)
        h4_ema20 = data_4h.get("ema_20", 0)
        h4_ema50 = data_4h.get("ema_50", 0)
        h4_rsi = data_4h.get("rsi_14", 50)
        # OBV trend for 4H
        hist_4h = historical_arrays.get("4h", {})
        h4_obv = "N/A"
        if hist_4h.get("close") and hist_4h.get("volume"):
            h4_obv = self._volume_analyzer.get_obv_trend(hist_4h["close"], hist_4h["volume"])
        lines.append(f"  [4H] Price: {h4_close:.2f} | EMA20: {h4_ema20:.2f} | EMA50: {h4_ema50:.2f} | RSI: {h4_rsi:.1f} | OBV: {h4_obv}")

        h1_close = data_1h.get("close", 0)
        h1_ema20 = data_1h.get("ema_20", 0)
        h1_rsi = data_1h.get("rsi_14", 50)
        lines.append(f"  [1H] Price: {h1_close:.2f} | EMA20: {h1_ema20:.2f} | RSI: {h1_rsi:.1f}")
        lines.append("")

        # MTF Sentez Rehberi
        lines.extend([
            "  MTF SENTEZ KURALI:",
            "  - 3/3 TF ayni yonde (Price vs EMA) = GUCLU sinyal",
            "  - 2/3 TF ayni yonde = ORTA sinyal",
            "  - TF'ler farkli yonde = ZAYIF/CATISMA",
            "  - HTF (1D) > LTF (1H) onceligi - 1D trend yonu belirleyici",
            "",
        ])

        # B. Momentum Vectors (Son 8 bar dizisi - GLM egilimi gorebilsin)
        hist_4h = historical_arrays.get("4h", {})
        lines.append("B. MOMENTUM VECTORS (Last 8 bars):")
        if hist_4h:
            rsi_series = hist_4h.get("rsi_14", [])[-8:]
            if rsi_series:
                lines.append(f"                  RSI Sequence: {format_array(rsi_series, 1)}")

            macd_hist_series = hist_4h.get("macd_hist", [])[-8:]
            if macd_hist_series:
                lines.append(f"                  MACD Hist Seq: {format_array(macd_hist_series, 4)}")
        else:
            lines.append("  (No historical data available)")
        lines.append("")

        # C. Key Levels & Distances (Matematiksel - yorum yok)
        sr = self._calculate_sr_distances(historical_arrays, current_price)
        lines.append("C. PROXIMITY TO KEY LEVELS:")
        # Distance to Support with edge case handling
        if sr['support_dist_pct'] > 0.05:
            lines.append(f"  Distance to Support: {sr['support_dist_pct']:.2f}% (Level: {sr['nearest_support']:.2f})")
        else:
            lines.append(f"  Distance to Support: AT LEVEL (Level: {sr['nearest_support']:.2f})")
        # Distance to Resistance with edge case handling
        if sr['resistance_dist_pct'] > 0.05:
            lines.append(f"  Distance to Resistance: {sr['resistance_dist_pct']:.2f}% (Level: {sr['nearest_resistance']:.2f})")
        else:
            lines.append(f"  Distance to Resistance: AT LEVEL (Level: {sr['nearest_resistance']:.2f})")

        # EMA Uzakligi (Mean Reversion potansiyeli icin)
        if h4_ema20 > 0 and current_price > 0:
            ema_dist = (current_price - h4_ema20) / h4_ema20 * 100
            lines.append(f"  Extension from 4H EMA20: {ema_dist:+.2f}%")

        if sr['rr_ratio'] > 0:
            lines.append(f"  R:R Ratio: {sr['rr_ratio']:.2f}:1")
        elif sr.get('resistance_dist_pct', 0) < 0.05 and sr.get('support_dist_pct', 0) > 0.05:
            lines.append("  R:R Ratio: 0:1 (AT RESISTANCE)")
        elif sr.get('support_dist_pct', 0) < 0.05 and sr.get('resistance_dist_pct', 0) > 0.05:
            lines.append("  R:R Ratio: INF:1 (AT SUPPORT)")
        else:
            lines.append("  R:R Ratio: N/A (price at pivot)")
        lines.append("")

        # D. Volatility Context (Ham veri)
        regime = self._vol_regime_key()
        atr_pct = self._current_atr_pct or 0.0
        vol_ratio = self._vol_ratio or 1.0
        lines.append(f"D. VOLATILITY: Regime={regime.upper()} | ATR={atr_pct:.2f}% | Vol Ratio={vol_ratio:.2f}x")
        lines.append("")

        return "\n".join(lines)

    # ---------------------------------------------------------------------
    # ENHANCED FEATURES SECTION (v2.0)
    # ---------------------------------------------------------------------
    def _build_enhanced_features_section(
        self,
        symbol: str,
        current_price: float,
        historical_arrays: Dict,
        futures_data: Dict,
        portfolio_metrics: Dict,
    ) -> str:
        """Build enhanced features section including volume, funding, ADX, time filter, etc."""
        if not self._enhanced_features_enabled:
            return ""

        lines = [
            "",
            "=" * 80,
            "ENHANCED ANALYSIS (v2.0 Features)",
            "=" * 80,
            "",
        ]

        try:
            # 1. Session Info (No judgments - just data)
            time_result = self._time_filter.analyze()
            current_hour = datetime.utcnow().hour
            lines.extend([
                "SESSION INFO:",
                f"  Session: {time_result.session.value}",
                f"  Hour (UTC): {current_hour}",
            ])
            lines.append("")

            # 2. Volume Data (Raw values only)
            hist_primary = historical_arrays.get(self._primary_tf, {})
            if hist_primary.get("close") and hist_primary.get("volume"):
                closes = hist_primary["close"]
                volumes = hist_primary["volume"]
                highs = hist_primary.get("high", closes)
                lows = hist_primary.get("low", closes)

                # CVD - raw values + contextual interpretation
                cvd_values, _ = self._volume_analyzer.calculate_cvd(closes, highs, lows, volumes)
                if cvd_values:
                    # Show last 8 CVD values for GLM to analyze slope
                    cvd_recent = cvd_values[-8:] if len(cvd_values) >= 8 else cvd_values

                    # Mevcut pozisyonu belirle (pozisyon-aware CVD yorumu için)
                    current_position = None
                    if portfolio_metrics:
                        net_pos = portfolio_metrics.get("net_position", 0)
                        if net_pos > 0:
                            current_position = "LONG"
                        elif net_pos < 0:
                            current_position = "SHORT"

                    # Context-aware interpretation (pozisyon bilgisiyle)
                    cvd_label, cvd_desc = self._interpret_cvd_context(
                        cvd_values, closes, current_position=current_position
                    )
                    
                    # Add to active glossary contexts for dynamic glossary
                    if cvd_label and cvd_label != "NEUTRAL":
                        if cvd_label not in self._active_glossary_contexts:
                            self._active_glossary_contexts.append(cvd_label)
                    
                    lines.extend([
                        "VOLUME DATA:",
                        f"  CVD Sequence: {format_array(cvd_recent, 0)}",
                        f"  Context: [{cvd_label}]",
                        f"  Interpretation: {cvd_desc}",
                    ])

                    # VWAP - raw distance
                    vwap_result = self._volume_analyzer.calculate_vwap(highs, lows, closes, volumes)
                    if vwap_result.get("vwap") and current_price > 0:
                        vwap = vwap_result["vwap"]
                        vwap_dist = ((current_price - vwap) / vwap) * 100
                        lines.append(f"  VWAP: {vwap:,.2f} | Price Distance: {vwap_dist:+.2f}%")

                    # VWAP LEVELS (4H + 1D)
                    lines.append("")
                    lines.append("VWAP LEVELS:")
                    # 4H VWAP (already calculated above)
                    if vwap_result.get("vwap"):
                        bias_4h = "BULLISH" if vwap_dist > 0 else "BEARISH"
                        lines.append(f"  [4H] VWAP: {vwap:,.2f} | Price: {vwap_dist:+.2f}% | Bias: {bias_4h}")
                    # 1D VWAP
                    hist_1d = historical_arrays.get("1d", {})
                    if hist_1d.get("high") and hist_1d.get("low") and hist_1d.get("close") and hist_1d.get("volume"):
                        vwap_1d = self._volume_analyzer.calculate_vwap(hist_1d["high"], hist_1d["low"], hist_1d["close"], hist_1d["volume"])
                        if vwap_1d.get("vwap") and current_price > 0:
                            v1d = vwap_1d["vwap"]
                            dist_1d = ((current_price - v1d) / v1d) * 100
                            bias_1d = "BULLISH" if dist_1d > 0 else "BEARISH"
                            lines.append(f"  [1D] VWAP: {v1d:,.2f} | Price: {dist_1d:+.2f}% | Bias: {bias_1d}")

                    # Volume Spike detection
                    is_spike, vol_ratio = self._volume_analyzer.detect_volume_spike(volumes)
                    if is_spike:
                        lines.append(f"  🔥 Volume Spike: {vol_ratio:.1f}x ortalama - yüksek aktivite")
                    elif vol_ratio < 0.1:
                        lines.append(f"  ⚠️ Volume Ratio: {vol_ratio:.2f}x - ÇOK DÜŞÜK HACİM (sahte kırılma riski)")
                    else:
                        lines.append(f"  Volume Ratio: {vol_ratio:.1f}x ortalama")

                    # OBV Divergence check
                    _, obv_divergence = self._volume_analyzer.calculate_obv(closes, volumes)
                    if obv_divergence:
                        lines.append(f"  ⚠️ OBV Divergence: Fiyat-Hacim uyumsuzluğu tespit edildi")

                    lines.append("")

            # 3. ADX Data (Raw values - no interpretation)
            if hist_primary.get("high") and hist_primary.get("low") and hist_primary.get("close"):
                highs = hist_primary["high"]
                lows = hist_primary["low"]
                closes = hist_primary["close"]

                adx_result = calculate_adx(highs, lows, closes, period=14)
                if adx_result:
                    adx_list = adx_result.get("adx", [])
                    plus_di_list = adx_result.get("plus_di", [])
                    minus_di_list = adx_result.get("minus_di", [])

                    if adx_list and plus_di_list and minus_di_list:
                        adx_value = adx_list[-1]
                        plus_di = plus_di_list[-1]
                        minus_di = minus_di_list[-1]

                        # Raw data + RSI contextual interpretation
                        lines.extend([
                            "ADX DATA:",
                            f"  ADX: {adx_value:.1f}",
                            f"  +DI: {plus_di:.1f} | -DI: {minus_di:.1f}",
                        ])

                        # RSI Context (ADX-aware interpretation)
                        rsi_values = hist_primary.get("rsi_14", [])
                        if rsi_values:
                            rsi_current = rsi_values[-1] if rsi_values else 50.0
                            price_slope = calculate_slope(closes, 4) if closes else 0.0
                            rsi_label, rsi_desc = self._interpret_rsi_context(rsi_current, adx_value, price_slope)
                            lines.append(f"  RSI Context: [{rsi_label}]")
                            lines.append(f"  Interpretation: {rsi_desc}")

                        lines.append("")

            # 4. Funding Rate Analysis (with signals)
            if futures_data and futures_data.get("current"):
                funding_rate = futures_data["current"].get("funding_rate", 0)
                funding_avg = futures_data.get("averages", {}).get("funding_rate_avg", funding_rate)

                if funding_rate != 0:
                    lines.extend([
                        "FUNDING RATE:",
                        f"  Current: {funding_rate:.6f} ({funding_rate*100:.4f}%)",
                        f"  8h Avg: {funding_avg:.6f}",
                    ])

                    # Funding Rate Analyzer - sinyal ve uyarı üret
                    if self._funding_analyzer:
                        # Trend direction'ı belirle
                        trend_direction = "NEUTRAL"
                        if hist_primary.get("close"):
                            closes = hist_primary["close"]
                            ema20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 2 else closes[-1]
                            if current_price > ema20 * 1.01:
                                trend_direction = "BULLISH"
                            elif current_price < ema20 * 0.99:
                                trend_direction = "BEARISH"

                        funding_analysis = self._funding_analyzer.analyze(
                            current_rate=funding_rate,
                            rate_8h_avg=funding_avg,
                            rate_24h_avg=funding_avg,  # Simplified
                            price_trend=trend_direction,
                        )

                        # Sinyal ve uyarı bilgisini ekle
                        if funding_analysis.signal.value != "neutral":
                            signal_emoji = {
                                "contrarian_long": "🟢",
                                "contrarian_short": "🔴",
                                "squeeze_risk_long": "⚠️",
                                "squeeze_risk_short": "⚠️",
                            }.get(funding_analysis.signal.value, "")

                            lines.append(f"  Signal: {signal_emoji} {funding_analysis.signal.value.upper()}")

                            if funding_analysis.confidence_adjustment != 0:
                                adj_sign = "+" if funding_analysis.confidence_adjustment > 0 else ""
                                lines.append(f"  Confidence Adj: {adj_sign}{funding_analysis.confidence_adjustment}")

                            if funding_analysis.warning:
                                lines.append(f"  Warning: {funding_analysis.warning}")

                            if funding_analysis.opportunity:
                                lines.append(f"  Opportunity: {funding_analysis.opportunity}")

                    # OI Context (Funding + OI kombinasyonu yorumu)
                    oi_current = futures_data.get("current", {}).get("open_interest", 0)
                    oi_avg = futures_data.get("averages", {}).get("open_interest_avg", oi_current)
                    if oi_current > 0 and hist_primary.get("close"):
                        closes = hist_primary["close"]
                        price_slope = calculate_slope(closes, 4) if closes else 0.0
                        oi_label, oi_desc = self._interpret_funding_oi_context(
                            funding_rate, oi_current, oi_avg, price_slope
                        )
                        
                        # Add to active glossary contexts for dynamic glossary
                        if oi_label and oi_label != "NEUTRAL":
                            if oi_label not in self._active_glossary_contexts:
                                self._active_glossary_contexts.append(oi_label)
                        
                        lines.append(f"  OI Context: [{oi_label}]")
                        lines.append(f"  Interpretation: {oi_desc}")

                    lines.append("")

            # 5. Liquidation Levels (Raw distances only)
            if current_price > 0 and hist_primary.get("high") and hist_primary.get("low"):
                recent_high = max(hist_primary["high"][-20:]) if len(hist_primary["high"]) >= 20 else max(hist_primary["high"])
                recent_low = min(hist_primary["low"][-20:]) if len(hist_primary["low"]) >= 20 else min(hist_primary["low"])
                oi = futures_data.get("current", {}).get("open_interest", 0) if futures_data else 0

                if oi > 0:
                    liq_result = self._liquidation_analyzer.analyze(
                        symbol, current_price, recent_high, recent_low, oi
                    )
                    if liq_result.feature_enabled:
                        lines.append("LIQUIDATION LEVELS:")
                        if liq_result.nearest_long_liq:
                            lines.append(f"  Long Liq: {liq_result.nearest_long_liq.price:,.0f} ({liq_result.nearest_long_liq.distance_pct:.1f}% below)")
                        if liq_result.nearest_short_liq:
                            lines.append(f"  Short Liq: {liq_result.nearest_short_liq.price:,.0f} ({liq_result.nearest_short_liq.distance_pct:.1f}% above)")
                        lines.append("")

            # 6. Drawdown Data (Raw percentages)
            equity = portfolio_metrics.get("equity", 10000)
            dd_status = self._drawdown_manager.get_status()
            if dd_status.get("feature_enabled"):
                daily_dd = dd_status.get("current_daily_drawdown_pct", 0)
                weekly_dd = dd_status.get("current_weekly_drawdown_pct", 0)
                max_dd = dd_status.get("current_max_drawdown_pct", 0)

                lines.extend([
                    "DRAWDOWN DATA:",
                    f"  Daily: {daily_dd:.2f}%",
                    f"  Weekly: {weekly_dd:.2f}%",
                    f"  Max: {max_dd:.2f}%",
                ])
                lines.append("")

            # 7. Correlation Guard (if positions exist)
            long_pos = portfolio_metrics.get("long_position", 0)
            short_pos = portfolio_metrics.get("short_position", 0)
            if abs(long_pos) > 0.0001 or abs(short_pos) > 0.0001:
                portfolio_risk = self._correlation_guard.get_portfolio_risk(equity)
                if portfolio_risk.get("feature_enabled"):
                    lines.extend([
                        "CORRELATION RISK:",
                        f"  Total Exposure: {portfolio_risk.get('total_exposure', 0):.1%}",
                        f"  Diversification: {portfolio_risk.get('diversification_score', 1):.2f}",
                    ])
                    lines.append("")

            # 8. TP/Hold Decision Summary (v3.1 - Token Efficient)
            position_data = portfolio_metrics.get("open_position")
            if position_data and position_data.get("entry_time"):
                tp_hold_summary = self._build_tp_hold_summary(symbol, position_data, current_price)
                if tp_hold_summary:
                    lines.append(tp_hold_summary)
                    lines.append("")

        except Exception as e:
            logger.warning("Error building enhanced features section: %s", e)
            lines.append(f"[Enhanced features error: {str(e)[:50]}]")
            lines.append("")

        return "\n".join(lines)

    def _build_tp_hold_summary(
        self,
        symbol: str,
        position_data: Dict,
        current_price: float,
    ) -> str:
        """
        Build compact TP/Hold summary for prompt (~50-80 tokens).

        Format: TP: BALANCED | TP1✅ TP2⏳ | BE✅ TRAIL❌ | HOLD: 78% | 12.5h
        """
        try:
            from datetime import datetime, timezone

            entry_time = position_data.get("entry_time", "")
            entry_price = position_data.get("entry_price", 0)
            position_side = position_data.get("side", "LONG")
            unrealized_pnl_pct = position_data.get("unrealized_pnl_pct", 0.0)
            stop_loss = position_data.get("stop_loss", 0)

            if not entry_time or entry_price <= 0:
                return ""

            current_time = datetime.now(timezone.utc).isoformat()

            # Price change since entry
            if entry_price > 0:
                price_change_pct = ((current_price - entry_price) / entry_price) * 100
                if position_side == "SHORT":
                    price_change_pct = -price_change_pct
            else:
                price_change_pct = 0.0

            parts = []

            # 1. Time-based exit check
            time_result = self._time_exit_manager.check_time_based_exit(
                entry_time=entry_time,
                current_time=current_time,
                unrealized_pnl_pct=unrealized_pnl_pct,
                price_change_since_entry_pct=price_change_pct,
            )
            time_summary = self._time_exit_manager.get_summary_for_prompt(time_result)
            if time_summary:
                parts.append(time_summary)

            # 2. Hold decision (simplified - only if position has been held for a while)
            hours_held = time_result.hours_held
            if hours_held >= 1.0:
                # Get trend info from cached data
                current_trend = position_data.get("current_trend", "NEUTRAL")
                trend_strength = position_data.get("trend_strength", "MODERATE")
                rsi = position_data.get("rsi", 50)
                volume_trend = position_data.get("volume_trend", "STABLE")
                mtf_confluence = position_data.get("mtf_confluence", 50)

                # Initialize position if not already
                if symbol not in self._hold_engine._position_states:
                    self._hold_engine.initialize_position(
                        symbol=symbol,
                        entry_price=entry_price,
                        entry_time=entry_time,
                        position_side=position_side,
                        stop_loss=stop_loss if stop_loss > 0 else entry_price * 0.97,
                    )

                hold_eval = self._hold_engine.evaluate_hold(
                    symbol=symbol,
                    current_price=current_price,
                    current_time=current_time,
                    current_trend=current_trend,
                    trend_strength=trend_strength,
                    rsi=rsi,
                    volume_trend=volume_trend,
                    mtf_confluence=mtf_confluence,
                    unrealized_pnl_pct=unrealized_pnl_pct,
                )
                hold_summary = self._hold_engine.get_summary_for_prompt(symbol, hold_eval)
                if hold_summary:
                    parts.append(hold_summary)

            # 3. TP Status (if available)
            tp_plan = position_data.get("tp_plan")
            if tp_plan and hasattr(self._partial_tp_manager, 'get_compact_summary'):
                tp_summary = self._partial_tp_manager.get_compact_summary(tp_plan)
                if tp_summary:
                    parts.append(tp_summary)

            if parts:
                return "POSITION MGT: " + " | ".join(parts)
            return ""

        except Exception as e:
            logger.debug("Error building TP/Hold summary: %s", e)
            return ""

    # ---------------------------------------------------------------------
    # BTC CORRELATION CONTEXT (for ETH/SOL trading)
    # ---------------------------------------------------------------------
    def _determine_tf_trend(self, features: Dict[str, Any]) -> str:
        """Determine trend direction from features dict."""
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

    def _fetch_btc_correlation_context(self) -> Optional[Dict[str, Any]]:
        """
        Fetch BTC trend data for ETH/SOL correlation analysis.
        Uses InfluxDB enriched data - compressed output (~80 tokens).
        Queries multiple timeframes for consensus/conflict detection.

        Returns structured multi-TF data:
        - tf_trends: Dict of timeframe -> trend direction
        - consensus_trend: The agreed trend if all TFs align, else "MIXED"
        - has_conflict: True if TFs show conflicting signals
        - primary_trend: Trend from primary timeframe
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
                    trend = self._determine_tf_trend(btc_features)
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

    # ---------------------------------------------------------------------
    # EXIT PLAN CALCULATOR (Python-computed, always valid)
    # ---------------------------------------------------------------------
    def calculate_exit_plan(
        self,
        signal: str,
        entry_price: float,
        historical_arrays: Dict,
    ) -> Dict:
        """
        Calculate valid exit plan based on volatility and S/R levels.
        Always produces valid SL/TP that satisfies R:R requirements.
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

        atr_pct = self._current_atr_pct or 1.0
        vol = self._current_volatility or 0.5

        # Get S/R levels
        sr = self._calculate_sr_distances(historical_arrays, entry_price)

        # Get bounds from existing methods
        position_side = "LONG" if signal == "BUY" else "SHORT"
        min_sl, max_sl, regime = self._sl_distance_bounds(atr_pct, vol, position_side)
        min_tp, max_tp, _ = self._tp_distance_bounds(atr_pct, vol)
        min_rr = self._get_min_rr_ratio(vol, position_side)

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
            # SECURITY: Use safe division to prevent division by zero
            if self._safe_divide(tp_dist, sl_dist, default=min_rr) < min_rr and tp_dist >= min_tp:
                sl_dist = self._safe_divide(tp_dist, min_rr, default=min_sl)
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
            # SECURITY: Use safe division to prevent division by zero
            if self._safe_divide(tp_dist, sl_dist, default=min_rr) < min_rr and tp_dist >= min_tp:
                sl_dist = self._safe_divide(tp_dist, min_rr, default=min_sl)
                sl_dist = max(min_sl, sl_dist)

            # SECURITY: Ensure SL/TP distances are within safe bounds
            sl_dist = max(0.3, min(sl_dist, 99.0))  # 0.3% to 99%
            tp_dist = max(0.6, min(tp_dist, 99.0))  # 0.6% to 99%

            stop_loss = entry_price * (1 + sl_dist / 100)
            profit_target = entry_price * (1 - tp_dist / 100)

            inv_price = entry_price * (1 + sl_dist * 0.7 / 100)
            invalidation = f"If price closes above {inv_price:.2f} on 15m candle"

        # Leverage based on regime
        leverage, _ = self._recommended_leverage_cap(vol, position_side)

        # Final R:R calculation (using safe divide)
        final_rr = self._safe_divide(tp_dist, sl_dist, default=0.0)

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
            "invalidation_timeframe": "15m",  # Invalidation kontrolü için gereken mum periyodu
            "leverage": leverage,
            "sl_distance_pct": round(sl_dist, 2),
            "tp_distance_pct": round(tp_dist, 2),
            "rr_ratio": round(final_rr, 2),
        }

    # ---------------------------------------------------------------------
    # PROMPT BUILD
    # ---------------------------------------------------------------------
    def build_prompt(
        self,
        raw_market_data: Dict[str, Any],
        portfolio_metrics: Dict[str, Any],
        htf_analysis: Dict[str, Any] = None,
    ) -> str:
        self._invocation_count += 1
        
        # Reset active glossary contexts for this invocation
        self._active_glossary_contexts = []

        runtime_minutes = int((datetime.utcnow() - self._start_time).total_seconds() / 60)
        current_time = datetime.utcnow()

        symbol = raw_market_data.get("symbol", "BTCUSDT")
        current_snapshots = raw_market_data.get("current_snapshots", {})
        historical_arrays = raw_market_data.get("historical_arrays", {})
        futures_data = raw_market_data.get("futures_data", {})

        # GLM özgürlüğü: BTC correlation context kaldırıldı

        # Cache historical_arrays for exit plan calculation (used by manager.py)
        self._cached_historical_arrays = historical_arrays

        current_price = 0.0
        atr_value = 0.0
        # Use primary timeframe (4H for swing, 30M for scalp)
        primary_tf = self._primary_tf
        if current_snapshots.get(primary_tf):
            current_price = current_snapshots[primary_tf].get("close", 0.0)
            # Use pre-calculated ATR from enriched data if available
            atr_value = current_snapshots[primary_tf].get("atr_14", 0.0)

        volatility_score = 0.5
        hist_primary = historical_arrays.get(primary_tf, {})
        if hist_primary and "close" in hist_primary:
            closes = hist_primary["close"]

            # Fall back to calculating ATR from closes if not available from snapshot
            if atr_value == 0.0:
                atr_value = self._risk_manager.calculate_atr(closes, period=14)
            volatility_score = self._risk_manager.calculate_volatility(closes)

            # Relative volatility context
            realized_vol_pct = compute_realized_vol_pct(closes, lookback=50)

            abs_ret_pct_series = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    abs_ret_pct_series.append(abs(closes[i] - closes[i - 1]) / closes[i - 1] * 100)

            # Sample realized vol history using cached strategic sampling (O(1) vs O(n))
            realized_samples = self._vol_cache.get_cached_samples(symbol, closes, compute_realized_vol_pct)

            median_realized = rolling_median(realized_samples, lookback=20)
            median_atr_pct = rolling_median(abs_ret_pct_series, lookback=200)

            self._current_realized_vol_pct = realized_vol_pct
            self._median_realized_vol_pct = median_realized
            self._median_atr_pct = median_atr_pct

            self._vol_ratio = (realized_vol_pct / median_realized) if median_realized > 0 else 1.0
            current_atr_pct_est = abs_ret_pct_series[-1] if abs_ret_pct_series else 0.0
            self._atr_ratio = (current_atr_pct_est / median_atr_pct) if median_atr_pct > 0 else 1.0

        self._current_volatility = volatility_score
        self._current_atr = atr_value
        self._current_atr_pct = (atr_value / current_price * 100) if (current_price and atr_value) else None

        # DEBUG: Log ATR calculation details
        logger.info(
            "📊 ATR Debug [%s]: current_price=%.2f, atr_value=%.4f, atr_pct=%s, "
            "hist_exists=%s, close_count=%d",
            primary_tf,
            current_price,
            atr_value,
            f"{self._current_atr_pct:.2f}%" if self._current_atr_pct else "None",
            bool(hist_primary),
            len(hist_primary.get("close", [])) if hist_primary else 0
        )

        self._update_performance_tracking(portfolio_metrics)

        # GLM özgürlüğü: Regime/direction/btc_context hesaplamaları kaldırıldı

        sections = []
        sections.append(self._build_header(runtime_minutes, current_time))
        sections.append(self._build_pre_calculated_section(
            current_snapshots, historical_arrays, current_price
        ))
        sections.append(self._build_market_state(
            symbol, current_snapshots, historical_arrays, futures_data,
            atr_value=atr_value, htf_analysis=htf_analysis
        ))
        sections.append(self._build_account_info(portfolio_metrics, symbol=symbol))

        # Enhanced Features Section (v2.0)
        enhanced_section = self._build_enhanced_features_section(
            symbol, current_price, historical_arrays, futures_data, portfolio_metrics
        )
        if enhanced_section:
            sections.append(enhanced_section)

        # Feedback Loop - Son 5 işlem bilgisi (sadece veri, uyarı yok)
        feedback_section = self._build_feedback_section(portfolio_metrics)
        if feedback_section:
            sections.append(feedback_section)

        # Pozisyon durumunu belirle (instructions için)
        long_pos = portfolio_metrics.get("long_position", 0.0)
        short_pos = portfolio_metrics.get("short_position", 0.0)
        has_long = abs(long_pos) > 0.0001
        has_short = abs(short_pos) > 0.0001
        has_position = has_long or has_short
        position_type = "LONG" if has_long else ("SHORT" if has_short else None)

        # Position-aware instructions (Red Team Mode için ek veriler)
        sections.append(self._build_instructions(
            symbol=symbol,
            has_position=has_position,
            position_type=position_type,
            current_price=current_price,
            current_snapshots=current_snapshots,
            historical_arrays=historical_arrays,
            futures_data=futures_data
        ))

        return "\n\n".join(sections)

    def _update_performance_tracking(self, portfolio_metrics: Dict[str, Any]) -> None:
        # Check for consecutive losses override flag
        if getattr(self._settings, 'override_consecutive_losses', False):
            self._consecutive_losses = 0
            self._last_trade_side = None
            self._performance_history = []
            self._recent_win_rate = 0.5
            logger.info("🔄 Consecutive losses override ACTIVE - reset to 0")
            return

        recent_trades = portfolio_metrics.get("recent_trades", [])

        if not recent_trades:
            self._consecutive_losses = 0
            self._last_trade_side = None
            self._performance_history = []
            self._recent_win_rate = 0.5
            return

        consecutive_losses = 0
        for trade in recent_trades[:3]:
            pnl = trade.get("pnl", 0)
            if pnl < 0:
                consecutive_losses += 1
            else:
                break
        self._consecutive_losses = consecutive_losses

        last_trade = recent_trades[0]
        side = last_trade.get("side", "").upper()
        if side in ["BUY", "SELL"]:
            self._last_trade_side = "LONG" if side == "BUY" else "SHORT"

        last10 = recent_trades[:10]
        self._performance_history = [1 if t.get("pnl", 0) > 0 else 0 for t in last10]
        self._recent_win_rate = (
            sum(self._performance_history) / len(self._performance_history)
            if self._performance_history else 0.5
        )

    def _build_feedback_section(self, portfolio_metrics: Dict[str, Any]) -> str:
        """Compressed last 5 trades feedback for GLM learning (~100 tokens)"""
        recent_trades = portfolio_metrics.get("recent_trades", [])

        if not recent_trades:
            return ""

        trades = recent_trades[:5]
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        losses = len(trades) - wins

        # Safe average calculation
        pnl_values = [t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct") is not None]
        avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0

        # Streak detection
        streak = 0
        if trades:
            streak_type = "W" if trades[0].get("pnl", 0) > 0 else "L"
            for t in trades:
                if (t.get("pnl", 0) > 0) == (streak_type == "W"):
                    streak += 1
                else:
                    break
        else:
            streak_type = "N"

        lines = [
            "",
            "=" * 60,
            "SON 5 İŞLEM GERİ BİLDİRİMİ (FEEDBACK LOOP)",
            "=" * 60,
            f"Sonuç: {wins}W/{losses}L | Streak: {streak}{streak_type} | Ort: {avg_pnl:+.1f}%",
        ]

        # Loss streak uyarısı - overtrading önleme
        if streak >= 2 and streak_type == "L":
            lines.append(f"⚠️ UYARI: {streak} ardışık kayıp - overtrading riski, confidence -5")

        # Show last trade direction for bias consideration
        if trades:
            last_side = trades[0].get("side", "")
            last_pnl = trades[0].get("pnl_pct", 0)
            if last_side:
                lines.append(f"Son işlem: {last_side} ({last_pnl:+.1f}%)")

        lines.append("")

        return "\n".join(lines)

    def _build_header(self, runtime_minutes: int, current_time: datetime) -> str:
        # GLM özgürlüğü: Minimal header, trading mode/regime yok
        return f"""Runtime: {runtime_minutes} min | Time: {current_time} | Invocation: {self._invocation_count}

DATA ORDER: OLDEST → NEWEST"""

    def _build_market_state(
        self,
        symbol: str,
        current_snapshots: Dict[str, Dict],
        historical_arrays: Dict[str, Dict],
        futures_data: Dict[str, Any],
        atr_value: float = 0.0,
        htf_analysis: Dict[str, Any] = None,
    ) -> str:
        lines = [
            "=" * 80,
            f"CURRENT MARKET STATE FOR {symbol}",
            "=" * 80,
        ]

        # Use primary timeframe (4H for swing, 30M for scalp)
        primary_tf = self._primary_tf
        data_primary = current_snapshots.get(primary_tf, {})
        hist_primary = historical_arrays.get(primary_tf, {})

        volatility_score = self._current_volatility or 0.5
        atr_pct = self._current_atr_pct or 0.0
        regime_key = self._vol_regime_key()

        # GLM özgürlüğü: Sadece ham veri, yargı/etiket yok
        lines.extend([
            "",
            "VOLATILITY DATA:",
            f"• Volatility Score: {volatility_score:.2f}",
            f"• Volatility Regime: {regime_key.upper()}",
            f"• ATR (14-period): {atr_value:.2f} ({atr_pct:.2f}%)",
            "",
        ])

        # Relative vol diagnostics
        rel = max(self._vol_ratio or 1.0, self._atr_ratio or 1.0)
        lines.extend([
            "RELATIVE VOLATILITY CONTEXT (asset-agnostic):",
            f"• Realized Vol (50 bars): {(self._current_realized_vol_pct or 0.0):.2f}%",
            f"• Median Realized Vol: {(self._median_realized_vol_pct or 0.0):.2f}%",
            f"• Vol Ratio (current/median): {(self._vol_ratio or 1.0):.2f}x",
            f"• ATR Ratio (proxy): {(self._atr_ratio or 1.0):.2f}x",
            f"• Relative Intensity (max ratio): {rel:.2f}x",
            f"• Regime Key: {regime_key.upper()}",
            "",
        ])

        # Volatilite Risk Etkisi Rehberi
        lines.extend([
            "VOLATILITE RISK ETKISI:",
            "• LOW regime: Dar SL kabul edilebilir, R:R hedefi yukselt (2:1+)",
            "• MEDIUM regime: Standart SL/TP mesafeleri uygula",
            "• HIGH regime: Pozisyon boyutu %50 azalt, SL mesafesi 1.2x genis tut",
            "• EXTREME regime: Confidence -15, pozisyon boyutu %75 azalt veya HOLD",
            "• Dusuk hacim (Vol Ratio < 0.3) + HIGH vol = Sahte kirilma riski yuksek",
            "",
        ])

        # Optional HTF S/R injection
        if htf_analysis:
            sr = htf_analysis.get("support_resistance", [])
            if sr:
                lines.extend(["HTF SUPPORT/RESISTANCE (external):"])
                for lvl in sr[:6]:
                    lines.append(f"• {lvl}")
                lines.append("")

        # Key levels
        hist_4h = historical_arrays.get("4h", {})
        hist_1d = historical_arrays.get("1d", {})
        key_levels = []

        if hist_4h and "high" in hist_4h and "low" in hist_4h:
            h4_high = max(hist_4h["high"][-10:])
            h4_low = min(hist_4h["low"][-10:])
            current_price = data_primary.get("close", 0) if data_primary else 0
            if current_price > 0:
                dist_to_high = ((h4_high - current_price) / current_price) * 100
                dist_to_low = ((current_price - h4_low) / current_price) * 100
                key_levels.append(f"• 4H Resistance (Liquidity Pool): {h4_high:.2f} (Distance: +{dist_to_high:.2f}%)")
                key_levels.append(f"• 4H Support (Liquidity Pool): {h4_low:.2f} (Distance: -{dist_to_low:.2f}%)")
            else:
                key_levels.append(f"• 4H Resistance (High): {h4_high:.2f}")
                key_levels.append(f"• 4H Support (Low): {h4_low:.2f}")

        if hist_1d and "high" in hist_1d and "low" in hist_1d:
            d1_high = max(hist_1d["high"][-5:])
            d1_low = min(hist_1d["low"][-5:])
            key_levels.append(f"• Daily Resistance: {d1_high:.2f}")
            key_levels.append(f"• Daily Support: {d1_low:.2f}")

        if key_levels:
            lines.extend([
                "LIQUIDITY POOLS & KEY LEVELS (Targets for TP / Invalidation for SL):",
                *key_levels,
                ""
            ])

        # Trend + Structure + Price Action (using primary timeframe)
        if data_primary and hist_primary:
            current_price = data_primary.get("close", 0)
            ema20 = data_primary.get("ema_20", 0)
            ema50 = data_primary.get("ema_50", 0)
            rsi = data_primary.get("rsi_14", 50)
            ema20_dist = ((current_price - ema20) / ema20 * 100) if ema20 else 0.0
            ema50_dist = ((current_price - ema50) / ema50 * 100) if ema50 else 0.0

            trend_direction = "NEUTRAL"
            trend_strength = "WEAK"
            if current_price > ema20 and ema20 > ema50:
                trend_direction = "BULLISH"
                if current_price > ema20 * 1.02 and ema20 > ema50 * 1.01:
                    trend_strength = "STRONG"
                elif current_price > ema20 * 1.01 and ema20 > ema50 * 1.005:
                    trend_strength = "MODERATE"
            elif current_price < ema20 and ema20 < ema50:
                trend_direction = "BEARISH"
                if current_price < ema20 * 0.98 and ema20 < ema50 * 0.99:
                    trend_strength = "STRONG"
                elif current_price < ema20 * 0.99 and ema20 < ema50 * 0.995:
                    trend_strength = "MODERATE"

            market_structure = self._analyze_market_structure(hist_primary)

            fomo_long_band = ema20 + 2 * atr_value if (ema20 and atr_value) else None
            fomo_short_band = ema20 - 2 * atr_value if (ema20 and atr_value) else None

            tf_label = self._primary_tf.upper()  # "4H" - volatility-based, no swing/scalp
            lines.extend([
                f"PRIMARY TIMEFRAME ({tf_label}) - ENHANCED ANALYSIS:",
                f"Trend Direction: {trend_direction} ({trend_strength})",
                f"Market Structure (pivot-based): {market_structure}",
                f"Current Price: {current_price:.2f}",
                f"EMA 20: {ema20:.2f} (Distance: {ema20_dist:+.2f}%)",
                f"EMA 50: {ema50:.2f} (Distance: {ema50_dist:+.2f}%)",
                f"RSI (14): {rsi:.2f}",
                "",
            ])

            if fomo_long_band and fomo_short_band:
                lines.extend([
                    "VOLATILITY BANDS (EMA20 ± 2*ATR):",
                    f"• Upper Band: {fomo_long_band:.2f}",
                    f"• Lower Band: {fomo_short_band:.2f}",
                    "",
                ])

            # Data conflict detection
            data_conflicts = self._detect_data_conflicts(
                trend_direction=trend_direction,
                market_structure=market_structure,
                rsi=rsi,
                ema_distance=ema20_dist
            )
            if data_conflicts:
                lines.extend([
                    "DATA CONFLICTS DETECTED:",
                    *data_conflicts,
                    "",
                ])

            lines.extend([
                f"SERIES SUMMARIES ({primary_tf} tail stats):",
                summarize_series(hist_primary.get("close", []), 2, "Close"),
                summarize_series(hist_primary.get("ema_20", []), 2, "EMA20"),
                summarize_series(hist_primary.get("ema_50", []), 2, "EMA50"),
                summarize_series(hist_primary.get("macd", []), 2, "MACD"),
                summarize_series(hist_primary.get("rsi_14", []), 2, "RSI14"),
                "",
            ])

        # Futures data
        if futures_data and futures_data.get("current"):
            lines.extend([
                "",
                "=" * 80,
                "FUTURES MARKET DATA:",
                "",
            ])
            cur_f = futures_data.get("current", {})
            avg_f = futures_data.get("averages", {})

            fr = cur_f.get("funding_rate")  # None if missing, allows 0 as valid value
            oi = cur_f.get("open_interest")  # None if missing, allows 0 as valid value
            lsr = cur_f.get("long_short_ratio")  # None if missing, allows 0 as valid value

            lines.extend([
                f"Funding Rate: {fr:.8f}" if fr is not None else "Funding Rate: N/A",
                f"  (8h avg: {avg_f.get('funding_rate_avg', 0):.8f})",
                "",
                f"Open Interest: {oi:.2f}" if oi is not None else "Open Interest: N/A",
                f"  (20p avg: {avg_f.get('open_interest_avg', 0):.2f})",
                "",
                f"Long/Short Ratio: {lsr:.4f}" if lsr is not None else "Long/Short Ratio: N/A",
                f"  (20p avg: {avg_f.get('long_short_ratio_avg', 0):.4f})",
                "",
            ])

            # Futures Yorum Rehberi
            lines.extend([
                "FUTURES YORUM REHBERI:",
                "- OI azaliyor + Fiyat dusuyor = Long Liquidation (bearish devam)",
                "- OI artiyor + Fiyat dusuyor = Aggressive Shorting (short squeeze riski)",
                "- OI azaliyor + Fiyat yukseliyor = Short Liquidation (bullish devam)",
                "- OI artiyor + Fiyat yukseliyor = New Longs (bullish momentum)",
                "- L/S > 3.0 + Destek yakin = Long squeeze potansiyeli",
                "- Funding > 0.01% = Crowded long, contrarian short sinyali",
                "- Funding < -0.01% = Crowded short, contrarian long sinyali",
                "",
            ])

        # Intraday 1m summary
        hist_1m = historical_arrays.get("1m", {})
        if hist_1m and "close" in hist_1m:
            lines.extend([
                "",
                "=" * 80,
                "INTRADAY (1-minute) SUMMARY:",
                summarize_series(hist_1m.get("close", []), 2, "1m Close"),
                summarize_series(hist_1m.get("rsi_14", []), 2, "1m RSI14"),
                summarize_series(hist_1m.get("macd", []), 2, "1m MACD"),
                "",
            ])

        # v3.2: Narrative Context - yorumlanmış veri
        # NOTE: LONGER-TERM CONTEXT (4H) ve ADDITIONAL TIMEFRAMES SNAPSHOT kaldırıldı
        # Çünkü aynı veriler Section A (MTF Structure) ve PRIMARY TIMEFRAME'de zaten mevcut
        lines.append(self._generate_narrative(data_primary))

        return "\n".join(lines)

    def _build_account_info(self, portfolio_metrics: Dict[str, Any], symbol: str = "BTCUSDT") -> str:
        lines = [
            "=" * 80,
            "HERE IS YOUR ACCOUNT INFORMATION & PERFORMANCE",
            "=" * 80,
            "",
        ]

        notification = self._check_position_close_notification(symbol=symbol)
        if notification:
            lines.extend(self._format_close_notification(notification, symbol=symbol))

        base_asset = symbol.replace("USDT", "")

        equity = portfolio_metrics.get("equity", 10000)
        initial_capital = portfolio_metrics.get("initial_capital") or 10000
        if initial_capital <= 0:
            initial_capital = 10000
        total_return_pct = ((equity - initial_capital) / initial_capital) * 100 if initial_capital else 0.0

        lines.extend([
            f"Current Total Return (percent): {total_return_pct:.2f}%",
            f"Available Cash: {portfolio_metrics.get('available_cash', equity):.2f}",
            f"Current Account Value: {equity:.2f}",
        ])
        # Win Rate conditional display
        if len(self._performance_history) >= 3:
            lines.append(f"Recent Win Rate (last {len(self._performance_history)} trades): {self._recent_win_rate:.0%}")
        elif len(self._performance_history) > 0:
            lines.append(f"Recent Win Rate: Insufficient data ({len(self._performance_history)} trades)")
        else:
            lines.append("Recent Win Rate: N/A (no trade history)")
        lines.append("")

        long_position = portfolio_metrics.get("long_position", 0.0)
        short_position = portfolio_metrics.get("short_position", 0.0)
        net_position = portfolio_metrics.get("net_position", portfolio_metrics.get("position", 0.0))

        has_long = abs(long_position) > 0.0001
        has_short = abs(short_position) > 0.0001

        current_price = portfolio_metrics.get("current_price", 0)
        last_trade_price = portfolio_metrics.get("last_trade_price")

        # P1-003 FIX: Use TradingConfig values instead of hardcoded
        trading_cfg = self._settings.trading
        taker_fee_pct = trading_cfg.taker_fee_pct
        maker_fee_pct = trading_cfg.maker_fee_pct
        example_position_usd = trading_cfg.example_position_usd
        max_margin = trading_cfg.max_margin_per_position
        max_leverage = trading_cfg.max_leverage

        example_fee = example_position_usd * (taker_fee_pct / 100)

        lines.extend([
            "=" * 80,
            "FEE COSTS & TRADING LIMITS",
            "=" * 80,
            "",
            f"Taker Fee: {taker_fee_pct}% (per trade)",
            f"Maker Fee: {maker_fee_pct}% (per trade)",
            f"Example fee for ${example_position_usd:,.0f} position: ${example_fee:.2f}",
            f"Round-trip cost (open + close): ${example_fee * 2:.2f}",
            "",
            "LIMITS:",
            f"  • Maximum margin per position: ${max_margin:,.0f}",
            f"  • Maximum leverage: {max_leverage}x",
            "",
        ])

        if last_trade_price and current_price:
            pc = abs((current_price - last_trade_price) / last_trade_price) * 100
            status = "✓ OK" if pc <= 5.0 else "✗ BLOCKED"
            lines.extend([
                "=" * 80,
                "PRICE CHANGE AWARENESS",
                "=" * 80,
                "",
                f"Last trade price: ${last_trade_price:.2f}",
                f"Current price: ${current_price:.2f}",
                f"Price change: {pc:.2f}%",
                f"System threshold status: {status}",
                "",
            ])

        if has_long or has_short:
            lines.extend([
                "=" * 80,
                "CURRENT LIVE POSITIONS",
                "=" * 80,
                "",
            ])

            if has_long:
                long_entry = portfolio_metrics.get("long_avg_price", 0)
                long_pnl_pct = ((current_price - long_entry) / long_entry) * 100 if long_entry else 0
                long_pnl_usd = (current_price - long_entry) * long_position if long_entry else 0
                long_lev = portfolio_metrics.get("long_leverage", 1)
                long_exit_plan = portfolio_metrics.get("long_exit_plan")

                long_notional = long_position * long_entry
                long_fee = long_notional * (taker_fee_pct / 100)

                lines.extend([
                    "📈 LONG POSITION:",
                    "{",
                    f"  'symbol': '{symbol}',",
                    "  'position_type': 'LONG',",
                    f"  'quantity': {long_position:.6f} {base_asset},",
                    f"  'entry_price': ${long_entry:.2f},",
                    f"  'current_price': ${current_price:.2f},",
                    f"  'unrealized_pnl': ${long_pnl_usd:.2f} ({long_pnl_pct:.2f}%),",
                    f"  'leverage': {long_lev}x,",
                    f"  'entry_fee_paid': ${long_fee:.2f},",
                ])
                if long_exit_plan:
                    sl = long_exit_plan.get("stop_loss", 0)
                    inv = long_exit_plan.get("invalidation_condition", "N/A")
                    lines.extend([
                        "  'exit_plan': {",
                        f"    'stop_loss': ${sl:.2f},",
                        f"    'invalidation': '{inv}'",
                        "  }",
                    ])
                lines.extend(["}", ""])

            if has_short:
                short_entry = portfolio_metrics.get("short_avg_price", 0)
                short_pnl_pct = ((short_entry - current_price) / short_entry) * 100 if short_entry else 0
                short_pnl_usd = (short_entry - current_price) * abs(short_position) if short_entry else 0
                short_lev = portfolio_metrics.get("short_leverage", 1)
                short_exit_plan = portfolio_metrics.get("short_exit_plan")

                short_notional = abs(short_position) * short_entry
                short_fee = short_notional * (taker_fee_pct / 100)

                lines.extend([
                    "📉 SHORT POSITION:",
                    "{",
                    f"  'symbol': '{symbol}',",
                    "  'position_type': 'SHORT',",
                    f"  'quantity': {abs(short_position):.6f} {base_asset},",
                    f"  'entry_price': ${short_entry:.2f},",
                    f"  'current_price': ${current_price:.2f},",
                    f"  'unrealized_pnl': ${short_pnl_usd:.2f} ({short_pnl_pct:.2f}%),",
                    f"  'leverage': {short_lev}x,",
                    f"  'entry_fee_paid': ${short_fee:.2f},",
                ])
                if short_exit_plan:
                    sl = short_exit_plan.get("stop_loss", 0)
                    inv = short_exit_plan.get("invalidation_condition", "N/A")
                    lines.extend([
                        "  'exit_plan': {",
                        f"    'stop_loss': ${sl:.2f},",
                        f"    'invalidation': '{inv}'",
                        "  }",
                    ])
                lines.extend(["}", ""])

            lines.extend([
                f"NET POSITION: {net_position:.6f} {base_asset}",
                f"  • Long: {long_position:.6f} {base_asset}",
                f"  • Short: {short_position:.6f} {base_asset}",
                "",
            ])
        else:
            lines.extend([
                "Current live positions: NONE (FLAT)",
                "",
            ])

        return "\n".join(lines)

    def _build_instructions(self, symbol: str = "BTCUSDT", **kwargs) -> str:
        """
        GLM için pozisyon durumuna göre farklı talimatlar.
        Uses modular template functions from prompts module.
        """
        has_position = kwargs.get('has_position', False)
        position_type = kwargs.get('position_type', None)

        if has_position:
            # Logic Gates section oluştur (if builder available)
            logic_gates_section = ""
            if self._logic_gates_builder and self._enhanced_features_enabled:
                current_price = kwargs.get('current_price', 0.0)
                current_snapshots = kwargs.get('current_snapshots', {})
                historical_arrays = kwargs.get('historical_arrays', {})
                futures_data = kwargs.get('futures_data', {})
                
                if historical_arrays and current_price > 0:
                    try:
                        logic_gates_section = self._logic_gates_builder.build_section(
                            symbol=symbol,
                            position_type=position_type,
                            current_price=current_price,
                            current_snapshots=current_snapshots,
                            historical_arrays=historical_arrays,
                            futures_data=futures_data
                        )
                    except Exception as e:
                        logger.warning(f"Logic gates section failed: {e}")
                        logic_gates_section = "\n[Logic Gates: Hesaplanamadı]\n"
            
            # Build glossary with active contexts
            glossary_section = build_dynamic_glossary(self._active_glossary_contexts)
            
            # Use modular template
            return build_position_active_instructions_template(
                symbol=symbol,
                position_type=position_type,
                logic_gates_section=logic_gates_section,
                glossary_section=glossary_section
            )
        else:
            # Build glossary with active contexts
            glossary_section = build_dynamic_glossary(self._active_glossary_contexts)
            
            # Use modular template
            return build_no_position_instructions_template(
                symbol=symbol,
                glossary_section=glossary_section
            )

    # =========================================================================
    # INSTRUCTION TEMPLATES - Now using modular templates
    # =========================================================================

    def _check_position_close_notification(self, symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
        """
        Check for position close notification in Redis with security validation.

        Security measures:
        1. JSON parsing with error handling
        2. HMAC signature verification (if secret configured)
        3. Pydantic schema validation
        4. Timestamp freshness check (max 5 minutes)
        """
        try:
            from app.utils.redis import get_redis_client

            redis_client = get_redis_client()
            redis_key = f"position_closed:{symbol}"
            notification_json = redis_client.get(redis_key)

            if not notification_json:
                return None

            # 1. Parse JSON safely
            try:
                raw_data = json.loads(notification_json)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in Redis notification: %s", e)
                redis_client.delete(redis_key)
                return None

            # 2. Extract and verify HMAC signature
            signature = raw_data.pop('signature', '')
            if not self._verify_hmac_signature(raw_data, signature):
                logger.warning("⚠️ HMAC verification failed for Redis notification - possible injection attempt!")
                redis_client.delete(redis_key)
                return None

            # 3. Validate with Pydantic model
            try:
                notification = PositionCloseNotification(**raw_data, signature=signature)
            except Exception as e:
                logger.error("Schema validation failed for notification: %s", e)
                redis_client.delete(redis_key)
                return None

            # 4. Check timestamp freshness
            try:
                max_age = self._settings.security.notification_max_age_minutes
                notif_time = datetime.fromisoformat(notification.timestamp.replace('Z', '+00:00'))
                age = datetime.utcnow() - notif_time.replace(tzinfo=None)
                if age > timedelta(minutes=max_age):
                    logger.warning(
                        "Stale notification ignored (age: %s, max: %d min)",
                        age, max_age
                    )
                    redis_client.delete(redis_key)
                    return None
            except (ValueError, TypeError) as e:
                logger.warning("Could not parse notification timestamp: %s", e)
                # Continue anyway - timestamp format may vary

            # Success - delete from Redis and return
            redis_client.delete(redis_key)
            logger.info(
                "✅ GLM read validated position close notification | trigger=%s, pnl=%.2f",
                notification.trigger_type, notification.pnl
            )
            return notification.model_dump()

        except Exception as exc:
            logger.error("Failed to read position close notification from Redis: %s", exc)
            return None

    def _format_close_notification(self, notification: Dict[str, Any], symbol: str = "BTCUSDT") -> List[str]:
        base_asset = symbol.replace("USDT", "")
        trigger_type = notification.get("trigger_type", "unknown")
        # SECURITY: Sanitize reason field to prevent prompt injection
        reason = self._sanitize_prompt_input(notification.get("reason", "N/A"))
        timestamp = notification.get("timestamp", "N/A")
        entry_price = notification.get("entry_price", 0)
        exit_price = notification.get("exit_price", 0)
        pnl = notification.get("pnl", 0)
        pnl_pct = notification.get("pnl_pct", 0)
        position_type = notification.get("position_type", "UNKNOWN")
        quantity = notification.get("quantity", 0)

        emoji_map = {"stop_loss": "🛑", "invalidation": "⚠️"}
        emoji = emoji_map.get(trigger_type, "🔔")

        return [
            "=" * 80,
            f"{emoji} RECENT POSITION CLOSE NOTIFICATION (AUTOMATIC)",
            "=" * 80,
            "",
            f"⏰ Time: {timestamp}",
            f"🎯 Trigger: {trigger_type.upper().replace('_', ' ')}",
            f"📊 Position Type: {position_type}",
            f"📦 Quantity: {quantity:.6f} {base_asset}",
            "",
            f"💵 Entry Price: ${entry_price:,.2f}",
            f"💵 Exit Price: ${exit_price:,.2f}",
            f"💰 PnL: ${pnl:,.2f} ({pnl_pct:+.2f}%)",
            "",
            f"💬 Reason: {reason}",
            "",
            "✅ Position was AUTOMATICALLY CLOSED by exit plan monitor",
            "Focus on NEW opportunities; do not issue CLOSE unless a new position is open.",
            "",
        ]

    # ---------------------------------------------------------------------
    # PUBLIC GETTERS FOR ENHANCED FEATURES (for manager.py integration)
    # ---------------------------------------------------------------------
    def get_position_sizer(self) -> Optional["DynamicPositionSizer"]:
        """Get position sizer for trade sizing calculations"""
        if self._enhanced_features_enabled:
            return self._position_sizer
        return None

    def get_drawdown_manager(self) -> Optional["DrawdownManager"]:
        """Get drawdown manager for equity tracking"""
        if self._enhanced_features_enabled:
            return self._drawdown_manager
        return None

    def get_partial_tp_manager(self) -> Optional["PartialTakeProfitManager"]:
        """Get partial take profit manager"""
        if self._enhanced_features_enabled:
            return self._partial_tp_manager
        return None

    def get_trailing_stop_manager(self) -> Optional["AdvancedTrailingStop"]:
        """Get trailing stop manager"""
        if self._enhanced_features_enabled:
            return self._trailing_stop_manager
        return None

    def get_breakeven_manager(self) -> Optional["BreakevenManager"]:
        """Get breakeven manager"""
        if self._enhanced_features_enabled:
            return self._breakeven_manager
        return None

    def get_time_filter(self) -> Optional["TimeBasedFilter"]:
        """Get time filter for session analysis"""
        if self._enhanced_features_enabled:
            return self._time_filter
        return None

    def get_correlation_guard(self) -> Optional["CorrelationGuard"]:
        """Get correlation guard for exposure management"""
        if self._enhanced_features_enabled:
            return self._correlation_guard
        return None

    def get_volume_analyzer(self) -> Optional["VolumeAnalyzer"]:
        """Get volume analyzer"""
        if self._enhanced_features_enabled:
            return self._volume_analyzer
        return None

    def get_funding_analyzer(self) -> Optional["FundingRateAnalyzer"]:
        """Get funding rate analyzer"""
        if self._enhanced_features_enabled:
            return self._funding_analyzer
        return None

    def get_liquidation_analyzer(self) -> Optional["LiquidationAnalyzer"]:
        """Get liquidation analyzer"""
        if self._enhanced_features_enabled:
            return self._liquidation_analyzer
        return None

    def is_enhanced_features_enabled(self) -> bool:
        """Check if enhanced features are available"""
        return self._enhanced_features_enabled

    def update_drawdown(self, current_equity: float) -> Dict:
        """Update drawdown tracking with current equity"""
        if self._enhanced_features_enabled:
            return self._drawdown_manager.update_equity(current_equity)
        return {"feature_enabled": False}

    def record_trade_result(self, pnl: float) -> Dict:
        """Record trade result for drawdown tracking"""
        if self._enhanced_features_enabled:
            return self._drawdown_manager.record_trade(pnl)
        return {"feature_enabled": False}

    def can_trade_drawdown(self) -> Tuple[bool, str]:
        """Check if trading is allowed based on drawdown limits"""
        if self._enhanced_features_enabled:
            return self._drawdown_manager.can_trade()
        return True, "Drawdown manager disabled"

    def calculate_dynamic_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_pct: float,
        confidence: float,
        atr_pct: float,
    ) -> Dict:
        """Calculate dynamic position size using enhanced position sizer"""
        if self._enhanced_features_enabled:
            result = self._position_sizer.calculate_position_size(
                equity=equity,
                entry_price=entry_price,
                stop_loss_pct=stop_loss_pct,
                confidence=confidence,
                atr_pct=atr_pct,
                win_rate=self._recent_win_rate,
                avg_win_loss_ratio=2.0,
                consecutive_losses=self._consecutive_losses,
                drawdown_multiplier=self._drawdown_manager.get_multiplier() if hasattr(self._drawdown_manager, 'get_multiplier') else 1.0,
            )
            return {
                "quantity": result.quantity,
                "margin_required": result.margin_required,
                "leverage": result.leverage,
                "risk_amount": result.risk_amount,
                "risk_pct": result.risk_pct,
                "position_value": result.position_value,
                "method_used": result.method_used,
                "adjustments": result.adjustments,
                "feature_enabled": True,
            }
        return {"feature_enabled": False}


"""
SAFE JSON PARSE SUGGESTION (put in your executor side, not here):

import json, re

def safe_json_loads(s: str):
    # extract first {...} block greedily
    m = re.search(r'(\{.*\})', s, flags=re.S)
    if not m:
        raise ValueError("No JSON object found")
    block = m.group(1)
    # remove trailing commas
    block = re.sub(r',\s*([}\]])', r'\1', block)
    return json.loads(block)
"""
