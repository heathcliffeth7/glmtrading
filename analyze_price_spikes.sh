#!/bin/bash

# Price Spike Analiz Script
# Son 24 saatteki price spike olaylarını analiz eder

echo "============================================================"
echo "📊 Price Spike Analizi - Son 24 Saat"
echo "============================================================"
echo ""

# Zaman aralığı
TIME_PERIOD="24 hours ago"

echo "🔍 Arama periyodu: Son 24 saat"
echo ""

# 1. Toplam bozuk fiyat sayısı
echo "1️⃣ Toplam Bozuk Fiyat (0.00) Tespit Edildi:"
BOZUK_COUNT=$(journalctl --since "$TIME_PERIOD" | grep "Bozuk fiyat filtrelendi" | wc -l)
echo "   Count: $BOZUK_COUNT"
echo ""

# 2. OHLC ihlalleri
echo "2️⃣ OHLC İlişkisi İhlali:"
OHLC_COUNT=$(journalctl --since "$TIME_PERIOD" | grep "OHLC ilişkisi ihlali" | wc -l)
echo "   Count: $OHLC_COUNT"
echo ""

# 3. Price spike >5%
echo "3️⃣ Price Spike (>5% değişim):"
SPIKE_COUNT=$(journalctl --since "$TIME_PERIOD" | grep "Price spike.*değişim" | wc -l)
echo "   Count: $SPIKE_COUNT"
echo ""

# 4. Eksik kline yapısı
echo "4️⃣ Eksik Kline Yapısı:"
EKSIK_COUNT=$(journalctl --since "$TIME_PERIOD" | grep "Eksik kline yapısı" | wc -l)
echo "   Count: $EKSIK_COUNT"
echo ""

# 5. Geçersiz close price
echo "5️⃣ Geçersiz Close Price (≤0):"
GECERSIZ_COUNT=$(journalctl --since "$TIME_PERIOD" | grep "Geçersiz close price" | wc -l)
echo "   Count: $GECERSIZ_COUNT"
echo ""

# Toplam
TOTAL=$((BOZUK_COUNT + OHLC_COUNT + SPIKE_COUNT + EKSIK_COUNT + GECERSIZ_COUNT))

echo "============================================================"
echo "📈 TOPLAM: $TOTAL filtrelenen mesaj"
echo "============================================================"
echo ""

# Eğer örnek varsa göster
if [ $TOTAL -gt 0 ]; then
    echo "📝 Son 10 Örnek:"
    echo "-----------------------------------------------------------"
    journalctl --since "$TIME_PERIOD" | \
      grep -E "Bozuk fiyat|OHLC ilişkisi|Price spike|Eksik kline|Geçersiz close" | \
      tail -10
    echo ""
else
    echo "✅ Son 24 saatte hiçbir bozuk veri tespit edilmedi!"
    echo "   Sistem temiz çalışıyor 🎉"
    echo ""
fi

# Toplam mesaj sayısı tahmini (WebSocket mesajları)
echo "============================================================"
echo "📊 İstatistikler"
echo "============================================================"

# 1 dakikada ~60 mesaj, 1 saatte 3600, 24 saatte 86400
ESTIMATED_TOTAL=86400
if [ $TOTAL -gt 0 ]; then
    PERCENTAGE=$(awk "BEGIN {printf \"%.4f\", ($TOTAL / $ESTIMATED_TOTAL) * 100}")
    echo "Tahmini Toplam Mesaj: ~$ESTIMATED_TOTAL (24 saat)"
    echo "Filtrelenen Mesaj: $TOTAL"
    echo "Filtreleme Oranı: ~$PERCENTAGE%"
else
    echo "Filtreleme Oranı: 0% (Mükemmel! 🎯)"
fi

echo ""
echo "============================================================"
echo "✅ Analiz tamamlandı"
echo "============================================================"
