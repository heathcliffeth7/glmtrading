# Mimari Genel Bakış

## Diyagram

```
┌──────────────────────────────┐    ┌──────────────────────────────┐
│        Data Sources          │    │       External Services      │
│  • Binance WS (1m, 5m)       │    │  • Twelve Data Indicators    │
│  • Binance Futures REST      │    │  • GLM 4.6 (Risk LLM)         │
│  • Dummy Feed (tests)        │    │  • Telegram Bot API          │
└──────────────┬──────────────┘    └────────────┬─────────────────┘
               │                                 │
               ▼                                 ▼
┌──────────────────────────────┐    ┌──────────────────────────────┐
│      Data Feed Orchestrator  │    │         Risk Manager         │
│  • asyncio.gather            │    │  • GLM client + rate limiter │
│  • Health publishing         │    │  • Fallback heuristics       │
└──────────────┬──────────────┘    └────────────┬─────────────────┘
               │ Redis Pub/Sub                  │
               ▼                                 ▼
┌──────────────────────────────┐    ┌──────────────────────────────┐
│         Feature Workers      │    │           Executor           │
│  • Interval windows          │    │  • Portfolio & ledger        │
│  • TA-Lib + fallback         │    │  • Leverage normalization    │
│  • InfluxDB + QLib writes    │    │  • Telegram notifications    │
└──────────────┬──────────────┘    └────────────┬─────────────────┘
               │                                 │
               ▼                                 ▼
┌──────────────────────────────┐    ┌──────────────────────────────┐
│          Storage Layer       │    │       Monitoring & UI        │
│  • InfluxDB (features)       │    │  • Prometheus /metrics       │
│  • QLib Parquet store        │    │  • Streamlit dashboard       │
│  • SQLite ledger             │    │  • Grafana + Alertmanager    │
└──────────────┬──────────────┘    └────────────┬─────────────────┘
               │                                 │
               ▼                                 ▼
           Automated Runner ─────> Backtester ────┘
           (5 dakikalık döngü)      + Agents
```

## Çekirdek Bileşenler

### 1. Konfigürasyon & Ortam
- `app/config/settings.py` Pydantic Settings: Binance, Twelve Data, Influx, Redis, Telegram, QLib, GLM.
- `.env` gerçek API anahtarlarını içerir; nested modeller `env_prefix` ile yüklenir.
- Poetry tabanlı proje; Python 3.12, `ta-lib`, `httpx`, `asyncio`, `sqlalchemy`, `pydantic-settings` vb.

### 2. Veri Toplama Katmanı
- **Binance WebSocket (`app/data_feeds/binance_ws.py`)**
  - 1 dakikalık ve 5 dakikalık kline stream’leri dinler, her mesajı Redis `KLINE_CHANNEL` kanalına yayınlar.
  - Sağlık durumunu `HEALTH_CHANNEL` ile raporlar.
- **Binance Futures REST (`app/data_feeds/binance_futures.py`)**
  - `globalLongShortAccountRatio`, `openInterestHist`, `fapi/v1/fundingRate` uç noktalarını 5 dakikalık periyotlarla çeker.
- **Twelve Data Poller (`app/data_feeds/twelve_data.py`)**
  - 11 API anahtarı için kota rotasyonu, tüm indikatörler 5 dakikalık interval ile çekilir.
- **Orchestrator (`app/data_feeds/service.py`)**
  - WebSocket, REST, Twelve Data poller’larını `asyncio.gather` ile paralel çalıştırır, start-up health mesajı yollar.

### 3. Mesajlaşma
- Redis Pub/Sub merkezi omurga; `KLINE_CHANNEL` kline mesajı, `HEALTH_CHANNEL` servis metrikleri, diğer kanal isimleri sabitler altında toparlanır.
- `app/utils/redis.py` publish yardımcıları JSON serileme + hata log’ları sağlar.

### 4. Özellik (Feature) Katmanı
- **FeatureWorker (`app/features/feature_worker.py`)**
  - Symbol+interval için sliding window tutar.
  - TA-Lib göstergeleri: EMA20/50, RSI14, MACD, ATR14, VWAP20. Minimum 50 bar sağlanamazsa fallback hesaplamaları devreye girer.
  - Son hesaplanan feature set’ini InfluxDB’ye (`features_{interval}` measurement) ve QLib Parquet deposuna yazar.
  - Hatalı veri durumlarında HEALTH kanalına uyarı yollar.
- **Feature orchestrator (`app/features/orchestrator.py`)**
  - Belirtilen symbol & interval kombinasyonları için worker’ları başlatır, async görev olarak çalışır.

