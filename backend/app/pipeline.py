"""
Pipeline for the Hydration Monitoring System.

The pipeline is the top-level orchestrator. It owns the main loop,
wires all modules together, and controls the sampling rate. Nothing
else in the system knows about the loop or timing — they only respond
to calls.

Typical deployment::

    config = SystemConfig()
    bed = BedProfile(bed_id="ward-4-bed-7", daily_goal_ml=1500.0)

    pipeline = Pipeline(
        config=config,
        bed=bed,
        sensor=HX711SensorReader(config, data_pin=5, clock_pin=6),
        led=RgbLedController(config, pin=18),
        button=GpioObservationButton(config),
        record=JsonLinesHydrationRecord("records/"),
    )
    pipeline.run()

For development without hardware::

    pipeline = Pipeline.make_mock(config, bed)
    pipeline.sensor.push([500.0] * 40 + [0.0] * 10 + [430.0] * 40)
    pipeline.run(max_ticks=90)
"""

import logging
import time

from app.alert_engine import AlertEngine, AlertLevel
from app.config import SystemConfig
from app.hydration_record import HydrationRecord, JsonLinesHydrationRecord
from app.led_controller import LedController, MockLedController
from app.observation_button import MockObservationButton, ObservationButton
from app.patient_profile import BedProfile
from app.interactions.classifier import PlatformInteractionClassifier
from app.sensor_reader import MockSensorReader, SensorReader
from app.interactions.session import DrinkEvent, RefillEvent, SessionManager

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Main loop that wires sensor → classifier → session → alert → LED.

    Each tick of the loop:
    1. Reads one weight sample from the sensor.
    2. Feeds it to the classifier.
    3. Passes the result to the session manager.
    4. Drains any button observations and forwards them.
    5. Evaluates the alert engine.
    6. Applies the resulting level to the LED.
    7. Sleeps until the next sample is due.

    All modules are injected via the constructor so they can be swapped
    for mocks in tests without subclassing Pipeline itself.
    """

    def __init__(
        self,
        config: SystemConfig,
        bed: BedProfile,
        sensor: SensorReader,
        led: LedController,
        button: ObservationButton,
        record: HydrationRecord,
    ):
        self.config = config
        self.bed = bed
        self.sensor = sensor
        self.led = led
        self.button = button
        self.record = record

        self._classifier = PlatformInteractionClassifier(config)
        self._session = SessionManager(
            config=config,
            daily_goal_ml=bed.daily_goal_ml,
        )
        self._alert_engine = AlertEngine(config)
        self._running = False

        self._register_callbacks()

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

        Args:
            config: System configuration.
            bed: Bed profile to run under.
            storage_dir: Directory for the record file backend.

        Returns:
            A fully wired :class:`Pipeline` ready to call :meth:`run` on.
        """
        return cls(
            config=config,
            bed=bed,
            sensor=MockSensorReader(config),
            led=MockLedController(config),
            button=MockObservationButton(config),
            record=JsonLinesHydrationRecord(storage_dir),
        )

    def run(self, max_ticks: int | None = None) -> None:
        """
        Start the main sampling loop.

        Blocks until :meth:`stop` is called or ``max_ticks`` is reached.
        In production ``max_ticks`` is None and the loop runs until the
        process is terminated or ``stop()`` is called from another thread.

        Args:
            max_ticks: Maximum number of sensor reads before stopping.
                None means run indefinitely. Useful for testing.
        """
        self._running = True
        self._session.start()
        logger.info("Pipeline started for bed %s.", self.bed.bed_id)

        interval_s = 1.0 / self.config.sensor.sampling_rate_hz
        tick = 0

        try:
            while self._running:
                if max_ticks is not None and tick >= max_ticks:
                    break

                tick_start = time.monotonic()
                self._tick()
                tick += 1

                elapsed = time.monotonic() - tick_start
                sleep_s = interval_s - elapsed
                if sleep_s > 0:
                    time.sleep(sleep_s)

        except KeyboardInterrupt:
            logger.info("Pipeline interrupted by keyboard.")
        finally:
            self._shutdown()

    def stop(self) -> None:
        """
        Signal the pipeline to stop after the current tick completes.

        Safe to call from another thread.
        """
        self._running = False

    def _tick(self) -> None:
        weight = self.sensor.read_grams()
        result = self._classifier.update(weight)
        self._session.process(result)

        for observation in self.button.drain():
            self._alert_engine.record_button_press()
            self.record.write_observation(self.bed.bed_id, observation)
            logger.info(
                "Observation recorded for bed %s: %s",
                self.bed.bed_id,
                observation.note or "(no note)",
            )

        summary = self._session.summary()
        alert_state = self._alert_engine.evaluate(summary)
        self.led.apply(alert_state.level)

    def _register_callbacks(self) -> None:
        self._session.on_drink(self._on_drink)
        self._session.on_refill(self._on_refill)
        self._session.on_fault(
            lambda r: logger.error(
                "Sensor fault on bed %s: %s",
                self.bed.bed_id,
                r.metadata,
            )
        )

    def _on_drink(self, event: DrinkEvent) -> None:
        self.record.write_drink(self.bed.bed_id, event)
        logger.info(
            "Drink recorded for bed %s: %.0f ml (confidence %.2f).",
            self.bed.bed_id,
            event.volume_ml,
            event.confidence,
        )

    def _on_refill(self, event: RefillEvent) -> None:
        self.record.write_refill(self.bed.bed_id, event)
        logger.info(
            "Refill recorded for bed %s: %.0f ml added.",
            self.bed.bed_id,
            event.volume_added_ml,
        )

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