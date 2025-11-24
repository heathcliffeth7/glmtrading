#!/usr/bin/env python3
"""
Test the executor's CLOSE action with the fixed implementation
"""

from sqlalchemy.orm import Session

from app.executor.executor import Executor
from app.executor.ledger import engine, get_portfolio, get_recent_trades
from app.risk_manager.manager import RiskDecision


def test_executor_close():
    print("=== TEST EXECUTOR CLOSE ACTION ===")
    
    executor = Executor()
    
    # Create a small test position first
    with Session(engine) as session:
        portfolio = get_portfolio(session, "BTCUSDT")
        
        # Reset to small test position
        portfolio.position = -0.1  # Small SHORT position
        portfolio.average_price = 109000.0
        session.commit()
        
        print(f"Test position created: {portfolio.position} @ {portfolio.average_price}")
    
    # Now test the close action
    decision = RiskDecision(
        action="CLOSE",
        amount=1.0,
        leverage=10.0,
        reasoning="Test close action from executor"
    )
    
    print("\nExecuting CLOSE action...")
    result = executor.execute(decision)
    
    print(f"Execution Result: status={result.status}, details={result.details}")
    
    # Verify the result
    with Session(engine) as session:
        portfolio = get_portfolio(session, "BTCUSDT")
        recent_trades = get_recent_trades(session, "BTCUSDT", limit=2)
        
        print(f"\nFinal Position: {portfolio.position}")
        print(f"Final Average Price: {portfolio.average_price}")
        
        print("\nRecent Trades:")
        for trade in recent_trades:
            print(f"  {trade.timestamp} | {trade.side} | {trade.position_side} | amount: {trade.amount} | price: {trade.price} | close_price: {trade.close_price} | pnl: {trade.pnl}")
        
        # Check if close was successful
        if portfolio.position == 0.0:
            print("\n✅ SUCCESS: Position closed successfully")
        else:
            print(f"\n❌ FAILED: Position still open: {portfolio.position}")

if __name__ == "__main__":
    test_executor_close()
