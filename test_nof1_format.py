#!/usr/bin/env python3
"""
Test NOF1.AI response format
"""

import json


def test_nof1_response_format():
    """Test what format GLM is returning vs what we expect"""
    
    # This is the format we expect from GLM (based on nof1_prompt_builder.py)
    expected_format = {
        "BTCUSDT": {
            "trade_signal_args": {
                "coin": "BTCUSDT",
                "signal": "BUY|SELL|HOLD|CLOSE",
                "quantity": 0.05,
                "profit_target": 115000.0,
                "stop_loss": 105000.0,
                "invalidation_condition": "If price closes below X",
                "leverage": 10,
                "confidence": 0.75,
                "risk_usd": 500.0
            },
            "justification": "Detailed reasoning here"
        }
    }
    
    print("Expected NOF1.AI Format:")
    print(json.dumps(expected_format, indent=2))
    print()
    
    # The error "Invalid nof1.ai response format" occurs when:
    # 1. Response is not valid JSON
    # 2. JSON doesn't contain "BTCUSDT" or "BTC" key
    # 3. "trade_signal_args" is missing
    
    print("Common issues that cause 'Invalid nof1.ai response format':")
    print()
    
    # Issue 1: GLM returns plain text instead of JSON
    issue1 = "I think we should HOLD the position because the market is showing mixed signals."
    print("1. Plain text response (not JSON):")
    print(f"   Response: {issue1}")
    print("   ❌ This will cause the error")
    print()
    
    # Issue 2: Missing BTCUSDT key
    issue2 = {
        "response": {
            "signal": "BUY",
            "quantity": 0.05,
            "confidence": 0.75
        }
    }
    print("2. Missing BTCUSDT wrapper:")
    print(f"   Response: {json.dumps(issue2, indent=4)}")
    print("   ❌ This will cause the error - needs BTCUSDT -> trade_signal_args structure")
    print()
    
    # Issue 3: Missing trade_signal_args
    issue3 = {
        "BTCUSDT": {
            "signal": "BUY",
            "quantity": 0.05,
            "confidence": 0.75
        }
    }
    print("3. Missing trade_signal_args wrapper:")
    print(f"   Response: {json.dumps(issue3, indent=4)}")
    print("   ❌ This will cause the error - needs trade_signal_args nested structure")
    print()
    
    # Issue 4: Using BTC instead of BTCUSDT (this actually works as fallback)
    issue4 = {
        "BTC": {
            "trade_signal_args": {
                "signal": "BUY",
                "quantity": 0.05,
                "confidence": 0.75
            }
        }
    }
    print("4. Using BTC instead of BTCUSDT:")
    print(f"   Response: {json.dumps(issue4, indent=4)}")
    print("   ✅ This works (fallback to BTC key)")
    print()
    
    print("="*60)
    print("SOLUTION:")
    print("The prompt needs to be clearer about the exact JSON format.")
    print("GLM must return the exact structure with BTCUSDT -> trade_signal_args")
    print("="*60)

if __name__ == "__main__":
    test_nof1_response_format()
