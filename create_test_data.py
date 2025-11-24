#!/usr/bin/env python3
"""
InfluxDB'ye test verisi oluşturur
"""
import sys
from datetime import datetime, timedelta
sys.path.insert(0, '/root/trading')

from app.utils.influx import _ensure_client
from app.config.settings import get_settings
from influxdb_client import InfluxDBClient

def create_test_data():
    print("📊 InfluxDB Test Verisi Oluşturuluyor...")
    
    try:
        # Doğrudan client oluştur
        settings = get_settings()
        client = InfluxDBClient(
            url=str(settings.influx.url),
            token=settings.influx.token,
            org=settings.influx.org
        )
        write_api = client.write_api()
        
        print(f"✅ InfluxDB bağlantısı başarılı: {settings.influx.url}")
        
        # Test verisi oluştur - son 50 saatlik 15dk verileri
        base_time = datetime.utcnow() - timedelta(hours=50)
        
        points = []
        for i in range(200):  # 200 adet 15dk bar (50 saat)
            current_time = base_time + timedelta(minutes=15 * i)
            
            # BTC fiyat simülasyonu
            base_price = 65000
            price_variation = 2000 * (i % 20) / 20  # Dalgalanma
            
            open_price = base_price + price_variation - 100
            high_price = open_price + 500
            low_price = open_price - 300
            close_price = open_price + (200 if i % 2 == 0 else -200)
            
            # Teknik indikatörler
            rsi = 50 + 20 * (i % 10) / 10
            ema_20 = base_price + price_variation * 0.8
            ema_50 = base_price + price_variation * 0.6
            macd = (close_price - ema_20) / 100
            
            point = {
                "measurement": "enriched_15min",
                "tags": {
                    "symbol": "BTCUSDT",
                    "interval": "15min"
                },
                "fields": {
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": 1000 + i * 10,
                    "rsi_14": rsi,
                    "ema_20": ema_20,
                    "ema_50": ema_50,
                    "macd": macd,
                    "signal": 0.1 if close_price > ema_20 else -0.1
                },
                "time": current_time
            }
            points.append(point)
        
        # Verileri yaz
        from influxdb_client import Point
        for point_data in points:
            point = Point(point_data["measurement"]) \
                .tag("symbol", point_data["tags"]["symbol"]) \
                .tag("interval", point_data["tags"]["interval"]) \
                .field("open", point_data["fields"]["open"]) \
                .field("high", point_data["fields"]["high"]) \
                .field("low", point_data["fields"]["low"]) \
                .field("close", point_data["fields"]["close"]) \
                .field("volume", point_data["fields"]["volume"]) \
                .field("rsi_14", point_data["fields"]["rsi_14"]) \
                .field("ema_20", point_data["fields"]["ema_20"]) \
                .field("ema_50", point_data["fields"]["ema_50"]) \
                .field("macd", point_data["fields"]["macd"]) \
                .field("signal", point_data["fields"]["signal"]) \
                .time(point_data["time"])
            
            write_api.write(bucket=settings.influx.bucket, record=point)
        
        print(f"✅ {len(points)} adet test verisi oluşturuldu")
        
        # Son veriyi kontrol et
        write_api.flush()
        client.close()
        
        return True
        
    except Exception as e:
        print(f"❌ Hata: {e}")
        return False

if __name__ == "__main__":
    if create_test_data():
        print("🎉 Test verisi başarıyla oluşturuldu!")
    else:
        print("❌ Test verisi oluşturulamadı!")
        sys.exit(1)
