#!/usr/bin/env python3
"""
Test full cycle notification with all sections
"""

from datetime import datetime

from app.executor.executor import ExecutionResult, Executor
from app.risk_manager.manager import RiskDecision


def test_full_cycle_notification():
    print("=== TEST FULL CYCLE NOTIFICATION ===")
    
    executor = Executor()
    
    # Create a mock decision and result
    decision = RiskDecision(
        action="SELL",
        amount=0.1,
        leverage=5.0,
        reasoning="Bu bir test GLM kararidir. Piysada guclu ayi sinyalleri goruluyor,SHORT pozisyon acilmasi uygun gorunuyor. 4h timeframe'de olum haclari ve 30m'deki momentum kaybi dusus trendinin devam edecegini gosteriyor."
    )
    
    result = ExecutionResult(status="PAPER", details="Paper trading kaydedildi")
    
    # Get portfolio metrics
    metrics = executor.portfolio_metrics()
    
    # Get recent trades
    recent_trades = executor.get_recent_trades_with_pnl(limit=5)
    
    # Build notification exactly like in runtime.py
    print("Building notification message...")
    
    position = metrics['position']
    position_type = ""
    if position > 0.0001:
        position_type = "📈 LONG"
    elif position < -0.0001:
        position_type = "📉 SHORT"
    else:
        position_type = "⚪ FLAT"
    
    timestamp_header = f"BTC_ANALYZER, [{datetime.now().strftime('%d.%m.%Y %H:%M')}]"
    
    lines = [
        f"{timestamp_header}",
        "*📊 15 Dakikalık Döngü Tamamlandı*",
        "",
        "*🎯 GLM Kararı*",
        f"Karar: {decision.action}",
        f"Miktar: {decision.amount*100:.1f}% equity",
        f"Kaldıraç: {decision.leverage:.1f}x",
        f"Durum: {result.status}",
        "",
        "*💰 Portföy Durumu*",
        f"Sermaye: ${metrics['equity']:,.2f}",
        f"Pozisyon: {position_type} {abs(position):.4f} BTC",
        f"BTC Fiyat: ${metrics['price']:,.2f}",
        f"PnL: ${metrics['total_pnl']:,.2f} ({(metrics['total_pnl']/metrics['equity']*100):+.2f}%)",
        "",
        f"*📝 Son 5 İşlem* (Toplam: {len(recent_trades)})",
    ]
    
    # Add recent trades with detailed info
    if recent_trades:
        for i, trade in enumerate(recent_trades[:5], 1):
            emoji = "🟢" if trade['pnl'] >= 0 else "🔴"
            status = "🔒 Kapandı" if trade['is_closed'] else "🔓 Açık"
            
            # Format trade line
            trade_line = f"{i}. {emoji} {trade['side']} {trade['amount']:.4f} BTC {status}"
            lines.append(trade_line)
            
            # Add price details
            price_label = "Kapanış" if trade['is_closed'] else "Güncel"
            lines.append(f"   Açılış: ${trade['open_price']:,.2f} → {price_label}: ${trade['close_price']:,.2f}")
            
            # Add PnL
            pnl_sign = "+" if trade['pnl'] >= 0 else ""
            lines.append(f"   PnL: {pnl_sign}${trade['pnl']:,.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%)")
    else:
        lines.append("Henüz işlem yok")
    
    # Add latest trade details section if there was execution
    if hasattr(result, 'status') and result.status == "PAPER":
        lines.extend([
            "",
            "*🚨 Son İşlem Detayları*",
        ])
        
        if recent_trades:
            latest_trade = recent_trades[0]
            side_emoji = "📈" if latest_trade['side'] == "BUY" else "📉"
            notional_value = latest_trade['amount'] * latest_trade['open_price']
            pnl_sign = "+" if latest_trade['pnl'] >= 0 else ""
            
            lines.extend([
                f"{side_emoji} En Son İşlem: {latest_trade['side']} {latest_trade['amount']:.4f} BTC @ ${latest_trade['open_price']:,.2f}",
                f"💰 Notional Değeri: ${notional_value:,.2f}",
                f"{'🟢' if latest_trade['pnl'] >= 0 else '🔴'} Anlık PnL: {pnl_sign}${latest_trade['pnl']:,.2f} ({pnl_sign}{latest_trade['pnl_pct']:.2f}%)",
            ])
            
            if latest_trade['is_closed']:
                lines.extend([
                    f"🔒 Kapanış: ${latest_trade['close_price']:,.2f}",
                    f"✅ Gerçekleşen PnL: {pnl_sign}${latest_trade['pnl']:,.2f} ({pnl_sign}{latest_trade['pnl_pct']:.2f}%)",
                ])
            else:
                lines.append(f"📊 Güncel Fiyat: ${latest_trade['close_price']:,.2f}")
    
    # Add reasoning header
    lines.extend([
        "",
        "*💬 Gerekçe*",
    ])
    
    # Build the full message
    main_message = "\n".join(lines)
    
    print("\n" + "="*60)
    print("FULL CYCLE NOTIFICATION MESSAGE:")
    print("="*60)
    print(main_message)
    print()
    print("💬 Gerekçe:")
    print(decision.reasoning)
    print("="*60)
    
    # Check message length for Telegram limit
    full_message_with_reason = main_message + "\n\n💬 Gerekçe:\n" + decision.reasoning
    print(f"\nMessage stats:")
    print(f"Main message length: {len(main_message)} chars")
    print(f"With reasoning: {len(full_message_with_reason)} chars")
    print(f"Telegram limit: 4096 chars")
    print(f"Status: {'✅ OK' if len(full_message_with_reason) <= 4096 else '⚠️ EXCEEDS LIMIT'}")

if __name__ == "__main__":
    test_full_cycle_notification()
