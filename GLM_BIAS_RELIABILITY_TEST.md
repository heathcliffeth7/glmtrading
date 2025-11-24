# GLM Bias Reliability Test Implementation

**Date**: October 29, 2025  
**Status**: ✅ Implemented

---

## 🎯 Overview

Implemented **Bias Reliability Test** to compare GLM's confidence with the system's composite bias score. This helps determine:
- When to trust the bias score vs GLM's assessment
- Which bias scenarios correlate with GLM confidence
- How to improve confidence calibration over time

---

## 📊 What Was Implemented

### 1. **Structured GLM Output** (`/root/trading/app/risk_manager/manager.py`)

GLM now returns structured JSON with:
```json
{
  "karar": "BUY",
  "miktar": 0.15,
  "kaldıraç": 10,
  "ai_confidence": 85,
  "reason_primary": "RSI shows clear divergence on 4h chart (RSI=28, price making lower lows)",
  "reason_secondary": "Funding rate slightly negative (-0.0002) indicates short squeeze risk",
  "gerekçe": "Detailed Turkish analysis..."
}
```

**New Fields**:
- `ai_confidence` (0-100): GLM's self-assessed confidence
- `reason_primary`: Most important factor for decision
- `reason_secondary`: Second most important factor

### 2. **RiskDecision Dataclass Enhanced**

```python
@dataclass
class RiskDecision:
    action: str
    amount: float
    reasoning: str
    leverage: float = 5.0
    glm_confidence: float = 0.0  # NEW: GLM's confidence (0-100)
    reason_primary: str = ""     # NEW: Primary reason
    reason_secondary: str = ""   # NEW: Secondary reason
```

### 3. **Bias Reliability Test Method**

New method `_log_bias_reliability()` that:
- Compares GLM confidence with bias confidence
- Checks direction agreement (BUY/SELL vs positive/negative bias)
- Logs to InfluxDB for analysis
- Warns on high disagreement

---

## 🔍 How It Works

### Comparison Logic

```python
# 1. Extract scores
glm_confidence = decision.glm_confidence / 100.0  # Normalize to 0-1
bias_confidence = bias_snapshot["bias_confidence_score"]  # Already 0-1
composite_bias = bias_snapshot["composite_bias_score"]  # -1 to +1

# 2. Determine directions
glm_direction = 1 if action == "BUY" else -1 if action == "SELL" else 0
bias_direction = 1 if composite_bias > 0.1 else -1 if composite_bias < -0.1 else 0

# 3. Calculate agreement
direction_agreement = (glm_direction == bias_direction)
confidence_diff = abs(glm_confidence - bias_confidence)

# 4. Categorize reliability
if direction_agreement and confidence_diff < 0.2:
    status = "HIGH_AGREEMENT" 🟢
elif direction_agreement and confidence_diff < 0.4:
    status = "MODERATE_AGREEMENT" 🟡
elif not direction_agreement:
    status = "DIRECTION_MISMATCH" 🔴
else:
    status = "CONFIDENCE_MISMATCH" 🟠
```

### Reliability Scenarios

| Scenario | GLM | Bias | Confidence Diff | Status | Color |
|----------|-----|------|-----------------|--------|-------|
| **Perfect Agreement** | BUY 85% | +0.80 (80%) | 5% | HIGH_AGREEMENT | 🟢 |
| **Good Agreement** | SELL 70% | -0.60 (60%) | 10% | HIGH_AGREEMENT | 🟢 |
| **Moderate Agreement** | BUY 75% | +0.45 (45%) | 30% | MODERATE_AGREEMENT | 🟡 |
| **Direction Mismatch** | BUY 80% | -0.50 (SELL) | N/A | DIRECTION_MISMATCH | 🔴 |
| **Confidence Mismatch** | BUY 90% | +0.30 (30%) | 60% | CONFIDENCE_MISMATCH | 🟠 |

---

## 📈 InfluxDB Logging

### Measurement: `bias_reliability`

**Tags**:
- `symbol`: BTCUSDT
- `action`: BUY, SELL, HOLD, CLOSE
- `reliability_status`: HIGH_AGREEMENT, MODERATE_AGREEMENT, DIRECTION_MISMATCH, CONFIDENCE_MISMATCH
- `direction_agreement`: true/false

