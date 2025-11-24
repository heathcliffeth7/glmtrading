#!/usr/bin/env python3
"""Real-time 1min data monitoring demo."""

import time
from datetime import datetime
from app.utils.influx import query_latest_snapshot, query_historical_snapshots

print("="*80)
print("1 DAKİKALIK (1MIN) TIMEFRAME - CANLI DEMO")
print("="*80)
print()

# Get latest 1m data
latest = query_latest_snapshot("enriched_1m", "BTCUSDT", "1m")

if not latest:
    print("⚠️  1m data yok! Service çalışıyor mu kontrol et:")
    print("   systemctl status trading-enriched-feed-1m.service")
    exit(1)

print("📊 ŞU ANKİ DURUM (Latest 1min Bar):")
print("-" * 80)
print(f"   Timestamp: {latest.get('timestamp', 'N/A')}")
print(f"   Close:     ${latest.get('close', 0):,.2f}")
print(f"   Volume:    {latest.get('volume', 0):.2f} BTC")
print(f"   RSI(14):   {latest.get('rsi_14', 0):.2f}")
print(f"   MACD:      {latest.get('macd', 0):.2f}")
print(f"   EMA20:     ${latest.get('ema_20', 0):,.2f}")
print(f"   EMA50:     ${latest.get('ema_50', 0):,.2f}")
print(f"   ATR(14):   ${latest.get('atr_14', 0):.2f}")
print()

# Analyze current state
rsi = latest.get('rsi_14', 50)
macd = latest.get('macd', 0)
close = latest.get('close', 0)
ema20 = latest.get('ema_20', 0)

print("📈 DURUM ANALİZİ:")
print("-" * 80)

# RSI analysis
if rsi > 70:
    rsi_status = "🔴 OVERBOUGHT (Aşırı alım)"
    rsi_advice = "Pullback bekle, satış fırsatı"
elif rsi < 30:
    rsi_status = "🟢 OVERSOLD (Aşırı satım)"
    rsi_advice = "Toparlanma için iyi entry noktası"
elif 40 <= rsi <= 60:
    rsi_status = "⚪ NEUTRAL (Nötr)"
    rsi_advice = "Net trend yok, bekle"
else:
    rsi_status = f"🟡 {'BULLISH' if rsi > 50 else 'BEARISH'}"
    rsi_advice = f"{'Yukarı' if rsi > 50 else 'Aşağı'} momentum"

print(f"   RSI:  {rsi_status}")
print(f"         → {rsi_advice}")

# MACD analysis
if macd > 50:
    macd_status = "🟢 Güçlü BULLISH"
elif macd > 0:
    macd_status = "🟡 Zayıf BULLISH"
elif macd > -50:
    macd_status = "🟡 Zayıf BEARISH"
else:
    macd_status = "🔴 Güçlü BEARISH"

print(f"   MACD: {macd_status} ({macd:.2f})")

# EMA position
if close > ema20 > latest.get('ema_50', 0):
    ema_status = "🟢 Fiyat EMA20 ve EMA50 üstünde (BULLISH)"
elif close < ema20 < latest.get('ema_50', 0):
    ema_status = "🔴 Fiyat EMA20 ve EMA50 altında (BEARISH)"
else:
    ema_status = "🟡 EMA'ler karışık (NEUTRAL)"

print(f"   EMA:  {ema_status}")
print()

# Historical analysis (last 10 minutes)
snapshots = query_historical_snapshots("enriched_1m", "BTCUSDT", "1m", limit=10)

