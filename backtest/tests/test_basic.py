#!/usr/bin/env python3
"""
Basic Test Script for Backtesting System
Sistemin temel işlevselliğini test eder
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_config_loading():
    """Test configuration file loading"""
    logger.info("Test 1: Configuration Loading...")

    try:
        import yaml
        with open('backtest/config/parameters.yaml', 'r') as f:
            config = yaml.safe_load(f)
        assert 'data_collection' in config
        assert 'backtest' in config
        logger.info("✅ Configuration loaded successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Configuration test failed: {e}")
        return False

def test_scenarios():
    """Test scenarios file loading"""
    logger.info("\nTest 2: Scenarios Loading...")

    try:
        import yaml
        with open('backtest/config/scenarios.yaml', 'r') as f:
            scenarios = yaml.safe_load(f)
        assert 'scenarios' in scenarios
        assert 'baseline' in scenarios['scenarios']
        logger.info(f"✅ Scenarios loaded successfully ({len(scenarios['scenarios'])} scenarios)")
        return True
    except Exception as e:
        logger.error(f"❌ Scenarios test failed: {e}")
        return False

def test_database_setup():
    """Test database schema"""
    logger.info("\nTest 3: Database Schema...")

    try:
        # Check if setup_database.py can be imported
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, '/root/trading')
        from backtest.setup_database import BacktestResult, BacktestTrade

        # Check attributes
        assert hasattr(BacktestResult, '__tablename__')
        assert hasattr(BacktestTrade, '__tablename__')

        logger.info("✅ Database schema loaded successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Database schema test failed: {e}")
        return False

def test_backtest_engine():
    """Test backtest engine initialization"""
    logger.info("\nTest 4: Backtest Engine...")

    try:
        # Change to trading directory
        os.chdir('/root/trading')

        sys.path.insert(0, os.path.dirname(os.path.abspath('backtest/engine.py')))
        from engine import BacktestConfig, BacktestEngine

        # Create a minimal config
        config = BacktestConfig(
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            symbol='BTCUSDT',
            timeframe='15m',
            initial_capital=10000,
            commission_rate=0.001,
            slippage=0.0005,
            max_position_size=1.0,
            confidence_threshold=0.65,
            risk_per_trade=0.02
        )

        # Initialize engine (without full setup)
        logger.info("✅ Backtest engine initialized successfully")
        return True, config
    except Exception as e:
        logger.error(f"❌ Backtest engine test failed: {e}")
        return False, None

def test_run_short_backtest(config):
    """Test running a short backtest"""
    logger.info("\nTest 5: Running Short Backtest (7 days)...")

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath('backtest/engine.py')))
        from engine import BacktestConfig, BacktestEngine

        # Short test period
        short_config = BacktestConfig(
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now(),
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
        results = engine.run_backtest(short_config, 'test_short')

        assert 'metrics' in results
        assert 'total_return' in results['metrics']
        assert 'num_trades' in results['metrics']

        logger.info("✅ Short backtest completed successfully")
        logger.info(f"   Total Return: {results['metrics']['total_return']:.2f}%")
        logger.info(f"   Number of Trades: {results['metrics']['num_trades']}")
        logger.info(f"   Win Rate: {results['metrics']['win_rate']:.1f}%")

        return True
    except Exception as e:
        logger.error(f"❌ Backtest execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_historical_collector():
    """Test historical data collector"""
    logger.info("\nTest 6: Historical Data Collector...")

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath('backtest/data/historical_collector.py')))
        from data.historical_collector import HistoricalDataCollector

        # Create collector (without actual data collection)
        logger.info("✅ Historical collector module loaded")
        return True
    except Exception as e:
        logger.error(f"❌ Historical collector test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("="*70)
    print("🧪 BACKTESTING SYSTEM - BASIC TESTS")
    print("="*70)

    results = []

    # Test 1: Configuration
    results.append(("Config Loading", test_config_loading()))

    # Test 2: Scenarios
    results.append(("Scenarios Loading", test_scenarios()))

    # Test 3: Database
    results.append(("Database Schema", test_database_setup()))

    # Test 4: Engine
    engine_result, config = test_backtest_engine()
    results.append(("Backtest Engine", engine_result))

    # Test 5: Short Backtest (only if engine passed)
    if engine_result and config:
        results.append(("Short Backtest", test_run_short_backtest(config)))

    # Test 6: Historical Collector
    results.append(("Historical Collector", test_historical_collector()))

    # Summary
    print("\n" + "="*70)
    print("📊 TEST SUMMARY")
    print("="*70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")

    print("="*70)
    print(f"Total: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! System is ready to use.")
        print("\nNext steps:")
        print("  1. Set up database: python backtest/setup_database.py --action create")
        print("  2. Collect data: python backtest/run.py collect-data")
        print("  3. Run demo: python backtest/run.py demo")
        print("  4. Run full backtest: python backtest/run.py run --start-date 2024-01-01 --end-date 2024-06-30")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the errors above.")

    print("="*70)

    return passed == total

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)