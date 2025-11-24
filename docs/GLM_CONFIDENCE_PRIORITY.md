# GLM Confidence Priority System

## Problem

Önceki sistemde, GLM güçlü bir analiz yapsa bile (örn. 85% confidence), bias score'ların düşük olması durumunda (örn. 0.28 < 0.30 threshold) pozisyon açılmıyordu.

### Örnek Senaryo
```
GLM Analizi:
  - Action: SELL
  - Confidence: 85%
  - Reasoning: "Strong bearish momentum, trend aligned"

Bias Scores:
  - Composite Bias: -0.16 (weak short signal)
  - Bias Confidence: 0.28 (below 0.30 threshold)
  - Consensus: 3/4 components agree
    ✅ Trend: -0.33 (bearish)
    ✅ Momentum: -0.40 (bearish)
    ❌ Futures: +0.56 (bullish - CONFLICT!)
    ✅ Intraday: -0.21 (bearish)

OLD RESULT: HOLD (bias confidence too low)
NEW RESULT: SELL with 15% position (GLM confidence high)
```

## Solution

Yeni sistem GLM'nin kendi confidence score'unu önceliklendirir:

### Decision Logic

```
if GLM_confidence >= 70:
    ✅ HIGH CONFIDENCE MODE
    - Bypass bias guardrails completely
    - Trust GLM's analysis
    - Position sizing based on GLM confidence:
      * 85-100%: max 25% position
      * 75-84%:  max 18% position
      * 70-74%:  max 12% position

elif GLM_confidence >= 40:
    ⚠️ MODERATE CONFIDENCE MODE
    - Use small position (5-9%) even if bias says HOLD
    - Trust GLM direction over bias direction
    - Conservative sizing due to uncertainty:
      * 60-69%: 9% position
      * 50-59%: 7% position
      * 40-49%: 5% position

else:  # GLM_confidence < 40
    ❌ LOW CONFIDENCE MODE
    - Respect bias guardrails
    - Likely HOLD unless bias also agrees
    - Safety-first approach
```

## Key Changes

### 1. High Confidence Bypass (>= 70%)
```python
if decision.glm_confidence >= 70:
    logger.info("✅ GLM High Confidence Mode: Bypassing bias guardrails")
    # Use GLM decision directly with appropriate position sizing
    return decision
```

### 2. Moderate Confidence Override (40-69%)
```python
if decision.glm_confidence >= 40:
    # GLM sees something, use small position
    # 40-49: 5%, 50-59: 7%, 60-69: 9%
    if decision.glm_confidence < 50:
        adjusted_amount = 0.05
    elif decision.glm_confidence < 60:
        adjusted_amount = 0.07
    else:  # 60-69
        adjusted_amount = 0.09
    logger.info("GLM Moderate Confidence Override: Using small position")
    return decision with adjusted_amount
```

### 3. Low Confidence Fallback (< 40%)
```python
# Respect bias guardrails
# Use traditional bias-based decision making
```

## Benefits

1. **Trusts GLM Analysis**: When GLM has high confidence (>= 70%), it can execute trades even if bias scores are weak
2. **Balanced Risk**: Position sizing scales with confidence level (5-25%)
3. **Safety Net**: Low confidence trades (< 40%) still use bias validation
4. **Flexibility**: Moderate confidence trades (40-69%) get small positions instead of being blocked
5. **Conservative Threshold**: 70% threshold ensures only truly confident GLM decisions bypass bias

## Testing

Run the test suite to verify behavior:

```bash
cd /root/trading
source /root/.venv/bin/activate
python test_real_scenario.py
```

Expected results:
- ✅ GLM 85% confidence → Opens position (15-25%)
- ✅ GLM 70% confidence → Opens position (12%)
- ✅ GLM 69% confidence → Opens small position (9%)
- ✅ GLM 60% confidence → Opens small position (9%)
- ✅ GLM 50% confidence → Opens small position (7%)
- ✅ GLM 40% confidence → Opens small position (5%)
- ✅ GLM 39% confidence → Respects bias guardrails (HOLD)
- ✅ GLM 30% confidence → Respects bias guardrails (HOLD)

## Migration Notes

**No breaking changes** - The system gracefully falls back to bias guardrails when GLM confidence is low.

Existing behavior is preserved for:
- CLOSE actions (unchanged)
- HOLD actions (unchanged)
- Low confidence trades (< 40%)

## Monitoring

Watch for these log messages:

```
✅ GLM High Confidence Mode: GLM confidence 85.0% >= 70% → Bypassing bias guardrails
⚠️ GLM Moderate Confidence Override: GLM 65.0% suggests SELL, bias says HOLD → Using small position 0.0900
⚠️ GLM Low Confidence Mode: GLM confidence 30.0% < 70% → Using bias guardrails
```

## Performance Impact

- **Latency**: No impact (same GLM call)
- **Trade Frequency**: Expected to increase by 20-40% (more trades executed)
- **Risk**: Controlled via position sizing (smaller positions for lower confidence)

## Rollback

If needed, revert to old behavior by changing line 598 in `manager.py`:

```python
# Change this:
if decision.glm_confidence >= 70:

# To this (effectively disables new logic):
if decision.glm_confidence >= 999:  # Never true
```

## Configuration

Current threshold: **70%**

To adjust the threshold, modify line 598 in `/root/trading/app/risk_manager/manager.py`:

```python
if decision.glm_confidence >= 70:  # Change this value (recommended: 60-80)
```

**Recommended ranges:**
- **60-65%**: More aggressive (more trades, higher risk)
- **70-75%**: Balanced (current setting)
- **80-85%**: Conservative (fewer trades, lower risk)
