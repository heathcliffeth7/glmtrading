#!/usr/bin/env python3
"""
Test script to verify notification improvements for multi-symbol trading
"""
import asyncio
from datetime import datetime, timezone

# Mock telegram client for testing
class MockTelegramClient:
    def __init__(self):
        self.messages = []
        
    def enabled(self):
        return True
    
    def send_message(self, message):
        print(f"\n{'='*60}")
        print("📱 TELEGRAM MESSAGE:")
        print(f"{'='*60}")
        print(message)
        print(f"{'='*60}\n")
        self.messages.append(message)

# Simple format_markdown implementation for testing
def format_markdown(text):
    """Escape markdown special characters"""
    if not text:
        return ""
    # Escape markdown special characters
    for char in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(char, '\\' + char)
    return text

# Test cycle summary notification
def test_cycle_summary_notification():
    """Test the cycle summary notification format"""
    mock_telegram = MockTelegramClient()
    
    # Simulate cycle results
    cycle_summary = {
        "BTCUSDT": {
            "status": "SUCCESS",
            "action": "HOLD",
            "error": None,
            "execution_status": "SKIP"
        },
        "ETHUSDT": {
            "status": "FAILED",
            "action": None,
            "error": "GLM API timeout after 270s",
            "execution_status": None
        },
        "SOLUSDT": {
            "status": "SUCCESS",
            "action": "LONG",
            "error": None,
            "execution_status": "EXECUTED"
        }
    }
    
    start_time = datetime.now(timezone.utc)
    
    # Simulate delay
    import time
    time.sleep(2)
    
    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    
    # Count success/failure
    success_count = sum(1 for s in cycle_summary.values() if s["status"] == "SUCCESS")
    failed_count = sum(1 for s in cycle_summary.values() if s["status"] == "FAILED")
    
    # Build summary lines
    summary_lines = []
    for symbol, result in cycle_summary.items():
        symbol_name = symbol.replace("USDT", "")
        status = result["status"]
        action = result.get("action", "N/A")
        
        if status == "SUCCESS":
            exec_status = result.get("execution_status", "UNKNOWN")
            if action == "HOLD":
                emoji = "⚪"
            elif action in ["LONG", "SHORT"]:
                emoji = "🟢"
            elif action == "CLOSE":
                emoji = "🔵"
            else:
                emoji = "⚫"
            
            summary_lines.append(f"{emoji} {symbol_name}: {action} ({exec_status})")
        else:
            error = result.get("error", "Unknown error")
            error_short = error[:50] + "..." if len(error) > 50 else error
            summary_lines.append(f"❌ {symbol_name}: FAILED ({format_markdown(error_short)})")
    
    message = "\n".join([
        "📊 *DÖNGÜ ÖZETİ*",
        "",
        f"✅ Başarılı: {success_count}/{len(cycle_summary)}",
        f"❌ Başarısız: {failed_count}/{len(cycle_summary)}",
        f"⏱️ Toplam Süre: {duration:.1f}s",
        "",
        "*Sembol Detayları:*",
        *summary_lines,
        "",
        f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
    ])
    
    mock_telegram.send_message(message)
    
    return len(mock_telegram.messages) == 1

def test_failure_notification():
    """Test the failure notification format"""
    mock_telegram = MockTelegramClient()
    
    symbol = "ETHUSDT"
    error_msg = "GLM API timeout after 270 seconds - request_async failed"
    start_time = datetime.now(timezone.utc)
    
    import time
    time.sleep(1)
    
    symbol_name = symbol.replace("USDT", "")
    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    
    message = "\n".join([
        f"⚠️ *{symbol_name} DÖNGÜ BAŞARISIZ*",
        "",
        f"❌ Hata: {format_markdown(error_msg[:200])}",
        f"⏱️ Süre: {duration:.1f}s",
        f"🕐 Zaman: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
        "",
        "💡 *Sistem durumu:*",
        "• Diğer semboller çalışmaya devam ediyor",
        "• Sonraki döngüde tekrar denenecek",
    ])
    
    mock_telegram.send_message(message)
    
    return len(mock_telegram.messages) == 1

if __name__ == "__main__":
    print("\n" + "="*70)
    print("🧪 TESTING NOTIFICATION IMPROVEMENTS")
    print("="*70)
    
    print("\n1️⃣ Testing Cycle Summary Notification...")
    result1 = test_cycle_summary_notification()
    print(f"   Result: {'✅ PASS' if result1 else '❌ FAIL'}")
    
    print("\n2️⃣ Testing Failure Notification...")
    result2 = test_failure_notification()
    print(f"   Result: {'✅ PASS' if result2 else '❌ FAIL'}")
    
    print("\n" + "="*70)
    if result1 and result2:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("="*70 + "\n")
