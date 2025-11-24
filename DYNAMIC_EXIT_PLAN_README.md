# Dinamik Exit Plan Güncelleme Sistemi

## Genel Bakış

Bu sistem, açık pozisyonların exit planlarını (stop loss, take profit, invalidation condition) piyasa koşullarına göre her 3 dakikada otomatik olarak günceller. GLM, volatilite, trend gücü, momentum ve RSI gibi piyasa verilerini analiz ederek exit planlarını optimize eder.

## Özellikler

- ✅ **Tam Otomatik Güncelleme**: Kullanıcı onayı gerekmez
- ✅ **GLM Bazlı Karar**: GLM piyasa koşullarını analiz eder ve formül çarpanlarını belirler
- ✅ **Invalidation Kuralları**: LONG/SHORT pozisyonlar için doğru invalidation pozisyonu zorlanır
- ✅ **Risk Kontrolleri**: SL max genişleme, TP minimum risk/reward oranı, invalidation pozisyon doğrulaması
- ✅ **Şeffaflık**: Her güncelleme database'de loglanır ve Telegram'a bildirilir
- ✅ **Öğrenme**: Geçmiş güncellemelerin başarı oranları GLM'e geri beslenir

## Mimari

### Yeni Dosyalar

1. **`app/risk_manager/risk_controls.py`**
   - Exit plan validasyon kontrolleri
   - Invalidation pozisyon doğrulaması
   - Risk/reward oranı kontrolü

2. **`app/risk_manager/dynamic_exit_updater.py`**
   - Dinamik exit plan güncelleme motoru
   - GLM'e sorgu gönderme
   - Piyasa koşulu değişimini algılama

3. **`migrations/add_exit_plan_history.sql`**
   - Database migration script'i
   - `exit_plan_history` kolonu ekler

4. **`migrations/run_migration.py`**
   - Python migration runner
   - Database güncelleme otomasyonu

5. **`tests/test_risk_controls.py`**
   - Risk kontrol validasyon testleri
   - Invalidation pozisyon testleri

### Değiştirilen Dosyalar

1. **`app/executor/ledger.py`**
   - `Trade` modeline `exit_plan_history` kolonu eklendi

2. **`app/risk_manager/nof1_prompt_builder.py`**
   - Invalidation validasyon mantığı düzeltildi
   - GLM prompt'una invalidation kuralları eklendi

3. **`app/monitoring/position_monitor.py`**
   - `_monitor_cycle()` içine dinamik güncelleme adımı eklendi
   - GLM client desteği eklendi

## Kurulum

### 1. Database Migration

```bash
# SQL ile
psql -U your_user -d trading_db -f migrations/add_exit_plan_history.sql

# veya Python script ile
python3 migrations/run_migration.py
```

### 2. Test

```bash
# Risk kontrol validasyonlarını test et
python3 tests/test_risk_controls.py
```

Beklenen çıktı:
```
🎉 ALL TESTS PASSED
```

### 3. Position Monitor'a GLM Client Ekle

`runtime.py` veya benzer orchestrator dosyasında:

```python
from app.risk_manager.glm_client import GLMClient

glm_client = GLMClient()

position_monitor = PositionMonitor(
    symbol="BTCUSDT",
    interval_seconds=180,
    executor=executor,
    glm_client=glm_client  # YENİ parametre
)
```

## Invalidation Kuralları

### LONG Pozisyon

**Doğru Sıralama:** `Stop Loss < Invalidation < Entry < Profit Target`

**Örnek:**
```
Entry: $110,000
Stop Loss: $105,000
Invalidation: $107,000 ✅ (Entry ile SL arasında)
Profit Target: $117,500
```

**Formül:**
```python
Invalidation = Entry - (Entry - SL) * 0.4
```

### SHORT Pozisyon

**Doğru Sıralama:** `Profit Target < Entry < Invalidation < Stop Loss`

**Örnek:**
```
Entry: $110,000
Stop Loss: $115,000
Invalidation: $113,000 ✅ (Entry ile SL arasında)
Profit Target: $102,500
```

**Formül:**
```python
Invalidation = Entry + (SL - Entry) * 0.4
```

## Risk Kontrolleri

### 1. SL Maksimum Genişleme
- SL asla entry'den %10'dan fazla uzaklaşamaz

### 2. Risk/Reward Minimum Oranı
- Minimum 1:1.5 risk/reward oranı zorunlu

### 3. Invalidation Pozisyon Kontrolü
- LONG: `Entry > Invalidation > SL` (zorunlu)
- SHORT: `SL > Invalidation > Entry` (zorunlu)
- Invalidation SL ile Entry arasında %20-%60 bandında olmalı

### 4. SL Yön Kontrolü
- LONG: SL < Entry
- SHORT: SL > Entry

## Güncelleme Tetikleyicileri

Sistem aşağıdaki durumlarda exit plan güncellemesi yapar:

1. **Volatilite Değişimi**: %10'dan fazla volatilite değişimi
2. **Fiyat Değişimi**: Entry'den %1'den fazla fiyat değişimi
3. **Pozisyon Yaşı**: 30 dakikadan eski pozisyon, hiç güncelleme yapılmamışsa
4. **Spam Önleme**: Son güncelleme 6 dakikadan yeniyse atlanır
5. **Maksimum Güncelleme**: Pozisyon başına max 10 güncelleme

## GLM Prompt Formatı

GLM'e gönderilen bağlam:

