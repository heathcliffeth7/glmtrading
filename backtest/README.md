# AI Trading Backtesting System

Kapsamlı backtesting ve walk-forward analiz sistemi.

## 🚀 Hızlı Başlangıç

### 1. Setup

```bash
# Database tablolarını oluştur
python backtest/run.py setup

# Veya adım adım:
python backtest/run.py init_db
python backtest/run.py collect_data
```

### 2. Demo Çalıştır

```bash
python backtest/run.py demo
```

### 3. Tekil Backtest

```bash
python backtest/run.py run \
    --run-name "test_15m" \
    --start-date 2024-01-01 \
    --end-date 2024-06-30 \
    --timeframe 15m \
    --confidence-threshold 0.70 \
    --risk-per-trade 0.02
```

### 4. Senaryo Çalıştır

```bash
# Mevcut senaryoları listele
python backtest/run.py list-scenarios

# Belirli senaryoyu çalıştır
python backtest/run.py run-scenario --scenario conservative
```

## 📋 Mevcut Senaryolar

| Senaryo | Açıklama | Period | Timeframe |
|---------|----------|--------|-----------|
| `baseline` | Mevcut strateji | 2023-2024 | 15m |
| `conservative` | Düşük risk, yüksek güvenilirlik | 2023-2024 | 30m |
| `aggressive` | Yüksek risk/yüksek getiri | 2023-2024 | 5m |
| `bull_market` | Boğa piyasası optimizasyonu | Q4 2023 | 15m |
| `bear_market` | Ayı piyasası optimizasyonu | 2022 H2 | 30m |
| `sideways` | Yatay piyasalar | Q2 2024 | 1h |
| `high_volatility` | Yüksek volatilite | Q1 2024 | 15m |

## 🔧 Komutlar

### `run` - Tekil Backtest

```bash
python backtest/run.py run [OPTIONS]

Options:
  --run-name TEXT              Run name  [default: backtest]
  --start-date TEXT            Start date (YYYY-MM-DD)  [required]
  --end-date TEXT              End date (YYYY-MM-DD)  [required]
  --timeframe {1m,5m,15m,30m,1h,4h}  [default: 15m]
  --initial-capital FLOAT      Initial capital (USDT)  [default: 10000.0]
  --confidence-threshold FLOAT Signal confidence threshold  [default: 0.65]
  --risk-per-trade FLOAT       Risk per trade (0.02 = 2%)  [default: 0.02]
  --max-position FLOAT         Max position size (BTC)  [default: 1.0]
  --output PATH                Output JSON file
```

**Örnekler:**

```bash
# Temel test (son 3 ay)
python backtest/run.py run \
    --run-name "q1_2024" \
    --start-date 2024-01-01 \
    --end-date 2024-03-31

# Konservatif yaklaşım
python backtest/run.py run \
    --run-name "conservative_2024" \
    --start-date 2024-01-01 \
    --end-date 2024-06-30 \
    --confidence-threshold 0.80 \
    --risk-per-trade 0.01

# Yüksek frekanslı
python backtest/run.py run \
    --run-name "high_freq" \
    --start-date 2024-01-01 \
    --end-date 2024-03-31 \
    --timeframe 5m \
    --confidence-threshold 0.55
```

### `run-scenario` - Predefined Senaryolar

```bash
python backtest/run.py run-scenario --scenario <name>

# Çıktıyı kaydet
python backtest/run.py run-scenario \
    --scenario conservative \
    --output /root/trading/backtest/results
```

### `list-scenarios` - Senaryoları Listele

```bash
python backtest/run.py list-scenarios
```

### `collect-data` - Historical Data

```bash
# Tüm timeframeler için veri topla
python backtest/run.py collect-data

# Belirli timeframe
python backtest/run.py collect-data --timeframe 15m
```

### `verify-data` - Veri Doğrulama

```bash
# Tüm timeframeleri doğrula
python backtest/run.py verify-data

# Belirli timeframe
python backtest/run.py verify-data --verify-timeframe 15m
```

## 📊 Metrikler

Sistem aşağıdaki metrikleri hesaplar:

### Return Metrikleri
- **Total Return**: Toplam getiri (%)
- **Annual Return (CAGR)**: Yıllık bileşik getiri
- **Benchmark Return**: BTC buy-and-hold karşılaştırması
- **Excess Return**: Benchmark'ın üzerinde getiri

### Risk Metrikleri
- **Sharpe Ratio**: Risk-adjusted return
- **Sortino Ratio**: Downside risk-adjusted return
- **Max Drawdown**: Maksimum çekilme (%)
- **Calmar Ratio**: Return / Max Drawdown
- **VaR 95%**: Value at Risk (95% güven)
- **CVaR 95%**: Conditional VaR

### Trade Metrikleri
- **Number of Trades**: Toplam işlem sayısı
- **Win Rate**: Kazanan işlem oranı (%)
- **Profit Factor**: Toplam kazanç / Toplam zarar
- **Average Win**: Ortalama kazanç
- **Average Loss**: Ortalama zarar
- **Largest Win**: En büyük kazanç
- **Largest Loss**: En büyük zarar
- **Average Trade Duration**: Ortalama işlem süresi

## 📁 Dosya Yapısı

```
backtest/
├── config/
│   ├── parameters.yaml       # Backtest parametreleri
│   └── scenarios.yaml        # Predefined senaryolar
├── data/
│   └── historical_collector.py  # Veri toplama
├── engine.py                 # Ana backtest engine
├── run.py                    # CLI interface
├── setup_database.py         # Database setup
├── reports/                  # Çıktı raporları
├── tests/                    # Test dosyaları
└── README.md                 # Bu dosya
```

