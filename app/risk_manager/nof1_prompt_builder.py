"""
NOF1.AI Style Prompt Builder for GLM
Builds prompts in the exact format used by professional trading systems
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.risk_manager.advanced_parser import AdvancedInvalidationParser
from app.risk_manager.dynamic_risk_manager import DynamicRiskManager


class Nof1PromptBuilder:
    """Builds NOF1.AI style prompts with full market context"""

    def __init__(self):
        self._start_time = datetime.utcnow()
        self._invocation_count = 0

    def validate_exit_plan(
        self, exit_plan: dict, position_side: str, entry_price: float
    ) -> Tuple[bool, str]:
        """
        Exit planını doğrular ve mantıksal çatışmaları kontrol eder

        Args:
            exit_plan: GLM'den gelen exit planı
            position_side: "LONG" veya "SHORT"
            entry_price: Giriş fiyatı

        Returns:
            (is_valid, error_message)
        """
        if not exit_plan:
            return False, "Exit plan eksik"

        profit_target = exit_plan.get("profit_target", 0.0)
        stop_loss = exit_plan.get("stop_loss", 0.0)
        invalidation_condition = exit_plan.get("invalidation_condition", "")

        # Değerlerin geçerliliğini kontrol et
        if profit_target <= 0.0 or stop_loss <= 0.0:
            return False, "Profit target ve stop loss 0'dan büyük olmalı"

        # Invalidation condition parse et
        direction, invalidation_price = self._parse_invalidation_condition(invalidation_condition)

        # Mantıksal doğruluk kontrolü
        if position_side == "LONG":
            # LONG için: stop_loss < profit_target ve entry ORTADA
            if not (stop_loss < entry_price < profit_target):
                return (
                    False,
                    f"LONG için stop_loss({stop_loss}) < entry({entry_price}) < profit_target({profit_target}) olmalı",
                )

            # Invalidation condition kontrolü - LONG: Entry > Invalidation > Stop Loss
            if direction and invalidation_price:
                if not (entry_price > invalidation_price > stop_loss):
                    return (
                        False,
                        f"LONG için Entry({entry_price}) > Invalidation({invalidation_price}) > SL({stop_loss}) olmalı (erken uyarı mantığı)",
                    )
        else:  # SHORT
            # SHORT için: profit_target < entry < stop_loss
            if not (profit_target < entry_price < stop_loss):
                return (
                    False,
                    f"SHORT için profit_target({profit_target}) < entry({entry_price}) < stop_loss({stop_loss}) olmalı",
                )

            # Invalidation condition kontrolü - SHORT: Stop Loss > Invalidation > Entry
            if direction and invalidation_price:
                if not (stop_loss > invalidation_price > entry_price):
                    return (
                        False,
                        f"SHORT için SL({stop_loss}) > Invalidation({invalidation_price}) > Entry({entry_price}) olmalı (erken uyarı mantığı)",
                    )

        return True, ""

    def _parse_invalidation_condition(
        self, invalidation_text: str
    ) -> Tuple[Optional[str], Optional[float]]:
        """
        Invalidation condition string'ini parse et

        Args:
            invalidation_text: GLM'den gelen invalidation condition

        Returns:
            (direction, price) tuple veya (None, None) if parse failed
        """
        if not invalidation_text or invalidation_text == "N/A":
            return None, None

        try:
            import re

            # Önceden derlenmiş regex desenleri kullan (optimize edilmiş)
            patterns = [
                # İngilizce desenler
                (
                    r"closes?\s+(below|above)\s+([\d,.]+)",
                    lambda m: (m.group(1).lower(), float(m.group(2).replace(",", ""))),
                ),
                # Türkçe desenler
                (
                    r"fiyat\s+([\d,.]+)\s+seviyesinin\s+(altında|üstünde|üzerinde)",
                    lambda m: (
                        "below" if m.group(2).lower() == "altında" else "above",
                        float(m.group(1).replace(",", "").replace(".", "")),
                    ),
                ),
                (
                    r"([\d,.]+)\s+(altında|üstünde|üzerinde)",
                    lambda m: (
                        "below" if m.group(2).lower() == "altında" else "above",
                        float(m.group(1).replace(",", "").replace(".", "")),
                    ),
                ),
                (
                    r"(altında|üstünde|üzerinde)\s+([\d,.]+)",
                    lambda m: (
                        "below" if m.group(1).lower() == "altında" else "above",
                        float(m.group(2).replace(",", "").replace(".", "")),
                    ),
                ),
            ]

            for pattern, processor in patterns:
                match = re.search(pattern, invalidation_text, re.IGNORECASE)
                if match:
                    direction, price = processor(match)
                    return direction, price

            return None, None

        except Exception:
            return None, None

    def build_prompt(
        self,
        raw_market_data: Dict[str, Any],
        portfolio_metrics: Dict[str, Any],
        htf_analysis: Dict[str, Any] = None,
    ) -> str:
        """
        Build comprehensive NOF1.AI style prompt

        Args:
            raw_market_data: All timeframe data from PureDataCollector
            portfolio_metrics: Current portfolio state
            htf_analysis: Higher timeframe support/resistance

        Returns:
            Formatted prompt string
        """
        self._invocation_count += 1

        # Calculate runtime
        runtime_minutes = int((datetime.utcnow() - self._start_time).total_seconds() / 60)
        current_time = datetime.utcnow()

        # Extract data
        symbol = raw_market_data.get("symbol", "BTCUSDT")
        current_snapshots = raw_market_data.get("current_snapshots", {})
        historical_arrays = raw_market_data.get("historical_arrays", {})
        futures_data = raw_market_data.get("futures_data", {})

        # Build prompt sections
        sections = []

        # === HEADER ===
        sections.append(self._build_header(runtime_minutes, current_time))

        # === CURRENT MARKET STATE ===
        sections.append(
            self._build_market_state(symbol, current_snapshots, historical_arrays, futures_data)
        )

        # === ACCOUNT INFORMATION ===
        sections.append(self._build_account_info(portfolio_metrics))

        # === TRADING INSTRUCTIONS ===
        sections.append(self._build_instructions())

        return "\n\n".join(sections)

    def _build_header(self, runtime_minutes: int, current_time: datetime) -> str:
        """Build header with runtime info"""
        return f"""It has been {runtime_minutes} minutes since you started trading. The current time is {current_time} and you've been invoked {self._invocation_count} times. Below, we are providing you with a variety of state data, price data, and predictive signals so you can discover alpha. Below that is your current account information, value, performance, positions, etc.

