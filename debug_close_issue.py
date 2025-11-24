#!/usr/bin/env python3
"""
Debug and fix the CLOSE action issue
"""

from app.executor.ledger import engine, get_portfolio, get_daily_pnl, record_trade, close_open_trades
from app.executor.executor import Executor
from app.risk_manager.manager import RiskDecision
from app.utils.influx import query_latest
from sqlalchemy.orm import Session
from datetime import datetime
import traceback

def debug_close_issue():
    print("=== DEBUG CLOSE ACTION ISSUE ===")
    
    # Create executor
    executor = Executor()
    
    # Create a mock CLOSE decision
    decision = RiskDecision(
        action="CLOSE",
        amount=1.0,
        leverage=10.0,
        reasoning="Test close action to debug issue"
    )
    
    with Session(engine) as session:
        # Check current state
        portfolio = get_portfolio(session, "BTCUSDT")
        daily_pnl = get_daily_pnl(session)
        
        print(f"BEFORE - Position: {portfolio.position}")
        print(f"BEFORE - Average Price: {portfolio.average_price}")
        print(f"BEFORE - Daily PnL Realized: {daily_pnl.realized_pnl}")
        print(f"BEFORE - Daily PnL Unrealized: {daily_pnl.unrealized_pnl}")
        
        # Try to get current price
        try:
            price_data = query_latest('BTCUSDT', '1m')
            current_price = price_data[0]['close'] if price_data else 109000.0
            print(f"Current price: {current_price}")
        except Exception as e:
            print(f"Error getting price: {e}")
            current_price = 109000.0  # Fallback price
        
        # Execute close manually
        pre_position = portfolio.position
        
        if abs(portfolio.position) < 0.0001:
            print("No position to close")
            return
        
        try:
            # Step 1: Calculate close parameters
            max_closeable = abs(portfolio.position)
            btc_amount = max_closeable * decision.amount
            position_size_usd = btc_amount * current_price
            is_long = portfolio.position > 0
            position_side = "LONG" if is_long else "SHORT"
            
            print(f"Close parameters:")
            print(f"  - Max closeable: {max_closeable}")
            print(f"  - BTC amount: {btc_amount}")
            print(f"  - Position side: {position_side}")
            print(f"  - Position size USD: {position_size_usd}")
            
            # Step 2: Calculate fee and PnL
            fee = position_size_usd * executor._taker_fee_rate
            
            if is_long:
                pnl_before_fee = (current_price - portfolio.average_price) * btc_amount
            else:
                pnl_before_fee = (portfolio.average_price - current_price) * btc_amount
            
            pnl = pnl_before_fee - fee
            
            print(f"Fee: {fee}")
            print(f"PNL before fee: {pnl_before_fee}")
            print(f"PNL after fee: {pnl}")
            
            # Step 3: Record trade
            trade = record_trade(
                session,
                symbol=executor._symbol,
                side="BUY" if is_long else "SELL",
                amount=btc_amount,
                price=current_price,
                pnl=pnl,
                leverage=1.0,
                fees=fee,
                close_price=current_price,
                position_side=position_side,
            )
            
            print(f"Trade recorded: ID={trade.id}")
            
            # Step 4: Close opposite trades
            close_open_trades(
                session=session,
                symbol=executor._symbol,
                close_side="BUY" if is_long else "SELL",
                close_amount=btc_amount,
                close_price=current_price,
            )
            
            print("Open trades closed")
            
            # Step 5: Update portfolio
            if decision.amount >= 0.9999:  # Full close
                portfolio.position = 0.0
                portfolio.average_price = 0.0
                print("Full close - position reset to 0")
            else:  # Partial close
                if is_long:
                    portfolio.position -= btc_amount
                else:
                    portfolio.position += btc_amount
                print(f"Partial close - new position: {portfolio.position}")
            
            portfolio.updated_at = datetime.utcnow()
            
            # Step 6: Update daily PnL
            daily_pnl.realized_pnl += pnl
            daily_pnl.unrealized_pnl = (current_price - portfolio.average_price) * portfolio.position if portfolio.position != 0 else 0.0
            
            print(f"Updated daily PnL - Realized: {daily_pnl.realized_pnl}, Unrealized: {daily_pnl.unrealized_pnl}")
            
            # Step 7: Flush and commit
            session.flush()
            print("Session flushed successfully")
            
            session.commit()
            print("Session committed successfully")
            
            # Check final state
            portfolio_after = get_portfolio(session, "BTCUSDT")
            daily_pnl_after = get_daily_pnl(session)
            
            print(f"AFTER - Position: {portfolio_after.position}")
            print(f"AFTER - Average Price: {portfolio_after.average_price}")
            print(f"AFTER - Daily PnL Realized: {daily_pnl_after.realized_pnl}")
            print(f"AFTER - Daily PnL Unrealized: {daily_pnl_after.unrealized_pnl}")
            
            success = portfolio_after.position == 0.0
            print(f"Close action {'SUCCESS' if success else 'FAILED'}")
            
        except Exception as e:
            print(f"Error during close: {e}")
            traceback.print_exc()
            session.rollback()
            print("Session rolled back")

if __name__ == "__main__":
    debug_close_issue()
