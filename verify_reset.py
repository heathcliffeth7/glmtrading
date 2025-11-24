#!/usr/bin/env python3
"""
Veritabanının temizlendiğini doğrular
"""

from app.executor.ledger import engine, Portfolio, DailyPnL, Trade, PredictionLog
from sqlalchemy.orm import Session
from datetime import date

def verify_reset():
    with Session(engine) as session:
        portfolio = session.query(Portfolio).filter_by(symbol='BTCUSDT').first()
        today = date.today()
        daily_pnl = session.query(DailyPnL).filter_by(date=today).first()
        trade_count = session.query(Trade).filter_by(symbol='BTCUSDT').count()
        prediction_count = session.query(PredictionLog).filter_by(symbol='BTCUSDT').count()
        
        print('=== SIFIRLAMA SONRASI DURUM ===')
        print(f'Portfolio Pozisyon: {portfolio.position if portfolio else 0} BTC')
        print(f'Portfolio Ortalama Fiyat: ${portfolio.average_price if portfolio else 0}')
        print(f'Günlük Realized PnL: ${daily_pnl.realized_pnl if daily_pnl else 0}')
        print(f'Günlük Unrealized PnL: ${daily_pnl.unrealized_pnl if daily_pnl else 0}')
        print(f'Toplam İşlem Kaydı: {trade_count}')
        print(f'Toplam Prediction Kaydı: {prediction_count}')
        
        if trade_count == 0 and prediction_count == 0 and portfolio and portfolio.position == 0.0:
            print('✅ SİSTEM TAMAMEN TEMİZLENDİ!')
            return True
        else:
            print('⚠️ Sistem tam temiz değil!')
            return False

if __name__ == "__main__":
    verify_reset()

