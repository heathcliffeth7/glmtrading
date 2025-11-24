# GLM Kalite İyileştirme - Daha Az ve Daha Kesin Trade

**Tarih**: 26 Ekim 2025  
**Seviye**: Seviye 2 (Orta - Önerilen)  
**Durum**: ✅ Uygulandı

## 🎯 Hedef

GLM'nin başarısız trade oranını düşürmek için:
- ✅ **Daha az işlem** (sadece yüksek kaliteli fırsatlarda)
- ✅ **Daha yüksek confidence threshold** (zayıf sinyalleri filtrele)
- ✅ **Daha küçük pozisyonlar** (risk azaltma)
- ✅ **HOLD'u varsayılan yap** (belirsizlikte bekle)

## 📊 Yapılan Değişiklikler

### 1. Confidence Threshold Yükseltildi ⭐

**Dosya**: `/root/trading/app/risk_manager/manager.py` (Satır 526-539)

**Öncesi**:
```python
if abs_conf < 0.3:  # Çok düşük confidence
    return HOLD

if abs_conf < 0.5:
    amount = 0.1  # 10%
elif abs_conf < 0.7:
    amount = 0.3  # 30%
else:
    amount = 0.5  # 50%
```

**Sonrası**:
```python
if abs_conf < 0.5:  # 0.5'ten düşük → HOLD (daha seçici!)
    return HOLD

if abs_conf < 0.7:
    amount = 0.1  # 10%
elif abs_conf < 0.85:
    amount = 0.2  # 20% (yeni seviye)
else:
    amount = 0.3  # 30% (eski max: 0.5)
```

**Etki**:
- Confidence < 0.5 → Trade YOK, HOLD
- Confidence 0.5-0.7 → Küçük pozisyon (10%)
- Confidence 0.7-0.85 → Orta pozisyon (20%)
- Confidence > 0.85 → Büyük pozisyon (30%, eski max 50%)

**Beklenen Sonuç**: ~50% daha az trade, sadece güçlü sinyallerde işlem

### 2. GLM Prompt'a "Quality > Quantity" Kuralları Eklendi ⭐⭐

**Dosya**: `/root/trading/app/risk_manager/manager.py` (Satır 290-320)

Eklenen kurallar:

#### ✅ Trade Yapma Koşulları:
```
✅ ONLY TRADE WHEN ALL CONDITIONS ARE MET:
  1. ALL 3 timeframes (1m/30m/4h) ALIGNED in same direction
  2. Multiple confirmation indicators (at least 4-5 agreeing)
  3. Clear trend direction (NOT choppy/sideways/ranging)
  4. Strong momentum indicators (RSI not neutral 45-55)
  5. Reasonable volatility (ATR not spiking, no panic)
```

#### ❌ HOLD Kullan Durumları:
```
❌ USE HOLD WHEN:
  - Timeframes are MIXED (1m bullish but 4h bearish)
  - Market is CHOPPY/SIDEWAYS (no clear direction)
  - Indicators CONFLICTING (some bullish, some bearish)
  - RSI NEUTRAL (45-55 range = uncertain, wait!)
  - Price moved TOO FAST recently (whipsaw risk)
  - Volatility TOO HIGH (ATR extreme, panic mode)
  - Recent news/event causing uncertainty
```

#### 📊 Kalite Felsefesi:
```
📊 QUALITY > QUANTITY PHILOSOPHY:
  ⭐ Better to HOLD and wait than force a mediocre trade
  ⭐ One high-quality trade per day > Five weak trades
  ⭐ If you're unsure → ALWAYS choose HOLD, don't guess!
  ⭐ Missing a trade is OK, losing money is NOT OK
```

#### 🎯 Pozisyon Büyüklüğü Rehberi:
```
🎯 POSITION SIZING - BE CONSERVATIVE:
  - miktar: 0.05-0.1 → Use this for MOST trades (5-10%)
  - miktar: 0.15-0.2 → Only for VERY strong signals
  - miktar: 0.3 → RARELY! Only when ALL indicators perfectly aligned
  - miktar: 0.5 → NEVER use! Too risky, always start small
  - Default approach: Start with 0.1 (10%) for safety
```

**Etki**: GLM artık çok daha seçici davranacak, HOLD'u "varsayılan" seçenek olarak görecek

### 3. Miktar Hard Cap Eklendi ⭐

**Dosya**: `/root/trading/app/executor/executor.py` (Satır 173-181)

```python
# GLM AMOUNT HARD CAP: Maksimum %20 equity (daha güvenli trading)
MAX_GLM_AMOUNT = 0.20  # %20
if decision.amount > MAX_GLM_AMOUNT:
    logger.warning(
        "GLM amount capped for safety: requested=%.2f%% capped=%.2f%%",
        decision.amount * 100,
        MAX_GLM_AMOUNT * 100
    )
    decision.amount = MAX_GLM_AMOUNT
```

**Etki**: 
- GLM %30-50 önerse bile, maksimum %20 kullanılır
- Büyük kayıpları önler
- Daha güvenli risk yönetimi

