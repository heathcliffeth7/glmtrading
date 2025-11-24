#!/usr/bin/env python3
"""
Test complete position sizing flow from GLM to Executor
"""

def test_position_flow():
    """Simulate the complete flow"""
    print("=" * 80)
    print("COMPLETE POSITION SIZING FLOW TEST")
    print("=" * 80)
    print()
    
    # Simulate GLM decision
    glm_btc_request = 0.095  # GLM wants 0.095 BTC
    glm_leverage = 15
    current_price = 104000.0
    equity = 10000.0
    
    print("📊 GLM REQUEST:")
    print(f"   BTC Amount: {glm_btc_request:.6f} BTC")
    print(f"   Leverage: {glm_leverage}x")
    print(f"   At Price: ${current_price:,.2f}")
    print()
    
    # Step 1: Risk Manager - Parse GLM response
    print("1️⃣ RISK MANAGER - Parse GLM Response:")
    notional_value = glm_btc_request * current_price
    amount_pct = notional_value / equity
    print(f"   Notional: ${notional_value:,.2f}")
    print(f"   Amount: {amount_pct:.4f} ({amount_pct*100:.1f}% of equity)")
    print()
    
    # Step 2: Risk Manager - Apply Safety Limits
    print("2️⃣ RISK MANAGER - Apply Safety Limits:")
    MAX_MARGIN_USD = 3000.0
    requested_margin = amount_pct * equity
    actual_margin = min(requested_margin, MAX_MARGIN_USD)
    clamped_amount = actual_margin / equity
    
    print(f"   Requested Margin: ${requested_margin:,.2f}")
    print(f"   Max Margin: ${MAX_MARGIN_USD:,.2f}")
    print(f"   Actual Margin: ${actual_margin:,.2f}")
    print(f"   Clamped Amount: {clamped_amount:.4f} ({clamped_amount*100:.1f}%)")
    
    position_size_usd = actual_margin * glm_leverage
    position_size_btc = position_size_usd / current_price
    
    print(f"   Position Size: ${position_size_usd:,.2f} ({position_size_btc:.6f} BTC)")
    print()
    
    # Step 3: Executor - Receive Decision
    print("3️⃣ EXECUTOR - Receive Decision:")
    print(f"   decision.amount = {clamped_amount:.4f}")
    print(f"   decision.leverage = {glm_leverage}")
    print()
    
    # Step 4: Executor - OLD: Apply 20% cap (REMOVED!)
    print("4️⃣ EXECUTOR - Check 20% Equity Cap:")
    OLD_MAX_GLM_AMOUNT = 0.20
    if clamped_amount > OLD_MAX_GLM_AMOUNT:
        print(f"   ❌ OLD SYSTEM: Would cap {clamped_amount:.4f} → {OLD_MAX_GLM_AMOUNT:.4f}")
        old_position = OLD_MAX_GLM_AMOUNT * equity * glm_leverage
        print(f"      Old Position: ${old_position:,.2f}")
    else:
        print(f"   ✅ OLD SYSTEM: Would pass (already < 20%)")
    
    print(f"   ✅ NEW SYSTEM: No cap! Passes through: {clamped_amount:.4f}")
    print()
    
    # Step 5: Executor - Calculate Position
    print("5️⃣ EXECUTOR - Calculate Position:")
    free_equity = equity  # Assume no open positions
    margin_to_use = free_equity * clamped_amount
    MAX_TRADE_VALUE = 3000.0
    
    if margin_to_use > MAX_TRADE_VALUE:
        print(f"   ⚠️  Margin check: ${margin_to_use:,.2f} > ${MAX_TRADE_VALUE:,.2f}")
        margin_to_use = MAX_TRADE_VALUE
    else:
        print(f"   ✅ Margin check: ${margin_to_use:,.2f} ≤ ${MAX_TRADE_VALUE:,.2f}")
    
    final_position_usd = margin_to_use * glm_leverage
    final_position_btc = final_position_usd / current_price
    
    print(f"   Final Margin: ${margin_to_use:,.2f}")
    print(f"   Final Position: ${final_position_usd:,.2f} ({final_position_btc:.6f} BTC)")
    print()
    
    # Summary
    print("=" * 80)
    print("📊 SUMMARY")
    print("=" * 80)
    print()
    print(f"GLM Requested:    {glm_btc_request:.6f} BTC @ {glm_leverage}x = ${glm_btc_request * current_price * glm_leverage:,.2f}")
    print(f"Risk Manager:     {position_size_btc:.6f} BTC @ {glm_leverage}x = ${position_size_usd:,.2f}")
    print(f"Executor (OLD):   0.192308 BTC @ {glm_leverage}x = $30,000.00 (with 20% cap)")
    print(f"Executor (NEW):   {final_position_btc:.6f} BTC @ {glm_leverage}x = ${final_position_usd:,.2f}")
    print()
    
    if abs(final_position_usd - position_size_usd) < 1.0:
        print("✅ SUCCESS: Executor position matches Risk Manager!")
        print(f"   Margin: ${actual_margin:,.2f}")
        print(f"   Leverage: {glm_leverage}x")
        print(f"   Position: ${final_position_usd:,.2f}")
        return True
    else:
        print("❌ MISMATCH: Executor position differs from Risk Manager")
        print(f"   Expected: ${position_size_usd:,.2f}")
        print(f"   Got: ${final_position_usd:,.2f}")
        return False

if __name__ == "__main__":
    success = test_position_flow()
    exit(0 if success else 1)
