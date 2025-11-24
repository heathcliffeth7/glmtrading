## Çoklu Ajanlı AI Trading Sistemi Planı

### 1. Amaç ve Kapsam
- **Hedef:** Binance spot/türev verileri, Twelve Data indikatörleri, QLib model pipeline’ı ve GLM 4.6 tabanlı risk yönetimi ile çok ajanlı bir paper-trading platformu işletmek.
- **Yaklaşım:** Spot/türev/piyasa göstergelerini ayrı ajanlara dağıtıp kararları risk yöneticisinde birleştirmek; veri toplama → özellik → ajan → risk → kısıtlı yürütme zinciri boyunca asenkron servisler ve paylaşılan veri mağazaları kullanmak.
- **Sınırlar:** Zincir üstü, haber, sosyal medya ve gerçek emir gönderimi kapsam dışıdır; odak teknik verilerde ve paper trading modundadır.

### 2. Mevcut Sistem Özeti
#### 2.1 Veri Katmanı
- Binance spot 1m kline akışı `python-binance` ile toplanıyor, Redis stream kanalına (`stream:binance:kline`) yayınlanıyor; orchestrator (`app/data_feeds/service.py`) reconnect ve health mesajı üretiyor.
- Binance Futures REST poller (long/short ratio, open interest, funding rate) `stream:binance:futures` kanalına yayın yapıyor.
- Twelve Data indikatör toplama servisi 11 API anahtarını kaydırmalı quota takipçisiyle yönetiyor; aşımda `stream:health` kanalına `quota_exceeded` uyarısı gönderiyor.
- Tüm yayınlar Redis JSON formatında `{symbol, interval?, payload, timestamp}` şemasına uyuyor.

#### 2.2 Depolama ve Altyapı
- InfluxDB gerçek zamanlı özellikler için kullanılıyor (`features_{interval}` ölçümleri, interval tag’leri ile).
- QLib Parquet deposu (`qlib_data/`) `qlib_converter` aracılığıyla güncelleniyor; duplicate temizliği mevcut.
- SQLite tabanlı ledger (`portfolio.db`) trade ve portföy izlemeye hizmet ediyor; Redis ile health logları merkezi tutuluyor.
- `.env`/Pydantic settings; Telegram bot tokeni ve kanal id’si dahil tüm secret’lar environment üzerinden yönetiliyor.

#### 2.3 Özellik Mühendisliği
- `FeatureWorker` Redis akışını tüketip ta-lib hesaplamaları (EMA20/50, RSI14, MACD, ATR, VWAP vb.) yapıyor, sonuçları hem Influx’e yazıyor hem de QLib’e append ediyor.
- Negatif/şüpheli değerlerde `stream:health` kanalına `data_error` alarmı düşüyor.
- Worker orchestrator (`start_feature_workers`) çoklu sembol desteği sunuyor.

#### 2.4 Ajan Katmanı
- QLib workflow scriptleri (short/long) LightGBM modellerini eğitip `models/*.joblib` olarak kaydediyor; metadata JSON dosyaları oluşturuluyor.
- Türev ajan logistic regression fallback’i içeriyor.
- Ajan sınıfları Influx’ten gerçek özellikleri okuyup `AgentSignal` döndürüyor.

#### 2.5 Risk ve Yürütme
- GLMClient JSON formatlı cevap zorunluluğu, kaydırmalı rate limiter (window=5 saat, 1000 istek) ve hata kaydı içeriyor.
- RiskManager fallback mantığında ağırlıklı güven hesaplama, zıt sinyal eşiği (10%) ve HOLD kararları mevcut.
- Executor guardrail’leri (max pozisyon, kısmi daily loss şablonu), SQLite ledger kaydı ve paper trading modu sağlıyor.

