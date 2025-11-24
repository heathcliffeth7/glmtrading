#!/usr/bin/env python3
"""
Test multi-timeframe data collection
"""
import sys

from app.agents.derivatives import DerivativesAgent
from app.utils.logging import configure_logging, get_logger

configure_logging("INFO")
logger = get_logger(__name__)


def test_multiframe_collection():
    """Test that multi-timeframe data is collected correctly"""
    logger.info("=== Testing Multi-Timeframe Data Collection ===")
    
    # Create agent
    agent = DerivativesAgent(symbol="BTCUSDT")
    
    # Collect features
    logger.info("Collecting features from 3 timeframes...")
    features = agent._collect_features()
    
    # Check 30min (main)
    logger.info("\n📊 30min (Main):")
    logger.info(f"  Close: ${features.close:.2f}")
    logger.info(f"  RSI: {features.rsi_14:.2f}")
    logger.info(f"  EMA20: {features.ema_20:.2f} | EMA50: {features.ema_50:.2f}")
    logger.info(f"  MACD: {features.macd:.2f}")
    
    # Check 1min (intraday)
    logger.info("\n⚡ 1min (Intraday):")
    if features.intraday_close > 0:
        logger.info(f"  Close: ${features.intraday_close:.2f}")
        logger.info(f"  RSI: {features.intraday_rsi_14:.2f}")
        logger.info(f"  MACD: {features.intraday_macd:.2f}")
        logger.info(f"  EMA20: {features.intraday_ema_20:.2f}")
    else:
        logger.warning("  ⚠️ No 1min data available (enriched_1m not running?)")
    
    # Check 4h (long-term)
    logger.info("\n🔭 4h (Long-term):")
    if features.longterm_ema_20 > 0:
        logger.info(f"  EMA20: {features.longterm_ema_20:.2f} | EMA50: {features.longterm_ema_50:.2f}")
        logger.info(f"  RSI: {features.longterm_rsi_14:.2f}")
        logger.info(f"  MACD: {features.longterm_macd:.2f}")
        logger.info(f"  ATR: {features.longterm_atr_14:.2f}")
        logger.info(f"  Volume: {features.longterm_volume:.2f}")
    else:
        logger.warning("  ⚠️ No 4h data available (enriched_4h not running?)")
    
    # Collect historical data
    logger.info("\n\n=== Testing Multi-Timeframe Historical Data ===")
    historical = agent._collect_historical_data()
    
    # 1min history
    intraday = historical.get("intraday_1m", {})
    if intraday:
        logger.info("\n⚡ 1min History (last 10 min):")
        for key, values in intraday.items():
            if values:
                logger.info(f"  {key.upper()}: {values[-3:]}... (showing last 3)")
    else:
        logger.warning("  ⚠️ No 1min historical data")
    
    # 30min history
    main = historical.get("main_30min", {})
    if main:
        logger.info("\n📊 30min History (last 5 hours):")
        for key, values in main.items():
            if values:
                logger.info(f"  {key.upper()}: {values[-3:]}... (showing last 3)")
    else:
        logger.warning("  ⚠️ No 30min historical data")
    
    # 4h history
    longterm = historical.get("longterm_4h", {})
    if longterm:
        logger.info("\n🔭 4h History (last 40 hours):")
        for key, values in longterm.items():
            if values:
                logger.info(f"  {key.upper()}: {values[-3:]}... (showing last 3)")
    else:
        logger.warning("  ⚠️ No 4h historical data")
    
    # Generate signal
    logger.info("\n\n=== Testing Signal Generation ===")
    signal = agent.generate_signal()
    logger.info(f"\nSignal: {signal.direction}")
    logger.info(f"Confidence: {signal.confidence:.2f}")
    logger.info(f"Reasoning: {signal.reasoning}")
    
    # Check metadata
    metadata = signal.metadata.get("historical_data", {})
    if metadata:
        logger.info("\n✅ Historical data included in signal metadata")
        for tf_key in ["intraday_1m", "main_30min", "longterm_4h"]:
            if tf_key in metadata:
                logger.info(f"  ✅ {tf_key}: {len(metadata[tf_key])} indicators")
    else:
        logger.warning("\n⚠️ No historical data in signal metadata")
    
    logger.info("\n=== Test Complete ===")
    
    # Summary
    has_1m = features.intraday_close > 0
    has_30m = features.close > 0
    has_4h = features.longterm_ema_20 > 0
    
    logger.info("\n📋 Summary:")
    logger.info(f"  1m data: {'✅' if has_1m else '❌'}")
    logger.info(f"  30m data: {'✅' if has_30m else '❌'}")
    logger.info(f"  4h data: {'✅' if has_4h else '❌'}")
    
    if not has_1m:
        logger.warning("\n⚠️ To enable 1m data, run:")
        logger.warning("  sudo cp /root/trading/infra/trading-enriched-feed-1m.service /etc/systemd/system/")
        logger.warning("  sudo systemctl daemon-reload")
        logger.warning("  sudo systemctl start trading-enriched-feed-1m")
    
    if not has_4h:
        logger.warning("\n⚠️ To enable 4h data, run:")
        logger.warning("  sudo cp /root/trading/infra/trading-enriched-feed-4h.service /etc/systemd/system/")
        logger.warning("  sudo systemctl daemon-reload")
        logger.warning("  sudo systemctl start trading-enriched-feed-4h")
    
    return has_1m and has_30m and has_4h


if __name__ == "__main__":
    try:
        success = test_multiframe_collection()
        sys.exit(0 if success else 1)
    except Exception as exc:
        logger.error("Test failed: %s", exc, exc_info=True)
        sys.exit(1)
