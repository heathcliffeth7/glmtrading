#!/usr/bin/env python3
"""
Backtesting CLI Interface
Kolay backtest çalıştırma için komut satırı arayüzü
"""

import sys
import os
import asyncio
import click
from datetime import datetime
import yaml
import json
from pathlib import Path

# Add path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import BacktestEngine, BacktestConfig
from data.historical_collector import HistoricalDataCollector
from setup_database import create_tables, verify_tables

@click.group()
def cli():
    """AI Trading Backtesting System CLI"""
    pass

@cli.command()
@click.option('--run-name', default='backtest', help='Backtest run name')
@click.option('--start-date', required=True, help='Start date (YYYY-MM-DD)')
@click.option('--end-date', required=True, help='End date (YYYY-MM-DD)')
@click.option('--timeframe', default='15m', type=click.Choice(['1m', '5m', '15m', '30m', '1h', '4h']))
@click.option('--initial-capital', default=10000, help='Initial capital (USDT)')
@click.option('--confidence-threshold', default=0.65, help='Signal confidence threshold')
@click.option('--risk-per-trade', default=0.02, help='Risk per trade (0.02 = 2%)')
@click.option('--max-position', default=1.0, help='Max position size (BTC)')
@click.option('--output', type=click.Path(), help='Output JSON file (optional)')
def run(run_name, start_date, end_date, timeframe, initial_capital,
        confidence_threshold, risk_per_trade, max_position, output):
    """Run a single backtest"""
    click.echo(f"\n{'='*60}")
    click.echo(f"🚀 Starting Backtest: {run_name}")
    click.echo(f"{'='*60}\n")

    # Create config
    config = BacktestConfig(
        start_date=datetime.strptime(start_date, '%Y-%m-%d'),
        end_date=datetime.strptime(end_date, '%Y-%m-%d'),
        symbol='BTCUSDT',
        timeframe=timeframe,
        initial_capital=initial_capital,
        commission_rate=0.001,
        slippage=0.0005,
        max_position_size=max_position,
        confidence_threshold=confidence_threshold,
        risk_per_trade=risk_per_trade
    )

    # Run backtest
    engine = BacktestEngine()
    results = engine.run_backtest(config, run_name)

    # Display results
    click.echo(f"\n{'='*60}")
    click.echo("📊 BACKTEST RESULTS")
    click.echo(f"{'='*60}\n")

    metrics = results['metrics']
    click.echo(f"💰 Total Return:        {metrics['total_return']:.2f}%")
    click.echo(f"📈 Annual Return:       {metrics['annual_return']:.2f}%")
    click.echo(f"📊 Benchmark (BTC):     {metrics['benchmark_return']:.2f}%")
    click.echo(f"⚡ Sharpe Ratio:        {metrics['sharpe_ratio']:.2f}")
    click.echo(f"📉 Max Drawdown:        {metrics['max_drawdown']:.2f}%")
    click.echo(f"🔢 Number of Trades:    {metrics['num_trades']}")
    click.echo(f"✅ Win Rate:            {metrics['win_rate']:.1f}%")
    click.echo(f"💎 Profit Factor:       {metrics['profit_factor']:.2f}")

    click.echo(f"\n{'='*60}\n")

    # Save to file
    if output:
        with open(output, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        click.echo(f"✅ Results saved to: {output}\n")

@cli.command()
@click.option('--scenario', required=True, help='Scenario name from scenarios.yaml')
@click.option('--output', type=click.Path(), help='Output directory for results')
def run_scenario(scenario, output):
    """Run a predefined scenario"""
    click.echo(f"\n🚀 Running Scenario: {scenario}\n")

    # Load scenario config
    scenarios_path = Path(__file__).parent / 'config' / 'scenarios.yaml'
    with open(scenarios_path, 'r') as f:
        scenarios = yaml.safe_load(f)

    if scenario not in scenarios['scenarios']:
        click.echo(f"❌ Scenario '{scenario}' not found!", fg='red')
        return

    scenario_config = scenarios['scenarios'][scenario]
    params = scenario_config['parameters']

    # Create config
    config = BacktestConfig(
        start_date=datetime.strptime(params['start_date'], '%Y-%m-%d'),
        end_date=datetime.strptime(params['end_date'], '%Y-%m-%d'),
        symbol='BTCUSDT',
        timeframe=params['timeframe'],
        initial_capital=10000,
        commission_rate=0.001,
        slippage=0.0005,
        max_position_size=params.get('max_position_size', 1.0),
        confidence_threshold=params['confidence_threshold'],
        risk_per_trade=params['risk_per_trade']
    )

    # Run backtest
    engine = BacktestEngine()
    results = engine.run_backtest(config, scenario)

    # Save results
    if output:
        os.makedirs(output, exist_ok=True)
        results_file = Path(output) / f'{scenario}_results.json'
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        click.echo(f"✅ Results saved to: {results_file}\n")

@cli.command()
def list_scenarios():
    """List all available scenarios"""
    click.echo("\n📋 Available Scenarios:\n")

    scenarios_path = Path(__file__).parent / 'config' / 'scenarios.yaml'
    with open(scenarios_path, 'r') as f:
        scenarios = yaml.safe_load(f)

    for key, scenario in scenarios['scenarios'].items():
        click.echo(f"  {key}: {scenario['name']}")
        click.echo(f"    {scenario['description']}\n")

@cli.command()
@click.option('--timeframe', help='Specific timeframe to collect')
def collect_data(timeframe):
    """Collect historical data from Binance"""
    click.echo("\n📥 Collecting Historical Data...\n")

    collector = HistoricalDataCollector()

    if timeframe:
        asyncio.run(collector.collect_timeframe_data(timeframe))
    else:
        asyncio.run(collector.collect_all_timeframes())

@cli.command()
@click.option('--verify-timeframe', help='Verify specific timeframe')
def verify_data(verify_timeframe):
    """Verify collected data"""
    click.echo("\n🔍 Verifying Data...\n")

    collector = HistoricalDataCollector()

    if verify_timeframe:
        count = asyncio.run(collector.verify_data(verify_timeframe))
        click.echo(f"\n✅ {verify_timeframe}: {count} bars\n")
    else:
        for tf in collector.timeframes:
            count = asyncio.run(collector.verify_data(tf))
            click.echo(f"{tf}: {count} bars")

@cli.command()
def init_db():
    """Initialize backtest database tables"""
    click.echo("\n🗄️  Initializing Database...\n")

    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        click.echo("❌ DATABASE_URL not set!", fg='red')
        return

    if create_tables(db_url):
        click.echo("✅ Database initialized successfully!\n")

@cli.command()
def setup():
    """Complete setup: initialize database and collect data"""
    click.echo("\n⚙️  Running Complete Setup...\n")

    # Initialize database
    click.echo("1. Initializing database...")
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        create_tables(db_url)
    else:
        click.echo("⚠️  DATABASE_URL not set, skipping database setup")

    # Collect data
    click.echo("\n2. Collecting historical data...")
    collector = HistoricalDataCollector()
    asyncio.run(collector.collect_all_timeframes())

    click.echo("\n✅ Setup complete!\n")

@cli.command()
@click.option('--run-name', default='demo', help='Run name')
def demo(run_name):
    """Run a quick demo backtest"""
    click.echo("\n🎯 Running Quick Demo...\n")

    # Demo period: Last 30 days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        symbol='BTCUSDT',
        timeframe='15m',
        initial_capital=10000,
        commission_rate=0.001,
        slippage=0.0005,
        max_position_size=1.0,
        confidence_threshold=0.65,
        risk_per_trade=0.02
    )

    engine = BacktestEngine()
    results = engine.run_backtest(config, run_name)

    metrics = results['metrics']
    click.echo(f"\n{'='*60}")
    click.echo("📊 DEMO RESULTS (Last 30 days)")
    click.echo(f"{'='*60}\n")
    click.echo(f"💰 Total Return:     {metrics['total_return']:.2f}%")
    click.echo(f"🔢 Trades:           {metrics['num_trades']}")
    click.echo(f"✅ Win Rate:         {metrics['win_rate']:.1f}%")
    click.echo(f"💎 Profit Factor:    {metrics['profit_factor']:.2f}")
    click.echo(f"\n{'='*60}\n")

