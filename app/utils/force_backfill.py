"""
One-time historical OHLC backfill for candlestick patterns.

This script forces a fresh backfill from Binance to InfluxDB,
including all OHLC data and technical indicators.

Usage:
    cd /root/trading
    source .venv/bin/activate
    python -m app.utils.force_backfill
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.utils import influx
from app.utils.influx import _backfill_from_binance, _backfill_completed

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVALS = [
    ("1h", "enriched_1h"),
    ("4h", "enriched_4h"),
    ("1d", "enriched_1d"),
]


def force_backfill_all():
    """Clear tracking and force fresh backfill for all symbols/intervals."""
    print("=" * 60)
    print("Historical Data Backfill")
    print("=" * 60)
    print()

    # Clear tracking set to allow re-backfill
    before_count = len(_backfill_completed)
    _backfill_completed.clear()
    print(f"Cleared {before_count} backfill tracking entries")
    print()

    total_success = 0
    total_failed = 0

    for symbol in SYMBOLS:
        print(f"\n{'='*40}")
        print(f"Symbol: {symbol}")
        print(f"{'='*40}")

        for interval, measurement in INTERVALS:
            print(f"\n  Backfilling {interval}...")
            success = _backfill_from_binance(
                measurement=measurement,
                symbol=symbol,
                interval=interval,
                limit=200,
            )
            if success:
                print(f"  {interval} OK")
                total_success += 1
            else:
                print(f"  {interval} FAILED")
                total_failed += 1

    # Force flush all pending writes
    print("\nFlushing pending writes to InfluxDB...")
    influx._ensure_client()
    if influx._write_api:
        influx._write_api.flush()
        print("Flush complete!")
    else:
        print("Warning: write_api not initialized")

    print()
    print("=" * 60)
    print(f"Backfill complete!")
    print(f"  Success: {total_success}")
    print(f"  Failed: {total_failed}")
    print("=" * 60)


if __name__ == "__main__":
    force_backfill_all()
