#!/usr/bin/env python3
"""Window size hesaplama testi"""

# Window size hesaplama mantığı
def calculate_window_size(interval: str) -> int:
    target_hours = 24
    interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
    minutes = interval_minutes.get(interval, 5)
    window_size = (target_hours * 60) // minutes
    return window_size


if __name__ == "__main__":
    intervals = ["1m", "5m", "15m", "1h", "4h"]
    
    print("=" * 60)
    print("Window Size Hesaplama Testi (24 saatlik hedef)")
    print("=" * 60)
    print()
    
    for interval in intervals:
        window_size = calculate_window_size(interval)
        
        # Gerçek zaman hesaplama
        interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
        minutes = interval_minutes.get(interval, 5)
        actual_hours = (window_size * minutes) / 60
        
        print(f"Interval: {interval:5s}")
        print(f"  Window Size: {window_size:5d} bar")
        print(f"  Gerçek Süre: {actual_hours:5.1f} saat")
        print(f"  EMA 200 için: {'✅ Yeterli' if window_size >= 200 else '❌ Yetersiz'}")
        print()
    
    print("=" * 60)
    print("Önceki sistem (240 bar sabit):")
    print("=" * 60)
    print()
    print("5m interval:")
    print(f"  240 bar × 5 min = {240 * 5 / 60:.1f} saat")
    print(f"  EMA 200: 200 bar × 5 min = {200 * 5 / 60:.1f} saat (buffer: {(240-200) * 5 / 60:.1f} saat)")
    print()
    print("=" * 60)
    print("Yeni sistem (288 bar @ 5m):")
    print("=" * 60)
    print()
    print("5m interval:")
    print(f"  288 bar × 5 min = {288 * 5 / 60:.1f} saat")
    print(f"  EMA 200: 200 bar × 5 min = {200 * 5 / 60:.1f} saat (buffer: {(288-200) * 5 / 60:.1f} saat)")
    print()
    print("✅ İyileştirme: +{:.1f} saat buffer (+%{:.0f})".format(
        (288-240) * 5 / 60,
        ((288-240) / 240) * 100
    ))
