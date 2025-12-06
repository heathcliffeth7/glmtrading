"""
Feature Analysis Components for Enhanced Trading Signals

Logic gate checks and feature analysis builders for NOF1.AI prompt system.
Extracted from nof1_prompt_builder.py for token optimization.

This module contains:
- 8 Logic Gate checks (Red Team Mode)
- Enhanced feature builders
- Market mechanics analysis
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# LOGIC GATE CHECK FUNCTIONS (Red Team Mode - 8 Checkpoints)
# =============================================================================

def volume_depth_check(
    historical_arrays: Dict[str, Any],
    primary_tf: str = "4h"
) -> float:
    """
    Gate 1: Volume Depth - Calculate volume ratio.
    
    Args:
        historical_arrays: Historical OHLCV data by timeframe
        primary_tf: Primary timeframe (default: "4h")
        
    Returns:
        Volume ratio (current/avg)
    """
    hist = historical_arrays.get(primary_tf, {})
    volumes = hist.get("volume", [])
    
    if len(volumes) < 21:
        return 1.0  # Default healthy
    
    avg_volume = sum(volumes[-21:-1]) / 20
    current_volume = volumes[-1]
    
    if avg_volume <= 0:
        return 1.0
    
    return round(current_volume / avg_volume, 2)


def session_anomaly_check(volume_ratio: float) -> Tuple[str, bool]:
    """
    Gate 2: Session Anomaly - Check for low volume during London/NY overlap.
    
    Args:
        volume_ratio: Current volume ratio
        
    Returns:
        (session_name, is_anomaly)
    """
    try:
        from app.risk_manager.time_filter import TimeBasedFilter, TradingSession
        
        # Use swing mode for 4H primary timeframe
        time_filter = TimeBasedFilter(mode="swing")
        result = time_filter.analyze()
        
        session_name = result.session.value
        is_overlap = result.session in [
            TradingSession.OVERLAP_EUROPE_US,
            TradingSession.OVERLAP_ASIA_EUROPE
        ]
        
        # Overlap + low volume = TRAP WARNING
        is_anomaly = is_overlap and volume_ratio < 0.5
        
        return session_name, is_anomaly
    except Exception as e:
        logger.warning(f"Session anomaly check failed: {e}")
        return "UNKNOWN", False


def oi_analysis_check(
    futures_data: Dict[str, Any],
    historical_arrays: Dict[str, Any],
    primary_tf: str = "4h"
) -> str:
    """
    Gate 3: OI Analysis - Who is selling? Long liquidation vs Aggressive shorting.
    
    Args:
        futures_data: Futures market data (OI, funding, etc.)
        historical_arrays: Historical OHLCV data
        primary_tf: Primary timeframe
        
    Returns:
        OI interpretation: "LONG_LIQUIDATION", "AGGRESSIVE_SHORTING", 
        "SHORT_LIQUIDATION", "NEW_LONGS", or "UNKNOWN"
    """
    hist = historical_arrays.get(primary_tf, {})
    closes = hist.get("close", [])
    
    if len(closes) < 2:
        return "UNKNOWN"
    
    price_change = closes[-1] - closes[-2]
    price_down = price_change < 0
    
    # OI change
    oi_current = futures_data.get("open_interest", 0)
    oi_prev = futures_data.get("open_interest_prev", oi_current)
    oi_change = oi_current - oi_prev
    oi_down = oi_change < 0
    
    if price_down and oi_down:
        return "LONG_LIQUIDATION"
    elif price_down and not oi_down:
        return "AGGRESSIVE_SHORTING"
    elif not price_down and oi_down:
        return "SHORT_LIQUIDATION"
    else:
        return "NEW_LONGS"


def vwap_position_check(
    position_type: str,
    current_price: float,
    historical_arrays: Dict[str, Any],
    volume_analyzer,
    primary_tf: str = "4h"
) -> Tuple[bool, float]:
    """
    Gate 4: VWAP Position - Price position relative to VWAP.
    
    Args:
        position_type: "LONG" or "SHORT"
        current_price: Current price
        historical_arrays: Historical OHLCV data
        volume_analyzer: VolumeAnalyzer instance
        primary_tf: Primary timeframe
        
    Returns:
        (is_counter_trend, vwap_value)
    """
    hist = historical_arrays.get(primary_tf, {})
    
    highs = hist.get("high", [])
    lows = hist.get("low", [])
    closes = hist.get("close", [])
    volumes = hist.get("volume", [])
    
    if len(closes) < 20:
        return False, current_price  # Can't calculate
    
    try:
        vwap_data = volume_analyzer.calculate_vwap(highs, lows, closes, volumes)
        vwap = vwap_data.get("vwap", current_price)
    except Exception as e:
        logger.warning(f"VWAP calculation failed: {e}")
        return False, current_price
    
    if vwap <= 0:
        return False, current_price
    
    # Counter-trend check
    is_counter_trend = False
    if position_type == "SHORT" and current_price > vwap:
        is_counter_trend = True
    elif position_type == "LONG" and current_price < vwap:
        is_counter_trend = True
    
    return is_counter_trend, vwap


def liquidation_magnet_check(
    symbol: str,
    current_price: float,
    volume_ratio: float,
    futures_data: Dict[str, Any],
    historical_arrays: Dict[str, Any],
    primary_tf: str = "4h"
) -> Tuple[bool, float]:
    """
    Gate 5: Liquidation Magnet - Check for nearby liquidation levels.
    
    Args:
        symbol: Trading symbol
        current_price: Current price
        volume_ratio: Volume ratio from Gate 1
        futures_data: Futures market data
        historical_arrays: Historical OHLCV data
        primary_tf: Primary timeframe
        
    Returns:
        (is_magnet_risk, nearest_distance_pct)
    """
    try:
        from app.indicators.liquidation_analyzer import LiquidationAnalyzer
        
        # Use swing mode for 4H primary timeframe
        analyzer = LiquidationAnalyzer(mode="swing")
        
        hist = historical_arrays.get(primary_tf, {})
        highs = hist.get("high", [])
        lows = hist.get("low", [])
        
        if not highs or not lows:
            return False, 100.0  # No risk
        
        recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
        oi_usd = futures_data.get("open_interest_usd", 
                                  futures_data.get("open_interest", 0) * current_price)
        
        result = analyzer.analyze(
            symbol=symbol,
            current_price=current_price,
            recent_high=recent_high,
            recent_low=recent_low,
            open_interest_usd=oi_usd
        )
        
        # Find nearest liquidation distance
        nearest_dist = 100.0
        if result.nearest_long_liq:
            nearest_dist = min(nearest_dist, result.nearest_long_liq.distance_pct)
        if result.nearest_short_liq:
            nearest_dist = min(nearest_dist, result.nearest_short_liq.distance_pct)
        
        # Magnet risk: nearby liquidation + low volume
        is_magnet_risk = nearest_dist < 0.5 and volume_ratio < 0.7
        
        return is_magnet_risk, nearest_dist
    except Exception as e:
        logger.warning(f"Liquidation magnet check failed: {e}")
        return False, 100.0


def data_sanity_check(
    current_snapshots: Dict[str, Any],
    historical_arrays: Dict[str, Any],
    primary_tf: str = "4h"
) -> Tuple[bool, str]:
    """
    Gate 6: Data Sanity - Check data consistency.
    
    Args:
        current_snapshots: Current market snapshots
        historical_arrays: Historical data arrays
        primary_tf: Primary timeframe
        
    Returns:
        (has_conflict, conflict_detail)
    """
    snapshot = current_snapshots.get(primary_tf, {})
    hist = historical_arrays.get(primary_tf, {})
    
    conflicts = []
    
    # Price consistency
    snapshot_price = snapshot.get("close", 0)
    hist_prices = hist.get("close", [])
    if hist_prices and snapshot_price > 0:
        last_hist_price = hist_prices[-1]
        price_diff_pct = abs(snapshot_price - last_hist_price) / last_hist_price * 100
        if price_diff_pct > 1.0:  # >1% difference
            conflicts.append(f"Price mismatch: {price_diff_pct:.2f}%")
    
    # ADX consistency
    snapshot_adx = snapshot.get("adx_14", 0)
    hist_adx = hist.get("adx_14", [])
    if hist_adx and snapshot_adx > 0:
        last_hist_adx = hist_adx[-1]
        adx_diff = abs(snapshot_adx - last_hist_adx)
        if adx_diff > 10:  # >10 point difference
            conflicts.append(f"ADX mismatch: {adx_diff:.1f}")
    
    has_conflict = len(conflicts) > 0
    conflict_detail = ", ".join(conflicts) if conflicts else "YOK"
    
    return has_conflict, conflict_detail


def adx_regime_check(
    historical_arrays: Dict[str, Any],
    primary_tf: str = "4h"
) -> Tuple[bool, float]:
    """
    Gate 7: ADX Regime - Check trend strength.
    
    Args:
        historical_arrays: Historical data arrays
        primary_tf: Primary timeframe
        
    Returns:
        (is_choppy, adx_value)
    """
    hist = historical_arrays.get(primary_tf, {})
    adx_values = hist.get("adx_14", [])
    
    if not adx_values:
        return False, 25.0  # Default moderate
    
    current_adx = adx_values[-1]
    is_choppy = current_adx < 20
    
    return is_choppy, current_adx


def micro_divergence_check(
    position_type: str,
    historical_arrays: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Gate 8: Micro Structure - 1m divergence check.
    
    Args:
        position_type: "LONG" or "SHORT"
        historical_arrays: Historical data arrays
        
    Returns:
        (is_risk, divergence_type)
    """
    hist_1m = historical_arrays.get("1m", {})
    
    closes = hist_1m.get("close", [])
    rsi_values = hist_1m.get("rsi_14", [])
    
    if len(closes) < 5 or len(rsi_values) < 5:
        return False, "YOK"
    
    # Last 5 bar analysis
    price_change = closes[-1] - closes[-5]
    rsi_change = rsi_values[-1] - rsi_values[-5]
    
    price_down = price_change < 0
    rsi_up = rsi_change > 0
    
    price_up = price_change > 0
    rsi_down = rsi_change < 0
    
    # Divergence detection
    if price_down and rsi_up:
        div_type = "BULLISH"
        # Risk for SHORT positions
        is_risk = position_type == "SHORT"
    elif price_up and rsi_down:
        div_type = "BEARISH"
        # Risk for LONG positions
        is_risk = position_type == "LONG"
    else:
        div_type = "YOK"
        is_risk = False
    
    return is_risk, div_type


