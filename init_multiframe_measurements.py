#!/usr/bin/env python3
"""
Initialize InfluxDB measurements for multi-timeframe trading
Creates enriched_1m and enriched_4h measurements by writing initial data
"""
import asyncio
from datetime import datetime

from app.data_feeds.enriched_feed import aggregate_enriched_data
from app.utils.influx import write_measurement, query_latest_snapshot
from app.utils.logging import configure_logging, get_logger


configure_logging("INFO")
logger = get_logger(__name__)


async def init_measurement(symbol: str, interval: str, measurement_name: str):
    """
    Initialize a single measurement by aggregating and writing first data point
    
    Args:
        symbol: Binance symbol (e.g., BTCUSDT)
        interval: Time interval (e.g., 1m, 4h)
        measurement_name: InfluxDB measurement name (e.g., enriched_1m)
    """
    logger.info("=" * 60)
    logger.info(f"Initializing {measurement_name} (interval={interval})")
    logger.info("=" * 60)
    
    # Check if measurement already has data
    existing = query_latest_snapshot(measurement_name, symbol, interval)
    if existing:
        logger.info(f"✅ {measurement_name} already exists with data:")
        logger.info(f"   Close: ${existing.get('close', 0):.2f}")
        logger.info(f"   RSI: {existing.get('rsi_14', 50):.2f}")
        logger.info(f"   Timestamp: {existing.get('timestamp', 'N/A')}")
        return True
    
    # Aggregate enriched data
    logger.info(f"Fetching and aggregating data for {interval}...")
    try:
        enriched, _ = await aggregate_enriched_data(symbol, interval)
        
        if not enriched:
            logger.error(f"❌ No data available for {interval}")
            return False
        
        # Write to InfluxDB
        timestamp = datetime.utcnow()
        write_measurement(
            measurement=measurement_name,
            tags={'symbol': symbol, 'interval': interval},
            fields=enriched,
            timestamp=timestamp,
        )
        
        logger.info(f"✅ Successfully initialized {measurement_name}")
        logger.info(f"   Close: ${enriched.get('close', 0):.2f}")
        logger.info(f"   RSI: {enriched.get('rsi_14', 50):.2f}")
        logger.info(f"   EMA20: {enriched.get('ema_20', 0):.2f}")
        logger.info(f"   MACD: {enriched.get('macd', 0):.2f}")
        logger.info(f"   Long/Short Ratio: {enriched.get('long_short_ratio', 0):.4f}")
        logger.info(f"   Funding Rate: {enriched.get('funding_rate', 0):.6f}")
        
        # Verify write
        verify = query_latest_snapshot(measurement_name, symbol, interval)
        if verify:
            logger.info(f"✅ Verified: Data written successfully")
            return True
        else:
            logger.warning(f"⚠️ Write succeeded but verification failed")
            return False
            
    except Exception as exc:
        logger.error(f"❌ Failed to initialize {measurement_name}: {exc}", exc_info=True)
        return False


async def main():
    """Initialize all multi-timeframe measurements"""
    logger.info("\n" + "=" * 60)
    logger.info("MULTI-TIMEFRAME INFLUXDB INITIALIZATION")
    logger.info("=" * 60)
    
    symbol = "BTCUSDT"
    
    results = {}
    
    # Initialize 1min (intraday)
    logger.info("\n🔹 Step 1/3: Initialize 1min (Intraday)")
    results['1m'] = await init_measurement(symbol, "1m", "enriched_1m")
    await asyncio.sleep(2)
    
    # Initialize 30min (main) - should already exist
    logger.info("\n🔹 Step 2/3: Verify 30min (Main)")
    results['30min'] = await init_measurement(symbol, "30min", "enriched_30min")
    await asyncio.sleep(2)
    
    # Initialize 4h (long-term)
    logger.info("\n🔹 Step 3/3: Initialize 4h (Long-term)")
    results['4h'] = await init_measurement(symbol, "4h", "enriched_4h")
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("INITIALIZATION SUMMARY")
    logger.info("=" * 60)
    
    for interval, success in results.items():
        status = "✅ SUCCESS" if success else "❌ FAILED"
        logger.info(f"  {interval:>6}: {status}")
    
    all_success = all(results.values())
    
    if all_success:
        logger.info("\n🎉 All measurements initialized successfully!")
        logger.info("\n📋 Next steps:")
        logger.info("  1. Start enriched feed services:")
        logger.info("     sudo systemctl start trading-enriched-feed-1m")
        logger.info("     sudo systemctl start trading-enriched-feed-4h")
        logger.info("  2. Test multi-timeframe collection:")
        logger.info("     .venv/bin/python test_multiframe.py")
    else:
        logger.warning("\n⚠️ Some measurements failed to initialize")
        logger.warning("Check the logs above for details")
        logger.warning("\nCommon issues:")
        logger.warning("  - Binance API rate limit (wait 1 minute)")
        logger.warning("  - External API limit (check quota)")
        logger.warning("  - InfluxDB connection (check service status)")
    
    return all_success


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        exit(1)
    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        exit(1)
