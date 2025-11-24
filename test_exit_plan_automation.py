#!/usr/bin/env python3
"""
Test Exit Plan Automation System

Tests:
1. Position close notification to Redis
2. GLM reading notification from Redis
3. Exit plan monitoring in GLM prompt (direct code inspection)
4. End-to-end flow simulation

NOTE: This test uses minimal imports to avoid dependency issues.
It tests the core logic and Redis integration.
"""
import json
import sys
from datetime import datetime

print("Exit Plan Automation Test Suite")
print("="*80)
print("Checking dependencies...")

# Try importing redis
try:
    import redis
    print("✅ redis module available")
    REDIS_AVAILABLE = True
except ImportError:
    print("❌ redis module not available - Redis tests will be skipped")
    REDIS_AVAILABLE = False


def test_redis_notification_write():
    """Test 1: Redis notification yazma"""
    print("\n" + "="*80)
    print("TEST 1: Redis Notification Write")
    print("="*80)
    
    if not REDIS_AVAILABLE:
        print("⚠️ Test skipped: Redis module not available")
        return None
    
    try:
        # Connect to Redis
        redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
        redis_client.ping()  # Test connection
        
        # Test notification data
        notification_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "position_id": "TEST-POS-001",
            "trigger_type": "profit_target",
            "reason": "Profit target reached: $123,311.55",
            "entry_price": 110099.60,
            "exit_price": 123311.55,
            "pnl": 1200.50,
            "pnl_pct": 12.5,
            "position_type": "LONG",
            "quantity": 0.0908,
        }
        
        # Write to Redis
        redis_key = "position_closed:BTCUSDT"
        redis_client.setex(redis_key, 600, json.dumps(notification_data))
        
        print(f"✅ Notification written to Redis: {redis_key}")
        print(f"   TTL: 600 seconds (10 minutes)")
        print(f"   Data: {json.dumps(notification_data, indent=2)}")
        
        # Verify write
        stored = redis_client.get(redis_key)
        if stored:
            print(f"✅ Verification successful: notification exists in Redis")
            return True
        else:
            print(f"❌ Verification failed: notification not found in Redis")
            return False
            
    except Exception as exc:
        print(f"❌ Test failed with error: {exc}")
        import traceback
        traceback.print_exc()
        return False


def test_redis_notification_read():
    """Test 2: Redis notification okuma ve silme"""
    print("\n" + "="*80)
    print("TEST 2: Redis Notification Read & Delete")
    print("="*80)
    
    if not REDIS_AVAILABLE:
        print("⚠️ Test skipped: Redis module not available")
        return None
    
    try:
        redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
        redis_key = "position_closed:BTCUSDT"
        
        # Read notification
        notification_json = redis_client.get(redis_key)
        
        if not notification_json:
            print("⚠️ No notification found in Redis (run test_redis_notification_write first)")
            return False
        
        notification_data = json.loads(notification_json)
        print(f"✅ Notification read from Redis:")
        print(f"   Trigger: {notification_data.get('trigger_type')}")
        print(f"   PnL: ${notification_data.get('pnl'):.2f}")
        print(f"   Entry: ${notification_data.get('entry_price'):.2f}")
        print(f"   Exit: ${notification_data.get('exit_price'):.2f}")
        
        # Delete after reading
        redis_client.delete(redis_key)
        print(f"✅ Notification deleted from Redis")
        
        # Verify deletion
        check = redis_client.get(redis_key)
        if check is None:
            print(f"✅ Verification successful: notification removed from Redis")
            return True
        else:
            print(f"❌ Verification failed: notification still exists in Redis")
            return False
            
    except Exception as exc:
        print(f"❌ Test failed with error: {exc}")
        import traceback
        traceback.print_exc()
        return False


