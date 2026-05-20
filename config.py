"""
Central configuration for the Hydration Monitoring System.

All tuneable values live here. Nothing is hardcoded across other modules;
they import from this file or accept a :class:`SystemConfig` instance.
To adjust behaviour for a deployment, edit this file only.

For per-patient overrides (e.g. a different daily goal) see
:mod:`patient_profile`.
"""

from dataclasses import dataclass, field


@dataclass
class SensorConfig:
    """
    Load cell and ADC configuration.

    Attributes:
        sampling_rate_hz: How many weight readings to take per second.
            Typical range 10–40 Hz. Higher values improve responsiveness
            but increase CPU load on the Pi.
        empty_threshold_g: Platform is considered empty when stable
            average is below this value (grams).
        noise_threshold_g: Weight fluctuations smaller than this are
            treated as electrical noise and ignored.
        max_valid_weight_g: Readings above this are flagged as a sensor
            fault. Set comfortably above the heaviest likely cup + fluid.
        stable_variance_threshold: Population variance must stay below
            this for a reading window to be considered stable.
        stability_window_size: Number of samples used to assess stability.
            At 20 Hz, a window of 20 means 1 second of data.
        absent_timeout_s: Seconds before a missing cup triggers a timeout
            event. 300 s (5 min) is a reasonable default.
        meaningful_change_threshold_g: Net weight changes below this are
            classified as no meaningful change rather than a drink event.
    """

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
    """
    Session and fluid intake configuration.

    Attributes:
        default_daily_goal_ml: Default target fluid intake per day in ml.
            Overridden per patient by :class:`patient_profile.PatientProfile`.
        fluid_density_g_per_ml: Grams per ml for the monitored fluid.
            Use 1.0 for water, tea, or juice. Adjust for denser drinks.
        min_credible_volume_ml: Drink events smaller than this are
            discarded as noise.
        max_credible_volume_ml: Drink events larger than this are clamped.
            Guards against sensor spikes creating unrealistic totals.
    """

    default_daily_goal_ml: float = 2000.0
    fluid_density_g_per_ml: float = 1.0
    min_credible_volume_ml: float = 1.0
    max_credible_volume_ml: float = 500.0


@dataclass
class AlertConfig:
    """
    Alert thresholds and timing configuration.

    Attributes:
        no_drink_warning_s: Seconds since last drink before the LED
            switches to the reminder state. Default 30 minutes.
        no_drink_urgent_s: Seconds since last drink before the LED
            switches to the urgent reminder state. Default 60 minutes.
        quiet_hours_start: Hour (24h, local time) at which the LED is
            suppressed overnight. Set to None to disable quiet hours.
        quiet_hours_end: Hour at which the LED resumes after quiet hours.
        goal_reached_display_s: How long the goal-reached pulse plays
            before the LED returns to the idle state. 
    """

    no_drink_warning_s: float = 1800.0
    no_drink_urgent_s: float = 3600.0
    quiet_hours_start: int | None = 22
    quiet_hours_end: int | None = 7
    goal_reached_display_s: float = 10.0


@dataclass
class LedConfig:
    """
    Cactus LED behaviour configuration.

    All brightness values are normalised 0.0–1.0. The LED hardware
    driver scales these to the appropriate PWM range.

    Attributes:
        idle_brightness: Brightness when no reminder is needed.
            0.0 turns the LED fully off when the patient is on track.
        reminder_brightness: Brightness during a gentle drink reminder.
        urgent_brightness: Brightness during an urgent drink reminder.
        goal_brightness: Brightness for the brief goal-reached pulse.
        pulse_period_s: Duration of one full breathe-in/breathe-out
            cycle in seconds. Longer values feel calmer.
        reminder_color: RGB tuple (0–255 each) for the reminder state.
            Warm white by default — visible but non-clinical.
        urgent_color: RGB tuple for the urgent reminder state.
            Slightly warmer/brighter than reminder, still not alarming.
        goal_color: RGB tuple for the goal-reached celebration pulse.
        idle_color: RGB tuple when the LED is in idle state.
    """

    idle_brightness: float = 0.0
    reminder_brightness: float = 0.15
    urgent_brightness: float = 0.25
    goal_brightness: float = 0.3
    pulse_period_s: float = 4.0
    reminder_color: tuple[int, int, int] = field(
        default_factory=lambda: (255, 220, 180)
    )
    urgent_color: tuple[int, int, int] = field(
        default_factory=lambda: (255, 200, 140)
    )
    goal_color: tuple[int, int, int] = field(
        default_factory=lambda: (180, 255, 180)
    )
    idle_color: tuple[int, int, int] = field(
        default_factory=lambda: (0, 0, 0)
    )


@dataclass
class ButtonConfig:
    """
    Observation button configuration.

    Attributes:
        debounce_s: Minimum seconds between two registered presses.
            Prevents a single physical press from registering multiple
            times due to contact bounce.
        gpio_pin: GPIO pin number on the Raspberry Pi that the button
            is wired to. BCM numbering.
    """

    debounce_s: float = 0.3
    gpio_pin: int = 17


@dataclass
class SystemConfig:
    """
    Top-level configuration container for the full system.

    Pass one instance of this class to any module that needs
    configuration. Modules should not import sub-configs directly;
    they should accept a :class:`SystemConfig` and read from it.

    Example::

        config = SystemConfig()
        config.alert.no_drink_warning_s = 900  # 15 min for a specific ward
        manager = SessionManager(config)
    """

    sensor: SensorConfig = field(default_factory=SensorConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    led: LedConfig = field(default_factory=LedConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)