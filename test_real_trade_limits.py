#!/usr/bin/env python3
"""
Test real trade execution with $2000 and 20x limits
"""

from app.executor.executor import Executor
from app.risk_manager.manager import RiskDecision


def test_real_trade_scenarios():
    print("=== TEST REAL TRADE LIMITS ===")
    
    executor = Executor()
    
    test_scenarios = [
        {
            "name": "Small trade within limits",
            "action": "SELL", 
            "amount": 0.05,  # 5% equity = $500
            "leverage": 5.0,  # 5x = $2500 position -> will be capped to $2000
            "expected_position_size": 2000.0
        },
        {
            "name": "Large trade request",
            "action": "BUY",
            "amount": 0.3,   # 30% equity = $3000  
            "leverage": 15.0, # 15x = $45,000 position -> will be capped to $2000
            "expected_position_size": 2000.0
        },
        {
            "name": "Very high leverage request",
            "action": "SELL",
            "amount": 0.1,   # 10% equity = $1000
            "leverage": 50.0, # 50x = $50,000 -> capped to 20x = $20,000 -> capped to $2000
            "expected_position_size": 2000.0
        },
        {
            "name": "Normal trade request",
            "action": "SELL", 
            "amount": 0.1,   # 10% equity = $1000
            "leverage": 15.0, # 15x = $15,000 -> capped to $2000
            "expected_position_size": 2000.0
        },
        {
            "name": "Very small trade",
            "action": "BUY",
            "amount": 0.02,  # 2% equity = $200
            "leverage": 10.0, # 10x = $2000 (at limit)
            "expected_position_size": 2000.0
        }
    ]
    
    for scenario in test_scenarios:
        print(f"\n--- Testing: {scenario['name']} ---")
        print(f"Request: {scenario['amount']*100:.1f}% equity, {scenario['leverage']}x leverage")
        
        decision = RiskDecision(
            action=scenario['action'],
            amount=scenario['amount'],
            leverage=scenario['leverage'],
            reasoning=f"Test scenario: {scenario['name']}"
        )
        
        # Calculate what the executor should do
        print(f"Expected position size: ${scenario['expected_position_size']:,.2f}")
        
        # Execute the decision
        result = executor.execute(decision)
        print(f"Result: {result.status} - {result.details}")
        
        # Check current position
        metrics = executor.portfolio_metrics()
        print(f"Current metrics:")
        print(f"  - Position: {metrics['position']:.4f} BTC")
        print(f"  - Position value: ${abs(metrics['position'] * metrics['price']):,.2f}")
        print(f"  - Equity: ${metrics['equity']:,.2f}")
        print(f"  - Total PnL: ${metrics['total_pnl']:,.2f}")

def test_close_scenario():
    print("\n=== TEST CLOSE SCENARIO ===")
    
    executor = Executor()
    
    # First create a position
    print("Creating a test position...")
    open_decision = RiskDecision(
        action="SELL",
        amount=0.1,
        leverage=10.0,
        reasoning="Create test position to close"
    )
    
    open_result = executor.execute(open_decision)
    print(f"Open result: {open_result.status}")
    
    # Check position
    metrics_after = executor.portfolio_metrics()
    print(f"Position after open: {metrics_after['position']:.4f} BTC")
    print(f"Position value: ${abs(metrics_after['position'] * metrics_after['price']):,.2f}")
    
    # Now close it
    print("\nClosing the position...")
    close_decision = RiskDecision(
        action="CLOSE",
        amount=1.0,
        leverage=1.0,
        reasoning="Test close action"
    )
    
    close_result = executor.execute(close_decision)
    print(f"Close result: {close_result.status} - {close_result.details}")
    
    # Check final position
    metrics_final = executor.portfolio_metrics()
    print(f"Final position: {metrics_final['position']:.4f} BTC")
    print(f"Final equity: ${metrics_final['equity']:,.2f}")
    print(f"Final total PnL: ${metrics_final['total_pnl']:,.2f}")

if __name__ == "__main__":
    test_real_trade_scenarios()
    test_close_scenario()
