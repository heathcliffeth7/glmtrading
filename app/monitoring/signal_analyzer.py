"""
Signal Analysis and Correlation Tools

Analyzes trading signals and their outcomes to:
1. Calculate success rates by regime (volatility, trend, etc.)
2. Correlate risk scores with outcomes
3. Identify optimal parameter ranges
4. Generate performance reports
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.utils.influx import _ensure_client, _query_api
from app.utils.logging import get_logger

logger = get_logger(__name__)


class SignalAnalyzer:
    """
    Analyze trading signals and their outcomes

    Usage:
        analyzer = SignalAnalyzer()

        # Get success rate by volatility regime
        success_by_vol = analyzer.success_rate_by_regime(
            regime_field='volatility_regime',
            threshold=0.9
        )

        # Correlate risk scores with outcomes
        correlations = analyzer.correlate_scores_with_outcomes()

        # Get optimal parameter ranges
        optimal = analyzer.find_optimal_parameters()
    """

    def __init__(self, bucket: str = None):
        from app.config.settings import get_settings

        settings = get_settings()
        self.bucket = bucket or settings.influx.bucket

    def success_rate_by_regime(
        self,
        regime_field: str,
        threshold: float,
        action: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, any]:
        """
        Calculate success rate for signals in a specific regime

        Example: What's the success rate of BUY signals when volatility >= 0.9?

        Args:
            regime_field: Field to filter on (e.g., 'volatility_regime')
            threshold: Threshold value for regime
            action: Filter by action (BUY/SELL/HOLD), None for all
            days: Number of days to analyze

        Returns:
            Dict with success rate and statistics
        """
        _ensure_client()

        # Build action filter
        action_filter = ""
        if action:
            action_filter = f'|> filter(fn: (r) => r["action"] == "{action}")'

        query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["_measurement"] == "trading_signals")
  {action_filter}
  |> filter(fn: (r) => r["_field"] == "{regime_field}" or r["_field"] == "outcome_success")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r["{regime_field}"] >= {threshold})
  |> filter(fn: (r) => exists r["outcome_success"])
"""

        try:
            tables = _query_api.query(query)

            if not tables or not tables[0].records:
                return {
                    "regime": f"{regime_field} >= {threshold}",
                    "action": action or "ALL",
                    "total_signals": 0,
                    "success_rate": 0.0,
                }

            successes = sum(
                1 for record in tables[0].records if record.values.get("outcome_success", 0) > 0.5
            )
            total = len(tables[0].records)

            return {
                "regime": f"{regime_field} >= {threshold}",
                "action": action or "ALL",
                "total_signals": total,
                "successful_signals": successes,
                "failed_signals": total - successes,
                "success_rate": (successes / total) * 100 if total > 0 else 0.0,
            }

        except Exception as e:
            logger.error("Failed to calculate success rate by regime: %s", e)
            return {}

    def correlate_scores_with_outcomes(
        self,
        outcome_field: str = "outcome_pnl_4h",
        days: int = 30,
    ) -> Dict[str, float]:
        """
        Calculate correlation between risk scores and outcomes

        Args:
            outcome_field: Outcome field to correlate with (e.g., 'outcome_pnl_4h')
            days: Number of days to analyze

        Returns:
            Dict with correlation coefficients for each risk score
        """
        _ensure_client()

        # Get all signals with outcomes
        query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["_measurement"] == "trading_signals")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r["{outcome_field}"])
