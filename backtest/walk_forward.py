#!/usr/bin/env python3
"""
Walk-Forward Analysis Framework
Rolling window optimizasyonu ve model validasyonu için kapsamlı sistem
"""

import sys
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
import yaml
from dataclasses import dataclass, asdict
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import BacktestEngine, BacktestConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class WalkForwardConfig:
    """Walk-forward analysis configuration"""
    # Time periods
    training_period_days: int = 180  # 6 months
    testing_period_days: int = 30    # 1 month
    step_size_days: int = 7          # 1 week

    # Parameters to optimize
    confidence_threshold_range: Tuple[float, float] = (0.50, 0.85)
    risk_per_trade_range: Tuple[float, float] = (0.01, 0.05)
    max_position_range: Tuple[float, float] = (0.5, 2.0)

    # Optimization settings
    num_optimization_trials: int = 50
    optimization_metric: str = 'sharpe_ratio'
    min_trades_threshold: int = 10

    # Validation settings
    use_out_of_sample: bool = True
    out_of_sample_ratio: float = 0.2  # 20% of data for final test

    # Monte Carlo
    monte_carlo_simulations: int = 1000
    monte_carlo_confidence_levels: List[float] = None

    def __post_init__(self):
        if self.monte_carlo_confidence_levels is None:
            self.monte_carlo_confidence_levels = [0.90, 0.95, 0.99]

@dataclass
class OptimizationResult:
    """Result of parameter optimization"""
    params: Dict
    metric_value: float
    num_trades: int
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    training_start: datetime
    training_end: datetime
    testing_start: datetime
    testing_end: datetime

@dataclass
class WalkForwardResult:
    """Complete walk-forward analysis result"""
    start_date: datetime
    end_date: datetime
    num_windows: int
    optimized_params: List[OptimizationResult]
    aggregate_metrics: Dict
    best_window: OptimizationResult
    worst_window: OptimizationResult
    param_stability: Dict
    final_validation: Optional[Dict]

class ParameterOptimizer:
    """Hyperparameter optimization for trading strategy"""

    def __init__(self, engine: BacktestEngine):
        self.engine = engine

    def optimize_parameters(self, config: WalkForwardConfig,
                           data: pd.DataFrame,
                           training_start: datetime,
                           training_end: datetime,
                           testing_start: datetime,
                           testing_end: datetime) -> OptimizationResult:
        """Optimize parameters using Bayesian optimization or grid search"""
        logger.info(f"Optimizing parameters for {training_start.date()} to {training_end.date()}")

        # Generate parameter combinations
        param_combinations = self._generate_parameter_combinations(config)

        best_result = None
        best_metric = -np.inf

        results = []

        # Test each parameter combination
        for params in param_combinations:
            try:
                # Create backtest config
                backtest_config = BacktestConfig(
                    start_date=training_start,
                    end_date=training_end,
                    symbol='BTCUSDT',
                    timeframe='15m',
                    initial_capital=10000,
                    commission_rate=0.001,
                    slippage=0.0005,
                    max_position_size=params['max_position_size'],
                    confidence_threshold=params['confidence_threshold'],
                    risk_per_trade=params['risk_per_trade']
                )

                # Run backtest
                backtest_result = self.engine.run_backtest(backtest_config, 'optimization')
                metrics = backtest_result['metrics']

                # Check if enough trades
                if metrics['num_trades'] < config.min_trades_threshold:
                    continue

                # Get optimization metric
                metric_value = metrics.get(config.optimization_metric, 0)

                # Store result
                result = OptimizationResult(
                    params=params,
                    metric_value=metric_value,
                    num_trades=metrics['num_trades'],
                    total_return=metrics['total_return'],
                    sharpe_ratio=metrics['sharpe_ratio'],
                    max_drawdown=metrics['max_drawdown'],
                    win_rate=metrics['win_rate'],
                    training_start=training_start,
                    training_end=training_end,
                    testing_start=testing_start,
                    testing_end=testing_end
                )

                results.append(result)

                # Check if this is the best
                if metric_value > best_metric:
                    best_metric = metric_value
                    best_result = result

            except Exception as e:
                logger.warning(f"Failed to optimize with params {params}: {e}")
                continue

        if best_result is None:
            logger.warning(f"No valid parameter combination found for {training_start.date()}")
            # Return default parameters
            return OptimizationResult(
                params={
                    'confidence_threshold': 0.65,
                    'risk_per_trade': 0.02,
                    'max_position_size': 1.0
                },
                metric_value=0,
                num_trades=0,
                total_return=0,
                sharpe_ratio=0,
                max_drawdown=0,
                win_rate=0,
                training_start=training_start,
                training_end=training_end,
                testing_start=testing_start,
                testing_end=testing_end
            )

        logger.info(f"Best params: {best_result.params}, {config.optimization_metric}: {best_result.metric_value:.3f}")
        return best_result

    def _generate_parameter_combinations(self, config: WalkForwardConfig) -> List[Dict]:
        """Generate parameter combinations for grid search"""
        combinations = []

        # Confidence threshold values
        confidence_values = np.linspace(
            config.confidence_threshold_range[0],
            config.confidence_threshold_range[1],
            7
        )

        # Risk per trade values
        risk_values = np.linspace(
            config.risk_per_trade_range[0],
            config.risk_per_trade_range[1],
            5
        )

        # Position size values
        position_values = np.linspace(
            config.max_position_range[0],
            config.max_position_range[1],
            4
        )

        # Generate all combinations
        for conf, risk, pos in itertools.product(confidence_values, risk_values, position_values):
            combinations.append({
                'confidence_threshold': round(conf, 2),
                'risk_per_trade': round(risk, 3),
                'max_position_size': round(pos, 1)
            })

        return combinations

