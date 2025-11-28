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


# =============================================================================
# SECURITY: Pydantic model for Redis notification validation
# =============================================================================
class PositionCloseNotification(BaseModel):
    """Validated position close notification from Redis"""
    trigger_type: str
    reason: str
    timestamp: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    position_type: str
    quantity: float
    signature: str = ""  # HMAC signature (optional for backward compatibility)

    @field_validator('trigger_type')
    @classmethod
    def validate_trigger(cls, v: str) -> str:
        allowed = {'stop_loss', 'invalidation', 'take_profit', 'manual', 'trailing_stop', 'breakeven'}
        if v not in allowed:
            raise ValueError(f'Invalid trigger_type: {v}. Allowed: {allowed}')
        return v

    @field_validator('position_type')
    @classmethod
    def validate_position(cls, v: str) -> str:
        if v not in {'LONG', 'SHORT'}:
            raise ValueError(f'Invalid position_type: {v}. Must be LONG or SHORT')
        return v

    @field_validator('entry_price', 'exit_price', 'quantity')
    @classmethod
    def validate_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f'Value must be non-negative: {v}')
        return v

# =============================================================================
# Enhanced Feature Imports with Error Tracking
# =============================================================================
ENHANCED_FEATURES_AVAILABLE = False
ENHANCED_FEATURES_ERRORS: List[str] = []

try:
    from app.indicators.volume_analyzer import VolumeAnalyzer
    from app.indicators.funding_analyzer import FundingRateAnalyzer, calculate_adx, interpret_adx
    from app.indicators.liquidation_analyzer import LiquidationAnalyzer
    from app.risk_manager.time_filter import TimeBasedFilter
    from app.risk_manager.correlation_guard import CorrelationGuard
    from app.risk_manager.drawdown_manager import DrawdownManager
    from app.risk_manager.position_sizer import DynamicPositionSizer
    from app.risk_manager.partial_tp_manager import PartialTakeProfitManager
    from app.risk_manager.trailing_stop import AdvancedTrailingStop
    from app.risk_manager.breakeven_manager import BreakevenManager
    ENHANCED_FEATURES_AVAILABLE = True
except ImportError as e:
    ENHANCED_FEATURES_ERRORS.append(str(e))
    logging.getLogger(__name__).warning("Enhanced feature import failed: %s", e)

logger = logging.getLogger(__name__)

# Dynamic risk parameters - ATR-based calculations instead of hard-coded dictionaries
_MODE_PARAMS = {
    "swing": {"sl_base": 1.5, "tp_rr": 2.5, "max_lev": 7},
    "scalp": {"sl_base": 0.5, "tp_rr": 2.0, "max_lev": 15},
}
_REGIME_INTENSITY = {"low": 0.8, "medium": 1.0, "high": 1.4, "extreme": 2.0}


# =============================================================================
# CONFIGURATION DATACLASSES (P3-002, P3-003)
# =============================================================================
@dataclass(frozen=True)
class Nof1Config:
    """
    Centralized configuration for NOF1 prompt builder.
    Replaces magic numbers scattered throughout the code.
    Frozen to ensure immutability.
    """
    # Move thresholds
    min_move_pct_base: float = 0.25
    atr_multiplier: float = 0.3
    fomo_atr_multiplier: float = 2.0

    # Stop loss bounds
    sl_floor_pct: float = 0.3
    sl_cap_swing_pct: float = 15.0
    sl_cap_scalp_pct: float = 8.0

    # Take profit bounds
    tp_floor_pct: float = 0.6

    # Invalidation
    invalidation_sl_ratio: float = 0.7

    # Confidence calculations
    confidence_base: float = 0.80
    confidence_loss_boost: float = 0.10

    # Performance-based leverage caps
    consecutive_loss_lev_cap: int = 5
    low_winrate_lev_cap: int = 6

    # Caching
    cache_ttl_seconds: int = 60
    notification_max_age_minutes: int = 5


@dataclass(frozen=True)
class VolatilityState:
    """
    Immutable state container for volatility metrics.
    Groups related volatility values together.
    """
    legacy_score: float = 0.5
    atr: float = 0.0
    atr_pct: float = 0.0
    realized_vol_pct: float = 0.0
    median_realized_vol_pct: float = 0.0
    vol_ratio: float = 1.0
    atr_ratio: float = 1.0
    regime: str = "medium"


@dataclass(frozen=True)
class PerformanceState:
    """
    Immutable state container for performance tracking.
    Groups related performance metrics together.
    """
    consecutive_losses: int = 0
    last_trade_side: Optional[str] = None
    recent_win_rate: float = 0.5
    performance_history: Tuple[int, ...] = field(default_factory=tuple)


