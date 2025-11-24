# Price Spike Monitoring - Kullanım Kılavuzu

## 📊 Durum: ✅ Sistem Temiz Çalışıyor

**Son 24 saat**: 0 filtrelenen mesaj (Mükemmel! 🎯)

## 🛠️ Monitoring Araçları

### 1. Real-Time Monitoring (Canlı İzleme)

```bash
cd /root/trading
./monitor_price_spikes.sh
```

**Ne yapar?**
- Tüm servislerden gelen logları gerçek zamanlı izler
- Price spike/bozuk veri tespit edildiğinde anında gösterir
- Renkli output ile kolay takip
- CTRL+C ile durur

**Çıktı örneği**:
```
⚠️  Bozuk fiyat filtrelendi: BTCUSDT | mevcut=0.00 | son_geçerli=103000.00 | kapanmış=False | zaman=1699...
⚠️  OHLC ilişkisi ihlali BTCUSDT: O=100000.00 H=100100.00 L=99900.00 C=100500.00
```

### 2. Historical Analysis (Geçmiş Analizi)

```bash
cd /root/trading
./analyze_price_spikes.sh
```

**Ne yapar?**
- Son 24 saatteki tüm price spike olaylarını analiz eder
- Kategorilere göre istatistik verir
- Filtreleme oranını hesaplar

**Çıktı**:
```
1️⃣ Toplam Bozuk Fiyat (0.00): 0
2️⃣ OHLC İlişkisi İhlali: 0
3️⃣ Price Spike (>5%): 0
4️⃣ Eksik Kline Yapısı: 0
5️⃣ Geçersiz Close Price: 0

📈 TOPLAM: 0 filtrelenen mesaj
Filtreleme Oranı: 0% (Mükemmel!)
```

### 3. Manuel Log Arama

#### Son 1 saatteki uyarılar
```bash
journalctl --since "1 hour ago" | grep -E "Bozuk fiyat|OHLC|Price spike" | tail -20
```

#### Spesifik servis logları
```bash
# Dummy feed (WebSocket)
journalctl -u trading-dummy-feed.service --since "1 hour ago" | grep -i "bozuk\|spike"

# 1m enriched feed
journalctl -u trading-enriched-1m.service --since "1 hour ago" | grep -i "bozuk\|spike"

# Orchestrator
journalctl -u trading-orchestrator.service --since "1 hour ago" | grep -i "bozuk\|spike"
```

#### Debug seviyesi loglar
```bash
# Tüm validasyon detaylarını görmek için
journalctl --since "10 minutes ago" | grep -E "Eksik kline|Geçersiz close|Price spike.*değişim"
```

## 📈 Beklenen Davranış

### Normal Durum (Şu Anki Durum)
- ✅ Filtreleme oranı: **0%**
- ✅ Tüm mesajlar geçerli
- ✅ Sistem kesintisiz çalışıyor

