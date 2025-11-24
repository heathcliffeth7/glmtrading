#!/usr/bin/env python3
"""
Test Real Scenario from User's Report

User reported:
- GLM Confidence: 85%
- Composite Bias: -0.16 (weak short signal)
- Bias Confidence: 0.28 (below 0.30 threshold)
- Consensus: 3/4 components agree (Futures disagrees)

Expected: Position should open because GLM confidence is high (85%)
"""

from app.agents.base import AgentSignal
from app.risk_manager.manager import RiskDecision, RiskManager


def main():
    print("\n" + "="*80)
    print("REAL SCENARIO TEST: User's Reported Case")
    print("="*80)
    print()
    print("User's Data:")
    print("  GLM Confidence: 85%")
    print("  Composite Bias: -0.16 (weak short)")
    print("  Bias Confidence: 0.28 (below 0.30 threshold)")
    print("  Consensus: 3/4 components")
    print("    - Trend: -0.33 ✅ (bearish)")
    print("    - Momentum: -0.40 ✅ (bearish)")
    print("    - Futures: +0.56 ❌ (bullish - CONFLICT!)")
    print("    - Intraday: -0.21 ✅ (bearish)")
    print()
    print("OLD SYSTEM: Would HOLD (bias confidence 0.28 < 0.30)")
    print("NEW SYSTEM: Should allow position (GLM confidence 85% >= 70%)")
    print()
    
    # Create signal matching user's scenario
    signal = AgentSignal(
        direction="SELL",  # GLM suggested SHORT
        confidence=0.5,
        reasoning="User's real scenario",
        timestamp="2024-01-01T00:00:00Z",
        metadata={
            "bias_snapshot": {
                "composite_bias_score": -0.16,
                "bias_confidence_score": 0.28,
                "trend_bias_score": -0.33,
                "momentum_bias_score": -0.40,
                "futures_bias_score": 0.56,  # Conflicting!
                "intraday_bias_score": -0.21,
                "volatility_regime_score": 0.5,
            }
        }
    )
    
    # GLM decision
    decision = RiskDecision(
        action="SELL",
        amount=0.15,  # GLM requested 15%
        reasoning="Strong bearish momentum across multiple timeframes. Trend and momentum aligned.",
        leverage=10.0,
        glm_confidence=85.0,
        reason_primary="Trend and momentum strongly bearish (-0.33, -0.40)",
        reason_secondary="Futures conflict (+0.56) but spot dominates",
    )
    
    print("="*80)
    print("TESTING NEW SYSTEM...")
    print("="*80)
    print()
    
    # Apply guardrails
    manager = RiskManager()
    final_decision = manager._apply_confidence_guardrails(decision, [signal])
    
    # Print results
    print("FINAL DECISION:")
    print(f"  Action: {final_decision.action}")
    print(f"  Amount: {final_decision.amount:.4f} ({final_decision.amount*100:.1f}% of equity)")
    print(f"  Leverage: {final_decision.leverage:.1f}x")
    print(f"  GLM Confidence: {final_decision.glm_confidence:.1f}%")
    print()
    print("Reasoning:")
    print(f"  {final_decision.reasoning}")
    print()
    
    # Verdict
    if final_decision.action == "SELL" and final_decision.amount > 0:
        print("✅ SUCCESS: Position opened despite low bias confidence!")
        print(f"   GLM's high confidence (85%) overrode bias guardrails")
        print(f"   Position size: {final_decision.amount*100:.1f}% (reasonable for 85% confidence)")
    elif final_decision.action == "HOLD":
        print("❌ FAILED: Still blocked by bias guardrails")
        print("   System did not trust GLM's high confidence")
    else:
        print("⚠️  UNEXPECTED: Check the logic")
    
    print()
    print("="*80)


if __name__ == "__main__":
    main()
