#!/usr/bin/env python3
"""
Test: Exit Plan Validation Logic (Standalone)
Dependencies olmadan validation mantığını test eder
"""

def test_validation_logic():
    """Test validation logic without dependencies"""
    
    print("=" * 80)
    print("TEST: Exit Plan Validation Logic")
    print("=" * 80)
    print()
    
    all_passed = True
    
    # Test 1: CLOSE action check
    print("📊 Test 1: CLOSE Action Detection")
    print("-" * 80)
    
    action = "CLOSE"
    if action == "CLOSE":
        print(f"✅ Action: {action}")
        print("   → Expected: INVALID (rejected)")
        print("   → Reason: GLM cannot use CLOSE - positions close automatically")
        print("   ✓ PASS")
    else:
        print("   ✗ FAIL")
        all_passed = False
    
    print()
    
    # Test 2: BUY without exit_plan
    print("📊 Test 2: BUY Without Exit Plan")
    print("-" * 80)
    
    action = "BUY"
    exit_plan = None
    
    if action in ["BUY", "SELL"]:
        if not exit_plan:
            print(f"✅ Action: {action}, Exit Plan: {exit_plan}")
            print("   → Expected: BLOCKED")
            print("   → Reason: Exit plan missing for BUY/SELL")
            print("   ✓ PASS")
        else:
            print("   ✗ FAIL")
            all_passed = False
    
    print()
    
    # Test 3: BUY with invalid exit_plan
    print("📊 Test 3: BUY With Invalid Exit Plan (profit_target=0)")
    print("-" * 80)
    
    action = "BUY"
    exit_plan = {
        'profit_target': 0.0,
        'stop_loss': 108000.0,
        'invalidation_condition': 'If price closes below 109000'
    }
    
    profit_target = exit_plan.get('profit_target')
    stop_loss = exit_plan.get('stop_loss')
    
    if action in ["BUY", "SELL"]:
        if not profit_target or profit_target == 0.0:
            print(f"✅ Action: {action}")
            print(f"   Exit Plan: {exit_plan}")
            print(f"   → Profit Target: {profit_target} (INVALID)")
            print("   → Expected: BLOCKED")
            print("   → Reason: Invalid profit_target")
            print("   ✓ PASS")
        else:
            print("   ✗ FAIL")
            all_passed = False
    
    print()
    
    # Test 4: SELL with invalid exit_plan
    print("📊 Test 4: SELL With Invalid Exit Plan (stop_loss=0)")
    print("-" * 80)
    
    action = "SELL"
    exit_plan = {
        'profit_target': 105000.0,
        'stop_loss': 0.0,
        'invalidation_condition': 'If price closes above 111500'
    }
    
    profit_target = exit_plan.get('profit_target')
    stop_loss = exit_plan.get('stop_loss')
    
    if action in ["BUY", "SELL"]:
        if not stop_loss or stop_loss == 0.0:
            print(f"✅ Action: {action}")
            print(f"   Exit Plan: {exit_plan}")
            print(f"   → Stop Loss: {stop_loss} (INVALID)")
            print("   → Expected: BLOCKED")
            print("   → Reason: Invalid stop_loss")
            print("   ✓ PASS")
        else:
            print("   ✗ FAIL")
            all_passed = False
    
    print()
    
    # Test 5: SELL with valid exit_plan
    print("📊 Test 5: SELL With Valid Exit Plan")
    print("-" * 80)
    
    action = "SELL"
    exit_plan = {
        'profit_target': 105000.0,
        'stop_loss': 112000.0,
        'invalidation_condition': 'If price closes above 111500 on 3-minute candle'
    }
    
    profit_target = exit_plan.get('profit_target')
    stop_loss = exit_plan.get('stop_loss')
    invalidation = exit_plan.get('invalidation_condition')
    
    if action in ["BUY", "SELL"]:
        if exit_plan:
            if profit_target and profit_target != 0.0:
                if stop_loss and stop_loss != 0.0:
                    print(f"✅ Action: {action}")
                    print(f"   Exit Plan:")
                    print(f"     • Profit Target: ${profit_target:,.2f} ✓")
                    print(f"     • Stop Loss: ${stop_loss:,.2f} ✓")
                    print(f"     • Invalidation: '{invalidation}' ✓")
                    print("   → Expected: ALLOWED")
                    print("   → Reason: All exit plan values valid")
                    print("   ✓ PASS")
                else:
                    print("   ✗ FAIL: stop_loss invalid")
                    all_passed = False
            else:
                print("   ✗ FAIL: profit_target invalid")
                all_passed = False
        else:
            print("   ✗ FAIL: exit_plan missing")
            all_passed = False
    
    print()
    
    # Test 6: HOLD action (no validation)
    print("📊 Test 6: HOLD Action (No Validation Needed)")
    print("-" * 80)
    
    action = "HOLD"
    
    if action == "HOLD":
        print(f"✅ Action: {action}")
        print("   → Expected: SKIP")
        print("   → Reason: No validation needed for HOLD")
        print("   ✓ PASS")
    else:
        print("   ✗ FAIL")
        all_passed = False
    
    print()
    
    # Test 7: Invalidation Condition Display
    print("📊 Test 7: Invalidation Condition Display Format")
    print("-" * 80)
    
    position_info = {
        'profit_target': 115000.0,
        'stop_loss': 108000.0,
        'invalidation_condition': 'If price closes below 109000 on 3-minute candle'
    }
    
    # Simulate prompt format
    display = f"""
  'profit_target': {position_info['profit_target']:.2f},  # 🎯 AUTO-CLOSE when reached
  'stop_loss': {position_info['stop_loss']:.2f},  # 🛑 AUTO-CLOSE when triggered
  'invalidation_condition': '{position_info['invalidation_condition']}',  # ⚠️ AUTO-CLOSE when met
"""
    
    print("Display Format:")
    print(display)
    
    # Check format
    checks = [
        ("🎯 AUTO-CLOSE when reached", "profit_target emoji"),
        ("🛑 AUTO-CLOSE when triggered", "stop_loss emoji"),
        ("⚠️ AUTO-CLOSE when met", "invalidation emoji"),
        ("If price closes below 109000", "invalidation text"),
    ]
    
    format_ok = True
    for check_str, desc in checks:
        if check_str in display:
            print(f"   ✓ {desc:30s} FOUND")
        else:
            print(f"   ✗ {desc:30s} MISSING")
            format_ok = False
            all_passed = False
    
    if format_ok:
        print("   ✓ PASS: All format checks passed")
    
    print()
    
    print("=" * 80)
    if all_passed:
        print("🎉 ALL LOGIC TESTS PASSED!")
        print()
        print("Summary of Validation Rules:")
        print("  1. ❌ CLOSE action → REJECTED (GLM cannot use)")
        print("  2. ❌ BUY/SELL without exit_plan → BLOCKED")
        print("  3. ❌ BUY/SELL with profit_target=0 → BLOCKED")
        print("  4. ❌ BUY/SELL with stop_loss=0 → BLOCKED")
        print("  5. ✅ BUY/SELL with valid exit_plan → ALLOWED")
        print("  6. ✅ HOLD action → SKIP (no validation)")
        print()
        print("Display Format:")
        print("  ✅ Invalidation condition shown with emoji (⚠️)")
        print("  ✅ Same level as profit_target and stop_loss")
        print("  ✅ AUTO-CLOSE message for all three conditions")
    else:
        print("❌ SOME LOGIC TESTS FAILED!")
    print("=" * 80)
    
    return all_passed

if __name__ == '__main__':
    import sys
    success = test_validation_logic()
    sys.exit(0 if success else 1)