**Fields**:
- `glm_confidence`: GLM's confidence (0-100)
- `bias_confidence`: Bias confidence (0-100, converted from 0-1)
- `composite_bias`: Composite bias score (-1 to +1)
- `trend_bias`: Trend bias component
- `momentum_bias`: Momentum bias component
- `futures_bias`: Futures bias component
- `volatility_regime`: Volatility regime score
- `confidence_diff`: Absolute difference between GLM and bias confidence
- `glm_direction`: +1 (BUY), -1 (SELL), 0 (HOLD/CLOSE)
- `bias_direction`: +1 (bullish), -1 (bearish), 0 (neutral)

---

## 📊 Analysis Queries

### 1. Overall Agreement Rate

```flux
from(bucket: "trading")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "bias_reliability")
  |> filter(fn: (r) => r.reliability_status == "HIGH_AGREEMENT")
  |> count()
```

### 2. Confidence Difference Distribution

```flux
from(bucket: "trading")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "bias_reliability")
  |> filter(fn: (r) => r._field == "confidence_diff")
  |> histogram(bins: [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
```

### 3. Direction Mismatch Cases

```flux
from(bucket: "trading")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "bias_reliability")
  |> filter(fn: (r) => r.reliability_status == "DIRECTION_MISMATCH")
  |> yield(name: "mismatches")
```

### 4. GLM vs Bias Confidence Correlation

```flux
import "join"

glm = from(bucket: "trading")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "bias_reliability")
  |> filter(fn: (r) => r._field == "glm_confidence")

bias = from(bucket: "trading")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "bias_reliability")
  |> filter(fn: (r) => r._field == "bias_confidence")

join.inner(
  left: glm,
  right: bias,
  on: (l, r) => l._time == r._time,
  as: (l, r) => ({l with bias_confidence: r._value})
)
```

### 5. Bias Component Analysis for Disagreements

```flux
from(bucket: "trading")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "bias_reliability")
  |> filter(fn: (r) => r.reliability_status == "DIRECTION_MISMATCH" or r.reliability_status == "CONFIDENCE_MISMATCH")
  |> filter(fn: (r) => r._field == "trend_bias" or r._field == "momentum_bias" or r._field == "futures_bias")
  |> mean()
```

---

## 🎯 Use Cases

### 1. **Confidence Calibration**

**Question**: Is GLM overconfident or underconfident compared to bias scores?

**Analysis**:
```python
# If GLM confidence consistently higher than bias confidence:
# → GLM is overconfident, reduce trust in high GLM confidence

# If GLM confidence consistently lower than bias confidence:
# → GLM is underconfident, can trust GLM more even at moderate confidence
```

### 2. **Bias Component Reliability**

**Question**: Which bias components (trend, momentum, futures) correlate best with GLM confidence?

**Analysis**:
```flux
# Compare GLM confidence with each bias component
# Find which component has highest correlation
# Adjust bias weights accordingly
```

### 3. **Market Regime Detection**

**Question**: In which market conditions do GLM and bias agree/disagree?

**Analysis**:
```flux
# Group by volatility_regime
# Calculate agreement rate for each regime
# Identify regimes where bias is more/less reliable
```

### 4. **Decision Override Strategy**

**Question**: When should we override GLM decision based on bias disagreement?

**Strategy**:
```python
if reliability_status == "DIRECTION_MISMATCH":
    if abs(composite_bias) > 0.7 and bias_confidence > 0.8:
        # Strong bias signal, weak GLM agreement
        # → Consider following bias instead of GLM
        logger.warning("Strong bias override candidate")
```

---

## 📝 Log Output Examples

### High Agreement (🟢)

```
INFO: 🟢 Bias Reliability Test: HIGH_AGREEMENT | GLM_conf=82.0% Bias_conf=78.0% | GLM_dir=BUY Bias_dir=0.75 | diff=0.04
INFO: GLM decision: action=BUY amount=0.1500 leverage=10.0 confidence=82.0
INFO:   Primary reason: All 3 timeframes aligned bullish, 30m MACD crossed above signal
INFO:   Secondary reason: RSI bounced from 32 (oversold), L/S ratio 0.68 (contrarian bullish)
```

