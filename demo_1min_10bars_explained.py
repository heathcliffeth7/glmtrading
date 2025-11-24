#!/usr/bin/env python3
"""1min timeframe'de 'son 10 dakika' kavramını detaylı açıklama."""

from datetime import datetime, timedelta
from app.utils.influx import query_historical_snapshots

print("="*80)
print("1MIN TIMEFRAME: 'SON 10 DAKİKA' DETAYLI AÇIKLAMA")
print("="*80)
print()

# Get last 10 bars from 1m measurement
print("📊 InfluxDB'den son 10 bar çekiliyor...")
print("   Query: query_historical_snapshots('enriched_1m', 'BTCUSDT', '1m', limit=10)")
print()

snapshots = query_historical_snapshots("enriched_1m", "BTCUSDT", "1m", limit=10)

if not snapshots:
    print("⚠️  Veri yok!")
    exit(1)

print(f"✅ {len(snapshots)} bar alındı")
print()

# Explain each bar
print("="*80)
print("HER BİR BAR NE DEMEK?")
print("="*80)
print()
print("1 bar = 1 dakikalık OHLCV verisi + hesaplanmış indicator'ler")
print()

# Show first 3 bars in detail
for i, snap in enumerate(snapshots[:3], 1):
    ts = snap.get('timestamp')
    close = snap.get('close', 0)
    volume = snap.get('volume', 0)
    rsi = snap.get('rsi_14', 0)
    macd = snap.get('macd', 0)
    ema20 = snap.get('ema_20', 0)
    
    # Parse timestamp
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    else:
        dt = ts
    
    time_str = dt.strftime('%H:%M:%S') if dt else 'N/A'
    
    print(f"📍 BAR #{i}:")
    print(f"   Timestamp: {ts}")
    print(f"   Saat:      {time_str}")
    print(f"   ─────────────────────────────────────────")
    print(f"   Close:     ${close:,.2f}")
    print(f"   Volume:    {volume:.2f} BTC")
    print(f"   RSI(14):   {rsi:.2f}")
    print(f"   MACD:      {macd:.2f}")
    print(f"   EMA(20):   ${ema20:,.2f}")
    print()

print("... (toplam 10 bar)")
print()

# Timeline visualization
print("="*80)
print("ZAMAN ÇİZGİSİ GÖRSELLEŞTİRME")
print("="*80)
print()

print("Şimdi", end="")
for i in range(10):
    print(f" ← {i+1}dk", end="")
print()

print("  │", end="")
for i in range(10):
    print("    │", end="")
print()

print(" Bar10", end="")
for i in range(9, 0, -1):
    print(f"  Bar{i:2d}", end="")
print()
print()

# Show actual timeline
print("GERÇEK ZAMAN DAMGALARI:")
print()

for i, snap in enumerate(reversed(snapshots), 1):
    ts = snap.get('timestamp')
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    else:
        dt = ts
    
    time_str = dt.strftime('%H:%M:%S') if dt else 'N/A'
    close = snap.get('close', 0)
    
    # Calculate how many minutes ago
    if dt:
        now = datetime.now(dt.tzinfo)
        diff = now - dt
        mins_ago = int(diff.total_seconds() / 60)
    else:
        mins_ago = 0
    
    arrow = "←" * i
    print(f"   Bar {i:2d}: {time_str} ({mins_ago:2d} dakika önce)  {arrow}  ${close:,.2f}")

print()

# Time series arrays
print("="*80)
print("TIME-SERIES ARRAYS (GLM'e giden format)")
print("="*80)
print()

closes = [s.get('close', 0) for s in snapshots]
rsis = [s.get('rsi_14', 50) for s in snapshots]
macds = [s.get('macd', 0) for s in snapshots]
ema20s = [s.get('ema_20', 0) for s in snapshots]

print("Python dict formatında GLM'e gönderiliyor:")
print()
print("{")
print('    "intraday_1m": {')
print(f'        "close": {closes},')
print(f'        "rsi_14": {rsis},')
print(f'        "macd": {macds},')
print(f'        "ema_20": {ema20s}')
print('    }')
print("}")
print()

# Trend analysis
print("="*80)
print("TREND ANALİZİ (Son 10 dakika)")
print("="*80)
print()

if len(closes) >= 10:
    # Price trend
    first_close = closes[0]
    last_close = closes[-1]
    price_change = last_close - first_close
    price_change_pct = (price_change / first_close) * 100
    
    print(f"💰 FİYAT TRENDİ:")
    print(f"   10 dakika önce: ${first_close:,.2f}")
    print(f"   Şimdi:          ${last_close:,.2f}")
    print(f"   Değişim:        ${price_change:+,.2f} ({price_change_pct:+.3f}%)")
    
    if abs(price_change_pct) < 0.05:
        trend = "➡️  YATAY (Consolidation)"
    elif price_change_pct > 0:
        trend = "📈 YUKARI (Bullish)"
    else:
        trend = "📉 AŞAĞI (Bearish)"
    
    print(f"   Trend:          {trend}")
    print()
    
    # Show price movement bar by bar
    print("   Bar-by-bar değişim:")
    for i in range(len(closes) - 1):
        change = closes[i+1] - closes[i]
        change_pct = (change / closes[i]) * 100
        symbol = "↗" if change > 0 else "↘" if change < 0 else "→"
        print(f"      Bar {i+1}→{i+2}: {symbol} {change:+.2f} ({change_pct:+.4f}%)")
    print()

