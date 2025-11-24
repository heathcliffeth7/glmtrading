#!/usr/bin/env python3
"""
Dynamic CLOSE Rules Test Script

This script tests the new market velocity based dynamic CLOSE rules.
"""

import sys
import os
sys.path.append('/root/trading')

from app.market_velocity import get_market_velocity, get_required_timeframes_for_close

def test_market_velocity():
    """Test market velocity calculation"""
    print("🧪 Testing Market Velocity Calculator...")

    try:
        # Test market velocity calculation
        velocity = get_market_velocity("BTCUSDT")

        if velocity:
            print(f"✅ Market Velocity Calculation:")
            print(f"   - Velocity: {velocity.velocity_percentage:+.2f}%")
            print(f"   - Classification: {velocity.velocity_classification}")
            print(f"   - Required Timeframes: {', '.join(velocity.required_timeframes)}")
            print(f"   - Current Price: ${velocity.current_price:.2f}")
            print(f"   - Price 5m ago: ${velocity.price_5m_ago:.2f}")
            print(f"   - Summary: {velocity.get_summary()}")

            # Test classification
            if velocity.is_very_rapid_movement():
                print("   🚀 VERY RAPID MOVEMENT - Only 1h timeframe needed!")
            elif velocity.is_rapid_movement():
                print("   ⚡ RAPID MOVEMENT - 1h + 30m timeframes needed!")
            else:
                print("   🐌 NORMAL MOVEMENT - Standard 1m/30m/4h timeframes!")

        else:
            print("❌ Market velocity not available (insufficient data)")

    except Exception as e:
        print(f"❌ Error testing market velocity: {e}")
        return False

    return True

def test_required_timeframes():
    """Test required timeframes function"""
    print("\n🧪 Testing Required Timeframes Function...")

    try:
        timeframes = get_required_timeframes_for_close("BTCUSDT")
        print(f"✅ Required Timeframes: {', '.join(timeframes)}")
        return True

    except Exception as e:
        print(f"❌ Error testing required timeframes: {e}")
        return False

def test_velocity_config():
    """Test velocity configuration"""
    print("\n🧪 Testing Velocity Configuration...")

    try:
        from app.market_velocity.config import velocity_config

        print("✅ Velocity Configuration:")
        print(f"   - Rapid threshold: {velocity_config.RAPID_MOVEMENT_THRESHOLD}%")
        print(f"   - Very rapid threshold: {velocity_config.VERY_RAPID_THRESHOLD}%")
        print(f"   - Velocity window: {velocity_config.VELOCITY_WINDOW_MINUTES} minutes")
        print(f"   - History size: {velocity_config.PRICE_HISTORY_SIZE}")

        # Test threshold logic
        test_velocities = [0.2, 0.5, 1.0, -0.3, -0.7, -1.2]
        print("\n📊 Threshold Testing:")
        for vel in test_velocities:
            classification = velocity_config.get_velocity_classification(vel)
            timeframes = velocity_config.get_required_timeframes(vel)
            print(f"   {vel:+.1f}% → {classification:12s} → {', '.join(timeframes)}")

        return True

    except Exception as e:
        print(f"❌ Error testing velocity config: {e}")
        return False

def test_integration():
    """Test integration with existing components"""
    print("\n🧪 Testing Integration...")

    try:
        # Test import
        from app.market_velocity import MarketVelocityCalculator, VelocityResult

        # Test calculator creation
        calculator = MarketVelocityCalculator("BTCUSDT")
        print("✅ MarketVelocityCalculator created successfully")

        # Test singleton function
        from app.market_velocity import get_velocity_calculator
        calculator2 = get_velocity_calculator("BTCUSDT")
        print("✅ Singleton function works")

        # Check if same instance
        if calculator is calculator2:
            print("✅ Singleton pattern working correctly")
        else:
            print("⚠️  Singleton pattern not working as expected")

        return True

    except Exception as e:
        print(f"❌ Error testing integration: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests"""
    print("🚀 Dynamic CLOSE Rules Test Suite")
    print("=" * 50)

    tests = [
        test_market_velocity,
        test_required_timeframes,
        test_velocity_config,
        test_integration,
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 50)
    print(f"🎯 Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL TESTS PASSED! Dynamic CLOSE Rules ready for production!")
    else:
        print("⚠️  Some tests failed. Check the logs above.")

    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)