#!/usr/bin/env python3
"""
ETH, SOL ve BTC için portföyü ve geçmiş işlemleri sıfırlar.
Sadece belirtilen sembolleri (BTCUSDT, ETHUSDT, SOLUSDT) etkiler.
"""

import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from app.executor.ledger import (
    engine, 
    Portfolio, 
    DailyPnL, 
    Trade, 
    StopLossOrder, 
    StopLossNotification, 
    PredictionLog
)
from sqlalchemy.orm import Session
from datetime import date, datetime

TARGET_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

def reset_target_portfolio():
    print('=== PORTFÖY SIFIRLAMA (BTC, ETH, SOL) ===')
    print(f'Hedef Semboller: {", ".join(TARGET_SYMBOLS)}')

    with Session(engine) as session:
        # 1. Trade Geçmişini Temizle
        print('\n1. Trade Geçmişi Temizleniyor...')
        trade_query = session.query(Trade).filter(Trade.symbol.in_(TARGET_SYMBOLS))
        trade_count = trade_query.count()
        if trade_count > 0:
            trade_query.delete(synchronize_session=False)
            print(f'✅ {trade_count} adet trade kaydı silindi.')
        else:
            print('ℹ️  Silinecek trade kaydı bulunamadı.')

        # 2. Stop Loss Emirlerini Temizle
        print('\n2. Stop Loss Emirleri Temizleniyor...')
        sl_query = session.query(StopLossOrder).filter(StopLossOrder.symbol.in_(TARGET_SYMBOLS))
        sl_count = sl_query.count()
        if sl_count > 0:
            sl_query.delete(synchronize_session=False)
            print(f'✅ {sl_count} adet stop-loss emri silindi.')
        else:
            print('ℹ️  Silinecek stop-loss emri bulunamadı.')

        # 3. Stop Loss Bildirimlerini Temizle
        print('\n3. Stop Loss Bildirimleri Temizleniyor...')
        sl_notif_query = session.query(StopLossNotification).filter(StopLossNotification.symbol.in_(TARGET_SYMBOLS))
        sl_notif_count = sl_notif_query.count()
        if sl_notif_count > 0:
            sl_notif_query.delete(synchronize_session=False)
            print(f'✅ {sl_notif_count} adet stop-loss bildirimi silindi.')
        else:
            print('ℹ️  Silinecek stop-loss bildirimi bulunamadı.')

        # 4. Prediction Loglarını (AI Tahminleri) Temizle
        print('\n4. Prediction Logları Temizleniyor...')
        pred_query = session.query(PredictionLog).filter(PredictionLog.symbol.in_(TARGET_SYMBOLS))
        pred_count = pred_query.count()
        if pred_count > 0:
            pred_query.delete(synchronize_session=False)
            print(f'✅ {pred_count} adet prediction logu silindi.')
        else:
            print('ℹ️  Silinecek prediction logu bulunamadı.')

        # 5. Portfolio Tablosunu Sıfırla (Update)
        print('\n5. Portfolio Durumu Sıfırlanıyor...')
        for symbol in TARGET_SYMBOLS:
            portfolio = session.query(Portfolio).filter_by(symbol=symbol).first()
            if portfolio:
                print(f'  [{symbol}] Önceki: Pos={portfolio.position:.4f}, AvgPrice=${portfolio.average_price:.2f}')
                
                # Reset values
                portfolio.position = 0.0
                portfolio.long_position = 0.0
                portfolio.short_position = 0.0
                portfolio.net_position = 0.0
                portfolio.average_price = 0.0
                portfolio.long_avg_price = 0.0
                portfolio.short_avg_price = 0.0
                portfolio.initial_capital = 10000.0  # Reset to 10k default
                portfolio.updated_at = datetime.utcnow()
                
                print(f'  [{symbol}] ✅ Sıfırlandı (10k sermaye)')
            else:
                # Create if not exists
                portfolio = Portfolio(
                    symbol=symbol, 
                    position=0.0, 
                    average_price=0.0,
                    initial_capital=10000.0
                )
                session.add(portfolio)
                print(f'  [{symbol}] ✅ Yeni portfolio oluşturuldu (10k sermaye)')

        # 6. Daily PnL Tablosunu Sıfırla (Bugün için)
        print('\n6. Daily PnL (Bugün) Sıfırlanıyor...')
        today = date.today()
        daily_pnl = session.query(DailyPnL).filter_by(date=today).first()
        if daily_pnl:
            print(f'  Önceki PnL: Realized=${daily_pnl.realized_pnl:.2f}, Unrealized=${daily_pnl.unrealized_pnl:.2f}')
            daily_pnl.realized_pnl = 0.0
            daily_pnl.unrealized_pnl = 0.0
            daily_pnl.total_fees = 0.0
            print('  ✅ Daily PnL sıfırlandı.')
        else:
            daily_pnl = DailyPnL(date=today, realized_pnl=0.0, unrealized_pnl=0.0, total_fees=0.0)
            session.add(daily_pnl)
            print('  ✅ Yeni Daily PnL kaydı oluşturuldu.')

        session.commit()
        print('\n🎉 TÜM İŞLEMLER BAŞARIYLA TAMAMLANDI!')

def show_current_status():
    print('\n=== MEVCUT DURUM ===')
    with Session(engine) as session:
        for symbol in TARGET_SYMBOLS:
            portfolio = session.query(Portfolio).filter_by(symbol=symbol).first()
            if portfolio:
                print(f'[{symbol}] Pos: {portfolio.position} | AvgPrice: {portfolio.average_price} | Capital: {portfolio.initial_capital}')
            else:
                print(f'[{symbol}] Kayıt yok')

if __name__ == "__main__":
    # Kullanıcı onayı
    print("UYARI: Bu işlem BTC, ETH ve SOL için TÜM GEÇMİŞ İŞLEMLERİ SİLECEKTİR.")
    response = input("Devam etmek istiyor musunuz? (y/n): ")
    
    if response.lower() == 'y':
        show_current_status()
        print('\n' + '-'*40)
        try:
            reset_target_portfolio()
        except Exception as e:
            print(f'\n❌ HATA OLUŞTU: {e}')
            import traceback
            traceback.print_exc()
        print('-'*40)
        show_current_status()
    else:
        print("İşlem iptal edildi.")
