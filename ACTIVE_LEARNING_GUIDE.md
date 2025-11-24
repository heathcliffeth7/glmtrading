# Active Learning Pipeline - Kullanım Kılavuzu

## 🎯 Genel Bakış

Active Learning sistemi, GLM 4.6'nın feedback'leri ile derivatives model'ini sürekli geliştirir.

## 📊 Sistem Bileşenleri

### 1. Prediction Logging
**Dosya:** `app/agents/derivatives.py`, `app/agents/feedback/logger.py`

Her derivatives agent tahmini otomatik olarak kaydedilir:
- Features (LSR, OI, FR, EMA, RSI)
- Model skoru ve yön
- Zaman damgası

```python
# Otomatik çalışır - kod değişikliği gerekmez
signal = derivatives_agent.generate_signal()
# → Prediction DB'ye kaydedilir
```

### 2. Feedback Collector
**Dosya:** `app/agents/feedback/collector.py`

**Ne yapar:**
- Her 5 dakikada bir çalışır
- 15 dakika önce yapılmış tahminleri bulur
- InfluxDB'den o zamandaki gerçek fiyatı çeker
- Actual result hesaplar (fiyat değişimi %)
- GLM'e sorar: "Tahmin doğru muydu?"
- Feedback'i DB'ye kaydeder

**GLM Feedback Soruları:**
- Model doğru tahmin etti mi?
- Hangi feature'a daha fazla dikkat etmeliydi?
- En önemli ipucu neydi?

### 3. Active Learning Retrainer
**Dosya:** `app/agents/feedback/retrainer.py`

**Ne yapar:**
- Son 7 günün feedback'lerini toplar
- GLM'in "yanlış" dediği örneklere 3x ağırlık verir
- Yeni model eğitir
- Eski vs yeni model karşılaştırır
- Daha iyiyse kaydeder + arşivler
- Telegram'a bildirim gönderir

**Weighted Training:**
```
GLM "yanlış" dedi → 3x ağırlık
Model yanlış tahmin etti → 2x ağırlık
Doğru tahmin → 1x ağırlık
```

### 4. Daily Retraining Scheduler
**Dosya:** `app/orchestrator/runtime.py`

Her gün 02:00 UTC'de otomatik retraining:
```python
runner = AutomatedRunner(
    enable_feedback_collector=True,
    enable_daily_retraining=True,
    retraining_hour=2,  # UTC
)
```

## 🚀 Kullanım

### Production Deployment

```bash
# Normal orchestrator başlatma
python -m app.orchestrator.runtime

# Active Learning otomatik çalışır:
# - Feedback Collector: Her 5dk
# - Retraining: Her gün 02:00 UTC
```

### Manuel Retraining

```python
from app.agents.feedback.retrainer import ActiveLearningRetrainer

retrainer = ActiveLearningRetrainer()

# Dry run (model kaydetmez)
result = retrainer.retrain(days=7, dry_run=True)

# Gerçek retraining
result = retrainer.retrain(days=7, dry_run=False)

print(f"Old accuracy: {result['old_accuracy']:.2%}")
print(f"New accuracy: {result['new_accuracy']:.2%}")
print(f"Improvement: {result['improvement']:+.2%}")
```

### Feedback Report

```python
from app.agents.feedback.retrainer import ActiveLearningRetrainer

retrainer = ActiveLearningRetrainer()
report = retrainer.generate_report(days=7)

print(f"Total samples: {report['total_samples']}")
print(f"GLM feedback rate: {report['glm_feedback_rate']:.1%}")
print(f"Model accuracy: {report['model_accuracy']:.1%}")
print(f"Ready for retrain: {report['ready_for_retrain']}")
print(f"Top features: {report['important_features']}")
```

## 📈 Monitoring

### Database Queries

```python
from sqlalchemy.orm import Session
from app.executor.ledger import engine, PredictionLog

with Session(engine) as session:
    # Total predictions
    total = session.query(PredictionLog).count()
    
    # Pending feedback collection
    pending = session.query(PredictionLog).filter_by(result_collected=False).count()
    
    # GLM feedbacks
    glm_feedbacks = session.query(PredictionLog).filter_by(glm_feedback_collected=True).count()
    
    # Recent errors (GLM flagged)
    errors = (
        session.query(PredictionLog)
        .filter_by(glm_correct=False)
        .order_by(PredictionLog.timestamp.desc())
        .limit(10)
        .all()
    )
```

### Telegram Notifications

Sistem otomatik bildirim gönderir:

**Retraining Success:**
```
🔄 Model Retrained (Active Learning)

📊 Training Data
Total Samples: 86
Error-Weighted: 58 (67.4%)
GLM Feedbacks: 56
GLM Flagged Errors: 41

🎯 Performance
Old Accuracy: 75.58%
New Accuracy: 91.86%
🟢 Improvement: +16.28%

✅ Status
New model saved and deployed
Old model archived
```

**GLM Failure:**
```
🚨 GLM API HATASI

Hata Detayı: Rate limit exceeded
⚠️ Karar: HOLD (Güvenli mod)
```

## 🔧 Konfigürasyon

### Feedback Collector Ayarları

