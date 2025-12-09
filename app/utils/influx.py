import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, WriteOptions
from influxdb_client.rest import ApiException

from app.config.settings import get_settings
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)

# Backfill state to prevent repeated backfills in same session
_backfill_completed: set = set()  # Set of "symbol_interval" keys


_client: InfluxDBClient | None = None
_write_api = None
_query_api = None
_client_initialized_logged = False
_client_lock = threading.Lock()  # Thread-safe client initialization


def _influx_retry(func):
    def wrapper(*args, **kwargs):
        max_retries = 3
        retry_delay = 1
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except ApiException as e:
                if attempt == max_retries - 1:
                    logger.error("InfluxDB API error after %d attempts: %s", max_retries, e)
                    raise
                logger.warning("InfluxDB API error (attempt %d/%d): %s", attempt + 1, max_retries, e)
                time.sleep(retry_delay * (2 ** attempt))
            except Exception as e:
                error_str = str(e).lower()
                # Connection refused hatası için özel mesaj
                if "connection refused" in error_str or "errno 111" in error_str:
                    if attempt == max_retries - 1:
                        logger.error(
                            "InfluxDB bağlantı hatası: Sunucuya bağlanılamıyor (%s:%s). "
                            "Lütfen InfluxDB servisinin çalıştığından emin olun: %s",
                            settings.influx.url.host if hasattr(settings.influx.url, 'host') else str(settings.influx.url),
                            settings.influx.url.port if hasattr(settings.influx.url, 'port') else '8086',
                            e
                        )
                    else:
                        logger.warning(
                            "InfluxDB bağlantı hatası (deneme %d/%d): %s - Tekrar deneniyor...",
                            attempt + 1, max_retries, e
                        )
                elif attempt == max_retries - 1:
                    logger.error("InfluxDB connection error after %d attempts: %s", max_retries, e)
                else:
                    logger.warning("InfluxDB connection error (attempt %d/%d): %s", attempt + 1, max_retries, e)
                
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                else:
                    raise
    return wrapper


def _ensure_client() -> None:
    global _client, _write_api, _query_api, _client_initialized_logged

    # Quick check without lock (optimization for common case after initialization)
    if _client is not None:
        return

    # Double-checked locking for thread safety
    with _client_lock:
        # Re-check after acquiring lock (another thread might have initialized)
        if _client is None:
            try:
                if not _client_initialized_logged:
                    logger.info("InfluxDB client initializing (PID: %d)", __import__('os').getpid())
                    _client_initialized_logged = True

                _client = InfluxDBClient(
                    url=str(settings.influx.url),
                    token=settings.influx.token,
                    org=settings.influx.org,
                )
                # Use batched writes for better performance (non-blocking)
                _write_api = _client.write_api(
                    write_options=WriteOptions(
                        batch_size=100,
                        flush_interval=500,  # 500ms max latency
                        jitter_interval=100,
                        retry_interval=1000,
                        max_retries=3,
                        max_retry_delay=5000,
                        exponential_base=2
                    )
                )
                _query_api = _client.query_api()
                logger.info("InfluxDB client initialized successfully (PID: %d)", __import__('os').getpid())
            except Exception as e:
                error_str = str(e).lower()
                if "connection refused" in error_str or "errno 111" in error_str:
                    logger.error(
                        "InfluxDB başlatılamadı: Sunucuya bağlanılamıyor (%s). "
                        "Lütfen InfluxDB servisinin çalıştığından emin olun: docker-compose up -d influxdb",
                        settings.influx.url
                    )
                else:
                    logger.error("Failed to initialize InfluxDB client: %s", e)
                # Reset all state on failure to allow retry
                _client = None
                _write_api = None
                _query_api = None
                raise


