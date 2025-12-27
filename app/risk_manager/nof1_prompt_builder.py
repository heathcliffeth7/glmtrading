"""
NOF1.AI Style Prompt Builder for GLM
Builds prompts in the exact format used by professional trading systems

Supports DAY TRADE mode (1H primary) via settings.day_trade_mode

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

# FAZA 3: Builder modules
from app.risk_manager.prompts.builders import (
    NotificationHandler,
    ExitPlanCalculator,
    MarketStateBuilder,
    AccountInfoBuilder,
    EnhancedFeaturesBuilder,
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

        # Primary timeframe - from settings (day trade: 1h, swing: 4h)
        self._primary_tf = getattr(self._settings, 'day_trade_primary_timeframe', '1h')

        # MTF alignment weights - Day Trade mode (1H primary)
        self._mtf_weights = {
            "1d": 2.0,    # Background trend (less weight for day trade)
            "4h": 4.0,    # Trend confirmation (dominant for direction)
            "1h": 3.0,    # PRIMARY TF - main signal
            "30m": 1.0,   # Entry timing support
            "15m": 1.5,   # Entry timing (more weight)
            "5m": 0.0,    # ZERO - noise, ignore
            "1m": 0.0,    # ZERO - noise, ignore
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

        # Initialize builders (NEW)
        self._notification_handler = NotificationHandler(
            settings=self._settings,
            data_validator=self._data_validator
        )
        
        self._exit_plan_calculator = ExitPlanCalculator(
            settings=self._settings,
            primary_tf=self._primary_tf,
            data_validator=self._data_validator
        )
        
        self._market_state_builder = MarketStateBuilder(
            settings=self._settings,
            primary_tf=self._primary_tf,
            market_analyzer=self._market_analyzer,
            entry_analyzer=self._entry_analyzer if self._enhanced_features_enabled else None,
            volume_analyzer=self._volume_analyzer if self._enhanced_features_enabled else None,
            exit_plan_calculator=self._exit_plan_calculator
        )
        
        self._account_info_builder = AccountInfoBuilder(
            settings=self._settings,
            notification_handler=self._notification_handler,
            data_validator=self._data_validator
        )
        
        self._enhanced_features_builder = EnhancedFeaturesBuilder(
            settings=self._settings,
            primary_tf=self._primary_tf,
            volume_analyzer=self._volume_analyzer if self._enhanced_features_enabled else None,
            funding_analyzer=self._funding_analyzer if self._enhanced_features_enabled else None,
            liquidation_analyzer=self._liquidation_analyzer if self._enhanced_features_enabled else None,
            entry_analyzer=self._entry_analyzer if self._enhanced_features_enabled else None,
            hold_engine=self._hold_engine if self._enhanced_features_enabled else None,
            time_exit_manager=self._time_exit_manager if self._enhanced_features_enabled else None,
            time_filter=self._time_filter if self._enhanced_features_enabled else None,
            drawdown_manager=self._drawdown_manager if self._enhanced_features_enabled else None,
            correlation_guard=self._correlation_guard if self._enhanced_features_enabled else None,
            logic_gates_builder=self._logic_gates_builder,
            indicator_interpreter=self._indicator_interpreter,
            enhanced_features_enabled=self._enhanced_features_enabled
        )

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
    # Regime mapping (RELATIVE) - Moved to ExitPlanCalculator
    # ---------------------------------------------------------------------

    def _vol_regime_key(self) -> str:
        """Delegate to ExitPlanCalculator for backwards compatibility."""
        return self._exit_plan_calculator.calculate_volatility_regime(
            vol_ratio=self._vol_ratio or 1.0,
            atr_ratio=self._atr_ratio or 1.0,
            atr_pct=self._current_atr_pct or 0.0
        )

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

    def _calculate_sr_distances(self, historical_arrays: Dict, current_price: float) -> Dict:
        """Delegate to EntryAnalyzer (v4.0)"""
        if self._enhanced_features_enabled and hasattr(self, '_entry_analyzer'):
            return self._entry_analyzer.calculate_sr_distances(historical_arrays, current_price)
        return {
            "nearest_support": 0, "nearest_resistance": 0,
            "support_dist_pct": 0, "resistance_dist_pct": 0,
            "rr_ratio": 0, "levels": {},
        }

    # ---------------------------------------------------------------------
    # ENHANCED FEATURES SECTION (v2.0)
    # ---------------------------------------------------------------------
    # ---------------------------------------------------------------------
    # BTC CORRELATION CONTEXT (for ETH/SOL trading)
    # ---------------------------------------------------------------------
    # ---------------------------------------------------------------------
    # EXIT PLAN CALCULATOR (Python-computed, always valid)
    # ---------------------------------------------------------------------
    def calculate_exit_plan(
        self,
        signal: str,
        entry_price: float,
        historical_arrays: Dict,
    ) -> Dict:
        """Public API - delegates to ExitPlanCalculator"""
        # Calculate S/R distances first
        sr_distances = self._calculate_sr_distances(historical_arrays, entry_price)
        
        return self._exit_plan_calculator.calculate_exit_plan(
            signal=signal,
            entry_price=entry_price,
            historical_arrays=historical_arrays,
            atr_pct=self._current_atr_pct or 1.0,
            volatility=self._current_volatility or 0.5,
            consecutive_losses=self._consecutive_losses,
            recent_win_rate=self._recent_win_rate,
            performance_history_len=len(self._performance_history),
            sr_distances=sr_distances
        )

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

        # Build sections using builders
        header = self._build_header(runtime_minutes, current_time)

        # Create volatility state dict
        volatility_state = {
            "volatility": self._current_volatility or 0.5,
            "atr_pct": self._current_atr_pct or 0.0,
            "vol_ratio": self._vol_ratio or 1.0,
            "atr_ratio": self._atr_ratio or 1.0,
            "realized_vol_pct": self._current_realized_vol_pct or 0.0,
            "median_realized_vol_pct": self._median_realized_vol_pct or 0.0,
            "median_atr_pct": self._median_atr_pct or 0.0,
        }

        # Create performance state dict
        performance_state = {
            "consecutive_losses": self._consecutive_losses,
            "recent_win_rate": self._recent_win_rate,
            "performance_history": self._performance_history,
            "last_trade_side": self._last_trade_side,
        }

        # Market state
        market_state = self._market_state_builder.build(
            symbol=symbol,
            current_snapshots=current_snapshots,
            historical_arrays=historical_arrays,
            futures_data=futures_data,
            atr_value=atr_value,
            volatility_state=volatility_state,
            htf_analysis=htf_analysis
        )

        # Pre-calculated section
        pre_calculated = self._market_state_builder.build_pre_calculated_section(
            current_snapshots=current_snapshots,
            historical_arrays=historical_arrays,
            current_price=current_price
        )

        # Account info
        account_info = self._account_info_builder.build(
            portfolio_metrics=portfolio_metrics,
            symbol=symbol,
            performance_state=performance_state
        )

        # Regime for enhanced features
        regime = self._exit_plan_calculator.calculate_volatility_regime(
            vol_ratio=volatility_state["vol_ratio"],
            atr_ratio=volatility_state["atr_ratio"],
            atr_pct=volatility_state["atr_pct"]
        )

        # Enhanced features - with new data sources
        btc_prices = raw_market_data.get("btc_prices", None)  # For correlation analysis
        orderbook_data = raw_market_data.get("orderbook_data", None)  # For order book imbalance

        enhanced_features = self._enhanced_features_builder.build(
            symbol=symbol,
            current_price=current_price,
            current_snapshots=current_snapshots,
            historical_arrays=historical_arrays,
            futures_data=futures_data,
            portfolio_metrics=portfolio_metrics,
            regime=regime,
            active_glossary_contexts=self._active_glossary_contexts,
            btc_prices=btc_prices,
            orderbook_data=orderbook_data,
        )

        # Pozisyon durumunu belirle (instructions için)
        long_pos = portfolio_metrics.get("long_position", 0.0)
        short_pos = portfolio_metrics.get("short_position", 0.0)
        has_long = abs(long_pos) > 0.0001
        has_short = abs(short_pos) > 0.0001
        has_position = has_long or has_short
        position_type = "LONG" if has_long else ("SHORT" if has_short else None)

        # Instructions (keep existing)
        instructions = self._build_instructions(
            symbol=symbol,
            has_position=has_position,
            position_type=position_type,
            current_price=current_price,
            current_snapshots=current_snapshots,
            historical_arrays=historical_arrays,
            futures_data=futures_data
        )

        # Feedback
        feedback = self._account_info_builder.build_feedback_section(
            portfolio_metrics=portfolio_metrics,
            performance_state=performance_state
        )

        # Combine sections
        sections = [
            header,
            market_state,
            pre_calculated,
            account_info,
            enhanced_features,
            instructions,
            feedback
        ]

        return "\n\n".join(s for s in sections if s)

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

    def _build_header(self, runtime_minutes: int, current_time: datetime) -> str:
        # GLM özgürlüğü: Minimal header, trading mode/regime yok
        return f"""Runtime: {runtime_minutes} min | Time: {current_time} | Invocation: {self._invocation_count}

DATA ORDER: OLDEST → NEWEST"""

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


r"""
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
