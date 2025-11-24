#!/usr/bin/env python3
"""
Test: Aynı açılış/kapanış fiyatı bug'ının tamamen düzeltildiğini doğrula
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.executor.executor import Executor
from app.executor.ledger import DailyPnL, Portfolio, Trade, engine
from app.risk_manager.manager import RiskDecision


def cleanup_test_data():
    """Test verilerini temizle"""
    with Session(engine) as session:
        session.query(Trade).delete()
        session.query(DailyPnL).delete()
        portfolio = session.query(Portfolio).filter_by(symbol='BTCUSDT').first()
        if portfolio:
            portfolio.position = 0.0
            portfolio.average_price = 0.0
        else:
            portfolio = Portfolio(symbol='BTCUSDT', position=0.0, average_price=0.0)
            session.add(portfolio)
        session.add(DailyPnL(date=date.today(), realized_pnl=0.0, unrealized_pnl=0.0))
        session.commit()

def setup_portfolio_position(position_amount):
    """
    Portfolio pozisyonunu ayarla ve gerekli trade'leri oluştur
    
    Args:
        position_amount: Oluşturulacak pozisyon miktarı
    """
    with Session(engine) as session:
        # Önce temizle
        session.query(Trade).delete()
        
        # Portfolio'yu sıfırla
        portfolio = session.query(Portfolio).filter_by(symbol='BTCUSDT').first()
        if not portfolio:
            portfolio = Portfolio(symbol='BTCUSDT', position=0.0, average_price=0.0)
            session.add(portfolio)
        portfolio.position = 0.0
        portfolio.average_price = 0.0
        session.commit()
        
        # Eğer pozisyon varsa, ilgili trade'leri oluştur
        if abs(position_amount) > 0.0001:
            from app.executor.ledger import record_trade
            side = "BUY" if position_amount > 0 else "SELL"
            position_side = "LONG" if position_amount > 0 else "SHORT"
            
            # Açık pozisyon trade'i oluştur
            trade = record_trade(
                session,
                symbol='BTCUSDT',
                side=side,
                amount=abs(position_amount),
                price=100000.0,
                pnl=0.0,
                leverage=10.0,
                fees=50.0,
                close_price=None,  # Hala açık
                position_side=position_side,
                position_id='test_position_123'
            )
            session.commit()
            
            print(f"✅ Test pozisyonu oluşturuldu: {abs(position_amount):.4f} BTC {position_side}")
        else:
            print("✅ Boş portfolio ayarlandı")
        
        # Portfolio senkronizasyonunu kontrol et
        from app.executor.portfolio_sync import get_synced_portfolio
        synced_portfolio = get_synced_portfolio(session, 'BTCUSDT')
        print(f"📊 Sync edilmiş portfolio: {synced_portfolio.position:.4f} BTC")

def get_all_trades():
    """Tüm trade'leri getir"""
    with Session(engine) as session:
        return session.query(Trade).order_by(Trade.timestamp.asc()).all()

