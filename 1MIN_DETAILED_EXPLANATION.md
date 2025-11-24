# 1 Dakikalık (1min) Timeframe Detaylı Açıklama

## 🎯 Amaç ve Kullanım

**1min timeframe NE İÇİN kullanılıyor?**
- **Entry/Exit Timing**: 30m signal geldiğinde tam hangi anda girelim?
- **Micro Trend Detection**: Son 10 dakikadaki momentum değişimi
- **False Breakout Detection**: Ani spike'lar vs gerçek hareketler
- **Stop-Loss Optimization**: Kısa vadeli volatilite ölçümü
- **Scalping Signals**: Hızlı giriş-çıkış fırsatları

**Örnek Senaryo:**
```
30m: BUY signal, RSI=55, yükseliş trendi
1m: RSI=65 (overbought), fiyat yeni zirve yaptı
→ Karar: 5 dakika bekle, pullback'te gir (daha iyi entry)

30m: BUY signal, RSI=55, yükseliş trendi  
1m: RSI=45, fiyat düşüyor, MACD negatif
→ Karar: Signal cancel (1m divergence)
```

## 🔧 Teknik Detaylar

### 1. Service Configuration

**Dosya:** `/root/trading/infra/trading-enriched-feed-1m.service`

```ini
[Service]
ExecStart=/root/trading/.venv/bin/python -m app.data_feeds.enriched_feed \
    --symbol BTCUSDT \
    --interval 1m \        # Binance 1-minute klines
    --poll-interval 60     # Her 60 saniyede bir çalış

Restart=always
RestartSec=10
```

**Çalışma Mantığı:**
```
┌─────────────────────────────────────────────────────┐
│  Her 60 saniyede bir:                               │
├─────────────────────────────────────────────────────┤
│  1. Binance API'den son 100 adet 1m kline al        │
│  2. 100 bar üzerinden indicator hesapla:            │
│     - EMA(20), EMA(50)                              │
│     - RSI(14)                                       │
│     - MACD(12,26,9)                                 │
│     - Stochastic, Bollinger, ATR, etc.             │
│  3. Futures metrics ekle (L/S, OI, FR)             │
│  4. InfluxDB'ye yaz: enriched_1m measurement        │
│  5. 60 saniye bekle, tekrar et                     │
└─────────────────────────────────────────────────────┘
```

### 2. InfluxDB Storage

**Measurement:** `enriched_1m`

**Tags:**
- `symbol`: BTCUSDT
- `interval`: 1m

**Fields (22 adet):**
```python
{
    # Price & Volume
    "close": 107949.44,
    "volume": 39.69,
    
    # Moving Averages
    "ema_20": 108207.94,
    "ema_50": 108738.97,
    
    # Momentum
    "rsi_14": 46.0,
    "macd": -161.19,
    "macd_signal": -145.68,
    "stoch_k": 77.44,
    "stoch_d": 56.87,
    
    # Volatility
    "atr_14": 459.92,
    "bb_upper": 109791.78,
    "bb_middle": 109262.46,
    "bb_lower": 108733.14,
    
    # Others
    "willr": -22.55,
    "cci": 48.52,
    "mfi": 43.90,
    "obv": -1769.64,
    "vwap_20": 109213.35,
    
    # Futures
    "long_short_ratio": 2.35,
    "open_interest": 8870672000.0,
    "funding_rate": 0.000024
}
```

**Retention:**
- 1 dakikada 1 kayıt
- Saatte 60 kayıt
- Günde 1440 kayıt
- **Haftalık: ~10K kayıt** (yaklaşık 1MB veri)

### 3. Agent Tarafından Kullanım

#### A. Latest Snapshot (Mevcut Durum)
```python
# derivatives.py - _collect_features()
features_1m = query_latest_snapshot("enriched_1m", "BTCUSDT", "1m")

# DerivativesFeatures dataclass'ına yazılır:
DerivativesFeatures(
    # ... 30m main features ...
    
    # 1m intraday features (4 adet)
    intraday_rsi_14=features_1m.get("rsi_14", 50.0),
    intraday_macd=features_1m.get("macd", 0.0),
    intraday_ema_20=features_1m.get("ema_20", 0.0),
    intraday_close=features_1m.get("close", 0.0),
)
```

**NOT:** Model bu 4 feature'ı **kullanmıyor** (sadece 30m features kullanılır).
Bu features sadece **GLM için context** olarak gönderilir.

#### B. Historical Time-Series (Son 10 Dakika)
```python
# derivatives.py - _collect_historical_data()
snapshots_1m = query_historical_snapshots("enriched_1m", "BTCUSDT", "1m", limit=10)

intraday = {
    "close": [107949.44, 107950.0, 107948.0, ...],    # 10 values
    "rsi_14": [46.0, 46.5, 45.8, ...],                # 10 values
    "macd": [-161.19, -160.0, -162.0, ...],           # 10 values
    "ema_20": [108207.94, 108210.0, 108205.0, ...],   # 10 values
}

# Signal metadata'ya eklenir
signal.metadata = {
    "historical_data": {
        "intraday_1m": intraday,
        "main_30min": {...},
        "longterm_4h": {...}
    }
}
```

