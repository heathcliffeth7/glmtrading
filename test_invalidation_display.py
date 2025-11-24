#!/usr/bin/env python3
"""
Test: Invalidation Condition Display
Mevcut pozisyonun invalidation condition'ının doğru gösterildiğini test eder
"""

import sys

sys.path.insert(0, '/root/trading')

from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder


def test_invalidation_display():
    """Test invalidation condition display in prompt"""
    
    print("=" * 80)
    print("TEST: Invalidation Condition Display")
    print("=" * 80)
    print()
    
    builder = Nof1PromptBuilder()
    
    # Test Case 1: LONG position with invalidation
    print("📊 Test Case 1: LONG Position with Invalidation Condition")
    print("-" * 80)
    
    portfolio_long = {
        'equity': 10500,
        'position': 0.05,  # LONG
        'entry_price': 110000,
        'current_price': 111000,
        'unrealized_pnl': 50,
        'leverage': 10,
        'exit_plan': {
            'profit_target': 115000,
            'stop_loss': 108000,
            'invalidation_condition': 'If price closes below 109000 on 3-minute candle'
        }
    }
    
    raw_data = {
        'symbol': 'BTCUSDT',
        'current_snapshots': {'30m': {'close': 111000}},
        'historical_arrays': {},
        'futures_data': {}
    }
    
    prompt_long = builder.build_prompt(raw_data, portfolio_long)
    
    # Extract position info
    lines = prompt_long.split('\n')
    in_position = False
    position_lines = []
    
    for line in lines:
        if 'Current live positions & performance:' in line:
            in_position = True
        if in_position:
            position_lines.append(line)
            if '⚠️ EXIT PLAN SUMMARY' in line:
                # Get next 4 lines
                idx = lines.index(line)
                position_lines.extend(lines[idx+1:idx+5])
                break
    
    for line in position_lines:
        print(line)
    
    print()
    print("✅ Verification:")
    prompt_text = '\n'.join(position_lines)
    
    checks = [
        ("'profit_target':", "'profit_target': 115000.00,  # 🎯 AUTO-CLOSE when reached"),
        ("'stop_loss':", "'stop_loss': 108000.00,  # 🛑 AUTO-CLOSE when triggered"),
        ("'invalidation_condition':", "# ⚠️ AUTO-CLOSE when met"),
        ("'⚠️ EXIT PLAN SUMMARY':", "EXIT PLAN SUMMARY"),
    ]
    
    all_passed = True
    for check_key, expected in checks:
        if check_key in prompt_text:
            print(f"  ✓ {check_key:30s} FOUND")
        else:
            print(f"  ✗ {check_key:30s} MISSING")
            all_passed = False
    
    print()
    
    # Test Case 2: SHORT position with invalidation
    print("📊 Test Case 2: SHORT Position with Invalidation Condition")
    print("-" * 80)
    
    portfolio_short = {
        'equity': 10300,
        'position': -0.08,  # SHORT
        'entry_price': 110000,
        'current_price': 109000,
        'unrealized_pnl': 80,
        'leverage': 8,
        'exit_plan': {
            'profit_target': 105000,
            'stop_loss': 112000,
            'invalidation_condition': 'If price closes above 111500 on 3-minute candle'
        }
    }
    
    raw_data_short = {
        'symbol': 'BTCUSDT',
        'current_snapshots': {'30m': {'close': 109000}},
        'historical_arrays': {},
        'futures_data': {}
    }
    
    prompt_short = builder.build_prompt(raw_data_short, portfolio_short)
    
    lines = prompt_short.split('\n')
    in_position = False
    position_lines = []
    
    for line in lines:
        if 'Current live positions & performance:' in line:
            in_position = True
        if in_position:
            position_lines.append(line)
            if '⚠️ EXIT PLAN SUMMARY' in line:
                idx = lines.index(line)
                position_lines.extend(lines[idx+1:idx+5])
                break
    
    for line in position_lines:
        print(line)
    
    print()
    print("✅ Verification:")
    prompt_text = '\n'.join(position_lines)
    
    if "'profit_target': 105000.00" in prompt_text:
        print("  ✓ SHORT Profit Target displayed correctly")
    else:
        print("  ✗ SHORT Profit Target missing")
        all_passed = False
    
    if "'stop_loss': 112000.00" in prompt_text:
        print("  ✓ SHORT Stop Loss displayed correctly")
    else:
        print("  ✗ SHORT Stop Loss missing")
        all_passed = False
    
    if "closes above 111500" in prompt_text:
        print("  ✓ SHORT Invalidation condition displayed correctly")
    else:
        print("  ✗ SHORT Invalidation condition missing")
        all_passed = False
    
    print()
    
    # Test Case 3: No position (FLAT)
    print("📊 Test Case 3: No Position (FLAT)")
    print("-" * 80)
    
    portfolio_flat = {
        'equity': 10000,
        'position': 0.0,  # FLAT
    }
    
    raw_data_flat = {
        'symbol': 'BTCUSDT',
        'current_snapshots': {'30m': {'close': 110000}},
        'historical_arrays': {},
        'futures_data': {}
    }
    
    prompt_flat = builder.build_prompt(raw_data_flat, portfolio_flat)
    
    if 'Current live positions: NONE (FLAT)' in prompt_flat:
        print("Current live positions: NONE (FLAT)")
        print()
        print("✅ Verification:")
        print("  ✓ FLAT position displayed correctly")
    else:
        print("  ✗ FLAT position display error")
        all_passed = False
    
    print()
    print("=" * 80)
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
    else:
        print("❌ SOME TESTS FAILED!")
    print("=" * 80)
    
    return all_passed

if __name__ == '__main__':
    success = test_invalidation_display()
    sys.exit(0 if success else 1)
