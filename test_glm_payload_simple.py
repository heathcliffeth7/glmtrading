#!/usr/bin/env python3
"""
Test GLM Payload Logging - Basit Test
Tests payload logging without requiring full data collection
"""

import os
import sys

sys.path.insert(0, '/root/trading')

# Mock data to create a 6600+ char prompt
def create_test_prompt():
    """Create a test prompt with 6600+ characters"""
    
    # Market data section
    market_data = """
================================================================================
CURRENT MARKET STATE FOR BTCUSDT
================================================================================

PRIMARY TIMEFRAME (30-minute)
current_price = 110000.00
current_ema20 = 109500.00
current_ema50 = 109000.00
current_macd = 125.50
current_rsi (14-period) = 58.25

30-minute series (oldest → latest):

Mid prices: [108500.00, 108750.00, 109000.00, 109250.00, 109500.00, 109750.00, 110000.00, 110250.00, 110500.00, 110750.00]
EMA indicators (20-period): [108450.00, 108700.00, 108950.00, 109200.00, 109450.00, 109700.00, 109950.00, 110200.00, 110450.00, 110700.00]
MACD indicators: [100.00, 105.00, 110.00, 115.00, 120.00, 125.00, 125.50, 130.00, 135.00, 140.00]
RSI indicators (14-Period): [45.00, 47.50, 50.00, 52.50, 55.00, 57.50, 58.25, 60.00, 62.50, 65.00]

================================================================================
FUTURES MARKET DATA:
================================================================================

Funding Rate: 0.00012500
  (8-hour average: 0.00012000)

Open Interest: 25000000.00
  (20-period average: 24500000.00)

Long/Short Ratio: 1.2500
  (20-period average: 1.2300)

Funding Rate history (10 periods): [0.00010000, 0.00011000, 0.00012000, 0.00012500, 0.00013000, 0.00012500, 0.00012000, 0.00012500, 0.00013000, 0.00012500]
Open Interest history (10 periods): [24000000.00, 24200000.00, 24400000.00, 24600000.00, 24800000.00, 25000000.00, 25200000.00, 25000000.00, 24800000.00, 25000000.00]
Long/Short Ratio history (10 periods): [1.2000, 1.2200, 1.2400, 1.2500, 1.2600, 1.2500, 1.2400, 1.2500, 1.2600, 1.2500]

================================================================================
INTRADAY SERIES (1-minute, oldest → latest):
================================================================================

Mid prices: [109500.00, 109550.00, 109600.00, 109650.00, 109700.00, 109750.00, 109800.00, 109850.00, 109900.00, 109950.00, 110000.00, 110050.00, 110100.00, 110150.00, 110200.00, 110250.00, 110300.00, 110350.00, 110400.00, 110450.00]
RSI indicators (14-Period): [55.00, 56.00, 57.00, 58.00, 59.00, 60.00, 61.00, 62.00, 63.00, 64.00, 65.00, 66.00, 67.00, 68.00, 69.00, 70.00, 71.00, 72.00, 73.00, 74.00]
MACD indicators: [120.00, 122.00, 124.00, 126.00, 128.00, 130.00, 132.00, 134.00, 136.00, 138.00, 140.00, 142.00, 144.00, 146.00, 148.00, 150.00, 152.00, 154.00, 156.00, 158.00]

================================================================================
LONGER-TERM CONTEXT (4-hour timeframe):
================================================================================

20-Period EMA: 108500.00 vs. 50-Period EMA: 108000.00
14-Period ATR: 2500.00
Current Volume: 150000000.00
MACD indicators: [50.00, 60.00, 70.00, 80.00, 90.00, 100.00, 110.00, 120.00, 130.00, 140.00]
RSI indicators (14-Period): [40.00, 42.00, 44.00, 46.00, 48.00, 50.00, 52.00, 54.00, 56.00, 58.00]

================================================================================
ADDITIONAL TIMEFRAMES AVAILABLE:
================================================================================

 5m: Price=110000.00, RSI=58.00, MACD=125.00
 15m: Price=110000.00, RSI=58.50, MACD=125.25
 1h: Price=110000.00, RSI=58.75, MACD=125.40
 1d: Price=110000.00, RSI=59.00, MACD=125.60

================================================================================
HERE IS YOUR ACCOUNT INFORMATION & PERFORMANCE
================================================================================

Current Total Return (percent): 0.00%
Available Cash: 10000.00
Current Account Value: 10000.00

Current live positions: NONE (FLAT)

================================================================================
YOUR TASK
================================================================================

Analyze the market data and your current position. Decide on ONE of these actions:

1. **HOLD** - Keep current position (if you have one) or stay flat
2. **BUY** - Enter a new LONG position (only if FLAT)
3. **SELL** - Enter a new SHORT position (only if FLAT)
4. **CLOSE** - Close your current position

🎯 COMPLETE TRADING FREEDOM MODE:

You have COMPLETE FREEDOM to trade as you see fit! The only hard limits are:
- ⚠️ Max 3000 USD per single trade (unleveraged notional value)
- ⚠️ Max 20x leverage (minimum 1x)

EVERYTHING ELSE IS UP TO YOU:
- ✅ You can open multiple positions
- ✅ You can add to existing positions
- ✅ You can reverse positions (close and open opposite)
- ✅ You can trade as frequently as you want
- ✅ You decide position sizing, timing, and strategy
- ✅ No confidence restrictions, no bias filters

Your only job: MAXIMIZE PROFIT while respecting the safety limits above.

OUTPUT FORMAT:

⚠️ CRITICAL: You MUST respond with valid JSON only - no explanations before or after!
⚠️ CRITICAL: The 'gerekçe' field MUST be written in TURKISH language only!

Respond with a JSON object in this exact format:

```json
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "<BUY|SELL|HOLD|CLOSE>",
      "quantity": <float>,
      "profit_target": <float>,
      "stop_loss": <float>,
      "invalidation_condition": "<string>",
      "leverage": <int 1-20>,
      "confidence": <0.0-1.0>,
      "risk_usd": <float>
    },
    "gerekçe": "<your reasoning here - MUST be in TURKISH>"
  }
}
```

IMPORTANT:
- For HOLD: Provide gerekçe explaining why you're holding the current position
- For BUY/SELL: Provide full entry plan with gerekçe
- For CLOSE: Explain why you're closing the position
- Confidence should reflect your conviction (0.5-1.0 range)
- Risk should be proportional to confidence and account size
- ⚠️ CRITICAL: Gerekçe MUST be written in TURKISH language - no English allowed

Think step by step and make your decision based on:
1. Current market state across all timeframes
2. Technical indicators alignment
3. Your existing position (if any) and exit plan
4. Risk/reward ratio
5. Market structure and momentum
"""
    
    # Pad to reach 6600+ characters
    padding = "=" * 3000  # Add padding to reach 6600+ chars
    return market_data + padding

