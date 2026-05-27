"""BLE transport for DRIP firmware (MicroPython, Raspberry Pi Pico W).

Broadcasts DRIP events as BLE manufacturer-specific advertisements so
the Camgenium relay can pick them up and forward to the backend webhook.

Wire format is defined by the shared ``protocol`` package: an event is
encoded with ``event_codec`` (7-byte payload) and wrapped in a SOLO frame
with ``framing``. The backend (``backend/app/routes/ingest.py``) decodes
the same way. ``event_codec.py`` and ``framing.py`` must be flashed onto
the Pico alongside this file (they are imported flat, not as a package).

NOTE: only the intake path is on the shared protocol so far. The
sleep-start/sleep-end path still uses the legacy raw frame below and is
not yet decoded by the backend — see the project notes on deferred
sleep handling.

Hardware integration
--------------------
Replace the body of :meth:`_send` with real BLE calls once the Camgenium
relay's expected advertisement format is confirmed with the supervisor.
Search for ``# HARDWARE`` comments.
"""

import struct

from event_codec import encode, intake as _intake_event
from framing import build_frame

# Legacy raw event type codes (sleep path only; intake uses event_codec).
EVT_SLEEP_START = 0x02
EVT_SLEEP_END = 0x03

# Placeholder company ID for manufacturer-specific data.
# Replace with SoftSilicon / Camgenium assigned value once confirmed.
COMPANY_ID = 0xFFFF

try:
    import bluetooth as _bt
    _BLE_AVAILABLE = True
except ImportError:
    _bt = None
    _BLE_AVAILABLE = False


class BleTransport:
    """Thin BLE advertising wrapper for DRIP events.

    On hardware where the ``bluetooth`` module is unavailable (e.g. a
    desktop test environment), all transmit calls print to stdout so
    the pipeline can still run without crashing.
    """

    def __init__(self, device_name: str):
        self._name = device_name
        self._ble = _bt.BLE() if _BLE_AVAILABLE else None
        self._msg_id = 0

    def _next_msg_id(self) -> int:
        self._msg_id = (self._msg_id + 1) & 0xFF
        return self._msg_id

    def start(self) -> None:
        """Activate BLE and configure the device name."""
        if self._ble is None:
            print(f"[BLE] running without hardware, device={self._name}")
            return
        # HARDWARE: Activate BLE and set the GAP device name.
        #
        #   self._ble.active(True)
        #   self._ble.config(gap_name=self._name)
        pass

    def transmit_intake(self, volume_ml: float, ts_ms: int) -> None:
        """Broadcast an intake event using the shared protocol codec.

        ``ts_ms`` (device ticks) is not a wall-clock time, so the event's
        timestamp field is left unset (0); the backend stamps each event
        with the relay's delivery time on arrival.
        """
        payload = encode(_intake_event(int(volume_ml)))
        frame = build_frame(payload, self._next_msg_id())
        self._send(frame)

    def transmit_sleep_start(self, ts_ms: int) -> None:
        """Broadcast a sleep-start event."""
        self._advertise(EVT_SLEEP_START, 0, ts_ms)

    def transmit_sleep_end(self, ts_ms: int) -> None:
        """Broadcast a sleep-end (wake) event."""
        self._advertise(EVT_SLEEP_END, 0, ts_ms)

    def stop(self) -> None:
        """Stop advertising and power down the radio.

        Call this ~2 seconds after :meth:`transmit_intake` /
        :meth:`transmit_sleep_start` / :meth:`transmit_sleep_end` to
        keep the radio on only long enough for the Camgenium relay to
        pick up the frame, then cut power to extend battery life.
        """
        if self._ble is None:
            print("[BLE] stop() — no hardware")
            return
        # HARDWARE: Disable advertising and deactivate the radio.
        #
        #   self._ble.gap_advertise(None)   # None interval = stop advertising
        #   # Optionally power down:
        #   # self._ble.active(False)
        pass

    def _advertise(self, event_type: int, volume_ml: int, ts_ms: int) -> None:
        # Legacy raw frame, used by the deferred sleep path only.
        frame = struct.pack(
            "<BHI",
            event_type,
            volume_ml & 0xFFFF,
            ts_ms & 0xFFFFFFFF,
        )
        self._send(frame)

    def _send(self, frame: bytes) -> None:
        """Broadcast a complete frame over BLE (or print it in dev)."""
        if self._ble is None:
            print(f"[BLE TX] frame={frame.hex()}")
            return
        # HARDWARE: Build and broadcast the advertisement payload.
        #
        #   mfr_data = struct.pack("<H", COMPANY_ID) + frame
        #   payload = _adv_payload(name=self._name, manufacturer=mfr_data)
        #   # Interval 100 ms (100_000 µs). Adjust for Camgenium relay sensitivity.
        #   self._ble.gap_advertise(100_000, adv_data=payload)
        pass

    @staticmethod
    def _adv_payload(
        name: str | None = None,
        manufacturer: bytes | None = None,
    ) -> bytes:
        """Build a minimal BLE advertisement payload byte string."""
        payload = bytearray()
        if name:
            encoded = name.encode()
            payload += bytes((len(encoded) + 1, 0x09)) + encoded
        if manufacturer:
            payload += bytes((len(manufacturer) + 1, 0xFF)) + manufacturer
        return bytes(payload)