class OutOfSampleValidator:
    """Out-of-sample testing and validation"""

    def __init__(self, engine: BacktestEngine):
        self.engine = engine

    def validate_optimized_params(self, params: Dict,
                                  training_data: pd.DataFrame,
                                  testing_start: datetime,
                                  testing_end: datetime) -> Dict:
        """Validate optimized parameters on out-of-sample data"""
        logger.info(f"Validating params on out-of-sample: {testing_start.date()} to {testing_end.date()}")

        try:
            # Create backtest config with optimized params
            backtest_config = BacktestConfig(
                start_date=testing_start,
                end_date=testing_end,
                symbol='BTCUSDT',
                timeframe='15m',
                initial_capital=10000,
                commission_rate=0.001,
                slippage=0.0005,
                max_position_size=params['max_position_size'],
                confidence_threshold=params['confidence_threshold'],
                risk_per_trade=params['risk_per_trade']
            )

            # Run validation backtest
            result = self.engine.run_backtest(backtest_config, 'validation')
            metrics = result['metrics']

            validation_result = {
                'params': params,
                'metrics': metrics,
                'validation_period': {
                    'start': testing_start,
                    'end': testing_end
                },
                'success': True
            }

            logger.info(f"Validation metrics: Return={metrics['total_return']:.2f}%, "
                       f"Sharpe={metrics['sharpe_ratio']:.2f}, "
                       f"Trades={metrics['num_trades']}")

            return validation_result

        except Exception as e:
            logger.error(f"Validation failed: {e}")
            return {
                'params': params,
                'validation_period': {
                    'start': testing_start,
                    'end': testing_end
                },
                'success': False,
                'error': str(e)
            }

class MonteCarloSimulator:
    """Monte Carlo simulation for risk analysis"""

    def __init__(self, engine: BacktestEngine):
        self.engine = engine

    def run_monte_carlo(self, base_returns: pd.Series,
                       num_simulations: int = 1000,
                       confidence_levels: List[float] = None) -> Dict:
        """Run Monte Carlo simulation on returns"""
        if confidence_levels is None:
            confidence_levels = [0.90, 0.95, 0.99]

        logger.info(f"Running Monte Carlo with {num_simulations} simulations")

        # Calculate basic statistics
        mean_return = base_returns.mean()
        std_return = base_returns.std()

        # Generate random returns
        simulated_returns = np.random.normal(
            mean_return,
            std_return,
            (num_simulations, len(base_returns))
        )

        # Calculate cumulative returns
        cumulative_returns = np.cumprod(1 + simulated_returns, axis=1)

        # Calculate statistics
        results = {
            'num_simulations': num_simulations,
            'mean_return': float(mean_return),
            'std_return': float(std_return),
            'final_returns': {
                'mean': float(np.mean(cumulative_returns[:, -1])),
                'median': float(np.median(cumulative_returns[:, -1])),
                'std': float(np.std(cumulative_returns[:, -1])),
                'min': float(np.min(cumulative_returns[:, -1])),
                'max': float(np.max(cumulative_returns[:, -1]))
            }
        }

        # Calculate confidence intervals
        for confidence in confidence_levels:
            alpha = (1 - confidence) / 2
            lower_percentile = alpha * 100
            upper_percentile = (1 - alpha) * 100

            results[f'var_{int(confidence*100)}'] = {
                'value': float(np.percentile(cumulative_returns[:, -1], (1 - confidence) * 100)),
                'confidence_level': confidence,
                'lower_ci': float(np.percentile(cumulative_returns[:, -1], lower_percentile)),
                'upper_ci': float(np.percentile(cumulative_returns[:, -1], upper_percentile))
            }

        # Calculate probability of loss
        prob_loss = np.mean(cumulative_returns[:, -1] < 1.0)
        results['probability_of_loss'] = float(prob_loss)

        logger.info(f"Monte Carlo complete. Mean final return: {results['final_returns']['mean']:.3f}, "
                   f"Prob of loss: {prob_loss:.2%}")

        return results

