"""
Enhanced Features Builder Module

Responsible for building enhanced analysis sections in GLM prompts:
- Volume analysis (CVD, OBV, VWAP)
- Funding rate analysis with signals
- ADX data with RSI context
- Liquidation levels
- Drawdown tracking
- Correlation risk
- Time-based position management summaries (TP/Hold)
- Taker Buy/Sell Ratio (NEW)
- Order Book Imbalance (NEW)
- BTC Correlation (NEW)
- Fibonacci Levels (NEW)
- Candlestick Patterns (NEW)

This module provides a clean interface for enhanced features while supporting
graceful degradation when analyzers are unavailable.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from app.config.settings import get_settings
from app.risk_manager.prompts.metrics_calculator import (
    calculate_slope,
    format_array,
)

# New analyzers
try:
    from app.indicators.correlation_analyzer import CorrelationAnalyzer
except ImportError:
    CorrelationAnalyzer = None

try:
    from app.indicators.fibonacci_analyzer import FibonacciAnalyzer
except ImportError:
    FibonacciAnalyzer = None

try:
    from app.indicators.candlestick_analyzer import CandlestickAnalyzer
except ImportError:
    CandlestickAnalyzer = None

try:
    from app.indicators.orderbook_analyzer import OrderBookAnalyzer
except ImportError:
    OrderBookAnalyzer = None

try:
    from app.indicators.liquidation_heatmap import LiquidationHeatmapAnalyzer
except ImportError:
    LiquidationHeatmapAnalyzer = None

logger = logging.getLogger(__name__)


class EnhancedFeaturesBuilder:
    """
    Builds enhanced features sections for GLM prompts.
    
    Supports all enhanced analyzers with graceful degradation when components
    are unavailable. Manages active glossary contexts for dynamic glossary building.
    """

    def __init__(
        self,
        settings=None,
        primary_tf: str = "4h",
        indicator_interpreter=None,
        enhanced_features_enabled: bool = True,
        volume_analyzer=None,
        funding_analyzer=None,
        liquidation_analyzer=None,
        entry_analyzer=None,
        hold_engine=None,
        time_exit_manager=None,
        time_filter=None,
        drawdown_manager=None,
        correlation_guard=None,
        logic_gates_builder=None,
    ):
        """
        Initialize the enhanced features builder.

        Args:
            settings: App settings (required)
            primary_tf: Primary timeframe (required)
            indicator_interpreter: Interpreter for indicators (required)
            enhanced_features_enabled: Whether enhanced features are enabled (required)
            volume_analyzer: Volume analysis component (optional)
            funding_analyzer: Funding rate analysis component (optional)
            liquidation_analyzer: Liquidation level analysis component (optional)
            entry_analyzer: Entry confirmation analysis component (optional)
            hold_engine: Hold decision engine (optional)
            time_exit_manager: Time-based exit manager (optional)
            time_filter: Time-based trading filter (optional)
            drawdown_manager: Drawdown tracking manager (optional)
            correlation_guard: Portfolio correlation guard (optional)
            logic_gates_builder: Logic gates builder for red team mode (optional)
        """
        self._settings = settings or get_settings()
        self._primary_tf = primary_tf
        self._indicator_interpreter = indicator_interpreter
        self._enhanced_features_enabled = enhanced_features_enabled

        # Optional analyzers - support graceful degradation
        self._volume_analyzer = volume_analyzer
        self._funding_analyzer = funding_analyzer
        self._liquidation_analyzer = liquidation_analyzer
        self._entry_analyzer = entry_analyzer
        self._hold_engine = hold_engine
        self._time_exit_manager = time_exit_manager
        self._time_filter = time_filter
        self._drawdown_manager = drawdown_manager
        self._correlation_guard = correlation_guard
        self._logic_gates_builder = logic_gates_builder
        self._partial_tp_manager = None  # Will be set if available

        # Initialize new analyzers
        self._btc_correlation_analyzer = (
            CorrelationAnalyzer(window=20) if CorrelationAnalyzer else None
        )
        self._fibonacci_analyzer = (
            FibonacciAnalyzer(swing_lookback=20) if FibonacciAnalyzer else None
        )
        self._candlestick_analyzer = (
            CandlestickAnalyzer() if CandlestickAnalyzer else None
        )
        self._orderbook_analyzer = (
            OrderBookAnalyzer(depth=20) if OrderBookAnalyzer else None
        )
        self._liquidation_heatmap_analyzer = (
            LiquidationHeatmapAnalyzer(lookback_hours=24, num_zones=5)
            if LiquidationHeatmapAnalyzer else None
        )

    def build(
        self,
        symbol: str,
        current_price: float,
        current_snapshots: Dict,
        historical_arrays: Dict,
        futures_data: Dict,
        portfolio_metrics: Dict,
        regime: str,
        active_glossary_contexts: List[str],
        btc_prices: Optional[List[float]] = None,
        orderbook_data: Optional[Dict] = None,
    ) -> str:
        """
        Build enhanced features section including volume, funding, ADX, time filter, etc.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            current_price: Current market price
            current_snapshots: Current market snapshots by timeframe
            historical_arrays: Historical data arrays by timeframe
            futures_data: Futures market data (funding, OI, taker ratio, etc.)
            portfolio_metrics: Current portfolio metrics
            regime: Current volatility regime
            active_glossary_contexts: Mutable list to track active contexts for dynamic glossary
            btc_prices: BTC prices for correlation analysis (optional)
            orderbook_data: Order book data {"bids": [...], "asks": [...]} (optional)

        Returns:
            Formatted enhanced features section string
        """
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
            if self._time_filter:
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
            if self._volume_analyzer and hist_primary.get("close") and hist_primary.get("volume"):
                closes = hist_primary["close"]
                volumes = hist_primary["volume"]
                highs = hist_primary.get("high", closes)
                lows = hist_primary.get("low", closes)

                # CVD - raw values + contextual interpretation
                cvd_values, _ = self._volume_analyzer.calculate_cvd(closes, highs, lows, volumes)
                if cvd_values:
                    # Show last 8 CVD values for GLM to analyze slope
                    cvd_recent = cvd_values[-8:] if len(cvd_values) >= 8 else cvd_values

                    # Determine current position (position-aware CVD interpretation)
                    current_position = None
                    if portfolio_metrics:
                        net_pos = portfolio_metrics.get("net_position", 0)
                        if net_pos > 0:
                            current_position = "LONG"
                        elif net_pos < 0:
                            current_position = "SHORT"

                    # Context-aware interpretation (with position info)
                    if self._indicator_interpreter:
                        cvd_label, cvd_desc = self._indicator_interpreter.interpret_cvd_context(
                            cvd_values, closes, current_position=current_position
                        )
                    else:
                        cvd_label, cvd_desc = "NEUTRAL", "No interpreter available"
                    
                    # Add to active glossary contexts for dynamic glossary
                    if cvd_label and cvd_label != "NEUTRAL":
                        if cvd_label not in active_glossary_contexts:
                            active_glossary_contexts.append(cvd_label)
                    
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

                    # Volume Spike detection (vs 20-bar average)
                    is_spike, vol_ratio = self._volume_analyzer.detect_volume_spike(volumes)
                    if is_spike:
                        lines.append(f"  🔥 Volume Spike: {vol_ratio:.1f}x (vs 20-bar avg) - yüksek aktivite")
                    elif vol_ratio < 0.3:
                        lines.append(f"  ⚠️ Volume Ratio: {vol_ratio:.2f}x (vs 20-bar avg) - DÜŞÜK HACİM")
                    else:
                        lines.append(f"  Volume Ratio: {vol_ratio:.1f}x (vs 20-bar avg)")

                    # OBV Divergence check
                    _, obv_divergence = self._volume_analyzer.calculate_obv(closes, volumes)
                    if obv_divergence:
                        lines.append(f"  ⚠️ OBV Divergence: Fiyat-Hacim uyumsuzluğu tespit edildi")

                    lines.append("")

            # 3. ADX Data (Raw values - no interpretation)
            if hist_primary.get("high") and hist_primary.get("low") and hist_primary.get("close"):
                try:
                    from app.indicators.funding_analyzer import calculate_adx
                    
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
                            if rsi_values and self._indicator_interpreter:
                                rsi_current = rsi_values[-1] if rsi_values else 50.0
                                price_slope = calculate_slope(closes, 4) if closes else 0.0
                                rsi_label, rsi_desc = self._indicator_interpreter.interpret_rsi_context(rsi_current, adx_value, price_slope)
                                lines.append(f"  RSI Context: [{rsi_label}]")
                                lines.append(f"  Interpretation: {rsi_desc}")

                            lines.append("")
                except ImportError:
                    logger.debug("ADX calculation not available")

            # 4. Funding Rate Analysis (with signals)
            if self._funding_analyzer and futures_data and futures_data.get("current"):
                funding_rate = futures_data["current"].get("funding_rate", 0)
                funding_avg = futures_data.get("averages", {}).get("funding_rate_avg", funding_rate)

                if funding_rate != 0:
                    lines.extend([
                        "FUNDING RATE:",
                        f"  Current: {funding_rate:.6f} ({funding_rate*100:.4f}%)",
                        f"  8h Avg: {funding_avg:.6f}",
                    ])

                    # Funding Rate Analyzer - generate signals and warnings
                    # Determine trend direction
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

                    # Add signal and warning information
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

                    # OI Context (Funding + OI combination interpretation)
                    oi_current = futures_data.get("current", {}).get("open_interest", 0)
                    oi_avg = futures_data.get("averages", {}).get("open_interest_avg", oi_current)
                    if oi_current > 0 and hist_primary.get("close") and self._indicator_interpreter:
                        closes = hist_primary["close"]
                        price_slope = calculate_slope(closes, 4) if closes else 0.0
                        oi_label, oi_desc = self._indicator_interpreter.interpret_funding_oi_context(
                            funding_rate, oi_current, oi_avg, price_slope
                        )
                        
                        # Add to active glossary contexts for dynamic glossary
                        if oi_label and oi_label != "NEUTRAL":
                            if oi_label not in active_glossary_contexts:
                                active_glossary_contexts.append(oi_label)
                        
                        lines.append(f"  OI Context: [{oi_label}]")
                        lines.append(f"  Interpretation: {oi_desc}")

                    lines.append("")

            # 5. Liquidation Levels (Raw distances only)
            if self._liquidation_analyzer and current_price > 0 and hist_primary.get("high") and hist_primary.get("low"):
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
            if self._drawdown_manager:
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
            if self._correlation_guard:
                long_pos = portfolio_metrics.get("long_position", 0)
                short_pos = portfolio_metrics.get("short_position", 0)
                if abs(long_pos) > 0.0001 or abs(short_pos) > 0.0001:
                    equity = portfolio_metrics.get("equity", 10000)
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
                tp_hold_summary = self.build_tp_hold_summary(
                    symbol=symbol,
                    position_data=position_data,
                    current_price=current_price,
                    historical_arrays=historical_arrays,
                    atr_pct=None,  # Will be extracted from position_data if available
                    volatility=None,  # Will be extracted from position_data if available
                )
                if tp_hold_summary:
                    lines.append(tp_hold_summary)
                    lines.append("")

            # 9. Taker Buy/Sell Ratio (NEW - from futures_data)
            if futures_data and futures_data.get("current"):
                taker_ratio = futures_data["current"].get("taker_buy_sell_ratio", 0)
                taker_buy = futures_data["current"].get("taker_buy_volume", 0)
                taker_sell = futures_data["current"].get("taker_sell_volume", 0)
                if taker_ratio > 0:
                    if taker_ratio > 1.2:
                        bias = "BUYERS DOMINANT"
                        emoji = "🟢"
                    elif taker_ratio < 0.8:
                        bias = "SELLERS DOMINANT"
                        emoji = "🔴"
                    else:
                        bias = "BALANCED"
                        emoji = "⚪"
                    lines.extend([
                        "TAKER BUY/SELL RATIO:",
                        f"  {emoji} Ratio: {taker_ratio:.2f}x ({bias})",
                        f"  Buy Vol: {taker_buy:,.0f} | Sell Vol: {taker_sell:,.0f}",
                    ])
                    lines.append("")

            # 10. Order Book Imbalance (NEW)
            if self._orderbook_analyzer and orderbook_data:
                bids = orderbook_data.get("bids", [])
                asks = orderbook_data.get("asks", [])
                if bids and asks:
                    ob_section = self._orderbook_analyzer.get_prompt_section(
                        symbol=symbol, bids=bids, asks=asks
                    )
                    if ob_section:
                        lines.append(ob_section)
                        lines.append("")

            # 11. BTC Correlation (NEW - only for altcoins)
            if (self._btc_correlation_analyzer and
                btc_prices and
                not symbol.upper().startswith("BTC")):
                symbol_prices = hist_primary.get("close", [])
                if symbol_prices and len(symbol_prices) >= 21:
                    corr_section = self._btc_correlation_analyzer.get_prompt_section(
                        symbol, symbol_prices, btc_prices
                    )
                    if corr_section:
                        lines.append(corr_section)
                        lines.append("")

            # 12. Fibonacci Levels (NEW)
            if self._fibonacci_analyzer and hist_primary.get("high") and hist_primary.get("low"):
                highs = hist_primary["high"]
                lows = hist_primary["low"]
                closes = hist_primary.get("close", [])
                if len(highs) >= 20 and len(lows) >= 20:
                    fib_section = self._fibonacci_analyzer.get_prompt_section(
                        highs, lows, closes, current_price
                    )
                    if fib_section:
                        lines.append(fib_section)
                        lines.append("")

            # 13. Candlestick Patterns (NEW)
            if self._candlestick_analyzer and hist_primary.get("open"):
                raw_opens = hist_primary["open"]
                raw_highs = hist_primary.get("high", [])
                raw_lows = hist_primary.get("low", [])
                raw_closes = hist_primary.get("close", [])

                # Filter out None values - only use indices where ALL OHLC values exist
                min_len = min(len(raw_opens), len(raw_highs), len(raw_lows), len(raw_closes))
                valid_candles = []
                for i in range(min_len):
                    o, h, l, c = raw_opens[i], raw_highs[i], raw_lows[i], raw_closes[i]
                    if o is not None and h is not None and l is not None and c is not None:
                        valid_candles.append((float(o), float(h), float(l), float(c)))

                if len(valid_candles) >= 3:
                    opens = [v[0] for v in valid_candles]
                    highs = [v[1] for v in valid_candles]
                    lows = [v[2] for v in valid_candles]
                    closes = [v[3] for v in valid_candles]
                    candle_section = self._candlestick_analyzer.get_prompt_section(
                        opens, highs, lows, closes, self._primary_tf.upper()
                    )
                    if candle_section:
                        lines.append(candle_section)
                        lines.append("")

            # 14. Liquidation Heatmap (NEW)
            if self._liquidation_heatmap_analyzer and futures_data and futures_data.get("current"):
                current_futures = futures_data["current"]
                oi = current_futures.get("open_interest", 0)
                funding = current_futures.get("funding_rate", 0)
                ls_ratio = current_futures.get("long_short_ratio", 1.0)

                # Get price range from historical data
                recent_high = current_price * 1.03
                recent_low = current_price * 0.97
                if hist_primary.get("high") and hist_primary.get("low"):
                    recent_high = max(hist_primary["high"][-20:]) if len(hist_primary["high"]) >= 20 else max(hist_primary["high"])
                    recent_low = min(hist_primary["low"][-20:]) if len(hist_primary["low"]) >= 20 else min(hist_primary["low"])

                if oi > 0:
                    heatmap_result = self._liquidation_heatmap_analyzer.estimate_liquidation_zones(
                        symbol=symbol,
                        current_price=current_price,
                        open_interest=oi,
                        funding_rate=funding,
                        long_short_ratio=ls_ratio,
                        recent_high=recent_high,
                        recent_low=recent_low
                    )
                    heatmap_section = self._liquidation_heatmap_analyzer.get_prompt_section(heatmap_result)
                    if heatmap_section:
                        lines.append(heatmap_section)
                        lines.append("")

            # 15. ENTRY CHECKLIST SUMMARY (Pre-calculated confidence adjustments)
            checklist_parts = []
            net_conf_adj = 0

            # MTF Alignment (calculate from snapshots)
            data_1d = current_snapshots.get("1d", {})
            data_4h = current_snapshots.get("4h", {})
            data_1h = current_snapshots.get("1h", {})

            def _get_trend(close, ema20, ema50=None):
                if not close or not ema20:
                    return "N/A"
                if close > ema20:
                    return "BULL"
                elif close < ema20:
                    return "BEAR"
                return "NEUT"

            trends = {
                "1D": _get_trend(data_1d.get("close"), data_1d.get("ema_20"), data_1d.get("ema_50")),
                "4H": _get_trend(data_4h.get("close"), data_4h.get("ema_20"), data_4h.get("ema_50")),
                "1H": _get_trend(data_1h.get("close"), data_1h.get("ema_20")),
            }
            bullish = sum(1 for t in trends.values() if t == "BULL")
            bearish = sum(1 for t in trends.values() if t == "BEAR")
            aligned = max(bullish, bearish)

            if aligned == 3:
                tf_adj = "+10"
                net_conf_adj += 10
            elif aligned == 2:
                tf_adj = "0"
            else:
                tf_adj = "-20"
                net_conf_adj -= 20
            checklist_parts.append(f"TF:{aligned}/3({tf_adj})")

            # Volume Ratio (from earlier calculation if available)
            if hist_primary.get("volume") and self._volume_analyzer:
                _, vol_ratio = self._volume_analyzer.detect_volume_spike(hist_primary["volume"])
                if vol_ratio > 0.7:
                    vol_adj = "+5"
                    net_conf_adj += 5
                elif vol_ratio < 0.3:
                    vol_adj = "-10"
                    net_conf_adj -= 10
                else:
                    vol_adj = "0"
                checklist_parts.append(f"Vol:{vol_ratio:.1f}x({vol_adj})")

            # S/R Distance
            if self._entry_analyzer:
                sr = self._entry_analyzer.calculate_sr_distances(historical_arrays, current_price)
                sr_dist = min(sr.get("support_dist_pct", 100), sr.get("resistance_dist_pct", 100))
                if sr_dist > 1.0:
                    sr_adj = "OK"
                elif sr_dist > 0.5:
                    sr_adj = "-5"
                    net_conf_adj -= 5
                else:
                    sr_adj = "-10"
                    net_conf_adj -= 10
                checklist_parts.append(f"S/R:{sr_dist:.1f}%({sr_adj})")

            # ADX Cap
            adx_val = data_4h.get("adx_14", 25)
            if adx_val < 15:
                adx_note = "cap50"
            elif adx_val < 20:
                adx_note = "cap70"
            else:
                adx_note = "OK"
            checklist_parts.append(f"ADX:{adx_val:.0f}({adx_note})")

            # Net adjustment
            net_str = f"+{net_conf_adj}" if net_conf_adj >= 0 else str(net_conf_adj)
            lines.append(f"ENTRY CHECKLIST: {' | '.join(checklist_parts)} → Net: {net_str} conf")
            lines.append("")

        except Exception as e:
            logger.warning("Error building enhanced features section: %s", e)
            lines.append(f"[Enhanced features error: {str(e)[:50]}]")
            lines.append("")

        return "\n".join(lines)

    def build_tp_hold_summary(
        self,
        symbol: str,
        position_data: Dict,
        current_price: float,
        historical_arrays: Dict,
        atr_pct: Optional[float] = None,
        volatility: Optional[float] = None,
    ) -> str:
        """
        Build compact TP/Hold summary for prompt (~50-80 tokens).

        Format: TP: BALANCED | TP1✅ TP2⏳ | BE✅ TRAIL❌ | HOLD: 78% | 12.5h
        
        Args:
            symbol: Trading symbol
            position_data: Position data including entry time, price, side, etc.
            current_price: Current market price
            historical_arrays: Historical data arrays by timeframe
            atr_pct: ATR percentage (optional, will use position_data if available)
            volatility: Volatility value (optional, will use position_data if available)
            
        Returns:
            Formatted TP/Hold summary string
        """
        if not self._time_exit_manager or not self._hold_engine:
            return ""
            
        try:
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
            if tp_plan and self._partial_tp_manager and hasattr(self._partial_tp_manager, 'get_compact_summary'):
                tp_summary = self._partial_tp_manager.get_compact_summary(tp_plan)
                if tp_summary:
                    parts.append(tp_summary)

            if parts:
                return "POSITION MGT: " + " | ".join(parts)
            return ""

        except Exception as e:
            logger.debug("Error building TP/Hold summary: %s", e)
            return ""
