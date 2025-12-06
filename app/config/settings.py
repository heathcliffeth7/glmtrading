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
    connection_cooldown: int = Field(60, alias="BINANCE_WS_CONNECTION_COOLDOWN")  # 1 minute (reduced from 5 min)

    # Heartbeat monitoring - OPTIMIZED for faster stall detection
    ping_interval: int = Field(15, alias="BINANCE_WS_PING_INTERVAL")  # seconds (reduced from 45)
    heartbeat_check_interval: int = Field(5, alias="BINANCE_WS_HEARTBEAT_CHECK_INTERVAL")  # seconds
    message_timeout_multiplier: float = Field(2.0, alias="BINANCE_WS_MESSAGE_TIMEOUT_MULTIPLIER")  # reduced from 3.0

    # Stall detection settings (for 1-5 second stalls)
    stall_detection_enabled: bool = Field(True, alias="BINANCE_WS_STALL_DETECTION_ENABLED")
    stall_threshold_seconds: float = Field(3.0, alias="BINANCE_WS_STALL_THRESHOLD_SECONDS")  # 3s silence = stall
    stall_max_consecutive: int = Field(3, alias="BINANCE_WS_STALL_MAX_CONSECUTIVE")  # 3 consecutive = reconnect

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

    # Redis connection reliability settings
    socket_timeout: int = Field(30, alias="REDIS_SOCKET_TIMEOUT")  # Normal operations (30s)
    pubsub_socket_timeout: int = Field(60, alias="REDIS_PUBSUB_SOCKET_TIMEOUT")  # Blocking listen (60s)
    socket_keepalive: bool = Field(True, alias="REDIS_SOCKET_KEEPALIVE")
    max_consecutive_errors: int = Field(10, alias="REDIS_MAX_CONSECUTIVE_ERRORS")

    # Symbol-specific health monitor thresholds (in seconds)
    price_cache_monitor_thresholds: dict = Field(
        default={
            "BTCUSDT": 45,      # BTC: 45s (volatile, can have traffic gaps)
            "ETHUSDT": 45,      # ETH: 45s
            "SOLUSDT": 45,      # SOL: 45s
            "default": 60,      # Conservative 60s default
        },
        description="Symbol-specific silence thresholds for pub/sub health monitor"
    )


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


class SecuritySettings(BaseSettings):
    """Security-related settings for HMAC validation and notification freshness"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    redis_hmac_secret: str = Field("", alias="REDIS_HMAC_SECRET")
    notification_max_age_minutes: int = Field(5, alias="NOTIFICATION_MAX_AGE_MINUTES")
    require_enhanced_features: bool = Field(False, alias="REQUIRE_ENHANCED_FEATURES")


class TradingConfig(BaseSettings):
    """Trading configuration for fees, limits, and position sizing"""

    model_config = SettingsConfigDict(
        env_prefix="TRADING_",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    taker_fee_pct: float = Field(0.05, alias="TRADING_TAKER_FEE_PCT")
    maker_fee_pct: float = Field(0.02, alias="TRADING_MAKER_FEE_PCT")
    max_margin_per_position: float = Field(3000.0, alias="TRADING_MAX_MARGIN_PER_POSITION")
    max_leverage: int = Field(20, alias="TRADING_MAX_LEVERAGE")
    example_position_usd: float = Field(50000.0, alias="TRADING_EXAMPLE_POSITION_USD")


class ZAISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GLM_",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    api_key: str = Field("changeme")
    # Additional API keys for parallel processing (one per symbol)
    api_key_2: str = Field("65895bc22a0440f89c8fba9b03a239c9.86OSst0vZNrFevUb", alias="GLM_API_KEY_2")
    api_key_3: str = Field("b42cc5962a7346d49cf63541ce559a21.3acBWSE3GuN1QRCF", alias="GLM_API_KEY_3")
    base_url: AnyHttpUrl = Field("https://api.z.ai/api/coding/paas/v4")
    model: str = Field("glm-4.6")
    window_seconds: int = Field(5 * 60 * 60)
    max_requests: int = Field(1000)
    max_tokens: int = Field(16000, alias="GLM_MAX_TOKENS")

    # GLM Confidence thresholds for actions
    min_confidence_close_base: float = Field(95.0, alias="GLM_MIN_CONFIDENCE_CLOSE_BASE")
    min_confidence_close_profitable: float = Field(98.0, alias="GLM_MIN_CONFIDENCE_CLOSE_PROFITABLE")

    # GLM Schema Enhancement Feature Flags (Thesis/Antithesis/Synthesis)
    enable_thought_process: bool = Field(False, alias="GLM_FF_THOUGHT_PROCESS")
    enable_consistency_validation: bool = Field(False, alias="GLM_FF_CONSISTENCY")
    enable_consistency_enforcement: bool = Field(False, alias="GLM_FF_ENFORCE_CONSISTENCY")
    enable_dynamic_threshold: bool = Field(False, alias="GLM_FF_DYNAMIC_THRESHOLD")


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

    # =========================================================================
    # SWING TRADE SETTINGS
    # =========================================================================
    swing_trade_mode: bool = Field(True, alias="SWING_TRADE_MODE")
    swing_max_leverage: int = Field(7, alias="SWING_MAX_LEVERAGE")
    swing_primary_timeframe: str = Field("4h", alias="SWING_PRIMARY_TIMEFRAME")

    # Performance Tracking Override
    override_consecutive_losses: bool = Field(False, alias="OVERRIDE_CONSECUTIVE_LOSSES")

    # =========================================================================
    # CRASH PROTECTION SETTINGS (3-Tier System + HTF Confirmation)
    # =========================================================================
    crash_protection_enabled: bool = Field(True, alias="CRASH_PROTECTION_ENABLED")
    crash_protection_cooldown_minutes: int = Field(30, alias="CRASH_PROTECTION_COOLDOWN_MINUTES")
    crash_protection_htf_timeframe: str = Field("30m", alias="CRASH_HTF_TIMEFRAME")

    # Seviye 1: UYARI (1 dakika içinde drop - HTF ile doğrulama yapılır)
    crash_warning_1min_btc: float = Field(1.0, alias="CRASH_WARNING_1MIN_BTC")  # %1
    crash_warning_1min_eth: float = Field(1.5, alias="CRASH_WARNING_1MIN_ETH")  # %1.5
    crash_warning_1min_sol: float = Field(2.0, alias="CRASH_WARNING_1MIN_SOL")  # %2

    # Seviye 2: TEHLİKE (5 dakika içinde drop - %50 azalt)
    crash_danger_5min_btc: float = Field(3.0, alias="CRASH_DANGER_5MIN_BTC")  # %3
    crash_danger_5min_eth: float = Field(4.0, alias="CRASH_DANGER_5MIN_ETH")  # %4
    crash_danger_5min_sol: float = Field(5.0, alias="CRASH_DANGER_5MIN_SOL")  # %5

    # Seviye 3: KRİTİK (10 dakika içinde drop - TÜM pozisyonları kapat)
    crash_critical_10min_btc: float = Field(5.0, alias="CRASH_CRITICAL_10MIN_BTC")  # %5
    crash_critical_10min_eth: float = Field(7.0, alias="CRASH_CRITICAL_10MIN_ETH")  # %7
    crash_critical_10min_sol: float = Field(10.0, alias="CRASH_CRITICAL_10MIN_SOL")  # %10

    # Per-symbol cooldown for DANGER level (sadece o coin için)
    crash_danger_cooldown_minutes: int = Field(10, alias="CRASH_DANGER_COOLDOWN_MINUTES")
    
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
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    trading: TradingConfig = Field(default_factory=TradingConfig)


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
