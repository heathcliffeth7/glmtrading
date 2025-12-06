import math
from typing import Optional

from app.utils.logging import get_logger

logger = get_logger(__name__)


class MetricsCalculator:
    """
    MetricsCalculator: Performance metrics hesaplama modülü
    
    Sorumluluk:
    - Sharpe ratio hesaplama
    - Parsing metrics tracking ve reporting
    
    Single Responsibility: Metrics calculation ve monitoring
    """
    
    def __init__(self):
        self._parsing_metrics = {
            "total_json_requests": 0,
            "successful_json_parsing": 0,
            "json_parsing_errors": 0,
            "fallback_parsing_successes": 0,
            "fallback_parsing_failures": 0,
            "signal_recoveries": 0,
            "complete_failures": 0,
            "thought_process_present": 0,
            "thought_process_missing": 0,
            "thought_process_parse_errors": 0,
            "consistency_checks_performed": 0,
            "consistency_mismatches": 0,
            "consistency_forced_holds": 0,
            "dynamic_threshold_low": 0,
            "dynamic_threshold_medium": 0,
            "dynamic_threshold_high": 0,
            "dynamic_threshold_extreme": 0,
        }
    
    def calculate_sharpe_ratio(self, portfolio_metrics: Optional[dict]) -> float:
        """Calculate Sharpe ratio from portfolio metrics."""
        if not portfolio_metrics:
            return 0.0
        
        try:
            from app.executor.executor import Executor
            executor = Executor(symbol="BTCUSDT")
            recent_trades = executor.get_recent_trades_with_pnl(limit=50)
            
            if not recent_trades:
                return 0.0
            
            returns = []
            for trade in recent_trades:
                if trade.get("is_closed", False):
                    pnl_pct = trade.get("pnl_pct", 0.0) or 0.0
                    if pnl_pct != 0:
                        returns.append(pnl_pct / 100.0)
            
            if len(returns) < 3:
                return 0.0
            
            mean_return = sum(returns) / len(returns)
            variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
            std_return = math.sqrt(max(variance, 1e-18))
            
            if std_return == 0:
                return 0.0
            
            # Annualize: assume daily returns
            sharpe = (mean_return / std_return) * math.sqrt(252)
            return sharpe
        except Exception:
            return 0.0
    
    def get_parsing_metrics(self) -> dict:
        """Get current JSON parsing metrics for monitoring"""
        total = self._parsing_metrics["total_json_requests"]
        if total == 0:
            return {
                "status": "No requests processed yet",
                "metrics": self._parsing_metrics.copy()
            }

        success_rate = (self._parsing_metrics["successful_json_parsing"] / total) * 100
        error_rate = (self._parsing_metrics["json_parsing_errors"] / total) * 100
        recovery_rate = (self._parsing_metrics["signal_recoveries"] / max(1, self._parsing_metrics["json_parsing_errors"])) * 100

        return {
            "total_requests": total,
            "success_rate_percent": round(success_rate, 2),
            "error_rate_percent": round(error_rate, 2),
            "signal_recovery_rate_percent": round(recovery_rate, 2),
            "metrics": self._parsing_metrics.copy(),
            "alert_status": self.get_alert_status()
        }

    def get_alert_status(self) -> dict:
        """Determine alert status based on metrics"""
        total = self._parsing_metrics["total_json_requests"]
        if total == 0:
            return {"level": "INFO", "message": "No data yet"}

        error_rate = (self._parsing_metrics["json_parsing_errors"] / total) * 100
        recovery_rate = (self._parsing_metrics["signal_recoveries"] / max(1, self._parsing_metrics["json_parsing_errors"])) * 100

        if error_rate > 20:
            return {
                "level": "CRITICAL",
                "message": f"High JSON error rate: {error_rate:.1f}% - GLM response format issues"
            }
        elif error_rate > 10:
            return {
                "level": "WARNING",
                "message": f"Elevated JSON error rate: {error_rate:.1f}% - Monitor closely"
            }
        elif recovery_rate < 50 and self._parsing_metrics["json_parsing_errors"] > 0:
            return {
                "level": "WARNING",
                "message": f"Low signal recovery rate: {recovery_rate:.1f}% - Fallback parsing needs improvement"
            }
        else:
            return {
                "level": "OK",
                "message": "JSON parsing performance is acceptable"
            }

    def log_parsing_metrics(self) -> None:
        """Log current parsing metrics with alert status"""
        metrics = self.get_parsing_metrics()
        
        if "alert_status" not in metrics:
            logger.info("📊 Parsing Metrics: No data yet")
            return
        
        alert = metrics["alert_status"]

        if alert["level"] == "CRITICAL":
            logger.critical("🚨 JSON Parsing Alert: %s", alert["message"])
        elif alert["level"] == "WARNING":
            logger.warning("⚠️ JSON Parsing Warning: %s", alert["message"])
        else:
            logger.info("✅ JSON Parsing Status: %s", alert["message"])

        logger.info("📊 Parsing Metrics: %d total, %.1f%% success, %.1f%% errors, %d signals recovered",
                   metrics["total_requests"], metrics["success_rate_percent"],
                   metrics["error_rate_percent"], metrics["metrics"]["signal_recoveries"])