class Nof1PromptBuilder:
    """Builds NOF1.AI style prompts with full market context"""

    # =============================================================================
    # HARD RULES - GLM Elite Swing Trader Framework
    # =============================================================================
    HARD_RULES_BLOCK = """
================================================================================
DEGİŞTİRİLEMEZ KURALLAR (HARD RULES)
================================================================================

1. SL/TP TETİKLENMEDEN CLOSE VERİLMEZ
   - Stop Loss fiyatına ulaşılmadıysa → HOLD
   - Take Profit fiyatına ulaşılmadıysa → HOLD
   - "Belirsizlik" gerekçesiyle kapatma YASAK

2. MİNİMUM 4 SAAT HOLD SÜRESİ
   - Pozisyon açıldıktan sonra 4 saat geçmeden CLOSE verilemez
   - Exception: Sadece SL hit durumunda immediate close

3. TEZ GEÇERSİZLİĞİ TANIMLI OLMALI
   - CLOSE vermek için thesis invalidation level belirlenmeli
   - "MTF çelişkili" = Normal volatilite, CLOSE değil
   - 15M/30M noise CLOSE gerekçesi OLAMAZ

4. FEE BREAK-EVEN KONTROLÜ
   - |PnL| < %0.15 ise CLOSE verilmez (fee maliyeti)

5. CLOSE İÇİN exit_validation ZORUNLU
   - Geçerli değerler: SL_HIT, TP_HIT, THESIS_INVALID
   - N/A veya belirtilmezse → HOLD verilmeli

ÇIKIŞ KARARI CHECKLIST (CLOSE vermeden önce):
□ SL fiyatına ulaşıldı mı?
□ TP fiyatına ulaşıldı mı?
□ 4H candle BODY invalidation altında KAPANDI mı?
□ Hold süresi 4 saatten fazla mı?
□ |PnL| > %0.15 mi?

Tümü "Hayır" ise → CLOSE VERİLMEZ, HOLD ver
================================================================================
"""

    # Loss management multipliers for consecutive losses
    LOSS_MANAGEMENT = {
        0: {"size_mult": 1.0, "extra_confluence": 0},
        1: {"size_mult": 1.0, "extra_confluence": 0},
        2: {"size_mult": 0.75, "extra_confluence": 5},
        3: {"size_mult": 0.50, "extra_confluence": 10, "require_grade": "A+"},
        4: {"size_mult": 0.25, "extra_confluence": 15, "require_grade": "A+"},
    }

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

        # Volatility cache for performance optimization
        # Key: (symbol, data_hash), Value: (timestamp, vol_samples)
        self._vol_cache: Dict[str, Tuple[float, List[float]]] = {}
        self._vol_cache_ttl: int = 60  # seconds

        # How much raw series to show if needed
        self._series_tail = 6  # keep tiny to avoid token blowups

        # Load swing trade mode from settings
        self._settings = get_settings()  # Store settings reference for override checks
        self._swing_mode = getattr(self._settings, 'swing_trade_mode', False)
        self._swing_max_leverage = getattr(self._settings, 'swing_max_leverage', 7)

        # Primary timeframe selection based on mode
        self._primary_tf = "4h" if self._swing_mode else "30m"

        # MTF alignment weights (swing: 4H/1D heavy, scalp: equal)
        if self._swing_mode:
            self._mtf_weights = {
                "1d": 3.0,    # Most important - big picture
                "4h": 2.5,    # Primary TF
                "1h": 1.5,    # Intermediate
                "30m": 0.5,   # Low weight - noise
                "15m": 0.2,   # Very low
                "5m": 0.1,    # Almost ignore
                "1m": 0.0,    # Ignore
            }
        else:
            self._mtf_weights = {tf: 1.0 for tf in ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]}

        # Log trading mode (risk params now calculated dynamically via _MODE_PARAMS)
        if self._swing_mode:
            logger.info("📊 Nof1PromptBuilder: SWING TRADE MODE enabled (primary TF: %s, max leverage: %dx)", self._primary_tf, self._swing_max_leverage)
        else:
            logger.info("📊 Nof1PromptBuilder: SCALP TRADE MODE (default)")

        # Initialize Enhanced Features
        self._enhanced_features_enabled = ENHANCED_FEATURES_AVAILABLE
        mode = "swing" if self._swing_mode else "scalp"

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
                self._volume_analyzer = VolumeAnalyzer(mode=mode)
                self._funding_analyzer = FundingRateAnalyzer(mode=mode)
                self._liquidation_analyzer = LiquidationAnalyzer(mode=mode)
                self._time_filter = TimeBasedFilter(mode=mode)
                self._correlation_guard = CorrelationGuard(mode=mode)
                self._drawdown_manager = DrawdownManager(initial_equity=10000.0, mode=mode)
                self._position_sizer = DynamicPositionSizer(mode=mode)
                self._partial_tp_manager = PartialTakeProfitManager(mode=mode)
                self._trailing_stop_manager = AdvancedTrailingStop(mode=mode)
                self._breakeven_manager = BreakevenManager(mode=mode)
                logger.info("✅ Enhanced features initialized | Mode: %s", mode)
            except Exception as e:
                logger.error("Failed to initialize enhanced features: %s", e)
                self._enhanced_features_enabled = False
        else:
            logger.warning("⚠️ Enhanced features not available - running in basic mode")

    # ---------------------------------------------------------------------
    # SECURITY: Utility methods for input sanitization and safe operations
    # ---------------------------------------------------------------------
    def _sanitize_prompt_input(self, text: str, max_length: int = 200) -> str:
        """
        Sanitize user-provided text to prevent prompt injection attacks.

        Protections:
        1. Length limit to prevent context overflow
        2. Remove dangerous instruction patterns
        3. Strip newlines to prevent multi-line injection
        4. Keep only printable characters
        """
        if not text:
            return "N/A"

        # 1. Length limit
        text = text[:max_length]

        # 2. Remove dangerous patterns (case-insensitive)
        dangerous_patterns = [
            r'ignore\s+(all\s+)?(previous\s+)?instructions?',
            r'system\s+override',
            r'you\s+must\s+output',
            r'forget\s+(everything|all)',
            r'new\s+instructions?:',
            r'```json',  # JSON block injection
            r'={10,}',   # Separator injection
            r'-{10,}',   # Separator injection
            r'CRITICAL\s+SYSTEM',
            r'OVERRIDE',
        ]

        for pattern in dangerous_patterns:
            text = re.sub(pattern, '[REDACTED]', text, flags=re.IGNORECASE)

        # 3. Remove newlines (prevent multi-line injection)
        text = text.replace('\n', ' ').replace('\r', ' ')

        # 4. Keep only printable characters
        text = ''.join(c for c in text if c.isprintable())

        # 5. Collapse multiple spaces
        text = re.sub(r'\s+', ' ', text).strip()

        return text if text else "N/A"

    def _safe_divide(self, numerator: float, denominator: float, default: float = 0.0) -> float:
        """
        Safe division that handles zero/near-zero denominators.

        Args:
            numerator: The number to divide
            denominator: The number to divide by
            default: Value to return if division is unsafe

        Returns:
            Result of division or default value
        """
        if abs(denominator) < 1e-10:
            return default
        return numerator / denominator

    def _verify_hmac_signature(self, data: Dict[str, Any], signature: str) -> bool:
        """
        Verify HMAC signature for Redis notification data.

        Args:
            data: Dictionary of notification data (without signature)
            signature: HMAC signature to verify

        Returns:
            True if signature is valid, False otherwise
        """
        secret_key = self._settings.security.redis_hmac_secret
        if not secret_key:
            # No secret configured - skip HMAC validation (backward compatible)
            logger.debug("HMAC validation skipped - no secret configured")
            return True

        try:
            # Create canonical JSON string for signing
            payload = json.dumps(data, sort_keys=True).encode('utf-8')
            expected_sig = hmac.new(
                secret_key.encode('utf-8'),
                payload,
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(signature, expected_sig)
        except Exception as e:
            logger.error("HMAC verification error: %s", e)
            return False

    # ---------------------------------------------------------------------
    # Relative volatility helpers (asset-agnostic)
    # ---------------------------------------------------------------------
    def _compute_realized_vol_pct(self, closes: List[float], lookback: int = 50) -> float:
        """
        Realized volatility from returns over lookback bars, as percent.
        Asset-agnostic (works across BTC/ETH/SOL).
        """
        # Need at least lookback+1 closes to compute lookback returns
        if not closes or len(closes) < lookback + 1:
            return 0.0
        window = closes[-(lookback + 1):]
        rets = []
        for i in range(1, len(window)):
            p0 = window[i - 1]
            p1 = window[i]
            if p0 > 0 and p1 > 0:
                rets.append((p1 / p0) - 1.0)
        if len(rets) < 2:
            return 0.0
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / len(rets)
        return (var ** 0.5) * 100

    def _rolling_median(self, values: List[float], lookback: int = 200) -> float:
        if not values:
            return 0.0
        w = values[-lookback:] if len(values) >= lookback else values[:]
        w_sorted = sorted(w)
        n = len(w_sorted)
        mid = n // 2
        if n % 2 == 1:
            return float(w_sorted[mid])
        return float((w_sorted[mid - 1] + w_sorted[mid]) / 2)

    def _get_cached_volatility_samples(
        self, symbol: str, closes: List[float]
    ) -> List[float]:
        """
        Get volatility samples with caching for performance.
        Uses 3 strategic sample points instead of O(n) sampling.

        Args:
            symbol: Trading symbol for cache key
            closes: List of close prices

        Returns:
            List of realized volatility samples
        """
        import time as time_module

        if len(closes) < 60:
            return []

        # Create cache key from symbol and data fingerprint (first, last, length)
        data_fingerprint = f"{closes[0]:.2f}_{closes[-1]:.2f}_{len(closes)}"
        cache_key = f"{symbol}_{data_fingerprint}"

        current_time = time_module.time()

        # Check cache
        if cache_key in self._vol_cache:
            cached_time, cached_samples = self._vol_cache[cache_key]
            if current_time - cached_time < self._vol_cache_ttl:
                return cached_samples

        # Calculate using 3 strategic sample points instead of O(n)
        # Points: early (25%), middle (50%), recent (100%)
        samples = []
        data_len = len(closes)

        sample_points = [
            int(data_len * 0.25),  # Early: 25% of data
            int(data_len * 0.5),   # Middle: 50% of data
            data_len,              # Recent: all data
        ]

        for point in sample_points:
            if point >= 60:  # Need at least 60 points for valid calculation
                rv = self._compute_realized_vol_pct(closes[:point], lookback=50)
                if rv > 0:
                    samples.append(rv)

        # Cache the result
        self._vol_cache[cache_key] = (current_time, samples)

        # Cleanup old cache entries (keep cache small)
        if len(self._vol_cache) > 100:
            oldest_keys = sorted(
                self._vol_cache.keys(),
                key=lambda k: self._vol_cache[k][0]
            )[:50]
            for k in oldest_keys:
                del self._vol_cache[k]

        return samples

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

        # Relative intensity + absolute ATR% guard rails
        if rel < 0.85 and atr_pct < 1.0:
            return "low"
        if rel < 1.25 and atr_pct < 1.8:
            return "medium"
        if rel < 1.9 and atr_pct < 3.0:
            return "high"
        return "extreme"

    def _sl_distance_bounds(self, atr_pct: float, vol: float, position_side: Optional[str] = None) -> Tuple[float, float, str]:
        """ATR-based dynamic SL calculation (replaces hard-coded dictionary)"""
        if vol is None:
            vol = 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0  # Default fallback

        regime = self._vol_regime_key()
        mode = "swing" if self._swing_mode else "scalp"
        params = _MODE_PARAMS[mode]
        intensity = _REGIME_INTENSITY[regime]

        side_mult = 1.2 if position_side == "SHORT" else 1.0

        # Min SL: ATR × base × intensity × side
        min_distance_pct = atr_pct * params["sl_base"] * intensity * side_mult
        min_distance_pct = max(min_distance_pct, 0.3)  # Floor: 0.3%

        # Max SL: ATR × max_mult × intensity
        max_mult = 3.0 if self._swing_mode else 2.0
        max_cap = 15.0 if self._swing_mode else 8.0
        max_distance_pct = atr_pct * max_mult * intensity
        max_distance_pct = min(max_distance_pct, max_cap)

        # Ensure min <= max
        if min_distance_pct > max_distance_pct:
            min_distance_pct = max_distance_pct * 0.6

        return min_distance_pct, max_distance_pct, regime

    def _tp_distance_bounds(self, atr_pct: float, vol: float, sl_min: Optional[float] = None) -> Tuple[float, float, str]:
        """ATR-based dynamic TP calculation with R:R ratio from SL (replaces hard-coded dictionary)"""
        if vol is None:
            vol = 0.5
        if atr_pct is None or atr_pct <= 0:
            atr_pct = 1.0  # Default fallback

        regime = self._vol_regime_key()
        mode = "swing" if self._swing_mode else "scalp"
        params = _MODE_PARAMS[mode]
        intensity = _REGIME_INTENSITY[regime]

        # Min TP: SL × R:R ratio (ensures consistent risk/reward)
        if sl_min and sl_min > 0:
            min_tp_pct = sl_min * params["tp_rr"]
        else:
            # Fallback: ATR-based
            min_tp_pct = atr_pct * params["sl_base"] * params["tp_rr"] * intensity
        min_tp_pct = max(min_tp_pct, 0.6)  # Floor: 0.6%

        # Max TP: ATR × max_mult × intensity
        max_mult = 5.0 if self._swing_mode else 3.0
        max_cap = 30.0 if self._swing_mode else 15.0
        max_tp_pct = atr_pct * max_mult * intensity
        max_tp_pct = min(max_tp_pct, max_cap)

        # Ensure min <= max
        if min_tp_pct > max_tp_pct:
            min_tp_pct = max_tp_pct * 0.6

        return min_tp_pct, max_tp_pct, regime

    def _recommended_leverage_cap(self, vol: float, position_side: Optional[str] = None) -> Tuple[int, str]:
        """Dynamic leverage cap based on regime intensity (replaces hard-coded dictionary)"""
        regime = self._vol_regime_key()
        mode = "swing" if self._swing_mode else "scalp"
        base = _MODE_PARAMS[mode]["max_lev"]
        intensity = _REGIME_INTENSITY[regime]

        # Cap = base / intensity (higher volatility = lower leverage)
        cap = int(base / intensity)

        if position_side == "SHORT":
            cap = int(cap * 0.9)  # Slightly lower leverage for shorts

        # Performance-based reduction (REDUCED PENALTY for larger positions)
        if self._consecutive_losses >= 2:
            cap = min(cap, 5)  # Increased from 3x to 5x - less aggressive
        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            cap = min(cap, 6)  # Increased from 4x to 6x

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
        if position_side == "SHORT":
            rr += 0.2

        return rr

    # ---------------------------------------------------------------------
    # Series summarization (token saver)
    # ---------------------------------------------------------------------
    def _summarize_series(self, values: List[float], decimals: int = 2, name: str = "") -> str:
        if not values:
            return f"{name}: N/A"

        tail = values[-self._series_tail:]
        last = values[-1]
        vmin = min(values[-50:]) if len(values) >= 50 else min(values)
        vmax = max(values[-50:]) if len(values) >= 50 else max(values)
        mean = sum(values[-50:]) / (50 if len(values) >= 50 else len(values))

        if len(tail) >= 2:
            slope = (tail[-1] - tail[0]) / max(1, (len(tail) - 1))
        else:
            slope = 0.0

        if len(tail) >= 2:
            m = sum(tail) / len(tail)
            var = sum((x - m) ** 2 for x in tail) / len(tail)
            std = var ** 0.5
        else:
            std = 0.0

        return (
            f"{name}: last={last:.{decimals}f}, "
            f"min={vmin:.{decimals}f}, max={vmax:.{decimals}f}, "
            f"mean={mean:.{decimals}f}, slope={slope:.{decimals}f}/bar, std_tail={std:.{decimals}f}"
        )

    # ---------------------------------------------------------------------
    # Market structure via pivots (fractal ZigZag-lite)
    # ---------------------------------------------------------------------
    def _detect_pivots(
        self,
        highs: List[float],
        lows: List[float],
        window: int = 2,
        min_move_pct: float = 0.25
    ) -> List[Tuple[int, str, float]]:
        pivots: List[Tuple[int, str, float]] = []
        n = len(highs)
        if n < (2 * window + 1):
            return pivots

        for i in range(window, n - window):
            hi = highs[i]
            lo = lows[i]
            is_ph = all(hi > highs[j] for j in range(i - window, i + window + 1) if j != i)
            is_pl = all(lo < lows[j] for j in range(i - window, i + window + 1) if j != i)

            if is_ph:
                pivots.append((i, "H", hi))
            if is_pl:
                pivots.append((i, "L", lo))

        pivots.sort(key=lambda x: x[0])
        filtered: List[Tuple[int, str, float]] = []
        last_type = None
        last_price = None

        for idx, ptype, price in pivots:
            if last_type is None:
                filtered.append((idx, ptype, price))
                last_type = ptype
                last_price = price
                continue

            if ptype == last_type:
                if (ptype == "H" and price > last_price) or (ptype == "L" and price < last_price):
                    filtered[-1] = (idx, ptype, price)
                    last_price = price
                continue

            move_pct = abs(price - last_price) / max(1e-9, last_price) * 100
            if move_pct >= min_move_pct:
                filtered.append((idx, ptype, price))
                last_type = ptype
                last_price = price

        return filtered

    def _analyze_market_structure(self, hist_data: Dict[str, List[float]]) -> str:
        if not hist_data or "high" not in hist_data or "low" not in hist_data:
            return "UNKNOWN"

        highs = hist_data["high"]
        lows = hist_data["low"]

        atr_pct = self._current_atr_pct or 0.0
        vol = self._current_volatility or 0.5
        min_move_pct = max(0.25, atr_pct * 0.3)

        pivots = self._detect_pivots(highs, lows, window=2, min_move_pct=min_move_pct)
        if len(pivots) < 4:
            return "NEUTRAL (Insufficient pivots)"

        swing_highs = [p for p in pivots if p[1] == "H"]
        swing_lows = [p for p in pivots if p[1] == "L"]

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "NEUTRAL (Incomplete swings)"

        h1 = swing_highs[-2][2]
        h2 = swing_highs[-1][2]
        l1 = swing_lows[-2][2]
        l2 = swing_lows[-1][2]

        if h2 > h1 and l2 > l1:
            return "BULLISH (Swing HH + HL)"
        if h2 < h1 and l2 < l1:
            return "BEARISH (Swing LH + LL)"

        return "NEUTRAL (Mixed swings)"

    # ---------------------------------------------------------------------
    # PRE-CALCULATED ANALYSIS (Token Optimization)
    # ---------------------------------------------------------------------
    def _calculate_mtf_alignment(self, current_snapshots: Dict) -> Dict:
        """Calculate trend for each timeframe and weighted alignment score"""
        tf_trends = {}

        for tf in ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]:
            data = current_snapshots.get(tf, {})
            close = data.get("close", 0)
            ema20 = data.get("ema_20", 0)
            ema50 = data.get("ema_50", 0)

            if close > 0 and ema20 > 0 and ema50 > 0:
                if close > ema20 > ema50:
                    trend = "BULLISH"
                elif close < ema20 < ema50:
                    trend = "BEARISH"
                else:
                    trend = "NEUTRAL"
            else:
                trend = "N/A"

            tf_trends[tf] = trend

        # Weighted alignment score using MTF weights
        bullish_weight = 0.0
        bearish_weight = 0.0
        total_weight = 0.0

        # P1-002 FIX: Count only TFs with weight > 0 (consistent with weighting)
        bullish_count = 0
        bearish_count = 0
        total_valid = 0
        ignored_tfs = []

        for tf, trend in tf_trends.items():
            weight = self._mtf_weights.get(tf, 1.0)

            # Skip TFs with zero or negative weight
            if weight <= 0:
                ignored_tfs.append(tf)
                continue

            if trend == "N/A":
                continue

            total_weight += weight
            total_valid += 1

            if trend == "BULLISH":
                bullish_weight += weight
                bullish_count += 1
            elif trend == "BEARISH":
                bearish_weight += weight
                bearish_count += 1

        # Weighted alignment determination
        if total_weight == 0:
            alignment = "UNKNOWN"
        else:
            bullish_pct = bullish_weight / total_weight
            bearish_pct = bearish_weight / total_weight

            if bullish_pct >= 0.7:
                alignment = "STRONG_BULLISH"
            elif bullish_pct >= 0.5:
                alignment = "WEAK_BULLISH"
            elif bearish_pct >= 0.7:
                alignment = "STRONG_BEARISH"
            elif bearish_pct >= 0.5:
                alignment = "WEAK_BEARISH"
            else:
                alignment = "MIXED"

        return {
            "trends": tf_trends,
            "alignment": alignment,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "total_valid": total_valid,
            "bullish_weight": round(bullish_weight, 2),
            "bearish_weight": round(bearish_weight, 2),
            "total_weight": round(total_weight, 2),
            "ignored_tfs": ignored_tfs,  # P1-002: Debug info for excluded TFs
        }

    def _detect_market_regime_type(self, historical_arrays: Dict, current_snapshots: Dict) -> str:
        """
        Detect TRENDING vs RANGING market for dynamic prompt instructions.
        Returns: "TREND_STRONG", "TREND_WEAK", "RANGE", or "CHOPPY"
        """
        # Get MTF alignment info
        mtf = self._calculate_mtf_alignment(current_snapshots)
        alignment = mtf.get("alignment", "MIXED")

        # Get ATR ratio for volatility context
        atr_ratio = self._atr_ratio or 1.0

        # Strong trend detection
        if alignment in ["STRONG_BULLISH", "STRONG_BEARISH"]:
            return "TREND_STRONG" if atr_ratio > 0.8 else "TREND_WEAK"
        elif alignment in ["WEAK_BULLISH", "WEAK_BEARISH"]:
            return "TREND_WEAK" if atr_ratio >= 1.0 else "RANGE"
        else:
            # MIXED alignment
            return "RANGE" if atr_ratio < 0.7 else "CHOPPY"

    def _get_regime_instructions(self, regime_type: str) -> str:
        """
        Get compact trading instructions specific to market regime.
        Returns 1-2 line regime hint (~50 tokens).
        """
        if regime_type == "TREND_STRONG":
            return "📈 REJİM: GÜÇLÜ TREND - Trend yönünde işlem, pullback'lerde giriş. KARŞI YÖNDE İŞLEM AÇMA!"
        elif regime_type == "TREND_WEAK":
            return "📊 REJİM: ZAYIF TREND - Dikkatli ol, pozisyon boyutunu küçük tut, tight stop kullan."
        elif regime_type == "RANGE":
            return "📦 REJİM: YATAY PİYASA - Destek/direnç seviyelerinde işlem, ortada HOLD, breakout bekle."
        else:  # CHOPPY
            return "⚠️ REJİM: DALGALI - İşlem açmaktan KAÇIN, net trend bekle. Sadece confidence > 90% için gir."

    def _calculate_entry_quality(self, data_primary: Dict, mtf_alignment: Dict) -> Dict:
        """Calculate entry quality score based on multiple factors (uses primary TF data)"""
        score = 0
        factors = []

        # Factor 1: MTF Alignment (max 30 points)
        alignment = mtf_alignment.get("alignment", "UNKNOWN")
        if alignment in ["STRONG_BULLISH", "STRONG_BEARISH"]:
            score += 30
            factors.append("Strong MTF alignment (+30)")
        elif alignment in ["WEAK_BULLISH", "WEAK_BEARISH"]:
            score += 15
            factors.append("Weak MTF alignment (+15)")

        # Factor 2: RSI not extreme (max 20 points)
        rsi = data_primary.get("rsi_14", 50)
        if 35 <= rsi <= 65:
            score += 20
            factors.append(f"RSI healthy zone ({rsi:.0f}) (+20)")
        elif 25 <= rsi <= 75:
            score += 10
            factors.append(f"RSI moderate ({rsi:.0f}) (+10)")

        # Factor 3: Price near EMA (max 25 points)
        close = data_primary.get("close", 0)
        ema20 = data_primary.get("ema_20", 0)
        if ema20 > 0 and close > 0:
            dist = abs(close - ema20) / ema20 * 100
            if dist < 0.5:
                score += 25
                factors.append(f"Price at EMA20 ({dist:.1f}%) (+25)")
            elif dist < 1.0:
                score += 15
                factors.append(f"Price near EMA20 ({dist:.1f}%) (+15)")

        # Factor 4: Volatility regime (max 25 points)
        regime = self._vol_regime_key()
        if regime in ["low", "medium"]:
            score += 25
            factors.append(f"Good vol regime ({regime}) (+25)")
        elif regime == "high":
            score += 10
            factors.append(f"High vol regime (+10)")

        # Grade
        if score >= 80:
            grade = "A+"
        elif score >= 60:
            grade = "A"
        elif score >= 40:
            grade = "B"
        elif score >= 20:
            grade = "C"
        else:
            grade = "D"

        return {
            "score": score,
            "grade": grade,
            "factors": factors,
        }

    def _calculate_sr_distances(self, historical_arrays: Dict, current_price: float) -> Dict:
        """Calculate nearest support/resistance levels and distances"""
        if current_price <= 0:
            return {
                "nearest_support": 0,
                "nearest_resistance": 0,
                "support_dist_pct": 0,
                "resistance_dist_pct": 0,
                "rr_ratio": 0,
                "levels": {},
            }

        # 4H high/low from historical
        hist_4h = historical_arrays.get("4h", {})
        h4_highs = hist_4h.get("high", [current_price])[-10:]
        h4_lows = hist_4h.get("low", [current_price])[-10:]
        h4_high = max(h4_highs) if h4_highs else current_price
        h4_low = min(h4_lows) if h4_lows else current_price

        # 1D high/low
        hist_1d = historical_arrays.get("1d", {})
        d1_highs = hist_1d.get("high", [current_price])[-5:]
        d1_lows = hist_1d.get("low", [current_price])[-5:]
        d1_high = max(d1_highs) if d1_highs else current_price
        d1_low = min(d1_lows) if d1_lows else current_price

        # Calculate distances
        dist_h4_high = (h4_high - current_price) / current_price * 100 if h4_high > current_price else 0
        dist_h4_low = (current_price - h4_low) / current_price * 100 if h4_low < current_price else 0
        dist_d1_high = (d1_high - current_price) / current_price * 100 if d1_high > current_price else 0
        dist_d1_low = (current_price - d1_low) / current_price * 100 if d1_low < current_price else 0

        # Nearest support (below current price)
        support_dist = min(dist_h4_low, dist_d1_low) if dist_h4_low > 0 or dist_d1_low > 0 else 0
        nearest_support = h4_low if dist_h4_low <= dist_d1_low and dist_h4_low > 0 else d1_low

        # Nearest resistance (above current price)
        resistance_dist = min(dist_h4_high, dist_d1_high) if dist_h4_high > 0 or dist_d1_high > 0 else 0
        nearest_resistance = h4_high if dist_h4_high <= dist_d1_high and dist_h4_high > 0 else d1_high

        # Calculate R:R from current price
        rr_ratio = resistance_dist / support_dist if support_dist > 0 else 0

        return {
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "support_dist_pct": support_dist,
            "resistance_dist_pct": resistance_dist,
            "rr_ratio": rr_ratio,
            "levels": {
                "4h_high": h4_high,
                "4h_low": h4_low,
                "1d_high": d1_high,
                "1d_low": d1_low,
            }
        }

    def _build_pre_calculated_section(
        self,
        current_snapshots: Dict,
        historical_arrays: Dict,
        current_price: float,
    ) -> str:
        """Build pre-calculated analysis section for GLM"""
        lines = [
            "",
            "=" * 80,
            "PRE-CALCULATED ANALYSIS (Python-computed summaries)",
            "=" * 80,
            "",
        ]

        # 1. MTF Alignment
        mtf = self._calculate_mtf_alignment(current_snapshots)
        trend_symbols = {"BULLISH": "↑", "BEARISH": "↓", "NEUTRAL": "→", "N/A": "?"}

        lines.append(f"MTF ALIGNMENT: {mtf['alignment']} ({mtf['bullish_count']}/{mtf['total_valid']} bullish)")

        tf_line_1 = " | ".join(
            f"{tf}:{trend_symbols.get(mtf['trends'].get(tf, 'N/A'), '?')}"
            for tf in ["1m", "5m", "15m", "30m"]
        )
        tf_line_2 = " | ".join(
            f"{tf}:{trend_symbols.get(mtf['trends'].get(tf, 'N/A'), '?')}"
            for tf in ["1h", "4h", "1d"]
        )
        lines.append(f"  {tf_line_1}")
        lines.append(f"  {tf_line_2}")
        lines.append("")

        # 2. Entry Quality (using primary timeframe)
        data_primary = current_snapshots.get(self._primary_tf, {})
        quality = self._calculate_entry_quality(data_primary, mtf)

        lines.append(f"ENTRY QUALITY: {quality['grade']} ({quality['score']}/100)")
        for factor in quality["factors"]:
            lines.append(f"  + {factor}")
        lines.append("")

        # 3. S/R Distances
        sr = self._calculate_sr_distances(historical_arrays, current_price)

        lines.append("S/R CONTEXT:")
        lines.append(f"  Support: ${sr['nearest_support']:,.2f} (-{sr['support_dist_pct']:.2f}%)")
        lines.append(f"  Resistance: ${sr['nearest_resistance']:,.2f} (+{sr['resistance_dist_pct']:.2f}%)")
        if sr['rr_ratio'] > 0:
            lines.append(f"  R:R from current: {sr['rr_ratio']:.2f}:1")
        lines.append("")

        # 4. Regime Summary
        regime = self._vol_regime_key()
        atr_pct = self._current_atr_pct or 0.0
        vol_ratio = self._vol_ratio or 1.0

        lines.append(f"REGIME: {regime.upper()} | ATR: {atr_pct:.2f}% | Vol Ratio: {vol_ratio:.2f}x")
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
            # 1. Time Filter Analysis
            time_result = self._time_filter.analyze()
            lines.extend([
                "TIME FILTER:",
                f"  Session: {time_result.session.value.upper()}",
                f"  Quality: {time_result.quality.value.upper()}",
                f"  Can Trade: {'YES' if time_result.can_trade else 'NO'}",
            ])
            if time_result.warnings:
                for w in time_result.warnings[:2]:
                    lines.append(f"  - {w}")
            lines.append("")

            # 2. Volume Analysis (if data available)
            hist_primary = historical_arrays.get(self._primary_tf, {})
            if hist_primary.get("close") and hist_primary.get("volume"):
                closes = hist_primary["close"]
                volumes = hist_primary["volume"]
                highs = hist_primary.get("high", closes)
                lows = hist_primary.get("low", closes)

                # CVD - returns Tuple[List[float], str]
                cvd_values, cvd_trend = self._volume_analyzer.calculate_cvd(closes, highs, lows, volumes)
                if cvd_values:  # Check if CVD calculation succeeded
                    lines.extend([
                        "VOLUME ANALYSIS:",
                        f"  CVD Trend: {cvd_trend}",
                    ])

                    # VWAP if available - returns Dict[str, float]
                    vwap_result = self._volume_analyzer.calculate_vwap(highs, lows, closes, volumes)
                    if vwap_result.get("vwap") and current_price > 0:
                        vwap = vwap_result["vwap"]
                        vwap_dist = ((current_price - vwap) / vwap) * 100
                        lines.append(f"  VWAP: ${vwap:,.2f} (Price {'+' if vwap_dist >= 0 else ''}{vwap_dist:.2f}%)")

                    lines.append("")

            # 3. ADX Trend Strength
            if hist_primary.get("high") and hist_primary.get("low") and hist_primary.get("close"):
                highs = hist_primary["high"]
                lows = hist_primary["low"]
                closes = hist_primary["close"]

                adx_result = calculate_adx(highs, lows, closes, period=14)
                if adx_result:
                    adx_list = adx_result.get("adx", [])
                    plus_di_list = adx_result.get("plus_di", [])
                    minus_di_list = adx_result.get("minus_di", [])

                    # Get latest values from lists
                    if adx_list and plus_di_list and minus_di_list:
                        adx_value = adx_list[-1]
                        plus_di = plus_di_list[-1]
                        minus_di = minus_di_list[-1]

                        # Use interpret_adx (3 params) instead of get_adx_trade_filter (4 params)
                        adx_interp = interpret_adx(adx_value, plus_di, minus_di)
                        lines.extend([
                            "ADX TREND STRENGTH:",
                            f"  ADX: {adx_value:.1f} ({adx_interp.get('strength', 'N/A')})",
                            f"  +DI: {plus_di:.1f} | -DI: {minus_di:.1f}",
                            f"  Bias: {adx_interp.get('direction', 'NEUTRAL')}",
                        ])
                        if adx_interp.get("strength") == "WEAK":
                            lines.append(f"  Warning: {adx_interp.get('recommendation', 'No clear trend')}")
                        lines.append("")

            # 4. Funding Rate Analysis
            if futures_data and futures_data.get("current"):
                funding_rate = futures_data["current"].get("funding_rate", 0)
                if funding_rate != 0:
                    funding_result = self._funding_analyzer.analyze(funding_rate)
                    if funding_result.get("feature_enabled"):
                        lines.extend([
                            "FUNDING RATE:",
                            f"  Current: {funding_rate:.6f}",
                            f"  Signal: {funding_result.get('signal', 'NEUTRAL')}",
                        ])
                        warnings = funding_result.get("warnings", [])
                        for w in warnings[:1]:
                            lines.append(f"  - {w}")
                        lines.append("")

            # 5. Liquidation Analysis
            if current_price > 0 and hist_primary.get("high") and hist_primary.get("low"):
                recent_high = max(hist_primary["high"][-20:]) if len(hist_primary["high"]) >= 20 else max(hist_primary["high"])
                recent_low = min(hist_primary["low"][-20:]) if len(hist_primary["low"]) >= 20 else min(hist_primary["low"])
                oi = futures_data.get("current", {}).get("open_interest", 0) if futures_data else 0

                if oi > 0:
                    liq_result = self._liquidation_analyzer.analyze(
                        symbol, current_price, recent_high, recent_low, oi
                    )
                    if liq_result.feature_enabled:
                        lines.extend([
                            "LIQUIDATION ZONES:",
                            f"  Risk Level: {liq_result.risk_level.value.upper()}",
                        ])
                        if liq_result.nearest_long_liq:
                            lines.append(f"  Long Liq: ${liq_result.nearest_long_liq.price:,.0f} ({liq_result.nearest_long_liq.distance_pct:.1f}% below)")
                        if liq_result.nearest_short_liq:
                            lines.append(f"  Short Liq: ${liq_result.nearest_short_liq.price:,.0f} ({liq_result.nearest_short_liq.distance_pct:.1f}% above)")
                        for w in liq_result.warnings[:1]:
                            lines.append(f"  - {w}")
                        lines.append("")

            # 6. Drawdown Status
            equity = portfolio_metrics.get("equity", 10000)
            dd_status = self._drawdown_manager.get_status()
            if dd_status.get("feature_enabled"):
                can_trade = dd_status.get("can_trade", True)
                daily_dd = dd_status.get("current_daily_drawdown_pct", 0)
                weekly_dd = dd_status.get("current_weekly_drawdown_pct", 0)
                max_dd = dd_status.get("current_max_drawdown_pct", 0)

                lines.extend([
                    "DRAWDOWN STATUS:",
                    f"  Daily DD: {daily_dd:.2f}% (Limit: {dd_status.get('daily_limit_pct', 5):.0f}%)",
                    f"  Weekly DD: {weekly_dd:.2f}% (Limit: {dd_status.get('weekly_limit_pct', 10):.0f}%)",
                    f"  Max DD: {max_dd:.2f}% (Limit: {dd_status.get('max_limit_pct', 20):.0f}%)",
                    f"  Can Trade: {'YES' if can_trade else 'NO - DRAWDOWN LIMIT REACHED'}",
                ])
                if not can_trade:
                    lines.append("  ⚠️ TRADING PAUSED DUE TO DRAWDOWN LIMIT")
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

        except Exception as e:
            logger.warning("Error building enhanced features section: %s", e)
            lines.append(f"[Enhanced features error: {str(e)[:50]}]")
            lines.append("")

        return "\n".join(lines)

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

        logger.info(
            "📐 Exit Plan Calculated: signal=%s entry=%.2f SL=%.2f (%.2f%%) TP=%.2f (%.2f%%) R:R=%.2f lev=%dx",
            signal, entry_price, stop_loss, sl_dist, profit_target, tp_dist, final_rr, leverage
        )

        return {
            "stop_loss": round(stop_loss, 2),
            "profit_target": round(profit_target, 2),
            "invalidation_condition": invalidation,
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

        runtime_minutes = int((datetime.utcnow() - self._start_time).total_seconds() / 60)
        current_time = datetime.utcnow()

        symbol = raw_market_data.get("symbol", "BTCUSDT")
        current_snapshots = raw_market_data.get("current_snapshots", {})
        historical_arrays = raw_market_data.get("historical_arrays", {})
        futures_data = raw_market_data.get("futures_data", {})

        # BTC Correlation Context for ETH/SOL trades
        btc_context = None
        if symbol in ["ETHUSDT", "SOLUSDT"]:
            btc_context = self._fetch_btc_correlation_context()

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
            realized_vol_pct = self._compute_realized_vol_pct(closes, lookback=50)

            abs_ret_pct_series = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    abs_ret_pct_series.append(abs(closes[i] - closes[i - 1]) / closes[i - 1] * 100)

            # Sample realized vol history using cached strategic sampling (O(1) vs O(n))
            realized_samples = self._get_cached_volatility_samples(symbol, closes)

            median_realized = self._rolling_median(realized_samples, lookback=20)
            median_atr_pct = self._rolling_median(abs_ret_pct_series, lookback=200)

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

        # Dynamic Regime Detection
        regime_type = self._detect_market_regime_type(historical_arrays, current_snapshots)
        regime_instructions = self._get_regime_instructions(regime_type)

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

        # Feedback Loop - Son 5 işlem bilgisi
        feedback_section = self._build_feedback_section(portfolio_metrics)
        if feedback_section:
            sections.append(feedback_section)

        # Instructions with CoT, regime info and BTC context
        sections.append(self._build_instructions(
            symbol=symbol,
            atr_value=atr_value,
            current_price=current_price,
            regime_instructions=regime_instructions,
            btc_context=btc_context
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

        # Warning if losing streak
        if streak >= 2 and streak_type == "L":
            lines.append("⚠️ UYARI: Ardışık kayıp serisi - daha seçici ol!")

        # Show last trade direction for bias consideration
        if trades:
            last_side = trades[0].get("side", "")
            last_pnl = trades[0].get("pnl_pct", 0)
            if last_side:
                lines.append(f"Son işlem: {last_side} ({last_pnl:+.1f}%)")

        lines.append("")

        return "\n".join(lines)

    def _build_header(self, runtime_minutes: int, current_time: datetime) -> str:
        mode_label = "SWING" if self._swing_mode else "SCALP"
        tf_label = "4-hour" if self._swing_mode else "30-minute"

        header = f"""It has been {runtime_minutes} minutes since you started trading. The current time is {current_time} and you've been invoked {self._invocation_count} times. Below, we are providing you with a variety of state data, price data, and predictive signals so you can discover alpha. Below that is your current account information, value, performance, positions, etc.

🎯 TRADING MODE: {mode_label} (Primary TF: {self._primary_tf})

ALL OF THE PRICE OR SIGNAL DATA BELOW IS ORDERED: OLDEST → NEWEST

Timeframes note: Unless stated otherwise in a section title, the primary timeframe is {tf_label} intervals. Additional timeframes are provided for comprehensive analysis."""

        if self._consecutive_losses >= 2:
            header += f"""

⚠️⚠️⚠️ PERFORMANCE ALERT: You have {self._consecutive_losses} consecutive losses.
- Increase selectivity.
- Reduce position size.
- Prefer HOLD unless setup is A+.
- Consider bias flip if losses were same-direction. ⚠️⚠️⚠️"""

        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            header += f"""

🟡 WIN-RATE WARNING: Recent win rate is {self._recent_win_rate:.0%} over last {len(self._performance_history)} trades.
Be conservative; wait for clearer alignment."""

        return header

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

        if regime_key == "low":
            market_regime = "Low Volatility (Stable)"
        elif regime_key == "medium":
            market_regime = "Medium Volatility (Normal)"
        elif regime_key == "high":
            market_regime = "High Volatility (Active)"
        else:
            market_regime = "Extreme Volatility (Dangerous)"

        lines.extend([
            "",
            "RISK & VOLATILITY REGIME:",
            f"• Volatility Score (legacy): {volatility_score:.2f} (0.0-1.0 scale)",
            f"• Market Regime (relative): {market_regime}",
            f"• ATR (14-period): {atr_value:.2f} (~{atr_pct:.2f}% expected move)",
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

            tf_label = "4-hour" if self._swing_mode else "30-minute"
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
                    "NO-TRADE / FOMO ZONES (numeric):",
                    f"• LONG FOMO band ≈ EMA20 + 2*ATR = {fomo_long_band:.2f}",
                    f"• SHORT FOMO band ≈ EMA20 - 2*ATR = {fomo_short_band:.2f}",
                    "Rule: If price is beyond these bands + RSI extreme, avoid chasing.",
                    "",
                ])

            lines.extend([
                f"SERIES SUMMARIES ({primary_tf} tail stats):",
                self._summarize_series(hist_primary.get("close", []), 2, "Close"),
                self._summarize_series(hist_primary.get("ema_20", []), 2, "EMA20"),
                self._summarize_series(hist_primary.get("ema_50", []), 2, "EMA50"),
                self._summarize_series(hist_primary.get("macd", []), 2, "MACD"),
                self._summarize_series(hist_primary.get("rsi_14", []), 2, "RSI14"),
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

            fr = cur_f.get("funding_rate", 0)
            oi = cur_f.get("open_interest", 0)
            lsr = cur_f.get("long_short_ratio", 0)

            lines.extend([
                f"Funding Rate: {fr:.8f}" if fr else "Funding Rate: N/A",
                f"  (8h avg: {avg_f.get('funding_rate_avg', 0):.8f})",
                "",
                f"Open Interest: {oi:.2f}" if oi else "Open Interest: N/A",
                f"  (20p avg: {avg_f.get('open_interest_avg', 0):.2f})",
                "",
                f"Long/Short Ratio: {lsr:.4f}" if lsr else "Long/Short Ratio: N/A",
                f"  (20p avg: {avg_f.get('long_short_ratio_avg', 0):.4f})",
                "",
            ])

        # Intraday 1m summary
        hist_1m = historical_arrays.get("1m", {})
        if hist_1m and "close" in hist_1m:
            lines.extend([
                "",
                "=" * 80,
                "INTRADAY (1-minute) SUMMARY:",
                self._summarize_series(hist_1m.get("close", []), 2, "1m Close"),
                self._summarize_series(hist_1m.get("rsi_14", []), 2, "1m RSI14"),
                self._summarize_series(hist_1m.get("macd", []), 2, "1m MACD"),
                "",
            ])

        # 4H context
        data_4h = current_snapshots.get("4h", {})
        if data_4h:
            lines.extend([
                "",
                "=" * 80,
                "LONGER-TERM CONTEXT (4H):",
                f"4H Close: {data_4h.get('close', 0):.2f}",
                f"4H EMA20: {data_4h.get('ema_20', 0):.2f}",
                f"4H EMA50: {data_4h.get('ema_50', 0):.2f}",
                f"4H ATR14: {data_4h.get('atr_14', 0):.2f}",
                "",
            ])

        lines.extend([
            "=" * 80,
            "ADDITIONAL TIMEFRAMES SNAPSHOT:",
        ])
        for tf in ["5m", "15m", "1h", "1d"]:
            d = current_snapshots.get(tf, {})
            if d:
                lines.append(
                    f"{tf.upper()} | Close: {d.get('close',0):.2f} | EMA20: {d.get('ema_20',0):.2f} | EMA50: {d.get('ema_50',0):.2f} | RSI14: {d.get('rsi_14',50):.2f}"
                )
            else:
                lines.append(f"{tf.upper()}: No data available")

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
            f"Recent Win Rate (last {len(self._performance_history)} trades): {self._recent_win_rate:.0%}",
            "",
        ])

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

    def _build_instructions(
        self,
        symbol: str = "BTCUSDT",
        atr_value: float = 0.0,
        current_price: float = 0.0,
        regime_instructions: str = "",
        btc_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Instructions with integrated Chain of Thought - GLM thinks step-by-step BEFORE outputting JSON.
        Now includes Capital Preservation persona and dynamic regime/BTC context.
        """

        # Dynamic confidence threshold based on performance
        confidence_threshold = 0.80
        if self._consecutive_losses >= 2:
            confidence_threshold = 0.90
        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            confidence_threshold = max(confidence_threshold, 0.88)

        vol = self._current_volatility or 0.5
        regime_key = self._vol_regime_key()

        # Build warnings section
        warnings = []
        if self._consecutive_losses >= 2:
            warnings.append(f"⚠️ UYARI: {self._consecutive_losses} ardışık kayıp - daha seçici ol!")

        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            warnings.append(f"🟡 Win rate düşük ({self._recent_win_rate:.0%}) - konservatif ol!")

        warnings_block = "\n".join(warnings) + "\n" if warnings else ""

        # Build BTC correlation block for ETH/SOL (with multi-TF consensus)
        btc_block = ""
        if btc_context and symbol != "BTCUSDT":
            btc_trend = btc_context.get("trend", "NEUTRAL")
            btc_rsi = btc_context.get("rsi", 50)
            consensus = btc_context.get("consensus_trend", btc_trend)
            has_conflict = btc_context.get("has_conflict", False)
            agreement_pct = btc_context.get("agreement_pct", 0)
            tf_trends = btc_context.get("tf_trends", {})

            # Build TF summary string (compact)
            tf_summary = "/".join(f"{tf}:{t[0]}" for tf, t in tf_trends.items()) if tf_trends else ""

            if has_conflict:
                # Mixed signals across timeframes - warn about increased risk
                btc_block = (
                    f"\n⚠️ BTC KORELASYON: KARISIK SİNYALLER!\n"
                    f"   TF Trendleri: {tf_summary}\n"
                    f"   RSI: {btc_rsi:.0f} | Konsensüs yok - DİKKATLİ OL!\n"
                )
            elif consensus == "BEAR":
                btc_block = (
                    f"\n📊 BTC KORELASYON: {consensus} (RSI: {btc_rsi:.0f})\n"
                    f"   TF Uyumu: %{agreement_pct:.0f} | BTC bearish iken LONG riskli!\n"
                )
            elif consensus == "BULL":
                btc_block = (
                    f"\n📊 BTC KORELASYON: {consensus} (RSI: {btc_rsi:.0f})\n"
                    f"   TF Uyumu: %{agreement_pct:.0f} | BTC bullish - trend uyumlu\n"
                )
            else:
                btc_block = f"\n📊 BTC KORELASYON: {consensus} (RSI: {btc_rsi:.0f}) - Nötr\n"

        # Add regime instructions if provided
        regime_block = f"\n{regime_instructions}\n" if regime_instructions else ""

        # Get loss management settings
        loss_config = self.LOSS_MANAGEMENT.get(min(self._consecutive_losses, 4), self.LOSS_MANAGEMENT[0])
        size_mult = loss_config.get("size_mult", 1.0)
        extra_confluence = loss_config.get("extra_confluence", 0)
        required_grade = loss_config.get("require_grade", None)

        loss_warning = ""
        if self._consecutive_losses >= 2:
            loss_warning = f"""
⚠️ KAYIP SERİSİ UYARISI: {self._consecutive_losses} ardışık kayıp
   • Position size çarpanı: {size_mult:.0%}
   • Ekstra confluence gereksinimi: +{extra_confluence} puan
   {"• Sadece " + required_grade + " grade setup'lar!" if required_grade else ""}
"""

        return f"""
{self.HARD_RULES_BLOCK}
================================================================================
KARAR SÜRECİ (ÖNCE DÜŞÜN, SONRA JSON)
================================================================================
{warnings_block}{regime_block}{btc_block}{loss_warning}
ROLE: Disciplined Swing Trader
PRIME DIRECTIVE: Sermayeyi koru > Kar ara. Şüphe durumunda HOLD.

--------------------------------------------------------------------------------
ANALİZ ADIMLARI (SESLİ DÜŞÜN)
--------------------------------------------------------------------------------
### ADIM 1: POZİSYON KONTROLÜ (AÇIK POZİSYON VARSA)
- SL tetiklendi mi? → Hayır → HOLD
- TP tetiklendi mi? → Hayır → HOLD
- Thesis invalidation (4H close)? → Değilse → HOLD
- Hold süresi < 4 saat? → HOLD (cooldown)
- PnL < %0.15? → HOLD (fee break-even)
⚠️ "Çelişkili sinyal" veya "belirsizlik" = HOLD, CLOSE değil

### ADIM 2: FOMO KONTROLÜ
- LONG: Fiyat > EMA20 + 2*ATR VE RSI > 70 → ALIM YAPMA
- SHORT: Fiyat < EMA20 - 2*ATR VE RSI < 30 → SATIŞ YAPMA

### ADIM 3: MTF UYUMU
- PRE-CALCULATED MTF ALIGNMENT bölümüne bak
- 4H ve 30M uyumlu olmalı
- KARŞI trend'de işlem AÇMA

### ADIM 4: GİRİŞ KALİTESİ
- PRE-CALCULATED ENTRY QUALITY bölümündeki nota bak
- A+ veya A tercih edilir, B ve altı → confidence -10%

### ADIM 5: REJİM
- Mevcut rejim: {regime_key.upper()}
- Güven eşiği: {confidence_threshold:.0%}

--------------------------------------------------------------------------------
KARAR SEÇENEKLERİ
--------------------------------------------------------------------------------
1) HOLD (varsayılan) → confidence < {confidence_threshold:.0%} veya net setup yok
2) BUY → bullish setup, confluence ≥ 80, ADX > 25, FOMO yok
3) SELL → bearish setup, confluence ≥ 80, ADX > 25, FOMO yok
4) CLOSE → SADECE: SL hit, TP hit, veya 4H thesis invalidation
   ⚠️ CLOSE için exit_validation ZORUNLU!

