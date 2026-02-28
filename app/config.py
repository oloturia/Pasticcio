# ============================================================
# app/config.py — application configuration
# ============================================================
#
# Pydantic Settings automatically reads environment variables
# (or the .env file) and validates them. If a required variable
# is missing, the app won't start and will show a clear error.
#
# Usage:
#   from app.config import settings
#   print(settings.instance_name)

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Pydantic Settings looks for these variables in the environment
    # or in the .env file (thanks to model_config below)

    # --- Database ---
    database_url: str

    # --- Redis ---
    redis_url: str = "redis://redis:6379/0"

    # --- Security ---
    secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # --- Instance ---
    instance_domain: str
    instance_name: str = "Pasticcio"
    instance_description: str = "A federated recipe social network"
    instance_contact: str = ""

    # --- Features ---
    enable_registrations: bool = True
    enable_nutrition: bool = True

    # --- Storage ---
    storage_backend: str = "local"  # "local" or "s3"
    media_root: str = "/app/media"

    # S3 (optional)
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # --- Environment ---
    environment: str = "development"
    debug: bool = False

    # Configuration: read from .env file if it exists,
    # but real environment variables take priority
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Singleton instance — import this throughout the project
settings = Settings()
