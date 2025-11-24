#!/usr/bin/env python3
"""
Check current system status
"""

from app.executor.ledger import engine, get_portfolio, get_daily_pnl, get_recent_trades
from sqlalchemy.orm import Session

def check_status():
    print('=== CURRENT SYSTEM STATUS ===')
    
    with Session(engine) as session:
        portfolio = get_portfolio(session, 'BTCUSDT')
        daily_pnl = get_daily_pnl(session)
        recent_trades = get_recent_trades(session, 'BTCUSDT', limit=5)
        
        print('Portfolio:')
        print(f'  Position: {portfolio.position:.4f} BTC')
        print(f'  Average Price: ${portfolio.average_price:.2f}')
        print(f'  Updated At: {portfolio.updated_at}')
        
        print('\nDaily PnL:')
        print(f'  Realized PnL: ${daily_pnl.realized_pnl:.2f}')
        print(f'  Unrealized PnL: ${daily_pnl.unrealized_pnl:.2f}')
        print(f'  Date: {daily_pnl.date}')
        
        total_equity = 10000 + daily_pnl.realized_pnl + daily_pnl.unrealized_pnl
        print(f'\nTotal Equity: ${total_equity:.2f}')
        print(f'Return: {((total_equity/10000 - 1) * 100):+.2f}%')
        
        print(f'\nRecent Trades: {len(recent_trades)} total')
        for i, trade in enumerate(recent_trades, 1):
            print(f'  {i}. {trade.side} {trade.amount:.4f} BTC @ ${trade.price:.2f}')
            print(f'     Close: ${trade.close_price:.2f}' if trade.close_price else '     Close: OPEN')
            print(f'     PnL: ${trade.pnl:.2f}')
        
        # Check if system is truly reset
        if len(recent_trades) == 0 and portfolio.position == 0.0:
            print('\n✅ SYSTEM IS CLEAN - Fresh Start!')
        else:
            print('\n⚠️ System has existing data')

if __name__ == "__main__":
    check_status()