### Binance Sorunları Olduğunda
- ⚠️ Filtreleme oranı: **<1%** (Normal)
- 🔴 Filtreleme oranı: **>1%** (Dikkat!)
- 🚨 Filtreleme oranı: **>5%** (Ciddi sorun - Binance API'de problem var)

## 🔔 Alarm Eşikleri

| Oran | Durum | Aksiyon |
|------|-------|---------|
| 0% | ✅ Mükemmel | Aksiyon gerekli değil |
| <0.1% | ✅ Normal | İzlemeye devam et |
| 0.1-1% | ⚠️ Dikkat | Logları incele, pattern var mı? |
| >1% | 🔴 Sorun | Binance API durumunu kontrol et |
| >5% | 🚨 Kritik | Sistem durdurulmalı mı? |

## 📝 Log Mesajları ve Anlamları

### 1. Bozuk Fiyat (0.00)
```
⚠️ Bozuk fiyat filtrelendi: BTCUSDT | mevcut=0.00 | son_geçerli=103000.00 | kapanmış=False | zaman=1699000000000
```
**Anlam**: Binance 0.00 fiyat gönderdi, sistem reddetti  
**Sebep**: Network glitch, incomplete kline, WebSocket reconnection  
**Etki**: Yok - sistem kötü veriyi filtreliyor

### 2. OHLC İlişkisi İhlali
```
OHLC ilişkisi ihlali BTCUSDT: O=100000.00 H=100100.00 L=99900.00 C=100500.00
```
**Anlam**: Close price (100500) > High price (100100) - mantıksal hata  
**Sebep**: Binance veri bozukluğu  
**Etki**: Yok - sistem kötü veriyi filtreliyor

### 3. Price Spike (>5%)
```
Price spike BTCUSDT: 8.50% değişim (limit: 5.00%)
```
**Anlam**: Fiyat son geçerli fiyattan %8.5 değişti (çok fazla)  
**Sebep**: Binance veri hatası veya gerçek flash crash  
**Etki**: Yok - sistem aşırı değişimi filtreliyor

### 4. Eksik Kline Yapısı
```
Eksik kline yapısı, atlanıyor: ['c']
```
**Anlam**: Kline'da sadece 'c' (close) field'ı var, o/h/l eksik  
**Sebep**: Incomplete WebSocket message  
**Etki**: Yok - sistem eksik veriyi filtreliyor

### 5. Geçersiz Close Price
```
Geçersiz close price: -100.00 (sıfır veya negatif)
```
**Anlam**: Negatif veya sıfır fiyat  
**Sebep**: Veri bozukluğu  
**Etki**: Yok - sistem geçersiz veriyi filtreliyor

## 🔧 Troubleshooting

### Problem: Çok fazla filtreleme (>1%)
**Kontroller**:
1. Binance API durumu: https://www.binance.com/en/support/announcement/system
2. Network bağlantısı stabil mi?
3. WebSocket reconnection sıklığı normal mi?

**Çözüm**:
- Binance API sorunuysa bekleyin
- Network sorunu varsa sunucu bağlantısını kontrol edin
- Çok sık reconnection varsa WebSocket timeout ayarlarını gözden geçirin

### Problem: Hiç log görünmüyor
**Kontroller**:
1. Servisler çalışıyor mu? `systemctl status trading-*`
2. Log seviyesi doğru mu? (WARNING ve üzeri)
3. Son 24 saatte gerçekten sorun olmamış olabilir (şu anki durum)

## 📊 İstatistik Toplama (Opsiyonel)

### Günlük Rapor
```bash
# Cronjob ekle: Her gün 23:59'da analiz yap
crontab -e

# Ekle:
59 23 * * * /root/trading/analyze_price_spikes.sh >> /root/trading/logs/daily_spike_report.log
```

### Haftalık Özet
```bash
# Son 7 günün özetini al
journalctl --since "7 days ago" | grep -E "Bozuk fiyat|OHLC|Price spike" | wc -l
```

## ✅ Mevcut Durum Özeti

**Tarih**: 2025-11-05  
**Durum**: ✅ Sistem mükemmel çalışıyor  
**Son 24 saat**: 0 filtrelenen mesaj  
**Filtreleme oranı**: 0%  
**Aksiyon gerekli**: Yok

**Sonuç**: Sisteminiz şu anda ideal durumda. Price spike filtresi hazır bekliyor, ama Binance temiz veri gönderiyor. İyi haber! 🎉

## 📚 İlgili Dosyalar

- `app/data_feeds/binance_ws.py` - Ana WebSocket client (filtreleme mantığı)
- `test_price_spike_logging.py` - Test suite
- `monitor_price_spikes.sh` - Real-time monitoring script
- `analyze_price_spikes.sh` - Historical analysis script
- `PRICE_SPIKE_FILTER_IMPROVEMENT.md` - Teknik dokümantasyon
- `PRICE_SPIKE_MONITORING.md` - Bu dosya (kullanım kılavuzu)

## 🚀 Quick Start

```bash
# 1. Hızlı durum kontrolü
cd /root/trading && ./analyze_price_spikes.sh

# 2. Canlı izleme başlat (CTRL+C ile dur)
./monitor_price_spikes.sh

# 3. Manuel log araması
journalctl --since "1 hour ago" | grep -i "bozuk\|spike"
```

**Sorun mu var?** Yukarıdaki komutları çalıştırın ve çıktıyı inceleyin!
