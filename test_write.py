#!/usr/bin/env python3
"""Test InfluxDB write with specific timestamp."""

from datetime import datetime, timedelta

import pandas as pd

import app.utils.influx
from app.config.settings import get_settings
from app.utils.influx import _ensure_client, write_measurement
from app.utils.logging import configure_logging, get_logger

configure_logging("INFO")
logger = get_logger(__name__)

# Test writing 5 records with different timestamps
s = get_settings()
_ensure_client()

print("Writing 5 test records...")
base_time = datetime(2025, 9, 26, 16, 0, 0)

for i in range(5):
    timestamp = base_time + timedelta(minutes=30*i)
    fields = {
        'close': 100000.0 + i * 100,
        'rsi_14': 50.0 + i,
        'macd': -10.0 + i * 2,
    }
    
    write_measurement(
        measurement="enriched_30m",
        tags={'symbol': 'BTCUSDT', 'interval': '30m'},
        fields=fields,
        timestamp=timestamp,
    )
    print(f"  Written record {i+1}: {timestamp}, close={fields['close']}")

print("\nQuerying to verify...")
query = f'''
from(bucket: "{s.influx.bucket}")
  |> range(start: 2025-09-26T00:00:00Z, stop: 2025-09-27T00:00:00Z)
  |> filter(fn: (r) => r._measurement == "enriched_30m")
  |> filter(fn: (r) => r.symbol == "BTCUSDT")
  |> filter(fn: (r) => r._field == "close")
  |> sort(columns: ["_time"], desc: false)
'''

tables = app.utils.influx._query_api.query(query)
if tables and tables[0].records:
    print(f"Found {len(tables[0].records)} records:")
    for rec in tables[0].records:
        print(f"  {rec.get_time()}: close={rec.get_value()}")
else:
    print("No records found!")
