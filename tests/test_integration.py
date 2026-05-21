"""
Integration tests for the Hydration Monitoring System.

These tests wire the full pipeline together using mock hardware and
verify that events flow correctly from a simulated weight sequence all
the way through to recorded drink events and LED state changes.

No physical hardware is required. Tests use:
- MockSensorReader — injected weight sequences
- MockLedController — records LED state changes
- MockObservationButton — simulates button presses
- JsonLinesHydrationRecord — writes to a tmp_path directory
"""

import time
import pytest

from alert_engine import AlertLevel
from config import SystemConfig
from led_controller import MockLedController
from observation_button import MockObservationButton
from patient_profile import BedProfile
from pipeline import Pipeline
from hydration_record import JsonLinesHydrationRecord
from sensor_reader import MockSensorReader


@pytest.fixture
def mock_pipeline(config, bed, tmp_path):
    """Return a fully mocked pipeline ready to run."""
    pipeline = Pipeline(
        config=config,
        bed=bed,
        sensor=MockSensorReader(config),
        led=MockLedController(config),
        button=MockObservationButton(config),
        record=JsonLinesHydrationRecord(tmp_path),
    )
    return pipeline


def push_drink_sequence(sensor, pre_g=450.0, post_g=380.0, stable_n=25, absent_n=5):
    """Push a standard lift-drink-return weight sequence to a mock sensor."""
    sensor.push([pre_g] * stable_n + [0.0] * absent_n + [post_g] * stable_n)


class TestFullDrinkCycle:
    def test_drink_event_written_to_record(self, mock_pipeline, tmp_path, bed):
        push_drink_sequence(mock_pipeline.sensor)
        mock_pipeline.run(max_ticks=55)

        record = JsonLinesHydrationRecord(tmp_path)
        drinks = record.read_drinks(bed.bed_id)
        assert len(drinks) >= 1
        assert drinks[0].volume_ml == pytest.approx(70.0, abs=5.0)

    def test_session_summary_written_on_shutdown(self, mock_pipeline, tmp_path, bed):
        push_drink_sequence(mock_pipeline.sensor)
        mock_pipeline.run(max_ticks=55)

        # Verify the record file exists and has content
        record_file = list(tmp_path.iterdir())
        assert len(record_file) == 1
        content = record_file[0].read_text()
        assert "session_summary" in content

    def test_no_drink_event_on_empty_platform(self, mock_pipeline, tmp_path, bed):
        mock_pipeline.sensor.push([0.0] * 55)
        mock_pipeline.run(max_ticks=55)

        record = JsonLinesHydrationRecord(tmp_path)
        drinks = record.read_drinks(bed.bed_id)
        assert len(drinks) == 0


class TestLedBehaviour:
    def test_led_turns_off_on_shutdown(self, mock_pipeline):
        mock_pipeline.sensor.push([450.0] * 30)
        mock_pipeline.run(max_ticks=30)
        last = mock_pipeline.led.last_state()
        assert last.level == AlertLevel.IDLE
        assert last.brightness == 0.0

    def test_led_shows_reminder_after_long_absence(self, config, bed, tmp_path):
        # Set a very short warning window so we can test without waiting
        config.alert.no_drink_warning_s = 0.01
        pipeline = Pipeline(
            config=config,
            bed=bed,
            sensor=MockSensorReader(config),
            led=MockLedController(config),
            button=MockObservationButton(config),
            record=JsonLinesHydrationRecord(tmp_path),
        )
        # Feed a drink cycle, then stable cup with no further drinks
        push_drink_sequence(pipeline.sensor, pre_g=450.0, post_g=380.0)
        pipeline.sensor.push([380.0] * 30)
        pipeline.run(max_ticks=85)

        levels = [s.level for s in pipeline.led.history()]
        assert AlertLevel.REMINDER in levels or AlertLevel.URGENT in levels


class TestObservationButton:
    def test_button_press_recorded_to_file(self, mock_pipeline, tmp_path, bed):
        mock_pipeline.sensor.push([450.0] * 30)
        mock_pipeline.button.press(note="Patient refused water")
        mock_pipeline.run(max_ticks=30)

        record = JsonLinesHydrationRecord(tmp_path)
        observations = record.read_observations(bed.bed_id)
        assert len(observations) == 1
        assert observations[0].note == "Patient refused water"

    def test_multiple_button_presses_all_recorded(
        self, config, mock_pipeline, tmp_path, bed
    ):
        config.button.debounce_s = 0.0
        mock_pipeline.sensor.push([450.0] * 30)
        mock_pipeline.button.press(note="First observation")
        mock_pipeline.button.press(note="Second observation")
        mock_pipeline.run(max_ticks=30)

        record = JsonLinesHydrationRecord(tmp_path)
        observations = record.read_observations(bed.bed_id)
        assert len(observations) == 2


class TestMultipleDrinkCycles:
    def test_two_drinks_accumulate_correctly(self, mock_pipeline, tmp_path, bed):
        # First drink: 450 -> 380 = 70 ml
        push_drink_sequence(mock_pipeline.sensor, pre_g=450.0, post_g=380.0)
        # Second drink: 380 -> 310 = 70 ml
        push_drink_sequence(mock_pipeline.sensor, pre_g=380.0, post_g=310.0)

        mock_pipeline.run(max_ticks=110)

        record = JsonLinesHydrationRecord(tmp_path)
        drinks = record.read_drinks(bed.bed_id)
        total = sum(d.volume_ml for d in drinks)
        assert total == pytest.approx(140.0, abs=10.0)


class TestSensorFaultHandling:
    def test_fault_does_not_crash_pipeline(self, mock_pipeline):
        # Inject a fault reading mid-sequence
        mock_pipeline.sensor.push([450.0] * 10 + [-200.0] * 5 + [450.0] * 15)
        # Should complete without raising
        mock_pipeline.run(max_ticks=30)

    def test_fault_does_not_produce_drink_event(
        self, mock_pipeline, tmp_path, bed
    ):
        mock_pipeline.sensor.push([-200.0] * 30)
        mock_pipeline.run(max_ticks=30)

        record = JsonLinesHydrationRecord(tmp_path)
        drinks = record.read_drinks(bed.bed_id)
        assert len(drinks) == 0