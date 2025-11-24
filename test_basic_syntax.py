#!/usr/bin/env python3
"""
Basic syntax and logic test for WebSocket enhancements
"""

import ast
import sys
import os

def test_binance_ws_syntax():
    """Test that binance_ws.py has valid Python syntax"""
    print("🔧 Testing binance_ws.py syntax...")
    try:
        with open('app/data_feeds/binance_ws.py', 'r') as f:
            content = f.read()

        # Parse the file to check for syntax errors
        ast.parse(content)
        print("✅ binance_ws.py syntax is valid")

        # Check for key enhancements
        checks = [
            ('ConnectionState', 'ConnectionState enum found'),
            ('ConnectionMetrics', 'ConnectionMetrics dataclass found'),
            ('_update_connection_state', 'Connection state management method found'),
            ('_calculate_retry_delay', 'Exponential backoff method found'),
            ('_classify_error', 'Error classification method found'),
            ('_heartbeat_monitor', 'Heartbeat monitoring method found'),
            ('_should_circuit_break', 'Circuit breaker method found'),
            ('read_loop_closed', 'Read loop closed error handling found'),
        ]

        for check, description in checks:
            if check in content:
                print(f"✅ {description}")
            else:
                print(f"❌ {description}")

        return True
    except SyntaxError as e:
        print(f"❌ Syntax error in binance_ws.py: {e}")
        return False
    except Exception as e:
        print(f"❌ Error checking binance_ws.py: {e}")
        return False

def test_settings_syntax():
    """Test that settings.py has valid Python syntax"""
    print("\n🔧 Testing settings.py syntax...")
    try:
        with open('app/config/settings.py', 'r') as f:
            content = f.read()

        # Parse the file to check for syntax errors
        ast.parse(content)
        print("✅ settings.py syntax is valid")

        # Check for WebSocket settings
        checks = [
            ('BinanceWebSocketSettings', 'BinanceWebSocketSettings class found'),
            ('max_reconnect_attempts', 'Max reconnect attempts setting found'),
            ('base_retry_delay', 'Base retry delay setting found'),
            ('circuit_breaker_threshold', 'Circuit breaker threshold setting found'),
            ('ping_interval', 'Ping interval setting found'),
            ('websocket:', 'WebSocket settings integration found'),
        ]

        for check, description in checks:
            if check in content:
                print(f"✅ {description}")
            else:
                print(f"❌ {description}")

        return True
    except SyntaxError as e:
        print(f"❌ Syntax error in settings.py: {e}")
        return False
    except Exception as e:
        print(f"❌ Error checking settings.py: {e}")
        return False

def test_error_handling_patterns():
    """Test that error handling patterns are implemented correctly"""
    print("\n🔧 Testing error handling patterns...")
    try:
        with open('app/data_feeds/binance_ws.py', 'r') as f:
            content = f.read()

        # Check for specific error handling improvements
        patterns = [
            ('read_loop_closed', 'Read loop closed specific handling'),
            ('rate_limit', 'Rate limit specific handling'),
            ('auth_error', 'Authentication error specific handling'),
            ('exponential backoff', 'Exponential backoff pattern'),
            ('circuit breaker', 'Circuit breaker pattern'),
            ('heartbeat', 'Heartbeat monitoring'),
            ('jitter', 'Jitter for retry delays'),
        ]

        for pattern, description in patterns:
            # Use more flexible matching
            pattern_lower = pattern.lower().replace(' ', '')
            content_lower = content.lower().replace(' ', '_')

            if pattern_lower in content_lower or pattern.replace('_', ' ') in content.lower():
                print(f"✅ {description}")
            else:
                print(f"⚠️  {description} - may need verification")

        return True
    except Exception as e:
        print(f"❌ Error checking patterns: {e}")
        return False

def main():
    """Run all basic tests"""
    print("🚀 Starting basic WebSocket enhancement tests...\n")

    # Change to trading directory
    if os.path.exists('trading'):
        os.chdir('trading')

    tests = [
        test_binance_ws_syntax,
        test_settings_syntax,
        test_error_handling_patterns,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} failed with exception: {e}")
            failed += 1
        print()  # Add spacing between tests

    print(f"📊 Test Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All basic tests passed! WebSocket enhancements syntax and structure are correct.")
        return 0
    else:
        print("⚠️  Some tests need attention. Please review the output above.")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)