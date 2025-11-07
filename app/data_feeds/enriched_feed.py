"""
Enriched Feed - Combines Binance data sources for comprehensive trading signals
Aggregates: Binance Spot + Binance Futures + TA Features
"""
import asyncio
from datetime import datetime
from typing import Dict, Any, List

import httpx
import pandas as pd
import ta

from app.config.settings import get_settings
from app.data_feeds.binance_futures import BinanceFuturesClient
from app.utils.influx import write_measurement, query_latest_snapshot
from app.utils.latency import get_latency_tracker
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)


async def fetch_binance_klines(symbol: str, interval: str = "1m", limit: int = 200) -> List[Dict]:
    """
    Fetch historical klines from Binance Spot API
    
    Args:
        symbol: Trading symbol (e.g., BTCUSDT)
        interval: Kline interval (1m, 5m, 15m, etc.)
        limit: Number of klines to fetch (max 1000)
    
    Returns:
        List of kline dicts with OHLCV data
    """
    try:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("https://api.binance.com/api/v3/klines", params=params)
            response.raise_for_status()
            klines = response.json()
        
        # Convert to dict format
        # Kline format: [open_time, open, high, low, close, volume, close_time, ...]
        result = []
        for kline in klines:
            result.append({
                "timestamp": pd.to_datetime(int(kline[0]), unit='ms'),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            })
        
        return result
        
    except Exception as e:
        logger.error("Failed to fetch Binance klines: %s", e)
        return []


