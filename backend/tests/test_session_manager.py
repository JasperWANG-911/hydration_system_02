"""Unit tests for SessionManager."""

import time
import pytest
from app.interactions.classifier import InteractionResult, PlatformState
from app.interactions.session import SessionManager, SessionState


def make_loss_result(net_change_g: float, confidence: float = 0.88) -> InteractionResult:
    return InteractionResult(
        state=PlatformState.NET_WEIGHT_LOSS,
        confidence=confidence,
        metadata={"net_change": net_change_g},
    )


def make_gain_result(net_change_g: float, confidence: float = 0.88) -> InteractionResult:
    return InteractionResult(
        state=PlatformState.NET_WEIGHT_GAIN,
        confidence=confidence,
        metadata={"net_change": net_change_g},
    )


def make_noise_result() -> InteractionResult:
    return InteractionResult(
        state=PlatformState.SENSOR_NOISE,
        confidence=0.5,
    )


@pytest.fixture
def manager():
    m = SessionManager(
        daily_goal_ml=500.0,
        min_credible_volume_ml=1.0,
        max_credible_volume_ml=400.0,
    )
    m.start()
    return m


class TestLifecycle:
    def test_starts_idle(self):
        m = SessionManager()
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

    def test_cannot_end_before_start(self):
        m = SessionManager()
        with pytest.raises(RuntimeError):
            m.end()


class TestDrinkAccumulation:
    def test_single_drink_recorded(self, manager):
        manager.process(make_loss_result(-80.0))
        assert manager.summary().drink_count == 1
        assert manager.summary().total_consumed_ml == pytest.approx(80.0)

    def test_multiple_drinks_accumulate(self, manager):
        manager.process(make_loss_result(-80.0))
        manager.process(make_loss_result(-50.0))
        assert manager.summary().drink_count == 2
        assert manager.summary().total_consumed_ml == pytest.approx(130.0)

    def test_below_min_credible_ignored(self, manager):
        # 0.5 g is below min_credible_volume_ml of 1.0
        manager.process(make_loss_result(-0.5))
        assert manager.summary().drink_count == 0

    def test_above_max_credible_clamped(self, manager):
        # 600 ml exceeds max_credible_volume_ml of 400
        manager.process(make_loss_result(-600.0))
        assert manager.summary().total_consumed_ml == pytest.approx(400.0)

    def test_noise_events_ignored(self, manager):
        manager.process(make_noise_result())
        assert manager.summary().drink_count == 0
        assert manager.summary().total_consumed_ml == 0.0

    def test_events_ignored_when_paused(self, manager):
        manager.pause()
        manager.process(make_loss_result(-80.0))
        assert manager.summary().drink_count == 0


class TestRefills:
    def test_refill_recorded(self, manager):
        manager.process(make_gain_result(200.0))
        assert manager.summary().refill_count == 1

    def test_refill_does_not_affect_consumed(self, manager):
        manager.process(make_gain_result(200.0))
        assert manager.summary().total_consumed_ml == 0.0


class TestGoalProgress:
    def test_no_drinks_zero_progress(self, manager):
        assert manager.goal_progress() == pytest.approx(0.0)

    def test_half_goal_reached(self, manager):
        manager.process(make_loss_result(-250.0))
        assert manager.goal_progress() == pytest.approx(0.5)

    def test_goal_exceeded(self, manager):
        manager.process(make_loss_result(-600.0))  # clamped to 400
        manager.process(make_loss_result(-200.0))
        assert manager.goal_progress() > 1.0


class TestCallbacks:
    def test_drink_callback_called(self, manager):
        received = []
        manager.on_drink(received.append)
        manager.process(make_loss_result(-80.0))
        assert len(received) == 1
        assert received[0].volume_ml == pytest.approx(80.0)

    def test_refill_callback_called(self, manager):
        received = []
        manager.on_refill(received.append)
        manager.process(make_gain_result(200.0))
        assert len(received) == 1

    def test_fault_callback_called(self, manager):
        received = []
        manager.on_fault(received.append)
        fault = InteractionResult(
            state=PlatformState.SENSOR_FAULT,
            confidence=0.99,
            metadata={"weight": -200.0},
        )
        manager.process(fault)
        assert len(received) == 1

    def test_multiple_callbacks_all_called(self, manager):
        results_a, results_b = [], []
        manager.on_drink(results_a.append)
        manager.on_drink(results_b.append)
        manager.process(make_loss_result(-80.0))
        assert len(results_a) == 1
        assert len(results_b) == 1


class TestLastDrinkTime:
    def test_no_drinks_returns_none(self, manager):
        assert manager.summary().last_drink_time is None

    def test_last_drink_time_set_after_drink(self, manager):
        before = time.time()
        manager.process(make_loss_result(-80.0))
        after = time.time()
        t = manager.summary().last_drink_time
        assert t is not None
        assert before <= t <= after


class TestExplicitTimestamp:
    def test_drink_uses_supplied_now_ts(self, manager):
        replay_ts = 1_700_000_000.0
        manager.process(make_loss_result(-80.0), now_ts=replay_ts)
        assert manager.summary().last_drink_time == replay_ts
