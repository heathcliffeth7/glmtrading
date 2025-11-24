# Multi-Timeframe Trading System Açıklaması

## Sistem Mimarisi

### 1. Veri Toplama Katmanı (3 Ayrı Servis)

**enriched-feed.service** (30min - Ana timeframe)
- Poll interval: 1800 saniye (30 dakika)
- Measurement: `enriched_30min`
- Son 24 saat: ~49 kayıt
- Kullanım: Ana trading kararları

**enriched-feed-1m.service** (1min - Intraday)
- Poll interval: 60 saniye (1 dakika)
- Measurement: `enriched_1m`
- Son 24 saat: ~146 kayıt
- Kullanım: Kısa vadeli trend ve entry/exit timing

**enriched-feed-4h.service** (4hour - Long-term)
- Poll interval: 14400 saniye (4 saat)
- Measurement: `enriched_4h`
- Son 24 saat: ~3 kayıt
- Kullanım: Genel trend yönü ve büyük resim

### 2. Agent Veri Toplama (derivatives.py)

Agent `generate_signal()` çağrıldığında:

#### A. Latest Snapshot (Mevcut Durum)
```python
# Her timeframe'den son değerleri al
features_30min = query_latest_snapshot("enriched_30min", "BTCUSDT", "30min")
features_1m = query_latest_snapshot("enriched_1m", "BTCUSDT", "1m")
features_4h = query_latest_snapshot("enriched_4h", "BTCUSDT", "4h")
```

**DerivativesFeatures** dataclass'ına yazılır:
- 30min: 20 ana feature (close, ema, rsi, macd, etc.)
- 1min: 4 intraday feature (intraday_rsi, intraday_macd, etc.)
- 4h: 6 longterm feature (longterm_ema, longterm_rsi, etc.)

#### B. Historical Time-Series (Son 10 Bar)
```python
# Her timeframe'den son 10 snapshot al
intraday_1m = query_historical_snapshots("enriched_1m", limit=10)      # Son 10 dakika
main_30min = query_historical_snapshots("enriched_30min", limit=10)    # Son 5 saat
longterm_4h = query_historical_snapshots("enriched_4h", limit=10)      # Son 40 saat
```

**Signal metadata'ya eklenir:**
```python
{
    "intraday_1m": {
        "close": [107949.44, 107950.0, ...],
        "rsi_14": [46.0, 46.5, ...],
        "macd": [-161.2, -160.0, ...],
        "ema_20": [108207.9, 108210.0, ...]
    },
    "main_30min": {
        "close": [107970.0, 108686.62, 107978.25, ...],
        "rsi_14": [44.56, 54.04, 45.72, ...],
        "macd": [-234.2, -169.5, -173.5, ...],
        ...
    },
    "longterm_4h": {
        "ema_20": [108197.8, 108195.3, 108184.8, ...],
        "rsi_14": [48.5, 48.1, 46.2, ...],
        ...
    }
}
```

### 3. ML Model Prediction

Model **sadece 30min ana features** kullanır:
```python
df = features.to_dataframe()  # 20 features from 30min
prediction = model.predict_proba(df)[0][1]  # BUY probability
```

**Model Features (20):**
- Futures: long_short_ratio, open_interest, funding_rate
- Binance: close, ema_20, ema_50, rsi_14, macd, macd_signal, atr_14, 
  stoch_k, stoch_d, bb_upper, bb_middle, bb_lower, willr, cci, mfi, obv, vwap_20

### 4. GLM Risk Analysis (Multi-Timeframe)

GLM'e **tüm timeframe'ler + historical data** gönderilir:

```python
prompt = f"""
Multi-Timeframe Analysis:
========================

CURRENT STATE (Latest):
- 1m (Intraday): RSI={intraday_rsi}, MACD={intraday_macd}, EMA20={intraday_ema}
- 30m (Main): RSI={rsi}, MACD={macd}, EMA20={ema}, close={close}
- 4h (Long-term): RSI={longterm_rsi}, MACD={longterm_macd}, EMA20={longterm_ema}

HISTORICAL TIME-SERIES:
- Intraday (last 10 min): {intraday_1m}
- Main (last 5 hours): {main_30min}
- Long-term (last 40 hours): {longterm_4h}

Model Prediction: {model_score}
Direction: {direction}

Analyze convergence/divergence across timeframes and validate the signal.
"""
```

