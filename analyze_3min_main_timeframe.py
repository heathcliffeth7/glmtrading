#!/usr/bin/env python3
"""30min main timeframe'i 3min'e çevirmek - analiz."""

print("="*80)
print("30 DAKİKA MAIN TIMEFRAME → 3 DAKİKA'YA ÇEVİRMEK")
print("="*80)
print()

print("ŞU ANKİ YAPI:")
print("-" * 80)
print("  Intraday: 1min  (son 10 bar = 10 dakika)")
print("  MAIN:     30min (son 10 bar = 5 saat) ← ML model burada")
print("  Long-term: 4h   (son 10 bar = 40 saat)")
print()

print("ÖNERİLEN:")
print("-" * 80)
print("  Intraday: 1min  (son 10 bar = 10 dakika)")
print("  MAIN:     3min  (son 10 bar = 30 dakika) ← ML model burada")
print("  Long-term: 4h   (son 10 bar = 40 saat)")
print()

print("="*80)
print("MAJOR FARKLAR")
print("="*80)
print()

# Trading frequency
print("1. TRADING FREQUENCY:")
print("-" * 80)
print("30min: Signal her 30 dakikada 1 üretilebilir")
print("       → Günde 48 potansiyel trade")
print("       → Ama gerçekte ~5-10 trade/gün (selective)")
print()
print("3min:  Signal her 3 dakikada 1 üretilebilir")
print("       → Günde 480 potansiyel trade!")
print("       → Çok daha aktif trading")
print()

# ML Model
print("2. ML MODEL:")
print("-" * 80)
print("Şu an: 1245 bar 30min data = 26 gün")
print("       Model bu data ile trained")
print()
print("3min:  1245 bar 3min data = 2.6 gün!")
print("       → Model RETRAIN gerekir")
print("       → 30min trained model kullanılamaz")
print()
print("Mevcut model:")
print("  - 30min için optimize edilmiş")
print("  - Feature distribution 30min scale'de")
print("  - 3min'de çalışmaz (farklı volatility)")
print()

# Data volume
print("3. DATA VOLUME:")
print("-" * 80)
print("30min: 48 bar/gün × 20 ind = 960 writes/day")
print("3min:  480 bar/gün × 20 ind = 9,600 writes/day")
print("       → 10× DAHA FAZLA! (Major storage)")
print()

# Historical data
print("4. HISTORICAL DATA:")
print("-" * 80)
print("30min için 28 gün data import ettik:")
print("  • 1344 bar")
print("  • 26 gün coverage")
print("  • Model bu data ile trained")
print()
print("3min için aynı süre:")
print("  • 13,440 bar gerekir (10×)")
print("  • Binance'den çekmek ~2 saat sürer")
print("  • InfluxDB import ~30 dakika")
print("  • Model retrain ~10 dakika")
print()

# Signal quality
print("5. SIGNAL QUALITY:")
print("-" * 80)
print("30min:")
print("  ✅ Daha az false signal")
print("  ✅ Daha güvenilir trend")
print("  ✅ Lower frequency = daha selective")
print("  ✅ Swing trading'e uygun")
print()
print("3min:")
print("  ⚠️  Daha fazla noise")
print("  ⚠️  Daha fazla false breakout")
print("  ✅ Daha hızlı reaction")
print("  ✅ Scalping'e uygun")
print()

print("="*80)
print("AVANTAJLAR (3min Main)")
print("="*80)
print()

advantages = {
    "⚡ Daha Hızlı": "3 dakikada yeni signal (vs 30 dakika)",
    "📈 Daha Aktif": "Günde 50-100 trade potansiyeli (vs 5-10)",
    "🎯 Scalping": "Kısa vadeli trade'ler için ideal",
    "💹 More Opportunities": "Her 3 dakika yeni fırsat",
    "🔄 Quick Adaptation": "Piyasa değişimlerine hızlı tepki",
}

for title, desc in advantages.items():
    print(f"{title}:")
    print(f"  {desc}")
    print()

print("="*80)
print("DEZAVANTAJLAR (3min Main)")
print("="*80)
print()

