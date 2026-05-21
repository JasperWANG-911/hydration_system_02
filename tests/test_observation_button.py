"""Unit tests for ObservationButton (via MockObservationButton)."""

import time
import pytest
from observation_button import MockObservationButton


@pytest.fixture
def button(config):
    return MockObservationButton(config)


class TestMockObservationButton:
    def test_drain_empty_initially(self, button):
        assert button.drain() == []

    def test_press_creates_observation(self, button):
        button.press()
        obs = button.drain()
        assert len(obs) == 1

    def test_drain_clears_queue(self, button):
        button.press()
        button.drain()
        assert button.drain() == []

    def test_note_attached_to_observation(self, button):
        button.press(note="Patient declined water")
        obs = button.drain()
        assert obs[0].note == "Patient declined water"

    def test_timestamp_is_recent(self, button):
        before = time.time()
        button.press()
        after = time.time()
        obs = button.drain()
        assert before <= obs[0].timestamp <= after

    def test_observation_not_acknowledged_by_default(self, button):
        button.press()
        obs = button.drain()
        assert obs[0].acknowledged is False

    def test_debounce_prevents_double_press(self, config, button):
        # debounce_s is 0.3 in default config — two instant presses
        # should only produce one observation
        button.press()
        button.press()
        obs = button.drain()
        assert len(obs) == 1

    def test_multiple_presses_after_debounce(self, config):
        config.button.debounce_s = 0.0
        button = MockObservationButton(config)
        button.press()
        button.press()
        obs = button.drain()
        assert len(obs) == 2