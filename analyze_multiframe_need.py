#!/usr/bin/env python3
"""30m indicator'leri için multiframe gerekli mi analizi."""

print("="*80)
print("30M INDICATOR'LER İÇİN MULTIFRAME GEREKLİ Mİ?")
print("="*80)
print()

# Current setup
current_1m = ["close", "rsi_14", "macd", "ema_20"]
current_30m = [
    "long_short_ratio", "open_interest", "funding_rate",
    "close", "ema_20", "ema_50", "rsi_14", "macd", "macd_signal",
    "atr_14", "stoch_k", "stoch_d", "bb_upper", "bb_middle", "bb_lower",
    "willr", "cci", "mfi", "obv", "vwap_20"
]
current_4h = ["ema_20", "ema_50", "rsi_14", "macd", "atr_14", "volume"]

print("ŞU ANKİ DURUM:")
print(f"  1m:  {len(current_1m)} indicator")
print(f"  30m: {len(current_30m)} indicator")
print(f"  4h:  {len(current_4h)} indicator")
print()

# Analyze each 30m indicator
indicators_30m = {
    "Core (Multi-TF mantıklı)": {
        "close": "✅ Zaten var (1m, 30m)",
        "ema_20": "✅ Zaten var (1m, 30m, 4h)",
        "ema_50": "✅ Zaten var (30m, 4h)",
        "rsi_14": "✅ Zaten var (1m, 30m, 4h)",
        "macd": "✅ Zaten var (1m, 30m, 4h)",
    },
    "Momentum (1m'de faydalı olabilir)": {
        "stoch_k": "🟡 1m'de overbought/oversold timing için kullanılabilir",
        "stoch_d": "🟡 Stoch_k ile birlikte",
        "willr": "❌ RSI ile redundant, gereksiz",
        "cci": "❌ Çok gürültülü 1m'de",
    },
    "Volatility (Seçici eklenebilir)": {
        "atr_14": "✅ Zaten var (30m, 4h) - 1m'de stop-loss için faydalı olabilir",
        "bb_upper": "❌ 1m'de çok gürültülü, false breakout",
        "bb_middle": "❌ EMA ile redundant",
        "bb_lower": "❌ 1m'de gereksiz",
    },
    "Volume (Gereksiz 1m'de)": {
        "mfi": "❌ 1m volume çok volatile, anlamlı değil",
        "obv": "❌ 1m'de accumulation anlamsız",
    },
    "Others": {
        "vwap_20": "🟡 1m'de fair value için kullanılabilir ama gereksiz",
        "macd_signal": "❌ MACD zaten var, signal redundant",
    },
    "Futures (Sadece 30m'de olmalı)": {
        "long_short_ratio": "❌ 1m'de değişmez, 30m yeterli",
        "open_interest": "❌ 1m'de değişmez, 30m yeterli",
        "funding_rate": "❌ 8 saatte 1 güncellenir, 1m gereksiz",
    }
}

print("ANALİZ SONUÇLARI:")
print("="*80)
print()

for category, indicators in indicators_30m.items():
    print(f"📁 {category}:")
    for ind, explanation in indicators.items():
        symbol = explanation[:2]
        print(f"   {symbol} {ind:20s} {explanation[2:]}")
    print()

# Recommendation
print("="*80)
print("💡 ÖNERİ")
print("="*80)
print()

print("ŞU ANKİ YAPIYI KOR! (OPTIMAL)")
print()
print("Neden?")
print("  1. CORE indicator'ler zaten multi-TF (EMA, RSI, MACD)")
print("  2. 1m'de fazla indicator = NOISE (gürültü)")
print("  3. Futures data 1m'de değişmez (gereksiz)")
print("  4. Volume indicators 1m'de anlamsız (çok volatile)")
print()

print("OPSİYONEL EKLEMELER (İsteğe bağlı):")
print()
print("  🟡 1m'e Stochastic ekle:")
print("     • Overbought/oversold timing için faydalı")
print("     • 1m Stoch >80 ve 30m BUY → Pullback bekle")
print("     • Cost: +2 field × 1440/day = 2880 writes")
print()
print("  🟡 1m'e ATR ekle:")
print("     • Stop-loss calculation için faydalı")
print("     • 1m ATR × 2 = tighter stop")
print("     • Cost: +1 field × 1440/day = 1440 writes")
print()

# Cost analysis
print("="*80)
print("💰 COST ANALİZİ")
print("="*80)
print()

print("ŞU ANKİ:")
print(f"  1m: 4 ind × 1440/day = 5,760 writes/day")
print(f"  30m: 20 ind × 48/day = 960 writes/day")
print(f"  4h: 6 ind × 6/day = 36 writes/day")
print(f"  TOPLAM: 6,756 writes/day")
print()

print("TÜM 20 INDICATOR MULTI-TF OLSAYDI:")
print(f"  1m: 20 ind × 1440/day = 28,800 writes/day")
print(f"  30m: 20 ind × 48/day = 960 writes/day")
print(f"  4h: 20 ind × 6/day = 120 writes/day")
print(f"  TOPLAM: 29,880 writes/day")
print()
print("  → 4.4× DAHA FAZLA! (Storage + cost)")
print()

print("GLM TOKEN COST:")
print(f"  Şu an: ~180 değer (4+8+6 array × 10)")
print(f"  Full:  ~600 değer (20+20+20 array × 10)")
print(f"  → 3.3× DAHA FAZLA TOKEN!")
print()

print("="*80)
print("SONUÇ")
print("="*80)
print()
print("✅ ŞU ANKİ YAPI OPTIMAL!")
print()
print("Sebep:")
print("  • Core indicators zaten multi-TF")
print("  • 1m'de fazla indicator = noise")
print("  • Cost 4.4× artacak")
print("  • GLM token 3.3× artacak")
print("  • Marginal benefit, major cost")
print()
print("İSTERSEN EKLEYEBİLİRİZ:")
print("  🟡 1m'e Stochastic (timing için)")
print("  🟡 1m'e ATR (stop-loss için)")
print("  → Ama şart değil, mevcut yapı yeterli")
print()
