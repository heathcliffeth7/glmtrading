from typing import Any, Dict, List, Optional

from app.agents.base import AgentSignal
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
from app.utils.logging import get_logger
from app.utils.runtime_tracker import RuntimeTracker

logger = get_logger(__name__)


class PromptBuilder:
    """
    PromptBuilder: Prompt oluşturma modülü
    
    Sorumluluk:
    - Regular prompt oluşturma (DeepSeek style)
    - NOF1.AI prompt oluşturma (JSON format)
    - Regular prompt fallback (natural language response)
    - Volatility context exposure
    
    Single Responsibility: Prompt generation
    """
    
    def __init__(
        self,
        nof1_prompt_builder: Nof1PromptBuilder,
        runtime_tracker: RuntimeTracker,
    ):
        self._nof1_prompt_builder = nof1_prompt_builder
        self._runtime_tracker = runtime_tracker
    
    def build_prompt(
        self,
        signals: List[AgentSignal],
        portfolio_metrics: dict = None,
        use_nof1_style: bool = False,
    ) -> List[dict[str, str]]:
        """Build prompt messages based on style preference."""
        if use_nof1_style:
            return self.build_nof1_prompt(signals, portfolio_metrics)
        else:
            return self._build_regular_style_prompt(signals, portfolio_metrics)
    
    def build_nof1_prompt(
        self,
        signals: List[AgentSignal],
        portfolio_metrics: dict = None,
    ) -> List[dict[str, str]]:
        """Build NOF1.AI style prompt with runtime tracking and complete market data."""
        
        if not signals:
            return []
        
        signal = signals[0]
        raw_market_data = signal.metadata.get("raw_market_data", {})
        htf_analysis = signal.metadata.get("htf_analysis")
        
        if portfolio_metrics:
            logger.info("🔍 DEBUG Portfolio Metrics Keys: %s", list(portfolio_metrics.keys()))
            logger.info("🔍 DEBUG long_position: %s", portfolio_metrics.get("long_position"))
            logger.info("🔍 DEBUG short_position: %s", portfolio_metrics.get("short_position"))
        else:
            logger.warning("⚠️ DEBUG: portfolio_metrics is None or empty!")
        
        if raw_market_data:
            content = self._nof1_prompt_builder.build_prompt(
                raw_market_data=raw_market_data,
                portfolio_metrics=portfolio_metrics or {},
                htf_analysis=htf_analysis,
            )

            estimated_tokens = len(content) // 4
            logger.info("📤 NOF1 GLM Prompt: %d chars, ~%d tokens", len(content), estimated_tokens)

            # System instruction'ı user content'in başına ekle (tek mesaj olarak gönder)
            system_instruction = """Sen bir kripto analisti. Verileri analiz et ve JSON formatında yanıt ver.

FUTURES YORUM REHBERI:
- OI azaliyor + Fiyat dusuyor = Long Liquidation (bearish devam)
- OI artiyor + Fiyat dusuyor = Aggressive Shorting (short squeeze riski)
- OI azaliyor + Fiyat yukseliyor = Short Liquidation (bullish devam)
- OI artiyor + Fiyat yukseliyor = New Longs (bullish momentum)

MTF SENTEZ KURALI (Day Trade - 1H Primary):
- Primary: 1H (ana sinyal kaynagi)
- Confirmation: 4H (trend yonu dogrulama)
- Timing: 15m (giris zamanlama)
- 3/3 TF ayni yonde = GUCLU sinyal
- 2/3 TF ayni yonde = ORTA sinyal
- TF'ler farkli yonde = ZAYIF/CATISMA

RSI CONTEXT RULES:
- ADX > 30: RSI 70+ = Momentum devami (satis DEGIL)
- ADX < 20: RSI 70+ = Overbought, RSI 30- = Oversold

ADX HARD CAP (oncelikli):
- ADX < 15 = max confidence 50 (range market, islem yasak)
- ADX < 20 = max confidence 70 (zayif trend)

CONFIDENCE PENALTY:
- Volume Ratio < 0.3 = confidence -10
- HIGH/EXTREME volatilite = confidence -15
- S/R mesafesi < 0.5% = confidence -10

"""
            combined_content = system_instruction + content

            return [
                {"role": "user", "content": combined_content}
            ]
        
        logger.warning("No raw_market_data in nof1 prompt, using fallback")
        return self._build_nof1_fallback_prompt(signals, portfolio_metrics)
    
    def _build_nof1_fallback_prompt(
        self,
        signals: List[AgentSignal],
        portfolio_metrics: dict = None,
    ) -> List[dict[str, str]]:
        """Fallback NOF1 prompt when raw_market_data is missing."""
        signal = signals[0]
        historical_data = signal.metadata.get("historical_data", {})
        feature_snapshot = signal.metadata.get("feature_snapshot", {})
        
        runtime_info = self._runtime_tracker.get_runtime_info()
        minutes_since_start = runtime_info["minutes_since_start"]
        invocation_count = runtime_info["invocation_count"]
        current_time = runtime_info["current_time"]
        
        intraday_1m = historical_data.get("intraday_1m", {})
        main_30min = historical_data.get("main_30min", {})
        longterm_4h = historical_data.get("longterm_4h", {})
        
        def get_latest(data_dict, key, default=0):
            values = data_dict.get(key, [])
            return values[-1] if values else default
        
        def format_array(values, decimals=2, limit=10):
            if not values:
                return "[]"
            values = values[-limit:] if len(values) > limit else values
            formatted = [f"{v:.{decimals}f}" if isinstance(v, float) else str(v) for v in values]
            return "[" + ", ".join(formatted) + "]"
        
        current_price = get_latest(main_30min, "close", 0)
        current_ema20 = get_latest(main_30min, "ema_20", 0)
        current_macd = get_latest(main_30min, "macd", 0)
        current_rsi7 = get_latest(intraday_1m, "rsi_7", 50) if intraday_1m.get("rsi_7") else get_latest(intraday_1m, "rsi_14", 50)
        
        oi = feature_snapshot.get("open_interest")
        oi_avg = feature_snapshot.get("open_interest_avg")
        fr = feature_snapshot.get("funding_rate")
        
        ema20_4h = get_latest(longterm_4h, "ema_20", 0)
        ema50_4h = get_latest(longterm_4h, "ema_50", 0)
        atr_14_4h = get_latest(longterm_4h, "atr_14", 0)
        atr_3_4h = get_latest(longterm_4h, "atr_3", 0)
        volume_4h = get_latest(longterm_4h, "volume", 0)
        
        volume_4h_array = longterm_4h.get("volume", [])
        volume_4h_avg = sum(volume_4h_array) / len(volume_4h_array) if volume_4h_array else volume_4h
        
        if oi_avg is None and oi is not None:
            oi_avg = oi
        
        content_lines = [
            f"It has been {minutes_since_start:.0f} minutes since you started trading. The current time is {current_time} and you've been invoked {invocation_count} times.",
            "",
            "ALL OF THE PRICE OR SIGNAL DATA BELOW IS ORDERED: OLDEST → NEWEST",
            "",
            "CURRENT MARKET STATE FOR ALL COINS",
            "",
            "ALL BTC DATA",
            "",
            f"current_price = {current_price:.2f}, current_ema20 = {current_ema20:.2f}, current_macd = {current_macd:.2f}, current_rsi (7 period) = {current_rsi7:.3f}",
            "",
        ]
        
        if oi is not None:
            oi_avg_val = oi_avg if oi_avg is not None else oi
            content_lines.append(f"Open Interest: Latest: {oi:.2f} Average: {oi_avg_val:.2f}")
        else:
            content_lines.append("Open Interest: Latest: N/A Average: N/A")
        
        if fr is not None:
            content_lines.append(f"Funding Rate: {fr:.6e}")
        else:
            content_lines.append("Funding Rate: N/A")
        
        content_lines.extend(["", "Intraday series (by minute, oldest → latest):", ""])
        
        mid_prices = intraday_1m.get("close", [])
        if mid_prices:
            mid_prices_3m = mid_prices[::3][-10:] if len(mid_prices) >= 3 else mid_prices[-10:]
        else:
            mid_prices_3m = []
        
        ema20_values = intraday_1m.get("ema_20", [])
        if ema20_values:
            ema20_3m = ema20_values[::3][-10:] if len(ema20_values) >= 3 else ema20_values[-10:]
        else:
            ema20_3m = []
        
        macd_values = intraday_1m.get("macd", [])
        if macd_values:
            macd_3m = macd_values[::3][-10:] if len(macd_values) >= 3 else macd_values[-10:]
        else:
            macd_3m = []
        
        rsi7_values = intraday_1m.get("rsi_7", [])
        if not rsi7_values:
            rsi7_values = intraday_1m.get("rsi_14", [])
        if rsi7_values:
            rsi7_3m = rsi7_values[::3][-10:] if len(rsi7_values) >= 3 else rsi7_values[-10:]
        else:
            rsi7_3m = []
        
        rsi14_values = intraday_1m.get("rsi_14", [])
        if rsi14_values:
            rsi14_3m = rsi14_values[::3][-10:] if len(rsi14_values) >= 3 else rsi14_values[-10:]
        else:
            rsi14_3m = []
        
        content_lines.extend([
            f"Mid prices: {format_array(mid_prices_3m, decimals=1)}",
            f"EMA indicators (20‑period): {format_array(ema20_3m, decimals=3)}",
            f"MACD indicators: {format_array(macd_3m, decimals=2)}",
            f"RSI indicators (7‑Period): {format_array(rsi7_3m, decimals=3)}",
            f"RSI indicators (14‑Period): {format_array(rsi14_3m, decimals=3)}",
            "",
            "Longer‑term context (4‑hour timeframe):",
            "",
            f"20‑Period EMA: {ema20_4h:.3f} vs. 50‑Period EMA: {ema50_4h:.3f}",
            f"3‑Period ATR: {atr_3_4h:.2f} vs. 14‑Period ATR: {atr_14_4h:.2f}",
            f"Current Volume: {volume_4h:.3f} vs. Average Volume: {volume_4h_avg:.3f}",
            "",
        ])
        
        macd_4h = longterm_4h.get("macd", [])
        rsi_4h = longterm_4h.get("rsi_14", [])
        content_lines.extend([
            f"MACD indicators: {format_array(macd_4h, decimals=3, limit=10)}",
            f"RSI indicators (14‑Period): {format_array(rsi_4h, decimals=3, limit=10)}",
            "",
            "HERE IS YOUR ACCOUNT INFORMATION & PERFORMANCE",
            "",
        ])
        
        if portfolio_metrics:
            equity = portfolio_metrics.get("equity", 10000.0)
            starting_cash = portfolio_metrics.get("starting_cash", 10000.0)
            available_cash = portfolio_metrics.get("free_cash", equity)
            position = portfolio_metrics.get("position", 0.0)
            
            total_return_pct = ((equity - starting_cash) / starting_cash) * 100.0 if starting_cash > 0 else 0.0
            
            content_lines.extend([
                f"Current Total Return (percent): {total_return_pct:.2f}%",
                f"Available Cash: {available_cash:.2f}",
                f"Current Account Value: {equity:.2f}",
                "",
            ])
            
            if abs(position) > 0.0001:
                entry_price = portfolio_metrics.get("entry_price", current_price)
                unrealized_pnl = portfolio_metrics.get("unrealized_pnl", 0.0)
                leverage = portfolio_metrics.get("leverage", 10.0)
                exit_plan = portfolio_metrics.get("exit_plan", {})
                
                sl_oid = portfolio_metrics.get("sl_oid", -1)
                tp_oid = portfolio_metrics.get("tp_oid", -1)
                entry_oid = portfolio_metrics.get("entry_oid", -1)
                notional_usd = portfolio_metrics.get("notional_usd", abs(position) * current_price)
                confidence = portfolio_metrics.get("confidence", 0.65)
                
                liquidation_price = entry_price * (1 - 0.9 / leverage) if position > 0 else entry_price * (1 + 0.9 / leverage)
                risk_usd = abs(position) * entry_price * 0.01
                
                position_dict = {
                    "symbol": "BTC",
                    "quantity": abs(position),
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "liquidation_price": liquidation_price,
                    "unrealized_pnl": unrealized_pnl,
                    "leverage": int(leverage) if leverage else 10,
                    "exit_plan": exit_plan,
                    "confidence": confidence if confidence is not None else 0.65,
                    "risk_usd": risk_usd,
                    "sl_oid": int(sl_oid) if sl_oid != -1 else -1,
                    "tp_oid": int(tp_oid) if tp_oid != -1 else -1,
                    "wait_for_fill": False,
                    "entry_oid": int(entry_oid) if entry_oid != -1 else -1,
                    "notional_usd": notional_usd,
                }
                
                import json as json_lib
                content_lines.append(f"Current live positions & performance: {json_lib.dumps(position_dict, indent=2)}")
            else:
                content_lines.append("Current live positions & performance: {}")
        
        content_lines.extend([
            "",
            "Based on the above market state and account information, analyze the market and provide your trading decision.",
            "",
            "You must output your decision in JSON format:",
            "",
            '{\n  "BTCUSDT": {\n    "trade_signal_args": {\n      "coin": "BTCUSDT",\n      "signal": "hold|close_position|buy|sell",\n      "quantity": 0.12,\n      "stop_loss": 102026.675,\n      "profit_target": 115000.0,\n      "invalidation_condition": "If the price closes below 105000 on a 3-minute candle",\n      "leverage": 10,\n      "confidence": 0.75,\n      "risk_usd": 619.2345,\n      "justification": "..."\n    }\n  }\n}',
            "",
            "⚠️ CRITICAL: The 'justification' field MUST be written in TURKISH.",
        ])
        
        prompt_content = "\n".join(content_lines)
        estimated_tokens = len(prompt_content) // 4
        logger.info("📤 GLM Prompt (fallback): %d chars, ~%d tokens", len(prompt_content), estimated_tokens)
        
        # System instruction'ı user content'in başına ekle (tek mesaj olarak gönder)
        system_instruction = (
            "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. "
            "KRİTİK KURAL: JSON yanıtındaki 'justification' alanı MUTLAKA TÜRKÇE olmalıdır.\n\n"
        )
        combined_content = system_instruction + prompt_content

        return [
            {"role": "user", "content": combined_content},
        ]
    
    def _build_regular_style_prompt(
        self,
        signals: List[AgentSignal],
        portfolio_metrics: dict = None,
    ) -> List[dict[str, str]]:
        """Build regular prompt (NOT NOF1.AI format)."""
        
        if not signals:
            return []
        
        signal = signals[0]
        raw_market_data = signal.metadata.get("raw_market_data", {})
        htf_analysis = signal.metadata.get("htf_analysis")
        
        if raw_market_data:
            content = self.build_regular_prompt(
                raw_market_data=raw_market_data,
                portfolio_metrics=portfolio_metrics or {},
                htf_analysis=htf_analysis,
            )
            
            return [{"role": "user", "content": content}]
        
        logger.warning("No raw_market_data found, using fallback prompt")
        return []
    
    def build_regular_prompt(
        self,
        raw_market_data: Dict[str, Any],
        portfolio_metrics: Dict[str, Any],
        htf_analysis: Dict[str, Any] = None,
    ) -> str:
        """Build regular prompt that asks for natural language response (not JSON)"""
        
        content = self._nof1_prompt_builder.build_prompt(
            raw_market_data=raw_market_data,
            portfolio_metrics=portfolio_metrics,
            htf_analysis=htf_analysis,
        )
        
        split_marker = "OUTPUT FORMAT (JSON ONLY)"
        if split_marker in content:
            base_content = content.split(split_marker)[0]
            
            new_instructions = """OUTPUT FORMAT:

Respond with a clear, concise decision in natural language. Start with your action:

ACTION: BUY | SELL | HOLD | CLOSE

Then provide your reasoning in 1-2 sentences.

IMPORTANT:
- Keep responses concise and actionable
- Focus on the most important factors
- Mention key indicator levels if relevant
- Consider your current position and exit plan"""
            
            return base_content + new_instructions
            
        return content
    
    def get_volatility_context(self) -> dict:
        """Get volatility context from nof1_prompt_builder."""
        try:
            regime_key = "medium"
            if hasattr(self._nof1_prompt_builder, "_vol_regime_key"):
                try:
                    regime_key = self._nof1_prompt_builder._vol_regime_key()
                except Exception:
                    regime_key = "medium"
            
            return {
                "volatility": getattr(self._nof1_prompt_builder, "_current_volatility", None),
                "atr": getattr(self._nof1_prompt_builder, "_current_atr", None),
                "atr_pct": getattr(self._nof1_prompt_builder, "_current_atr_pct", None),
                "vol_ratio": getattr(self._nof1_prompt_builder, "_vol_ratio", None),
                "atr_ratio": getattr(self._nof1_prompt_builder, "_atr_ratio", None),
                "volatility_regime": regime_key,
            }
        except Exception as e:
            logger.warning("Failed to get volatility context: %s", e)
            return {
                "volatility": None,
                "atr": None,
                "atr_pct": None,
                "vol_ratio": None,
                "atr_ratio": None,
                "volatility_regime": "medium",
            }
