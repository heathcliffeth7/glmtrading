import json
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import AnyHttpUrl, AnyUrl, Field, validator
from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class BinanceSettings(BaseSettings):
    api_key: str = Field("changeme")
    api_secret: str = Field("changeme")
    websocket_endpoint: AnyUrl = Field("wss://stream.binance.com:9443/ws")
    futures_rest_endpoint: AnyHttpUrl = Field("https://fapi.binance.com")

    class Config:
        env_prefix = "BINANCE_"
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"


class TwelveDataSettings(BaseSettings):
    api_keys: List[str] = Field(default_factory=list, alias="TWELVE_DATA_API_KEYS")
    base_url: AnyHttpUrl = Field("https://api.twelvedata.com", alias="TWELVE_DATA_BASE_URL")

    @validator("api_keys", pre=True)
    @classmethod
    def parse_api_keys(cls, value):
        if value is None:
            return value
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed]
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"


class RedisSettings(BaseSettings):
    url: AnyUrl = Field("redis://localhost:6379/0", alias="REDIS_URL")


class InfluxSettings(BaseSettings):
    url: AnyHttpUrl = Field("http://localhost:8086")
    token: str = Field("changeme")
    org: str = Field("default")
    bucket: str = Field("trading")

    class Config:
        env_prefix = "INFLUX_"
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"


class QLibSettings(BaseSettings):
    data_path: str = Field("./qlib_data/binance", alias="QLIB_DATA_PATH")
    region: str = Field("us", alias="QLIB_REGION")


class ZAISettings(BaseSettings):
    api_key: str = Field("changeme")
    base_url: AnyHttpUrl = Field("https://api.z.ai/api/coding/paas/v4/chat/completions")
    model: str = Field("glm-4.6")
    window_seconds: int = Field(5 * 60 * 60)
    max_requests: int = Field(1000)

    class Config:
        env_prefix = "GLM_"
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"


class AppSettings(BaseSettings):
    environment: str = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="APP_LOG_LEVEL")
    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    telegram_channel_id: str = Field("", alias="TELEGRAM_CHANNEL_ID")
    use_nof1_style: bool = Field(False, alias="USE_NOF1_STYLE")
    
    # Position Monitor Settings
    enable_position_monitor: bool = Field(True, alias="ENABLE_POSITION_MONITOR")
    position_monitor_interval_seconds: int = Field(180, alias="POSITION_MONITOR_INTERVAL_SECONDS")  # 3 dakika
    position_monitor_check_invalidation: bool = Field(True, alias="POSITION_MONITOR_CHECK_INVALIDATION")
    
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    twelve_data: TwelveDataSettings = Field(default_factory=TwelveDataSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    influx: InfluxSettings = Field(default_factory=InfluxSettings)
    qlib: QLibSettings = Field(default_factory=QLibSettings)
    zai: ZAISettings = Field(default_factory=ZAISettings)

    class Config:
        env_file = str(ENV_FILE)
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"
        env_nested_delimiter = None


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
