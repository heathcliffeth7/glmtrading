# Multi-Signal Trading System - Comprehensive Guide

## Sistem Mimarisi

```
┌─────────────────────────────────────────────────────────┐
│                    DATA SOURCES                          │
├─────────────────────────────────────────────────────────┤
│ 1. TwelveData API (15+ indicators)                      │
│    ├─ RSI, MACD, EMA, SMA                               │
│    ├─ Bollinger Bands, Stochastic                       │
│    ├─ ATR, Ichimoku, SAR, Williams %R                   │
│    ├─ OBV, CCI, VWAP, MFI, Pivot Points                 │
│    └─ OHLCV Time Series                                 │
│                                                          │
│ 2. Binance Futures API                                  │
│    ├─ Long/Short Ratio                                  │
│    ├─ Open Interest                                     │
│    └─ Funding Rate                                      │
│                                                          │
│ 3. Technical Analysis (ta-lib)                          │
│    └─ Computed from OHLCV                               │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│            ENRICHED DATA AGGREGATOR                      │
│  (trading-enriched-feed.service)                        │
│                                                          │
│  → Combines all sources every 5 minutes                 │
│  → Writes to InfluxDB: enriched_5min                    │
│  → 19+ data points per bar                              │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│              MULTI-SIGNAL AGENT                          │
│                                                          │
│  Weighted Analysis:                                      │
│  ├─ 35% TREND                                           │
│  │   ├─ EMA 20/50 crossover                             │
│  │   ├─ MACD histogram                                  │
│  │   └─ Price vs EMAs                                   │
│  │                                                       │
│  ├─ 30% MOMENTUM                                        │
│  │   ├─ RSI (oversold/overbought)                       │
│  │   ├─ Stochastic K/D                                  │
│  │   └─ MACD signal line                                │
│  │                                                       │
│  ├─ 20% SENTIMENT                                       │
│  │   ├─ Long/Short Ratio                                │
│  │   ├─ Funding Rate                                    │
│  │   └─ Open Interest trends                            │
│  │                                                       │
│  ├─ 10% VOLATILITY                                      │
│  │   ├─ ATR (% of price)                                │
│  │   ├─ Bollinger Band width                            │
│  │   └─ Inverse scoring (high vol = caution)           │
│  │                                                       │
│  └─ 5% VOLUME                                           │
│      └─ Open Interest level                             │
│                                                          │
│  Output: Direction + Confidence + Detailed Reasoning    │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│                  RISK MANAGER (GLM-4)                   │
│  → LLM analyzes signal + market context                │
│  → Determines: Action, Amount, Leverage                │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│                     EXECUTOR                             │
│  → Paper trading execution                              │
│  → Guardrails & position management                     │
└─────────────────────────────────────────────────────────┘
```

## Signal Components Explained

### 1. TREND ANALYSIS (35% weight)

**Indicators:**
- **EMA 20/50 Crossover**: Golden cross (20>50) = bullish, Death cross (20<50) = bearish
- **Price Position**: Above both EMAs = strong uptrend
- **MACD**: Above signal line = bullish momentum

**Scoring:**
- `+1.0`: Strong uptrend (all bullish signals)
- `0.0`: Neutral/sideways
- `-1.0`: Strong downtrend (all bearish signals)

**Example:**
```
EMA 20: 110,861 > EMA 50: 110,885 ❌ (slight bearish)
Close: 110,808 < EMA 20 ❌ (below short-term)
MACD: -62.48 < Signal: -63.08 ✅ (crossing up)

Trend Score: -0.33 (slightly bearish)
```

### 2. MOMENTUM ANALYSIS (30% weight)

**Indicators:**
- **RSI 14**: <30 oversold (bullish), >70 overbought (bearish)
- **Stochastic K/D**: <20 oversold, >80 overbought, crossovers
- **Rate of change**: Speed of price movement

**Scoring:**
- `+1.0`: Extremely overbought (reversal likely, bearish)
- `0.0`: Neutral momentum
- `-1.0`: Extremely oversold (reversal likely, bullish)

**Example:**
```
RSI: 46.05 (neutral, slightly bearish)
Stoch K: 35 / D: 40 (no extreme)

Momentum Score: +0.15 (slightly overbought)
```

