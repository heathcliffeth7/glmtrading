# Trading Cycle Update: 30min → 15min

## ✅ Değişiklik Yapıldı (Oct 23, 2025 - 10:21 UTC)

Trading cycle süresi **30 dakika → 15 dakika** olarak güncellendi.

---

## 🔄 Değişiklikler

### runtime.py Güncellemesi

**Önce:**
```python
cycle_seconds: int = 1800,  # 30 dakika
```

**Sonra:**
```python
cycle_seconds: int = 900,  # 15 dakika
```

### Log Mesajı Güncellemesi

**Önce:**
```
"Starting automated runner with 30min interval (feature workers disabled, using TwelveData sync)"
```

**Sonra:**
```
"Starting automated runner with 15min cycle (30min interval data, feature workers disabled)"
```

---

## 📊 Yeni Cycle Parametreleri

| Parametre | Değer | Açıklama |
|-----------|-------|----------|
| **Cycle Interval** | 15 dakika | GLM'e her 15dk'da karar sorulur |
| **Data Interval** | 30min (main) | Ana timeframe hala 30min |
| **API Calls/Gün** | ~96 calls | 24h / 0.25h = 96 |
| **API Calls/Ay** | ~2,880 calls | 96 × 30 = 2,880 |

---

## 💰 Maliyet Değişimi

### Önce (30min cycle):
- API Calls/Gün: **48 calls**
- API Calls/Ay: **1,440 calls**

### Sonra (15min cycle):
- API Calls/Gün: **96 calls** (2x artış)
- API Calls/Ay: **2,880 calls** (2x artış)

**Maliyet Artışı:** %100 (2x)

---

## 🎯 Beklenen Faydalar

### ✅ Artıları
1. **Daha Hızlı Reaksiyon:** Piyasa değişikliklerine 15 dakika içinde yanıt
2. **Daha Fazla Fırsat:** Günde 2x daha fazla trading fırsatı
3. **Risk Yönetimi:** Pozisyonlar daha sık gözden geçirilir
4. **30min Uyum:** 15dk, 30min bar'ın yarısı (iyi uyum)

### ⚠️ Dikkat Edilmesi Gerekenler
1. **GLM Performansı:** Her call 60-90 saniye sürer (15dk içinde tamamlanmalı)
2. **Overtrading Riski:** Daha sık karar = daha fazla işlem ücreti
3. **Short-term Noise:** 15dk'lık değişimler yanıltıcı olabilir

---

## 📈 Veri Akışı

Veri toplama zamanlaması **değişmedi**:

```
Binance API → enriched_feed.py → InfluxDB
  ├─ 1m:    Her 60 saniye
  ├─ 30min: Her 1800 saniye (30dk)
  └─ 4h:    Her 14400 saniye (4h)

InfluxDB → DerivativesAgent → GLM
  └─ Her 15 dakika (900 saniye) ← YENİ
```

**Not:** GLM her 15dk'da çalışır ama 30min data'sı sadece 30dk'da bir güncellenir. Bu normaldir - sistem en son mevcut data ile çalışır.

---

## 🔄 Cycle Timing Örnekleri

| Saat | Event | Data Freshness |
|------|-------|----------------|
| 10:00 | Cycle #1 | 30min data: 09:30 |
| 10:15 | Cycle #2 | 30min data: 09:30 (aynı) |
| 10:30 | Cycle #3 | 30min data: 10:00 ✅ (yeni) |
| 10:45 | Cycle #4 | 30min data: 10:00 (aynı) |
| 11:00 | Cycle #5 | 30min data: 10:30 ✅ (yeni) |

**Özet:** Her 30 dakikada bir **2 GLM decision** olur. İlki yeni data ile, ikincisi aynı data ile.

---

## 🎛️ Geri Alma (İsterseniz)

Eski 30 dakikalık cycle'a dönmek için:

```bash
# runtime.py'de değiştir:
cycle_seconds: int = 1800,  # 30 dakika

# Restart
systemctl restart trading-orchestrator.service
```

---

## 📝 Test Edilecekler

- [ ] İlk 15dk cycle tamamlandı mı?
- [ ] GLM kararı 15dk içinde geldi mi?
- [ ] Telegram bildirimleri çalışıyor mu?
- [ ] 30dk yeni data geldiğinde GLM farklı karar veriyor mu?
- [ ] Overtrading olmuyor mu?

---

## ✅ Sonuç

Cycle süresi **30dk → 15dk** olarak güncellendi. Sistem:
- ✅ 2x daha sık GLM kararı alacak
- ✅ Piyasa değişikliklerine daha hızlı yanıt verecek
- ✅ API maliyeti 2x artacak
- ✅ Ana timeframe (30min) korundu

**Sistem şu an 15 dakikalık cycle ile çalışıyor! 🚀**
