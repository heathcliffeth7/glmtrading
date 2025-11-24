#!/usr/bin/env python3
"""
Portföyü ve tüm veri tabanını TAMAMEN sıfırlar
- Tüm trade kayıtlarını siler
- Portfolio sıfırlar
- Daily PnL sıfırlar
- Başlangıç durumuna getirir
"""

from app.executor.ledger import engine, Portfolio, DailyPnL, Trade, PredictionLog
from sqlalchemy.orm import Session
from datetime import date, datetime

def complete_reset():
    print('=== TAM PORTFÖY SIFIRLAMA ===')
    print('UYARI: Bu işlem tüm trade geçmişini silecektir!')
    print()
    
    with Session(engine) as session:
        # 1. Tüm trade kayıtlarını sil
        try:
            trade_count = session.query(Trade).filter_by(symbol='BTCUSDT').count()
            if trade_count > 0:
                print(f'Önceki Trade Kayıtları: {trade_count} adet')
                deleted_trades = session.query(Trade).filter_by(symbol='BTCUSDT').delete()
                print(f'{deleted_trades} trade kaydı silindi 🗑️')
            else:
                print('Trade kaydı bulunamadı ✅')
        except Exception as e:
            print(f'Trade silme hatası: {e}')
        
        # 2. Tüm prediction loglarını sil
        try:
            prediction_count = session.query(PredictionLog).filter_by(symbol='BTCUSDT').count()
            if prediction_count > 0:
                print(f'Önceki Prediction Kayıtları: {prediction_count} adet')
                deleted_predictions = session.query(PredictionLog).filter_by(symbol='BTCUSDT').delete()
                print(f'{deleted_predictions} prediction kaydı silindi 🗑️')
            else:
                print('Prediction kaydı bulunamadı ✅')
        except Exception as e:
            print(f'Prediction silme hatası: {e}')
        
        # 3. Portfolio tablosunu sıfırla
        try:
            portfolio = session.query(Portfolio).filter_by(symbol='BTCUSDT').first()
            if portfolio:
                print(f'Önceki Portfolio: Pos={portfolio.position:.4f}, Avg=${portfolio.average_price:.2f}')
                portfolio.position = 0.0
                portfolio.average_price = 0.0
                portfolio.updated_at = datetime.utcnow()
                print('Portfolio sıfırlandı 🔄')
            else:
                portfolio = Portfolio(symbol='BTCUSDT', position=0.0, average_price=0.0)
                session.add(portfolio)
                print('Yeni portfolio oluşturuldu ➕')
        except Exception as e:
            print(f'Portfolio sıfrlama hatası: {e}')
        
        # 4. Daily PnL tablosunu sıfırla
        try:
            today = date.today()
            daily_pnl = session.query(DailyPnL).filter_by(date=today).first()
            if daily_pnl:
                print(f'Önceki Daily PnL: Real=${daily_pnl.realized_pnl:.2f}, Unreal=${daily_pnl.unrealized_pnl:.2f}, Fees=${daily_pnl.total_fees:.2f}')
                daily_pnl.realized_pnl = 0.0
                daily_pnl.unrealized_pnl = 0.0
                daily_pnl.total_fees = 0.0
                print('Daily PnL sıfırlandı 🔄')
            else:
                daily_pnl = DailyPnL(date=today, realized_pnl=0.0, unrealized_pnl=0.0, total_fees=0.0)
                session.add(daily_pnl)
                print('Yeni Daily PnL oluşturuldu ➕')
        except Exception as e:
            print(f'Daily PnL sıfırlama hatası: {e}')
        
        session.commit()
        print('\n✅ VERİ TABANI TAMAMEN SIFIRLANDI!')
        print()
        print('Tüm geçmiş kayıtlar silindi.')
        print('Portföy başlangıç durumuna getirildi.')

def show_complete_status():
    print('\n=== TAM SIFIRLAMA SONRASI DURUM ===')
    
    with Session(engine) as session:
        # Portfolio durum
        portfolio = session.query(Portfolio).filter_by(symbol='BTCUSDT').first()
        print('Portfolio:')
        if portfolio:
            print(f'  Position: {portfolio.position:.4f} BTC')
            print(f'  Average Price: ${portfolio.average_price:.2f}')
            print(f'  Updated At: {portfolio.updated_at}')
        else:
            print('  Portfolio bulunamadı')
        
        # Daily PnL durum
        today = date.today()
        daily_pnl = session.query(DailyPnL).filter_by(date=today).first()
        print('\nDaily PnL:')
        if daily_pnl:
            print(f'  Date: {daily_pnl.date}')
            print(f'  Realized PnL: ${daily_pnl.realized_pnl:.2f}')
            print(f'  Unrealized PnL: ${daily_pnl.unrealized_pnl:.2f}')
            print(f'  Total Fees: ${daily_pnl.total_fees:.2f}')
        else:
            print('  Daily PnL bulunamadı')
        
        # Trade kayıtları
        trade_count = session.query(Trade).filter_by(symbol='BTCUSDT').count()
        print(f'\nTrade Kayıtları: {trade_count} adet')
        
        # Prediction kayıtları
        prediction_count = session.query(PredictionLog).filter_by(symbol='BTCUSDT').count()
        print(f'Prediction Kayıtları: {prediction_count} adet')
        
        # Özet
        starting_cash = 10000.0
        total_pnl = (daily_pnl.realized_pnl if daily_pnl else 0.0) + (daily_pnl.unrealized_pnl if daily_pnl else 0.0)
        current_equity = starting_cash + total_pnl
        
        print(f'\nÖzet:')
        print(f'  Başlangıç Sermaye: ${starting_cash:.2f}')
        print(f'  Toplam PnL: ${total_pnl:.2f}')
        print(f'  Güncel Sermaye: ${current_equity:.2f}')
        print(f'  Getiri/ Zarar: {((current_equity/starting_cash - 1) * 100):+.2f}%')
        print()
        
        if trade_count == 0 and prediction_count == 0 and portfolio and portfolio.position == 0.0:
            print('🎉 SİSTEM TAMAMEN TEMİZ - BAŞLANGIÇ DURUMUNDA!')
        else:
            print('⚠️ DİKKAT: Sistem tam temiz değil!')

if __name__ == "__main__":
    show_complete_status()
    print('\n' + '='*60)
    print('Tüm veriyi sıfırlıyor...')
    complete_reset()
    print('\n' + '='*60)
    show_complete_status()
