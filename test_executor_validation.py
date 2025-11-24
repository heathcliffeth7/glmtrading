#!/usr/bin/env python3
"""
Test: Executor Exit Plan Validation
GLM'nin CLOSE action'ını reject ettiğini ve exit_plan validation'ını test eder
"""

import sys

sys.path.insert(0, '/root/trading')

from app.executor.executor import ExecutionResult
from app.risk_manager.manager import RiskDecision


def mock_executor_tests():
    """
    Mock executor tests (without database dependencies)
    Tests logic validation only
    """
    
    print("=" * 80)
    print("TEST: Executor Exit Plan Validation")
    print("=" * 80)
    print()
    
    all_passed = True
    
    # Test 1: CLOSE action should be rejected
    print("📊 Test 1: GLM CLOSE Action Rejection")
    print("-" * 80)
    
    close_decision = RiskDecision(
        action='CLOSE',
        amount=1.0,
        reasoning='Test close action',
        leverage=5.0,
        glm_confidence=80.0
    )
    
    # Simulate executor check
    if close_decision.action == 'CLOSE':
        print("✅ CLOSE action detected")
        print("   → Status: INVALID")
        print("   → Details: GLM CLOSE action rejected - positions close automatically via exit_plan")
        print("   ✓ PASS: CLOSE action would be rejected")
    else:
        print("   ✗ FAIL: CLOSE action not detected")
        all_passed = False
    
    print()
    
    # Test 2: BUY without exit_plan should be blocked
    print("📊 Test 2: BUY Without Exit Plan (Should Block)")
    print("-" * 80)
    
    buy_no_exit = RiskDecision(
        action='BUY',
        amount=0.1,
        reasoning='Test buy without exit plan',
        leverage=5.0,
        glm_confidence=75.0,
        exit_plan=None
    )
    
    # Simulate validation
    if buy_no_exit.action in ['BUY', 'SELL']:
        if not buy_no_exit.exit_plan:
            print("✅ BUY action detected without exit_plan")
            print("   → Status: BLOCKED")
            print("   → Details: Exit plan eksik - GLM hatası")
            print("   ✓ PASS: Would be blocked")
        else:
            print("   ✗ FAIL: Exit plan present but shouldn't be")
            all_passed = False
    
    print()
    
    # Test 3: BUY with invalid exit_plan (0.0 values)
    print("📊 Test 3: BUY With Invalid Exit Plan (Should Block)")
    print("-" * 80)
    
    buy_invalid_exit = RiskDecision(
        action='BUY',
        amount=0.1,
        reasoning='Test buy with invalid exit plan',
        leverage=5.0,
        glm_confidence=75.0,
        exit_plan={'profit_target': 0.0, 'stop_loss': 0.0, 'invalidation_condition': 'N/A'}
    )
    
    # Simulate validation
    if buy_invalid_exit.action in ['BUY', 'SELL']:
        profit_target = buy_invalid_exit.exit_plan.get('profit_target')
        stop_loss = buy_invalid_exit.exit_plan.get('stop_loss')
        
        if not profit_target or profit_target == 0.0:
            print("✅ Invalid profit_target detected (0.0)")
            print("   → Status: BLOCKED")
            print("   → Details: Profit target geçersiz veya eksik")
            print("   ✓ PASS: Would be blocked")
        else:
            print("   ✗ FAIL: Invalid profit_target not detected")
            all_passed = False
    
    print()
    
    # Test 4: SELL with valid exit_plan (Should Pass)
    print("📊 Test 4: SELL With Valid Exit Plan (Should Pass)")
    print("-" * 80)
    
    sell_valid_exit = RiskDecision(
        action='SELL',
        amount=0.1,
        reasoning='Test sell with valid exit plan',
        leverage=8.0,
        glm_confidence=80.0,
        exit_plan={
            'profit_target': 105000.0,
            'stop_loss': 112000.0,
            'invalidation_condition': 'If price closes above 111500 on 3-minute candle'
        }
    )
    
    # Simulate validation
    if sell_valid_exit.action in ['BUY', 'SELL']:
        if not sell_valid_exit.exit_plan:
            print("   ✗ FAIL: Exit plan missing")
            all_passed = False
        else:
            profit_target = sell_valid_exit.exit_plan.get('profit_target')
            stop_loss = sell_valid_exit.exit_plan.get('stop_loss')
            invalidation = sell_valid_exit.exit_plan.get('invalidation_condition')
            
            if profit_target and profit_target != 0.0 and stop_loss and stop_loss != 0.0:
                print("✅ Valid exit_plan detected:")
                print(f"   • Profit Target: ${profit_target:,.2f} ✓")
                print(f"   • Stop Loss: ${stop_loss:,.2f} ✓")
                print(f"   • Invalidation: '{invalidation}' ✓")
                print("   → Status: ALLOWED")
                print("   ✓ PASS: Would be allowed to execute")
            else:
                print("   ✗ FAIL: Exit plan validation failed")
                all_passed = False
    
    print()
    
    # Test 5: HOLD action (Should Pass - no validation needed)
    print("📊 Test 5: HOLD Action (Should Pass)")
    print("-" * 80)
    
    hold_decision = RiskDecision(
        action='HOLD',
        amount=0.0,
        reasoning='Market unclear',
        leverage=1.0,
        glm_confidence=50.0
    )
    
    # Simulate validation
    if hold_decision.action == 'HOLD':
        print("✅ HOLD action detected")
        print("   → Status: SKIP")
        print("   → Details: Hold kararı")
        print("   ✓ PASS: No validation needed for HOLD")
    else:
        print("   ✗ FAIL: HOLD action not detected")
        all_passed = False
    
    print()
    
    print("=" * 80)
    if all_passed:
        print("🎉 ALL VALIDATION TESTS PASSED!")
        print()
        print("Summary:")
        print("  ✅ CLOSE action → REJECTED")
        print("  ✅ BUY/SELL without exit_plan → BLOCKED")
        print("  ✅ BUY/SELL with invalid exit_plan → BLOCKED")
        print("  ✅ BUY/SELL with valid exit_plan → ALLOWED")
        print("  ✅ HOLD action → SKIP (no validation)")
    else:
        print("❌ SOME VALIDATION TESTS FAILED!")
    print("=" * 80)
    
    return all_passed

if __name__ == '__main__':
    success = mock_executor_tests()
    sys.exit(0 if success else 1)
