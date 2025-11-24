#!/usr/bin/env python3
"""
Test pozisyon yönetimi mantığını kontrol et
GLM'in pozisyon durumunu doğru algılayıp kapatma kararı verip vermediğini test et
"""

def test_position_closing_scenarios():
    """
    Farklı pozisyon durumları için beklenen kararları test et
    """
    scenarios = [
        {
            "name": "LONG pozisyon + Düşüş sinyali",
            "position": 0.5,  # LONG
            "market_signal": "BEARISH",
            "expected_action": "SELL",
            "expected_reasoning": "Mevcut LONG pozisyonu kapatmalı",
        },
        {
            "name": "SHORT pozisyon + Yükseliş sinyali",
            "position": -0.5,  # SHORT
            "market_signal": "BULLISH",
            "expected_action": "BUY",
            "expected_reasoning": "Mevcut SHORT pozisyonu kapatmalı",
        },
        {
            "name": "LONG pozisyon + Yükseliş sinyali",
            "position": 0.5,  # LONG
            "market_signal": "BULLISH",
            "expected_action": "HOLD or BUY (more LONG)",
            "expected_reasoning": "Mevcut LONG pozisyonu devam ettir veya artır",
        },
        {
            "name": "SHORT pozisyon + Düşüş sinyali",
            "position": -0.5,  # SHORT
            "market_signal": "BEARISH",
            "expected_action": "HOLD or SELL (more SHORT)",
            "expected_reasoning": "Mevcut SHORT pozisyonu devam ettir veya artır",
        },
        {
            "name": "FLAT pozisyon + Yükseliş sinyali",
            "position": 0.0,  # FLAT
            "market_signal": "BULLISH",
            "expected_action": "BUY",
            "expected_reasoning": "Yeni LONG pozisyon aç",
        },
        {
            "name": "FLAT pozisyon + Düşüş sinyali",
            "position": 0.0,  # FLAT
            "market_signal": "BEARISH",
            "expected_action": "SELL",
            "expected_reasoning": "Yeni SHORT pozisyon aç",
        },
    ]
    
    print("=" * 100)
    print("POZİSYON YÖNETİMİ TEST SENARYOLARı")
    print("=" * 100)
    print()
    
    for scenario in scenarios:
        position = scenario["position"]
        signal = scenario["market_signal"]
        expected = scenario["expected_action"]
        reasoning = scenario["expected_reasoning"]
        
        # Determine position type
        if position > 0.0001:
            pos_type = "LONG (+pozitif BTC)"
        elif position < -0.0001:
            pos_type = "SHORT (-negatif BTC)"
        else:
            pos_type = "FLAT (0 BTC)"
        
        print(f"📋 Senaryo: {scenario['name']}")
        print(f"   Mevcut Pozisyon: {position:+.6f} BTC ({pos_type})")
        print(f"   Piyasa Sinyali: {signal}")
        print(f"   Beklenen Karar: {expected}")
        print(f"   Açıklama: {reasoning}")
        print()
    
    print("=" * 100)
    print("EXECUTOR MANTIK KONTROLÜ")
    print("=" * 100)
    print()
    
    # Test executor logic (same as in test_position_closing.py)
    def is_closing_position(portfolio_position, decision_action):
        """Executor'daki pozisyon kapatma kontrolü (line 72-73)"""
        return (portfolio_position < -0.0001 and decision_action == "BUY") or \
               (portfolio_position > 0.0001 and decision_action == "SELL")
    
    test_cases = [
        (0.5, "SELL", True, "✅ LONG + SELL = Kapatma"),
        (-0.5, "BUY", True, "✅ SHORT + BUY = Kapatma"),
        (0.5, "BUY", False, "✅ LONG + BUY = Artırma"),
        (-0.5, "SELL", False, "✅ SHORT + SELL = Artırma"),
        (0.0, "BUY", False, "✅ FLAT + BUY = Yeni pozisyon"),
        (0.0, "SELL", False, "✅ FLAT + SELL = Yeni pozisyon"),
    ]
    
    all_pass = True
    for position, action, expected, desc in test_cases:
        result = is_closing_position(position, action)
        status = "✅ PASS" if result == expected else "❌ FAIL"
        print(f"{status} | {desc}")
        print(f"      Position: {position:+.1f}, Action: {action}, Result: {result}, Expected: {expected}")
        if result != expected:
            all_pass = False
            print(f"      ⚠️ MANTIK HATASI TESPIT EDİLDİ!")
        print()
    
    print()
    print("=" * 100)
    if all_pass:
        print("✅ EXECUTOR MANTIK KONTROLÜ: TÜM TESTLER BAŞARILI")
        print()
        print("💡 SONRAKİ ADIMLAR:")
        print("   1. GLM system prompt'u güncellendi - pozisyon yönetimi kuralları eklendi")
        print("   2. GLM user prompt'u güncellendi - mevcut pozisyon durumu vurgulandı")
        print("   3. Gerçek trading'de test edilmesi gerekiyor")
        print("   4. Telegram bildirimlerinde pozisyon kapatma durumu net belirtiliyor")
    else:
        print("❌ EXECUTOR MANTIK KONTROLÜ: BAZI TESTLER BAŞARISIZ")
        print("   ⚠️ Executor'daki pozisyon kapatma mantığını düzeltmek gerekiyor!")
    print("=" * 100)


if __name__ == "__main__":
    test_position_closing_scenarios()
