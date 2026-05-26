from dataclasses import dataclass, field
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Path to the .env file the backend reads at startup.
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
    camgenium_client_id: str = "cg-harvester-public-api"
    camgenium_refresh_token: str = ""
    camgenium_instrument_ids: str = ""
    camgenium_webhook_mode: int = 0
    public_ingest_url: str = ""
    ingest_shared_secret: str = ""
    webhook_keepalive_seconds: int = 300

    # Alert thresholds
    no_drink_alert_hours: int = 3
    waking_start_hour: int = 8
    waking_end_hour: int = 22
    evening_check_hour: int = 18
    evening_min_target_fraction: float = 0.5
    device_offline_minutes: int = 10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


# ---------------------------------------------------------------------------
# Hardware / session configuration.
#
# These dataclasses are passed to SessionManager, AlertEngine, LedController,
# ButtonBox, PaceModel, and DisplayDriver. They are independent of the
# pydantic Settings above, which configures the backend service itself.
# ---------------------------------------------------------------------------


@dataclass
class ButtonConfig:
    """GPIO pin assignments and timing for the three bedside buttons.

    All pin numbers default to 0 (unset) so that any attempt to use them
    without explicit configuration fails loudly rather than silently
    driving the wrong pin. Set them to match your physical wiring.
    """
    plus_pin: int = 14              # + button: pull-up, active LOW
    minus_pin: int = 15             # - button: pull-up, active LOW
    sleep_pin: int = 16             # sleep/wake toggle: pull-up, active LOW
    debounce_s: float = 0.3         # minimum seconds between recognised presses
    step_ml: float = 50.0           # ml recorded per button press
    aggregation_window_s: float = 15.0  # presses within this window form one drink event


@dataclass
class SessionConfig:
    default_daily_goal_ml: float = 2000.0


@dataclass
class AlertConfig:
    no_drink_warning_s: float = 1800.0   # 30 min → REMINDER
    no_drink_urgent_s: float = 3600.0    # 60 min → URGENT
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
class PaceModelConfig:
    """Configuration for the expected-intake pace model."""
    update_interval_s: float = 1800.0   # refresh display every 30 min
    daily_reset_hour: int = 6           # new session starts at 06:00
    active_day_hours: float = 16.0      # assumed waking hours for linear model
    weighted: bool = False              # use meal-weighted curve instead of linear


@dataclass
class DisplayConfig:
    """I2C display wiring. Pins default to 0 (unset) — set to match wiring."""
    i2c_sda_pin: int = 4
    i2c_scl_pin: int = 5
    i2c_address: int = 0x27     # PCF8574 I2C backpack: 0x27 or 0x3F
    dim_on_sleep: bool = True   # reduce backlight when in nap/sleep mode


@dataclass
class SystemConfig:
    session: SessionConfig = field(default_factory=SessionConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    led: LedConfig = field(default_factory=LedConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)
    pace_model: PaceModelConfig = field(default_factory=PaceModelConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
