from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Supply Chain Coordinator")
    environment: str = os.getenv("APP_ENV", "development")
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")
    require_human_approval: bool = os.getenv("REQUIRE_HUMAN_APPROVAL", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_settings() -> Settings:
    return Settings()