def calculate_indicators_from_klines(klines: List[Dict]) -> Dict[str, Any]:
    """
    Calculate technical indicators from Binance klines using pandas-ta
    
    Args:
        klines: List of kline dicts with OHLCV data
    
    Returns:
        Dict with calculated indicators for the latest bar
    """
    try:
        if not klines or len(klines) < 1:
            logger.warning("No klines available for indicator calculation")
            return {}
        
        # Convert to DataFrame
        df = pd.DataFrame(klines)
        
        # Calculate indicators using ta library
        indicators = {}
        
        # Latest bar data - ALWAYS include basic OHLCV regardless of kline count
        latest = df.iloc[-1]
        indicators['close'] = float(latest['close'])
        indicators['high'] = float(latest['high'])
        indicators['low'] = float(latest['low'])
        indicators['volume'] = float(latest['volume'])
        
        # Early return if not enough klines for indicators (but we have OHLCV)
        if len(klines) < 50:
            logger.warning("Not enough klines for full indicator calculation (need 50+, got %d), but OHLCV data included", len(klines))
            logger.info("✅ Basic OHLCV data: close=%.2f high=%.2f low=%.2f volume=%.2f", 
                       indicators.get('close', 0), 
                       indicators.get('high', 0),
                       indicators.get('low', 0),
                       indicators.get('volume', 0))
            return indicators
        
        # Trend indicators
        if len(df) >= 20:
            ema_20 = ta.trend.EMAIndicator(df['close'], window=20, fillna=True)
            indicators['ema_20'] = float(ema_20.ema_indicator().iloc[-1])
        
        if len(df) >= 50:
            ema_50 = ta.trend.EMAIndicator(df['close'], window=50, fillna=True)
            indicators['ema_50'] = float(ema_50.ema_indicator().iloc[-1])
        
        # MACD
        macd = ta.trend.MACD(df['close'], fillna=True)
        indicators['macd'] = float(macd.macd().iloc[-1])
        indicators['macd_signal'] = float(macd.macd_signal().iloc[-1])
        
        # Momentum indicators
        if len(df) >= 14:
            rsi14 = ta.momentum.RSIIndicator(df['close'], window=14, fillna=True)
            indicators['rsi_14'] = float(rsi14.rsi().iloc[-1])
        if len(df) >= 7:
            rsi7 = ta.momentum.RSIIndicator(df['close'], window=7, fillna=True)
            indicators['rsi_7'] = float(rsi7.rsi().iloc[-1])
            
            stoch = ta.momentum.StochasticOscillator(
                df['high'], df['low'], df['close'], window=14, smooth_window=3, fillna=True
            )
            indicators['stoch_k'] = float(stoch.stoch().iloc[-1])
            indicators['stoch_d'] = float(stoch.stoch_signal().iloc[-1])
            
            willr = ta.momentum.WilliamsRIndicator(
                df['high'], df['low'], df['close'], lbp=14, fillna=True
            )
            indicators['willr'] = float(willr.williams_r().iloc[-1])
        
        # CCI
        cci = ta.trend.CCIIndicator(df['high'], df['low'], df['close'], window=20, fillna=True)
        indicators['cci'] = float(cci.cci().iloc[-1])
        
        # MFI (Money Flow Index) - requires volume
        if len(df) >= 14:
            mfi = ta.volume.MFIIndicator(
                df['high'], df['low'], df['close'], df['volume'], window=14, fillna=True
            )
            indicators['mfi'] = float(mfi.money_flow_index().iloc[-1])
        
        # OBV (On Balance Volume)
        obv = ta.volume.OnBalanceVolumeIndicator(df['close'], df['volume'], fillna=True)
        indicators['obv'] = float(obv.on_balance_volume().iloc[-1])
        
        # Volatility indicators
        if len(df) >= 14:
            atr14 = ta.volatility.AverageTrueRange(
                df['high'], df['low'], df['close'], window=14, fillna=True
            )
            indicators['atr_14'] = float(atr14.average_true_range().iloc[-1])
        if len(df) >= 3:
            atr3 = ta.volatility.AverageTrueRange(
                df['high'], df['low'], df['close'], window=3, fillna=True
            )
            indicators['atr_3'] = float(atr3.average_true_range().iloc[-1])
        
        # Bollinger Bands
        if len(df) >= 20:
            bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2, fillna=True)
            indicators['bb_upper'] = float(bb.bollinger_hband().iloc[-1])
            indicators['bb_middle'] = float(bb.bollinger_mavg().iloc[-1])
            indicators['bb_lower'] = float(bb.bollinger_lband().iloc[-1])

        # Parabolic SAR (PSAR)
        try:
            psar = ta.trend.PSARIndicator(
                high=df['high'], low=df['low'], close=df['close'], step=0.02, max_step=0.2, fillna=True
            )
            indicators['sar'] = float(psar.psar().iloc[-1])
        except Exception:
            # If PSAR not available in current ta version or data insufficient
            indicators['sar'] = 0.0
        
        # Volume indicators
        if len(df) >= 20:
            vwap = ta.volume.VolumeWeightedAveragePrice(
                df['high'], df['low'], df['close'], df['volume'], window=20, fillna=True
            )
            indicators['vwap_20'] = float(vwap.volume_weighted_average_price().iloc[-1])
        
        logger.info("✅ Binance indicator calculation: close=%.2f rsi=%.2f macd=%.4f", 
                   indicators.get('close', 0), 
                   indicators.get('rsi_14', 50),
                   indicators.get('macd', 0))
        
        return indicators
        
    except Exception as e:
        logger.error("Failed to calculate indicators from klines: %s", e, exc_info=True)
        # Even on error, try to return at least basic OHLCV if available
        if klines and len(klines) >= 1:
            try:
                df = pd.DataFrame(klines)
                latest = df.iloc[-1]
                return {
                    'close': float(latest['close']),
                    'high': float(latest['high']),
                    'low': float(latest['low']),
                    'volume': float(latest['volume']),
                }
            except Exception:
                pass
        return {}


async def fetch_from_influxdb_fallback(
    symbol: str,
    interval: str,
) -> Dict[str, Any]:
    """
    Fallback: Fetch latest indicators from InfluxDB when REST API fails
    
    Args:
        symbol: Trading symbol
        interval: Time interval (1m, 30min, 4h)
    
    Returns:
        Dict with indicators from InfluxDB
    """
    try:
        from app.utils.influx import query_latest_snapshot
        
        snapshot = query_latest_snapshot(f"enriched_{interval}", symbol, interval)
        if snapshot:
            logger.info(
                "✅ InfluxDB fallback: Retrieved %d indicators for %s/%s",
                len(snapshot),
                symbol,
                interval,
            )
            return snapshot
        else:
            logger.warning("InfluxDB fallback: No data found for %s/%s", symbol, interval)
            return {}
    except Exception as e:
        logger.error("InfluxDB fallback failed: %s", e)
        return {}


