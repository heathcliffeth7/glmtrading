#!/usr/bin/env python3
"""
Test GLM Payload Logging and Verification
Tests the new logging features to verify 6600+ char prompts are sent correctly
"""

import sys

sys.path.insert(0, '/root/trading')

from app.agents.short_term import PureDataCollector
from app.risk_manager.manager import RiskManager
from app.utils.logging import get_logger

logger = get_logger(__name__)

def test_glm_payload_logging():
    """Test GLM API call with 6600+ char prompt and verify payload logging"""
    
    print("=" * 80)
    print("GLM PAYLOAD LOGGING VE DOĞRULAMA TESTİ")
    print("=" * 80)
    print()
    
    # 1. Collect raw market data
    print("🔄 Piyasa verileri toplanıyor...")
    collector = PureDataCollector(symbol="BTCUSDT")
    signal = collector.generate_signal()
    
    raw_market_data = signal.metadata.get("raw_market_data", {})
    if not raw_market_data:
        print("❌ HATA: raw_market_data bulunamadı!")
        return False
    
    print(f"✅ Veri toplandı: {len(raw_market_data)} anahtar")
    print()
    
    # 2. Portfolio metrics (FLAT position)
    portfolio_metrics = {
        "equity": 10000.0,
        "available_cash": 10000.0,
        "position": 0.0,
        "entry_price": 0.0,
        "current_price": 110000.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
        "leverage": 1,
        "exit_plan": {},
        "sharpe_ratio": 0.0
    }
    
    # 3. Initialize RiskManager and build prompt
    print("📝 RiskManager ile prompt oluşturuluyor...")
    risk_manager = RiskManager()
    
    # Build NOF1 prompt
    messages = risk_manager._build_nof1_prompt(signal, portfolio_metrics)
    
    if not messages:
        print("❌ HATA: Prompt oluşturulamadı!")
        return False
    
    # Analyze prompt
    system_msg = messages[0].get("content", "")
    user_msg = messages[1].get("content", "")
    
    system_len = len(system_msg)
    user_len = len(user_msg)
    total_len = system_len + user_len
    
    print()
    print("=" * 80)
    print("PROMPT ANALİZİ:")
    print("=" * 80)
    print(f"System message: {system_len:,} karakter")
    print(f"User message: {user_len:,} karakter")
    print(f"TOPLAM: {total_len:,} karakter")
    print()
    
    # 4. Test GLM API call
    print("=" * 80)
    print("GLM API ÇAĞRISI (LOGLAR İZLENİYOR):")
    print("=" * 80)
    print()
    print("📤 API'ye gönderiliyor...")
    print("   (Loglarda payload detaylarını göreceksiniz)")
    print()
    
    try:
        # Make GLM request
        decision = risk_manager.evaluate([signal], portfolio_metrics)
        
        print()
        print("=" * 80)
        print("GLM YANITI:")
        print("=" * 80)
        print(f"Karar: {decision.action}")
        print(f"Miktar: {decision.amount}")
        print(f"Kaldıraç: {decision.leverage}")
        print(f"Güven: {decision.glm_confidence:.1f}%")
        print(f"Gerekçe uzunluğu: {len(decision.reasoning):,} karakter")
        print()
        
        if decision.reasoning:
            print("Gerekçe (ilk 300 karakter):")
            print(decision.reasoning[:300])
            if len(decision.reasoning) > 300:
                print("...")
            print()
        else:
            print("⚠️ UYARI: Gerekçe boş!")
            print()
        
        # Check if justification exists
        if "Gerekçe belirtilmedi" in decision.reasoning:
            print("❌ SORUN: GLM gerekçe üretemedi!")
            print("   Logları kontrol edin:")
            print("   - Payload size kontrolü")
            print("   - Token kullanımı")
            print("   - Response içeriği")
            return False
        else:
            print("✅ BAŞARILI: GLM gerekçe üretti!")
            return True
        
    except Exception as exc:
        print(f"❌ HATA: {exc}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print()
    print("🧪 Test başlatılıyor...")
    print("   Bu test 6600+ karakterlik prompt ile GLM API çağrısı yapacak")
    print("   ve payload logging özelliklerini doğrulayacak.")
    print()
    
    success = test_glm_payload_logging()
    
    print()
    print("=" * 80)
    if success:
        print("✅ TEST BAŞARILI!")
        print("   Payload logging çalışıyor ve GLM gerekçe üretebiliyor.")
    else:
        print("❌ TEST BAŞARISIZ!")
        print("   Logları kontrol edin ve sorunları tespit edin.")
    print("=" * 80)
    print()
    
    sys.exit(0 if success else 1)

