"""
NOF1.AI Style Prompt Builder for GLM
Builds prompts in the exact format used by professional trading systems
"""

from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional
from app.risk_manager.dynamic_risk_manager import DynamicRiskManager
from app.risk_manager.advanced_parser import AdvancedInvalidationParser


class Nof1PromptBuilder:
    """Builds NOF1.AI style prompts with full market context"""

    def __init__(self):
        self._start_time = datetime.utcnow()
        self._invocation_count = 0
        self._advanced_parser = AdvancedInvalidationParser()
        self._risk_manager = DynamicRiskManager()

        self._consecutive_losses = 0
        self._last_trade_side = None
        self._performance_history: List[int] = []  # 1=win, 0=loss
        self._recent_win_rate: float = 0.5

        self._current_volatility: Optional[float] = None  # 0.0-1.0
        self._current_atr: Optional[float] = None
        self._current_atr_pct: Optional[float] = None

        # How much raw series to show if needed
        self._series_tail = 6  # keep tiny to avoid token blowups

        # Volatility→SL width calibration presets (% of entry)
        self._vol_sl_bounds = {
            "low": {
                "min_base": 0.30, "min_atr_mult": 0.35, "min_vol_mult": 1.5,
                "max_atr_mult": 2.0, "max_cap": 6.0
            },
            "medium": {
                "min_base": 0.40, "min_atr_mult": 0.40, "min_vol_mult": 2.0,
                "max_atr_mult": 2.5, "max_cap": 8.0
            },
            "high": {
                "min_base": 0.60, "min_atr_mult": 0.50, "min_vol_mult": 2.5,
                "max_atr_mult": 3.0, "max_cap": 10.0
            },
            "extreme": {
                "min_base": 0.80, "min_atr_mult": 0.60, "min_vol_mult": 3.0,
                "max_atr_mult": 3.5, "max_cap": 12.0
            },
        }

        # Volatility→TP distance calibration presets (% of entry)
        self._vol_tp_bounds = {
            "low": {
                "min_base": 0.60, "min_atr_mult": 0.80, "min_vol_mult": 3.0,
                "max_atr_mult": 5.0, "max_cap": 12.0
            },
            "medium": {
                "min_base": 0.80, "min_atr_mult": 1.00, "min_vol_mult": 3.5,
                "max_atr_mult": 6.0, "max_cap": 18.0
            },
            "high": {
                "min_base": 1.00, "min_atr_mult": 1.20, "min_vol_mult": 4.0,
                "max_atr_mult": 7.0, "max_cap": 25.0
            },
            "extreme": {
                "min_base": 1.20, "min_atr_mult": 1.40, "min_vol_mult": 4.5,
                "max_atr_mult": 8.0, "max_cap": 35.0
            },
        }

        # Volatility→Leverage caps (hard ceiling), will be reduced by performance
        self._vol_leverage_caps = {
            "low": 15,
            "medium": 10,
            "high": 7,
            "extreme": 5,
        }

    # ---------------------------------------------------------------------
    # Regime mapping
    # ---------------------------------------------------------------------
    def _vol_regime_key(self, vol: float) -> str:
        if vol is None:
            vol = 0.5
        if vol < 0.25:
            return "low"
        if vol < 0.5:
            return "medium"
        if vol < 0.75:
            return "high"
        return "extreme"

    def _sl_distance_bounds(self, atr_pct: float, vol: float) -> Tuple[float, float, str]:
        if vol is None:
            vol = 0.5
        if atr_pct is None:
            atr_pct = 0.0

        regime = self._vol_regime_key(vol)
        cfg = self._vol_sl_bounds[regime]

        min_distance_pct = max(
            cfg["min_base"],
            atr_pct * cfg["min_atr_mult"],
            vol * cfg["min_vol_mult"],
        )

        max_distance_pct = atr_pct * cfg["max_atr_mult"] if atr_pct > 0 else cfg["max_cap"]

        min_distance_pct = min(min_distance_pct, cfg["max_cap"] * 0.6)
        max_distance_pct = min(max_distance_pct, cfg["max_cap"])

        return min_distance_pct, max_distance_pct, regime

    def _tp_distance_bounds(self, atr_pct: float, vol: float) -> Tuple[float, float, str]:
        if vol is None:
            vol = 0.5
        if atr_pct is None:
            atr_pct = 0.0

        regime = self._vol_regime_key(vol)
        cfg = self._vol_tp_bounds[regime]

        min_tp_pct = max(
            cfg["min_base"],
            atr_pct * cfg["min_atr_mult"],
            vol * cfg["min_vol_mult"],
        )

        max_tp_pct = atr_pct * cfg["max_atr_mult"] if atr_pct > 0 else cfg["max_cap"]

        min_tp_pct = min(min_tp_pct, cfg["max_cap"] * 0.6)
        max_tp_pct = min(max_tp_pct, cfg["max_cap"])

        return min_tp_pct, max_tp_pct, regime

    def _recommended_leverage_cap(self, vol: float) -> Tuple[int, str]:
        regime = self._vol_regime_key(vol)
        cap = self._vol_leverage_caps[regime]

        if self._consecutive_losses >= 2:
            cap = min(cap, 3)
        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            cap = min(cap, 4)

        cap = max(1, min(cap, 20))
        return cap, regime

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

        # Simple slope on last tail
        if len(tail) >= 2:
            slope = (tail[-1] - tail[0]) / max(1, (len(tail) - 1))
        else:
            slope = 0.0

        # Std on last tail
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
    # Price action patterns
    # ---------------------------------------------------------------------
    def _detect_candle_patterns(self, hist: Dict[str, List[float]], atr_value: float) -> List[str]:
        """
        Detect simple, high-signal price-action patterns on last 2 candles.
        Uses ATR to normalize noise.
        """
        patterns = []
        if not hist or not all(k in hist for k in ("open", "high", "low", "close")):
            return patterns

        o = hist["open"]
        h = hist["high"]
        l = hist["low"]
        c = hist["close"]
        if len(c) < 3:
            return patterns

        def candle(i: int):
            return o[i], h[i], l[i], c[i]

        o1, h1, l1, c1 = candle(-2)
        o2, h2, l2, c2 = candle(-1)

        rng2 = max(1e-9, h2 - l2)
        body2 = abs(c2 - o2)
        upper_wick2 = h2 - max(o2, c2)
        lower_wick2 = min(o2, c2) - l2

        atr = atr_value if atr_value and atr_value > 0 else rng2
        body_ratio = body2 / rng2
        upper_ratio = upper_wick2 / rng2
        lower_ratio = lower_wick2 / rng2

        # Doji: tiny body
        if body_ratio < 0.2 and rng2 > 0.3 * atr:
            patterns.append("Doji (indecision)")

        # Pinbar: long wick, small body
        if lower_ratio > 0.6 and body_ratio < 0.35:
            patterns.append("Bullish Pinbar (long lower wick)")
        if upper_ratio > 0.6 and body_ratio < 0.35:
            patterns.append("Bearish Pinbar (long upper wick)")

        # Engulfing
        bull_engulf = (c1 < o1) and (c2 > o2) and (o2 <= c1) and (c2 >= o1)
        bear_engulf = (c1 > o1) and (c2 < o2) and (o2 >= c1) and (c2 <= o1)
        if bull_engulf:
            patterns.append("Bullish Engulfing")
        if bear_engulf:
            patterns.append("Bearish Engulfing")

        return patterns

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
        """
        Returns list of pivots: (index, "H"/"L", price)
        - window=2 means fractal over 5 bars.
        - min_move_pct filters tiny swings (noise).
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

        # sort by time, then filter alternation + min move
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

            # if same type, keep more extreme
            if ptype == last_type:
                if (ptype == "H" and price > last_price) or (ptype == "L" and price < last_price):
                    filtered[-1] = (idx, ptype, price)
                    last_price = price
                continue

            # enforce min move
            move_pct = abs(price - last_price) / max(1e-9, last_price) * 100
            if move_pct >= min_move_pct:
                filtered.append((idx, ptype, price))
                last_type = ptype
                last_price = price

        return filtered

    def _analyze_market_structure(self, hist_data: Dict[str, List[float]]) -> str:
        """
        Fractal/pivot-based market structure:
        - Detect swing highs/lows
        - Compare last two highs and lows for HH/HL or LH/LL.
        """
        if not hist_data or "high" not in hist_data or "low" not in hist_data:
            return "UNKNOWN"

        highs = hist_data["high"]
        lows = hist_data["low"]

        # Noise filter based on ATR/vol
        atr_pct = self._current_atr_pct or 0.0
        vol = self._current_volatility or 0.5
        # min move: at least 0.25% or 0.3*ATR%
        min_move_pct = max(0.25, atr_pct * 0.3)

        pivots = self._detect_pivots(highs, lows, window=2, min_move_pct=min_move_pct)
        if len(pivots) < 4:
            return "NEUTRAL (Insufficient pivots)"

        # Get last two swing highs and lows
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
    # VALIDATION
    # ---------------------------------------------------------------------
    def validate_exit_plan(self, exit_plan: dict, position_side: str, entry_price: float) -> Tuple[bool, str]:
        if not exit_plan:
            return False, "Exit plan eksik"

        stop_loss = exit_plan.get("stop_loss")
        profit_target = exit_plan.get("profit_target")

        # Backward compatibility
        if profit_target is None:
            tp = exit_plan.get("take_profit")
            if tp is not None:
                profit_target = tp
                exit_plan["profit_target"] = tp
                exit_plan.pop("take_profit", None)

        invalidation_condition = exit_plan.get("invalidation_condition", "")

        if stop_loss is None:
            return False, "Stop loss boş olamaz"
        if profit_target is None or profit_target == 0:
            return False, "Profit target (profit_target) zorunlu ve 0 olamaz"
        if stop_loss <= 0.0:
            return False, "Stop loss 0'dan büyük olmalı"
        if entry_price <= 0.0:
            return False, "Entry price geçersiz"

        atr_pct = self._current_atr_pct if self._current_atr_pct else 0.0
        vol = self._current_volatility if self._current_volatility else 0.5

        # --- SL distance checks ---
        dist_sl_pct = abs(entry_price - stop_loss) / entry_price * 100
        min_sl_pct, max_sl_pct, sl_regime = self._sl_distance_bounds(atr_pct, vol)

        # Fee/spread buffer floor (round-trip fee ~0.10%, safety factor 2 => 0.20%)
        fee_floor_pct = 0.20
        min_sl_pct = max(min_sl_pct, fee_floor_pct)

        if dist_sl_pct < min_sl_pct:
            return False, (
                f"Stop loss too tight ({dist_sl_pct:.2f}%). "
                f"Min required {min_sl_pct:.2f}% for {sl_regime} regime "
                f"(ATR%={atr_pct:.2f}, vol={vol:.2f})."
            )
        if dist_sl_pct > max_sl_pct:
            return False, (
                f"Stop loss too wide ({dist_sl_pct:.2f}%). "
                f"Max allowed {max_sl_pct:.2f}% for {sl_regime} regime "
                f"(ATR%={atr_pct:.2f})."
            )

        # --- TP distance checks ---
        dist_tp_pct = abs(profit_target - entry_price) / entry_price * 100
        min_tp_pct, max_tp_pct, tp_regime = self._tp_distance_bounds(atr_pct, vol)

        if dist_tp_pct < min_tp_pct:
            return False, (
                f"Profit target too close ({dist_tp_pct:.2f}%). "
                f"Min required {min_tp_pct:.2f}% for {tp_regime} regime "
                f"(ATR%={atr_pct:.2f}, vol={vol:.2f})."
            )
        if dist_tp_pct > max_tp_pct:
            return False, (
                f"Profit target too far ({dist_tp_pct:.2f}%). "
                f"Max allowed {max_tp_pct:.2f}% for {tp_regime} regime "
                f"(ATR%={atr_pct:.2f})."
            )

        # Directional logic
        if position_side == "LONG":
            if stop_loss >= entry_price:
                return False, f"LONG için Stop Loss ({stop_loss}) giriş fiyatından ({entry_price}) düşük olmalı"
            if profit_target <= entry_price:
                return False, f"LONG için profit_target ({profit_target}) giriş fiyatının ({entry_price}) üstünde olmalı"

            risk = entry_price - stop_loss
            reward = profit_target - entry_price
            if risk > 0:
                rr_ratio = reward / risk
                min_rr = 2.5
                if self._consecutive_losses >= 2:
                    min_rr = 3.0
                if rr_ratio < min_rr:
                    return False, f"Risk/Reward ratio too low ({rr_ratio:.2f}). Must be at least {min_rr:.1f}:1."

        elif position_side == "SHORT":
            if stop_loss <= entry_price:
                return False, f"SHORT için Stop Loss ({stop_loss}) giriş fiyatından ({entry_price}) yüksek olmalı"
            if profit_target >= entry_price:
                return False, f"SHORT için profit_target ({profit_target}) giriş fiyatının ({entry_price}) altında olmalı"

            risk = stop_loss - entry_price
            reward = entry_price - profit_target
            if risk > 0:
                rr_ratio = reward / risk
                min_rr = 2.5
                if self._consecutive_losses >= 2:
                    min_rr = 3.0
                if rr_ratio < min_rr:
                    return False, f"Risk/Reward ratio too low ({rr_ratio:.2f}). Must be at least {min_rr:.1f}:1."

        # Invalidation condition parse + logic
        if invalidation_condition:
            direction, invalidation_price, time_frame, metadata = self._advanced_parser.parse(invalidation_condition)
            if direction and invalidation_price:
                if position_side == "LONG":
                    if direction == "below":
                        if invalidation_price > entry_price:
                            return False, f"LONG için invalidation({invalidation_price}) giriş fiyatının({entry_price}) altında olmalı"
                        if invalidation_price <= stop_loss:
                            return False, f"LONG için invalidation({invalidation_price}) stop loss({stop_loss}) üstünde olmalı"
                        if not (stop_loss < invalidation_price < entry_price):
                            return False, f"LONG invalidation {invalidation_price} entry({entry_price}) ile SL({stop_loss}) arasında olmalı"
                else:  # SHORT
                    if direction == "above":
                        if invalidation_price < entry_price:
                            return False, f"SHORT için invalidation({invalidation_price}) giriş fiyatının({entry_price}) üstünde olmalı"
                        if invalidation_price >= stop_loss:
                            return False, f"SHORT için invalidation({invalidation_price}) stop loss({stop_loss}) altında olmalı"
                        if not (entry_price < invalidation_price < stop_loss):
                            return False, f"SHORT invalidation {invalidation_price} entry({entry_price}) ile SL({stop_loss}) arasında olmalı"

        return True, ""

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

        current_price = 0.0
        if current_snapshots.get("30m"):
            current_price = current_snapshots["30m"].get("close", 0.0)

        atr_value = 0.0
        volatility_score = 0.5
        hist_30m = historical_arrays.get("30m", {})
        if hist_30m and "close" in hist_30m:
            closes = hist_30m["close"]
            atr_value = self._risk_manager.calculate_atr(closes, period=14)
            volatility_score = self._risk_manager.calculate_volatility(closes)

        self._current_volatility = volatility_score
        self._current_atr = atr_value
        self._current_atr_pct = (atr_value / current_price * 100) if (current_price and atr_value) else None

        self._update_performance_tracking(portfolio_metrics)

        sections = []
        sections.append(self._build_header(runtime_minutes, current_time))
        sections.append(self._build_market_state(
            symbol, current_snapshots, historical_arrays, futures_data,
            atr_value=atr_value, htf_analysis=htf_analysis
        ))
        sections.append(self._build_account_info(portfolio_metrics, symbol=symbol))
        sections.append(self._build_instructions(symbol=symbol, atr_value=atr_value, current_price=current_price))

        return "\n\n".join(sections)

    def _update_performance_tracking(self, portfolio_metrics: Dict[str, Any]) -> None:
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
        header = f"""It has been {runtime_minutes} minutes since you started trading. The current time is {current_time} and you've been invoked {self._invocation_count} times. Below, we are providing you with a variety of state data, price data, and predictive signals so you can discover alpha. Below that is your current account information, value, performance, positions, etc.