async def aggregate_enriched_data(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    klines_cache: List[Dict] = None,
    trace_id: str = None,
) -> tuple[Dict[str, Any], List[Dict]]:
    """
    Aggregate data from Binance sources with multi-level fallback
    
    Strategy:
    1. PRIMARY: Binance REST API klines (with rolling window optimization)
    2. FALLBACK 1: InfluxDB cached data (if REST API fails)
    3. Calculate all indicators from OHLCV (RSI, MACD, EMA, BB, Stoch, etc.)
    4. Fetch Binance Futures metrics (L/S ratio, Open Interest, Funding Rate)
    
    Fallback Mechanism:
    - If WebSocket disconnects, system queries at least 4h and 30m data from Binance REST API
    - If REST API fails, falls back to InfluxDB
    - If InfluxDB fails, returns empty dict (system degraded but doesn't crash)
    
    Returns:
        Tuple of (enriched_data_dict, updated_klines_cache)
    """
    enriched = {}
    klines = []
    
    # Convert interval format for Binance API (30min → 30m, 1m → 1m, 4h → 4h)
    binance_interval = interval.replace("min", "m")
    
    # 1. PRIMARY SOURCE: Binance REST API klines (optimized with rolling window)
    logger.info("Fetching data from Binance REST API (primary source)...")
    try:
        # OPTIMIZATION: Use cache for rolling window
        # Cache size: 200 bars to ensure we always have enough for indicator calculation (need 50+)
        CACHE_SIZE = 200
        if klines_cache and len(klines_cache) >= CACHE_SIZE:
            # We have cached data, fetch only the latest bar
            new_klines = await fetch_binance_klines(symbol, interval=binance_interval, limit=1)
            
            if new_klines and new_klines[0]['timestamp'] > klines_cache[-1]['timestamp']:
                # New bar available, update rolling window
                klines = klines_cache[1:] + new_klines  # Remove oldest, add newest
                logger.info("✅ Rolling window: Added 1 new bar, keeping last %d", CACHE_SIZE)
            else:
                # No new bar yet, use cached data
                klines = klines_cache
                logger.info("⏳ No new bar yet, using cached data (%d bars)", len(klines_cache))
        else:
            # First time or cache invalid, fetch full 200 bars to ensure sufficient data
            klines = await fetch_binance_klines(symbol, interval=binance_interval, limit=200)
            logger.info("🔄 Initial fetch: Loaded 200 bars from REST API (sufficient for all indicators)")
        
        if klines:
            # Calculate all possible indicators from Binance OHLCV
            binance_indicators = calculate_indicators_from_klines(klines)
            
            if binance_indicators:
                enriched.update(binance_indicators)
                
                # LATENCY TRACKING: Stage 2d - TA Calculation Complete
                if trace_id:
                    tracker = get_latency_tracker()
                    trace = tracker.get_trace(trace_id)
                    if trace:
                        trace.mark_ta_complete()
                
                logger.info(
                    "✅ Binance REST API: %d indicators calculated (close=%.2f rsi=%.2f)",
                    len(binance_indicators),
                    binance_indicators.get('close', 0),
                    binance_indicators.get('rsi_14', 50)
                )
            else:
                logger.warning("Binance indicator calculation returned empty")
        else:
            logger.warning("Binance REST API klines fetch returned empty")
    except Exception as exc:
        logger.error("Binance REST API failed: %s", exc, exc_info=True)
    
    # 2. FALLBACK: InfluxDB cached data
    if 'close' not in enriched:
        logger.warning("⚠️ Binance REST API failed, trying InfluxDB fallback...")
        influx_data = await fetch_from_influxdb_fallback(symbol, interval)
        if influx_data:
            enriched.update(influx_data)
            logger.info("✅ InfluxDB fallback successful: %d indicators", len(influx_data))
        else:
            logger.error(
                "🛑 CRITICAL: Both Binance REST API and InfluxDB failed for %s/%s! System degraded.",
                symbol,
                interval,
            )
            return enriched, klines
    
    # 2. Get Binance Futures metrics (L/S ratio, Open Interest, Funding Rate)
    try:
        futures_client = BinanceFuturesClient()
        futures_snapshot = await futures_client.fetch_metrics(symbol)
        enriched.update({
            'long_short_ratio': futures_snapshot.long_short_ratio,
            'open_interest': futures_snapshot.open_interest,
            'funding_rate': futures_snapshot.funding_rate,
        })
    except Exception as exc:
        logger.warning("Could not fetch futures metrics: %s", exc)
    
    return enriched, klines  # Return klines for caching


