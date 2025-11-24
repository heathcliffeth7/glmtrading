# Active Learning Retrain Daemon - Kurulum Tamamlandı ✅

## Servis Bilgileri

### Servis Adı
`trading-active-learning.service`

### Durum
```bash
sudo systemctl status trading-active-learning
```

### Kontrol Komutları
```bash
# Başlat
sudo systemctl start trading-active-learning

# Durdur
sudo systemctl stop trading-active-learning

# Restart
sudo systemctl restart trading-active-learning

# Logları görüntüle
journalctl -u trading-active-learning -f

# Son 100 log
journalctl -u trading-active-learning -n 100
```

## Nasıl Çalışır?

### 1. Sürekli İzleme
- Her **30 dakikada** bir kontrol eder
- `prediction_logs` tablosundaki yeni tahminleri sayar

### 2. Retrain Koşulları
Model **aşağıdaki durumlardan biri** gerçekleştiğinde yeniden eğitilir:

#### A) İlk Eğitim
- Minimum **20 prediction** gerçek sonuçlarla toplanmış olmalı
- İlk çalıştığında otomatik train eder

#### B) Yeni Veri Biriktikçe
- Son retrain'den beri **10+ yeni prediction** toplanmış olmalı
- Minimum 1 saat bekleme süresi var (çok sık retrain olmasın)

#### C) Günlük Planlı Retrain
- Her gün **02:00 UTC**'de otomatik retrain
- Son retrain'den 12+ saat geçmiş olmalı

### 3. Active Learning Özellikleri

#### Veri Kaynağı
- `prediction_logs` tablosundan **gerçek sonuçlar**
- `actual_direction` (BUY/SELL/HOLD)
- `actual_price_change_pct` (gerçek fiyat değişimi)

#### GLM Feedback Weighting
- **GLM doğru demiş**: 2.0x ağırlık
- **GLM yanlış demiş**: 1.5x ağırlık (hatalardan öğren)
- **GLM feedback yok**: 1.0x ağırlık

#### Confidence Weighting
- **Yüksek güven (>70%)**: 1.2x ağırlık
- Normal güven: 1.0x ağırlık

### 4. Model Backup
Her retrain'de:
- Eski model `derivatives_backup_YYYYMMDD_HHMMSS.joblib` olarak yedeklenir
- Yeni model `models/derivatives.joblib` olarak kaydedilir

## Konfigürasyon

Servis dosyası: `/etc/systemd/system/trading-active-learning.service`

### Parametreler
```bash
--check-interval 30              # 30 dakikada bir kontrol
--min-new-predictions 10         # Minimum 10 yeni prediction
--min-total-predictions 20       # İlk eğitim için minimum 20 prediction
```

### Değiştirmek İçin
1. Servisi düzenle:
```bash
sudo systemctl edit --full trading-active-learning
```

2. Parametreleri değiştir (örn: `--check-interval 60` için 1 saatte bir)

3. Reload ve restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart trading-active-learning
```

## Resource Limits
- **Memory**: 512MB limit
- **CPU**: 50% quota (tek core'un yarısı)

## Monitoring

### Status Check
```bash
# Servis çalışıyor mu?
systemctl is-active trading-active-learning

# Son retrain ne zaman?
journalctl -u trading-active-learning | grep "Active Learning Retrain Complete" | tail -1

# Kaç prediction var?
sqlite3 /root/trading/portfolio.db "SELECT COUNT(*) FROM prediction_logs WHERE result_collected=1"
```

### Canlı Log İzleme
```bash
journalctl -u trading-active-learning -f | grep -E "(Status check|Retrain|predictions=)"
```

## Diğer Trading Servisleri

### Mevcut Servisler
1. **trading-orchestrator**: Ana trading loop (5 dakikada bir)
2. **trading-enriched-feed**: TwelveData + Binance veri toplama (5 dakikada bir)
3. **trading-active-learning**: Model retrain (30 dakikada kontrol)
4. **trading-feature-sync**: TwelveData feature sync (opsiyonel, şu an inactive)

### Tüm Servisleri Görüntüle
```bash
systemctl list-units --all | grep trading
```

### Tüm Servisleri Restart
```bash
sudo systemctl restart trading-orchestrator
sudo systemctl restart trading-enriched-feed
sudo systemctl restart trading-active-learning
```

## Örnek Log Çıktısı

```
Active Learning Daemon Started
Configuration:
  Check interval: 30 minutes
  Min new predictions: 10
  Min total predictions: 20

Status check | predictions=21 | should_retrain=True | reason=Initial training with 21 predictions
🔄 Triggering model retrain...
Starting Active Learning Retrain...
Fetched 21 predictions with ACTUAL results
Label distribution: BUY=3 SELL=5
Training with 8 REAL predictions, 25 features
GLM weighting: 0 correct (2.0x), 8 wrong (1.5x)
Confidence weighting: 8 high confidence (1.2x)
Active Learning Model Performance:
Training samples: 6
Test samples: 2
Accuracy: 50.00%
📦 Old model backed up
✅ Active Learning model saved
✅ Retrain successful
Next check in 30 minutes...
```

## Troubleshooting

### Servis Başlamıyor
```bash
# Detaylı log
journalctl -u trading-active-learning -xe

# Manuel test
cd /root/trading
.venv/bin/python scripts/active_learning_daemon.py
```

### Retrain Çalışmıyor
```bash
# Prediction sayısını kontrol et
sqlite3 portfolio.db "SELECT COUNT(*) FROM prediction_logs WHERE result_collected=1"

# Manuel retrain
cd /root/trading
.venv/bin/python scripts/retrain_derivatives_active_learning.py
```

### Memory/CPU Problemi
```bash
# Resource kullanımını gör
systemctl status trading-active-learning

# Limitleri artır
sudo systemctl edit --full trading-active-learning
# MemoryLimit=1G, CPUQuota=100% yap
```

## Model Comparison

### Eski Yöntem (Sadece Fiyat Hareketleri)
- Script: `scripts/retrain_derivatives_24features.py`
- Veri: InfluxDB'den OHLCV
- Label: `future_return > 0.1%`
- Feedback: Yok

### Yeni Yöntem (Active Learning) ✅
- Script: `scripts/retrain_derivatives_active_learning.py`
- Daemon: `scripts/active_learning_daemon.py`
- Veri: `prediction_logs` (gerçek sonuçlar)
- Label: `actual_direction` (gerçek)
- Feedback: GLM + confidence weighted
- **Otomatik**: Arka planda sürekli çalışır

---

**Son Güncelleme**: 2025-10-21 10:01 UTC  
**Durum**: ✅ Aktif ve Çalışıyor