ALL OF THE PRICE OR SIGNAL DATA BELOW IS ORDERED: OLDEST → NEWEST

Timeframes note: Unless stated otherwise in a section title, the primary timeframe is 30-minute intervals. Additional timeframes (1m, 5m, 15m, 1h, 4h, 1d) are provided for comprehensive analysis."""

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

        data_30m = current_snapshots.get("30m", {})
        hist_30m = historical_arrays.get("30m", {})

        volatility_score = self._current_volatility or 0.5
        atr_pct = self._current_atr_pct or 0.0

        if volatility_score < 0.25:
            market_regime = "Low Volatility (Stable)"
        elif volatility_score < 0.5:
            market_regime = "Medium Volatility (Normal)"
        elif volatility_score < 0.75:
            market_regime = "High Volatility (Active)"
        else:
            market_regime = "Extreme Volatility (Dangerous)"

        lines.extend([
            "",
            "RISK & VOLATILITY REGIME:",
            f"• Volatility Score: {volatility_score:.2f} (0.0-1.0 scale)",
            f"• Market Regime: {market_regime}",
            f"• ATR (14-period): {atr_value:.2f} (~{atr_pct:.2f}% expected move)",
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
            current_price = data_30m.get("close", 0) if data_30m else 0
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

        # Trend + Structure + Price Action
        if data_30m and hist_30m:
            current_price = data_30m.get("close", 0)
            ema20 = data_30m.get("ema_20", 0)
            ema50 = data_30m.get("ema_50", 0)
            rsi = data_30m.get("rsi_14", 50)

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

            market_structure = self._analyze_market_structure(hist_30m)

            fomo_long_band = ema20 + 2 * atr_value if (ema20 and atr_value) else None
            fomo_short_band = ema20 - 2 * atr_value if (ema20 and atr_value) else None

            candle_patterns = self._detect_candle_patterns(hist_30m, atr_value=atr_value)

            lines.extend([
                "PRIMARY TIMEFRAME (30-minute) - ENHANCED ANALYSIS:",
                f"Trend Direction: {trend_direction} ({trend_strength})",
                f"Market Structure (pivot-based): {market_structure}",
                f"Current Price: {current_price:.2f}",
                f"EMA 20: {ema20:.2f} (Distance: {((current_price-ema20)/ema20*100):+.2f}%)",
                f"EMA 50: {ema50:.2f} (Distance: {((current_price-ema50)/ema50*100):+.2f}%)",
                f"RSI (14): {rsi:.2f}",
                "",
            ])

            if candle_patterns:
                lines.extend(["PRICE ACTION (last candles):"])
                for p in candle_patterns:
                    lines.append(f"• {p}")
                lines.append("")

            if fomo_long_band and fomo_short_band:
                lines.extend([
                    "NO-TRADE / FOMO ZONES (numeric):",
                    f"• LONG FOMO band ≈ EMA20 + 2*ATR = {fomo_long_band:.2f}",
                    f"• SHORT FOMO band ≈ EMA20 - 2*ATR = {fomo_short_band:.2f}",
                    "Rule: If price is beyond these bands + RSI extreme, avoid chasing.",
                    "",
                ])

            # Series summaries (token saver)
            lines.extend([
                "SERIES SUMMARIES (tail stats, not full arrays):",
                self._summarize_series(hist_30m.get("close", []), 2, "Close"),
                self._summarize_series(hist_30m.get("ema_20", []), 2, "EMA20"),
                self._summarize_series(hist_30m.get("ema_50", []), 2, "EMA50"),
                self._summarize_series(hist_30m.get("macd", []), 2, "MACD"),
                self._summarize_series(hist_30m.get("rsi_14", []), 2, "RSI14"),
                "",
                f"DEBUG tail closes: {self._format_array(hist_30m.get('close', [])[-self._series_tail:], 2)}",
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

        # Additional TFs lightweight
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
        initial_capital = 10000
        total_return_pct = ((equity - initial_capital) / initial_capital) * 100

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
        taker_fee_pct = 0.05

        example_position_usd = 50000
        example_fee = example_position_usd * (taker_fee_pct / 100)

        lines.extend([
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

    def _build_instructions(self, symbol: str = "BTCUSDT", atr_value: float = 0.0, current_price: float = 0.0) -> str:
        base_asset = symbol.replace("USDT", "")

        if current_price > 0:
            ex_sl = current_price * 0.98
            ex_tp = current_price * 1.05
            ex_inv_price = current_price * 0.99
        else:
            ex_sl, ex_tp, ex_inv_price = 100.0, 110.0, 99.0

        confidence_threshold = 0.80
        if self._consecutive_losses >= 2:
            confidence_threshold = 0.90
        if self._recent_win_rate <= 0.4 and len(self._performance_history) >= 5:
            confidence_threshold = max(confidence_threshold, 0.88)

        position_size_note = "Use normal position size (0.05-0.15 of equity)"
        if self._consecutive_losses >= 2 or self._recent_win_rate <= 0.4:
            position_size_note = "REDUCE position size (0.02-0.06 of equity) until performance recovers"

        vol = self._current_volatility or 0.5
        lev_cap, lev_regime = self._recommended_leverage_cap(vol)
        ex_leverage = min(5, lev_cap)

        atr_pct = self._current_atr_pct if self._current_atr_pct is not None else 0.0
        min_sl, max_sl, sl_reg = self._sl_distance_bounds(atr_pct, vol)
        min_tp, max_tp, tp_reg = self._tp_distance_bounds(atr_pct, vol)

        bounds_note = (
            f"- SL bounds for {sl_reg} vol regime: min≈{min_sl:.2f}% , max≈{max_sl:.2f}% of entry\n"
            f"- TP bounds for {tp_reg} vol regime: min≈{min_tp:.2f}% , max≈{max_tp:.2f}% of entry\n"
            f"- Leverage cap for {lev_regime} vol regime (perf-adjusted): ≤{lev_cap}x"
        )

        revenge_trading_warning = ""
        if self._consecutive_losses >= 2:
            revenge_trading_warning = f"""
