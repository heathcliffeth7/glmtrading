# AI Trading Platform Runbook

## Özet
- Durum: Çok ajanlı AI trading sistemi (paper trading modunda)
- Servisler: Veri toplayıcıları, özellik işleyicileri, ajanlar, risk yöneticisi, executor, dashboard
- Bağımlılıklar: Redis, InfluxDB, QLib data store, GLM 4.6 API, Binance API, Twelve Data API
- İzleme: Prometheus (placeholder), Grafana panelleri (planlandı)

## Ortam Değişkenleri
`.env` dosyasındaki anahtarlar Vault/secret store’da yönetilir; prod deploy için:
- BINANCE_API_KEY/SECRET
- TWELVE_DATA_API_KEYS (virgül ayrılmış)
- GLM_API_KEY
- INFLUX_TOKEN

## Başlatma
1. `poetry install`
2. `. .venv/bin/activate`
3. `docker compose up redis influxdb`
4. `python -m app.features.qlib_init`
5. Veri toplayıcı worker’ları başlat: `python -m app.data_feeds.service`
6. Dashboard: `streamlit run app/dashboard/app.py`

## Sağlık Kontrolleri
- Redis health channel mesajlarını izleyin (`stream:health`).
- InfluxDB yazımlarını `influx query` ile doğrulayın.
- GLM rate limiter loglarını kontrol edin (`fallback` oranı yüksekse alarm).

## Incident Response
1. **Veri kesintisi**: Redis health channel’da `error` mesajı varsa ilgili toplayıcıyı yeniden başlatın.
2. **QLib veri bozulması**: `qlib_data` dizinini yedekten yükleyin, `qlib_update.py` betiğini yeniden çalıştırın.
3. **GLM API başarısızlığı**: Rate limit aşımı veya bağlantı hatası durumunda fallback kararına geçilir; API key rotasyonu yapın.

## Deployment
- CI (TODO) pipeline: lint, mypy, pytest, docker build.
- Prod deploy: Docker image push + compose/k8s rollout.
- Rollback: Bir önceki image tag’ine dön, `qlib_data`’yı backup’tan restore et.

## Secret Rotasyonu
- Binance/Twelve Data anahtarlarını aylık rotasyonla yenileyin.
- GLM API anahtarını 5 saatte 1000 hak sınırını izleyerek, gerekirse alternatif anahtar ekleyin.

## Alarm Süreçleri
- Prometheus alarmları: WebSocket disconnect sayısı, GLM fallback oranı, Influx write hataları.
- Webhook: incident Slack kanalına JSON payload (source, severity, timestamp).

## Backups
- `qlib_data` günlük snapshot.
- `portfolio.db` (ledger) saatlik backup.

## İletişim
- DevOps: devops@example.com
- Data Science: ds@example.com
- Trading Ops: trading@example.com
