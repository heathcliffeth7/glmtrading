"""
TwelveData Feature Feed - Continuously fetch and process new bars
Simplified version that works with existing infrastructure
"""

import asyncio
from datetime import datetime
from typing import Optional

import pandas as pd
import ta

from app.config.settings import get_settings
from app.data_feeds.twelve_data import TimeSeriesBar, TwelveDataClient
from app.utils.influx import query_latest_snapshot, write_measurement
from app.utils.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


def calculate_features(bar: TimeSeriesBar, symbol: str, interval: str) -> dict:
    """
    Calculate features for a single bar using historical context

    Args:
        bar: New bar from TwelveData
        symbol: Trading symbol
        interval: Time interval

    Returns:
        Dict with all features
    """
    from app.utils.influx import query_range

    # Get last 50 bars for TA context
    try:
        historical = query_range(
            f"features_{interval}", symbol, interval, minutes=1500  # ~50 bars at 30min
        )

        if historical:
            # Convert to DataFrame
            hist_df = pd.DataFrame(historical)
            hist_df = hist_df.pivot(index="timestamp", columns="field", values="value")
            hist_df = hist_df.reset_index()
            # Use format='ISO8601' for flexible parsing
            hist_df["timestamp"] = pd.to_datetime(hist_df["timestamp"], format="ISO8601")

            # Add new bar
            new_row = pd.DataFrame(
                [
                    {
                        "timestamp": bar.timestamp,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                ]
            )

            df = pd.concat([hist_df, new_row], ignore_index=True)
            df = df.drop_duplicates(subset=["timestamp"], keep="last")
            df = df.sort_values("timestamp")
        else:
            # No historical data, use just this bar
            df = pd.DataFrame(
                [
                    {
                        "timestamp": bar.timestamp,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                ]
            )
    except Exception as exc:
        logger.warning("Could not fetch historical context: %s", exc)
        df = pd.DataFrame(
            [
                {
                    "timestamp": bar.timestamp,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
            ]
        )

    # Calculate features
    try:
        if len(df) >= 20:  # Need at least 20 bars for EMA_20
            df["ema_20"] = ta.trend.EMAIndicator(
                df["close"], window=20, fillna=True
            ).ema_indicator()

            df["ema_50"] = ta.trend.EMAIndicator(
                df["close"], window=50, fillna=True
            ).ema_indicator()

            df["rsi_14"] = ta.momentum.RSIIndicator(df["close"], window=14, fillna=True).rsi()

            macd = ta.trend.MACD(df["close"], fillna=True)
            df["macd"] = macd.macd()
            df["macd_signal"] = macd.macd_signal()

            df["atr_14"] = ta.volatility.AverageTrueRange(
                df["high"], df["low"], df["close"], window=14, fillna=True
            ).average_true_range()

            df["vwap_20"] = ta.volume.VolumeWeightedAveragePrice(
                df["high"], df["low"], df["close"], df["volume"], window=20, fillna=True
            ).volume_weighted_average_price()
        else:
            # Not enough data, use simple values
            df["ema_20"] = df["close"]
            df["ema_50"] = df["close"]
            df["rsi_14"] = 50.0
            df["macd"] = 0.0
            df["macd_signal"] = 0.0
            df["atr_14"] = 0.0
            df["vwap_20"] = df["close"]
    except Exception as exc:
        logger.error("Error calculating features: %s", exc)
        # Return basic features
        return {
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "ema_20": bar.close,
            "ema_50": bar.close,
            "rsi_14": 50.0,
            "macd": 0.0,
            "macd_signal": 0.0,
            "atr_14": 0.0,
            "vwap_20": bar.close,
        }

    # Return features for the new bar only
    latest = df.iloc[-1]
    return {
        "open": float(latest["open"]),
        "high": float(latest["high"]),
        "low": float(latest["low"]),
        "close": float(latest["close"]),
        "volume": float(latest["volume"]),
        "ema_20": float(latest["ema_20"]),
        "ema_50": float(latest["ema_50"]),
        "rsi_14": float(latest["rsi_14"]),
        "macd": float(latest["macd"]),
        "macd_signal": float(latest["macd_signal"]),
        "atr_14": float(latest["atr_14"]),
        "vwap_20": float(latest["vwap_20"]),
    }


async def feed_features_continuously(
    symbol: str = "BTC/USD",
    binance_symbol: str = "BTCUSDT",
    interval: str = "30min",
    poll_interval: int = 60,
) -> None:
    """
    Continuously fetch bars from TwelveData and write features to InfluxDB

    Args:
        symbol: TwelveData symbol (e.g., "BTC/USD")
        binance_symbol: Binance-compatible symbol for tagging
        interval: Time interval
        poll_interval: Seconds between polls
    """
    logger.info(
        "Starting TwelveData feature feed: symbol=%s interval=%s poll=%ds",
        symbol,
        interval,
        poll_interval,
    )

    client = TwelveDataClient(settings.twelve_data.api_keys)
    last_timestamp: Optional[datetime] = None

    # Get last timestamp from InfluxDB
    try:
        snapshot = query_latest_snapshot(f"features_{interval}", binance_symbol, interval)
        if snapshot and "timestamp" in snapshot:
            ts_str = snapshot["timestamp"].replace("Z", "").replace("+00:00", "")
            last_timestamp = datetime.fromisoformat(ts_str)
            logger.info("Resuming from last timestamp: %s", last_timestamp)
    except Exception as exc:
        logger.warning("Could not get last timestamp: %s", exc)

    while True:
        try:
            # Fetch latest 10 bars
            bars = await client.fetch_time_series(
                symbol=symbol,
                interval=interval,
                outputsize=10,
            )

            if not bars:
                logger.debug("No bars received")
                await asyncio.sleep(poll_interval)
                continue

            # Filter for new bars
            new_bars = []
            for bar in bars:
                if last_timestamp is None:
                    new_bars.append(bar)
                else:
                    # Make both timezone-naive for comparison
                    bar_ts = (
                        bar.timestamp.replace(tzinfo=None)
                        if bar.timestamp.tzinfo
                        else bar.timestamp
                    )
                    last_ts = (
                        last_timestamp.replace(tzinfo=None)
                        if last_timestamp.tzinfo
                        else last_timestamp
                    )
                    if bar_ts > last_ts:
                        new_bars.append(bar)

            if not new_bars:
                logger.debug("No new bars since %s", last_timestamp)
                await asyncio.sleep(poll_interval)
                continue

            logger.info("Processing %d new bars", len(new_bars))

            # Process each new bar
            for bar in new_bars:
                try:
                    # Calculate features
                    features = calculate_features(bar, binance_symbol, interval)

                    # Write to InfluxDB
                    write_measurement(
                        measurement=f"features_{interval}",
                        tags={"symbol": binance_symbol, "interval": interval},
                        fields=features,
                        timestamp=bar.timestamp,
                    )

                    last_timestamp = bar.timestamp
                    logger.info(
                        "Wrote bar: timestamp=%s close=%.2f ema20=%.2f ema50=%.2f rsi=%.2f",
                        bar.timestamp,
                        features["close"],
                        features["ema_20"],
                        features["ema_50"],
                        features["rsi_14"],
                    )

                except Exception as exc:
                    logger.error("Error processing bar: %s", exc, exc_info=True)

        except Exception as exc:
            logger.error("Feed error: %s", exc, exc_info=True)

        await asyncio.sleep(poll_interval)


async def main():
    """Entry point for standalone execution"""
    import argparse

    parser = argparse.ArgumentParser(description="TwelveData continuous feature feed")
    parser.add_argument("--symbol", default="BTC/USD", help="TwelveData symbol")
    parser.add_argument("--binance-symbol", default="BTCUSDT", help="Binance symbol")
    parser.add_argument("--interval", default="30min", help="Time interval")
    parser.add_argument("--poll-interval", type=int, default=60, help="Poll interval (seconds)")

    args = parser.parse_args()

    await feed_features_continuously(
        symbol=args.symbol,
        binance_symbol=args.binance_symbol,
        interval=args.interval,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    asyncio.run(main())