ALL OF THE PRICE OR SIGNAL DATA BELOW IS ORDERED: OLDEST → NEWEST

Timeframes note: Unless stated otherwise in a section title, the primary timeframe is 30-minute intervals. Additional timeframes (1m, 5m, 15m, 1h, 4h, 1d) are provided for comprehensive analysis."""

    def _build_market_state(
        self,
        symbol: str,
        current_snapshots: Dict[str, Dict],
        historical_arrays: Dict[str, Dict],
        futures_data: Dict[str, any],
    ) -> str:
        """Build complete market state section"""

        lines = [
            "=" * 80,
            f"CURRENT MARKET STATE FOR {symbol}",
            "=" * 80,
        ]

        # Primary 30m data
        data_30m = current_snapshots.get("30m", {})
        hist_30m = historical_arrays.get("30m", {})

        if data_30m:
            lines.extend(
                [
                    "",
                    f"PRIMARY TIMEFRAME (30-minute)",
                    f"current_price = {data_30m.get('close', 0):.2f}",
                    f"current_ema20 = {data_30m.get('ema_20', 0):.2f}",
                    f"current_ema50 = {data_30m.get('ema_50', 0):.2f}",
                    f"current_macd = {data_30m.get('macd', 0):.2f}",
                    f"current_rsi (14-period) = {data_30m.get('rsi_14', 50):.2f}",
                ]
            )

            # Intraday series (30m)
            if hist_30m:
                lines.extend(
                    [
                        "",
                        "30-minute series (oldest → latest):",
                        "",
                    ]
                )

                # Close prices
                if "close" in hist_30m:
                    prices = hist_30m["close"][-10:]  # Last 10
                    lines.append(f"Mid prices: {self._format_array(prices, 2)}")

                # EMA 20
                if "ema_20" in hist_30m:
                    ema20 = hist_30m["ema_20"][-10:]
                    lines.append(f"EMA indicators (20-period): {self._format_array(ema20, 2)}")

                # MACD
                if "macd" in hist_30m:
                    macd = hist_30m["macd"][-10:]
                    lines.append(f"MACD indicators: {self._format_array(macd, 2)}")

                # RSI 14
                if "rsi_14" in hist_30m:
                    rsi14 = hist_30m["rsi_14"][-10:]
                    lines.append(f"RSI indicators (14-Period): {self._format_array(rsi14, 2)}")

        # FUTURES MARKET DATA (Funding Rate, Open Interest, Long/Short Ratio)
        if futures_data and futures_data.get("current"):
            lines.extend(
                [
                    "",
                    "=" * 80,
                    "FUTURES MARKET DATA:",
                    "",
                ]
            )

            current_futures = futures_data.get("current", {})
            avg_futures = futures_data.get("averages", {})

            funding_rate = current_futures.get("funding_rate", 0)
            open_interest = current_futures.get("open_interest", 0)
            long_short_ratio = current_futures.get("long_short_ratio", 0)

            lines.extend(
                [
                    f"Funding Rate: {funding_rate:.8f}" if funding_rate else "Funding Rate: N/A",
                    f"  (8-hour average: {avg_futures.get('funding_rate_avg', 0):.8f})",
                    "",
                    (
                        f"Open Interest: {open_interest:.2f}"
                        if open_interest
                        else "Open Interest: N/A"
                    ),
                    f"  (20-period average: {avg_futures.get('open_interest_avg', 0):.2f})",
                    "",
                    (
                        f"Long/Short Ratio: {long_short_ratio:.4f}"
                        if long_short_ratio
                        else "Long/Short Ratio: N/A"
                    ),
                    f"  (20-period average: {avg_futures.get('long_short_ratio_avg', 0):.4f})",
                    "",
                ]
            )

            # Add historical futures data if available
            hist_futures = futures_data.get("historical", {})
            if hist_futures:
                lines.extend(
                    [
                        "",
                        "FUTURES HISTORICAL DATA (20 periods):",
                        "",
                    ]
                )

                if "funding_rate" in hist_futures:
                    fr_hist = hist_futures["funding_rate"][-20:]
                    lines.append(
                        f"Funding Rate history (20 periods): {self._format_array(fr_hist, 8)}"
                    )

                if "open_interest" in hist_futures:
                    oi_hist = hist_futures["open_interest"][-20:]
                    lines.append(
                        f"Open Interest history (20 periods): {self._format_array(oi_hist, 2)}"
                    )

                if "long_short_ratio" in hist_futures:
                    lsr_hist = hist_futures["long_short_ratio"][-20:]
                    lines.append(
                        f"Long/Short Ratio history (20 periods): {self._format_array(lsr_hist, 4)}"
                    )
            else:
                lines.extend(
                    [
                        "",
                        "FUTURES HISTORICAL DATA: No historical data available",
                        "",
                    ]
                )

        # 1-minute intraday data
        data_1m = current_snapshots.get("1m", {})
        hist_1m = historical_arrays.get("1m", {})

        if hist_1m:
            lines.extend(
                [
                    "",
                    "=" * 80,
                    "INTRADAY SERIES (1-minute, oldest → latest):",
                    "",
                ]
            )

            if "close" in hist_1m:
                prices = hist_1m["close"][-20:]  # Last 20
                lines.append(f"Mid prices: {self._format_array(prices, 2)}")

            if "rsi_14" in hist_1m:
                rsi = hist_1m["rsi_14"][-20:]
                lines.append(f"RSI indicators (14-Period): {self._format_array(rsi, 2)}")

            if "macd" in hist_1m:
                macd = hist_1m["macd"][-20:]
                lines.append(f"MACD indicators: {self._format_array(macd, 2)}")

        # 4-hour longer-term context
        data_4h = current_snapshots.get("4h", {})
        hist_4h = historical_arrays.get("4h", {})

        if data_4h and hist_4h:
            lines.extend(
                [
                    "",
                    "=" * 80,
                    "LONGER-TERM CONTEXT (4-hour timeframe):",
                    "",
                ]
            )

            lines.extend(
                [
                    f"20-Period EMA: {data_4h.get('ema_20', 0):.2f} vs. 50-Period EMA: {data_4h.get('ema_50', 0):.2f}",
                    f"14-Period ATR: {data_4h.get('atr_14', 0):.2f}",
                    f"Current Volume: {data_4h.get('volume', 0):.2f}",
                ]
            )

            # 4h series
            if "macd" in hist_4h:
                macd_4h = hist_4h["macd"][-10:]
                lines.append(f"MACD indicators: {self._format_array(macd_4h, 2)}")

            if "rsi_14" in hist_4h:
                rsi_4h = hist_4h["rsi_14"][-10:]
                lines.append(f"RSI indicators (14-Period): {self._format_array(rsi_4h, 2)}")

        # Additional timeframes with full data
        lines.extend(
            [
                "",
                "=" * 80,
                "ADDITIONAL TIMEFRAMES AVAILABLE:",
                "",
            ]
        )

        for tf in ["5m", "15m", "1h", "1d"]:
            data = current_snapshots.get(tf, {})
            hist_data = historical_arrays.get(tf, {})

            if data:
                lines.extend(
                    [
                        f"",
                        f"{tf.upper()} TIMEFRAME:",
                        f"Current Price: {data.get('close', 0):.2f}",
                        f"Current EMA 20: {data.get('ema_20', 0):.2f}",
                        f"Current EMA 50: {data.get('ema_50', 0):.2f}",
                        f"Current MACD: {data.get('macd', 0):.2f}",
                        f"Current RSI (14): {data.get('rsi_14', 50):.2f}",
                        f"Current Volume: {data.get('volume', 0):.2f}",
                    ]
                )

                # Add historical series if available
                if hist_data:
                    lines.extend([f"", f"{tf.upper()} Historical Series (latest 10):"])

                    if "close" in hist_data:
                        prices = hist_data["close"][-10:]
                        lines.append(f"  Close prices: {self._format_array(prices, 2)}")

                    if "ema_20" in hist_data:
                        ema20 = hist_data["ema_20"][-10:]
                        lines.append(f"  EMA 20: {self._format_array(ema20, 2)}")

                    if "ema_50" in hist_data:
                        ema50 = hist_data["ema_50"][-10:]
                        lines.append(f"  EMA 50: {self._format_array(ema50, 2)}")

                    if "macd" in hist_data:
                        macd = hist_data["macd"][-10:]
                        lines.append(f"  MACD: {self._format_array(macd, 2)}")

                    if "rsi_14" in hist_data:
                        rsi = hist_data["rsi_14"][-10:]
                        lines.append(f"  RSI 14: {self._format_array(rsi, 2)}")

                    if "volume" in hist_data:
                        volume = hist_data["volume"][-10:]
                        lines.append(f"  Volume: {self._format_array(volume, 2)}")
            else:
                lines.append(f"  {tf}: No data available")

        return "\n".join(lines)

    def _build_account_info(self, portfolio_metrics: Dict[str, Any]) -> str:
        """Build account information section with multi-position and fee support"""

        lines = [
            "=" * 80,
            "HERE IS YOUR ACCOUNT INFORMATION & PERFORMANCE",
            "=" * 80,
            "",
        ]

        # CHECK FOR RECENT POSITION CLOSE NOTIFICATIONS
        close_notification = self._check_position_close_notification()
        if close_notification:
            lines.extend(self._format_close_notification(close_notification))
            lines.append("")

        # Account metrics
        equity = portfolio_metrics.get("equity", 10000)
        initial_capital = 10000  # From settings
        total_return_pct = ((equity - initial_capital) / initial_capital) * 100

        lines.extend(
            [
                f"Current Total Return (percent): {total_return_pct:.2f}%",
                f"Available Cash: {portfolio_metrics.get('available_cash', equity):.2f}",
                f"Current Account Value: {equity:.2f}",
                "",
            ]
        )

        # YENİ: Multi-position support
        long_position = portfolio_metrics.get("long_position", 0.0)
        short_position = portfolio_metrics.get("short_position", 0.0)
        net_position = portfolio_metrics.get("net_position", portfolio_metrics.get("position", 0.0))

        has_long = abs(long_position) > 0.0001
        has_short = abs(short_position) > 0.0001

        # Fee bilgileri
        current_price = portfolio_metrics.get("current_price", 0)
        last_trade_price = portfolio_metrics.get("last_trade_price")
        last_trade_timestamp = portfolio_metrics.get("last_trade_timestamp")
        taker_fee_pct = 0.05  # Binance USDT-M Futures taker fee

        # Fee cost örnek hesaplama
        example_position_usd = 50000  # $50k notional için örnek
        example_fee = example_position_usd * (taker_fee_pct / 100)

        lines.extend(
            [
                "=" * 80,
                "FEE COSTS & TRADING LIMITS",
                "=" * 80,
                "",
                f"Taker Fee: {taker_fee_pct}% (per trade)",
                f"Example fee for $50,000 position: ${example_fee:.2f}",
                f"Round-trip cost (open + close): ${example_fee * 2:.2f}",
                "",
                "LIMITS:",
                "  • Maximum margin per position: $3,000",
                "  • Maximum leverage: 20x",
                "  • Maximum position per side: 1.0 BTC",
                "",
            ]
        )

        # Fiyat değişimi kontrolü
        if last_trade_price and current_price:
            price_change_pct = abs((current_price - last_trade_price) / last_trade_price) * 100
            price_change_status = "✓ OK" if price_change_pct >= 3.0 else "✗ BLOCKED"
            allowed_status = "Allowed" if price_change_pct >= 3.0 else "Will be blocked"

            lines.extend(
                [
                    "=" * 80,
                    "PRICE CHANGE AWARENESS (Fee Optimization)",
                    "=" * 80,
                    "",
                    f"Last trade price: ${last_trade_price:.2f}",
                    f"Current price: ${current_price:.2f}",
                    f"Price change: {price_change_pct:.2f}%",
                    f"System threshold status: {price_change_status}",
                    "",
                    "💡 FEE OPTIMIZATION GUIDELINE (Not a requirement for YOUR decision):",
                    "",
                    "  ℹ️ System Behavior:",
                    "  • If price change <3%: System will BLOCK your trade",
                    "  • If price change ≥3%: System will ALLOW your trade",
                    f"  • Current: {price_change_status} ({allowed_status})",
                    "",
                    "  💭 Your Decision Process:",
                    "  • YOU are not forced to trade just because price moved 3%+",
                    "  • YOU can choose HOLD even at 5%, 10%, or any price change",
                    "  • This 3% rule is for system's fee protection, not your strategy",
                    "  • Analyze market conditions and decide what's best",
                    "",
                    "  💰 Fee Context:",
                    "  • Round-trip fee: 0.10% (open + close)",
                    "  • Trading at small moves (<3%) increases fee/profit ratio",
                    "  • Larger moves (3%+) have better fee/profit efficiency",
                    "",
                ]
            )

        # Current positions (MULTI-POSITION SUPPORT)
        if has_long or has_short:
            lines.extend(
                [
                    "=" * 80,
                    "CURRENT LIVE POSITIONS",
                    "=" * 80,
                    "",
                ]
            )

            if has_long:
                long_entry = portfolio_metrics.get("long_avg_price", 0)
                long_pnl = ((current_price - long_entry) / long_entry) * 100 if long_entry else 0
                long_pnl_usd = (current_price - long_entry) * long_position if long_entry else 0
                long_leverage = portfolio_metrics.get("long_leverage", 1)
                long_exit_plan = portfolio_metrics.get("long_exit_plan")

                # Entry fee (ödenmiş)
                long_notional = long_position * long_entry
                long_entry_fee = long_notional * (taker_fee_pct / 100)

                lines.extend(
                    [
                        "📈 LONG POSITION:",
                        f"  {{",
                        f"    'symbol': 'BTCUSDT',",
                        f"    'position_type': 'LONG',",
                        f"    'quantity': {long_position:.6f} BTC,",
                        f"    'entry_price': ${long_entry:.2f},",
                        f"    'current_price': ${current_price:.2f},",
                        f"    'unrealized_pnl': ${long_pnl_usd:.2f} ({long_pnl:.2f}%),",
                        f"    'leverage': {long_leverage}x,",
                        f"    'entry_fee_paid': ${long_entry_fee:.2f},",
                    ]
                )

                if long_exit_plan:
                    pt = long_exit_plan.get("profit_target", 0)
                    sl = long_exit_plan.get("stop_loss", 0)
                    inv = long_exit_plan.get("invalidation_condition", "N/A")
                    lines.extend(
                        [
                            f"    'exit_plan': {{",
                            f"      'profit_target': ${pt:.2f},",
                            f"      'stop_loss': ${sl:.2f},",
                            f"      'invalidation': '{inv}'",
                            f"    }}",
                        ]
                    )

                lines.extend(
                    [
                        f"  }}",
                        "",
                    ]
                )

            if has_short:
                short_entry = portfolio_metrics.get("short_avg_price", 0)
                short_pnl = (
                    ((short_entry - current_price) / short_entry) * 100 if short_entry else 0
                )
                short_pnl_usd = (
                    (short_entry - current_price) * abs(short_position) if short_entry else 0
                )
                short_leverage = portfolio_metrics.get("short_leverage", 1)
                short_exit_plan = portfolio_metrics.get("short_exit_plan")

                # Entry fee (ödenmiş)
                short_notional = abs(short_position) * short_entry
                short_entry_fee = short_notional * (taker_fee_pct / 100)

                lines.extend(
                    [
                        "📉 SHORT POSITION:",
                        f"  {{",
                        f"    'symbol': 'BTCUSDT',",
                        f"    'position_type': 'SHORT',",
                        f"    'quantity': {abs(short_position):.6f} BTC,",
                        f"    'entry_price': ${short_entry:.2f},",
                        f"    'current_price': ${current_price:.2f},",
                        f"    'unrealized_pnl': ${short_pnl_usd:.2f} ({short_pnl:.2f}%),",
                        f"    'leverage': {short_leverage}x,",
                        f"    'entry_fee_paid': ${short_entry_fee:.2f},",
                    ]
                )

                if short_exit_plan:
                    pt = short_exit_plan.get("profit_target", 0)
                    sl = short_exit_plan.get("stop_loss", 0)
                    inv = short_exit_plan.get("invalidation_condition", "N/A")
                    lines.extend(
                        [
                            f"    'exit_plan': {{",
                            f"      'profit_target': ${pt:.2f},",
                            f"      'stop_loss': ${sl:.2f},",
                            f"      'invalidation': '{inv}'",
                            f"    }}",
                        ]
                    )

                lines.extend(
                    [
                        f"  }}",
                        "",
                    ]
                )

            # Net exposure
            lines.extend(
                [
                    f"NET POSITION: {net_position:.6f} BTC",
                    f"  • Long: {long_position:.6f} BTC",
                    f"  • Short: {short_position:.6f} BTC",
                    "",
                ]
            )

            # HEDGE RULES
            lines.extend(
                [
                    "=" * 80,
                    "HEDGE STRATEGY RULES",
                    "=" * 80,
                    "",
                    "✅ YOU CAN:",
                    "  • Open LONG + SHORT simultaneously (hedge position)",
                    "  • Example: LONG 0.5 BTC + SHORT 0.3 BTC = 0.2 BTC net long exposure",
                    "",
                    "❌ YOU CANNOT:",
                    "  • Open LONG + LONG (duplicate same-direction position)",
                    "  • Open SHORT + SHORT (duplicate same-direction position)",
                    "",
                    f"CURRENT STATE:",
                    f"  • Long: {'OPEN (%.6f BTC)' % long_position if has_long else 'NONE'}",
                    f"  • Short: {'OPEN (%.6f BTC)' % abs(short_position) if has_short else 'NONE'}",
                    "",
                    "STRATEGY EXAMPLES:",
                    "  1. Market Neutral Hedge:",
                    "     → Open LONG 0.5 BTC + SHORT 0.5 BTC (net: 0 BTC)",
                    "     → Profit from funding rates or volatility",
                    "",
                    "  2. Partial Hedge:",
                    "     → Long açıkken düşüş riski var → SHORT 0.3 BTC ekle (risk azalt)",
                    "",
                    "  3. Directional with Protection:",
                    "     → Bullish ama hedge için SHORT 0.2 BTC + LONG 0.8 BTC",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "Current live positions: NONE (FLAT)",
                    "",
                    "=" * 80,
                    "HEDGE STRATEGY (AVAILABLE)",
                    "=" * 80,
                    "",
                    "Since you have NO positions, you can:",
                    "  1. Open LONG if bullish",
                    "  2. Open SHORT if bearish",
                    "  3. Open both LONG + SHORT for neutral/hedge strategy",
                    "",
                ]
            )

        # Sharpe ratio (if available)
        sharpe = portfolio_metrics.get("sharpe_ratio", 0)
        if sharpe:
            lines.append(f"Sharpe Ratio: {sharpe:.3f}")

        return "\n".join(lines)

    def _build_instructions(self) -> str:
        """Build trading instructions section"""

        return """