if snapshots and len(snapshots) >= 2:
    print("📉 SON 10 DAKİKA TRENDİ:")
    print("-" * 80)
    
    closes = [s.get('close', 0) for s in snapshots]
    rsis = [s.get('rsi_14', 50) for s in snapshots]
    macds = [s.get('macd', 0) for s in snapshots]
    
    # Price change
    price_change = ((closes[-1] - closes[0]) / closes[0]) * 100
    price_trend = "📈 YUKARI" if price_change > 0 else "📉 AŞAĞI" if price_change < 0 else "➡️  YATAY"
    
    print(f"   Fiyat:  {closes[0]:.2f} → {closes[-1]:.2f}")
    print(f"           {price_trend} {price_change:+.3f}%")
    
    # RSI momentum
    if len(rsis) >= 5:
        rsi_start = rsis[-5]
        rsi_end = rsis[-1]
        rsi_change = rsi_end - rsi_start
        
        if abs(rsi_change) > 10:
            rsi_momentum = "⚡ GÜÇLÜ" if abs(rsi_change) > 15 else "💨 ORTA"
        else:
            rsi_momentum = "😴 ZAYIF"
        
        print(f"   RSI:    {rsi_start:.1f} → {rsi_end:.1f}")
        print(f"           {rsi_momentum} momentum ({rsi_change:+.1f})")
    
    # MACD trend
    if len(macds) >= 5:
        macd_start = macds[-5]
        macd_end = macds[-1]
        macd_improving = macd_end > macd_start
        
        print(f"   MACD:   {macd_start:.1f} → {macd_end:.1f}")
        print(f"           {'✅ İyileşiyor' if macd_improving else '❌ Kötüleşiyor'}")
    
    print()
    
    # Volatility
    price_range = max(closes) - min(closes)
    volatility_pct = (price_range / closes[0]) * 100
    
    if volatility_pct > 1.0:
        volatility = "🌪️  ÇOK YÜKSEK"
    elif volatility_pct > 0.5:
        volatility = "⚡ YÜKSEK"
    elif volatility_pct > 0.2:
        volatility = "💨 ORTA"
    else:
        volatility = "😴 DÜŞÜK"
    
    print(f"   Volatilite: {volatility} ({volatility_pct:.3f}%)")
    print(f"   Range:      ${min(closes):,.2f} - ${max(closes):,.2f}")
    print()

# Trading advice
print("💡 TRADING TAVSİYESİ (1min Perspektifi):")
print("-" * 80)

# Combine signals
bullish_signals = 0
bearish_signals = 0

if rsi > 50:
    bullish_signals += 1
elif rsi < 50:
    bearish_signals += 1

if macd > 0:
    bullish_signals += 1
elif macd < 0:
    bearish_signals += 1

if close > ema20:
    bullish_signals += 1
elif close < ema20:
    bearish_signals += 1

if snapshots and len(snapshots) >= 2:
    if price_change > 0.1:
        bullish_signals += 1
    elif price_change < -0.1:
        bearish_signals += 1

# Decision
if bullish_signals >= 3:
    decision = "🟢 BULLISH (Entry fırsatı)"
    action = "30m BUY signal gelirse: Hemen GİR (1m destekliyor)"
elif bearish_signals >= 3:
    decision = "🔴 BEARISH (Exit sinyali)"
    action = "30m BUY signal olsa bile: BEKLE veya İPTAL (1m çelişiyor)"
else:
    decision = "🟡 NEUTRAL (Net sinyal yok)"
    action = "30m signal'i bekle, 1m henüz net değil"

print(f"   Durum:  {decision}")
print(f"   Aksiyon: {action}")
print()

# Timing advice
print("⏰ ENTRY TİMİNG TAVSİYESİ:")
print("-" * 80)

if rsi > 70:
    print("   ⚠️  RSI overbought! 5-10 dakika bekle, RSI 60'a düşsün")
elif rsi < 30:
    print("   ✅ RSI oversold! İyi entry noktası, hemen girebilirsin")
elif 45 <= rsi <= 55:
    print("   ✅ RSI dengeli, normal timing kullanabilirsin")
elif rsi > 60:
    print("   ⚠️  RSI yüksek, küçük bir pullback bekle")
else:
    print("   ⚠️  RSI düşük, momentum zayıf, bekle")

print()
print("="*80)
print("ℹ️  Bu 1min analizi 30m ana signal ile birlikte kullanılmalıdır!")
print("ℹ️  Sadece 1m'e bakarak trade yapma, GLM'in final kararını bekle!")
print("="*80)
