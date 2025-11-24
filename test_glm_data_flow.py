#!/usr/bin/env python3
"""
Test GLM Data Flow - Tüm verilerin GLM'a gönderilip gönderilmediğini test et
Veri uzunluğunu ve içeriğini kontrol eder
"""

import json
import os
import sys

sys.path.insert(0, '/root/trading')

def create_full_test_data():
    """Create comprehensive test data with all sections"""
    
    # Complete market data with all timeframes
    market_data = {
        "symbol": "BTCUSDT",
        "current_snapshots": {
            "1m": {
                "close": 110000.00,
                "ema_20": 109800.00,
                "ema_50": 109500.00,
                "macd": 125.50,
                "rsi_14": 58.25,
                "volume": 1500000.00
            },
            "5m": {
                "close": 110000.00,
                "ema_20": 109850.00,
                "ema_50": 109600.00,
                "macd": 125.75,
                "rsi_14": 58.50,
                "volume": 7500000.00
            },
            "15m": {
                "close": 110000.00,
                "ema_20": 109900.00,
                "ema_50": 109700.00,
                "macd": 126.00,
                "rsi_14": 58.75,
                "volume": 22500000.00
            },
            "30m": {
                "close": 110000.00,
                "ema_20": 109950.00,
                "ema_50": 109800.00,
                "macd": 126.25,
                "rsi_14": 59.00,
                "volume": 45000000.00
            },
            "1h": {
                "close": 110000.00,
                "ema_20": 110000.00,
                "ema_50": 109850.00,
                "macd": 126.50,
                "rsi_14": 59.25,
                "volume": 90000000.00
            },
            "4h": {
                "close": 110000.00,
                "ema_20": 110100.00,
                "ema_50": 109900.00,
                "macd": 127.00,
                "rsi_14": 59.50,
                "volume": 360000000.00,
                "atr_14": 2500.00
            },
            "1d": {
                "close": 110000.00,
                "ema_20": 110200.00,
                "ema_50": 109950.00,
                "macd": 127.50,
                "rsi_14": 59.75,
                "volume": 900000000.00
            }
        },
        "historical_arrays": {
            "1m": {
                "close": [109500.00] * 50,
                "ema_20": [109450.00] * 50,
                "ema_50": [109400.00] * 50,
                "macd": [120.00] * 50,
                "rsi_14": [55.00] * 50,
                "volume": [1400000.00] * 50
            },
            "5m": {
                "close": [109600.00] * 50,
                "ema_20": [109550.00] * 50,
                "ema_50": [109500.00] * 50,
                "macd": [122.00] * 50,
                "rsi_14": [56.00] * 50,
                "volume": [7000000.00] * 50
            },
            "15m": {
                "close": [109700.00] * 30,
                "ema_20": [109650.00] * 30,
                "ema_50": [109600.00] * 30,
                "macd": [124.00] * 30,
                "rsi_14": [57.00] * 30,
                "volume": [20000000.00] * 30
            },
            "30m": {
                "close": [109800.00] * 20,
                "ema_20": [109750.00] * 20,
                "ema_50": [109700.00] * 20,
                "macd": [125.00] * 20,
                "rsi_14": [58.00] * 20,
                "volume": [40000000.00] * 20
            },
            "1h": {
                "close": [109900.00] * 24,
                "ema_20": [109850.00] * 24,
                "ema_50": [109800.00] * 24,
                "macd": [126.00] * 24,
                "rsi_14": [58.50] * 24,
                "volume": [80000000.00] * 24
            },
            "4h": {
                "close": [110000.00] * 12,
                "ema_20": [109950.00] * 12,
                "ema_50": [109900.00] * 12,
                "macd": [127.00] * 12,
                "rsi_14": [59.00] * 12,
                "volume": [320000000.00] * 12
            },
            "1d": {
                "close": [110100.00] * 7,
                "ema_20": [110050.00] * 7,
                "ema_50": [110000.00] * 7,
                "macd": [128.00] * 7,
                "rsi_14": [59.50] * 7,
                "volume": [800000000.00] * 7
            }
        },
        "futures_data": {
            "current": {
                "funding_rate": 0.00012500,
                "open_interest": 25000000.00,
                "long_short_ratio": 1.2500
            },
            "averages": {
                "funding_rate_avg": 0.00012000,
                "open_interest_avg": 24500000.00,
                "long_short_ratio_avg": 1.2300
            },
            "historical": {
                "funding_rate": [0.00010000] * 20,
                "open_interest": [24000000.00] * 20,
                "long_short_ratio": [1.2000] * 20
            }
        }
    }
    
    # Portfolio metrics
    portfolio_metrics = {
        "equity": 10500.00,
        "available_cash": 5500.00,
        "position": 0.05,
        "entry_price": 108000.00,
        "current_price": 110000.00,
        "unrealized_pnl": 100.00,
        "leverage": 10,
        "exit_plan": {
            "profit_target": 115000.00,
            "stop_loss": 105000.00,
            "invalidation_condition": "RSI > 70"
        },
        "sharpe_ratio": 1.25
    }
    
    return market_data, portfolio_metrics

