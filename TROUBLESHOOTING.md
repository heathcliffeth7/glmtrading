# Troubleshooting Guide

## 🚨 GLM API Timeout

### Belirti:
```
ERROR | GLM API read timeout (waited 110s)
🚨 GLM API HATASI - Karar: HOLD (Güvenli mod)
```

### Nedenleri:
1. GLM API yavaş yanıt veriyor
2. Network problemi
3. GLM servis yoğunluğu

### Çözüm:
```python
# GLM zaten 110s timeout'a sahip
# Timeout arttırılabilir ama önerilmez

# Alternatif: GLM skip mod
# .env dosyasında:
# GLM_ENABLED=false  # Geçici olarak devre dışı bırak
```

### Sistem Davranışı:
- GLM timeout olursa → **HOLD kararı** (güvenli mod)
- Trade açılmaz, sermaye korunur
- 5dk sonraki cycle'da tekrar dener

---

## 📊 Sürekli Retraining Logları

### Belirti:
```
Telegram'da sürekli retraining log'ları geliyor
```

### Neden:
Test script'leri production'da çalışıyor veya manuel test yapılıyor

### Çözüm:
**Tüm test script'lerinde Telegram devre dışı:**
```bash
# Artık test script'leri Telegram'a log göndermez
python scripts/test_retrainer.py  # ✅ Telegram disabled
```

**Production retraining:**
```python
# Orchestrator'da retraining devre dışı bırakılabilir:
AutomatedRunner(
    enable_daily_retraining=False,  # Henüz çok erken
)
```

---

## 🔄 HOLD Sinyalleri

### Belirti:
```
Sürekli HOLD sinyali, hiç trade açılmıyor
```

### Olası Nedenler:
1. **Agent güvensiz** - confidence=0.00
2. **Veri eksikliği** - InfluxDB'de yeterli veri yok
3. **Model tahmin edemiyor** - Feature'lar uyumsuz

### Çözümler:

**1. Data Feed Kontrolü:**
```bash
# Data feed çalışıyor mu?
systemctl status trading-data-feed

# Log kontrolü
tail -f /var/log/trading/data_feed.log
```

**2. InfluxDB Kontrolü:**
```python
# Feature'lar var mı?
python -c "
from app.utils.influx import query_latest_snapshot
data = query_latest_snapshot('features_5min', 'BTCUSDT', '5min')
print(data)
"
```

**3. Model Kontrolü:**
```bash
# Model dosyası var mı?
ls -lh models/derivatives.joblib

# Model test et
python scripts/test_twelve_data_integration.py
```

---

## 💾 Database Sorunları

### Migration Hataları:
```bash
# Twelve Data kolonları ekle
python scripts/migrate_db_twelve_data.py
```

### Database backup:
```bash
cp portfolio.db portfolio.db.backup
```

### Database reset (DIKKAT: Tüm veri silinir):
```bash
rm portfolio.db
python -c "from app.executor.ledger import engine, Base; Base.metadata.create_all(engine)"
```

---

## 🔍 Log Analizi

### Production Log'ları:
```bash
# Orchestrator log
journalctl -u trading-orchestrator -f

# Son hataları göster
journalctl -u trading-orchestrator -p err --since "1 hour ago"

# GLM timeout'ları say
journalctl -u trading-orchestrator | grep "GLM API timeout" | wc -l
```

### Test Log'ları:
```bash
# Test log'ları dosyada kalır, Telegram'a gitmez
cat logs/test_*.log
```

---

## 📈 Performance Check

### System Status:
```bash
python scripts/check_system_status.py
```

**Beklenen Çıktı:**
```
Running Processes:
  - orchestrator ✅
  - data_feed ✅

Database:
  - Predictions: 200+
  - GLM feedbacks: 70%+
  - Trades: 0 (normal if HOLD)
```

### Prediction Success Rate:
```python
from sqlalchemy.orm import Session
from app.executor.ledger import engine, PredictionLog

with Session(engine) as session:
    total = session.query(PredictionLog).filter_by(result_collected=True).count()
    correct = session.query(PredictionLog).filter(
        PredictionLog.result_collected == True,
        PredictionLog.predicted_direction == PredictionLog.actual_direction
    ).count()
    
    print(f"Success Rate: {correct}/{total} = {(correct/total)*100:.1f}%")
```

---

## 🛑 Emergency Stop

### Tüm servisleri durdur:
```bash
systemctl stop trading-orchestrator
systemctl stop trading-data-feed
```

### Sadece trading durdur (data feed devam):
```bash
# Orchestrator'ı durdur
systemctl stop trading-orchestrator

# Data feed devam eder, veri toplamaya devam eder
```

---

## 🔧 Quick Fixes

### GLM sürekli timeout:
```bash
# Geçici çözüm: GLM fallback kullan
# RiskManager otomatik fallback yapıyor, sorun yok
```

### Twelve Data quota aşımı:
```bash
# Data feed log'una bak
journalctl -u trading-data-feed | grep "quota"

# Quota durumu
python scripts/check_api_usage.sh
```

### Model accuracy düşük:
```bash
# Yeniden eğit
python -c "
from app.agents.feedback.retrainer import ActiveLearningRetrainer
retrainer = ActiveLearningRetrainer()
result = retrainer.retrain(days=7, dry_run=False)
print(f'Improvement: {result[\"improvement\"]*100:.2f}%')
"
```

---

## 📞 Support Checklist

Sorun yaşadığında şunları kontrol et:

- [ ] Orchestrator çalışıyor mu? (`systemctl status`)
- [ ] Data feed çalışıyor mu?
- [ ] InfluxDB'de veri var mı?
- [ ] Model dosyası var mı? (`models/derivatives.joblib`)
- [ ] GLM API key geçerli mi?
- [ ] Twelve Data API key'leri geçerli mi?
- [ ] Disk dolmuş mu? (`df -h`)
- [ ] Memory yeterli mi? (`free -h`)
- [ ] Son 10 hata ne? (`journalctl -p err -n 10`)

---

## 🎯 Normal Davranış

### Beklenen:
- Her 5dk bir orchestrator cycle
- HOLD kararları normal (güvenli trade yok)
- GLM bazen timeout (fallback çalışır)
- Twelve Data quota uyarıları (rotation var)
- 0 trade (henüz güvenli sinyal yok)

### Anormal:
- Hiç log gelmiyor
- Sürekli hata mesajları
- Database lock
- Memory leak (sürekli artış)
- Disk dolu

---

## 🚀 Production Best Practices

1. **İlk 7 gün**: Sadece feedback topla, trade açma
2. **7-14 gün**: İlk retraining, model doğrula
3. **14+ gün**: Daily retraining aktif
4. **Paper trading**: En az 30 gün test
5. **Real trading**: Önce küçük pozisyonlarla

---

**Sorun devam ederse:** Log'ları ve system status'u kaydet, support'a ilet.