--------------------------------------------------------------------------------
ÇIKTI FORMATI (DÜŞÜNCE + JSON)
--------------------------------------------------------------------------------
Önce 2-3 cümle DÜŞÜNCE sürecini yaz, sonra JSON bloğunu ver.

Örnek format (YENİ POZİSYON):

DÜŞÜNCE:
FOMO kontrolü OK. MTF 5/7 bullish. Entry kalitesi A. BTC uyumlu.
RSI 48 sağlıklı bölgede. Pullback tamamlanmış, giriş uygun.

```json
{{
  "{symbol}": {{
    "signal": "BUY",
    "confidence": 85,
    "reasoning": "MTF bullish uyumu, EMA20 desteği, RSI sağlıklı",
    "exit_validation": "N/A"
  }}
}}
```

Örnek format (POZİSYON KAPATMA):

DÜŞÜNCE:
SL seviyesine ulaşıldı. Risk yönetimi gereği pozisyon kapatılmalı.

```json
{{
  "{symbol}": {{
    "signal": "CLOSE",
    "confidence": 95,
    "reasoning": "Stop loss tetiklendi, risk yönetimi",
    "exit_validation": "SL_HIT"
  }}
}}
```

**ÖNEMLİ:**
- "reasoning" alanı HER ZAMAN TÜRKÇE yazılmalıdır
- "exit_validation" alanı ZORUNLU: SL_HIT | TP_HIT | THESIS_INVALID | N/A
- CLOSE için exit_validation "N/A" ise → HOLD ver!

**KRİTİK:** Emin değilsen → HOLD. Kötü bir trade açmak, fırsat kaçırmaktan daha kötü.
"""

    def _format_array(self, values: List[float], decimals: int = 2) -> str:
        if not values:
            return "[]"
        return "[" + ", ".join(f"{v:.{decimals}f}" for v in values) + "]"

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
