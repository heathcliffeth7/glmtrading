#!/usr/bin/env python3
"""
Test GLM Confidence Threshold = 70%

Verify that:
- GLM >= 70%: Bypass bias guardrails
- GLM 40-69%: Use small position (bias override)
- GLM < 40%: Respect bias guardrails
"""

from app.agents.base import AgentSignal
from app.risk_manager.manager import RiskDecision, RiskManager


def test_scenario(name: str, glm_confidence: float, expected_behavior: str):
    """Test a specific GLM confidence level"""
    print(f"\n{'='*80}")
    print(f"TEST: {name}")
    print(f"{'='*80}")
    print(f"GLM Confidence: {glm_confidence:.1f}%")
    print(f"Expected: {expected_behavior}")
    print()
    
    # Create signal with low bias (would normally HOLD)
    signal = AgentSignal(
        direction="SELL",
        confidence=0.5,
        reasoning="Test signal",
        timestamp="2024-01-01T00:00:00Z",
        metadata={
            "bias_snapshot": {
                "composite_bias_score": -0.16,  # Weak short
                "bias_confidence_score": 0.28,  # Below 0.30 threshold
                "trend_bias_score": -0.33,
                "momentum_bias_score": -0.40,
                "futures_bias_score": 0.56,  # Conflicting
                "intraday_bias_score": -0.21,
                "volatility_regime_score": 0.5,
            }
        }
    )
    
    # GLM decision
    decision = RiskDecision(
        action="SELL",
        amount=0.15,
        reasoning="Test reasoning",
        leverage=10.0,
        glm_confidence=glm_confidence,
        reason_primary="Test primary",
        reason_secondary="Test secondary",
    )
    
    # Apply guardrails
    manager = RiskManager()
    final_decision = manager._apply_confidence_guardrails(decision, [signal])
    
    # Print results
    print(f"RESULT:")
    print(f"  Action: {final_decision.action}")
    print(f"  Amount: {final_decision.amount:.4f} ({final_decision.amount*100:.1f}%)")
    
    # Verdict
    if glm_confidence >= 70:
        if final_decision.action == "SELL" and final_decision.amount >= 0.12:
            print(f"  ✅ PASS: Bypassed bias guardrails (amount >= 12%)")
        else:
            print(f"  ❌ FAIL: Should bypass bias with larger position")
    elif glm_confidence >= 40:
        if final_decision.action == "SELL" and 0.05 <= final_decision.amount <= 0.10:
            print(f"  ✅ PASS: Small position despite bias HOLD")
        else:
            print(f"  ❌ FAIL: Should use small position (5-10%)")
    else:
        if final_decision.action == "HOLD" or final_decision.amount < 0.05:
            print(f"  ✅ PASS: Respected bias guardrails")
        else:
            print(f"  ❌ FAIL: Should respect bias HOLD")
    
    return final_decision


def main():
    print("\n" + "="*80)
    print("GLM CONFIDENCE THRESHOLD = 70% - TEST SUITE")
    print("="*80)
    
    # Test boundary cases
    test_scenario(
        name="GLM 85% (Well above 70%)",
        glm_confidence=85.0,
        expected_behavior="Bypass bias, use 15-25% position"
    )
    
    test_scenario(
        name="GLM 70% (Exactly at threshold)",
        glm_confidence=70.0,
        expected_behavior="Bypass bias, use 12% position"
    )
    
    test_scenario(
        name="GLM 69% (Just below threshold)",
        glm_confidence=69.0,
        expected_behavior="Small position 9% (moderate confidence)"
    )
    
    test_scenario(
        name="GLM 60% (Moderate confidence)",
        glm_confidence=60.0,
        expected_behavior="Small position 9% (moderate confidence)"
    )
    
    test_scenario(
        name="GLM 50% (Moderate confidence)",
        glm_confidence=50.0,
        expected_behavior="Small position 7% (moderate confidence)"
    )
    
    test_scenario(
        name="GLM 40% (Edge of moderate)",
        glm_confidence=40.0,
        expected_behavior="Small position 5% (moderate confidence)"
    )
    
    test_scenario(
        name="GLM 39% (Just below moderate)",
        glm_confidence=39.0,
        expected_behavior="Respect bias guardrails → HOLD or very small"
    )
    
    test_scenario(
        name="GLM 30% (Low confidence)",
        glm_confidence=30.0,
        expected_behavior="Respect bias guardrails → HOLD"
    )
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print("\n📊 GLM Confidence Thresholds:")
    print("  🟢 >= 70%: HIGH - Bypass bias, use GLM decision directly")
    print("  🟡 40-69%: MODERATE - Small position (5-9%) despite bias")
    print("  🔴 < 40%: LOW - Respect bias guardrails")
    print()
    print("📈 Position Sizing:")
    print("  85-100%: max 25%")
    print("  75-84%:  max 18%")
    print("  70-74%:  max 12%")
    print("  60-69%:  9%")
    print("  50-59%:  7%")
    print("  40-49%:  5%")
    print("  < 40%:   Bias-based")
    print()


if __name__ == "__main__":
    main()
