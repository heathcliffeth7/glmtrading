"""
Entry Analyzer - Entry signal quality assessment and confirmation.

Extracted from nof1_prompt_builder.py for better code organization.
Plus new win rate improvement features:
- Pullback Confirmation (EMA bounce detection)
- Candle Pattern Recognition (Engulfing, Pin Bar, Inside Bar)
- Volume Confirmation (volume/price divergence)
- MTF Confluence Scoring (enhanced weighting)
- Overextension Filter (Bollinger Band position check)

Version: 4.0
"""

from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class CandlePattern(Enum):
    """Candle pattern types for entry confirmation"""
    NONE = "none"
    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"
    BULLISH_PIN_BAR = "bullish_pin_bar"
    BEARISH_PIN_BAR = "bearish_pin_bar"
    INSIDE_BAR_BULL = "inside_bar_bull"
    INSIDE_BAR_BEAR = "inside_bar_bear"


@dataclass
class EntryConfirmation:
    """Complete entry confirmation result with all factors"""
    score: int  # 0-125
    grade: str  # A+, A, B, C, D
    pullback_confirmed: bool
    pullback_type: str
    pullback_bonus: int
    candle_pattern: CandlePattern
    candle_bonus: int
    volume_confirmed: bool
    volume_reason: str
    volume_bonus: int
    mtf_confluence: float  # 0.0-1.0
    mtf_bonus: int
    is_overextended: bool
    overext_reason: str
    overext_penalty: int
    factors: List[str]
    warnings: List[str]
    can_trade: bool


