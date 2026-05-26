"""Shared pytest fixtures for the DRIP test suite."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from app.config import SystemConfig
from app.patient_profile import BedProfile


@pytest.fixture
def config():
    """Return a SystemConfig with fast alert timings for tests."""
    cfg = SystemConfig()
    cfg.alert.no_drink_warning_s = 10.0
    cfg.alert.no_drink_urgent_s = 20.0
    cfg.alert.quiet_hours_start = None
    cfg.alert.quiet_hours_end = None
    cfg.button.aggregation_window_s = 0.5  # short window so tests don't wait 15s
    return cfg


@pytest.fixture
def bed():
    """Return a minimal BedProfile for testing."""
    return BedProfile(
        bed_id="test-ward-bed-01",
        daily_goal_ml=1000.0,
    )
