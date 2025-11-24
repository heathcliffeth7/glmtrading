#!/usr/bin/env python3
"""
GLM Prompt Tam Gönderim Testi
6600 karakterlik prompt'un tam olarak gönderilip gönderilmediğini ve 
GLM yanıtının kısalıp kısalmadığını test eder
"""

import json
import logging
import os
import sys

sys.path.insert(0, '/root/trading')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

def create_exact_6600_char_prompt():
    """Create exactly 6600 character prompt with markers"""
    
    # Header with marker
    header = "=" * 80 + "\n"
    header += "TEST PROMPT - 6600 CHARACTERS EXACT\n"
    header += "=" * 80 + "\n\n"
    
    # Market data section
    market_data = """
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
Open Interest: 25000000.00
Long/Short Ratio: 1.2500

Funding Rate history (10 periods): [0.00010000, 0.00011000, 0.00012000, 0.00012500, 0.00013000, 0.00012500, 0.00012000, 0.00012500, 0.00013000, 0.00012500]
Open Interest history (10 periods): [24000000.00, 24200000.00, 24400000.00, 24600000.00, 24800000.00, 25000000.00, 25200000.00, 25000000.00, 24800000.00, 25000000.00]
Long/Short Ratio history (10 periods): [1.2000, 1.2200, 1.2400, 1.2500, 1.2600, 1.2500, 1.2400, 1.2500, 1.2600, 1.2500]

================================================================================
INTRADAY SERIES (1-minute):
================================================================================

Mid prices: [109500.00, 109550.00, 109600.00, 109650.00, 109700.00, 109750.00, 109800.00, 109850.00, 109900.00, 109950.00, 110000.00, 110050.00, 110100.00, 110150.00, 110200.00, 110250.00, 110300.00, 110350.00, 110400.00, 110450.00]
RSI indicators (14-Period): [55.00, 56.00, 57.00, 58.00, 59.00, 60.00, 61.00, 62.00, 63.00, 64.00, 65.00, 66.00, 67.00, 68.00, 69.00, 70.00, 71.00, 72.00, 73.00, 74.00]
MACD indicators: [120.00, 122.00, 124.00, 126.00, 128.00, 130.00, 132.00, 134.00, 136.00, 138.00, 140.00, 142.00, 144.00, 146.00, 148.00, 150.00, 152.00, 154.00, 156.00, 158.00]

================================================================================
LONGER-TERM CONTEXT (4-hour):
================================================================================

20-Period EMA: 108500.00 vs. 50-Period EMA: 108000.00
14-Period ATR: 2500.00
Current Volume: 150000000.00
MACD indicators: [50.00, 60.00, 70.00, 80.00, 90.00, 100.00, 110.00, 120.00, 130.00, 140.00]
RSI indicators (14-Period): [40.00, 42.00, 44.00, 46.00, 48.00, 50.00, 52.00, 54.00, 56.00, 58.00]

================================================================================
ACCOUNT INFORMATION:
================================================================================

Current Total Return: 0.00%
Available Cash: 10000.00
Current Account Value: 10000.00
Current live positions: NONE (FLAT)

================================================================================
YOUR TASK
================================================================================

Analyze the market data and decide: HOLD, BUY, SELL, or CLOSE.

Respond with JSON format with 'gerekçe' field in Turkish (MINIMUM 800-1000 characters).

"""
    
    # Combine and calculate needed padding
    base = header + market_data
    current_len = len(base)
    
    # Add padding to reach exactly 6600 characters
    # Add marker at end to verify it was sent
    target_len = 6600
    padding_needed = target_len - current_len - 50  # Reserve 50 chars for end marker
    
    padding = "=" * padding_needed + "\n"
    end_marker = "\n[END_MARKER_6600_CHARS]\n"
    
    full_prompt = base + padding + end_marker
    
    # Verify exact length
    actual_len = len(full_prompt)
    if actual_len != target_len:
        # Adjust if needed
        diff = target_len - actual_len
        if diff > 0:
            full_prompt += "=" * diff
        else:
            full_prompt = full_prompt[:target_len]
    
    return full_prompt

