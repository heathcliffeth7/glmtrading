#!/bin/bash

# Price Spike Monitoring Script
# Bu script price spike/filtreleme olaylarını gerçek zamanlı izler

echo "============================================================"
echo "🔍 Price Spike Monitor - Gerçek Zamanlı Log İzleme"
echo "============================================================"
echo ""
echo "İzlenen servisler:"
echo "  - trading-dummy-feed (WebSocket feed)"
echo "  - trading-enriched-1m (1 dakikalık data feed)"
echo "  - trading-orchestrator (Ana orchestrator)"
echo ""
echo "Aranacak kelimeler:"
echo "  - 'Bozuk fiyat filtrelendi'"
echo "  - 'OHLC ilişkisi ihlali'"
echo "  - 'Price spike'"
echo "  - 'Geçersiz close price'"
echo "  - 'Eksik kline yapısı'"
echo ""
echo "CTRL+C ile durdurun"
echo "============================================================"
echo ""

# Renkli output için
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Real-time log monitoring
journalctl -f -n 0 \
  -u trading-dummy-feed.service \
  -u trading-enriched-1m.service \
  -u trading-enriched-feed-1m.service \
  -u trading-orchestrator.service \
  | grep --line-buffered -E "Bozuk fiyat|OHLC ilişkisi|Price spike|Geçersiz close|Eksik kline|WARNING.*price|ERROR.*price" \
  | while IFS= read -r line; do
    # Renklendirme
    if [[ $line == *"Bozuk fiyat"* ]] || [[ $line == *"filtrelendi"* ]]; then
      echo -e "${RED}⚠️  $line${NC}"
    elif [[ $line == *"OHLC"* ]]; then
      echo -e "${YELLOW}⚠️  $line${NC}"
    elif [[ $line == *"ERROR"* ]]; then
      echo -e "${RED}❌ $line${NC}"
    else
      echo -e "${BLUE}ℹ️  $line${NC}"
    fi
  done
