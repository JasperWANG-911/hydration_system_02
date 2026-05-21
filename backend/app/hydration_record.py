"""
Hydration Record for the Hydration Monitoring System.

Persists drink events, refill events, button observations, and session
summaries to disk. Uses newline-delimited JSON (one record per line) for
simplicity and robustness — records can be appended without rewriting
the file, and a corrupted line does not invalidate the rest.

Each bed gets its own file named by bed ID. The persistence
layer is intentionally dumb: it writes records and reads them back. All
interpretation (daily totals, trend analysis) is left to upstream
consumers.

This module has no hardware dependencies and can be used as-is.
If you later want a proper database backend, swap out the
:class:`JsonLinesHydrationRecord` implementation while keeping the
:class:`HydrationRecord` abstract interface.
"""

import abc
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.observation_button import ButtonObservation
from app.interactions.session import DrinkEvent, RefillEvent, SessionSummary


@dataclass
class PersistedRecord:
    """
    A single record as stored on disk.

    Attributes:
        record_type: One of ``"drink"``, ``"refill"``, ``"observation"``,
            or ``"session_summary"``.
        bed_id: Identifier of the patient this record belongs to.
        timestamp: Unix timestamp of the event.
        payload: Event-specific data as a plain dict.
    """

    record_type: str
    bed_id: str
    timestamp: float
    payload: dict


class HydrationRecord(abc.ABC):
    """
    Abstract base class for the persistence layer.

    Subclass this to replace the JSON file backend with a database,
    cloud API, or any other storage mechanism.
    """

    @abc.abstractmethod
    def write_drink(self, bed_id: str, event: DrinkEvent) -> None:
        """Persist a drink event for the given patient."""

    @abc.abstractmethod
    def write_refill(self, bed_id: str, event: RefillEvent) -> None:
        """Persist a refill event for the given patient."""

    @abc.abstractmethod
    def write_observation(
        self, bed_id: str, observation: ButtonObservation
    ) -> None:
        """Persist a button observation for the given patient."""

    @abc.abstractmethod
    def write_session_summary(
        self, bed_id: str, summary: SessionSummary
    ) -> None:
        """Persist a session summary snapshot for the given patient."""

    @abc.abstractmethod
    def read_drinks(
        self,
        bed_id: str,
        since: float | None = None,
    ) -> list[DrinkEvent]:
        """
        Return drink events for a patient, optionally filtered by time.

        Args:
            bed_id: Patient to query.
            since: If provided, only return events with a timestamp at
                or after this Unix timestamp.

        Returns:
            List of :class:`DrinkEvent` instances in chronological order.
        """

    @abc.abstractmethod
    def read_observations(
        self,
        bed_id: str,
        unacknowledged_only: bool = False,
    ) -> list[ButtonObservation]:
        """
        Return button observations for a patient.

        Args:
            bed_id: Patient to query.
            unacknowledged_only: If True, return only observations that
                have not yet been acknowledged by staff.

        Returns:
            List of :class:`ButtonObservation` instances.
        """


class JsonLinesHydrationRecord(HydrationRecord):
    """
    File-based persistence using newline-delimited JSON.

    Each bed's records are stored in a separate ``.jsonl`` file
    inside ``storage_dir``. Records are appended on write, so the file
    grows over time. For long-running deployments, add a rotation
    strategy (e.g. one file per day per patient).

    Args:
        storage_dir: Directory in which patient record files are stored.
            Created automatically if it does not exist.
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
                payload={
                    "volume_ml": event.volume_ml,
                    "confidence": event.confidence,
                    "raw_net_change_g": event.raw_net_change_g,
                },
            ),
        )

    def write_refill(self, bed_id: str, event: RefillEvent) -> None:
        self._append(
            bed_id,
            PersistedRecord(
                record_type="refill",
                bed_id=bed_id,
                timestamp=event.timestamp,
                payload={
                    "volume_added_ml": event.volume_added_ml,
                    "confidence": event.confidence,
                    "raw_net_change_g": event.raw_net_change_g,
                },
            ),
        )

    def write_observation(
        self, bed_id: str, observation: ButtonObservation
    ) -> None:
        self._append(
            bed_id,
            PersistedRecord(
                record_type="observation",
                bed_id=bed_id,
                timestamp=observation.timestamp,
                payload={
                    "note": observation.note,
                    "acknowledged": observation.acknowledged,
                },
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
                    "refill_count": summary.refill_count,
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
                    confidence=r.payload["confidence"],
                    raw_net_change_g=r.payload["raw_net_change_g"],
                )
            )
        return events

    def read_observations(
        self,
        bed_id: str,
        unacknowledged_only: bool = False,
    ) -> list[ButtonObservation]:
        records = self._read_all(bed_id, record_type="observation")
        observations = []
        for r in records:
            obs = ButtonObservation(
                timestamp=r.timestamp,
                note=r.payload.get("note", ""),
                acknowledged=r.payload.get("acknowledged", False),
            )
            if unacknowledged_only and obs.acknowledged:
                continue
            observations.append(obs)
        return observations

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