disadvantages = {
    "🔄 Model Retrain": "30min model çalışmaz, 3min için retrain gerekir (2-3 saat iş)",
    "💰 10× Cost": "9,600 writes/day (vs 960) → Major storage cost",
    "📉 More Noise": "3min'de daha fazla false signal, daha az güvenilir",
    "⚡ GLM Calls": "Her 3 dakika GLM çağrısı yapabilir (vs 30 dakika) → 10× token cost",
    "🎪 Overtrading Risk": "Çok fazla trade = fees + slippage + emotion",
    "📊 Less Data": "1245 bar = 2.6 gün (vs 26 gün) → ML model için yetersiz",
}

for title, desc in disadvantages.items():
    print(f"{title}:")
    print(f"  {desc}")
    print()

print("="*80)
print("TRADİNG STİLİ")
print("="*80)
print()

print("30min Main:")
print("  • Swing Trading")
print("  • 2-5 trade/gün")
print("  • Hold time: Saatler - günler")
print("  • Lower frequency, higher quality")
print("  • Daha az stress")
print()

print("3min Main:")
print("  • Scalping / Day Trading")
print("  • 20-50 trade/gün")
print("  • Hold time: Dakikalar - saatler")
print("  • High frequency, more noise")
print("  • Sürekli monitoring gerekir")
print()

print("="*80)
print("ML MODEL PROBLEM")
print("="*80)
print()

print("Mevcut model:")
print("  • 30min scale'de trained")
print("  • Feature distribution 30min'e göre")
print("  • RSI, MACD, ATR değerleri 30min için optimize")
print()

print("3min'e geçince:")
print("  ❌ Model kullanılamaz!")
print("  • Feature scale farklı (3min RSI ≠ 30min RSI)")
print("  • Volatility farklı (3min ATR << 30min ATR)")
print("  • Pattern'ler farklı")
print()

print("Çözüm:")
print("  1. 3min için 13,440 bar (26 gün) import et")
print("  2. Model'i 3min data ile retrain et")
print("  3. Hiperparameters tune et")
print("  4. Backtest yap")
print("  → Toplam 3-4 saat iş")
print()

print("="*80)
print("COST COMPARISON")
print("="*80)
print()

print("                      30min       3min        Fark")
print("─────────────────────────────────────────────────────")
print("Bars per day          48          480         10×")
print("InfluxDB writes       960         9,600       10×")
print("Storage (1 month)     ~1.5MB      ~15MB       10×")
print("GLM calls potential   48/day      480/day     10×")
print("GLM token cost        ~$5/day     ~$50/day    10×")
print("Model retrain         Done        Need 3h     -")
print()

print("="*80)
print("💡 ÖNERİM")
print("="*80)
print()

print("SORU: Ne tür trading yapmak istiyorsun?")
print()

print("📊 SWING TRADING (2-5 trade/gün, saatler hold):")
print("   → ✅ 30min MAIN timeframe KOR")
print("   → Daha güvenilir, daha az stress")
print("   → Mevcut model çalışıyor")
print("   → Proven effective")
print()

print("⚡ SCALPING/DAY TRADING (20-50 trade/gün, dakikalar hold):")
print("   → ✅ 3min MAIN timeframe'e GEÇ")
print("   → Daha hızlı signals")
print("   → Ama: Model retrain + 10× cost")
print()

print("🎯 HYBRİD (İki sistemin en iyisi):")
print("   → ✅ MAIN 30min KOR (swing trading)")
print("   → ✅ 1min'i 3min YAP (intraday timing)")
print("   → Best of both worlds!")
print()

print("="*80)
print("BENİM ÖNERİM: HYBRİD!")
print("="*80)
print()

print("Yapı:")
print("  • Intraday: 3min  (30 dakika window, timing)")
print("  • Main:     30min (5 saat window, main decision)")
print("  • Long-term: 4h   (40 saat window, trend)")
print()

print("Neden?")
print("  ✅ 30min main = proven, güvenilir, mevcut model çalışıyor")
print("  ✅ 3min intraday = daha anlamlı timing (vs 1min)")
print("  ✅ Cost optimal (sadece intraday 3× azalır)")
print("  ✅ No model retrain needed!")
print("  ✅ Clean timeframe distribution")
print()

print("Değişiklik:")
print("  1. trading-enriched-feed-1m.service:")
print("     --interval 1m → 3m")
print("     --poll-interval 60 → 180")
print()
print("  2. enriched_1m → enriched_3m")
print()
print("  3. Agent: intraday_1m → intraday_3m")
print()
print("  4. Restart services")
print()

print("Süre: 5 dakika")
print("Risk: Low")
print("Model: No change needed! ✅")
print()
