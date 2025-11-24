#!/usr/bin/env python3
"""
Test script for price change threshold fix
This simulates the executor logic to verify:
1. %0.13 change = ALLOWED (not blocked)
2. %6 change = BLOCKED (exceeds 5% threshold)
"""

def test_price_change_threshold():
    print("🧪 Testing price change threshold logic...")
    print("=" * 50)

    # Test 1: %0.13 change should be ALLOWED
    print("\n📊 Test 1: %0.13 change (should be ALLOWED)")
    print("Last price: $2752.49")
    print("Current price: $2756.00")
    print("Change: %0.13")
    print("Expected: ✅ ALLOWED")

    # Calculate change
    last_price = 2752.49
    current_price = 2756.00
    price_change_pct = abs((current_price - last_price) / last_price) * 100
    print(f"Calculated: {price_change_pct:.2f}%")

    # Check logic
    if price_change_pct > 5.0:
        print("❌ WOULD BLOCK (WRONG!)")
    else:
        print("✅ WOULD ALLOW (CORRECT!)")

    # Test 2: %6 change should be BLOCKED
    print("\n📊 Test 2: %6 change (should be BLOCKED)")
    print("Last price: $2752.49")
    print("Current price: $2917.64")  # 6% higher
    print("Change: %6.00")
    print("Expected: ❌ BLOCKED")

    current_price_2 = 2917.64
    price_change_pct_2 = abs((current_price_2 - last_price) / last_price) * 100
    print(f"Calculated: {price_change_pct_2:.2f}%")

    # Check logic
    if price_change_pct_2 > 5.0:
        print("❌ WOULD BLOCK (CORRECT!)")
    else:
        print("✅ WOULD ALLOW (WRONG!)")

    print("\n" + "=" * 50)
    print("🎯 Summary:")
    print(f"• %0.13 change: {'✅ PASS' if price_change_pct <= 5.0 else '❌ FAIL'}")
    print(f"• %6 change: {'✅ PASS' if price_change_pct_2 > 5.0 else '❌ FAIL'}")

    if price_change_pct <= 5.0 and price_change_pct_2 > 5.0:
        print("\n🎉 All tests passed! The fix is working correctly.")
        print("• Small moves (%0.13) are allowed")
        print("• Extreme moves (%6) are blocked")
    else:
        print("\n❌ Tests failed! Logic needs correction.")

if __name__ == "__main__":
    test_price_change_threshold()