"""Unit tests for PlatformInteractionClassifier."""

import pytest
from config import SystemConfig
from platform_interaction_classifier import PlatformInteractionClassifier, PlatformState

# Stability window is 20 samples. A full drink cycle needs:
#   - enough pre-removal samples to exit warmup AND register as stable
#   - enough absent samples to sit in WAITING_FOR_RETURN
#   - enough post-return samples to fill the buffer and settle
# 40 + 10 + 40 gives comfortable headroom at each stage.
_STABLE = 40
_ABSENT = 10


def _feed(classifier, readings):
    """Feed a list of readings and return all results."""
    return [classifier.update(w) for w in readings]


def _states(results):
    """Return the set of states seen across a list of results."""
    return {r.state for r in results}


def _last_meaningful(results):
    """Return the last result that is not sensor noise or warmup."""
    noise_states = {PlatformState.SENSOR_NOISE, PlatformState.UNSTABLE_MOVEMENT}
    meaningful = [r for r in results if r.state not in noise_states]
    return meaningful[-1] if meaningful else None


def _find(results, state):
    """Return the first result matching state, or None."""
    return next((r for r in results if r.state == state), None)


def _drink_sequence(pre=450.0, post=380.0):
    """Build a stable-cup → lift → return weight sequence."""
    return [pre] * _STABLE + [0.0] * _ABSENT + [post] * _STABLE


@pytest.fixture
def clf(config):
    return PlatformInteractionClassifier(config)


class TestWarmup:
    def test_returns_sensor_noise_during_warmup(self, clf):
        results = _feed(clf, [400.0] * 10)
        assert all(r.state == PlatformState.SENSOR_NOISE for r in results)

    def test_transitions_after_full_window(self, clf):
        results = _feed(clf, [400.0] * _STABLE)
        assert PlatformState.CUP_PRESENT_STABLE in _states(results)


class TestNoCup:
    def test_empty_platform_stays_no_cup(self, clf):
        results = _feed(clf, [0.0] * _STABLE)
        last = _last_meaningful(results)
        assert last.state == PlatformState.NO_CUP

    def test_below_threshold_stays_no_cup(self, clf):
        # 14 g is below the default 15 g empty_threshold
        results = _feed(clf, [14.0] * _STABLE)
        last = _last_meaningful(results)
        assert last.state == PlatformState.NO_CUP


class TestCupPresent:
    def test_stable_cup_detected(self, clf):
        results = _feed(clf, [450.0] * _STABLE)
        last = _last_meaningful(results)
        assert last.state == PlatformState.CUP_PRESENT_STABLE

    def test_stable_weight_in_metadata(self, clf):
        results = _feed(clf, [450.0] * _STABLE)
        cup_results = [r for r in results if r.state == PlatformState.CUP_PRESENT_STABLE]
        assert cup_results[-1].metadata["stable_weight"] == pytest.approx(450.0, abs=1.0)


class TestDrinkCycle:
    def test_cup_removed_detected(self, clf):
        results = _feed(clf, _drink_sequence())
        assert PlatformState.CUP_REMOVED in _states(results)

    def test_net_weight_loss_detected(self, clf):
        results = _feed(clf, _drink_sequence(pre=450.0, post=380.0))
        assert PlatformState.NET_WEIGHT_LOSS in _states(results)

    def test_net_change_approximately_correct(self, clf):
        results = _feed(clf, _drink_sequence(pre=450.0, post=380.0))
        loss = _find(results, PlatformState.NET_WEIGHT_LOSS)
        assert loss is not None, "NET_WEIGHT_LOSS not found in results"
        assert loss.metadata["net_change"] == pytest.approx(-70.0, abs=2.0)

    def test_net_weight_gain_on_refill(self, clf):
        results = _feed(clf, _drink_sequence(pre=300.0, post=500.0))
        assert PlatformState.NET_WEIGHT_GAIN in _states(results)

    def test_no_meaningful_change_on_small_delta(self, clf):
        # 5 g delta is below the 10 g meaningful_change_threshold
        results = _feed(clf, _drink_sequence(pre=450.0, post=445.0))
        assert PlatformState.NO_MEANINGFUL_CHANGE in _states(results)

    def test_waiting_for_return_seen_while_cup_absent(self, clf):
        results = _feed(clf, _drink_sequence())
        assert PlatformState.WAITING_FOR_RETURN in _states(results)

    def test_returns_to_cup_present_after_cycle(self, clf):
        results = _feed(clf, _drink_sequence())
        assert _last_meaningful(results).state == PlatformState.CUP_PRESENT_STABLE


class TestSensorFault:
    def test_negative_spike_triggers_fault(self, clf):
        result = clf.update(-100.0)
        assert result.state == PlatformState.SENSOR_FAULT

    def test_over_max_triggers_fault(self, clf):
        result = clf.update(99999.0)
        assert result.state == PlatformState.SENSOR_FAULT

    def test_fault_confidence_is_high(self, clf):
        result = clf.update(-200.0)
        assert result.confidence >= 0.99


class TestConfidence:
    def test_cup_present_confidence_reasonable(self, clf):
        results = _feed(clf, [450.0] * _STABLE)
        cup = _find(results, PlatformState.CUP_PRESENT_STABLE)
        assert cup is not None
        assert 0.8 <= cup.confidence <= 1.0


class TestConfigRespected:
    def test_custom_empty_threshold(self):
        config = SystemConfig()
        config.sensor.empty_threshold_g = 50.0
        clf = PlatformInteractionClassifier(config)
        # 40 g is below the custom threshold — should never detect a cup
        results = _feed(clf, [40.0] * _STABLE)
        assert _last_meaningful(results).state == PlatformState.NO_CUP

    def test_custom_meaningful_change_threshold(self):
        config = SystemConfig()
        config.sensor.meaningful_change_threshold_g = 100.0
        clf = PlatformInteractionClassifier(config)
        # 70 g change is below the new threshold — should be no meaningful change
        results = _feed(clf, _drink_sequence(pre=450.0, post=380.0))
        assert PlatformState.NO_MEANINGFUL_CHANGE in _states(results)