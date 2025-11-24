#!/usr/bin/env python3
"""
Test cycle notification with new trade details
"""

from sqlalchemy.orm import Session

from app.executor.executor import Executor
from app.executor.ledger import engine, get_portfolio
from app.risk_manager.manager import RiskDecision


def test_cycle_notification_format():
    print("=== TEST CYCLE NOTIFICATION FORMAT ===")
    
    executor = Executor()
    
    # Create a test execution to simulate a new trade
    with Session(engine) as session:
        portfolio = get_portfolio(session, "BTCUSDT")
        
        # Reset to start fresh
        portfolio.position = 0.0
        portfolio.average_price = 0.0
        session.commit()
        
        print("Portfolio reset to 0 position")
    
    # Execute a SELL trade to test notification
    decision = RiskDecision(
        action="SELL",
        amount=0.1,  # 10% equity
        leverage=5.0,  # 5x leverage
        reasoning="Test trade for cycle notification format"
    )
    
    print("Executing test trade...")
    result = executor.execute(decision)
    
    print(f"Execution result: {result.status} - {result.details}")
    
    # Test the get_recent_trades_with_pnl method
    recent_trades = executor.get_recent_trades_with_pnl(limit=3)
    
    print("\nRecent trades data structure:")
    for i, trade in enumerate(recent_trades, 1):
        print(f"Trade {i}:")
        print(f"  - side: {trade['side']}")
        print(f"  - amount: {trade['amount']}")
        print(f"  - open_price: {trade['open_price']}")
        print(f"  - close_price: {trade['close_price']}")
        print(f"  - is_closed: {trade['is_closed']}")
        print(f"  - pnl: {trade['pnl']}")
        print(f"  - pnl_pct: {trade['pnl_pct']}")
        print()
    
    # Test notification format construction
    if recent_trades:
        latest_trade = recent_trades[0]
        side_emoji = "📈" if latest_trade['side'] == "BUY" else "📉"
        notional_value = latest_trade['amount'] * latest_trade['open_price']
        pnl_sign = "+" if latest_trade['pnl'] >= 0 else ""
        
        print("Sample notification format:")
        print("🚨 Son İşlem Detayları")
        print(f"{side_emoji} En Son İşlem: {latest_trade['side']} {latest_trade['amount']:.4f} BTC @ ${latest_trade['open_price']:,.2f}")
        print(f"💰 Notional Değeri: ${notional_value:,.2f}")
        print(f"{'🟢' if latest_trade['pnl'] >= 0 else '🔴'} Anlık PnL: {pnl_sign}${latest_trade['pnl']:,.2f} ({pnl_sign}{latest_trade['pnl_pct']:.2f}%)")
        
        if latest_trade['is_closed']:
            print(f"🔒 Kapanış: ${latest_trade['close_price']:,.2f}")
            print(f"✅ Gerçekleşen PnL: {pnl_sign}${latest_trade['pnl']:,.2f} ({pnl_sign}{latest_trade['pnl_pct']:.2f}%)")
        else:
            print(f"📊 Güncel Fiyat: ${latest_trade['close_price']:,.2f}")
    
    # Test closing the position
    print("\n=== Testing CLOSE action ===")
    close_decision = RiskDecision(
        action="CLOSE",
        amount=1.0,
        leverage=1.0,
        reasoning="Test close action for cycle notification"
    )
    
    close_result = executor.execute(close_decision)
    print(f"Close execution result: {close_result.status} - {close_result.details}")
    
    # Check the latest trade after close
    recent_trades_after = executor.get_recent_trades_with_pnl(limit=1)
    if recent_trades_after:
        latest_close_trade = recent_trades_after[0]
        print(f"\nLatest trade after close:")
        print(f"  - side: {latest_close_trade['side']}")
        print(f"  - is_closed: {latest_close_trade['is_closed']}")
        print(f"  - pnl: {latest_close_trade['pnl']}")

if __name__ == "__main__":
    test_cycle_notification_format()