### 5. Depolama
- **InfluxDB** otomatik Docker compose stack ile; feature ölçümleri `timestamp`, `symbol`, `interval` tag’leri ve alanlar ile tutulur.
- **QLib Parquet Store** ML eğitim pipeline’ları için `app/features/qlib_converter.py` ile yazılır.
- **SQLite Ledger (`app/executor/ledger.py`)** işlemler, portföy durumu, günlük PnL kayıtları.

### 6. Ajanlar & Araştırma
- **Agents (`app/agents/`)**
  - QLib tabanlı kısa/uzun vade modelleri (LightGBM), türev strateji (Logistic Regression).
  - `ShortTermAgent`, Influx’tan en güncel feature’ı okuyarak sinyal üretir.
- **Backtester (`app/research/backtest.py`)**
  - Influx veri dilimini alır, ajanla sanal işlemler yapar, PnL ve trade istatistiklerini döner.
  - `AutomatedRunner` her 5 dakikalık döngüde hızlı backtest çalıştırır.

### 7. Risk Yönetimi
- `app/risk_manager/manager.py`
  - GLM 4.6 API’sine prompt gönderir, JSON formatında aksiyon ve kaldıracı bekler.
  - Sliding window rate limiter (1000 istek / 5 saat) ile API kotasını korur.
  - LLM cevabı hatalıysa fallback: sinyal gücüne göre aksiyon + 5-20x normalize leverage.

### 8. Uygulayıcı (Executor)
- `app/executor/executor.py`
  - Portföy pozisyonu, ortalama fiyat günceller; kaldıracı normalize eder.
  - İşlem detayı + PnL’i ledger’a yazar, Telegram’a Markdown formatında gönderir.
  - Guardrails: max pozisyon, veri tutarlılığı kontrolü.

### 9. Otomasyon
- `app/orchestrator/runtime.py`
  - 5 dakikalık döngü: data feed orchestrator, feature worker’lar paralel başlar.
  - Her cycle’da Influx’dan 5m veri çekilir → backtest → ajan sinyali → risk kararı → executor → Telegram raporu.
  - Veri yetersizse uyarı log’u üretir, yeterli bar oluştuğunda tam pipeline çalışır.

### 10. İzleme ve Kullanıcı Arayüzü
- **Prometheus Endpoint**: FastAPI `/metrics` sayacı (gözlem, hata metrikleri).
- **Streamlit Dashboard**: Influx fiyatları, özellikler, ledger PnL grafikleri.
- **Grafana + Alertmanager**: Docker compose altında panel + Telegram webhook alarm entegrasyonu.

### 11. Test & CI
- `pytest` ile 13 unit/integration test; feature, risk manager, Redis, Telegram, settings.
- Docker Compose test stack (Redis + Influx + dummy feed).
- `.github/workflows/ci.yml`: Poetry install, black/isort/mypy, pytest coverage, docker build.

### 12. Dağıtım / Çalıştırma
1. `docker-compose -f infra/docker-compose.test.yml up -d` ile Redis & Influx stack.
2. `.venv/bin/python -m app.data_feeds.service` ile veri orkestratörü (veya otomasyon runner).
3. `.venv/bin/python -m app.orchestrator.runtime` 5 dakikalık tam döngüyü başlatır.
4. Çalışma sırasında Telegram bildirimleri, Influx kayıtları ve Prometheus metrikleri üretmeye başlar.

## Veri Yaşam Döngüsü
1. **Toplama**: Binance WS/REST + Twelve Data → Redis Pub/Sub.
2. **Özellik Üretimi**: FeatureWorker sliding window → TA-Lib / fallback → Influx + QLib.
3. **Analiz**: Backtester + Agent → trade sinyal listesi.
4. **Risk Değerlendirmesi**: GLM 4.6 yanıtı + fallback → RiskDecision.
5. **Uygulama**: Executor pozisyonu günceller, ledger kaydı + Telegram bildirimi.
6. **Raporlama**: Streamlit & Grafana panelleri, Prometheus metrikleri, Alertmanager bildirimleri.
7. **Sürekli Döngü**: AutomatedRunner her 5 dakikada süreci tekrarlar.

## Güvenlik ve Sağlamlık
- API anahtarları `.env` içinde, kod depolamada yok.
- Rate limit ve hata yakalamalar ile LLM / Twelve Data / Binance servis kesintileri tolere edilir.
- Sağlık mesajları ve Prometheus sayaçları canlılık takibi sağlar.
- Telegram bildirimleri manuel gözetim imkânı sunar.
