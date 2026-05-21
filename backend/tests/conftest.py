"""
Shared pytest fixtures for the Hydration Monitoring System test suite.

Import these in any test file via standard pytest fixture injection —
no explicit import of conftest is needed.
"""

import sys
import os
import pytest

# Make the hydration_system package importable from tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import SystemConfig
from app.patient_profile import BedProfile


@pytest.fixture
def config():
    """Return a default SystemConfig with fast timings for tests."""
    cfg = SystemConfig()
    # Speed up alert thresholds so tests don't have to wait 30 minutes
    cfg.alert.no_drink_warning_s = 10.0
    cfg.alert.no_drink_urgent_s = 20.0
    cfg.alert.quiet_hours_start = None
    cfg.alert.quiet_hours_end = None
    return cfg


@pytest.fixture
def bed():
    """Return a minimal BedProfile for testing."""
    return BedProfile(
        bed_id="test-ward-bed-01",
        daily_goal_ml=500.0,
    )


@pytest.fixture
def stable_cup_readings():
    """Return a sequence simulating a stable cup on the platform."""
    return [450.0] * 25


@pytest.fixture
def drink_sequence():
    """
    Return a weight sequence simulating a cup lift, drink, and return.

    Cup starts at 450 g, is lifted (0 g), and returns at 380 g —
    a 70 ml drink.
    """
    return (
        [450.0] * 25   # stable cup present
        + [0.0] * 5    # cup lifted
        + [380.0] * 25 # cup returned, settled
    )