def test_glm_payload():
    """Test GLM payload logging with 6600+ char prompt"""
    
    print("=" * 80)
    print("GLM PAYLOAD LOGGING TESTİ")
    print("=" * 80)
    print()
    
    try:
        from app.risk_manager.glm_client import GLMClient

        # Create test prompt
        print("📝 Test prompt oluşturuluyor (6600+ karakter)...")
        user_content = create_test_prompt()
        system_content = "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. KRİTİK KURAL: JSON yanıtındaki 'gerekçe' alanı MUTLAKA TÜRKÇE ve EN AZ 400 KARAKTER uzunluğunda olmalıdır."
        
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ]
        
        system_len = len(system_content)
        user_len = len(user_content)
        total_len = system_len + user_len
        
        print(f"✅ Prompt oluşturuldu:")
        print(f"   System: {system_len:,} karakter")
        print(f"   User: {user_len:,} karakter")
        print(f"   TOPLAM: {total_len:,} karakter")
        print()
        
        if total_len < 6000:
            print(f"⚠️ UYARI: Prompt {total_len} karakter, 6600'den az!")
        else:
            print(f"✅ Prompt uzunluğu yeterli ({total_len:,} karakter)")
        print()
        
        # Initialize GLM client
        print("🔌 GLM Client başlatılıyor...")
        client = GLMClient()
        print("✅ GLM Client hazır")
        print()
        
        # Make API call
        print("=" * 80)
        print("GLM API ÇAĞRISI:")
        print("=" * 80)
        print("📤 API'ye gönderiliyor...")
        print("   (Loglarda payload logging detaylarını göreceksiniz)")
        print()
        
        response = client.request(messages)
        
        print()
        print("=" * 80)
        print("API YANITI:")
        print("=" * 80)
        
        # Extract response content
        if "choices" in response and len(response["choices"]) > 0:
            content = response["choices"][0].get("message", {}).get("content", "")
            print(f"✅ Yanıt alındı: {len(content):,} karakter")
            print()
            
            # Show first 500 chars
            print("Yanıt (ilk 500 karakter):")
            print(content[:500])
            if len(content) > 500:
                print("...")
            print()
            
            # Check for justification
            if "gerekçe" in content.lower() or "justification" in content.lower():
                print("✅ Gerekçe alanı bulundu!")
            else:
                print("⚠️ UYARI: Gerekçe alanı bulunamadı!")
            
            # Token usage
            usage = response.get("usage", {})
            if usage:
                print()
                print("Token Kullanımı:")
                print(f"  Prompt tokens: {usage.get('prompt_tokens', 0):,}")
                print(f"  Completion tokens: {usage.get('completion_tokens', 0):,}")
                print(f"  Total tokens: {usage.get('total_tokens', 0):,}")
            
        else:
            print("❌ HATA: Yanıt içeriği bulunamadı!")
            print(f"Response keys: {list(response.keys())}")
        
        print()
        print("=" * 80)
        print("✅ TEST TAMAMLANDI!")
        print("=" * 80)
        print()
        print("📋 Kontrol Edilecekler:")
        print("   1. Payload size logları (KB cinsinden)")
        print("   2. Her message'ın uzunluğu")
        print("   3. Token kullanımı")
        print("   4. Response içeriği ve gerekçe varlığı")
        print()
        
        return True
        
    except Exception as exc:
        print(f"❌ HATA: {exc}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print()
    print("🧪 GLM Payload Logging Test Başlatılıyor...")
    print()
    
    success = test_glm_payload()
    
    sys.exit(0 if success else 1)

