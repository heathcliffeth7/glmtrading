#!/usr/bin/env python3
"""
Historical data import from TwelveData to InfluxDB
Fills the feature_worker's window with initial data
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import ta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.settings import get_settings
from app.data_feeds.twelve_data import TwelveDataClient
from app.utils.influx import write_measurement
from app.utils.logging import configure_logging, get_logger


settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


def calculate_features(df: pd.DataFrame, min_periods: int = 50) -> pd.DataFrame:
    """
    Calculate technical indicators from OHLCV data
    
    Args:
        df: DataFrame with columns [open, high, low, close, volume, timestamp]
        min_periods: Minimum number of periods required for TA calculations
    
    Returns:
        DataFrame with added feature columns
    """
    if len(df) < min_periods:
        logger.warning("Insufficient data for TA calculation: %d < %d", len(df), min_periods)
        # Still calculate with available data
    
    df = df.copy()
    
    # Sort by timestamp
    df = df.sort_values('timestamp')
    
    try:
        # EMA
        df['ema_20'] = ta.trend.EMAIndicator(
            df['close'], window=20, fillna=True
        ).ema_indicator()
        
        df['ema_50'] = ta.trend.EMAIndicator(
            df['close'], window=50, fillna=True
        ).ema_indicator()
        
        # RSI
        df['rsi_14'] = ta.momentum.RSIIndicator(
            df['close'], window=14, fillna=True
        ).rsi()
        
        # MACD
        macd = ta.trend.MACD(df['close'], fillna=True)
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        
        # ATR
        df['atr_14'] = ta.volatility.AverageTrueRange(
            df['high'], df['low'], df['close'], window=14, fillna=True
        ).average_true_range()
        
        # VWAP
        df['vwap_20'] = ta.volume.VolumeWeightedAveragePrice(
            df['high'], df['low'], df['close'], df['volume'], window=20, fillna=True
        ).volume_weighted_average_price()
        
        logger.info("Features calculated successfully for %d bars", len(df))
        
    except Exception as exc:
        logger.error("Error calculating features: %s", exc)
        # Fill with fallback values
        for col in ['ema_20', 'ema_50', 'macd', 'macd_signal', 'atr_14', 'vwap_20']:
            if col not in df.columns:
                df[col] = df['close']
        if 'rsi_14' not in df.columns:
            df['rsi_14'] = 50.0
    
    return df


async def import_data(
    symbol: str = "BTC/USD",
    binance_symbol: str = "BTCUSDT",
    interval: str = "5min",
    outputsize: int = 200,
) -> None:
    """
    Import historical data from TwelveData and write to InfluxDB
    
    Args:
        symbol: TwelveData symbol (e.g., "BTC/USD")
        binance_symbol: Binance-compatible symbol for tagging (e.g., "BTCUSDT")
        interval: Time interval (1min, 5min, 15min, etc.)
        outputsize: Number of bars to fetch (max 5000, free tier: 800)
    """
    logger.info("Starting historical data import: symbol=%s interval=%s outputsize=%d", 
                symbol, interval, outputsize)
    
    client = TwelveDataClient(settings.twelve_data.api_keys)
    
    try:
        # Fetch historical bars
        bars = await client.fetch_time_series(
            symbol=symbol,
            interval=interval,
            outputsize=outputsize,
        )
        
        if not bars:
            logger.error("No data received from TwelveData")
            return
        
        logger.info("Fetched %d bars from TwelveData", len(bars))
        
        # Convert to DataFrame
        df = pd.DataFrame([
            {
                'timestamp': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            }
            for bar in bars
        ])
        
        # Calculate features
        df_with_features = calculate_features(df)
        
        # Write to InfluxDB
        measurement = f"features_{interval}"
        success_count = 0
        error_count = 0
        
        for _, row in df_with_features.iterrows():
            try:
                fields = {
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume']),
                    'ema_20': float(row['ema_20']),
                    'ema_50': float(row['ema_50']),
                    'rsi_14': float(row['rsi_14']),
                    'macd': float(row['macd']),
                    'macd_signal': float(row['macd_signal']),
                    'atr_14': float(row['atr_14']),
                    'vwap_20': float(row['vwap_20']),
                }
                
                write_measurement(
                    measurement=measurement,
                    tags={'symbol': binance_symbol, 'interval': interval},
                    fields=fields,
                    timestamp=row['timestamp'],
                )
                success_count += 1
                
            except Exception as exc:
                logger.error("Error writing row to InfluxDB: %s", exc)
                error_count += 1
        
        logger.info(
            "Import completed: %d successful, %d errors out of %d total bars",
            success_count, error_count, len(df_with_features)
        )
        
        # Log sample of latest data
        latest = df_with_features.iloc[-1]
        logger.info(
            "Latest data point: timestamp=%s close=%.2f ema_20=%.2f ema_50=%.2f rsi_14=%.2f",
            latest['timestamp'], latest['close'], latest['ema_20'], latest['ema_50'], latest['rsi_14']
        )
        
    except Exception as exc:
        logger.error("Import failed: %s", exc, exc_info=True)
        raise


async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Import historical data from TwelveData')
    parser.add_argument('--symbol', default='BTC/USD', help='TwelveData symbol (default: BTC/USD)')
    parser.add_argument('--binance-symbol', default='BTCUSDT', help='Binance symbol for tagging')
    parser.add_argument('--interval', default='5min', help='Time interval (default: 5min)')
    parser.add_argument('--outputsize', type=int, default=200, help='Number of bars to fetch')
    
    args = parser.parse_args()
    
    await import_data(
        symbol=args.symbol,
        binance_symbol=args.binance_symbol,
        interval=args.interval,
        outputsize=args.outputsize,
    )


if __name__ == '__main__':
    asyncio.run(main())
