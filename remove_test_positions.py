#!/usr/bin/env python3
"""
Test pozisyonlarını PostgreSQL'den kaldırma scripti
"""

from app.executor.ledger import Session, engine, Trade, PredictionLog
from app.executor.portfolio_sync import sync_portfolio_with_trades
from sqlalchemy import text
import sys

def remove_test_positions():
    """Test pozisyonlarını ve ilgili kayıtları kaldır"""

    print("🔍 Test pozisyonları kontrol ediliyor...")

    with Session(engine) as session:
        # Test pozisyonlarını bul (position_id 'test' ile başlayanlar)
        test_trades = session.execute(
            text("SELECT id, position_id, symbol, side, amount, price FROM trades WHERE position_id LIKE 'test%'")
        ).fetchall()

        if not test_trades:
            print("✅ Test pozisyonu bulunamadı.")
            return

        print(f"📋 Bulunan test pozisyonları ({len(test_trades)} adet):")
        for trade in test_trades:
            print(f"  - ID: {trade.id}, Position: {trade.position_id}, {trade.symbol} {trade.side} {trade.amount} @ {trade.price}")

        # PredictionLog'lardan test pozisyonlarına ait kayıtları sil
        deleted_predictions = 0
        for trade in test_trades:
            prediction_count = session.execute(
                text("DELETE FROM prediction_logs WHERE trade_id = :trade_id"),
                {"trade_id": trade.id}
            ).rowcount
            deleted_predictions += prediction_count

        # Test trade'lerini sil
        deleted_trades = session.execute(
            text("DELETE FROM trades WHERE position_id LIKE 'test%'")
        ).rowcount

        # Portfolio'yu senkronize et
        print("🔄 Portfolio senkronizasyonu yapılıyor...")
        sync_portfolio_with_trades(session, "BTCUSDT")
        session.commit()

        print("✅ TEMİZLİK TAMAMLANDI")
        print(f"   - Silinen trade sayısı: {deleted_trades}")
        print(f"   - Silinen prediction log sayısı: {deleted_predictions}")

        # Temizlik sonrası kontrol
        remaining_open = session.execute(
            text("SELECT COUNT(*) FROM trades WHERE close_price IS NULL")
        ).scalar()

        print(f"   - Kalan açık pozisyon sayısı: {remaining_open}")

if __name__ == "__main__":
    try:
        remove_test_positions()
        print("\n🎉 Test pozisyonları başarıyla kaldırıldı!")
    except Exception as e:
        print(f"❌ Hata: {e}")
        sys.exit(1)
