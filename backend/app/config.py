from dataclasses import dataclass, field
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Path to the .env file the backend reads at startup. Resolved relative
# to cwd so it matches whatever pydantic-settings picks up below.
#   - docker (WORKDIR=/app, host-mounted ./.env:/app/.env): /app/.env
#   - host (`uvicorn` run from repo root):                  ./repo/.env
# `camgenium.py` writes rotated refresh tokens back to this same file.
ENV_FILE_PATH = Path(".env").resolve()


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://hydration:hydration@localhost:5432/hydration"

    # Camgenium harvester API. Leave the refresh token empty in dev —
    # backend will skip webhook registration and only serve the local
    # ingest endpoint (so fake_gateway.py still works).
    camgenium_base_url: str = "https://apisoftdev.l2s2.com"
    camgenium_token_url: str = (
        "https://keycloaksoftdev.l2s2.com/realms/SoftSilicon"
        "/protocol/openid-connect/token"
    )
    # Public OAuth client (no secret). Identified by `azp` claim in the
    # tokens issued by SoftSilicon's Keycloak realm.
    camgenium_client_id: str = "cg-harvester-public-api"
    # Long-lived refresh token issued for this account. Treat as a
    # password — never commit. The backend exchanges it for short-lived
    # access tokens on demand.
    camgenium_refresh_token: str = ""
    # Comma-separated Camgenium instrument identifiers to subscribe to.
    # Required by `POST /webhooks` — webhook registration is skipped
    # entirely when this is empty.
    camgenium_instrument_ids: str = ""
    # Webhook mode: 0 / 1. The two enum values aren't documented in the
    # schema; 0 is the safe default and likely means "forward raw data
    # packets". Change once we confirm with the supervisor.
    camgenium_webhook_mode: int = 0
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


# ---------------------------------------------------------------------------
# Hardware / session configuration (merged from root config.py on main).
#
# These dataclasses are passed to SessionManager, PlatformInteractionClassifier,
# AlertEngine, LedController, ObservationButton, etc. They are independent of
# the pydantic Settings above, which configures the backend service itself.
# ---------------------------------------------------------------------------


@dataclass
class SensorConfig:
    sampling_rate_hz: float = 20.0
    empty_threshold_g: float = 15.0
    noise_threshold_g: float = 5.0
    max_valid_weight_g: float = 5000.0
    stable_variance_threshold: float = 8.0
    stability_window_size: int = 20
    absent_timeout_s: float = 300.0
    meaningful_change_threshold_g: float = 10.0


@dataclass
class SessionConfig:
    default_daily_goal_ml: float = 2000.0
    fluid_density_g_per_ml: float = 1.0
    min_credible_volume_ml: float = 1.0
    max_credible_volume_ml: float = 500.0


@dataclass
class AlertConfig:
    no_drink_warning_s: float = 1800.0
    no_drink_urgent_s: float = 3600.0
    quiet_hours_start: int | None = 22
    quiet_hours_end: int | None = 7
    goal_reached_display_s: float = 10.0


@dataclass
class LedConfig:
    idle_brightness: float = 0.0
    reminder_brightness: float = 0.15
    urgent_brightness: float = 0.25
    goal_brightness: float = 0.3
    pulse_period_s: float = 4.0
    reminder_color: tuple[int, int, int] = field(default_factory=lambda: (255, 220, 180))
    urgent_color: tuple[int, int, int] = field(default_factory=lambda: (255, 200, 140))
    goal_color: tuple[int, int, int] = field(default_factory=lambda: (180, 255, 180))
    idle_color: tuple[int, int, int] = field(default_factory=lambda: (0, 0, 0))


@dataclass
class ButtonConfig:
    debounce_s: float = 0.3
    gpio_pin: int = 17


@dataclass
class SystemConfig:
    sensor: SensorConfig = field(default_factory=SensorConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    led: LedConfig = field(default_factory=LedConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)
