# Multi-Timeframe Indicator'ler - Detaylı Dağılım

## 📊 Genel Bakış

Sistemimizde **3 timeframe** var ve **her birinde farklı indicator set'i** kullanılıyor:

```
┌─────────────────────────────────────────────────────────────┐
│  30min (MAIN)     →  20 indicator  (FULL SET)              │
│  1min (INTRADAY)  →  4 indicator   (TIMING)                │
│  4h (LONG-TERM)   →  6 indicator   (TREND)                 │
└─────────────────────────────────────────────────────────────┘
```

## 1️⃣ INTRADAY (1 Dakika) - 4 Indicator

**Amaç:** Entry/exit timing, micro-momentum

| Indicator | Açıklama | Kullanım |
|-----------|----------|----------|
| **Close** | Fiyat | Son 10 dakikadaki fiyat hareketi |
| **RSI(14)** | Momentum | Overbought/oversold timing |
| **MACD** | Trend momentum | Hızlı dönüş sinyalleri |
| **EMA(20)** | Kısa trend | Destek/direnç seviyeleri |

**Kod:**
```python
# derivatives.py - DerivativesFeatures
intraday_close: float        # 1m fiyat
intraday_rsi_14: float       # 1m RSI(14)
intraday_macd: float         # 1m MACD
intraday_ema_20: float       # 1m EMA(20)

# Historical arrays (son 10 dakika)
intraday_1m = {
    "close": [108044.94, ..., 108091.68],    # 10 değer
    "rsi_14": [47.54, ..., 48.15],           # 10 değer
    "macd": [-121.37, ..., -113.63],         # 10 değer
    "ema_20": [108158.53, ..., 108152.18],   # 10 değer
}
```

**Neden sadece 4?**
- 1m çok gürültülü (noise), sadece kritik indicator'ler yeterli
- Timing için: RSI (overbought/oversold), MACD (momentum shift)
- Support/resistance için: EMA20, Close

## 2️⃣ MAIN (30 Dakika) - 20 Indicator

**Amaç:** Ana trading kararları, ML model input

| Kategori | Indicator'ler | Açıklama |
|----------|---------------|----------|
| **Futures (3)** | long_short_ratio, open_interest, funding_rate | Piyasa sentiment |
| **Price (1)** | close | Kapanış fiyatı |
| **Trend (4)** | ema_20, ema_50, macd, macd_signal | Trend yönü ve güç |
| **Momentum (3)** | rsi_14, stoch_k, stoch_d | Momentum ve timing |
| **Volatility (4)** | atr_14, bb_upper, bb_middle, bb_lower | Volatilite ve range |
| **Volume (2)** | mfi, obv | Hacim konfirmasyonu |
| **Other (3)** | willr, cci, vwap_20 | Ek sinyaller |

**Kod:**
```python
# derivatives.py - DerivativesFeatures (30min MAIN)
# Futures
long_short_ratio: float      # Binance futures L/S ratio
open_interest: float         # Açık pozisyon
funding_rate: float          # Funding rate

# Price & Trend
close: float                 # Kapanış fiyatı
ema_20: float               # 20-period EMA
ema_50: float               # 50-period EMA
macd: float                 # MACD
macd_signal: float          # MACD signal line

# Momentum
rsi_14: float               # 14-period RSI
stoch_k: float              # Stochastic %K
stoch_d: float              # Stochastic %D

# Volatility
atr_14: float               # Average True Range
bb_upper: float             # Bollinger üst band
bb_middle: float            # Bollinger orta
bb_lower: float             # Bollinger alt band

# Volume & Others
mfi: float                  # Money Flow Index
obv: float                  # On Balance Volume
willr: float                # Williams %R
cci: float                  # Commodity Channel Index
vwap_20: float             # Volume Weighted Average Price

# Historical arrays (son 5 saat = 10 bar)
main_30min = {
    "close": [...],         # 10 değer
    "rsi_14": [...],        # 10 değer
    "macd": [...],          # 10 değer
    "ema_20": [...],        # 10 değer
    "ema_50": [...],        # 10 değer
    "stoch_k": [...],       # 10 değer
    "atr_14": [...],        # 10 değer
    "mfi": [...],           # 10 değer
}
```

**Neden 20?**
- Ana karar timeframe'i
- ML model bu 20 feature'ı kullanıyor
- Full technical analysis için yeterli
- TwelveData kaldırıldıktan sonra 25'ten 20'ye düştü

## 3️⃣ LONG-TERM (4 Saat) - 6 Indicator

**Amaç:** Genel trend yönü, büyük resim

| Indicator | Açıklama | Kullanım |
|-----------|----------|----------|
| **EMA(20)** | 20-period trend | Ana trend çizgisi |
| **EMA(50)** | 50-period trend | Uzun vade trend |
| **RSI(14)** | Momentum | Genel momentum durumu |
| **MACD** | Trend strength | Trend gücü |
| **ATR(14)** | Volatility | 4h volatilite seviyesi |
| **Volume** | Hacim | Piyasa ilgisi |

**Kod:**
```python
# derivatives.py - DerivativesFeatures
longterm_ema_20: float       # 4h EMA(20)
longterm_ema_50: float       # 4h EMA(50)
longterm_rsi_14: float       # 4h RSI(14)
longterm_macd: float         # 4h MACD
longterm_atr_14: float       # 4h ATR(14)
longterm_volume: float       # 4h Volume

# Historical arrays (son 40 saat = 10 bar)
longterm_4h = {
    "ema_20": [...],         # 10 değer
    "ema_50": [...],         # 10 değer
    "rsi_14": [...],         # 10 değer
    "macd": [...],           # 10 değer
    "atr_14": [...],         # 10 değer
    "volume": [...],         # 10 değer
}
```

