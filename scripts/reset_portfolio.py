#!/usr/bin/env python3
"""
Reset portfolio: Close all positions and reset database
"""
import sys
sys.path.insert(0, '/root/trading')

from app.executor.executor import Executor
from app.executor.ledger import Session, engine, Trade, Portfolio, DailyPnL
from app.risk_manager.manager import RiskDecision
from app.utils.logging import get_logger

logger = get_logger(__name__)


def close_position():
    """Close current open position"""
    executor = Executor('BTCUSDT')
    metrics = executor.portfolio_metrics()
    
    position = metrics['position']
    
    if abs(position) < 0.0001:  # No position
        print("✅ Açık pozisyon yok, kapatma gereksiz")
        return
    
    print(f"\n📊 Kapatılacak Pozisyon: {position:+.4f} BTC")
    print(f"💵 Güncel Fiyat: ${metrics['price']:,.2f}")
    print(f"💎 Unrealized PnL: ${metrics['unrealized_pnl']:,.2f}")
    
    # Create close decision
    if position > 0:  # LONG position, SELL to close
        action = "SELL"
        amount = position
    else:  # SHORT position, BUY to close
        action = "BUY"
        amount = abs(position)
    
    print(f"\n🔄 Pozisyon Kapatılıyor: {action} {amount:.4f} BTC @ ${metrics['price']:,.2f}")
    
    # Execute close
    decision = RiskDecision(
        action=action,
        amount=1.0,  # Use full available (will calculate exact amount needed)
        leverage=1.0,  # No leverage for closing
        reasoning="Manuel pozisyon kapatma - sistem reset"
    )
    
    # Calculate exact amount to close
    decision.amount = amount / metrics['price']  # Convert BTC to equity ratio
    
    result = executor.execute(decision)
    
    print(f"\n✅ Pozisyon Kapatıldı: {result.status}")
    
    # Verify
    new_metrics = executor.portfolio_metrics()
    print(f"📊 Yeni Pozisyon: {new_metrics['position']:+.4f} BTC")
    print(f"✅ Realized PnL: ${new_metrics['realized_pnl']:,.2f}")


def reset_database():
    """Reset all tables to initial state"""
    print("\n" + "="*50)
    print("DATABASE RESET")
    print("="*50)
    
    with Session(engine) as session:
        # Count before
        trade_count = session.query(Trade).count()
        portfolio_count = session.query(Portfolio).count()
        pnl_count = session.query(DailyPnL).count()
        
        print(f"\n📊 Silinecek Kayıtlar:")
        print(f"  - Trades: {trade_count}")
        print(f"  - Portfolios: {portfolio_count}")
        print(f"  - Daily PnL: {pnl_count}")
        
        # Delete all
        session.query(Trade).delete()
        session.query(Portfolio).delete()
        session.query(DailyPnL).delete()
        
        session.commit()
        
        print("\n✅ Tüm kayıtlar silindi")
        
        # Verify
        trade_count = session.query(Trade).count()
        portfolio_count = session.query(Portfolio).count()
        pnl_count = session.query(DailyPnL).count()
        
        print(f"\n📊 Mevcut Kayıtlar:")
        print(f"  - Trades: {trade_count}")
        print(f"  - Portfolios: {portfolio_count}")
        print(f"  - Daily PnL: {pnl_count}")


def main():
    print("╔══════════════════════════════════════════╗")
    print("║   PORTFOLIO RESET                        ║")
    print("╚══════════════════════════════════════════╝")
    
    # Show current state
    executor = Executor('BTCUSDT')
    metrics = executor.portfolio_metrics()
    
    print(f"\n💰 Mevcut Equity: ${metrics['equity']:,.2f}")
    print(f"📊 Açık Pozisyon: {metrics['position']:+.4f} BTC")
    print(f"💎 Toplam PnL: ${metrics['total_pnl']:,.2f}")
    
    # Step 1: Close position if any
    try:
        close_position()
    except Exception as e:
        print(f"⚠️  Pozisyon kapatma hatası: {e}")
        logger.error(f"Failed to close position: {e}", exc_info=True)
    
    # Step 2: Reset database
    try:
        reset_database()
    except Exception as e:
        print(f"⚠️  Database reset hatası: {e}")
        logger.error(f"Failed to reset database: {e}", exc_info=True)
        raise
    
    print("\n" + "="*50)
    print("✅ RESET TAMAMLANDI!")
    print("="*50)
    print("\n📝 Sonraki adım: Orchestrator restart")
    print("   systemctl restart trading-orchestrator")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Reset portfolio and database')
    parser.add_argument('--confirm', action='store_true', help='Confirm reset')
    args = parser.parse_args()
    
    if not args.confirm:
        print("⚠️  Bu işlem:")
        print("  1. Tüm açık pozisyonları kapatır")
        print("  2. Trade geçmişini siler")
        print("  3. Portfolio'yu sıfırlar")
        print("\nDevam etmek için --confirm flag kullanın:")
        print("  python scripts/reset_portfolio.py --confirm")
        sys.exit(0)
    
    main()