================================================================================
YOUR TASK
================================================================================

Analyze market data and your current position. Decide on ONE of these actions:

1. **HOLD** - Keep current position (if you have one) or stay flat
2. **BUY** - Enter a new LONG position (only if FLAT)
3. **SELL** - Enter a new SHORT position (only if FLAT)

⚠️ **IMPORTANT: DO NOT USE CLOSE ACTION!**
- Positions are AUTOMATICALLY closed by system when:
  1. Profit target is reached
  2. Stop loss is triggered  
  3. Invalidation condition occurs
- Your exit_plan (profit_target, stop_loss, invalidation_condition) controls when to close
- You can ONLY choose: BUY, SELL, or HOLD

🎯 TRADING STRATEGY: QUALITY OVER QUANTITY

⚠️ CRITICAL: Be SELECTIVE! Don't force trades!

Your goal is to MAXIMIZE LONG-TERM PROFIT through HIGH-QUALITY trades, not trade frequency.
Better to wait patiently for strong setups than to overtrade on weak signals!

================================================================================
⚠️ MANDATORY RULE: MINIMUM CONFIDENCE THRESHOLD
================================================================================

✅ You can ONLY open positions (BUY/SELL) if your confidence is ≥ 0.80 (80%)
❌ If confidence < 80% → You MUST choose HOLD instead