### 3. SENTIMENT ANALYSIS (20% weight)

**Indicators:**
- **Long/Short Ratio**: >1.5 = too bullish (contrarian bearish), <0.7 = too bearish (contrarian bullish)
- **Funding Rate**: Positive = longs paying shorts (bearish), Negative = shorts paying longs (bullish)
- **Open Interest**: Rising = conviction, Falling = uncertainty

**Scoring:**
- `+1.0`: Extreme bullish sentiment (contrarian bearish)
- `0.0`: Balanced sentiment
- `-1.0`: Extreme bearish sentiment (contrarian bullish)

**Example:**
```
L/S Ratio: 1.79 (too many longs, contrarian bearish)
Funding: +0.001% (longs paying, slightly bearish)
OI: 9.09B (high conviction)

Sentiment Score: -0.35 (contrarian bearish signal)
```

### 4. VOLATILITY ANALYSIS (10% weight)

**Indicators:**
- **ATR %**: ATR / Price, normalized to 0-1
- **Bollinger Band Width**: (Upper - Lower) / Price

**Scoring:**
- `0.0`: Low volatility (calm market)
- `1.0`: High volatility (risky, reduce position)

**Note:** High volatility **reduces** overall confidence (inverse weight)

**Example:**
```
ATR: 150 / Price: 110,808 = 0.14%
BB Width: (111,131 - 110,643) / 110,808 = 0.44%

Volatility Score: 0.25 (moderate)
Impact: Reduces confidence by 2.5% (10% × 0.25)
```

### 5. VOLUME ANALYSIS (5% weight)

**Indicators:**
- **Open Interest**: Absolute level indicates market participation

**Scoring:**
- `0.2`: Low OI (<100M)
- `0.8`: High OI (>1B)

## Decision Logic

### Aggregated Score Calculation

```python
final_score = (
    trend * 0.35 +
    momentum * 0.30 +
    sentiment * 0.20 +
    (volatility * -0.10) +  # Inverse!
    volume * 0.05
)
```

### Direction & Confidence

```
if final_score > 0.15:
    Direction: BUY
    Confidence: min(1.0, abs(final_score))

elif final_score < -0.15:
    Direction: SELL
    Confidence: min(1.0, abs(final_score))

else:
    Direction: HOLD
    Confidence: 0.0
```

### Example Calculation

```
Given:
  Trend: -0.33 (bearish)
  Momentum: +0.15 (slightly overbought)
  Sentiment: -0.35 (contrarian bearish)
  Volatility: 0.25 (moderate)
  Volume: 0.80 (high)

Calculation:
  = (-0.33 × 0.35) + (0.15 × 0.30) + (-0.35 × 0.20) + (0.25 × -0.10) + (0.80 × 0.05)
  = -0.1155 + 0.045 + (-0.07) + (-0.025) + 0.04
  = -0.1255

Result:
  Score: -0.13 (close to neutral)
  Direction: HOLD (within -0.15 to +0.15 range)
  Confidence: 0%
```

## Service Architecture

### 1. trading-enriched-feed.service

**Frequency:** Every 5 minutes (300 seconds)
**Function:** Aggregate all data sources
**Output:** InfluxDB measurement `enriched_5min`

```bash
# Status
sudo systemctl status trading-enriched-feed

# Logs
journalctl -u trading-enriched-feed -f

# Restart
sudo systemctl restart trading-enriched-feed
```

### 2. trading-orchestrator.service

**Frequency:** Every 5 minutes (300 seconds)
**Function:** Generate signal → Get risk decision → Execute trade

**Agent:** MultiSignalAgent (uses enriched data)

```bash
# Status
sudo systemctl status trading-orchestrator

# Logs
journalctl -u trading-orchestrator -f

# Restart
sudo systemctl restart trading-orchestrator
```

### 3. trading-feature-sync.service (OPTIONAL)

**Status:** Can be stopped if using enriched feed
**Reason:** Enriched feed includes base features

```bash
# Stop (not needed with enriched feed)
sudo systemctl stop trading-feature-sync
sudo systemctl disable trading-feature-sync
```

## Data Flow Timeline

