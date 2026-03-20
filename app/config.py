# ============================================================
# app/config.py — application configuration
# ============================================================

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
	# --- Testing environment ---
    testing: bool = False
    
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

    # --- Comments moderation ---
    # "off" → federated comments are published immediately
    # "on"  → federated comments arrive as "pending" and require
    #          approval from the recipe author before becoming visible
    comments_moderation: str = "off"

    # --- AP inbox rate limiting ---
    # Per-IP: max requests allowed in the time window (seconds)
    inbox_ratelimit_ip_max: int = 300
    inbox_ratelimit_ip_window: int = 300  # 5 minutes
    # Per-domain: max requests from the same remote server
    inbox_ratelimit_domain_max: int = 600
    inbox_ratelimit_domain_window: int = 300  # 5 minutes
    
    # --- API rate limiting ---
    api_ratelimit_ip_max: int = 60
    api_ratelimit_ip_window: int = 60
    api_ratelimit_user_max: int = 300
    api_ratelimit_user_window: int = 60

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
