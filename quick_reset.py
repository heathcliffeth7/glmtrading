#!/usr/bin/env python3
from app.executor.ledger import engine, Portfolio, DailyPnL, Trade, PredictionLog
from sqlalchemy.orm import Session
from datetime import date, datetime

print('=== PORTFÖY SIFIRLAMA ===')
print('UYARI: Bu işlem tüm trade geçmişini silecektir!')
print()

with Session(engine) as session:
    try:
        # Tüm trade kayıtlarını sil
        trade_count = session.query(Trade).filter_by(symbol='BTCUSDT').count()
        if trade_count > 0:
            print(f'Önceki Trade Kayıtları: {trade_count} adet')
            deleted_trades = session.query(Trade).filter_by(symbol='BTCUSDT').delete()
            print(f'{deleted_trades} trade kaydı silindi 🗑️')
        else:
            print('Trade kaydı bulunamadı ✅')
    except Exception as e:
        print(f'Trade silme hatası: {e}')
        
    try:
        # Portfolio sıfırla
        portfolio = session.query(Portfolio).filter_by(symbol='BTCUSDT').first()
        if portfolio:
            print(f'Önceki Portfolio: Pos={portfolio.position:.4f}, Avg=${portfolio.average_price:.2f}')
            portfolio.position = 0.0
            portfolio.average_price = 0.0
            print('Portfolio sıfırlandı 🔄')
        else:
            portfolio = Portfolio(symbol='BTCUSDT', position=0.0, average_price=0.0)
            session.add(portfolio)
            print('Yeni portfolio oluşturuldu ➕')
    except Exception as e:
        print(f'Portfolio sıfrlama hatası: {e}')
        
    try:
        # Daily PnL sıfırla
        today = date.today()
        daily_pnl = session.query(DailyPnL).filter_by(date=today).first()
        if daily_pnl:
            print(f'Önceki Daily PnL: Real=${daily_pnl.realized_pnl:.2f}')
            daily_pnl.realized_pnl = 0.0
            daily_pnl.unrealized_pnl = 0.0
            print('Daily PnL sıfırlandı 🔄')
        else:
            daily_pnl = DailyPnL(date=today, realized_pnl=0.0, unrealized_pnl=0.0)
            session.add(daily_pnl)
            print('Yeni Daily PnL oluşturuldu ➕')
    except Exception as e:
        print(f'Daily PnL sıfırlama hatası: {e}')
        
    session.commit()
    print('✅ VERİ TABANI SIFIRLANDI!')

print()
print('YENİ PORTFÖY DURUMU:')
print('💰 Başlangıç Sermaye: $10,000.00')
print('📍 Mevcut Pozisyon: 0.0000 BTC')
print('✅ Realized PnL: $0.00')
print('📊 Toplam Equity: $10,000.00')
print('🎉 Portföy hazır!')
