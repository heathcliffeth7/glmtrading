#!/usr/bin/env python3
"""
Price Cache Debug Tool - BTC fiyat sorunlarını teşhis etme ve düzeltme
"""

import asyncio
import sys
sys.path.append('/root/trading')

from app.utils.price_cache import price_cache
from app.data_feeds.binance_ws import BinanceWebSocketClient
from app.utils.logging import get_logger
from app.executor.executor import Executor

logger = get_logger(__name__)

async def debug_price_cache():
    """Price cache durumunu analiz et ve düzelt"""
    print("=" * 60)
    print("🔍 BTCUSDT Price Cache Debug Tool")
    print("=" * 60)

    symbol = "BTCUSDT"

    # 1. Mevcut cache durumunu kontrol et
    print("\n📊 Current Cache Status:")
    snapshot = price_cache.get_snapshot(symbol)

    if snapshot:
        print(f"  ✅ Cached Price: ${snapshot.price:.2f}")
        print(f"  🕒 Age: {snapshot.age_seconds():.1f}s")
        print(f"  📡 Source: {snapshot.source}")
        print(f"  ✅ Valid: {snapshot.is_valid()}")
        print(f"  ⚠️ Stale: {snapshot.is_stale()}")
    else:
        print("  ❌ No cached price found")

    # 2. REST API fiyatını kontrol et
    print(f"\n🌐 REST API Price Check:")
    try:
        executor = Executor(symbol)
        rest_price = executor._resolve_price()

        if rest_price > 0:
            print(f"  ✅ REST Price: ${rest_price:.2f}")

            # Fiyat farkını hesapla
            if snapshot:
                diff_pct = abs(rest_price - snapshot.price) / snapshot.price
                print(f"  📈 Difference: {diff_pct * 100:.2f}%")

                if diff_pct > 0.10:
                    print("  🚨 SIGNIFICANT DIFFERENCE (>10%) - Cache reset needed!")

                    # Manuel cache reset
                    print(f"  🔄 Force resetting cache from ${snapshot.price:.2f} to ${rest_price:.2f}")
                    price_cache.force_reset(symbol, rest_price, "manual_debug")
                else:
                    # Yine de manuel düzeltelim - cache'deki yanlış veriyi güncelle
                    print(f"  🔄 Manually updating cache from ${snapshot.price:.2f} to ${rest_price:.2f}")
                    price_cache.force_reset(symbol, rest_price, "manual_debug")

                    # Verify reset
                new_snapshot = price_cache.get_snapshot(symbol)
                if new_snapshot and new_snapshot.price == rest_price:
                    print("  ✅ Cache reset successful!")
                else:
                    print("  ❌ Cache reset failed!")
            else:
                print("  ⚠️ NO SNAPSHOT FOUND - Manual fix")
                price_cache.force_reset(symbol, rest_price, "emergency_fix")
        else:
            print("  ❌ Failed to get REST price")

    except Exception as e:
        print(f"  ❌ Error checking REST price: {e}")

    # 3. WebSocket connection kontrolü
    print(f"\n📡 WebSocket Connection Check:")
    try:
        ws_manager = BinanceWebSocketClient(symbol, "1m")

        # WebSocket attributes kontrolü
        print(f"  📊 Last valid price: ${ws_manager._last_valid_price}")
        print(f"  📊 Last REST price: ${ws_manager._last_rest_price}")
        print(f"  📊 Max change threshold: {ws_manager._max_price_change_pct * 100:.1f}%")
        print(f"  📊 Cache reset threshold: {ws_manager._cache_reset_threshold * 100:.1f}%")

        # REST price update test
        print(f"\n🔄 Testing REST price update...")
        if rest_price > 0:
            ws_manager.update_rest_price(rest_price)
            print(f"  ✅ Updated REST price in WebSocket manager: ${rest_price:.2f}")

    except Exception as e:
        print(f"  ❌ Error checking WebSocket: {e}")

    # 4. Son durum kontrolü
    print(f"\n🎯 Final Cache Status:")
    final_snapshot = price_cache.get_snapshot(symbol)

    if final_snapshot:
        print(f"  ✅ Final Price: ${final_snapshot.price:.2f}")
        print(f"  🕒 Age: {final_snapshot.age_seconds():.1f}s")
        print(f"  📡 Source: {final_snapshot.source}")
        print(f"  🟢 Cache is healthy!")
    else:
        print("  ❌ Still no cached price")

    print("\n" + "=" * 60)
    print("✅ Price cache debug complete!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(debug_price_cache())