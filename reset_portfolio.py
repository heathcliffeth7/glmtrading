#!/usr/bin/env python3
"""
Portföyü başlangıç durumuna sıfırlar
"""

from app.executor.ledger import engine, Portfolio, DailyPnL, get_portfolio, get_daily_pnl, Trade
from sqlalchemy.orm import Session
from datetime import date, datetime

def reset_portfolio():
    print('=== PORTFÖY SIFIRLAMA ===')

    with Session(engine) as session:
        # Tüm trade geçmişini temizle
        trade_count = session.query(Trade).count()
        if trade_count > 0:
            print(f'🗑️  {trade_count} adet trade kaydı siliniyor...')
            session.query(Trade).delete()
            print('✅ Tüm trade geçmişi temizlendi')
        else:
            print('ℹ️  Temizlenecek trade kaydı bulunamadı')
        # Portfolio tablosunu sıfırla (her sembol için)
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        
        for symbol in symbols:
            portfolio = session.query(Portfolio).filter_by(symbol=symbol).first()
            if portfolio:
                print(f'[{symbol}] Önceki durum: Position={portfolio.position:.4f}, Avg_Price=${portfolio.average_price:.2f}')
                portfolio.position = 0.0
                portfolio.average_price = 0.0
                portfolio.initial_capital = 10000.0
                portfolio.updated_at = datetime.utcnow()
                print(f'[{symbol}] Portfolio sıfırlandı (10k sermaye)')
            else:
                portfolio = Portfolio(
                    symbol=symbol, 
                    position=0.0, 
                    average_price=0.0,
                    initial_capital=10000.0
                )
                session.add(portfolio)
                print(f'[{symbol}] Yeni portfolio oluşturuldu (10k sermaye)')
        
        # Daily PnL tablosunu sıfırla (bugün için)
        today = date.today()
        daily_pnl = session.query(DailyPnL).filter_by(date=today).first()
        if daily_pnl:
            print(f'Önceki Daily PnL: Realized=${daily_pnl.realized_pnl:.2f}, Unrealized=${daily_pnl.unrealized_pnl:.2f}')
            daily_pnl.realized_pnl = 0.0
            daily_pnl.unrealized_pnl = 0.0
            print('Daily PnL sıfırlandı')
        else:
            daily_pnl = DailyPnL(date=today, realized_pnl=0.0, unrealized_pnl=0.0)
            session.add(daily_pnl)
            print('Yeni Daily PnL oluşturuldu')
        
        session.commit()
        print('✅ Portföy başarıyla sıfırlandı!')

def show_current_status():
    print('\n=== MEVCUT DURUM ===')
    
    with Session(engine) as session:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        daily_pnl = get_daily_pnl(session)
        
        for symbol in symbols:
            portfolio = get_portfolio(session, symbol)
            
            # Fallback for initial_capital if column missing (for migration safety)
            initial_cap = getattr(portfolio, "initial_capital", 10000.0)
            
            print(f'\n[{symbol}] Portfolio:')
            print(f'  Position: {portfolio.position:.4f}')
            print(f'  Average Price: ${portfolio.average_price:.2f}')
            print(f'  Initial Capital: ${initial_cap:.2f}')
        
        print(f'\nDaily PnL (Global):')
        print(f'  Realized PnL: ${daily_pnl.realized_pnl:.2f}')
        print(f'  Unrealized PnL: ${daily_pnl.unrealized_pnl:.2f}')

if __name__ == "__main__":
    show_current_status()
    print('\n' + '='*50)
    reset_portfolio()
    print('\n' + '='*50)
    show_current_status()
