#!/usr/bin/env python3
"""
Test pozisyon kapatma mantığını kontrol et
"""

def test_closing_logic_v1(position, action):
    """Executor'daki ilk kontrol (line 72-73)"""
    is_closing = (position < -0.0001 and action == "BUY") or \
                 (position > 0.0001 and action == "SELL")
    return is_closing

def test_closing_logic_v2(position, action):
    """Executor'daki ikinci kontrol (line 145-148) - düzeltilmiş"""
    is_long = position > 0
    is_short = position < 0
    is_closing = (is_long and action == "SELL") or (is_short and action == "BUY")
    return is_closing

# Test senaryoları
scenarios = [
    # (position, action, expected_result, description)
    (0.5, "SELL", True, "LONG pozisyon → SELL ile kapatılıyor"),
    (-0.5, "BUY", True, "SHORT pozisyon → BUY ile kapatılıyor"),
    (0.5, "BUY", False, "LONG pozisyon → BUY ile artırılıyor"),
    (-0.5, "SELL", False, "SHORT pozisyon → SELL ile artırılıyor"),
    (0.0, "BUY", False, "Boş portföy → BUY açılıyor"),
    (0.0, "SELL", False, "Boş portföy → SELL açılıyor"),
]

print("=" * 80)
print("POZİSYON KAPATMA MANTIK TESTİ")
print("=" * 80)
print()

print("TEST: İlk Kontrol (line 72-73)")
print("-" * 80)
for position, action, expected, desc in scenarios:
    result_v1 = test_closing_logic_v1(position, action)
    status = "✅ PASS" if result_v1 == expected else "❌ FAIL"
    print(f"{status} | {desc}")
    print(f"      Position: {position:+.1f}, Action: {action}, Result: {result_v1}, Expected: {expected}")
    if result_v1 != expected:
        print(f"      ⚠️ MANTIK HATASI!")
    print()

print()
print("TEST: İkinci Kontrol (line 145-148) - Düzeltilmiş")
print("-" * 80)
for position, action, expected, desc in scenarios:
    result_v2 = test_closing_logic_v2(position, action)
    status = "✅ PASS" if result_v2 == expected else "❌ FAIL"
    print(f"{status} | {desc}")
    print(f"      Position: {position:+.1f}, Action: {action}, Result: {result_v2}, Expected: {expected}")
    if result_v2 != expected:
        print(f"      ⚠️ MANTIK HATASI!")
    print()

print()
print("=" * 80)
print("SONUÇ:")
print("=" * 80)

all_v1_pass = all(test_closing_logic_v1(p, a) == e for p, a, e, _ in scenarios)
all_v2_pass = all(test_closing_logic_v2(p, a) == e for p, a, e, _ in scenarios)

print(f"İlk Kontrol (line 72): {'✅ TÜM TESTLER GEÇER' if all_v1_pass else '❌ BAZI TESTLER BAŞARISIZ'}")
print(f"İkinci Kontrol (line 145): {'✅ TÜM TESTLER GEÇER' if all_v2_pass else '❌ BAZI TESTLER BAŞARISIZ'}")
print()

if not all_v1_pass or not all_v2_pass:
    print("⚠️ UYARI: İki kontrol farklı sonuç veriyor! Birini düzeltmek gerekebilir.")
else:
    print("✅ Her iki kontrol de doğru çalışıyor!")
