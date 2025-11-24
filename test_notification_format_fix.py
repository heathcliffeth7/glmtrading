#!/usr/bin/env python3
"""
Test notification format fix for both opening and closing trades
"""

from app.executor.executor import Executor
from app.risk_manager.manager import RiskDecision


def test_notification_formats():
    print("=== TEST NOTIFICATION FORMATS ===")
    
    executor = Executor()
    
    print("\n1. Testing OPEN SELL notification...")
    
    # Test opening a SELL position
    open_decision = RiskDecision(
        action="SELL",
        amount=0.05,
        leverage=5.0,
        reasoning="Test opening SELL position"
    )
    
    open_result = executor.execute(open_decision)
    print(f"OPEN result: {open_result.status} - {open_result.details}")
    
    metrics_after_open = executor.portfolio_metrics()
    print(f"Position after open: {metrics_after_open['position']:.4f} BTC")
    
    print("\n2. Testing CLOSE notification...")
    
    # Test closing the position
    close_decision = RiskDecision(
        action="CLOSE",
        amount=1.0,
        leverage=1.0,
        reasoning="Test closing position"
    )
    
    close_result = executor.execute(close_decision)
    print(f"CLOSE result: {close_result.status} - {close_result.details}")
    
    metrics_after_close = executor.portfolio_metrics()
    print(f"Position after close: {metrics_after_close['position']:.4f} BTC")
    
    print("\n3. Testing OPEN BUY notification...")
    
    # Test opening a BUY position
    buy_decision = RiskDecision(
        action="BUY",
        amount=0.05,
        leverage=5.0,
        reasoning="Test opening BUY position"
    )
    
    buy_result = executor.execute(buy_decision)
    print(f"BUY result: {buy_result.status} - {buy_result.details}")
    
    metrics_after_buy = executor.portfolio_metrics()
    print(f"Position after buy: {metrics_after_buy['position']:.4f} BTC")
    
    print("\n4. Testing CLOSE BUY notification...")
    
    # Test closing the BUY position
    close_buy_decision = RiskDecision(
        action="CLOSE",
        amount=1.0,
        leverage=1.0,
        reasoning="Test closing BUY position"
    )
    
    close_buy_result = executor.execute(close_buy_decision)
    print(f"CLOSE BUY result: {close_buy_result.status} - {close_buy_result.details}")
    
    final_metrics = executor.portfolio_metrics()
    print(f"Final position: {final_metrics['position']:.4f} BTC")
    
    # Check recent trades to see the pattern
    print("\n5. Recent trades analysis:")
    recent_trades = executor.get_recent_trades_with_pnl(limit=4)
    
    for i, trade in enumerate(recent_trades, 1):
        print(f"Trade {i}: {trade['side']} {trade['amount']:.4f} BTC @ ${trade['open_price']:,.2f}")
        print(f"  - Closed: {trade['is_closed']}")
        print(f"  - PnL: ${trade['pnl']:,.2f}")
        
        # Expected notification headers based on trade type and position changes
        if trade['side'] == 'SELL':
            expected_action = "SHORT AÇILDI"
        else:  # BUY
            expected_action = "LONG AÇILDI"
            
        print(f"  - Expected notification: 🚨 İşlem Gerçekleşti - {expected_action}")
        print()

def test_position_status_descriptions():
    print("=== TEST POSITION STATUS DESCRIPTIONS ===")
    
    executor = Executor()
    
    test_cases = [
        (0.0, 0.0, "Pozisyon yok"),
        (0.5, 0.0, "Pozisyon kapandı"),
        (0.0, 0.3, "Yeni pozisyon açıldı"),
        (0.2, -0.3, "Pozisyon yön değiştirdi"),
        (0.3, 0.5, "Pozisyon artırıldı"),
        (0.5, 0.3, "Pozisyon azaltıldı"),
        (-0.2, 0.0, "Pozisyon kapandı"),
        (0.0, -0.4, "Yeni pozisyon açıldı"),
        (-0.3, 0.2, "Pozisyon yön değiştirdi"),
        (-0.4, -0.6, "Pozisyon artırıldı"),
        (-0.6, -0.4, "Pozisyon azaltıldı"),
    ]
    
    for before, after, expected_contains in test_cases:
        result = executor._describe_position_change(before, after)
        status = "✅" if expected_contains in result else "❌"
        print(f"{status} | {before:+.3f} → {after:+.3f} = \"{result}\"")
        print(f"    Expected: Contains '{expected_contains}'")
        if expected_contains not in result:
            print(f"    ⚠️ MISMATCH!")

if __name__ == "__main__":
    test_notification_formats()
    print("\n" + "="*60)
    test_position_status_descriptions()
