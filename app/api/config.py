"""Dashboard API Configuration"""
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class DashboardSettings(BaseSettings):
    """Dashboard-specific settings"""

    model_config = SettingsConfigDict(
        env_prefix="DASHBOARD_",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    api_host: str = Field("0.0.0.0", alias="DASHBOARD_API_HOST")
    api_port: int = Field(8001, alias="DASHBOARD_API_PORT")
    cors_origins: List[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://65.109.229.27:3000",
            "http://65.109.229.27:5173",
            "*"  # Allow all origins for development
        ]
    )
    ws_heartbeat_seconds: int = Field(30)


_dashboard_settings = None


def get_dashboard_settings() -> DashboardSettings:
    global _dashboard_settings
    if _dashboard_settings is None:
        _dashboard_settings = DashboardSettings()
    return _dashboard_settings
