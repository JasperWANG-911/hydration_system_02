"""Unit tests for JsonLinesHydrationRecord."""

import time
import pytest
from app.hydration_record import JsonLinesHydrationRecord
from app.input_buttons import SleepToggleEvent
from app.interactions.session import DrinkEvent, SessionState, SessionSummary


@pytest.fixture
def record(tmp_path):
    return JsonLinesHydrationRecord(storage_dir=tmp_path)


def make_drink(volume_ml=100.0, ts=None):
    return DrinkEvent(
        timestamp=ts or time.time(),
        volume_ml=volume_ml,
    )


def make_summary():
    return SessionSummary(
        session_state=SessionState.ENDED,
        total_consumed_ml=300.0,
        drink_count=3,
        start_time=time.time() - 3600,
        duration_s=3600.0,
        last_drink_time=time.time() - 10,
    )


class TestDrinkPersistence:
    def test_write_and_read_drink(self, record):
        event = make_drink(volume_ml=75.0)
        record.write_drink("ward-4-bed-1", event)
        drinks = record.read_drinks("ward-4-bed-1")
        assert len(drinks) == 1
        assert drinks[0].volume_ml == pytest.approx(75.0)

    def test_multiple_drinks_returned(self, record):
        t = time.time()
        record.write_drink("ward-4-bed-1", make_drink(100.0, ts=t))
        record.write_drink("ward-4-bed-1", make_drink(50.0, ts=t + 1))
        drinks = record.read_drinks("ward-4-bed-1")
        assert len(drinks) == 2

    def test_since_filter_excludes_old_events(self, record):
        old_ts = time.time() - 3600
        record.write_drink("ward-4-bed-1", make_drink(100.0, ts=old_ts))
        record.write_drink("ward-4-bed-1", make_drink(50.0, ts=time.time()))
        drinks = record.read_drinks("ward-4-bed-1", since=time.time() - 60)
        assert len(drinks) == 1
        assert drinks[0].volume_ml == pytest.approx(50.0)

    def test_no_records_returns_empty_list(self, record):
        assert record.read_drinks("ward-1-bed-unknown") == []


class TestSleepPersistence:
    def test_write_and_read_sleep_event(self, record):
        event = SleepToggleEvent(timestamp=time.time(), sleeping=True)
        record.write_sleep_event("ward-4-bed-1", event)
        results = record.read_sleep_events("ward-4-bed-1")
        assert len(results) == 1
        assert results[0].sleeping is True

    def test_sleep_wake_pair(self, record):
        t = time.time()
        record.write_sleep_event("ward-4-bed-1", SleepToggleEvent(t,     sleeping=True))
        record.write_sleep_event("ward-4-bed-1", SleepToggleEvent(t + 5, sleeping=False))
        results = record.read_sleep_events("ward-4-bed-1")
        assert len(results) == 2
        assert results[0].sleeping is True
        assert results[1].sleeping is False


class TestPatientIsolation:
    def test_different_beds_do_not_share_records(self, record):
        record.write_drink("ward-1-bed-a", make_drink(100.0))
        record.write_drink("ward-1-bed-b", make_drink(200.0))
        a_drinks = record.read_drinks("ward-1-bed-a")
        b_drinks = record.read_drinks("ward-1-bed-b")
        assert len(a_drinks) == 1
        assert len(b_drinks) == 1
        assert a_drinks[0].volume_ml == pytest.approx(100.0)
        assert b_drinks[0].volume_ml == pytest.approx(200.0)


class TestSessionSummaryPersistence:
    def test_write_session_summary_does_not_raise(self, record):
        record.write_session_summary("ward-4-bed-1", make_summary())
