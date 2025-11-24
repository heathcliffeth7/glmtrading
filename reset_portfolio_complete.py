#!/usr/bin/env python3
"""
Portfolio'yu sıfırdan başlatma scripti
Tüm portfolio verilerini temizler ve başlangıç durumuna getirir
"""

from app.executor.ledger import Session, engine, Portfolio, DailyPnL, Trade, PredictionLog, FeedbackSummary
from sqlalchemy import text
import sys

def reset_portfolio_completely():
    """Portföyü tamamen sıfırdan başlat"""

    print("⚠️  PORTFOLYO RESET İŞLEMİ BAŞLATILIYOR...")
    print("Bu işlem şunları silecektir:")
    print("  - Tüm trade kayıtları")
    print("  - Portfolio pozisyonları")
    print("  - Daily PnL kayıtları")
    print("  - Prediction log'ları")
    print("  - Feedback summary'leri")
    print()

    # Onay iste (konsol üzerinden)
    confirm = input("Devam etmek istiyor musunuz? (yes/no): ").lower().strip()
    if confirm not in ['yes', 'y', 'evet']:
        print("❌ İşlem iptal edildi.")
        return

    try:
        with Session(engine) as session:
            print("🗑️  Veriler siliniyor...")

            # 1. Prediction log'ları sil (foreign key constraint için önce)
            deleted_predictions = session.query(PredictionLog).delete()

            # 2. Feedback summary'leri sil
            deleted_feedback = session.query(FeedbackSummary).delete()

            # 3. Daily PnL kayıtlarını sil
            deleted_pnl = session.query(DailyPnL).delete()

            # 4. Tüm trade'leri sil
            deleted_trades = session.query(Trade).delete()

            # 5. Portfolio kayıtlarını sil
            deleted_portfolios = session.query(Portfolio).delete()

            # Commit et
            session.commit()

            print("✅ TEMİZLİK BAŞARILI")
            print(f"   - Silinen prediction log: {deleted_predictions}")
            print(f"   - Silinen feedback summary: {deleted_feedback}")
            print(f"   - Silinen daily PnL: {deleted_pnl}")
            print(f"   - Silinen trade: {deleted_trades}")
            print(f"   - Silinen portfolio: {deleted_portfolios}")

            # Temizlik sonrası kontrol
            print("\n🔍 TEMİZLİK SONRASI KONTROL:")
            remaining_trades = session.query(Trade).count()
            remaining_portfolios = session.query(Portfolio).count()
            remaining_pnl = session.query(DailyPnL).count()

            print(f"   - Kalan trade sayısı: {remaining_trades}")
            print(f"   - Kalan portfolio sayısı: {remaining_portfolios}")
            print(f"   - Kalan daily PnL sayısı: {remaining_pnl}")

            if remaining_trades == 0 and remaining_portfolios == 0 and remaining_pnl == 0:
                print("✅ Portföy tamamen sıfırlandı!")
            else:
                print("⚠️  Bazı kayıtlar kaldığı için tekrar kontrol edin.")

    except Exception as e:
        print(f"❌ Hata oluştu: {e}")
        session.rollback()
        raise

def show_reset_status():
    """Reset sonrası durumu göster"""
    print("\n=== PORTFOLYO RESET SONRASI DURUM ===")
    with Session(engine) as session:
        portfolios = session.query(Portfolio).all()
        print(f"Portfolio kayıtları: {len(portfolios)}")

        trades = session.query(Trade).count()
        print(f"Trade kayıtları: {trades}")

        pnl_count = session.query(DailyPnL).count()
        print(f"Daily PnL kayıtları: {pnl_count}")

        predictions = session.query(PredictionLog).count()
        print(f"Prediction log kayıtları: {predictions}")

        feedback = session.query(FeedbackSummary).count()
        print(f"Feedback summary kayıtları: {feedback}")

if __name__ == "__main__":
    try:
        reset_portfolio_completely()
        show_reset_status()
        print("\n🎉 Portföy başarıyla sıfırlandı! Sistemi yeniden başlatabilirsiniz.")
    except Exception as e:
        print(f"❌ Reset başarısız: {e}")
        sys.exit(1)