#### 2.6 İzleme ve Operasyon
- FastAPI `/metrics` endpoint’i Prometheus uyumlu; ws reconnect, fallback sayacı, queue lag parametreleri mevcut.
- `infra/prometheus.yml` Alertmanager Telegram webhook’una yönlendiriliyor; Grafana için JSON dashboard şablonu hazır.
- Streamlit dashboard Influx ve ledger verilerini gösteriyor (fiyat grafiği, PnL, trade tablosu, risk metrik notları).
- CI: GitHub Actions pipeline (Poetry install, black/isort/mypy, pytest --cov, docker build) + coverage badge workflow’u tanımlı.
- Docker-compose test konfigürasyonu Redis + Influx + dummy feed içeriyor; dummy feed hem Redis’e yayın hem Influx’e veri yazabiliyor.

### 3. Güncel Test Stratejisi
- **Unit Tests:** Feature hesaplama (mock TA), risk manager JSON parse & fallback, rate limiter, Redis yayınlayıcı.
- **Integration Tests:** Compose dosya kontrolü ve dummy feed veri üretimi (yerel doğrulama). Docker gereksinimi olmadan içsel kontrol ile geçer.
- **Backtest Harness:** `app/research/backtest.py` ve `scripts/run_backtest.py` ile Influx tabanlı geçmiş veri üzerinde ajan performansı ölçümü yapılabiliyor.
- **Sürekli Test:** `poetry run pytest` toplam 9 test (integration dahil) çalıştırıyor.

### 4. Güncel Riskler ve Kontroller
| Risk | Durum / Kontrol |
| --- | --- |
| Twelve Data kotası | 11 anahtar + quota uyarısı; uyarılar Redis health kanalına düşüyor. |
| GLM format hataları | JSON zorunluluğu + fallback; rate limiter aşımlarında `RuntimeError`. |
| Veri kalitesi | Soft validation (negatif close/volume) → health uyarısı; daha kapsamlı validation TODO. |
| Prometheus/Grafana | Temel dashboard mevcut, ileri seviye alarm metrikleri tanımlanacak. |
| Backtest doğruluğu | Şu an basit portföy simülasyonu var; gerçekçi komisyon/slippage modellemesi TODO. |
| MCP TestSprite | Kurulum tamamlandı, proje özel config bekliyor. |

### 5. Kalan Yol Haritası (Öncelik Sırasıyla)
1. **Risk Yönetimi / Executor** – Daily loss kontrolünü gerçek PnL’e bağlama, leverage/checklist genişletmesi.
2. **Ajan Geliştirme** – Türev ajan için gerçek veri akışını bağlama, short/long modelleri için periyodik yeniden eğitim işleri.
3. **Backtest Geliştirme** – Komisyon, slipaj, benchmark karşılaştırması ve raporlama.
4. **İzleme** – Grafana panelini genişletmek, Telegram alert playbook’unu runbook’a entegre etmek.
5. **LLM Testleri** – GLM yanıtlarının otomatik doğrulaması ve fallback başarı oranı metriği.
6. **Deployment** – Paper trading otomasyonuna geçiş; gerçek emir modunun gereksinim analizi.

### 6. Operasyon ve Dokümantasyon
- Runbook: Başlatma/adım uyarıları, Prometheus/Telergram alarm akışları, secret rotasyon prosedürü.
- Secrets: `.env` yalnızca lokal kullanım içindir; prod ortamı için Vault/CI secrets planlanmalı.
- MCP/TestSprite: Node v18 ile çalışsa da paket Node 22 talep ediyor; uzun vadede LTS yükseltmesi planlanmalı.
- Plan bu dokümanla güncel tutulacak; üretim öncesi değişikliklerde roadmap yeniden değerlendirilir.

### 7. Başarı Ölçütleri (Paper Trading)
- Sharpe/Sortino, günlük PnL ve max drawdown.
- Ajan başına sinyal isabeti ve risk yöneticisi fallback oranı.
- Operasyonel metrikler: veri hattı uptime’ı, health uyarı süresi, GLM istek başarı oranı.

---
Bu sürüm, mevcut implementasyonu belgeleyip kalan işleri netleştirir; yeni geliştirmelerde ilgili bölüm güncellenmelidir.
