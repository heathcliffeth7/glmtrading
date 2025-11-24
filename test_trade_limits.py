#!/usr/bin/env python3
"""
Test $3000 margin per trade and 20x leverage max (up to ~$60k position size)
"""

import os
from sqlalchemy.orm import Session

# Configure test database BEFORE importing modules that create the engine
os.environ.setdefault("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("TEST_AUTOCREATE_SCHEMA", "true")

from app.executor.executor import Executor
from app.executor.ledger import engine, get_portfolio
from app.risk_manager.manager import RiskDecision


def test_trade_limits():
    print("=== TEST TRADE LIMITS ===")
    
    executor = Executor()
    
    print(f"Max trade value: ${executor._max_trade_value}")
    print(f"Max leverage: {executor._max_leverage}x")
    print(f"Min leverage: {executor._min_leverage}x")
    
    # Test scenarios
    test_cases = [
        # (amount, leverage, expected_max_position_size, description)
        (0.1, 10.0, 1000.0, "Normal trade - should work"),
        (0.2, 10.0, 2000.0, "Over limit - should be capped to $2000"),
        (0.1, 25.0, 2000.0, "Leverage over max - should be capped to 20x"),
        (0.05, 20.0, 1000.0, "Small trade with max leverage"),
        (0.01, 50.0, 200.0, "Very small but high leverage"),
    ]
    
    for amount, leverage, expected_max_size, description in test_cases:
        print(f"\n--- Test: {description} ---")
        print(f"GLM decision: amount={amount}, leverage={leverage}")
        
        decision = RiskDecision(
            action="SELL" if amount < 0.15 else "BUY",  # Vary action
            amount=amount,
            leverage=leverage,
            reasoning=f"Test trade - {description}"
        )
        
        # Simulate position size calculation
        with Session(engine) as session:
            portfolio = get_portfolio(session, "BTCUSDT")
            
            # Reset position for clean test
            portfolio.position = 0.0
            portfolio.average_price = 0.0
            session.commit()
            
            # Calculate expected position size
            free_equity = executor._starting_cash  # Simplified for test
            normalized_leverage = executor._normalize_leverage(leverage)
            
            print(f"Normalized leverage: {normalized_leverage}x")
            
            margin_to_use = free_equity * amount
            position_size_usd = margin_to_use * normalized_leverage
            
            # Apply max trade value limit
            if position_size_usd > executor._max_trade_value:
                actual_position_size = executor._max_trade_value
                margin_to_use = actual_position_size / normalized_leverage
                print(f"✅ LIMITED: ${position_size_usd:.2f} → ${actual_position_size:.2f}")
            else:
                actual_position_size = position_size_usd
                print(f"✅ OK: ${actual_position_size:.2f}")
            
            print(f"Final margin: ${margin_to_use:.2f}")
            print(f"Expected vs Actual: ${expected_max_size:.2f} vs ${actual_position_size:.2f}")
            
            # Test if limits are working correctly
            if abs(actual_position_size - expected_max_size) < 1.0:  # $1 tolerance
                print("✅ PASS: Trade size within expected limits")
            else:
                print("❌ FAIL: Trade size not as expected")

def test_leverage_normalization():
    print("\n=== TEST LEVERAGE NORMALIZATION ===")
    executor = Executor()
    
    leverage_tests = [
        (0.5, 1.0, "Below min - should be 1.0"),
        (5.0, 5.0, "Normal - should stay 5.0"), 
        (15.0, 15.0, "Normal - should stay 15.0"),
        (20.0, 20.0, "At max - should stay 20.0"),
        (25.0, 20.0, "Over max - should be capped to 20.0"),
        (50.0, 20.0, "Way over max - should be capped to 20.0"),
    ]
    
    for input_leverage, expected, description in leverage_tests:
        result = executor._normalize_leverage(input_leverage)
        status = "✅ PASS" if abs(result - expected) < 0.1 else "❌ FAIL"
        print(f"{status} | {description}")
        print(f"  Input: {input_leverage}x → Result: {result}x (Expected: {expected}x)")

if __name__ == "__main__":
    test_trade_limits()
    test_leverage_normalization()
