#!/bin/bash
# Quick health check for trading bot

echo "=== Trading Bot Health Check ==="
echo ""

echo "1. Services Status:"
systemctl is-active trading-orchestrator && echo "  ✅ Orchestrator: Running" || echo "  ❌ Orchestrator: Stopped"
systemctl is-active trading-feature-sync && echo "  ✅ Feature Sync: Running" || echo "  ❌ Feature Sync: Stopped"

echo ""
echo "2. Latest Data:"
cd /root/trading
.venv/bin/python -c "
from app.utils.influx import query_latest_snapshot
from datetime import datetime, timezone

snapshot = query_latest_snapshot('features_5min', 'BTCUSDT', '5min')
if snapshot:
    ts_str = snapshot['timestamp'].replace('Z', '+00:00')
    ts = datetime.fromisoformat(ts_str)
    now = datetime.now(timezone.utc)
    age_minutes = (now - ts).total_seconds() / 60
    
    print(f'  Timestamp: {ts_str}')
    print(f'  Age: {age_minutes:.1f} minutes')
    print(f'  Close: {snapshot.get(\"close\"):.2f}')
    print(f'  EMA20: {snapshot.get(\"ema_20\"):.2f}')
    print(f'  EMA50: {snapshot.get(\"ema_50\"):.2f}')
    print(f'  RSI: {snapshot.get(\"rsi_14\"):.2f}')
    
    if age_minutes < 10:
        print('  ✅ Data is fresh')
    else:
        print('  ⚠️  Data is stale')
else:
    print('  ❌ No data found')
" 2>/dev/null

echo ""
echo "3. Recent Logs:"
echo "  Feature Sync:"
journalctl -u trading-feature-sync --since "2 minutes ago" --no-pager -n 3 2>/dev/null | tail -3
echo ""
echo "  Orchestrator:"
journalctl -u trading-orchestrator --since "2 minutes ago" --no-pager -n 3 2>/dev/null | tail -3

echo ""
echo "=== End of Health Check ==="
