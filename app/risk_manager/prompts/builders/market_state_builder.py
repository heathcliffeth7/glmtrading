"""
Market State Builder Module

Responsibilities:
- Build comprehensive market state section for GLM prompts
- Aggregate volatility, trend, structure, and futures data
- Generate pre-calculated analysis blocks with raw data
- Provide contextual narrative without judgments

This module extracts market state building logic from Nof1PromptBuilder
to improve modularity and testability.
"""

from typing import Dict, List, Any, Optional
import logging

from app.risk_manager.prompts import (
    summarize_series,
    format_array,
)

logger = logging.getLogger(__name__)


def calculate_level_status(
    *,
    current_price: float,
    support_level: float,
    resistance_level: float,
    proximity_threshold: float = 0.25,
) -> Dict[str, Any]:
    """
    Summarize price position vs support/resistance.

    proximity_threshold is percent distance (e.g. 0.5 = 0.5%).
    """
    def _dist_pct(level: float, price: float) -> Optional[float]:
        if not level or level <= 0 or not price or price <= 0:
            return None
        return abs((price - level) / level * 100)

    support_dist = _dist_pct(support_level, current_price)
    resistance_dist = _dist_pct(resistance_level, current_price)

    if not current_price or current_price <= 0 or not support_level or support_level <= 0 or not resistance_level or resistance_level <= 0:
        return {
            "position": "UNKNOWN",
            "support": {"level": support_level, "status": "UNKNOWN", "distance_pct": support_dist},
            "resistance": {"level": resistance_level, "status": "UNKNOWN", "distance_pct": resistance_dist},
            "proximity_threshold": proximity_threshold,
        }

    position = "IN_RANGE"
    if current_price > resistance_level:
        position = "BREAKOUT"
    elif current_price < support_level:
        position = "BREAKDOWN"

    support_status = "ABOVE_SUPPORT"
    if current_price < support_level:
        support_status = "BELOW_SUPPORT"
    elif support_dist is not None and support_dist <= proximity_threshold:
        support_status = "AT_SUPPORT"

    resistance_status = "BELOW_RESISTANCE"
    if current_price > resistance_level:
        resistance_status = "ABOVE_RESISTANCE"
    elif resistance_dist is not None and resistance_dist <= proximity_threshold:
        resistance_status = "AT_RESISTANCE"

    return {
        "position": position,
        "support": {"level": support_level, "status": support_status, "distance_pct": support_dist},
        "resistance": {"level": resistance_level, "status": resistance_status, "distance_pct": resistance_dist},
        "proximity_threshold": proximity_threshold,
    }


