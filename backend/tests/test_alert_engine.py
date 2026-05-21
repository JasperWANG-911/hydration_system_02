"""Unit tests for AlertEngine."""

import time
import pytest
from app.alert_engine import AlertEngine, AlertLevel
from app.config import SystemConfig
from app.interactions.session import SessionState, SessionSummary


def make_summary(
    last_drink_time=None,
    total_consumed_ml=0.0,
    drink_count=0,
):
    return SessionSummary(
        session_state=SessionState.ACTIVE,
        total_consumed_ml=total_consumed_ml,
        drink_count=drink_count,
        refill_count=0,
        start_time=time.time() - 60,
        duration_s=60.0,
        last_drink_time=last_drink_time,
    )


@pytest.fixture
def engine(config):
    return AlertEngine(config)


class TestAlertLevels:
    def test_idle_when_recently_drank(self, engine):
        summary = make_summary(last_drink_time=time.time() - 5)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.IDLE

    def test_reminder_after_warning_threshold(self, engine, config):
        # config fixture sets no_drink_warning_s = 10
        t = time.time() - config.alert.no_drink_warning_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER

    def test_urgent_after_urgent_threshold(self, engine, config):
        t = time.time() - config.alert.no_drink_urgent_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.URGENT

    def test_reminder_when_no_drink_ever_recorded(self, engine):
        summary = make_summary(last_drink_time=None)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER


class TestQuietHours:
    def test_suppresses_reminder_during_quiet_hours(self, config):
        config.alert.quiet_hours_start = 0
        config.alert.quiet_hours_end = 23
        engine = AlertEngine(config)
        # long time since last drink — would normally be REMINDER
        t = time.time() - config.alert.no_drink_warning_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.IDLE

    def test_urgent_not_suppressed_during_quiet_hours(self, config):
        config.alert.quiet_hours_start = 0
        config.alert.quiet_hours_end = 23
        engine = AlertEngine(config)
        t = time.time() - config.alert.no_drink_urgent_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.URGENT

    def test_no_quiet_hours_when_disabled(self, config):
        # conftest already sets quiet_hours_start/end to None
        engine = AlertEngine(config)
        t = time.time() - config.alert.no_drink_warning_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER


class TestObservationButton:
    def test_button_press_sets_pending(self, engine):
        summary = make_summary(last_drink_time=time.time())
        engine.record_button_press()
        state = engine.evaluate(summary)
        assert state.observation_pending is True

    def test_acknowledge_clears_pending(self, engine):
        summary = make_summary(last_drink_time=time.time())
        engine.record_button_press()
        ts = engine.acknowledge_observation()
        state = engine.evaluate(summary)
        assert state.observation_pending is False
        assert ts is not None

    def test_acknowledge_with_no_press_returns_none(self, engine):
        result = engine.acknowledge_observation()
        assert result is None


class TestMetadata:
    def test_time_since_drink_in_state(self, engine):
        t = time.time() - 30
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.time_since_last_drink_s == pytest.approx(30.0, abs=1.0)

    def test_no_drink_time_is_none(self, engine):
        summary = make_summary(last_drink_time=None)
        state = engine.evaluate(summary)
        assert state.time_since_last_drink_s is None