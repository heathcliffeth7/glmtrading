#!/usr/bin/env python3
"""
Import historical data from Binance and write to InfluxDB
Fetches 4 weeks of 30min OHLCV data, calculates indicators, and imports to enriched_30min
"""
import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from app.data_feeds.enriched_feed import calculate_indicators_from_klines
from app.data_feeds.binance_futures import BinanceFuturesClient
from app.utils.influx import write_measurement
from app.utils.logging import configure_logging, get_logger

# Disable Telegram notifications during bulk import
import os
os.environ['TELEGRAM_ENABLED'] = 'false'

configure_logging("INFO")
logger = get_logger(__name__)


async def fetch_binance_historical_klines(
    symbol: str = "BTCUSDT",
    interval: str = "30m",
    days: int = 28,
) -> list[dict]:
    """
    Fetch historical klines from Binance Spot API
    
    Args:
        symbol: Trading symbol
        interval: Kline interval
        days: Number of days to fetch
    
    Returns:
        List of kline dicts with OHLCV data
    """
    logger.info("=" * 60)
    logger.info("FETCHING HISTORICAL DATA FROM BINANCE")
    logger.info("=" * 60)
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Interval: {interval}")
    logger.info(f"Days: {days}")
    
    # Normalize interval for Binance API (e.g., 15min -> 15m)
    api_interval = interval
    if interval.endswith("min"):
        api_interval = interval.replace("min", "m")
        logger.info(f"Using Binance interval: {api_interval} (from {interval})")
    
    # Calculate bars/day from interval (e.g., 5m -> 288/day, 15m -> 96/day, 30m -> 48/day)
    def _interval_to_minutes(val: str) -> float:
        if val.endswith("min"):
            return float(val[:-3] or 0)
        if val.endswith("m"):
            return float(val[:-1] or 0)
        if val.endswith("h"):
            return float(val[:-1] or 0) * 60
        if val.endswith("d"):
            return float(val[:-1] or 0) * 1440
        return 0.0

    interval_minutes = _interval_to_minutes(api_interval)
    bars_per_day = int(1440 / interval_minutes) if interval_minutes else 48
    bars_needed = days * bars_per_day
    
    # Binance limit is 1000 per request, so we may need multiple requests
    logger.info(f"Need {bars_needed} bars ({days} days x {bars_per_day} bars/day)")
    
    # Binance API limit is 1000 klines per request
    # Fetch in batches working backwards from now
    all_klines = []
    fetched = 0
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        while fetched < bars_needed:
            # How many more do we need?
            remaining = bars_needed - fetched
            batch_size = min(1000, remaining)
            
            # Fetch without timestamps - Binance will give us latest bars
            params = {
                "symbol": symbol,
                "interval": api_interval,
                "limit": batch_size,
            }
            
            # If we already have data, use endTime to fetch older data
            if all_klines:
                # Get timestamp of oldest kline we have
                oldest_time_ms = int(all_klines[0]['timestamp'].timestamp() * 1000)
                params['endTime'] = oldest_time_ms - 1  # Fetch bars before this
            
            logger.info(f"Fetching batch: {batch_size} bars (progress: {fetched}/{bars_needed})")
            
            try:
                response = await client.get(
                    "https://api.binance.com/api/v3/klines",
                    params=params
                )
                response.raise_for_status()
                klines_batch = response.json()
                
                if not klines_batch:
                    logger.info("No more klines available")
                    break
                
                # Convert to dict format (prepend to list since we're going backwards)
                batch_data = []
                for kline in klines_batch:
                    batch_data.append({
                        "timestamp": pd.to_datetime(int(kline[0]), unit='ms'),
                        "open": float(kline[1]),
                        "high": float(kline[2]),
                        "low": float(kline[3]),
                        "close": float(kline[4]),
                        "volume": float(kline[5]),
                    })
                
                # Prepend to all_klines (since we're fetching backwards)
                all_klines = batch_data + all_klines
                fetched += len(klines_batch)
                
                logger.info(f"  Fetched {len(klines_batch)} klines (total: {len(all_klines)})")
                
                # If we got less than requested, we've hit the limit
                if len(klines_batch) < batch_size:
                    logger.info(f"Reached data limit (got {len(klines_batch)}, requested {batch_size})")
                    break
                
                # Avoid rate limits
                await asyncio.sleep(0.5)
                
            except Exception as exc:
                logger.error(f"Failed to fetch klines: {exc}")
                break
    
    logger.info(f"✅ Total fetched: {len(all_klines)} klines")
    return all_klines


