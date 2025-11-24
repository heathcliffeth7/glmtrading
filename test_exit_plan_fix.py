"""
Test: Exit Plan Update - Session Cache Fix
Pozisyon açıp exit plan güncellemesi yaptıktan sonra yeni değerlerin görünüp görünmediğini test eder
"""
import sys
from datetime import datetime

from sqlalchemy.orm import Session

from app.executor.ledger import (
    Portfolio,
    Trade,
    engine,
    get_open_position_with_exit_plan,
)
from app.executor.portfolio_sync import sync_portfolio_with_trades
from app.utils.logging import configure_logging, get_logger

configure_logging("INFO")
logger = get_logger(__name__)


def cleanup_test_data(session: Session, symbol: str = "BTCUSDT"):
    """Test verisini temizle"""
    session.query(Trade).filter_by(symbol=symbol, close_price=None).delete()
    session.query(Portfolio).filter_by(symbol=symbol).delete()
    session.commit()
    logger.info("✅ Test data cleaned up")


def create_test_position(session: Session, symbol: str = "BTCUSDT") -> str:
    """Test pozisyonu oluştur"""
    position_id = f"TEST-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    
    # Trade oluştur
    trade = Trade(
        position_id=position_id,
        symbol=symbol,
        side="BUY",
        position_side="LONG",
        amount=0.1,
        price=100000.0,
        close_price=None,  # Açık pozisyon
        leverage=10.0,
        exit_plan={
            "profit_target": 105000.0,
            "stop_loss": 95000.0,
            "invalidation_condition": "If price closes below 97000 on 3m candle"
        },
        timestamp=datetime.utcnow()
    )
    
    session.add(trade)
    session.commit()
    
    # Portfolio oluştur
    portfolio = Portfolio(
        symbol=symbol,
        long_position=0.1,
        long_avg_price=100000.0,
        short_position=0.0,
        short_avg_price=None,
        net_position=0.1,
        position=0.1,
        average_price=100000.0,
        updated_at=datetime.utcnow()
    )
    
    session.add(portfolio)
    session.commit()
    
    logger.info(f"✅ Test position created: {position_id}")
    logger.info(f"   Entry: $100,000 | SL: $95,000 | TP: $105,000")
    
    return position_id


def update_exit_plan_directly(session: Session, position_id: str):
    """Exit plan'ı direkt database'de güncelle (DynamicExitUpdater simülasyonu)"""
    trade = session.query(Trade).filter_by(
        position_id=position_id,
        close_price=None
    ).first()
    
    if not trade:
        logger.error("Trade not found!")
        return False
    
    # Yeni exit plan
    new_exit_plan = {
        "profit_target": 110000.0,  # 105k -> 110k (güncellendi)
        "stop_loss": 92000.0,       # 95k -> 92k (güncellendi)
        "invalidation_condition": "If price closes below 94000 on 3m candle"  # 97k -> 94k
    }
    
    old_exit_plan = trade.exit_plan.copy()
    trade.exit_plan = new_exit_plan
    
    session.commit()
    
    logger.info("✅ Exit plan updated in DB")
    logger.info(f"   OLD: SL=$95,000 TP=$105,000 INV='below 97000'")
    logger.info(f"   NEW: SL=$92,000 TP=$110,000 INV='below 94000'")
    
    return True


def test_exit_plan_refresh():
    """Ana test: Exit plan güncellemesi görünüyor mu?"""
    symbol = "BTCUSDT"
    
    logger.info("=" * 80)
    logger.info("TEST BAŞLIYOR: Exit Plan Update - Session Cache Fix")
    logger.info("=" * 80)
    
    with Session(engine) as session:
        # 1. Temizlik
        logger.info("\n[1/5] Cleaning up old test data...")
        cleanup_test_data(session, symbol)
        
        # 2. Test pozisyonu oluştur
        logger.info("\n[2/5] Creating test position...")
        position_id = create_test_position(session, symbol)
        
        # 3. İlk okuma (güncellemeden ÖNCE)
        logger.info("\n[3/5] Reading position BEFORE update...")
        position_before = get_open_position_with_exit_plan(session, symbol)
        
        if not position_before:
            logger.error("❌ TEST FAILED: Position not found!")
            return False
        
        exit_plan_before = position_before['exit_plan']
        logger.info(f"   BEFORE: SL={exit_plan_before.get('stop_loss')} TP={exit_plan_before.get('profit_target')}")
        
        # 4. Exit plan güncelle
        logger.info("\n[4/5] Updating exit plan in database...")
        update_exit_plan_directly(session, position_id)
        
        # 5. İkinci okuma (güncellemeden SONRA) - session.expire_all() devreye girer
        logger.info("\n[5/5] Reading position AFTER update...")
        position_after = get_open_position_with_exit_plan(session, symbol)
        
        if not position_after:
            logger.error("❌ TEST FAILED: Position not found after update!")
            return False
        
        exit_plan_after = position_after['exit_plan']
        logger.info(f"   AFTER:  SL={exit_plan_after.get('stop_loss')} TP={exit_plan_after.get('profit_target')}")
        
        # 6. Doğrulama
        logger.info("\n" + "=" * 80)
        logger.info("TEST SONUÇLARI")
        logger.info("=" * 80)
        
        expected_sl = 92000.0
        expected_tp = 110000.0
        actual_sl = exit_plan_after.get('stop_loss')
        actual_tp = exit_plan_after.get('profit_target')
        
        sl_match = abs(actual_sl - expected_sl) < 0.01
        tp_match = abs(actual_tp - expected_tp) < 0.01
        
        if sl_match and tp_match:
            logger.info("✅ TEST PASSED!")
            logger.info(f"   ✅ Stop Loss güncellendi: $95,000 → ${actual_sl:,.0f}")
            logger.info(f"   ✅ Profit Target güncellendi: $105,000 → ${actual_tp:,.0f}")
            logger.info("\n💡 session.expire_all() fix'i ÇALIŞIYOR!")
            success = True
        else:
            logger.error("❌ TEST FAILED!")
            logger.error(f"   ❌ Stop Loss: expected=${expected_sl:,.0f}, got=${actual_sl:,.0f}")
            logger.error(f"   ❌ Profit Target: expected=${expected_tp:,.0f}, got=${actual_tp:,.0f}")
            logger.error("\n💥 session.expire_all() fix'i ÇALIŞMIYOR - eski değerler kalıyor!")
            success = False
        
        # Temizlik
        logger.info("\nCleaning up test data...")
        cleanup_test_data(session, symbol)
        
        return success


if __name__ == "__main__":
    try:
        success = test_exit_plan_refresh()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Test exception: {e}", exc_info=True)
        sys.exit(1)