```
Current Position:
  Entry Price: $110,000
  Position Type: LONG
  Position Age: 12 minutes
  Current Exit Plan:
    - Profit Target: $115,000
    - Stop Loss: $105,000
    - Invalidation: "If price closes below $109,000 on 3m candle"

Market Changes Since Position Open:
  - Volatility: 0.35 → 0.65 (+85%)
  - Trend Strength: 0.80 → 0.82 (+2%)
  - RSI: 65 → 72 (+7 points)
  - Current Price: $112,500 (+2.27%)

⚠️ CRITICAL INVALIDATION CONDITION RULES:

LONG Pozisyon için:
  ✅ Invalidation MUTLAKA Stop Loss'un ÜSTÜNDE olmalı
  ✅ Doğru sıralama: Entry > Invalidation > Stop Loss
  ...
```

## GLM Yanıt Formatı

```json
{
  "update_needed": true,
  "reasoning": "Volatilite %85 arttı, SL genişletilmeli...",
  "multipliers": {
    "volatility_multiplier": 1.6,
    "trend_multiplier": 1.3,
    "momentum_multiplier": 1.2,
    "age_multiplier": 1.1
  },
  "new_exit_plan": {
    "stop_loss": 101000,
    "profit_target": 114500,
    "invalidation_condition": "If price closes below 104000 on 5m candle"
  }
}
```

## Telegram Bildirimi

Her güncelleme Telegram'a bildirilir:

```
🔄 Exit Plan Güncellendi - BTCUSDT LONG

📊 Değişiklikler:
  SL: $105,000 → $101,000 (genişletildi)
  TP: $115,000 → $114,500 (düşürüldü)
  INV: $109,000 → $104,000 (SL'ye yaklaştırıldı)
  Time Frame: 3m → 5m candle

💡 Sebep: Volatilite %85 arttı, gürültüyü azaltmak için time frame uzatıldı

⏰ Pozisyon Yaşı: 12 dakika
📈 Güncel Fiyat: $112,500 (+2.27%)

✅ Validasyon: Entry($110,000) > INV($104,000) > SL($101,000) ❌
```

## Loglama

```python
logger.info(
    "Exit plan updated | position=%s old_sl=%.2f new_sl=%.2f reason=%s",
    position_id, old_sl, new_sl, glm_reasoning
)

logger.warning(
    "Invalidation validation FAILED | position=%s side=%s entry=%.2f inv=%.2f sl=%.2f",
    position_id, position_side, entry_price, inv_price, stop_loss
)
```

## Başarı Metrikleri

### Ölçülecek KPI'lar
- Exit plan güncelleme sayısı / pozisyon
- Güncellemelerden sonra TP hit oranı
- Güncellemelerden sonra SL hit oranı
- Invalidation tetiklenme oranı (SL'den önce çıkış)
- Ortalama pozisyon PnL (güncellemeli vs. güncellemesiz)
- GLM'in güncelleme önerilerinin kabul oranı
- Invalidation validasyon hatası oranı

### Başarı Kriterleri
- TP hit oranı %10+ artış
- SL hit oranı %5- azalış
- Invalidation tetiklenme oranı %15+ (erken çıkış başarısı)
- Ortalama PnL %15+ artış
- GLM önerilerinin %80+ kabul edilmesi
- Invalidation validasyon hatası %5 altında

## Örnek Senaryo

### T=0: Pozisyon Açıldı
```
Entry: $110,000 (LONG)
SL: $105,000 (-4.5%)
Invalidation: $107,000 (-2.7%) ✅ [Entry > INV > SL]
TP: $117,500 (+6.8%)
Volatilite: 0.35
```

### T=3dk: İlk Güncelleme
```
Fiyat: $111,500 (+1.36%)
Volatilite: 0.38 (+8%)
GLM Karar: "Trend güçlü, SL'yi zarara taşı"

YENİ PLAN:
  SL: $108,000 (-1.8%) [zarara taşındı]
  Invalidation: $109,000 (-0.9%) ✅ [Entry > INV > SL]
  TP: $118,000 (+7.3%)
```

### T=6dk: İkinci Güncelleme
```
Fiyat: $113,000 (+2.73%)
Volatilite: 0.65 (+85%)
GLM Karar: "Volatilite spike, SL genişlet ama zararda tut"

YENİ PLAN:
  SL: $107,000 (-2.7%)
  Invalidation: $108,500 (-1.4%) ✅ [Entry > INV > SL]
  TP: $120,000 (+9.1%)
```

## Hata Yönetimi

### GLM Timeout
```python
if glm_timeout(30s):
    logger.warning("GLM timeout, skipping update")
    return None
```

### Validasyon Başarısız
```python
if not validate_exit_plan_update(...):
    logger.error("Validation failed, rejecting update")
    send_telegram_alert("Exit plan update rejected")
    return False
```

### Database Hatası
```python
try:
    session.commit()
except Exception:
    session.rollback()
    logger.error("Database error, rolling back")
```

## Sık Sorulan Sorular

### Q: Kullanıcı onayı gerekli mi?
**A:** Hayır, tam otomatik. Güncelleme anında uygulanır.

### Q: Kaç kez güncelleme yapılır?
**A:** Limitsiz, ancak spam önleme: max 10 güncelleme/pozisyon, 6 dakika minimum aralık.

### Q: GLM yanıt vermezse ne olur?
**A:** Mevcut exit plan korunur, güncelleme atlanır.

### Q: Invalidation validasyon başarısız olursa?
**A:** Güncelleme reddedilir, GLM'e feedback gönderilir, Telegram alert.

### Q: Eski pozisyonlar nasıl migrate edilir?
**A:** Migration script otomatik olarak açık pozisyonlara boş history ekler.

## Lisans

Bu proje MIT lisansı altında lisanslanmıştır.
