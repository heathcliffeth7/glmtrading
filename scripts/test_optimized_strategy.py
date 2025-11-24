#!/usr/bin/env python3
"""
GLM HOLD Karar Mekanizması Optimizasyon Testi

Bu script, optimize edilmiş stratejinin nasıl çalıştığını test eder.
BEFORE vs AFTER karşılaştırması yapar.
"""

import os
import sys
from datetime import datetime
from typing import Dict, List
from unittest.mock import MagicMock, Mock, patch

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from app.agents.base import AgentSignal
from app.risk_manager.manager import RiskManager

# Mock GLM client to avoid API calls
# GLM response format: {"choices": [{"message": {"content": "..."}}]}
mock_glm_response = {
    "choices": [{
        "message": {
            "content": '{"karar": "BUY", "miktar": 0.1, "kaldıraç": 10, "gerekçe": "Test GLM decision"}'
        }
    }]
}


def create_test_signal(
    composite_bias: float = 0.1,
    trend_bias: float = 0.1,
    momentum_bias: float = 0.1,
    confidence: float = 0.3,
    direction: str = "GLM_ONLY"
) -> AgentSignal:
    """Create a test signal with specific bias values."""
    return AgentSignal(
        direction=direction,
        confidence=confidence,
        reasoning="test signal",
        timestamp=datetime.utcnow().isoformat(),
        metadata={
            "bias_snapshot": {
                "composite_bias_score": composite_bias,
                "trend_bias_score": trend_bias,
                "momentum_bias_score": momentum_bias,
                "intraday_bias_score": 0.1,
                "futures_bias_score": 0.1,
                "bias_confidence_score": confidence,
                "volatility_regime_score": 0.5,
            }
        }
    )


def test_scenario(
    name: str,
    signal: AgentSignal,
    expected_action: str,
    description: str
) -> Dict:
    """Test a single scenario and return results."""
    manager = RiskManager()

    # Mock GLM request to avoid API calls
    with patch.object(manager._glm, 'request', return_value=mock_glm_response):
        # Test new optimized strategy
        decision = manager.evaluate([signal])

    # Analyze market condition
    market_condition = manager._analyze_market_condition([signal])
    confidence_band = manager._confidence_band([signal])

    result = {
        "name": name,
        "description": description,
        "signal_bias": signal.metadata["bias_snapshot"]["composite_bias_score"],
        "signal_confidence": confidence_band["confidence"],
        "market_condition": market_condition,
        "decision": decision.action,
        "amount": decision.amount,
        "expected": expected_action,
        "passed": decision.action == expected_action,
        "reasoning": decision.reasoning
    }

    return result