WHY?
- Low confidence = weak/unclear signals = HIGH RISK of loss
- High confidence = strong confirmed signals = BETTER win probability
- Patience pays: waiting for quality setups beats overtrading!

EXAMPLES:

✅ Confidence 0.80 (80%) → OK to trade, meets minimum threshold
✅ Confidence 0.85 (85%) → Excellent, high-quality setup!
❌ Confidence 0.78 (78%) → HOLD! Not confident enough, wait for clarity
❌ Confidence 0.68 (68%) → HOLD! Very weak signals, definitely don't trade

================================================================================
🎯 NOF1.AI DEEPSEEK STYLE: HOLDING STEADY & LET YOUR WINNERS RUN
================================================================================

ÖNCELİKLİ İLKELER:

1. **Pozisyonları sabit tut, küçük dalgalanmalarda asla panik yapma!**
2. **KAZANAN POZİSYONLARI ERKEN KAPATMA!** Kâr hedefine ulaşana kadar sabret.
3. Kâr hedefine, stop-loss seviyesine veya geçersiz kılma koşuluna ulaşılana kadar pozisyonu KAPATMA!
4. Küçük fiyat değişimleri (günlük %3-5 arası) ve küçük kâr geri çekilmeleri pozisyon değişikliği için yeterli DEĞİLDİR!