async def write_enriched_feed(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    poll_interval: int = 60,
) -> None:
    """
    Continuously aggregate and write enriched data with multi-level fallback
    
    Data Sources (in priority order):
    1. Binance REST API (primary)
    2. InfluxDB cached data (fallback)
    
    Resilience Features:
    - Rolling window optimization for REST API calls
    - Automatic fallback to InfluxDB if REST API fails
    - Continues operation even if both sources temporarily fail
    
    Args:
        symbol: Binance symbol
        interval: Time interval (1m, 30min, 4h)
        poll_interval: Seconds between aggregations
    """
    logger.info(
        "Starting enriched feed: symbol=%s interval=%s poll=%ds (Binance REST + InfluxDB fallback)",
        symbol, interval, poll_interval
    )
    
    klines_cache = None  # Cache for rolling window
    
    # IMMEDIATE FIRST RUN: Write data immediately on startup
    logger.info("🚀 Immediate first run - collecting and writing data...")
    try:
        # Generate trace_id for this aggregation cycle
        trace_id = f"{symbol}_{interval}_{int(datetime.utcnow().timestamp() * 1000)}"
        
        # Aggregate all data (with cache for optimization)
        enriched, klines_cache = await aggregate_enriched_data(symbol, interval, klines_cache, trace_id)
        
        # Ensure required fields are present
        enriched = ensure_required_fields(enriched, klines_cache if klines_cache else None)
        
        # Validate data completeness before writing
        is_valid, missing_fields = validate_enriched_data(enriched, interval)
        
        if not is_valid:
            logger.error(
                "❌ CRITICAL: Cannot write enriched data - missing required fields: %s",
                ", ".join(missing_fields)
            )
            logger.error("⚠️ Skipping write - will retry in next cycle")
        elif enriched:
            # Write to InfluxDB
            timestamp = datetime.utcnow()
            write_measurement(
                measurement=f"enriched_{interval}",
                tags={'symbol': symbol, 'interval': interval},
                fields=enriched,
                timestamp=timestamp,
            )
            
            # LATENCY TRACKING: Stage 3d - InfluxDB Write Complete
            tracker = get_latency_tracker()
            trace = tracker.get_trace(trace_id)
            if trace:
                trace.mark_influx_write()
                # Write latency metrics to InfluxDB
                tracker.write_latency_metrics(trace)
            
            # Log with latency info
            latency_info = ""
            if trace:
                latencies = trace.get_latencies()
                total_ms = latencies.get('total_e2e', 0) * 1000
                latency_info = f" | Latency: {total_ms:.0f}ms"
            
            logger.info(
                "✅ FIRST RUN SUCCESS: close=%.2f high=%.2f low=%.2f rsi=%.2f l/s_ratio=%.4f bb_position=%.2f%%%s",
                enriched.get('close', 0),
                enriched.get('high', 0),
                enriched.get('low', 0),
                enriched.get('rsi_14', 50),
                enriched.get('long_short_ratio', 1.0),
                calculate_bb_position(enriched) * 100,
                latency_info,
            )
        else:
            logger.warning("⚠️ FIRST RUN FAILED: No enriched data collected")
    except Exception as exc:
        logger.error("❌ FIRST RUN ERROR: %s", exc, exc_info=True)
    
    # Now enter the polling loop
    logger.info("Entering polling loop (interval: %ds)...", poll_interval)
    
    while True:
        try:
            # Wait before next collection
            await asyncio.sleep(poll_interval)
            
            # Generate trace_id for this aggregation cycle
            trace_id = f"{symbol}_{interval}_{int(datetime.utcnow().timestamp() * 1000)}"
            
            # Aggregate all data (with cache for optimization)
            enriched, klines_cache = await aggregate_enriched_data(symbol, interval, klines_cache, trace_id)
            
            # Ensure required fields are present
            enriched = ensure_required_fields(enriched, klines_cache if klines_cache else None)
            
            # Validate data completeness before writing
            is_valid, missing_fields = validate_enriched_data(enriched, interval)
            
            if not enriched:
                logger.warning("No enriched data collected")
                continue
            
            if not is_valid:
                logger.error(
                    "❌ CRITICAL: Cannot write enriched data - missing required fields: %s",
                    ", ".join(missing_fields)
                )
                logger.error("⚠️ Skipping write - will retry in next cycle")
                continue
            
            # Write to InfluxDB
            timestamp = datetime.utcnow()
            write_measurement(
                measurement=f"enriched_{interval}",
                tags={'symbol': symbol, 'interval': interval},
                fields=enriched,
                timestamp=timestamp,
            )
            
            # LATENCY TRACKING: Stage 3d - InfluxDB Write Complete
            tracker = get_latency_tracker()
            trace = tracker.get_trace(trace_id)
            if trace:
                trace.mark_influx_write()
                # Write latency metrics to InfluxDB
                tracker.write_latency_metrics(trace)
            
            # Log with latency info
            latency_info = ""
            if trace:
                latencies = trace.get_latencies()
                total_ms = latencies.get('total_e2e', 0) * 1000
                latency_info = f" | Latency: {total_ms:.0f}ms"
            
            logger.info(
                "✅ Wrote enriched data: close=%.2f high=%.2f low=%.2f rsi=%.2f l/s_ratio=%.4f bb_position=%.2f%%%s",
                enriched.get('close', 0),
                enriched.get('high', 0),
                enriched.get('low', 0),
                enriched.get('rsi_14', 50),
                enriched.get('long_short_ratio', 1.0),
                calculate_bb_position(enriched) * 100,
                latency_info,
            )
            
        except Exception as exc:
            logger.error("Enriched feed error: %s", exc, exc_info=True)


