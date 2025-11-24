# 🚀 AI Trading Sistemi - Komple Dokümentasyon

## 📋 İçindekiler
1. [Genel Mimari](#genel-mimari)
2. [Veri Kaynakları](#veri-kaynakları)
3. [Karar Alma Süreci](#karar-alma-süreci)
4. [Risk Yönetimi](#risk-yönetimi)
5. [İşlem Akışı](#işlem-akışı)
6. [Önemli Hesaplamalar](#önemli-hesaplamalar)
7. [Bugün Yapılan İyileştirmeler](#bugün-yapılan-iyileştirmeler)

---

## 🏗️ Genel Mimari

```
┌─────────────────────────────────────────────────────────────────┐
│                     AI Trading Sistemi                           │
│                   (Paper Trading - Test Modu)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────┐
        │   Orchestrator (Her 5 Dakika)       │
        │   - Döngü yönetimi                  │
        │   - Bileşen koordinasyonu           │
        └─────────────────────────────────────┘
                              │
        ┌─────────────┬───────┴────────┬──────────────┐
        ▼             ▼                ▼              ▼
    ┌─────┐     ┌─────────┐     ┌──────────┐   ┌─────────┐
    │ Data│     │  Agent  │     │   Risk   │   │Executor │
    │Feed │     │ (Sinyal)│     │ Manager  │   │(İşlem)  │
    └─────┘     └─────────┘     └──────────┘   └─────────┘
        │             │                │              │
        ▼             ▼                ▼              ▼
    InfluxDB    SignalComponents  GLM-4 API     PostgreSQL
                                                 (Ledger)
```

---

## 📡 Veri Kaynakları

### 1. **TwelveData API** (Teknik İndikatörler)
**Kullanım:** Cryptocurrency piyasa verileri ve indikatörler

**Çekilen Veriler:**
```python
- Time Series (OHLCV): Close, Open, High, Low, Volume
- RSI (Relative Strength Index): Aşırı alım/satım göstergesi
- MACD (Moving Average Convergence Divergence): Trend momentum
- Bollinger Bands: Volatilite bantları
- EMA (Exponential Moving Average): Trend çizgisi
```

**API Limiti:** 
- Free tier: 800 request/gün per key
- 12 API key: ~9,600 request/gün
- Rate limit: 8 request/dakika

**Veri Akışı:**
```
TwelveData API → enriched_feed.py → InfluxDB (enriched_5min) → Agent
```

### 2. **Binance Futures API** (Piyasa Sentiment)
**Kullanım:** Futures market sentiment verileri

**Çekilen Veriler:**
```python
- Long/Short Ratio: Trader pozisyon dağılımı
- Open Interest: Açık kontrat sayısı
- Funding Rate: Pozisyon maliyeti
- Real-time Price: Spot fiyat (işlem için)
```

**Veri Akışı:**
```
Binance API → enriched_feed.py → InfluxDB → Agent
             → Executor (price için)
```

### 3. **InfluxDB** (Zaman Serisi Database)
**Kullanım:** Tüm market verileri ve hesaplanmış feature'lar

**Measurements:**
```
- enriched_5min: Tüm kaynaklardan birleştirilmiş veri
  ├─ close, volume (fiyat verileri)
  ├─ rsi_14, ema_20, ema_50 (teknik indikatörler)
  ├─ macd, macd_signal (momentum)
  ├─ bb_upper, bb_middle, bb_lower (bollinger)
  ├─ long_short_ratio, open_interest (sentiment)
  └─ funding_rate (futures)
```

### 4. **PostgreSQL** (İşlem Kayıtları)
**Kullanım:** Tüm trade'ler, portföy durumu, PnL

**Tablolar:**
```sql
- trades: Her işlemin detayı
- portfolio: Açık pozisyonlar ve ortalama fiyat
- daily_pnl: Günlük realized/unrealized PnL
```

---

## 🧠 Karar Alma Süreci

### Adım 1: Veri Toplama (Enriched Feed)

**enriched_feed.py** her 5 dakikada bir çalışır:

```python
async def aggregate_enriched_data():
    # 1. InfluxDB'den base features kontrol
    features = query_latest_snapshot("features_5min", "BTCUSDT", "5min")
    
    # 2. Yoksa TwelveData'dan time_series çek
    if not features or features['close'] == 0:
        bars = await client.fetch_time_series("BTC/USD", "5min", 50)
        close = bars[0].close
        # EMA, RSI hesapla
    
    # 3. TwelveData indicators (RSI, MACD, Bollinger)
    essential = ['rsi', 'macd', 'bbands']
    results = await asyncio.gather(*[
        client.fetch_indicator("BTC/USD", ind, "5min")
        for ind in essential
    ])
    
    # 4. Binance Futures sentiment
    futures = await fetch_metrics("BTCUSDT")
    # long_short_ratio, open_interest, funding_rate
    
    # 5. Tümünü birleştir
    enriched = {**base, **indicators, **futures}
    
    # 6. InfluxDB'ye yaz
    write_measurement("enriched_5min", enriched)
```

**Sonuç:** InfluxDB'de güncel piyasa verisi

---

### Adım 2: Sinyal Üretimi (Multi-Signal Agent)

**multi_signal.py** piyasa verilerini analiz edip sinyal üretir:

```python
def generate_signal():
    # 1. Enriched data'yı oku
    data = query_latest_snapshot("enriched_5min", "BTCUSDT", "5min")
    
    # 2. Bileşenleri analiz et
    trend = _analyze_trend(data)       # EMA 20 vs EMA 50
    momentum = _analyze_momentum(data) # RSI, MACD
    sentiment = _analyze_sentiment(data) # L/S ratio, funding
    volatility = _analyze_volatility(data) # ATR, BB width
    
    # 3. Adaptive weight ile kombinasyon
    score = (trend * 0.45) + (momentum * 0.25) + (sentiment * 0.15)
    
    # 4. Direction ve confidence belirle
    if score > 0.3:
        direction = "BUY"
        confidence = min(score, 1.0)
    elif score < -0.3:
        direction = "SELL"
        confidence = min(abs(score), 1.0)
    else:
        direction = "HOLD"
        confidence = 0.0
    
    return AgentSignal(
        direction=direction,
        confidence=confidence,
        reasoning="Trend: yukarı, RSI: 35..."
    )
```

**Örnek Sinyal:**
```
Direction: BUY
Confidence: 0.65
Reasoning: "EMA 20 > EMA 50 (uptrend), RSI 35 (oversold), L/S ratio 1.3 (balanced)"
```

---

### Adım 3: Risk Değerlendirme (GLM-4 LLM)

**manager.py** agent sinyalini GLM-4'e gönderir:

```python
def evaluate(signals):
    # 1. Prompt hazırla
    prompt = f"""
    Ajan Sinyalleri:
    - {signal.timestamp} | {signal.direction} | güven: {signal.confidence}
    - {signal.reasoning}
    
    Çıktı formatı JSON:
    {{"karar": "BUY/SELL/HOLD", "miktar": 0-1, "kaldıraç": 5-20, "gerekçe": "..."}}
    """
    
    # 2. GLM-4 API çağrısı
    response = glm_client.request(prompt)
    
    # 3. Yanıtı parse et
    decision = json.loads(response)
    
    return RiskDecision(
        action=decision['karar'],        # BUY/SELL/HOLD
        amount=decision['miktar'],       # 0.0 - 1.0 (equity yüzdesi)
        leverage=decision['kaldıraç'],   # 5 - 20
        reasoning=decision['gerekçe']
    )
```

**GLM-4'ün Değerlendirmesi:**
```
Sinyal: BUY, Confidence: 0.65
→ GLM analiz eder:
  - RSI 35: Oversold (alım fırsatı)
  - EMA uptrend: Pozitif
  - Confidence 0.65: Orta seviye
  - L/S ratio 1.3: Nötr

→ Karar:
  Karar: BUY
  Miktar: 0.3 (equity'nin %30'u)
  Kaldıraç: 5x (düşük risk)
  Gerekçe: "Teknik olarak alım sinyali ama confidence orta, pozisyon küçük tutuldu"
```

**GLM Başarısız Olursa:**
```
→ HOLD kararı ver
→ Telegram bildirimi gönder (GLM hatası)
→ İşlem yapma (güvenli mod)
```

---

### Adım 4: İşlem Gerçekleştirme (Executor)

**executor.py** GLM kararını uygular:

```python
def execute(decision):
    # 1. Mevcut fiyatı al (Binance API)
    price = get_binance_price("BTCUSDT")  # $110,000
    
    # 2. Portfolio durumunu kontrol et
    portfolio = get_portfolio("BTCUSDT")
    current_position = portfolio.position  # 0.5 BTC
    
    # 3. Serbest sermaye hesapla
    total_equity = 10000 + realized_pnl + unrealized_pnl  # $12,000
    current_exposure = abs(current_position * price)      # $55,000
    used_margin = current_exposure / leverage             # $11,000 (5x)
    free_equity = total_equity - used_margin              # $1,000
    
    # 4. İşlem miktarını hesapla
    base_amount = (free_equity * decision.amount) / price
    leveraged_amount = base_amount * decision.leverage
    
    # 5. Fee hesapla
    notional = leveraged_amount * price
    fee = notional * 0.0005  # Binance taker: 0.05%
    
    # 6. PnL hesapla
    if closing_position:
        pnl = (price - avg_price) * amount - fee
    else:
        pnl = -fee  # Sadece fee
    
    # 7. Database'e kaydet
    record_trade(
        side=decision.action,
        amount=leveraged_amount,
        price=price,
        pnl=pnl
    )
    
    # 8. Portfolio güncelle
    update_portfolio(leveraged_amount, price)
    
    # 9. Telegram bildirimi
    notify_telegram(trade_details)
```

**Örnek İşlem:**
```
Karar: BUY 30% equity, 5x leverage
Free Equity: $1,000
Price: $110,000

Hesaplama:
→ Base amount: ($1,000 * 0.3) / $110,000 = 0.0027 BTC
→ Leveraged: 0.0027 * 5 = 0.0135 BTC
→ Notional: 0.0135 * $110,000 = $1,485
→ Fee: $1,485 * 0.0005 = $0.74

Sonuç:
✅ 0.0135 BTC LONG açıldı
💸 Fee: $0.74
📊 Yeni pozisyon: +0.5135 BTC
```

---

## 🛡️ Risk Yönetimi

### 1. Guardrails (Koruma Mekanizmaları)

```python
def _check_guardrails(portfolio, leveraged_amount, equity):
    # Max pozisyon kontrolü
    new_position = abs(portfolio.position + leveraged_amount)
    if new_position > MAX_POSITION:  # 1.0 BTC
        return False  # İşlem engellendi
    
    # Serbest sermaye kontrolü
    if free_equity <= 0:
        return False  # Kullanılabilir sermaye yok
    
    return True
```

### 2. GLM Fail Senaryosu

```python
if GLM fails:
    → Return HOLD decision
    → Send Telegram alert
    → Don't execute trade
    → Log error details
```

### 3. Fiyat Kontrolü

```python
# Primary: Binance real-time
price = fetch_binance_price()

# Fallback: InfluxDB
if price <= 0:
    price = query_influxdb_price()

# Final check
if price <= 0:
    SKIP trade
```

---

## 📊 Önemli Hesaplamalar

### 1. Serbest Sermaye

```python
Total Equity = Starting Cash + Realized PnL + Unrealized PnL
             = $10,000 + $1,000 + $500 = $11,500

Current Position = 0.5 BTC @ $110,000
Current Exposure = 0.5 * $110,000 = $55,000
Used Margin = $55,000 / 5 (leverage) = $11,000

Free Equity = $11,500 - $11,000 = $500

→ Yeni işlem sadece $500 ile yapılabilir
```

### 2. Kaldıraçlı Pozisyon

```python
Decision: BUY 30% equity, 5x leverage
Free Equity: $500

Base Amount = ($500 * 0.30) / $110,000 = 0.00136 BTC
Leveraged Amount = 0.00136 * 5 = 0.0068 BTC
Notional Value = 0.0068 * $110,000 = $748

→ $748 notional pozisyon açılır
→ Sadece $150 margin kullanılır ($748 / 5)
```

### 3. Fee Hesabı

```python
Entry:
Notional = $748
Fee = $748 * 0.0005 = $0.37

Exit (fiyat $115,000 olsa):
Notional = 0.0068 * $115,000 = $782
Fee = $782 * 0.0005 = $0.39

Total Fee = $0.37 + $0.39 = $0.76

PnL Before Fee = (115000 - 110000) * 0.0068 = $34
PnL After Fee = $34 - $0.76 = $33.24
```

### 4. PnL Hesabı

**LONG Pozisyon:**
```python
Entry: $110,000 @ 0.5 BTC
Exit: $115,000 @ 0.5 BTC

PnL = (Exit Price - Entry Price) * Amount
    = ($115,000 - $110,000) * 0.5
    = $2,500

PnL After Fee = $2,500 - (entry_fee + exit_fee)
              = $2,500 - ($27.50 + $28.75)
              = $2,443.75
```

**SHORT Pozisyon:**
```python
Entry: $110,000 @ -0.5 BTC (short)
Exit: $105,000 @ -0.5 BTC (close)

PnL = (Entry Price - Exit Price) * Amount
    = ($110,000 - $105,000) * 0.5
    = $2,500

PnL After Fee = $2,500 - fees = $2,443.75
```

---

## 🎯 Tam Döngü Örneği

### 5 Dakikalık Döngü Başlangıcı

**1. Veri Toplama (13:35:00)**
```
enriched_feed.py çalışır:
→ TwelveData: Close=$110,950, RSI=58, MACD=+32
→ Binance: L/S=1.74, Funding=0.00001
→ InfluxDB'ye yaz
```

**2. Agent Sinyal (13:35:05)**
```
multi_signal.py analiz:
→ Trend: +0.15 (hafif yukarı)
→ Momentum: +0.25 (RSI neutral-positive)
→ Sentiment: -0.10 (L/S yüksek, short squeeze riski)
→ Score: 0.30

Sinyal:
Direction: BUY
Confidence: 0.30
Reasoning: "Hafif uptrend, RSI nötr, L/S riski var"
```

**3. GLM Değerlendirme (13:35:10)**
```
GLM-4 analiz:
→ Confidence düşük (0.30)
→ L/S ratio riski var
→ Trend pozitif ama zayıf

Karar:
Action: HOLD
Amount: 0.0
Leverage: 5.0
Reasoning: "Confidence düşük, L/S riski yüksek, belirsizlik var"
```

**4. İşlem (13:35:15)**
```
Executor:
→ Action: HOLD
→ İşlem yapılmadı
→ Status: SKIP
```

**5. Telegram Bildirimi (13:35:20)**
```
📊 5 Dakikalık Döngü Tamamlandı

🎯 GLM Kararı
Karar: HOLD
Miktar: 0.0% equity
Kaldıraç: 5.0x
Durum: SKIP

💰 Portföy Durumu
Sermaye: $10,000.00
Pozisyon: +0.0000 BTC
BTC Fiyat: $110,950.00
PnL: $0.00 (+0.00%)

📝 İşlem Geçmişi (Toplam: 0)
Henüz işlem yok

💬 Gerekçe
Confidence düşük, L/S riski yüksek, belirsizlik var
```

---

## 🔧 Bugün Yapılan İyileştirmeler

### 1. ❌ → ✅ Fiyat Sorunu Çözüldü
**Problem:** TwelveData eski $50k fiyat gösteriyordu
**Çözüm:** 
- Binance API'den real-time fiyat (primary)
- InfluxDB fallback
- Yanlış veriyi temizledik

### 2. ❌ → ✅ Fallback Mekanizması Kaldırıldı
**Önceki:** GLM fail → Confidence-based fallback karar
**Yeni:** GLM fail → HOLD + Telegram alert

### 3. ❌ → ✅ Enriched Feed Düzeltildi
**Problem:** InfluxDB'den features gelmiyor, hep 0
**Çözüm:** TwelveData time_series'den çek + hesapla

### 4. ❌ → ✅ Serbest Sermaye Hesabı
**Problem:** Mevcut pozisyonun margin'i düşülmüyordu
**Çözüm:**
```python
free_equity = total_equity - used_margin
# Sadece serbest sermaye ile işlem
```

### 5. ❌ → ✅ Fee Hesabı Eklendi
**Problem:** Binance fee'leri yoktu
**Çözüm:**
```python
fee = notional * 0.0005  # Taker: 0.05%
pnl = pnl_before_fee - fee
```

### 6. ❌ → ✅ Telegram Spam Azaltıldı
**Önceki:** 3-4 mesaj per döngü
**Yeni:** 1 kapsamlı mesaj
- GLM kararı
- Portföy durumu
- İşlem geçmişi
- Gerekçe

---

## 📁 Dosya Yapısı

```
/root/trading/
├── app/
│   ├── agents/
│   │   ├── multi_signal.py       # Sinyal üretimi
│   │   └── adaptive_weights.py   # Dinamik ağırlıklar
│   ├── risk_manager/
│   │   ├── manager.py            # Risk değerlendirme
│   │   └── glm_client.py         # GLM-4 API
│   ├── executor/
│   │   ├── executor.py           # İşlem gerçekleştirme
│   │   └── ledger.py             # Database (trades, portfolio)
│   ├── data_feeds/
│   │   ├── enriched_feed.py      # Veri birleştirme
│   │   ├── twelve_data.py        # TwelveData API
│   │   └── binance_futures.py    # Binance API
│   ├── orchestrator/
│   │   └── runtime.py            # Ana döngü yönetimi
│   └── utils/
│       ├── influx.py             # InfluxDB operasyonlar
│       ├── telegram.py           # Bildirimler
│       └── logging.py            # Log sistemi
├── scripts/
│   ├── reset_portfolio.py        # Portfolio sıfırlama
│   ├── test_glm.py              # GLM testi
│   └── cleanup_bad_data.py      # Veri temizleme
└── infra/
    ├── trading-orchestrator.service      # Systemd servis
    └── trading-enriched-feed.service     # Veri servis
```

---

## 🚀 Sistem Başlatma

```bash
# Servisleri başlat
systemctl start trading-enriched-feed
systemctl start trading-orchestrator

# Durumu kontrol et
systemctl status trading-orchestrator
journalctl -u trading-orchestrator -f

# Database sıfırlama
python scripts/reset_portfolio.py --confirm

# GLM test
python scripts/test_glm.py
```

---

## 📊 Monitoring

### Loglar
```bash
# Orchestrator
tail -f /root/trading/orchestrator.log

# Enriched Feed
journalctl -u trading-enriched-feed -f
```

### Database
```python
# Portfolio durumu
from app.executor.executor import Executor
executor = Executor('BTCUSDT')
metrics = executor.portfolio_metrics()
```

### InfluxDB
```python
# Son enriched data
from app.utils.influx import query_latest_snapshot
data = query_latest_snapshot('enriched_5min', 'BTCUSDT', '5min')
```

---

## ⚙️ Konfigürasyon

### .env Dosyası
```bash
# GLM API
GLM_API_KEY=your_key

# TwelveData (12 key, virgülle ayrılmış)
TWELVE_DATA_API_KEYS=key1,key2,...,key12

# Binance
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret

# Telegram
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# InfluxDB
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=your_token
INFLUX_ORG=trading
INFLUX_BUCKET=trading_data
```

### Executor Ayarları
```python
Executor(
    symbol="BTCUSDT",
    max_position=1.0,           # Max 1 BTC
    max_daily_loss=0.1,         # Max %10 loss
    min_leverage=5.0,           # Min 5x
    max_leverage=20.0,          # Max 20x
    taker_fee_rate=0.0005,      # Binance: 0.05%
)
```

---

## 🎓 Terimler Sözlüğü

**Equity:** Toplam sermaye (başlangıç + PnL)
**Free Equity:** Kullanılabilir sermaye (equity - used margin)
**Leverage:** Kaldıraç (5x = 5 kat büyük pozisyon)
**Notional:** Pozisyon değeri (amount * price)
**Margin:** Pozisyon için gereken teminat (notional / leverage)
**PnL:** Profit and Loss (Kar/Zarar)
**Realized PnL:** Kapatılan pozisyonlardan gelen kar/zarar
**Unrealized PnL:** Açık pozisyonların şu anki kar/zarar
**Fee:** İşlem ücreti (Binance taker: 0.05%)
**Long:** Fiyat artışından kazanmak (BUY)
**Short:** Fiyat düşüşünden kazanmak (SELL)

---

## 🎯 Sonuç

Sistem **tam otomatik** çalışıyor:
- ✅ Her 5 dakikada bir döngü
- ✅ 19+ veri kaynağı
- ✅ GLM-4 AI karar verme
- ✅ Risk yönetimi
- ✅ Fee ve margin hesabı
- ✅ Telegram bildirimleri
- ✅ Tam kayıt sistemi

**Paper trading modunda** güvenle test ediliyor! 🚀
