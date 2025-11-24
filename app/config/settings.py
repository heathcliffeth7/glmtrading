import json
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import AnyHttpUrl, AnyUrl, Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class BinanceWebSocketSettings(BaseSettings):
    """WebSocket connection settings for enhanced reliability"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    # Connection management
    max_reconnect_attempts: int = Field(10, alias="BINANCE_WS_MAX_RECONNECT_ATTEMPTS")
    base_retry_delay: int = Field(5, alias="BINANCE_WS_BASE_RETRY_DELAY")  # seconds
    max_retry_delay: int = Field(60, alias="BINANCE_WS_MAX_RETRY_DELAY")  # seconds
    circuit_breaker_threshold: int = Field(5, alias="BINANCE_WS_CIRCUIT_BREAKER_THRESHOLD")
    connection_cooldown: int = Field(300, alias="BINANCE_WS_CONNECTION_COOLDOWN")  # 5 minutes

    # Heartbeat monitoring
    ping_interval: int = Field(45, alias="BINANCE_WS_PING_INTERVAL")  # seconds (increased from 30)
    heartbeat_check_interval: int = Field(5, alias="BINANCE_WS_HEARTBEAT_CHECK_INTERVAL")  # seconds (decreased from 10)
    message_timeout_multiplier: float = Field(3.0, alias="BINANCE_WS_MESSAGE_TIMEOUT_MULTIPLIER")  # increased from 2.0

    # Connection validation - 1% için BTC, ETH, SOL
    validate_ohlc_relationship: bool = Field(True, alias="BINANCE_WS_VALIDATE_OHLC")
    max_price_change_pct: float = Field(0.01, alias="BINANCE_WS_MAX_PRICE_CHANGE_PCT")  # %45 → %1
    cache_reset_threshold: float = Field(0.05, alias="BINANCE_WS_CACHE_RESET_THRESHOLD")  # %18 → %5


class BinanceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BINANCE_",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    api_key: str = Field("changeme")
    api_secret: str = Field("changeme")
    websocket_endpoint: AnyUrl = Field("wss://stream.binance.com:9443/ws")
    spot_rest_endpoint: AnyHttpUrl = Field("https://api.binance.com")
    futures_rest_endpoint: AnyHttpUrl = Field("https://fapi.binance.com")  # Kept for reference
    websocket: BinanceWebSocketSettings = Field(default_factory=BinanceWebSocketSettings)


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: AnyUrl = Field("redis://localhost:6379/0", alias="REDIS_URL")


class InfluxSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INFLUX_",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    url: AnyHttpUrl = Field("http://localhost:8086")
    token: str = Field("changeme")
    org: str = Field("default")
    bucket: str = Field("trading")


class QLibSettings(BaseSettings):
    data_path: str = Field("./qlib_data/binance", alias="QLIB_DATA_PATH")
    region: str = Field("us", alias="QLIB_REGION")


class ZAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GLM_",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    api_key: str = Field("changeme")
    base_url: AnyHttpUrl = Field("https://api.z.ai/api/coding/paas/v4")
    model: str = Field("glm-4.6")
    window_seconds: int = Field(5 * 60 * 60)
    max_requests: int = Field(1000)
    max_tokens: int = Field(16000, alias="GLM_MAX_TOKENS")

    # GLM Confidence thresholds for actions
    min_confidence_close_base: float = Field(95.0, alias="GLM_MIN_CONFIDENCE_CLOSE_BASE")
    min_confidence_close_profitable: float = Field(98.0, alias="GLM_MIN_CONFIDENCE_CLOSE_PROFITABLE")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
        env_nested_delimiter=None,
    )

    environment: str = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="APP_LOG_LEVEL")
    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    telegram_channel_id: str = Field("", alias="TELEGRAM_CHANNEL_ID")
    use_nof1_style: bool = Field(False, alias="USE_NOF1_STYLE")
    
    # Position Monitor Settings
    enable_position_monitor: bool = Field(True, alias="ENABLE_POSITION_MONITOR")
    position_monitor_interval_seconds: int = Field(180, alias="POSITION_MONITOR_INTERVAL_SECONDS")  # 3 dakika
    position_monitor_check_invalidation: bool = Field(True, alias="POSITION_MONITOR_CHECK_INVALIDATION")
    
    # Price Freshness Thresholds (symbol-specific, in seconds)
    # Volatile assets need stricter freshness requirements
    price_freshness_thresholds: dict = Field(
        default={
            "SOLUSDT": 10,  # SOL: 10s (reduced from 3s - more realistic)
            "ETHUSDT": 15,  # ETH: 15s (reduced from 3s - more realistic)
            "BTCUSDT": 15,  # BTC: 15s (increased from 5s - more realistic)
            "default": 20,  # Other symbols: 20s (conservative)
        },
        description="Symbol-specific price freshness thresholds in seconds - made more realistic"
    )
    
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    influx: InfluxSettings = Field(default_factory=InfluxSettings)
    qlib: QLibSettings = Field(default_factory=QLibSettings)
    zai: ZAISettings = Field(default_factory=ZAISettings)


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
