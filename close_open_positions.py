#!/usr/bin/env python3
"""Açık pozisyonları kapat"""
import sys
from sqlalchemy.orm import Session
from app.executor.ledger import engine, Trade, close_open_trades, get_synced_portfolio
from app.utils.price_cache import price_cache
from app.utils.logging import get_logger

logger = get_logger(__name__)

def close_all_open_positions():
    """Tüm açık pozisyonları kapat"""
    symbol = "BTCUSDT"
    
    with Session(engine) as session:
        # Açık trade'leri bul
        open_trades = (
            session.query(Trade)
            .filter_by(symbol=symbol)
            .filter(Trade.close_price.is_(None))
            .all()
        )
        
        if not open_trades:
            logger.info("✅ Açık pozisyon yok")
            return
        
        logger.info(f"📊 {len(open_trades)} açık trade bulundu")
        
        # Portfolio'yu kontrol et
        portfolio = get_synced_portfolio(session, symbol)
        
        if abs(portfolio.position) < 0.0001:
            logger.info("⚠️ Portfolio'da pozisyon yok ama açık trade'ler var - temizleme yapılıyor")
            # Tüm açık trade'leri kapat
            for trade in open_trades:
                close_side = "SELL" if trade.side == "BUY" else "BUY"
                current_price = price_cache.get(symbol) or 100000.0  # Fallback price
                
                logger.info(f"Kapatılıyor: {trade.position_id} - {trade.side} {trade.quantity} BTC @ ${trade.price:.2f}")
                
                updated_count, realized_delta, closing_fee = close_open_trades(
                    session=session,
                    symbol=symbol,
                    close_side=close_side,
                    close_amount=trade.quantity,
                    close_price=current_price,
                    taker_fee_rate=0.0004
                )
                
                logger.info(f"✅ Kapatıldı: {updated_count} trade, PnL: ${realized_delta:.2f}, Fee: ${closing_fee:.2f}")
        else:
            # Normal pozisyon kapatma
            is_long = portfolio.position > 0
            close_side = "SELL" if is_long else "BUY"
            current_price = price_cache.get(symbol) or 100000.0
            
            logger.info(f"📉 Pozisyon kapatılıyor: {close_side} {abs(portfolio.position):.6f} BTC @ ${current_price:.2f}")
            
            updated_count, realized_delta, closing_fee = close_open_trades(
                session=session,
                symbol=symbol,
                close_side=close_side,
                close_amount=abs(portfolio.position),
                close_price=current_price,
                taker_fee_rate=0.0004
            )
            
            # Portfolio'yu güncelle
            portfolio.position = 0.0
            portfolio.average_price = 0.0
            
            session.commit()
            logger.info(f"✅ Pozisyon kapatıldı: {updated_count} trade, PnL: ${realized_delta:.2f}, Fee: ${closing_fee:.2f}")
        
        session.commit()
        logger.info("✅ Tüm açık pozisyonlar kapatıldı")

if __name__ == "__main__":
    try:
        close_all_open_positions()
    except Exception as e:
        logger.error(f"❌ Hata: {e}", exc_info=True)
        sys.exit(1)





