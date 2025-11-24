#!/usr/bin/env python3
"""
Test portfolio_metrics function after reset
"""

import sys
import os
sys.path.append('/root/trading')

from app.executor.executor import Executor

def test_portfolio_metrics():
    """Test that portfolio_metrics returns correct values after reset"""
    print("🧪 Testing portfolio_metrics after reset...")

    # Create executor instance
    executor = Executor(symbol="BTCUSDT")

    # Get portfolio metrics
    metrics = executor.portfolio_metrics()

    print("\n📊 Portfolio Metrics:")
    for key, value in metrics.items():
        if isinstance(value, float):
            print(f"  {key}: ${value:.2f}" if 'equity' in key or 'pnl' in key or 'cash' in key else f"  {key}: {value:.6f}")
        else:
            print(f"  {key}: {value}")

    # Check key values
    total_equity = metrics.get('equity', 0)
    starting_cash = 10000.0

    print(f"\n✅ Verification:")
    print(f"  Expected equity: ${starting_cash:.2f}")
    print(f"  Actual equity: ${total_equity:.2f}")

    if abs(total_equity - starting_cash) < 0.01:
        print("  🟢 Equity matches expected value!")
        return True
    else:
        print(f"  🔴 Equity mismatch! Difference: ${total_equity - starting_cash:.2f}")
        return False

if __name__ == "__main__":
    success = test_portfolio_metrics()
    if success:
        print("\n🎉 Portfolio reset verification PASSED!")
    else:
        print("\n❌ Portfolio reset verification FAILED!")
        sys.exit(1)