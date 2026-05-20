from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://hydration:hydration@localhost:5432/hydration"

    # Camgenium harvester API. Leave empty in dev — backend will skip
    # webhook registration and only serve the local ingest endpoint.
    camgenium_base_url: str = ""
    camgenium_token_url: str = ""
    camgenium_client_id: str = ""
    camgenium_client_secret: str = ""
    # Public URL Camgenium should POST measurements to. Set this to your
    # ngrok / tunnel URL during demos, e.g. "https://abc123.ngrok.io".
    public_ingest_url: str = ""
    # Shared secret Camgenium signs webhook deliveries with; the ingest
    # route rejects requests whose signature header doesn't match.
    ingest_shared_secret: str = ""
    # Keepalive cadence for the registered webhook subscription.
    webhook_keepalive_seconds: int = 300

    # Alert thresholds
    no_drink_alert_hours: int = 3
    waking_start_hour: int = 8
    waking_end_hour: int = 22
    evening_check_hour: int = 18
    evening_min_target_fraction: float = 0.5
    device_offline_minutes: int = 10

    # Sensor → intake conversion (1g of water ≈ 1ml)
    g_to_ml: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
