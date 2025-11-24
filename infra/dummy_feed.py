import json
import json
import os
import random
import signal
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import redis
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "dev-token")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "default")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "trading")


def generate_bar(prev_close: float) -> tuple[pd.Series, float]:
    close = max(1000.0, prev_close + random.uniform(-150.0, 150.0))
    high = close + random.uniform(5.0, 35.0)
    low = close - random.uniform(5.0, 35.0)
    open_price = (close + prev_close) / 2
    volume = random.uniform(80.0, 150.0)
    timestamp = datetime.now(timezone.utc)
    row = pd.Series(
        {
            "datetime": timestamp.isoformat(),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    return row, close


def publish_to_redis(r: redis.Redis, row: pd.Series, intervals: list[str]) -> None:
    payload = {
        "o": float(row["open"]),
        "h": float(row["high"]),
        "l": float(row["low"]),
        "c": float(row["close"]),
        "v": float(row["volume"]),
        "T": row["datetime"],
    }
    message_template = {
        "symbol": "BTCUSDT",
        "payload": {"k": payload},
    }
    for interval in intervals:
        message = {**message_template, "interval": interval}
        r.publish("stream:binance:kline", json.dumps(message))


def write_to_influx(write_api, row: pd.Series) -> None:
    for measurement, interval in (("features_1m", "1m"), ("features_5min", "5min")):
        point = (
            Point(measurement)
            .tag("symbol", "BTCUSDT")
            .tag("interval", interval)
            .field("close", float(row["close"]))
            .field("ema_20", float(row["close"]))
            .field("ema_50", float(row["close"]))
            .field("rsi_14", 50.0)
            .time(row["datetime"])
        )
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)


def main() -> None:
    should_run = True

    def _handle_shutdown(signum: int, frame) -> None:  # noqa: D401, ARG001
        nonlocal should_run
        should_run = False

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    redis_client = redis.Redis.from_url(REDIS_URL)
    influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    intervals = ["1m", "5min"]
    close = 50000.0
    lookback: list[pd.Series] = []

    while should_run:
        row, close = generate_bar(close)
        lookback.append(row)
        # keep last 300 entries (approx 5h for 1m data)
        if len(lookback) > 300:
            lookback.pop(0)

        write_to_influx(write_api, row)
        publish_to_redis(redis_client, row, intervals)
        time.sleep(60)

    write_api.close()
    influx_client.close()


if __name__ == "__main__":
    main()