⚠️ KARARLI KALMA KURALI (ZARAR DURUMUNDA):
- Mevcut pozisyon küçük negatif PnL'de (< %5) ama stop-loss tetiklenmemişse → MUTLAKA HOLD.

⚠️ KAZANAN POZİSYONLARI BÜYÜT PRENSİBİ (KÂR DURUMUNDA):
- Mevcut pozisyon kârlıysa ve kâr hedefine henüz ulaşılmadıysa → KESİNLİKLE HOLD.
- Kârın bir kısmının geri gelmesi normaldir. Bu, trendin değiştiği anlamına gelmez.
- Kârlı bir pozisyonu erken kapatmak için tek geçerli neden: 30m ve 4h grafiklerinde 
  çok güçlü, net ve teyit edilmiş bir trend değişikliğinin başlaması. 
  Tek bir indikatörün sinyali YETERLİ DEĞİLDİR.

================================================================================
KARAR VERME SÜRECİ (ADIM ADIM)
================================================================================

**ADIM 1: POZİSYON DURUMUNU KONTROL ET**
- Mevcut pozisyon var mı? LONG mu SHORT mu?
- Pozisyonun kâr/zarar durumu nedir? (Pozitif mi, Negatif mi?)

**ADIM 2: ÇIKIŞ PLANINI DEĞERLENDİR (EN ÖNEMLİ ADIM)**
- Kâr hedefine ulaşıldı mı? → Evet ise, sistem otomatik kapatmıştır.
- Stop-loss tetiklendi mi? → Evet ise, sistem otomatik kapatmıştır.
- Geçersiz kılma koşulu gerçekleşti mi? → Evet ise, sistem otomatik kapatmıştır.
- **Eğer hiçbiri gerçekleşmediyse → MUTLAKA HOLD! Diğer adımlara gerek yok.**

