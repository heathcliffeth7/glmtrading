#!/usr/bin/env python3
"""
WebSocket enhancements test script
Tests new connection management, error handling, and heartbeat features
"""

import asyncio
import sys
import os
from unittest.mock import Mock, patch

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def test_imports():
    """Test that all imports work correctly"""
    print("🔧 Testing imports...")
    try:
        from app.data_feeds.binance_ws import BinanceWebSocketClient, ConnectionState, ConnectionMetrics
        from app.config.settings import get_settings
        print("✅ All imports successful")
        return True
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False

def test_connection_states():
    """Test connection state management"""
    print("🔧 Testing connection states...")
    try:
        from app.data_feeds.binance_ws import ConnectionState, ConnectionMetrics

        # Test enum values
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.ERROR.value == "error"

        # Test metrics dataclass
        metrics = ConnectionMetrics(state=ConnectionState.DISCONNECTED)
        assert metrics.state == ConnectionState.DISCONNECTED
        assert metrics.reconnect_attempts == 0
        assert metrics.total_messages == 0

        print("✅ Connection states test passed")
        return True
    except Exception as e:
        print(f"❌ Connection states test failed: {e}")
        return False

def test_websocket_client_initialization():
    """Test WebSocket client initialization with new settings"""
    print("🔧 Testing WebSocket client initialization...")
    try:
        from app.data_feeds.binance_ws import BinanceWebSocketClient

        # Mock settings to avoid needing real API keys
        with patch('app.data_feeds.binance_ws.settings') as mock_settings:
            mock_settings.binance.api_key = "test_key"
            mock_settings.binance.api_secret = "test_secret"
            mock_settings.binance.websocket.max_reconnect_attempts = 5
            mock_settings.binance.websocket.base_retry_delay = 3
            mock_settings.binance.websocket.ping_interval = 25

            client = BinanceWebSocketClient("BTCUSDT", "1m")

            # Check that settings are applied
            assert client._max_reconnect_attempts == 5
            assert client._base_retry_delay == 3
            assert client._ping_interval == 25
            assert client._symbol == "BTCUSDT"
            assert client._interval == "1m"

            # Check stream name generation
            assert client.stream_name == "btcusdt@kline_1m"

        print("✅ WebSocket client initialization test passed")
        return True
    except Exception as e:
        print(f"❌ WebSocket client initialization test failed: {e}")
        return False

def test_error_classification():
    """Test error classification logic"""
    print("🔧 Testing error classification...")
    try:
        from app.data_feeds.binance_ws import BinanceWebSocketClient

        # Mock settings
        with patch('app.data_feeds.binance_ws.settings') as mock_settings:
            mock_settings.binance.api_key = "test_key"
            mock_settings.binance.api_secret = "test_secret"
            mock_settings.binance.websocket.max_reconnect_attempts = 5
            mock_settings.binance.websocket.base_retry_delay = 3
            mock_settings.binance.websocket.ping_interval = 25

            client = BinanceWebSocketClient("BTCUSDT", "1m")

            # Test error classification
            test_cases = [
                ("Read loop has been closed", "read_loop_closed"),
                ("Connection lost", "connection_lost"),
                ("Timeout occurred", "timeout"),
                ("Rate limit exceeded", "rate_limit"),
                ("Invalid API key", "auth_error"),
                ("Unknown error", "unknown"),
            ]

            for error_msg, expected_type in test_cases:
                error = Exception(error_msg)
                classified = client._classify_error(error)
                assert classified == expected_type, f"Expected {expected_type}, got {classified} for '{error_msg}'"

        print("✅ Error classification test passed")
        return True
    except Exception as e:
        print(f"❌ Error classification test failed: {e}")
        return False

def test_retry_delay_calculation():
    """Test exponential backoff with jitter"""
    print("🔧 Testing retry delay calculation...")
    try:
        from app.data_feeds.binance_ws import BinanceWebSocketClient

        # Mock settings
        with patch('app.data_feeds.binance_ws.settings') as mock_settings:
            mock_settings.binance.api_key = "test_key"
            mock_settings.binance.api_secret = "test_secret"
            mock_settings.binance.websocket.max_reconnect_attempts = 5
            mock_settings.binance.websocket.base_retry_delay = 5
            mock_settings.binance.websocket.max_retry_delay = 60
            mock_settings.binance.websocket.ping_interval = 25

            client = BinanceWebSocketClient("BTCUSDT", "1m")

            # Test retry delay calculation
            for attempt in range(5):
                delay = client._calculate_retry_delay(attempt)
                expected_min = min(client._base_retry_delay * (2 ** attempt), client._max_retry_delay)
                expected_max = expected_min * 1.3  # max jitter (0.3 * delay)

                assert delay >= expected_min, f"Attempt {attempt}: delay {delay} < expected min {expected_min}"
                assert delay <= expected_max, f"Attempt {attempt}: delay {delay} > expected max {expected_max}"

        print("✅ Retry delay calculation test passed")
        return True
    except Exception as e:
        print(f"❌ Retry delay calculation test failed: {e}")
        return False

def test_settings_integration():
    """Test that WebSocket settings are properly integrated"""
    print("🔧 Testing settings integration...")
    try:
        from app.config.settings import get_settings

        settings = get_settings()

        # Check that WebSocket settings exist
        assert hasattr(settings.binance, 'websocket'), "WebSocket settings not found"

        ws_settings = settings.binance.websocket

        # Check default values
        assert ws_settings.max_reconnect_attempts == 10
        assert ws_settings.base_retry_delay == 5
        assert ws_settings.max_retry_delay == 60
        assert ws_settings.circuit_breaker_threshold == 5
        assert ws_settings.connection_cooldown == 300
        assert ws_settings.ping_interval == 30
        assert ws_settings.heartbeat_check_interval == 10
        assert ws_settings.message_timeout_multiplier == 2.0
        assert ws_settings.validate_ohlc_relationship == True
        assert ws_settings.max_price_change_pct == 0.20
        assert ws_settings.cache_reset_threshold == 0.10

        print("✅ Settings integration test passed")
        return True
    except Exception as e:
        print(f"❌ Settings integration test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Starting WebSocket enhancements tests...\n")

    tests = [
        test_imports,
        test_connection_states,
        test_websocket_client_initialization,
        test_error_classification,
        test_retry_delay_calculation,
        test_settings_integration,
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
        print("🎉 All tests passed! WebSocket enhancements are working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)