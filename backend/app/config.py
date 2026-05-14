from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://hydration:hydration@localhost:5432/hydration"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883

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
