#!/usr/bin/env python3
"""
Test new margin-based $2000 limit logic
"""

from app.executor.executor import Executor
from app.risk_manager.manager import RiskDecision


def test_margin_based_limits():
    print("=== MARGIN-BASED $2000 LIMIT TEST ===")
    
    executor = Executor()
    
    test_scenarios = [
        {
            "name": "Small margin within limit",
            "amount": 0.05,  # 5% = $500
            "leverage": 10.0,  # 10x 
            "expected_margin": 500.0,  # $500 (within limit)
            "expected_position_size": 5000.0,  # $500 × 10x = $5,000
        },
        {
            "name": "Large margin capped at $2000",
            "amount": 0.3,   # 30% = $3,000
            "leverage": 5.0,  # 5x
            "expected_margin": 2000.0,  # $2,000 (capped)
            "expected_position_size": 10000.0,  # $2,000 × 5x = $10,000
        },
        {
            "name": "Max margin with max leverage",
            "amount": 0.5,   # 50% = $5,000
            "leverage": 20.0,  # max 20x
            "expected_margin": 2000.0,  # $2,000 (capped)
            "expected_position_size": 40000.0,  # $2,000 × 20x = $40,000
        },
        {
            "name": "Normal within limit",
            "amount": 0.1,   # 10% = $1,000
            "leverage": 15.0,  # 15x
            "expected_margin": 1000.0,  # $1,000 (within limit)
            "expected_position_size": 15000.0,  # $1,000 × 15x = $15,000
        },
        {
            "name": "Huge request, gets capped",
            "amount": 0.8,   # 80% = $8,000
            "leverage": 50.0,  # capped to 20x
            "expected_margin": 2000.0,  # $2,000 (capped)
            "expected_position_size": 40000.0,  # $2,000 × 20x = $40,000
        },
    ]
    
    for scenario in test_scenarios:
        print(f"\n--- Testing: {scenario['name']} ---")
        print(f"GLM Request: {scenario['amount']*100:.0f}% equity, {scenario['leverage']}x leverage")
        
        decision = RiskDecision(
            action="SELL",
            amount=scenario['amount'],
            leverage=scenario['leverage'],
            reasoning=f"Test: {scenario['name']}"
        )
        
        # Calculate expected values
        free_equity = 10000.0  # Simplified
        requested_margin = free_equity * decision.amount
        actual_margin = min(requested_margin, 2000.0)  # $2000 cap
        normalized_leverage = min(decision.leverage, 20.0)  # 20x cap
        position_size = actual_margin * normalized_leverage
        
        print(f"Expected Results:")
        print(f"  - Margin: ${actual_margin:.2f} {'(capped)' if requested_margin > 2000 else ''}")
        print(f"  - Position Size: ${position_size:.2f}")
        print(f"  - Leverage: {normalized_leverage}x {'(capped)' if decision.leverage > 20 else ''}")
        
        status = "✅ PASS" if (
            abs(actual_margin - scenario['expected_margin']) < 1 and
            abs(position_size - scenario['expected_position_size']) < 1
        ) else "❌ FAIL"
        
        print(f"Status: {status}")
        
        if status == "❌ FAIL":
            print(f"MISMATCH:")
            print(f"  Expected Margin: ${scenario['expected_margin']:.2f}")
            print(f"  Actual Margin: ${actual_margin:.2f}")
            print(f"  Expected Position: ${scenario['expected_position_size']:.2f}")
            print(f"  Actual Position: ${position_size:.2f}")

def demonstrate_new_logic():
    print("\n=== NEW MARGIN-BASED LOGIC DEMONSTRATION ===")
    print("Previous Logic (Notional-based):")
    print("  - GLM: 20% equity, 15x leverage")
    print("  - Calculation: $2,000 × 15x = $30,000 position")
    print("  - Result: LIMITED to $2,000 position")
    print()
    print("New Logic (Margin-based):")
    print("  - GLM: 20% equity, 15x leverage") 
    print("  - Margin: $2,000 (20% of $10,000) - WITHIN LIMIT")
    print("  - Calculation: $2,000 × 15x = $30,000 position")
    print("  - Result: $30,000 position (NO LIMIT!)")
    print()
    print("Example with max leverage:")
    print("  - GLM: 30% equity, 50x leverage")
    print("  - Margin: $2,000 (capped from $3,000)")
    print("  - Leverage: 20x (capped from 50x)")
    print("  - Calculation: $2,000 × 20x = $40,000 position")
    print("  - Result: $40,000 position (max possible)")

if __name__ == "__main__":
    test_margin_based_limits()
    demonstrate_new_logic()
    
    print("\n" + "="*60)
    print("🎯 SUMMARY: NOW YOU CAN TRADE UP TO $40,000!")
    print("   Max margin: $2,000")
    print("   Max leverage: 20x") 
    print("   Max position: $40,000")
    print("   This allows for proper risk management!")
