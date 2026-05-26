"""Unit tests for SessionManager."""

import time
import pytest
from app.config import SystemConfig
from app.interactions.session import SessionManager, SessionState


@pytest.fixture
def manager(config):
    config.session.default_daily_goal_ml = 500.0
    m = SessionManager(config)
    m.start()
    return m


class TestLifecycle:
    def test_starts_idle(self, config):
        m = SessionManager(config)
        assert m.summary().session_state == SessionState.IDLE

    def test_start_sets_active(self, manager):
        assert manager.summary().session_state == SessionState.ACTIVE

    def test_cannot_start_twice(self, manager):
        with pytest.raises(RuntimeError):
            manager.start()

    def test_pause_and_resume(self, manager):
        manager.pause()
        assert manager.summary().session_state == SessionState.PAUSED
        manager.resume()
        assert manager.summary().session_state == SessionState.ACTIVE

    def test_cannot_pause_when_not_active(self, manager):
        manager.pause()
        with pytest.raises(RuntimeError):
            manager.pause()

    def test_end_sets_ended(self, manager):
        manager.end()
        assert manager.summary().session_state == SessionState.ENDED

    def test_cannot_start_after_end(self, manager):
        manager.end()
        with pytest.raises(RuntimeError):
            manager.start()

    def test_cannot_end_before_start(self, config):
        m = SessionManager(config)
        with pytest.raises(RuntimeError):
            m.end()


class TestIntakeAccumulation:
    def test_single_intake_recorded(self, manager):
        manager.record_intake(150.0)
        assert manager.summary().drink_count == 1
        assert manager.summary().total_consumed_ml == pytest.approx(150.0)

    def test_multiple_intakes_accumulate(self, manager):
        manager.record_intake(150.0)
        manager.record_intake(100.0)
        assert manager.summary().drink_count == 2
        assert manager.summary().total_consumed_ml == pytest.approx(250.0)

    def test_zero_volume_ignored(self, manager):
        manager.record_intake(0.0)
        assert manager.summary().drink_count == 0

    def test_negative_volume_ignored(self, manager):
        manager.record_intake(-50.0)
        assert manager.summary().drink_count == 0

    def test_intake_ignored_when_paused(self, manager):
        manager.pause()
        manager.record_intake(150.0)
        assert manager.summary().drink_count == 0

    def test_intake_ignored_before_start(self, config):
        m = SessionManager(config)
        m.record_intake(150.0)
        assert m.summary().drink_count == 0


class TestGoalProgress:
    def test_no_intake_zero_progress(self, manager):
        assert manager.goal_progress() == pytest.approx(0.0)

    def test_half_goal_reached(self, manager):
        manager.record_intake(250.0)
        assert manager.goal_progress() == pytest.approx(0.5)

    def test_goal_exceeded(self, manager):
        manager.record_intake(600.0)
        assert manager.goal_progress() > 1.0


class TestCallbacks:
    def test_drink_callback_called(self, manager):
        received = []
        manager.on_drink(received.append)
        manager.record_intake(150.0)
        assert len(received) == 1
        assert received[0].volume_ml == pytest.approx(150.0)

    def test_multiple_callbacks_all_called(self, manager):
        results_a, results_b = [], []
        manager.on_drink(results_a.append)
        manager.on_drink(results_b.append)
        manager.record_intake(150.0)
        assert len(results_a) == 1
        assert len(results_b) == 1


class TestLastDrinkTime:
    def test_no_intake_returns_none(self, manager):
        assert manager.summary().last_drink_time is None

    def test_last_drink_time_set_after_intake(self, manager):
        before = time.time()
        manager.record_intake(150.0)
        after = time.time()
        t = manager.summary().last_drink_time
        assert t is not None
        assert before <= t <= after


class TestExplicitTimestamp:
    def test_intake_uses_supplied_now_ts(self, manager):
        replay_ts = 1_700_000_000.0
        manager.record_intake(150.0, now_ts=replay_ts)
        assert manager.summary().last_drink_time == replay_ts
