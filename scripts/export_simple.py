#!/usr/bin/env python3
"""Simple export script without pivot - get all records."""

import pandas as pd
from datetime import datetime, timedelta
from app.utils.influx import _ensure_client
import app.utils.influx
from app.config.settings import get_settings
from app.utils.logging import configure_logging, get_logger
from collections import defaultdict

configure_logging("INFO")
logger = get_logger(__name__)

# Initialize
s = get_settings()
_ensure_client()

logger.info("Querying InfluxDB for all enriched_30m records...")

# Query without pivot - get all fields
query = f'''
from(bucket: "{s.influx.bucket}")
  |> range(start: -60d)
  |> filter(fn: (r) => r._measurement == "enriched_30m")
  |> filter(fn: (r) => r.symbol == "BTCUSDT")
  |> sort(columns: ["_time"], desc: false)
'''

tables = app.utils.influx._query_api.query(query)

if not tables:
    logger.error("No data found!")
    exit(1)

# Group by timestamp
grouped = defaultdict(dict)
for table in tables:
    for record in table.records:
        timestamp = record.get_time()
        field = record.get_field()
        value = record.get_value()
        grouped[timestamp][field] = value

logger.info(f"Found {len(grouped)} unique timestamps")

# Convert to dataframe
rows = []
for timestamp, fields in sorted(grouped.items()):
    row = {'timestamp': timestamp}
    row.update(fields)
    rows.append(row)

df = pd.DataFrame(rows)
logger.info(f"DataFrame shape: {df.shape}")
logger.info(f"Columns: {list(df.columns)}")

# Save
output_file = "data/derivatives_20features_full.csv"
df.to_csv(output_file, index=False)
logger.info(f"✅ Saved to {output_file}")

# Show stats
logger.info(f"\nFirst record: {df['timestamp'].iloc[0]}")
logger.info(f"Last record: {df['timestamp'].iloc[-1]}")
logger.info(f"Total records: {len(df)}")
