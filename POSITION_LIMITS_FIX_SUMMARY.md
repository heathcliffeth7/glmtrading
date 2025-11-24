# Position Limits & Execution Logging Fix Summary

**Date**: 2025-11-05
**Issue**: GLM SELL signals with 82% confidence were not executing due to double position limiting and unclear skip reasons

## Problems Identified

### 1. Double Position Limiting
- **First Limit** (manager.py:2224): 50% of equity cap (~$5,000 from $10k equity)
- **Second Limit** (manager.py:999): 3000 USD "unleveraged" cap
- **Result**: GLM requesting 0.095 BTC → reduced to 0.048 BTC → further reduced to 0.029 BTC

### 2. Incorrect Margin Calculation
- Code treated $3,000 as "unleveraged notional" instead of margin
- With 20x leverage: Should allow $3,000 margin × 20 = $60,000 position
- Actually allowed: Only $3,000 position (no leverage benefit)

### 3. JSON Truncation
- Required 800-1000 character justifications
- GLM responses exceeded token limits and got cut mid-sentence
- Caused JSON parsing failures → fallback to HOLD

### 4. Unclear Skip Reasons
- Logs showed "SKIP" without specific reason
- Could be: cooldown, stale price, stale decision, or price change threshold
- No way to diagnose why trades were blocked

## Changes Made

### 1. Removed 50% Equity Limit ✅
**File**: `app/risk_manager/manager.py:2223-2229`

**Before**:
```python
# LIMIT: Prevent GLM from requesting too large positions
max_position_value = equity * 0.5  # Max 50% of equity per trade
if current_price > 0:
    max_btc_quantity = max_position_value / current_price
    if quantity > max_btc_quantity:
        logger.warning("GLM requested too large position...")
        quantity = max_btc_quantity
```

**After**:
```python
# NOTE: Position size limiting is handled by _apply_safety_limits()
# No arbitrary % of equity limit here - GLM has freedom within safety limits
# Safety limits: max 3000 USD margin, max 20x leverage = up to 60k USD position
```

### 2. Fixed Margin Calculation ✅
**File**: `app/risk_manager/manager.py:969-1049`

**Before**:
```python
# Max notional value = 3000 USD (unleveraged)
max_notional_usd = 3000.0
```

**After**:
```python
# Maximum margin (teminat) that can be allocated
MAX_MARGIN_USD = 3000.0

# Calculate leveraged position size
position_size_usd = actual_margin_usd * clamped_leverage
position_size_btc = position_size_usd / current_price
```

**New Calculation**:
- Margin: $3,000 (equity used as collateral)
- Leverage: up to 20x
- Position: $3,000 × 20 = **$60,000** (0.577 BTC @ $104k)

### 3. Reduced Justification Length ✅
**Files**: 
- `app/risk_manager/nof1_prompt_builder.py:785, 803, 956-957, 964`
- `app/risk_manager/manager.py:1840, 2116-2117, 2141`

**Before**: 800-1000 characters MANDATORY
**After**: 400-600 characters recommended

### 4. Added Detailed Execution Logging ✅
**File**: `app/executor/executor.py:283-430`

**Added logs for**:
- Decision staleness check with age
- Price freshness validation
- HOLD cooldown status
- Same-direction trade cooldown with elapsed/remaining time
- Price change threshold check

**Example output**:
```
🔍 Decision age check: 2.3s old (max: 60s)
🔍 Checking price freshness for SELL action...
✅ Price is fresh: 103931.80
🔍 Checking same-direction trade cooldown for SELL...
   └─ Last trade direction: SELL, Last trade time: 2025-11-05 18:10:15
❌ SKIP REASON: Same-direction trade cooldown - SELL blocked (245s elapsed, 655s remaining)
```

### 5. Improved Telegram Status Display ✅
**File**: `app/orchestrator/runtime.py:392-418`

**Before**: `Durum: SKIP` (ambiguous)

