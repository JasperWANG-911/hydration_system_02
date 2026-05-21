"""
Shared pytest fixtures for the Hydration Monitoring System test suite.

Place this file in the same directory as your test files.
"""

import os
import sys

import pytest

# Ensure the directory containing this file is on the path so that
# all modules (config, platform_interaction_classifier, etc.) can be
# imported regardless of where pytest is invoked from.
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
    return [450.0] * 40


@pytest.fixture
def drink_sequence():
    """
    Return a weight sequence simulating a cup lift, drink, and return.

    Cup starts at 450 g, is lifted (0 g), and returns at 380 g —
    a 70 ml drink.
    """
    return [450.0] * 40 + [0.0] * 10 + [380.0] * 40