@cli.group()
def walkforward():
    """Walk-Forward Analysis commands"""
    pass

@walkforward.command()
@click.option('--start-date', required=True, help='Start date (YYYY-MM-DD)')
@click.option('--end-date', required=True, help='End date (YYYY-MM-DD)')
@click.option('--training-days', default=180, help='Training period in days (default: 180)')
@click.option('--testing-days', default=30, help='Testing period in days (default: 30)')
@click.option('--step-days', default=7, help='Step size in days (default: 7)')
@click.option('--trials', default=50, help='Number of optimization trials (default: 50)')
@click.option('--output', help='Output JSON file')
def analyze(start_date, end_date, training_days, testing_days, step_days, trials, output):
    """Run walk-forward analysis"""
    click.echo(f"\n{'='*70}")
    click.echo("🔄 WALK-FORWARD ANALYSIS")
    click.echo(f"{'='*70}\n")

    click.echo(f"Period: {start_date} to {end_date}")
    click.echo(f"Training: {training_days} days | Testing: {testing_days} days | Step: {step_days} days")
    click.echo(f"Optimization trials: {trials}\n")

    # Import here to avoid issues
    from walk_forward import WalkForwardAnalyzer, WalkForwardConfig

    # Create config
    config = WalkForwardConfig(
        training_period_days=int(training_days),
        testing_period_days=int(testing_days),
        step_size_days=int(step_days),
        num_optimization_trials=int(trials)
    )

    # Create analyzer
    analyzer = WalkForwardAnalyzer()

    # Run analysis
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')

    result = analyzer.run_analysis(start, end, config)

    # Display summary
    metrics = result.aggregate_metrics
    click.echo(f"\n{'='*70}")
    click.echo("📊 AGGREGATE RESULTS")
    click.echo(f"{'='*70}\n")
    click.echo(f"💰 Mean Return:      {metrics['total_returns']['mean']:.2f}%")
    click.echo(f"📈 Sharpe Ratio:     {metrics['sharpe_ratios']['mean']:.2f}")
    click.echo(f"✅ Win Rate:         {metrics['win_rates']['mean']:.1f}%")
    click.echo(f"📉 Max Drawdown:     {metrics['max_drawdowns']['mean']:.2f}%")
    click.echo(f"🔢 Consistency:      {metrics['consistency']['positive_returns_pct']:.1f}% positive windows")
    click.echo(f"\n{'='*70}\n")

    # Save results
    if output:
        import json
        from dataclasses import asdict
        with open(output, 'w') as f:
            json.dump(asdict(result), f, indent=2, default=str)
        click.echo(f"✅ Results saved to: {output}\n")

