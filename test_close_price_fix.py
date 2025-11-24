#!/usr/bin/env python3
"""
Test: Pozisyon kapatıldığında close_price'ların doğru kaydedildiğini kontrol et
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "trading"))

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.executor.executor import Executor
from app.executor.ledger import DailyPnL, Portfolio, Trade, engine, get_portfolio
from app.risk_manager.manager import RiskDecision


def test_close_price_fix():
    print("=" * 80)
    print("TEST: Pozisyon Kapatma ve Close Price Güncellemesi")
    print("=" * 80)
    
    # 1. Database'i temizle (test için)
    with Session(engine) as session:
        session.query(Trade).delete()
        session.query(DailyPnL).delete()
        # Portfolio'yu sıfırla
        portfolio = get_portfolio(session, "BTCUSDT")
        portfolio.position = 0.0
        portfolio.average_price = 0.0
        portfolio.equity = 10000.0
        session.commit()
        print("\n✅ Database ve Portfolio sıfırlandı")
    
    # 2. Executor oluştur
    executor = Executor(symbol="BTCUSDT", max_position=1.0)
    
    # 3. SHORT pozisyon aç (SELL 0.5 BTC @ $100,000)
    print("\n" + "=" * 80)
    print("STEP 1: SHORT Pozisyon Açılışı (SELL 0.5 BTC @ $100,000)")
    print("=" * 80)
    
    decision_open = RiskDecision(
        action="SELL",
        amount=0.005,  # 0.5% equity
        leverage=5.0,
        reasoning="Test: SHORT açılış"
    )
    
    # Mock price
    executor._resolve_price = lambda: 100000.0
    
    result_open = executor.execute(decision_open)
    print(f"Sonuç: {result_open.status}")
    print(f"Detay: {result_open.details}")
    
    # Trade'i kontrol et
    with Session(engine) as session:
        trades = session.query(Trade).order_by(Trade.timestamp.asc()).all()
        print(f"\n📊 Trade Durumu:")
        for trade in trades:
            print(f"  ID: {trade.id}")
            print(f"  Side: {trade.side}")
            print(f"  Amount: {trade.amount:.4f} BTC")
            print(f"  Price: ${trade.price:,.2f}")
            print(f"  Close Price: {'NULL ❌' if trade.close_price is None else f'${trade.close_price:,.2f} ✅'}")
            print(f"  PnL: ${trade.pnl:.2f}")
            print()
        
        # Beklenen: Trade 1'in close_price = NULL (pozisyon açık)
        assert trades[0].close_price is None, "❌ Trade 1'in close_price NULL olmalı (pozisyon açık)"
        print("✅ Trade 1: close_price = NULL (doğru)")
    
    # 4. SHORT pozisyonu kapat (BUY 0.5 BTC @ $98,000)
    print("\n" + "=" * 80)
    print("STEP 2: SHORT Pozisyon Kapanışı (BUY @ $98,000)")
    print("=" * 80)
    
    decision_close = RiskDecision(
        action="BUY",
        amount=0.005,  # Aynı miktar
        leverage=5.0,
        reasoning="Test: SHORT kapanış"
    )
    
    # Mock price (profit scenario: SHORT $100k → close $98k = +$2k)
    executor._resolve_price = lambda: 98000.0
    
    result_close = executor.execute(decision_close)
    print(f"Sonuç: {result_close.status}")
    print(f"Detay: {result_close.details}")
    
    # Trade'leri kontrol et
    with Session(engine) as session:
        trades = session.query(Trade).order_by(Trade.timestamp.asc()).all()
        print(f"\n📊 Final Trade Durumu:")
        for i, trade in enumerate(trades, 1):
            print(f"Trade {i}:")
            print(f"  ID: {trade.id}")
            print(f"  Side: {trade.side}")
            print(f"  Amount: {trade.amount:.4f} BTC")
            print(f"  Price: ${trade.price:,.2f}")
            print(f"  Close Price: {'NULL ❌' if trade.close_price is None else f'${trade.close_price:,.2f} ✅'}")
            print(f"  PnL: ${trade.pnl:.2f}")
            print()
        
        # Beklenen: Her iki trade'in de close_price set olmalı
        print("\n" + "=" * 80)
        print("SONUÇ KONTROLÜ")
        print("=" * 80)
        
        if len(trades) != 2:
            print(f"❌ HATA: {len(trades)} trade bulundu, 2 olmalıydı")
            return False
        
        trade1, trade2 = trades[0], trades[1]
        
        # Trade 1 (SELL - SHORT açılış): close_price set olmalı
        if trade1.close_price is None:
            print("❌ HATA: Trade 1 (SHORT açılış) close_price NULL, set olmalıydı!")
            return False
        else:
            print(f"✅ Trade 1 (SHORT açılış): close_price = ${trade1.close_price:,.2f}")
        
        # Trade 2 (BUY - SHORT kapanış): close_price set olmalı
        if trade2.close_price is None:
            print("❌ HATA: Trade 2 (SHORT kapanış) close_price NULL, set olmalıydı!")
            return False
        else:
            print(f"✅ Trade 2 (SHORT kapanış): close_price = ${trade2.close_price:,.2f}")
        
        # Her iki trade'in close_price'ı aynı olmalı (kapanış fiyatı)
        if trade1.close_price != trade2.close_price:
            print(f"❌ HATA: Trade 1 ve Trade 2'nin close_price'ları farklı!")
            print(f"  Trade 1: ${trade1.close_price:,.2f}")
            print(f"  Trade 2: ${trade2.close_price:,.2f}")
            return False
        else:
            print(f"✅ Her iki trade'in close_price'ı aynı: ${trade1.close_price:,.2f}")
        
        print("\n" + "=" * 80)
        print("🎉 TÜM TESTLER BAŞARILI!")
        print("=" * 80)
        return True

if __name__ == "__main__":
    success = test_close_price_fix()
    sys.exit(0 if success else 1)
