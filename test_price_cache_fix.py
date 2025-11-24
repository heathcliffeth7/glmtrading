#!/usr/bin/env python3
"""
Test script for price cache listener fix
"""
import sys
import time


def test_price_cache():
    """Test that price cache listener works correctly"""
    print("=" * 80)
    print("PRICE CACHE LISTENER TEST")
    print("=" * 80)
    print()
    
    from app.utils.price_cache import ensure_price_cache_listener, price_cache

    # Start listener
    print("1. Starting price cache listener for BTCUSDT...")
    ensure_price_cache_listener('BTCUSDT')
    print()
    
    # Wait for data
    print("2. Waiting for WebSocket data (5 seconds)...")
    for i in range(5, 0, -1):
        print(f"   {i}...", end='', flush=True)
        time.sleep(1)
    print(" done!")
    print()
    
    # Check cache
    print("3. Checking price cache...")
    snapshot = price_cache.get_snapshot('BTCUSDT')
    
    if snapshot:
        print(f"✅ SUCCESS: Price cached!")
        print(f"   Price: ${snapshot.price:,.2f}")
        print(f"   Age: {snapshot.age_seconds():.1f}s")
        print(f"   Source: {snapshot.source}")
        print()
        
        # Try to get price with validation
        print("4. Testing price retrieval with 10s age limit...")
        price = price_cache.get('BTCUSDT', max_age_seconds=10)
        if price:
            print(f"✅ Got price: ${price:,.2f}")
        else:
            print("❌ Could not get price (too old or invalid)")
        print()
        
        return True
    else:
        print("❌ FAILED: No price in cache after 5 seconds")
        print()
        print("Possible issues:")
        print("  - WebSocket connection not working")
        print("  - Redis messages not being published")
        print("  - Thread crashed silently")
        print()
        return False

def test_executor_price_validation():
    """Test executor's _get_current_price_validated method"""
    print("=" * 80)
    print("EXECUTOR PRICE VALIDATION TEST")
    print("=" * 80)
    print()
    
    # This would require full executor initialization
    # Just check that method exists
    try:
        from app.executor.executor import Executor

        # Check method exists
        if hasattr(Executor, '_get_current_price_validated'):
            print("✅ _get_current_price_validated() method exists")
            
            # Check signature
            import inspect
            sig = inspect.signature(Executor._get_current_price_validated)
            params = list(sig.parameters.keys())
            print(f"   Parameters: {params}")
            
            if 'symbol' in params and 'max_age_seconds' in params:
                print("✅ Method has correct parameters")
                return True
            else:
                print("❌ Method parameters incorrect")
                return False
        else:
            print("❌ _get_current_price_validated() method NOT found")
            return False
    
    except Exception as exc:
        print(f"❌ Error testing executor: {exc}")
        return False

if __name__ == "__main__":
    print()
    
    # Test 1: Price cache listener
    cache_ok = test_price_cache()
    
    print()
    
    # Test 2: Executor method
    executor_ok = test_executor_price_validation()
    
    print()
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"Price Cache Listener: {'✅ PASS' if cache_ok else '❌ FAIL'}")
    print(f"Executor Method:      {'✅ PASS' if executor_ok else '❌ FAIL'}")
    print()
    
    if cache_ok and executor_ok:
        print("✅ ALL TESTS PASSED!")
        sys.exit(0)
    else:
        print("❌ SOME TESTS FAILED")
        sys.exit(1)