class EntryAnalyzer:
    """
    Entry signal quality assessment and confirmation system.

    Combines market structure analysis with new win rate features
    for comprehensive entry quality scoring.
    """

    def __init__(self, mode: str = "scalp"):
        """
        Initialize EntryAnalyzer.

        Args:
            mode: Trading mode - "scalp" or "swing"
        """
        self.mode = mode

        # Mode-specific parameters
        self._ema_tolerance = 0.3 if mode == "scalp" else 0.5
        self._bb_threshold = 0.90  # 90% toward band = overextended
        self._min_volume_ratio = 1.3  # 130% of avg volume for confirmation

        # MTF weights for confluence scoring
        self._mtf_weights = {
            "1d": 4.0 if mode == "swing" else 2.0,
            "4h": 3.0 if mode == "swing" else 2.0,
            "1h": 1.5,
            "30m": 1.0 if mode == "scalp" else 0.0,
            "15m": 1.0 if mode == "scalp" else 0.0,
            "5m": 0.5 if mode == "scalp" else 0.0,
            "1m": 0.0,
        }

        logger.info("EntryAnalyzer initialized | Mode: %s", mode)

    # =========================================================================
    # EXTRACTED FROM nof1_prompt_builder.py
    # =========================================================================

    def detect_pivots(
        self,
        highs: List[float],
        lows: List[float],
        window: int = 2,
        min_move_pct: float = 0.25
    ) -> List[Tuple[int, str, float]]:
        """
        Detect swing high/low pivots using fractal ZigZag-lite algorithm.
        Extracted from nof1_prompt_builder._detect_pivots
        """
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

    def analyze_market_structure(
        self,
        hist_data: Dict[str, List[float]],
        atr_pct: float = 0.0,
        volatility: float = 0.5
    ) -> str:
        """
        Analyze market structure via pivot detection.
        Extracted from nof1_prompt_builder._analyze_market_structure

        Returns one of:
        - "BULLISH (Swing HH + HL)"
        - "BEARISH (Swing LH + LL)"
        - "NEUTRAL (Mixed swings)"
        - "NEUTRAL (Insufficient pivots)"
        - "UNKNOWN"
        """
        if not hist_data or "high" not in hist_data or "low" not in hist_data:
            return "UNKNOWN"

        highs = hist_data["high"]
        lows = hist_data["low"]

        min_move_pct = max(0.25, atr_pct * 0.3)

        pivots = self.detect_pivots(highs, lows, window=2, min_move_pct=min_move_pct)
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

    def check_liquidity_sweep(
        self,
        close: float,
        low: float,
        high: float,
        prev_swing_low: float,
        prev_swing_high: float,
        alignment: str
    ) -> bool:
        """
        Detect liquidity sweep (fake-out) pattern.
        Extracted from nof1_prompt_builder._check_liquidity_sweep
        """
        # BULLISH + fiyat prev_low'u sweep edip döndüyse
        if "BULLISH" in alignment and low < prev_swing_low and close > prev_swing_low:
            return True
        # BEARISH + fiyat prev_high'ı sweep edip döndüyse
        if "BEARISH" in alignment and high > prev_swing_high and close < prev_swing_high:
            return True
        return False

    def calculate_sr_distances(
        self,
        historical_arrays: Dict[str, Dict[str, List[float]]],
        current_price: float
    ) -> Dict[str, Any]:
        """
        Calculate nearest support/resistance levels and distances.
        Extracted from nof1_prompt_builder._calculate_sr_distances
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

    # =========================================================================
    # NEW WIN RATE FEATURES
    # =========================================================================

    def check_pullback_confirmation(
        self,
        close: float,
        prev_close: float,
        ema20: float,
        ema50: float,
        signal: str  # "BUY" or "SELL"
    ) -> Tuple[bool, str, int]:
        """
        Feature 1: Pullback Confirmation (EMA Bounce Detection)

        Confirms entry by checking if price has pulled back to EMA and bounced.
        """
        if ema20 <= 0 or ema50 <= 0 or close <= 0:
            return False, "", 0

        dist_ema20 = abs(close - ema20) / ema20 * 100
        dist_ema50 = abs(close - ema50) / ema50 * 100
        tolerance = self._ema_tolerance

        if signal == "BUY":
            # Fiyat EMA20'ye yakın ve bounce yapıyor
            if dist_ema20 < tolerance and close > prev_close and close >= ema20:
                return True, "EMA20 bounce", 15
            # Derin pullback - EMA50'den bounce
            if dist_ema50 < tolerance * 1.5 and close > prev_close and close >= ema50:
                return True, "EMA50 deep pullback", 20
            # Fiyat EMA'ların üzerinde ama çok uzaklaşmamış (sağlıklı trend)
            if close > ema20 > ema50 and dist_ema20 < 1.0:
                return True, "Healthy uptrend", 10

        elif signal == "SELL":
            # Fiyat EMA20'ye yakın ve rejection yapıyor
            if dist_ema20 < tolerance and close < prev_close and close <= ema20:
                return True, "EMA20 rejection", 15
            # Derin bounce - EMA50'den rejection
            if dist_ema50 < tolerance * 1.5 and close < prev_close and close <= ema50:
                return True, "EMA50 deep rejection", 20
            # Fiyat EMA'ların altında ama çok uzaklaşmamış
            if close < ema20 < ema50 and dist_ema20 < 1.0:
                return True, "Healthy downtrend", 10

        return False, "", 0

    def detect_candle_pattern(
        self,
        opens: List[float],
        highs: List[float],
        lows: List[float],
        closes: List[float],
        signal: str  # "BUY" or "SELL"
    ) -> Tuple[CandlePattern, int]:
        """
        Feature 2: Candle Pattern Recognition

        Detects confirmation candle patterns:
        - Engulfing (bullish/bearish)
        - Pin Bar (hammer/shooting star)
        - Inside Bar breakout
        """
        # Check all lists have at least 3 elements
        if len(closes) < 3 or len(opens) < 3 or len(highs) < 3 or len(lows) < 3:
            return CandlePattern.NONE, 0

        # Son 3 mum
        o1, o2, o3 = opens[-3], opens[-2], opens[-1]
        h1, h2, h3 = highs[-3], highs[-2], highs[-1]
        l1, l2, l3 = lows[-3], lows[-2], lows[-1]
        c1, c2, c3 = closes[-3], closes[-2], closes[-1]

        body3 = abs(c3 - o3)
        body2 = abs(c2 - o2)
        range3 = h3 - l3 if h3 > l3 else 0.0001

        upper_wick3 = h3 - max(c3, o3)
        lower_wick3 = min(c3, o3) - l3

        # Bullish Engulfing
        if signal == "BUY":
            if c2 < o2 and c3 > o3 and c3 > o2 and o3 < c2 and body3 > body2 * 1.2:
                return CandlePattern.BULLISH_ENGULFING, 18

            # Bullish Pin Bar (uzun alt fitil)
            if body3 > 0 and lower_wick3 > body3 * 2 and lower_wick3 > upper_wick3 * 2:
                return CandlePattern.BULLISH_PIN_BAR, 15

            # Inside Bar Bullish Breakout
            if h2 < h1 and l2 > l1 and c3 > h2:
                return CandlePattern.INSIDE_BAR_BULL, 12

        # Bearish patterns
        elif signal == "SELL":
            # Bearish Engulfing
            if c2 > o2 and c3 < o3 and c3 < o2 and o3 > c2 and body3 > body2 * 1.2:
                return CandlePattern.BEARISH_ENGULFING, 18

            # Bearish Pin Bar (uzun üst fitil)
            if body3 > 0 and upper_wick3 > body3 * 2 and upper_wick3 > lower_wick3 * 2:
                return CandlePattern.BEARISH_PIN_BAR, 15

            # Inside Bar Bearish Breakout
            if h2 < h1 and l2 > l1 and c3 < l2:
                return CandlePattern.INSIDE_BAR_BEAR, 12

        return CandlePattern.NONE, 0

    def check_volume_confirmation(
        self,
        closes: List[float],
        volumes: List[float],
        signal: str  # "BUY" or "SELL"
    ) -> Tuple[bool, str, int]:
        """
        Feature 3: Volume Confirmation

        Checks for volume/price agreement or divergence.
        """
        if len(volumes) < 20 or len(closes) < 5:
            return False, "Insufficient data", 0

        avg_vol = sum(volumes[-20:]) / 20
        recent_vol = sum(volumes[-3:]) / 3
        last_vol = volumes[-1]

        price_up = closes[-1] > closes[-3]
        vol_spike = last_vol > avg_vol * self._min_volume_ratio
        vol_expanding = recent_vol > avg_vol * 1.1
        vol_contracting = recent_vol < avg_vol * 0.7

        if signal == "BUY":
            # Yükselen fiyat + artan hacim = GÜÇLÜ
            if price_up and vol_spike:
                return True, "Strong volume on up move", 15
            if price_up and vol_expanding:
                return True, "Expanding volume on uptrend", 10
            # Düşen fiyat + düşen hacim = Sağlıklı pullback
            if not price_up and vol_contracting:
                return True, "Healthy pullback (low vol)", 8
            # Yükselen fiyat + düşen hacim = Zayıf (distribution?)
            if price_up and vol_contracting:
                return False, "WARNING: Low volume rally", -5

        elif signal == "SELL":
            # Düşen fiyat + artan hacim = GÜÇLÜ
            if not price_up and vol_spike:
                return True, "Strong volume on down move", 15
            if not price_up and vol_expanding:
                return True, "Expanding volume on downtrend", 10
            # Yükselen fiyat + düşen hacim = Zayıf bounce
            if price_up and vol_contracting:
                return True, "Weak bounce (low vol)", 8
            # Düşen fiyat + düşen hacim = Possible accumulation
            if not price_up and vol_contracting:
                return False, "WARNING: Low volume drop", -5

        return False, "", 0

    def calculate_mtf_confluence(
        self,
        tf_trends: Dict[str, str],
        signal: str  # "BUY" or "SELL"
    ) -> Tuple[float, int, List[str]]:
        """
        Feature 4: MTF Confluence Scoring (Enhanced)

        Enhanced scoring with weighted timeframes.
        """
        bullish_weight = 0.0
        bearish_weight = 0.0
        total_weight = 0.0
        details = []

        for tf, trend in tf_trends.items():
            weight = self._mtf_weights.get(tf, 0.0)
            if weight <= 0 or trend == "N/A":
                continue

            total_weight += weight

            if trend == "BULLISH":
                bullish_weight += weight
            elif trend == "BEARISH":
                bearish_weight += weight

            details.append(f"{tf}:{trend[:1]}")

        if total_weight == 0:
            return 0.0, 0, []

        # Confluence yüzdesi
        if signal == "BUY":
            confluence = bullish_weight / total_weight
        else:
            confluence = bearish_weight / total_weight

        # Puan hesapla
        if confluence >= 0.80:
            bonus = 25
        elif confluence >= 0.70:
            bonus = 20
        elif confluence >= 0.55:
            bonus = 10
        elif confluence >= 0.40:
            bonus = 0
        else:
            bonus = -10  # Counter-trend penalty

        return confluence, bonus, details

    def check_overextension(
        self,
        close: float,
        bb_upper: float,
        bb_lower: float,
        ema20: float,
        signal: str  # "BUY" or "SELL"
    ) -> Tuple[bool, str, int]:
        """
        Feature 5: Overextension Filter (Bollinger Band Position)

        Blocks entries when price is overextended.
        """
        if bb_upper <= bb_lower or close <= 0:
            return False, "", 0

        bb_range = bb_upper - bb_lower
        bb_position = (close - bb_lower) / bb_range  # 0-1 arası

        # EMA20'den uzaklık kontrolü
        ema_dist_pct = abs(close - ema20) / ema20 * 100 if ema20 > 0 else 0

        if signal == "BUY":
            # Çok yukarıda = FOMO zone
            if bb_position > self._bb_threshold:
                return True, f"Overextended (BB {bb_position*100:.0f}%)", -20
            if ema_dist_pct > 3.0 and close > ema20:
                return True, f"Far from EMA20 ({ema_dist_pct:.1f}%)", -15

        elif signal == "SELL":
            # Çok aşağıda = Panic zone
            if bb_position < (1 - self._bb_threshold):
                return True, f"Oversold extreme (BB {bb_position*100:.0f}%)", -20
            if ema_dist_pct > 3.0 and close < ema20:
                return True, f"Far from EMA20 ({ema_dist_pct:.1f}%)", -15

        # Optimal zone bonus
        if 0.35 <= bb_position <= 0.65:
            return False, "Optimal zone", 5

        return False, "", 0

    # =========================================================================
    # COMPOSITE ENTRY QUALITY
    # =========================================================================

    def get_entry_confirmation(
        self,
        signal: str,
        data_primary: Dict[str, Any],
        hist_primary: Dict[str, List[float]],
        tf_trends: Dict[str, str],
        vol_regime: str = "medium",
        liquidity_sweep: bool = False
    ) -> EntryConfirmation:
        """
        Composite entry quality assessment combining all factors.

        This is the main method to call for entry confirmation.
        """
        score = 0
        factors = []
        warnings = []

        # Extract data with safe defaults
        close = data_primary.get("close", 0)
        ema20 = data_primary.get("ema_20", 0)
        ema50 = data_primary.get("ema_50", 0)
        bb_upper = data_primary.get("bb_upper", 0)
        bb_lower = data_primary.get("bb_lower", 0)
        rsi = data_primary.get("rsi_14", 50)

        # Safe extraction of historical arrays
        opens = hist_primary.get("open", []) if hist_primary else []
        highs = hist_primary.get("high", []) if hist_primary else []
        lows = hist_primary.get("low", []) if hist_primary else []
        closes = hist_primary.get("close", []) if hist_primary else []
        volumes = hist_primary.get("volume", []) if hist_primary else []

        # Safe prev_close extraction
        prev_close = closes[-2] if len(closes) >= 2 else close

        # 1. MTF Confluence (max 25)
        confluence, mtf_bonus, mtf_details = self.calculate_mtf_confluence(tf_trends, signal)
        score += mtf_bonus
        if mtf_bonus > 0:
            factors.append(f"MTF confluence {confluence*100:.0f}% (+{mtf_bonus})")
        elif mtf_bonus < 0:
            warnings.append(f"Weak MTF confluence {confluence*100:.0f}%")

        # 2. Pullback Confirmation (max 20)
        pullback_ok, pullback_type, pullback_bonus = self.check_pullback_confirmation(
            close, prev_close, ema20, ema50, signal
        )
        score += pullback_bonus
        if pullback_ok:
            factors.append(f"{pullback_type} (+{pullback_bonus})")
        else:
            warnings.append("No pullback confirmation")

        # 3. Candle Pattern (max 18)
        # Safety check: ensure all lists have at least 3 elements
        if not opens or not highs or not lows or not closes or len(closes) < 3:
            candle_pattern, candle_bonus = CandlePattern.NONE, 0
        else:
            candle_pattern, candle_bonus = self.detect_candle_pattern(opens, highs, lows, closes, signal)
        score += candle_bonus
        if candle_bonus > 0:
            factors.append(f"{candle_pattern.value} (+{candle_bonus})")

        # 4. Volume Confirmation (max 15)
        vol_ok, vol_reason, vol_bonus = self.check_volume_confirmation(closes, volumes, signal)
        score += vol_bonus
        if vol_bonus > 0:
            factors.append(f"Volume: {vol_reason} (+{vol_bonus})")
        elif vol_bonus < 0:
            warnings.append(vol_reason)

        # 5. Overextension Check (penalty up to -20)
        is_overext, overext_reason, overext_penalty = self.check_overextension(
            close, bb_upper, bb_lower, ema20, signal
        )
        score += overext_penalty
        if is_overext:
            warnings.append(f"OVEREXTENDED: {overext_reason}")
        elif overext_penalty > 0:
            factors.append(f"Zone: {overext_reason} (+{overext_penalty})")

        # 6. RSI Check (max 20)
        if 40 <= rsi <= 60:
            score += 20
            factors.append(f"RSI optimal ({rsi:.0f}) (+20)")
        elif 30 <= rsi <= 70:
            score += 10
            factors.append(f"RSI acceptable ({rsi:.0f}) (+10)")
        else:
            warnings.append(f"RSI extreme ({rsi:.0f})")

        # 7. Volatility Regime (max 15)
        if vol_regime in ["low", "medium"]:
            score += 15
            factors.append(f"Vol regime OK ({vol_regime}) (+15)")
        elif vol_regime == "high":
            score += 5
            factors.append(f"Vol regime elevated (+5)")
        else:
            warnings.append(f"Extreme volatility ({vol_regime})")

        # 8. Liquidity Sweep Bonus (max 15)
        if liquidity_sweep:
            score += 15
            factors.append("Liquidity sweep confirmed (+15)")

        # Calculate grade
        if score >= 85:
            grade = "A+"
        elif score >= 70:
            grade = "A"
        elif score >= 55:
            grade = "B"
        elif score >= 40:
            grade = "C"
        else:
            grade = "D"

        # Can trade?
        can_trade = grade in ["A+", "A", "B"] and not is_overext

        return EntryConfirmation(
            score=score,
            grade=grade,
            pullback_confirmed=pullback_ok,
            pullback_type=pullback_type,
            pullback_bonus=pullback_bonus,
            candle_pattern=candle_pattern,
            candle_bonus=candle_bonus,
            volume_confirmed=vol_ok,
            volume_reason=vol_reason,
            volume_bonus=vol_bonus,
            mtf_confluence=confluence,
            mtf_bonus=mtf_bonus,
            is_overextended=is_overext,
            overext_reason=overext_reason,
            overext_penalty=overext_penalty,
            factors=factors,
            warnings=warnings,
            can_trade=can_trade
        )

    def get_prompt_section(self, confirmation: EntryConfirmation) -> str:
        """Generate entry analysis section for GLM prompt"""
        factors_str = "\n".join(f"  + {f}" for f in confirmation.factors) if confirmation.factors else "  (none)"
        warnings_str = "\n".join(f"  ! {w}" for w in confirmation.warnings) if confirmation.warnings else ""

        section = f"""
================================================================================
ENHANCED ENTRY ANALYSIS (v4.0)
================================================================================
Grade: {confirmation.grade} ({confirmation.score}/125)
Tradeable: {"YES" if confirmation.can_trade else "NO - " + (confirmation.overext_reason if confirmation.is_overextended else "Low grade")}

Factors:
{factors_str}
"""
        if warnings_str:
            section += f"""
Warnings:
{warnings_str}
"""

        section += "================================================================================"
        return section
