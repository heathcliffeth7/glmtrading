#!/usr/bin/env python3
"""
Test script for new position limit calculations
Verifies margin-based position sizing with leverage
"""

def test_position_calculation():
    """Test the new margin × leverage position sizing"""
    
    # Test parameters
    equity = 10000.0  # $10k starting equity
    MAX_MARGIN_USD = 3000.0
    current_price = 104000.0  # BTC price
    
    test_cases = [
        # (GLM requested equity %, leverage) -> expected margin, expected position
        (0.95, 15),  # GLM wants 95% equity with 15x leverage
        (0.50, 20),  # GLM wants 50% equity with 20x leverage
        (0.30, 10),  # GLM wants 30% equity with 10x leverage
        (0.20, 5),   # GLM wants 20% equity with 5x leverage
    ]
    
    print("=" * 80)
    print("POSITION LIMIT CALCULATION TEST")
    print("=" * 80)
    print(f"Equity: ${equity:,.2f}")
    print(f"Max Margin: ${MAX_MARGIN_USD:,.2f}")
    print(f"BTC Price: ${current_price:,.2f}")
    print()
    
    for glm_amount_pct, leverage in test_cases:
        print("-" * 80)
        print(f"TEST: GLM requests {glm_amount_pct*100:.0f}% equity @ {leverage}x leverage")
        print()
        
        # Step 1: GLM's requested margin
        requested_margin_usd = glm_amount_pct * equity
        print(f"  1. GLM requested margin: ${requested_margin_usd:,.2f} ({glm_amount_pct*100:.0f}% of equity)")
        
        # Step 2: Apply margin limit
        actual_margin_usd = min(requested_margin_usd, MAX_MARGIN_USD)
        was_clamped = actual_margin_usd < requested_margin_usd
        
        if was_clamped:
            print(f"  2. ⚠️  Margin clamped to: ${actual_margin_usd:,.2f} (max margin limit)")
        else:
            print(f"  2. ✅ Margin approved: ${actual_margin_usd:,.2f} (within limits)")
        
        # Step 3: Calculate leveraged position
        position_size_usd = actual_margin_usd * leverage
        position_size_btc = position_size_usd / current_price
        
        print(f"  3. Position calculation:")
        print(f"     Margin: ${actual_margin_usd:,.2f}")
        print(f"     Leverage: {leverage}x")
        print(f"     Position: ${position_size_usd:,.2f} ({position_size_btc:.6f} BTC)")
        
        # Step 4: Calculate equity allocation
        clamped_amount_pct = actual_margin_usd / equity
        print(f"  4. Final equity allocation: {clamped_amount_pct*100:.1f}% (${actual_margin_usd:,.2f} margin)")
        
        # Step 5: Show comparison
        print()
        print(f"  📊 SUMMARY:")
        print(f"     Margin Used: ${actual_margin_usd:,.2f} ({clamped_amount_pct*100:.1f}% of ${equity:,.2f})")
        print(f"     Position Size: ${position_size_usd:,.2f}")
        print(f"     BTC Amount: {position_size_btc:.6f} BTC")
        print(f"     Effective Leverage: {position_size_usd/actual_margin_usd:.1f}x")
        print()

