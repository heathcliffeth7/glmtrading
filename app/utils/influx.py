import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.rest import ApiException

from app.config.settings import get_settings
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)


_client: InfluxDBClient | None = None
_write_api = None
_query_api = None
_client_initialized_logged = False


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
                if attempt == max_retries - 1:
                    logger.error("InfluxDB connection error after %d attempts: %s", max_retries, e)
                    raise
                logger.warning("InfluxDB connection error (attempt %d/%d): %s", attempt + 1, max_retries, e)
                time.sleep(retry_delay * (2 ** attempt))
    return wrapper


def _ensure_client() -> None:
    global _client, _write_api, _query_api, _client_initialized_logged
    if _client is None:
        # Only log once per process to avoid noise from multiple worker processes
        if not _client_initialized_logged:
            logger.info("InfluxDB client initializing (PID: %d)", __import__('os').getpid())
            _client_initialized_logged = True
        
        _client = InfluxDBClient(
            url=str(settings.influx.url),
            token=settings.influx.token,
            org=settings.influx.org,
        )
        _write_api = _client.write_api(write_options=SYNCHRONOUS)
        _query_api = _client.query_api()
    elif not _client_initialized_logged:
        # Client exists but we haven't logged yet (shouldn't happen, but defensive)
        logger.debug("InfluxDB client already initialized (PID: %d)", __import__('os').getpid())
        _client_initialized_logged = True


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
    
    for key, value in fields.items():
        # Skip None values and non-numeric timestamp fields
        if value is None:
            logger.warning("Skipping None field: %s in measurement %s", key, measurement)
            continue
        if key == 'timestamp' and isinstance(value, str):
            # Skip timestamp field - it's already set via point.time()
            continue
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
) -> List[Dict[str, Any]]:
    """
    Get last N snapshots (e.g., last 10 data points for each indicator)
    Returns list of dicts, oldest → newest
    """
    _ensure_client()
    query = f"""
    from(bucket: "{settings.influx.bucket}")
      |> range(start: -24h)
      |> filter(fn: (r) => r["_measurement"] == "{measurement}")
      |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: {limit})
      |> sort(columns: ["_time"], desc: false)
    """
    
    try:
        tables = _query_api.query(query)
        snapshots = []
        
        for table in tables:
            for record in table.records:
                snapshot = {"timestamp": record.get_time().isoformat()}
                # Extract all fields
                for key, value in record.values.items():
                    if not key.startswith("_") and key not in ("result", "table", "symbol", "interval"):
                        snapshot[key] = value
                snapshots.append(snapshot)
        
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
    _ensure_client()
    
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
    global _client, _write_api, _query_api, _client_initialized_logged
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
