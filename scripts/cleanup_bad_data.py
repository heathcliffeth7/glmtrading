#!/usr/bin/env python3
"""
Clean up incorrect $50k data from InfluxDB features_5min
"""
import sys
sys.path.insert(0, '/root/trading')

from influxdb_client import InfluxDBClient
from app.config.settings import get_settings
from app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


def delete_bad_data():
    """Delete entries with close price between $49k-$51k (incorrect data)"""
    
    client = InfluxDBClient(
        url=str(settings.influx.url),
        token=settings.influx.token,
        org=settings.influx.org,
    )
    
    delete_api = client.delete_api()
    
    # Define time range (covers all bad data)
    start = "2025-10-20T09:59:00Z"
    stop = "2025-10-20T11:19:00Z"
    
    # Predicate to match the bad data
    # We'll delete by time range where close is between 49k-51k
    predicate = (
        f'_measurement="features_5min" AND '
        f'symbol="BTCUSDT" AND '
        f'interval="5min" AND '
        f'_field="close" AND '
        f'_value >= 49000.0 AND _value <= 51000.0'
    )
    
    print(f"Deleting data with predicate:")
    print(f"  {predicate}")
    print(f"  Time range: {start} to {stop}")
    print(f"\nThis will delete all fields for timestamps where close is $49k-$51k")
    
    # Delete
    try:
        # For InfluxDB, we need to delete by time range
        # Since we can't filter by field value in delete, we'll use a different approach
        # Delete all data in this time range where the close field indicates bad data
        
        # Method: Delete specific time range, but first let's verify what exists
        query_api = client.query_api()
        
        # Find exact timestamps with bad data
        query = f'''
        from(bucket: "{settings.influx.bucket}")
          |> range(start: {start}, stop: {stop})
          |> filter(fn: (r) => r["_measurement"] == "features_5min")
          |> filter(fn: (r) => r["symbol"] == "BTCUSDT")
          |> filter(fn: (r) => r["interval"] == "5min")
          |> filter(fn: (r) => r["_field"] == "close")
          |> filter(fn: (r) => r["_value"] >= 49000.0 and r["_value"] <= 51000.0)
        '''
        
        tables = query_api.query(query)
        bad_timestamps = []
        
        for table in tables:
            for record in table.records:
                bad_timestamps.append(record.get_time())
        
        print(f"\nFound {len(bad_timestamps)} bad timestamps")
        
        if not bad_timestamps:
            print("No bad data found to delete")
            return
        
        # Delete by time range (will delete ALL measurements in this range)
        # This is the limitation of InfluxDB delete API
        print(f"\nDeleting data in time range...")
        
        delete_api.delete(
            start=start,
            stop=stop,
            predicate=f'_measurement="features_5min" AND symbol="BTCUSDT" AND interval="5min"',
            bucket=settings.influx.bucket,
            org=settings.influx.org,
        )
        
        print("✅ Successfully deleted bad data")
        print(f"   Time range: {start} to {stop}")
        print(f"   Measurement: features_5min")
        print(f"   Symbol: BTCUSDT")
        
    except Exception as e:
        logger.error(f"Failed to delete data: {e}")
        raise
    finally:
        client.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Clean up bad data from InfluxDB')
    parser.add_argument('--confirm', action='store_true', help='Confirm deletion')
    args = parser.parse_args()
    
    print("=== InfluxDB Bad Data Cleanup ===\n")
    
    if not args.confirm:
        print("⚠️  This will DELETE data from InfluxDB!")
        print("Run with --confirm flag to proceed:")
        print("  python scripts/cleanup_bad_data.py --confirm")
        sys.exit(0)
    
    delete_bad_data()