```python
FeedbackCollector(
    check_interval_minutes=5,      # Her kaç dakikada kontrol edilsin
    result_after_minutes=15,        # Kaç dakika sonraki fiyatı al
    batch_size=20,                  # Her seferde kaç prediction işlensin
    enable_glm_feedback=True,       # GLM feedback aktif mi
)
```

### Retrainer Ayarları

```python
ActiveLearningRetrainer(
    model_path="models/derivatives.joblib",
    min_samples=50,                  # Minimum feedback sayısı
    error_weight_multiplier=3.0,     # GLM hataları için ağırlık
)
```

### Orchestrator Ayarları

```python
AutomatedRunner(
    enable_feedback_collector=True,   # Feedback collector aktif
    enable_daily_retraining=True,     # Günlük retraining aktif
    retraining_hour=2,                # UTC saat
)
```

## 📊 Performance Metrics

### Örnek Sonuçlar (100 feedback örneği):

| Metric | Değer |
|--------|-------|
| Eski Model Accuracy | 75.58% |
| Yeni Model Accuracy | 91.86% |
| İyileşme | +16.28% |
| GLM Feedback Rate | 65.1% |
| GLM Flagged Errors | 41 |
| Error-Weighted Samples | 58 (67.4%) |

### Feature Importance (GLM öğrenimi sonrası):

| Feature | Importance |
|---------|-----------|
| funding_rate | 0.226 |
| long_short_ratio | 0.202 |
| ema_50 | 0.167 |
| ema_20 | 0.145 |
| open_interest | 0.138 |
| rsi_14 | 0.122 |

**GLM'in en çok bahsettiği feature:** funding_rate (36 mentions)

## ⚠️ Troubleshooting

### Problem: Feedback toplanmıyor

**Çözüm 1:** InfluxDB'de veri var mı?
```python
from app.utils.influx import query_price_at_time
from datetime import datetime, timedelta

price = query_price_at_time(
    symbol="BTCUSDT",
    target_time=datetime.utcnow() - timedelta(minutes=15),
)
print(price)  # None değilse veri var
```

**Çözüm 2:** Feedback Collector çalışıyor mu?
```bash
# Log'larda şunu arayın:
grep "Feedback Collection Cycle" orchestrator.log
```

### Problem: GLM rate limit

**Belirti:** `429 Too Many Requests`

**Çözüm:** GLM feedback'i geçici devre dışı bırak:
```python
FeedbackCollector(enable_glm_feedback=False)
```

### Problem: Retraining başarısız

**Hata:** `Insufficient data`

**Çözüm:** Minimum sample sayısını azalt:
```python
ActiveLearningRetrainer(min_samples=30)  # Default: 50
```

## 🔍 Debug Mode

```python
import logging
logging.getLogger('app.agents.feedback').setLevel(logging.DEBUG)
logging.getLogger('app.agents.feedback.collector').setLevel(logging.DEBUG)
logging.getLogger('app.agents.feedback.retrainer').setLevel(logging.DEBUG)

# Şimdi daha detaylı log'lar göreceksiniz
```

## 📝 Best Practices

1. **İlk 7 gün:** Feedback toplanır, retraining yapılmaz (min 50 sample gerekli)
2. **1-2 hafta sonra:** İlk retraining, model accuracy +5-10% artar
3. **1 ay sonra:** Sistem stabil hale gelir, aylık +2-3% improvement
4. **GLM quota yönetimi:** Günlük 500-1000 GLM request (normal kullanım)
5. **Model arşivleme:** Her retraining'de eski model `models/archive/` altına kaydedilir

## 🚀 Production Checklist

- [ ] InfluxDB veri akışı çalışıyor
- [ ] Feedback Collector başlatıldı
- [ ] Daily retraining scheduler aktif
- [ ] Telegram bildirimleri çalışıyor
- [ ] Model dosyası mevcut (`models/derivatives.joblib`)
- [ ] Archive dizini oluşturuldu (`models/archive/`)
- [ ] Log seviyesi ayarlandı (INFO veya DEBUG)
- [ ] GLM API key geçerli
- [ ] Database backup stratejisi mevcut

## 📚 İleri Seviye

### Custom GLM Prompts

```python
# app/agents/feedback/collector.py'da prompt'u özelleştir

def _build_glm_feedback_prompt(self, pred, actual):
    # Kendi prompt tasarımınızı ekleyin
    # Örnek: Daha fazla kontekst, farklı sorular
    pass
```

### Multi-Model Retraining

```python
# Birden fazla model için
models = ["derivatives", "short_term", "long_term"]

for model_name in models:
    retrainer = ActiveLearningRetrainer(
        model_path=f"models/{model_name}.joblib"
    )
    result = retrainer.retrain(days=7)
```

### A/B Testing

```python
# İki model karşılaştırması
old_model = joblib.load("models/derivatives.joblib")
new_model = joblib.load("models/archive/derivatives_20251020.joblib")

# Aynı veriler üzerinde test et
# Hangisi daha iyi performans gösteriyor?
```

---

## 📞 Destek

Sorularınız için:
- Logs: `orchestrator.log`
- Database: `portfolio.db`
- Telegram: Trading bot kanalı