**ADIM 3: TEKNİK ANALİZ (SADECE BAĞLAM İÇİN, KARARI DEĞİŞTİRMEZ!)**
- RSI, MACD, EMA göstergelerini kontrol et.
- ⚠️ **ÖNEMLİ**: Teknik göstergeler, çıkış planını GEÇERSİZ KILMAZ!
- **Örnek**: Pozisyon kârlı ve RSI aşırı alımda olsa bile, kâr hedefine ulaşana kadar HOLD!

**ADIM 4: KARAR MANTIĞI**
- Eğer çıkış planı koşulları karşılandıysa → Sistem zaten kapatmıştır.
- Eğer çıkış planı koşulları karşılanmadıysa → **HOLD** (göstergeler ne gösterirse göstersin, 
  pozisyon kârlı da olsa zararlı da olsa).
- Pozisyon yoksa → BUY/SELL ile yeni pozisyon açabilirsin.

**🔥 KRİTİK**: Göstergeler kârlı bir pozisyonu erken kapatmak için bahane DEĞİLDİR! Çıkış planına güven!

================================================================================
GEREKÇE FORMATI
================================================================================

**HOLD (Kârlı Pozisyon İçin)**: Neden kâr hedefine ulaşılmadığını açıkla.
- Örnek: 'BTC SHORT pozisyonu kârlı ve büyümeye bırakılıyor. Mevcut fiyat 102,807, 
  giriş 103,138. Kâr hedefi 101,500'a henüz ulaşılmadı. Mevcut kâr +$337, 
  ancak ana trend hala aşağı yönlü. Kâr hedefine ulaşana kadar pozisyon sabit tutuluyor.'

**HOLD (Zararlı Pozisyon İçin)**: Neden stop-loss tetiklenmediğini açıkla.
- Örnek: 'BTC pozisyonu kararlı tutuluyor. Mevcut fiyat 109967, giriş 107343, 
  kâr/zarar +314.94. Çıkış planı: kâr hedefi 118136 (ulaşılmadı), stop-loss 102026 
  (tetiklenmedi), geçersiz kılma 105000 altı (tetiklenmedi). RSI aşırı satımda (29.7) 
  ama geçersiz kılma koşulu karşılanmadığı için çıkış planına göre tutuluyor.'

================================================================================
WHEN TO HOLD (NOT TRADE)
================================================================================

Choose HOLD when ANY of these is true:
- ❌ Your confidence is below 80% (MANDATORY rule)
- ❌ Market signals are mixed or unclear across timeframes
- ❌ Multiple timeframes show conflicting directions
- ❌ Price is consolidating/ranging without clear breakout
- ❌ No strong technical setup (support/resistance, pattern, trend)
- ❌ You're waiting for better entry opportunity
- ❌ Recent volatility is too high (choppy/unpredictable price action)

✅ REMEMBER:
- PATIENCE is a profitable trading edge!
- Missing a mediocre trade is BETTER than taking a bad trade
- It's PERFECTLY OK to HOLD for multiple cycles until a strong setup appears
- Quality over quantity = long-term profitability!

================================================================================
SAFETY LIMITS (Hard Constraints)
================================================================================

These technical limits still apply:
- ⚠️ Max 3000 USD per single trade (unleveraged notional value)
- ⚠️ Max 20x leverage (minimum 1x)

But now you ALSO have confidence requirement:
- ⚠️ Minimum 80% confidence to open any position

================================================================================
DECISION CHECKLIST
================================================================================

Before opening a position (BUY/SELL), ask yourself:

1. Is my confidence ≥ 80%? (MANDATORY)
   - If NO → Choose HOLD
   
2. Are signals CLEAR and STRONG?
   - Multiple timeframes aligned?
   - Clear technical setup?
   - Strong momentum/trend?
   
3. Is this a HIGH-QUALITY opportunity?
   - Or am I forcing a trade just to "do something"?

If you're unsure → Choose HOLD and wait for better clarity!

================================================================================
⚠️ ENHANCED EXIT PLAN VALIDATION
================================================================================

🔍 **YOUR EXIT PLAN WILL BE VALIDATED BEFORE EXECUTION!**

The system will check your exit plan for:
1. **Logical Consistency**: profit_target, stop_loss, and invalidation_condition must be logically consistent
2. **Price Levels**: All values must be realistic price levels (not 0.0 or null)
3. **Position Type**: Exit levels must match your position type (LONG/SHORT)

🚫 **INVALID EXIT PLANS WILL BE REJECTED AND CONVERTED TO HOLD!**

**VALIDATION RULES:**
- For LONG positions: entry_price < stop_loss < profit_target
- For SHORT positions: profit_target < stop_loss < entry_price
- Invalidation condition must be DIFFERENT from stop_loss level
- Invalidation for LONG: price level BELOW stop_loss
- Invalidation for SHORT: price level ABOVE stop_loss

================================================================================
DYNAMIC RISK MANAGEMENT
================================================================================

📊 **SYSTEM WILL ADJUST YOUR EXIT PLAN BASED ON MARKET CONDITIONS**

The system uses advanced risk management to optimize your exit levels:
- Volatility-based risk sizing
- ATR (Average True Range) calculations
- Dynamic stop-loss and profit-target adjustments
- Market condition analysis

📈 **EXAMPLES OF OPTIMIZED EXIT PLANS:**

For LONG @ $110,000 with medium volatility:
```json
"profit_target": 114000.0,  // +3.6% (volatility-adjusted)
"stop_loss": 107800.0,      // -2.0% (volatility-adjusted)
"invalidation_condition": "If price closes below 107500 on 3-minute candle"  // Below stop_loss
```

