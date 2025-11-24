#!/usr/bin/env python3
"""
Tüm veritabanını temizler: Portfolio, Daily PnL ve tüm geçmiş işlemler
"""

from app.executor.ledger import engine, Trade, Portfolio, DailyPnL
from sqlalchemy.orm import Session
from datetime import date, datetime, timezone

def clean_all_data():
    print('=== VERİTABANI TEMİZLEME ===')
    
    with Session(engine) as session:
        # Tüm geçmiş işlemleri sil
        trade_count = session.query(Trade).count()
        print(f'Mevcut işlem sayısı: {trade_count}')
        session.query(Trade).delete()
        print('✅ Tüm geçmiş işlemler silindi')
        
        # Portfolio tablosunu sıfırla
        portfolio = session.query(Portfolio).filter_by(symbol='BTCUSDT').first()
        if portfolio:
            print(f'Önceki durum: Position={portfolio.position:.4f} BTC, Avg_Price=${portfolio.average_price:.2f}')
            portfolio.position = 0.0
            portfolio.average_price = 0.0
            portfolio.updated_at = datetime.now(timezone.utc)
            print('✅ Portfolio sıfırlandı')
        else:
            portfolio = Portfolio(symbol='BTCUSDT', position=0.0, average_price=0.0)
            session.add(portfolio)
            print('✅ Yeni portfolio oluşturuldu')
        
        # Daily PnL tablosunu sıfırla (bugün için)
        today = date.today()
        daily_pnl = session.query(DailyPnL).filter_by(date=today).first()
        if daily_pnl:
            print(f'Önceki Daily PnL: Realized=${daily_pnl.realized_pnl:.2f}, Unrealized=${daily_pnl.unrealized_pnl:.2f}')
            daily_pnl.realized_pnl = 0.0
            daily_pnl.unrealized_pnl = 0.0
            print('✅ Daily PnL sıfırlandı')
        else:
            daily_pnl = DailyPnL(date=today, realized_pnl=0.0, unrealized_pnl=0.0)
            session.add(daily_pnl)
            print('✅ Yeni Daily PnL oluşturuldu')
        
        session.commit()
        print('\n🎉 VERİTABANI BAŞARIYLA TEMİZLENDİ! 🎉')
        print('Artık taze bir başlangıç yapabilirsiniz.')

def show_status_before():
    print('\n=== TEMİZLİK ÖNCESİ DURUM ===')
    
    with Session(engine) as session:
        # Trade sayısı
        trade_count = session.query(Trade).count()
        print(f'Toplam İşlem Sayısı: {trade_count}')
        
        # Son işlemler
        recent_trades = session.query(Trade).order_by(Trade.timestamp.desc()).limit(3).all()
        if recent_trades:
            print('Son 3 İşlem:')
            for i, trade in enumerate(recent_trades, 1):
                close_status = f"Kapandı (${trade.close_price:.2f})" if trade.close_price else "Açık"
                print(f'  {i}. {trade.side} {trade.amount:.4f} BTC @ ${trade.price:.2f} - {close_status}')
        else:
            print('Hiç işlem kaydı yok')
        
        # Portfolio durumu
        from app.executor.portfolio_sync import get_synced_portfolio
        portfolio = get_synced_portfolio(session, 'BTCUSDT')
        
        daily_pnl = session.query(DailyPnL).filter_by(date=date.today()).first()
        realized_pnl = daily_pnl.realized_pnl if daily_pnl else 0.0
        unrealized_pnl = daily_pnl.unrealized_pnl if daily_pnl else 0.0
        
        total_pnl = realized_pnl + unrealized_pnl
        starting_cash = 10000.0
        current_equity = starting_cash + total_pnl
        
        print(f'\nPortfolio Durumu:')
        print(f'  Position: {portfolio.position:.4f} BTC')
        print(f'  Average Price: ${portfolio.average_price:.2f}')
        print(f'  Realized PnL: ${realized_pnl:.2f}')
        print(f'  Unrealized PnL: ${unrealized_pnl:.2f}')
        print(f'  Current Equity: ${current_equity:.2f}')
        print(f'  Total Return: {((current_equity/starting_cash - 1) * 100):+.2f}%')

if __name__ == "__main__":
    show_status_before()
    print('\n' + '='*60)
    clean_all_data()
    print('\n' + '='*60)