⚠️⚠️⚠️ CRITICAL: You have {self._consecutive_losses} consecutive losses. ⚠️⚠️⚠️
1. DO NOT revenge trade.
2. Increase confidence threshold to {confidence_threshold:.0f}%.
3. Reduce position size ({position_size_note}).
4. Reduce leverage; cap now {lev_cap}x.
"""

        return f"""
================================================================================
YOUR TASK: TRADING DECISION & RISK PLAN
================================================================================

You are a professional crypto trader. Analyze price, indicators, futures and account data, then produce exactly one decision.

{revenge_trading_warning}
--------------------------------------------------------------------------------
STEP 1: MARKET REGIME & FOMO FILTER
--------------------------------------------------------------------------------
1) Regime: Trending (EMA20 > EMA50) or ranging?
2) FOMO check:
- LONG: If price > EMA20 + 2*ATR AND RSI > 70 → DO NOT BUY; wait pullback to EMA20/50.
- SHORT: If price < EMA20 - 2*ATR AND RSI < 30 → DO NOT SELL; wait pullback.

--------------------------------------------------------------------------------
STEP 2: MULTI-TIMEFRAME ALIGNMENT
--------------------------------------------------------------------------------
- 4H: Primary trend direction (avoid fighting it)
- 30M: Market structure (pivot-based swings)
- 5M: Entry trigger confirmation
Higher timeframe trend takes precedence.

