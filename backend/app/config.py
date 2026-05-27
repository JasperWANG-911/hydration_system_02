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
    # Base warning (30 min) — used when no pace data is available.
    no_drink_warning_s: float = 1800.0
    # Shortened warning (15 min) — patient is significantly behind on pace.
    no_drink_warning_behind_s: float = 900.0
    # Extended warning (45 min) — patient is on pace or ahead.
    no_drink_warning_ahead_s: float = 2700.0
    # Absolute maximum (60 min) — always triggers, overrides quiet hours.
    no_drink_urgent_s: float = 3600.0
    # Pace deficit (ml) that classifies the patient as "significantly behind".
    behind_threshold_ml: float = 150.0
    quiet_hours_start: int | None = 22
    quiet_hours_end: int | None = 7


@dataclass
class LedConfig:
    idle_brightness: float = 0.0
    # REMINDER and the future URGENT tier both use the same amber colour;
    # brightness will differentiate them when the third state is added.
    reminder_brightness: float = 0.20
    pulse_period_s: float = 4.0
    # Amber (255, 165, 0) — warm, visible but not clinical / alarming.
    reminder_color: tuple[int, int, int] = field(default_factory=lambda: (255, 165, 0))
    idle_color: tuple[int, int, int] = field(default_factory=lambda: (0, 0, 0))


@dataclass
class PaceModelConfig:
    """Configuration for the expected-intake pace model.

    Default milestone curve (Addenbrooke's / nurse guidance):
      - 0 h  →   0 % of daily goal  (session start)
      - 6 h  →  53 % of daily goal  (~800 ml at 1500 ml minimum; midday anchor)
      - 16 h → 100 % of daily goal  (end of active day)

    Front-loads morning intake: morning rate ≈ 1.4 × the afternoon rate,
    matching the nurse's recommendation that patients reach roughly half
    their daily target by midday.
    """
    update_interval_s: float = 1800.0   # refresh display every 30 min
    daily_reset_hour: int = 6           # new session starts at 06:00
    active_day_hours: float = 16.0      # assumed waking hours (used by linear mode)
    # Grace period: no deficit is shown for this many seconds after session
    # start, so the display does not open with an immediate deficit.
    grace_period_s: float = 1800.0      # 30 min
    # Pace mode: "milestone" (default, piecewise linear) or "linear" (uniform).
    mode: str = "milestone"
    # Parallel lists defining the piecewise-linear target curve.
    # milestone_hours[i] = elapsed active hours at waypoint i.
    # milestone_fractions[i] = fraction of daily_goal expected by that time.
    # First entry must be (0, 0); last entry should be (active_day_hours, 1.0).
    milestone_hours: list = field(default_factory=lambda: [0.0, 6.0, 16.0])
    milestone_fractions: list = field(default_factory=lambda: [0.0, 0.53, 1.0])


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