def test_old_vs_new():
    """Compare old vs new position sizing"""
    
    print("=" * 80)
    print("OLD vs NEW POSITION SIZING COMPARISON")
    print("=" * 80)
    print()
    
    equity = 10000.0
    current_price = 104000.0
    glm_btc_request = 0.095  # GLM requested 0.095 BTC
    leverage = 15
    
    print(f"Scenario: GLM requests 0.095 BTC with 15x leverage @ ${current_price:,.2f}")
    print()
    
    # OLD SYSTEM
    print("🔴 OLD SYSTEM:")
    print()
    
    # Step 1: 50% equity limit
    max_position_value_old = equity * 0.5
    max_btc_old_step1 = max_position_value_old / current_price
    btc_after_step1 = min(glm_btc_request, max_btc_old_step1)
    
    print(f"  1. 50% equity limit: {max_position_value_old:,.2f} USD")
    print(f"     Max BTC: {max_btc_old_step1:.6f}")
    print(f"     After limit: {btc_after_step1:.6f} BTC")
    
    # Step 2: 3000 USD "unleveraged" limit
    max_notional_old = 3000.0
    max_btc_old_step2 = max_notional_old / current_price
    btc_after_step2 = min(btc_after_step1, max_btc_old_step2)
    
    print(f"  2. 3000 USD 'unleveraged' limit: {max_notional_old:,.2f} USD")
    print(f"     Max BTC: {max_btc_old_step2:.6f}")
    print(f"     After limit: {btc_after_step2:.6f} BTC")
    
    position_old = btc_after_step2 * current_price
    
    print()
    print(f"  ❌ FINAL POSITION (OLD): {btc_after_step2:.6f} BTC = ${position_old:,.2f}")
    print(f"     Effective leverage: {leverage}x (not actually applied)")
    print(f"     Margin used: ~${position_old:,.2f}")
    print()
    
    # NEW SYSTEM
    print("🟢 NEW SYSTEM:")
    print()
    
    # Step 1: Convert GLM request to margin
    requested_notional = glm_btc_request * current_price
    requested_margin = requested_notional  # GLM's amount is the margin (equity used)
    
    print(f"  1. GLM request: {glm_btc_request:.6f} BTC = ${requested_notional:,.2f}")
    print(f"     Requested margin: ${requested_margin:,.2f}")
    
    # Step 2: Apply margin limit
    MAX_MARGIN_USD = 3000.0
    actual_margin = min(requested_margin, MAX_MARGIN_USD)
    
    print(f"  2. Margin limit: ${MAX_MARGIN_USD:,.2f}")
    print(f"     Actual margin: ${actual_margin:,.2f}")
    
    # Step 3: Apply leverage
    position_new = actual_margin * leverage
    btc_new = position_new / current_price
    
    print(f"  3. Apply leverage: {leverage}x")
    print(f"     Position: ${position_new:,.2f}")
    print(f"     BTC amount: {btc_new:.6f} BTC")
    
    print()
    print(f"  ✅ FINAL POSITION (NEW): {btc_new:.6f} BTC = ${position_new:,.2f}")
    print(f"     Margin used: ${actual_margin:,.2f} ({actual_margin/equity*100:.1f}% of equity)")
    print(f"     Effective leverage: {position_new/actual_margin:.1f}x")
    print()
    
    # COMPARISON
    print("=" * 80)
    print("📊 COMPARISON:")
    print()
    print(f"  Position Size:  OLD ${position_old:,.2f} → NEW ${position_new:,.2f}")
    print(f"  Increase:       {(position_new/position_old - 1)*100:.1f}% larger")
    print(f"  BTC Amount:     OLD {btc_after_step2:.6f} → NEW {btc_new:.6f}")
    print(f"  Leverage Used:  OLD ~1x (not applied) → NEW {leverage}x (fully applied)")
    print()

def test_max_position():
    """Test maximum position size achievable"""
    
    print("=" * 80)
    print("MAXIMUM POSITION SIZE TEST")
    print("=" * 80)
    print()
    
    equity = 10000.0
    MAX_MARGIN_USD = 3000.0
    MAX_LEVERAGE = 20
    current_price = 104000.0
    
    max_position = MAX_MARGIN_USD * MAX_LEVERAGE
    max_btc = max_position / current_price
    
    print(f"System Configuration:")
    print(f"  Equity: ${equity:,.2f}")
    print(f"  Max Margin: ${MAX_MARGIN_USD:,.2f} ({MAX_MARGIN_USD/equity*100:.0f}% of equity)")
    print(f"  Max Leverage: {MAX_LEVERAGE}x")
    print()
    print(f"Maximum Achievable Position:")
    print(f"  Position Size: ${max_position:,.2f}")
    print(f"  BTC Amount: {max_btc:.6f} BTC")
    print(f"  At Price: ${current_price:,.2f}")
    print()
    print(f"✅ This allows aggressive trading while limiting risk to ${MAX_MARGIN_USD:,.2f} margin")
    print()

if __name__ == "__main__":
    test_position_calculation()
    print()
    test_old_vs_new()
    print()
    test_max_position()
    
    print("=" * 80)
    print("✅ All tests completed!")
    print("=" * 80)
