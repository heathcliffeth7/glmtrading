#!/usr/bin/env python3
"""
Position Monitor Test - Exit plan kontrol mekanizmasını test eder
"""
import asyncio
from datetime import datetime

from sqlalchemy.orm import Session

from app.executor.executor import Executor
from app.executor.ledger import (
    DailyPnL,
    Portfolio,
    Trade,
    engine,
    get_daily_pnl,
    get_open_position_with_exit_plan,
    get_portfolio,
    record_trade,
)
from app.monitoring.exit_checker import (
    check_all_exit_conditions,
    check_invalidation_condition,
    check_profit_target,
    check_stop_loss,
    parse_invalidation_condition,
)
from app.monitoring.position_monitor import PositionMonitor
from app.utils.logging import configure_logging, get_logger
from app.utils.price_cache import price_cache

configure_logging()
logger = get_logger(__name__)


def setup_test_position(session: Session, is_long: bool = True):
    """Test için pozisyon aç"""
    symbol = "BTCUSDT"
    
    # Portfolio temizle
    portfolio = session.query(Portfolio).filter_by(symbol=symbol).first()
    if not portfolio:
        portfolio = Portfolio(symbol=symbol, position=0.0, average_price=0.0)
        session.add(portfolio)
    else:
        portfolio.position = 0.0
        portfolio.average_price = 0.0
    
    # Daily PnL
    daily_pnl = session.query(DailyPnL).filter_by(date=datetime.utcnow().date()).first()
    if not daily_pnl:
        daily_pnl = DailyPnL(date=datetime.utcnow().date())
        session.add(daily_pnl)
    
    session.flush()
    
    # Test pozisyonu aç
    entry_price = 100000.0
    quantity = 0.01  # 0.01 BTC
    
    # Exit plan oluştur
    if is_long:
        exit_plan = {
            "profit_target": 105000.0,  # +5%
            "stop_loss": 98000.0,        # -2%
            "invalidation_condition": "If price closes below 98000 on 3m candle"
        }
        side = "BUY"
        position_side = "LONG"
        position_qty = quantity
    else:
        exit_plan = {
            "profit_target": 95000.0,    # -5% (SHORT için kar)
            "stop_loss": 102000.0,       # +2% (SHORT için zarar)
            "invalidation_condition": "If price closes above 102000 on 3m candle"
        }
        side = "SELL"
        position_side = "SHORT"
        position_qty = -quantity
    
    trade = record_trade(
        session=session,
        symbol=symbol,
        side=side,
        position_side=position_side,
        amount=quantity,
        price=entry_price,
        pnl=0.0,  # İlk açılışta PnL 0
        leverage=1.0,
        fees=0.0,
        exit_plan=exit_plan
    )
    
    # Portfolio güncelle
    portfolio.position = position_qty
    portfolio.average_price = entry_price
    portfolio.updated_at = datetime.utcnow()
    
    session.commit()
    
    logger.info(
        "✅ Test position created | %s | qty=%.6f | entry=%.2f | exit_plan=%s",
        position_side,
        quantity,
        entry_price,
        exit_plan
    )
    
    return trade.id


def test_exit_checker():
    """Exit checker fonksiyonlarını test et"""
    logger.info("=" * 80)
    logger.info("TEST 1: Exit Checker Functions")
    logger.info("=" * 80)
    
    # Test 1: Profit Target (LONG)
    result = check_profit_target(
        entry_price=100000.0,
        current_price=105500.0,
        profit_target=105000.0,
        is_long=True
    )
    assert result == True, "Profit target should be reached (LONG)"
    logger.info("✅ Profit target check (LONG) - PASSED")
    
    # Test 2: Stop Loss (LONG)
    result = check_stop_loss(
        entry_price=100000.0,
        current_price=97500.0,
        stop_loss=98000.0,
        is_long=True
    )
    assert result == True, "Stop loss should be triggered (LONG)"
    logger.info("✅ Stop loss check (LONG) - PASSED")
    
    # Test 3: Invalidation Parsing
    direction, price = parse_invalidation_condition(
        "If price closes below 98000 on 3m candle"
    )
    assert direction == "below" and price == 98000.0
    logger.info("✅ Invalidation parsing - PASSED")
    
    # Test 4: Invalidation Check
    triggered, reason = check_invalidation_condition(
        current_price=97500.0,
        invalidation_condition="If price closes below 98000 on 3m candle",
        is_long=True
    )
    assert triggered == True, "Invalidation should be triggered"
    logger.info("✅ Invalidation check - PASSED")
    
    # Test 5: All Exit Conditions (none triggered)
    exit_plan = {
        "profit_target": 105000.0,
        "stop_loss": 98000.0,
        "invalidation_condition": "If price closes below 98000 on 3m candle"
    }
    should_close, trigger_type, reason = check_all_exit_conditions(
        entry_price=100000.0,
        current_price=101000.0,  # Between entry and profit target
        exit_plan=exit_plan,
        is_long=True
    )
    assert should_close == False, "No exit condition should be met"
    logger.info("✅ All exit conditions (none) - PASSED")
    
    # Test 6: All Exit Conditions (profit target triggered)
    should_close, trigger_type, reason = check_all_exit_conditions(
        entry_price=100000.0,
        current_price=105500.0,
        exit_plan=exit_plan,
        is_long=True
    )
    assert should_close == True and trigger_type == "profit_target"
    logger.info("✅ All exit conditions (profit target) - PASSED")
    
    logger.info("\n✅ ALL EXIT CHECKER TESTS PASSED\n")