def test_code_inspection_exit_plan_monitoring():
    """Test 3: Code inspection - Exit plan monitoring in prompt builder"""
    print("\n" + "="*80)
    print("TEST 3: Code Inspection - Exit Plan Monitoring")
    print("="*80)
    
    try:
        # Read the prompt builder code
        with open('/root/trading/app/risk_manager/nof1_prompt_builder.py', 'r') as f:
            code = f.read()
        
        print("✅ Prompt builder code loaded")
        
        # Check for exit plan monitoring section
        checks = {
            "AUTOMATIC EXIT PLAN MONITORING": "Exit plan monitoring header",
            "DO NOT MAKE CLOSE DECISIONS BASED ON EXIT PLAN": "GLM instruction",
            "profit_target": "Profit target reference",
            "stop_loss": "Stop loss reference",
            "invalidation_condition": "Invalidation condition reference",
            "_check_position_close_notification": "Notification check method",
            "_format_close_notification": "Notification format method",
        }
        
        all_found = True
        for pattern, description in checks.items():
            if pattern in code:
                print(f"   ✅ {description} found")
            else:
                print(f"   ❌ {description} NOT found")
                all_found = False
        
        return all_found
        
    except Exception as exc:
        print(f"❌ Test failed with error: {exc}")
        import traceback
        traceback.print_exc()
        return False


def test_code_inspection_position_monitor():
    """Test 4: Code inspection - Position monitor Redis notification"""
    print("\n" + "="*80)
    print("TEST 4: Code Inspection - Position Monitor")
    print("="*80)
    
    try:
        # Read the position monitor code
        with open('/root/trading/app/monitoring/position_monitor.py', 'r') as f:
            code = f.read()
        
        print("✅ Position monitor code loaded")
        
        # Check for Redis notification method
        checks = {
            "_notify_glm_via_redis": "Redis notification method",
            "position_closed:": "Redis key pattern",
            "setex": "Redis write with TTL",
            "GLM notification sent via Redis": "Notification log message",
            "trigger_type": "Trigger type field",
            "pnl": "PnL field",
            "entry_price": "Entry price field",
            "exit_price": "Exit price field",
        }
        
        all_found = True
        for pattern, description in checks.items():
            if pattern in code:
                print(f"   ✅ {description} found")
            else:
                print(f"   ❌ {description} NOT found")
                all_found = False
        
        return all_found
        
    except Exception as exc:
        print(f"❌ Test failed with error: {exc}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("EXIT PLAN AUTOMATION SYSTEM - TEST SUITE")
    print("="*80)
    print(f"Start time: {datetime.utcnow().isoformat()}")
    print()
    print("NOTE: This is a simplified test suite that checks:")
    print("  1. Redis notification write/read (if Redis available)")
    print("  2. Code inspection of exit plan monitoring features")
    print("  3. Code inspection of position monitor Redis integration")
    print("="*80)
    
    results = {}
    
    # Test 1: Redis write
    result1 = test_redis_notification_write()
    results['test_1_redis_write'] = result1 if result1 is not None else "skipped"
    
    # Test 2: Redis read
    result2 = test_redis_notification_read()
    results['test_2_redis_read'] = result2 if result2 is not None else "skipped"
    
    # Test 3: Code inspection - Exit plan monitoring
    results['test_3_code_exit_plan'] = test_code_inspection_exit_plan_monitoring()
    
    # Test 4: Code inspection - Position monitor
    results['test_4_code_position_monitor'] = test_code_inspection_position_monitor()
    
    # Summary
    print("\n" + "="*80)
    print("TEST RESULTS SUMMARY")
    print("="*80)
    
    passed_tests = 0
    failed_tests = 0
    skipped_tests = 0
    
    for test_name, result in results.items():
        if result == "skipped":
            status = "⏭️  SKIPPED"
            skipped_tests += 1
        elif result:
            status = "✅ PASSED"
            passed_tests += 1
        else:
            status = "❌ FAILED"
            failed_tests += 1
        print(f"{test_name}: {status}")
    
    print("="*80)
    total_tests = len(results)
    print(f"Total: {total_tests} | Passed: {passed_tests} | Failed: {failed_tests} | Skipped: {skipped_tests}")
    print("="*80)
    
    if failed_tests == 0:
        print("🎉 ALL TESTS PASSED!")
        if skipped_tests > 0:
            print(f"   ({skipped_tests} test(s) skipped due to missing dependencies)")
        return 0
    else:
        print(f"⚠️ {failed_tests} TEST(S) FAILED")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