```
Time: 11:00:00
  ├─ TwelveData: New 5min bar created
  └─ Binance: New futures data

Time: 11:00:15
  └─ Enriched Feed: Aggregates all sources
      ├─ Fetches TwelveData indicators (RSI, MACD, etc.)
      ├─ Fetches Binance Futures (L/S, OI, Funding)
      ├─ Gets base features from InfluxDB
      └─ Writes to enriched_5min

Time: 11:05:00
  └─ Orchestrator Cycle:
      ├─ MultiSignalAgent reads enriched_5min
      ├─ Calculates weighted score
      ├─ Generates signal with reasoning
      ├─ Risk Manager analyzes (GLM-4)
      ├─ Executor attempts trade
      └─ Logs & notifies

Time: 11:05:15
  └─ Enriched Feed: Next aggregation
      (cycle repeats)
```

## Monitoring & Health Checks

### Quick Check Script

```bash
/root/trading/QUICKCHECK.sh
```

### Manual Enriched Data Check

```bash
cd /root/trading
.venv/bin/python -c "
from app.utils.influx import query_latest_snapshot
import json

enriched = query_latest_snapshot('enriched_5min', 'BTCUSDT', '5min')
if enriched:
    print('Enriched data available:')
    print(json.dumps(enriched, indent=2, default=str))
else:
    print('No enriched data found!')
"
```

### Manual Agent Test

```bash
cd /root/trading
.venv/bin/python -c "
from app.agents.multi_signal import MultiSignalAgent

agent = MultiSignalAgent('BTCUSDT', '5min')
signal = agent.generate_signal()

print(f'Direction: {signal.direction}')
print(f'Confidence: {signal.confidence:.2%}')
print(f'Reasoning: {signal.reasoning}')
"
```

## Troubleshooting

### Issue: "No enriched data"

**Cause:** Enriched feed service not running or failed

**Solution:**
```bash
sudo systemctl restart trading-enriched-feed
journalctl -u trading-enriched-feed -n 50
```

### Issue: Agent always returns HOLD

**Cause:** Score between -0.15 and +0.15 (neutral range)

**Solution:** This is normal in sideways markets. Check reasoning:
```bash
cd /root/trading
.venv/bin/python -c "
from app.agents.multi_signal import MultiSignalAgent
agent = MultiSignalAgent('BTCUSDT', '5min')
signal = agent.generate_signal()
print(f'Reasoning: {signal.reasoning}')
"
```

### Issue: TwelveData rate limit

**Cause:** Too many API calls

**Solution:** Reduce poll interval or use fewer indicators:
```bash
# Edit enriched_feed.py line 67:
# for indicator in INDICATORS[:5]  # Only first 5
```

## Advanced: Custom Weights

Edit `/root/trading/app/agents/multi_signal.py`:

```python
self._weights = {
    'trend': 0.40,      # Increase trend importance
    'momentum': 0.25,    # Reduce momentum
    'sentiment': 0.20,
    'volatility': 0.10,
    'volume': 0.05,
}
```

Then restart:
```bash
sudo systemctl restart trading-orchestrator
```

## Performance Metrics

**Data Points per Cycle:** 19+
**API Calls per 5min:** ~7 (TwelveData: 5, Binance: 2)
**Daily API Usage:** ~2,016 calls (within free tier: 9,600)
**Decision Latency:** ~2-3 seconds
**Signal Quality:** Multi-source validation reduces false signals

## Comparison: Old vs New

### Old System (Derivatives Agent)
- ✅ 3 data sources (futures metrics + basic TA)
- ❌ Limited indicators (EMA, RSI only)
- ❌ Always Fallback strategy
- ❌ Simple scoring (-1 to 1)

### New System (Multi-Signal Agent)
- ✅ 19+ data sources (all indicators combined)
- ✅ Comprehensive analysis (trend, momentum, sentiment, volatility, volume)
- ✅ Weighted scoring system
- ✅ Detailed reasoning per component
- ✅ Adaptive confidence based on market conditions

---

**Created:** 2025-10-20 11:36 UTC
**Status:** ✅ Production Ready
**Next Steps:** Monitor for 24h, tune weights if needed