"""

        try:
            tables = _query_api.query(query)

            if not tables or not tables[0].records:
                logger.warning("No signals with outcomes found for correlation analysis")
                return {}

            # Convert to DataFrame for correlation analysis
            data = []
            for record in tables[0].records:
                data.append(
                    {
                        "volatility_regime": record.values.get("volatility_regime", 0),
                        "trend_bias": record.values.get("trend_bias", 0),
                        "momentum_bias": record.values.get("momentum_bias", 0),
                        "futures_bias": record.values.get("futures_bias", 0),
                        "composite_bias": record.values.get("composite_bias", 0),
                        "bias_confidence": record.values.get("bias_confidence", 0),
                        "outcome": record.values.get(outcome_field, 0),
                    }
                )

            df = pd.DataFrame(data)

            # Calculate correlations
            correlations = {}
            for col in df.columns:
                if col != "outcome":
                    corr = df[col].corr(df["outcome"])
                    correlations[col] = corr

            return correlations

        except Exception as e:
            logger.error("Failed to correlate scores with outcomes: %s", e)
            return {}

    def find_optimal_parameters(
        self,
        parameter_field: str,
        bins: int = 10,
        days: int = 30,
    ) -> List[Dict[str, any]]:
        """
        Find optimal parameter ranges based on success rate

        Example: What volatility_regime range has the best success rate?

        Args:
            parameter_field: Parameter to analyze (e.g., 'volatility_regime')
            bins: Number of bins to divide parameter range into
            days: Number of days to analyze

        Returns:
            List of dicts with parameter range and success rate
        """
        _ensure_client()

        query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["_measurement"] == "trading_signals")
  |> filter(fn: (r) => r["_field"] == "{parameter_field}" or r["_field"] == "outcome_success")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r["outcome_success"])
"""

        try:
            tables = _query_api.query(query)

            if not tables or not tables[0].records:
                return []

            # Extract data
            data = []
            for record in tables[0].records:
                data.append(
                    {
                        "parameter": record.values.get(parameter_field, 0),
                        "success": record.values.get("outcome_success", 0) > 0.5,
                    }
                )

            df = pd.DataFrame(data)

            # Create bins
            df["bin"] = pd.cut(df["parameter"], bins=bins)

            # Calculate success rate per bin
            results = []
            for bin_range, group in df.groupby("bin"):
                total = len(group)
                successes = group["success"].sum()

                results.append(
                    {
                        "parameter_range": f"{bin_range.left:.2f} - {bin_range.right:.2f}",
                        "total_signals": total,
                        "successful_signals": int(successes),
                        "success_rate": (successes / total) * 100 if total > 0 else 0.0,
                    }
                )

            # Sort by success rate
            results.sort(key=lambda x: x["success_rate"], reverse=True)

            return results

        except Exception as e:
            logger.error("Failed to find optimal parameters: %s", e)
            return []

    def get_action_distribution(
        self,
        days: int = 30,
    ) -> Dict[str, int]:
        """
        Get distribution of actions (BUY/SELL/HOLD)

        Args:
            days: Number of days to analyze

        Returns:
            Dict with action counts
        """
        _ensure_client()

        query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["_measurement"] == "trading_signals")
  |> filter(fn: (r) => r["_field"] == "action_numeric")
  |> group(columns: ["action"])
  |> count()
