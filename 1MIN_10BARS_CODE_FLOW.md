# 1min "Son 10 Dakika" - Kod Akışı Detaylı Açıklama

## 📊 Veri Toplama Süreci

### Adım 1: Service Her 60 Saniyede Çalışır

**Service:** `trading-enriched-feed-1m.service`

```bash
# Her 60 saniyede bir:
ExecStart=python -m app.data_feeds.enriched_feed \
    --symbol BTCUSDT \
    --interval 1m \
    --poll-interval 60
```

**Çalışma döngüsü:**
```
18:53:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
18:54:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
18:55:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
18:56:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
18:57:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
18:58:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
18:59:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
19:00:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
19:01:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle
19:02:00 → Çalış → InfluxDB'ye yaz → 60 saniye bekle ← ŞİMDİ
```

**Her çalışmada yapılanlar:**

```python
# app/data_feeds/enriched_feed.py - main loop

while True:
    # 1. Binance'den son 100 adet 1m kline al
    klines = await fetch_binance_klines(
        symbol="BTCUSDT",
        interval="1m",
        limit=100  # Son 100 dakika
    )
    
    # 2. Indicator'leri hesapla (son 100 bar üzerinden)
    indicators = calculate_indicators_from_klines(klines)
    # Bu fonksiyon içinde:
    # - EMA(20) hesaplanır (son 20 bar gerekir)
    # - RSI(14) hesaplanır (son 14 bar gerekir)
    # - MACD(12,26,9) hesaplanır (son 26 bar gerekir)
    # - Stochastic, BB, ATR, etc.
    
    # 3. Futures metrics ekle
    futures = await fetch_futures_metrics()
    indicators.update(futures)
    
    # 4. InfluxDB'ye yaz
    write_measurement(
        measurement="enriched_1m",
        tags={"symbol": "BTCUSDT", "interval": "1m"},
        fields=indicators,  # 22 field
        timestamp=datetime.utcnow()
    )
    
    # 5. 60 saniye bekle
    await asyncio.sleep(60)
```

### Adım 2: InfluxDB'de Veri Birikir

**InfluxDB Storage:**

```
Measurement: enriched_1m
│
├─ 18:53:29 → {close: 108016.56, rsi: 47.19, macd: -125.00, ...}
├─ 18:54:30 → {close: 108016.56, rsi: 47.19, macd: -125.00, ...}
├─ 18:55:32 → {close: 108016.56, rsi: 47.19, macd: -125.00, ...}
├─ 18:56:33 → {close: 108016.56, rsi: 47.19, macd: -125.00, ...}
├─ 18:57:34 → {close: 108016.56, rsi: 47.19, macd: -125.00, ...}
├─ 18:58:36 → {close: 108016.56, rsi: 47.19, macd: -125.00, ...}
├─ 18:59:38 → {close: 108016.56, rsi: 47.19, macd: -125.00, ...}
├─ 19:00:39 → {close: 108044.94, rsi: 47.54, macd: -121.37, ...}
├─ 19:01:41 → {close: 108044.94, rsi: 47.54, macd: -121.37, ...}
└─ 19:02:42 → {close: 108044.94, rsi: 47.54, macd: -121.37, ...} ← EN SON
```

**Veri yapısı (her timestamp için):**
```json
{
  "timestamp": "2025-10-22T19:02:42+00:00",
  "symbol": "BTCUSDT",
  "interval": "1m",
  "fields": {
    "close": 108044.94,
    "volume": 12.86,
    "ema_20": 108158.53,
    "ema_50": 108670.42,
    "rsi_14": 47.54,
    "macd": -121.37,
    "macd_signal": -143.21,
    "stoch_k": 78.45,
    "stoch_d": 67.23,
    "atr_14": 624.64,
    "bb_upper": 109856.32,
    "bb_middle": 109245.67,
    "bb_lower": 108635.02,
    "willr": -21.55,
    "cci": 52.34,
    "mfi": 45.67,
    "obv": -1234.56,
    "vwap_20": 109123.45,
    "long_short_ratio": 2.35,
    "open_interest": 8339566740,
    "funding_rate": 0.000024
  }
}
```

## 🔍 Agent Tarafından Kullanım

### Adım 3: Agent Signal Üretirken Query Yapar

**Kod:** `app/agents/derivatives.py`

