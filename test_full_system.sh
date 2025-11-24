#!/bin/bash
# Full system test for multi-signal trading bot

echo "=== Multi-Signal Trading System Test ==="
echo ""

cd /root/trading

echo "1. Check Services:"
echo "  Enriched Feed:"
systemctl is-active trading-enriched-feed && echo "    ✅ Running" || echo "    ❌ Stopped"
echo "  Orchestrator:"
systemctl is-active trading-orchestrator && echo "    ✅ Running" || echo "    ❌ Stopped"

echo ""
echo "2. Check Enriched Data:"
.venv/bin/python -c "
from app.utils.influx import query_latest_snapshot
from datetime import datetime, timezone

enriched = query_latest_snapshot('enriched_5min', 'BTCUSDT', '5min')
if enriched:
    ts_str = enriched['timestamp'].replace('Z', '+00:00')
    ts = datetime.fromisoformat(ts_str)
    now = datetime.now(timezone.utc)
    age_minutes = (now - ts).total_seconds() / 60
    
    print(f'  Timestamp: {ts_str}')
    print(f'  Age: {age_minutes:.1f} minutes')
    print(f'  Fields: {len(enriched)}')
    print(f'  Close: {enriched.get(\"close\"):.2f}')
    print(f'  RSI: {enriched.get(\"rsi_14\"):.2f}')
    print(f'  L/S Ratio: {enriched.get(\"long_short_ratio\"):.4f}')
    print(f'  Funding: {enriched.get(\"funding_rate\"):.6f}')
    
    if age_minutes < 10:
        print('  ✅ Fresh data')
    else:
        print('  ⚠️  Stale data')
else:
    print('  ❌ No enriched data')
" 2>/dev/null

echo ""
echo "3. Test MultiSignalAgent:"
.venv/bin/python -c "
from app.agents.multi_signal import MultiSignalAgent

agent = MultiSignalAgent('BTCUSDT', '5min')
signal = agent.generate_signal()

print(f'  Direction: {signal.direction}')
print(f'  Confidence: {signal.confidence:.2%}')
print(f'  Reasoning: {signal.reasoning}')

if signal.direction in ['BUY', 'SELL', 'HOLD']:
    print('  ✅ Valid signal')
else:
    print('  ❌ Invalid signal')
" 2>/dev/null

echo ""
echo "4. Recent Orchestrator Activity:"
journalctl -u trading-orchestrator --since "5 minutes ago" --no-pager -n 5 2>/dev/null | grep -E "Cycle|decision|action" | tail -3

echo ""
echo "5. Recent Enriched Feed Activity:"
journalctl -u trading-enriched-feed --since "5 minutes ago" --no-pager -n 3 2>/dev/null | tail -3

echo ""
echo "=== Test Complete ==="
