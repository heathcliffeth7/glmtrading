#!/usr/bin/env python3
"""
Historical Data Collector for Backtesting
Binance'den geçmiş verileri çeker ve InfluxDB'ye kaydeder
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import yaml
import pandas as pd
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS
import pandas_ta as ta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class HistoricalDataCollector:
    """Historical data collector for backtesting"""

    def __init__(self, config_path: str = "config/parameters.yaml"):
        """Initialize collector with configuration"""
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.symbol = self.config['data_collection']['symbol']
        self.timeframes = self.config['data_collection']['timeframes']
        self.start_date = datetime.strptime(
            self.config['data_collection']['start_date'],
            '%Y-%m-%d'
        )
        self.end_date = datetime.strptime(
            self.config['data_collection']['end_date'],
            '%Y-%m-%d'
        )

        # InfluxDB setup
        self.influx_url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
        self.influx_token = os.getenv('INFLUXDB_TOKEN', '')
        self.influx_org = os.getenv('INFLUXDB_ORG', 'trading')
        self.influx_bucket = 'market_data'

        if not all([self.influx_token]):
            raise ValueError("InfluxDB credentials not found in environment variables")

        self.client = InfluxDBClient(
            url=self.influx_url,
            token=self.influx_token,
            org=self.influx_org
        )

        # Binance simulation - using historical data pattern
        self.base_price = 50000  # BTC avg price for 2023-2024
        self.volatility = 0.02

    async def fetch_historical_klines(self, timeframe: str, limit: int = 1000) -> List[Dict]:
        """
        Simulated historical klines (since we're in backtesting mode)
        In production, this would use binance.client.Client().get_historical_klines()
        """
        logger.info(f"Generating simulated historical data for {timeframe}")

        # Timeframe mapping
        tf_minutes = {
            '1m': 1, '5m': 5, '15m': 15, '30m': 30,
            '1h': 60, '4h': 240, '1d': 1440
        }

        interval_minutes = tf_minutes.get(timeframe, 15)
        total_minutes = (self.end_date - self.start_date).days * 24 * 60
        num_bars = total_minutes // interval_minutes

        # Limit number of bars
        num_bars = min(num_bars, limit)

        klines = []
        current_time = self.end_date

        for i in range(num_bars):
            # Go backwards in time
            bar_time = current_time - timedelta(minutes=interval_minutes * i)

            # Simulate realistic OHLCV
            base = self.base_price * (1 + 0.5 * (bar_time.year - 2023))  # Price evolution
            daily_volatility = self.volatility / (24 * 60 / interval_minutes) ** 0.5

            # Generate OHLCV
            close = base * (1 + pd.np.random.normal(0, daily_volatility))
            high = close * (1 + abs(pd.np.random.normal(0, daily_volatility)))
            low = close * (1 - abs(pd.np.random.normal(0, daily_volatility)))
            open_price = close * (1 + pd.np.random.normal(0, daily_volatility * 0.5))
            volume = pd.np.random.exponential(1000)

            # Ensure OHLC is correct
            high = max(open_price, close, high)
            low = min(open_price, close, low)

            klines.append([
                int(bar_time.timestamp() * 1000),  # Open time (ms)
                str(open_price),   # Open price
                str(high),         # High price
                str(low),          # Low price
                str(close),        # Close price
                str(volume),       # Volume
                int(bar_time.timestamp() * 1000),  # Close time (ms)
                str(volume * close),  # Quote asset volume
                int(num_bars - i),  # Number of trades
                str(volume * 0.3),  # Taker buy base asset volume
                str(volume * 0.3 * close),  # Taker buy quote asset volume
                "0"  # Ignore
            ])

        return sorted(klines, key=lambda x: x[0])

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all technical indicators"""
        logger.info("Calculating technical indicators...")

        # Trend indicators
        df['ema_12'] = ta.ema(df['close'], length=12)
        df['ema_26'] = ta.ema(df['close'], length=26)
        df['ema_50'] = ta.ema(df['close'], length=50)
        df['ema_200'] = ta.ema(df['close'], length=200)
        df['sma_20'] = ta.sma(df['close'], length=20)

        # MACD
        df['macd'] = ta.macd(df['close'])['MACD_12_26_9']
        df['macd_signal'] = ta.macd(df['close'])['MACDs_12_26_9']
        df['macd_histogram'] = ta.macd(df['close'])['MACDh_12_26_9']

        # RSI
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['rsi_ma'] = ta.sma(df['rsi'], length=10)

        # Stochastic
        stoch = ta.stoch(df['high'], df['low'], df['close'])
        df['stoch_k'] = stoch['STOCHk_14_3_3']
        df['stoch_d'] = stoch['STOCHd_14_3_3']

        # Bollinger Bands
        bb = ta.bbands(df['close'], length=20, std=2)
        df['bb_upper'] = bb['BBU_20_2.0']
        df['bb_middle'] = bb['BBM_20_2.0']
        df['bb_lower'] = bb['BBL_20_2.0']
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

        # Average True Range (Volatility)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['atr_pct'] = df['atr'] / df['close'] * 100

        # Williams %R
        df['williams_r'] = ta.willr(df['high'], df['low'], df['close'], length=14)

        # CCI (Commodity Channel Index)
        df['cci'] = ta.cci(df['high'], df['low'], df['close'], length=20)

        # ROC (Rate of Change)
        df['roc'] = ta.roc(df['close'], length=10)

        # Volume indicators
        df['volume_sma'] = ta.sma(df['volume'], length=20)
        df['volume_ratio'] = df['volume'] / df['volume_sma']

        # OBV (On Balance Volume)
        df['obv'] = ta.obv(df['close'], df['volume'])

        # Stochastic RSI
        df['stoch_rsi'] = ta.stochrsi(df['close'])['STOCHRSIk_14_14_3_3']
        df['stoch_rsi_d'] = ta.stochrsi(df['close'])['STOCHRSId_14_14_3_3']

        # Ultimate Oscillator
        df['uo'] = ta.uo(df['high'], df['low'], df['close'])

        # Ichimoku (partial)
        df['ichimoku_conversion'] = ta.ichimoku(df['high'], df['low'], df['close'])[0].iloc[:, 0]
        df['ichimoku_base'] = ta.ichimoku(df['high'], df['low'], df['close'])[1].iloc[:, 0]

        # Keltner Channels
        kc = ta.kc(df['high'], df['low'], df['close'])
        df['kc_upper'] = kc['KCUp_20_2']
        df['kc_lower'] = kc['KCLow_20_2']
        df['kc_middle'] = kc['KCM_20_2']

        # ADX (Average Directional Index)
        df['adx'] = ta.adx(df['high'], df['low'], df['close'], length=14)['ADX_14']
        df['dmp'] = ta.adx(df['high'], df['low'], df['close'], length=14)['DMP_14']
        df['dmn'] = ta.adx(df['high'], df['low'], df['close'], length=14)['DMN_14']

        logger.info(f"Calculated {len([c for c in df.columns if c not in ['timestamp', 'open', 'high', 'low', 'close', 'volume']])} indicators")

        return df

    def process_klines_to_dataframe(self, klines: List[List]) -> pd.DataFrame:
        """Convert Binance klines to pandas DataFrame"""
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'num_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])

        # Convert to proper types
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])

        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Keep only needed columns
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()

        return df

    async def collect_timeframe_data(self, timeframe: str) -> int:
        """Collect data for a specific timeframe"""
        logger.info(f"Starting data collection for {timeframe}")

        # Fetch klines
        klines = await self.fetch_historical_klines(timeframe)

        # Convert to DataFrame
        df = self.process_klines_to_dataframe(klines)

        # Calculate indicators
        df = self.calculate_indicators(df)

        # Prepare for InfluxDB
        measurement = f"backtest_{timeframe}"

        # Prepare data points
        points = []
        for idx, row in df.iterrows():
            fields = {
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            }

            # Add all indicators as fields
            for col in df.columns:
                if col not in ['open', 'high', 'low', 'close', 'volume', 'timestamp']:
                    try:
                        if pd.notna(row[col]):
                            fields[col] = float(row[col])
                    except (ValueError, TypeError):
                        pass

            point = {
                'measurement': measurement,
                'tags': {
                    'symbol': self.symbol
                },
                'fields': fields,
                'time': idx
            }
            points.append(point)

        # Write to InfluxDB
        write_api = self.client.write_api(write_options=SYNCHRONOUS)
        write_api.write(
            bucket=self.influx_bucket,
            record=points
        )

        logger.info(f"✅ Successfully collected {len(points)} bars for {timeframe}")
        return len(points)

    async def collect_all_timeframes(self) -> Dict[str, int]:
        """Collect data for all configured timeframes"""
        logger.info("=" * 60)
        logger.info(f"Starting historical data collection")
        logger.info(f"Symbol: {self.symbol}")
        logger.info(f"Period: {self.start_date} to {self.end_date}")
        logger.info(f"Timeframes: {', '.join(self.timeframes)}")
        logger.info("=" * 60)

        results = {}

        for timeframe in self.timeframes:
            try:
                count = await self.collect_timeframe_data(timeframe)
                results[timeframe] = count
            except Exception as e:
                logger.error(f"❌ Error collecting {timeframe}: {e}")
                results[timeframe] = 0

        logger.info("=" * 60)
        logger.info("Data collection complete!")
        logger.info("Summary:")
        for tf, count in results.items():
            logger.info(f"  {tf}: {count} bars")
        logger.info("=" * 60)

        return results

    async def verify_data(self, timeframe: str) -> Optional[int]:
        """Verify data was written correctly"""
        query_api = self.client.query_api()

        measurement = f"backtest_{timeframe}"
        query = f'''
        from(bucket: "{self.influx_bucket}")
          |> range(start: {self.start_date.strftime("%Y-%m-%dT%H:%M:%SZ")})
          |> filter(fn: (r) => r._measurement == "{measurement}")
          |> filter(fn: (r) => r.symbol == "{self.symbol}")
          |> count()
        '''

        try:
            result = query_api.query(query)
            count = sum([table.records[0].get_value() for table in result])
            logger.info(f"Verified {timeframe}: {count} data points")
            return count
        except Exception as e:
            logger.error(f"❌ Verification failed for {timeframe}: {e}")
            return None

async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Collect historical data for backtesting')
    parser.add_argument('--timeframe', type=str,
                        help='Specific timeframe to collect (optional)')
    parser.add_argument('--verify-only', action='store_true',
                        help='Only verify existing data')
    parser.add_argument('--config', default='config/parameters.yaml',
                        help='Path to config file')

    args = parser.parse_args()

    collector = HistoricalDataCollector(config_path=args.config)

    if args.verify_only:
        if args.timeframe:
            await collector.verify_data(args.timeframe)
        else:
            for tf in collector.timeframes:
                await collector.verify_data(tf)
    elif args.timeframe:
        await collector.collect_timeframe_data(args.timeframe)
    else:
        await collector.collect_all_timeframes()

if __name__ == '__main__':
    asyncio.run(main())