class MarketStateBuilder:
    """Builds market state section of GLM prompts"""

    def __init__(
        self,
        settings,
        primary_tf: str,
        market_analyzer,
        entry_analyzer=None,
        volume_analyzer=None,
        exit_plan_calculator=None,
    ):
        """
        Initialize MarketStateBuilder.

        Args:
            settings: Application settings instance
            primary_tf: Primary timeframe string (e.g., "4h", "30m")
            market_analyzer: MarketAnalyzer instance for conflict detection
            entry_analyzer: Optional EntryAnalyzer for structure/SR analysis
            volume_analyzer: Optional VolumeAnalyzer for OBV trend
            exit_plan_calculator: Optional ExitPlanCalculator (future use)
        """
        self._settings = settings
        self._primary_tf = primary_tf
        self._market_analyzer = market_analyzer
        self._entry_analyzer = entry_analyzer
        self._volume_analyzer = volume_analyzer
        self._exit_plan_calculator = exit_plan_calculator

    def build(
        self,
        symbol: str,
        current_snapshots: Dict[str, Dict],
        historical_arrays: Dict[str, Dict],
        futures_data: Dict[str, Any],
        volatility_state: Dict[str, Any],
        atr_value: float = 0.0,
        htf_analysis: Dict[str, Any] = None,
    ) -> str:
        """
        Build market state section.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            current_snapshots: Dict of current indicator values by timeframe
            historical_arrays: Dict of historical arrays by timeframe
            futures_data: Futures market data (funding, OI, L/S ratio)
            volatility_state: Dict containing volatility metrics:
                - volatility_score: float (0-1)
                - atr_pct: float
                - regime_key: str ("low", "medium", "high", "extreme")
                - realized_vol_pct: float
                - median_realized_vol_pct: float
                - vol_ratio: float
                - atr_ratio: float
            atr_value: Absolute ATR value for volatility bands
            htf_analysis: Optional HTF support/resistance data

        Returns:
            str: Formatted market state section
        """
        lines = [
            "=" * 80,
            f"CURRENT MARKET STATE FOR {symbol}",
            "=" * 80,
        ]

        # Use primary timeframe (4H for swing, 30M for scalp)
        primary_tf = self._primary_tf
        data_primary = current_snapshots.get(primary_tf, {})
        hist_primary = historical_arrays.get(primary_tf, {})

        # Extract volatility state
        volatility_score = volatility_state.get("volatility_score", 0.5)
        atr_pct = volatility_state.get("atr_pct", 0.0)
        regime_key = volatility_state.get("regime_key", "medium")
        realized_vol_pct = volatility_state.get("realized_vol_pct", 0.0)
        median_realized_vol_pct = volatility_state.get("median_realized_vol_pct", 0.0)
        vol_ratio = volatility_state.get("vol_ratio", 1.0)
        atr_ratio = volatility_state.get("atr_ratio", 1.0)

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
        rel = max(vol_ratio, atr_ratio)
        lines.extend([
            "RELATIVE VOLATILITY CONTEXT (asset-agnostic):",
            f"• Realized Vol (50 bars): {realized_vol_pct:.2f}%",
            f"• Median Realized Vol: {median_realized_vol_pct:.2f}%",
            f"• Vol Ratio (current/median): {vol_ratio:.2f}x",
            f"• ATR Ratio (proxy): {atr_ratio:.2f}x",
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

            market_structure = self._analyze_market_structure(hist_primary, volatility_state)

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
        lines.append(self.generate_narrative(data_primary))

        return "\n".join(lines)

    def build_pre_calculated_section(
        self,
        current_snapshots: Dict,
        historical_arrays: Dict,
        current_price: float,
    ) -> str:
        """
        Build pre-calculated analysis section (v4.0: RAW DATA BLOCKS).

        GLM'in kendi analizini yapabilmesi icin ham verileri ve matematiksel
        mesafeleri sunar. Puanlama yok - GLM ozgurce karar verecek.

        Args:
            current_snapshots: Dict of current indicator values by timeframe
            historical_arrays: Dict of historical arrays by timeframe
            current_price: Current trading price

        Returns:
            str: Formatted pre-calculated section
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
        if self._volume_analyzer and hist_4h.get("close") and hist_4h.get("volume"):
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
        support_level = float(sr.get("nearest_support") or 0.0)
        resistance_level = float(sr.get("nearest_resistance") or 0.0)
        level_status = calculate_level_status(
            current_price=current_price,
            support_level=support_level,
            resistance_level=resistance_level,
        )

        support_label = f"{support_level:.2f}" if support_level > 0 else "N/A"
        resistance_label = f"{resistance_level:.2f}" if resistance_level > 0 else "N/A"
        lines.append(f"  Support: {support_label} ({level_status['support']['status']})")
        lines.append(f"  Resistance: {resistance_label} ({level_status['resistance']['status']})")
        lines.append(f"  Position: {level_status['position']}")
        lines.append("  [15M] Support: N/A | Resistance: N/A | Position: UNKNOWN")

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

        return "\n".join(lines)

    def generate_narrative(self, data_primary: Dict) -> str:
        """
        Generate contextual narrative (v4.0: Contextual Data Points).

        Sadece ham veri, yorum GLM'e birakilir.

        Args:
            data_primary: Primary timeframe current snapshot

        Returns:
            str: Formatted narrative section
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

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

    def _analyze_market_structure(
        self,
        hist_data: Dict[str, List[float]],
        volatility_state: Dict[str, Any]
    ) -> str:
        """Delegate to EntryAnalyzer if available."""
        if self._entry_analyzer:
            atr_pct = volatility_state.get("atr_pct", 0.0)
            vol = volatility_state.get("volatility_score", 0.5)
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

    def _calculate_sr_distances(
        self,
        historical_arrays: Dict,
        current_price: float
    ) -> Dict:
        """Delegate to EntryAnalyzer (v4.0)"""
        if self._entry_analyzer:
            return self._entry_analyzer.calculate_sr_distances(historical_arrays, current_price)
        return {
            "nearest_support": 0,
            "nearest_resistance": 0,
            "support_dist_pct": 0,
            "resistance_dist_pct": 0,
            "rr_ratio": 0,
            "levels": {},
        }