@_influx_retry
def write_measurement(measurement: str, tags: Dict[str, str], fields: Dict[str, Any], timestamp: datetime) -> None:
    _ensure_client()
    point = Point(measurement)
    for key, value in tags.items():
        point = point.tag(key, value)
    
    # Debug: Log fields being written
    if 'high' in fields or 'low' in fields:
        logger.debug("Writing %s with OHLCV: close=%.2f high=%.2f low=%.2f volume=%.2f",
                   measurement, 
                   fields.get('close', 0), 
                   fields.get('high', 0), 
                   fields.get('low', 0), 
                   fields.get('volume', 0))
    
    # Define float fields for trading_signals to prevent type conflicts
    float_fields = set()
    if measurement == 'trading_signals':
        float_fields = {'equity', 'position', 'unrealized_pnl', 'realized_pnl', 'total_pnl'}

    for key, value in fields.items():
        # Skip None values and non-numeric timestamp fields
        if value is None:
            logger.warning("Skipping None field: %s in measurement %s", key, measurement)
            continue
        if key == 'timestamp' and isinstance(value, str):
            # Skip timestamp field - it's already set via point.time()
            continue
            
        # Enforce float type for specific fields
        if key in float_fields:
            try:
                val_float = float(value)
                # Check if it was an integer and log it (debug)
                if isinstance(value, int):
                    logger.debug("Converted integer field %s=%s to float %.1f", key, value, val_float)
                
                # HACK: If the value is a whole number (e.g. 100.0), InfluxDB client might serialize it as "100"
                # which causes type conflict if the field is already float.
                # We add a tiny epsilon to force it to look like a float in line protocol.
                if val_float.is_integer():
                    val_float += 0.0000001
                    
                value = val_float
            except (ValueError, TypeError):
                logger.warning("Failed to convert field %s to float: %s", key, value)
                
        point = point.field(key, value)
    
    point = point.time(timestamp, WritePrecision.NS)
    _write_api.write(bucket=settings.influx.bucket, record=point)
    logger.debug("Successfully wrote measurement %s with tags %s", measurement, tags)


@_influx_retry
def write_point(point_dict: Dict[str, Any]) -> None:
    """
    Write a point to InfluxDB using dict-based API
    
    Args:
        point_dict: Dict with keys: 'measurement', 'tags', 'fields', 'time'
    """
    measurement = point_dict["measurement"]
    tags = point_dict.get("tags", {})
    fields = point_dict["fields"]
    timestamp = point_dict.get("time", datetime.utcnow())
    
    write_measurement(measurement, tags, fields, timestamp)


@_influx_retry
def query_latest(measurement: str, symbol: str, interval: str) -> Dict[str, Any] | None:
    _ensure_client()

    # Safety check: ensure query API is available
    if _query_api is None:
        logger.warning("InfluxDB query API not initialized for query_latest")
        return None

    query = f"""
    from(bucket: "{settings.influx.bucket}")
      |> range(start: -1h)
      |> filter(fn: (r) => r["_measurement"] == "{measurement}")
      |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n:1)
    """
    tables = _query_api.query(query)
    if not tables:
        logger.debug("No data found for measurement %s, symbol %s, interval %s", measurement, symbol, interval)
        return None
    record = tables[0].records[0]
    result = {
        "value": record.get_value(),
        "field": record.get_field(),
        "timestamp": record.get_time().isoformat(),
    }
    logger.debug("Query successful for %s/%s/%s", measurement, symbol, interval)
    return result


def query_latest_snapshot(measurement: str, symbol: str, interval: str) -> Dict[str, Any] | None:
    try:
        _ensure_client()
    except Exception as e:
        logger.warning("InfluxDB client initialization failed in query_latest_snapshot: %s", e)
        return None

    # Safety check: ensure query API is available
    if _query_api is None:
        logger.warning("InfluxDB query API not initialized for latest snapshot - returning None")
        return None

    try:
        # Use -24h range to find latest data even if it's older (e.g., 4h interval)
        query = f"""
        from(bucket: "{settings.influx.bucket}")
          |> range(start: -24h)
          |> filter(fn: (r) => r["_measurement"] == "{measurement}")
          |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n:20)
        """
        tables = _query_api.query(query)
        if not tables:
            return None

        grouped: Dict[datetime, Dict[str, Any]] = defaultdict(dict)
        for table in tables:
            for record in table.records:
                timestamp = record.get_time()
                field = record.get_field()
                grouped[timestamp][field] = record.get_value()

        if not grouped:
            return None

        latest_timestamp = max(grouped)
        snapshot = grouped[latest_timestamp]
        snapshot["timestamp"] = latest_timestamp.isoformat()
        return snapshot
    except Exception as exc:
        logger.warning(
            "Failed to fetch latest snapshot for %s/%s/%s: %s",
            measurement,
            symbol,
            interval,
            exc,
        )
        return None


