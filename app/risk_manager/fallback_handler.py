"""
Fallback Handler Module

Handles error recovery and fallback decisions when LLM fails:
- Conservative fallback decisions
- LLM failure notifications
- Translation utilities
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.risk_manager.decision_models import RiskDecision
from app.utils.logging import get_logger
from app.utils.telegram import telegram_client, format_markdown


logger = get_logger(__name__)


class FallbackHandler:
    """
    Handles fallback decisions when LLM API is unavailable or fails.
    """

    def __init__(self, market_analyzer=None):
        """
        Initialize the fallback handler.

        Args:
            market_analyzer: Optional RiskMarketAnalyzer for confidence calculation
        """
        self._market_analyzer = market_analyzer

    def fallback_decision(
        self,
        signals: List[Any],
        reason: str
    ) -> RiskDecision:
        """
        Produce a conservative decision when LLM API fails.

        When LLM API is unavailable, we prioritize risk management:
        - Default to HOLD to avoid making decisions without AI analysis
        - Only allow trading if there's very strong signal confidence
        - Use reduced position sizes for safety
        """
        if not signals:
            logger.warning("No signals available for fallback decision - defaulting to HOLD")
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"LLM API unavailable and no signals: {reason}",
                leverage=5.0,
                decision_timestamp=datetime.now(timezone.utc),
            )

        # Get confidence band from available signals
        band = self._get_confidence_band(signals)

        # Extra conservative approach when LLM is down
        if band["confidence"] < 70.0:
            logger.info(
                "LLM API down - signal confidence %.1f%% below 70%% threshold → HOLD for safety",
                band["confidence"]
            )
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=f"LLM API unavailable - signal confidence too low ({band['confidence']:.1f}% < 70%): {reason}",
                leverage=5.0,
                decision_timestamp=datetime.now(timezone.utc),
            )

        # If confidence is high enough, allow trading but with reduced size
        reduced_amount = min(band["amount"] * 0.5, 0.1)
        reasoning = (
            f"LLM API down - using reduced position (confidence={band['confidence']:.1f}%, "
            f"reduced_amount={reduced_amount:.3f}) | {reason}"
        )

        logger.warning(
            "LLM API down but using fallback trade: action=%s amount=%.3f confidence=%.1f%%",
            band["action"],
            reduced_amount,
            band["confidence"]
        )

        return RiskDecision(
            action=band["action"],
            amount=reduced_amount,
            reasoning=reasoning,
            leverage=5.0,
            decision_timestamp=datetime.now(timezone.utc),
        )

    def _get_confidence_band(self, signals: List[Any]) -> Dict[str, Any]:
        """
        Get confidence band from signals.
        """
        if self._market_analyzer:
            return self._market_analyzer.confidence_band(signals)

        # Simple fallback calculation
        if not signals:
            return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

        buy_conf = 0.0
        sell_conf = 0.0
        total = 0.0

        for signal in signals:
            direction = getattr(signal, 'direction', '').upper()
            conf = getattr(signal, 'confidence', 0.0) * 100  # Convert to percentage

            if direction in ['BUY', 'LONG']:
                buy_conf += conf
            elif direction in ['SELL', 'SHORT']:
                sell_conf += conf

            total += 1.0

        if total == 0:
            return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

        avg_buy = buy_conf / total
        avg_sell = sell_conf / total

        if avg_buy > avg_sell and avg_buy > 50:
            return {
                "action": "BUY",
                "amount": min(0.1, avg_buy / 1000),
                "confidence": avg_buy,
            }
        elif avg_sell > avg_buy and avg_sell > 50:
            return {
                "action": "SELL",
                "amount": min(0.1, avg_sell / 1000),
                "confidence": avg_sell,
            }

        return {"action": "HOLD", "amount": 0.0, "confidence": max(avg_buy, avg_sell)}

    def notify_llm_failure(
        self,
        signals: List[Any],
        error: str
    ) -> None:
        """
        Send Telegram notification when LLM fails.
        """
        try:
            if not telegram_client.enabled():
                return

            # Summarize signals
            signal_summary = []
            for signal in signals:
                direction = getattr(signal, 'direction', 'N/A')
                conf = getattr(signal, 'confidence', 0.0)
                signal_summary.append(f"  • {direction} (conf: {conf:.2f})")

            message = "\n".join([
                "🚨 *LLM API ERROR*",
                "",
                "*Error Details:*",
                f"{format_markdown(error)}",
                "",
                "*Agent Signals:*",
                "\n".join(signal_summary) if signal_summary else "  No signals",
                "",
                "⚠️ *Decision: HOLD (Safe mode)*",
                "No trading while LLM is unavailable.",
                "",
                f"🕒 {datetime.utcnow().isoformat()}",
                "",
                "💡 *Action:*",
                "1. Check LLM API key/cookie",
                "2. Verify LLM service status",
                "3. Check logs if error persists",
            ])

            telegram_client.send_message(message)
            logger.info("LLM failure notification sent to Telegram")

        except Exception as exc:
            logger.error("Failed to send LLM failure notification: %s", exc)

    def translate_to_turkish(self, text: str, glm_client=None) -> str:
        """
        Translate English text to Turkish using GLM.
        If the text is already in Turkish, returns it unchanged.
        """
        if not text or len(text) < 20:
            return text

        if not glm_client:
            return text

        # Check for common English words/patterns
        english_markers = [
            'the ', 'market', 'price', 'trend', 'bearish', 'bullish',
            'support', 'resistance', 'momentum', 'indicates', 'suggests',
            'trading', 'position', 'volume', 'level', 'break', 'strong',
            'despite', 'therefore', 'however', 'confluence'
        ]

        text_lower = text.lower()
        english_word_count = sum(1 for marker in english_markers if marker in text_lower)

        if english_word_count < 3:
            return text

        logger.info("English reasoning detected (%d markers), translating...", english_word_count)

        try:
            translation_prompt = (
                "Aşağıdaki İngilizce kripto analiz metnini Türkçe'ye çevir. "
                "Sadece çeviriyi yaz, başka bir şey ekleme. "
                "Teknik terimleri (RSI, EMA, MACD, support, resistance) olduğu gibi bırak:\n\n"
                f"{text}"
            )

            response = glm_client.request([
                {"role": "user", "content": translation_prompt}
            ])

            if response and "choices" in response:
                translated = response["choices"][0]["message"]["content"].strip()
                logger.info("Translation completed: %d → %d chars", len(text), len(translated))
                return translated

        except Exception as e:
            logger.warning("Translation failed: %s - using original text", e)

        return text
