#!/usr/bin/env python3
"""
Test brüt PnL hesaplamasının doğru çalıştığını kontrol et
"""

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.executor.executor import Executor
from app.executor.ledger import engine, Trade


def test_gross_pnl_calculation():
    print("=== TEST BRÜT PNL HESAPLAMASI ===")
    
    executor = Executor()
    symbol = "BTCUSDT"
    
    with Session(engine) as session:
        # Tüm işlemleri kontrol et
        all_trades = session.query(Trade).filter(Trade.symbol == symbol).all()
        print(f"\nToplam işlem sayısı: {len(all_trades)}")
        
        # Kapalı işlemleri kontrol et
        closed_trades = [t for t in all_trades if t.close_price is not None]
        print(f"Kapalı işlem sayısı: {len(closed_trades)}")
        
        # Açık işlemleri kontrol et
        open_trades = [t for t in all_trades if t.close_price is None]
        print(f"Açık işlem sayısı: {len(open_trades)}")
        
        # Manuel hesaplama - kapalı işlemlerin brüt PnL'i
        manual_closed_gross_pnl = 0.0
        manual_closed_fees = 0.0
        manual_closed_net_pnl = 0.0
        
        print("\n=== KAPALI İŞLEMLER ===")
        for i, trade in enumerate(closed_trades[:10], 1):  # İlk 10 işlemi göster
            pnl = trade.pnl or 0.0
            fees = trade.fees or 0.0
            gross = pnl + fees
            manual_closed_gross_pnl += gross
            manual_closed_fees += fees
            manual_closed_net_pnl += pnl
            
            print(f"{i}. Trade ID: {trade.id} | PnL: ${pnl:.2f} | Fee: ${fees:.2f} | Brüt: ${gross:.2f}")
        
        if len(closed_trades) > 10:
            # Kalan işlemleri de topla
            for trade in closed_trades[10:]:
                pnl = trade.pnl or 0.0
                fees = trade.fees or 0.0
                gross = pnl + fees
                manual_closed_gross_pnl += gross
                manual_closed_fees += fees
                manual_closed_net_pnl += pnl
        
        print(f"\nManuel Toplam (Kapalı):")
        print(f"  Brüt PnL: ${manual_closed_gross_pnl:.2f}")
        print(f"  Net PnL: ${manual_closed_net_pnl:.2f}")
        print(f"  Fees: ${manual_closed_fees:.2f}")
        
        # SQL ile hesaplama - kapalı işlemlerin brüt PnL'i
        sql_closed_gross_pnl = (
            session.query(
                func.coalesce(
                    func.sum(func.coalesce(Trade.pnl, 0.0) + func.coalesce(Trade.fees, 0.0)), 
                    0.0
                )
            )
            .filter(Trade.symbol == symbol)
            .filter(Trade.close_price.isnot(None))
            .scalar_one()
        )
        
        print(f"\nSQL Toplam (Kapalı):")
        print(f"  Brüt PnL: ${float(sql_closed_gross_pnl or 0.0):.2f}")
        
        # Açık işlemlerin fee'leri
        open_trades_fees = (
            session.query(func.coalesce(func.sum(Trade.fees), 0.0))
            .filter(Trade.symbol == symbol)
            .filter(Trade.close_price.is_(None))
            .scalar_one()
        )
        
        print(f"\n=== AÇIK İŞLEMLER ===")
        print(f"Açık işlem sayısı: {len(open_trades)}")
        print(f"Açık işlemlerin fee'leri: ${float(open_trades_fees or 0.0):.2f}")
        
        # Portfolio metrics'ten unrealized PnL al
        metrics = executor.portfolio_metrics()
        unrealized_pnl = metrics.get('unrealized_pnl', 0.0)
        total_pnl = metrics.get('total_pnl', 0.0)
        total_fees = metrics.get('total_fees', 0.0)
        
        print(f"\n=== PORTFÖY METRİKLERİ ===")
        print(f"Unrealized PnL: ${unrealized_pnl:.2f}")
        print(f"Total PnL (net): ${total_pnl:.2f}")
        print(f"Total Fees: ${total_fees:.2f}")
        
        # Brüt PnL hesaplama
        unrealized_gross_pnl = unrealized_pnl + (open_trades_fees or 0.0)
        total_gross_pnl = float(sql_closed_gross_pnl or 0.0) + unrealized_gross_pnl
        
        print(f"\n=== BRÜT PNL HESAPLAMASI ===")
        print(f"Kapalı işlemler brüt PnL: ${float(sql_closed_gross_pnl or 0.0):.2f}")
        print(f"Açık pozisyonlar unrealized brüt PnL: ${unrealized_gross_pnl:.2f}")
        print(f"  (Unrealized net: ${unrealized_pnl:.2f} + Açık fee'ler: ${float(open_trades_fees or 0.0):.2f})")
        print(f"\nTOPLAM BRÜT PNL: ${total_gross_pnl:.2f}")
        
        # Eski yöntemle karşılaştır
        old_gross_pnl = total_pnl + total_fees
        print(f"\n=== KARŞILAŞTIRMA ===")
        print(f"Yeni yöntem (Trade tablosundan): ${total_gross_pnl:.2f}")
        print(f"Eski yöntem (total_pnl + total_fees): ${old_gross_pnl:.2f}")
        print(f"Fark: ${abs(total_gross_pnl - old_gross_pnl):.2f}")
        
        # Doğrulama
        if abs(total_gross_pnl - manual_closed_gross_pnl) < 0.01:
            print("\n✅ KAPALI İŞLEMLER BRÜT PNL DOĞRU!")
        else:
            print(f"\n⚠️ UYARI: Manuel hesaplama ile SQL hesaplama farklı!")
            print(f"  Manuel: ${manual_closed_gross_pnl:.2f}")
            print(f"  SQL: ${float(sql_closed_gross_pnl or 0.0):.2f}")
        
        return {
            'total_gross_pnl': total_gross_pnl,
            'old_gross_pnl': old_gross_pnl,
            'closed_gross_pnl': float(sql_closed_gross_pnl or 0.0),
            'unrealized_gross_pnl': unrealized_gross_pnl,
            'total_fees': total_fees,
            'total_pnl': total_pnl
        }


if __name__ == "__main__":
    try:
        result = test_gross_pnl_calculation()
        print("\n✅ Test tamamlandı!")
    except Exception as e:
        print(f"\n❌ Test başarısız: {e}")
        import traceback
        traceback.print_exc()