def query_range(measurement: str, symbol: str, interval: str, minutes: int = 120) -> List[Dict[str, Any]]:
    _ensure_client()

    # Safety check: ensure query API is available
    if _query_api is None:
        logger.warning("InfluxDB query API not initialized for query_range")
        return []

    query = f"""
    from(bucket: "{settings.influx.bucket}")
      |> range(start: -{minutes}m)
      |> filter(fn: (r) => r["_measurement"] == "{measurement}")
      |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
      |> sort(columns: ["_time"], desc: false)
    """
    tables = _query_api.query(query)
    data: List[Dict[str, Any]] = []
    for table in tables:
        for record in table.records:
            data.append(
                {
                    "timestamp": record.get_time().isoformat(),
                    "field": record.get_field(),
                    "value": record.get_value(),
                }
            )
    return data


def query_historical_snapshots(
    measurement: str,
    symbol: str,
    interval: str,
    limit: int = 10,
    hours: int = 24,
) -> List[Dict[str, Any]]:
    """
    Get last N snapshots (e.g., last 10 data points for each indicator)
    Returns list of dicts, oldest → newest.

    If InfluxDB has fewer than 'limit' records, automatically backfills from Binance.

    Args:
        hours: How far back to look (default 24 hours, max 720 = 30 days)
    """
    try:
        _ensure_client()
    except Exception as e:
        logger.warning("InfluxDB client initialization failed: %s", e)
        return []

    # Safety check: ensure query API is available
    if _query_api is None:
        logger.warning("InfluxDB query API not initialized - returning empty result")
        return []

    # Increase hours based on interval to ensure we look far enough back
    interval_hours_map = {
        "1m": 24, "5m": 48, "15min": 72, "30min": 168,
        "1h": 336, "4h": 1344, "1d": 4320
    }
    hours = max(hours, interval_hours_map.get(interval, hours))
    hours = min(hours, 4320)  # Cap at 180 days

    query = f"""
    from(bucket: "{settings.influx.bucket}")
      |> range(start: -{hours}h)
      |> filter(fn: (r) => r["_measurement"] == "{measurement}")
      |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: {limit})
      |> sort(columns: ["_time"], desc: false)
    """

    def _execute_query() -> List[Dict[str, Any]]:
        tables = _query_api.query(query)
        snapshots = []
        for table in tables:
            for record in table.records:
                snapshot = {"timestamp": record.get_time().isoformat()}
                for key, value in record.values.items():
                    if not key.startswith("_") and key not in ("result", "table", "symbol", "interval"):
                        snapshot[key] = value
                snapshots.append(snapshot)
        return snapshots

    try:
        snapshots = _execute_query()

        # Check if we have enough data, if not, backfill from Binance
        if len(snapshots) < limit:
            logger.info(
                "📉 Insufficient data for %s %s: got %d, need %d - triggering backfill",
                symbol, interval, len(snapshots), limit
            )

            # Only backfill for enriched measurements
            if measurement.startswith("enriched_"):
                backfill_success = _backfill_from_binance(
                    measurement=measurement,
                    symbol=symbol,
                    interval=interval,
                    limit=limit,
                )

                if backfill_success:
                    # Re-query after backfill
                    snapshots = _execute_query()
                    logger.info(
                        "✅ After backfill: %s %s now has %d records",
                        symbol, interval, len(snapshots)
                    )

        return snapshots

    except Exception as exc:
        logger.warning("Failed to fetch historical snapshots: %s", exc)
        return []