@walkforward.command()
@click.option('--start-date', required=True, help='Start date (YYYY-MM-DD)')
@click.option('--end-date', required=True, help='End date (YYYY-MM-DD)')
@click.option('--output', help='Output JSON file')
def quick(start_date, end_date, output):
    """Run quick walk-forward analysis (fewer trials)"""
    click.echo("\n⚡ Quick Walk-Forward Analysis (30 trials)\n")

    from walk_forward import WalkForwardAnalyzer, WalkForwardConfig

    config = WalkForwardConfig(
        training_period_days=90,
        testing_period_days=30,
        step_size_days=7,
        num_optimization_trials=30
    )

    analyzer = WalkForwardAnalyzer()

    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')

    result = analyzer.run_analysis(start, end, config)

    click.echo(f"\n{'='*70}")
    click.echo("📊 QUICK ANALYSIS RESULTS")
    click.echo(f"{'='*70}\n")

    metrics = result.aggregate_metrics
    click.echo(f"💰 Mean Return:      {metrics['total_returns']['mean']:.2f}%")
    click.echo(f"📈 Sharpe Ratio:     {metrics['sharpe_ratios']['mean']:.2f}")
    click.echo(f"✅ Win Rate:         {metrics['win_rates']['mean']:.1f}%")

    if output:
        import json
        from dataclasses import asdict
        with open(output, 'w') as f:
            json.dump(asdict(result), f, indent=2, default=str)
        click.echo(f"\n✅ Results saved to: {output}\n")

@walkforward.command()
def help():
    """Show walk-forward analysis help"""
    click.echo("\n🔄 Walk-Forward Analysis\n")
    click.echo("Walk-forward analysis tests your strategy on historical data by:")
    click.echo("  1. Training on past data (e.g., 6 months)")
    click.echo("  2. Testing on following data (e.g., 1 month)")
    click.echo("  3. Repeating with rolling windows")
    click.echo("  4. Optimizing parameters for each window")
    click.echo("")
    click.echo("Commands:")
    click.echo("  analyze  - Full analysis with many optimization trials")
    click.echo("  quick    - Faster analysis with fewer trials")
    click.echo("")
    click.echo("Example:")
    click.echo("  python backtest/run.py walkforward analyze \\")
    click.echo("    --start-date 2023-01-01 \\")
    click.echo("    --end-date 2024-12-31 \\")
    click.echo("    --training-days 180 \\")
    click.echo("    --testing-days 30 \\")
    click.echo("    --output wf_results.json")
    click.echo("\n")

if __name__ == '__main__':
    from datetime import timedelta
    cli()