"""Application payload codec — the "letter" inside the BLE frame.

The framing layer (framing.py) is the envelope: it's dictated by the
PicoRelay BLE spec and we cannot change it. THIS module is the content
we put inside, and it is entirely our own definition — PicoRelay and
Camgenium never look at these bytes, they just carry them. The only
hard rule is that both ends agree, so the Pico encoder and the backend
decoder both import this module.

Every event fits in 7 bytes, well under the 16-byte single-frame
budget, so an event is always one SOLO frame.

Layout (little-endian):

    byte 0     EVENT_TYPE (uint8)
    bytes 1-2  ARG        (int16 signed) — meaning depends on type
    bytes 3-6  TS         (uint32, Unix seconds; 0 = unset)

ARG by event type:
    INTAKE  → volume delta in ml  (+50 drink-add, -50 correction)
    LIGHT   → 1 = on, 0 = off
    RESET   → 1 = shutdown, 0 = sleep
"""
import struct
from dataclasses import dataclass

EVENT_INTAKE = 0x01
EVENT_LIGHT = 0x02
EVENT_RESET = 0x03

EVENT_NAMES = {
    EVENT_INTAKE: "INTAKE",
    EVENT_LIGHT: "LIGHT",
    EVENT_RESET: "RESET",
}

_LAYOUT = struct.Struct("<BhI")  # type (u8), arg (i16), ts (u32)
PAYLOAD_SIZE = _LAYOUT.size  # 7 bytes


class EventCodecError(ValueError):
    """Raised when a payload can't be decoded as a known event."""


@dataclass
class Event:
    type: int
    arg: int
    ts: int = 0  # Unix seconds; 0 means unset

    @property
    def type_name(self):
        return EVENT_NAMES.get(self.type, f"0x{self.type:02x}")

    def __str__(self):
        return f"{self.type_name}(arg={self.arg}, ts={self.ts})"


def encode(event):
    """Event -> 7-byte payload (the bytes that go inside a frame)."""
    if not 0 <= event.type <= 0xFF:
        raise EventCodecError(f"event type out of range: {event.type}")
    if not -0x8000 <= event.arg <= 0x7FFF:
        raise EventCodecError(f"arg out of int16 range: {event.arg}")
    if not 0 <= event.ts <= 0xFFFFFFFF:
        raise EventCodecError(f"ts out of uint32 range: {event.ts}")
    return _LAYOUT.pack(event.type, event.arg, event.ts)


def decode(payload):
    """7-byte payload -> Event. Raises EventCodecError on bad input."""
    if len(payload) < PAYLOAD_SIZE:
        raise EventCodecError(
            f"payload too short: {len(payload)}B < {PAYLOAD_SIZE}B"
        )
    etype, arg, ts = _LAYOUT.unpack(payload[:PAYLOAD_SIZE])
    if etype not in EVENT_NAMES:
        raise EventCodecError(f"unknown event type: 0x{etype:02x}")
    return Event(type=etype, arg=arg, ts=ts)


# Convenience constructors — clearer call sites on the Pico/sender side.

def intake(delta_ml, ts=0):
    return Event(EVENT_INTAKE, delta_ml, ts)


def light(on, ts=0):
    return Event(EVENT_LIGHT, 1 if on else 0, ts)


def reset(shutdown, ts=0):
    return Event(EVENT_RESET, 1 if shutdown else 0, ts)