--------------------------------------------------------------------------------
STEP 3: PRICE ACTION & STRUCTURE
--------------------------------------------------------------------------------
- Use swing HH/HL vs LH/LL (not candle-to-candle noise)
- Look for MSB as reversal confirmation
- Confirm with candle patterns (pinbar/engulfing/doji)

--------------------------------------------------------------------------------
STEP 4: DECISION
--------------------------------------------------------------------------------
Pick exactly one:
1) HOLD (default) → confidence < {confidence_threshold:.0f}% or no clean setup.
2) BUY (Long) → pullback + support/EMA20-50, no FOMO, confidence ≥ {confidence_threshold:.0f}%.
3) SELL (Short) → rally into resistance/EMA20-50, no panic-sell, confidence ≥ {confidence_threshold:.0f}%.
4) CLOSE → thesis invalidated or trend reversal. If unsure, choose HOLD.

--------------------------------------------------------------------------------
STEP 5: RISK MANAGEMENT & EXIT PLAN
--------------------------------------------------------------------------------
- STOP LOSS (SL): Logical swing level; not too tight, not too wide.
- TAKE PROFIT (TP): REQUIRED for BUY/SELL. Use key "profit_target".
- invalidation_condition: soft exit between entry and stop_loss.
- POSITION SIZE: 'quantity' is equity allocation (0-1), not {base_asset} amount.