def test_glm_full_prompt():
    """Test GLM with exact 6600 char prompt"""
    
    print("=" * 80)
    print("GLM PROMPT TAM GÖNDERİM TESTİ")
    print("=" * 80)
    print()
    
    try:
        from app.config.settings import get_settings
        from app.risk_manager.glm_client import GLMClient
        
        settings = get_settings()
        
        if not settings.zai.api_key or settings.zai.api_key == "changeme":
            print("⚠️ UYARI: GLM API key yapılandırılmamış!")
            return False
        
        print("✅ GLM Client modülü yüklendi")
        print()
        
        # Create exact 6600 char prompt
        print("📝 6600 karakterlik test prompt oluşturuluyor...")
        user_content = create_exact_6600_char_prompt()
        actual_len = len(user_content)
        
        print(f"✅ Prompt oluşturuldu: {actual_len:,} karakter")
        print(f"   Başlangıç: {user_content[:100]}...")
        print(f"   Son 100 karakter: ...{user_content[-100:]}")
        print()
        
        # Check for markers
        if "[END_MARKER_6600_CHARS]" in user_content:
            print("✅ END marker bulundu - prompt tamam")
        else:
            print("⚠️ END marker bulunamadı!")
        print()
        
        system_content = "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. KRİTİK KURAL: JSON yanıtındaki 'gerekçe' alanı MUTLAKA TÜRKÇE ve EN AZ 800-1000 KARAKTER uzunluğunda olmalıdır. Bu zorunlu bir gerekliliktir. KISA VEYA GENEL AÇIKLAMALAR KESİNLİKLE KABUL EDİLMEZ. Gerekçe çok detaylı ve kapsamlı olmalı - en az 800 karakter yazmalısın."
        
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ]
        
        system_len = len(system_content)
        user_len = len(user_content)
        total_len = system_len + user_len
        
        print("=" * 80)
        print("PROMPT ANALİZİ:")
        print("=" * 80)
        print(f"System message: {system_len:,} karakter")
        print(f"User message: {user_len:,} karakter")
        print(f"TOPLAM: {total_len:,} karakter")
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
        print("   (Payload logging loglarını izleyin)")
        print()
        
        response = client.request(messages)
        
        print()
        print("=" * 80)
        print("API YANITI ANALİZİ:")
        print("=" * 80)
        
        # Extract response content
        if "choices" in response and len(response["choices"]) > 0:
            content = response["choices"][0].get("message", {}).get("content", "")
            print(f"✅ Yanıt alındı: {len(content):,} karakter")
            print()
            
            # Token usage
            usage = response.get("usage", {})
            if usage:
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
                
                print("📊 Token Kullanımı:")
                print(f"   Prompt tokens: {prompt_tokens:,}")
                print(f"   Completion tokens: {completion_tokens:,}")
                print(f"   Total tokens: {total_tokens:,}")
                print()
                
                # Calculate expected vs actual
                expected_prompt_tokens = total_len // 4
                print(f"📊 Token Analizi:")
                print(f"   Beklenen prompt tokens: ~{expected_prompt_tokens:,}")
                print(f"   Gerçek prompt tokens: {prompt_tokens:,}")
                if abs(prompt_tokens - expected_prompt_tokens) > 200:
                    print(f"   ⚠️ FARK VAR! Prompt tam gönderilmedi olabilir!")
                else:
                    print(f"   ✅ Token sayısı beklenenle uyumlu")
                print()
            
            # Show response content
            print("📄 Yanıt içeriği (ilk 500 karakter):")
            print("-" * 80)
            print(content[:500])
            if len(content) > 500:
                print("...")
            print("-" * 80)
            print()
            
            # Try to parse JSON
            try:
                cleaned = content.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                
                parsed = json.loads(cleaned)
                print("✅ JSON parse başarılı!")
                
                # Check for justification
                btc_data = parsed.get("BTCUSDT", {})
                trade_args = btc_data.get("trade_signal_args", {})
                justification = (
                    trade_args.get("justification", "") or 
                    trade_args.get("gerekçe", "") or 
                    btc_data.get("justification", "") or 
                    btc_data.get("gerekçe", "")
                )
                
                if justification:
                    justification_len = len(justification)
                    print(f"✅ Gerekçe bulundu: {justification_len:,} karakter")
                    print()
                    
                    if justification_len >= 800:
                        print("✅ Gerekçe uzunluğu yeterli (800+ karakter)")
                    elif justification_len >= 400:
                        print(f"⚠️ Gerekçe {justification_len} karakter - 800'den az ama 400'den fazla")
                    else:
                        print(f"❌ SORUN: Gerekçe sadece {justification_len} karakter - ÇOK KISA!")
                    
                    print()
                    print("📝 Gerekçe (ilk 300 karakter):")
                    print("-" * 80)
                    print(justification[:300])
                    if len(justification) > 300:
                        print("...")
                    print("-" * 80)
                else:
                    print("❌ SORUN: Gerekçe alanı bulunamadı!")
                    
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON parse hatası: {e}")
                print("   Response içeriği:")
                print(content[:1000])
            
            # Check response length
            print()
            print("=" * 80)
            print("SONUÇ:")
            print("=" * 80)
            print(f"Prompt uzunluğu: {total_len:,} karakter")
            print(f"Yanıt uzunluğu: {len(content):,} karakter")
            print(f"Completion tokens: {completion_tokens:,}")
            
            if completion_tokens < 1000:
                print("⚠️ UYARI: Completion tokens çok düşük!")
                print("   Model tam yanıt vermemiş olabilir.")
            elif completion_tokens >= 7000:
                print("⚠️ UYARI: Completion tokens limit yakınında!")
            else:
                print("✅ Completion tokens normal görünüyor")
        
        return True
        
    except Exception as exc:
        print(f"❌ HATA: {exc}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print()
    print("🧪 GLM Prompt Tam Gönderim Testi Başlatılıyor...")
    print("   Bu test 6600 karakterlik prompt'un tam gönderilip gönderilmediğini")
    print("   ve GLM yanıtının kısalıp kısalmadığını kontrol edecek.")
    print()
    
    success = test_glm_full_prompt()
    
    sys.exit(0 if success else 1)