### 4. GLM Prompt'ta Nasıl Görünüyor?

Risk manager'da GLM'e şu formatta gönderilir:

```python
prompt = f"""
BTCUSDT Multi-Timeframe Analysis

=== INTRADAY (1min - Last 10 Minutes) ===
Current Close: ${intraday_close:.2f}
Current RSI: {intraday_rsi:.1f}
Current MACD: {intraday_macd:.1f}
Current EMA20: {intraday_ema:.2f}

Time-Series (last 10 bars):
CLOSE:  {intraday['close']}
RSI_14: {intraday['rsi_14']}
MACD:   {intraday['macd']}
EMA_20: {intraday['ema_20']}

Micro-trend: Calculate if momentum is building or fading in last 10 minutes

=== MAIN (30min - Last 5 Hours) ===
[30min data...]

=== ANALYSIS REQUIRED ===
1. Does 1m momentum support 30m signal?
2. Is there divergence between 1m and 30m?
3. Is this a good entry timing (1m perspective)?
4. Should we wait for 1m pullback before entry?
"""
```

**GLM görevi:**
- 1m'de ani spike var mı? (false breakout?)
- 1m RSI overbought/oversold mu? (pullback beklemeli miyiz?)
- 1m trend 30m trend'i destekliyor mu?
- Entry timing optimal mi yoksa 5-10 dakika beklemeli miyiz?

## 📊 Pratik Kullanım Örnekleri

### Örnek 1: Perfect Entry Timing
```
30m Signal: BUY, RSI=52, MACD pozitife dönüyor
1m Durum:
  - Son 5 dakika: Fiyat +0.3% yükseldi
  - RSI: 45 → 58 (momentum artıyor)
  - MACD: -50 → +10 (pozitife dönüş)
  - EMA20'nin üstünde

GLM Kararı: ✅ APPROVE
Reasoning: "1m momentum supports 30m signal. Strong entry timing."
```

### Örnek 2: Wait for Pullback
```
30m Signal: BUY, RSI=55, trend UP
1m Durum:
  - Son 5 dakika: Fiyat +2% SPIKE!
  - RSI: 50 → 75 (overbought!)
  - MACD: +100 → +200 (aşırı momentum)
  - BB üst bandına yapıştı

GLM Kararı: ⚠️ APPROVE with LOW CONFIDENCE
Reasoning: "1m shows overbought condition. Consider waiting for 1m pullback to RSI 60 before entry."
```

### Örnek 3: Divergence - Reject
```
30m Signal: BUY, RSI=58, MACD pozitif
1m Durum:
  - Son 10 dakika: Fiyat -0.8% düştü
  - RSI: 65 → 40 (momentum kaybı)
  - MACD: +50 → -30 (negatife döndü)
  - EMA20'nin altına düştü

GLM Kararı: ❌ REJECT
Reasoning: "1m shows strong bearish divergence. Momentum lost in last 10 minutes. Signal cancelled."
```

### Örnek 4: Consolidation - Wait
```
30m Signal: BUY, RSI=50, belirsiz
1m Durum:
  - Son 10 dakika: Fiyat ±0.1% (flat)
  - RSI: 48-52 arası oynuyor
  - MACD: -10 ila +10 (flat)
  - Volume düşük

GLM Kararı: ⏸️ WAIT
Reasoning: "Market consolidating on 1m. No clear momentum. Wait for breakout."
```

## 🔍 Debugging ve Monitoring

### Service Status
```bash
# Service çalışıyor mu?
systemctl status trading-enriched-feed-1m.service

# Son 50 log
journalctl -u trading-enriched-feed-1m.service -n 50

# Canlı log takip
journalctl -u trading-enriched-feed-1m.service -f
```

### Data Check
```bash
cd /root/trading

# Son 1m snapshot
.venv/bin/python -c "
from app.utils.influx import query_latest_snapshot
data = query_latest_snapshot('enriched_1m', 'BTCUSDT', '1m')
print('Latest 1m data:', data)
"

# Son 15 dakika
.venv/bin/python -c "
from app.utils.influx import query_historical_snapshots
snapshots = query_historical_snapshots('enriched_1m', 'BTCUSDT', '1m', limit=15)
for s in snapshots[-5:]:
    print(f\"{s['timestamp']}: Close={s['close']:.2f}, RSI={s['rsi_14']:.1f}\")
"

# Kayıt sayısı (son 24 saat)
.venv/bin/python -c "
from app.utils.influx import _ensure_client
import app.utils.influx
from app.config.settings import get_settings
s = get_settings()
_ensure_client()
q = f'from(bucket: \"{s.influx.bucket}\") |> range(start: -24h) |> filter(fn: (r) => r._measurement == \"enriched_1m\") |> filter(fn: (r) => r._field == \"close\") |> count()'
tables = app.utils.influx._query_api.query(q)
print(f'1m records (24h): {tables[0].records[0].get_value()}')
"
```

