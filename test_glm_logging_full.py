#!/usr/bin/env python3
"""
GLM Payload Logging Test - Gerçek API Çağrısı
6600+ karakterlik prompt ile GLM API testi ve log analizi
"""

import json
import logging
import os
import sys

# Setup logging to see all logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

sys.path.insert(0, '/root/trading')

def create_large_prompt():
    """Create 6600+ character prompt"""
    base_content = """
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
Open Interest: 25000000.00
Long/Short Ratio: 1.2500

================================================================================
INTRADAY SERIES (1-minute):
================================================================================

Mid prices: [109500.00, 109550.00, 109600.00, 109650.00, 109700.00, 109750.00, 109800.00, 109850.00, 109900.00, 109950.00, 110000.00, 110050.00, 110100.00, 110150.00, 110200.00, 110250.00, 110300.00, 110350.00, 110400.00, 110450.00]
RSI indicators: [55.00, 56.00, 57.00, 58.00, 59.00, 60.00, 61.00, 62.00, 63.00, 64.00, 65.00, 66.00, 67.00, 68.00, 69.00, 70.00, 71.00, 72.00, 73.00, 74.00]
MACD indicators: [120.00, 122.00, 124.00, 126.00, 128.00, 130.00, 132.00, 134.00, 136.00, 138.00, 140.00, 142.00, 144.00, 146.00, 148.00, 150.00, 152.00, 154.00, 156.00, 158.00]

================================================================================
LONGER-TERM CONTEXT (4-hour):
================================================================================

20-Period EMA: 108500.00 vs. 50-Period EMA: 108000.00
14-Period ATR: 2500.00
Current Volume: 150000000.00
MACD indicators: [50.00, 60.00, 70.00, 80.00, 90.00, 100.00, 110.00, 120.00, 130.00, 140.00]
RSI indicators: [40.00, 42.00, 44.00, 46.00, 48.00, 50.00, 52.00, 54.00, 56.00, 58.00]

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

Respond with JSON:
{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "hold|close_position|buy|sell",
      "quantity": 0.12,
      "profit_target": 118136.15,
      "stop_loss": 102026.675,
      "leverage": 10,
      "confidence": 0.75,
      "risk_usd": 619.2345,
      "justification": "..."
    }
  }
}

⚠️ CRITICAL: The 'justification' field MUST be in TURKISH and MINIMUM 400 characters!
"""
    
    # Add padding to reach 6600+ chars
    padding = "\n" + "=" * 3000
    return base_content + padding