**Neden sadece 6?**
- 4h için detaylı indicator'ler gereksiz
- Trend yönü (EMA20 vs EMA50) en önemli
- Momentum (RSI, MACD) yeterli
- Volume ve ATR context için

## 📋 Tablo: Hangi Indicator Hangi Timeframe'de?

| Indicator | 1m | 30m | 4h | Açıklama |
|-----------|:--:|:---:|:--:|----------|
| **Close** | ✅ | ✅ | ❌ | Fiyat (1m ve 30m) |
| **EMA(20)** | ✅ | ✅ | ✅ | Kısa trend (tüm TF'lerde) |
| **EMA(50)** | ❌ | ✅ | ✅ | Uzun trend (sadece 30m ve 4h) |
| **RSI(14)** | ✅ | ✅ | ✅ | Momentum (tüm TF'lerde) |
| **MACD** | ✅ | ✅ | ✅ | Trend momentum (tüm TF'lerde) |
| **MACD Signal** | ❌ | ✅ | ❌ | MACD çizgisi (sadece 30m) |
| **Stochastic** | ❌ | ✅ | ❌ | Overbought/oversold (sadece 30m) |
| **ATR(14)** | ❌ | ✅ | ✅ | Volatilite (30m ve 4h) |
| **Bollinger** | ❌ | ✅ | ❌ | Range (sadece 30m) |
| **MFI** | ❌ | ✅ | ❌ | Money flow (sadece 30m) |
| **OBV** | ❌ | ✅ | ❌ | Volume trend (sadece 30m) |
| **WillR** | ❌ | ✅ | ❌ | Momentum (sadece 30m) |
| **CCI** | ❌ | ✅ | ❌ | Trend strength (sadece 30m) |
| **VWAP** | ❌ | ✅ | ❌ | Fair value (sadece 30m) |
| **Volume** | ❌ | ❌ | ✅ | Hacim (sadece 4h) |
| **L/S Ratio** | ❌ | ✅ | ❌ | Futures sentiment (sadece 30m) |
| **Open Interest** | ❌ | ✅ | ❌ | Futures (sadece 30m) |
| **Funding Rate** | ❌ | ✅ | ❌ | Futures (sadece 30m) |

## 🎯 Neden Bu Dağılım?

### 1m: Minimal Set (4)
```
Amaç: Timing optimization
Sadece gerekli: Price, RSI, MACD, EMA20
Gürültü az, sinyal net
```

### 30m: Full Set (20)
```
Amaç: Main trading decisions
ML model input (20 features)
Full technical analysis
Risk/reward hesaplama
```

### 4h: Context Set (6)
```
Amaç: Trend direction
EMA20 vs EMA50 → Bullish/Bearish?
Momentum check (RSI, MACD)
Volatility context (ATR)
```

## 🔄 Multi-Timeframe İş Birliği

**Örnek Senaryo:**

```python
# 4h: BEARISH TREND
4h_ema20 = 108167  # <
4h_ema50 = 108778  # EMA20 < EMA50 → Downtrend
4h_rsi = 44        # Düşüyor

# 30m: RECOVERY ATTEMPT
30m_macd = -121    # İyileşiyor (-169'dan)
30m_rsi = 48       # Neutral
30m_stoch = 40     # Düşüyor (94'ten)

# 1m: SIDEWAYS
1m_rsi = 48        # Neutral
1m_macd = -113     # Hafif iyileşiyor

# GLM Analizi:
"""
4h downtrend devam ediyor (EMA20 < EMA50).
30m'de MACD iyileşiyor ama Stochastic 94'ten 40'a düşmüş (momentum kaybı).
1m flat, net sinyal yok.

KARAR: HOLD veya LOW CONFIDENCE BUY
Gerekçe: 4h bearish, 30m mixed, 1m neutral → Uyumsuzluk
Öneri: 4h confirmation bekle veya 30m RSI <40 olunca gir
"""
```

## 💡 Özet

**CORE INDICATORS (Tüm TF'lerde):**
- EMA(20): 1m ✅ 30m ✅ 4h ✅
- RSI(14): 1m ✅ 30m ✅ 4h ✅
- MACD: 1m ✅ 30m ✅ 4h ✅

**30m EXCLUSIVE (Sadece 30m'de):**
- Futures: L/S, OI, FR
- Advanced: Stoch, BB, MFI, OBV, WillR, CCI, VWAP
- Model için gerekli: 20 feature ML input

**4h EXCLUSIVE (Sadece 4h'de):**
- EMA(50): Uzun vade trend
- Volume: Piyasa ilgisi
- ATR: Macro volatility

**1m MINIMAL (Timing için):**
- Sadece 4: Close, RSI, MACD, EMA20
- Gürültü azaltma: Fazla indicator = false signal
- Timing optimization: Overbought/oversold check

---

**Toplam:** 4 (1m) + 20 (30m) + 6 (4h) = **30 unique indicator**
**Overlap:** 3 (EMA20, RSI14, MACD) tüm TF'lerde var
**Total unique fields:** ~26 indicator
