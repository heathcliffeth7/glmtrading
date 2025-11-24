# Multi-Timeframe Trading Setup

## 🎯 Overview

Multi-timeframe trading sistemi **3 farklı zaman dilimini** analiz eder:

- **1min (Intraday)**: Hızlı momentum değişimleri - RSI, MACD, EMA
- **30min (Main)**: Ana karar timeframe'i - Tüm 25 gösterge
- **4h (Long-term)**: Trend context - EMA, RSI, MACD, ATR, Volume

## 📊 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   InfluxDB (Time-Series DB)                  │
├─────────────────────────────────────────────────────────────┤
│  enriched_1m   │  enriched_30min  │  enriched_4h            │
│  (every 1 min) │  (every 30 min)  │  (every 4 hours)        │
└─────────────────────────────────────────────────────────────┘
                           ▲
                           │
           ┌───────────────┴───────────────┐
           │                               │
      ┌────▼────┐        ┌────▼────┐  ┌───▼────┐
      │ 1m Feed │        │30m Feed │  │ 4h Feed│
      └─────────┘        └─────────┘  └────────┘
                               │
                          ┌────▼────────┐
                          │ DerivativesAgent │
                          │ (Multi-TF)       │
                          └────┬────────┘
                               │
                          ┌────▼────────┐
                          │ RiskManager  │
                          │ (GLM + MT)   │
                          └──────────────┘
```

## 🚀 Quick Start

### 1. Test Current State

```bash
cd /root/trading
.venv/bin/python test_multiframe.py
```

Bu test gösterecek:
- ✅ Hangi timeframe'ler aktif
- ❌ Hangileri eksik
- 📊 Örnek veri çıktıları

### 2. Enable 1min Feed (Intraday)

```bash
# Copy service file
sudo cp /root/trading/infra/trading-enriched-feed-1m.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Start service
sudo systemctl start trading-enriched-feed-1m

# Enable auto-start on boot
sudo systemctl enable trading-enriched-feed-1m

# Check status
sudo systemctl status trading-enriched-feed-1m

# View logs
sudo journalctl -u trading-enriched-feed-1m -f
```

### 3. Enable 4h Feed (Long-term)

```bash
# Copy service file
sudo cp /root/trading/infra/trading-enriched-feed-4h.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Start service
sudo systemctl start trading-enriched-feed-4h

# Enable auto-start on boot
sudo systemctl enable trading-enriched-feed-4h

# Check status
sudo systemctl status trading-enriched-feed-4h

# View logs
sudo journalctl -u trading-enriched-feed-4h -f
```

### 4. Verify All Services

```bash
# Check all trading services
sudo systemctl list-units "trading-*" --all

# Expected output:
# trading-enriched-feed-1m.service    loaded active running
# trading-enriched-feed.service       loaded active running  (30min)
# trading-enriched-feed-4h.service    loaded active running
```

### 5. Test Multi-Timeframe Again

```bash
# Wait 5-10 minutes for data to populate, then test again
.venv/bin/python test_multiframe.py
```

Şimdi hepsi ✅ olmalı!

## 📈 Data Collection Timeline

| Timeframe | Interval | Data Points (10 snapshots) | Coverage |
|-----------|----------|---------------------------|----------|
| 1min      | 60s      | Last 10 minutes           | Short-term momentum |
| 30min     | 1800s    | Last 5 hours              | Main trading decisions |
| 4h        | 14400s   | Last 40 hours             | Long-term trend context |

## 🧠 GLM Prompt Enhancement

Multi-timeframe sistemi GLM'e şu ek bilgileri verir:

### Agent Signal Example:
```
Model: 0.62 | TF: 1m(RSI:28🟢) 30m(main) 4h(↑trend) | 
L/S: 1.45(↑long) | OI: 24890 | FR: -0.000009(!) | 
EMA: ↑ | RSI: 28🟢 | MACD: ↑ (-167.5) | ...
```

### Historical Data Structure:
```yaml
historical_data:
  intraday_1m:
    close: [107804, 107752, 107701, ...]
    rsi_14: [37, 33, 31, ...]
    macd: [-59, -66, -72, ...]
    ema_20: [107912, 107895, 107878, ...]
  
  main_30min:
    close: [...]
    rsi_14: [...]
    macd: [...]
    # ... 8 indicators
  
  longterm_4h:
    ema_20: [...]
    ema_50: [...]
    rsi_14: [...]
    # ... 6 indicators