def test_glm_with_logging():
    """Test GLM API with payload logging"""
    
    print("=" * 80)
    print("GLM PAYLOAD LOGGING TESTİ")
    print("=" * 80)
    print()
    
    try:
        print("📦 Modüller import ediliyor...")
        from app.config.settings import get_settings
        from app.risk_manager.glm_client import GLMClient
        
        settings = get_settings()
        
        # Check if API key is configured
        if not settings.zai.api_key or settings.zai.api_key == "changeme":
            print("⚠️ UYARI: GLM API key yapılandırılmamış!")
            print("   Test için API key gerekli.")
            print(f"   API URL: {settings.zai.base_url}")
            print(f"   Model: {settings.zai.model}")
            return False
        
        print("✅ GLM Client modülü yüklendi")
        print(f"   API URL: {settings.zai.base_url}")
        print(f"   Model: {settings.zai.model}")
        print()
        
        # Create test prompt
        print("📝 Test prompt oluşturuluyor (6600+ karakter)...")
        user_content = create_large_prompt()
        system_content = "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. KRİTİK KURAL: JSON yanıtındaki 'justification' alanı MUTLAKA TÜRKÇE ve EN AZ 400 KARAKTER uzunluğunda olmalıdır. Bu zorunlu bir gerekliliktir. KISA VEYA GENEL AÇIKLAMALAR KABUL EDİLMEZ. Her karar için: (1) TÜM timeframe'leri (1m, 30m, 4h) AYRI AYRI analiz et ve SAYISAL değerler ver, (2) Her timeframe için RSI, EMA20, EMA50, MACD değerlerini TAM SAYILARLA belirt, (3) Fiyat seviyelerini, destek/direnç noktalarını KESIN RAKAMLARLA açıkla, (4) Timeframe'ler arasındaki uyum/çelişkileri DETAYLI açıkla, (5) Neden ŞİMDİ bu kararı verdiğini piyasa bağlamında izah et. HOLD kararları BUY/SELL kadar detaylı gerekçe gerektirir. Genel laflar etme, her cümle spesifik veri içermeli."
        
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ]
        
        system_len = len(system_content)
        user_len = len(user_content)
        total_len = system_len + user_len
        
        print(f"✅ Prompt oluşturuldu:")
        print(f"   System message: {system_len:,} karakter")
        print(f"   User message: {user_len:,} karakter")
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
        print("GLM API ÇAĞRISI BAŞLIYOR:")
        print("=" * 80)
        print("📤 API'ye gönderiliyor...")
        print("   (Aşağıda payload logging detaylarını göreceksiniz)")
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
            
            # Show first 500 chars
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
                justification = trade_args.get("justification", "") or trade_args.get("gerekçe", "") or btc_data.get("justification", "") or btc_data.get("gerekçe", "")
                
                if justification:
                    print(f"✅ Gerekçe bulundu: {len(justification):,} karakter")
                    if len(justification) >= 400:
                        print("✅ Gerekçe uzunluğu yeterli (400+ karakter)")
                    else:
                        print(f"⚠️ UYARI: Gerekçe {len(justification)} karakter, 400'den az!")
                    
                    print()
                    print("📝 Gerekçe (ilk 300 karakter):")
                    print("-" * 80)
                    print(justification[:300])
                    if len(justification) > 300:
                        print("...")
                    print("-" * 80)
                else:
                    print("❌ SORUN: Gerekçe alanı bulunamadı!")
                    print("   Response yapısı:")
                    print(json.dumps(parsed, indent=2, ensure_ascii=False)[:500])
                
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON parse hatası: {e}")
                print("   Response içeriği düz metin olabilir")
            
            # Token usage
            usage = response.get("usage", {})
            if usage:
                print()
                print("📊 Token Kullanımı:")
                print(f"   Prompt tokens: {usage.get('prompt_tokens', 0):,}")
                print(f"   Completion tokens: {usage.get('completion_tokens', 0):,}")
                print(f"   Total tokens: {usage.get('total_tokens', 0):,}")
                
                # Check if completion tokens are reasonable
                comp_tokens = usage.get('completion_tokens', 0)
                if comp_tokens < 100:
                    print("⚠️ UYARI: Completion tokens çok düşük! Model tam yanıt vermemiş olabilir.")
                elif comp_tokens >= 4000:
                    print("⚠️ UYARI: Completion tokens limit yakınında! max_tokens artırılabilir.")
            
            latency = response.get('_glm_latency_ms', 0)
            if latency:
                print(f"   Latency: {latency:.0f}ms")
        
        else:
            print("❌ HATA: Yanıt içeriği bulunamadı!")
            print(f"Response keys: {list(response.keys())}")
            print(f"Full response: {json.dumps(response, indent=2, ensure_ascii=False)[:1000]}")
        
        print()
        print("=" * 80)
        print("✅ TEST TAMAMLANDI!")
        print("=" * 80)
        print()
        print("📋 Kontrol Edilecekler:")
        print("   1. ✅ Payload size logları (KB cinsinden)")
        print("   2. ✅ Her message'ın uzunluğu")
        print("   3. ✅ Token kullanımı")
        print("   4. ✅ Response içeriği ve gerekçe varlığı")
        print()
        
        return True
        
    except ImportError as e:
        print(f"❌ Import hatası: {e}")
        print("   Gerekli modüller yüklü değil.")
        return False
    except Exception as exc:
        print(f"❌ HATA: {exc}")
        import traceback
        print()
        print("Traceback:")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print()
    print("🧪 GLM Payload Logging Test Başlatılıyor...")
    print("   Bu test 6600+ karakterlik prompt ile GLM API çağrısı yapacak")
    print("   ve payload logging özelliklerini doğrulayacak.")
    print()
    
    success = test_glm_with_logging()
    
    sys.exit(0 if success else 1)

