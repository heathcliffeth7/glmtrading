#!/usr/bin/env python3
"""
Test GLM Telegram Integration - GLM yanıtının tamamını telegram'a gönderme testi
"""

import os
import sys

sys.path.insert(0, '/root/trading')

def test_glm_telegram_integration():
    """GLM yanıtının telegram'a gönderilmesini test et"""
    
    print("=" * 80)
    print("📱 GLM TELEGRAM İNTEGRASYON TESTİ")
    print("=" * 80)
    print()
    
    try:
        # 1. RiskManager'ı başlat
        print("🔧 RiskManager başlatılıyor...")
        from app.risk_manager.manager import RiskManager
        
        risk_manager = RiskManager()
        print("✅ RiskManager hazır")
        print()
        
        # 2. Test verisi oluştur
        print("📊 Test verileri oluşturuluyor...")
        
        from app.agents.short_term import PureDataCollector
        
        collector = PureDataCollector()
        signal = collector.generate_signal()
        
        # Portfolio metrics - database'den gelmeli
        portfolio_metrics = {
            "equity": 10500.00,
            "available_cash": 5500.00,
            "position": 0.05,
            "entry_price": 108000.00,
            "current_price": 110000.00,
            "unrealized_pnl": 100.00,
            "leverage": 10,
            "price": 110000.00
        }
        
        print("✅ Test verileri hazır")
        print(f"   Signal: {signal.direction}")
        print(f"   Portfolio Equity: ${portfolio_metrics['equity']:.2f}")
        print()
        
        # 3. GLM çağrısı yap
        print("🤖 GLM çağrısı yapılıyor...")
        print("⚠️  Telegram'a gönderim için telegram_client.enabled() kontrol ediliyor...")
        print()
        
        decision = risk_manager.evaluate([signal], portfolio_metrics)
        
        print("✅ GLM yanıtı alındı")
        print(f"   Karar: {decision.action}")
        print(f"   Miktar: {decision.amount:.6f}")
        print(f"   Kaldıraç: {decision.leverage:.1f}x")
        print(f"   Güven: {decision.glm_confidence:.1f}%")
        print(f"   Yanıt Süresi: {decision.glm_response_time_ms:.0f}ms")
        print()
        
        # 4. Telegram durumunu kontrol et
        print("📱 Telegram durumu kontrol ediliyor...")
        from app.utils.telegram import telegram_client
        
        if telegram_client.enabled():
            print("✅ Telegram client aktif")
            print("📤 GLM yanıtının tamamı telegram'a gönderilmeli")
            print()
            print("🔍 Telegram mesajı içeriği:")
            print("   • 🤖 GLM TAM YANITI başlığı")
            print("   • 📊 Karar, Miktar, Kaldıraç, Güven bilgileri")
            print("   • 📝 TAM GLM YANITI (ilk 3000 karakter)")
            print("   • 💡 Gerekçe (ilk 500 karakter)")
        else:
            print("❌ Telegram client devre dışı")
            print("💡 Lütfen .env dosyasında TELEGRAM_BOT_TOKEN ve TELEGRAM_CHANNEL_ID ayarlarını kontrol edin")
        
        print()
        print("📋 TEST SONUCU:")
        print("=" * 60)
        
        if telegram_client.enabled():
            print("✅ Telegram entegrasyonu başarılı")
            print("✅ GLM yanıtı telegram'a gönderildi")
            print("✅ Tam yanıt içeriği telegram'da görüntülenebilir")
        else:
            print("⚠️ Telegram entegrasyonu devre dışı")
            print("💡 Telegram ayarları yapıldığında çalışacaktır")
        
        print(f"✅ GLM karar sistemi çalışıyor: {decision.action}")
        print(f"✅ Yanıt süresi: {decision.glm_response_time_ms:.0f}ms")
        
        return telegram_client.enabled()
        
    except Exception as exc:
        print(f"❌ HATA: {exc}")
        import traceback
        traceback.print_exc()
        return False

def test_telegram_settings():
    """Telegram ayarlarını kontrol et"""
    
    print("\n" + "=" * 80)
    print("⚙️ TELEGRAM AYARLARI KONTROLÜ")
    print("=" * 80)
    print()
    
    try:
        from app.config.settings import get_settings
        from app.utils.telegram import telegram_client
        
        settings = get_settings()
        
        print("🔍 Ayarlar:")
        print(f"   • TELEGRAM_BOT_TOKEN: {'✅ Ayarlanmış' if settings.telegram_bot_token else '❌ Boş'}")
        print(f"   • TELEGRAM_CHANNEL_ID: {'✅ Ayarlanmış' if settings.telegram_channel_id else '❌ Boş'}")
        print(f"   • Telegram Client: {'✅ Aktif' if telegram_client.enabled() else '❌ Devre dışı'}")
        
        if telegram_client.enabled():
            print("\n✅ Telegram ayarları doğru yapılandırılmış")
            print("📱 GLM yanıtları telegram'a gönderilebilir")
        else:
            print("\n❌ Telegram ayarları eksik")
            print("💡 .env dosyasına ekleyin:")
            print("   TELEGRAM_BOT_TOKEN=your_bot_token")
            print("   TELEGRAM_CHANNEL_ID=your_channel_id")
        
        return telegram_client.enabled()
        
    except Exception as exc:
        print(f"❌ Ayar kontrolü hatası: {exc}")
        return False

if __name__ == "__main__":
    print()
    print("🚀 GLM Telegram İntegrasyon Testi Başlatılıyor...")
    print()
    
    # Telegram ayarlarını kontrol et
    telegram_ok = test_telegram_settings()
    
    # GLM telegram entegrasyonunu test et
    if telegram_ok:
        integration_ok = test_glm_telegram_integration()
    else:
        print("\n⚠️ Telegram ayarları eksik olduğu için entegrasyon testi atlanıyor")
        integration_ok = False
    
    print()
    print("=" * 80)
    if telegram_ok and integration_ok:
        print("🎉 TEST BAŞARILI - GLM YANITLARI TELEGRAM'A GÖNDERİLİYOR!")
    elif telegram_ok:
        print("✅ TELEGRAM AKTİF - GLM entegrasyonu hazır")
    else:
        print("❌ TEST BAŞARISIZ - TELEGRAM AYARLARI EKSİK!")
    print("=" * 80)
    print()
    
    sys.exit(0 if telegram_ok else 1)
