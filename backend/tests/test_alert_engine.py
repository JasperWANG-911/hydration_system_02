"""Unit tests for AlertEngine."""

import time
import pytest
from app.alert_engine import AlertEngine, AlertLevel
from app.config import SystemConfig
from app.interactions.session import SessionState, SessionSummary

DAILY_GOAL_ML = 2000.0


def make_summary(
    last_drink_time=None,
    total_consumed_ml=0.0,
    drink_count=0,
):
    return SessionSummary(
        session_state=SessionState.ACTIVE,
        total_consumed_ml=total_consumed_ml,
        drink_count=drink_count,
        start_time=time.time() - 60,
        duration_s=60.0,
        last_drink_time=last_drink_time,
    )


@pytest.fixture
def engine(config):
    return AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)


class TestAlertLevels:
    def test_idle_when_recently_drank(self, engine):
        summary = make_summary(last_drink_time=time.time() - 5)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.IDLE

    def test_reminder_after_warning_threshold(self, engine, config):
        t = time.time() - config.alert.no_drink_warning_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER

    def test_reminder_after_urgent_threshold(self, engine, config):
        """Urgent threshold also maps to REMINDER (single LED state for now)."""
        t = time.time() - config.alert.no_drink_urgent_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER

    def test_reminder_when_no_drink_ever_recorded(self, engine):
        summary = make_summary(last_drink_time=None)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER

    def test_idle_when_goal_reached(self, engine):
        """Goal reached → LED off (IDLE), not a separate visual state."""
        summary = make_summary(total_consumed_ml=DAILY_GOAL_ML)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.IDLE


class TestQuietHours:
    def test_suppresses_reminder_during_quiet_hours(self, config):
        config.alert.quiet_hours_start = 0
        config.alert.quiet_hours_end = 23
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        t = time.time() - config.alert.no_drink_warning_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.IDLE

    def test_reminder_not_suppressed_past_urgent_threshold_during_quiet_hours(self, config):
        """Very long absence still fires REMINDER even during quiet hours."""
        config.alert.quiet_hours_start = 0
        config.alert.quiet_hours_end = 23
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        t = time.time() - config.alert.no_drink_urgent_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER

    def test_no_quiet_hours_when_disabled(self, config):
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        t = time.time() - config.alert.no_drink_warning_s - 1
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)
        assert state.level == AlertLevel.REMINDER


class TestGoalProgress:
    def test_goal_progress_zero_at_start(self, engine):
        summary = make_summary(total_consumed_ml=0.0)
        state = engine.evaluate(summary)
        assert state.goal_progress == pytest.approx(0.0)

    def test_goal_progress_half(self, engine):
        summary = make_summary(total_consumed_ml=DAILY_GOAL_ML / 2)
        state = engine.evaluate(summary)
        assert state.goal_progress == pytest.approx(0.5)


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
