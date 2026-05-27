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


class TestPaceAwareThresholds:
    """Alert threshold adapts based on the pace model deficit."""

    def test_idle_when_ahead_and_within_extended_window(self, config):
        """Deficit=0 (ahead) → 30-s window; 8 s since drink → IDLE."""
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        # 8 s is past the base warning (10 s) but within ahead window (30 s)
        t = time.time() - 8
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary, deficit_ml=0.0)
        assert state.level == AlertLevel.IDLE

    def test_reminder_when_ahead_but_past_extended_window(self, config):
        """Deficit=0 → 30-s window; 31 s since drink → REMINDER."""
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        t = time.time() - (config.alert.no_drink_warning_ahead_s + 1)
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary, deficit_ml=0.0)
        assert state.level == AlertLevel.REMINDER

    def test_reminder_when_behind_and_past_short_window(self, config):
        """Deficit ≥ threshold → 5-s window; 7 s since drink → REMINDER."""
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        # 7 s is between short (5 s) and base (10 s) windows
        t = time.time() - 7
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary, deficit_ml=config.alert.behind_threshold_ml)
        assert state.level == AlertLevel.REMINDER

    def test_idle_when_behind_but_within_short_window(self, config):
        """Deficit ≥ threshold → 5-s window; 3 s since drink → still IDLE."""
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        t = time.time() - 3
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary, deficit_ml=config.alert.behind_threshold_ml)
        assert state.level == AlertLevel.IDLE

    def test_base_window_when_no_deficit_provided(self, config):
        """No deficit_ml → base 10-s window; 11 s since drink → REMINDER."""
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        t = time.time() - (config.alert.no_drink_warning_s + 1)
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary)  # no deficit_ml arg
        assert state.level == AlertLevel.REMINDER

    def test_slight_deficit_uses_base_window(self, config):
        """Deficit below threshold → base window, not shortened window."""
        engine = AlertEngine(config, daily_goal_ml=DAILY_GOAL_ML)
        slight_deficit = config.alert.behind_threshold_ml - 1  # just under threshold
        # 7 s is past short window (5 s) but within base window (10 s)
        t = time.time() - 7
        summary = make_summary(last_drink_time=t)
        state = engine.evaluate(summary, deficit_ml=slight_deficit)
        assert state.level == AlertLevel.IDLE  # base window not yet exceeded


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
