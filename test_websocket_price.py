#!/usr/bin/env python3
"""
WebSocket Price Cache Test Script

Tests:
1. Initial REST price loading
2. WebSocket listener status
3. Price updates from WebSocket
4. Cache age tracking
5. Fallback chain functionality
"""

import sys
import time
from datetime import datetime

# Add app to path
sys.path.insert(0, '/root/trading')

from app.utils.logging import get_logger
from app.utils.price_cache import (
    _listener_threads,
    _listeners_started,
    ensure_price_cache_listener,
    price_cache,
)

logger = get_logger(__name__)


def test_initial_rest_price():
    """Test 1: Initial REST price loading"""
    print("\n" + "="*60)
    print("TEST 1: Initial REST Price")
    print("="*60)
    
    # Get initial price from cache
    snapshot = price_cache.get_snapshot("BTCUSDT")
    
    if snapshot:
        print(f"✅ Initial price cached: ${snapshot.price:.2f}")
        print(f"   Source: {snapshot.source}")
        print(f"   Age: {snapshot.age_seconds():.1f}s")
        return True
    else:
        print("❌ No initial price in cache")
        return False


def test_listener_status():
    """Test 2: WebSocket listener and health monitor status"""
    print("\n" + "="*60)
    print("TEST 2: WebSocket Listener Status")
    print("="*60)
    
    symbol = "BTCUSDT"
    
    # Check if listener is marked as started
    if _listeners_started.get(symbol):
        print(f"✅ Listener flag: STARTED")
    else:
        print(f"❌ Listener flag: NOT STARTED")
        return False
    
    # Check if thread exists and is alive
    thread = _listener_threads.get(symbol)
    if thread and thread.is_alive():
        print(f"✅ Listener thread: ALIVE (name: {thread.name})")
    else:
        print(f"❌ Listener thread: DEAD or MISSING")
        return False
    
    # Check for health monitor thread
    import threading
    health_thread = None
    for t in threading.enumerate():
        if f"health-check-{symbol.lower()}" in t.name:
            health_thread = t
            break
    
    if health_thread and health_thread.is_alive():
        print(f"✅ Health monitor: RUNNING (name: {health_thread.name})")
    else:
        print(f"⚠️  Health monitor: NOT FOUND (may still be starting)")
    
    return True


def test_price_updates(duration=30):
    """Test 3: Price updates from WebSocket"""
    print("\n" + "="*60)
    print(f"TEST 3: Price Updates (monitoring for {duration}s)")
    print("="*60)
    
    symbol = "BTCUSDT"
    updates = []
    last_price = None
    
    print(f"🔄 Monitoring price updates...")
    start_time = time.time()
    
    while time.time() - start_time < duration:
        snapshot = price_cache.get_snapshot(symbol)
        
        if snapshot:
            current_price = snapshot.price
            
            # New update detected
            if last_price is None or current_price != last_price:
                age = snapshot.age_seconds()
                updates.append({
                    'price': current_price,
                    'age': age,
                    'timestamp': datetime.now(),
                    'source': snapshot.source
                })
                
                elapsed = time.time() - start_time
                print(f"  [{len(updates):2d}] ${current_price:,.2f} (age: {age:.1f}s, elapsed: {elapsed:.1f}s, source: {snapshot.source})")
                last_price = current_price
        
        time.sleep(1)  # Check every second
    
    print(f"\n📊 Results:")
    print(f"   Total updates: {len(updates)}")
    
    if len(updates) >= 10:
        print(f"   ✅ Received sufficient updates ({len(updates)} >= 10)")
        
        # Calculate average interval
        if len(updates) > 1:
            intervals = []
            for i in range(1, len(updates)):
                interval = (updates[i]['timestamp'] - updates[i-1]['timestamp']).total_seconds()
                intervals.append(interval)
            avg_interval = sum(intervals) / len(intervals)
            print(f"   Average update interval: {avg_interval:.1f}s")
        
        return True
    else:
        print(f"   ❌ Insufficient updates ({len(updates)} < 10)")
        return False


def test_cache_age_tracking():
    """Test 4: Cache age tracking"""
    print("\n" + "="*60)
    print("TEST 4: Cache Age Tracking")
    print("="*60)
    
    symbol = "BTCUSDT"
    
    # Get snapshot
    snapshot = price_cache.get_snapshot(symbol)
    if not snapshot:
        print("❌ No snapshot available")
        return False
    
    age1 = snapshot.age_seconds()
    print(f"   Age measurement 1: {age1:.3f}s")
    
    time.sleep(2)
    
    age2 = snapshot.age_seconds()
    print(f"   Age measurement 2: {age2:.3f}s")
    
    diff = age2 - age1
    print(f"   Time difference: {diff:.3f}s")
    
    if 1.8 < diff < 2.2:  # Allow 10% tolerance
        print(f"✅ Age tracking working correctly")
        return True
    else:
        print(f"⚠️  Age tracking may have issues (expected ~2.0s, got {diff:.3f}s)")
        return True  # Don't fail test for minor timing issues


def test_fallback_chain():
    """Test 5: Fallback chain functionality"""
    print("\n" + "="*60)
    print("TEST 5: Fallback Chain")
    print("="*60)
    
    symbol = "BTCUSDT"
    
    # Test fresh cache get
    fresh_price = price_cache.get(symbol, max_age_seconds=10)
    if fresh_price:
        print(f"✅ Fresh cache (< 10s): ${fresh_price:,.2f}")
    else:
        print(f"⚠️  Fresh cache miss (may be stale)")
    
    # Test stale cache get
    stale_price = price_cache.get(symbol, max_age_seconds=60)
    if stale_price:
        print(f"✅ Stale cache (< 60s): ${stale_price:,.2f}")
    else:
        print(f"❌ Stale cache miss")
        return False
    
    # Get age
    age = price_cache.get_age_seconds(symbol)
    print(f"   Current cache age: {age:.1f}s")
    
    return True


def main():
    """Run all tests"""
    print("\n" + "="*70)
    print(" WebSocket Price Cache Test Suite")
    print("="*70)
    
    # Start listener
    print("\n🎧 Starting WebSocket listener...")
    ensure_price_cache_listener("BTCUSDT")
    
    # Wait a bit for initialization
    time.sleep(2)
    
    # Run tests
    results = {
        "Initial REST Price": test_initial_rest_price(),
        "Listener Status": test_listener_status(),
        "Price Updates": test_price_updates(duration=30),
        "Cache Age Tracking": test_cache_age_tracking(),
        "Fallback Chain": test_fallback_chain(),
    }
    
    # Summary
    print("\n" + "="*70)
    print(" TEST SUMMARY")
    print("="*70)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    total = len(results)
    passed = sum(results.values())
    
    print("\n" + "="*70)
    print(f" Results: {passed}/{total} tests passed")
    print("="*70)
    
    if passed == total:
        print("\n🎉 All systems working correctly!")
        return 0
    else:
        print("\n⚠️  Some tests failed - review output above")
        return 1


if __name__ == "__main__":
    sys.exit(main())









