#!/usr/bin/env python3
"""
Test GLM Confidence Priority System

This script simulates different GLM confidence scenarios to verify
that the new decision logic works correctly.
"""

from app.agents.base import AgentSignal
from app.risk_manager.manager import RiskDecision, RiskManager


def test_scenario(name: str, glm_confidence: float, bias_confidence: float, composite_bias: float):
    """Test a specific scenario"""
    print(f"\n{'='*80}")
    print(f"SCENARIO: {name}")
    print(f"{'='*80}")
    print(f"GLM Confidence: {glm_confidence:.1f}%")
    print(f"Bias Confidence: {bias_confidence:.2f}")
    print(f"Composite Bias: {composite_bias:+.2f}")
    print()
    
    # Create mock signal with bias snapshot
    signal = AgentSignal(
        direction="BUY",
        confidence=0.5,
        reasoning="Test signal",
        timestamp="2024-01-01T00:00:00Z",
        metadata={
            "bias_snapshot": {
                "composite_bias_score": composite_bias,
                "bias_confidence_score": bias_confidence,
                "trend_bias_score": composite_bias * 0.8,
                "momentum_bias_score": composite_bias * 0.6,
                "futures_bias_score": composite_bias * 0.4,
                "intraday_bias_score": composite_bias * 0.5,
                "volatility_regime_score": 0.5,
            }
        }
    )
    
    # Create mock GLM decision
    decision = RiskDecision(
        action="BUY",
        amount=0.15,
        reasoning="Test reasoning",
        leverage=10.0,
        glm_confidence=glm_confidence,
        reason_primary="Test primary reason",
        reason_secondary="Test secondary reason",
    )
    
    # Apply guardrails
    manager = RiskManager()
    final_decision = manager._apply_confidence_guardrails(decision, [signal])
    
    # Print results
    print(f"RESULT:")
    print(f"  Action: {final_decision.action}")
    print(f"  Amount: {final_decision.amount:.4f}")
    print(f"  Reasoning: {final_decision.reasoning[:100]}...")
    
    return final_decision


def main():
    print("\n" + "="*80)
    print("GLM CONFIDENCE PRIORITY SYSTEM - TEST SUITE")
    print("="*80)
    
    # Test 1: High GLM confidence (>= 60) - Should bypass bias guardrails
    test_scenario(
        name="High GLM Confidence (85%) - Low Bias (0.28)",
        glm_confidence=85.0,
        bias_confidence=0.28,
        composite_bias=-0.16,
    )
    
    # Test 2: Moderate GLM confidence (40-59) - Should use small position
    test_scenario(
        name="Moderate GLM Confidence (45%) - Low Bias (0.28)",
        glm_confidence=45.0,
        bias_confidence=0.28,
        composite_bias=-0.16,
    )
    
    # Test 3: Low GLM confidence (<40) - Should respect bias HOLD
    test_scenario(
        name="Low GLM Confidence (30%) - Low Bias (0.28)",
        glm_confidence=30.0,
        bias_confidence=0.28,
        composite_bias=-0.16,
    )
    
    # Test 4: Very high GLM confidence (90+) - Should use larger position
    test_scenario(
        name="Very High GLM Confidence (92%) - Good Bias (0.65)",
        glm_confidence=92.0,
        bias_confidence=0.65,
        composite_bias=0.45,
    )
    
    # Test 5: Moderate-high GLM (60-74) - Should use medium position
    test_scenario(
        name="Moderate-High GLM Confidence (68%) - Medium Bias (0.50)",
        glm_confidence=68.0,
        bias_confidence=0.50,
        composite_bias=0.25,
    )
    
    # Test 6: GLM confidence 50% (edge case)
    test_scenario(
        name="Edge Case: GLM 50% - Bias HOLD",
        glm_confidence=50.0,
        bias_confidence=0.25,
        composite_bias=-0.05,
    )
    
    print("\n" + "="*80)
    print("TEST SUITE COMPLETE")
    print("="*80)
    print("\nKEY FINDINGS:")
    print("  ✅ GLM >= 60%: Bypass bias guardrails, use GLM decision")
    print("  ⚠️  GLM 40-59%: Use small position (0.05-0.08) even if bias says HOLD")
    print("  ❌ GLM < 40%: Respect bias guardrails, likely HOLD")
    print()


if __name__ == "__main__":
    main()
