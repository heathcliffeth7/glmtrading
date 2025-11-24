#!/usr/bin/env python3
"""
PostgreSQL: Tüm trades kayıtlarını ve portföy durumunu temizle (BTCUSDT için)
Etkiler:
- trades: Tüm satırlar silinir
- portfolio: BTCUSDT kaydı 0 BTC ve 0 avg_price ile yeniden oluşturulur

Kullanım:
  python scripts/hard_reset_portfolio_and_trades.py
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.executor.ledger import engine, Trade, Portfolio


def main() -> None:
    print("== HARD RESET (trades + portfolio) ==")
    with Session(engine) as session:
        # 1) Delete all trades (all symbols)
        total_trades = session.query(Trade).count()
        session.query(Trade).delete()
        print(f"🗑️  Silinen trade sayısı: {total_trades}")

        # 2) Reset portfolio for BTCUSDT
        rows = session.query(Portfolio).all()
        for row in rows:
            session.delete(row)
        session.flush()
        fresh = Portfolio(symbol="BTCUSDT", position=0.0, average_price=0.0, updated_at=datetime.utcnow())
        session.add(fresh)
        print("📊 Portföy sıfırlandı: BTCUSDT -> position=0.0, avg_price=0.0")

        session.commit()
        print("✅ Tamamlandı.")


if __name__ == "__main__":
    main()