if len(rsis) >= 10:
    # RSI momentum
    first_rsi = rsis[0]
    last_rsi = rsis[-1]
    rsi_change = last_rsi - first_rsi
    
    print(f"📊 RSI MOMENTUM:")
    print(f"   10 dakika önce: {first_rsi:.2f}")
    print(f"   Şimdi:          {last_rsi:.2f}")
    print(f"   Değişim:        {rsi_change:+.2f}")
    
    if abs(rsi_change) < 3:
        momentum = "😴 ZAYIF (Sideways)"
    elif abs(rsi_change) < 10:
        momentum = "💨 ORTA"
    else:
        momentum = "⚡ GÜÇLÜ"
    
    direction = "yukarı" if rsi_change > 0 else "aşağı" if rsi_change < 0 else "yatay"
    print(f"   Momentum:       {momentum} ({direction})")
    print()

if len(macds) >= 10:
    # MACD trend
    first_macd = macds[0]
    last_macd = macds[-1]
    macd_change = last_macd - first_macd
    
    print(f"📈 MACD TRENDİ:")
    print(f"   10 dakika önce: {first_macd:.2f}")
    print(f"   Şimdi:          {last_macd:.2f}")
    print(f"   Değişim:        {macd_change:+.2f}")
    
    if macd_change > 10:
        status = "✅ Güçlü iyileşme (Bullish)"
    elif macd_change > 0:
        status = "🟢 Hafif iyileşme"
    elif macd_change > -10:
        status = "🔴 Hafif kötüleşme"
    else:
        status = "❌ Güçlü kötüleşme (Bearish)"
    
    print(f"   Durum:          {status}")
    print()

# Pattern detection
print("="*80)
print("PATTERN TESPİTİ (Son 10 dakika)")
print("="*80)
print()

# Check for consistent trend
if len(closes) >= 5:
    # Last 5 bars trend
    last_5_closes = closes[-5:]
    increasing = all(last_5_closes[i] <= last_5_closes[i+1] for i in range(4))
    decreasing = all(last_5_closes[i] >= last_5_closes[i+1] for i in range(4))
    
    if increasing:
        print("✅ CONSISTENT UPTREND:")
        print("   Son 5 bar sürekli yükseliyor")
        print("   → Güçlü bullish momentum")
    elif decreasing:
        print("❌ CONSISTENT DOWNTREND:")
        print("   Son 5 bar sürekli düşüyor")
        print("   → Güçlü bearish momentum")
    else:
        print("🔄 CHOPPY MOVEMENT:")
        print("   Yukarı-aşağı hareket (volatile)")
        print("   → Net trend yok, bekle")
    print()

# Volatility
if len(closes) >= 10:
    price_range = max(closes) - min(closes)
    avg_price = sum(closes) / len(closes)
    volatility_pct = (price_range / avg_price) * 100
    
    print(f"🌊 VOLATİLİTE:")
    print(f"   Min:  ${min(closes):,.2f}")
    print(f"   Max:  ${max(closes):,.2f}")
    print(f"   Range: ${price_range:,.2f} ({volatility_pct:.3f}%)")
    
    if volatility_pct > 1.0:
        vol_status = "🌪️  ÇOK YÜKSEK (Riskli)"
    elif volatility_pct > 0.5:
        vol_status = "⚡ YÜKSEK (Dikkatli ol)"
    elif volatility_pct > 0.2:
        vol_status = "💨 ORTA (Normal)"
    else:
        vol_status = "😴 DÜŞÜK (Sakin)"
    
    print(f"   Durum: {vol_status}")
    print()

# GLM interpretation
print("="*80)
print("GLM NASIL KULLANIR?")
print("="*80)
print()

print("GLM bu 10 bar'lık time-series'i alır ve şunları analiz eder:")
print()
print("1. 📊 MOMENTUM DIRECTION:")
print("   - RSI artıyor mu, azalıyor mu?")
print("   - MACD iyileşiyor mu, kötüleşiyor mu?")
print("   - Fiyat hangi yönde hareket ediyor?")
print()

print("2. 🔄 CONSISTENCY:")
print("   - Trend tutarlı mı, yoksa volatile mı?")
print("   - Son 5 bar aynı yönde mi?")
print("   - Ani spike var mı (false breakout)?")
print()

print("3. ⚡ STRENGTH:")
print("   - Momentum ne kadar güçlü?")
print("   - RSI değişimi >10 ise güçlü")
print("   - Fiyat değişimi >0.5% ise güçlü")
print()

print("4. ⏰ TIMING:")
print("   - 30m BUY signal varsa:")
print("     • 1m RSI >70 → Pullback bekle")
print("     • 1m RSI <30 → Hemen gir (oversold)")
print("     • 1m trend ↑ → Perfect timing")
print("     • 1m trend ↓ → Signal cancel (divergence)")
print()

print("5. 🎯 FINAL DECISION:")
print("   - 1m + 30m + 4h hepsi align mi?")
print("   - Entry risk/reward optimal mi?")
print("   - Stop-loss nereye koymalı? (1m ATR kullan)")
print()

print("="*80)
print("ÖZET")
print("="*80)
print()
print("✅ 'Son 10 dakika' = Son 10 adet 1-dakikalık bar")
print("✅ Her bar 1 dakika içindeki OHLCV + indicator'ler içerir")
print("✅ 10 bar = 10 dakikalık micro-trend'i gösterir")
print("✅ Array formatında GLM'e gönderilir (time-series analysis)")
print("✅ GLM momentum, trend, timing analizi yapar")
print("✅ 30m ana signal ile birlikte entry/exit kararı verir")
print()
print("💡 Önemli: 1m TEK BAŞINA karar vermez!")
print("   Sadece 30m signal için TIMING optimize eder.")
print()