CURRENT SAFETY BOUNDS (vol+ATR calibrated):
{bounds_note}

--------------------------------------------------------------------------------
OUTPUT FORMAT (PURE JSON)
--------------------------------------------------------------------------------
Return only valid JSON.
IMPORTANT:
- Do NOT add any text like "Here is your JSON".
- Do NOT wrap inside markdown fences.
- Output must be parseable by json.loads directly.

{{
"{symbol}": {{
    "trade_signal_args": {{
        "coin": "{symbol}",
        "signal": "BUY",  
        "quantity": 0.10,
        "stop_loss": {ex_sl:.2f},
        "profit_target": {ex_tp:.2f},
        "invalidation_condition": "If price closes below {ex_inv_price:.2f} on 15m candle",
        "leverage": {ex_leverage},
        "confidence": 0.85,
        "risk_usd": 500.0
    }},
    "gerekçe": "1) FOMO: ...\\n2) Giriş kalitesi: ...\\n3) Çıkış planı: ...\\n4) Karar gerekçesi: ..."
}}
}}

INVALIDATION FORMAT (REQUIRED FOR BUY/SELL):
- Template: "If price closes {{below|above}} {{price}} on {{timeframe}} candle"
"""

    def _format_array(self, values: List[float], decimals: int = 2) -> str:
        if not values:
            return "[]"
        return "[" + ", ".join(f"{v:.{decimals}f}" for v in values) + "]"

    def _check_position_close_notification(self, symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
        try:
            import json
            from app.utils.redis import get_redis_client

            redis = get_redis_client()
            redis_key = f"position_closed:{symbol}"
            notification_json = redis.get(redis_key)

            if notification_json:
                notification_data = json.loads(notification_json)
                redis.delete(redis_key)

                from app.utils.logging import get_logger
                logger = get_logger(__name__)
                logger.info(
                    "✅ GLM read position close notification from Redis | trigger=%s",
                    notification_data.get("trigger_type")
                )
                return notification_data

            return None
        except Exception as exc:
            from app.utils.logging import get_logger
            logger = get_logger(__name__)
            logger.error("Failed to read position close notification from Redis: %s", exc)
            return None

    def _format_close_notification(self, notification: Dict[str, Any], symbol: str = "BTCUSDT") -> List[str]:
        base_asset = symbol.replace("USDT", "")
        trigger_type = notification.get("trigger_type", "unknown")
        reason = notification.get("reason", "N/A")
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