```python
class DerivativesAgent(Agent):
    
    def generate_signal(self) -> AgentSignal:
        """Signal üret - her çağrıldığında çalışır."""
        
        # 1. Latest snapshots al (3 timeframe)
        features = self._collect_features()
        
        # 2. Historical data al (10 bar × 3 timeframe)
        historical_data = self._collect_historical_data()
        
        # 3. Model prediction
        model_score = self._predict_with_model(features)
        
        # 4. Signal oluştur
        return AgentSignal(
            direction="BUY" or "SELL",
            confidence=0.64,
            reasoning="...",
            metadata={"historical_data": historical_data}
        )
```

### Adım 4: Historical Data Collection (SON 10 DAKİKA!)

```python
def _collect_historical_data(self) -> Dict[str, dict]:
    """
    Son 10 snapshot al (her timeframe için).
    
    Bu fonksiyon InfluxDB'den son 10 kaydı çeker:
    - 1m için son 10 kayıt = son 10 dakika
    - 30m için son 10 kayıt = son 5 saat
    - 4h için son 10 kayıt = son 40 saat
    """
    
    # ========================================
    # 1MIN - SON 10 DAKİKA
    # ========================================
    snapshots_1m = query_historical_snapshots(
        measurement="enriched_1m",
        symbol="BTCUSDT",
        interval="1m",
        limit=10  # SON 10 KAYIT = SON 10 DAKİKA
    )
    
    # snapshots_1m şu şekilde döner:
    # [
    #   {timestamp: "18:53:29", close: 108016.56, rsi: 47.19, ...},  ← 10dk önce
    #   {timestamp: "18:54:30", close: 108016.56, rsi: 47.19, ...},  ← 9dk önce
    #   {timestamp: "18:55:32", close: 108016.56, rsi: 47.19, ...},  ← 8dk önce
    #   {timestamp: "18:56:33", close: 108016.56, rsi: 47.19, ...},  ← 7dk önce
    #   {timestamp: "18:57:34", close: 108016.56, rsi: 47.19, ...},  ← 6dk önce
    #   {timestamp: "18:58:36", close: 108016.56, rsi: 47.19, ...},  ← 5dk önce
    #   {timestamp: "18:59:38", close: 108016.56, rsi: 47.19, ...},  ← 4dk önce
    #   {timestamp: "19:00:39", close: 108044.94, rsi: 47.54, ...},  ← 3dk önce
    #   {timestamp: "19:01:41", close: 108044.94, rsi: 47.54, ...},  ← 2dk önce
    #   {timestamp: "19:02:42", close: 108044.94, rsi: 47.54, ...},  ← 1dk önce (ŞİMDİ)
    # ]
    
    # Array'lere çevir (GLM için)
    intraday = {}
    if snapshots_1m:
        intraday = {
            "close": [s.get("close", 0) for s in snapshots_1m],
            "rsi_14": [s.get("rsi_14", 50) for s in snapshots_1m],
            "macd": [s.get("macd", 0) for s in snapshots_1m],
            "ema_20": [s.get("ema_20", 0) for s in snapshots_1m],
        }
    
    # intraday şu şekilde olur:
    # {
    #   "close": [108016.56, 108016.56, ..., 108044.94],    # 10 değer
    #   "rsi_14": [47.19, 47.19, ..., 47.54],               # 10 değer
    #   "macd": [-125.0, -125.0, ..., -121.37],            # 10 değer
    #   "ema_20": [108170.49, 108170.49, ..., 108158.53],  # 10 değer
    # }
    
    # Aynı şekilde 30m ve 4h için de yap
    main_30min = {...}     # 30m için son 10 kayıt
    longterm_4h = {...}    # 4h için son 10 kayıt
    
    return {
        "intraday_1m": intraday,
        "main_30min": main_30min,
        "longterm_4h": longterm_4h,
    }
```

### Adım 5: InfluxDB Query (Nasıl Çekiliyor?)

