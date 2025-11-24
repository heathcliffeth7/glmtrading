#!/usr/bin/env python3
"""
Pure GLM Portfolio Management System Test
Derivatives Agent REMOVED - GLM makes ALL decisions with raw data
"""

from app.agents.short_term import PureDataCollectorsAgent

print('='*60)
print('MULTIFRAME SYSTEM NASIL ÇALIŞIYOR')
print('='*60)
print()

agent = PureDataCollectorsAgent()
agent = DerivativesAgent()
signal = agent.generate_signal()

print(f'📊 SIGNAL OUTPUT:')
print(f'  Direction: {signal.direction}')
print(f'  Confidence: {signal.confidence:.2f}')
print(f'  Reasoning: {signal.reasoning[:120]}...')
print()

# Historical data metadata içinde
if 'historical_data' not in signal.metadata:
    print('⚠️  Historical data yok!')
    exit(0)

historical = signal.metadata['historical_data']
print(f'📈 MULTI-TIMEFRAME DATA:')
print()

# 1min intraday
if 'intraday_1m' in historical:
    data = historical['intraday_1m']
    print(f'1️⃣  INTRADAY (1min - son 10 dakika):')
    print(f'   Toplanan: {list(data.keys())}')
    if 'close' in data and data['close']:
        closes = data['close']
        print(f'   Close: [{closes[0]:.1f} -> {closes[-1]:.1f}] ({len(closes)} bar)')
        change = ((closes[-1] - closes[0]) / closes[0]) * 100
        trend = 'UP' if change > 0 else 'DOWN'
        print(f'   Trend: {trend} {change:+.2f}%')
    if 'rsi_14' in data and data['rsi_14']:
        print(f'   RSI: {data["rsi_14"][-1]:.1f}')
    print()

# 30min main  
if 'main_30min' in historical:
    data = historical['main_30min']
    print(f'2️⃣  MAIN (30min - son 5 saat):')
    print(f'   Toplanan: {list(data.keys())}')
    if 'close' in data and data['close']:
        closes = data['close']
        print(f'   Close: [{closes[0]:.1f} -> {closes[-1]:.1f}] ({len(closes)} bar)')
        change = ((closes[-1] - closes[0]) / closes[0]) * 100
        trend = 'UP' if change > 0 else 'DOWN'
        print(f'   Trend: {trend} {change:+.2f}%')
    if 'rsi_14' in data and data['rsi_14']:
        rsis = data['rsi_14']
        print(f'   RSI: [{rsis[0]:.1f} -> {rsis[-1]:.1f}]')
    if 'macd' in data and data['macd']:
        macds = data['macd']
        print(f'   MACD: [{macds[0]:.1f} -> {macds[-1]:.1f}]')
    print()

# 4h longterm
if 'longterm_4h' in historical:
    data = historical['longterm_4h']
    print(f'3️⃣  LONG-TERM (4hour - son 40 saat):')
    print(f'   Toplanan: {list(data.keys())}')
    if 'ema_20' in data and data['ema_20']:
        emas = data['ema_20']
        print(f'   EMA20: [{emas[0]:.1f} -> {emas[-1]:.1f}] ({len(emas)} bar)')
        change = ((emas[-1] - emas[0]) / emas[0]) * 100
        trend = 'UP' if change > 0 else 'DOWN'
        print(f'   Trend: {trend} {change:+.2f}%')
    if 'rsi_14' in data and data['rsi_14']:
        rsis = data['rsi_14']
        print(f'   RSI: [{rsis[0]:.1f} -> {rsis[-1]:.1f}]')
    print()

print('='*60)
print('ÖZET')
print('='*60)
print()
print('✅ 3 servis sürekli veri topluyor:')
print('   - enriched-feed-1m.service  (her 1 dakika)')
print('   - enriched-feed.service     (her 30 dakika)')
print('   - enriched-feed-4h.service  (her 4 saat)')
print()
print('✅ Agent her signal\'de:')
print('   - Latest snapshot al (3 timeframe)')
print('   - Historical data al (son 10 bar)')
print('   - Model prediction yap (30min data ile)')
print('   - GLM\'e gönder (tüm timeframe + historical)')
print()
print('✅ GLM multi-timeframe analiz yapıyor:')
print('   - Timeframe confluence check')
print('   - Trend consistency')
print('   - Momentum patterns')
print('   - Risk assessment')
print()