**After**: Detailed status codes:
- `SKIP_COOLDOWN`: Trade blocked by frequency limit
- `SKIP_HOLD`: Normal HOLD decision
- `SKIP_FREQUENCY`: Same-direction trade too soon
- `SKIP_STALE`: Decision too old
- `SKIP_NO_PRICE`: No fresh price data
- `SKIP_PRICE_CHANGE`: Price change < 3% threshold
- `EXECUTED`: Trade executed successfully

## Expected Behavior After Fix

### Scenario: GLM SELL 82% Confidence

**Before**:
```
GLM: 0.095 BTC (~$9,900)
↓ [50% equity limit]
0.048 BTC (~$5,000)
↓ [3000 USD "unleveraged" limit]
0.029 BTC (~$3,000)
Status: SKIP (unknown reason)
```

**After**:
```
GLM: 0.095 BTC (~$9,900 equity = 99%)
↓ [No 50% limit]
0.095 BTC (~$9,900)
↓ [3000 USD margin with 15x leverage]
0.433 BTC ($45,000 position, $3,000 margin)
Status: EXECUTED or clear SKIP reason with timing
```

**Logs**:
```
✅ Position within limits: 3000.00 USD margin, 45000.00 USD position (0.433000 BTC) @ 15x leverage
🔍 Decision age check: 2.1s old (max: 60s)
🔍 Checking price freshness for SELL action...
✅ Price is fresh: 103931.80
🔍 Checking same-direction trade cooldown for SELL...
✅ Same-direction cooldown check passed
🔍 Checking 3% price change threshold for SELL...
✅ Price change threshold passed: 4.2% change from last trade
→ Executing SELL 0.433 BTC @ 15x leverage
```

## Risk Management Still Active

✅ **Maximum margin**: $3,000 per trade
✅ **Maximum leverage**: 20x (clamped from GLM request)
✅ **Maximum position**: $60,000 ($3,000 × 20x)
✅ **Confidence threshold**: 80% minimum for BUY/SELL
✅ **Cooldown periods**: 15 minutes same-direction
✅ **Price freshness**: Required for all trades
✅ **Decision staleness**: 60 seconds maximum age
✅ **Price change threshold**: 3% minimum change

## Files Modified

1. `app/risk_manager/manager.py`
   - Line 969-1049: Fixed `_apply_safety_limits()` margin calculation
   - Line 1840: Reduced system prompt justification requirement
   - Line 2116-2117: Reduced user prompt justification requirement
   - Line 2141: Reduced fallback prompt justification requirement
   - Line 2223-2225: Removed 50% equity limit

2. `app/risk_manager/nof1_prompt_builder.py`
   - Line 785: Reduced justification requirement in header
   - Line 803: Reduced justification requirement in format
   - Line 956-957: Reduced justification requirement in examples
   - Line 964: Reduced justification requirement in rules

3. `app/executor/executor.py`
   - Line 286-287: Added decision age logging
   - Line 291: Improved stale decision error message
   - Line 307-315: Added price freshness logging
   - Line 319-332: Added HOLD cooldown detailed logging
   - Line 338-359: Added same-direction cooldown detailed logging
   - Line 424-427: Added price change threshold logging

4. `app/orchestrator/runtime.py`
   - Line 393-418: Improved status display with specific skip reasons

## Testing Recommendations

1. **Monitor next SELL signal**:
   - Check logs for position size calculation
   - Verify margin × leverage = position size
   - Confirm no arbitrary % equity limit applied

2. **Check JSON parsing**:
   - Verify GLM responses complete successfully
   - No truncation errors in logs
   - Justifications 400-600 characters

3. **Verify skip logging**:
   - Clear reasons when trades are skipped
   - Cooldown timers show elapsed/remaining
   - Status codes in Telegram are specific

4. **Position size validation**:
   ```bash
   # Check that positions can reach $60k with $3k margin @ 20x
   # Example: BTC @ $100k
   # Max position: $60,000 / $100,000 = 0.6 BTC
   # Margin used: $3,000 (30% of $10k equity)
   ```

## Next Steps

1. ✅ All changes implemented
2. 🔄 Monitor system logs for next SELL signal
3. 🔄 Verify position sizes match margin × leverage formula
4. 🔄 Check Telegram messages show clear skip reasons
5. 🔄 Confirm JSON responses complete without truncation