### Live Signal Test
```bash
cd /root/trading
.venv/bin/python -c "
from app.agents.derivatives import DerivativesAgent

agent = DerivativesAgent()
signal = agent.generate_signal()

historical = signal.metadata.get('historical_data', {})
intraday = historical.get('intraday_1m', {})

print('=== 1MIN INTRADAY DATA ===')
print(f'Indicators: {list(intraday.keys())}')

if 'close' in intraday:
    closes = intraday['close']
    print(f'Close prices: {closes}')
    if len(closes) >= 2:
        change = ((closes[-1] - closes[0]) / closes[0]) * 100
        print(f'10-minute change: {change:+.3f}%')

if 'rsi_14' in intraday:
    print(f'RSI values: {intraday[\"rsi_14\"]}')
"
```

## ⚠️ Önemli Notlar

### 1. Indicator Hesaplama
- **100 bar window** gerekiyor (EMA, MACD için)
- İlk 100 bar'da indicator hesaplanamaz
- Bu yüzden sistem başlatıldığında 100 dakika beklemek gerekir

### 2. Data Volume
```
1 dakika = 1 kayıt (22 field × 8 bytes = ~176 bytes)
1 saat = 60 kayıt (~10 KB)
1 gün = 1440 kayıt (~250 KB)
1 hafta = 10,080 kayıt (~1.7 MB)
1 ay = 43,200 kayıt (~7.5 MB)
```

**Retention Policy önerisi:**
- 1 haftalık veri tut (10K kayıt)
- 1 haftadan eski kayıtları sil
- Ya da 30m'ye downsample et

### 3. Performance Impact
- **InfluxDB writes**: Saniyede 1 yazma (düşük)
- **Agent queries**: Her signal'de 2 query (latest + historical)
- **GLM token cost**: 1m arrays ekstra ~100 token

### 4. Binance API Limits
- 1m klines: Her çağrıda 100 bar çekilir
- Rate limit: 1200 req/min (her 60 saniyede 1 req = OK)
- Weight: 1 (düşük)

### 5. False Signals
1m çok hassas, gürültülü olabilir:
- Ani spike'lar
- Low volume manipülasyon
- Binance kline delay (1-2 saniye)

**Çözüm:**
- GLM'de threshold kullan (örn: %0.5'ten küçük değişim ignore)
- 1m tek başına karar verme, sadece context
- 30m signal'i confirm/reject için kullan

## 🎓 Best Practices

1. **1m'i Ana Karar İçin Kullanma**
   - ❌ Yanlış: "1m RSI 70, SELL yap"
   - ✅ Doğru: "30m SELL var, ama 1m RSI 30 (oversold), 5 dk bekle"

2. **Micro-Trend Patterns Ara**
   ```python
   # Son 10 dakikada momentum artıyor mu?
   rsi_values = [46, 48, 50, 52, 55, 58, 60, 62, 65, 68]
   # Düzenli artış = güçlü momentum
   
   rsi_values = [65, 45, 70, 40, 68, 42, 66, 44, 69, 43]
   # Çok volatile = belirsiz, WAIT
   ```

3. **Entry Timing Optimization**
   ```python
   if signal_30m == "BUY":
       if rsi_1m > 70:
           wait_for_pullback()  # RSI < 60 olunca gir
       elif rsi_1m < 30:
           immediate_entry()    # Oversold, hemen gir
       else:
           normal_entry()       # Normal timing
   ```

4. **Stop-Loss Placement**
   ```python
   # 1m ATR ile dinamik stop-loss
   atr_1m = 450.0  # Son 14 bar ATR
   entry_price = 108000.0
   
   stop_loss = entry_price - (2 * atr_1m)  # 2 ATR aşağı
   # Stop: 107100 (tighter stop for short-term)
   ```

## 📈 Gelecek İyileştirmeler

1. **Volume Profile**: 1m volume pattern detection
2. **Order Book**: Real-time bid/ask spread analysis
3. **Tick Data**: Sub-minute data (Binance WebSocket)
4. **ML Model**: 1m-specific model (entry timing predictor)
5. **Alert System**: 1m spike/dump alerts via Telegram

---

**Özet:** 1min timeframe **mikro timing** için kullanılır. 30m signal'ini **confirm/reject** ve **entry timing optimize** etmek için GLM'e context sağlar. Tek başına trading kararı vermez, destek role oynar.