def test_scenario(name, portfolio_start_position, action, should_set_close_price):
    """
    Belirli bir senaryoyu test et
    
    Args:
        name: Test adı
        portfolio_start_position: Başlangıç pozisyonu
        action: BUY veya SELL
        should_set_close_price: close_price set edilmeli mi?
    """
    print(f"\n{'='*80}")
    print(f"TEST: {name}")
    print(f"Portfolio başlangıç: {portfolio_start_position} BTC, Action: {action}")
    print(f"{'='*80}")
    
    # Portfolio'yu ayarla
    setup_portfolio_position(portfolio_start_position)
    
    # Executor oluştur
    executor = Executor(symbol="BTCUSDT", max_position=1.0)
    executor._resolve_price = lambda: 105000.0  # Sabit fiyat
    
    # Karar çalıştır
    decision = RiskDecision(
        action=action,
        amount=0.001,  # %0.1 equity - küçük miktar
        leverage=10.0,
        reasoning=f"Test senaryosu: {name}"
    )
    
    result = executor.execute(decision)
    print(f"Execution Sonucu: {result.status}")
    print(f"Detay: {result.details}")
    
    # Trade'i kontrol et
    with Session(engine) as session:
        trades = session.query(Trade).order_by(Trade.timestamp.desc()).limit(1).all()
        if not trades:
            print("❌ HATA: Hiç trade oluşturulmadı!")
            return False
        
        trade = trades[0]
        print(f"\n📊 Trade Durumu:")
        print(f"  Side: {trade.side}")
        print(f"  Amount: {trade.amount:.6f} BTC")
        print(f"  Open Price: ${trade.price:,.2f}")
        print(f"  Close Price: {'NULL' if trade.close_price is None else f'${trade.close_price:,.2f}'}")
        
        # Beklenen sonucu kontrol et
        if should_set_close_price:
            if trade.close_price is None:
                print(f"❌ HATA: close_price NULL olmalıydı fakat NULL olarak ayarlandı!")
                return False
            else:
                print(f"✅ Doğru: close_price set edildi (${trade.close_price:,.2f})")
                # Açılış ve kapanış fiyatlarının aynı olup olmadığını kontrol et
                if abs(trade.price - trade.close_price) < 0.01:
                    print(f"⚠️  UYARI: Açılış ve kapanış fiyatları neredeyse aynı (${trade.price:,.2f} → ${trade.close_price:,.2f})")
                    print(f"      Bu, düzelttiğimiz bug olabilir!")
                    return False
        else:
            if trade.close_price is not None:
                print(f"❌ HATA: close_price NULL olmalıydı fakat ${trade.close_price:,.2f} olarak ayarlandı!")
                return False
            else:
                print(f"✅ Doğru: close_price NULL olarak kaldı")
        
        return True

def run_comprehensive_tests():
    """Tüm senaryoları test et"""
    print("="*100)
    print("AYNI AÇILIŞ/KAPANIŞ FİYATI BUG FIX - KAPSAMLI TESTLER")
    print("="*100)
    
    # Test senaryoları
    test_cases = [
        # (name, start_position, action, should_set_close_price)
        ("Boş Portfolio → BUY (LONG açılış)", 0.0, "BUY", False),
        ("Boş Portfolio → SELL (SHORT açılış)", 0.0, "SELL", False),
        ("LONG Pozisyon → BUY (artırma)", 0.5, "BUY", False),
        ("SHORT Pozisyon → SELL (artırma)", -0.5, "SELL", False),
        ("LONG Pozisyon → SELL (kapatma)", 0.5, "SELL", True),
        ("SHORT Pozisyon → BUY (kapatma)", -0.5, "BUY", True),
    ]
    
    results = []
    for i, (name, start_pos, action, should_close) in enumerate(test_cases, 1):
        try:
            # Her testten önce temizle
            cleanup_test_data()
            
            success = test_scenario(name, start_pos, action, should_close)
            results.append((name, success))
            
            if success:
                print(f"✅ Test {i}: BAŞARILI")
            else:
                print(f"❌ Test {i}: BAŞARISIZ")
                
        except Exception as e:
            print(f"💥 Test {i}: HATA - {e}")
            results.append((name, False))
    
    # Özet
    print(f"\n{'='*100}")
    print("TEST ÖZETİ")
    print(f"{'='*100}")
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for i, (name, success) in enumerate(results, 1):
        status = "✅ BAŞARILI" if success else "❌ BAŞARISIZ"
        print(f"{i}. {name}")
        print(f"   Sonuç: {status}")
    
    print(f"\n📊 Genel Durum: {passed}/{total} test başarılı ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("\n🎉 TÜM TESTLER BAŞARILI! Bug düzeltmesi tamamlanmış. 🎉")
        return True
    else:
        print(f"\n⚠️  {total-passed} test başarısız. Bug düzeltmesi tam değil!")
        return False

if __name__ == "__main__":
    success = run_comprehensive_tests()
    sys.exit(0 if success else 1)
