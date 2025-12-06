"""
Risk Monitoring Module

Handles logging, metrics, and notifications for risk management:
- Signal logging
- Analysis summary notifications
- Parsing metrics tracking
- Prompt/response file logging
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging import get_logger
from app.utils.telegram import telegram_client


logger = get_logger(__name__)


class RiskMonitor:
    """
    Monitors and logs risk management activities.
    """

    def __init__(self, signal_logger=None):
        """
        Initialize the risk monitor.

        Args:
            signal_logger: Optional signal logger instance
        """
        self._signal_logger = signal_logger
        self._log_dir = Path("/root/trading/logs")

    def log_signal(
        self,
        symbol: str,
        action: str,
        amount: float,
        confidence: float,
        reasoning: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log a trading signal with risk scores.
        """
        if self._signal_logger:
            self._signal_logger.log(
                symbol=symbol,
                action=action,
                amount=amount,
                confidence=confidence,
                reasoning=reasoning,
                metadata=metadata or {}
            )
        else:
            logger.info(
                "Signal: %s %s amount=%.4f conf=%.1f%% - %s",
                symbol, action, amount, confidence, reasoning[:100]
            )

    def send_analysis_summary(
        self,
        symbol: str,
        decision: Any,
        market_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Send detailed analysis summary via Telegram.
        """
        try:
            # Build message
            lines = [
                f"📊 *Analysis Summary: {symbol}*",
                "",
                f"*Decision:* {decision.action}",
                f"*Amount:* {decision.amount:.4f}",
                f"*Leverage:* {decision.leverage:.1f}x",
                f"*Confidence:* {decision.glm_confidence:.1f}%",
            ]

            if decision.exit_plan:
                ep = decision.exit_plan
                lines.extend([
                    "",
                    "*Exit Plan:*",
                    f"  SL: ${ep.get('stop_loss', 0):,.2f}",
                    f"  TP: ${ep.get('profit_target', ep.get('take_profit', 0)):,.2f}",
                ])

            if decision.reasoning:
                lines.extend([
                    "",
                    f"*Reasoning:* {decision.reasoning[:200]}...",
                ])

            message = "\n".join(lines)
            telegram_client.send(message)
        except Exception as e:
            logger.error("Failed to send analysis summary: %s", e)

    def get_parsing_metrics(self, metrics: Dict[str, int]) -> Dict[str, Any]:
        """
        Return parsing metrics with calculated rates.
        """
        total = metrics.get("total_json_requests", 0)
        successful = metrics.get("successful_json_parsing", 0)
        failures = metrics.get("complete_failures", 0)

        success_rate = (successful / total * 100) if total > 0 else 0.0
        failure_rate = (failures / total * 100) if total > 0 else 0.0

        return {
            **metrics,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
        }

    def get_alert_status(self, metrics: Dict[str, Any]) -> str:
        """
        Determine alert level based on metrics.
        """
        failure_rate = metrics.get("failure_rate", 0)
        if failure_rate > 20:
            return "CRITICAL"
        elif failure_rate > 10:
            return "WARNING"
        elif failure_rate > 5:
            return "INFO"
        return "OK"

    def log_parsing_metrics(self, metrics: Dict[str, int]) -> None:
        """
        Log parsing metrics with alert status.
        """
        enhanced = self.get_parsing_metrics(metrics)
        status = self.get_alert_status(enhanced)

        if status == "CRITICAL":
            logger.error("Parsing metrics CRITICAL: %s", enhanced)
        elif status == "WARNING":
            logger.warning("Parsing metrics WARNING: %s", enhanced)
        else:
            logger.info("Parsing metrics: %s", enhanced)

    def write_prompt_to_file(
        self,
        symbol: str,
        system_message: str,
        user_content: str,
        estimated_tokens: int
    ) -> None:
        """
        Write GLM prompt to log file for analysis.
        """
        try:
            self._log_dir.mkdir(exist_ok=True)
            log_file = self._log_dir / "glm_prompts.log"

            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            with open(log_file, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 100 + "\n")
                f.write(f"GLM PROMPT | {timestamp} | {symbol}\n")
                f.write(f"Stats: {len(user_content)} chars, ~{estimated_tokens} tokens\n")
                f.write("=" * 100 + "\n\n")
                f.write("SYSTEM MESSAGE:\n")
                f.write("-" * 50 + "\n")
                f.write(system_message + "\n\n")
                f.write("USER CONTENT:\n")
                f.write("-" * 50 + "\n")
                f.write(user_content + "\n")
                f.write("=" * 100 + "\n\n")

            logger.info("Prompt logged to %s", log_file)

        except Exception as e:
            logger.warning("Failed to write prompt to file: %s", e)

    def write_response_to_file(
        self,
        response_content: str,
        estimated_tokens: int
    ) -> None:
        """
        Write GLM response to log file for analysis.
        """
        try:
            log_file = self._log_dir / "glm_prompts.log"
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            with open(log_file, "a", encoding="utf-8") as f:
                f.write("\n" + "-" * 100 + "\n")
                f.write(f"GLM RESPONSE | {timestamp}\n")
                f.write(f"Stats: {len(response_content)} chars, ~{estimated_tokens} tokens\n")
                f.write("-" * 100 + "\n")
                f.write(response_content + "\n")
                f.write("-" * 100 + "\n\n")

        except Exception as e:
            logger.warning("Failed to write response to file: %s", e)
