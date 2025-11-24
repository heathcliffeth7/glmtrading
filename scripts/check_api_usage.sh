#!/bin/bash
# Check TwelveData API usage and optimize

echo "=== TwelveData API Usage Analysis ==="
echo ""

# Count recent API calls in last 5 minutes
echo "Recent API calls (last 5 min):"
journalctl -u trading-enriched-feed -u trading-orchestrator --since "5 minutes ago" 2>/dev/null | \
  grep "twelvedata.com" | wc -l

echo ""
echo "API credits per minute:"
echo "  Free tier: 8 requests/minute per key"
echo "  Total keys: 12"
echo "  Theoretical max: 96 requests/minute"

echo ""
echo "Current configuration:"
echo "  Enriched Feed: 3 indicators every 5 minutes (0.6 req/min)"
echo "  Feature Sync: Disabled"
echo "  Data Feed Orchestrator: May be running"

echo ""
echo "Checking active TwelveData services..."
ps aux | grep -E "twelve|enriched" | grep -v grep

echo ""
echo "Recommendations:"
echo "  1. Keep enriched feed at 5min interval (current)"
echo "  2. Use only 3 essential indicators (RSI, MACD, BBands)"
echo "  3. Disable data_feed_orchestrator's twelve_data poll if running"
echo "  4. Stagger requests across time"
