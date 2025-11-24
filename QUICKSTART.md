# Trading Bot Quickstart - Historical Data Import

## Problem Çözümü

Bot'unuz şu anda **yeterli historical data olmadan** çalışıyor, bu yüzden:
- %22.5 güven değeri sürekli aynı kalıyor
- "Fallback BUY" sinyalleri alıyorsunuz
- Kaldıraç her zaman 20.0x

## Çözüm: TwelveData'dan Historical Import

### 1. İlk Kurulum: Historical Data Import

Bot'u durdurun ve historical data import edin:

```bash
# Bot'u durdur
sudo systemctl stop trading-orchestrator

# Historical data import (son 288 bar = ~24 saat @ 5min)
cd /root/trading
.venv/bin/python scripts/import_historical_data.py --outputsize 288

# Başarılı olursa şöyle bir çıktı görmelisiniz:
# "Import completed: 288 successful, 0 errors out of 288 total bars"
```

### 2. Feature Sync Servisini Başlat

Historical data import başarılı olduysa, düzenli sync için servisi başlatın:

```bash
# Service dosyalarını kopyala ve enable et
cd /root/trading
bash scripts/setup_services.sh

# Feature sync servisini başlat
sudo systemctl start trading-feature-sync

# Log kontrol et
journalctl -u trading-feature-sync -f
```

### 3. Trading Orchestrator'ı Yeniden Başlat

Artık yeterli data var, trading bot'u başlatabilirsiniz:

```bash
sudo systemctl start trading-orchestrator

# Log kontrol
journalctl -u trading-orchestrator -f
```

## Beklenen Sonuçlar

Import'tan sonra:
- ✅ EMA_20 ve EMA_50 farklı değerler olacak
- ✅ RSI_14 gerçek piyasa durumunu yansıtacak
- ✅ Güven değeri %22.5'ten farklı olacak
- ✅ "Fallback BUY" yerine gerçek model tahminleri gelecek
- ✅ Kaldıraç dinamik olarak değişecek

## Servis Durumlarını Kontrol

```bash
# Her iki servisin durumu
sudo systemctl status trading-feature-sync
sudo systemctl status trading-orchestrator

# Logları izle
journalctl -u trading-feature-sync -f
journalctl -u trading-orchestrator -f
```

## Troubleshooting

### Import Çok Yavaş / API Limiti

TwelveData free tier'da günlük 800 request limiti var. 12 API key'iniz var, yani günde ~9600 request.

Eğer limit sorunu yaşarsanız:

```bash
# Daha az bar import edin
.venv/bin/python scripts/import_historical_data.py --outputsize 100
```

### InfluxDB Bağlantı Hatası

```bash
# InfluxDB çalışıyor mu?
sudo systemctl status influxdb

# Test et
cd /root/trading
.venv/bin/python -c "from app.utils.influx import check_health; print(check_health())"
```

### Feature Sync Çalışmıyor

```bash
# Hata loglarına bak
journalctl -u trading-feature-sync -n 100 --no-pager

# Manual test
cd /root/trading
.venv/bin/python scripts/sync_features.py --poll-interval 10
```

## Manuel Veri Kontrolü

Import'tan sonra verileri kontrol edin:

```bash
cd /root/trading
.venv/bin/python debug_check.py
```

Şunu görmelisiniz:
```
Close: 67123.45
EMA 20: 67089.23  # ✅ Close'dan farklı
EMA 50: 67001.87  # ✅ EMA_20'den farklı
RSI 14: 58.34     # ✅ 50.0'dan farklı
```

## Önerilen Konfigürasyon

1. **Feature Sync**: Her 60 saniyede bir TwelveData'dan yeni bar çeker
2. **Trading Orchestrator**: Her 5 dakikada bir karar verir (cycle_seconds=300)
3. **Historical Window**: 288 bar = 24 saat @ 5min interval

## Gelişmiş: Birden Fazla Interval

Eğer 1m ve 5min interval'leri birlikte kullanmak isterseniz:

```bash
# 1m için import
.venv/bin/python scripts/import_historical_data.py --interval 1min --outputsize 1440

# 1m için sync servisi (yeni bir service dosyası oluşturun)
# /etc/systemd/system/trading-feature-sync-1m.service
```

## Sonraki Adımlar

1. ✅ Historical data import
2. ✅ Feature sync servisi çalışıyor
3. ⏳ ML model train (opsiyonel): `scripts/train_derivatives_pipeline.py`
4. ⏳ Database initialize: Portfolio tracking için

---

## Hızlı Komutlar

```bash
# Hepsi bir arada: Import + Start
cd /root/trading && \
sudo systemctl stop trading-orchestrator && \
.venv/bin/python scripts/import_historical_data.py --outputsize 288 && \
bash scripts/setup_services.sh && \
sudo systemctl start trading-feature-sync && \
sleep 5 && \
sudo systemctl start trading-orchestrator

# Status check
sudo systemctl status trading-feature-sync trading-orchestrator

# Logs
journalctl -u trading-feature-sync -u trading-orchestrator -f
```
