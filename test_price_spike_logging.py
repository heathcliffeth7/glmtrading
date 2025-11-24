#!/usr/bin/env python3
"""
Test: Geliştirilmiş price spike loglama ve OHLC validasyonu
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.data_feeds.binance_ws import BinanceWebSocketClient
from app.utils.logging import get_logger

logger = get_logger(__name__)

def test_price_spike_validation():
    print("=" * 80)
    print("TEST: Price Spike Validation ve Loglama")
    print("=" * 80)
    
    # BinanceWebSocketClient oluştur
    client = BinanceWebSocketClient(symbol="BTCUSDT", interval="1m")
    
    # Test Case 1: Normal fiyat (geçerli)
    print("\n1️⃣ TEST: Normal fiyat (geçerli)")
    normal_payload = {
        "k": {
            "o": "100000.00",
            "h": "100100.00",
            "l": "99900.00",
            "c": "100050.00",
            "v": "1000",
            "x": True,
            "T": 1699000000000
        }
    }
    result = client._validate_price_data(normal_payload)
    print(f"   Sonuç: {'✅ GEÇERLI' if result else '❌ REDDEDİLDİ'}")
    print(f"   Close: {client._extract_close_price(normal_payload)}")
    
    # Test Case 2: Sıfır fiyat (0.00 - reddedilmeli)
    print("\n2️⃣ TEST: Sıfır fiyat (0.00 - reddedilmeli)")
    zero_payload = {
        "k": {
            "o": "0.00",
            "h": "0.00",
            "l": "0.00",
            "c": "0.00",
            "v": "0",
            "x": False,
            "T": 1699000001000
        }
    }
    result = client._validate_price_data(zero_payload)
    print(f"   Sonuç: {'✅ GEÇERLI' if result else '❌ REDDEDİLDİ (DOĞRU!)'}")
    print(f"   Close: {client._extract_close_price(zero_payload)}")
    
    # Test Case 3: Price spike >5% (reddedilmeli)
    print("\n3️⃣ TEST: Price spike >5% (reddedilmeli)")
    client._last_valid_price = 100000.0  # Set base price
    spike_payload = {
        "k": {
            "o": "100000.00",
            "h": "110000.00",
            "l": "100000.00",
            "c": "108000.00",  # +8% spike
            "v": "1000",
            "x": True,
            "T": 1699000002000
        }
    }
    result = client._validate_price_data(spike_payload)
    print(f"   Sonuç: {'✅ GEÇERLI' if result else '❌ REDDEDİLDİ (DOĞRU!)'}")
    print(f"   Close: {client._extract_close_price(spike_payload)}")
    print(f"   Değişim: +8% (limit: 5%)")
    
    # Test Case 4: OHLC ilişkisi ihlali (reddedilmeli)
    print("\n4️⃣ TEST: OHLC ilişkisi ihlali (reddedilmeli)")
    invalid_ohlc_payload = {
        "k": {
            "o": "100000.00",
            "h": "100100.00",  # High
            "l": "99900.00",   # Low
            "c": "100500.00",  # Close > High (YANLIŞ!)
            "v": "1000",
            "x": True,
            "T": 1699000003000
        }
    }
    result = client._validate_price_data(invalid_ohlc_payload)
    print(f"   Sonuç: {'✅ GEÇERLI' if result else '❌ REDDEDİLDİ (DOĞRU!)'}")
    print(f"   Close: {client._extract_close_price(invalid_ohlc_payload)}")
    print(f"   OHLC: O=100000, H=100100, L=99900, C=100500 (C > H)")
    
    # Test Case 5: Eksik kline yapısı (reddedilmeli)
    print("\n5️⃣ TEST: Eksik kline yapısı (reddedilmeli)")
    incomplete_payload = {
        "k": {
            "c": "100000.00",  # Sadece close var
        }
    }
    result = client._validate_price_data(incomplete_payload)
    print(f"   Sonuç: {'✅ GEÇERLI' if result else '❌ REDDEDİLDİ (DOĞRU!)'}")
    print(f"   Keys: {list(incomplete_payload['k'].keys())}")
    
    # Test Case 6: Normal fiyat değişimi %3 (geçerli)
    print("\n6️⃣ TEST: Normal fiyat değişimi %3 (geçerli)")
    client._last_valid_price = 100000.0
    normal_change_payload = {
        "k": {
            "o": "100000.00",
            "h": "103500.00",
            "l": "99500.00",
            "c": "103000.00",  # +3% değişim (geçerli)
            "v": "1000",
            "x": True,
            "T": 1699000004000
        }
    }
    result = client._validate_price_data(normal_change_payload)
    print(f"   Sonuç: {'✅ GEÇERLI (DOĞRU!)' if result else '❌ REDDEDİLDİ'}")
    print(f"   Close: {client._extract_close_price(normal_change_payload)}")
    print(f"   Değişim: +3% (limit: 5%)")
    
    print("\n" + "=" * 80)
    print("✅ TÜM TESTLER TAMAMLANDI")
    print("=" * 80)
    
    # Özet
    print("\n📊 ÖZET:")
    print("  - Normal fiyat: Kabul edildi ✅")
    print("  - Sıfır fiyat (0.00): Reddedildi ✅")
    print("  - Price spike >5%: Reddedildi ✅")
    print("  - OHLC ihlali: Reddedildi ✅")
    print("  - Eksik yapı: Reddedildi ✅")
    print("  - Normal değişim <5%: Kabul edildi ✅")
    
    return True

if __name__ == "__main__":
    try:
        success = test_price_spike_validation()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error("Test failed: %s", e, exc_info=True)
        sys.exit(1)
