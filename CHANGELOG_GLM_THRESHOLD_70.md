# Changelog: GLM Confidence Threshold = 70%

**Date**: October 29, 2025  
**Version**: 2.0  
**Status**: ✅ Active

---

## Summary

GLM confidence threshold updated from **60%** to **70%** for bypassing bias guardrails.

## What Changed

### Before (Threshold = 60%)
```
GLM >= 60%: Bypass bias guardrails
GLM 40-59%: Small position (5-8%)
GLM < 40%: Respect bias
```

### After (Threshold = 70%)
```
GLM >= 70%: Bypass bias guardrails
GLM 40-69%: Small position (5-9%)
GLM < 40%: Respect bias
```

## Impact

### More Conservative
- Only GLM confidence >= 70% bypasses bias (was 60%)
- GLM 60-69% now uses small position (9%) instead of full bypass
- Better risk management for moderate confidence signals

### Position Sizing Table

| GLM Confidence | Old Behavior | New Behavior | Change |
|----------------|--------------|--------------|--------|
| **85-100%** | 25% (bypass) | 25% (bypass) | ✅ Same |
| **75-84%** | 18% (bypass) | 18% (bypass) | ✅ Same |
| **70-74%** | 12% (bypass) | 12% (bypass) | ✅ Same |
| **60-69%** | 12% (bypass) | **9% (small)** | ⚠️ More conservative |
| **50-59%** | 7-8% (small) | 7% (small) | ✅ Similar |
| **40-49%** | 5% (small) | 5% (small) | ✅ Same |
| **< 40%** | Bias-based | Bias-based | ✅ Same |

## Files Modified

1. **`/root/trading/app/risk_manager/manager.py`**
   - Line 587: Updated docstring (60% → 70%)
   - Line 598: Updated threshold check (60% → 70%)
   - Line 600: Updated log message (60% → 70%)
   - Line 608: Updated comment (60-74 → 70-74)
   - Line 641: Updated log message (60% → 70%)
   - Lines 650-659: Updated moderate confidence range (40-59 → 40-69)
   - Lines 715-723: Updated moderate confidence range (40-59 → 40-69)

2. **`/root/trading/docs/GLM_CONFIDENCE_PRIORITY.md`**
   - Updated all references from 60% to 70%
   - Added configuration section
   - Updated test expectations

3. **Test Files**
   - `test_real_scenario.py`: Updated expectations
   - `test_threshold_70.py`: New comprehensive test suite

## Testing

All tests pass with new threshold:

```bash
cd /root/trading
source /root/.venv/bin/activate
python test_threshold_70.py
```

**Results**: ✅ 8/8 tests passed

## Rationale

User requested: *"GLM confidence'de %70'in altında bias'a göre olsun"*

**Benefits:**
1. More conservative approach - only high confidence (70+) bypasses bias
2. Moderate confidence (60-69) still gets small position (9%)
3. Better alignment with bias scores for borderline cases
4. Reduced risk of false signals in 60-69% range

## Rollback Plan

If needed, revert by changing line 598 in `manager.py`:

```python
if decision.glm_confidence >= 70:  # Change back to 60
```

## Monitoring

Watch for these patterns in logs:

**High Confidence (>= 70%)**
```
✅ GLM High Confidence Mode: GLM confidence 75.0% >= 70% → Bypassing bias guardrails
Using GLM decision directly: action=SELL amount=0.1800 confidence=75.0%
```

**Moderate Confidence (60-69%)**
```
⚠️ GLM Low Confidence Mode: GLM confidence 65.0% < 70% → Using bias guardrails
GLM Moderate Confidence Override: GLM 65.0% suggests SELL, bias says HOLD → Using small position 0.0900
```

**Low Confidence (< 40%)**
```
⚠️ GLM Low Confidence Mode: GLM confidence 35.0% < 70% → Using bias guardrails
Confidence guardrail: insufficient confidence (bias=0.28, GLM=35.0%) -> HOLD
```

## Performance Expectations

- **Trade Frequency**: Slight decrease (5-10%) compared to 60% threshold
- **Win Rate**: Expected to improve (filtering out 60-69% borderline cases)
- **Risk**: Lower (more conservative threshold)
- **Drawdown**: Expected to reduce (better risk management)

## Next Steps

1. Monitor live trading performance
2. Track GLM confidence distribution (how often 60-69% occurs)
3. Evaluate win rate improvement after 1 week
4. Consider fine-tuning if needed (65% or 75%)

---

**Approved by**: User  
**Implemented by**: Cascade AI  
**Status**: Production Ready ✅
