# Telegram Bildirimi Metrics Hatası Düzeltmesi

**Tarih:** 2025-10-23  
**Sorun ID:** Telegram bildiriminde eski pozisyon bilgisi gösterilmesi

## Sorun

Telegram'daki döngü bildirimi YANLIŞ pozisyon bilgisi gösteriyordu:

```
💰 Portföy Durumu
Sermaye: $9,995.16
Pozisyon: 📉 SHORT 0.5505 BTC  ← YANLIŞ!
```

Oysa:
- GLM kararı: **BUY** (SHORT'u kapatmak için) ✅
- Executor: Pozisyon **BAŞARIYLA kapandı** ✅
- Database: `Position: 0.000000 BTC` ✅
- Log: `position=0.0000 avg_price=0.00` ✅

**AMA** telegram bildirimi hala **ESKİ pozisyonu** gösteriyordu!

## Kök Neden

`app/orchestrator/runtime.py` → `_run_cycle` fonksiyonunda:

```python
# İŞLEM ÖNCESİ metrics alınıyor
metrics = self._executor.portfolio_metrics()

# GLM kararı
decision = self._safe_evaluate([signal], metrics)

# İŞLEM YAPILIYOR
result = self._executor.execute(decision)

# ESKI metrics ile bildirim gönderiliyor! ❌
self._notify_cycle_complete(decision, result, metrics)
```

**Sorun:** Telegram bildirimi işlem ÖNCESI alınan metrics'i kullanıyor!

## Çözüm

İşlem yapıldıktan SONRA metrics'i yeniden al:

```python
# İŞLEM ÖNCESİ metrics (GLM için gerekli)
metrics_before = self._executor.portfolio_metrics()

# GLM kararı
decision = self._safe_evaluate([signal], metrics_before)

# İŞLEM YAPILIYOR
result = self._executor.execute(decision)

# İŞLEM SONRASI GÜNCEL metrics al ✅
metrics_after = self._executor.portfolio_metrics()

# GÜNCEL metrics ile bildirim gönder ✅
self._notify_cycle_complete(decision, result, metrics_after)
```

## Test Senaryosu

### Önceki Durum (HATALI):
1. Döngü başlangıcı: `position = -0.5505 BTC` (SHORT)
2. GLM kararı: `BUY` (SHORT'u kapat)
3. Executor: Pozisyon kapandı → `position = 0 BTC` ✅
4. Telegram: `position = -0.5505 BTC` gösteriyor ❌ (ESKİ metrics)

### Yeni Durum (DOĞRU):
1. Döngü başlangıcı: `position = -0.5505 BTC` (SHORT)
2. GLM kararı: `BUY` (SHORT'u kapat)
3. Executor: Pozisyon kapandı → `position = 0 BTC` ✅
4. Telegram: `position = 0 BTC` gösteriyor ✅ (GÜNCEL metrics)

## Log Analizi

### Başarılı Pozisyon Kapatma (Log'lardan):

```
13:21:05 | CLOSING position: current=-0.550489 btc_to_close=0.550489
13:21:06 | Closing SHORT position: amount=0.5505 close_price=108919.80
13:21:06 | position=0.0000 avg_price=0.00 equity=9980.92
```

### Database Doğrulaması:

```sql
Position: +0.000000 BTC
Average Price: $0.00
```

✅ Pozisyon GERÇEKTEN kapandı!

## İlgili Düzeltmeler (Aynı Gün)

### 1. Pozisyon Yönetimi Kuralları (Sabah)
- GLM system prompt'una pozisyon kapatma kuralları eklendi
- LONG + düşüş → SELL ile kapat
- SHORT + yükseliş → BUY ile kapat

### 2. Executor Bug Düzeltmesi (Öğlen)
- `position_size_usd` tanımsız hatası giderildi
- Pozisyon kapatırken notional value hesaplanması eklendi

### 3. Telegram Metrics Hatası (Şimdi)
- Döngü sonunda GÜNCEL metrics kullanılması sağlandı

## Değiştirilen Dosyalar

### `app/orchestrator/runtime.py`
- Line 143-144: `metrics` → `metrics_before`
- Line 158-159: İşlem sonrası `metrics_after` eklendi
- Line 163: `_notify_cycle_complete(decision, result, metrics_after)`

## Sonraki Döngüde Beklenen

Bir sonraki 15 dakikalık döngüde:
1. Telegram bildirimi **GÜNCEL** pozisyon bilgisi gösterecek
2. Pozisyon kapatma durumları doğru yansıyacak
3. FLAT pozisyon → "⚪ FLAT 0.0000 BTC" görünecek

## Test Komutu

```bash
# Orchestrator restart
systemctl restart trading-orchestrator

# Log takibi
journalctl -u trading-orchestrator -f

# Bir sonraki döngüyü bekle (15 dakika)
# Telegram'da pozisyon bilgisini kontrol et
```

## Özet

| Bileşen | Durum | Açıklama |
|---------|-------|----------|
| GLM Kararı | ✅ | SHORT + yükseliş → BUY (DOĞRU) |
| Executor | ✅ | Pozisyon başarıyla kapandı |
| Database | ✅ | Position = 0 BTC |
| Telegram (Eski) | ❌ | Eski metrics gösteriyordu |
| Telegram (Yeni) | ✅ | Güncel metrics gösterecek |

## İlgili Dokümantasyon

- `POSITION_MANAGEMENT_FIX.md` - Pozisyon kapatma mantığı düzeltmesi
- `PORTFOLIO_RESET_OCT23.md` - Portföy sıfırlama ve executor bug fix
- `TELEGRAM_NOTIFICATION_UPDATE.md` - Eski telegram güncelleme notları