## 🗄️ Database Schema

### backtest_results
Ana backtest sonuçlarını saklar.

```sql
- id: Primary key
- run_name: Run adı
- start_date, end_date: Test period
- total_return: Toplam getiri
- sharpe_ratio: Risk-adjusted metrik
- max_drawdown: Maksimum çekilme
- num_trades: İşlem sayısı
- win_rate: Kazanma oranı
- parameters: JSON formatında parametreler
```

### backtest_trades
Her trade'in detaylarını saklar.

```sql
- id: Primary key
- backtest_id: Foreign key
- timestamp: İşlem zamanı
- action: BUY/SELL/CLOSE
- price: İşlem fiyatı
- amount: BTC miktarı
- pnl: Kar/Zarar
- signal_confidence: Sinyal güveni
- glm_reasoning: AI karar gerekçesi
```

## ⚙️ Yapılandırma

### parameters.yaml

```yaml
data_collection:
  start_date: "2022-01-01"
  end_date: "2024-12-31"
  symbol: "BTCUSDT"
  timeframes: ["1m", "5m", "15m", "30m", "1h", "4h"]

backtest:
  initial_capital: 10000
  commission_rate: 0.001  # 0.1%
  slippage: 0.0005        # 0.05%

  position:
    max_position_size: 1.0  # BTC
    risk_per_trade: 0.02    # 2%

  risk:
    stop_loss: 0.05         # 5%
    take_profit: 0.10       # 10%
    confidence_threshold: 0.65
```

### scenarios.yaml

Yeni senaryo eklemek için:

```yaml
scenarios:
  my_custom_scenario:
    name: "Custom Scenario Name"
    description: "Description of the scenario"
    parameters:
      start_date: "2024-01-01"
      end_date: "2024-12-31"
      timeframe: "15m"
      confidence_threshold: 0.70
      risk_per_trade: 0.02
      max_position_size: 1.0
```

## 📈 Rapor Oluşturma

JSON çıktı:

```bash
python backtest/run.py run \
    --run-name "test" \
    --start-date 2024-01-01 \
    --end-date 2024-06-30 \
    --output results.json
```

Database'den sonuçları çekmek için:

```python
from setup_database import get_session
session = get_session(DATABASE_URL)

results = session.query(BacktestResult).all()
for r in results:
    print(f"{r.run_name}: {r.total_return}%")
```

## 🔍 Troubleshooting

### Veri Bulunamadı Hatası

```bash
# Veri topla
python backtest/run.py collect_data

# Veri doğrula
python backtest/run.py verify-data
```

### Database Bağlantı Hatası

```bash
# .env dosyasında DATABASE_URL kontrol et
echo $DATABASE_URL

# Database tabloları kontrol et
python backtest/run.py init-db
```

### InfluxDB Bağlantı Hatası

```bash
# .env dosyasında InfluxDB credentials kontrol et
echo $INFLUXDB_URL
echo $INFLUXDB_TOKEN
echo $INFLUXDB_ORG
```

## 🎯 Best Practices

1. **Always verify data** before running backtests
2. **Use multiple scenarios** to test different market conditions
3. **Compare with benchmark** (BTC buy-and-hold) for context
4. **Check Sharpe ratio** - > 1.0 is good, > 2.0 is excellent
5. **Monitor max drawdown** - < 20% is acceptable
6. **Win rate alone is not enough** - check profit factor too
7. **Out-of-sample testing** - test on data the model hasn't seen

## 📝 Örnek Kullanım

### Temel Analiz

```bash
# 2024 ilk yarısını test et
python backtest/run.py run \
    --run-name "h1_2024" \
    --start-date 2024-01-01 \
    --end-date 2024-06-30 \
    --timeframe 15m \
    --output h1_2024_results.json
```

### Parametre Optimizasyonu

```bash
# Farklı confidence threshold değerleri
for threshold in 0.60 0.65 0.70 0.75 0.80; do
    python backtest/run.py run \
        --run-name "threshold_$threshold" \
        --start-date 2024-01-01 \
        --end-date 2024-06-30 \
        --confidence-threshold $threshold
done
```

### Karşılaştırmalı Analiz

```bash
# Konservatif vs Agresif
python backtest/run.py run-scenario --scenario conservative
python backtest/run.py run-scenario --scenario aggressive
```

## 🤝 Mevcut Sistemle Entegrasyon

Backtesting sistemi mevcut trading sistemiyle uyumlu çalışır:

```python
# Mevcut signal agent'ı import et
from app.agents.multi_signal import MultiSignalAgent

# Backtest engine'de kullan
signal_agent = MultiSignalAgent(symbol, timeframe)
signal = signal_agent.generate_signal(data)
```

## 📞 Destek

Sorular için:
- System logs: `/root/trading/backtest/logs/`
- Test script: `python backtest/tests/test_basic.py`

## 🎓 İleri Konular

### Walk-Forward Analysis

```python
# Bu feature yakında eklenecek
from backtest.walk_forward import WalkForwardAnalyzer

analyzer = WalkForwardAnalyzer(
    training_period=timedelta(days=180),
    testing_period=timedelta(days=30)
)
results = analyzer.optimize(data)
```

### Monte Carlo Simulation

```python
# Bu feature yakında eklenecek
from backtest.monte_carlo import MonteCarlo

mc = MonteCarlo(num_simulations=1000)
risk_metrics = mc.run(returns_series)
```

## 📄 Lisans

Bu sistem AI Trading projesinin bir parçasıdır.