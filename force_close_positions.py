#!/usr/bin/env python3
"""Açık pozisyonları zorla kapat"""
import os
import sys

# Trading dizinine git
sys.path.insert(0, '/root/trading')

# Environment variables
os.environ.setdefault('PYTHONPATH', '/root/trading')

try:
    from sqlalchemy.orm import Session
    from app.executor.ledger import engine, Trade, close_open_trades
    from app.executor.portfolio_sync import get_synced_portfolio
    from app.utils.price_cache import price_cache
    from datetime import datetime
    
    symbol = "BTCUSDT"
    
    with Session(engine) as session:
        # Açık trade'leri bul
        open_trades = (
            session.query(Trade)
            .filter_by(symbol=symbol)
            .filter(Trade.close_price.is_(None))
            .all()
        )
        
        print(f"📊 Açık trade sayısı: {len(open_trades)}")
        
        if not open_trades:
            print("✅ Açık pozisyon yok")
            sys.exit(0)
        
        # Her trade'i göster
        for trade in open_trades:
            quantity = trade.amount if hasattr(trade, 'amount') else 0.0
            print(f"  - Trade {trade.id}: {trade.side} {quantity} BTC @ ${trade.price:.2f} (Position: {trade.position_id})")
        
        # Portfolio'yu kontrol et
        portfolio = get_synced_portfolio(session, symbol)
        print(f"\nPortfolio pozisyon: {portfolio.position}")
        
        # Current price al
        current_price = price_cache.get(symbol)
        if not current_price:
            # Fallback: REST API'den al
            try:
                import httpx
                response = httpx.get(
                    "https://fapi.binance.com/fapi/v1/ticker/price",
                    params={"symbol": symbol},
                    timeout=5.0
                )
                current_price = float(response.json()["price"])
                print(f"✅ Fiyat API'den alındı: ${current_price:.2f}")
            except Exception as e:
                print(f"⚠️ Fiyat alınamadı, fallback kullanılıyor: {e}")
                current_price = 100000.0
        
        print(f"Current price: ${current_price:.2f}\n")
        
        if abs(portfolio.position) > 0.0001:
            # Normal pozisyon kapatma
            is_long = portfolio.position > 0
            close_side = "SELL" if is_long else "BUY"
            close_amount = abs(portfolio.position)
            
            print(f"📉 Pozisyon kapatılıyor: {close_side} {close_amount:.6f} BTC @ ${current_price:.2f}")
            
            updated_count, realized_delta, closing_fee = close_open_trades(
                session=session,
                symbol=symbol,
                close_side=close_side,
                close_amount=close_amount,
                close_price=current_price,
                taker_fee_rate=0.0004
            )
            
            # Portfolio'yu güncelle
            portfolio.position = 0.0
            portfolio.average_price = 0.0
            portfolio.updated_at = datetime.utcnow()
            
            session.commit()
            print(f"✅ Pozisyon kapatıldı: {updated_count} trade, PnL: ${realized_delta:.2f}, Fee: ${closing_fee:.2f}")
        else:
            # Portfolio'da pozisyon yok ama açık trade var - manuel kapatma
            print("⚠️ Portfolio'da pozisyon yok ama açık trade'ler var - manuel kapatma yapılıyor")
            
            for trade in open_trades:
                close_side = "SELL" if trade.side == "BUY" else "BUY"
                trade_amount = trade.amount if hasattr(trade, 'amount') else 0.0
                
                print(f"Kapatılıyor: Trade {trade.id} - {trade.side} {trade_amount} BTC @ ${trade.price:.2f}")
                
                updated_count, realized_delta, closing_fee = close_open_trades(
                    session=session,
                    symbol=symbol,
                    close_side=close_side,
                    close_amount=trade_amount,
                    close_price=current_price,
                    taker_fee_rate=0.0004
                )
                
                print(f"  ✅ Kapatıldı: {updated_count} trade, PnL: ${realized_delta:.2f}, Fee: ${closing_fee:.2f}")
            
            session.commit()
            print(f"\n✅ Tüm açık trade'ler kapatıldı")
        
        # Son kontrol
        remaining = session.query(Trade).filter_by(symbol=symbol).filter(Trade.close_price.is_(None)).count()
        print(f"\n📊 Kalan açık trade sayısı: {remaining}")
        
except Exception as e:
    print(f"❌ Hata: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)