class WalkForwardAnalyzer:
    """Main walk-forward analysis orchestrator"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            # Get the directory where this script is located
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "config", "parameters.yaml")
        self.engine = BacktestEngine(config_path=config_path)
        self.optimizer = ParameterOptimizer(self.engine)
        self.validator = OutOfSampleValidator(self.engine)
        self.monte_carlo = MonteCarloSimulator(self.engine)

    def run_analysis(self, start_date: datetime, end_date: datetime,
                    config: WalkForwardConfig) -> WalkForwardResult:
        """Run complete walk-forward analysis"""
        logger.info("=" * 80)
        logger.info("STARTING WALK-FORWARD ANALYSIS")
        logger.info("=" * 80)
        logger.info(f"Period: {start_date.date()} to {end_date.date()}")
        logger.info(f"Training: {config.training_period_days} days")
        logger.info(f"Testing: {config.testing_period_days} days")
        logger.info(f"Step size: {config.step_size_days} days")
        logger.info(f"Optimization trials: {config.num_optimization_trials}")
        logger.info("=" * 80)

        # Generate windows
        windows = self._generate_windows(start_date, end_date, config)

        logger.info(f"Generated {len(windows)} analysis windows")
        logger.info("=" * 80)

        # Run optimization for each window
        optimized_results = []
        aggregate_returns = []

        for i, window in enumerate(windows, 1):
            logger.info(f"\nWindow {i}/{len(windows)}")
            logger.info(f"Training: {window['training_start'].date()} to {window['training_end'].date()}")
            logger.info(f"Testing: {window['testing_start'].date()} to {window['testing_end'].date()}")

            # Load data for this window
            # Note: In production, you'd load actual data for each window
            # For now, we'll simulate

            # Optimize parameters on training data
            opt_result = self.optimizer.optimize_parameters(
                config,
                pd.DataFrame(),  # Empty data frame for now
                window['training_start'],
                window['training_end'],
                window['testing_start'],
                window['testing_end']
            )

            # Validate on testing data
            if config.use_out_of_sample:
                validation_result = self.validator.validate_optimized_params(
                    opt_result.params,
                    pd.DataFrame(),
                    window['testing_start'],
                    window['testing_end']
                )

                opt_result.validation = validation_result

            optimized_results.append(opt_result)
            aggregate_returns.append(opt_result.total_return)

        # Calculate aggregate metrics
        aggregate_metrics = self._calculate_aggregate_metrics(optimized_results)

        # Find best and worst windows
        best_window = max(optimized_results, key=lambda x: x.metric_value)
        worst_window = min(optimized_results, key=lambda x: x.metric_value)

        # Calculate parameter stability
        param_stability = self._calculate_parameter_stability(optimized_results)

        # Run Monte Carlo on aggregate returns
        if len(aggregate_returns) > 10:
            mc_results = self.monte_carlo.run_monte_carlo(
                pd.Series(aggregate_returns),
                num_simulations=config.monte_carlo_simulations,
                confidence_levels=config.monte_carlo_confidence_levels
            )
        else:
            mc_results = None

        # Final validation on entire out-of-sample period
        final_validation = None
        if config.use_out_of_sample:
            final_params = self.optimizer.optimize_parameters(
                config,
                pd.DataFrame(),
                start_date,
                start_date + timedelta(days=config.training_period_days),
                end_date - timedelta(days=config.testing_period_days),
                end_date
            )
            final_validation = self.validator.validate_optimized_params(
                final_params.params,
                pd.DataFrame(),
                end_date - timedelta(days=config.testing_period_days),
                end_date
            )

        # Create result
        result = WalkForwardResult(
            start_date=start_date,
            end_date=end_date,
            num_windows=len(windows),
            optimized_params=optimized_results,
            aggregate_metrics=aggregate_metrics,
            best_window=best_window,
            worst_window=worst_window,
            param_stability=param_stability,
            final_validation=final_validation
        )

        # Log summary
        self._log_summary(result, mc_results)

        return result

    def _generate_windows(self, start_date: datetime, end_date: datetime,
                         config: WalkForwardConfig) -> List[Dict]:
        """Generate rolling windows for analysis"""
        windows = []

        current_start = start_date
        training_end = current_start + timedelta(days=config.training_period_days)
        testing_end = training_end + timedelta(days=config.testing_period_days)

        while testing_end <= end_date:
            windows.append({
                'training_start': current_start,
                'training_end': training_end,
                'testing_start': training_end,
                'testing_end': testing_end
            })

            # Move to next window
            current_start = current_start + timedelta(days=config.step_size_days)
            training_end = current_start + timedelta(days=config.training_period_days)
            testing_end = training_end + timedelta(days=config.testing_period_days)

        return windows

    def _calculate_aggregate_metrics(self, results: List[OptimizationResult]) -> Dict:
        """Calculate aggregate metrics across all windows"""
        if not results:
            return {}

        returns = [r.total_return for r in results]
        sharpe_ratios = [r.sharpe_ratio for r in results if r.sharpe_ratio > 0]
        win_rates = [r.win_rate for r in results]
        max_drawdowns = [r.max_drawdown for r in results]
        num_trades = [r.num_trades for r in results]

        metrics = {
            'total_returns': {
                'mean': float(np.mean(returns)),
                'median': float(np.median(returns)),
                'std': float(np.std(returns)),
                'min': float(np.min(returns)),
                'max': float(np.max(returns))
            },
            'sharpe_ratios': {
                'mean': float(np.mean(sharpe_ratios)) if sharpe_ratios else 0,
                'median': float(np.median(sharpe_ratios)) if sharpe_ratios else 0,
                'std': float(np.std(sharpe_ratios)) if sharpe_ratios else 0
            },
            'win_rates': {
                'mean': float(np.mean(win_rates)),
                'median': float(np.median(win_rates)),
                'std': float(np.std(win_rates))
            },
            'max_drawdowns': {
                'mean': float(np.mean(max_drawdowns)),
                'median': float(np.median(max_drawdowns)),
                'worst': float(np.min(max_drawdowns))
            },
            'num_trades': {
                'mean': float(np.mean(num_trades)),
                'median': float(np.median(num_trades)),
                'min': int(np.min(num_trades)),
                'max': int(np.max(num_trades))
            },
            'consistency': {
                'positive_returns_pct': float(np.mean(np.array(returns) > 0)) * 100,
                'sharpe_gt_1_pct': float(np.mean(np.array(sharpe_ratios) > 1.0)) * 100 if sharpe_ratios else 0
            }
        }

        return metrics

    def _calculate_parameter_stability(self, results: List[OptimizationResult]) -> Dict:
        """Calculate stability of optimized parameters across windows"""
        if not results:
            return {}

        confidence_thresholds = [r.params['confidence_threshold'] for r in results]
        risk_per_trade = [r.params['risk_per_trade'] for r in results]
        max_position = [r.params['max_position_size'] for r in results]

        return {
            'confidence_threshold': {
                'mean': float(np.mean(confidence_thresholds)),
                'std': float(np.std(confidence_thresholds)),
                'cv': float(np.std(confidence_thresholds) / np.mean(confidence_thresholds)) if np.mean(confidence_thresholds) > 0 else 0,
                'most_common': float(self._find_most_common(confidence_thresholds, bin_size=0.05))
            },
            'risk_per_trade': {
                'mean': float(np.mean(risk_per_trade)),
                'std': float(np.std(risk_per_trade)),
                'cv': float(np.std(risk_per_trade) / np.mean(risk_per_trade)) if np.mean(risk_per_trade) > 0 else 0,
                'most_common': float(self._find_most_common(risk_per_trade, bin_size=0.005))
            },
            'max_position': {
                'mean': float(np.mean(max_position)),
                'std': float(np.std(max_position)),
                'cv': float(np.std(max_position) / np.mean(max_position)) if np.mean(max_position) > 0 else 0,
                'most_common': float(self._find_most_common(max_position, bin_size=0.1))
            }
        }

    def _find_most_common(self, values: List[float], bin_size: float) -> float:
        """Find the most common value by binning"""
        if not values:
            return 0

        bins = np.arange(min(values), max(values) + bin_size, bin_size)
        hist, _ = np.histogram(values, bins=bins)
        most_common_idx = np.argmax(hist)
        return bins[most_common_idx]

    def _log_summary(self, result: WalkForwardResult, mc_results: Optional[Dict]):
        """Log analysis summary"""
        logger.info("\n" + "=" * 80)
        logger.info("WALK-FORWARD ANALYSIS SUMMARY")
        logger.info("=" * 80)

        metrics = result.aggregate_metrics

        logger.info(f"\n📊 Aggregate Performance ({result.num_windows} windows):")
        logger.info(f"   Total Return:     {metrics['total_returns']['mean']:.2f}% (±{metrics['total_returns']['std']:.2f}%)")
        logger.info(f"   Sharpe Ratio:     {metrics['sharpe_ratios']['mean']:.2f} (±{metrics['sharpe_ratios']['std']:.2f})")
        logger.info(f"   Win Rate:         {metrics['win_rates']['mean']:.1f}% (±{metrics['win_rates']['std']:.1f}%)")
        logger.info(f"   Max Drawdown:     {metrics['max_drawdowns']['mean']:.2f}% (worst: {metrics['max_drawdowns']['worst']:.2f}%)")

        logger.info(f"\n🎯 Best Window:")
        logger.info(f"   Return:           {result.best_window.total_return:.2f}%")
        logger.info(f"   Sharpe:           {result.best_window.metric_value:.2f}")
        logger.info(f"   Params:           {result.best_window.params}")

        logger.info(f"\n⚠️  Worst Window:")
        logger.info(f"   Return:           {result.worst_window.total_return:.2f}%")
        logger.info(f"   Sharpe:           {result.worst_window.metric_value:.2f}")

        logger.info(f"\n🔄 Parameter Stability:")
        logger.info(f"   Confidence:       {result.param_stability['confidence_threshold']['mean']:.2f} "
                   f"(CV: {result.param_stability['confidence_threshold']['cv']:.2f})")
        logger.info(f"   Risk/Trade:       {result.param_stability['risk_per_trade']['mean']:.3f} "
                   f"(CV: {result.param_stability['risk_per_trade']['cv']:.2f})")

        if mc_results:
            logger.info(f"\n📈 Monte Carlo ({mc_results['num_simulations']} simulations):")
            logger.info(f"   Mean Final Return: {mc_results['final_returns']['mean']:.3f}")
            logger.info(f"   Prob of Loss:      {mc_results['probability_of_loss']:.2%}")
            for conf in [0.90, 0.95, 0.99]:
                var_key = f'var_{int(conf*100)}'
                if var_key in mc_results:
                    logger.info(f"   VaR {int(conf*100)}:           {mc_results[var_key]['value']:.3f}")

        if result.final_validation:
            logger.info(f"\n✅ Final Validation:")
            logger.info(f"   Success:          {result.final_validation['success']}")
            if result.final_validation['success']:
                logger.info(f"   Return:           {result.final_validation['metrics']['total_return']:.2f}%")
                logger.info(f"   Sharpe:           {result.final_validation['metrics']['sharpe_ratio']:.2f}")

        logger.info("=" * 80)

async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Run Walk-Forward Analysis')
    parser.add_argument('--start-date', required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', required=True, help='End date (YYYY-MM-DD)')
    parser.add_argument('--training-days', type=int, default=180, help='Training period in days')
    parser.add_argument('--testing-days', type=int, default=30, help='Testing period in days')
    parser.add_argument('--step-days', type=int, default=7, help='Step size in days')
    parser.add_argument('--trials', type=int, default=50, help='Number of optimization trials')
    parser.add_argument('--output', help='Output JSON file')

    args = parser.parse_args()

    # Create config
    config = WalkForwardConfig(
        training_period_days=args.training_days,
        testing_period_days=args.testing_days,
        step_size_days=args.step_days,
        num_optimization_trials=args.trials
    )

    # Create analyzer
    analyzer = WalkForwardAnalyzer()

    # Run analysis
    start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
    end_date = datetime.strptime(args.end_date, '%Y-%m-%d')

    result = analyzer.run_analysis(start_date, end_date, config)

    # Save results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(asdict(result), f, indent=2, default=str)
        logger.info(f"\n✅ Results saved to: {args.output}")

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())