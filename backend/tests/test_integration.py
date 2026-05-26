"""
Integration tests for the DRIP Hydration Monitoring System.

Wires the full pipeline together using mock hardware and verifies that
button press events flow correctly from the button box through to
recorded drink events, display updates, and LED state changes.

No physical hardware is required. Tests use:
  - MockButtonBox    — simulates button presses
  - MockLedController — records LED state changes
  - MockDisplayDriver — records display updates
  - JsonLinesHydrationRecord — writes to a tmp_path directory
"""

import time
import pytest

from app.alert_engine import AlertLevel
from app.config import SystemConfig
from app.patient_profile import BedProfile
from app.pipeline import Pipeline
from app.hydration_record import JsonLinesHydrationRecord
from app.input_buttons import MockButtonBox
from app.led_controller import MockLedController
from app.display_driver import MockDisplayDriver


@pytest.fixture
def mock_pipeline(config, bed, tmp_path):
    """Return a fully mocked pipeline ready to run."""
    return Pipeline(
        config=config,
        bed=bed,
        buttons=MockButtonBox(config),
        led=MockLedController(config),
        display=MockDisplayDriver(config),
        record=JsonLinesHydrationRecord(tmp_path),
    )


class TestButtonIntakeFlow:
    def test_single_plus_press_records_drink(self, mock_pipeline, config, tmp_path):
        pipeline = mock_pipeline
        pipeline._session.start()
        # Press in the past so the aggregation window is already expired
        # by the time drain_intake() calls time.time().
        t = time.time() - config.button.aggregation_window_s - 1

        pipeline.buttons.press_plus(now=t)
        # drain_intake() evaluates the window lazily; since t is in the past
        # the window is already expired → event is committed immediately.
        events = pipeline.buttons.drain_intake()

        assert len(events) >= 1
        assert events[0].volume_ml == pytest.approx(50.0)

    def test_minus_reduces_pending_delta(self, config):
        from app.input_buttons import MockButtonBox
        box = MockButtonBox(config)
        t = time.time()
        box.press_plus(now=t)
        box.press_plus(now=t + 1)
        box.press_minus(now=t + 2)
        # Force window close
        box.press_plus(now=t + config.button.aggregation_window_s + 1)
        events = box.drain_intake()
        # First window: +50 +50 -50 = 50ml; second window opens with +50
        assert events[0].volume_ml == pytest.approx(50.0)

    def test_minus_cannot_go_below_zero(self, config):
        from app.input_buttons import MockButtonBox
        box = MockButtonBox(config)
        t = time.time()
        box.press_plus(now=t)
        box.press_minus(now=t + 1)
        box.press_minus(now=t + 2)  # extra minus — should not go negative
        # Force commit
        box.press_plus(now=t + config.button.aggregation_window_s + 1)
        events = box.drain_intake()
        # First window: +50 -50 -50(clamped) = 0, not committed
        assert len(events) == 0 or events[0].volume_ml >= 0


class TestSleepToggle:
    def test_sleep_button_toggles_sleeping(self, config):
        from app.input_buttons import MockButtonBox
        box = MockButtonBox(config)
        assert box.sleeping is False
        box.press_sleep()
        assert box.sleeping is True
        box.press_sleep()
        assert box.sleeping is False

    def test_sleep_event_drained(self, config):
        from app.input_buttons import MockButtonBox
        box = MockButtonBox(config)
        box.press_sleep()
        events = box.drain_sleep()
        assert len(events) == 1
        assert events[0].sleeping is True


class TestDisplayUpdates:
    def test_display_updates_after_intake(self, mock_pipeline, config, tmp_path):
        pipeline = mock_pipeline
        pipeline._session.start()

        t = time.time()
        pipeline.buttons.press_plus(now=t)
        # Force window commit by pressing well past window
        pipeline.buttons._force_commit(t + config.button.aggregation_window_s + 1)
        pipeline._session.record_intake(50.0, now_ts=t)

        # on_drink callback should have updated the display
        state = pipeline.display.last_state()
        assert state is not None
        assert state.actual_ml == pytest.approx(50.0)


class TestPaceModel:
    def test_expected_zero_at_start(self, config, bed):
        from app.pace_model import PaceModel
        pm = PaceModel(config, bed)
        assert pm.expected_by_now(0.0) == pytest.approx(0.0)

    def test_expected_grows_with_time(self, config, bed):
        from app.pace_model import PaceModel
        pm = PaceModel(config, bed)
        early = pm.expected_by_now(3600.0)
        later = pm.expected_by_now(7200.0)
        assert later > early

    def test_deficit_zero_when_ahead(self, config, bed):
        from app.pace_model import PaceModel
        pm = PaceModel(config, bed)
        # 0 elapsed → 0 expected → 0 deficit regardless of actual
        assert pm.deficit(actual_ml=500.0, active_elapsed_s=0.0) == pytest.approx(0.0)

    def test_deficit_positive_when_behind(self, config, bed):
        from app.pace_model import PaceModel
        pm = PaceModel(config, bed)
        # After 8 hours: expect ~500ml (linear, 1000ml goal, 16hr day)
        deficit = pm.deficit(actual_ml=0.0, active_elapsed_s=8 * 3600)
        assert deficit > 0
