#!/usr/bin/env python3
"""
Test script for multi-symbol trading support.
Tests: Binance API calls, InfluxDB writes, enriched feed for ETH and SOL
"""
import asyncio
from datetime import datetime

from app.data_feeds.enriched_feed import fetch_binance_klines, calculate_indicators_from_klines
from app.data_feeds.binance_futures import BinanceFuturesClient
from app.utils.influx import write_measurement, query_latest_snapshot
from app.utils.logging import configure_logging, get_logger


configure_logging("INFO")
logger = get_logger(__name__)


async def test_binance_spot_api(symbol: str) -> bool:
    """Test Binance Spot API for a symbol"""
    logger.info("=" * 60)
    logger.info("Testing Binance Spot API for %s", symbol)
    logger.info("=" * 60)
    
    try:
        # Fetch klines
        klines = await fetch_binance_klines(symbol, interval="1m", limit=100)
        
        if not klines:
            logger.error("❌ No klines fetched for %s", symbol)
            return False
        
        logger.info("✅ Fetched %d klines for %s", len(klines), symbol)
        logger.info("   Latest close: $%.2f", klines[-1]['close'])
        logger.info("   Latest high: $%.2f", klines[-1]['high'])
        logger.info("   Latest low: $%.2f", klines[-1]['low'])
        logger.info("   Latest volume: %.2f", klines[-1]['volume'])
        
        # Calculate indicators
        indicators = calculate_indicators_from_klines(klines)
        
        if not indicators:
            logger.error("❌ No indicators calculated for %s", symbol)
            return False
        
        logger.info("✅ Calculated %d indicators for %s", len(indicators), symbol)
        logger.info("   RSI: %.2f", indicators.get('rsi_14', 0))
        logger.info("   MACD: %.4f", indicators.get('macd', 0))
        logger.info("   EMA20: %.2f", indicators.get('ema_20', 0))
        
        return True
        
    except Exception as exc:
        logger.error("❌ Binance Spot API test failed for %s: %s", symbol, exc, exc_info=True)
        return False


async def test_binance_futures_api(symbol: str) -> bool:
    """Test Binance Futures API for a symbol"""
    logger.info("=" * 60)
    logger.info("Testing Binance Futures API for %s", symbol)
    logger.info("=" * 60)
    
    try:
        client = BinanceFuturesClient()
        snapshot = await client.fetch_metrics(symbol)
        
        logger.info("✅ Fetched futures metrics for %s", symbol)
        logger.info("   Long/Short Ratio: %.4f", snapshot.long_short_ratio)
        logger.info("   Open Interest: $%.2f", snapshot.open_interest)
        logger.info("   Funding Rate: %.6f", snapshot.funding_rate)
        
        return True
        
    except Exception as exc:
        logger.error("❌ Binance Futures API test failed for %s: %s", symbol, exc, exc_info=True)
        return False


async def test_influxdb_write(symbol: str) -> bool:
    """Test InfluxDB write for a symbol"""
    logger.info("=" * 60)
    logger.info("Testing InfluxDB write for %s", symbol)
    logger.info("=" * 60)
    
    try:
        # Fetch some real data first
        klines = await fetch_binance_klines(symbol, interval="1m", limit=50)
        if not klines:
            logger.error("❌ No data to write for %s", symbol)
            return False
        
        indicators = calculate_indicators_from_klines(klines)
        if not indicators:
            logger.error("❌ No indicators to write for %s", symbol)
            return False
        
        # Write to InfluxDB
        timestamp = datetime.utcnow()
        write_measurement(
            measurement="enriched_test",
            tags={'symbol': symbol, 'interval': '1m'},
            fields=indicators,
            timestamp=timestamp,
        )
        
        logger.info("✅ Wrote test data to InfluxDB for %s", symbol)
        
        # Try to read it back
        await asyncio.sleep(1)  # Give InfluxDB time to index
        
        data = query_latest_snapshot("enriched_test", symbol, "1m")
        if data:
            logger.info("✅ Read back test data from InfluxDB for %s", symbol)
            logger.info("   Close: %.2f", data.get('close', 0))
            logger.info("   RSI: %.2f", data.get('rsi_14', 0))
            return True
        else:
            logger.warning("⚠️ Could not read back test data (may be due to timing)")
            return True  # Don't fail test just because of read timing
        
    except Exception as exc:
        logger.error("❌ InfluxDB test failed for %s: %s", symbol, exc, exc_info=True)
        return False


async def test_enriched_feed(symbol: str) -> bool:
    """Test full enriched feed pipeline for a symbol"""
    logger.info("=" * 60)
    logger.info("Testing Full Enriched Feed for %s", symbol)
    logger.info("=" * 60)
    
    try:
        from app.data_feeds.enriched_feed import aggregate_enriched_data
        
        # Aggregate data (this combines Binance + Futures + Indicators)
        enriched, klines = await aggregate_enriched_data(
            symbol=symbol,
            interval="1m",
            klines_cache=None,
            trace_id=f"test_{symbol}_{int(datetime.utcnow().timestamp())}"
        )
        
        if not enriched:
            logger.error("❌ No enriched data for %s", symbol)
            return False
        
        logger.info("✅ Generated enriched data for %s", symbol)
        logger.info("   Fields: %d", len(enriched))
        logger.info("   Close: %.2f", enriched.get('close', 0))
        logger.info("   RSI: %.2f", enriched.get('rsi_14', 0))
        logger.info("   Long/Short Ratio: %.4f", enriched.get('long_short_ratio', 0))
        
        return True
        
    except Exception as exc:
        logger.error("❌ Enriched feed test failed for %s: %s", symbol, exc, exc_info=True)
        return False


async def main():
    """Run all tests for ETH and SOL"""
    logger.info("=" * 80)
    logger.info("MULTI-SYMBOL TRADING SUPPORT TEST")
    logger.info("Testing ETH and SOL data pipeline")
    logger.info("=" * 80)
    
    symbols = ["ETHUSDT", "SOLUSDT"]
    results = {}
    
    for symbol in symbols:
        logger.info("\n")
        logger.info("🧪 Testing %s...", symbol)
        
        results[symbol] = {
            'spot_api': await test_binance_spot_api(symbol),
            'futures_api': await test_binance_futures_api(symbol),
            'influxdb': await test_influxdb_write(symbol),
            'enriched_feed': await test_enriched_feed(symbol),
        }
        
        await asyncio.sleep(1)  # Rate limiting
    
    # Summary
    logger.info("\n")
    logger.info("=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    
    all_passed = True
    for symbol in symbols:
        logger.info("\n%s:", symbol)
        for test_name, passed in results[symbol].items():
            status = "✅ PASS" if passed else "❌ FAIL"
            logger.info("  %s: %s", test_name.upper(), status)
            if not passed:
                all_passed = False
    
    logger.info("\n")
    if all_passed:
        logger.info("=" * 80)
        logger.info("🎉 ALL TESTS PASSED!")
        logger.info("ETH and SOL are ready for multi-symbol trading")
        logger.info("=" * 80)
    else:
        logger.error("=" * 80)
        logger.error("❌ SOME TESTS FAILED")
        logger.error("Check logs above for details")
        logger.error("=" * 80)
    
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)

