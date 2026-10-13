"""Runtime configuration, read from FOODWISE_* environment variables (or backend/.env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_PREFIX = "dev-only-"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FOODWISE_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://foodwise:foodwise@localhost:5433/foodwise"

    jwt_secret: str = "dev-only-jwt-secret-change-me-0123456789"
    link_secret: str = "dev-only-link-secret-change-me-0123456789"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    public_base_url: str = "http://localhost:5173"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    storage_dir: Path = Path("var/storage")

    # Runs the job worker inside the API process. Handy for a one-process demo;
    # production runs `foodwise worker` separately so training never competes with requests.
    inline_worker: bool = False

    weather_provider: Literal["open-meteo", "none"] = "open-meteo"
    routing_provider: Literal["estimate", "osrm"] = "estimate"
    osrm_url: str = "https://router.project-osrm.org"
    # Nearby places that feed people (OpenStreetMap via Overpass) and dish pictures (Wikimedia
    # Commons). Both are free and keyless; "none" turns them off (tests, air-gapped installs).
    places_provider: Literal["overpass", "none"] = "overpass"
    overpass_urls: list[str] = Field(default_factory=lambda: [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ])
    images_provider: Literal["wikimedia", "none"] = "wikimedia"
    # Wikimedia and OpenStreetMap ask every client to identify itself with a contact.
    contact: str = "https://github.com/foodwise"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "FoodWise <alerts@foodwise.local>"

    training_time_budget_s: int = 240
    # Folder holding the public Genpact food demand CSVs (train.csv, meal_info.csv,
    # fulfilment_center_info.csv) for the real-data benchmark. Relative to where the backend runs.
    genpact_dir: Path = Path("../data/genpact")
    log_level: str = "INFO"

    @property
    def is_dev(self) -> bool:
        return self.env == "development"

    @model_validator(mode="after")
    def _refuse_dev_secrets_in_production(self) -> Settings:
        if self.env == "production":
            for name in ("jwt_secret", "link_secret"):
                value = getattr(self, name)
                if value.startswith(DEV_SECRET_PREFIX) or len(value) < 32:
                    raise ValueError(f"FOODWISE_{name.upper()} must be set to a strong secret in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