**GLM görevi:**
- Timeframe'ler arasında uyum kontrol et (confluence)
- Trend yönü tutarlılığı (1m, 30m, 4h aynı yönde mi?)
- Momentum değişimi (historical arrays'de pattern var mı?)
- Risk seviyesi belirle
- Final karar: APPROVE veya REJECT

### 5. Signal Flow

```
┌─────────────────────────────────────────────────────────┐
│  Data Collection (InfluxDB)                             │
├─────────────────────────────────────────────────────────┤
│  enriched_1m   ─┐                                       │
│  enriched_30m  ─┼─→ Agent.generate_signal()             │
│  enriched_4h   ─┘                                       │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│  DerivativesFeatures (30 fields)                        │
├─────────────────────────────────────────────────────────┤
│  - 20 main features (30min)                             │
│  - 4 intraday features (1min)                           │
│  - 6 longterm features (4h)                             │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│  ML Model (RandomForest)                                │
├─────────────────────────────────────────────────────────┤
│  Input: 20 main features                                │
│  Output: BUY probability (0-1)                          │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│  RiskManager + GLM                                      │
├─────────────────────────────────────────────────────────┤
│  Input: All timeframes + historical + model score       │
│  Analysis: Multi-timeframe confluence check             │
│  Output: APPROVE/REJECT + risk_level                    │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│  Final Signal                                           │
├─────────────────────────────────────────────────────────┤
│  - direction: BUY/SELL                                  │
│  - confidence: 0-1                                      │
│  - risk_level: low/medium/high                          │
│  - reasoning: Multi-TF analysis                         │
│  - metadata: {intraday_1m, main_30min, longterm_4h}     │
└─────────────────────────────────────────────────────────┘
```

## Avantajlar

1. **Trend Confirmation**: Farklı timeframe'lerde aynı sinyali görüyorsak daha güvenilir
2. **Entry Timing**: 1m ile micro-timing, 30m ile genel yön, 4h ile büyük trend
3. **False Signal Reduction**: GLM farklı timeframe'lerde çelişki görürse reddetebilir
4. **Context-Aware**: Historical arrays ile momentum değişimini görebiliyoruz

## Örnek Senaryolar

### Scenario 1: Strong BUY Signal
```
1m:  RSI=65 (yükseliyor), MACD=+50 (pozitif), EMA20 > price
30m: RSI=60 (yükseliyor), MACD=+100, EMA20 > EMA50 (golden cross)
4h:  RSI=55 (yükseliyor), MACD=+200, trend UP
→ GLM: APPROVE (tüm timeframe'ler align, güçlü BUY)
```

### Scenario 2: Divergence - REJECT
```
1m:  RSI=70 (overbought), MACD=+20 (zayıflıyor)
30m: RSI=55 (neutral), MACD=-50 (negatif)
4h:  RSI=40 (oversold), MACD=-300, trend DOWN
→ GLM: REJECT (timeframe'ler çelişiyor, 4h downtrend)
```

### Scenario 3: Range-Bound - Low Confidence
```
1m:  RSI=50, MACD=0, consolidation
30m: RSI=48, MACD=-5, sideways
4h:  RSI=52, MACD=+10, no clear trend
→ GLM: APPROVE but low confidence (sideways market)
```

## Monitoring

```bash
# Servislerin durumu
systemctl status trading-enriched-feed.service        # 30min
systemctl status trading-enriched-feed-1m.service     # 1min
systemctl status trading-enriched-feed-4h.service     # 4h

# Veri kontrolü
cd /root/trading
.venv/bin/python -c "
from app.utils.influx import query_latest_snapshot
print('1m:', query_latest_snapshot('enriched_1m', 'BTCUSDT', '1m'))
print('30m:', query_latest_snapshot('enriched_30min', 'BTCUSDT', '30min'))
print('4h:', query_latest_snapshot('enriched_4h', 'BTCUSDT', '4h'))
"

# Signal test
.venv/bin/python test_multiframe.py
```

## Configuration

Timeframe'leri değiştirmek için:
- Service dosyalarında `--interval` ve `--poll-interval` parametrelerini ayarla
- `derivatives.py`'de `query_historical_snapshots(limit=N)` değerini ayarla
- GLM prompt'ta açıklamaları güncelle

## Önemli Notlar

1. **Data Latency**: 1m en güncel, 4h en yavaş güncellenir
2. **Storage**: 1m çok kayıt üretir, retention policy gerekebilir
3. **Model Training**: Model sadece 30m data ile eğitildi, diğerleri context için
4. **GLM Cost**: Her signal'de GLM çağrısı yapılıyor, maliyet artabilir