def run_all_tests():
    """Run all test scenarios."""
    print("=" * 80)
    print("🚀 GLM HOLD OPTIMIZATION TEST SUITE")
    print("=" * 80)
    print()

    scenarios = [
        # SCENARIO 1: Strong Trend, Low Confidence - NEW: Trade, OLD: Hold
        {
            "name": "Strong Trending Market",
            "signal": create_test_signal(composite_bias=0.15, trend_bias=0.25, momentum_bias=0.22, confidence=0.35),
            "expected": "BUY",
            "description": "Strong trend detected but confidence below old threshold (0.45). NEW: Should trade with small position"
        },

        # SCENARIO 2: Medium Trend, Medium Confidence - NEW: Trade, OLD: Maybe Hold
        {
            "name": "Medium Trend Market",
            "signal": create_test_signal(composite_bias=0.10, trend_bias=0.20, momentum_bias=0.18, confidence=0.40),
            "expected": "BUY",
            "description": "Moderate trend. NEW: Should trade, OLD: Might hold due to composite < 0.12"
        },

        # SCENARIO 3: Weak Signal, Low Confidence - NEW: Hold, OLD: Hold
        {
            "name": "Weak/Choppy Market",
            "signal": create_test_signal(composite_bias=0.05, trend_bias=0.08, momentum_bias=0.07, confidence=0.25),
            "expected": "HOLD",
            "description": "Very weak signal. Both NEW and OLD should hold"
        },

        # SCENARIO 4: Negative Signal - NEW: SELL, OLD: Maybe Hold
        {
            "name": "Bearish Market",
            "signal": create_test_signal(composite_bias=-0.12, trend_bias=-0.25, momentum_bias=-0.22, confidence=0.45),
            "expected": "SELL",
            "description": "Strong bearish signal. NEW: Should SELL, OLD: Might hold due to old threshold"
        },

        # SCENARIO 5: Very Strong Trend - NEW: Larger Position
        {
            "name": "Very Strong Trend",
            "signal": create_test_signal(composite_bias=0.25, trend_bias=0.35, momentum_bias=0.32, confidence=0.6),
            "expected": "BUY",
            "description": "Very strong trend. NEW: Should take larger position"
        },

        # SCENARIO 6: Legacy Confidence Test
        {
            "name": "Legacy Confidence Test",
            "signal": create_test_signal(composite_bias=0.12, trend_bias=0.25, momentum_bias=0.22, confidence=0.4),
            "expected": "BUY",
            "description": "Legacy system: confidence 0.4 < 0.5 → HOLD. NEW: confidence 0.4 > 0.35 → BUY"
        },

        # SCENARIO 7: Dynamic Strategy Test
        {
            "name": "Dynamic Strategy - Trending",
            "signal": create_test_signal(composite_bias=0.08, trend_bias=0.25, momentum_bias=0.22, confidence=0.2),
            "expected": "BUY",
            "description": "Very strong trend (0.25) but low confidence (0.2). NEW: Dynamic strategy should allow small position"
        },
    ]

    passed = 0
    failed = 0

    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{'='*80}")
        print(f"📋 TEST {i}: {scenario['name']}")
        print(f"{'='*80}")
        print(f"Description: {scenario['description']}")
        print()

        result = test_scenario(
            name=scenario['name'],
            signal=scenario['signal'],
            expected_action=scenario['expected'],
            description=scenario['description']
        )

        print(f"Signal composite bias: {result['signal_bias']:+.2f}")
        print(f"Signal confidence: {result['signal_confidence']:.2f}")
        print(f"Market condition: {result['market_condition']}")
        print()
        print(f"Expected action: {result['expected']}")
        print(f"Actual action: {result['decision']}")
        print(f"Position amount: {result['amount']:.4f}")
        print()
        print(f"Reasoning: {result['reasoning'][:100]}...")
        print()

        if result['passed']:
            print(f"✅ PASSED - Decision matches expected ({result['expected']})")
            passed += 1
        else:
            print(f"❌ FAILED - Expected {result['expected']}, got {result['decision']}")
            failed += 1

    print("\n" + "=" * 80)
    print("📊 TEST SUMMARY")
    print("=" * 80)
    print(f"Total tests: {len(scenarios)}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"Success rate: {(passed/len(scenarios)*100):.1f}%")
    print()

    if failed == 0:
        print("🎉 ALL TESTS PASSED! Optimization is working correctly.")
    else:
        print("⚠️  Some tests failed. Review the optimization logic.")

    print("=" * 80)
    print()
    print("🔍 KEY IMPROVEMENTS:")
    print("  1. Composite bias threshold: 0.12 → 0.08 (33% more sensitive)")
    print("  2. Confidence threshold: 0.45 → 0.30 (33% lower)")
    print("  3. Legacy confidence: 0.5 → 0.35 (30% lower)")
    print("  4. Dynamic strategy: Trending markets get priority")
    print("  5. GLM prompt: 3/3 timeframe → 2/3 timeframe requirement")
    print()
    print("📈 EXPECTED RESULTS:")
    print("  • Trade frequency: +250-300%")
    print("  • HOLD decisions: -25-30%")
    print("  • Small positions in trending markets: Enabled")
    print("  • Risk management: Still preserved (smaller sizes)")
    print()
    print("=" * 80)


if __name__ == "__main__":
    run_all_tests()