async def import_to_influxdb(
    klines: list[dict],
    symbol: str = "BTCUSDT",
    interval: str = "30min",
) -> None:
    """
    Process klines, calculate indicators, and import to InfluxDB
    
    Args:
        klines: List of kline dicts
        symbol: Trading symbol
        interval: Time interval
    """
    logger.info("\n" + "=" * 60)
    logger.info("PROCESSING AND IMPORTING TO INFLUXDB")
    logger.info("=" * 60)
    
    if len(klines) < 100:
        logger.error(f"Not enough klines for indicator calculation (need 100+, got {len(klines)})")
        return
    
    # Get current futures metrics (will use same for all historical data)
    # Note: This is not ideal but historical futures data is harder to get
    logger.info("Fetching current futures metrics...")
    try:
        futures_client = BinanceFuturesClient()
        futures_snapshot = await futures_client.fetch_metrics(symbol)
        futures_data = {
            'long_short_ratio': futures_snapshot.long_short_ratio,
            'open_interest': futures_snapshot.open_interest,
            'funding_rate': futures_snapshot.funding_rate,
        }
        logger.info(f"  L/S: {futures_data['long_short_ratio']:.4f}")
        logger.info(f"  OI: {futures_data['open_interest']:.0f}")
        logger.info(f"  FR: {futures_data['funding_rate']:.6f}")
    except Exception as exc:
        logger.warning(f"Could not fetch futures metrics: {exc}")
        futures_data = {
            'long_short_ratio': 0.0,
            'open_interest': 0.0,
            'funding_rate': 0.0,
        }
    
    # Process in batches of 100 (need 100 for rolling indicators)
    logger.info(f"\nProcessing {len(klines)} klines...")
    
    imported_count = 0
    failed_count = 0
    
    # We need to process with rolling window
    # Start from bar 100 (so we have 100 bars of history for indicators)
    for i in range(100, len(klines)):
        # Get last 100 bars for indicator calculation
        window = klines[i-100:i+1]
        current_bar = klines[i]
        
        try:
            # Calculate indicators
            indicators = calculate_indicators_from_klines(window)
            
            if not indicators or 'close' not in indicators:
                failed_count += 1
                continue
            
            # Add futures metrics
            indicators.update(futures_data)
            
            # Write to InfluxDB
            # Convert pandas Timestamp to datetime if needed
            bar_timestamp = current_bar['timestamp']
            if hasattr(bar_timestamp, 'to_pydatetime'):
                bar_timestamp = bar_timestamp.to_pydatetime()
            
            write_measurement(
                measurement=f"enriched_{interval}",
                tags={'symbol': symbol, 'interval': interval},
                fields=indicators,
                timestamp=bar_timestamp,
            )
            
            imported_count += 1
            
            # Log progress every 100 bars
            if imported_count % 100 == 0:
                logger.info(f"  Imported {imported_count} / {len(klines) - 100} bars...")
                
        except Exception as exc:
            logger.debug(f"Failed to process bar {i}: {exc}")
            failed_count += 1
            continue
    
    logger.info(f"\n✅ Import completed!")
    logger.info(f"  Successfully imported: {imported_count} bars")
    logger.info(f"  Failed: {failed_count} bars")
    logger.info(f"  Time range: {klines[100]['timestamp']} to {klines[-1]['timestamp']}")


async def main():
    parser = argparse.ArgumentParser(description="Import historical Binance data to InfluxDB")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--interval", default="30min", help="Time interval")
    parser.add_argument("--days", type=int, default=28, help="Number of days to import (default: 28 = 4 weeks)")
    
    args = parser.parse_args()
    
    # Fetch historical klines
    klines = await fetch_binance_historical_klines(
        symbol=args.symbol,
        interval=args.interval,
        days=args.days,
    )
    
    if not klines:
        logger.error("No klines fetched!")
        exit(1)
    
    # Import to InfluxDB
    await import_to_influxdb(
        klines=klines,
        symbol=args.symbol,
        interval=args.interval,
    )
    
    logger.info("\n" + "=" * 60)
    logger.info("HISTORICAL DATA IMPORT COMPLETED")
    logger.info("=" * 60)
    logger.info("\nNext steps:")
    logger.info("  1. Export data: .venv/bin/python scripts/export_historical_20features.py --days 30")
    logger.info("  2. Train model: .venv/bin/python scripts/train_model_20features.py")
    logger.info("  3. Test model: .venv/bin/python test_multiframe.py")


if __name__ == "__main__":
    asyncio.run(main())
