#!/usr/bin/env python3
"""
Continuous feature synchronization from TwelveData
Runs as a background service to keep InfluxDB updated
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import ta

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.settings import get_settings
from app.data_feeds.twelve_data import TwelveDataClient
from app.utils.influx import query_latest_snapshot, write_measurement
from app.utils.logging import configure_logging, get_logger


settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


def calculate_features_incremental(
    latest_bars: list,
    symbol: str,
    interval: str,
) -> list[dict]:
    """
    Calculate features for the latest bars only
    
    Args:
        latest_bars: List of recent bars from TwelveData
        symbol: Trading symbol
        interval: Time interval
    
    Returns:
        List of dicts with features for each bar
    """
    if not latest_bars:
        return []
    
    # Get the last 50 bars from InfluxDB to have enough context for TA
    try:
        from app.utils.influx import query_range
        historical = query_range(
            f"features_{interval}",
            symbol,
            interval,
            minutes=250  # ~50 bars at 5min interval
        )
        
        # Convert to DataFrame
        if historical:
            hist_df = pd.DataFrame(historical)
            hist_df = hist_df.pivot(index='timestamp', columns='field', values='value')
            hist_df = hist_df.reset_index()
            # Convert timestamp strings to datetime
            if 'timestamp' in hist_df.columns:
                hist_df['timestamp'] = pd.to_datetime(hist_df['timestamp'])
        else:
            hist_df = pd.DataFrame()
            
    except Exception as exc:
        logger.warning("Could not fetch historical context: %s", exc)
        hist_df = pd.DataFrame()
    
    # Convert latest bars to DataFrame
    new_df = pd.DataFrame([
        {
            'timestamp': bar.timestamp,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume,
        }
        for bar in latest_bars
    ])
    
    # Combine historical + new
    if not hist_df.empty:
        combined = pd.concat([hist_df, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=['timestamp'], keep='last')
        combined = combined.sort_values('timestamp')
    else:
        combined = new_df
    
    # Calculate features on combined data
    try:
        combined['ema_20'] = ta.trend.EMAIndicator(
            combined['close'], window=20, fillna=True
        ).ema_indicator()
        
        combined['ema_50'] = ta.trend.EMAIndicator(
            combined['close'], window=50, fillna=True
        ).ema_indicator()
        
        combined['rsi_14'] = ta.momentum.RSIIndicator(
            combined['close'], window=14, fillna=True
        ).rsi()
        
        macd = ta.trend.MACD(combined['close'], fillna=True)
        combined['macd'] = macd.macd()
        combined['macd_signal'] = macd.macd_signal()
        
        combined['atr_14'] = ta.volatility.AverageTrueRange(
            combined['high'], combined['low'], combined['close'], window=14, fillna=True
        ).average_true_range()
        
        combined['vwap_20'] = ta.volume.VolumeWeightedAveragePrice(
            combined['high'], combined['low'], combined['close'], 
            combined['volume'], window=20, fillna=True
        ).volume_weighted_average_price()
        
    except Exception as exc:
        logger.error("Error calculating features: %s", exc)
        return []
    
    # Extract only the new bars with features
    new_timestamps = set(new_df['timestamp'])
    result_df = combined[combined['timestamp'].isin(new_timestamps)]
    
    return result_df.to_dict('records')


async def sync_loop(
    symbol: str = "BTC/USD",
    binance_symbol: str = "BTCUSDT",
    interval: str = "5min",
    poll_interval: int = 60,
) -> None:
    """
    Continuously sync features from TwelveData to InfluxDB
    
    Args:
        symbol: TwelveData symbol
        binance_symbol: Binance symbol for tagging
        interval: Time interval
        poll_interval: Seconds between polls
    """
    logger.info("Starting feature sync loop: symbol=%s interval=%s poll_interval=%ds",
                symbol, interval, poll_interval)
    
    client = TwelveDataClient(settings.twelve_data.api_keys)
    last_timestamp = None
    
    # Get the latest timestamp from InfluxDB
    try:
        snapshot = query_latest_snapshot(f"features_{interval}", binance_symbol, interval)
        if snapshot and 'timestamp' in snapshot:
            last_timestamp = datetime.fromisoformat(snapshot['timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
            logger.info("Resuming from last timestamp: %s", last_timestamp)
    except Exception as exc:
        logger.warning("Could not get last timestamp: %s", exc)
    
    while True:
        try:
            # Fetch latest bars
            bars = await client.fetch_time_series(
                symbol=symbol,
                interval=interval,
                outputsize=10,  # Get last 10 bars
            )
            
            if not bars:
                logger.warning("No bars received from TwelveData")
                await asyncio.sleep(poll_interval)
                continue
            
            # Filter for new bars only
            if last_timestamp:
                # Make sure both timestamps are timezone-naive for comparison
                new_bars = []
                for bar in bars:
                    bar_ts = bar.timestamp
                    if hasattr(bar_ts, 'tzinfo') and bar_ts.tzinfo:
                        bar_ts = bar_ts.replace(tzinfo=None)
                    last_ts = last_timestamp
                    if hasattr(last_ts, 'tzinfo') and last_ts.tzinfo:
                        last_ts = last_ts.replace(tzinfo=None)
                    if bar_ts > last_ts:
                        new_bars.append(bar)
            else:
                new_bars = bars
            
            if not new_bars:
                logger.debug("No new bars since %s", last_timestamp)
                await asyncio.sleep(poll_interval)
                continue
            
            logger.info("Processing %d new bars", len(new_bars))
            
            # Calculate features
            bars_with_features = calculate_features_incremental(
                new_bars, binance_symbol, interval
            )
            
            # Write to InfluxDB
            measurement = f"features_{interval}"
            for bar_dict in bars_with_features:
                try:
                    timestamp = bar_dict['timestamp']
                    if isinstance(timestamp, str):
                        timestamp = datetime.fromisoformat(timestamp)
                    
                    fields = {
                        'open': float(bar_dict['open']),
                        'high': float(bar_dict['high']),
                        'low': float(bar_dict['low']),
                        'close': float(bar_dict['close']),
                        'volume': float(bar_dict['volume']),
                        'ema_20': float(bar_dict['ema_20']),
                        'ema_50': float(bar_dict['ema_50']),
                        'rsi_14': float(bar_dict['rsi_14']),
                        'macd': float(bar_dict['macd']),
                        'macd_signal': float(bar_dict['macd_signal']),
                        'atr_14': float(bar_dict['atr_14']),
                        'vwap_20': float(bar_dict['vwap_20']),
                    }
                    
                    write_measurement(
                        measurement=measurement,
                        tags={'symbol': binance_symbol, 'interval': interval},
                        fields=fields,
                        timestamp=timestamp,
                    )
                    
                    last_timestamp = timestamp
                    logger.debug("Wrote bar: timestamp=%s close=%.2f", timestamp, fields['close'])
                    
                except Exception as exc:
                    logger.error("Error writing bar: %s", exc)
            
            logger.info("Sync cycle completed, next in %d seconds", poll_interval)
            
        except Exception as exc:
            logger.error("Sync error: %s", exc, exc_info=True)
        
        await asyncio.sleep(poll_interval)


async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Continuously sync features from TwelveData')
    parser.add_argument('--symbol', default='BTC/USD', help='TwelveData symbol')
    parser.add_argument('--binance-symbol', default='BTCUSDT', help='Binance symbol for tagging')
    parser.add_argument('--interval', default='5min', help='Time interval')
    parser.add_argument('--poll-interval', type=int, default=60, help='Seconds between polls')
    
    args = parser.parse_args()
    
    await sync_loop(
        symbol=args.symbol,
        binance_symbol=args.binance_symbol,
        interval=args.interval,
        poll_interval=args.poll_interval,
    )


if __name__ == '__main__':
    asyncio.run(main())
