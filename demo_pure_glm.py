#!/usr/bin/env python3
"""
PURE GLM PORTFOLIO MANAGEMENT SYSTEM
DerivativesAgent TAMAMEN KALDIRILDI - GLM her şeyi karar veriyor
"""

from app.agents.short_term import PureDataCollector

print('='*70)
print('🚀 PURE GLM PORTFOLIO MANAGEMENT SYSTEM')
print('='*70)
print()

print('📋 Sistem Özellikleri:')
print('   ✅ DerivativesAgent kaldırıldı')
print('   ✅ Tüm ağırlıklar kaldırıldı')
print('   ✅ Bias scorelar kaldırıldı')
print('   ✅ Ön işleme YOK')
print('   ✅ GLM tam özgürlük ile trade yapıyor')
print()

# PureDataCollector başlat
collector = PureDataCollector(symbol="BTCUSDT")
signal = collector.generate_signal()

print('📊 Signal Details:')
print(f'   Direction: {signal.direction}')
print(f'   Confidence: {signal.confidence}')
print(f'   Reasoning: {signal.reasoning}')
print()

# Raw data kontrolü
if 'raw_market_data' in signal.metadata:
    raw_data = signal.metadata['raw_market_data']
    current = raw_data.get('current_snapshots', {})
    historical = raw_data.get('historical_arrays', {})
    
    print('📈 Toplanan Ham Veriler:')
    print(f'   Timeframe sayısı: {len(current)}')
    print(f'   Timeframes: {list(current.keys())}')
    print()
    
    print('⏰ Her Timeframe İçin:')
    for tf in sorted(current.keys()):
        snap = current.get(tf, {})
        hist = historical.get(tf, {})
        
        if snap:
            print(f'   {tf}:')
            print(f'      Current: {len(snap)} indicator')
            if hist:
                print(f'      Historical: {len(hist)} indicator arrays')
            if 'close' in snap:
                print(f'      Close Price: ${snap["close"]:.2f}')
    print()

print('='*70)
print('✅ SİSTEM HAZIR!')
print('='*70)
print()
print('🎯 GLM Özellikleri:')
print('   • Tüm timeframeleri analiz eder')
print('   • Kendi ağırlıklarını belirler')
print('   • Kendi biasını hesaplar')
print('   • Kendi confidenceını ayarlar')
print('   • Portföy yönetimini yapar')
print('   • Tam özgür trading yapar')
print()
