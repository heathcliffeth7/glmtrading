"""
Portfolio veritabanını temizle ve trades'den yeniden hesapla.
Kullanım: python fix_portfolio_database.py
"""

from sqlalchemy.orm import Session
from app.executor.ledger import engine, Portfolio, Trade, DailyPnL
from app.executor.portfolio_sync import calculate_actual_position_from_trades
from datetime import date


def fix_portfolio():
    with Session(engine) as session:
        symbol = "BTCUSDT"
        
        print("🔧 Portfolio veritabanı düzeltiliyor...")
        print("")
        
        # 1. Portfolio tablosunu kontrol et
        portfolio = session.query(Portfolio).filter_by(symbol=symbol).first()
        if portfolio:
            print(f"📊 Mevcut Portfolio Durumu:")
            print(f"   Pozisyon: {portfolio.position:.6f} BTC")
            print(f"   Ortalama Fiyat: ${portfolio.average_price:.2f}")
            
            # Astronomik değer kontrolü
            if abs(portfolio.position) > 10.0:
                print(f"   ❌ HATA: Astronomik pozisyon tespit edildi!")
            elif abs(portfolio.position) < 0.0001:
                print(f"   ✅ Pozisyon: FLAT")
            else:
                print(f"   ℹ️  Pozisyon: {'LONG' if portfolio.position > 0 else 'SHORT'}")
        else:
            print("⚠️  Portfolio kaydı bulunamadı, yeni kayıt oluşturulacak")
        
        print("")
        
        # 2. Trades'den gerçek pozisyonu hesapla
        print("🔍 Trades tablosundan gerçek pozisyon hesaplanıyor...")
        actual_state = calculate_actual_position_from_trades(session, symbol)
        
        print(f"✅ Gerçek Pozisyon (trades'den hesaplanan):")
        print(f"   Pozisyon: {actual_state['position']:.6f} BTC")
        print(f"   Ortalama Fiyat: ${actual_state['average_price']:.2f}")
        print(f"   Toplam Maliyet: ${actual_state['total_cost']:.2f}")
        print("")
        
        # 3. Portfolio'yu güncelle veya oluştur
        if not portfolio:
            portfolio = Portfolio(symbol=symbol)
            session.add(portfolio)
            print("📝 Yeni portfolio kaydı oluşturuldu")
        
        # Değerleri güncelle
        old_position = portfolio.position
        old_avg_price = portfolio.average_price
        
        portfolio.position = actual_state['position']
        portfolio.average_price = actual_state['average_price']
        
        # 4. Günlük PnL'yi kontrol et
        daily_pnl = session.query(DailyPnL).filter_by(date=date.today()).first()
        if daily_pnl:
            print(f"💰 Günlük PnL Durumu:")
            print(f"   Gerçekleşen PnL: ${daily_pnl.realized_pnl:.2f}")
            print(f"   Gerçekleşmemiş PnL: ${daily_pnl.unrealized_pnl:.2f}")
            
            # Astronomik PnL kontrolü
            if abs(daily_pnl.realized_pnl) > 100000 or abs(daily_pnl.unrealized_pnl) > 100000:
                print(f"   ⚠️  Uyarı: PnL değerleri çok yüksek, sıfırlamak isteyebilirsiniz")
                print(f"   (Scriptin içindeki ilgili satırların yorumunu kaldırın)")
        
        print("")
        
        # Değişiklikleri kaydet
        session.commit()
        
        # 5. Özet rapor
        print("=" * 60)
        print("✅ Portfolio Düzeltme Tamamlandı!")
        print("=" * 60)
        
        if abs(old_position - actual_state['position']) > 0.0001:
            print(f"🔄 Pozisyon Güncellendi:")
            print(f"   Eski: {old_position:.6f} BTC @ ${old_avg_price:.2f}")
            print(f"   Yeni: {actual_state['position']:.6f} BTC @ ${actual_state['average_price']:.2f}")
        else:
            print(f"✓ Pozisyon zaten doğru (değişiklik yapılmadı)")
        
        print("")
        print(f"📊 Güncel Durum:")
        print(f"   Symbol: {symbol}")
        print(f"   Pozisyon: {portfolio.position:.6f} BTC")
        print(f"   Ortalama Fiyat: ${portfolio.average_price:.2f}")
        
        if abs(portfolio.position) > 0.0001:
            position_type = "LONG (pozitif)" if portfolio.position > 0 else "SHORT (negatif)"
            print(f"   Pozisyon Tipi: {position_type}")
        else:
            print(f"   Pozisyon Tipi: FLAT (pozisyon yok)")
        
        print("")
        print("✅ Sistem artık doğru değerlerle çalışmaya hazır!")
        print("")
        
        # Opsiyonel: Trade istatistikleri
        total_trades = session.query(Trade).filter_by(symbol=symbol).count()
        open_trades = session.query(Trade).filter_by(symbol=symbol).filter(Trade.close_price.is_(None)).count()
        closed_trades = total_trades - open_trades
        
        print(f"📈 Trade İstatistikleri:")
        print(f"   Toplam İşlem: {total_trades}")
        print(f"   Açık İşlem: {open_trades}")
        print(f"   Kapalı İşlem: {closed_trades}")


def reset_daily_pnl():
    """Günlük PnL'yi sıfırla (ihtiyaç halinde çağrılabilir)"""
    with Session(engine) as session:
        daily_pnl = session.query(DailyPnL).filter_by(date=date.today()).first()
        if daily_pnl:
            print(f"⚠️  Günlük PnL sıfırlanıyor...")
            print(f"   Önceki Realized PnL: ${daily_pnl.realized_pnl:.2f}")
            print(f"   Önceki Unrealized PnL: ${daily_pnl.unrealized_pnl:.2f}")
            
            daily_pnl.realized_pnl = 0.0
            daily_pnl.unrealized_pnl = 0.0
            session.commit()
            
            print(f"✅ Günlük PnL sıfırlandı")
        else:
            print("ℹ️  Bugüne ait PnL kaydı bulunamadı")


if __name__ == "__main__":
    import sys
    
    print("")
    print("=" * 60)
    print("  TRADING BOT - PORTFOLIO DATABASE FIX")
    print("=" * 60)
    print("")
    
    if len(sys.argv) > 1 and sys.argv[1] == "--reset-pnl":
        # PnL'yi sıfırla
        reset_daily_pnl()
    else:
        # Normal portfolio düzeltme
        fix_portfolio()
    
    print("")
    print("💡 Not: Günlük PnL'yi sıfırlamak için:")
    print("   python fix_portfolio_database.py --reset-pnl")
    print("")