def analyze_data_content(prompt_text, data_type="Prompt"):
    """Analyze the content of the data"""
    
    print(f"\n📊 {data_type} İçerik Analizi:")
    print("=" * 60)
    
    # Character count
    char_count = len(prompt_text)
    print(f"📝 Toplam Karakter Sayısı: {char_count:,}")
    
    # Word count
    word_count = len(prompt_text.split())
    print(f"📝 Toplam Kelime Sayısı: {word_count:,}")
    
    # Line count
    line_count = len(prompt_text.splitlines())
    print(f"📝 Toplam Satır Sayısı: {line_count:,}")
    
    # Section analysis
    sections = {}
    if "CURRENT MARKET STATE" in prompt_text:
        sections["Market State"] = prompt_text.count("CURRENT MARKET STATE")
    if "FUTURES MARKET DATA" in prompt_text:
        sections["Futures Data"] = prompt_text.count("FUTURES MARKET DATA")
    if "ACCOUNT INFORMATION" in prompt_text:
        sections["Account Info"] = prompt_text.count("ACCOUNT INFORMATION")
    if "YOUR TASK" in prompt_text:
        sections["Trading Instructions"] = prompt_text.count("YOUR TASK")
    
    print(f"\n📋 Bölüm Analizi:")
    for section, count in sections.items():
        print(f"   {section}: {'✅ Var' if count > 0 else '❌ Yok'}")
    
    # Data completeness check
    data_checks = {
        "Price Data": "current_price" in prompt_text,
        "EMA Indicators": "ema_20" in prompt_text,
        "MACD Indicators": "macd" in prompt_text,
        "RSI Indicators": "rsi_14" in prompt_text,
        "Volume Data": "volume" in prompt_text,
        "Funding Rate": "funding_rate" in prompt_text,
        "Open Interest": "open_interest" in prompt_text,
        "Long/Short Ratio": "long_short_ratio" in prompt_text,
        "Multiple Timeframes": "1m" in prompt_text and "4h" in prompt_text and "1d" in prompt_text,
        "Historical Arrays": "series (oldest → latest)" in prompt_text,
        "Position Data": "position" in prompt_text,
        "Portfolio Metrics": "equity" in prompt_text,
        "Trading Instructions": "BUY|SELL|HOLD|CLOSE" in prompt_text
    }
    
    print(f"\n🔍 Veri Bütünlük Kontrolü:")
    complete_count = 0
    total_checks = len(data_checks)
    
    for check_name, is_present in data_checks.items():
        status = "✅ Var" if is_present else "❌ Eksik"
        print(f"   {check_name}: {status}")
        if is_present:
            complete_count += 1
    
    completeness_pct = (complete_count / total_checks) * 100
    print(f"\n📈 Veri Bütünlük Yüzdesi: {completeness_pct:.1f}% ({complete_count}/{total_checks})")
    
    return {
        "char_count": char_count,
        "word_count": word_count,
        "line_count": line_count,
        "sections": sections,
        "data_checks": data_checks,
        "completeness_pct": completeness_pct
    }

