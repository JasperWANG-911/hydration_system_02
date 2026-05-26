"""
Pipeline for the DRIP Hydration Monitoring System.

The pipeline is the top-level orchestrator for the bedside unit. It owns
the main loop, wires all modules together, and manages session lifecycle
including daily resets and nap pauses.

Each tick of the loop:
1. Check whether a daily reset is due (06:00 auto-reset).
2. Drain sleep toggle events → pause/resume session, dim/wake display.
3. Drain intake events → record in session, update display immediately.
4. Refresh pace model display on the configured interval (default 30 min).
5. Evaluate alert engine → apply resulting level to the cactus LED.

Typical deployment::

    config = SystemConfig()
    bed = BedProfile(bed_id="ward-4-bed-7", daily_goal_ml=1500.0)

    pipeline = Pipeline(
        config=config,
        bed=bed,
        buttons=GpioButtonBox(config),
        led=RgbLedController(config, pin=18),
        display=I2cDisplayDriver(config),
        record=JsonLinesHydrationRecord("records/"),
    )
    pipeline.run()

For development without hardware::

    pipeline = Pipeline.make_mock(config, bed)
    pipeline.run(max_ticks=100)
"""

import datetime
import logging
import time

from app.alert_engine import AlertEngine, AlertLevel
from app.config import SystemConfig
from app.display_driver import DisplayDriver, MockDisplayDriver
from app.hydration_record import HydrationRecord, JsonLinesHydrationRecord
from app.input_buttons import ButtonBox, IntakeEvent, MockButtonBox, SleepToggleEvent
from app.interactions.session import DrinkEvent, SessionManager
from app.led_controller import LedController, MockLedController
from app.pace_model import PaceModel
from app.patient_profile import BedProfile

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Main loop that wires buttons → session → alert → LED + display.

    All modules are injected via the constructor so they can be swapped
    for mocks in tests without subclassing Pipeline.
    """

    def __init__(
        self,
        config: SystemConfig,
        bed: BedProfile,
        buttons: ButtonBox,
        led: LedController,
        display: DisplayDriver,
        record: HydrationRecord,
    ):
        self.config = config
        self.bed = bed
        self.buttons = buttons
        self.led = led
        self.display = display
        self.record = record

        self._session = SessionManager(config, daily_goal_ml=bed.daily_goal_ml)
        self._pace_model = PaceModel(config, bed)
        self._alert_engine = AlertEngine(config, daily_goal_ml=bed.daily_goal_ml)
        self._running = False

        self._cached_expected_ml: float = 0.0
        self._last_pace_refresh: float = 0.0
        self._last_reset_date: datetime.date | None = None

        self._session.on_drink(self._on_drink)

    @classmethod
    def make_mock(
        cls,
        config: SystemConfig,
        bed: BedProfile,
        storage_dir: str = "records/",
    ) -> "Pipeline":
        """
        Construct a Pipeline with all hardware replaced by mocks.

        Useful for development, integration tests, and CI where no
        physical hardware is present.
        """
        return cls(
            config=config,
            bed=bed,
            buttons=MockButtonBox(config),
            led=MockLedController(config),
            display=MockDisplayDriver(config),
            record=JsonLinesHydrationRecord(storage_dir),
        )

    def run(self, max_ticks: int | None = None) -> None:
        """
        Start the main loop.

        Blocks until :meth:`stop` is called or ``max_ticks`` is reached.

        Args:
            max_ticks: Maximum ticks before stopping. None means run
                indefinitely. Useful for testing.
        """
        self._running = True
        self._session.start()
        self._last_reset_date = datetime.date.today()
        self.display.show_startup(self.bed.bed_id)
        self._refresh_pace()
        logger.info("Pipeline started for bed %s.", self.bed.bed_id)

        tick = 0
        tick_interval_s = 0.1  # 10 Hz — responsive to button presses

        try:
            while self._running:
                if max_ticks is not None and tick >= max_ticks:
                    break
                tick_start = time.monotonic()
                self._tick()
                tick += 1
                elapsed = time.monotonic() - tick_start
                sleep_s = tick_interval_s - elapsed
                if sleep_s > 0:
                    time.sleep(sleep_s)
        except KeyboardInterrupt:
            logger.info("Pipeline interrupted by keyboard.")
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the pipeline to stop after the current tick completes."""
        self._running = False

    # -------------------------------------------------------------------------
    # Internal tick logic
    # -------------------------------------------------------------------------

    def _tick(self) -> None:
        now = time.time()

        self._maybe_daily_reset(now)

        for sleep_event in self.buttons.drain_sleep():
            self._handle_sleep_toggle(sleep_event)

        if not self.buttons.sleeping:
            for intake_event in self.buttons.drain_intake():
                self._session.record_intake(
                    intake_event.volume_ml, now_ts=intake_event.timestamp
                )
                # Display updates immediately on intake via the on_drink callback.

            summary = self._session.summary()
            alert_state = self._alert_engine.evaluate(summary)
            self.led.apply(alert_state.level)
        else:
            # Discard any intake events that arrived while sleeping.
            self.buttons.drain_intake()

        if now - self._last_pace_refresh >= self.config.pace_model.update_interval_s:
            self._refresh_pace()

    def _handle_sleep_toggle(self, event: SleepToggleEvent) -> None:
        self.record.write_sleep_event(self.bed.bed_id, event)
        if event.sleeping:
            try:
                self._session.pause()
            except RuntimeError:
                pass  # already paused
            self.display.dim()
            self.led.off()
            logger.info("Bed %s entered sleep/nap mode.", self.bed.bed_id)
        else:
            try:
                self._session.resume()
            except RuntimeError:
                pass  # already active
            self.display.wake()
            self._refresh_pace()
            logger.info("Bed %s woke from sleep/nap mode.", self.bed.bed_id)

    def _on_drink(self, event: DrinkEvent) -> None:
        self.record.write_drink(self.bed.bed_id, event)
        # Update the display immediately so the patient sees their new total.
        summary = self._session.summary()
        self.display.update(summary.total_consumed_ml, self._cached_expected_ml)
        logger.info(
            "Drink recorded for bed %s: %.0f ml (session total: %.0f ml).",
            self.bed.bed_id,
            event.volume_ml,
            summary.total_consumed_ml,
        )

    def _refresh_pace(self) -> None:
        summary = self._session.summary()
        self._cached_expected_ml = self._pace_model.expected_by_now(summary.duration_s)
        self.display.update(summary.total_consumed_ml, self._cached_expected_ml)
        self._last_pace_refresh = time.time()

    def _maybe_daily_reset(self, now: float) -> None:
        reset_hour = self.config.pace_model.daily_reset_hour
        dt = datetime.datetime.fromtimestamp(now)
        today = dt.date()
        reset_time = datetime.datetime.combine(today, datetime.time(reset_hour))
        if dt >= reset_time and (
            self._last_reset_date is None or self._last_reset_date < today
        ):
            self._do_daily_reset(now)

    def _do_daily_reset(self, now: float) -> None:
        logger.info(
            "Daily reset for bed %s at %s.",
            self.bed.bed_id,
            datetime.datetime.fromtimestamp(now).strftime("%H:%M"),
        )
        try:
            self._session.end()
            summary = self._session.summary()
            self.record.write_session_summary(self.bed.bed_id, summary)
        except RuntimeError:
            pass

        self._session = SessionManager(
            self.config, daily_goal_ml=self.bed.daily_goal_ml
        )
        self._session.on_drink(self._on_drink)
        self._alert_engine = AlertEngine(
            self.config, daily_goal_ml=self.bed.daily_goal_ml
        )
        self._session.start()
        self._last_reset_date = datetime.date.today()
        self._cached_expected_ml = 0.0
        self._refresh_pace()

    def _shutdown(self) -> None:
        try:
            self._session.end()
            summary = self._session.summary()
            self.record.write_session_summary(self.bed.bed_id, summary)
            logger.info(
                "Session ended for bed %s. Total consumed: %.0f ml.",
                self.bed.bed_id,
                summary.total_consumed_ml,
            )
        except RuntimeError as e:
            logger.warning("Session shutdown error: %s", e)
        finally:
            self.led.off()