@_influx_retry
def query_range_between(
    measurement: str,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    _ensure_client()

    # Safety check: ensure query API is available
    if _query_api is None:
        logger.warning("InfluxDB query API not initialized for query_range_between")
        return []

    # Format timestamps for Flux (RFC3339)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    query = f"""
from(bucket: "{settings.influx.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
  |> sort(columns: ["_time"], desc: false)
    """
    tables = _query_api.query(query)
    data: List[Dict[str, Any]] = []
    for table in tables:
        for record in table.records:
            data.append(
                {
                    "timestamp": record.get_time().isoformat(),
                    "field": record.get_field(),
                    "value": record.get_value(),
                }
            )
    return data


def check_health() -> Dict[str, Any]:
    try:
        _ensure_client()
        health = _client.health()
        return {
            "status": "healthy",
            "message": health.message,
            "url": str(settings.influx.url),
            "bucket": settings.influx.bucket,
            "org": settings.influx.org,
        }
    except Exception as e:
        logger.error("InfluxDB health check failed: %s", e)
        return {
            "status": "unhealthy",
            "message": str(e),
            "url": str(settings.influx.url),
            "bucket": settings.influx.bucket,
            "org": settings.influx.org,
        }


def test_connection() -> bool:
    try:
        _ensure_client()

        # Safety check: ensure query API is available
        if _query_api is None:
            logger.warning("InfluxDB query API not initialized for test_connection")
            return False

        query = f'buckets() |> filter(fn: (r) => r.name == "{settings.influx.bucket}") |> limit(n:1)'
        tables = _query_api.query(query)
        success = len(tables) > 0
        if success:
            logger.debug("InfluxDB connection test successful")
        else:
            logger.warning("InfluxDB connection test failed: bucket not found")
        return success
    except Exception as e:
        logger.error("InfluxDB connection test failed: %s", e)
        return False


def _fetch_binance_historical_price(symbol: str, target_time: datetime) -> float | None:
    """
    Fallback: Fetch historical price from Binance Spot API
    
    Args:
        symbol: Trading symbol (e.g., BTCUSDT)
        target_time: Target timestamp
    
    Returns:
        Close price at that time, or None if not found
    """
    try:
        import httpx
        
        # Convert to milliseconds timestamp
        target_ms = int(target_time.timestamp() * 1000)
        
        # Fetch 5-minute klines around target time (±10 minutes = 4 klines)
        # Binance klines endpoint: /api/v3/klines
        params = {
            "symbol": symbol,
            "interval": "5m",
            "startTime": target_ms - (10 * 60 * 1000),  # 10 min before
            "endTime": target_ms + (10 * 60 * 1000),    # 10 min after
            "limit": 10,
        }
        
        response = httpx.get("https://api.binance.com/api/v3/klines", params=params, timeout=10.0)
        response.raise_for_status()
        klines = response.json()
        
        if not klines:
            logger.warning("Binance returned no klines for %s at %s", symbol, target_time)
            return None
        
        # Find closest kline to target time
        # Kline format: [open_time, open, high, low, close, volume, close_time, ...]
        closest_kline = None
        min_diff = float('inf')
        
        for kline in klines:
            kline_time_ms = int(kline[0])
            time_diff = abs(kline_time_ms - target_ms)
            if time_diff < min_diff:
                min_diff = time_diff
                closest_kline = kline
        
        if closest_kline:
            close_price = float(closest_kline[4])  # Close price is 5th element
            logger.info(
                "✅ Binance fallback: Found price %.2f for %s at %s (diff: %.1f min)",
                close_price,
                symbol,
                target_time,
                min_diff / 60000,  # Convert ms to minutes
            )
            return close_price
        else:
            logger.warning("Could not find closest kline from Binance for %s", symbol)
            return None
            
    except Exception as e:
        logger.error("Binance fallback failed: %s", e)
        return None


def query_price_at_time(
    symbol: str,
    target_time: datetime,
    interval: str = "1m",
    window_minutes: int = 5,
) -> float | None:
    """
    Query price at a specific time (for feedback collection)

    Args:
        symbol: Trading symbol
        target_time: Target timestamp
        interval: Data interval
        window_minutes: Time window to search around target

    Returns:
        Close price at that time, or None if not found
    """
    try:
        _ensure_client()
    except Exception as e:
        logger.warning("InfluxDB client initialization failed for query_price_at_time: %s", e)
        return _fetch_binance_historical_price(symbol, target_time)

    # Safety check: ensure query API is available
    if _query_api is None:
        logger.warning("InfluxDB query API not initialized for query_price_at_time, trying Binance fallback")
        return _fetch_binance_historical_price(symbol, target_time)

    # Search window around target time
    start = target_time - timedelta(minutes=window_minutes)
    end = target_time + timedelta(minutes=window_minutes)
    
    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    query = f"""
from(bucket: "{settings.influx.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r["_measurement"] == "enriched_{interval}")
  |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
  |> filter(fn: (r) => r["_field"] == "close")
  |> sort(columns: ["_time"], desc: false)
  |> limit(n: 1)
    """
    
    try:
        tables = _query_api.query(query)
        if tables and tables[0].records:
            price = float(tables[0].records[0].get_value())
            logger.debug("Found price %.2f at %s (±%d min window)", price, target_time, window_minutes)
            return price
        else:
            logger.warning("No price found in InfluxDB for %s at %s (±%d min), trying Binance fallback", symbol, target_time, window_minutes)
            # Fallback: Fetch from Binance historical klines
            return _fetch_binance_historical_price(symbol, target_time)
    except Exception as e:
        logger.error("Failed to query price at time %s: %s", target_time, e)
        # Try Binance fallback on InfluxDB error
        try:
            return _fetch_binance_historical_price(symbol, target_time)
        except:
            return None


def close_connections() -> None:
    """Graceful shutdown - flush pending batched writes before closing."""
    global _client, _write_api, _query_api, _client_initialized_logged
    if _write_api:
        try:
            # Flush any pending batched writes
            _write_api.close()
            logger.debug("InfluxDB write API closed (pending writes flushed)")
        except Exception as e:
            logger.error("Error closing InfluxDB write API: %s", e)
    if _client:
        try:
            _client.close()
            logger.debug("InfluxDB client closed")
        except Exception as e:
            logger.error("Error closing InfluxDB client: %s", e)
        finally:
            _client = None
            _write_api = None
            _query_api = None
            _client_initialized_logged = False


def _backfill_from_binance(
    measurement: str,
    symbol: str,
    interval: str,
    limit: int,
) -> bool:
    """
    Fetch historical klines from Binance and backfill InfluxDB.
    Calculates indicators for each bar using all prior bars.

    Args:
        measurement: InfluxDB measurement name (e.g., "enriched_4h")
        symbol: Trading symbol (e.g., "BTCUSDT")
        interval: Kline interval (e.g., "4h", "1h")
        limit: Number of bars to fetch

    Returns:
        True if backfill succeeded, False otherwise
    """
    global _backfill_completed

    backfill_key = f"{symbol}_{interval}"
    if backfill_key in _backfill_completed:
        logger.debug("Backfill already completed for %s, skipping", backfill_key)
        return True

    try:
        import httpx
        from app.data_feeds.enriched_feed import calculate_indicators_from_klines

        # Map interval to Binance format
        interval_map = {
            "1m": "1m", "5m": "5m", "15min": "15m", "30min": "30m",
            "1h": "1h", "4h": "4h", "1d": "1d"
        }
        binance_interval = interval_map.get(interval, interval)

        logger.info("📥 Backfilling %s %s data from Binance (limit=%d)...", symbol, interval, limit)

        # Fetch extra bars for indicator warmup (need 50+ for RSI, MACD, etc.)
        fetch_limit = min(limit + 100, 1000)  # Binance max is 1000

        params = {
            "symbol": symbol,
            "interval": binance_interval,
            "limit": fetch_limit,
        }

        response = httpx.get(
            "https://api.binance.com/api/v3/klines",
            params=params,
            timeout=30.0
        )
        response.raise_for_status()
        raw_klines = response.json()

        if not raw_klines:
            logger.warning("Binance returned no klines for %s %s", symbol, interval)
            return False

        # Convert to dict format with timestamps
        klines = []
        for kline in raw_klines:
            klines.append({
                "timestamp": datetime.utcfromtimestamp(int(kline[0]) / 1000),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            })

        logger.info("📊 Fetched %d klines from Binance for %s %s", len(klines), symbol, interval)

        # Calculate indicators and write each bar to InfluxDB
        # For proper indicator calculation, use sliding window approach
        written_count = 0

        # Start from index 50 (need warmup for RSI, MACD, etc.)
        start_idx = min(50, len(klines) - 1)

        for i in range(start_idx, len(klines)):
            # Use all bars up to and including i for indicator calculation
            window_klines = klines[:i + 1]

            # Calculate indicators
            indicators = calculate_indicators_from_klines(window_klines)

            if not indicators:
                continue

            # Get the timestamp for this bar
            bar_timestamp = klines[i]["timestamp"]

            # Write to InfluxDB
            write_measurement(
                measurement=measurement,
                tags={"symbol": symbol, "interval": interval},
                fields=indicators,
                timestamp=bar_timestamp,
            )
            written_count += 1

        # Mark backfill as completed
        _backfill_completed.add(backfill_key)

        logger.info(
            "✅ Backfill complete: Wrote %d enriched bars for %s %s",
            written_count, symbol, interval
        )

        # Give batched writes time to flush
        time.sleep(1)

        return True

    except Exception as e:
        logger.error("Backfill failed for %s %s: %s", symbol, interval, e)
        return False


def detect_htf_support_resistance(symbol, htf_interval="1h", current_price=None):
    """
    HTF destek/direnç bölgelerini tespit eder - Gerçek veri ile
    """
    try:
        _ensure_client()
        settings = get_settings()
        
        # Gerçek fiyatı al
        if current_price is None:
            try:
                import httpx
                with httpx.Client() as client:
                    response = client.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
                    if response.status_code == 200:
                        data = response.json()
                        current_price = float(data["price"])
                        logger.debug(f"Gerçek fiyat alındı: {symbol} = ${current_price:,.2f}")
            except Exception as e:
                logger.warning(f"Binance API fiyat alınamadı: {e}")
                current_price = 110000.0  # Güncel fallback
        
        # Gerçek veriyi çek - basit query ile
        try:
            snapshot = query_latest_snapshot('enriched_15min', symbol, '15min')
            if snapshot:
                # Gerçek veri varsa kullan
                price = current_price or snapshot.get('close', current_price)
                high = snapshot.get('high', price)
                low = snapshot.get('low', price)
                
                # Basit destek/direnç analizi - mevcut veriye göre
                # Eğer fiyat close'a yakınsa nötr, low'a yakınsa destek, high'a yakınsa direnç
                close_price = snapshot.get('close', price)
                
                in_support = abs(price - low) / price < 0.02  # %2 içinde low'a yakın
                in_resistance = abs(price - high) / price < 0.02  # %2 içinde high'a yakın
                
                result = {
                    "status": "success",
                    "current_price": price,
                    "in_support_zone": in_support,
                    "in_resistance_zone": in_resistance,
                    "nearest_support": low,
                    "nearest_resistance": high,
                    "htf_interval": htf_interval,
                    "note": "real_data_simple"
                }
            else:
                # Fallback: gerçek fiyat ile dummy sonuç
                result = {
                    "status": "success", 
                    "current_price": current_price,
                    "in_support_zone": False,
                    "in_resistance_zone": False,
                    "htf_interval": htf_interval,
                    "note": "fallback_mode_real_price"
                }
        except Exception as query_error:
            logger.warning(f"HTF query hatası: {query_error}")
            # Fallback: gerçek fiyat ile dummy sonuç
            result = {
                "status": "success", 
                "current_price": current_price,
                "in_support_zone": False,
                "in_resistance_zone": False,
                "htf_interval": htf_interval,
                "note": "fallback_mode_real_price"
            }
        
        logger.debug("HTF analiz tamamlandı: %s - Fiyat: %.2f", symbol, result.get('current_price', 0))
        return result
        
    except Exception as e:
        logger.error("HTF analiz hatası: %s", e)
        return {"status": "error", "error": str(e)}