def validate_enriched_data(enriched: Dict[str, Any], interval: str) -> tuple[bool, List[str]]:
    """
    Validate that enriched data contains all required fields
    
    Args:
        enriched: Enriched data dictionary
        interval: Time interval (used to determine required fields)
    
    Returns:
        Tuple of (is_valid, missing_fields_list)
    """
    # Basic OHLCV fields are ALWAYS required
    required_fields = ['close', 'high', 'low', 'volume']
    
    missing_fields = []
    for field in required_fields:
        if field not in enriched:
            missing_fields.append(f"{field} (missing)")
        elif enriched.get(field) is None:
            missing_fields.append(f"{field} (None)")
    
    is_valid = len(missing_fields) == 0
    
    if not is_valid:
        logger.error(
            "❌ CRITICAL: Enriched data missing required fields: %s",
            ", ".join(missing_fields)
        )
    
    return is_valid, missing_fields


def ensure_required_fields(enriched: Dict[str, Any], klines: List[Dict] = None) -> Dict[str, Any]:
    """
    Ensure that required OHLCV fields are present in enriched data.
    If missing, try to fill from klines if available.
    
    Args:
        enriched: Enriched data dictionary
        klines: Optional klines list to extract OHLCV from
    
    Returns:
        Enriched data with guaranteed required fields
    """
    required_fields = ['close', 'high', 'low', 'volume']
    missing_fields = [f for f in required_fields if f not in enriched or enriched.get(f) is None]
    
    if missing_fields and klines and len(klines) >= 1:
        try:
            import pandas as pd
            df = pd.DataFrame(klines)
            latest = df.iloc[-1]
            
            for field in missing_fields:
                if field in latest:
                    enriched[field] = float(latest[field])
                    logger.info("✅ Filled missing field '%s' from klines: %.2f", field, enriched[field])
        except Exception as e:
            logger.warning("Could not fill missing fields from klines: %s", e)
    
    return enriched


def calculate_bb_position(data: Dict[str, Any]) -> float:
    """
    Calculate position within Bollinger Bands (0-1)
    0 = at lower band, 0.5 = at middle, 1 = at upper band
    """
    close = data.get('close', 0)
    bb_lower = data.get('bb_lower', 0)
    bb_upper = data.get('bb_upper', 0)
    
    if bb_upper <= bb_lower or close <= 0:
        return 0.5
    
    position = (close - bb_lower) / (bb_upper - bb_lower)
    return max(0.0, min(1.0, position))


async def main():
    """Entry point for standalone execution"""
    import argparse
    from app.utils.logging import configure_logging
    
    parser = argparse.ArgumentParser(description='Enriched data feed aggregator - Binance only')
    parser.add_argument('--symbol', default='BTCUSDT', help='Binance symbol')
    parser.add_argument('--interval', default='1m', help='Time interval (1m, 30min, 4h)')
    parser.add_argument('--poll-interval', type=int, default=60, help='Poll interval (seconds)')
    
    args = parser.parse_args()
    
    # Configure logging
    configure_logging("INFO")
    logger.info("=" * 60)
    logger.info("Starting Enriched Feed Service")
    logger.info("Symbol: %s | Interval: %s | Poll: %ds", args.symbol, args.interval, args.poll_interval)
    logger.info("=" * 60)
    
    await write_enriched_feed(
        symbol=args.symbol,
        interval=args.interval,
        poll_interval=args.poll_interval,
    )


if __name__ == '__main__':
    asyncio.run(main())
