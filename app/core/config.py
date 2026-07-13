from __future__ import annotations

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the AI weather map service."""

    model_config = SettingsConfigDict(env_prefix="AI_WEATHER_", env_file=".env", extra="ignore")

    app_name: str = Field(default="AI Weather Map")
    app_version: str = Field(default="0.1.0")
    api_prefix: str = Field(default="/v1")
    stream_interval_seconds: float = Field(default=2.0, ge=0.1)
    default_grid_resolution: int = Field(default=7, ge=0)
    default_center_lat: float = Field(default=39.5)
    default_center_lon: float = Field(default=-98.35)
    weather_api_key: str = Field(default="")
    weather_api_base_url: str = Field(default="https://api.weatherapi.com")
    google_maps_api_key: str = Field(default="")
    groq_api_key: str = Field(default="")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