"""

        try:
            tables = _query_api.query(query)

            distribution = {}
            for table in tables:
                for record in table.records:
                    action = record.values.get("action", "UNKNOWN")
                    count = record.get_value()
                    distribution[action] = count

            return distribution

        except Exception as e:
            logger.error("Failed to get action distribution: %s", e)
            return {}

    def get_performance_by_timeframe(
        self,
        days: int = 30,
    ) -> Dict[str, Dict[str, float]]:
        """
        Get performance metrics by outcome timeframe (1h, 4h, 24h)

        Args:
            days: Number of days to analyze

        Returns:
            Dict with performance metrics per timeframe
        """
        _ensure_client()

        timeframes = ["1h", "4h", "24h"]
        results = {}

        for tf in timeframes:
            query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["_measurement"] == "trading_signals")
  |> filter(fn: (r) => r["_field"] == "outcome_pnl_{tf}")
  |> keep(columns: ["_value"])
"""

            try:
                tables = _query_api.query(query)

                if not tables or not tables[0].records:
                    continue

                pnls = [record.get_value() for record in tables[0].records]

                if pnls:
                    results[tf] = {
                        "total_signals": len(pnls),
                        "avg_pnl": sum(pnls) / len(pnls),
                        "max_pnl": max(pnls),
                        "min_pnl": min(pnls),
                        "positive_signals": sum(1 for p in pnls if p > 0),
                        "win_rate": (sum(1 for p in pnls if p > 0) / len(pnls)) * 100,
                    }

            except Exception as e:
                logger.error("Failed to get performance for %s: %s", tf, e)

        return results

    def generate_report(
        self,
        days: int = 30,
    ) -> str:
        """
        Generate comprehensive signal analysis report

        Args:
            days: Number of days to analyze

        Returns:
            Formatted report string
        """
        lines = []
        lines.append("=" * 70)
        lines.append(f"  SIGNAL ANALYSIS REPORT - Last {days} Days")
        lines.append("=" * 70)

        # Action distribution
        distribution = self.get_action_distribution(days)
        if distribution:
            lines.append("\n📊 Action Distribution:")
            for action, count in distribution.items():
                lines.append(f"   {action}: {count}")

        # Performance by timeframe
        performance = self.get_performance_by_timeframe(days)
        if performance:
            lines.append("\n💰 Performance by Timeframe:")
            for tf, metrics in performance.items():
                lines.append(f"\n   {tf.upper()}:")
                lines.append(f"      Total Signals: {metrics['total_signals']}")
                lines.append(f"      Avg PnL: ${metrics['avg_pnl']:.2f}")
                lines.append(f"      Win Rate: {metrics['win_rate']:.1f}%")
                lines.append(f"      Best: ${metrics['max_pnl']:.2f}")
                lines.append(f"      Worst: ${metrics['min_pnl']:.2f}")

        # Success rate by volatility regime
        lines.append("\n🌪️  Success Rate by Volatility Regime:")
        for threshold in [0.7, 0.8, 0.9]:
            stats = self.success_rate_by_regime("volatility_regime", threshold, days=days)
            if stats and stats["total_signals"] > 0:
                lines.append(
                    f"   Vol >= {threshold:.1f}: {stats['success_rate']:.1f}% "
                    f"({stats['successful_signals']}/{stats['total_signals']})"
                )

        # Correlations
        correlations = self.correlate_scores_with_outcomes(days=days)
        if correlations:
            lines.append("\n🔗 Score Correlations with 4h PnL:")
            sorted_corr = sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)
            for score, corr in sorted_corr:
                emoji = "📈" if corr > 0 else "📉"
                lines.append(f"   {emoji} {score}: {corr:+.3f}")

        # Optimal volatility range
        optimal_vol = self.find_optimal_parameters("volatility_regime", bins=5, days=days)
        if optimal_vol:
            lines.append("\n🎯 Best Volatility Ranges (by success rate):")
            for i, result in enumerate(optimal_vol[:3], 1):
                lines.append(
                    f"   {i}. {result['parameter_range']}: {result['success_rate']:.1f}% "
                    f"({result['successful_signals']}/{result['total_signals']})"
                )

        lines.append("\n" + "=" * 70)

        return "\n".join(lines)


def main():
    """CLI entry point for signal analysis"""
    import argparse

    parser = argparse.ArgumentParser(description="Signal Analysis Tools")
    parser.add_argument("--days", type=int, default=30, help="Number of days to analyze")
    parser.add_argument("--report", action="store_true", help="Generate full report")
    parser.add_argument("--regime", help="Analyze success rate by regime (e.g., volatility_regime)")
    parser.add_argument("--threshold", type=float, default=0.9, help="Regime threshold")
    parser.add_argument("--correlations", action="store_true", help="Show score correlations")

    args = parser.parse_args()

    analyzer = SignalAnalyzer()

    if args.report:
        print(analyzer.generate_report(days=args.days))

    elif args.regime:
        stats = analyzer.success_rate_by_regime(args.regime, args.threshold, days=args.days)
        print(f"\nSuccess Rate for {stats['regime']}:")
        print(f"  Total Signals: {stats['total_signals']}")
        print(f"  Success Rate: {stats['success_rate']:.1f}%")

    elif args.correlations:
        correlations = analyzer.correlate_scores_with_outcomes(days=args.days)
        print("\nScore Correlations with 4h PnL:")
        for score, corr in sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True):
            print(f"  {score}: {corr:+.3f}")

    else:
        print("Use --report, --regime, or --correlations")


if __name__ == "__main__":
    main()
