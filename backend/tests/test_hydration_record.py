"""Unit tests for JsonLinesHydrationRecord."""

import time
import pytest
from app.hydration_record import JsonLinesHydrationRecord
from app.observation_button import ButtonObservation
from app.interactions.session import DrinkEvent, RefillEvent, SessionState, SessionSummary


@pytest.fixture
def record(tmp_path):
    return JsonLinesHydrationRecord(storage_dir=tmp_path)


def make_drink(volume_ml=100.0, ts=None):
    return DrinkEvent(
        timestamp=ts or time.time(),
        volume_ml=volume_ml,
        confidence=0.88,
        raw_net_change_g=-volume_ml,
    )


def make_refill(volume_ml=200.0):
    return RefillEvent(
        timestamp=time.time(),
        volume_added_ml=volume_ml,
        confidence=0.88,
        raw_net_change_g=volume_ml,
    )


def make_summary():
    return SessionSummary(
        session_state=SessionState.ENDED,
        total_consumed_ml=300.0,
        drink_count=3,
        refill_count=1,
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

    def test_multiple_drinks_returned_in_order(self, record):
        t = time.time()
        record.write_drink("ward-4-bed-1", make_drink(100.0, ts=t))
        record.write_drink("ward-4-bed-1", make_drink(50.0, ts=t + 1))
        drinks = record.read_drinks("ward-4-bed-1")
        assert len(drinks) == 2
        assert drinks[0].volume_ml == pytest.approx(100.0)
        assert drinks[1].volume_ml == pytest.approx(50.0)

    def test_since_filter_excludes_old_events(self, record):
        old_ts = time.time() - 3600
        record.write_drink("ward-4-bed-1", make_drink(100.0, ts=old_ts))
        record.write_drink("ward-4-bed-1", make_drink(50.0, ts=time.time()))
        drinks = record.read_drinks("ward-4-bed-1", since=time.time() - 60)
        assert len(drinks) == 1
        assert drinks[0].volume_ml == pytest.approx(50.0)

    def test_no_records_returns_empty_list(self, record):
        assert record.read_drinks("ward-1-bed-unknown") == []


class TestObservationPersistence:
    def test_write_and_read_observation(self, record):
        obs = ButtonObservation(note="Patient refused water")
        record.write_observation("ward-4-bed-1", obs)
        results = record.read_observations("ward-4-bed-1")
        assert len(results) == 1
        assert results[0].note == "Patient refused water"

    def test_unacknowledged_filter(self, record):
        acked = ButtonObservation(note="seen", acknowledged=True)
        unacked = ButtonObservation(note="unseen", acknowledged=False)
        record.write_observation("ward-4-bed-1", acked)
        record.write_observation("ward-4-bed-1", unacked)
        results = record.read_observations("ward-4-bed-1", unacknowledged_only=True)
        assert len(results) == 1
        assert results[0].note == "unseen"


class TestPatientIsolation:
    def test_different_patients_do_not_share_records(self, record):
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