### Direction Mismatch (🔴)

```
INFO: 🔴 Bias Reliability Test: DIRECTION_MISMATCH | GLM_conf=75.0% Bias_conf=65.0% | GLM_dir=BUY Bias_dir=-0.45 | diff=0.10
WARNING: ⚠️ Bias-GLM Disagreement Detected:
  GLM: action=BUY confidence=75.0% reasons=[All 3 timeframes aligned bullish, 30m MACD cr..., RSI bounced from 32 (oversold), L/S ratio 0.68 ...]
  Bias: composite=-0.45 trend=-0.38 momentum=-0.52 futures=-0.25 volatility=0.65
```

### Confidence Mismatch (🟠)

```
INFO: 🟠 Bias Reliability Test: CONFIDENCE_MISMATCH | GLM_conf=90.0% Bias_conf=45.0% | GLM_dir=BUY Bias_dir=0.35 | diff=0.45
WARNING: ⚠️ Bias-GLM Disagreement Detected:
  GLM: action=BUY confidence=90.0% reasons=[Perfect setup: 4h uptrend + 30m breakout + 1m..., Volume surge confirms breakout, ATR expanding]
  Bias: composite=0.35 trend=0.42 momentum=0.28 futures=0.15 volatility=0.85
```

---

## 🔬 Research Questions

### Week 1-2: Data Collection

1. What is the average confidence difference?
2. What is the agreement rate (HIGH_AGREEMENT %)?
3. How often do direction mismatches occur?

### Week 3-4: Pattern Analysis

1. Which bias components correlate best with GLM confidence?
2. In which volatility regimes do they agree/disagree most?
3. Does GLM confidence predict trade success better than bias confidence?

### Month 2: Strategy Optimization

1. Should we adjust bias weights based on GLM feedback?
2. Should we implement GLM override logic for strong bias disagreements?
3. Can we improve GLM prompt to align better with bias scores?

---

## 🎯 Next Steps

### Phase 1: Monitoring (Week 1-2)
- ✅ Collect bias reliability data
- ⏳ Build Grafana dashboard for visualization
- ⏳ Set up alerts for high disagreement rates

### Phase 2: Analysis (Week 3-4)
- ⏳ Analyze correlation between GLM confidence and trade outcomes
- ⏳ Analyze correlation between bias confidence and trade outcomes
- ⏳ Identify patterns in disagreement cases

### Phase 3: Optimization (Month 2)
- ⏳ Adjust bias component weights based on findings
- ⏳ Implement confidence calibration adjustments
- ⏳ Test override strategies in paper trading

---

## 📊 Expected Insights

### Scenario A: High Agreement (>80%)
**Interpretation**: Bias scores are well-calibrated with GLM's assessment  
**Action**: Continue using both, no changes needed

### Scenario B: GLM Overconfident
**Interpretation**: GLM confidence consistently 20-30% higher than bias  
**Action**: Apply confidence discount factor to GLM (e.g., multiply by 0.8)

### Scenario C: GLM Underconfident
**Interpretation**: GLM confidence consistently 20-30% lower than bias  
**Action**: Can trust GLM even at moderate confidence (50-70%)

### Scenario D: Frequent Direction Mismatches
**Interpretation**: Bias and GLM see market differently  
**Action**: Investigate which is more accurate, adjust accordingly

---

## 🎉 Summary

**Implemented**:
- ✅ Structured GLM output with confidence and reasons
- ✅ Bias reliability test comparing GLM vs bias scores
- ✅ InfluxDB logging for analysis
- ✅ Warning system for high disagreements

**Benefits**:
- **Data-driven confidence calibration**: Know when to trust GLM vs bias
- **Component reliability analysis**: Identify which bias components work best
- **Market regime adaptation**: Adjust strategy based on regime-specific patterns
- **Continuous improvement**: Use feedback loop to optimize both systems

**Next**: Collect 1-2 weeks of data, then analyze patterns and optimize! 🚀