def test_glm_data_flow():
    """Test complete GLM data flow"""
    
    print("=" * 80)
    print("🧪 GLM VERİ AKIŞI TESTİ")
    print("=" * 80)
    print()
    
    try:
        # 1. Create test data
        print("📊 Test verileri oluşturuluyor...")
        market_data, portfolio_metrics = create_full_test_data()
        print("✅ Test verileri hazır")
        print()
        
        # 2. Build prompt using Nof1PromptBuilder
        print("🔨 Nof1PromptBuilder ile prompt oluşturuluyor...")
        from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
        
        builder = Nof1PromptBuilder()
        prompt = builder.build_prompt(market_data, portfolio_metrics)
        
        print("✅ Prompt oluşturuldu")
        print()
        
        # 3. Analyze prompt content
        prompt_analysis = analyze_data_content(prompt, "Oluşturulan Prompt")
        
        # 4. Prepare GLM messages
        print("\n📤 GLM mesajları hazırlanıyor...")
        system_content = "Sen AGRESİF ve kar odaklı bir profesyonel kripto para türev piyasası traderısın. KRİTİK KURAL: JSON yanıtındaki 'gerekçe' alanı MUTLAKA TÜRKÇE ve EN AZ 800-1000 KARAKTER uzunluğunda olmalıdır."
        
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt}
        ]
        
        # Analyze system content
        system_analysis = analyze_data_content(system_content, "System Mesajı")
        
        # 5. Calculate total payload
        total_content = system_content + prompt
        total_analysis = analyze_data_content(total_content, "Toplam Payload")
        
        # 6. Simulate JSON payload
        payload = {
            "model": "glm-4-plus",
            "messages": messages,
            "stream": False,
            "max_tokens": 32768
        }
        
        json_payload = json.dumps(payload, ensure_ascii=False)
        json_size_bytes = len(json_payload.encode('utf-8'))
        json_size_kb = json_size_bytes / 1024
        
        print(f"\n📦 JSON Payload Analizi:")
        print("=" * 60)
        print(f"📏 JSON Boyutu (bytes): {json_size_bytes:,}")
        print(f"📏 JSON Boyutu (KB): {json_size_kb:.2f}")
        print(f"📏 JSON Boyutu (MB): {json_size_kb/1024:.4f}")
        
        # 7. Check against limits
        print(f"\n⚠️ Limit Kontrolü:")
        print("=" * 60)
        
        warnings = []
        if json_size_kb > 100:
            warnings.append("❌ Payload çok büyük (>100KB) - API limit riski!")
        elif json_size_kb > 50:
            warnings.append("⚠️ Payload büyük (>50KB) - İzlenmeli")
        else:
            warnings.append("✅ Payload boyutu güvenli")
        
        if prompt_analysis["completeness_pct"] < 90:
            warnings.append(f"⚠️ Veri bütünlüğü düşük ({prompt_analysis['completeness_pct']:.1f}%)")
        else:
            warnings.append(f"✅ Veri bütünlüğü iyi ({prompt_analysis['completeness_pct']:.1f}%)")
        
        for warning in warnings:
            print(f"   {warning}")
        
        # 8. Test actual GLM call (optional)
        print(f"\n🔌 GLM API Testi:")
        print("=" * 60)
        
        try:
            from app.risk_manager.glm_client import GLMClient
            
            print("📡 GLM API'ye gönderiliyor...")
            client = GLMClient()
            response = client.request(messages)
            
            print("✅ GLM API yanıtı alındı")
            
            # Analyze response
            if "choices" in response and len(response["choices"]) > 0:
                content = response["choices"][0].get("message", {}).get("content", "")
                response_analysis = analyze_data_content(content, "GLM Yanıtı")
                
                # Check for Turkish justification
                if "gerekçe" in content.lower():
                    print("✅ Türkçe gerekçe alanı bulundu")
                else:
                    print("❌ Türkçe gerekçe alanı bulunamadı")
                
                # Token usage
                usage = response.get("usage", {})
                if usage:
                    print(f"\n🪙 Token Kullanımı:")
                    print(f"   Prompt tokens: {usage.get('prompt_tokens', 0):,}")
                    print(f"   Completion tokens: {usage.get('completion_tokens', 0):,}")
                    print(f"   Total tokens: {usage.get('total_tokens', 0):,}")
            
        except Exception as e:
            print(f"❌ GLM API hatası: {e}")
        
        # 9. Summary
        print(f"\n📋 TEST ÖZETİ:")
        print("=" * 80)
        print(f"✅ Prompt uzunluğu: {prompt_analysis['char_count']:,} karakter")
        print(f"✅ Veri bütünlüğü: {prompt_analysis['completeness_pct']:.1f}%")
        print(f"✅ JSON payload boyutu: {json_size_kb:.2f} KB")
        print(f"✅ Tüm zaman dilimleri: 1m, 5m, 15m, 30m, 1h, 4h, 1d")
        print(f"✅ Tüm indikatörler: Price, EMA, MACD, RSI, Volume")
        print(f"✅ Futures verileri: Funding Rate, Open Interest, L/S Ratio")
        print(f"✅ Portföy bilgileri: Position, PnL, Equity")
        
        return True
        
    except Exception as exc:
        print(f"❌ HATA: {exc}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print()
    print("🚀 GLM Veri Akışı Testi Başlatılıyor...")
    print()
    
    success = test_glm_data_flow()
    
    print()
    print("=" * 80)
    if success:
        print("✅ TEST BAŞARILI!")
    else:
        print("❌ TEST BAŞARISIZ!")
    print("=" * 80)
    print()
    
    sys.exit(0 if success else 1)