```python
# app/utils/influx.py

def query_historical_snapshots(
    measurement: str,
    symbol: str, 
    interval: str,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Son N snapshot'ı çek (en eski → en yeni sırayla).
    """
    
    # Flux query (InfluxDB'nin query dili)
    query = f"""
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -1h)                                   # Son 1 saat içinde ara
      |> filter(fn: (r) => r._measurement == "{measurement}")  # enriched_1m
      |> filter(fn: (r) => r.symbol == "{symbol}")           # BTCUSDT
      |> filter(fn: (r) => r.interval == "{interval}")       # 1m
      |> sort(columns: ["_time"], desc: false)               # Zamana göre sırala (eski→yeni)
      |> tail(n: {limit})                                    # Son 10 kayıt al
    """
    
    # Query çalıştır
    tables = query_api.query(query)
    
    # Results'ları işle
    # InfluxDB her field için ayrı satır döndürür:
    # timestamp=18:53:29, field=close, value=108016.56
    # timestamp=18:53:29, field=rsi_14, value=47.19
    # timestamp=18:53:29, field=macd, value=-125.0
    # ...
    
    # Bunları timestamp'e göre grupla
    grouped = defaultdict(dict)
    for table in tables:
        for record in table.records:
            timestamp = record.get_time()
            field = record.get_field()
            value = record.get_value()
            
            grouped[timestamp][field] = value
            grouped[timestamp]["timestamp"] = timestamp
    
    # En eski → en yeni sırala
    snapshots = [grouped[ts] for ts in sorted(grouped.keys())]
    
    return snapshots
    # [
    #   {timestamp: "18:53:29", close: 108016.56, rsi: 47.19, ...},
    #   {timestamp: "18:54:30", close: 108016.56, rsi: 47.19, ...},
    #   ...
    #   {timestamp: "19:02:42", close: 108044.94, rsi: 47.54, ...},
    # ]
```

## 📤 GLM'e Gönderme

### Adım 6: Risk Manager GLM Prompt Oluşturur

```python
# app/risk_manager/manager.py

def analyze_with_glm(signal: AgentSignal) -> str:
    """GLM ile signal analizi."""
    
    historical = signal.metadata.get("historical_data", {})
    intraday_1m = historical.get("intraday_1m", {})
    main_30min = historical.get("main_30min", {})
    longterm_4h = historical.get("longterm_4h", {})
    
    # Prompt oluştur
    prompt = f"""
BTCUSDT Multi-Timeframe Trading Signal Analysis

=== INTRADAY (1min - Last 10 Minutes) ===

Time-Series Data (oldest → newest):
CLOSE:  {intraday_1m.get('close', [])}
RSI_14: {intraday_1m.get('rsi_14', [])}
MACD:   {intraday_1m.get('macd', [])}
EMA_20: {intraday_1m.get('ema_20', [])}

Analysis Required:
1. Calculate 10-minute price change: (last - first) / first × 100
2. Check RSI momentum: Is RSI increasing or decreasing?
3. Check MACD trend: Is MACD improving or deteriorating?
4. Detect patterns: Consistent trend? Volatile? Spike?
5. Assess volatility: Calculate range (max - min) / avg × 100

=== MAIN (30min - Last 5 Hours) ===
[30min time-series...]

=== LONG-TERM (4hour - Last 40 Hours) ===
[4h time-series...]

=== MODEL PREDICTION ===
Direction: BUY
Score: 0.18
Confidence: 0.64

=== QUESTIONS FOR GLM ===

1. MOMENTUM ALIGNMENT:
   - Does 1m momentum support 30m signal?
   - Are all timeframes aligned (1m ↑, 30m ↑, 4h ↑)?
   - Or is there divergence (1m ↓, 30m ↑)?

2. ENTRY TIMING:
   - Is this a good entry point (1m perspective)?
   - Should we wait for 1m pullback?
   - Is 1m RSI overbought (>70) or oversold (<30)?

3. PATTERN RECOGNITION:
   - Any false breakout detected in 1m?
   - Consistent trend or choppy movement?
   - Strong momentum or weak?

4. RISK ASSESSMENT:
   - What's the optimal stop-loss? (use 1m ATR)
   - Entry risk/reward ratio?
   - Volatility level (high/medium/low)?

5. FINAL DECISION:
   Based on multi-timeframe analysis, should we:
   - APPROVE the signal (good timing)
   - APPROVE with LOW CONFIDENCE (wait recommended)
   - REJECT (divergence detected)

Please provide detailed analysis.
"""
    
    # GLM'e gönder
    response = glm_client.generate(prompt)
    
    return response
```

### Adım 7: GLM Analiz Yapar

**GLM'in gördüğü:**

