#!/usr/bin/env python3
"""
HTF Destek/Direnç Filtresi Test Script'i
"""
import sys

sys.path.insert(0, '/root/trading')

from app.agents.multi_timeframe import MultiTimeframeAgent
from app.utils.influx import detect_htf_support_resistance


def test_htf_filter():
    print("🧪 HTF Destek/Direnç Filtresi Testi")
    print("=" * 50)
    
    symbol = "BTCUSDT"
    
    # 1. HTF Analizi Test
    print("\n1️⃣ HTF Destek/Direnç Analizi:")
    htf_result = detect_htf_support_resistance(symbol)
    print(f"   Durum: {htf_result.get('status')}")
    print(f"   Fiyat: ${htf_result.get('current_price', 0):,.2f}")
    print(f"   Destek bölgesi: {htf_result.get('in_support_zone', False)}")
    print(f"   Direnç bölgesi: {htf_result.get('in_resistance_zone', False)}")
    
    # 2. Normal Agent Test
    print("\n2️⃣ Normal MultiTimeframeAgent:")
    normal_agent = MultiTimeframeAgent(symbol, enable_htf_filter=False)
    normal_signal = normal_agent.generate_signal()
    print(f"   Sinyal: {normal_signal.direction}")
    print(f"   Güven: {normal_signal.confidence:.1%}")
    
    # 3. HTF Filtreli Agent Test
    print("\n3️⃣ HTF Filtreli MultiTimeframeAgent:")
    enhanced_agent = MultiTimeframeAgent(symbol, enable_htf_filter=True)
    enhanced_signal = enhanced_agent.generate_signal()
    print(f"   Sinyal: {enhanced_signal.direction}")
    print(f"   Güven: {enhanced_signal.confidence:.1%}")
    print(f"   Gerekçe: {enhanced_signal.reasoning}")
    
    # 4. Karşılaştırma
    print("\n4️⃣ Karşılaştırma:")
    if normal_signal.direction != enhanced_signal.direction:
        print("   ⚡ HTF filtresi sinyal yönünü değiştirdi!")
    else:
        print("   ➡️ HTF filtresi sinyali korudu (nötr bölge)")
    
    print("\n✅ Test Tamamlandı!")
    return enhanced_signal

if __name__ == "__main__":
    try:
        signal = test_htf_filter()
        print(f"\n🎯 Son Sinyal: {signal.direction} ({signal.confidence:.1%})")
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        sys.exit(1)
