#!/usr/bin/env python3
"""
Export historical enriched_30min data for ML model training (20 features, no TwelveData)
"""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.utils.influx import _ensure_client, _query_api
from app.utils.logging import configure_logging, get_logger


configure_logging("INFO")
logger = get_logger(__name__)


def export_historical_data(
    symbol: str = "BTCUSDT",
    interval: str = "30min",
    days: int = 30,
    output: Path = Path("data/derivatives_20features.csv"),
) -> pd.DataFrame:
    """
    Export historical enriched data from InfluxDB
    
    Args:
        symbol: Trading symbol
        interval: Time interval
        days: Number of days to export
        output: Output CSV file path
    
    Returns:
        DataFrame with historical data
    """
    logger.info("=" * 60)
    logger.info("EXPORTING HISTORICAL DATA FOR ML TRAINING")
    logger.info("=" * 60)
    logger.info(f"Symbol: {symbol}")
    logger.info(f"Interval: {interval}")
    logger.info(f"Days: {days}")
    logger.info(f"Output: {output}")
    
    # Calculate time range
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    
    logger.info(f"Time range: {start} to {end}")
    
    # Query InfluxDB
    from app.config.settings import get_settings
    settings = get_settings()
    
    _ensure_client()
    
    # Import query_api after client initialization
    from app.utils.influx import _query_api
    
    measurement = f"enriched_{interval}"
    
    # Flux query to get all data with pivot
    query = f"""
from(bucket: "{settings.influx.bucket}")
  |> range(start: {start.strftime('%Y-%m-%dT%H:%M:%SZ')}, stop: {end.strftime('%Y-%m-%dT%H:%M:%SZ')})
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> filter(fn: (r) => r["symbol"] == "{symbol}")
  |> filter(fn: (r) => r["interval"] == "{interval}")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: false)
    """
    
    logger.info("Querying InfluxDB...")
    try:
        if _query_api is None:
            logger.error("InfluxDB query API not initialized")
            return pd.DataFrame()
        
        tables = _query_api.query(query)
        
        if not tables:
            logger.error("No data found in InfluxDB")
            return pd.DataFrame()
        
        # Extract records
        records = []
        for table in tables:
            for record in table.records:
                row = {"timestamp": record.get_time()}
                # Extract all fields
                for key, value in record.values.items():
                    if not key.startswith("_") and key not in ("result", "table", "symbol", "interval"):
                        row[key] = value
                records.append(row)
        
        logger.info(f"✅ Retrieved {len(records)} records from InfluxDB")
        
        # Create DataFrame
        df = pd.DataFrame(records)
        
        # Define 20 features (no TwelveData)
        REQUIRED_FEATURES = [
            # Futures (3)
            "long_short_ratio",
            "open_interest",
            "funding_rate",
            
            # Binance Spot (17)
            "close",
            "ema_20",
            "ema_50",
            "rsi_14",
            "macd",
            "macd_signal",
            "atr_14",
            "stoch_k",
            "stoch_d",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "willr",
            "cci",
            "mfi",
            "obv",
            "vwap_20",
        ]
        
        # Check which features exist
        available_features = [f for f in REQUIRED_FEATURES if f in df.columns]
        missing_features = [f for f in REQUIRED_FEATURES if f not in df.columns]
        
        logger.info(f"Available features: {len(available_features)}/20")
        if missing_features:
            logger.warning(f"Missing features: {missing_features}")
        
        # Keep only required features + timestamp
        columns_to_keep = ["timestamp"] + available_features
        df = df[columns_to_keep]
        
        # Drop rows with NaN values
        before_drop = len(df)
        df = df.dropna()
        after_drop = len(df)
        
        if before_drop > after_drop:
            logger.info(f"Dropped {before_drop - after_drop} rows with NaN values")
        
        logger.info(f"Final dataset: {len(df)} rows x {len(df.columns)} columns")
        
        # Save to CSV
        output.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output, index=False)
        logger.info(f"✅ Saved to {output}")
        
        # Show statistics
        logger.info("\n" + "=" * 60)
        logger.info("DATASET STATISTICS")
        logger.info("=" * 60)
        logger.info(f"Total records: {len(df)}")
        logger.info(f"Time span: {df['timestamp'].min()} to {df['timestamp'].max()}")
        logger.info(f"Features: {len(available_features)}")
        
        # Show sample
        logger.info("\nFirst 5 rows:")
        logger.info(df.head().to_string())
        
        logger.info("\nFeature statistics:")
        logger.info(df[available_features].describe().to_string())
        
        return df
        
    except Exception as exc:
        logger.error(f"Failed to export data: {exc}", exc_info=True)
        return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="Export historical data for ML training")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--interval", default="30min", help="Time interval")
    parser.add_argument("--days", type=int, default=30, help="Number of days to export")
    parser.add_argument("--output", default="data/derivatives_20features.csv", help="Output CSV file")
    
    args = parser.parse_args()
    
    df = export_historical_data(
        symbol=args.symbol,
        interval=args.interval,
        days=args.days,
        output=Path(args.output),
    )
    
    if df.empty:
        logger.error("Export failed!")
        exit(1)
    else:
        logger.info("Export completed successfully!")
        exit(0)


if __name__ == "__main__":
    main()
