"""
Test: Risk Controls and Invalidation Validation

Bu test, risk kontrol validasyonlarını ve invalidation pozisyon kurallarını test eder.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.risk_manager.risk_controls import (
    ValidationError,
    parse_invalidation_price,
    validate_exit_plan_update,
)


def test_parse_invalidation_price():
    """Test invalidation price parsing"""
    print("\n" + "=" * 60)
    print("TEST: Parse Invalidation Price")
    print("=" * 60)
    
    test_cases = [
        ("If price closes below 109000 on 3m candle", 109000.0),
        ("If price closes above 113000 on 5m candle", 113000.0),
        ("Fiyat 109200 seviyesinin altında kapanırsa", 109200.0),
        ("If RSI drops below 65 AND price closes below 111000", 111000.0),
    ]
    
    passed = 0
    for condition, expected in test_cases:
        result = parse_invalidation_price(condition)
        if result == expected:
            print(f"✅ PASS: {condition[:50]}... → {result}")
            passed += 1
        else:
            print(f"❌ FAIL: {condition[:50]}... → Expected {expected}, got {result}")
    
    print(f"\nResult: {passed}/{len(test_cases)} tests passed")
    return passed == len(test_cases)


def test_long_position_validation():
    """Test LONG position validation rules"""
    print("\n" + "=" * 60)
    print("TEST: LONG Position Validation")
    print("=" * 60)
    
    entry = 110000
    
    test_cases = [
        {
            "name": "Valid LONG (correct invalidation)",
            "exit_plan": {
                "stop_loss": 105000,
                "profit_target": 117500,
                "invalidation_condition": "If price closes below 107000 on 3m candle"
            },
            "should_pass": True
        },
        {
            "name": "Invalid LONG (invalidation below SL)",
            "exit_plan": {
                "stop_loss": 105000,
                "profit_target": 115000,
                "invalidation_condition": "If price closes below 104000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid LONG (invalidation above entry)",
            "exit_plan": {
                "stop_loss": 105000,
                "profit_target": 115000,
                "invalidation_condition": "If price closes below 111000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid LONG (SL > Entry)",
            "exit_plan": {
                "stop_loss": 111000,
                "profit_target": 115000,
                "invalidation_condition": "If price closes below 109000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid LONG (TP < Entry)",
            "exit_plan": {
                "stop_loss": 105000,
                "profit_target": 108000,
                "invalidation_condition": "If price closes below 107000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid LONG (Risk/Reward < 1.5)",
            "exit_plan": {
                "stop_loss": 108000,
                "profit_target": 111000,
                "invalidation_condition": "If price closes below 109000 on 3m candle"
            },
            "should_pass": False
        }
    ]
    
    passed = 0
    for test in test_cases:
        is_valid, error_msg = validate_exit_plan_update(
            old_plan={},
            new_plan=test["exit_plan"],
            entry_price=entry,
            position_side="LONG"
        )
        
        if (is_valid and test["should_pass"]) or (not is_valid and not test["should_pass"]):
            print(f"✅ PASS: {test['name']}")
            if not is_valid:
                print(f"         Error: {error_msg}")
            passed += 1
        else:
            print(f"❌ FAIL: {test['name']}")
            print(f"         Expected: {'VALID' if test['should_pass'] else 'INVALID'}")
            print(f"         Got: {'VALID' if is_valid else 'INVALID'}")
            if error_msg:
                print(f"         Error: {error_msg}")
    
    print(f"\nResult: {passed}/{len(test_cases)} tests passed")
    return passed == len(test_cases)


def test_short_position_validation():
    """Test SHORT position validation rules"""
    print("\n" + "=" * 60)
    print("TEST: SHORT Position Validation")
    print("=" * 60)
    
    entry = 110000
    
    test_cases = [
        {
            "name": "Valid SHORT (correct invalidation)",
            "exit_plan": {
                "stop_loss": 115000,
                "profit_target": 102500,
                "invalidation_condition": "If price closes above 113000 on 3m candle"
            },
            "should_pass": True
        },
        {
            "name": "Invalid SHORT (invalidation above SL)",
            "exit_plan": {
                "stop_loss": 115000,
                "profit_target": 105000,
                "invalidation_condition": "If price closes above 116000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid SHORT (invalidation below entry)",
            "exit_plan": {
                "stop_loss": 115000,
                "profit_target": 105000,
                "invalidation_condition": "If price closes above 109000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid SHORT (SL < Entry)",
            "exit_plan": {
                "stop_loss": 109000,
                "profit_target": 105000,
                "invalidation_condition": "If price closes above 111000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid SHORT (TP > Entry)",
            "exit_plan": {
                "stop_loss": 115000,
                "profit_target": 112000,
                "invalidation_condition": "If price closes above 113000 on 3m candle"
            },
            "should_pass": False
        },
        {
            "name": "Invalid SHORT (Risk/Reward < 1.5)",
            "exit_plan": {
                "stop_loss": 112000,
                "profit_target": 109000,
                "invalidation_condition": "If price closes above 111000 on 3m candle"
            },
            "should_pass": False
        }
    ]
    
    passed = 0
    for test in test_cases:
        is_valid, error_msg = validate_exit_plan_update(
            old_plan={},
            new_plan=test["exit_plan"],
            entry_price=entry,
            position_side="SHORT"
        )
        
        if (is_valid and test["should_pass"]) or (not is_valid and not test["should_pass"]):
            print(f"✅ PASS: {test['name']}")
            if not is_valid:
                print(f"         Error: {error_msg}")
            passed += 1
        else:
            print(f"❌ FAIL: {test['name']}")
            print(f"         Expected: {'VALID' if test['should_pass'] else 'INVALID'}")
            print(f"         Got: {'VALID' if is_valid else 'INVALID'}")
            if error_msg:
                print(f"         Error: {error_msg}")
    
    print(f"\nResult: {passed}/{len(test_cases)} tests passed")
    return passed == len(test_cases)


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("RISK CONTROLS AND INVALIDATION VALIDATION TESTS")
    print("=" * 60)
    
    results = []
    
    results.append(("Parse Invalidation Price", test_parse_invalidation_price()))
    results.append(("LONG Position Validation", test_long_position_validation()))
    results.append(("SHORT Position Validation", test_short_position_validation()))
    
    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(result[1] for result in results)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 ALL TESTS PASSED")
    else:
        print("⚠️ SOME TESTS FAILED")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