```

### GLM Decision Criteria:

**Timeframe Uyumu:**
- ✅ Tüm TF'ler aynı yönde → ⭐ Yüksek güven (0.8-1.0)
- ⚠️ 1m + 30m aynı, 4h farklı → Orta güven (0.5-0.7)
- ❌ TF'ler uyumsuz → HOLD veya düşük güven (<0.3)

**Pattern Recognition:**
- 1m RSI: `[35,30,28,30,35]` = Dip yapıp döndü 🟢 BUY
- 30m MACD: `[-100,-80,-60,-40,-20]` = Güçleniyor 🟢 BUY
- 4h EMA: Cross sinyali yaklaşıyor → Trend değişimi bekleniyor

## 🔧 Troubleshooting

### Problem: "No 1m/4h data available"

**Solution:**
```bash
# Check if services are running
sudo systemctl status trading-enriched-feed-1m
sudo systemctl status trading-enriched-feed-4h

# Check logs for errors
sudo journalctl -u trading-enriched-feed-1m -n 50
sudo journalctl -u trading-enriched-feed-4h -n 50

# Restart if needed
sudo systemctl restart trading-enriched-feed-1m
sudo systemctl restart trading-enriched-feed-4h
```

### Problem: "InfluxDB connection error"

**Solution:**
```bash
# Check InfluxDB is running
sudo systemctl status influxdb

# Test InfluxDB connection
.venv/bin/python -c "from app.utils.influx import test_connection; print(test_connection())"

# Check measurements
.venv/bin/python -c "from app.utils.influx import _ensure_client, _query_api; _ensure_client(); print('Connected')"
```

### Problem: "Historical data empty"

**Solution:**
```bash
# Wait 10-15 minutes after starting services
# InfluxDB needs time to accumulate 10 snapshots

# For 1min: Wait 10 minutes
# For 30min: Wait 5 hours (or use existing data)
# For 4h: Wait 40 hours (or use existing data)
```

## 📊 Monitoring

### Check Data Quality

```bash
# Query latest snapshots
.venv/bin/python -c "
from app.utils.influx import query_latest_snapshot
print('1m:', query_latest_snapshot('enriched_1m', 'BTCUSDT', '1m'))
print('30m:', query_latest_snapshot('enriched_30min', 'BTCUSDT', '30min'))
print('4h:', query_latest_snapshot('enriched_4h', 'BTCUSDT', '4h'))
"
```

### Monitor Feed Performance

```bash
# 1min feed logs (should update every minute)
sudo journalctl -u trading-enriched-feed-1m -f | grep "Wrote enriched"

# 30min feed logs (should update every 30 min)
sudo journalctl -u trading-enriched-feed -f | grep "Wrote enriched"

# 4h feed logs (should update every 4 hours)
sudo journalctl -u trading-enriched-feed-4h -f | grep "Wrote enriched"
```

## 🎯 Expected Improvements

### Before (Single Timeframe - 30min only):
- ❌ Blind to intraday momentum shifts
- ❌ No long-term trend context
- ❌ Late to catch reversals
- 📊 Confidence: Often 0.6-0.7

### After (Multi-Timeframe):
- ✅ Detects intraday RSI divergences (1min)
- ✅ Confirms with long-term trend (4h)
- ✅ Higher confidence when TFs align
- 📊 Confidence: 0.8-0.9 when aligned

### Example Decision Flow:

```
Scenario: BTC dropping fast

1min: RSI=28 (oversold) → Short-term BUY signal
30min: RSI=40, MACD negative → Neutral
4h: EMA20 > EMA50, trend bullish → Long-term BUY bias

GLM Decision: 
"1m RSI oversold + 4h bullish trend = Strong BUY
Gerekçe: 1m ve 4h uyumlu (kısa vadeli dip + uzun trend yukarı), 
30m henüz nötr ama reversal başlıyor. Güven: 0.85, Miktar: 0.7"
```

## 🔥 Next Steps

1. **Test**: Run `test_multiframe.py` to verify setup
2. **Enable**: Start 1m and 4h feeds
3. **Monitor**: Watch logs for 10-15 minutes
4. **Validate**: Run test again, check all ✅
5. **Trade**: Start main bot, observe multi-TF decisions

## 📚 References

- DerivativesAgent: `/root/trading/app/agents/derivatives.py`
- RiskManager: `/root/trading/app/risk_manager/manager.py`
- Enriched Feed: `/root/trading/app/data_feeds/enriched_feed.py`
- Test Script: `/root/trading/test_multiframe.py`
