"""Unit tests for LedController (via MockLedController)."""

import pytest
from app.alert_engine import AlertLevel
from app.led_controller import MockLedController


@pytest.fixture
def led(config):
    return MockLedController(config)


class TestMockLedController:
    def test_no_history_initially(self, led):
        assert led.last_state() is None

    def test_apply_records_state(self, led):
        led.apply(AlertLevel.REMINDER)
        assert led.last_state().level == AlertLevel.REMINDER

    def test_off_records_idle(self, led):
        led.apply(AlertLevel.REMINDER)
        led.off()
        assert led.last_state().level == AlertLevel.IDLE
        assert led.last_state().brightness == 0.0

    def test_history_grows_with_each_apply(self, led):
        led.apply(AlertLevel.IDLE)
        led.apply(AlertLevel.REMINDER)
        led.apply(AlertLevel.URGENT)
        assert len(led.history()) == 3

    def test_history_returns_copy(self, led):
        led.apply(AlertLevel.REMINDER)
        h = led.history()
        h.clear()
        assert len(led.history()) == 1

    def test_reminder_brightness_lower_than_urgent(self, led, config):
        led.apply(AlertLevel.REMINDER)
        reminder_brightness = led.last_state().brightness

        led.apply(AlertLevel.URGENT)
        urgent_brightness = led.last_state().brightness

        assert reminder_brightness < urgent_brightness

    def test_idle_brightness_is_zero(self, led):
        led.apply(AlertLevel.IDLE)
        assert led.last_state().brightness == 0.0

    def test_colors_are_rgb_tuples(self, led):
        for level in AlertLevel:
            led.apply(level)
            color = led.last_state().color
            assert isinstance(color, tuple)
            assert len(color) == 3
            assert all(0 <= c <= 255 for c in color)