def liquidity_sweep_check(
    data_primary: Dict,
    mtf_alignment: Dict
) -> bool:
    """
    Check for liquidity sweep pattern.
    
    Args:
        data_primary: Primary timeframe data
        mtf_alignment: Multi-timeframe alignment data
        
    Returns:
        True if liquidity sweep detected
    """
    # Placeholder - implement liquidity sweep logic
    # Original implementation would need access to more context
    return False


# =============================================================================
# LOGIC GATES BUILDER
# =============================================================================

class LogicGatesBuilder:
    """Builds the 8 Logic Gates section for Red Team Mode"""
    
    def __init__(self, primary_tf: str = "4h", volume_analyzer=None):
        """
        Initialize Logic Gates Builder.
        
        Args:
            primary_tf: Primary timeframe (default: "4h")
            volume_analyzer: VolumeAnalyzer instance (optional)
        """
        self._primary_tf = primary_tf
        self._volume_analyzer = volume_analyzer
    
    def build_section(
        self,
        symbol: str,
        position_type: str,
        current_price: float,
        current_snapshots: Dict[str, Any],
        historical_arrays: Dict[str, Any],
        futures_data: Dict[str, Any]
    ) -> str:
        """
        Build complete 8 Logic Gates section.
        
        Args:
            symbol: Trading symbol
            position_type: "LONG" or "SHORT"
            current_price: Current price
            current_snapshots: Current market snapshots
            historical_arrays: Historical OHLCV data
            futures_data: Futures market data
            
        Returns:
            Formatted logic gates section string
        """
        gates = []
        risk_count = 0
        
        # Gate 1: Volume Depth
        volume_ratio = volume_depth_check(historical_arrays, self._primary_tf)
        if volume_ratio < 0.5:
            gates.append(f"1. ⚠️ VOLUME DEPTH: {volume_ratio:.2f}x - ILLIQUID (vakum)")
            risk_count += 1
        else:
            gates.append(f"1. ✅ VOLUME DEPTH: {volume_ratio:.2f}x - SAĞLIKLI")
        
        # Gate 2: Session Anomaly
        session_name, is_session_anomaly = session_anomaly_check(volume_ratio)
        if is_session_anomaly:
            gates.append(f"2. ⚠️ SESSION: {session_name} + düşük hacim - TRAP WARNING")
            risk_count += 1
        else:
            gates.append(f"2. ✅ SESSION: {session_name} - NORMAL")
        
        # Gate 3: OI Analysis
        oi_interpretation = oi_analysis_check(futures_data, historical_arrays, self._primary_tf)
        if oi_interpretation == "LONG_LIQUIDATION" and position_type == "SHORT":
            gates.append(f"3. ⚠️ OI: {oi_interpretation} - Yakıt sınırlı, düşüş kalıcı olmayabilir")
            risk_count += 1
        elif oi_interpretation == "SHORT_LIQUIDATION" and position_type == "LONG":
            gates.append(f"3. ⚠️ OI: {oi_interpretation} - Yakıt sınırlı, yükseliş kalıcı olmayabilir")
            risk_count += 1
        else:
            gates.append(f"3. ✅ OI: {oi_interpretation}")
        
        # Gate 4: VWAP Position
        if self._volume_analyzer:
            is_counter_trend, vwap = vwap_position_check(
                position_type, current_price, historical_arrays, 
                self._volume_analyzer, self._primary_tf
            )
            if is_counter_trend:
                gates.append(f"4. ⚠️ VWAP: ${vwap:.2f} - {position_type} pozisyon COUNTER-TREND")
                risk_count += 1
            else:
                gates.append(f"4. ✅ VWAP: ${vwap:.2f} - Pozisyon trend yönünde")
        else:
            gates.append(f"4. ⚠️ VWAP: N/A - VolumeAnalyzer unavailable")
        
        # Gate 5: Liquidation Magnet
        is_magnet_risk, nearest_dist = liquidation_magnet_check(
            symbol, current_price, volume_ratio, futures_data, historical_arrays, self._primary_tf
        )
        if is_magnet_risk:
            gates.append(f"5. ⚠️ LIQUIDATION: %{nearest_dist:.2f} uzaklık - STOP HUNT RİSKİ")
            risk_count += 1
        else:
            gates.append(f"5. ✅ LIQUIDATION: %{nearest_dist:.2f} uzaklık - Güvenli")
        
        # Gate 6: Data Sanity
        has_conflict, conflict_detail = data_sanity_check(
            current_snapshots, historical_arrays, self._primary_tf
        )
        if has_conflict:
            gates.append(f"6. ⚠️ DATA SANITY: Tutarsızlık - {conflict_detail}")
            risk_count += 1
        else:
            gates.append(f"6. ✅ DATA SANITY: Veriler tutarlı")
        
        # Gate 7: ADX Regime
        is_choppy, adx_value = adx_regime_check(historical_arrays, self._primary_tf)
        if is_choppy:
            gates.append(f"7. ⚠️ ADX: {adx_value:.1f} - NO TREND (CHOP), kârı al")
            risk_count += 1
        else:
            gates.append(f"7. ✅ ADX: {adx_value:.1f} - Trend mevcut")
        
        # Gate 8: Micro Divergence
        is_div_risk, div_type = micro_divergence_check(position_type, historical_arrays)
        if is_div_risk:
            gates.append(f"8. ⚠️ 1m DIVERGENCE: {div_type} - Kısa vadeli tersine dönüş riski")
            risk_count += 1
        else:
            gates.append(f"8. ✅ 1m DIVERGENCE: {div_type}")
        
        # Build section
        gates_text = "\n".join(gates)
        recommendation = "CLOSE öner" if risk_count > 2 else "HOLD öner"
        
        return f"""
================================================================================
RED TEAM LOGIC GATES (8 CHECKPOINT)
================================================================================

{gates_text}

--------------------------------------------------------------------------------
SONUÇ: {risk_count}/8 risk faktörü tespit edildi → {recommendation}
--------------------------------------------------------------------------------
"""


__all__ = [
    # Check functions
    "volume_depth_check",
    "session_anomaly_check",
    "oi_analysis_check",
    "vwap_position_check",
    "liquidation_magnet_check",
    "data_sanity_check",
    "adx_regime_check",
    "micro_divergence_check",
    "liquidity_sweep_check",
    # Builder class
    "LogicGatesBuilder",
]
