"""
Hydration Record for the DRIP Hydration Monitoring System.

Persists drink events, sleep events, and session summaries to disk.
Uses newline-delimited JSON (one record per line) for simplicity and
robustness — records can be appended without rewriting the file, and a
corrupted line does not invalidate the rest.

Each bed gets its own file named by bed ID. The persistence layer is
intentionally dumb: it writes records and reads them back. All
interpretation (daily totals, trend analysis) is left to upstream
consumers.

If you later want a proper database backend, swap out
:class:`JsonLinesHydrationRecord` while keeping the :class:`HydrationRecord`
abstract interface.
"""

import abc
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.input_buttons import SleepToggleEvent
from app.interactions.session import DrinkEvent, SessionSummary


@dataclass
class PersistedRecord:
    """
    A single record as stored on disk.

    Attributes:
        record_type: One of ``"drink"``, ``"sleep"``, or
            ``"session_summary"``.
        bed_id: Identifier of the bed this record belongs to.
        timestamp: Unix timestamp of the event.
        payload: Event-specific data as a plain dict.
    """

    record_type: str
    bed_id: str
    timestamp: float
    payload: dict


class HydrationRecord(abc.ABC):
    """Abstract base class for the persistence layer."""

    @abc.abstractmethod
    def write_drink(self, bed_id: str, event: DrinkEvent) -> None:
        """Persist a drink event."""

    @abc.abstractmethod
    def write_sleep_event(self, bed_id: str, event: SleepToggleEvent) -> None:
        """Persist a sleep/wake toggle event."""

    @abc.abstractmethod
    def write_session_summary(
        self, bed_id: str, summary: SessionSummary
    ) -> None:
        """Persist a session summary snapshot."""

    @abc.abstractmethod
    def read_drinks(
        self,
        bed_id: str,
        since: float | None = None,
    ) -> list[DrinkEvent]:
        """
        Return drink events for a bed, optionally filtered by time.

        Args:
            bed_id: Bed to query.
            since: If provided, only return events with a timestamp at
                or after this Unix timestamp.
        """

    @abc.abstractmethod
    def read_sleep_events(
        self,
        bed_id: str,
        since: float | None = None,
    ) -> list[SleepToggleEvent]:
        """Return sleep toggle events for a bed."""


class JsonLinesHydrationRecord(HydrationRecord):
    """
    File-based persistence using newline-delimited JSON.

    Each bed's records are stored in a separate ``.jsonl`` file inside
    ``storage_dir``. Records are appended on write so the file grows
    over time. For long-running deployments add a daily rotation strategy.

    Args:
        storage_dir: Directory for record files. Created if absent.
    """

    def __init__(self, storage_dir: str | Path = "records"):
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def write_drink(self, bed_id: str, event: DrinkEvent) -> None:
        self._append(
            bed_id,
            PersistedRecord(
                record_type="drink",
                bed_id=bed_id,
                timestamp=event.timestamp,
                payload={"volume_ml": event.volume_ml},
            ),
        )

    def write_sleep_event(self, bed_id: str, event: SleepToggleEvent) -> None:
        self._append(
            bed_id,
            PersistedRecord(
                record_type="sleep",
                bed_id=bed_id,
                timestamp=event.timestamp,
                payload={"sleeping": event.sleeping},
            ),
        )

    def write_session_summary(
        self, bed_id: str, summary: SessionSummary
    ) -> None:
        self._append(
            bed_id,
            PersistedRecord(
                record_type="session_summary",
                bed_id=bed_id,
                timestamp=time.time(),
                payload={
                    "session_state": summary.session_state.value,
                    "total_consumed_ml": summary.total_consumed_ml,
                    "drink_count": summary.drink_count,
                    "duration_s": summary.duration_s,
                },
            ),
        )

    def read_drinks(
        self,
        bed_id: str,
        since: float | None = None,
    ) -> list[DrinkEvent]:
        records = self._read_all(bed_id, record_type="drink")
        events = []
        for r in records:
            if since is not None and r.timestamp < since:
                continue
            events.append(
                DrinkEvent(
                    timestamp=r.timestamp,
                    volume_ml=r.payload["volume_ml"],
                )
            )
        return events

    def read_sleep_events(
        self,
        bed_id: str,
        since: float | None = None,
    ) -> list[SleepToggleEvent]:
        records = self._read_all(bed_id, record_type="sleep")
        events = []
        for r in records:
            if since is not None and r.timestamp < since:
                continue
            events.append(
                SleepToggleEvent(
                    timestamp=r.timestamp,
                    sleeping=r.payload.get("sleeping", True),
                )
            )
        return events

    def _file_path(self, bed_id: str) -> Path:
        safe_id = "".join(c for c in bed_id if c.isalnum() or c in "-_")
        return self._dir / f"{safe_id}.jsonl"

    def _append(self, bed_id: str, record: PersistedRecord) -> None:
        path = self._file_path(bed_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record)) + "\n")

    def _read_all(
        self, bed_id: str, record_type: str | None = None
    ) -> list[PersistedRecord]:
        path = self._file_path(bed_id)
        if not path.exists():
            return []

        records = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if record_type and data.get("record_type") != record_type:
                        continue
                    records.append(
                        PersistedRecord(
                            record_type=data["record_type"],
                            bed_id=data["bed_id"],
                            timestamp=data["timestamp"],
                            payload=data["payload"],
                        )
                    )
                except (json.JSONDecodeError, KeyError):
                    # Skip corrupted lines rather than crashing.
                    continue

        return records