For SHORT @ $110,000 with high volatility:
```json
"profit_target": 105500.0,  // -4.1% (volatility-adjusted)
"stop_loss": 113300.0,      // +3.0% (volatility-adjusted)
"invalidation_condition": "If price closes above 113800 on 3-minute candle"  // Above stop_loss
```

================================================================================
OUTPUT FORMAT:
================================================================================

⚠️ CRITICAL: The 'gerekçe' field MUST be written in TURKISH language only!
⚠️ CRITICAL: The 'gerekçe' field should be DETAILED (400-600 characters minimum). Include specific indicator values and reasoning.

Respond with a JSON object in this exact format:

```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "<BUY|SELL|HOLD>",     ⚠️ ONLY these 3 options! NO CLOSE!
      "quantity": <float>,
      "profit_target": <float>,         ⚠️ REQUIRED! Must be a number (price level)
      "stop_loss": <float>,             ⚠️ REQUIRED! Must be a number (price level)
      "invalidation_condition": "<string>",  ⚠️ REQUIRED! Describe the exit condition
      "leverage": <int 1-20>,
      "confidence": <0.0-1.0>,
      "risk_usd": <float>
    },
    "gerekçe": "<your reasoning here - MUST be in TURKISH, 400-600 characters recommended>"
  }
}
```

⚠️⚠️⚠️ EXIT PLAN IS ABSOLUTELY MANDATORY FOR BUY/SELL ⚠️⚠️⚠️

**CRITICAL REQUIREMENT - YOUR TRADE WILL BE REJECTED IF YOU DON'T COMPLY:**

For ALL BUY/SELL actions, you **MUST** provide these three values:

1. **profit_target**: A SPECIFIC PRICE NUMBER (NOT 0.0, NOT null)
2. **stop_loss**: A SPECIFIC PRICE NUMBER (NOT 0.0, NOT null)
3. **invalidation_condition**: A CLEAR CONDITION STRING (NOT empty, NOT "N/A")

❌ **FORBIDDEN VALUES - THESE WILL CAUSE TRADE REJECTION:**
  • profit_target: 0.0 ← WRONG! Your trade will be REJECTED!
  • stop_loss: 0.0 ← WRONG! Your trade will be REJECTED!
  • profit_target: null or undefined ← WRONG! Your trade will be REJECTED!
  • invalidation_condition: "" or "N/A" ← WEAK! Provide specific condition!

⚠️⚠️⚠️ CRITICAL: INVALIDATION AS EARLY WARNING SYSTEM ⚠️⚠️⚠️

**INVALIDATION CONDITION LOGIC:**

Invalidation acts as an **EARLY WARNING** - it should trigger BEFORE stop loss!

**For LONG positions:**
- ✅ Invalidation MUTLAKA Stop Loss'un ÜSTÜNDE olmalı
- ✅ Doğru sıralama: Entry > Invalidation > Stop Loss
- ✅ Örnek: Entry=$110,000, SL=$105,000 → Invalidation=$107,000 ✅
- ❌ YANLIŞ: Entry=$110,000, SL=$105,000 → Invalidation=$104,000 ❌
- Mantık: Invalidation erken uyarı sistemidir, SL'den önce tetiklenmeli
- GLM should choose invalidation based on market conditions and volatility
- Önerilen formül: Invalidation = Entry - (Entry - SL) * 0.4

**For SHORT positions:**
- ✅ Invalidation MUTLAKA Stop Loss'un ALTINDA olmalı
- ✅ Doğru sıralama: Stop Loss > Invalidation > Entry
- ✅ Örnek: Entry=$110,000, SL=$115,000 → Invalidation=$113,000 ✅
- ❌ YANLIŞ: Entry=$110,000, SL=$115,000 → Invalidation=$116,000 ❌
- Mantık: Invalidation erken uyarı sistemidir, SL'den önce tetiklenmeli
- GLM should choose invalidation based on market conditions and volatility
- Önerilen formül: Invalidation = Entry + (SL - Entry) * 0.4

**WHY THIS IS IMPORTANT:**
- Invalidation triggers FIRST (early exit when trend weakens)
- Stop loss is the FINAL safety net (hard protection)
- This creates a two-layer defense: early warning + hard stop

✅ **CORRECT EXAMPLES:**

**For LONG @ $110,000:**
```json
"profit_target": 115000.0,  // +4.5% upside target
"stop_loss": 105000.0,      // -4.5% downside protection (hard stop)
"invalidation_condition": "If price closes below 107000 on 3-minute candle"  // Between entry and SL (early warning)
```
→ Order: SL (105000) < Invalidation (107000) < Entry (110000) < TP (115000) ✅

**For SHORT @ $110,000:**
```json
"profit_target": 105000.0,  // -4.5% downside (profit for SHORT)
"stop_loss": 115000.0,      // +4.5% upside (loss for SHORT, hard stop)
"invalidation_condition": "If price closes above 113000 on 3-minute candle"  // Between entry and SL (early warning)
```
→ Order: TP (105000) < Entry (110000) < Invalidation (113000) < SL (115000) ✅

**AUTOMATIC POSITION CLOSING RULES:**

Your positions will be AUTOMATICALLY CLOSED when any of these 3 conditions are met:

1. **Profit Target Reached:**
   - When price reaches your specified profit_target level, position closes automatically
   - System checks every 3 minutes (on each 3-minute candle close)
   
2. **Stop Loss Triggered:**
   - When price reaches your specified stop_loss level, position closes automatically
   - System checks every 3 minutes (on each 3-minute candle close)
   
3. **Invalidation Condition Met:**
   - When your special condition is met, position closes immediately
   - Example: "If price closes below $105,000 on a 3-minute candle"

Until one of these conditions is met, your position stays open. The system ONLY checks these 3 rules.

⚠️ **REMEMBER:** If you provide invalid exit plan (0.0 values or null), your BUY/SELL trade will be AUTOMATICALLY REJECTED and converted to HOLD!

**For HOLD actions:** Set profit_target: 0.0, stop_loss: 0.0, invalidation_condition: "N/A"

EXAMPLES:

Örnek HOLD yanıtı (Türkçe gerekçe ile):
```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "HOLD",
      "quantity": 0.0,
      "profit_target": 0.0,
      "stop_loss": 0.0,
      "invalidation_condition": "N/A",
      "leverage": 1,
      "confidence": 0.8,
      "risk_usd": 0.0
    },
    "gerekçe": "Piyasa karışık sinyaller gösteriyor, daha iyi yön için beklemek daha mantıklı. (800+ karakter gerekli)"
  }
}
```

Örnek BUY yanıtı (Türkçe gerekçe ile):
```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "BUY",
      "quantity": 0.05,
      "profit_target": 115000.0,
      "stop_loss": 105000.0,
      "invalidation_condition": "If price closes below 105000 on 30m candle",
      "leverage": 10,
      "confidence": 0.75,
      "risk_usd": 500.0
    },
    "gerekçe": "RSI aşırı satım bölgesinde 30 seviyesinde, uzun pozisyon için iyi risk/ödül oranı. (800+ karakter gerekli)"
  }
}
```

Örnek SELL yanıtı (Türkçe gerekçe ile):
```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "SELL",
      "quantity": 0.08,
      "profit_target": 105000.0,
      "stop_loss": 112000.0,
      "invalidation_condition": "If price closes above 111000 on 30m candle",
      "leverage": 8,
      "confidence": 0.80,
      "risk_usd": 600.0
    },
    "gerekçe": "RSI aşırı alım bölgesinde 75 seviyesinde, kısa pozisyon için iyi risk/ödül oranı. (800+ karakter gerekli)"
  }
}
```

IMPORTANT:
- For HOLD: Provide gerekçe explaining why you're holding (400-600 characters recommended)
- For BUY/SELL: Provide full entry plan with exit_plan (400-600 characters recommended)
  - profit_target MUST be a realistic price level where you expect profit
  - stop_loss MUST be a price level that protects against losses
  - invalidation_condition MUST describe scenario when trade is invalid (e.g., "If price closes below X on 3m candle")
- Confidence should reflect your conviction (0.5-1.0 range)
- Risk should be proportional to confidence and account size
- ⚠️ CRITICAL: Gerekçe MUST be written in TURKISH language - no English allowed
- ⚠️ Gerekçe should be DETAILED with specific indicator values from multiple timeframes
- ⚠️ CRITICAL: profit_target, stop_loss, and invalidation_condition are MANDATORY for BUY/SELL - do NOT leave them as 0 or empty!
- ⚠️ NO CLOSE ACTION ALLOWED - positions close automatically via exit_plan!

Think step by step and make your decision based on:
1. Current market state across all timeframes
2. Technical indicators alignment
3. Your existing position (if any) and exit plan
4. Risk/reward ratio
5. Market structure and momentum
"""

    def _format_array(self, values: List[float], decimals: int = 2) -> str:
        """Format array for display"""
        if not values:
            return "[]"
        formatted = [f"{v:.{decimals}f}" for v in values]
        return "[" + ", ".join(formatted) + "]"

    def _check_position_close_notification(self) -> Dict[str, Any] | None:
        """
        Redis'ten position close bildirimini kontrol et

        Returns:
            Notification data dict veya None
        """
        try:
            import json

            from app.utils.redis import get_redis_client

            redis = get_redis_client()
            redis_key = "position_closed:BTCUSDT"  # Hardcoded for now

            notification_json = redis.get(redis_key)

            if notification_json:
                notification_data = json.loads(notification_json)

                # Bildirimi oku ve sil (tek seferlik)
                redis.delete(redis_key)

                from app.utils.logging import get_logger

                logger = get_logger(__name__)
                logger.info(
                    "✅ GLM read position close notification from Redis | trigger=%s",
                    notification_data.get("trigger_type"),
                )

                return notification_data

            return None

        except Exception as exc:
            from app.utils.logging import get_logger

            logger = get_logger(__name__)
            logger.error("Failed to read position close notification from Redis: %s", exc)
            return None

    def _format_close_notification(self, notification: Dict[str, Any]) -> List[str]:
        """
        Position close notification'ı formatla

        Args:
            notification: Redis'ten okunan notification data

        Returns:
            Formatted lines list
        """
        trigger_type = notification.get("trigger_type", "unknown")
        reason = notification.get("reason", "N/A")
        timestamp = notification.get("timestamp", "N/A")
        entry_price = notification.get("entry_price", 0)
        exit_price = notification.get("exit_price", 0)
        pnl = notification.get("pnl", 0)
        pnl_pct = notification.get("pnl_pct", 0)
        position_type = notification.get("position_type", "UNKNOWN")
        quantity = notification.get("quantity", 0)

        # Emoji seçimi
        emoji_map = {"profit_target": "🎯", "stop_loss": "🛑", "invalidation": "⚠️"}
        emoji = emoji_map.get(trigger_type, "🔔")

        lines = [
            "=" * 80,
            f"{emoji} RECENT POSITION CLOSE NOTIFICATION (AUTOMATIC)",
            "=" * 80,
            "",
            f"⏰ Time: {timestamp}",
            f"🎯 Trigger: {trigger_type.upper().replace('_', ' ')}",
            f"📊 Position Type: {position_type}",
            f"📦 Quantity: {quantity:.6f} BTC",
            "",
            f"💵 Entry Price: ${entry_price:,.2f}",
            f"💵 Exit Price: ${exit_price:,.2f}",
            f"💰 PnL: ${pnl:,.2f} ({pnl_pct:+.2f}%)",
            "",
            f"💬 Reason: {reason}",
            "",
            "✅ Position was AUTOMATICALLY CLOSED by exit plan monitor",
            "",
            "🎯 YOUR ROLE:",
            "  • This position was closed automatically (you did NOT close it)",
            "  • Exit plan worked as expected",
            "  • You do NOT need to make a CLOSE decision",
            "  • Focus on analyzing current market for NEW opportunities",
            "  • Decide if market conditions are good for opening a new position",
            "",
        ]

        return lines
