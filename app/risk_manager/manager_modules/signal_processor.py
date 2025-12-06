from typing import Any, Dict, List, Optional

from app.agents.base import AgentSignal
from app.risk_manager.decision_models import RiskDecision
from app.utils.logging import get_logger
from app.utils.signal_logger import get_signal_logger
from app.utils.telegram import telegram_client

logger = get_logger(__name__)


class SignalProcessor:
    """
    SignalProcessor: Signal logging ve notification modülü
    
    Sorumluluk:
    - Signal logging (InfluxDB)
    - Telegram notification gönderme
    - Analysis summary oluşturma
    
    Single Responsibility: Signal processing ve notification
    """
    
    def __init__(self, symbol: str = "BTCUSDT"):
        self._symbol = symbol
        self._signal_logger = get_signal_logger()
    
    def log_signal(
        self,
        decision: RiskDecision,
        signal: AgentSignal | None,
        portfolio_metrics: dict | None,
    ) -> str:
        """
        Log trading signal with all risk scores and decision factors
        
        Args:
            decision: RiskDecision object
            signal: AgentSignal object (if available)
            portfolio_metrics: Portfolio state dict
            
        Returns:
            Summary string for Telegram notification
        """
        try:
            if signal is None:
                logger.warning("No signal available for logging")
                return ""
            
            # Extract latency metrics from signal metadata
            latency_metrics = {}
            if signal.metadata:
                trace_id = signal.metadata.get('trace_id')
                if trace_id:
                    try:
                        from app.utils.latency import get_latency_tracker
                        tracker = get_latency_tracker()
                        trace = tracker.get_trace(trace_id)
                        if trace:
                            latency_metrics = trace.get_latencies()
                    except Exception:
                        pass
            
            signal_log = self._signal_logger.create_signal_from_decision(
                decision=decision,
                signal=signal,
                symbol=self._symbol,
                portfolio_metrics=portfolio_metrics,
                latency_metrics=latency_metrics,
            )
            
            self._signal_logger.log_signal(signal_log)
            
            return signal_log.get_summary()
            
        except Exception as e:
            logger.error("Failed to log signal: %s", e, exc_info=True)
            return ""
    
    def send_analysis_summary(
        self,
        decision: RiskDecision,
        signals: List[AgentSignal],
        portfolio_metrics: dict | None,
        bias_reliability_data: Dict[str, Any] | None,
        signal_summary: str,
    ) -> None:
        """
        Send consolidated analysis summary to Telegram
        
        Args:
            decision: RiskDecision object
            signals: List of agent signals
            portfolio_metrics: Portfolio state
            bias_reliability_data: Bias reliability test results
            signal_summary: Signal log summary
        """
        try:
            if not telegram_client.enabled():
                return
            
            lines = ["🔄 *ANALİZ DÖNGÜSÜ TAMAMLANDI*", ""]
            
            if decision.glm_response_time_ms > 0:
                lines.extend([
                    "⏱️ *GLM Latency*",
                    f"  Yanıt Süresi: {decision.glm_response_time_ms:.0f}ms",
                    ""
                ])
            
            if bias_reliability_data:
                status_emoji = bias_reliability_data["reliability_color"]
                status = bias_reliability_data["reliability_status"].replace("_", " ")
                lines.extend([
                    "📊 *Bias Güvenilirlik Testi*",
                    f"  Status: {status} {status_emoji}",
                    f"  GLM Güven: {bias_reliability_data['glm_confidence']:.1f}%",
                    f"  Bias Güven: {bias_reliability_data['bias_confidence']:.1f}%",
                    f"  Composite Bias: {bias_reliability_data['composite_bias']:+.2f}",
                    f"  Güven Farkı: {bias_reliability_data['confidence_diff']:.2f}",
                    f"  Volatilite: {bias_reliability_data['volatility_regime']:.2f}",
                    ""
                ])
            
            action_emoji = "🟢" if decision.action == "BUY" else "🔴" if decision.action == "SELL" else "⚪"
            lines.extend([
                "🎯 *GLM Kararı*",
                f"  Action: {decision.action} {action_emoji}",
                f"  Amount: {decision.amount:.4f}",
                f"  Leverage: {decision.leverage:.2f}x",
                f"  Güven: {decision.glm_confidence:.1f}%",
                ""
            ])
            
            if decision.reason_primary or decision.reason_secondary:
                lines.append("📝 *Gerekçeler*")
                if decision.reason_primary:
                    lines.append(f"  ▸ Ana: {decision.reason_primary}")
                if decision.reason_secondary:
                    lines.append(f"  ▸ İkincil: {decision.reason_secondary}")
                lines.append("")
            
            if signal_summary:
                lines.extend([
                    "💾 *Signal Kaydedildi*",
                    f"  {signal_summary}",
                    ""
                ])
            
            if portfolio_metrics:
                equity = portfolio_metrics.get("equity", 0)
                position = portfolio_metrics.get("position", 0)
                total_pnl = portfolio_metrics.get("total_pnl", 0)
                pnl_pct = (total_pnl / 10000) * 100 if total_pnl else 0
                
                position_emoji = "🟢" if position > 0 else "🔴" if position < 0 else "⚪"
                pnl_emoji = "📈" if total_pnl > 0 else "📉" if total_pnl < 0 else "➖"
                
                lines.extend([
                    "💼 *Portfolio Durumu*",
                    f"  Equity: ${equity:,.2f}",
                    f"  Position: {position:+.6f} BTC {position_emoji}",
                    f"  PnL: ${total_pnl:,.2f} ({pnl_pct:+.2f}%) {pnl_emoji}",
                    ""
                ])
            
            message = "\n".join(lines)
            telegram_client.send_message(message, auto_split=True)
            logger.info("Analysis summary sent to Telegram")
            
        except Exception as exc:
            logger.error("Failed to send analysis summary to Telegram: %s", exc, exc_info=True)
    
    def get_alert_status(self) -> dict:
        """Determine alert status based on metrics (placeholder for future expansion)"""
        return {"level": "INFO", "message": "Signal processor operational"}