## 📈 Beklenen Sonuçlar

### Önceki Davranış (Tahmini):
```
- Her döngüde trade
- Düşük confidence'da bile işlem
- Büyük pozisyonlar (%30-50)
- Çok fazla whipsaw
- Yüksek fee maliyeti
```

### Yeni Davranış:
```
✅ Sadece güçlü sinyallerde trade (~50% daha az işlem)
✅ Confidence < 0.5 → HOLD (otomatik filtreleme)
✅ Küçük pozisyonlar (%10-20, nadiren %30)
✅ Timeframe uyumsuzluğunda bekle
✅ Belirsizlikte HOLD (güvenli davranış)
✅ Düşük fee (daha az işlem)
```

## 🎲 Trade Frekans Beklentileri

### Önceki (Agresif):
```
15 dakikada 1 döngü = günde 96 döngü
Her döngüde %60 trade yapma ihtimali
→ Günde ~58 trade (çok fazla!)
```

### Yeni (Seçici):
```
15 dakikada 1 döngü = günde 96 döngü
Confidence < 0.5 → HOLD (%50+ döngü)
Timeframe uyumsuz → HOLD (%20+ döngü)
→ Günde ~20-25 trade tahmini
→ %60 azalma ✅
```

### İdeal Hedef:
```
Günde 10-15 yüksek kaliteli trade
Win rate: %60+ (önceki %40-50'den artış)
Ortalama trade büyüklüğü: %10-15 equity
```

## 📊 Örnek Senaryo

### Eski Sistem:
```
Döngü 1: Confidence 0.35 → BUY 10% (zayıf sinyal)
  → Kayıp: -$50

Döngü 2: Confidence 0.42 → SELL 30% (karışık sinyal)
  → Kayıp: -$120

Döngü 3: Confidence 0.68 → BUY 50% (iyi sinyal ama çok büyük)
  → Kazanç: +$180, ama riskli

Net: +$10 (çok volatil, stresli)
```

### Yeni Sistem:
```
Döngü 1: Confidence 0.35 → HOLD ❌ (threshold altı)
  → İşlem YOK

Döngü 2: Confidence 0.42 → HOLD ❌ (threshold altı)
  → İşlem YOK

Döngü 3: Confidence 0.68 → BUY 10% ✅ (güçlü sinyal, küçük pozisyon)
  → Kazanç: +$40 (güvenli, kontrollü)

Net: +$40 (daha az volatil, daha güvenli)
```

## 🔧 Ek Ayarlar (Gerekirse)

### Daha da Konservatif Yapmak İçin:

1. **Threshold'u 0.6'ya çıkar**:
   ```python
   # manager.py satır 526
   if abs_conf < 0.6:  # Daha da seçici
       return HOLD
   ```

2. **Max amount'u 0.15'e düşür**:
   ```python
   # executor.py satır 174
   MAX_GLM_AMOUNT = 0.15  # %15
   ```

3. **Trade frequency limiti ekle**:
   ```python
   # executor.py __init__
   self._min_trade_interval = 900  # 15 dakika arası en az 1 trade
   ```

### Daha Agresif Yapmak İçin:

1. **Threshold'u 0.4'e düşür**:
   ```python
   if abs_conf < 0.4:
       return HOLD
   ```

2. **Max amount'u 0.25'e çıkar**:
   ```python
   MAX_GLM_AMOUNT = 0.25  # %25
   ```

## 📝 Değiştirilen Dosyalar

1. **`/root/trading/app/risk_manager/manager.py`**
   - Satır 526-539: Confidence threshold ve amount scaling
   - Satır 290-320: GLM prompt'a quality kuralları

2. **`/root/trading/app/executor/executor.py`**
   - Satır 173-181: GLM amount hard cap

## 🚀 Sonraki Adımlar

1. ✅ Değişiklikler uygulandı
2. ⏭️ Sistemi yeniden başlat
3. ⏭️ İlk 24 saat izle:
   - Trade frekansı (beklenti: %50-60 azalma)
   - HOLD oranı (beklenti: %50+ artış)
   - Ortalama confidence değerleri
   - Pozisyon büyüklükleri (%10-20 arası mı?)
4. ⏭️ 1 hafta sonra win rate analizi yap
5. ⏭️ Gerekirse threshold'u ayarla

## 📞 İzleme

### Loglardan İzle:
```bash
# HOLD kararlarını izle
journalctl -u trading-orchestrator -f | grep "HOLD"

# Confidence capping'i izle
journalctl -u trading-orchestrator -f | grep "Confidence guardrail"

# Amount capping'i izle
journalctl -u trading-orchestrator -f | grep "GLM amount capped"
```

### Telegram'dan İzle:
- HOLD kararı mesajları artacak
- Trade sayısı azalacak
- Pozisyon büyüklükleri daha küçük olacak

---

**Sonuç**: GLM artık **kalite odaklı** davranacak. Belirsizlikte bekleyecek, sadece net fırsatlarda küçük-orta pozisyonlarla girecek. Bu daha güvenli ve sürdürülebilir trading stratejisi sağlayacak.