def test_ledger_query():
    """Ledger query fonksiyonunu test et"""
    logger.info("=" * 80)
    logger.info("TEST 2: Ledger Query")
    logger.info("=" * 80)
    
    with Session(engine) as session:
        # Test pozisyonu aç
        trade_id = setup_test_position(session, is_long=True)
        
        # Query ile pozisyonu al
        position_info = get_open_position_with_exit_plan(session, "BTCUSDT")
        
        assert position_info is not None, "Position should exist"
        assert position_info["is_long"] == True
        assert position_info["entry_price"] == 100000.0
        assert "exit_plan" in position_info
        assert position_info["exit_plan"]["profit_target"] == 105000.0
        
        logger.info("✅ Position info retrieved: %s", position_info)
        
        # Pozisyonu kapat
        trade = session.query(Trade).get(trade_id)
        trade.close_price = 105500.0
        trade.close_time = datetime.utcnow()
        
        # Portfolio'yu temizle
        portfolio = session.query(Portfolio).filter_by(symbol="BTCUSDT").first()
        portfolio.position = 0.0
        portfolio.average_price = 0.0
        session.commit()
        
        # Query tekrar - pozisyon olmamalı
        position_info = get_open_position_with_exit_plan(session, "BTCUSDT")
        assert position_info is None, "Position should be closed"
        
        logger.info("✅ Position closed, query returns None")
    
    logger.info("\n✅ LEDGER QUERY TEST PASSED\n")


def test_position_monitor_ignores_non_websocket_price():
    """PositionMonitor, WebSocket dışı cache fiyatlarını exit için kullanmamalı."""
    symbol = "ETHUSDT"

    # REST / diğer kaynak gibi davranan bir snapshot yaz
    price_cache.force_reset(symbol, 3600.0, source="rest_api")

    monitor = PositionMonitor(
        symbol=symbol,
        interval_seconds=180,
        executor=None,
        enable_telegram=False,
    )

    price = asyncio.run(monitor._get_current_price(symbol=symbol))
    assert price is None, "Non-websocket source price MUST be ignored by PositionMonitor"


def test_position_monitor_uses_websocket_price():
    """PositionMonitor, WebSocket kaynağından gelen fiyatı kullanmalı."""
    symbol = "ETHUSDT"

    # Gerçek WebSocket kline kaynağını simüle et
    price_cache.force_reset(symbol, 2800.0, source="binance_websocket")

    monitor = PositionMonitor(
        symbol=symbol,
        interval_seconds=180,
        executor=None,
        enable_telegram=False,
    )

    price = asyncio.run(monitor._get_current_price(symbol=symbol))
    assert price == 2800.0


async def test_position_monitor_cycle():
    """Position monitor döngüsünü test et"""
    logger.info("=" * 80)
    logger.info("TEST 3: Position Monitor Cycle")
    logger.info("=" * 80)
    
    # Test pozisyonu aç
    with Session(engine) as session:
        trade_id = setup_test_position(session, is_long=True)
    
    # Executor mock (gerçek executor olmadan test)
    executor = Executor(symbol="BTCUSDT")
    
    # Position monitor oluştur (10 saniye interval - test için)
    monitor = PositionMonitor(
        symbol="BTCUSDT",
        interval_seconds=10,
        executor=None,  # Executor olmadan test (sadece kontrol)
        enable_telegram=False
    )
    
    # Tek bir monitor cycle çalıştır
    try:
        logger.info("Running monitor cycle...")
        await monitor._monitor_cycle()
        logger.info("✅ Monitor cycle completed without errors")
    except Exception as exc:
        logger.error("Monitor cycle failed: %s", exc, exc_info=True)
        raise
    
    # Pozisyonu temizle
    with Session(engine) as session:
        trade = session.query(Trade).get(trade_id)
        if trade:
            trade.close_price = 105500.0
            trade.close_time = datetime.utcnow()
        
        portfolio = session.query(Portfolio).filter_by(symbol="BTCUSDT").first()
        if portfolio:
            portfolio.position = 0.0
            portfolio.average_price = 0.0
        
        session.commit()
    
    logger.info("\n✅ POSITION MONITOR CYCLE TEST PASSED\n")


async def main():
    """Ana test runner"""
    logger.info("Starting Position Monitor Tests...")
    logger.info("")
    
    try:
        # Test 1: Exit checker fonksiyonları
        test_exit_checker()
        
        # Test 2: Ledger query
        test_ledger_query()
        
        # Test 3: Position monitor cycle
        await test_position_monitor_cycle()
        
        logger.info("=" * 80)
        logger.info("🎉 ALL TESTS PASSED!")
        logger.info("=" * 80)
        
    except Exception as exc:
        logger.error("❌ TEST FAILED: %s", exc, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