```
CLOSE:  [108016.56, 108016.56, 108016.56, 108016.56, 108016.56, 
         108016.56, 108016.56, 108044.94, 108044.94, 108044.94]
         └─────────── 7 bar flat ──────────┘ └─ 3 bar yükseldi ─┘

RSI_14: [47.19, 47.19, 47.19, 47.19, 47.19, 
         47.19, 47.19, 47.54, 47.54, 47.54]
         └─────── Hafif yükseldi (47.19 → 47.54) ──────┘

MACD:   [-125.0, -125.0, -125.0, -125.0, -125.0,
         -125.0, -125.0, -121.37, -121.37, -121.37]
         └─────── İyileşiyor (-125 → -121) ──────┘
```

**GLM'in analizi:**

```
Analysis:
1. Price: Flat for 7 minutes, then +$28 jump (0.026%)
   → Very low volatility, consolidation
   
2. RSI: Increased from 47.19 to 47.54 (+0.35)
   → Neutral zone, very weak momentum
   
3. MACD: Improved from -125 to -121 (+3.62)
   → Still negative but slightly improving
   
4. Pattern: Consolidation → breakout attempt
   → But momentum too weak to confirm
   
5. Timing Assessment:
   ✅ RSI not overbought (safe to enter)
   ⚠️  Momentum very weak (wait for confirmation)
   ⚠️  Low volatility (may continue sideways)

Recommendation:
APPROVE with LOW CONFIDENCE
Reason: Multi-timeframe aligned but 1m momentum too weak.
Suggest waiting 5-10 minutes for stronger confirmation.
```

## 📊 Görsel Özet

```
┌──────────────────────────────────────────────────────────────┐
│  ZAMAN ÇİZGİSİ (Son 10 dakika)                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  18:53  18:54  18:55  18:56  18:57  18:58  18:59  19:00  19:01  19:02
│    │      │      │      │      │      │      │      │      │      │
│    ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼
│  108016 108016 108016 108016 108016 108016 108016 108044 108044 108044
│    │      │      │      │      │      │      │      │      │      │
│  [─────────────── FLAT ───────────────────][──── JUMP ────────]
│                                              ↑
│                                      Tek hareket burada!
│                                                              │
│  RSI:  47.19 ───────────────────────────────→ 47.54         │
│  MACD: -125.0 ──────────────────────────────→ -121.37       │
│                                                              │
│  Analiz: Consolidation → Weak breakout attempt              │
└──────────────────────────────────────────────────────────────┘
```

## 🎯 Pratik Örnek: Farklı Senaryolar

### Senaryo 1: Güçlü Momentum (APPROVE)

```
CLOSE:  [108000, 108050, 108100, 108150, 108200, 108250, 
         108300, 108350, 108400, 108450]
         └────────── Sürekli artış (+0.42%) ───────────┘

RSI:    [45, 48, 51, 54, 57, 60, 63, 66, 69, 72]
         └────────── Güçlü momentum (+27) ─────────┘

GLM: ✅ APPROVE - Strong bullish momentum in last 10 minutes
```

### Senaryo 2: Overbought (WAIT)

```
CLOSE:  [107000, 107200, 107500, 107800, 108100, 108500,
         108900, 109300, 109700, 110100]
         └────────── Çok hızlı yükseldi (+2.9%!) ───────┘

RSI:    [50, 55, 60, 65, 70, 75, 78, 80, 82, 85]
         └────────── OVERBOUGHT! (+35) ─────────┘

GLM: ⚠️  APPROVE with LOW CONFIDENCE
     Reason: 1m RSI overbought (85). Wait for pullback to 70.
```

### Senaryo 3: Divergence (REJECT)

```
30m Signal: BUY (trend UP)

1m Data:
CLOSE:  [108500, 108400, 108300, 108200, 108100, 108000,
         107900, 107800, 107700, 107600]
         └────────── Düşüş trendi (-0.83%) ───────┘

RSI:    [65, 62, 58, 54, 50, 46, 42, 38, 34, 30]
         └────────── Momentum kaybı (-35) ────────┘

GLM: ❌ REJECT
     Reason: Strong bearish divergence on 1m (losing momentum)
     while 30m shows BUY. Conflicting signals.
```

## 📚 Özet

**"Son 10 dakika" şu demek:**

1. InfluxDB'den **son 10 kayıt** çek
2. Her kayıt = **1 dakikalık bar** (OHLCV + indicators)
3. 10 kayıt = **10 dakikalık time-series**
4. Array formatına çevir: `[val1, val2, ..., val10]`
5. GLM'e gönder: **Momentum analizi için**
6. GLM karar verir: **Entry timing optimal mi?**

**Kritik nokta:** 1m tek başına karar vermez, sadece 30m signal'in **timing'ini optimize** eder!
